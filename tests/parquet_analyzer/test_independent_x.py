"""Chapter 24: independent X axis per plot (detailed_specification.md 24,
specification.md 5.18)."""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QTableWidget

from parquet_analyzer import features
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui import i18n
from parquet_analyzer.ui.image_export import PlotSnapshot, SeriesSnapshot, build_figure
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture(autouse=True)
def _flag_and_no_modals(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "independent_x_axis", True)
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


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


def _xr(plot):
    lo, hi = plot.getViewBox().viewRange()[0]
    return round(lo, 6), round(hi, 6)


def _grid(qapp, n=3):
    grid = PlotGridWidget()
    for _ in range(n - 1):
        grid.add_plot()
    x = np.linspace(0.0, 1000.0, 5000)
    for plot in grid.plots:
        plot.add_series("s", x, np.sin(x / 50.0))
    grid.resize(800, 600)
    grid.show()
    _settle(qapp)
    return grid


class _CtxEvent:
    def globalPos(self):  # noqa: N802
        return QPoint(0, 0)


def _menu_picker(monkeypatch, text=None):
    """Swap the QMenu TimePlotWidget uses for one whose exec() records the top-level
    actions ({text: (enabled, checked, checkable)}) and returns the action called `text`
    (None -> cancel). Patching QMenu.exec on the class is not honoured by PySide6."""
    from parquet_analyzer.ui import plot_widget

    seen = {}

    class _Menu(QMenu):
        def exec(self, *a, **k):  # noqa: A003
            seen["actions"] = {
                ac.text(): (ac.isEnabled(), ac.isChecked(), ac.isCheckable())
                for ac in self.actions()
                if ac.text()
            }
            return next((ac for ac in self.actions() if text is not None and ac.text() == text), None)

    monkeypatch.setattr(plot_widget, "QMenu", _Menu)
    return seen


# -- 24.1 state and links --------------------------------------------------------------


def test_unlinked_plot_zooms_independently_and_relink_snaps_to_group(qapp):
    grid = _grid(qapp, 2)
    top, bottom = grid.plots
    assert grid.set_plot_x_linked(bottom, False) is True
    assert bottom.x_linked is False and top.x_linked is True

    bottom.setXRange(100, 200, padding=0)
    assert _xr(top) != (100.0, 200.0)
    top.setXRange(300, 400, padding=0)
    assert _xr(top) == (300.0, 400.0)
    assert _xr(bottom) == (100.0, 200.0)  # not dragged along

    assert grid.set_plot_x_linked(bottom, True)
    assert _xr(bottom) == (300.0, 400.0)  # snapped to the group, the group untouched
    assert _xr(top) == (300.0, 400.0)
    top.setXRange(10, 20, padding=0)
    assert _xr(bottom) == (10.0, 20.0)  # linked again


def test_relinking_the_first_plot_does_not_move_the_group(qapp):
    """The re-linked plot becomes the anchor again; it must adopt the group's range, not
    impose its own on the others."""
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    grid.set_plot_x_linked(p1, False)
    p1.setXRange(0, 50, padding=0)
    p2.setXRange(500, 600, padding=0)
    grid.set_plot_x_linked(p1, True)
    assert grid.anchor_plot() is p1
    assert _xr(p1) == _xr(p2) == _xr(p3) == (500.0, 600.0)


def test_last_linked_plot_cannot_be_unlinked(qapp):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    assert grid.set_plot_x_linked(p2, False)
    assert grid.can_unlink(p1) is False
    assert grid.set_plot_x_linked(p1, False) is False
    assert p1.x_linked is True
    single = _grid(qapp, 1)
    assert single.can_unlink(single.plots[0]) is False
    assert single.set_plot_x_linked(single.plots[0], False) is False


