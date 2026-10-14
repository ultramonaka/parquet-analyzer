"""A2 (app 46.5): every WorkerHub owner x every event on a job that is held mid-flight.

Jobs are parked with a `Gate` (a monkeypatched compute function waiting on a threading.Event,
always released in teardown); the event is triggered while held; then we wait in real time
until nothing is in flight and check: no crash, exactly-once callbacks, stale / orphaned results
not applied, busy markers and `hub.pending` cleared, a failure reported once, and no `exception`
record in the op log (`job fail` records are expected only for injected failures).

Owners: D = series downsample (plot_widget `_hub`; "lttb" = eager series, "-thin" = windowed
first draw), P = pyramid build (plot_widget `_hub`), N = navigator overview (navigator_sync
`_hub`; "-derived" = derived-series pyramid wait), S = stats (`_stats_hub`), F = FFT
(`_fft_hub`, flag-independent), A = analysis window (`_analysis_hub`, XY window).
Events: 1 success, 2 exception, 3 superseded by a newer job, 4 receiver removed, 5 another file
opened, 6 time axis switched.

Matrix (existing = other file; new = test id here, parametrized as <owner>-<event>[-variant]):
      1                      2                      3                 4                      5                          6
 D  new D-1[-thin]         new D-2[-thin]         new D-3[-thin]    new D-4[-thin],        new D-5[-thin],            new D-6[-thin]
    (also existing:        (also existing:                          test_downsample_       test_downsample_stale_job_
    test_worker_release::   test_worker_failure_                    remove_plot_...        of_previous_file_... (fixed, 28.4)
    test_downsample_jobs_   and_time_order::
    are_freed)              test_downsample_failure_...)
 P  new P-1               new P-2                new P-3           new P-4,               new P-5                    new P-6
    (also existing:        (also existing: test_worker_failure_and_ test_pyramid_remove_
    test_worker_release::   time_order::test_pyramid_build_   plot_with_build_in_flight
    test_pyramid_jobs_      failure_retries_...)
    are_freed)
 N  new N-1[-derived]      new N-2[-derived]      new N-3[-derived] new N-4[-derived]      new N-5[-derived],         new N-6[-derived]
    (also existing:        (also existing:        (also existing:                         test_navigator_stale_
    test_navigator_sync::   test_navigator_sync::   test_navigator_                        refinement_when_next_file_
    test_windowed_...)      test_failed_refinement  sync::test_stale_                      is_eager (fixed, 28.4)
                            _keeps_coarse_...)      refinement_is_dropped)
 S  new S-1                new S-2                new S-3           n/a: no receiver exists  new test_stats_result_of_   new S-6
    (also existing:        (also existing:        (stats have no    until delivery (the      previous_file_is_not_shown
    test_worker_release::   test_worker_failure_    generations: each dialog is created at   (xfail)
    test_stats_jobs_are_    and_time_order::        request is        delivery; closing the
    freed)                  test_stats_partial_...) answered on its   main window is covered by
                                                    own)              test_worker_release::
                                                                      test_destroying_hub_owner_...)
 F  new F-1                new F-2                new F-3           n/a: as S-4 (the plot    new test_fft_result_of_    new F-6
    (also existing:        (also existing:        (no generations:  is created at delivery)  previous_file_is_not_
    test_worker_release::   test_worker_failure_    both answered)                            added (xfail)
    test_fft_jobs_are_      and_time_order::
    freed)                  test_fft_failure_...)
 A  new A-1                new A-2                new A-3           new A-4                new A-5 (windows closed)   new A-6 (windows closed)
    (also existing:
    test_worker_release::
    test_xy_analysis_jobs_
    are_freed)

Cells 5/6 of S and F: the spec asks that a previous file's result is not used (5); for 6 the
result is a snapshot of the request (delivered once), see the tests. Findings are marked
`xfail(strict=True, reason="A2 finding: ...")`; app code is not changed (46 preamble).
"""
from __future__ import annotations

import gc
import json
import threading
import time
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QEvent, Qt, QThreadPool
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget
from waiting import wait_until

from parquet_analyzer import config
from parquet_analyzer.core import column as column_module
from parquet_analyzer.core import oplog
from parquet_analyzer.core.column import WindowedColumn
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui import navigator_sync as ns_module
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui import xy_plot_window
from parquet_analyzer.ui.analysis_window import AnalysisWindow
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.navigator import NavigatorWidget
from parquet_analyzer.ui.navigator_sync import NavigatorSync
from parquet_analyzer.ui.plot_widget import TimePlotWidget
from parquet_analyzer.ui.stats_dialog import StatsDialog
from parquet_analyzer.ui.xy_plot_window import XYPlotWindow

WIDE_POOL = 6  # threads per pool, whatever the machine's core count
HOLD_TIMEOUT = 30.0  # a held job gives up (and fails) after this long, so no thread outlives a test
SETTLE_TIMEOUT = 30.0
N_ROWS = 20_000


class Gate:
    """Wraps a compute function so a test can hold jobs on the pool thread (46.5 "止める道具").

    Every call gets an index; `entered` counts calls that reached the gate; a call blocks until
    `release(i)` / `release_all()`, then raises (`fail(i)` true) or returns the real result,
    passed through `transform(i, result)`."""

    def __init__(self, real, *, fail=None, transform=None):
        self.real = real
        self.fail = fail or (lambda i: False)
        self.transform = transform or (lambda i, result: result)
        self.entered = 0
        self.results: list = []
        self._lock = threading.Lock()
        self._release = threading.Event()  # release_all
        self._released: dict[int, threading.Event] = {}

    def _event(self, i):
        with self._lock:
            return self._released.setdefault(i, threading.Event())

    def __call__(self, *args, **kwargs):
        with self._lock:
            i = self.entered
            self.entered += 1
        released = self._event(i)
        deadline = time.monotonic() + HOLD_TIMEOUT
        while not (released.is_set() or self._release.is_set()):
            if time.monotonic() > deadline:
                raise RuntimeError("gate: held job was never released")
            time.sleep(0.002)
        if self.fail(i):
            raise RuntimeError(f"A2 injected failure #{i}")
        result = self.transform(i, self.real(*args, **kwargs))
        self.results.append(result)
        return result

    def release(self, i):
        self._event(i).set()

    def release_all(self):
        self._release.set()


