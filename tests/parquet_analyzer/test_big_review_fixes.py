"""Review fixes for the large-data flags A (strided first draw), C (DerivedColumn) and
D (lazy TimeIndex), data ch.36.5-36.8: the UI-thread read policy (O(1) anchor lookups and a few
cached reads per discrete user action are fine; per-point reads and per-mouse-move reads are not),
the navigator re-sync, pyramid completion for every waiting plot, and the registry snapshot."""
from __future__ import annotations

import threading
import warnings
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import features
from parquet_analyzer.core import column as column_module
from parquet_analyzer.core.analysis import chunked_stats
from parquet_analyzer.core.column import DerivedColumn, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.core.time_index import TimeIndex
from parquet_analyzer.ui import main_window as mw_module
from parquet_analyzer.ui import plot_widget as pw_module
from parquet_analyzer.ui.main_window import MainWindow

N = 60_000
T0 = 1_767_225_600.0
DT = 0.01
FLAGS = ("big_a_thin_first_draw", "big_c_windowed_derived", "big_d_time_index")


@pytest.fixture
def path(tmp_path):
    t = T0 + np.arange(N) * DT  # exactly linear: a row can be recovered from its X
    a = np.sin(np.arange(N) / 50.0)
    b = np.cos(np.arange(N) / 70.0)
    p = tmp_path / "r.parquet"
    pq.write_table(pa.table({"time": t, "a": a, "b": b}), p, row_group_size=6_000)
    return str(p)


def _set_flags(monkeypatch, a: bool, c: bool, d: bool) -> None:
    for flag, on in zip(FLAGS, (a, c, d)):
        monkeypatch.setitem(features._EFFECTIVE, flag, on)