def test_unlinking_the_anchor_promotes_the_next_linked_plot(qapp):
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    ranges = []
    grid.rangeChanged.connect(ranges.append)
    assert grid.set_plot_x_linked(p1, False)
    assert grid.anchor_plot() is p2
    assert ranges and ranges[-1] == tuple(p2.getViewBox().viewRange()[0])  # navigator told

    p2.setXRange(100, 200, padding=0)
    assert _xr(p3) == (100.0, 200.0)  # p3 follows the new anchor
    assert _xr(p1) != (100.0, 200.0)  # p1 no longer does (stale link to p2 cleared)

    ranges.clear()
    p1.setXRange(5, 6, padding=0)
    _settle(qapp)
    assert ranges == []  # an independent plot never drives the navigator
    p2.setXRange(300, 400, padding=0)
    assert ranges and ranges[-1] == (300.0, 400.0)
    assert _xr(p3) == (300.0, 400.0) and _xr(p1) == (5.0, 6.0)


def test_navigator_range_only_moves_the_group(qapp):
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(1, 2, padding=0)
    grid.set_x_range(400, 500)
    assert _xr(p1) == _xr(p3) == (400.0, 500.0)
    assert _xr(p2) == (1.0, 2.0)


def test_unlinked_first_plot_is_not_the_anchor_after_reorder_and_remove(qapp):
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    grid.set_plot_x_linked(p2, False)
    grid.move_plot_to(p2, 0)  # [p2, p1, p3]: the independent plot is first
    assert grid.plots[0] is p2
    assert grid.anchor_plot() is p1
    p1.setXRange(100, 200, padding=0)
    assert _xr(p3) == (100.0, 200.0) and _xr(p2) != (100.0, 200.0)

    grid.remove_plot(p1)  # remove the anchor: p3 takes over, p2 stays independent
    assert grid.anchor_plot() is p3
    p3.setXRange(7, 8, padding=0)
    assert _xr(p2) != (7.0, 8.0)


def test_removing_the_last_linked_plot_keeps_one_linked(qapp):
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    grid.set_plot_x_linked(p2, False)
    grid.set_plot_x_linked(p3, False)
    grid.remove_plot(p1)
    assert grid.linked_plots() == [p2]
    assert grid.anchor_plot() is p2


def test_fft_plot_is_never_linked(qapp):
    grid = _grid(qapp, 2)
    fft = grid.add_plot(x_axis_datetime=False)
    assert fft.x_linked is False
    assert grid.set_plot_x_linked(fft, True) is False
    assert fft not in grid.linked_plots()
    assert grid.anchor_plot() in grid.plots[:2]


def test_added_plot_is_linked_to_the_anchor_even_if_first_is_independent(qapp):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    grid.set_plot_x_linked(p1, False)
    p3 = grid.add_plot()
    p3.add_series("s", np.linspace(0, 1000, 100), np.zeros(100))
    assert p3.x_linked and grid.anchor_plot() is p2
    _settle(qapp)  # a just-added plot is laid out lazily (its ViewBox width differs until then; seen on Windows)
    p2.setXRange(11, 22, padding=0)
    assert _xr(p3) == (11.0, 22.0)


# -- 22.4 time labels -------------------------------------------------------------------


def test_tick_labels_follow_real_link_state(qapp):
    grid = _grid(qapp, 3)
    p1, p2, p3 = grid.plots
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, False, True]
    grid.set_plot_x_linked(p2, False)
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, True, True]
    grid.set_plot_x_linked(p3, False)  # p1 is the only linked plot: it shows the group's labels
    assert [p.x_tick_labels_visible() for p in grid.plots] == [True, True, True]
    grid.set_plot_x_linked(p2, True)
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, True, True]  # p1,p2 linked; p3 independent


# -- 24.3 UI: chain button and menu -----------------------------------------------------


def test_chain_click_unlinks_and_relinks_through_real_mouse_events(qapp):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    handle = grid._panes[p2].handle
    assert handle.chain.isVisibleTo(handle) and handle.chain.isChecked()
    QTest.mouseClick(handle.chain, Qt.LeftButton)
    _settle(qapp)
    assert p2.x_linked is False
    assert not handle.chain.isChecked()
    assert handle.x_linked is False  # tinted background state
    QTest.mouseClick(handle.chain, Qt.LeftButton)
    _settle(qapp)
    assert p2.x_linked is True and handle.chain.isChecked()


def test_chain_of_the_last_linked_plot_is_disabled_and_ignores_clicks(qapp):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    grid.set_plot_x_linked(p2, False)
    chain = grid._panes[p1].handle.chain
    assert not chain.isEnabled()
    QTest.mouseClick(chain, Qt.LeftButton)
    _settle(qapp)
    assert p1.x_linked is True and chain.isChecked()
    assert grid._panes[p2].handle.chain.isEnabled()  # the independent one can re-link


