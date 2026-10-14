"""Side-effect tests: a button/menu action changes only what it should (app 5.36, chapter 45).

One parametrized test per row of the 45.2 table. Each row's `case_*` function prepares the
(not yet counted) setup, then returns the action to run plus its allowed-change patterns and
dialog answers; `ctx.run` does the before/after diff and the watchers (side_effects_helper).
A row whose real behaviour differs from the table is marked `xfail(strict=True)` with the
side effect found -- never loosened (45.2, "表と実際が合わないとき").
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from side_effects_helper import settle
from side_effects_helper_start import ctx, log  # noqa: F401  (fixtures)

from parquet_analyzer.core.oplog import diff_states  # noqa: F401  (the diff the harness uses, 30.2)
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.stats_dialog import StatsDialog

# Companion text items that follow their numeric twin (derived from x_range / region).
X = ["plots.*.x_range", "plots.*.x_range_text"]
REGION = ["navigator.region", "navigator.region_text"]
POINTS = ["plots.*.series_info.*.points", "plots.*.series_info.*.coarse"]
Y = ["plots.*.y_range"]


def _pid(plot) -> str:
    return plot.plot_id


def _plot_paths(plot, *tails):
    return [f"plots.{_pid(plot)}.{t}" for t in tails]


def _wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the action's window/result")
        time.sleep(0.005)


# -- rows: each returns kwargs for ctx.run (action, allowed, answers, expected_dialogs) ----


def case_home(c):
    return dict(action=c.win._actions["home"].trigger, allowed=X + Y + REGION + POINTS)


def case_redraw(c):
    return dict(action=c.win._actions["redraw"].trigger, allowed=POINTS + Y)


def case_add_plot(c):
    known = set(p.plot_id for p in c.win.plot_grid.plots)

    def allowed():
        new = [p.plot_id for p in c.win.plot_grid.plots if p.plot_id not in known]
        assert len(new) == 1
        return [f"plots.{new[0]}", f"plots.{new[0]}.*", "plots.*.height_ratio"]

    return dict(action=c.win._actions["add_plot"].trigger, allowed=allowed)


def case_stats(c):
    def action():
        c.win._actions["stats"].trigger()
        _wait_for(lambda: c.win.findChildren(StatsDialog))

    return dict(action=action, allowed=[])


def _graphs_menu_action(c, key):
    return c.win._actions[key]


def case_graphs_xy(c):
    return dict(action=_graphs_menu_action(c, "xy_plot").trigger, allowed=[])


def case_graphs_histogram(c):
    return dict(action=_graphs_menu_action(c, "histogram").trigger, allowed=[])


def case_functions_open_close(c):
    def action():
        c.win._actions["user_functions"].trigger()
        mgr = c.win._function_manager
        assert mgr is not None and mgr.isVisible()
        mgr.close()

    return dict(action=action, allowed=[])


def case_functions_add_and_save(c):
    def action():
        c.win._actions["user_functions"].trigger()
        mgr = c.win._function_manager
        mgr.new_function()
        mgr.name_edit.setText("dbl_fn")  # not used by any plotted variable
        mgr.params_edit.setText("p")
        mgr.expression_edit.setText("p * 2")
        QTest.mouseClick(mgr.save_button, Qt.MouseButton.LeftButton)
        assert "dbl_fn" in c.win.function_registry.user_functions
        mgr.close()

    return dict(action=action, allowed=[])


def case_maps_open_close(c):
    def action():
        c.win._actions["maps"].trigger()
        mgr = c.win._map_manager
        assert mgr is not None and mgr.isVisible()
        mgr.close()

    return dict(action=action, allowed=[])


def _m_paths(c):
    """The derived variable m (plot 1) and its plot's y range: what a map change may touch."""
    p = _pid(c.plot1)
    return [f"plots.{p}.series_info.m.points", f"plots.{p}.series_info.m.coarse", f"plots.{p}.y_range"]