def _wait(qapp, win, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        if not win._busy_sources and not any(p._redraw_debounce.isActive() for p in win.plot_grid.plots):
            break
        time.sleep(0.003)
    qapp.processEvents()


def _open(qapp, path, monkeypatch):
    monkeypatch.setattr(pw_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 5_000)
    monkeypatch.setattr(mw_module, "_DERIVED_NAV_DIRECT_ROWS", 5_000)
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0
    assert win.load_parquet(path)
    win.resize(1000, 700)
    win.show()
    return win


class _UiCounter:
    """Counts calls of TimeIndex methods made on the UI (main) thread."""

    def __init__(self, monkeypatch, *names):
        self.main = threading.current_thread()
        self.calls = {n: 0 for n in names}
        for n in names:
            orig = getattr(TimeIndex, n)

            def wrapper(this, *a, _orig=orig, _n=n, **k):
                if threading.current_thread() is self.main:
                    self.calls[_n] += 1
                return _orig(this, *a, **k)

            monkeypatch.setattr(TimeIndex, n, wrapper)

    def reset(self):
        for n in self.calls:
            self.calls[n] = 0

    @property
    def total(self):
        return sum(self.calls.values())


# --- must-fix 1: windowed navigator overview from a built pyramid -------------------------


@pytest.mark.parametrize("flags", [(True, True, True), (False, False, False), (True, True, False)])
def test_navigator_overview_from_built_pyramid(qapp, isolated_config, monkeypatch, path, flags):
    _set_flags(monkeypatch, *flags)
    win = _open(qapp, path, monkeypatch)
    try:
        assert isinstance(win._time_values, TimeIndex) is flags[2]
        plot = win.plot_grid.plots[0]
        win._on_variable_dropped(plot, "a")
        col = win._raw_variables["a"]
        assert isinstance(col, WindowedColumn)
        end = time.time() + 15
        while col.pyramid is None and time.time() < end:  # the wide first view builds the pyramid
            _wait(qapp, win)
        assert col.pyramid is not None
        win._refresh_navigator_overview()  # NameError: lazy_t on the old code
        _wait(qapp, win)
        ox, oy = win.navigator._curve.getData()
        assert len(ox) > len(col.pyramid.row_starts) and np.isfinite(ox).all() and np.isfinite(oy).all()
        assert ox.min() == pytest.approx(T0) and ox.max() == pytest.approx(T0 + (N - 1) * DT)
        assert 0.9 < oy.max() <= 1.0 + 1e-9  # the real envelope, not a flat line
        # the same call without a pyramid (footer-stats branch): row-group starts, O(1) on a TimeIndex
        col._pyramid = None
        x, y = win._windowed_navigator_overview(col)
        assert np.isfinite(x).all() and len(x) == 2 * 10 + 2
    finally:
        win.close()


# --- must-fix 2: no per-point reads on the UI thread during the thin first draw ------------


def test_thin_first_draw_over_lazy_axis_has_no_per_point_ui_reads(qapp, isolated_config, monkeypatch, path):
    _set_flags(monkeypatch, True, False, True)
    counter = _UiCounter(monkeypatch, "window")  # every non-anchor lookup goes through window()
    win = _open(qapp, path, monkeypatch)
    try:
        monkeypatch.setattr(WindowedColumn, "request_pyramid", lambda self: None)  # keep the thin curve on screen
        plot = win.plot_grid.plots[0]
        counter.reset()
        win._on_variable_dropped(plot, "a")
        _wait(qapp, win)
        cx, cy = plot._series["a"]["curve"].getData()
        assert len(cx) > 500 and "a" in plot._coarse_series  # the strided curve, drawn
        i = np.rint((cx - T0) / DT).astype(int)
        np.testing.assert_allclose(cx, T0 + i * DT)
        np.testing.assert_allclose(cy, np.sin(i / 50.0))  # X belongs to the row Y was read from
        assert counter.total <= 10, counter.calls  # O(1) (range lookup), not one read per drawn point
    finally:
        win.close()


# --- should-fix 3: hover while moving never reads the file for a lazy axis ---------------


def test_hover_while_moving_over_lazy_axis_reads_nothing(qapp, isolated_config, monkeypatch, path):
    _set_flags(monkeypatch, False, False, True)  # C off: the derived series is resident over a TimeIndex
    win = _open(qapp, path, monkeypatch)
    try:
        win._on_add_derived_variable("m", "a * 2")
        assert not isinstance(win._raw_variables["m"], DerivedColumn)
        plot = win.plot_grid.plots[0]
        win._on_variable_dropped(plot, "m")
        _wait(qapp, win)
        entry = plot._series["m"]
        assert entry["y_source"] is None and isinstance(entry["x"], TimeIndex)
        plot.getViewBox().setXRange(T0 + 100, T0 + 101, padding=0)
        _wait(qapp, win)
        counter = _UiCounter(monkeypatch, "window", "value_at")
        plot.set_hover_enabled(True)
        for x in np.linspace(T0 + 100, T0 + 101, 40):
            plot.set_hover_x(float(x))
        assert counter.total == 0, counter.calls
        assert "≈" in plot._hover_label.toPlainText()  # approximate while moving
        plot._settle_hover()  # the stop timer: now the exact readout
        assert "≈" not in plot._hover_label.toPlainText()
        i = round((float(plot._hover_x) - T0) / DT)
        shown = float(plot._hover_label.toPlainText().split(":")[-1])
        assert shown == pytest.approx(2 * np.sin(i / 50.0), rel=1e-4, abs=1e-5)
    finally:
        win.close()


# --- should-fix 4: the navigator region follows the plots when a derived pyramid lands ---


def _derived_setup(qapp, monkeypatch, path):
    _set_flags(monkeypatch, True, True, True)
    win = _open(qapp, path, monkeypatch)
    win._on_add_derived_variable("d", "a * b")
    assert isinstance(win._raw_variables["d"], DerivedColumn)
    return win


def test_derived_pyramid_for_navigator_keeps_region_on_group_range(qapp, isolated_config, monkeypatch, path):
    win = _derived_setup(qapp, monkeypatch, path)
    try:
        plot = win.plot_grid.plots[0]
        d = win._raw_variables["d"]
        real_request = DerivedColumn.request_pyramid
        monkeypatch.setattr(DerivedColumn, "request_pyramid", lambda self: None)  # no build yet
        win._on_variable_dropped(plot, "a")
        _wait(qapp, win)
        win._on_variable_dropped(plot, "d")
        plot.remove_series("a")
        win._on_grid_layout_changed()
        assert "navigator" in d._pyramid_listeners  # waiting on the navigator's behalf
        plot.getViewBox().setXRange(T0 + 100, T0 + 200, padding=0)
        _wait(qapp, win)
        monkeypatch.setattr(DerivedColumn, "request_pyramid", real_request)
        d.set_pyramid(column_module.build_pyramid_from_windows(d.window, d.n_rows))  # lands late
        _wait(qapp, win)
        view = plot.getViewBox().viewRange()[0]
        region = win.navigator._region.getRegion()
        assert region[0] == pytest.approx(view[0], abs=1e-3) and region[1] == pytest.approx(view[1], abs=1e-3)
        assert "navigator" not in d._pyramid_listeners  # one-shot
        ox, _oy = win.navigator._curve.getData()
        assert len(ox) > 10  # the envelope replaced the flat placeholder
    finally:
        win.close()


def test_navigator_listener_detached_when_column_stops_driving(qapp, isolated_config, monkeypatch, path):
    win = _derived_setup(qapp, monkeypatch, path)
    try:
        plot = win.plot_grid.plots[0]
        d = win._raw_variables["d"]
        monkeypatch.setattr(DerivedColumn, "request_pyramid", lambda self: None)
        win._on_variable_dropped(plot, "d")
        assert "navigator" in d._pyramid_listeners
        win._on_variable_dropped(plot, "a")
        plot.remove_series("d")
        win._on_grid_layout_changed()  # "a" drives the overview now
        assert "navigator" not in d._pyramid_listeners
    finally:
        win.close()


# --- should-fix 5: every waiting plot is told when a pyramid build completes / fails -------


def test_plot_redrawn_when_navigator_built_the_pyramid(qapp, isolated_config, monkeypatch, path):
    win = _derived_setup(qapp, monkeypatch, path)
    try:
        plot = win.plot_grid.plots[0]
        d = win._raw_variables["d"]
        win._on_variable_dropped(plot, "a")
        _wait(qapp, win)
        win._on_variable_dropped(plot, "d")  # the navigator's build starts first; the plot only waits
        plot.remove_series("a")
        win._on_grid_layout_changed()
        plot.getViewBox().setXRange(T0, T0 + (N - 1) * DT, padding=0)
        _wait(qapp, win)
        _wait(qapp, win)
        assert d.pyramid is not None
        cx, _ = plot._series["d"]["curve"].getData()
        assert cx is not None and len(cx) > 10  # drawn from the pyramid, not left blank
        assert "d" in plot._coarse_series
    finally:
        win.close()


def test_failed_pyramid_is_reported_and_given_up_falls_back_to_a_real_fetch(qapp, isolated_config, monkeypatch, path):
    win = _derived_setup(qapp, monkeypatch, path)
    try:
        def boom(*a, **k):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(column_module, "build_pyramid_from_windows", boom)
        monkeypatch.setattr(column_module, "MAX_PYRAMID_FAILURES", 1)
        failures = []
        win.plot_grid.renderFailed.connect(lambda plot, name, msg: failures.append((name, msg)))
        plot = win.plot_grid.plots[0]
        win._on_variable_dropped(plot, "d")
        _wait(qapp, win)
        _wait(qapp, win)
        d = win._raw_variables["d"]
        assert d.pyramid is None and d.pyramid_given_up
        assert any(n == "d" for n, _m in failures)  # not silently blank
        cx, _ = plot._series["d"]["curve"].getData()
        assert cx is not None and len(cx) > 10  # given up: the real-fetch path drew it
    finally:
        win.close()


def test_pyramid_listeners_are_one_shot_and_keyed(path):
    a = WindowedColumn(ParquetDataSource(path), "a")
    d = column_module.make_derived_column("a * 2", {"a": a}, N, FunctionRegistry())
    seen = []
    d.add_pyramid_listener("k", lambda ok: seen.append(("first", ok)))
    d.add_pyramid_listener("k", lambda ok: seen.append(("second", ok)))  # replaces
    d.add_pyramid_listener("j", lambda ok: seen.append(("j", ok)))
    d.remove_pyramid_listener("j")
    d.pyramid_failed()
    d.pyramid_failed()  # nobody left to tell
    assert seen == [("second", False)]


# --- should-fix 6: a DerivedColumn evaluates against an immutable registry snapshot -------


def test_derived_column_uses_registry_snapshot(qapp, isolated_config, monkeypatch, path):
    _set_flags(monkeypatch, False, True, True)
    win = _open(qapp, path, monkeypatch)
    try:
        reg = win.function_registry
        reg.user_functions["f"] = UserFunction("f", ["x"], [], "x * 2")
        win._on_add_derived_variable("d", "f(a)")
        d = win._raw_variables["d"]
        assert isinstance(d, DerivedColumn) and d.registry is not reg
        a = np.sin(np.arange(N) / 50.0)
        # the live registry being rewritten in place (what update_registry does on the UI thread)
        # must not disturb a window evaluated meanwhile on a worker
        reg.user_functions.clear()
        np.testing.assert_allclose(d.window(100, 200), 2 * a[100:200])
        reg.user_functions["f"] = UserFunction("f", ["x"], [], "x * 2")

        cand = dict(reg.user_functions)
        cand["f"] = UserFunction("f", ["x"], [], "x * 3")
        assert win.update_registry(user_functions=cand, touched={"f"}) == []
        d2 = win._raw_variables["d"]
        assert d2 is not d and isinstance(d2, DerivedColumn) and d2.registry is not reg
        reg.user_functions.clear()
        reg.maps.clear()
        np.testing.assert_allclose(d2.window(100, 200), 3 * a[100:200])  # the new body, from the snapshot

        # all-or-nothing: a bad candidate changes nothing
        reg.user_functions["f"] = UserFunction("f", ["x"], [], "x * 3")
        bad = dict(reg.user_functions)
        bad["f"] = UserFunction("f", ["x"], [], "g_unknown(x)")
        assert win.update_registry(user_functions=bad, touched={"f"})
        assert win._raw_variables["d"] is d2
    finally:
        win.close()


# --- nits ----------------------------------------------------------------------------------


def test_time_index_take_anchor_rows_need_no_reads(path, monkeypatch):
    ds = ParquetDataSource(path, block_rows=1_000)
    ti = TimeIndex.build(ds, "time", pin_rows=[6_000, 12_000])
    counter = _UiCounter(monkeypatch, "window")
    rows = np.arange(0, N, 8192)
    np.testing.assert_allclose(ti.take(rows), T0 + rows * DT)
    assert counter.total == 0
    np.testing.assert_allclose(ti.take([-1, 5, 8192, 6_000]), T0 + np.array([N - 1, 5, 8192, 6_000]) * DT)
    with pytest.raises(IndexError):
        ti.take([N])


def test_chunked_stats_range_overflow_near_float_max():
    v = np.array([-1.5e308, -1e308, 0.0, 1e308, 1.5e308, np.nan])
    # The old binning turned the overflowing span into NaN positions (inf * 0): numpy warns (and on
    # x86 the NaN -> int cast gives a negative bin, so bincount raises). Moment overflow is expected.
    with np.errstate(over="ignore"), warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        stats = chunked_stats(lambda lo, hi: v[lo:hi], 0, len(v), chunk_rows=2, bins=16)
    assert stats["min"] == -1.5e308 and stats["max"] == 1.5e308
    assert stats["median"] == 0.0 and stats["count"] == 6 and stats["nan_count"] == 1
    w = np.array([0.0, 5e-324, 5e-324])  # subnormal-wide range
    assert chunked_stats(lambda lo, hi: w[lo:hi], 0, 3)["median"] == 5e-324