def test_chain_syncs_with_menu_changes(qapp, monkeypatch):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    _menu_picker(monkeypatch, i18n.tr("plot.link_x"))
    p2.contextMenuEvent(_CtxEvent())
    assert p2.x_linked is False
    assert not grid._panes[p2].handle.chain.isChecked()
    p2.contextMenuEvent(_CtxEvent())  # now checked -> toggles back
    assert p2.x_linked is True and grid._panes[p2].handle.chain.isChecked()


def test_menu_item_checked_state_and_disabled_for_last_linked(qapp, monkeypatch):
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    link = i18n.tr("plot.link_x")
    seen = _menu_picker(monkeypatch)
    p1.contextMenuEvent(_CtxEvent())
    assert seen["actions"][link] == (True, True, True)
    assert i18n.tr("plot.stats_this_range") not in seen["actions"]  # linked plots don't get it
    grid.set_plot_x_linked(p2, False)
    p1.contextMenuEvent(_CtxEvent())
    assert seen["actions"][link][0] is False and seen["actions"][link][1] is True
    p2.contextMenuEvent(_CtxEvent())
    assert seen["actions"][link] == (True, False, True)
    assert i18n.tr("plot.stats_this_range") in seen["actions"]


def test_fft_plot_menu_has_no_link_item(qapp, monkeypatch):
    grid = _grid(qapp, 1)
    fft = grid.add_plot(x_axis_datetime=False)
    seen = _menu_picker(monkeypatch)
    fft.contextMenuEvent(_CtxEvent())
    assert i18n.tr("plot.link_x") not in seen["actions"]
    assert not grid._panes[fft].handle.chain.isVisibleTo(grid._panes[fft].handle)


def test_flag_off_hides_menu_item_and_chain_but_mechanism_works(qapp, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "independent_x_axis", False)
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    seen = _menu_picker(monkeypatch)
    p2.contextMenuEvent(_CtxEvent())
    assert i18n.tr("plot.link_x") not in seen["actions"]
    handle = grid._panes[p2].handle
    assert not handle.chain.isVisibleTo(handle)
    # The internal mechanism still works (e.g. what load_view relies on) ...
    assert grid.set_plot_x_linked(p2, False)
    # ... and an independent plot is still tinted and offers "stats for this range".
    assert handle.x_linked is False
    p2.contextMenuEvent(_CtxEvent())
    assert i18n.tr("plot.stats_this_range") in seen["actions"]


def test_strings_have_en_and_ja():
    for key in ("plot.link_x", "plot.link_x_tooltip", "plot.stats_this_range"):
        assert i18n._STRINGS[key]["en"] and i18n._STRINGS[key]["ja"]


def test_chain_icon_and_tint_render(qapp):
    """Painting both states must not raise and the unlinked handle gets a different background."""
    grid = _grid(qapp, 2)
    p1, p2 = grid.plots
    handle = grid._panes[p2].handle
    linked_img = handle.grab().toImage()
    grid.set_plot_x_linked(p2, False)
    _settle(qapp)
    unlinked_img = handle.grab().toImage()
    assert linked_img != unlinked_img
    assert unlinked_img.pixelColor(2, handle.height() - 3) != linked_img.pixelColor(2, handle.height() - 3)


# -- window-level: ranges, stats, home, time column ---------------------------------------


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path):
    w = MainWindow()
    w.resize(1200, 900)
    w.show()
    w.load_parquet(str(sample_parquet_path))
    w.plot_grid.add_plot()
    w.plot_grid.add_plot()
    for plot, names in zip(w.plot_grid.plots, (("a", "b"), ("c",), ("a",))):
        for n in names:
            w._add_variable_to_plot(plot, n)
    _wait_idle(qapp, w)
    yield w
    w.close()


