"""Background computation of a loaded view's derived variables (expressions 55.3-55.5):
the scheduler (`ViewComputeRunner`) and the status-bar widget (`ViewComputeProgress`)."""

from __future__ import annotations

import functools
import math
import time
from collections.abc import Callable, Mapping

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from ..core.background_worker import WorkerHub
from ..core.column import DeferredColumn
from .i18n import tr


def estimate_remaining(elapsed: float, done: int, total: int) -> float | None:
    """Seconds left (expressions 55.4): elapsed / done * remaining; None until one has finished.
    `elapsed` is wall-clock since the start (variables run in parallel), not a sum of times."""
    if done <= 0:
        return None
    return max(0.0, elapsed / done * max(0, total - done))


def format_remaining(seconds: float) -> str:
    n = max(1, math.ceil(seconds))
    if n < 60:
        return tr("view_compute.seconds", n=n)
    return tr("view_compute.minutes", m=n // 60, s=n % 60)


class ViewComputeResult:
    """What a run produced: `computed` names, `failed` {name: reason}, `cancelled`, timings."""

    def __init__(self, computed: list[str], failed: dict[str, str], cancelled: bool, elapsed: float, parallel: int):
        self.computed = computed
        self.failed = failed
        self.cancelled = cancelled
        self.elapsed = elapsed
        self.parallel = parallel


class ViewComputeRunner:
    """Computes `columns` (name -> DeferredColumn) on a WorkerHub, one job per variable, in
    dependency order (`deps`: name -> names among `columns` it needs), at most `parallel` at once.

    Every job reports success or failure exactly once (WorkerHub); a failed variable and every
    variable depending on it are marked failed without being run. `cancel()` drops the pending
    results and calls `on_finished` at once with what is done so far; the running numpy work
    cannot be interrupted and its result stays in the DeferredColumn. `on_finished` is called
    exactly once (never after `abort()`), on the UI thread.
    """

    def __init__(
        self,
        hub: WorkerHub,
        columns: Mapping[str, DeferredColumn],
        deps: Mapping[str, set[str]],
        parallel: int,
        on_progress: Callable[[int, int], None],
        on_finished: Callable[[ViewComputeResult], None],
    ):
        self._hub = hub
        self._columns = dict(columns)
        self._deps = {n: set(deps.get(n, ())) & set(self._columns) for n in self._columns}
        self.parallel = max(1, parallel)
        self._on_progress = on_progress
        self._on_finished = on_finished
        self.total = len(self._columns)
        self.computed: list[str] = []
        self._computed_set: set[str] = set()
        self.failed: dict[str, str] = {}
        self._waiting = list(self._columns)  # not yet started (dependency order given by the caller)
        self._running: dict[str, int] = {}  # name -> hub token
        self._over = False
        self.started_at = time.perf_counter()

    @property
    def done(self) -> int:
        return len(self.computed) + len(self.failed)

    @property
    def running(self) -> int:
        return len(self._running)

    @property
    def active(self) -> bool:
        return not self._over

    def start(self) -> None:
        self.started_at = time.perf_counter()
        self._pump()

    def _pump(self) -> None:
        """Start every waiting variable whose dependencies are computed, up to the cap; settle
        variables whose dependency failed; finish when nothing is left."""
        if self._over:
            return
        progressed = True
        while progressed:
            progressed = False
            for name in list(self._waiting):
                blockers = self._deps[name] & set(self.failed)
                if blockers:
                    self._waiting.remove(name)
                    self.failed[name] = tr("view.skipped_dependency", name=sorted(blockers)[0])
                    progressed = True
                elif len(self._running) < self.parallel and self._deps[name].issubset(self._computed_set):
                    self._waiting.remove(name)
                    self._submit(name)
        self._on_progress(self.done, self.total)
        if not self._waiting and not self._running:
            self._finish(cancelled=False)

    def _submit(self, name: str) -> None:
        column = self._columns[name]

        def job() -> None:
            column.compute()  # the array stays in the column; nothing travels through the relay

        self._running[name] = self._hub.submit(
            job,
            functools.partial(self._ok, name),
            functools.partial(self._fail, name),
            job="view_compute",
            target=name,
        )

    def _ok(self, name: str, _result: object) -> None:
        if self._over:
            return
        self._running.pop(name, None)
        self.computed.append(name)
        self._computed_set.add(name)
        self._pump()

    def _fail(self, name: str, error: str) -> None:
        if self._over:
            return
        self._running.pop(name, None)
        self.failed[name] = tr("view.skipped_compute_failed", error=error)
        self._pump()

    def cancel(self) -> None:
        """The user's cancel: finish now with what is computed so far."""
        if not self._over:
            self._finish(cancelled=True)

    def abort(self) -> None:
        """Another file/view is being opened: forget everything, report nothing."""
        self._over = True
        self._drop_jobs()

    def _drop_jobs(self) -> None:
        for token in self._running.values():
            self._hub.cancel(token)
        self._running.clear()
        self._waiting.clear()

    def _finish(self, *, cancelled: bool) -> None:
        self._over = True
        self._drop_jobs()
        elapsed = time.perf_counter() - self.started_at
        self._on_finished(ViewComputeResult(list(self.computed), dict(self.failed), cancelled, elapsed, self.parallel))


class ViewComputeProgress(QWidget):
    """Status-bar widget: progress bar, "n / total - about M s left" and the cancel button
    (expressions 55.4). Shown by the owner only after the run has lasted a while."""

    def __init__(self, on_cancel: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("view_compute_progress")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setObjectName("view_compute_bar")
        self.bar.setMaximumWidth(140)
        self.bar.setTextVisible(False)
        self.label = QLabel()
        self.label.setObjectName("view_compute_label")
        self.cancel_button = QPushButton(tr("view_compute.cancel"))
        self.cancel_button.setObjectName("view_compute_cancel_button")
        self.cancel_button.clicked.connect(lambda *_: on_cancel())
        layout.addWidget(self.bar)
        layout.addWidget(self.label)
        layout.addWidget(self.cancel_button)
        self._done = 0
        self._total = 0
        self._started = time.perf_counter()
        self._tick = QTimer(self)  # the remaining time shrinks even when nothing finishes
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._refresh)
        self.setVisible(False)

    def begin(self, started_at: float, done: int, total: int) -> None:
        self._started = started_at
        self.set_progress(done, total)
        self._tick.start()

    def end(self) -> None:
        self._tick.stop()
        self.setVisible(False)

    def set_progress(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self._refresh()

    def _refresh(self) -> None:
        remaining = estimate_remaining(time.perf_counter() - self._started, self._done, self._total)
        if remaining is None:
            text = tr("view_compute.estimating", done=self._done, total=self._total)
        else:
            text = tr(
                "view_compute.progress", done=self._done, total=self._total, remaining=format_remaining(remaining)
            )
        self.label.setText(text)