@pytest.fixture
def make_gate():
    gates: list[Gate] = []

    def make(real, **kw):
        g = Gate(real, **kw)
        gates.append(g)
        return g

    yield make
    for g in gates:  # never leave a pool thread held (also when the test failed)
        g.release_all()


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture(autouse=True)
def _no_foreign_windows(qapp):
    """Gates are patched on module-level functions, so a window that an earlier test of this
    process left open (it may still redraw) would feed jobs into our gates and skew the counts.
    Delete such windows first."""
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget.close()
            widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QApplication.processEvents()
    yield


@pytest.fixture(autouse=True)
def _wide_pools():
    """Several jobs must be able to run (be held) at once, whatever the CI machine's core count."""
    pool = QThreadPool.globalInstance()
    old = pool.maxThreadCount()
    pool.setMaxThreadCount(max(old, WIDE_POOL))
    yield
    pool.setMaxThreadCount(old)


@pytest.fixture
def log(qapp, isolated_config):
    """Operation log on (tmp dir) with the uncaught-exception hooks, so a slot that raises
    shows up as an `exception` record."""
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    oplog.install_excepthooks()
    yield
    oplog.uninstall_excepthooks()
    oplog.reset()


def records(kind: str | None = None, **match) -> list[dict]:
    oplog.flush()
    path = oplog.log_path_for(date.today())
    if not path.exists():
        return []
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in out if (kind is None or r.get("kind") == kind) and all(r.get(k) == v for k, v in match.items())]


def write_file(path, seed=0, n=N_ROWS, row_group_size=None):
    """t ascending, t2 ascending on another scale (a second valid time axis), a/b random."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    table = pa.table({"t": t, "t2": t * 2.0 + 5.0, "a": rng.normal(size=n), "b": rng.normal(size=n) * 10.0})
    pq.write_table(table, path, row_group_size=row_group_size)
    return str(path)


@pytest.fixture
def two_files(tmp_path):
    return (
        write_file(tmp_path / "one.parquet", seed=1, row_group_size=2000),
        write_file(tmp_path / "two.parquet", seed=2, row_group_size=2000),
    )


def open_window(path, *, windowed=False):
    win = MainWindow()
    # The analysis jobs (stats/FFT/XY/overview) run on the window's own pool, which defaults to
    # the core count (2 on a CI runner) - too few for tests that hold 3 jobs at once.
    win._analysis_thread_pool.setMaxThreadCount(WIDE_POOL)
    if windowed:
        win.settings.eager_load_limit_mb = 0  # every column windowed
    win.resize(1200, 900)
    win.show()
    assert win.load_parquet(path)
    return win


def all_hubs(win):
    hubs = [p._hub for p in win.plot_grid.plots]
    hubs += [win._stats_hub, win._fft_hub, win._analysis_hub, win._nav_sync._hub]
    return hubs


def pending(win):
    return sum(len(h.pending) for h in all_hubs(win))


def jobs_entered_all(win, *gates):
    """Every submitted job has reached its (held) function: nothing is still queued."""
    return all(len(h.pending) >= 0 for h in all_hubs(win)) and sum(g.entered for g in gates) >= pending(win)


def wait_held(win, *gates, minimum=1):
    """Debounces fired and every submitted job is parked in its gate."""
    def cond():
        return (
            not any(p._redraw_debounce.isActive() for p in win.plot_grid.plots)
            and sum(g.entered for g in gates) >= minimum
            and sum(g.entered for g in gates) >= pending(win)
        )
    assert wait_until(cond, SETTLE_TIMEOUT), "jobs never reached the gate"
    time.sleep(0.05)  # a late debounce / second submit would show up now
    assert wait_until(cond, SETTLE_TIMEOUT)


def settle(win, extra=lambda: True):
    """Real-time wait until nothing is in flight or about to start, then give a duplicate
    delivery time to show up. Returns only if it really settled (else the assert fails)."""
    def cond():
        return (
            not win._busy_sources
            and pending(win) == 0
            and not any(p._redraw_debounce.isActive() for p in win.plot_grid.plots)
            and extra()
        )
    assert wait_until(cond, SETTLE_TIMEOUT), (
        f"never settled: busy={sorted(win._busy_sources)} pending={pending(win)}"
    )
    for _ in range(6):
        time.sleep(0.01)
        wait_until(lambda: True)
    assert cond()


def assert_clean_log(*, failed_jobs: dict[str, int] | None = None):
    """No `exception` record; `job fail` records exactly as many as the injected failures."""
    assert records("exception") == []
    fails = records("job", event="fail")
    counts: dict[str, int] = {}
    for r in fails:
        counts[r["job"]] = counts.get(r["job"], 0) + 1
    assert counts == (failed_jobs or {}), f"unexpected job failure records: {fails}"


class Spy:
    """Counts calls of an instance method of a class and still calls through. The patch is on the
    class, so only calls whose `self` passes `scope` are counted (other tests' windows in the same
    process may still be running jobs); until `scope` is set nothing is counted. `calls` holds
    the arguments without `self`."""

    def __init__(self, monkeypatch, owner, name, scope=None):
        self.calls: list[tuple] = []
        self.scope = scope or (lambda this: False)
        raw = owner.__dict__[name]

        def wrapper(this, *args, **kwargs):
            try:
                mine = self.scope(this)
            except RuntimeError:  # a deleted Qt object of some other test
                mine = False
            if mine:
                self.calls.append(args)
            return raw(this, *args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    def __len__(self):
        return len(self.calls)


def watch_curve(plot, name="a"):
    """Counts `setData` calls on one series' curve (what actually gets drawn)."""
    calls: list[tuple] = []
    curve_item = plot._series[name]["curve"]
    real = curve_item.setData

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    curve_item.setData = counting
    return calls