def test_visible_row_range_group_vs_plot(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 10, t0 + 20, padding=0)
    grid.set_x_range(t0 + 100, t0 + 200)
    _settle(qapp)

    group = win._visible_row_range()
    own = win._visible_row_range(p2)
    tv = win._time_values
    assert tv[group[0]] >= t0 + 100 - 0.02 and tv[group[1] - 1] <= t0 + 200 + 0.02
    assert tv[own[0]] >= t0 + 10 - 0.02 and tv[own[1] - 1] <= t0 + 20 + 0.02
    assert group != own
    assert win._visible_row_range(p1) == group  # a linked plot's own range equals the group's


def test_visible_row_range_ignores_empty_anchor(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    p1.remove_series("a")
    p1.remove_series("b")  # anchor now empty: its viewRange is meaningless
    t0 = float(win._time_values[0])
    p2.setXRange(t0 + 50, t0 + 60, padding=0)
    _settle(qapp)
    start, end = win._visible_row_range()
    assert (start, end) != (0, len(win._time_values))
    assert win._time_values[start] >= t0 + 50 - 0.02


def test_stats_for_this_range_uses_that_plots_range_and_series(qapp, win, monkeypatch):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 10, t0 + 20, padding=0)
    _settle(qapp)
    expected_rows = win._visible_row_range(p2)
    expected = win.variable_window("c", *expected_rows)

    _menu_picker(monkeypatch, i18n.tr("plot.stats_this_range"))
    p2.contextMenuEvent(_CtxEvent())  # real routing: menu -> signal -> MainWindow
    _drain(qapp)
    dialogs = win.findChildren(StatsDialog)
    assert len(dialogs) == 1
    table = dialogs[0].findChild(QTableWidget)
    assert table.rowCount() == 1  # only p2's series ("c"), not every plotted variable
    assert table.item(0, 1).text() == str(len(expected))
    assert expected_rows[1] - expected_rows[0] == len(expected)


def test_regular_stats_uses_the_linked_group_range(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 10, t0 + 20, padding=0)
    grid.set_x_range(t0 + 100, t0 + 130)
    _settle(qapp)
    rows = win._visible_row_range()
    win._actions["stats"].trigger()  # the toolbar action must not leak `checked` as `plot`
    _drain(qapp)
    table = win.findChildren(StatsDialog)[0].findChild(QTableWidget)
    assert table.rowCount() == 2  # a, b: the linked plots' variables (c is only on the independent plot)
    assert table.item(0, 1).text() == str(rows[1] - rows[0])


def test_home_resets_independent_plots_and_time_switch_keeps_link_state(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    grid.set_plot_x_linked(p2, False)
    full = _xr(p2)
    p2.setXRange(full[0] + 1, full[0] + 2, padding=0)
    grid.set_x_range(full[0] + 5, full[0] + 6)
    win.plot_grid.go_home()
    _settle(qapp)
    assert _xr(p2)[1] - _xr(p2)[0] > 400  # whole 500 s span (+ padding), back from 1 s
    assert _xr(p1)[1] - _xr(p1)[0] > 400
    assert p2.x_linked is False

    win.set_time_column("c")
    _wait_idle(qapp, win)
    assert [p.x_linked for p in grid.plots] == [True, False, True]
    win.set_time_column("time")
    _wait_idle(qapp, win)
    assert [p.x_linked for p in grid.plots] == [True, False, True]


def test_cursor_outside_an_independent_plots_range_does_not_raise(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_cursor_mode_enabled(True)
    grid._on_cursor_add_requested(p1, t0 + 300)
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 1, t0 + 2, padding=0)  # the cursor is far outside this plot
    _settle(qapp)
    p2._reposition_cursor_overlays()
    p2.refresh_cursor_labels()
    grid._on_cursor_move_requested(p1, "c1", t0 + 400)
    p2.setXRange(t0 + 399, t0 + 401, padding=0)  # and now it is back inside
    _settle(qapp)
    assert p2.cursor_ids() == ["c1"]


# -- 24.5 views --------------------------------------------------------------------------


def _save(win, monkeypatch, name):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
    win.save_view_dialog()


def test_view_round_trip_restores_link_state_and_ranges(qapp, win, monkeypatch):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 10, t0 + 20, padding=0)
    grid.set_x_range(t0 + 100, t0 + 130)
    _settle(qapp)
    group = _xr(p1)
    own = _xr(p2)
    _save(win, monkeypatch, "v_indep")

    saved = view_io.load_view("v_indep")
    assert [p.x_linked for p in saved.plots] == [True, False, True]
    assert saved.plots[0].x_axis_range is None and saved.plots[2].x_axis_range is None
    assert saved.plots[1].x_axis_range == pytest.approx(own)
    assert saved.x_axis_range == pytest.approx(group)

    win.load_view("v_indep")
    _wait_idle(qapp, win)
    plots = win.plot_grid.plots
    assert [p.x_linked for p in plots] == [True, False, True]
    assert _xr(plots[1]) == pytest.approx(own, abs=1e-3)
    assert _xr(plots[0]) == pytest.approx(group, abs=1e-3)
    assert _xr(plots[2]) == pytest.approx(group, abs=1e-3)


