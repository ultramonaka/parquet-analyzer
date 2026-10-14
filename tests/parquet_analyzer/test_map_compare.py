"""Map manager: Before / After plots while editing values, linked 3D pair
(detailed_specification.md 27.9). GL cannot render offscreen: the 3D tests check the logic
(surfaces, scales, camera sync); the visuals are checked on a real display."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox
from test_functions_maps_ui import CURVE, MAP, _csv, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)

from parquet_analyzer import features
from parquet_analyzer.ui import map_manager
from parquet_analyzer.ui import xyz_3d_window as x3
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.map_manager import MapManagerWindow


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    monkeypatch.setattr(map_manager.messages, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(map_manager.messages, "warning", lambda *a, **k: None)
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))


@pytest.fixture
def heat_calls(monkeypatch):
    """Record every `_heatmap` call: (title, levels, z) -- the colour range actually used."""
    calls = []
    orig = MapManagerWindow._heatmap

    def rec(plot, xs, ys, z, levels, title, axis_labels=("X", "Y")):
        calls.append((title, tuple(levels), np.array(z)))
        return orig(plot, xs, ys, z, levels, title, axis_labels)

    monkeypatch.setattr(MapManagerWindow, "_heatmap", staticmethod(rec))
    return calls


def _open(win, tmp_path, name="mp", text=MAP):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, f"{name}.csv", text)), name)
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index(name))
    return mgr


def _set(mgr, r, c, text):
    mgr.preview_table.item(r, c).setText(text)


# -- heat maps -------------------------------------------------------------------------------


def test_editing_turns_the_two_plots_into_before_and_after_and_save_restores(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)
    assert not mgr.compare_active
    assert [c[0] for c in heat_calls[-2:]] == [f"{tr('map.preview_table')} (Z)", f"{tr('map.preview_result')} (Z)"]
    heat_calls.clear()
    _set(mgr, 1, 1, "500")  # z at (x=0, y=0): 0 -> 500
    assert mgr.compare_active
    assert [c[0] for c in heat_calls] == [f"{tr('map.before')} (Z)", f"{tr('map.after')} (Z)"]
    before_z, after_z = heat_calls[0][2], heat_calls[1][2]
    assert before_z[0, 0] == 0 and after_z[0, 0] == 500  # saved vs live values
    assert mgr.plot_result.isVisibleTo(mgr) and not mgr.compare_error
    # every further edit redraws "After" from the current cells
    heat_calls.clear()
    _set(mgr, 1, 1, "600")
    assert heat_calls[1][2][0, 0] == 600 and heat_calls[0][2][0, 0] == 0
    # save -> the normal pair again, and the saved values are the new "before"
    heat_calls.clear()
    assert mgr.save_values()
    assert not mgr.compare_active
    assert [c[0] for c in heat_calls[-2:]] == [f"{tr('map.preview_table')} (Z)", f"{tr('map.preview_result')} (Z)"]
    assert all("Before" not in c[0] and "After" not in c[0] for c in heat_calls)
    assert heat_calls[-2][2][0, 0] == 600


def test_revert_restores_the_normal_pair(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "500")
    heat_calls.clear()
    mgr.revert_values()
    assert not mgr.compare_active
    assert [c[0] for c in heat_calls] == [f"{tr('map.preview_table')} (Z)", f"{tr('map.preview_result')} (Z)"]
    assert heat_calls[0][2][0, 0] == 0


def test_editing_back_to_the_original_leaves_compare_mode(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "500")
    heat_calls.clear()
    _set(mgr, 1, 1, "0")
    assert not mgr.compare_active
    assert heat_calls[0][0].startswith(tr("map.preview_table"))


def test_colour_range_covers_both_before_and_after(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)  # saved z in 0..110
    heat_calls.clear()
    _set(mgr, 1, 1, "500")  # after reaches 500
    assert heat_calls[0][1] == heat_calls[1][1] == (0.0, 500.0)
    heat_calls.clear()
    _set(mgr, 1, 1, "-50")  # after below the saved minimum
    assert heat_calls[0][1] == heat_calls[1][1] == (-50.0, 110.0)


def test_invalid_values_do_not_draw_after_and_show_the_reason(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)
    heat_calls.clear()
    _set(mgr, 1, 1, "abc")
    assert mgr.compare_active and mgr.compare_error
    assert [c[0] for c in heat_calls] == [f"{tr('map.before')} (Z)"]  # no "After"
    assert mgr.compare_error in mgr.message.text()
    assert mgr.plot_result.getPlotItem().titleLabel.text == tr("map.after_invalid_title")
    # a non-ascending axis is just as invalid
    _set(mgr, 1, 1, "0")
    _set(mgr, 0, 2, "-5")  # x axis 0, 10 -> 0, -5
    assert mgr.compare_error
    # fixing it brings "After" back and clears the reason
    heat_calls.clear()
    _set(mgr, 0, 2, "20")
    assert not mgr.compare_error and heat_calls[-1][0] == f"{tr('map.after')} (Z)"
    assert mgr.message.text() == ""


def test_save_with_invalid_values_still_refuses(win, tmp_path):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "abc")
    assert mgr.save_values() is False and mgr.compare_active


# -- curves -------------------------------------------------------------------------------------


def test_curve_overlays_before_thin_grey_and_after_thick_colour(win, tmp_path):
    mgr = _open(win, tmp_path, "cv", CURVE)
    items = mgr.plot_raw.getPlotItem().listDataItems()
    assert len(items) == 2  # normal: interpolated line + table points
    _set(mgr, 1, 1, "200")
    items = mgr.plot_raw.getPlotItem().listDataItems()
    assert not mgr.plot_result.isVisibleTo(mgr)  # still one graph
    widths = [it.opts["pen"].width() if it.opts.get("pen") is not None else None for it in items]
    assert len(items) == 4 and widths[0] == 1 and widths[2] == 3  # before thin, after thick
    assert items[0].opts["pen"].color().name() == "#9e9e9e"  # grey
    after_y = items[2].yData
    before_y = items[0].yData
    assert not np.allclose(after_y, before_y)
    mgr.revert_values()
    assert len(mgr.plot_raw.getPlotItem().listDataItems()) == 2


def test_curve_invalid_values_draw_only_before(win, tmp_path):
    mgr = _open(win, tmp_path, "cv", CURVE)
    _set(mgr, 1, 1, "oops")
    assert len(mgr.plot_raw.getPlotItem().listDataItems()) == 2  # before line + points only
    assert mgr.compare_error and mgr.compare_error in mgr.message.text()


# -- 3D pair ---------------------------------------------------------------------------------------


def _3d(mgr):
    mgr.toggle_3d.setChecked(True)
    assert mgr.view3d is not None


def test_3d_while_editing_shows_two_surfaces_with_one_z_scale(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    assert mgr.view3d_after is None  # no edits: a single surface
    _set(mgr, 1, 1, "500")
    assert mgr.view3d is not None and mgr.view3d_after is not None
    b, a = mgr.view3d.scene, mgr.view3d_after.scene
    assert b["kind"] == a["kind"] == "surface"
    assert b["bounds"]["z"] == a["bounds"]["z"] == (0.0, 500.0)  # union of both value ranges
    # same height = same value: before's 110 sits at 110/500 and after's untouched cells too
    assert b["z"].max() == pytest.approx(110 / 500)
    assert a["z"].max() == pytest.approx(1.0)
    # a value below the saved minimum widens the union downwards
    _set(mgr, 1, 1, "-100")
    assert mgr.view3d.scene["bounds"]["z"] == mgr.view3d_after.scene["bounds"]["z"] == (-100.0, 110.0)


def test_3d_views_are_reused_across_edits_and_dropped_on_save(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    _set(mgr, 1, 1, "500")
    first, second = mgr.view3d, mgr.view3d_after
    _set(mgr, 1, 2, "7")
    assert mgr.view3d is first and mgr.view3d_after is second  # no new GL widget per edit
    assert mgr.save_values()
    assert mgr.view3d is not None and mgr.view3d_after is None  # back to one surface
    assert mgr.view3d.scene["bounds"]["z"] == (7.0, 500.0)  # saved values only (min 7 after the second edit)


def test_toggle_during_edit_keeps_the_edits_and_works_both_ways(win, tmp_path, heat_calls):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "500")
    _3d(mgr)  # heat -> 3D pair while editing
    assert mgr.view3d_after is not None and mgr.has_unsaved_edits()
    assert mgr.preview_table.item(1, 1).text() == "500"
    heat_calls.clear()
    mgr.toggle_3d.setChecked(False)  # 3D -> heat, still editing
    assert mgr.view3d is None and mgr.view3d_after is None
    assert mgr.has_unsaved_edits() and mgr.compare_active
    assert [c[0] for c in heat_calls] == [f"{tr('map.before')} (Z)", f"{tr('map.after')} (Z)"]
    assert mgr.preview_table.item(1, 1).text() == "500"


def test_3d_invalid_values_clear_after_and_show_reason(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    _set(mgr, 1, 1, "abc")
    assert mgr.view3d_after.scene == {} and mgr.view3d.scene["kind"] == "surface"
    assert mgr.compare_error in mgr.message.text()
    _set(mgr, 1, 1, "5")
    assert mgr.view3d_after.scene["kind"] == "surface" and not mgr.compare_error


def test_3d_revert_returns_to_a_single_surface(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    _set(mgr, 1, 1, "500")
    mgr.revert_values()
    assert mgr.view3d is not None and mgr.view3d_after is None
    assert mgr.view3d.scene["bounds"]["z"] == (0.0, 110.0)


# -- camera sync -----------------------------------------------------------------------------------


def test_cameras_follow_each_other_both_ways(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    _set(mgr, 1, 1, "500")
    a, b = mgr.view3d, mgr.view3d_after
    a.view.setCameraPosition(elevation=10, azimuth=75)
    assert b.view_angles() == (10.0, 75.0)
    b.view.setCameraPosition(elevation=33, azimuth=-20, distance=7.0)
    assert a.view_angles() == (33.0, -20.0)
    assert a.view.opts["distance"] == pytest.approx(b.view.opts["distance"])  # zoom follows too


def test_presets_and_home_apply_to_both_views(win, tmp_path):
    mgr = _open(win, tmp_path)
    _3d(mgr)
    _set(mgr, 1, 1, "500")
    a, b = mgr.view3d, mgr.view3d_after
    a.apply_preset("yz")
    assert a.view_angles() == b.view_angles() == x3.VIEW_PRESETS["yz"]
    assert b._hidden_ticks == a._hidden_ticks == "x"  # the depth axis' ticks are hidden on both
    b.apply_preset("zx")
    assert a.view_angles() == b.view_angles() == x3.VIEW_PRESETS["zx"]
    b.go_home()
    assert a.view_angles() == b.view_angles() == (25.0, -60.0)
    assert a._hidden_ticks is None and b._hidden_ticks is None


def test_linking_does_not_loop_or_resend_an_unchanged_camera(qapp):
    a, b = x3.Xyz3DView(), x3.Xyz3DView()
    a.link_camera(b)
    sent = []
    a.cameraChanged.connect(lambda: sent.append("a"))
    b.cameraChanged.connect(lambda: sent.append("b"))
    a.view.setCameraPosition(elevation=40)
    assert sent == ["a"]  # b adopted the state silently (no echo back)
    a.view.setCameraPosition(elevation=40)  # nothing changed
    assert sent == ["a"]
    assert b.view_angles() == a.view_angles() == (40.0, -60.0)


def test_zoom_is_shared_independent_of_view_shape(qapp):
    a, b = x3.Xyz3DView(), x3.Xyz3DView()
    a._aspect_factor, b._aspect_factor = 1.0, 2.0  # a wide view and a square one
    a.link_camera(b)
    a.view.setCameraPosition(distance=5.0)
    assert b.view.opts["distance"] == pytest.approx(10.0)  # same zoom -> scaled by its own aspect


def test_set_surface_z_range_fixes_the_scale(qapp):
    v = x3.Xyz3DView()
    z = np.array([[0.0, 1.0], [2.0, 3.0]])
    v.set_surface([0, 1], [0, 1], z)
    assert v.scene["z"].max() == pytest.approx(1.0)
    v.set_surface([0, 1], [0, 1], z, z_range=(0.0, 6.0))
    assert v.scene["bounds"]["z"] == (0.0, 6.0) and v.scene["z"].max() == pytest.approx(0.5)


def test_no_new_op_log_entries(win, tmp_path, monkeypatch):
    mgr = _open(win, tmp_path)
    ops = []
    monkeypatch.setattr(map_manager, "log_op", lambda action, **d: ops.append(action))
    _set(mgr, 1, 1, "500")
    _set(mgr, 1, 1, "abc")
    mgr.revert_values()
    assert set(ops) <= {"map_values_revert", "map_preview_3d"}


def test_replaced_plots_never_become_top_level_windows(win, tmp_path, qapp):
    """Switching maps / toggling 3D / editing replaces the plot widgets; the old ones must not
    turn into stray top-level windows (on macOS that activated the main window on every map
    selection -- user report 2026-10-03)."""
    from PySide6.QtWidgets import QApplication

    _open(win, tmp_path, "m1")
    mgr = _open(win, tmp_path, "m2")
    names = sorted(win.function_registry.maps)

    def visible_top_level_plots():
        QApplication.processEvents()  # deleteLater is not run by processEvents: old widgets still exist
        return {id(w) for w in QApplication.topLevelWidgets()
                if w.isVisible() and isinstance(w, (map_manager.pg.PlotWidget, x3.Xyz3DView))}

    before = visible_top_level_plots()  # other tests' windows may still be around

    def stray():
        return sorted(visible_top_level_plots() - before)

    for name in (names[0], names[1], names[0]):
        mgr.map_list.setCurrentRow(names.index(name))
        assert stray() == []
    _set(mgr, 1, 1, "99")
    assert stray() == []
    mgr.toggle_3d.setChecked(True)
    assert stray() == []
    mgr.revert_values()
    assert stray() == []
    mgr.toggle_3d.setChecked(False)
    assert stray() == []
