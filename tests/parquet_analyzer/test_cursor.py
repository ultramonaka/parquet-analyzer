from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt

from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.plot_widget import TimePlotWidget


class _FakeClickEvent:
    """Minimal stand-in for pyqtgraph's MouseClickEvent -- TimePlotWidget's
    _on_scene_mouse_clicked only ever reads .button(), .scenePos(), and
    .currentItem from it, so a full QGraphicsSceneMouseEvent isn't needed (same
    style as test_plot_widget.py's _wheel_event, which builds a real QWheelEvent
    directly rather than driving it through the full Qt event dispatch pipeline).
    """

    def __init__(self, scene_pos, button=Qt.MouseButton.LeftButton, current_item=None):
        self._scene_pos = scene_pos
        self._button = button
        self.currentItem = current_item

    def button(self):
        return self._button

    def scenePos(self):
        return self._scene_pos


def _make_plot(qapp, width=900):
    plot = TimePlotWidget("p1")
    plot.resize(width, 400)
    plot.show()
    qapp.processEvents()
    return plot


def _click_background(plot, x, y=0.0):
    """Simulate a background (non-cursor) click at data coordinates (x, y)."""
    scene_pos = plot.getViewBox().mapViewToScene(QPointF(x, y))
    plot._on_scene_mouse_clicked(_FakeClickEvent(scene_pos))


def test_click_while_cursor_mode_enabled_requests_a_new_cursor(qapp):
    plot = _make_plot(qapp)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    plot.getViewBox().setRange(xRange=(0.0, 1000.0), padding=0)
    plot.set_cursor_mode_enabled(True)

    received = []
    plot.cursorAddRequested.connect(lambda p, x: received.append(x))
    _click_background(plot, 500.0)

    assert len(received) == 1
    assert received[0] == pytest.approx(500.0, abs=1.0)


def test_click_does_nothing_while_cursor_mode_disabled(qapp):
    plot = _make_plot(qapp)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()

    received = []
    plot.cursorAddRequested.connect(lambda p, x: received.append(x))
    _click_background(plot, 500.0)

    assert not received


def test_add_cursor_label_shows_time_and_every_series_value(qapp):
    plot = _make_plot(qapp)
    x = np.arange(1000, dtype=np.float64)
    plot.add_series("a", x, x * 2.0)
    plot.add_series("b", x, x * 3.0)
    qapp.processEvents()

    plot.add_cursor("c1", 100.0)
    text = plot._cursors["c1"]["label"].toPlainText()
    assert "a: 200" in text
    assert "b: 300" in text


def test_remove_series_drops_it_from_existing_cursor_label(qapp):
    plot = _make_plot(qapp)
    x = np.arange(1000, dtype=np.float64)
    plot.add_series("a", x, x * 2.0)
    plot.add_series("b", x, x * 3.0)
    qapp.processEvents()
    plot.add_cursor("c1", 100.0)

    plot.remove_series("b")
    text = plot._cursors["c1"]["label"].toPlainText()
    assert "a: 200" in text
    assert "b" not in text


def test_clicking_an_existing_cursor_line_requests_removal(qapp):
    plot = _make_plot(qapp)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    plot.set_cursor_mode_enabled(True)
    plot.add_cursor("c1", 100.0)

    removed = []
    plot.cursorRemoveRequested.connect(lambda p, cid: removed.append(cid))
    line = plot._cursors["c1"]["line"]
    line.sigClicked.emit(line, None)

    assert removed == ["c1"]


def test_clicking_cursor_line_while_disabled_does_not_request_removal(qapp):
    plot = _make_plot(qapp)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    plot.set_cursor_mode_enabled(True)
    plot.add_cursor("c1", 100.0)
    plot.set_cursor_mode_enabled(False)

    removed = []
    plot.cursorRemoveRequested.connect(lambda p, cid: removed.append(cid))
    line = plot._cursors["c1"]["line"]
    assert line.movable is False
    line.sigClicked.emit(line, None)

    assert not removed


