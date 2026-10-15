"""The XY map's aggregate "time" (analysis.ja.md 44.14): the time each cell was occupied.

Core part: `compute_xy_map` on synthetic 1 s data where (x, y) sits a known time in each cell. UI part:
the unit combo, 2D / 3D display, status, hover, export (CSV + `origin`) and the image legend.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer import features  # noqa: E402
from parquet_analyzer.core import histogram as hist  # noqa: E402
from parquet_analyzer.core.map_grid import GridAxisSpec  # noqa: E402
from parquet_analyzer.io import maps as maps_io  # noqa: E402
from parquet_analyzer.ui import map_export_dialog as med  # noqa: E402
from parquet_analyzer.ui import xy_map as xm  # noqa: E402
from parquet_analyzer.ui import xy_map_view as xmv  # noqa: E402
from parquet_analyzer.ui import xyz_3d_window as x3  # noqa: E402
from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402

N = 1200
# rows 0..399 at cell (x0, y0), 400..799 at (x1, y0), 800..1199 at (x1, y1); one row per second
X = np.r_[np.zeros(400), np.ones(800)]
Y = np.r_[np.zeros(800), np.ones(400)]
AX = ("count", GridAxisSpec("count", 0.0, 1.0, count=2))


def _run(t=None, kind="map", unit="h", chunk_rows=10_000, row_range=(0, N), excluded=None, agg="time", y=None):
    t = np.arange(N, dtype=float) if t is None else t
    cols = {"x": X, "y": Y if y is None else y, "z": np.arange(N, dtype=float), "t": t}
    return xm.compute_xy_map(
        lambda n, a, b: cols[n][a:b], "x", ["y"], "z", kind, AX, AX if kind == "map" else None, agg, 1, "none",
        row_range[0], row_range[1], chunk_rows=chunk_rows, scatter_limit=100_000, excluded_rows=excluded,
        fetch_time=lambda a, b: t[a:b], time_unit=unit,
    )


def test_cells_hold_the_time_in_seconds_minutes_and_hours():
    for unit, div in (("s", 1.0), ("min", 60.0), ("h", 3600.0)):
        g = _run(unit=unit).grids[0]
        # value[iy, ix]; the last row of the range has no next stamp: weight 0 (cell (x1, y1) = 399 s)
        np.testing.assert_allclose(g.value * div, [[400, 400], [0, 399]])
        assert g.measured_mask.all() and not g.filled_mask.any()  # every cell is valid, empty = 0
    r = _run(unit="min")
    assert r.is_time and r.time_unit == "min" and r.z_name is None
    assert r.grids[0].total_time == pytest.approx(1199 / 60)


def test_gap_time_is_excluded_and_counted():
    t = np.arange(N, dtype=float)
    t[600:] += 100.0  # a 101 s step between rows 599 and 600 (> 5 x the 1 s median)
    r = _run(t=t, unit="s")
    assert r.grids[0].value[0, 1] == 399  # (x1, y0) lost the row before the gap (not 400 + 100)
    assert r.gap_count == 1
    assert hist.scan_time_axis(lambda a, b: t[a:b], 0, N)[2] == pytest.approx(5.0)  # the histogram's gap value


def test_last_row_of_the_range_is_zero_even_when_more_rows_follow():
    r = _run(unit="s", row_range=(0, 800))
    assert r.grids[0].value[0, 1] == 399 and r.grids[0].value[1, 1] == 0
    assert r.grids[0].total_time == 799


@pytest.mark.parametrize("chunk_rows", [1, 7, 399, 400, 1199])
def test_small_chunks_equal_one_shot_even_across_gaps(chunk_rows):
    t = np.arange(N, dtype=float)
    t[600:] += 100.0
    one = _run(t=t, unit="s")
    many = _run(t=t, unit="s", chunk_rows=chunk_rows)
    np.testing.assert_array_equal(many.grids[0].value, one.grids[0].value)
    assert many.gap_count == one.gap_count == 1 and many.n_valid == one.n_valid == N


def test_curve_sums_the_time_over_x_only():
    r = _run(kind="curve", unit="s")
    assert r.is_curve and r.names == ["x_time"]
    np.testing.assert_array_equal(r.grids[0].value, [400, 799])  # x = 0, x = 1
    assert r.grids[0].measured_mask.all()
    assert len(r.points[0][0]) == 0  # nothing to overlay on a curve of times
    np.testing.assert_array_equal(_run(kind="curve", unit="s", chunk_rows=13).grids[0].value, [400, 799])


def test_time_axis_not_ascending_is_not_computed():
    t = np.arange(N, dtype=float)
    t[500] = 10.0
    r = _run(t=t)
    assert r.time_refused == "time_not_ascending" and not r.grids and r.no_data


def test_non_finite_value_rows_do_not_change_the_weights_of_the_others():
    y = Y.copy()
    y[10] = np.nan  # the row is dropped from the map, its neighbours keep w = 1
    r = _run(unit="s", y=y, chunk_rows=50)
    assert r.n_excluded == 1 and r.grids[0].value[0, 0] == 399


def test_excluded_rows_give_no_time_and_do_not_lengthen_the_neighbours():
    r = _run(unit="s", excluded=np.array([10, 11]), chunk_rows=50)
    # cell (x0, y0) loses exactly the two rows' own 2 s; row 9 still lasts 1 s (not 3 s)
    assert r.grids[0].value[0, 0] == 398
    assert r.grids[0].total_time == 1197


def test_other_aggregates_are_unchanged_by_the_time_machinery():
    r = _run(agg="count", unit="s")
    np.testing.assert_array_equal(r.grids[0].value, [[400, 400], [0, 400]])
    assert r.gap_count == 0 and not r.is_time


def test_time_without_a_fetcher_is_refused():
    with pytest.raises(ValueError):
        xm.compute_xy_map(lambda n, a, b: X[a:b], "x", ["y"], "z", "map", AX, AX, "time", 1, "none", 0, N,
                          chunk_rows=100, scatter_limit=100)


# -- UI ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setitem(features._EFFECTIVE, "map_from_xy", True)
    monkeypatch.setitem(features._EFFECTIVE, "maps", True)
    x3.reset_gl3d_probe()
    yield
    x3.reset_gl3d_probe()


@pytest.fixture
def ops_log(monkeypatch):
    recorded = []
    monkeypatch.setattr(xmv, "log_op", lambda *a, **kw: recorded.append((a[0], kw)))
    return recorded


def _wait(qapp, win, timeout=60.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


@pytest.fixture
def mw(qapp, isolated_config, tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    win = MainWindow()
    win.show()
    assert win.load_parquet(str(path))
    win._add_variable_to_plot(win.plot_grid.plots[0], "sine")
    qapp.processEvents()
    win.plot_grid.go_home()
    yield win
    win.close()


def _open(qapp, mw, agg="time", unit="h", ys=("cosine",), color="z"):
    w = mw.open_xy_plot()
    w.resize(1000, 700)
    w.show()
    combos = (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._display_combo)
    for widget in combos:
        widget.blockSignals(True)
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(list(ys))
    w._z_combo.setCurrentText("offset_sine")
    w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    w._display_combo.setCurrentIndex(w._display_combo.findData("map"))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData(agg))
    w._time_unit_combo.setCurrentIndex(w._time_unit_combo.findData(unit))
    for widget in combos:
        widget.blockSignals(False)
    w._apply_color_constraints()
    w._on_setting_changed()
    _wait(qapp, w)
    return w


def _expected_seconds(mw):
    t = np.asarray(mw._time_values, dtype=float)
    d = np.diff(t)
    gap = 5.0 * np.median(d)
    return float(d[d <= gap].sum()), int((d > gap).sum())


def test_unit_combo_shows_only_with_aggregate_time(qapp, mw):
    w = _open(qapp, mw, agg="mean")
    assert not w._time_unit_group.isVisible()
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("time"))
    _wait(qapp, w)
    assert w._time_unit_group.isVisible() and w._time_unit_combo.currentData() == "h"
    assert [w._time_unit_combo.itemData(i) for i in range(3)] == ["s", "min", "h"]
    assert not w._fill_group.isEnabled() and not w._min_group.isEnabled()
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    assert not w._time_unit_group.isVisible() and w._min_group.isEnabled()


def test_2d_map_status_hover_and_unit_change(qapp, mw, ops_log):
    w = _open(qapp, mw, unit="s")
    r = w._result
    total, gaps = _expected_seconds(mw)
    assert r.is_time and r.n_outside == 0
    assert r.time_total == pytest.approx(total) and r.gap_count == gaps >= 1
    assert r.grids[0].measured_mask.all() and not np.isnan(r.grids[0].value).any()
    assert f"{total:.6g}" in w.status_text()
    g = r.grids[0]
    iy, ix = np.unravel_index(np.argmax(g.value), g.value.shape)
    text = xm.map_hover_text(r, g.x_axis[ix], g.y_axis[iy])
    assert f"{g.value[iy, ix]:.6g} {xm.tr('hist.unit_s')}" in text
    assert xm.tr("xy.agg_time") in w._colorbar.axis.labelText
    # switching to hours divides every cell by 3600
    w._time_unit_combo.setCurrentIndex(w._time_unit_combo.findData("h"))
    _wait(qapp, w)
    np.testing.assert_allclose(w._result.grids[0].value * 3600.0, g.value)
    assert w._result.time_unit == "h" and xm.tr("hist.unit_h") in xm.map_hover_text(
        w._result, g.x_axis[ix], g.y_axis[iy])
    op = [kw for n, kw in ops_log if n == "xy_plot_compute"][-1]
    assert op["agg"] == "time" and op["time_unit"] == "h"


def test_z_is_not_used_by_time(qapp, mw, ops_log):
    w = _open(qapp, mw, agg="mean")
    assert w._z_combo.isEnabled() and w._map_kind() == "map"
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("count"))
    _wait(qapp, w)
    assert "time_unit" not in [kw for n, kw in ops_log if n == "xy_plot_compute"][-1]
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("time"))
    _wait(qapp, w)
    assert not w._z_combo.isEnabled() and w._z_combo.toolTip() == xm.tr("xy.map_z_unused_tooltip")
    first = w._result
    n_ops = len([1 for n, _ in ops_log if n == "xy_plot_compute"])
    w._z_combo.setCurrentText("sine")  # another Z: nothing to recompute
    _drain(qapp)
    assert not w.is_computing() and w._result is first
    assert len([1 for n, _ in ops_log if n == "xy_plot_compute"]) == n_ops
    # no Z at all does not stop the map (rule: one Y and colour "Z variable")
    w._z_combo.setCurrentIndex(-1)
    assert w._map_kind() == "map"
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("mean"))
    assert w._z_combo.isEnabled() and w._z_combo.toolTip() == ""


def _drain(qapp, seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_curve_display_with_time(qapp, mw):
    w = _open(qapp, mw, color="single", unit="min")
    r = w._result
    assert r.is_curve and r.is_time and r.names == ["sine_time"]
    total, _gaps = _expected_seconds(mw)
    assert float(r.grids[0].value.sum()) * 60.0 == pytest.approx(total)
    assert xm.tr("hist.unit_min") in xm.map_hover_text(r, r.grids[0].x_axis[3], 0.0)


def test_non_ascending_time_axis_shows_the_reason(qapp, mw):
    t = np.asarray(mw._time_values, dtype=float).copy()
    t[100] = t[0] - 5.0
    mw._time_values = t
    w = _open(qapp, mw)
    r = w._result
    assert r.time_refused == "time_not_ascending" and not r.grids
    assert xm.tr("xy.map_err_time_not_ascending") in w.status_text()
    assert not w.export_map_action.isEnabled()


def test_3d_map_height_is_the_time(qapp, mw, monkeypatch):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))
    w = mw.open_xy_plot()
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._kind3d_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(["cosine"])
    w._z_combo.setCurrentText("offset_sine")
    w._kind3d_combo.setCurrentIndex(w._kind3d_combo.findData("map"))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("time"))
    w._time_unit_combo.setCurrentIndex(w._time_unit_combo.findData("min"))
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._kind3d_combo):
        widget.blockSignals(False)
    w._on_setting_changed()
    _wait(qapp, w)
    r = w._result
    assert r.is_3d and r.is_time and w._view3d.scene["kind"] == "surface"
    g = r.grids[0]
    md = w._view3d._data_items[0].opts["meshdata"]
    assert md.faces().shape[0] == 2 * (len(g.x_axis) - 1) * (len(g.y_axis) - 1)  # no empty cells
    assert xm.tr("hist.unit_min") in r.value_label


def test_image_export_legend_has_the_unit(qapp, mw, tmp_path):
    w = _open(qapp, mw, unit="min")
    fig = w.build_export_figure(["s"], (800, 600))
    labels = [ax.get_ylabel() for ax in fig.axes]
    assert any(xm.tr("hist.unit_min") in text and xm.tr("xy.agg_time") in text for text in labels)
    stamp = "\n".join(w.export_stamp_extra())
    assert xm.tr("xy.agg_time") in stamp and f"[{xm.tr('hist.unit_min')}]" in stamp
    assert xm.tr("xy.map_min_count") not in stamp and "Z:" not in stamp
    path = tmp_path / "t.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000


def _values(**kw):
    base = dict(name="t_map", register=True, save_csv=False, csv_path="", interpolation="linear", extrapolation="clamp")
    base.update(kw)
    return med.ExportValues(**base)


def test_export_csv_values_and_origin(qapp, mw, tmp_path):
    w = _open(qapp, mw, unit="min")
    r = w._result
    assert med.default_name(r) == "sine_cosine_time"
    out = tmp_path / "t.csv"
    assert med.perform_export(w, r, _values(name="sine_cosine_time", save_csv=True, csv_path=str(out)))
    t = mw.function_registry.maps["sine_cosine_time"]
    np.testing.assert_array_equal(t.z, r.grids[0].value)  # the chosen unit, no NaN
    info = next(i for i in maps_io.list_maps() if i.name == "sine_cosine_time")
    assert info.origin["agg"] == "time" and info.origin["time_unit"] == "min" and info.origin["z"] is None
    assert info.filled == []
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("cosine\\sine,") and len(lines) == len(r.grids[0].y_axis) + 1


def test_export_curve_default_name_and_origin(qapp, mw):
    w = _open(qapp, mw, color="single")
    r = w._result
    assert med.default_name(r, 0) == "sine_time"
    assert med.perform_export(w, r, _values(name="sine_time"))
    info = next(i for i in maps_io.list_maps() if i.name == "sine_time")
    assert info.origin["agg"] == "time" and info.origin["time_unit"] == "h"


def test_non_time_export_origin_has_no_time_unit(qapp, mw):
    w = _open(qapp, mw, agg="mean")
    assert "time_unit" not in med.origin_info(mw, w._result, 0)


# -- review fixes ----------------------------------------------------------------------------------


def test_gap_scan_does_not_depend_on_the_map_chunk_size():
    # 5 chunks of 3 intervals [1, 1, 100] and 4 of [100, 100, 100]: the true median interval is 100 s
    # (gap 500 s), but a median of 3-row chunks' medians is 1 s (gap 5 s): the scan chunking matters
    diffs = np.r_[np.tile([1.0, 1.0, 100.0], 5), np.full(12, 100.0)]
    t = np.r_[0.0, np.cumsum(diffs)]
    m = len(t)
    assert hist.scan_time_axis(lambda a, b: t[a:b], 0, m)[2] == pytest.approx(500.0)
    assert hist.scan_time_axis(lambda a, b: t[a:b], 0, m, 3)[2] == pytest.approx(5.0)
    cols = {"x": np.zeros(m), "y": np.zeros(m), "z": np.zeros(m)}
    for chunk_rows in (3, 5, 1000):
        r = xm.compute_xy_map(
            lambda n, a, b: cols[n][a:b], "x", ["y"], "z", "map", AX, AX, "time", 1, "none", 0, m,
            chunk_rows=chunk_rows, scatter_limit=100, fetch_time=lambda a, b: t[a:b], time_unit="s")
        # no interval is a gap, whatever the map's chunk size; the last row weighs 0
        assert r.gap_count == 0 and r.grids[0].total_time == pytest.approx(diffs.sum())


def test_time_weights_is_the_one_gap_function():
    t = np.arange(N, dtype=float)
    t[600:] += 100.0
    w, n = hist.time_weights(t, N - 1, 5.0)
    assert n == 1 and w[599] == 0 and w.sum() == 1198


def test_non_finite_time_stamp_row_is_dropped_and_counted():
    t = np.arange(N, dtype=float)
    t[300] = np.nan
    r = _run(t=t, unit="s")
    assert r.n_excluded == 1 and r.n_valid == N - 1
    # rows 299 and 300 have no usable interval (NaN difference -> 0), the others keep 1 s
    assert r.grids[0].value[0, 0] == 400 - 1 - 1 - 0  # row 300 dropped, row 299 weighs 0
