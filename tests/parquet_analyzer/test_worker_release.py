"""detailed_specification.md 37.3 / dev notes 13.6: a finished background job (its task, its
function/closure and the arrays it captured) must actually be freed, each result is delivered
exactly once, and cyclic GC runs on the UI thread only.

The release tests go through a real MainWindow and hook QThreadPool.start, so they are
independent of how the worker is implemented -- and they fail against the old
BackgroundWorker design (which had slot lambdas capture the worker object; the pool kept it alive).
"""
from __future__ import annotations

import gc
import os
import subprocess
import sys
import textwrap
import time
import weakref
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QMessageBox
from waiting import wait_until

from parquet_analyzer import __main__ as app_main
from parquet_analyzer.core.background_worker import WorkerHub
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def tracked(monkeypatch):
    """Weakrefs to every runnable handed to a QThreadPool, its `signals` object (old design),
    its job function and every ndarray that function's closure/defaults capture."""
    refs: list[weakref.ref] = []
    real_start = QThreadPool.start

    def start(self, runnable, *args, **kwargs):
        refs.append(weakref.ref(runnable))
        if getattr(runnable, "signals", None) is not None:
            refs.append(weakref.ref(runnable.signals))
        func = getattr(runnable, "_func", None)
        if func is not None:
            refs.append(weakref.ref(func))
            cells = [c.cell_contents for c in (getattr(func, "__closure__", None) or ())]
            for obj in cells + list(getattr(func, "__defaults__", None) or ()):
                if isinstance(obj, np.ndarray):
                    refs.append(weakref.ref(obj))
        return real_start(self, runnable, *args, **kwargs)

    monkeypatch.setattr(QThreadPool, "start", start)
    return refs


def _drain(qapp, cond=None, rounds=None, timeout=30.0):
    """Process events until `cond()` (real-time deadline, so slow CI machines are fine; the
    caller asserts afterwards, so a job that really never finishes still fails). With `rounds`,
    just pump that many rounds (used to give a duplicate delivery time to show up). Without
    `rounds` a condition is required: waiting on nothing would always run to the deadline."""
    if rounds is not None:
        for _ in range(rounds):
            qapp.processEvents()
            time.sleep(0.01)
            if cond is not None and cond():
                break
    else:
        assert cond is not None, "_drain needs a condition or rounds="
        wait_until(cond, timeout)
    for _ in range(5):
        qapp.processEvents()
        time.sleep(0.005)


def _alive(refs):
    gc.collect()
    return [r for r in refs if r() is not None]


def _open(path, windowed=False):
    win = MainWindow()
    if windowed:
        win.settings.eager_load_limit_mb = 0
    win.show()
    assert win.load_parquet(str(path))
    return win


def _select(win, name):
    win.variable_panel.clearSelection()
    win.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)[0].setSelected(True)


def test_downsample_jobs_are_freed(qapp, isolated_config, sample_parquet_path, tracked):
    win = _open(sample_parquet_path)
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    plot0 = win.plot_grid.plots[0]
    _drain(qapp, lambda: not win._is_settling() and not plot0._active_workers and not _alive(tracked))
    assert tracked, "no downsample job was created"
    assert not win.plot_grid.plots[0]._active_workers
    assert not _alive(tracked), "downsample task/function/array leaked after its result was handled"
    win.close()


def test_pyramid_jobs_are_freed(qapp, isolated_config, tmp_path, monkeypatch, tracked):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    n = 5000
    rng = np.random.default_rng(0)
    path = tmp_path / "w.parquet"
    pq.write_table(pa.table({"t": np.arange(n, dtype=float), "a": rng.normal(size=n)}), path)
    win = _open(path, windowed=True)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    _drain(qapp, lambda: not win._is_settling() and win._raw_variables["a"].pyramid is not None
           and not plot._active_workers and not _alive(tracked))
    assert win._raw_variables["a"].pyramid is not None, "the pyramid job never ran"
    assert not plot._active_workers
    assert not _alive(tracked), "pyramid/downsample task/function leaked after its result was handled"
    win.close()


def test_stats_jobs_are_freed(qapp, isolated_config, sample_parquet_path, tracked):
    win = _open(sample_parquet_path)
    _drain(qapp, lambda: not win._is_settling())
    tracked.clear()
    _select(win, "a")
    win._on_stats_requested()
    _drain(qapp, lambda: tracked and not win._stats_workers and not _alive(tracked))
    assert tracked and not win._stats_workers
    assert not _alive(tracked), "stats task/function/array leaked after its result was handled"
    win.close()


