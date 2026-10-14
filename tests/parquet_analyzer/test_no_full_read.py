"""Watchdog: ordinary operations on windowed columns never read a whole column (app 46.6 (2), A3).

With `eager_load_limit_mb = 0` every column is a `WindowedColumn` and the time axis a `TimeIndex`.
For each everyday operation (open+draw, zoom/pan, navigator drag, hover, cursors, time-axis switch,
windowed derived variables) we count

  * `WindowedColumn.values` and `DerivedColumn.values` calls, and
  * `ParquetDataSource.read_window` calls covering more than half of the rows,

from any thread (lock-protected), keeping the caller stack of each so a failure says who did it.

Chunked scans of a derived column (`DerivedColumn.scan` / `bounds`, `build_pyramid_from_windows`) do
read every row, but chunk by chunk (the column is never resident as a whole); on this small test
file one chunk would span the whole file, so reads made from inside those functions are recorded
as "chunked" and not counted (46.6, operation 7).

Stats of a windowed column are aggregated per window in the worker (data 53; operations 8 and 9):
no row outside the visible range is read, and with small chunks no single read covers more than
half of the rows.

Not watched (they read everything today; reducing that is dev notes 29 stage 2): expressions that do
not become a `DerivedColumn` (unknown margin, some user functions), histogram / XY / FFT, image /
data export. The sanity test at the bottom proves the counter does count a deliberate full read
(`WindowedColumn.values()`).
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from waiting import wait_until

from parquet_analyzer.core.column import DerivedColumn, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.time_index import TimeIndex
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui import messages
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui.main_window import MainWindow

N = 300_000  # three 100,000-row blocks of the default block size
_CHUNKED_FUNCS = {"build_pyramid_from_windows", "scan", "bounds"}


class ReadCounter:
    """Thread-safe record of whole-column reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hits: list[tuple[str, int, str]] = []
        self.chunked = 0
        self.col_reads: list[tuple[int, int, tuple[str, ...]]] = []  # (start, end, columns), every read_columns

    def reset(self) -> None:
        with self._lock:
            self.hits.clear()
            self.col_reads.clear()
            self.chunked = 0

    def record(self, kind: str, rows: int, exempt_chunked: bool) -> None:
        if exempt_chunked and _called_from_chunked_scan():
            with self._lock:
                self.chunked += 1
            return
        stack = "".join(
            f"      {f.filename.split('parquet_analyzer')[-1]}:{f.lineno} {f.name}\n"
            for f in traceback.extract_stack()[:-2]
            if "parquet_analyzer" in f.filename
        )
        with self._lock:
            self.hits.append((kind, rows, f"[{threading.current_thread().name}]\n{stack}"))

    def report(self) -> str:
        with self._lock:
            return "\n".join(f"  {kind} rows={rows}\n{stack}" for kind, rows, stack in self.hits)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.hits)


def _called_from_chunked_scan() -> bool:
    f = sys._getframe(2)
    while f is not None:
        if f.f_code.co_name in _CHUNKED_FUNCS and "core" in f.f_code.co_filename:
            return True
        f = f.f_back
    return False


@pytest.fixture
def counter(monkeypatch):
    c = ReadCounter()
    orig_w, orig_d, orig_rw = WindowedColumn.values, DerivedColumn.values, ParquetDataSource.read_window

    def w_values(self):
        c.record("WindowedColumn.values", -1, False)
        return orig_w(self)

    def d_values(self):
        c.record("DerivedColumn.values", -1, False)
        return orig_d(self)

    def read_window(self, columns, row_start, row_end):
        total = self.row_count()
        span = min(total, row_end) - max(0, row_start)
        if span > total // 2:
            c.record("read_window", span, True)
        return orig_rw(self, columns, row_start, row_end)

    orig_rc = ParquetDataSource.read_columns

    def read_columns(self, columns, row_start=0, row_end=None):
        c.col_reads.append((row_start, row_end if row_end is not None else self.row_count(), tuple(columns)))
        return orig_rc(self, columns, row_start, row_end)

    monkeypatch.setattr(ParquetDataSource, "read_columns", read_columns)
    monkeypatch.setattr(WindowedColumn, "values", w_values)
    monkeypatch.setattr(DerivedColumn, "values", d_values)
    monkeypatch.setattr(ParquetDataSource, "read_window", read_window)
    return c


@pytest.fixture(scope="module")
def big_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("nofull") / "big.parquet"
    rng = np.random.default_rng(5)
    base = np.datetime64("2026-01-01", "ms") + np.arange(N, dtype="int64") * np.timedelta64(10, "ms")
    pq.write_table(
        pa.table({
            "time": pa.array(base),
            "t2": pa.array(base + np.timedelta64(1, "h")),
            "a": np.sin(np.arange(N) / 500.0) + rng.normal(size=N) * 0.1,
            "b": np.cumsum(rng.normal(size=N)),
        }),
        path,
        row_group_size=50_000,
    )
    return path


