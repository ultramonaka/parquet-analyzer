"""Chapter 23: per-series select / delete / show-hide (detailed_specification.md 23,
specification.md 5.17)."""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox
from test_windowed_series import _FakeWindowSource

from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.settings import DEFAULT_SHORTCUTS
from parquet_analyzer.ui import i18n
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.plot_widget import TimePlotWidget, _SeriesSample


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


class _Ev:
    """Minimal pyqtgraph click-event stand-in."""

    def __init__(self, button=Qt.MouseButton.LeftButton, pos=None):
        self._button = button
        self._accepted = False
        self._pos = pos or QPointF(0, 0)
        self.currentItem = None

    def button(self):
        return self._button

    def accept(self):
        self._accepted = True

    def isAccepted(self):  # noqa: N802
        return self._accepted

    def scenePos(self):  # noqa: N802
        return self._pos


def _settle(qapp, n=10):
    for _ in range(n):
        qapp.processEvents()


def _drain(qapp, iterations=40, delay=0.005):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _wait_idle(qapp, win):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not win._busy_sources:
            break
    _settle(qapp)


def _legend_pair(plot, name):
    for sample, label in plot.getPlotItem().legend.items:
        if label.text == name:
            return sample, label
    raise AssertionError(name)


def _label_color(plot, name):
    import re

    from PySide6.QtGui import QColor

    html = _legend_pair(plot, name)[1].item.toHtml()
    return QColor(re.search(r"color:(#[0-9a-fA-F]{6})", html).group(1))