def test_view_saved_with_independent_first_plot_round_trips(qapp, win, monkeypatch):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(p1, False)
    p1.setXRange(t0 + 1, t0 + 2, padding=0)
    grid.set_x_range(t0 + 100, t0 + 130)  # anchor is p2 now
    _settle(qapp)
    _save(win, monkeypatch, "v_first")
    saved = view_io.load_view("v_first")
    assert saved.x_axis_range == pytest.approx((t0 + 100, t0 + 130), abs=1e-3)
    win.load_view("v_first")
    _wait_idle(qapp, win)
    plots = win.plot_grid.plots
    assert [p.x_linked for p in plots] == [False, True, True]
    assert _xr(plots[0]) == pytest.approx((t0 + 1, t0 + 2), abs=1e-3)
    assert _xr(plots[1]) == pytest.approx((t0 + 100, t0 + 130), abs=1e-3)


def test_old_view_without_x_linked_loads_all_linked(qapp, win, monkeypatch):
    _save(win, monkeypatch, "v_old")
    path = view_io.view_path("v_old")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for p in payload["plots"]:
        p.pop("x_linked")
        p.pop("x_axis_range")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert all(p.x_linked and p.x_axis_range is None for p in view_io.load_view("v_old").plots)
    win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], False)
    win.load_view("v_old")
    _wait_idle(qapp, win)
    assert all(p.x_linked for p in win.plot_grid.plots)


def test_view_cannot_unlink_every_plot(qapp, win, monkeypatch):
    _save(win, monkeypatch, "v_bad")
    path = view_io.view_path("v_bad")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for p in payload["plots"]:
        p["x_linked"] = False
        p["x_axis_range"] = [0, 1]
    path.write_text(json.dumps(payload), encoding="utf-8")
    win.load_view("v_bad")
    _wait_idle(qapp, win)
    linked = [p.x_linked for p in win.plot_grid.plots]
    assert linked.count(True) == 1  # exactly the one the grid refused to unlink