# ===================================================================== D: downsample (eager, lttb)

SENTINEL_Y = 777.0


def sentinel_xy():
    return np.array([0.0, 1.0, 2.0, 3.0, 4.0]), np.full(5, SENTINEL_Y)


class World:
    """Plain attribute bag handed to the tests of one owner."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def tagged_gate(make_gate, monkeypatch, owner, name, fresh, *, wrap_job, sentinel):
    """Park `owner.name` in a Gate. `wrap_job=False`: `name` is the compute function itself
    (lttb); True: `name` is a method returning the job closure (WindowedColumn.thin_job)."""
    if wrap_job:
        gate = make_gate(lambda job: job())
        real_method = getattr(owner, name)

        def job_factory(self, *args, **kwargs):
            job = real_method(self, *args, **kwargs)

            def tagged():
                stale_at_entry = not fresh["on"]
                result = gate(job)
                return sentinel() if stale_at_entry else result

            return tagged

        monkeypatch.setattr(owner, name, job_factory)
        return gate

    gate = make_gate(getattr(owner, name))

    def tagged(*args, **kwargs):
        stale_at_entry = not fresh["on"]
        result = gate(*args, **kwargs)
        return sentinel() if stale_at_entry else result

    monkeypatch.setattr(owner, name, tagged)
    return gate


def sentinel_idx_y():
    return np.arange(5, dtype=np.int64), np.full(5, SENTINEL_Y)


@pytest.fixture
def d_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    """Factory for the downsample world. kind "lttb": eager file, `lttb` (run on the pool by the
    series worker) is parked in a gate. kind "thin": windowed file, wide view, no pyramid
    (36.6 A first draw): the strided-read job is parked. A call that *entered* before
    `fresh["on"]` is set returns a recognisable 5-point sentinel instead of the real result,
    so "a stale result was applied" is visible in the curve."""
    worlds: list[World] = []

    def build(kind):
        fresh = {"on": False}
        if kind == "lttb":
            gate = tagged_gate(make_gate, monkeypatch, plot_widget_module, "lttb", fresh,
                               wrap_job=False, sentinel=sentinel_xy)
            ok_name, windowed = "_on_downsampled", False
        else:
            monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
            monkeypatch.setattr(WindowedColumn, "request_pyramid", lambda self: None)  # only the thin draw runs
            gate = tagged_gate(make_gate, monkeypatch, WindowedColumn, "thin_job", fresh,
                               wrap_job=True, sentinel=sentinel_idx_y)
            ok_name, windowed = "_on_thin_drawn", True
        win = open_window(two_files[0], windowed=windowed)
        plot = win.plot_grid.plots[0]
        w = World(
            win=win, plot=plot, gate=gate, fresh=fresh, files=two_files, kind=kind,
            ok=Spy(monkeypatch, TimePlotWidget, ok_name),
            failed_cb=Spy(monkeypatch, TimePlotWidget, "_on_series_worker_failed"),
            render_failed=[], owned=[], sets=[],
        )
        for spy in (w.ok, w.failed_cb):
            spy.scope = lambda p, w=w: p.window() is w.win or p in w.owned
        plot.renderFailed.connect(lambda p, n, m: w.render_failed.append((n, m)))
        worlds.append(w)
        return w

    yield build
    for w in worlds:
        w.gate.release_all()
        w.win.close()


def curve(plot, name="a"):
    x, y = plot._series[name]["curve"].getData()
    return np.asarray(x), np.asarray(y)


def is_sentinel(plot, name="a"):
    _x, y = curve(plot, name)
    return len(y) == 5 and bool(np.all(y == SENTINEL_Y))


def assert_real_downsample(plot, name="a"):
    x, y = curve(plot, name)
    assert not is_sentinel(plot, name)
    assert 5 < len(x) < N_ROWS and len(x) == len(y)


D_CASES = [(kind, ev) for kind in ("lttb", "thin") for ev in range(1, 7)]
D_IDS = [f"D-{ev}" + ("-thin" if kind == "thin" else "") for kind, ev in D_CASES]


@pytest.mark.parametrize(("kind", "event"), D_CASES, ids=D_IDS)
def test_downsample_paths(d_world, kind, event):
    w = d_world(kind)
    win, plot, gate = w.win, w.plot, w.gate
    w.fresh["on"] = event in (1, 2)  # 3 and 6: the jobs started *before* the event are the stale ones
    win._on_variable_dropped(plot, "a")
    w.sets = watch_curve(plot)
    wait_held(win, gate)
    assert gate.entered >= 1
    failed_jobs: dict[str, int] = {}
    if event == 1:  # success
        gate.release_all()
        settle(win)
        assert len(w.ok) == gate.entered  # every job delivered exactly once
        assert len(w.sets) == 1  # ... but only the newest generation was applied
        assert_real_downsample(plot)
        assert not w.render_failed
    elif event == 2:  # the compute function raises
        gate.fail = lambda i: True
        gate.release_all()
        settle(win)
        assert len(w.ok) == 0 and len(w.failed_cb) == gate.entered  # failure callbacks once per job
        assert len(w.sets) == 0
        assert len(w.render_failed) == 1  # only the newest generation is reported (28.2)
        assert "A2 injected failure" in w.render_failed[0][1]
        assert "a" in win._notice_label.text()
        failed_jobs = {"downsample": gate.entered}  # one `job fail` record per failed job
    elif event == 3:  # a newer request (zoom) supersedes the held one
        w.fresh["on"] = True
        before = gate.entered
        plot.setXRange(2000.0, 8000.0, padding=0)
        wait_held(win, gate, minimum=before)  # (a short zoomed range needs no lttb: not every job enters the gate)
        gate.release_all()
        settle(win)
        assert len(w.ok) >= gate.entered  # every job delivered; the zoom's own jobs may not use the gate
        assert len(w.sets) == 1  # the stale ones were dropped
        assert_real_downsample(plot)
        x, _y = curve(plot)
        assert x.min() >= 1500.0 and x.max() <= 8500.0  # the zoomed range, not the first draw's
        assert not w.render_failed
    elif event == 4:  # the series is removed
        plot.remove_series("a")
        gate.release_all()
        settle(win)
        assert len(w.sets) == 0 and "a" not in plot._series
        assert len(w.ok) == gate.entered
        assert not w.render_failed
    elif event == 5:  # another file is opened
        assert win.load_parquet(w.files[1])
        gate.release_all()
        settle(win)
        assert len(w.sets) == 0 and not plot._series
        assert len(w.ok) == gate.entered
        assert not w.render_failed
    else:  # 6: the time axis is switched
        w.fresh["on"] = True
        before = gate.entered
        win.set_time_column("t2")
        wait_held(win, gate, minimum=before + 1)
        gate.release_all()
        settle(win)
        assert len(w.sets) == 1
        assert_real_downsample(plot)
        x, _y = curve(plot)
        assert x.max() > N_ROWS  # t2 runs to ~40000; the old axis ended at 20000
        assert not w.render_failed
    assert_clean_log(failed_jobs=failed_jobs)


def test_downsample_remove_plot_with_job_in_flight(d_world):
    """D-4 (plot variant): removing the whole plot while its job is held must neither crash nor
    leave the plot's hub with a reference; the result is dropped."""
    w = d_world("lttb")
    win, gate = w.win, w.gate
    plot2 = win.plot_grid.add_plot()
    w.owned.append(plot2)
    w.fresh["on"] = True
    win._on_variable_dropped(plot2, "a")
    wait_held(win, gate)
    win.plot_grid.remove_plot(plot2)
    assert plot2 not in win.plot_grid.plots
    gate.release_all()
    settle(win, extra=lambda: not plot2._hub.pending)
    assert plot2 not in win.plot_grid._panes
    # (the orphan plot's own entry may still receive its result: invisible, deleted with the pane)
    assert not w.render_failed
    assert_clean_log()