def test_fft_jobs_are_freed(qapp, isolated_config, sample_parquet_path, tracked):
    win = _open(sample_parquet_path)
    _drain(qapp, lambda: not win._is_settling())
    tracked.clear()
    _select(win, "a")
    win._on_fft_requested()
    _drain(qapp, lambda: tracked and not win._fft_workers and not _alive(tracked))
    assert tracked and not win._fft_workers
    assert not _alive(tracked), "FFT task/function/array leaked after its result was handled"
    win.close()


def test_xy_analysis_jobs_are_freed(qapp, isolated_config, sample_parquet_path, tracked):
    win = _open(sample_parquet_path)
    _drain(qapp, lambda: not win._is_settling())
    tracked.clear()
    w = win.open_xy_plot()
    w._x_combo.setCurrentText("a")
    w._y_picker.set_selected(w._y_picker.candidates()[:1])
    w.recompute()
    _drain(qapp, lambda: tracked and not w.is_computing() and not win._analysis_hub.pending
           and not [r for r in _alive(tracked) if not isinstance(r(), np.ndarray)])
    assert tracked and not w.is_computing() and not win._analysis_hub.pending
    # (arrays are not checked here: the job may legitimately capture the main window's own
    # column data, which stays alive with the open file)
    leaked = [r for r in _alive(tracked) if not isinstance(r(), np.ndarray)]
    assert not leaked, "analysis task/function leaked after its result was handled"
    w.close()
    win.close()


# -- WorkerHub delivery ----------------------------------------------------------------


def test_each_job_delivers_exactly_one_callback(qapp):
    hub = WorkerHub()
    events = []

    def boom():
        raise ValueError("kaboom")

    hub.submit(lambda: 7, lambda r: events.append(("ok", r)), lambda m: events.append(("fail", m)))
    hub.submit(boom, lambda r: events.append(("ok", r)), lambda m: events.append(("fail", m)))
    _drain(qapp, lambda: len(events) >= 2)
    _drain(qapp, rounds=20)  # a duplicate delivery, if any, would land here
    assert sorted(events) == [("fail", "ValueError: kaboom"), ("ok", 7)]
    assert not hub.pending


def test_cancelled_token_result_is_ignored(qapp):
    hub = WorkerHub()
    events = []
    token = hub.submit(lambda: (time.sleep(0.05), 1)[1], events.append, events.append)
    hub.cancel(token)
    assert not hub.pending
    _drain(qapp, rounds=40)
    assert events == []


def test_unknown_token_is_ignored_and_delivery_pops_first(qapp):
    hub = WorkerHub()
    hub._deliver(999_999, 1, "")  # never registered: no error, no callback
    hub._deliver(999_999, None, "x")
    seen = []
    hub.pending[5] = (lambda r: seen.append(5 in hub.pending), lambda m: seen.append("f"))
    hub._deliver(5, None, "")
    hub._deliver(5, None, "")  # second delivery of the same token: ignored
    assert seen == [False]


def test_trivial_jobs_leave_no_task_behind(qapp, monkeypatch):
    """37.1: submitting a QRunnable subclass leaked ~14% of trivial jobs (PySide registers the
    runnable as a pool child after the job already ran); a plain callable must leak none."""
    refs: list[weakref.ref] = []
    real_start = QThreadPool.start

    def start(self, task, *args, **kwargs):
        refs.append(weakref.ref(task))
        return real_start(self, task, *args, **kwargs)

    monkeypatch.setattr(QThreadPool, "start", start)
    hub = WorkerHub()
    done = [0]
    n = 3000
    for _ in range(n):
        hub.submit(lambda: 1, lambda r: done.__setitem__(0, done[0] + 1), lambda m: None)
    _drain(qapp, lambda: done[0] == n)
    QThreadPool.globalInstance().waitForDone()
    _drain(qapp, rounds=5)
    assert done[0] == n and not hub.pending
    # >= rather than ==: a timer left by an earlier test in the same process (which one depends
    # on the xdist split) can start a few jobs of its own while we drain; they must die too.
    assert len(refs) >= n
    assert not _alive(refs), "tasks were retained after running"