def _settle(win, timeout: float = 60.0) -> None:
    """Wait (real time) until every background source is idle and the debounce timers are done."""

    def idle() -> bool:
        if win._busy_sources:
            return False
        if any(p.pending_downsample_count() or p._redraw_debounce.isActive() for p in win.plot_grid.plots):
            return False
        return not win._nav_sync._hub.pending

    for _ in range(2):  # a finished job can start the next (pyramid -> redraw -> ...)
        assert wait_until(idle, timeout), f"background work did not settle: {win._busy_sources}"
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
    assert wait_until(idle, timeout)


@pytest.fixture
def win(qapp, isolated_config, counter, big_path, monkeypatch):
    # The app draws a visible range of up to 5M rows with one direct read (a wide range is only
    # served from the pyramid above that, plot_widget._PYRAMID_PREFERRED_ROW_THRESHOLD), so on this
    # small file the "whole file visible" case would legitimately read everything. Lower the
    # threshold so the 300k-row file behaves like a big one (the regime this watchdog is about).
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 50_000)
    for fn in ("information", "warning", "critical", "question"):  # no modal dialog may block the run
        monkeypatch.setattr(messages, fn, lambda *a, **k: None, raising=False)
    w = MainWindow()
    w.settings.eager_load_limit_mb = 0
    w.resize(1000, 800)
    w.show()
    qapp.processEvents()
    counter.reset()
    w._watchdog_counter = counter
    assert w.load_parquet(str(big_path))
    assert isinstance(w._raw_variables["a"], WindowedColumn)
    assert isinstance(w._time_values, TimeIndex)
    plot = w.plot_grid.plots[0]
    w.plot_grid.add_plot()
    w._add_variable_to_plot(plot, "a")
    w._add_variable_to_plot(w.plot_grid.plots[1], "b")
    _settle(w)
    yield w
    w.close()


def _zoom(win, frac: float = 0.01, at: float = 0.5) -> tuple[float, float]:
    lo, hi = win.plot_grid.anchor_plot().getViewBox().viewRange()[0]
    w = (hi - lo) * frac
    c = lo + (hi - lo) * at
    win.plot_grid.set_x_range(c - w / 2, c + w / 2)
    _settle(win)
    return win.plot_grid.anchor_plot().getViewBox().viewRange()[0]