def test_downsample_stale_job_of_previous_file_with_same_series_name(d_world):
    """D-5 (same name): a held job of the previous file's series "a" must not draw into the new
    file's series "a", even when it is delivered after the new series' own result and the
    per-series generation counters happen to coincide."""
    w = d_world("lttb")
    win, plot, gate = w.win, w.plot, w.gate
    win._on_variable_dropped(plot, "a")  # file one: jobs 0.. parked (stale-at-entry -> sentinel)
    wait_held(win, gate)
    old_jobs = gate.entered
    assert win.load_parquet(w.files[1])
    w.fresh["on"] = True
    win._on_variable_dropped(plot, "a")  # file two: new jobs
    w.sets = watch_curve(plot)
    wait_held(win, gate, minimum=old_jobs + 1)
    for i in range(old_jobs, gate.entered):  # the new file's jobs finish first ...
        gate.release(i)
    assert wait_until(lambda: len(w.sets) >= 1, SETTLE_TIMEOUT)
    assert_real_downsample(plot)
    gate.release_all()  # ... the previous file's jobs are delivered afterwards
    settle(win)
    assert not is_sentinel(plot), "a stale result of the previous file was drawn"
    assert len(w.sets) == 1
    assert_clean_log()


def test_downsample_stale_job_of_removed_series_with_same_name(qapp, isolated_config, make_gate, monkeypatch):
    """D (28.4, plot only): a series is removed and re-added under the same name while a job of
    the first one is held. Generation numbers come from one plot-wide source, so the held result
    never matches the new entry (whose own first job would get the same number under per-series
    counters) and is not drawn; the busy pair stays balanced."""
    gate = make_gate(plot_widget_module.lttb)
    monkeypatch.setattr(plot_widget_module, "lttb", gate)
    plot = TimePlotWidget("p_gen")
    plot.resize(400, 200)
    started, finished = [], []
    plot.downsampleStarted.connect(lambda: started.append(1))
    plot.downsampleFinished.connect(lambda: finished.append(1))
    n = 200_000
    x = np.arange(n, dtype=float)
    plot.add_series("a", x, np.zeros(n))
    plot.getViewBox().setXRange(0.0, float(n), padding=0)
    plot._redraw_debounce.stop()
    plot._redraw_series("a")  # old job (index 0), held
    assert wait_until(lambda: gate.entered >= 1, SETTLE_TIMEOUT)
    plot.remove_series("a")
    plot.add_series("a", x, np.full(n, 5.0))
    plot._redraw_debounce.stop()
    plot._redraw_series("a")  # the new series' latest job, held (add_series may have started one too)
    assert wait_until(lambda: gate.entered >= 2, SETTLE_TIMEOUT)
    time.sleep(0.1)
    QApplication.processEvents()
    entered = gate.entered
    drawn = []
    curve = plot._series["a"]["curve"]
    real = curve.setData
    curve.setData = lambda *a, **k: (drawn.append(a), real(*a, **k))[1]
    for i in range(1, entered):  # the new series' jobs finish first ...
        gate.release(i)
    assert wait_until(lambda: len(drawn) >= 1 and len(finished) == entered - 1, SETTLE_TIMEOUT)
    gate.release(0)  # ... then the old one is delivered
    assert wait_until(lambda: len(finished) == entered, SETTLE_TIMEOUT)
    for _ in range(20):
        time.sleep(0.01)
        QApplication.processEvents()
    assert len(started) == len(finished) == entered
    assert len(drawn) == 1 and float(np.max(drawn[0][1])) == 5.0, "a stale result was drawn"