def test_job_function_is_dropped_before_delivery(qapp):
    """37.1: the task releases the job's captures itself (not left on the pool thread)."""
    hub = WorkerHub()
    arr = np.zeros(1000)
    ref = weakref.ref(arr)
    got = []
    hub.submit(lambda a=arr: float(a.sum()), got.append, got.append)
    del arr
    _drain(qapp, lambda: got and not _alive([ref]))
    assert got == [0.0]
    assert not _alive([ref])


_STRESS_SCRIPT = textwrap.dedent(
    '''
    import faulthandler, gc, os, random, sys, time
    faulthandler.enable()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, sys.argv[1])
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThreadPool
    from PySide6.QtWidgets import QApplication
    from parquet_analyzer.core.background_worker import WorkerHub
    app = QApplication([])
    gc.disable()

    class Owner(QObject):
        def __init__(self):
            super().__init__()
            self.hub = WorkerHub()

    delivered = [0]
    for _ in range(int(sys.argv[2])):
        o = Owner()
        for _ in range(4):
            d = random.random() * 0.0002
            o.hub.submit(lambda d=d: time.sleep(d) or 1, lambda r: delivered.__setitem__(0, delivered[0] + 1), lambda m: None)
        t = time.perf_counter() + random.random() * 0.0003
        while time.perf_counter() < t:
            pass
        o.deleteLater()
        del o
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    QThreadPool.globalInstance().waitForDone()
    app.processEvents()
    print("ok")
    '''
)


def test_destroying_hub_owner_with_jobs_in_flight_does_not_crash(tmp_path):
    """37.1/37.3 stress (separate process: a use-after-free kills the interpreter): jobs finish
    while the UI thread destroys the hub's owner."""
    script = tmp_path / "stress.py"
    script.write_text(_STRESS_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")
    proc = subprocess.run(
        [sys.executable, str(script), src, "2000"], capture_output=True, text=True, timeout=300,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr[-2000:]


# -- 37.2 GC on the UI thread ----------------------------------------------------------


def test_app_startup_runs_the_gc_guard(qapp, monkeypatch):
    class _App:
        def __init__(self, *a):
            pass

        def exec(self):
            return 0

    class _Win:
        def __init__(self, *a, **k):
            pass

        def show(self):
            pass

    monkeypatch.setattr(app_main, "QApplication", _App)
    monkeypatch.setattr(app_main, "MainWindow", _Win)
    monkeypatch.setattr(app_main, "_configure_logging", lambda debug: None)
    monkeypatch.setattr(app_main.oplog, "shutdown", lambda: None)
    monkeypatch.setattr(app_main, "_gc_guard", None)
    try:
        gc.enable()
        assert app_main.main([]) == 0
        guard = app_main._gc_guard
        assert guard is not None, "main() did not start the GC guard"
        assert not gc.isenabled(), "automatic GC must be off in the app"
        assert guard.timer.isActive()
        assert guard.timer.interval() == 1000
    finally:
        guard = app_main._gc_guard
        if guard is not None:
            guard.timer.stop()
        gc.disable()  # the test session keeps automatic GC off (conftest)


def test_gc_guard_timer_collects_cycles(qapp):
    class Node:
        pass

    guard = app_main._start_gc_guard()
    try:
        guard.threshold = (0, 100000, 100000)  # collect gen 0 as soon as anything was allocated
        alive = []
        for _ in range(20):
            a, b = Node(), Node()
            a.other, b.other = b, a  # a cycle: only the GC can free it
            alive.append(weakref.ref(a))
        del a, b
        guard.check()
        assert not [r for r in alive if r() is not None]
    finally:
        guard.timer.stop()
        app_main._gc_guard = None
        gc.disable()


def test_gc_guard_timer_fires_with_default_threshold(qapp):
    """37.2/37.3: no check() call and the default threshold: the 1 s timer itself collects."""
    class Node:
        pass

    guard = app_main._start_gc_guard()
    try:
        refs = []
        for _ in range(3000):  # well past the gen-0 threshold (700 allocations)
            a, b = Node(), Node()
            a.other, b.other = b, a
            refs.append(weakref.ref(a))
        del a, b
        end = time.monotonic() + 1.2
        while time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert not [r for r in refs if r() is not None], "the GC timer never collected"
    finally:
        guard.timer.stop()
        app_main._gc_guard = None
        gc.disable()