def test_view_with_unlinked_plot_applies_even_when_flag_is_off(qapp, win, monkeypatch):
    _save(win, monkeypatch, "v_flag")
    path = view_io.view_path("v_flag")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["plots"][1]["x_linked"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setitem(features._EFFECTIVE, "independent_x_axis", False)
    win.load_view("v_flag")
    _wait_idle(qapp, win)
    assert [p.x_linked for p in win.plot_grid.plots] == [True, False, True]


@pytest.mark.parametrize("bad", ["x", [1], [1, 2, 3], [None, 2], ["a", "b"], [float("inf"), 1], True])
def test_garbage_per_plot_range_is_ignored(isolated_config, bad):
    view_io.save_view(
        view_io.View("g", view_io.SourceDef("/x.parquet", "absolute"), [view_io.PlotDef("p1", [view_io.SeriesDef("a", "#f00")])])
    )
    path = view_io.view_path("g")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["plots"][0]["x_axis_range"] = bad
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert view_io.load_view("g").plots[0].x_axis_range is None


# -- 24.6 image export -------------------------------------------------------------------


def _snap(linked=True, ratio=1.0, datetime_axis=True, x_range=(1.7e9, 1.7e9 + 100)):
    x = np.linspace(x_range[0], x_range[1], 50)
    return PlotSnapshot(
        datetime_axis, x_range, (-1.0, 1.0), [SeriesSnapshot("s", "#1f77b4", x, np.sin(x))], [],
        x_linked=linked, height_ratio=ratio,
    )


def _shared(a, b):
    return a.get_shared_x_axes().joined(a, b)


def test_build_figure_shares_x_only_between_linked_subplots():
    fig = build_figure([_snap(), _snap(linked=False, x_range=(1.7e9 + 500, 1.7e9 + 600)), _snap()], [], (800, 600))
    a1, a2, a3 = fig.axes
    assert _shared(a1, a3) and not _shared(a1, a2) and not _shared(a2, a3)
    # The group shows tick labels on its last subplot only; the independent one keeps its own.
    assert not a1.xaxis.get_tick_params()["labelbottom"]
    assert a2.xaxis.get_tick_params()["labelbottom"]
    assert a3.xaxis.get_tick_params()["labelbottom"]
    assert a2.get_xlim() != a1.get_xlim()


def test_build_figure_all_linked_is_unchanged_behaviour():
    fig = build_figure([_snap(), _snap(), _snap()], [], (800, 600))
    a1, a2, a3 = fig.axes
    assert _shared(a1, a2) and _shared(a2, a3)
    assert [a.xaxis.get_tick_params()["labelbottom"] for a in fig.axes] == [False, False, True]


def test_build_figure_height_ratios_follow_screen():
    fig = build_figure([_snap(ratio=2.0), _snap(ratio=1.0)], [], (800, 600))
    h1, h2 = (a.get_position().height for a in fig.axes)
    assert h1 / h2 == pytest.approx(2.0, rel=0.15)  # constrained layout shifts it slightly (tick labels)


def test_build_figure_fft_subplot_is_not_shared_with_time():
    fig = build_figure([_snap(), _snap(datetime_axis=False, linked=False, x_range=(0, 50))], [], (800, 600))
    assert not _shared(*fig.axes)


def test_snapshot_defaults_keep_old_callers_working():
    snap = PlotSnapshot(True, (0.0, 1.0), (0.0, 1.0))
    assert snap.x_linked is True and snap.height_ratio == 1.0


def test_export_snapshot_reports_link_state(qapp):
    grid = _grid(qapp, 2)
    grid.set_plot_x_linked(grid.plots[1], False)
    assert [p.export_snapshot().x_linked for p in grid.plots] == [True, False]


def test_export_image_with_an_independent_plot_end_to_end(qapp, win, tmp_path):
    grid = win.plot_grid
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(grid.plots[1], False)
    grid.plots[1].setXRange(t0 + 10, t0 + 20, padding=0)
    _wait_idle(qapp, win)
    seen = {}
    import parquet_analyzer.ui.main_window as mw

    real = mw.export_plots_image

    def spy(snapshots, *a, **k):
        seen["snaps"] = snapshots
        return real(snapshots, *a, **k)

    mw.export_plots_image = spy
    try:
        out = tmp_path / "indep.png"
        assert win.export_image(str(out))
    finally:
        mw.export_plots_image = real
    snaps = seen["snaps"]
    assert [s.x_linked for s in snaps] == [True, False, True]
    assert sum(s.height_ratio for s in snaps) == pytest.approx(1.0, abs=1e-6) or all(s.height_ratio > 0 for s in snaps)
    assert QImage(str(out)).width() > 0


# -- review fixes: navigator region, shared group range, stats default ------------------------


def _region(win):
    lo, hi = win.navigator._region.getRegion()
    return round(lo, 3), round(hi, 3)


def _check_region_is_anchor(win, qapp):
    _settle(qapp)
    anchor = win.plot_grid.anchor_plot()
    assert _region(win) == pytest.approx(_xr(anchor), abs=1e-2)


def _zoomed(win, qapp, lo=100, hi=200):
    t0 = float(win._time_values[0])
    win.plot_grid.set_x_range(t0 + lo, t0 + hi)
    _settle(qapp)
    assert _region(win) == pytest.approx((t0 + lo, t0 + hi), abs=1e-2)  # sanity: not the full extent
    return t0


def test_navigator_region_follows_anchor_after_unlinking_the_anchor(qapp, win):
    _zoomed(win, qapp)
    win.plot_grid.set_plot_x_linked(win.plot_grid.plots[0], False)
    _check_region_is_anchor(win, qapp)
    assert win.plot_grid.anchor_plot() is win.plot_grid.plots[1]


def test_navigator_region_keeps_group_range_after_unlinking_a_middle_plot(qapp, win):
    t0 = _zoomed(win, qapp)
    win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], False)
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_after_relink(qapp, win):
    t0 = _zoomed(win, qapp)
    p1, p2, p3 = win.plot_grid.plots
    win.plot_grid.set_plot_x_linked(p1, False)
    p1.setXRange(t0 + 1, t0 + 2, padding=0)
    win.plot_grid.set_plot_x_linked(p1, True)
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_after_reorder(qapp, win):
    t0 = _zoomed(win, qapp)
    win.plot_grid.move_plot_down(win.plot_grid.plots[0])
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_after_removing_the_anchor(qapp, win):
    t0 = _zoomed(win, qapp)
    win.plot_grid.remove_plot(win.plot_grid.plots[0])
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_after_auto_relink_on_removing_last_linked(qapp, win):
    t0 = _zoomed(win, qapp)
    p1, p2, p3 = win.plot_grid.plots
    win.plot_grid.set_plot_x_linked(p2, False)
    win.plot_grid.set_plot_x_linked(p3, False)
    p2.setXRange(t0 + 50, t0 + 60, padding=0)
    win.plot_grid.remove_plot(p1)  # p2 is re-linked automatically and becomes the anchor
    _settle(qapp)
    assert win.plot_grid.anchor_plot() is p2
    assert _region(win) == pytest.approx((t0 + 50, t0 + 60), abs=1e-2)