# ===================================================================== P: pyramid build


@pytest.fixture
def p_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    """Windowed file, wide view (> the lowered threshold), so dropping "a" starts a pyramid build
    (parked in a gate) next to a free-running thin first draw."""
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    gate = make_gate(column_module.build_pyramid)
    monkeypatch.setattr(column_module, "build_pyramid", gate)
    win = open_window(two_files[0], windowed=True)
    plot = win.plot_grid.plots[0]
    w = World(
        win=win, plot=plot, gate=gate, files=two_files,
        ready=Spy(monkeypatch, TimePlotWidget, "_on_pyramid_ready"),
        failed_cb=Spy(monkeypatch, TimePlotWidget, "_on_pyramid_failed"),
        render_failed=[], owned=[],
    )
    for spy in (w.ready, w.failed_cb):
        spy.scope = lambda p, w=w: p.window() is w.win or p in w.owned
    plot.renderFailed.connect(lambda p, n, m: w.render_failed.append((n, m)))
    yield w
    gate.release_all()
    win.close()


@pytest.mark.parametrize("event", range(1, 7), ids=[f"P-{i}" for i in range(1, 7)])
def test_pyramid_paths(p_world, event):
    w = p_world
    win, plot, gate = w.win, w.plot, w.gate
    col = win._raw_variables["a"]
    win._on_variable_dropped(plot, "a")
    wait_held(win, gate)
    assert gate.entered == 1 and col._pyramid_building
    failed_jobs: dict[str, int] = {}
    if event == 1:
        gate.release_all()
        settle(win)
        assert len(w.ready) == 1 and col.pyramid is not None and not col._pyramid_building
        assert not w.render_failed
    elif event == 2:
        gate.fail = lambda i: True
        gate.release_all()
        settle(win)
        assert len(w.failed_cb) == 1 and len(w.ready) == 0
        assert col.pyramid is None and not col._pyramid_building and col._pyramid_failures == 1
        assert gate.entered == 1  # nothing retried behind our back
        assert curve(plot)[0].size > 0  # the thin first draw stays on screen
        failed_jobs = {"pyramid": 1}
    elif event == 3:  # a new request (zoom) while the build is running: no second build
        plot.setXRange(9000.0, 12000.0, padding=0)
        wait_held(win, gate)
        gate.release_all()
        settle(win)
        assert gate.entered == 1 and len(w.ready) == 1
        x, _y = curve(plot)
        assert x.size > 0 and x.min() >= 8192.0  # redrawn from the pyramid for the *current* range
        assert not w.render_failed
    elif event == 4:  # the series is removed
        plot.remove_series("a")
        gate.release_all()
        settle(win)
        assert len(w.ready) == 1 and col.pyramid is not None  # kept on the column
        assert "a" not in plot._series
        assert not w.render_failed
    elif event == 5:  # another file is opened
        assert win.load_parquet(w.files[1])
        gate.release_all()
        settle(win)
        assert len(w.ready) == 1
        assert not plot._series
        new_col = win._raw_variables["a"]
        assert new_col is not col and new_col.pyramid is None and not new_col._pyramid_building
        assert not w.render_failed
    else:  # 6: the time axis is switched
        win.set_time_column("t2")
        wait_held(win, gate)
        gate.release_all()
        settle(win)
        assert len(w.ready) == 1 and col.pyramid is not None
        x, _y = curve(plot)
        assert x.size > 0 and x.max() > N_ROWS  # drawn on t2 (runs to ~40000), not on the old axis
        assert not w.render_failed
    assert_clean_log(failed_jobs=failed_jobs)


def test_pyramid_remove_plot_with_build_in_flight(p_world):
    """P-4 (plot variant): the plot is removed while its build runs; the result is delivered
    once, the column keeps the pyramid, nothing crashes."""
    w = p_world
    win, gate = w.win, w.gate
    plot2 = win.plot_grid.add_plot()
    w.owned.append(plot2)
    col = win._raw_variables["a"]
    win._on_variable_dropped(plot2, "a")
    wait_held(win, gate)
    win.plot_grid.remove_plot(plot2)
    gate.release_all()
    settle(win, extra=lambda: not plot2._hub.pending)
    assert len(w.ready) == 1 and col.pyramid is not None
    assert not w.render_failed
    assert_clean_log()


# ===================================================================== N: navigator overview


def nav_has_sentinel(w):
    return any(np.any(np.asarray(c[1], dtype=float) == SENTINEL_Y) for c in w.set_calls.calls)


