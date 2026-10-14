"""The navigator region must follow the plots after every series change (plots 24.1; backlog 14).

set_overview_data() used to reset the region to the full extent; before the fix several callers
(map/function changes via update_registry, derived add/edit/delete, a drop onto the first plot)
left it there while the plots stayed zoomed, and the next navigator interaction pushed the full
range onto the plots. The restore now lives inside NavigatorSync.refresh."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_functions_maps_ui import CURVE, _csv, boxes, flags_on, win  # noqa: F401  (fixtures)

from parquet_analyzer import features
from parquet_analyzer.ui.main_window import MainWindow


def _drain(qapp, w):
    end = time.monotonic() + 5
    for _ in range(50):
        qapp.processEvents()
    while w._busy_sources and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(50):
        qapp.processEvents()


def _zoom(qapp, w, plot):
    lo, hi = plot.getViewBox().viewRange()[0]
    mid, q = (lo + hi) / 2, (hi - lo) / 20
    w.plot_grid.set_x_range(mid - q, mid + q)
    _drain(qapp, w)
    return plot.getViewBox().viewRange()[0]


def _assert_synced(qapp, w, plot, zoomed):
    _drain(qapp, w)
    xr = plot.getViewBox().viewRange()[0]
    assert xr == pytest.approx(zoomed)
    assert w.navigator.selected_range() == pytest.approx(tuple(xr))
    # the visible symptom: a small navigator move must shift the plots from the zoomed range
    lo, hi = w.navigator.selected_range()
    d = (hi - lo) * 0.1
    w.navigator._region.setRegion((lo + d, hi + d))  # signals on: the real region-change path
    _drain(qapp, w)
    got = plot.getViewBox().viewRange()[0]
    assert got[1] - got[0] == pytest.approx(hi - lo, rel=1e-3)  # not the full extent
    assert got[0] == pytest.approx(lo + d, rel=1e-6, abs=1e-3)


@pytest.fixture
def mapped(qapp, win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    win._on_add_derived_variable("m", "cv(a)")
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "m")
    _drain(qapp, win)
    return win, mgr, plot, _zoom(qapp, win, plot), tmp_path


def test_map_apply_settings(qapp, mapped):
    w, mgr, plot, z, _ = mapped
    mgr.interp_combo.setCurrentIndex(mgr.interp_combo.findData("previous"))
    assert mgr.apply_settings()
    _assert_synced(qapp, w, plot, z)


def test_map_values_save(qapp, mapped):
    w, mgr, plot, z, _ = mapped
    mgr._on_selection_changed()
    mgr.preview_table.item(1, 1).setText("200")
    assert mgr.save_values()
    _assert_synced(qapp, w, plot, z)


def test_map_reimport(qapp, mapped):
    w, mgr, plot, z, tmp = mapped
    (tmp / "cv.csv").write_text("x,y\n0,1000\n20,1000\n", encoding="utf-8")
    assert mgr.reimport_selected()
    _assert_synced(qapp, w, plot, z)


def test_user_function_change_via_update_registry(qapp, win):
    win._on_add_derived_variable("d1", "Power_fn(a, b)")
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "d1")
    _drain(qapp, win)
    z = _zoom(qapp, win, plot)
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.expression_edit.setText("p * q * k")
    assert mgr.save_function()
    _assert_synced(qapp, win, plot, z)


def test_add_and_edit_derived_variable(qapp, win):
    plot = win.plot_grid.plots[0]
    win._on_add_derived_variable("d", "a * 2")
    win._add_variable_to_plot(plot, "d")
    _drain(qapp, win)
    z = _zoom(qapp, win, plot)
    win.edit_derived_variable("d", "a * 3")
    _assert_synced(qapp, win, plot, z)
    z = _zoom(qapp, win, plot)
    win._on_add_derived_variable("d", "a * 4")  # re-add of the same name = edit path
    _assert_synced(qapp, win, plot, z)


def test_delete_derived_variable(qapp, win):
    plot = win.plot_grid.plots[0]
    win._on_add_derived_variable("d", "a * 2")
    win._add_variable_to_plot(plot, "d")
    win._add_variable_to_plot(plot, "b")
    _drain(qapp, win)
    z = _zoom(qapp, win, plot)
    win.delete_derived_variable("d")
    _assert_synced(qapp, win, plot, z)


def test_drop_variable_on_first_plot(qapp, win):
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    _drain(qapp, win)
    z = _zoom(qapp, win, plot)
    win._on_variable_dropped(plot, "b")
    _assert_synced(qapp, win, plot, z)


def test_keep_region_false_goes_home(qapp, win):
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    _drain(qapp, win)
    _zoom(qapp, win, plot)
    win._nav_sync.refresh(home=True)
    lo, hi = win.navigator._region.getRegion()
    ox = win.navigator._curve.getData()[0]
    assert (lo, hi) == pytest.approx((float(np.min(ox)), float(np.max(ox))))


def test_windowed_derived_edit_keeps_region(qapp, isolated_config, tmp_path, monkeypatch):
    """big_c_windowed_derived: the series is replaced by a DerivedColumn (replace_series_source)."""
    monkeypatch.setitem(features._EFFECTIVE, "big_c_windowed_derived", True)
    n = 4000
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    rng = np.random.default_rng(3)
    path = tmp_path / "m.parquet"
    pq.write_table(
        pa.table({"time": pa.array(times), "a": rng.normal(size=n), "b": rng.normal(size=n)}),
        path,
        row_group_size=500,
    )
    w = MainWindow()
    w.settings.eager_load_limit_mb = 0
    try:
        assert w.load_parquet(str(path))
        w._on_add_derived_variable("d", "a + b")
        plot = w.plot_grid.plots[0]
        w._on_variable_dropped(plot, "d")
        _drain(qapp, w)
        z = _zoom(qapp, w, plot)
        w.edit_derived_variable("d", "a - b")
        _assert_synced(qapp, w, plot, z)
        w._add_variable_to_plot(plot, "b")
        _drain(qapp, w)
        z = _zoom(qapp, w, plot)
        w.delete_derived_variable("d")
        _assert_synced(qapp, w, plot, z)
    finally:
        w.close()
