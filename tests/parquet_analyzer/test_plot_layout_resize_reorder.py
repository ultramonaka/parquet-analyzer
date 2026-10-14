"""Chapter 22: plot height resize / reorder / x tick labels
(detailed_specification.md 22.0-22.5, specification.md 5.15/5.16)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QInputDialog, QMessageBox

from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.plot_widget import PLOT_MIME


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _settle(qapp, n=10):
    for _ in range(n):
        qapp.processEvents()


def _wait_idle(qapp, win):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not win._busy_sources:
            break
    _settle(qapp)


def _shown_grid(qapp, n_plots=3, w=800, h=700):
    grid = PlotGridWidget()
    for _ in range(n_plots - 1):
        grid.add_plot()
    grid.resize(w, h)
    grid.show()
    _settle(qapp)
    return grid


def _heights(grid):
    return [p.height() for p in grid.plots]


def _write_parquet(path, n=1000):
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    t = np.linspace(0, 10, n)
    pq.write_table(
        pa.table({"time": pa.array(times), "a": np.sin(t), "b": np.cos(t), "c": t}), path
    )


# -- 22.0 regression ----------------------------------------------------------


def test_reordering_keeps_every_plots_height(qapp):
    grid = _shown_grid(qapp)
    p1, p2, p3 = grid.plots
    before = {p: p.height() for p in grid.plots}
    assert min(before.values()) > 150  # genuinely sized, not a degenerate layout

    grid.move_plot_down(p1)
    _settle(qapp)
    assert grid.plots == [p2, p1, p3]
    for p in grid.plots:
        assert abs(p.height() - before[p]) <= 2

    grid.move_plot_up(p3)
    _settle(qapp)
    assert grid.plots == [p2, p3, p1]
    for p in grid.plots:
        assert abs(p.height() - before[p]) <= 2


def test_reordering_keeps_manually_resized_heights(qapp):
    grid = _shown_grid(qapp)
    p1, p2, p3 = grid.plots
    grid.set_plot_height_ratios([0.5, 0.3, 0.2])
    _settle(qapp)
    before = {p: p.height() for p in grid.plots}
    grid.move_plot_to(p1, 2)
    _settle(qapp)
    assert grid.plots == [p2, p3, p1]
    for p in grid.plots:
        assert abs(p.height() - before[p]) <= 2


# -- move_plot_to ---------------------------------------------------------------


def test_move_plot_to_arbitrary_positions_and_noops(qapp):
    grid = _shown_grid(qapp, 4)
    a, b, c, d = grid.plots
    events = []
    grid.layoutChanged.connect(lambda: events.append(1))

    grid.move_plot_to(a, 3)
    assert grid.plots == [b, c, d, a]
    grid.move_plot_to(a, 0)
    assert grid.plots == [a, b, c, d]
    grid.move_plot_to(c, 1)
    assert grid.plots == [a, c, b, d]
    assert len(events) == 3

    for bad in (-1, 4, 99, grid.plots.index(c)):
        grid.move_plot_to(c, bad)
    assert grid.plots == [a, c, b, d]
    assert len(events) == 3
    assert grid.anchor_plot() is a
    assert c.getViewBox().linkedView(0) is a.getViewBox()


def test_move_plot_to_refuses_domain_boundary(qapp):
    grid = PlotGridWidget()
    t1 = grid.plots[0]
    t2 = grid.add_plot()
    f1 = grid.add_plot(x_axis_datetime=False)
    grid.move_plot_to(t1, 2)
    grid.move_plot_to(f1, 0)
    grid.move_plot_to(f1, 1)
    assert grid.plots == [t1, t2, f1]
    grid.move_plot_to(t1, 1)  # within the time group is fine
    assert grid.plots == [t2, t1, f1]


# -- add / remove equalize --------------------------------------------------------


def test_add_and_remove_equalize_heights(qapp):
    grid = _shown_grid(qapp, 2)
    grid.set_plot_height_ratios([0.8, 0.2])
    _settle(qapp)
    p3 = grid.add_plot()
    _settle(qapp)
    hs = _heights(grid)
    assert max(hs) - min(hs) <= 3
    grid.set_plot_height_ratios([0.6, 0.3, 0.1])
    _settle(qapp)
    grid.remove_plot(p3)
    _settle(qapp)
    hs = _heights(grid)
    assert len(hs) == 2 and abs(hs[0] - hs[1]) <= 3


# -- ratios API ----------------------------------------------------------------------


def test_ratios_set_before_show_apply_after_show(qapp):
    grid = PlotGridWidget()
    grid.add_plot()
    grid.add_plot()
    grid.set_plot_height_ratios([0.5, 0.3, 0.2])  # not shown yet
    grid.resize(800, 800)
    grid.show()
    _settle(qapp)
    r = grid.plot_height_ratios()
    assert r == pytest.approx([0.5, 0.3, 0.2], abs=0.03)
    assert sum(r) == pytest.approx(1.0)


def test_invalid_ratios_are_ignored(qapp):
    grid = _shown_grid(qapp)
    before = grid.plot_height_ratios()
    for bad in ([0.5, 0.5], [1, 1, 1, 1], [-1, 1, 1], [0, 0, 0]):
        grid.set_plot_height_ratios(bad)
        _settle(qapp)
        assert grid.plot_height_ratios() == pytest.approx(before, abs=0.01)


def test_ratios_before_show_default_to_equal(qapp):
    grid = PlotGridWidget()
    grid.add_plot()
    assert grid.plot_height_ratios() == pytest.approx([0.5, 0.5])


# -- view round trip -------------------------------------------------------------------


def _open_with_three_plots(qapp, tmp_path):
    path = tmp_path / "a.parquet"
    _write_parquet(path)
    win = MainWindow()
    win.resize(1200, 900)
    win.show()
    win.load_parquet(str(path))
    p1 = win.plot_grid.plots[0]
    p2 = win.plot_grid.add_plot()
    p3 = win.plot_grid.add_plot()
    for p, name in zip((p1, p2, p3), "abc"):
        win._on_variable_dropped(p, name)
    _wait_idle(qapp, win)
    return win


def test_view_round_trip_restores_height_ratios(qapp, isolated_config, monkeypatch, tmp_path):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("hv", True)))
    win = _open_with_three_plots(qapp, tmp_path)
    win.plot_grid.set_plot_height_ratios([0.5, 0.3, 0.2])
    _settle(qapp)
    win.save_view_dialog()
    win.close()

    saved = view_io.load_view("hv")
    assert [p.height_ratio for p in saved.plots] == pytest.approx([0.5, 0.3, 0.2], abs=0.03)

    win2 = MainWindow()
    win2.resize(1200, 900)
    win2.show()
    win2.load_view("hv")
    _wait_idle(qapp, win2)
    assert win2.plot_grid.plot_height_ratios() == pytest.approx([0.5, 0.3, 0.2], abs=0.02)
    win2.close()


def test_old_view_without_height_ratio_loads_equal(qapp, isolated_config, monkeypatch, tmp_path):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("old", True)))
    win = _open_with_three_plots(qapp, tmp_path)
    win.save_view_dialog()
    win.close()
    path = view_io.view_path("old")
    payload = json.loads(path.read_text())
    for p in payload["plots"]:
        del p["height_ratio"]
    path.write_text(json.dumps(payload))
    assert all(p.height_ratio is None for p in view_io.load_view("old").plots)

    win2 = MainWindow()
    win2.resize(1200, 900)
    win2.show()
    win2.load_view("old")
    _wait_idle(qapp, win2)
    assert win2.plot_grid.plot_height_ratios() == pytest.approx([1 / 3] * 3, abs=0.03)
    win2.close()


def test_view_with_skipped_plot_normalizes_remaining_ratios(qapp, isolated_config, monkeypatch, tmp_path):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("sk", True)))
    win = _open_with_three_plots(qapp, tmp_path)
    win.plot_grid.set_plot_height_ratios([0.5, 0.3, 0.2])
    _settle(qapp)
    win.save_view_dialog()
    win.close()
    path = view_io.view_path("sk")
    payload = json.loads(path.read_text())
    payload["plots"][1]["series"][0]["variable"] = "does_not_exist"
    path.write_text(json.dumps(payload))

    win2 = MainWindow()
    win2.resize(1200, 900)
    win2.show()
    win2.load_view("sk")
    _wait_idle(qapp, win2)
    assert len(win2.plot_grid.plots) == 2
    assert win2.plot_grid.plot_height_ratios() == pytest.approx([0.5 / 0.7, 0.2 / 0.7], abs=0.03)
    win2.close()


def test_load_view_ignores_unknown_keys(isolated_config, tmp_path):
    view_io.save_view(
        view_io.View(
            view_name="u",
            source=view_io.SourceDef("x.parquet", "absolute"),
            plots=[view_io.PlotDef("p1", [view_io.SeriesDef("a", "#fff")])],
            derived_variables=[view_io.DerivedVariableDef("d", "a*2")],
        )
    )
    path = view_io.view_path("u")
    payload = json.loads(path.read_text())
    payload["source"]["future"] = 1
    payload["derived_variables"][0]["future"] = 1
    payload["plots"][0]["series"][0]["visible"] = False
    payload["plots"][0]["x_linked"] = False
    path.write_text(json.dumps(payload))
    v = view_io.load_view("u")
    assert v.plots[0].series[0].variable == "a"
    assert v.derived_variables[0].expression == "a*2"
    assert v.plots[0].height_ratio is None


# -- drag to reorder ------------------------------------------------------------------


_KEEP_ALIVE: list = []  # QDropEvent/QDragEnterEvent do not own their QMimeData


def _plot_mime(plot_id):
    m = QMimeData()
    m.setData(PLOT_MIME, plot_id.encode())
    _KEEP_ALIVE.append(m)
    return m


def test_insertion_index_and_allowed_slots(qapp):
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    assert grid._insertion_index_at(0) == 0
    assert grid._insertion_index_at(grid.height() // 2) in (1, 2)
    assert grid._insertion_index_at(grid.height() - 1) == 3
    # dropping p1 right where it is (slots 0 and 1) changes nothing -> not allowed
    assert grid._drop_target_index(p1, 0) is None
    assert grid._drop_target_index(p1, 1) is None
    assert grid._drop_target_index(p1, 3) == 2
    assert grid._drop_target_index(p3, 0) == 0


def test_drop_slot_across_domain_boundary_not_allowed(qapp):
    grid = PlotGridWidget()
    t1 = grid.plots[0]
    grid.add_plot()
    f1 = grid.add_plot(x_axis_datetime=False)
    grid.resize(600, 600)
    grid.show()
    _settle(qapp)
    assert grid._drop_target_index(t1, 3) is None  # would land after the FFT plot
    assert grid._drop_target_index(f1, 0) is None
    assert grid._drop_target_index(t1, 2) == 1


def test_drop_event_reorders_and_keeps_heights(qapp):
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    before = {p: p.height() for p in grid.plots}
    pos = QPointF(50, grid.height() - 2)
    ev = QDropEvent(pos, Qt.MoveAction, _plot_mime(p1.plot_id), Qt.LeftButton, Qt.NoModifier)
    grid.dropEvent(ev)
    _settle(qapp)
    assert grid.plots == [p2, p3, p1]
    for p in grid.plots:
        assert abs(p.height() - before[p]) <= 2
    assert not grid._drop_line.isVisible()


def test_drop_event_at_disallowed_slot_does_nothing(qapp):
    grid = _shown_grid(qapp, 3)
    p1 = grid.plots[0]
    order = list(grid.plots)
    ev = QDropEvent(QPointF(50, 1), Qt.MoveAction, _plot_mime(p1.plot_id), Qt.LeftButton, Qt.NoModifier)
    grid.dropEvent(ev)
    assert grid.plots == order


def test_plot_drag_is_not_accepted_by_time_plot_widget(qapp):
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent

    grid = _shown_grid(qapp, 1)
    plot = grid.plots[0]
    mime = _plot_mime(plot.plot_id)
    ev = QDragEnterEvent(QPoint(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    plot.dragEnterEvent(ev)
    assert not ev.isAccepted()
    ev = QDragMoveEvent(QPoint(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    plot.dragMoveEvent(ev)
    assert not ev.isAccepted()
    # variable drops (text) still work
    tm = QMimeData()
    tm.setText("a")
    _KEEP_ALIVE.append(tm)
    ev = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, tm, Qt.LeftButton, Qt.NoModifier)
    plot.dragEnterEvent(ev)
    assert ev.isAccepted()


def _route(grid, mime, points, drop=True, enter_cls=None):
    """Drive a drag through real Qt routing (QWidgetWindow -> findDnDTarget ->
    QApplication.notify), the way a platform drag would."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent

    win = grid.windowHandle()
    action = Qt.MoveAction
    for i, (x, y) in enumerate(points):
        cls = QDragEnterEvent if i == 0 else QDragMoveEvent
        QCoreApplication.sendEvent(win, cls(QPoint(x, y), action, mime, Qt.LeftButton, Qt.NoModifier))
    if drop:
        x, y = points[-1]
        QCoreApplication.sendEvent(
            win, QDropEvent(QPointF(x, y), action, mime, Qt.LeftButton, Qt.NoModifier)
        )