@pytest.fixture
def n_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    """kind "raw": windowed file with few row groups; the thinned refinement read
    (`navigator_sync.thin_column`) is parked, and starts already when the file is opened.
    kind "derived": a windowed derived series; the navigator waits for its pyramid
    (`build_pyramid_from_windows`, parked)."""
    worlds: list[World] = []

    def build(kind, fresh_from_start):
        fresh = {"on": fresh_from_start}
        if kind == "raw":
            gate = tagged_gate(make_gate, monkeypatch, ns_module, "thin_column", fresh,
                               wrap_job=False, sentinel=sentinel_idx_y)
        else:
            gate = make_gate(column_module.build_pyramid_from_windows)
            monkeypatch.setattr(column_module, "build_pyramid_from_windows", gate)
            monkeypatch.setattr(ns_module, "_DERIVED_NAV_DIRECT_ROWS", 0)
        w = World(
            gate=gate, fresh=fresh, files=two_files, kind=kind,
            set_calls=Spy(monkeypatch, NavigatorWidget, "set_overview_data"),
            ready=Spy(monkeypatch, NavigatorSync, "_on_navigator_overview_ready"),
            failed_cb=Spy(monkeypatch, NavigatorSync, "_on_navigator_overview_failed"),
            built=Spy(monkeypatch, NavigatorSync, "_on_derived_overview_built"),
            derived_failed=Spy(monkeypatch, NavigatorSync, "_on_derived_overview_failed"),
        )
        w.win = open_window(two_files[0], windowed=True)
        w.plot = w.win.plot_grid.plots[0]
        w.set_calls.scope = lambda this, w=w: this is w.win.navigator
        for spy in (w.ready, w.failed_cb, w.built, w.derived_failed):
            spy.scope = lambda this, w=w: this is w.win._nav_sync
        if kind == "derived":
            w.win._on_add_derived_variable("d", "a + b")
            w.win._on_variable_dropped(w.plot, "d")
        worlds.append(w)
        return w

    yield build
    for w in worlds:
        w.gate.release_all()
        w.win.close()


N_CASES = [(kind, ev) for kind in ("raw", "derived") for ev in range(1, 7)]
N_IDS = [f"N-{ev}" + ("-derived" if kind == "derived" else "") for kind, ev in N_CASES]


@pytest.mark.parametrize(("kind", "event"), N_CASES, ids=N_IDS)
def test_navigator_overview_paths(n_world, caplog, kind, event):
    w = n_world(kind, fresh_from_start=event in (1, 2))
    win, gate = w.win, w.gate
    wait_held(win, gate)
    assert gate.entered >= 1 and "navigator" in win._busy_sources
    nav_job = "navigator_overview"
    failed_jobs: dict[str, int] = {}
    if event == 1:
        gate.release_all()
        settle(win)
        if kind == "raw":
            assert len(w.ready) == gate.entered
            assert not nav_has_sentinel(w)
        else:
            assert len(w.built) == gate.entered and win._raw_variables["d"].pyramid is not None
    elif event == 2:
        gate.fail = lambda i: True
        with caplog.at_level("WARNING"):
            gate.release_all()
            settle(win)
        warned = [r for r in caplog.records if "navigator overview" in r.getMessage()]
        if kind == "raw":
            assert len(w.failed_cb) == gate.entered and len(warned) == gate.entered
        else:
            assert len(w.derived_failed) == gate.entered and len(warned) == gate.entered
        assert len(win.navigator._curve.getData()[0]) > 0  # the coarse overview stays
        failed_jobs = {nav_job: gate.entered}
    elif event == 3:  # a newer refresh supersedes the held request
        w.fresh["on"] = True
        before = gate.entered
        win._nav_sync.refresh()
        if kind == "raw":
            wait_held(win, gate, minimum=before + 1)
        gate.release_all()
        settle(win)
        assert not nav_has_sentinel(w)
        if kind == "raw":
            assert len(w.ready) == gate.entered
    elif event == 4:  # the plotted series that drives the overview is removed
        w.fresh["on"] = True
        if w.plot.series_names():
            w.plot.remove_series(w.plot.series_names()[0])
        win._nav_sync.refresh()
        gate.release_all()
        settle(win)
        assert not nav_has_sentinel(w)
    elif event == 5:  # another file (windowed too)
        w.fresh["on"] = True
        assert win.load_parquet(w.files[1])
        gate.release_all()
        settle(win)
        assert not nav_has_sentinel(w)
    else:  # 6: time axis switch
        w.fresh["on"] = True
        win.set_time_column("t2")
        gate.release_all()
        settle(win)
        assert not nav_has_sentinel(w)
    assert "navigator" not in win._busy_sources
    assert_clean_log(failed_jobs=failed_jobs)


def test_navigator_stale_refinement_when_next_file_is_eager(n_world):
    """N-5 (eager next file)."""
    w = n_world("raw", fresh_from_start=False)
    win, gate = w.win, w.gate
    wait_held(win, gate)
    win.settings.eager_load_limit_mb = 1000  # the next file is loaded resident
    assert win.load_parquet(w.files[1])
    gate.release_all()
    assert wait_until(lambda: pending(win) == 0, SETTLE_TIMEOUT)  # the stale delivery has happened
    wait_until(lambda: "navigator" not in win._busy_sources, 1.0)
    assert "navigator" not in win._busy_sources, "the progress bar would stay on"


def test_navigator_busy_flag_clears_when_nothing_can_be_drawn(n_world):
    """N (28.4): a held refinement and a refresh that returns early (no time values) must not
    leave the 'navigator' busy flag on."""
    w = n_world("raw", fresh_from_start=False)
    win, gate = w.win, w.gate
    wait_held(win, gate)
    assert "navigator" in win._busy_sources
    win._nav_sync._time_values = lambda: None
    win._nav_sync.refresh()
    gate.release_all()
    assert wait_until(lambda: pending(win) == 0, SETTLE_TIMEOUT)
    wait_until(lambda: "navigator" not in win._busy_sources, 1.0)
    assert "navigator" not in win._busy_sources, "the progress bar would stay on"


def test_navigator_busy_flag_clears_on_time_axis_switch_to_resident_columns(n_world):
    """N (28.4): the time axis is switched while a refinement is held and the redraw takes the
    resident (non-windowed) path."""
    w = n_world("raw", fresh_from_start=False)
    win, gate = w.win, w.gate
    wait_held(win, gate)
    win.settings.eager_load_limit_mb = 1000
    assert win.load_parquet(w.files[0])  # same file again, now resident
    win.set_time_column("t2")
    gate.release_all()
    assert wait_until(lambda: pending(win) == 0, SETTLE_TIMEOUT)
    wait_until(lambda: "navigator" not in win._busy_sources, 1.0)
    assert "navigator" not in win._busy_sources, "the progress bar would stay on"