def case_map_edit_save(c):
    def action():
        c.win._actions["maps"].trigger()
        mgr = c.win._map_manager
        mgr.map_list.setCurrentRow(sorted(c.win.function_registry.maps).index("cv"))
        before = c.win.variable_values("m").copy()
        mgr.preview_table.item(1, 1).setText("50")  # (0, 0) -> (0, 50)
        QTest.mouseClick(mgr.save_values_button, Qt.MouseButton.LeftButton)
        assert float(c.win.function_registry.maps["cv"].y[1]) == 50.0
        assert not (before == c.win.variable_values("m")).all()  # the derived values really changed
        mgr.close()

    return dict(action=action, allowed=_m_paths(c))


def case_map_reimport(c):
    def action():
        c.win._actions["maps"].trigger()
        mgr = c.win._map_manager
        mgr.map_list.setCurrentRow(sorted(c.win.function_registry.maps).index("cv"))
        QTest.mouseClick(mgr.reimport_button, Qt.MouseButton.LeftButton)
        mgr.close()

    return dict(action=action, allowed=_m_paths(c))


def case_save_view(c):
    return dict(
        action=c.win._actions["save_view"].trigger,
        allowed=[],
        answers={"QInputDialog.getItem": ("side_effect_view", True)},
        expected_dialogs=["QInputDialog.getItem", "QMessageBox.information"],
    )


def case_export_image(c):
    out = str(c.tmp_path / "export.png")
    return dict(
        action=c.win._actions["export_image"].trigger,
        allowed=[],
        answers={"QFileDialog.getSaveFileName": (out, "")},
        expected_dialogs=["QFileDialog.getSaveFileName"],
    )


def case_toggle_downsample(c):
    return dict(action=c.win._actions["toggle_downsample"].trigger, allowed=["downsample_enabled"] + POINTS)


def case_cursor_toggle(c):
    return dict(action=c.win._actions["cursor"].trigger, allowed=["cursors.*"])


def case_hover_toggle(c):
    return dict(action=c.win._actions["hover_values"].trigger, allowed=["hover_values"])