def test_disabling_cursor_mode_hides_cursors_without_deleting_them(qapp):
    """User-requested: turning the toolbar toggle off should hide existing cursors
    (not just freeze them in place, and not delete them either) -- turning it back
    on restores them at the same position.
    """
    plot = _make_plot(qapp)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    plot.set_cursor_mode_enabled(True)
    plot.add_cursor("c1", 100.0)

    plot.set_cursor_mode_enabled(False)
    assert plot._cursors["c1"]["line"].isVisible() is False
    assert plot._cursors["c1"]["label"].isVisible() is False
    assert "c1" in plot._cursors  # not deleted

    plot.set_cursor_mode_enabled(True)
    assert plot._cursors["c1"]["line"].isVisible() is True
    assert plot._cursors["c1"]["label"].isVisible() is True
    assert plot._cursors["c1"]["line"].value() == 100.0  # position preserved


def test_dragging_a_cursor_updates_its_label_and_emits_move(qapp):
    plot = _make_plot(qapp)
    x = np.arange(1000, dtype=np.float64)
    plot.add_series("a", x, x * 2.0)
    qapp.processEvents()
    plot.add_cursor("c1", 100.0)

    moved = []
    plot.cursorMoveRequested.connect(lambda p, cid, x: moved.append((cid, x)))
    plot._cursors["c1"]["line"].setValue(200.0)

    assert moved == [("c1", 200.0)]
    assert "a: 400" in plot._cursors["c1"]["label"].toPlainText()


def test_multiple_independent_cursors_can_coexist(qapp):
    plot = _make_plot(qapp)
    x = np.arange(1000, dtype=np.float64)
    plot.add_series("a", x, x * 2.0)
    qapp.processEvents()

    plot.add_cursor("c1", 100.0)
    plot.add_cursor("c2", 300.0)
    assert set(plot._cursors) == {"c1", "c2"}
    assert "a: 200" in plot._cursors["c1"]["label"].toPlainText()
    assert "a: 600" in plot._cursors["c2"]["label"].toPlainText()

    plot.remove_cursor("c1")
    assert set(plot._cursors) == {"c2"}


def test_switching_time_axis_clears_cursors(qapp):
    plot = _make_plot(qapp)
    x = np.arange(1000, dtype=np.float64)
    plot.add_series("a", x, x * 2.0)
    qapp.processEvents()
    plot.add_cursor("c1", 100.0)

    plot.set_x_data(np.arange(1000, dtype=np.float64) + 5000.0)
    assert plot._cursors == {}


def test_cursor_syncs_across_same_domain_plots_in_grid(qapp):
    grid = PlotGridWidget()
    grid.resize(200, 400)
    grid.show()
    plot0 = grid.plots[0]
    plot1 = grid.add_plot()
    qapp.processEvents()

    grid._on_cursor_add_requested(plot0, 123.0)
    assert "c1" in plot0._cursors
    assert "c1" in plot1._cursors

    plot1._cursors["c1"]["line"].setValue(456.0)
    assert plot0._cursors["c1"]["line"].value() == 456.0

    grid._on_cursor_remove_requested(plot0, "c1")
    assert plot0._cursors == {}
    assert plot1._cursors == {}


def test_cursor_does_not_sync_to_a_different_domain_plot(qapp):
    grid = PlotGridWidget()
    grid.resize(200, 400)
    grid.show()
    plot0 = grid.plots[0]  # time-domain
    fft_plot = grid.add_plot(x_axis_datetime=False)
    qapp.processEvents()

    grid._on_cursor_add_requested(plot0, 123.0)
    assert "c1" in plot0._cursors
    assert fft_plot._cursors == {}


def test_new_plot_is_seeded_with_existing_cursors_of_matching_domain(qapp):
    grid = PlotGridWidget()
    grid.resize(200, 400)
    grid.show()
    plot0 = grid.plots[0]
    qapp.processEvents()

    grid._on_cursor_add_requested(plot0, 123.0)
    plot1 = grid.add_plot()
    assert "c1" in plot1._cursors
    assert plot1._cursors["c1"]["line"].value() == 123.0


def test_set_cursor_mode_enabled_propagates_to_every_plot(qapp):
    grid = PlotGridWidget()
    grid.resize(200, 400)
    grid.show()
    plot1 = grid.add_plot()

    grid.set_cursor_mode_enabled(True)
    assert grid.plots[0]._cursor_mode_enabled is True
    assert plot1._cursor_mode_enabled is True

    plot2 = grid.add_plot()  # added after enabling -- must inherit the current mode
    assert plot2._cursor_mode_enabled is True