def _pane_y(grid, plot, frac):
    pane = grid._panes[plot]
    return pane.mapTo(grid, QPoint(0, int(pane.height() * frac))).y()


def test_real_routing_drag_down_the_handle_column(qapp):
    """Regression: an ignored DragEnter (the drag starts over its own grip, a disallowed
    slot) killed all later moves/drops."""
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    _route(grid, _plot_mime(p1.plot_id), [(7, _pane_y(grid, p1, 0.4)), (7, _pane_y(grid, p2, 0.8)), (7, _pane_y(grid, p3, 0.9))])
    assert grid.plots == [p2, p3, p1]


def test_real_routing_drag_through_disallowed_then_allowed_slot_over_plot(qapp):
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    pts = [(7, _pane_y(grid, p1, 0.4)), (400, _pane_y(grid, p2, 0.2)), (400, _pane_y(grid, p2, 0.8))]
    _route(grid, _plot_mime(p1.plot_id), pts)
    assert grid.plots == [p2, p1, p3]


def test_real_routing_drag_straight_into_last_plot(qapp):
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    _route(grid, _plot_mime(p1.plot_id), [(7, _pane_y(grid, p1, 0.4)), (400, _pane_y(grid, p3, 0.9))])
    assert grid.plots == [p2, p3, p1]


