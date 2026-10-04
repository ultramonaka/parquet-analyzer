from __future__ import annotations

import itertools
import logging
import time
import weakref
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal

from . import oplog

logger = logging.getLogger(__name__)

_tokens = itertools.count(1)


class _Relay(QObject):
    """The one and only signal sender (detailed_specification.md 37.1): a process-lifetime
    QObject on the UI thread that is never deleted. Pool threads emit `done` on it; the queued
    slot runs on the UI thread, resolves the hub weakref and drops results for dead hubs.

    Emitting on a per-hub QObject that the UI thread destroys mid-emit is a use-after-free
    (reproduced in review); a sender that never dies cannot be.
    """

    done = Signal(object, int, object, str)  # (hub weakref, token, result, error message or "")

    def __init__(self):
        super().__init__()
        self.done.connect(self._deliver)

    def _deliver(self, hub_ref, token: int, result: object, error: str) -> None:
        hub = hub_ref()
        if hub is not None:
            hub._deliver(token, result, error)


_relay: _Relay | None = None


def _get_relay() -> _Relay:
    """Create the relay lazily (needs a QCoreApplication, UI thread); kept in a module global."""
    global _relay
    if _relay is None:
        _relay = _Relay()
    return _relay


class _Task:
    """The pool-side half of one job (37.1): a plain callable, deliberately NOT a QRunnable.

    PySide's QThreadPool.start(QRunnable) registers the runnable as a Python child of the pool
    after releasing the GIL; a short job can run and be deleted first, and the child reference
    then never goes away (~430 of 3,000 trivial jobs stayed alive, pinning what they captured).
    The callable overload has no such race.
    """

    def __init__(self, func: Callable[[], object], hub_ref, token: int, job: str, target: object):
        self._func = func
        self._hub_ref = hub_ref
        self._token = token
        self._done = _get_relay().done  # created here, on the UI thread
        # Operation-log metadata (30.3): job kind, what it works on, and which user
        # operation was pending when it was queued (so a failure traces back to its cause).
        self._job = job
        self._target = target
        self._op = oplog.current_op()

    def __call__(self) -> None:
        func, self._func = self._func, None
        record_all = oplog.is_active() and oplog.job_recorded_always(self._job)
        t0 = time.perf_counter()
        if record_all:
            oplog.record_job(self._job, "start", target=self._target, op=self._op)
        try:
            result, error = func(), ""
        except Exception as exc:  # noqa: BLE001 - KeyboardInterrupt/SystemExit are not Exceptions
            logger.exception("Background job failed")
            result, error = None, f"{type(exc).__name__}: {exc}"
        del func  # drop what the job captured here, not on the pool thread after the emit
        if oplog.is_active():
            if error:  # recorded exactly once, here (30.3)
                oplog.record_job(
                    self._job, "fail", target=self._target, op=self._op,
                    ms=(time.perf_counter() - t0) * 1000, error=error,
                )
            elif record_all:
                oplog.record_job(
                    self._job, "finish", target=self._target, op=self._op, ms=(time.perf_counter() - t0) * 1000
                )
        self._done.emit(self._hub_ref, self._token, result, error)


class WorkerHub:
    """Runs jobs off the UI thread and hands each result (or failure) back to the UI thread
    (detailed_specification.md 37.1; 10章/28.1; backlog 13.6). Replaces `BackgroundWorker`.

    A plain Python object owned by its owner (plot, main window); not a QObject and not a
    signal sender (the module's `_Relay` is). `pending` maps token -> (on_finished, on_failed):
    the strong reference until the result is handled, and the owners' "anything in flight"
    bookkeeping. Exactly one callback runs per job, on the UI thread (28.1). Callbacks must not
    capture the task. Created on the UI thread.
    """

    def __init__(self, pool: QThreadPool | None = None):
        self._pool = pool if pool is not None else QThreadPool.globalInstance()
        self.pending: dict[int, tuple[Callable[[object], None], Callable[[str], None]]] = {}
        _get_relay()

    def submit(
        self,
        func: Callable[[], object],
        on_finished: Callable[[object], None],
        on_failed: Callable[[str], None],
        *,
        job: str = "task",
        target: object = None,
    ) -> int:
        """Start `func` on the hub's pool; later call `on_finished(result)` or
        `on_failed("<ExceptionType>: <message>")` (exactly one) on the UI thread.
        Returns the token (usable with `cancel`)."""
        token = next(_tokens)
        self.pending[token] = (on_finished, on_failed)
        self._pool.start(_Task(func, weakref.ref(self), token, job, target))
        return token

    def cancel(self, token: int) -> None:
        """Forget `token`: its result, when it arrives, is ignored (the job itself still runs)."""
        self.pending.pop(token, None)

    def _deliver(self, token: int, result: object, error: str) -> None:
        callbacks = self.pending.pop(token, None)  # pop first: a callback re-submitting is safe
        if callbacks is None:
            return  # cancelled / unknown
        if error:
            callbacks[1](error)
        else:
            callbacks[0](result)