def _real_click(plot, item):
    """A real mouse click through the view/scene dispatch, on a legend item's centre."""
    pt = plot.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseMove(plot.viewport(), pt)
    _settle(QApplication.instance(), 3)
    QTest.mouseClick(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    _settle(QApplication.instance(), 3)


def _real_click_empty(plot):
    vb = plot.getViewBox().sceneBoundingRect()
    pt = plot.mapFromScene(QPointF(vb.center().x(), vb.bottom() - 5))
    QTest.mouseMove(plot.viewport(), pt)
    QTest.mouseClick(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    _settle(QApplication.instance(), 3)


def _click_sample(plot, name):
    sample, _ = _legend_pair(plot, name)
    sample.mouseClickEvent(_Ev())


def _click_label(plot, name):
    _, label = _legend_pair(plot, name)
    label.mouseClickEvent(_Ev())


def _plot_with(qapp, series: dict[str, np.ndarray], width=900):
    plot = TimePlotWidget("p1")
    plot.resize(width, 400)
    plot.show()
    x = np.linspace(0.0, 1000.0, 5000)
    for name, y in series.items():
        plot.add_series(name, x, y)
    _settle(qapp)
    return plot, x


def _grid_with(qapp, n_plots=2, names=("a", "b")):
    grid = PlotGridWidget()
    for _ in range(n_plots - 1):
        grid.add_plot()
    x = np.linspace(0.0, 1000.0, 5000)
    for plot in grid.plots:
        for i, n in enumerate(names):
            plot.add_series(n, x, np.sin(x / 50.0) + i)
    grid.resize(800, 600)
    grid.show()
    _settle(qapp)
    return grid


# -- 23.1 visibility ---------------------------------------------------------------------


def test_sample_click_toggles_visible_state_curve_and_label(qapp):
    plot, _ = _plot_with(qapp, {"a": np.arange(5000.0), "b": np.arange(5000.0)})
    curve = plot._series["a"]["curve"]
    changes = []
    plot.seriesVisibilityChanged.connect(lambda p, n, v: changes.append((n, v)))

    _click_sample(plot, "a")
    assert plot.is_series_visible("a") is False
    assert not curve.isVisible()
    assert changes == [("a", False)]
    hidden_c = _label_color(plot, "a")
    normal_c = _label_color(plot, "b")
    assert hidden_c.lightness() < normal_c.lightness() - 40  # clearly dimmer, not just different
    assert plot.visible_series_names() == ["b"]

    _click_sample(plot, "a")
    assert plot.is_series_visible("a") is True
    assert curve.isVisible()
    assert _label_color(plot, "a") == normal_c


def test_legend_uses_our_sample_type_not_pyqtgraphs_direct_toggle(qapp):
    plot, _ = _plot_with(qapp, {"a": np.arange(5000.0)})
    sample, _ = _legend_pair(plot, "a")
    assert isinstance(sample, _SeriesSample)
    # The curve's own visibility must not be flipped behind the flag's back.
    plot._series["a"]["visible"] = False  # flag says hidden, curve still visible
    sample.mouseClickEvent(_Ev())
    assert plot.is_series_visible("a") is True  # driven off the flag, not curve.isVisible()


def test_hidden_series_excluded_from_export_snapshot_and_cursor_label(qapp):
    plot, _ = _plot_with(qapp, {"a": np.arange(5000.0), "b": np.arange(5000.0) * 2})
    plot.add_cursor("c1", 500.0)
    label = plot._cursors["c1"]["label"]
    assert "a:" in label.toPlainText() and "b:" in label.toPlainText()

    plot.set_series_visible("a", False)
    assert [s.name for s in plot.export_snapshot().series] == ["b"]
    assert "a:" not in label.toPlainText() and "b:" in label.toPlainText()

    plot.set_series_visible("a", True)
    assert [s.name for s in plot.export_snapshot().series] == ["a", "b"]
    assert "a:" in label.toPlainText()


def test_go_home_y_range_ignores_hidden_series_and_all_hidden_uses_all(qapp):
    plot, _ = _plot_with(qapp, {"small": np.linspace(0, 1, 5000), "huge": np.linspace(0, 1000, 5000)})
    plot.go_home()
    assert plot.getViewBox().viewRange()[1][1] > 900

    plot.set_series_visible("huge", False)
    plot.go_home()
    assert plot.getViewBox().viewRange()[1][1] < 2

    plot.set_series_visible("small", False)  # all hidden -> falls back to all, no crash/empty range
    plot.go_home()
    lo, hi = plot.getViewBox().viewRange()[1]
    assert hi > 900 and hi > lo


def test_hidden_series_makes_no_jobs_and_redraws_when_shown(qapp):
    plot, x = _plot_with(qapp, {"a": np.sin(np.linspace(0, 200, 400_000))})
    # big enough that LTTB runs on a worker
    x = np.linspace(0.0, 1000.0, 400_000)
    plot.remove_series("a")
    plot.add_series("a", x, np.sin(x))
    _drain(qapp)
    plot.set_series_visible("a", False)
    plot.getViewBox().setXRange(100.0, 200.0, padding=0)
    plot.redraw()
    for _ in range(30):
        assert plot.pending_downsample_count() == 0
        qapp.processEvents()
        time.sleep(0.01)  # outlast the 120 ms redraw debounce

    plot.set_series_visible("a", True)
    assert plot.pending_downsample_count() == 1  # redrawn for the current range
    _drain(qapp)
    cx, _ = plot._series["a"]["curve"].getData()
    assert cx.min() >= 90.0 and cx.max() <= 210.0


def test_windowed_series_survives_hide_show(qapp):
    n = 50_000
    x = np.linspace(0.0, 500.0, n)
    y = np.sin(x)
    plot = TimePlotWidget("p1")
    plot.resize(900, 400)
    plot.show()
    src = _FakeWindowSource(y)
    plot.add_windowed_series("w", x, src, (-1.0, 1.0))
    _drain(qapp)
    plot.set_series_visible("w", False)
    calls_hidden = len(src.calls)
    plot.getViewBox().setXRange(10.0, 20.0, padding=0)
    _drain(qapp, 30)
    assert len(src.calls) == calls_hidden  # no fetch while hidden
    plot.set_series_visible("w", True)
    _drain(qapp)
    assert len(src.calls) > calls_hidden
    assert plot.series_source("w") is not None


# -- 23.2 selection ----------------------------------------------------------------------


def test_label_click_selects_and_reclick_clears(qapp):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    events = []
    grid.selectionChanged.connect(events.append)

    _click_label(plot, "a")
    assert grid.selected_series() == (plot, "a")
    assert plot._series["a"]["curve"].opts["pen"].widthF() == pytest.approx(2.5)
    assert plot._series["b"]["curve"].opts["pen"].widthF() == pytest.approx(1)
    assert "font-weight:700" in _legend_pair(plot, "a")[1].item.toHtml().lower()

    _click_label(plot, "a")
    assert grid.selected_series() is None
    assert plot._series["a"]["curve"].opts["pen"].widthF() == pytest.approx(1)
    assert events == [(plot, "a"), None]


def test_selecting_in_another_plot_clears_previous(qapp):
    grid = _grid_with(qapp, 2)
    p1, p2 = grid.plots
    _click_label(p1, "a")
    _click_label(p2, "b")
    assert grid.selected_series() == (p2, "b")
    assert not p1.is_series_selected("a")
    assert p1._series["a"]["curve"].opts["pen"].widthF() == pytest.approx(1)


def test_background_click_clears_selection_but_cursor_mode_click_does_not(qapp):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    _click_label(plot, "a")
    plot.set_cursor_mode_enabled(True)
    placed = []
    plot.cursorAddRequested.connect(lambda p, x: placed.append(x))
    plot._on_scene_mouse_clicked(_Ev())
    assert placed and grid.selected_series() == (plot, "a")  # cursor placed, selection kept

    plot.set_cursor_mode_enabled(False)
    plot._on_scene_mouse_clicked(_Ev())
    assert grid.selected_series() is None


def test_accepted_click_does_not_clear_selection(qapp):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    _click_label(plot, "a")
    ev = _Ev()
    ev.accept()
    plot._on_scene_mouse_clicked(ev)
    assert grid.selected_series() == (plot, "a")


def test_selection_width_kept_across_downsample_redraw(qapp):
    grid = PlotGridWidget()
    plot = grid.plots[0]
    x = np.linspace(0.0, 1000.0, 400_000)
    plot.add_series("a", x, np.sin(x))
    grid.resize(800, 400)
    grid.show()
    _drain(qapp)
    _click_label(plot, "a")
    plot.getViewBox().setXRange(100.0, 300.0, padding=0)
    plot.redraw()
    _drain(qapp)
    assert plot._series["a"]["curve"].opts["pen"].widthF() == pytest.approx(2.5)


def test_selection_cleared_when_series_or_plot_removed(qapp):
    grid = _grid_with(qapp, 2)
    p1, p2 = grid.plots
    _click_label(p1, "a")
    p1.remove_series("a")
    assert grid.selected_series() is None

    _click_label(p2, "b")
    grid.remove_plot(p2)
    assert grid.selected_series() is None


def test_real_clicks_sample_toggles_label_selects_empty_deselects(qapp):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    sample, label = _legend_pair(plot, "a")

    _real_click(plot, sample)
    assert plot.is_series_visible("a") is False
    _real_click(plot, sample)
    assert plot.is_series_visible("a") is True

    _real_click(plot, label)
    assert grid.selected_series() == (plot, "a")  # not select-then-immediately-deselect
    _real_click(plot, label)
    assert grid.selected_series() is None
    _real_click(plot, label)
    _real_click_empty(plot)
    assert grid.selected_series() is None


def test_real_clicks_in_cursor_mode(qapp):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    grid.set_cursor_mode_enabled(True)
    sample, label = _legend_pair(plot, "a")

    _real_click_empty(plot)
    assert len(plot.cursor_ids()) == 1  # empty click places a cursor

    _real_click(plot, label)
    assert grid.selected_series() == (plot, "a")
    assert len(plot.cursor_ids()) == 1  # label click placed none
    _real_click(plot, sample)
    assert plot.is_series_visible("a") is False
    assert len(plot.cursor_ids()) == 1
    assert grid.selected_series() == (plot, "a")  # and did not clear selection


# -- 23.3 delete -------------------------------------------------------------------------


def test_remove_selected_series_keeps_plot_even_when_last(qapp):
    grid = _grid_with(qapp, 1, names=("a",))
    plot = grid.plots[0]
    layouts = []
    grid.layoutChanged.connect(lambda: layouts.append(1))
    assert grid.remove_selected_series() is False  # nothing selected
    assert plot.series_names() == ["a"]

    _click_label(plot, "a")
    assert grid.remove_selected_series() is True
    assert plot.series_names() == []
    assert grid.plots == [plot]
    assert grid.selected_series() is None
    assert layouts


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path):
    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    w.load_parquet(str(sample_parquet_path))
    for n in ("a", "b"):
        w._add_variable_to_plot(w.plot_grid.plots[0], n)
    _wait_idle(qapp, w)
    yield w
    w.close()


def test_remove_series_default_shortcut_and_backspace(win):
    assert DEFAULT_SHORTCUTS["remove_series"] == "Del"
    action = win._actions["remove_series"]
    keys = [s.toString() for s in action.shortcuts()]
    assert keys == ["Del", "Backspace"]
    assert action.shortcut().toString() == "Del"


def test_remove_series_rebinding_drops_backspace_unless_del(win):
    from dataclasses import replace

    s = win.settings
    win.apply_settings(replace(s, shortcuts={**s.shortcuts, "remove_series": "X"}))
    assert [k.toString() for k in win._actions["remove_series"].shortcuts()] == ["X"]
    win.apply_settings(replace(s, shortcuts={**s.shortcuts, "remove_series": "Del"}))
    assert [k.toString() for k in win._actions["remove_series"].shortcuts()] == ["Del", "Backspace"]


@pytest.mark.parametrize("key", [Qt.Key.Key_Delete, Qt.Key.Key_Backspace])
def test_remove_series_key_press_removes_selected(qapp, win, key):
    plot = win.plot_grid.plots[0]
    win.activateWindow()
    win.setFocus()
    _click_label(plot, "a")
    QTest.keyClick(win, key)
    _settle(qapp)
    assert plot.series_names() == ["b"]
    assert win.plot_grid.plots == [plot]


def test_remove_series_key_without_selection_does_nothing(qapp, win):
    plot = win.plot_grid.plots[0]
    QTest.keyClick(win, Qt.Key.Key_Delete)
    _settle(qapp)
    assert plot.series_names() == ["a", "b"]


def test_remove_series_key_does_not_fire_in_expression_bar(qapp, win):
    plot = win.plot_grid.plots[0]
    _click_label(plot, "a")
    edit = win.expression_bar.expr_edit
    edit.setFocus()
    edit.setText("abc")
    edit.setCursorPosition(3)
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    _settle(qapp)
    assert edit.text() == "ab"
    assert plot.series_names() == ["a", "b"]
    assert win.plot_grid.selected_series() == (plot, "a")


def test_escape_clears_selection(qapp, win):
    plot = win.plot_grid.plots[0]
    _click_label(plot, "a")
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win.plot_grid.selected_series() is None


def test_selection_cleared_on_file_open_view_load_and_time_switch(qapp, win, sample_parquet_path, monkeypatch):
    grid = win.plot_grid
    plot = grid.plots[0]
    _click_label(plot, "a")
    win.set_time_column("b")  # b is plotted; switching clears selection
    assert grid.selected_series() is None

    win.set_time_column("time")
    _click_label(grid.plots[0], "a")
    win.load_parquet(str(sample_parquet_path))
    assert grid.selected_series() is None

    win._add_variable_to_plot(grid.plots[0], "a")
    _click_label(grid.plots[0], "a")
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v_sel", True)))
    win.save_view_dialog()
    _click_label(grid.plots[0], "a")
    win.load_view("v_sel")
    assert grid.selected_series() is None


# -- stats / navigator -------------------------------------------------------------------


def test_plotted_variable_names_skip_hidden(win):
    plot = win.plot_grid.plots[0]
    assert win._plotted_variable_names() == ["a", "b"]
    plot.set_series_visible("a", False)
    assert win._plotted_variable_names() == ["b"]


def test_navigator_overview_uses_first_visible_series(qapp, win):
    plot = win.plot_grid.plots[0]
    used = []
    orig = win.navigator.set_overview_data
    win.navigator.set_overview_data = lambda x, y, **kw: (used.append(np.asarray(y).copy()), orig(x, y, **kw))[1]
    _, y_b, _ = plot.series_data("b")
    _, y_a, _ = plot.series_data("a")

    plot.set_series_visible("a", False)  # -> layoutChanged -> overview refresh
    assert np.array_equal(used[-1], y_b)
    plot.set_series_visible("a", True)
    assert np.array_equal(used[-1], y_a)


# -- 23.4 context menu -------------------------------------------------------------------


def _menu_exec_picker(monkeypatch, picker):
    """Replace the QMenu used by TimePlotWidget with a subclass whose (normally modal)
    exec() records the menu and returns picker(menu)'s action. (Patching QMenu.exec itself
    is not honoured by PySide6's C++ type.)"""
    from parquet_analyzer.ui import plot_widget

    seen = {}

    class _Menu(QMenu):
        def exec(self, *a, **k):  # noqa: A003
            seen["submenus"] = {ac.text(): ac.menu() for ac in self.actions() if ac.menu() is not None}
            seen["enabled"] = {t: m.isEnabled() for t, m in seen["submenus"].items()}
            seen["texts"] = [ac.text() for ac in self.actions()]
            return picker(self)

    monkeypatch.setattr(plot_widget, "QMenu", _Menu)
    return seen


def _sub_action(menu, submenu_text, item_text):
    sub = next(ac.menu() for ac in menu.actions() if ac.text() == submenu_text)
    return next(ac for ac in sub.actions() if ac.text() == item_text)


class _CtxEvent:
    def globalPos(self):  # noqa: N802
        from PySide6.QtCore import QPoint

        return QPoint(0, 0)


def test_context_menu_delete_series(qapp, monkeypatch):
    grid = _grid_with(qapp, 1, names=("a", "b", "c"))
    plot = grid.plots[0]
    _menu_exec_picker(monkeypatch, lambda m: _sub_action(m, i18n.tr("plot.delete_series"), "b"))
    plot.contextMenuEvent(_CtxEvent())
    assert plot.series_names() == ["a", "c"]
    assert grid.plots == [plot]


def test_context_menu_show_hide_is_checkable_and_toggles(qapp, monkeypatch):
    grid = _grid_with(qapp, 1)
    plot = grid.plots[0]
    plot.set_series_visible("b", False)
    state = {}

    def pick(menu):
        sub = next(ac.menu() for ac in menu.actions() if ac.text() == i18n.tr("plot.toggle_series_visible"))
        state.update({ac.text(): (ac.isCheckable(), ac.isChecked()) for ac in sub.actions()})
        return next(ac for ac in sub.actions() if ac.text() == "b")

    _menu_exec_picker(monkeypatch, pick)
    plot.contextMenuEvent(_CtxEvent())
    assert state == {"a": (True, True), "b": (True, False)}
    assert plot.is_series_visible("b") is True


def test_context_menu_separate_one_series_keeps_color_visibility_and_kind(qapp, monkeypatch):
    grid = PlotGridWidget()
    plot = grid.plots[0]
    n = 50_000
    x = np.linspace(0.0, 500.0, n)
    plot.add_series("a", x, np.sin(x), "#112233")
    plot.add_series("b", x, np.cos(x), "#445566")
    src = _FakeWindowSource(np.sin(x * 2))
    plot.add_windowed_series("w", x, src, (-1.0, 1.0), "#778899")
    plot.set_series_visible("w", False)
    grid.resize(800, 600)
    grid.show()
    _settle(qapp)

    _menu_exec_picker(monkeypatch, lambda m: _sub_action(m, i18n.tr("plot.separate_series"), "w"))
    plot.contextMenuEvent(_CtxEvent())
    assert len(grid.plots) == 2
    new_plot = grid.plots[1]
    assert plot.series_names() == ["a", "b"] and new_plot.series_names() == ["w"]
    assert new_plot.series_data("w")[2] == "#778899"
    assert new_plot.is_series_visible("w") is False  # hidden state travels
    assert new_plot.series_source("w") is not None  # still windowed

    # a resident series keeps its kind too
    _menu_exec_picker(monkeypatch, lambda m: _sub_action(m, i18n.tr("plot.separate_series"), "a"))
    plot.contextMenuEvent(_CtxEvent())
    assert grid.plots[2].series_names() == ["a"]
    assert grid.plots[2].series_source("a") is None


def _linked_grid_zoomed(qapp):
    grid = _grid_with(qapp, 2, names=("a", "b"))
    grid.plots[0].getViewBox().setXRange(200.0, 300.0, padding=0)
    _settle(qapp)
    return grid


@pytest.mark.parametrize("which", ["series", "plot"])
def test_separate_keeps_x_zoom_of_linked_plots(qapp, which):
    grid = _linked_grid_zoomed(qapp)
    p1 = grid.plots[0]
    before = [tuple(p.getViewBox().viewRange()[0]) for p in grid.plots]
    assert before[0] == pytest.approx((200.0, 300.0))
    if which == "series":
        grid._on_separate_series_requested(p1, "b")
    else:
        grid._on_separate_requested(p1)
    _settle(qapp)
    assert len(grid.plots) == 3
    for p in grid.plots:
        assert tuple(p.getViewBox().viewRange()[0]) == pytest.approx((200.0, 300.0))
    assert grid.plots[-1].series_names() == ["b"]


def test_separate_series_submenu_disabled_with_one_series(qapp, monkeypatch):
    grid = _grid_with(qapp, 1, names=("a",))
    seen = _menu_exec_picker(monkeypatch, lambda m: None)
    grid.plots[0].contextMenuEvent(_CtxEvent())
    assert seen["enabled"][i18n.tr("plot.separate_series")] is False
    assert seen["enabled"][i18n.tr("plot.delete_series")] is True


def test_separate_series_new_plot_stays_before_fft_plots(qapp):
    grid = _grid_with(qapp, 1)
    fft = grid.add_plot(x_axis_datetime=False)
    grid.plots[0].separateSeriesRequested.emit(grid.plots[0], "b")
    assert [p.x_axis_datetime for p in grid.plots] == [True, True, False]
    assert grid.plots[-1] is fft


def test_series_submenus_disabled_on_empty_plot_and_remove_wording(qapp, monkeypatch):
    grid = PlotGridWidget()
    plot = grid.plots[0]
    seen = _menu_exec_picker(monkeypatch, lambda m: None)
    plot.contextMenuEvent(_CtxEvent())
    for key in ("plot.delete_series", "plot.toggle_series_visible", "plot.separate_series"):
        assert seen["enabled"][i18n.tr(key)] is False
    assert i18n.tr("plot.remove") in seen["texts"]
    assert i18n.tr("plot.separate") in seen["texts"]
    assert i18n._STRINGS["plot.remove"]["en"] == "Remove plot"
    assert i18n._STRINGS["plot.remove"]["ja"] == "プロットを削除"


def test_remove_plot_menu_item_still_removes_the_plot(qapp, monkeypatch):
    grid = _grid_with(qapp, 2)
    p2 = grid.plots[1]
    _menu_exec_picker(monkeypatch, lambda m: next(ac for ac in m.actions() if ac.text() == i18n.tr("plot.remove")))
    p2.contextMenuEvent(_CtxEvent())
    assert len(grid.plots) == 1


# -- 23.5 view ---------------------------------------------------------------------------


def test_view_round_trip_keeps_visible(qapp, win, monkeypatch):
    plot = win.plot_grid.plots[0]
    plot.set_series_visible("a", False)
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v_vis", True)))
    win.save_view_dialog()

    saved = view_io.load_view("v_vis")
    assert [(s.variable, s.visible) for s in saved.plots[0].series] == [("a", False), ("b", True)]

    win.load_view("v_vis")
    _wait_idle(qapp, win)
    p = win.plot_grid.plots[0]
    assert p.is_series_visible("a") is False and p.is_series_visible("b") is True
    assert not p._series["a"]["curve"].isVisible()


def test_old_view_without_visible_key_loads_all_visible(isolated_config):
    import json

    view_io.save_view(
        view_io.View(
            view_name="old",
            source=view_io.SourceDef("/x.parquet", "absolute"),
            plots=[view_io.PlotDef("p1", [view_io.SeriesDef("a", "#ff0000")])],
        )
    )
    path = view_io.view_path("old")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for s in payload["plots"][0]["series"]:
        s.pop("visible")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert view_io.load_view("old").plots[0].series[0].visible is True


# -- i18n --------------------------------------------------------------------------------


def test_new_strings_have_en_and_ja():
    for key in (
        "plot.remove", "plot.delete_series", "plot.toggle_series_visible", "plot.separate_series",
        "shortcut.remove_series",
    ):
        assert i18n._STRINGS[key]["en"] and i18n._STRINGS[key]["ja"], key