def test_real_routing_drop_line_visible_inside_widget_at_the_end(qapp):
    grid = _shown_grid(qapp, 3)
    p1 = grid.plots[0]
    _route(grid, _plot_mime(p1.plot_id), [(7, _pane_y(grid, p1, 0.4)), (400, grid.height() - 3)], drop=False)
    line = grid._drop_line
    assert line.isVisible() and line.y() + line.height() <= grid.height()


def test_real_routing_text_drag_still_reaches_time_plot(qapp):
    grid = _shown_grid(qapp, 2)
    p1, p2 = grid.plots
    got = []
    p2.variableDropped.connect(lambda plot, name: got.append((plot, name)))
    tm = QMimeData()
    tm.setText("sine")
    _KEEP_ALIVE.append(tm)
    _route(grid, tm, [(400, _pane_y(grid, p2, 0.5)), (401, _pane_y(grid, p2, 0.5))])
    assert got == [(p2, "sine")]
    assert grid.plots == [p1, p2]


# -- time plots stay in front of FFT plots --------------------------------------------


def test_new_time_plot_goes_before_fft_plots(qapp):
    grid = _shown_grid(qapp, 2)
    fft = grid.add_plot(x_axis_datetime=False)
    t3 = grid.add_plot()
    assert grid.plots[:3] == [grid.plots[0], grid.plots[1], t3] and grid.plots[3] is fft
    assert [grid._splitter.widget(i) for i in range(4)] == [grid._panes[p] for p in grid.plots]
    # reordering the time plots still works with an FFT plot present
    a, b = grid.plots[:2]
    grid.move_plot_down(a)
    assert grid.plots[:2] == [b, a]
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, False, True, True]


