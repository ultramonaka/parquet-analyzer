"""36.6 C x D: a derived variable (`big_c_windowed_derived`) on a big file whose time axis is a
lazy `TimeIndex` (`big_d_time_index`) -- create, plot, navigator overview, stats over a zoomed
range; the values must match the flags-off (resident) result."""
from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import features
from parquet_analyzer.core.column import DerivedColumn, MaterializedColumn
from parquet_analyzer.core.time_index import TimeIndex
from parquet_analyzer.ui.main_window import MainWindow

N = 40_000


@pytest.fixture
def path(tmp_path):
    rng = np.random.default_rng(5)
    t = 1_767_225_600.0 + np.cumsum(rng.choice([0.01, 0.02, 0.5], size=N))
    a = np.sin(np.arange(N) / 50.0)
    b = rng.normal(size=N)
    p = tmp_path / "cd.parquet"
    pq.write_table(pa.table({"time": t, "a": a, "b": b}), p, row_group_size=5000)
    return str(p), t, a, b


def _wait(qapp, win):
    for _ in range(600):
        qapp.processEvents()
        if not win._busy_sources and not any(p._redraw_debounce.isActive() for p in win.plot_grid.plots):
            break
        time.sleep(0.005)
    qapp.processEvents()


def _forbid_axis_materialization(monkeypatch):
    """A silent full-axis materialization (np.asarray(TimeIndex)) is what flag D exists to avoid."""
    def boom(self, dtype=None, copy=None):
        raise AssertionError("TimeIndex materialized in full")

    monkeypatch.setattr(TimeIndex, "__array__", boom)


def _run(qapp, monkeypatch, path, t, on):
    if on:
        _forbid_axis_materialization(monkeypatch)
    for flag in ("big_c_windowed_derived", "big_d_time_index"):
        monkeypatch.setitem(features._EFFECTIVE, flag, on)
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0  # everything is "big"
    assert win.load_parquet(path)
    win.resize(1000, 700)
    win.show()
    try:
        win._on_add_derived_variable("d", "delta(a) * 2 + b")
        col = win._raw_variables["d"]
        assert isinstance(win._time_values, TimeIndex) is on
        assert isinstance(col, DerivedColumn) is on
        if not on:
            assert isinstance(col, MaterializedColumn)
        plot = win.plot_grid.plots[0]
        win._on_variable_dropped(plot, "d")
        _wait(qapp, win)
        assert len(plot._series["d"]["curve"].getData()[0]) > 0
        # navigator overview is built (windowed derived over a lazy axis) and finite
        ox, oy = win.navigator._curve.getData()
        assert len(ox) > 0 and np.isfinite(ox).all() and np.isfinite(oy).all()
        r = win.navigator._region.getRegion()
        assert r[0] == pytest.approx(t[0], abs=1e-3) and r[1] == pytest.approx(t[-1], abs=1e-3)
        # zoom, then stats over the visible range
        plot.getViewBox().setXRange(t[10_000], t[12_000], padding=0)
        _wait(qapp, win)
        rs, re = win._visible_row_range()
        captured = {}
        monkeypatch.setattr(win, "_show_stats_dialog", lambda batch: captured.update(batch))
        win._on_stats_requested()
        for _ in range(400):
            qapp.processEvents()
            if captured:
                break
            time.sleep(0.005)
        return (rs, re), captured["results"]["d"], np.asarray(win.variable_values("d"))
    finally:
        win.close()


def test_derived_with_lazy_time_axis_matches_flags_off(qapp, isolated_config, monkeypatch, path):
    p, t, a, b = path
    rng_on, st_on, vals_on = _run(qapp, monkeypatch, p, t, True)
    rng_off, st_off, vals_off = _run(qapp, monkeypatch, p, t, False)
    assert rng_on == rng_off
    want = (np.concatenate([[0.0], np.diff(a)]) * 2 + b)
    np.testing.assert_array_equal(vals_on, vals_off)
    np.testing.assert_allclose(vals_on, want)
    for k in ("count", "nan_count", "min", "max", "mean", "std", "median"):
        assert st_on[k] == pytest.approx(st_off[k], rel=1e-9, abs=1e-12), k
    rs, re = rng_on
    assert st_on["count"] == re - rs
    assert st_on["mean"] == pytest.approx(float(np.mean(want[rs:re])))


@pytest.mark.parametrize("direct_rows", [10**9, 0])  # computed directly / via the pyramid envelope
def test_navigator_overview_with_derived_and_lazy_axis(qapp, isolated_config, monkeypatch, path, direct_rows):
    from parquet_analyzer.ui import navigator_sync as main_window

    p, t, a, b = path
    _forbid_axis_materialization(monkeypatch)
    monkeypatch.setattr(main_window, "_DERIVED_NAV_DIRECT_ROWS", direct_rows)
    for flag in ("big_c_windowed_derived", "big_d_time_index"):
        monkeypatch.setitem(features._EFFECTIVE, flag, True)
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0
    assert win.load_parquet(p)
    try:
        win._on_add_derived_variable("d", "a + b")
        assert isinstance(win._raw_variables["d"], DerivedColumn)
        plot = win.plot_grid.plots[0]
        win._on_variable_dropped(plot, "d")
        _wait(qapp, win)
        _wait(qapp, win)
        ox, oy = win.navigator._curve.getData()
        assert len(ox) > 2 and np.isfinite(ox).all() and np.isfinite(oy).all()
        if direct_rows == 0:  # envelope: min/max of the real derived values appear in the overview
            want = a + b
            assert oy.max() <= want.max() + 1e-9 and oy.min() >= want.min() - 1e-9
            assert oy.max() > 0.5 * want.max()  # not the flat placeholder line
    finally:
        win.close()
