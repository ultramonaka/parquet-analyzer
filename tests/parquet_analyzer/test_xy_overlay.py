"""Overlay of registered maps / curves on the XY window, with contour levels (analysis.ja.md 44.7).

OpenGL cannot render offscreen: the 3D overlay is checked at the item level (what is handed to the view).
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
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer import features  # noqa: E402
from parquet_analyzer.core.contour_levels import LevelSpec  # noqa: E402
from parquet_analyzer.core.maps import LookupTable  # noqa: E402
from parquet_analyzer.ui import contour_levels_dialog as cld  # noqa: E402
from parquet_analyzer.ui import xy_overlay as xo  # noqa: E402
from parquet_analyzer.ui import xy_overlay_controller as xoc  # noqa: E402
from parquet_analyzer.ui import xy_plot_window as xy_mod  # noqa: E402
from parquet_analyzer.ui import xyz_3d_window as x3  # noqa: E402
from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402


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
def ops(monkeypatch):
    recorded = []
    for mod in (xy_mod, xoc):  # the controller logs the overlay ops (44.12)
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
def mw(qapp, isolated_config, tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    win = MainWindow()
    win.show()
    assert win.load_parquet(str(path))
    win._add_variable_to_plot(win.plot_grid.plots[0], "sine")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    yield win
    win.close()


def bowl(name="bowl", n=41, nan_corner=False, axis_names=("X", "Y", "Z")):
    """z = x^2 + y^2 on [-1, 1]^2 (the sample_map.csv shape): contours are circles."""
    x = np.linspace(-1, 1, n)
    z = x[None, :] ** 2 + x[:, None] ** 2
    if nan_corner:
        z[:5, :5] = np.nan
    return LookupTable(name, "map", x, x.copy(), z, axis_names=axis_names)


def curve(name="line"):
    x = np.linspace(-1, 1, 11)
    return LookupTable(name, "curve", x, x * 0.5)


def register(mw, *tables):
    maps = dict(mw.function_registry.maps)
    for t in tables:
        maps[t.name] = t
    assert mw.update_registry(maps=maps, touched={t.name for t in tables}) == []


def unregister(mw, name):
    maps = {n: t for n, t in mw.function_registry.maps.items() if n != name}
    assert mw.update_registry(maps=maps, touched={name}) == []


def open_xy(qapp, mw):
    w = mw.open_xy_plot()
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(["cosine"])
    w._display_combo.setCurrentIndex(w._display_combo.findData("scatter"))
    w.recompute()
    _wait(qapp, w)
    _drain(qapp)
    return w


def open_3d(qapp, mw, monkeypatch):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))
    w = mw.open_xy_plot()
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    for widget in (w._x_combo, w._y_picker, w._z_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(["cosine"])
    w._z_combo.setCurrentText("offset_sine")
    for widget in (w._x_combo, w._y_picker, w._z_combo):
        widget.blockSignals(False)
    w._on_setting_changed()
    _wait(qapp, w)
    return w


def menu_entries(w):
    w._overlays.rebuild_menu()
    return {a.objectName(): a for a in w._overlays.menu.actions() if a.objectName()}


def legend_index(w, name):
    return [i for i, (_s, lab) in enumerate(w._legend.items) if lab.text.startswith(name)][0]


def click_legend_entry(qapp, w, index):
    sample, _label = w._legend.items[index]
    centre = sample.mapToScene(sample.boundingRect().center())
    QTest.mouseClick(w._glw.viewport(), Qt.MouseButton.LeftButton, pos=w._glw.mapFromScene(centre))
    _drain(qapp)


def isos(w, name):
    return [it for it in w._overlays.items[name] if isinstance(it, xo.MapIsocurveItem)]


# -- menu / add / remove ---------------------------------------------------------------------


def test_menu_lists_registered_maps_and_curves(qapp, mw):
    register(mw, bowl(), curve())
    w = open_xy(qapp, mw)
    entries = menu_entries(w)
    assert {"xy_overlay_bowl", "xy_overlay_line", "xy_overlay_levels_action"} <= set(entries)
    assert entries["xy_overlay_bowl"].isCheckable() and entries["xy_overlay_line"].isCheckable()
    assert w._overlays.menu.actions()[-1].objectName() == "xy_overlay_levels_action"  # the last item


def test_overlay_button_absent_without_the_flag(qapp, mw, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "map_from_xy", False)
    w = mw.open_xy_plot()
    assert w._overlays.menu is None and w.overlay_names() == []
    w.set_overlay("bowl", True)  # nothing to overlay with: no effect, no error
    assert w.overlay_names() == []


def test_add_and_remove_a_map_and_a_curve(qapp, mw, ops):
    register(mw, bowl(), curve())
    w = open_xy(qapp, mw)
    base_items = len(w._plot.items)
    menu_entries(w)["xy_overlay_bowl"].setChecked(True)
    menu_entries(w)["xy_overlay_line"].setChecked(True)
    assert w.overlay_names() == ["bowl", "line"]
    assert isos(w, "bowl") and len(w._plot.items) > base_items
    assert w._overlays.state.colors["bowl"] != w._overlays.state.colors["line"]
    assert w._overlays.state.colors["bowl"] == xy_mod._COLOR_CYCLE[1]  # not the Y series' colour (44.7)
    assert any(lab.text.startswith("bowl") for _s, lab in w._legend.items)
    assert any(lab.text.startswith("line") for _s, lab in w._legend.items)
    menu_entries(w)["xy_overlay_bowl"].setChecked(False)
    menu_entries(w)["xy_overlay_line"].setChecked(False)
    assert w.overlay_names() == [] and w._overlays.items == {} and len(w._plot.items) == base_items
    assert not any(lab.text in ("bowl", "line") for _s, lab in w._legend.items)
    assert ("xy_plot_overlay", {"added": ["bowl"], "removed": []}) in ops
    assert ("xy_plot_overlay", {"added": [], "removed": ["line"]}) in ops


def test_overlay_survives_a_recompute_and_a_display_change(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w._display_combo.setCurrentIndex(w._display_combo.findData("density"))
    _wait(qapp, w)
    assert isos(w, "bowl")
    assert sum(1 for _s, lab in w._legend.items if lab.text.startswith("bowl")) == 1


def test_overlay_does_not_move_the_view(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    before = w._plot.viewRange()
    w.set_overlay("bowl", True)
    assert w._plot.viewRange() == before


# -- legend --------------------------------------------------------------------------------------


def test_legend_click_hides_and_shows_the_overlay(qapp, mw, ops):
    register(mw, bowl(), curve())
    w = open_xy(qapp, mw)
    w.set_overlay("line", True)
    idx = legend_index(w, "line")
    line_items = list(w._overlays.items["line"])
    assert all(it.isVisible() for it in line_items)
    click_legend_entry(qapp, w, idx)
    assert w._overlays.state.hidden == {"line"}
    assert not any(it.isVisible() for it in w._overlays.items["line"])
    assert any(op == "xy_plot_series_visibility" and kw.get("overlay") for op, kw in ops)
    click_legend_entry(qapp, w, legend_index(w, "line"))
    assert w._overlays.state.hidden == set() and all(it.isVisible() for it in w._overlays.items["line"])


def test_hidden_map_overlay_is_not_exported_and_keeps_its_name_out_of_the_stamp(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    click_legend_entry(qapp, w, legend_index(w, "bowl"))
    assert isos(w, "bowl") == []
    assert not any("bowl" in line for line in w.export_stamp_extra())


# -- registry changes -------------------------------------------------------------------------


def test_deleting_the_map_removes_the_overlay(qapp, mw):
    register(mw, bowl(), curve())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_overlay("line", True)
    unregister(mw, "bowl")
    assert w.overlay_names() == ["line"] and "bowl" not in w._overlays.items
    assert not any(lab.text.startswith("bowl") for _s, lab in w._legend.items)
    assert "xy_overlay_bowl" not in menu_entries(w)


def test_overwriting_the_map_redraws_the_overlay(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    lv_before = list(w._overlays.state.levels["bowl"])
    big = bowl()
    big.z = big.z * 10.0
    register(mw, big)
    assert w._overlays.state.levels["bowl"] != lv_before and w.overlay_names() == ["bowl"]


def test_closing_the_window_stops_registry_notifications(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.close()
    register(mw, bowl("other"))  # must not touch the closed window
    assert w not in mw._analysis_windows


# -- 2D contours --------------------------------------------------------------------------------


def test_contours_of_x2_plus_y2_are_circles(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_contour_spec(LevelSpec("list", values=(0.25, 0.49)))
    w.set_overlay("bowl", True)
    items = isos(w, "bowl")
    assert len(items) == 2 and isinstance(items[0], xo.pg.IsocurveItem)
    for it, level in zip(sorted(items, key=lambda i: i.level), (0.25, 0.49)):
        pts = it.segments.reshape(-1, 2)
        assert len(pts) > 20
        r = np.hypot(pts[:, 0], pts[:, 1])
        assert np.allclose(r, np.sqrt(level), atol=0.02)
    labels = [it for it in w._overlays.items["bowl"] if isinstance(it, xo.pg.TextItem)]
    assert sorted(t.toPlainText() for t in labels) == ["0.25", "0.49"]  # value labels


def test_contours_skip_empty_cells(qapp, mw):
    t = bowl(nan_corner=True)
    segs_full = xo.contour_segments(bowl().x, bowl().y, bowl().z, 1.5)
    segs = xo.contour_segments(t.x, t.y, t.z, 1.5)
    assert 0 < len(segs) < len(segs_full)
    assert np.isfinite(segs).all()
    # the dropped segments are the ones in the NaN corner (x, y <= about -0.9)
    assert not ((segs[..., 0] < -0.89) & (segs[..., 1] < -0.89)).any()


def test_curve_overlay_draws_a_line_with_markers_over_a_map_heat(qapp, mw):
    register(mw, curve())
    w = open_xy(qapp, mw)
    w._display_combo.setCurrentIndex(w._display_combo.findData("map"))
    w._color_combo.setCurrentIndex(w._color_combo.findData("z"))
    w._z_combo.setCurrentText("offset_sine")
    w._on_setting_changed()
    _wait(qapp, w)
    assert "image" in w._map_items
    w.set_overlay("line", True)
    line, dots = w._overlays.items["line"]
    assert len(dots.data) == 11 and np.isfinite(line.getData()[0]).all()


def test_axis_name_mismatch_shows_in_the_legend(qapp, mw):
    register(mw, bowl("named", axis_names=("rpm", "torque", "eff")), bowl("plain"))
    w = open_xy(qapp, mw)
    w.set_overlay("named", True)
    w.set_overlay("plain", True)
    texts = [lab.text for _s, lab in w._legend.items]
    assert any("named" in t and "rpm×torque" in t for t in texts)
    assert "plain" in texts  # default axis names say nothing: no hint


# -- contour levels ---------------------------------------------------------------------------


def test_auto_levels_are_a_round_step_with_about_ten_lines(qapp, mw):
    register(mw, bowl())  # values 0..2
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    levels = w._overlays.state.levels["bowl"]
    assert 5 <= len(levels) <= 12
    assert np.allclose(np.diff(levels), 0.2)
    assert len(isos(w, "bowl")) == len(levels)


def test_interval_and_list_values_are_used_exactly(qapp, mw, ops):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_contour_spec(LevelSpec("step", step=0.5))
    assert w._overlays.state.levels["bowl"] == [0.5, 1.0, 1.5]
    assert sorted(i.level for i in isos(w, "bowl")) == [0.5, 1.0, 1.5]
    w.set_contour_spec(LevelSpec("list", values=(0.3, 0.7)))
    assert w._overlays.state.levels["bowl"] == [0.3, 0.7]
    assert sorted(i.level for i in isos(w, "bowl")) == [0.3, 0.7]
    assert ("xy_plot_contour_levels", {"mode": "step", "step": 0.5}) in ops
    assert ("xy_plot_contour_levels", {"mode": "list", "values": [0.3, 0.7]}) in ops


def test_levels_apply_to_every_overlaid_map(qapp, mw):
    register(mw, bowl(), bowl("bowl2"))
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_overlay("bowl2", True)
    w.set_contour_spec(LevelSpec("list", values=(0.5,)))
    assert w._overlays.state.levels == {"bowl": [0.5], "bowl2": [0.5]}


def test_levels_outside_the_range_draw_nothing_and_say_so(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_contour_spec(LevelSpec("list", values=(50.0,)))
    assert isos(w, "bowl") == [] and w._overlays.state.levels == {"bowl": []}
    assert "等高線の値がマップの範囲にありません" in w.status_text() or (
        "outside the map's range" in w.status_text()
    )
    w.set_contour_spec(LevelSpec())
    assert "outside the map's range" not in w.status_text() and isos(w, "bowl")


def _dialog(spec=None, ranges=None):
    return cld.ContourLevelsDialog(spec or LevelSpec(), ranges if ranges is not None else {"m": (0.0, 2.0)})


@pytest.mark.parametrize(
    ("mode", "step", "values"),
    [("step", "0", ""), ("step", "-1", ""), ("step", "abc", ""), ("step", "inf", ""), ("list", "", "a, 1"),
     ("list", "", ""), ("step", "0.001", ""), ("list", "", ", ".join(str((i + 1) / 101) for i in range(201)))],
    ids=["step0", "step-neg", "step-word", "step-inf", "list-word", "list-empty", "step-too-many", "list-too-many"],
)
def test_dialog_stays_open_with_an_error_for_bad_input(qapp, mode, step, values):
    d = _dialog()
    d.show()
    d.set_mode(mode)
    d.step_edit.setText(step)
    d.list_edit.setText(values)
    d.button_box.accepted.emit()
    assert d.result() != QDialog.DialogCode.Accepted and d.isVisible()
    assert d.error_label.text()
    d.close()


def test_dialog_accepts_valid_input(qapp):
    d = _dialog()
    d.set_mode("list")
    d.list_edit.setText("0.9, 0.8, 0.9")
    d.button_box.accepted.emit()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.spec() == LevelSpec("list", values=(0.8, 0.9))
    d2 = _dialog()
    d2.set_mode("step")
    d2.step_edit.setText("0.25")
    d2.button_box.accepted.emit()
    assert d2.spec() == LevelSpec("step", step=0.25)


def test_levels_menu_item_opens_the_dialog_and_applies_the_result(qapp, mw, monkeypatch):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    seen = {}

    def fake_exec(self):
        seen["ranges"] = dict(self._ranges)
        self.set_mode("list")
        self.list_edit.setText("0.4, 0.6")
        self._on_ok()
        return self.result()

    monkeypatch.setattr(cld.ContourLevelsDialog, "exec", fake_exec)
    menu_entries(w)["xy_overlay_levels_action"].trigger()
    assert seen["ranges"] == {"bowl": (0.0, 2.0)}
    assert w.contour_spec() == LevelSpec("list", values=(0.4, 0.6)) and w._overlays.state.levels["bowl"] == [0.4, 0.6]


def test_contour_spec_is_per_window(qapp, mw):
    register(mw, bowl())
    w1, w2 = open_xy(qapp, mw), open_xy(qapp, mw)
    w1.set_contour_spec(LevelSpec("step", step=1.0))
    assert w2.contour_spec() == LevelSpec()


# -- image export -----------------------------------------------------------------------------


def test_export_figure_has_contours_lines_stamp_and_the_same_levels(qapp, mw, tmp_path):
    register(mw, bowl(), curve())
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_overlay("line", True)
    w.set_contour_spec(LevelSpec("list", values=(0.3, 0.9, 1.2)))
    stamp = w.export_stamp_extra()
    assert stamp[-1] == "重ね描き: bowl, line" or stamp[-1] == "Overlay: bowl, line"
    fig = w.build_export_figure(stamp, (800, 600))
    ax = fig.axes[0]
    contour_sets = [c for c in ax.collections if hasattr(c, "levels")]
    assert len(contour_sets) == 1
    assert list(contour_sets[0].levels) == [0.3, 0.9, 1.2]  # the on-screen levels
    assert any(len(line.get_xdata()) == 11 for line in ax.lines)  # the curve with its 11 grid points
    legend = [t.get_text() for t in ax.get_legend().get_texts()]
    assert "bowl" in legend and "line" in legend
    # the axes keep the range of the base figure (the map is clipped to it, nothing extrapolated)
    w.set_overlay("bowl", False)
    w.set_overlay("line", False)
    base_ax = w.build_export_figure(["x"], (800, 600)).axes[0]
    assert ax.get_xlim() == pytest.approx(base_ax.get_xlim()) and ax.get_ylim() == pytest.approx(base_ax.get_ylim())
    path = tmp_path / "overlay.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000


def test_export_without_overlay_has_no_overlay_stamp(qapp, mw):
    register(mw, bowl())
    w = open_xy(qapp, mw)
    assert all("bowl" not in line for line in (w.export_stamp_extra() if isinstance(w.export_stamp_extra(), list) else [w.export_stamp_extra()]))
    fig = w.build_export_figure(["x"], (600, 400))
    assert not [c for c in fig.axes[0].collections if hasattr(c, "levels")]


def test_export_with_empty_cells_masks_them(qapp, mw):
    register(mw, bowl(nan_corner=True))
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    fig = w.build_export_figure(["x"], (600, 400))
    sets = [c for c in fig.axes[0].collections if hasattr(c, "levels")]
    assert sets
    verts = np.concatenate([p.vertices for cs in sets for p in cs.get_paths() if len(p.vertices)])
    assert len(verts) > 50
    # the NaN corner is the lower left: grid indices 0..5 (x, y <= -0.85) carry no line at all
    assert not ((verts[:, 0] < -0.86) & (verts[:, 1] < -0.86)).any()


# -- 3D (item level) ---------------------------------------------------------------------------


def test_3d_overlay_adds_one_translucent_surface_per_map_and_no_curves(qapp, mw, monkeypatch):
    register(mw, bowl(), bowl("bowl2"), curve())
    w = open_3d(qapp, mw, monkeypatch)
    view = w._view3d
    entries = menu_entries(w)
    assert not entries["xy_overlay_line"].isEnabled() and entries["xy_overlay_bowl"].isEnabled()
    w.set_overlay("line", True)  # refused in 3D
    assert w.overlay_names() == []
    w.set_overlay("bowl", True)
    assert len(view.overlay_items) == 1  # sine / cosine span [-1, 1]: the map's X / Y range is inside
    w.set_overlay("bowl2", True)
    assert len(view.overlay_items) == 2
    w.set_overlay("bowl", False)
    assert len(view.overlay_items) == 1


def test_3d_overlay_with_empty_cells_drops_their_faces(qapp, mw, monkeypatch):
    register(mw, bowl(nan_corner=True))
    w = open_3d(qapp, mw, monkeypatch)
    view = w._view3d
    w.set_overlay("bowl", True)
    (item,) = view.overlay_items
    faces = item.opts["meshdata"].faces()
    assert 0 < len(faces) < 2 * 40 * 40
    assert np.isfinite(np.asarray(item.opts["meshdata"].vertexes())).all()


def test_curve_overlay_survives_a_3d_round_trip(qapp, mw, monkeypatch):
    """44.10: the selection is kept in 3D (not drawn there) and drawn again on return to 2D."""
    register(mw, bowl(), curve())
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    w.set_overlay("line", True)
    assert "line" in w._overlays.items
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    assert w.overlay_names() == ["bowl", "line"]
    assert "line" not in w._overlays.items  # a curve is not drawn in 3D
    QTest.mouseClick(w._mode_buttons["2d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    _drain(qapp)
    assert w.overlay_names() == ["bowl", "line"]
    assert "line" in w._overlays.items and len(w._overlays.items["line"]) > 0


def test_3d_export_with_overlay_renders(qapp, mw, monkeypatch, tmp_path):
    register(mw, bowl())
    w = open_3d(qapp, mw, monkeypatch)
    w.set_overlay("bowl", True)
    path = tmp_path / "o3d.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000


# -- review follow-ups ---------------------------------------------------------------------------


def test_overlay_colours_avoid_the_y_series_and_the_menu_shows_a_swatch(qapp, mw):
    register(mw, bowl(), bowl("bowl2"))
    w = open_xy(qapp, mw)  # one Y: it uses cycle[0]
    w.set_overlay("bowl", True)
    assert w._overlays.state.colors["bowl"] == xy_mod._COLOR_CYCLE[1]  # orange
    w.set_overlay("bowl2", True)
    assert w._overlays.state.colors["bowl2"] == xy_mod._COLOR_CYCLE[2]
    entries = menu_entries(w)
    pix = entries["xy_overlay_bowl"].icon().pixmap(14, 14).toImage()
    assert pix.pixelColor(7, 7).name() == w._overlays.state.colors["bowl"]
    assert entries["xy_overlay_bowl2"].icon().pixmap(14, 14).toImage().pixelColor(7, 7).name() == xy_mod._COLOR_CYCLE[2]
    w.set_overlay("bowl", False)
    assert menu_entries(w)["xy_overlay_bowl"].icon().isNull()  # no swatch for what is not overlaid


def test_two_ys_push_the_first_overlay_to_the_third_colour(qapp, mw):
    register(mw, bowl())
    w = mw.open_xy_plot()
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(["cosine", "noise"])
    w.set_overlay("bowl", True)
    assert w._overlays.state.colors["bowl"] == xy_mod._COLOR_CYCLE[2]


def test_3d_overlay_widens_z_to_fit_the_map_and_shrinks_back(qapp, mw, monkeypatch):
    register(mw, bowl())  # values 0..2, the measured offset_sine is around 10
    w = open_3d(qapp, mw, monkeypatch)
    view = w._view3d
    z0 = view.scene["bounds"]["z"]
    assert z0[0] > 5
    w.set_overlay("bowl", True)
    zlo, zhi = view.scene["bounds"]["z"]
    assert zlo <= 0.0 and zhi >= z0[1]  # nothing of the map is cut off
    (item,) = view.overlay_items
    verts = np.asarray(item.opts["meshdata"].vertexes())
    assert verts[:, 2].min() >= 0.0 and verts[:, 2].max() <= 1.0 + 1e-6  # all inside the normalised frame
    assert np.isclose(verts[:, 2].max(), (2.0 - zlo) / (zhi - zlo), atol=1e-4)  # the peak is there, not clipped
    w.set_overlay("bowl", False)
    assert view.scene["bounds"]["z"] == pytest.approx(z0) and view.overlay_items == []


def test_3d_map_display_widens_z_too(qapp, mw, monkeypatch):
    register(mw, bowl())
    w = open_3d(qapp, mw, monkeypatch)
    w._kind3d_combo.setCurrentIndex(w._kind3d_combo.findData("map"))
    _wait(qapp, w)
    assert w._result.is_3d and w._result.kind == "map"
    w.set_overlay("bowl", True)
    zlo, zhi = w._view3d.scene["bounds"]["z"]
    assert zlo <= 0.0 and zhi >= w._result.value_range()[1]
    assert len(w._view3d.overlay_items) == 1


def test_legend_hidden_overlay_is_drawn_again_in_3d(qapp, mw, monkeypatch):
    register(mw, bowl())
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))
    w = open_xy(qapp, mw)
    w.set_overlay("bowl", True)
    click_legend_entry(qapp, w, legend_index(w, "bowl"))
    assert w._overlays.state.hidden == {"bowl"}
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    assert w.overlay_names() == ["bowl"] and menu_entries(w)["xy_overlay_bowl"].isChecked()
    assert len(w._view3d.overlay_items) == 1  # 3D has no legend to bring it back: the hidden flag is ignored
    assert any("bowl" in line for line in w.export_stamp_extra())


def test_curve_tooltip_in_3d_can_be_shown(qapp, mw, monkeypatch):
    register(mw, curve())
    w = open_3d(qapp, mw, monkeypatch)
    assert w._overlays.menu.toolTipsVisible()
    assert menu_entries(w)["xy_overlay_line"].toolTip()


def test_export_legend_keeps_the_cjk_fallback_font(qapp, mw, monkeypatch):
    from parquet_analyzer.ui import i18n

    i18n.set_language("ja")
    try:
        register(mw, bowl("named", axis_names=("回転数", "トルク", "効率")))
        w = open_xy(qapp, mw)
        w.set_overlay("named", True)
        fig = w.build_export_figure(["x"], (800, 600))
        texts = fig.axes[0].get_legend().get_texts()
        assert any("軸名" in t.get_text() for t in texts)
        for t in texts:
            assert t.get_fontfamily()[0] == "DejaVu Sans" and len(t.get_fontfamily()) >= 1
            assert t.get_fontfamily() != ["sans-serif"]
    finally:
        i18n.set_language("en")


def test_second_refresh_does_not_recompute_contours(qapp, mw, monkeypatch):
    register(mw, bowl(), bowl("bowl2"))
    w = open_xy(qapp, mw)
    calls = []
    real = xo.contour_segments
    monkeypatch.setattr(xo, "contour_segments", lambda *a, **k: calls.append(1) or real(*a, **k))
    w.set_overlay("bowl", True)
    n = len(calls)
    assert n == len(w._overlays.state.levels["bowl"]) > 0
    w._overlays.refresh()
    w._overlays.refresh()
    assert len(calls) == n  # cached per table object and level
    w.set_overlay("bowl2", True)  # an unrelated map being added / registry notifications keep the cache
    register(mw, bowl("bowl3"))
    assert len(calls) == n + len(w._overlays.state.levels["bowl2"])
    w._overlays.refresh()
    assert len(calls) == n + len(w._overlays.state.levels["bowl2"])
    big = bowl()
    big.z = big.z * 10.0
    register(mw, big)  # an overwritten map is a new object: recomputed
    assert len(calls) > n + len(w._overlays.state.levels["bowl2"])


def test_a_failing_window_hook_does_not_break_update_registry(qapp, mw, monkeypatch):
    w = open_xy(qapp, mw)
    monkeypatch.setattr(w, "on_registry_changed", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    register(mw, bowl())  # asserts update_registry returned []
    assert "bowl" in mw.function_registry.maps