def test_separate_with_fft_present_keeps_time_plots_first(qapp):
    grid = PlotGridWidget()
    fft = grid.add_plot(x_axis_datetime=False)
    t1 = grid.plots[0]
    x = np.arange(10.0)
    t1.add_series("a", x, x)
    t1.add_series("b", x, x)
    grid._on_separate_requested(t1)
    assert [p.x_axis_datetime for p in grid.plots] == [True, True, False]
    assert grid.plots[2] is fft


# -- ratio edge cases ---------------------------------------------------------------------


def test_move_before_show_permutes_pending_ratios(qapp):
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    grid.add_plot()
    grid.add_plot()
    grid.set_plot_height_ratios([0.5, 0.3, 0.2])
    grid.move_plot_to(p1, 2)
    grid.resize(800, 800)
    grid.show()
    _settle(qapp)
    assert grid.plot_height_ratios() == pytest.approx([0.3, 0.2, 0.5], abs=0.03)


def test_load_view_rejects_non_numeric_height_ratio(isolated_config):
    view_io.save_view(
        view_io.View(
            view_name="r",
            source=view_io.SourceDef("x.parquet", "absolute"),
            plots=[view_io.PlotDef(f"p{i}", [view_io.SeriesDef("a", "#fff")]) for i in range(5)],
        )
    )
    path = view_io.view_path("r")
    payload = json.loads(path.read_text())
    for p, v in zip(payload["plots"], ["0.5", float("nan"), True, -1, 0.25]):
        p["height_ratio"] = v
    path.write_text(json.dumps(payload))  # NaN is written as the NaN token
    ratios = [p.height_ratio for p in view_io.load_view("r").plots]
    assert ratios == [None, None, None, None, 0.25]


# -- X tick labels (22.4) ----------------------------------------------------------------


