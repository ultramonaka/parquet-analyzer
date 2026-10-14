"""Map from XY measurements: the XY window's "map" display and the export (analysis.ja.md 44.3, 44.4, 44.6).

OpenGL cannot render offscreen: the 3D map is checked down to the data handed to the view (surface
faces, hover text, overlay points); how it looks is for a real display.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer import features  # noqa: E402
from parquet_analyzer.core import map_grid as mg  # noqa: E402
from parquet_analyzer.core.map_grid import GridAxisSpec  # noqa: E402
from parquet_analyzer.io import maps as maps_io  # noqa: E402
from parquet_analyzer.ui import map_export_dialog as med  # noqa: E402
from parquet_analyzer.ui import messages  # noqa: E402
from parquet_analyzer.ui import xy_map as xm  # noqa: E402
from parquet_analyzer.ui import xy_map_view as xmv  # noqa: E402
from parquet_analyzer.ui import xy_plot_window as xy_mod  # noqa: E402
from parquet_analyzer.ui import xyz_3d_window as x3  # noqa: E402
from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402

Yes, No = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


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
def ask(monkeypatch):
    """Answer for `messages.question`; `ask.answer`, `ask.calls`."""

    class Ask:
        answer = Yes

    a = Ask()
    a.calls = []

    def fake(parent, title, text, *args, **kwargs):
        a.calls.append(text)
        return a.answer

    monkeypatch.setattr(messages, "question", fake)
    return a


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(messages, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    return shown


@pytest.fixture
def ops(monkeypatch):
    recorded = []
    for mod in (xy_mod, xmv, med):  # xmv: the map mixin logs its own ops (44.12)
        monkeypatch.setattr(mod, "log_op", lambda *a, **kw: recorded.append((a[0], kw)))
    return recorded


def _drain(qapp, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _wait(qapp, win, timeout=30.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


@pytest.fixture
def feature_path(tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    return str(path)


@pytest.fixture
def mw(qapp, isolated_config, feature_path):
    win = MainWindow()
    win.show()
    assert win.load_parquet(feature_path)
    win._add_variable_to_plot(win.plot_grid.plots[0], "sine")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    yield win
    win.close()


def _open_map(qapp, mw, x="sine", ys=("cosine",), z="offset_sine", color="z", fill="smooth", agg="mean"):
    """An XY window in map display (2D)."""
    w = mw.open_xy_plot()
    w.resize(1000, 700)
    w.show()
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._display_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_picker.set_selected(list(ys))
    w._z_combo.setCurrentText(z)
    w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    w._display_combo.setCurrentIndex(w._display_combo.findData("map"))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData(agg))
    w._fill_combo.setCurrentIndex(w._fill_combo.findData(fill))
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._display_combo):
        widget.blockSignals(False)
    w._apply_color_constraints()
    w._on_setting_changed()
    _wait(qapp, w)
    return w


def _move_mouse(widget, pos) -> None:
    """A mouse move delivered to the widget like Qt's event loop would (QTest.mouseMove does not reach
    the viewport of a GraphicsView offscreen)."""
    from PySide6.QtWidgets import QApplication

    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# -- flag and entry points ---------------------------------------------------------------


def test_map_entries_hidden_without_the_flag(qapp, mw, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "map_from_xy", False)
    w = mw.open_xy_plot()
    assert w._display_combo.findData("map") < 0
    assert not w._map_groups and not w._map_selected()
    from PySide6.QtWidgets import QToolBar

    assert w.export_map_action not in w.findChild(QToolBar, "analysis_toolbar").actions()


def test_map_entries_present_with_the_flag(qapp, mw):
    w = mw.open_xy_plot()
    from PySide6.QtWidgets import QToolBar

    assert w._display_combo.findData("map") >= 0
    assert w.export_map_action in w.findChild(QToolBar, "analysis_toolbar").actions()
    assert not w.export_map_action.isEnabled()  # nothing computed yet
    assert all(g.isHidden() for g in w._map_groups)  # the map settings appear only in map display


def test_time_axis_column_disables_the_map_with_a_reason(qapp, mw):
    w = _open_map(qapp, mw)
    assert w._result.__class__ is xm.XYMapResult
    tc = mw._time_column
    item = w._display_combo.model().item(w._display_combo.findData("map"))
    assert item.isEnabled()
    w._x_combo.setCurrentText(tc)  # a time axis: the map is switched off, with the reason
    _wait(qapp, w)
    assert not item.isEnabled() and "time" in item.toolTip().lower()
    assert w._display_combo.currentData() == "auto"
    assert "time" in w.status_text().lower()
    assert not isinstance(w._result, xm.XYMapResult)


def test_time_column_as_z_or_y_also_blocks(qapp, mw):
    w = _open_map(qapp, mw)
    tc = mw._time_column
    w._z_combo.setCurrentText(tc)
    _wait(qapp, w)
    assert w._display_combo.currentData() == "auto"
    w2 = mw.open_xy_plot()
    w2._y_picker.set_selected([tc])
    w2._display_combo.setCurrentIndex(w2._display_combo.findData("map"))
    _wait(qapp, w2)
    assert w2._display_combo.currentData() == "auto"


# -- 2D map ------------------------------------------------------------------------------


def test_2d_map_computes_cells_status_and_round_default_ranges(qapp, mw):
    w = _open_map(qapp, mw)
    r = w._result
    assert isinstance(r, xm.XYMapResult) and r.kind == "map" and not r.is_3d
    assert r.x_name == "sine" and r.y_names == ["cosine"] and r.z_name == "offset_sine"
    g = r.grids[0]
    # sine^2 + cosine^2 = 1: a ring of measured cells, the inside and corners filled by `smooth`
    assert g.n_measured > 0 and g.n_filled > 0 and not np.isnan(g.value).any()
    np.testing.assert_allclose(g.x_axis, np.linspace(-1, 1, 21), atol=1e-9)  # nice: 0.1 steps over -1..1
    assert (w._x_grid.lo_edit.text(), w._x_grid.hi_edit.text(), w._x_grid.value_edit.text()) == ("-1", "1", "0.1")
    assert not w._x_grid.touched and not w._y_grid.touched
    text = w.status_text()
    assert "6000" in text and f"measured {g.n_measured}" in text and f"filled {g.n_filled}" in text
    assert w.export_map_action.isEnabled()
    # the colour scale and the drawn image carry the values; offset_sine = sine + 10 -> cell value ~ x + 10
    meas = g.measured_mask
    iy_m, ix_m = np.nonzero(meas)
    assert np.all(np.abs(g.value[iy_m, ix_m] - (g.x_axis[ix_m] + 10)) < 0.06)
    assert w._map_items["image"] is not None and "hatch" in w._map_items and "points" in w._map_items


def test_empty_cells_are_not_drawn_and_filled_cells_are_hatched(qapp, mw):
    w = _open_map(qapp, mw, fill="none")
    r = w._result
    g = r.grids[0]
    assert g.n_filled == 0 and np.isnan(g.value).any()
    assert "hatch" not in w._map_items  # nothing was filled
    rgba = xm.map_rgba(g.value, r.value_range(), xy_mod._colormap())
    empty_xy = np.argwhere(np.isnan(g.value))
    assert all(rgba[ix, iy, 3] == 0 for iy, ix in empty_xy)  # transparent: the background shows
    assert all(rgba[ix, iy, 3] == 255 for iy, ix in np.argwhere(~np.isnan(g.value)))
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("smooth"))
    _wait(qapp, w)
    assert w._map_items["hatch"].n_runs > 0  # the filled cells carry the hatch item


def test_filled_runs_cover_exactly_the_filled_cells():
    mask = np.zeros((3, 6), bool)
    mask[0, 1:4] = True
    mask[1, 0] = mask[1, 5] = True
    mask[2, :] = True
    assert xm.filled_runs(mask) == [(0, 1, 3), (1, 0, 0), (1, 5, 5), (2, 0, 5)]
    assert xm.filled_runs(np.zeros((2, 2), bool)) == []


def test_touched_range_is_kept_and_auto_restores_the_round_one(qapp, mw):
    w = _open_map(qapp, mw)
    xg = w._x_grid
    xg.lo_edit.setText("-1")
    xg.hi_edit.setText("1")
    xg.value_edit.setText("0.5")
    xg.lo_edit.textEdited.emit("-1")  # what typing does
    xg.value_edit.textEdited.emit("0.5")
    xg.edited.emit()
    _wait(qapp, w)
    assert xg.touched and len(w._result.grids[0].x_axis) == 5  # -1 -0.5 0 0.5 1
    w.recompute()  # a new computation does not replace the user's range
    _wait(qapp, w)
    assert xg.value_edit.text() == "0.5" and len(w._result.grids[0].x_axis) == 5
    w._auto_button.click()
    _wait(qapp, w)
    assert not xg.touched and xg.value_edit.text() == "0.1" and len(w._result.grids[0].x_axis) == 21


def test_changing_a_variable_takes_the_new_round_range(qapp, mw):
    w = _open_map(qapp, mw)
    w._x_grid.touched = True
    w._x_grid.lo_edit.setText("-5")
    w._z_combo.setCurrentText("sine")  # a different variable: the old ranges are forgotten
    _wait(qapp, w)
    assert not w._x_grid.touched and w._x_grid.lo_edit.text() == "-1"


def test_invalid_grid_is_reported_and_nothing_is_computed(qapp, mw):
    w = _open_map(qapp, mw)
    before = w._result
    xg = w._x_grid
    xg.touched = True
    xg.lo_edit.setText("1")
    xg.hi_edit.setText("1")  # lo >= hi
    w.recompute(keep_range=True)
    assert not w.is_computing() and w.status_text() == xm.tr("xy.grid_err_lo_ge_hi")
    assert w._result is before
    xg.lo_edit.setText("-1")
    xg.hi_edit.setText("1")
    xg.value_edit.setText("0.0001")  # thousands of grid points
    w.recompute(keep_range=True)
    assert not w.is_computing() and "500" in w.status_text()
    xg.value_edit.setText("abc")
    w.recompute(keep_range=True)
    assert not w.is_computing() and "number" in w.status_text()


def test_aggregate_min_count_and_fill_change_the_cells(qapp, mw):
    w = _open_map(qapp, mw, fill="none", agg="count")
    g = w._result.grids[0]
    assert g.agg == "count" and g.count.sum() == 6000 and not w._fill_group.isEnabled()
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    assert w._fill_group.isEnabled()
    n1 = w._result.grids[0].n_measured
    w._min_count_spin.setValue(60)
    _wait(qapp, w)
    assert w._result.grids[0].n_measured < n1  # thin cells have no data now
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("nearest"))
    _wait(qapp, w)
    g = w._result.grids[0]
    assert g.fill == "nearest" and g.n_filled == g.value.size - g.n_measured and not np.isnan(g.value).any()


def test_median_beyond_the_limit_is_refused_with_a_reason(qapp, mw, monkeypatch):
    monkeypatch.setattr(mg, "MAP_MEDIAN_MAX_POINTS", 100)
    w = _open_map(qapp, mw)
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("median"))
    _wait(qapp, w)
    item = w._agg_combo.model().item(w._agg_combo.findData("median"))
    assert not item.isEnabled() and "100" in item.toolTip()
    assert w._agg_combo.currentData() == "mean"  # fell back
    assert isinstance(w._result, xm.XYMapResult) and w._result.agg == "mean"
    assert "100" in w.status_text()


def test_overlay_points_checkbox_toggles_the_points(qapp, mw):
    w = _open_map(qapp, mw)
    assert "points" in w._map_items
    w._points_check.setChecked(False)
    _drain(qapp)
    assert "points" not in w._map_items
    w._points_check.setChecked(True)
    assert "points" in w._map_items


def test_hover_reads_the_cell_under_the_mouse(qapp, mw):
    w = _open_map(qapp, mw, fill="none")
    r = w._result
    g = r.grids[0]
    iy, ix = map(int, np.argwhere(g.measured_mask)[0])
    text = xm.map_hover_text(r, g.x_axis[ix] + 0.001, g.y_axis[iy] - 0.001)
    assert f"{g.x_axis[ix]:.6g}" in text and "measured" in text.lower() and f"{int(g.count[iy, ix])}" in text
    assert "offset_sine" in text
    iy, ix = map(int, np.argwhere(~g.measured_mask)[0])
    assert "empty" in xm.map_hover_text(r, g.x_axis[ix], g.y_axis[iy]).lower()
    assert xm.map_hover_text(r, 50.0, 0.0) == ""  # outside the grid
    # through the real mouse routing: a move over the plot sets the readout, leaving clears it
    vb = w._plot.vb
    centre = vb.mapViewToScene(QPointF(float(g.x_axis[len(g.x_axis) // 2]), 0.0))
    pos = w._glw.mapFromScene(centre)
    _move_mouse(w._glw.viewport(), pos)  # the viewport's own mouse-move handling -> the scene's signal
    _drain(qapp, 0.1)
    assert "offset_sine" in w._hover_label.text()
    _move_mouse(w._glw.viewport(), QPoint(1, 1))  # outside the plot area (inside the axis margin): cleared
    assert w._hover_label.text() == ""


def test_filled_hover_says_filled(qapp, mw):
    w = _open_map(qapp, mw)
    r = w._result
    g = r.grids[0]
    iy, ix = map(int, np.argwhere(g.filled_mask)[0])
    assert "filled" in xm.map_hover_text(r, g.x_axis[ix], g.y_axis[iy]).lower()


# -- curves ------------------------------------------------------------------------------


def test_curves_per_y_with_legend_hide_and_hover(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine", "noise"), color="single", fill="none")
    r = w._result
    assert isinstance(r, xm.XYMapResult) and r.is_curve and r.names == ["cosine", "noise"]
    assert len(r.grids) == 2 and all(g.value.ndim == 1 for g in r.grids)
    assert len(w._legend.items) == 2 and set(w._series_items) == {"cosine", "noise"}
    g = r.grids[0]
    i = int(np.flatnonzero(g.measured_mask)[0])
    both = xm.map_hover_text(r, g.x_axis[i], 0.0)
    assert "cosine" in both and "noise" in both
    # a click on the legend entry hides the curve (and it leaves the hover)
    sample, label = w._legend.items[0]
    name = label.text
    centre = sample.mapToScene(sample.boundingRect().center())
    QTest.mouseClick(w._glw.viewport(), Qt.MouseButton.LeftButton, pos=w._glw.mapFromScene(centre))
    _drain(qapp)
    assert w.hidden_series() == {name}
    assert all(not it.isVisible() for it in w._series_items[name])
    assert name not in xm.map_hover_text(r, g.x_axis[i], 0.0, w.hidden_series())
    fig = w.build_export_figure(["s"], (700, 500))
    assert [ln.get_label() for ln in fig.axes[0].lines if not ln.get_label().startswith("_")] == [
        n for n in r.names if n != name
    ]
    # stays hidden across a recompute
    w.recompute(keep_range=True)
    _wait(qapp, w)
    assert w.hidden_series() == {name} and not w._series_items[name][0].isVisible()


def test_curve_marks_filled_points_open(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine",), color="single", fill="smooth")
    w._min_count_spin.setValue(250)  # the thin middle cells (about 190 points each) become no-data -> filled
    _wait(qapp, w)
    r = w._result
    g = r.grids[0]
    assert g.n_filled > 0 and g.n_measured > 0
    scatters = [it for it in w._series_items["cosine"] if hasattr(it, "points")]
    assert len(scatters) >= 2  # measured (solid) + filled (open) + the overlay points


# -- image export ------------------------------------------------------------------------


def test_image_export_2d_map_masks_empty_and_hatches_filled(qapp, mw, tmp_path):
    w = _open_map(qapp, mw, fill="smooth")
    fig = w.build_export_figure(["stamp"], (800, 600))
    ax = fig.axes[0]
    from matplotlib.collections import PatchCollection, QuadMesh

    assert any(isinstance(c, QuadMesh) for c in ax.collections)
    hatched = [c for c in ax.collections if isinstance(c, PatchCollection)]
    assert hatched and hatched[0].get_hatch() == "//"
    path = tmp_path / "map.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000
    assert "Map:" in "\n".join(w.export_stamp_extra())  # the grid line of the stamp
    w2 = _open_map(qapp, mw, fill="none")
    ax2 = w2.build_export_figure(["s"], (800, 600)).axes[0]
    mesh = next(c for c in ax2.collections if isinstance(c, QuadMesh))
    assert np.ma.is_masked(mesh.get_array()) and not any(isinstance(c, PatchCollection) for c in ax2.collections)


def test_stamp_describes_the_grid(qapp, mw):
    w = _open_map(qapp, mw)
    lines = w.export_stamp_extra()
    assert lines[0] == "X: sine  Y: cosine  Z: offset_sine"
    assert "X -1 - 1" in lines[1] and "0.1" in lines[1] and "Aggregate" in lines[1] and "Interpolate" in lines[1]


# -- 3D (data path only: no GL offscreen) ---------------------------------------------------


def _open_map_3d(qapp, mw, monkeypatch, fill="none", x="sine", z="offset_sine"):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))
    w = mw.open_xy_plot()
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._kind3d_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_picker.set_selected(["cosine"])
    w._z_combo.setCurrentText(z)
    w._kind3d_combo.setCurrentIndex(w._kind3d_combo.findData("map"))
    w._fill_combo.setCurrentIndex(w._fill_combo.findData(fill))
    for widget in (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._kind3d_combo):
        widget.blockSignals(False)
    w._on_setting_changed()
    _wait(qapp, w)
    return w


def test_3d_map_hands_the_surface_to_the_view_without_empty_squares(qapp, mw, monkeypatch):
    w = _open_map_3d(qapp, mw, monkeypatch)
    r = w._result
    assert isinstance(r, xm.XYMapResult) and r.is_3d and w._kind3d_group.isVisible()
    view = w._view3d
    assert view.scene["kind"] == "surface"
    g = r.grids[0]
    n_ok = int(np.isfinite(g.value).sum())
    assert len(view.scene["pos"]) == n_ok  # empty grid points are no hover candidates
    md = view._data_items[0].opts["meshdata"]
    full = 2 * (len(g.x_axis) - 1) * (len(g.y_axis) - 1)
    assert md.faces().shape[0] < full  # faces touching an empty cell are gone
    # hover text of a measured grid point carries count + state
    i = 0
    text = view.hover_label_text(i)
    assert "offset_sine" in text and "Count:" in text and "measured" in text
    assert len(view._data_items) == 2 + (1 if len(view.orphan_points) else 0)  # surface (+ orphan dots) + overlay
    w._points_check.setChecked(False)
    assert len(view._data_items) == 1 + (1 if len(view.orphan_points) else 0)


def test_3d_map_with_fill_has_a_full_surface_and_exports_a_figure(qapp, mw, monkeypatch, tmp_path):
    w = _open_map_3d(qapp, mw, monkeypatch, fill="smooth")
    g = w._result.grids[0]
    md = w._view3d._data_items[0].opts["meshdata"]
    assert md.faces().shape[0] == 2 * (len(g.x_axis) - 1) * (len(g.y_axis) - 1)
    path = tmp_path / "s.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000


# -- export dialog and registration (44.4) -------------------------------------------------------


def _values(**kw):
    base = dict(name="eff_map", register=True, save_csv=False, csv_path="", interpolation="linear", extrapolation="clamp")
    base.update(kw)
    return med.ExportValues(**base)


def test_export_register_only_writes_files_json_origin_and_filled(qapp, mw, ops):
    w = _open_map(qapp, mw)
    g = w._result.grids[0]
    assert med.perform_export(w, w._result, _values())
    t = mw.function_registry.maps["eff_map"]
    assert t.kind == "map" and t.axis_names == ("sine", "cosine", "offset_sine")
    np.testing.assert_array_equal(t.z, g.value)
    info = next(i for i in maps_io.list_maps() if i.name == "eff_map")
    assert info.source_path == ""  # registered only: re-import is unavailable
    assert info.origin["kind"] == "xy_map" and info.origin["x"] == "sine" and info.origin["z"] == "offset_sine"
    assert info.origin["agg"] == "mean" and info.origin["fill"] == "smooth" and info.origin["min_count"] == 1
    assert info.origin["grid"]["x"]["mode"] == "step" and info.origin["time_range"][0] < info.origin["time_range"][1]
    assert len(info.filled) == g.n_filled and all(len(c) == 2 for c in info.filled)
    raw = json.loads((maps_io.maps_dir() / "eff_map.json").read_text(encoding="utf-8"))
    assert raw["origin"]["kind"] == "xy_map" and raw["filled"] == info.filled
    assert ("map_from_xy_export", ) == tuple(n for n, _ in ops if n == "map_from_xy_export")[:1]
    kw = next(kw for n, kw in ops if n == "map_from_xy_export")
    assert kw["name"] == "eff_map" and kw["register"] and not kw["csv"] and not kw["overwrite"]
    assert not np.isnan(maps_io.load_map("eff_map").z).any()


def test_export_csv_only_writes_the_file_and_registers_nothing(qapp, mw, tmp_path):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "out" / "m.csv"
    assert med.perform_export(w, w._result, _values(register=False, save_csv=True, csv_path=str(out)))
    assert out.exists() and "eff_map" not in mw.function_registry.maps and not maps_io.list_maps()
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("cosine\\sine,")  # 44.4: the corner holds <Y>\\<X>, X axis first
    assert "nan" not in text.lower() and ",," in text  # empty cells are blank
    assert mw.settings.last_export_folder == str((tmp_path / "out").resolve())


def test_export_both_registers_with_the_csv_as_source_and_round_trips(qapp, mw, tmp_path):
    w = _open_map(qapp, mw, fill="none")
    g = w._result.grids[0]
    out = tmp_path / "both.csv"
    assert med.perform_export(w, w._result, _values(name="both_map", save_csv=True, csv_path=str(out)))
    info = next(i for i in maps_io.list_maps() if i.name == "both_map")
    assert info.source_path == str(out.resolve())
    assert (maps_io.maps_dir() / "both_map.csv").read_bytes() == out.read_bytes()
    back = maps_io.parse_table_csv(out)
    np.testing.assert_array_equal(back.x, g.x_axis)
    np.testing.assert_array_equal(back.y, g.y_axis)
    np.testing.assert_array_equal(back.z, g.value)  # empty cells come back as NaN
    assert maps_io.reimport_map("both_map").n_empty == g.value.size - g.n_measured  # re-import works


def test_registered_map_reproduces_the_data_at_grid_points_and_between(qapp, mw):
    w = _open_map(qapp, mw)
    g = w._result.grids[0]
    assert med.perform_export(w, w._result, _values(name="eff_map"))
    t = mw.function_registry.maps["eff_map"]
    gx, gy = np.meshgrid(g.x_axis, g.y_axis)
    np.testing.assert_array_equal(t(gx, gy), g.value)  # exactly the aggregated value on the grid points
    mw._on_add_derived_variable("resid", "eff_map(sine, cosine) - offset_sine")
    resid = mw.variable_values("resid")
    assert np.nanmax(np.abs(resid)) < 0.1  # offset_sine = sine + 10: within about one cell width


def test_export_curve_registers_a_curve_with_filled_points(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine", "noise"), color="single", fill="smooth")
    r = w._result
    assert med.perform_export(w, r, _values(name="cos_curve", index=0))
    t = mw.function_registry.maps["cos_curve"]
    assert t.kind == "curve" and t.axis_names[:2] == ("sine", "cosine")
    info = next(i for i in maps_io.list_maps() if i.name == "cos_curve")
    assert info.origin["y"] == "cosine" and info.origin["z"] is None
    assert all(len(c) == 1 for c in info.filled) and len(info.filled) == r.grids[0].n_filled
    assert med.perform_export(w, r, _values(name="noise_curve", index=1))
    assert mw.function_registry.maps["noise_curve"].axis_names[1] == "noise"


def test_export_overwrite_confirms_recomputes_dependents_and_declining_changes_nothing(qapp, mw, ask, ops):
    w = _open_map(qapp, mw)
    assert med.perform_export(w, w._result, _values(name="eff_map"))
    mw._on_add_derived_variable("dep", "eff_map(sine, cosine)")
    before = mw.variable_values("dep").copy()
    ask.calls.clear()
    # a different map under the same name (other aggregate): declined first
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    ask.answer = No
    assert not med.perform_export(w, w._result, _values(name="eff_map"))
    assert len(ask.calls) == 1 and "dep" in ask.calls[0]
    np.testing.assert_array_equal(mw.variable_values("dep"), before)
    ask.answer = Yes
    assert med.perform_export(w, w._result, _values(name="eff_map"))
    assert not np.array_equal(mw.variable_values("dep"), before)  # recomputed with the new values
    np.testing.assert_allclose(mw.variable_values("dep"), mw.function_registry.maps["eff_map"](
        mw.variable_values("sine"), mw.variable_values("cosine")), equal_nan=True)
    kw = [kw for n, kw in ops if n == "map_from_xy_export"]
    assert [k["overwrite"] for k in kw] == [False, True, True]
    info = next(i for i in maps_io.list_maps() if i.name == "eff_map")
    assert info.origin["agg"] == "max"


def test_export_overwrite_failure_rolls_back_registry_and_files(qapp, mw, ask, warnings, tmp_path):
    # a curve "cv" used as cv(x); overwriting it with a MAP (2 arguments) breaks the derived variable
    maps_io.import_map(_csv(tmp_path, "cv.csv", "x,y\n-1,0\n1,10\n"), "cv")
    mw.update_registry(maps={**mw.function_registry.maps, "cv": maps_io.load_map("cv")}, touched={"cv"})
    mw._on_add_derived_variable("dep2", "cv(sine)")
    before_vals = mw.variable_values("dep2").copy()
    before_csv = (maps_io.maps_dir() / "cv.csv").read_bytes()
    before_json = (maps_io.maps_dir() / "cv.json").read_bytes()
    w = _open_map(qapp, mw)
    assert not med.perform_export(w, w._result, _values(name="cv"))
    assert any("dep2" in t for t in warnings)
    assert mw.function_registry.maps["cv"].kind == "curve"
    np.testing.assert_array_equal(mw.variable_values("dep2"), before_vals)
    assert (maps_io.maps_dir() / "cv.csv").read_bytes() == before_csv
    assert (maps_io.maps_dir() / "cv.json").read_bytes() == before_json


def _csv(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_dialog_validates_the_name_live(qapp, mw, tmp_path):
    from parquet_analyzer.core.functions import UserFunction

    mw._function_registry.user_functions["Ufn"] = UserFunction("Ufn", ["p"], [], "p", "")
    maps_io.import_map(_csv(tmp_path, "old.csv", "x,y\n0,0\n1,1\n"), "old_map")
    mw.update_registry(maps={**mw.function_registry.maps, "old_map": maps_io.load_map("old_map")}, touched={"old_map"})
    w = _open_map(qapp, mw)
    d = med.MapExportDialog(w, w._result)
    assert d.name_edit.text() == "offset_sine_map" and d.ok_button.isEnabled() and d.name_message.text() == ""
    for bad in ("", "1abc", "has space", "sin", "Ufn", "class", "a/b"):
        d.name_edit.setText(bad)
        assert not d.ok_button.isEnabled(), bad
        assert "Invalid name" in d.name_message.text()
    d.name_edit.setText("old_map")  # an existing map: an overwrite, allowed, with a warning text
    assert d.ok_button.isEnabled() and "overwritten" in d.name_message.text()
    d.register_check.setChecked(False)
    assert d.name_message.text() == ""  # CSV only: nothing is overwritten
    d.csv_check.setChecked(False)
    assert not d.ok_button.isEnabled()  # no output at all
    d.csv_check.setChecked(True)
    d.name_edit.setText("fresh")
    assert d.path_edit.text().endswith("fresh.csv")  # the default file name follows the name
    v = d.values()
    assert v.name == "fresh" and v.csv_path.endswith("fresh.csv") and not v.register
    d.deleteLater()


def test_dialog_defaults_for_curves_and_spline_with_empty_cells(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine", "noise"), color="single", fill="none")
    w._min_count_spin.setValue(250)  # thin cells become empty
    _wait(qapp, w)
    d = med.MapExportDialog(w, w._result)
    assert d.name_edit.text() == "cosine_curve" and d.curve_combo.count() == 2
    item = d.interp_combo.model().item(d.interp_combo.findData("spline"))
    assert not item.isEnabled()  # empty cells: no spline (27.10)
    d.curve_combo.setCurrentIndex(1)
    assert d.name_edit.text() == "noise_curve"
    w2 = _open_map(qapp, mw, x="sine", ys=("cosine",), color="single", fill="smooth")
    d2 = med.MapExportDialog(w2, w2._result)
    assert d2.interp_combo.model().item(d2.interp_combo.findData("spline")).isEnabled()
    w3 = _open_map(qapp, mw)
    d3 = med.MapExportDialog(w3, w3._result)
    assert d3.interp_combo.findData("spline") < 0  # a map: linear / nearest / previous only
    for dlg in (d, d2, d3):
        dlg.deleteLater()


def test_export_dialog_runs_through_the_toolbar_action(qapp, mw, monkeypatch):
    w = _open_map(qapp, mw)
    seen = []
    monkeypatch.setattr(med.MapExportDialog, "exec", lambda self: seen.append(self.name_edit.text()) or 0)
    w.export_map_action.trigger()
    assert seen == ["offset_sine_map"]
    assert "offset_sine_map" not in mw.function_registry.maps  # cancelled


def test_export_action_disabled_without_data(qapp, mw):
    w = _open_map(qapp, mw, fill="none")
    w._min_count_spin.setValue(10_000)  # nothing reaches it: no data
    _wait(qapp, w)
    assert w._result.no_data and not w.export_map_action.isEnabled()
    assert "No cell has enough points" in w.status_text()


# -- map manager: filled cells ------------------------------------------------------------------


def test_manager_shows_filled_cells_distinctly_and_editing_makes_them_measured(qapp, mw):
    w = _open_map(qapp, mw, fill="smooth")
    assert med.perform_export(w, w._result, _values(name="eff_map"))
    mw.open_map_manager()
    mgr = mw._map_manager
    mgr.map_list.setCurrentRow(sorted(mw.function_registry.maps).index("eff_map"))
    info = next(i for i in maps_io.list_maps() if i.name == "eff_map")
    assert info.filled
    iy, ix = info.filled[0]
    measured = np.argwhere(w._result.grids[0].measured_mask)[0]
    t = mgr.preview_table
    filled_item = t.item(iy + 1, ix + 1)
    meas_item = t.item(int(measured[0]) + 1, int(measured[1]) + 1)
    assert filled_item.font().italic() and not meas_item.font().italic()
    assert filled_item.foreground().color() != meas_item.foreground().color()
    assert "Filled-in cells" in mgr.info_label.text()
    # editing a filled cell turns it into a measured one: it leaves `filled`, the others stay
    filled_item.setText("123")
    assert mgr.save_values()
    info2 = next(i for i in maps_io.list_maps() if i.name == "eff_map")
    assert [iy, ix] not in info2.filled and len(info2.filled) == len(info.filled) - 1
    assert not mgr.preview_table.item(iy + 1, ix + 1).font().italic()
    # editing a measured cell leaves `filled` alone; axis names / settings changes keep origin + filled
    meas_item = mgr.preview_table.item(int(measured[0]) + 1, int(measured[1]) + 1)
    meas_item.setText("77")
    assert mgr.save_values()
    info3 = next(i for i in maps_io.list_maps() if i.name == "eff_map")
    assert info3.filled == info2.filled and info3.origin == info.origin


def test_manager_import_without_origin_keeps_json_unchanged(qapp, mw, tmp_path):
    maps_io.import_map(_csv(tmp_path, "plain.csv", "x,y\n0,0\n1,1\n"), "plain")
    raw = json.loads((maps_io.maps_dir() / "plain.json").read_text(encoding="utf-8"))
    assert "origin" not in raw and "filled" not in raw
    info = next(i for i in maps_io.list_maps() if i.name == "plain")
    assert info.origin == {} and info.filled == []


def test_old_json_with_odd_origin_and_filled_is_tolerated(qapp, mw, tmp_path):
    maps_io.import_map(_csv(tmp_path, "odd.csv", "x,y\n0,0\n1,1\n"), "odd")
    p = maps_io.maps_dir() / "odd.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["origin"], d["filled"], d["unknown_key"] = "nope", [[1, 2], "x", [True], [1, 2, 3], [0]], 5
    p.write_text(json.dumps(d), encoding="utf-8")
    info = next(i for i in maps_io.list_maps() if i.name == "odd")
    assert info.origin == {} and info.filled == [[1, 2], [0]]


def test_compute_xy_map_cancel_and_chunking():
    n = 3000
    rng = np.random.default_rng(0)
    data = {"x": rng.uniform(0, 10, n), "y": rng.uniform(0, 10, n), "z": rng.uniform(0, 1, n)}
    fetch = lambda name, a, b: data[name][a:b]  # noqa: E731
    args = dict(x_name="x", y_names=["y"], z_name="z", kind="map", x_request=("step", GridAxisSpec("step", 0, 10, step=1)),
                y_request=("step", GridAxisSpec("step", 0, 10, step=1)), agg="mean", min_count=1, fill="none",
                row_start=0, row_end=n, scatter_limit=500)
    one = xm.compute_xy_map(fetch, chunk_rows=10**6, **args)
    many = xm.compute_xy_map(fetch, chunk_rows=250, **args)
    np.testing.assert_allclose(many.grids[0].value, one.grids[0].value, equal_nan=True)
    assert one.thinned == (len(one.points[0][0]), n) and len(one.points[0][0]) <= 500
    from parquet_analyzer.ui.analysis_window import CANCELLED

    assert xm.compute_xy_map(fetch, chunk_rows=250, cancelled=lambda: True, **args) is CANCELLED
    empty = xm.compute_xy_map(lambda n_, a, b: np.full(b - a, np.nan), chunk_rows=250, **args)
    assert empty.n_valid == 0 and not empty.grids and empty.no_data


# -- review follow-up (analysis 44.1 / 44.3 / 44.4 / 44.5) --------------------------------------


def test_grid_errors_are_translated_in_the_status_line(qapp, mw):
    """44.1: core only gives codes; the status line shows `tr()` text (Japanese when selected)."""
    from parquet_analyzer.ui import i18n

    w = _open_map(qapp, mw)
    xg = w._x_grid
    xg.touched = True
    xg.lo_edit.setText("-1")
    xg.hi_edit.setText("1")
    xg.value_edit.setText("0.0001")
    old = i18n._current_language
    try:
        i18n._current_language = "ja"
        w.recompute(keep_range=True)
        assert w.status_text() == "格子点は各軸500点までです。"
    finally:
        i18n._current_language = old
    w.recompute(keep_range=True)
    assert "500" in w.status_text() and "grid points" in w.status_text()


def test_every_grid_error_code_has_both_translations():
    import re

    from parquet_analyzer.ui import i18n

    src = Path(mg.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'MapGridError\("(\w+)"', src))
    assert {"lo_ge_hi", "non_finite", "step_not_positive", "too_few", "too_many", "not_integer"} <= codes
    for code in codes:
        entry = i18n._STRINGS[f"xy.grid_err_{code}"]
        assert entry["en"] and entry["ja"]


def test_invalid_grid_disables_the_map_export(qapp, mw):
    w = _open_map(qapp, mw)
    assert w.export_map_action.isEnabled()
    xg = w._x_grid
    xg.touched = True
    xg.value_edit.setText("abc")
    w.recompute(keep_range=True)
    assert not w.export_map_action.isEnabled()
    assert w.export_map_dialog() is False  # the dialog refuses too
    xg.lo_edit.setText("-1")
    xg.hi_edit.setText("1")
    xg.value_edit.setText("0.1")
    w.recompute(keep_range=True)
    _wait(qapp, w)
    assert w.export_map_action.isEnabled()


def test_existing_csv_asks_before_overwriting(qapp, mw, tmp_path, ask):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "m.csv"
    out.write_text("keep me\n", encoding="utf-8")
    ask.answer = No
    for register in (False, True):  # "CSV only" and "both"
        assert not med.perform_export(w, w._result, _values(register=register, save_csv=True, csv_path=str(out)))
        assert out.read_text(encoding="utf-8") == "keep me\n" and "eff_map" not in mw.function_registry.maps
    assert len(ask.calls) == 2 and str(out) in ask.calls[0]
    ask.answer = Yes
    assert med.perform_export(w, w._result, _values(register=False, save_csv=True, csv_path=str(out)))
    assert out.read_text(encoding="utf-8") != "keep me\n"
    # the file dialog already asked: no second question
    ask.calls.clear()
    assert med.perform_export(w, w._result, _values(register=False, save_csv=True, csv_path=str(out), csv_confirmed=True))
    assert ask.calls == []


def test_dialog_marks_a_browsed_path_as_confirmed(qapp, mw, tmp_path, monkeypatch):
    w = _open_map(qapp, mw)
    d = med.MapExportDialog(w, w._result)
    assert not d.values().csv_confirmed
    target = str(tmp_path / "picked.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    d._browse()
    assert d.values().csv_confirmed and d.values().csv_path == target
    d.path_edit.textEdited.emit("x.csv")  # typing afterwards: the dialog's answer no longer covers it
    assert not d.values().csv_confirmed


def test_map_overwrite_confirmation_mentions_unsaved_edits(qapp, mw, ask):
    w = _open_map(qapp, mw)
    assert med.perform_export(w, w._result, _values())
    ask.calls.clear()
    assert med.perform_export(w, w._result, _values())  # the second time overwrites the map
    assert len(ask.calls) == 1 and "nsaved" in ask.calls[0]


def test_exported_csv_round_trips_the_axis_names(qapp, mw, tmp_path):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "ax.csv"
    assert med.perform_export(w, w._result, _values(register=False, save_csv=True, csv_path=str(out)))
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("cosine\\sine,")
    back = maps_io.parse_table_csv(out)
    assert back.axis_names[:2] == ("sine", "cosine")
    w2 = _open_map(qapp, mw, x="sine", ys=("cosine",), color="single", fill="none")
    out2 = tmp_path / "cv.csv"
    assert med.perform_export(w2, w2._result, _values(name="cv", register=False, save_csv=True, csv_path=str(out2)))
    assert out2.read_text(encoding="utf-8").splitlines()[0] == "sine,cosine"
    assert maps_io.parse_table_csv(out2).axis_names[:2] == ("sine", "cosine")


def test_cursor_marker_shows_in_the_2d_map(qapp, mw):
    w = _open_map(qapp, mw)
    grid = mw.plot_grid
    grid.set_cursor_mode_enabled(True)
    t = mw._time_values
    t1 = float(t[len(t) // 4])
    grid._on_cursor_add_requested(grid.plots[0], t1)
    row = int(np.argmin(np.abs(t - t1)))
    exp = (float(mw.variable_window("sine", row, row + 1)[0]), float(mw.variable_window("cosine", row, row + 1)[0]))
    assert w.cursor_highlight_points() == [pytest.approx(exp)]
    w._points_check.setChecked(False)  # a redraw of the map keeps the marker
    assert w.cursor_highlight_points() == [pytest.approx(exp)]
    w2 = _open_map(qapp, mw, color="single")  # a curve: (x, y) of the row too
    assert len(w2.cursor_highlight_points()) == 1


def test_orphan_vertices_are_the_valued_points_without_a_face():
    # 3x3 grid, vertex number i*ny + j; the centre is empty: every square touches it, no face remains
    keep = np.ones((3, 3), dtype=bool)
    keep[1, 1] = False
    orphans = x3.orphan_vertices(np.empty((0, 3), dtype=np.uint32), keep)
    np.testing.assert_array_equal(orphans, [0, 1, 2, 3, 5, 6, 7, 8])  # all valued vertices, not the empty centre
    faces = np.array([[0, 1, 3], [1, 4, 3]])  # a vertex that is used is no orphan
    np.testing.assert_array_equal(x3.orphan_vertices(faces, np.ones((3, 3), dtype=bool)), [2, 5, 6, 7, 8])


def test_3d_surface_drops_whole_squares_touching_an_empty_point(qapp, mw, monkeypatch):
    w = _open_map_3d(qapp, mw, monkeypatch)
    view = w._view3d
    xs, ys = np.arange(4.0), np.arange(5.0)
    z = np.ones((5, 4))
    z[2, 1] = np.nan  # (iy=2, ix=1) -> vertex ix*ny + iy = 7
    view.set_surface(xs, ys, z)
    faces = view._data_items[0].opts["meshdata"].faces()
    assert len(faces) == 2 * 3 * 4 - 2 * 4  # four squares touch the empty point, two triangles each
    assert 7 not in set(faces.ravel().tolist())
    # the edge vertex (ix=0, iy=2) had only the two squares that touch the empty point: it becomes a dot
    np.testing.assert_allclose(view.orphan_points, [[0.0, 0.5, 0.5]])


def test_3d_ring_map_hands_orphan_points_to_the_view(qapp, mw, monkeypatch):
    w = _open_map_3d(qapp, mw, monkeypatch)
    view = w._view3d
    xs, ys = np.arange(4.0), np.arange(4.0)
    z = np.full((4, 4), np.nan)
    z[0, :] = 1.0  # one measured row: no square has four valued corners
    z[3, 3] = 2.0
    view.set_surface(xs, ys, z)
    assert len(view.orphan_points) == 5 and view.orphan_points.shape == (5, 3)
    assert any(type(it).__name__ == "GLScatterPlotItem" for it in view._data_items)
    view.set_surface(xs, ys, np.ones((4, 4)))  # a full grid: no orphans, the old dots are gone
    assert len(view.orphan_points) == 0
    assert not any(type(it).__name__ == "GLScatterPlotItem" for it in view._data_items)


# -- curve vertical range (analysis 44.9) -------------------------------------------------


def _fake_curve_result(values, points=None):
    x = np.arange(len(values), dtype=float)
    g = mg.GridResult.__new__(mg.GridResult)
    g.x_axis, g.value = x, np.asarray(values, dtype=float)
    r = xm.XYMapResult(kind="curve", x_name="x", y_names=["y"], z_name=None, row_start=0, row_end=1, agg="mean",
                       min_count=1, fill="none", names=["y"], grids=[g])
    if points is not None:
        r.points = [(np.zeros(len(points)), np.asarray(points, dtype=float))]
    return r


def test_curve_y_range_unit_cases():
    lo, hi = xm.curve_y_range(_fake_curve_result([0.0, 10.0]), False)
    assert (lo, hi) == pytest.approx((-0.3, 10.3))
    lo, hi = xm.curve_y_range(_fake_curve_result([5.0, 5.0, 5.0]), False)  # constant: centred, finite width
    assert (lo + hi) / 2 == pytest.approx(5.0) and hi - lo == pytest.approx(5.0 * 1.06)
    lo, hi = xm.curve_y_range(_fake_curve_result([0.0, 0.0]), False)  # all zeros: width 1
    assert (lo, hi) == pytest.approx((-0.53, 0.53))
    # NaN / Inf in the values and the points are ignored; the points widen the range
    r = _fake_curve_result([1e-16, np.nan, -1e-16, np.inf], points=[-1.0, np.nan, 1.0, -np.inf])
    lo, hi = xm.curve_y_range(r, True)
    assert (lo, hi) == pytest.approx((-1.06, 1.06))
    lo, hi = xm.curve_y_range(r, False)  # without points: ~1e-16 spread counts as constant 0 -> width 1e-16 mag
    assert np.isfinite([lo, hi]).all()
    assert xm.curve_y_range(_fake_curve_result([np.nan, np.nan]), True) is None


def test_curve_range_contains_points_on_screen_and_in_image(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine",), color="single", fill="none")
    if not w._points_check.isChecked():
        w._points_check.setChecked(True)
        _wait(qapp, w)
    r = w._result
    assert r.points
    pv = r.points[0][1]
    lo, hi = w._plot.viewRange()[1]
    assert lo <= np.nanmin(pv) and hi >= np.nanmax(pv)  # the measured points are on screen (~ +-1)
    ax = w.build_export_figure(["s"], (700, 500)).axes[0]
    assert ax.get_ylim() == pytest.approx((lo, hi), rel=1e-6)


def test_curve_image_follows_points_check_off(qapp, mw):
    """Points OFF: the image neither draws the points nor widens its range for them (screen = image, 44.9)."""
    w = _open_map(qapp, mw, x="sine", ys=("cosine",), color="single", fill="none")
    w._points_check.setChecked(False)
    _wait(qapp, w)
    assert w._result.points  # the result still carries them
    lo, hi = w._plot.viewRange()[1]
    ax = w.build_export_figure(["s"], (700, 500)).axes[0]
    assert ax.get_ylim() == pytest.approx((lo, hi), rel=1e-6)
    assert not ax.collections  # no scatter of measured points


def test_curve_range_ignores_hidden_series_and_empty_cells(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine", "noise"), color="single", fill="none")
    before = w._plot.viewRange()[1]
    name = w._result.names[0]
    w._hidden_y.add(name)
    w.recompute(keep_range=True)
    _wait(qapp, w)
    assert w._plot.viewRange()[1] == pytest.approx(before)
    r = w._result
    for g in r.grids:  # all cells empty -> no range, no exception
        g.value[:] = np.nan
    r.points = []
    assert xm.curve_y_range(r, True) is None
    fig = xm.build_xy_map_figure(r, ["s"], (700, 500))
    assert fig.axes


# -- review fixes of 2026-10-07 (analysis 44.10) --------------------------------------------------


def test_failed_registry_change_leaves_the_existing_csv_and_no_temp_file(qapp, mw, tmp_path, monkeypatch, warnings):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "m.csv"
    out.write_text("keep me\n", encoding="utf-8")

    def boom(*a, **k):
        raise maps_io.MapError("rejected")

    monkeypatch.setattr(mw, "update_registry", boom)
    assert not med.perform_export(w, w._result, _values(save_csv=True, csv_path=str(out), csv_confirmed=True))
    assert out.read_text(encoding="utf-8") == "keep me\n"
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert warnings == ["rejected"]


def test_rolled_back_registry_change_leaves_the_existing_csv(qapp, mw, tmp_path, monkeypatch, warnings):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "m.csv"
    out.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setattr(mw, "update_registry", lambda *a, **k: [("dep", "failed")])
    assert not med.perform_export(w, w._result, _values(save_csv=True, csv_path=str(out), csv_confirmed=True))
    assert out.read_text(encoding="utf-8") == "keep me\n"
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_csv_is_written_after_a_successful_registry_change(qapp, mw, tmp_path):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "ok.csv"
    assert med.perform_export(w, w._result, _values(save_csv=True, csv_path=str(out)))
    assert out.exists() and not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert next(i for i in maps_io.list_maps() if i.name == "eff_map").source_path == str(out.resolve())


def _const_z_window(qapp, mw):
    """X constant, Z = 1 + sine * 1e-15 (cell means differ by rounding noise only)."""
    mw._on_add_derived_variable("const_x", "sine * 0 + 1")
    mw._on_add_derived_variable("tiny_z", "1 + sine * 1e-15")
    return _open_map(qapp, mw, x="const_x", ys=("cosine",), z="tiny_z", fill="smooth")


def test_heat_map_colour_range_uses_the_minimum_width_rule_on_screen_and_in_the_export(qapp, mw):
    w = _const_z_window(qapp, mw)
    r = w._result
    raw = r.value_range()
    assert raw is not None and raw[1] - raw[0] < 1e-12  # the noise the old code stretched
    lo, hi = r.color_range()
    assert np.isfinite([lo, hi]).all() and hi - lo == pytest.approx(1.0)  # width = magnitude (|1|)
    on_screen = w._colorbar.levels()
    assert tuple(on_screen) == pytest.approx((lo, hi))
    fig = xm.build_xy_map_figure(r, ["s"], (700, 500))
    assert fig.axes[0].collections[0].get_clim() == pytest.approx((lo, hi))
    assert (xm.min_width_range(0.0, 0.0)) == (-0.5, 0.5)


def test_grid_too_fine_for_the_range_is_an_error():
    with pytest.raises(mg.MapGridError) as ei:
        GridAxisSpec("count", 1e15, 1e15 + 1, count=400).points()
    assert ei.value.code == "too_fine"
    with pytest.raises(mg.MapGridError) as ei:
        GridAxisSpec("step", 1e15, 1e15 + 1, step=0.0025).points()
    assert ei.value.code == "too_fine"
    assert len(GridAxisSpec("count", 0.0, 1.0, count=400).points()) == 400  # a normal grid is unaffected
    from parquet_analyzer.ui import i18n

    assert i18n._STRINGS["xy.grid_err_too_fine"]["en"] and i18n._STRINGS["xy.grid_err_too_fine"]["ja"]


def test_too_fine_grid_is_shown_in_the_panel_and_disables_export(qapp, mw):
    w = _open_map(qapp, mw)
    xg = w._x_grid
    xg.mode_combo.setCurrentIndex(xg.mode_combo.findData("count"))
    xg.touched = True
    xg.lo_edit.setText("1e15")
    xg.hi_edit.setText("1000000000000001")
    xg.value_edit.setText("400")
    w.recompute(keep_range=True)
    assert "too fine" in w.status_text()
    assert not w.export_map_action.isEnabled()


def test_empty_cell_note_only_for_fill_none_with_empty_cells(qapp, mw):
    w = _open_map(qapp, mw, fill="none")
    g = w._result.grids[0]
    assert np.isnan(g.value).any()
    pct = max(1, round(100.0 * np.isnan(g.value).sum() / g.value.size))
    d = med.MapExportDialog(w, w._result)
    assert f"{pct}%" in d.empty_note.text() and d.ok_button.isEnabled()  # a note, not a block
    d.close()
    w2 = _open_map(qapp, mw, fill="nearest")
    assert med.MapExportDialog(w2, w2._result).empty_note.text() == ""
    w._result.grids[0].value[:] = 1.0  # fill none but nothing empty
    assert med.empty_note_text(w._result) == ""


def test_curve_status_counts_rows_not_rows_times_series(qapp, mw):
    w = _open_map(qapp, mw, x="sine", ys=("cosine", "noise"), color="single", fill="smooth")
    r = w._result
    rows = r.row_end - r.row_start
    assert r.n_valid == rows and r.n_excluded == 0
    assert f"{rows}" in w.status_text() and f"{2 * rows}" not in w.status_text()


def test_3d_surface_colour_and_z_range_use_the_minimum_width_rule(qapp, mw, monkeypatch):
    """44.11: constant X, Z = rounding noise -> the Z axis and colours span min_width_range, as the image does."""
    mw._on_add_derived_variable("const_x", "sine * 0 + 1")
    mw._on_add_derived_variable("tiny_z", "1 + sine * 1e-15")
    w = _open_map_3d(qapp, mw, monkeypatch, fill="smooth", x="const_x", z="tiny_z")
    r = w._result
    raw = r.value_range()
    assert raw is not None and raw[1] - raw[0] < 1e-12
    lo, hi = r.color_range()
    assert hi - lo == pytest.approx(1.0)
    assert tuple(w._view3d.scene["bounds"]["z"]) == pytest.approx((lo, hi))
    assert tuple(w._map_bounds_3d(r)["z"]) == pytest.approx((lo, hi))
    assert tuple(w._view3d._colorbar.levels()) == pytest.approx((lo, hi))
    fig = xm.build_xy_map_figure(r, ["s"], (700, 500))
    ax = fig.axes[0]
    assert ax.get_zlim() == pytest.approx((lo, hi))
    assert ax.collections[0].get_clim() == pytest.approx((lo, hi))


# -- export publishes the CSV inside the registry persist (44.11) --------------------------------


def _fail_replace(monkeypatch, target_suffix=".csv"):
    real = med.os.replace

    def fake(src, dst, *a, **k):
        if str(dst).endswith(target_suffix) and str(src).endswith(".tmp"):
            raise OSError("disk full")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(med.os, "replace", fake)


def _maps_files():
    d = maps_io.maps_dir()
    return {p.name: p.read_bytes() for p in d.iterdir()} if d.exists() else {}


def test_failed_csv_publish_does_not_register_the_map(qapp, mw, tmp_path, monkeypatch, warnings):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "m.csv"
    out.write_text("keep me\n", encoding="utf-8")
    _fail_replace(monkeypatch)
    assert not med.perform_export(w, w._result, _values(save_csv=True, csv_path=str(out), csv_confirmed=True))
    assert "eff_map" not in mw.function_registry.maps
    assert not any(i.name == "eff_map" for i in maps_io.list_maps())
    assert _maps_files() == {}
    assert out.read_text(encoding="utf-8") == "keep me\n"
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert len(warnings) == 1 and "disk full" in warnings[0]


def test_failed_csv_publish_keeps_the_old_map_when_overwriting(qapp, mw, tmp_path, monkeypatch, warnings, ask):
    w = _open_map(qapp, mw, fill="none")
    assert med.perform_export(w, w._result, _values())
    old_table = mw.function_registry.maps["eff_map"]
    before = _maps_files()
    assert before
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    out = tmp_path / "m.csv"
    _fail_replace(monkeypatch)
    assert not med.perform_export(w, w._result, _values(save_csv=True, csv_path=str(out)))
    assert mw.function_registry.maps["eff_map"] is old_table
    assert _maps_files() == before
    assert not out.exists() and not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert len(warnings) == 1


def test_failed_csv_publish_csv_only_warns_and_leaves_no_temp(qapp, mw, tmp_path, monkeypatch, warnings):
    w = _open_map(qapp, mw, fill="none")
    out = tmp_path / "only.csv"
    _fail_replace(monkeypatch)
    assert not med.perform_export(w, w._result, _values(register=False, save_csv=True, csv_path=str(out)))
    assert not out.exists() and not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert len(warnings) == 1 and "disk full" in warnings[0]