def test_export_stamp_range_is_the_linked_groups_not_an_independent_plots(qapp, win, tmp_path):
    grid = win.plot_grid
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(grid.plots[0], False)
    grid.plots[0].setXRange(t0 + 100, t0 + 130, padding=0)
    grid.set_x_range(t0 + 300, t0 + 400)
    _settle(qapp)
    ref = grid.plots[1]
    expected = f"{ref.format_x(t0 + 300)} - {ref.format_x(t0 + 400)}"
    lines, metadata = win._image_stamp(__import__("datetime").datetime.now())
    assert metadata["Range"] == expected
    assert any(expected.split(" - ")[0] in line for line in lines)
    assert metadata["Range"] != f"{ref.format_x(t0 + 100)} - {ref.format_x(t0 + 130)}"


def test_group_range_helper_is_shared(qapp, win, monkeypatch, tmp_path):
    grid = win.plot_grid
    t0 = float(win._time_values[0])
    grid.set_plot_x_linked(grid.plots[0], False)
    grid.plots[0].setXRange(t0 + 100, t0 + 130, padding=0)
    grid.set_x_range(t0 + 300, t0 + 400)
    _settle(qapp)
    _, lo, hi = win._group_x_range()
    assert (round(lo, 3), round(hi, 3)) == pytest.approx((t0 + 300, t0 + 400), abs=1e-2)
    _save(win, monkeypatch, "v_grp")
    assert view_io.load_view("v_grp").x_axis_range == pytest.approx((lo, hi))


def test_toolbar_stats_default_targets_only_linked_plots_series(qapp, win):
    grid = win.plot_grid
    p1, p2, p3 = grid.plots  # p1: a,b  p2: c  p3: a
    grid.set_plot_x_linked(p2, False)
    assert win._plotted_variable_names() == ["a", "b"]  # "c" lives only on the independent plot
    win._actions["stats"].trigger()
    _drain(qapp)
    table = win.findChildren(StatsDialog)[0].findChild(QTableWidget)
    assert table.rowCount() == 2


# -- abnormal paths reported on a real machine (2026-10-02): after deleting a series and then
#    "reloading" (redraw / re-adding it), the navigator region widened to the full extent while
#    the plots stayed zoomed. Same root cause as the link/reorder case above (a layout change
#    rebuilt the overview and reset the region); these pin the series-level triggers. ----------