def test_only_bottom_linked_plot_shows_time_labels(qapp):
    grid = _shown_grid(qapp, 3)
    p1, p2, p3 = grid.plots
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, False, True]

    grid.move_plot_down(p2)  # p1, p3, p2
    assert [p.x_tick_labels_visible() for p in grid.plots] == [False, False, True]
    assert p2.x_tick_labels_visible() and not p3.x_tick_labels_visible()

    p4 = grid.add_plot()
    assert p4.x_tick_labels_visible() and not p2.x_tick_labels_visible()

    grid.remove_plot(p4)
    assert p2.x_tick_labels_visible() and not p1.x_tick_labels_visible()


def test_fft_plot_always_shows_its_labels(qapp):
    grid = _shown_grid(qapp, 2)
    fft = grid.add_plot(x_axis_datetime=False)
    t1, t2 = grid.plots[:2]
    assert fft.x_tick_labels_visible()
    assert t2.x_tick_labels_visible()  # last *time* plot still shows its own
    assert not t1.x_tick_labels_visible()
    # grid lines stay even when labels are hidden
    assert t1.getAxis("bottom").grid is not False


# --- 22.6: left (Y) axis width alignment ------------------------------------------------

def _left_x(plot):
    return plot.getPlotItem().vb.mapToScene(plot.getPlotItem().vb.boundingRect().topLeft()).x()


def _left_xs(grid):
    return [_left_x(p) for p in grid.plots]


def _aligned(grid):
    xs = _left_xs(grid)
    return max(xs) - min(xs) <= 1.0


def _grid_with_series(qapp, scales, n_extra_fft=0):
    """One time plot per scale; each plots a ramp 0..scale so tick text widths differ."""
    grid = PlotGridWidget()
    x = np.linspace(1.7e9, 1.7e9 + 100, 200)
    for i, scale in enumerate(scales):
        plot = grid.plots[0] if i == 0 else grid.add_plot()
        plot.add_series(f"s{i}", x, np.linspace(0, scale, 200))
    grid.resize(900, 800)
    grid.show()
    _settle(qapp, 20)
    return grid, x


def test_left_axes_align_across_plots_with_different_tick_text(qapp):
    grid, _ = _grid_with_series(qapp, [1.0, 123456.0, 0.5])
    naturals = [p.getAxis("left").natural_width() for p in grid.plots]
    assert max(naturals) - min(naturals) > 5  # the scenario really has different widths
    assert _aligned(grid), _left_xs(grid)


def test_alignment_survives_add_remove_reorder_and_zoom(qapp):
    grid, x = _grid_with_series(qapp, [1.0, 123456.0, 0.5])
    extra = grid.add_plot()
    extra.add_series("e", x, np.linspace(0, 7.0, 200))
    _settle(qapp, 20)
    assert _aligned(grid)
    grid.move_plot_to(grid.plots[1], 0)
    _settle(qapp, 20)
    assert _aligned(grid)
    # zoom the widest plot's Y range so its tick text gets shorter
    wide = next(p for p in grid.plots if p.getAxis("left").natural_width() == max(
        q.getAxis("left").natural_width() for q in grid.plots))
    wide.getPlotItem().vb.setYRange(0.0, 0.5, padding=0)
    _settle(qapp, 20)
    assert _aligned(grid), _left_xs(grid)


def test_removing_widest_plot_shrinks_the_rest(qapp):
    grid, _ = _grid_with_series(qapp, [1.0, 123456.0, 0.5])
    before = _left_x(grid.plots[0])
    wide = next(p for p in grid.plots if p.getAxis("left").natural_width() == max(
        q.getAxis("left").natural_width() for q in grid.plots))
    grid.remove_plot(wide)
    _settle(qapp, 20)
    assert _aligned(grid)
    assert _left_x(grid.plots[0]) < before - 3


def test_fft_plot_aligns_with_time_plots(qapp):
    grid, x = _grid_with_series(qapp, [1.0, 123456.0])
    fft = grid.add_plot(x_axis_datetime=False)
    fft.add_series("f", np.linspace(0, 50, 100), np.linspace(0, 0.01, 100))
    _settle(qapp, 20)
    assert len(grid.plots) == 3 and not grid.plots[-1].x_axis_datetime
    assert _aligned(grid), _left_xs(grid)


def test_setting_aligned_width_does_not_change_natural_width(qapp):
    grid, _ = _grid_with_series(qapp, [1.0, 123456.0])
    axis = grid.plots[0].getAxis("left")
    nat = axis.natural_width()
    axis.set_aligned_width(nat + 50)
    _settle(qapp)
    assert axis.natural_width() == nat
    axis.set_aligned_width(None)
    assert axis.maximumWidth() == nat
