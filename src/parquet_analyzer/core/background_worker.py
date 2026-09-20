from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class _Signals(QObject):
    finished = Signal(object)  # whatever `func` returned, passed through as-is


class BackgroundWorker(QRunnable):
    """Runs `func` off the UI thread and hands its return value back via
    `signals.finished` (detailed_specification.md 10章/13.5.1/14章).

    Every background job in this app (LTTB downsampling, FFT, basic_stats) has the
    same shape — call a pure function, deliver the result to the UI thread through a
    queued signal — so they share this one worker instead of each re-implementing it.

    `setAutoDelete(False)` and the caller-held-strong-reference requirement below are
    not optional: `finished.emit()` is a queued cross-thread connection, and
    QThreadPool's default autoDelete=True destroys this QRunnable (and the QObject
    carrying the signal) as soon as run() returns, on the worker thread — before the
    main thread gets a chance to deliver the queued signal. The caller must keep a
    strong reference (e.g. in a set) until `finished` has been handled, then drop it.
    """

    def __init__(self, func: Callable[[], object]):
        super().__init__()
        self._func = func
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        self.signals.finished.emit(self._func())