def _move_to(plot, x: float) -> None:
    """A real mouse move over the viewport at data X (Qt routing; see test_hover_readout)."""
    vb = plot.getViewBox()
    rect = vb.sceneBoundingRect()
    pt = vb.mapViewToScene(QPointF(x, 0.0))
    pt.setY(rect.top() + rect.height() * 0.5)
    vp = plot.viewport()
    local = QPointF(plot.mapFromScene(pt))
    ev = QMouseEvent(
        QEvent.Type.MouseMove, local, vp.mapToGlobal(local), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(vp, ev)
    QApplication.processEvents()


def _drag_navigator(win) -> None:
    nav = win.navigator
    nav.resize(800, 100)
    QApplication.processEvents()
    lo, hi = nav.selected_range()
    p0 = nav.mapFromScene(nav.getViewBox().mapViewToScene(QPointF((lo + hi) / 2, 0)))
    p0.setY(nav.height() // 2)
    vp = nav.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
    for dx in (5, 15, 30):
        QTest.mouseMove(vp, p0 + QPointF(dx, 0).toPoint())
        QApplication.processEvents()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0 + QPointF(30, 0).toPoint())
    QApplication.processEvents()


# ---------------------------------------------------------------- the operations (one test each)


def op_open_and_draw(win):
    pass  # the fixture opened the file and drew two series, with the counter installed from the start


def op_zoom_and_pan(win):
    lo, hi = _zoom(win)
    width = hi - lo
    for k in range(1, 6):  # pan right, then back left
        win.plot_grid.set_x_range(lo + k * width / 2, hi + k * width / 2)
        _settle(win)
    win.plot_grid.set_x_range(lo, hi)
    _settle(win)
    _zoom(win, 4.0)  # and out again


def op_navigator_drag(win):
    before = _zoom(win)
    _drag_navigator(win)
    _settle(win)
    after = win.plot_grid.anchor_plot().getViewBox().viewRange()[0]
    assert abs(after[0] - before[0]) > 0  # the drag really moved the window


def op_hover(win):
    _zoom(win, 0.02)
    plot = win.plot_grid.plots[0]
    plot.set_hover_enabled(True)
    lo, hi = plot.getViewBox().viewRange()[0]
    _move_to(plot, lo + (hi - lo) * 0.4)
    assert plot.hover_x() is not None
    assert wait_until(lambda: "≈" not in plot.hover_text() and "a:" in plot.hover_text(), 10.0)  # exact read after the stop timer
    _move_to(plot, lo + (hi - lo) * 0.6)
    assert wait_until(lambda: "≈" not in plot.hover_text() and "a:" in plot.hover_text(), 10.0)


def op_cursor(win):
    lo, hi = _zoom(win, 0.02)
    plot = win.plot_grid.plots[0]
    win.plot_grid.set_cursor_mode_enabled(True)
    scene_pt = plot.getViewBox().mapViewToScene(QPointF(lo + (hi - lo) * 0.3, 0.0))
    scene_pt.setY(plot.getViewBox().sceneBoundingRect().center().y())
    QTest.mouseClick(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, plot.mapFromScene(scene_pt))
    QApplication.processEvents()
    assert win.plot_grid._cursor_positions, "the click did not place a cursor"
    cid = next(iter(win.plot_grid._cursor_positions))
    for f in (0.4, 0.5, 0.7):  # move it (the drag handler path)
        win.plot_grid._on_cursor_move_requested(plot, cid, lo + (hi - lo) * f)
        plot.set_cursor_x(cid, lo + (hi - lo) * f)
        QApplication.processEvents()
    _settle(win)


def op_time_axis_switch(win):
    win.set_time_column("t2")
    _settle(win)
    assert win._time_column == "t2"
    win.set_time_column("time")
    _settle(win)
    assert win._time_column == "time"


def op_windowed_derived(win):
    win._on_add_derived_variable("d_delta", "delta(a)")
    win._on_add_derived_variable("d_roll", "rolling_mean(a, 50)")
    for n in ("d_delta", "d_roll"):
        assert isinstance(win._raw_variables[n], DerivedColumn), n
        win._add_variable_to_plot(win.plot_grid.plots[0], n)
    _settle(win)
    _zoom(win, 0.05)


def _select(win, names) -> None:
    class _Item:
        def __init__(self, n):
            self._n = n

        def text(self):
            return self._n

    win.variable_panel.selectedItems = lambda: [_Item(n) for n in names]


def _run_stats(win, chunk_rows: int) -> None:
    """Press Stats for variable "a" with small chunks (this file is tiny) and wait for the answer."""
    orig = main_window_module.chunked_stats
    main_window_module.chunked_stats = lambda window, a, b: orig(window, a, b, chunk_rows=chunk_rows)
    try:
        _select(win, ["a"])
        win._on_stats_requested()
        assert wait_until(lambda: not win._stats_workers and "stats" not in win._busy_sources, 60.0)
    finally:
        main_window_module.chunked_stats = orig


def op_stats_partial_range(win):
    lo, hi = _zoom(win, 0.1)
    rs, re_ = win._visible_row_range()
    assert 0 < re_ - rs < N // 2
    win_counter = win._watchdog_counter
    win_counter.reset()
    _run_stats(win, 20_000)
    reads = [(s, e) for s, e, cols in win_counter.col_reads if cols == ("a",)]
    assert reads, "the stats worker did not read the column"
    outside = [(s, e) for s, e in reads if s < rs or e > re_]
    assert not outside, f"rows outside the visible range [{rs}, {re_}) were read: {outside}"


def op_stats_whole_range(win):
    win._watchdog_counter.reset()
    _run_stats(win, 40_000)
    reads = [(s, e) for s, e, cols in win._watchdog_counter.col_reads if cols == ("a",)]
    assert len(reads) >= 3 * (N // 40_000) - 3, "the whole range should be scanned in several chunks"
    assert max(e - s for s, e in reads) <= N // 2, f"a single read covered more than half: {max(e - s for s, e in reads)}"


OPERATIONS = {
    "open_and_draw": op_open_and_draw,
    "zoom_and_pan": op_zoom_and_pan,
    "navigator_drag": op_navigator_drag,
    "hover": op_hover,
    "cursor": op_cursor,
    "time_axis_switch": op_time_axis_switch,
    "windowed_derived": op_windowed_derived,
    "stats_partial_range": op_stats_partial_range,
    "stats_whole_range": op_stats_whole_range,
}


@pytest.mark.parametrize("op_name", list(OPERATIONS), ids=list(OPERATIONS))
def test_operation_never_reads_a_whole_column(win, counter, op_name):
    if op_name != "open_and_draw":
        counter.reset()  # the open itself is the first test's subject
    OPERATIONS[op_name](win)
    _settle(win)
    assert counter.count == 0, f"{op_name} read a whole column:\n{counter.report()}"


def test_counter_does_count_a_deliberate_full_read(win, counter):
    """The counter must see a deliberate full read (`WindowedColumn.values()`)."""
    counter.reset()
    win._raw_variables["a"].values()
    assert counter.count >= 1, "a full read was not counted: the counter is broken"