def test_navigator_region_kept_after_deleting_a_series(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    win.plot_grid.select_series(p1, "b")
    win._actions["remove_series"].trigger()
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_kept_after_deleting_then_redrawing_and_readding(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    win.plot_grid.select_series(p1, "b")
    win.plot_grid.remove_selected_series()
    win.plot_grid.redraw()
    _wait_idle(qapp, win)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)
    win._add_variable_to_plot(p1, "b")  # put the deleted series back
    _wait_idle(qapp, win)
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_kept_after_deleting_the_anchors_first_series(qapp, win):
    # The overview waveform switches to the next series; the region must not reset.
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    win.plot_grid.select_series(p1, "a")
    win.plot_grid.remove_selected_series()
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_kept_after_emptying_the_anchor_plot(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    for name in list(p1.series_names()):
        win.plot_grid.select_series(p1, name)
        win.plot_grid.remove_selected_series()
    _settle(qapp)
    assert p1.is_empty()
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_kept_after_hiding_and_showing_a_series(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    p1.set_series_visible("a", False)
    _check_region_is_anchor(win, qapp)
    p1.set_series_visible("a", True)
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_navigator_region_kept_after_separating_a_series(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    p1.separateSeriesRequested.emit(p1, "b")
    _wait_idle(qapp, win)
    _check_region_is_anchor(win, qapp)
    assert _region(win) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def _group_ranges(win):
    return [_xr(p) for p in win.plot_grid.linked_plots()]


def test_separating_a_series_from_a_non_anchor_plot_keeps_group_range(qapp, win):
    """The new pane is not laid out yet when it is seeded; pyqtgraph's geometry-based X link
    used to shift the whole group by ~2% of the span (detailed_specification.md 23.4/24.1)."""
    t0 = _zoomed(win, qapp)
    p2 = win.plot_grid.plots[1]
    win._add_variable_to_plot(p2, "a")  # p2 now holds c and a
    _wait_idle(qapp, win)
    p2.separateSeriesRequested.emit(p2, "a")
    _wait_idle(qapp, win)
    assert len(win.plot_grid.plots) == 4
    for r in _group_ranges(win):
        assert r == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)
    _check_region_is_anchor(win, qapp)


def test_separating_a_whole_plot_keeps_group_range(qapp, win):
    t0 = _zoomed(win, qapp)
    p1 = win.plot_grid.plots[0]
    p1.separateRequested.emit(p1)
    _wait_idle(qapp, win)
    for r in _group_ranges(win):
        assert r == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)


def test_separating_from_an_independent_plot_does_not_move_the_group(qapp, win):
    t0 = _zoomed(win, qapp)
    p2 = win.plot_grid.plots[1]
    win._add_variable_to_plot(p2, "a")
    _wait_idle(qapp, win)
    win.plot_grid.set_plot_x_linked(p2, False)
    p2.setXRange(t0 + 10, t0 + 20, padding=0)
    p2.separateSeriesRequested.emit(p2, "a")
    _wait_idle(qapp, win)
    for r in _group_ranges(win):
        assert r == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)
    assert _xr(p2) == pytest.approx((t0 + 10, t0 + 20), abs=1e-2)


def test_navigator_region_kept_after_loading_a_view(qapp, isolated_config, sample_parquet_path, monkeypatch):
    # Found through the chapter-30 op log: load_view refreshed the overview in its
    # `finally`, which reset the navigator region to the full extent while the plot was
    # correctly restored to the view's zoomed range. Single plot on purpose: with several
    # linked plots a late link-sync range event happened to repair the region and hid it.
    w = MainWindow()
    w.resize(1200, 900)
    w.show()
    try:
        w.load_parquet(str(sample_parquet_path))
        p1 = w.plot_grid.plots[0]
        w._add_variable_to_plot(p1, "a")
        w._add_variable_to_plot(p1, "b")
        _wait_idle(qapp, w)
        t0 = float(w._time_values[0])
        w.plot_grid.set_x_range(t0 + 100, t0 + 200)
        _wait_idle(qapp, w)
        _save(w, monkeypatch, "nav_v")
        w.plot_grid.select_series(p1, "b")  # the reported sequence: delete a series,
        w.plot_grid.remove_selected_series()  # then reload the view to get it back
        _wait_idle(qapp, w)
        w.load_view("nav_v")
        _wait_idle(qapp, w)
        assert _xr(p1) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)
        assert _region(w) == pytest.approx((t0 + 100, t0 + 200), abs=1e-2)
    finally:
        w.close()