def case_settings_ok(c, monkeypatch):
    # The real dialog is modal (exec); answer it "OK" without touching any field.
    monkeypatch.setattr(main_window_module.SettingsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    action = next(a for a in c.win.findChildren(QAction) if a.text() == tr("toolbar.settings_item"))
    return dict(action=action.trigger, allowed=[])


def case_delete_series(c):
    plot = c.plot1
    c.win.plot_grid.select_series(plot, "b")
    settle(c.win)
    p = _pid(plot)

    def action():
        c.win.activateWindow()
        c.win.setFocus()
        QTest.keyClick(c.win, Qt.Key.Key_Delete)
        if "b" in plot.series_names():  # the shortcut did not fire without window activation
            c.win._actions["remove_series"].trigger()
        assert "b" not in plot.series_names()

    return dict(
        action=action,
        allowed=[f"plots.{p}.series", f"plots.{p}.series_info.b", f"plots.{p}.series_info.b.*", f"plots.{p}.y_range",
                 "plots.*.series_info.*.selected"],
    )


def _legend_pair(plot, name):
    for sample, label in plot.getPlotItem().legend.items:
        if label.text == name:
            return sample, label
    raise AssertionError(name)


def case_legend_hide(c):
    plot = c.plot1
    p = _pid(plot)

    def action():
        sample, _ = _legend_pair(plot, "b")
        pt = plot.mapFromScene(sample.sceneBoundingRect().center())
        QTest.mouseMove(plot.viewport(), pt)
        QApplication.processEvents()
        QTest.mouseClick(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        assert plot.is_series_visible("b") is False

    return dict(action=action, allowed=[f"plots.{p}.series_info.b.visible", f"plots.{p}.series_info.b.points"])


def case_expression_add_unplotted(c):
    def action():
        bar = c.win.expression_bar
        bar.name_edit.setText("s1")
        bar.expr_edit.setText("a + b")
        QTest.mouseClick(bar.add_button, Qt.MouseButton.LeftButton)
        assert "s1" in c.win._derived

    return dict(action=action, allowed=[])


def case_expression_var_to_plot(c):
    c.win._on_add_derived_variable("s1", "a + b")  # setup: defined, not plotted
    settle(c.win)
    plot = c.plot2
    p = _pid(plot)
    return dict(
        action=lambda: plot.variableDropped.emit(plot, "s1"),  # what dropping the name on the plot sends
        allowed=[f"plots.{p}.series", f"plots.{p}.series_info.s1", f"plots.{p}.series_info.s1.*", f"plots.{p}.y_range"],
    )


def _drag(widget, p0: QPoint, p1: QPoint, steps: int = 6):
    """Press, move in steps, release -- real mouse events to the viewport."""
    btn = Qt.MouseButton.LeftButton
    mods = Qt.KeyboardModifier.NoModifier

    def ev(kind, pos, buttons):
        return QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), btn, buttons, mods)

    QApplication.sendEvent(widget, ev(QEvent.Type.MouseButtonPress, p0, btn))
    for i in range(1, steps + 1):
        pos = QPoint(p0.x() + (p1.x() - p0.x()) * i // steps, p0.y() + (p1.y() - p0.y()) * i // steps)
        QApplication.sendEvent(widget, ev(QEvent.Type.MouseMove, pos, btn))
        QApplication.processEvents()
    QApplication.sendEvent(widget, ev(QEvent.Type.MouseButtonRelease, p1, Qt.MouseButton.NoButton))


def case_navigator_drag(c):
    nav = c.win.navigator

    def action():
        lo, hi = nav.selected_range()
        vb = nav.getViewBox()
        y_mid = vb.viewRect().center().y()
        x_px = lambda x: nav.mapFromScene(vb.mapViewToScene(QPointF(x, y_mid))).x()  # noqa: E731
        y_px = nav.mapFromScene(vb.mapViewToScene(QPointF(lo, y_mid))).y()
        start = QPoint(int((x_px(lo) + x_px(hi)) / 2), y_px)
        end = QPoint(start.x() + 60, y_px)
        _drag(nav.viewport(), start, end)
        assert nav.selected_range()[0] > lo  # the region really moved

    return dict(action=action, allowed=X + Y + POINTS + REGION)


def _open_analysis(c, key):
    for item in c.win.variable_panel.findItems("a", Qt.MatchFlag.MatchExactly) + c.win.variable_panel.findItems(
        "b", Qt.MatchFlag.MatchExactly
    ):
        item.setSelected(True)
    c.win._actions[key].trigger()
    window = c.win._analysis_windows[-1]
    _wait_for(lambda: not c.win._busy_sources)
    settle(c.win)
    return window


def case_recompute_xy(c):
    window = _open_analysis(c, "xy_plot")
    return dict(action=window.recompute_action.trigger, allowed=[])


def case_recompute_histogram(c):
    window = _open_analysis(c, "histogram")
    return dict(action=window.recompute_action.trigger, allowed=[])


CASES = {
    "home": case_home,
    "redraw": case_redraw,
    "add_plot": case_add_plot,
    "stats": case_stats,
    "graphs_xy": case_graphs_xy,
    "graphs_histogram": case_graphs_histogram,
    "functions_open_close": case_functions_open_close,
    "functions_add_and_save": case_functions_add_and_save,
    "maps_open_close": case_maps_open_close,
    "map_edit_save": case_map_edit_save,
    "map_reimport": case_map_reimport,
    "save_view": case_save_view,
    "export_image": case_export_image,
    "toggle_downsample": case_toggle_downsample,
    "cursor_toggle": case_cursor_toggle,
    "hover_toggle": case_hover_toggle,
    "settings_ok": case_settings_ok,
    "delete_series": case_delete_series,
    "legend_hide": case_legend_hide,
    "expression_add_unplotted": case_expression_add_unplotted,
    "expression_var_to_plot": case_expression_var_to_plot,
    "navigator_drag": case_navigator_drag,
    "recompute_xy": case_recompute_xy,
    "recompute_histogram": case_recompute_histogram,
}

# Rows whose real behaviour differs from the table (45.2: judged by the main session); a bug
# found this way is marked xfail(strict=True) with its dev notes task. Never loosen the allowed set instead.
XFAIL: dict[str, str] = {}


@pytest.mark.parametrize(
    "name",
    [pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=XFAIL[n])) if n in XFAIL else n for n in CASES],
)
def test_action_has_no_side_effects(ctx, monkeypatch, name):
    case = CASES[name]
    kwargs = case(ctx, monkeypatch) if name == "settings_ok" else case(ctx)
    ctx.run(name, **kwargs)