# ===================================================================== S: statistics


def select_vars(win, *names):
    win.variable_panel.clearSelection()
    for name in names:
        win.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)[0].setSelected(True)


def stats_dialogs(win):
    return win.findChildren(StatsDialog)


def stats_rows(dialog):
    table = dialog.findChild(QTableWidget)
    return {table.item(r, 0).text(): table.item(r, 1).text() for r in range(table.rowCount())}


@pytest.fixture
def s_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    gate = make_gate(main_window_module.basic_stats)
    monkeypatch.setattr(main_window_module, "basic_stats", gate)
    win = open_window(two_files[0])
    w = World(win=win, gate=gate, files=two_files,
              finished=Spy(monkeypatch, MainWindow, "_on_stats_finished", scope=lambda this: this is win))
    yield w
    gate.release_all()
    win.close()


@pytest.mark.parametrize("event", [1, 2, 3, 6], ids=[f"S-{i}" for i in (1, 2, 3, 6)])
def test_stats_paths(s_world, event):
    w = s_world
    win, gate = w.win, w.gate
    select_vars(win, "a", "b")
    win._on_stats_requested()
    wait_held(win, gate, minimum=2)
    assert "stats" in win._busy_sources and len(win._stats_hub.pending) == 2
    failed_jobs: dict[str, int] = {}
    if event == 1:
        gate.release_all()
        settle(win)
        assert len(w.finished) == 2 and len(stats_dialogs(win)) == 1
        assert stats_rows(stats_dialogs(win)[0]) == {"a": str(N_ROWS), "b": str(N_ROWS)}
    elif event == 2:  # exactly one of the two variables fails
        gate.fail = lambda i: i == 0
        gate.release_all()
        settle(win)
        assert len(w.finished) == 2 and len(stats_dialogs(win)) == 1
        rows = stats_rows(stats_dialogs(win)[0])
        assert sorted(v == str(N_ROWS) for v in rows.values()) == [False, True]
        assert sum("A2 injected failure" in v for v in rows.values()) == 1
        failed_jobs = {"stats": 1}
    elif event == 3:  # a second request while the first is held: independent, both answered once
        select_vars(win, "b")
        win._on_stats_requested()
        wait_held(win, gate, minimum=3)
        gate.release_all()
        settle(win)
        assert len(w.finished) == 3 and len(stats_dialogs(win)) == 2
    else:  # 6: the time axis is switched: the answer is the request-time snapshot, shown once
        win.set_time_column("t2")
        gate.release_all()
        settle(win)
        assert len(w.finished) == 2 and len(stats_dialogs(win)) == 1
        assert stats_rows(stats_dialogs(win)[0]) == {"a": str(N_ROWS), "b": str(N_ROWS)}
    assert not win._stats_hub.pending
    assert_clean_log(failed_jobs=failed_jobs)


def test_stats_result_of_previous_file_is_not_shown(s_world):
    """S-5."""
    w = s_world
    win, gate = w.win, w.gate
    select_vars(win, "a", "b")
    win._on_stats_requested()
    wait_held(win, gate, minimum=2)
    assert win.load_parquet(w.files[1])
    gate.release_all()
    settle(win)
    assert not win._stats_hub.pending
    assert stats_dialogs(win) == []
    assert "stats" not in win._busy_sources


def test_stats_error_of_previous_file_is_dropped_silently(s_world):
    """S-5 (failure variant): a {"error"} answer for the previous file shows no dialog either."""
    w = s_world
    win, gate = w.win, w.gate
    select_vars(win, "a", "b")
    win._on_stats_requested()
    wait_held(win, gate, minimum=2)
    assert win.load_parquet(w.files[1])
    gate.fail = lambda i: True
    gate.release_all()
    settle(win)
    assert stats_dialogs(win) == []
    assert "stats" not in win._busy_sources and not win._stats_hub.pending


def test_stats_result_shown_when_opening_another_file_fails(s_world, monkeypatch):
    """A load that fails keeps the old file, so the generation is unchanged and the result is shown."""
    w = s_world
    win, gate = w.win, w.gate
    crits = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: crits.append(a)))
    select_vars(win, "a", "b")
    win._on_stats_requested()
    wait_held(win, gate, minimum=2)
    gen = win._file_generation
    bad = Path(w.files[1]).parent / "broken.parquet"
    bad.write_bytes(b"not a parquet file")
    assert not win.load_parquet(str(bad))
    assert crits and win._file_generation == gen
    gate.release_all()
    settle(win)
    assert len(stats_dialogs(win)) == 1


def test_file_generation_bumps_on_successful_load(s_world):
    win = s_world.win
    gen = win._file_generation
    assert win.load_parquet(s_world.files[1])
    assert win._file_generation == gen + 1


# ===================================================================== F: FFT


@pytest.fixture
def f_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    gate = make_gate(main_window_module.fft)
    monkeypatch.setattr(main_window_module, "fft", gate)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    win = open_window(two_files[0])
    w = World(win=win, gate=gate, files=two_files, warnings=warnings,
              finished=Spy(monkeypatch, MainWindow, "_on_fft_finished", scope=lambda this: this is win),
              failed_cb=Spy(monkeypatch, MainWindow, "_on_fft_failed", scope=lambda this: this is win))
    yield w
    gate.release_all()
    win.close()


@pytest.mark.parametrize("event", [1, 2, 3, 6], ids=[f"F-{i}" for i in (1, 2, 3, 6)])
def test_fft_paths(f_world, event):
    w = f_world
    win, gate = w.win, w.gate
    n_plots = len(win.plot_grid.plots)
    select_vars(win, "a")
    win._on_fft_requested()
    wait_held(win, gate)
    assert gate.entered == 1 and "fft" in win._busy_sources
    failed_jobs: dict[str, int] = {}
    if event == 1:
        gate.release_all()
        settle(win)
        assert len(w.finished) == 1 and len(w.failed_cb) == 0
        assert len(win.plot_grid.plots) == n_plots + 1
    elif event == 2:
        gate.fail = lambda i: True
        gate.release_all()
        settle(win)
        assert len(w.failed_cb) == 1 and len(w.finished) == 0
        assert len(w.warnings) == 1 and "A2 injected failure" in w.warnings[0]  # told once
        assert len(win.plot_grid.plots) == n_plots
        failed_jobs = {"fft": 1}
    elif event == 3:  # a second request while the first is held: both answered once
        win._on_fft_requested()
        wait_held(win, gate, minimum=2)
        gate.release_all()
        settle(win)
        assert len(w.finished) == 2 and len(win.plot_grid.plots) == n_plots + 2
    else:  # 6: the time axis is switched: the spectrum is a snapshot of the request, delivered once
        win.set_time_column("t2")
        gate.release_all()
        settle(win)
        assert len(w.finished) == 1 and len(win.plot_grid.plots) == n_plots + 1
    assert not win._fft_hub.pending
    assert_clean_log(failed_jobs=failed_jobs)


def test_fft_result_of_previous_file_is_not_added(f_world):
    """F-5."""
    w = f_world
    win, gate = w.win, w.gate
    select_vars(win, "a")
    win._on_fft_requested()
    wait_held(win, gate)
    assert win.load_parquet(w.files[1])
    n_plots = len(win.plot_grid.plots)
    gate.release_all()
    settle(win)
    assert not win._fft_hub.pending
    assert len(win.plot_grid.plots) == n_plots
    assert "fft" not in win._busy_sources


def test_fft_failure_of_previous_file_shows_no_warning(f_world):
    """F-5 (failure variant): an exception from the previous file's job is not reported."""
    w = f_world
    win, gate = w.win, w.gate
    select_vars(win, "a")
    win._on_fft_requested()
    wait_held(win, gate)
    assert win.load_parquet(w.files[1])
    n_plots = len(win.plot_grid.plots)
    gate.fail = lambda i: True
    gate.release_all()
    settle(win)
    assert w.warnings == []
    assert len(win.plot_grid.plots) == n_plots
    assert "fft" not in win._busy_sources and not win._fft_hub.pending


def test_fft_result_shown_when_opening_another_file_fails(f_world, monkeypatch):
    w = f_world
    win, gate = w.win, w.gate
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    select_vars(win, "a")
    win._on_fft_requested()
    wait_held(win, gate)
    n_plots = len(win.plot_grid.plots)
    bad = Path(w.files[1]).parent / "broken.parquet"
    bad.write_bytes(b"not a parquet file")
    assert not win.load_parquet(str(bad))
    gate.release_all()
    settle(win)
    assert len(win.plot_grid.plots) == n_plots + 1


# ===================================================================== A: analysis windows (XY)


@pytest.fixture
def a_world(qapp, isolated_config, log, two_files, monkeypatch, make_gate):
    gate = make_gate(xy_plot_window.compute_xy)
    monkeypatch.setattr(xy_plot_window, "compute_xy", gate)
    win = open_window(two_files[0])
    select_vars(win, "a", "b")  # x = a, y = b: opening the window computes at once
    w = World(win=win, gate=gate, files=two_files,
              result=Spy(monkeypatch, XYPlotWindow, "on_result", scope=lambda this: this._main is win),
              failure=Spy(monkeypatch, AnalysisWindow, "on_failure", scope=lambda this: this._main is win))
    w.xy = win.open_xy_plot()
    yield w
    gate.release_all()
    for window in list(win._analysis_windows):
        window.close()
    win.close()


@pytest.mark.parametrize("event", range(1, 7), ids=[f"A-{i}" for i in range(1, 7)])
def test_analysis_window_paths(a_world, event):
    w = a_world
    win, gate, xy = w.win, w.gate, w.xy
    wait_held(win, gate)
    assert gate.entered == 1 and xy.is_computing() and len(win._analysis_hub.pending) == 1
    failed_jobs: dict[str, int] = {}
    if event == 1:
        gate.release_all()
        settle(win, extra=lambda: not xy.is_computing())
        assert len(w.result) == 1 and len(w.failure) == 0
        assert xy._generation == 1
    elif event == 2:
        gate.fail = lambda i: True
        gate.release_all()
        settle(win, extra=lambda: not xy.is_computing())
        assert len(w.failure) == 1 and len(w.result) == 0
        assert "A2 injected failure" in xy.status_text()
        failed_jobs = {"analysis": 1}
    elif event == 3:  # a recompute supersedes the held one
        xy.recompute()
        wait_held(win, gate, minimum=2)
        gate.release_all()
        settle(win, extra=lambda: not xy.is_computing())
        assert len(w.result) == 1 and len(w.failure) == 0  # the stale result was dropped
        assert xy._generation == 2
    else:
        if event == 4:  # the window is closed
            xy.close()
        elif event == 5:  # another file is opened (closes the windows, 25.1)
            assert win.load_parquet(w.files[1])
        else:  # 6: the time axis is switched (closes the windows, 25.1)
            win.set_time_column("t2")
        assert xy not in win._analysis_windows
        gate.release_all()
        settle(win)
        assert len(w.result) == 0 and len(w.failure) == 0
    assert not win._analysis_hub.pending
    assert_clean_log(failed_jobs=failed_jobs)
