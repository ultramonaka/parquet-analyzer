"""Map manager: frozen axis row / column and click-on-plot -> table cell
(detailed_specification.md 27.11). Plots are real widgets; clicks go through Qt's routing."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from test_functions_maps_ui import CURVE, _csv, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)

from parquet_analyzer.ui import map_manager


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    monkeypatch.setattr(map_manager.messages, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(map_manager.messages, "warning", lambda *a, **k: None)


@pytest.fixture
def ops(monkeypatch):
    recorded = []
    monkeypatch.setattr(map_manager, "log_op", lambda action, **d: recorded.append((action, d)))
    return recorded


def _map_text(xs, ys):
    lines = ["x," + ",".join(str(x) for x in xs)]
    for y in ys:
        lines.append(str(y) + "," + ",".join(str(x * 10 + y) for x in xs))
    return "\n".join(lines) + "\n"


def _open(win, tmp_path, qapp, name="mp", text=None):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.resize(1100, 800)
    mgr.show()
    text = text or _map_text(range(30), range(30))
    assert mgr.do_import(str(_csv(tmp_path, f"{name}.csv", text)), name)
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index(name))
    qapp.processEvents()
    return mgr


def _current(mgr):
    t = mgr.preview_table
    return t.currentRow(), t.currentColumn()


def _view_pos(plot, x, y):
    vb = plot.getPlotItem().getViewBox()
    return plot.mapFromScene(vb.mapViewToScene(QPointF(x, y)))


def _click_view(mgr, plot, x, y, qapp, button=Qt.MouseButton.LeftButton):
    """Click at view coordinates (x, y) of `plot` through its viewport."""
    QTest.mouseClick(plot.viewport(), button, Qt.KeyboardModifier.NoModifier, _view_pos(plot, x, y))
    qapp.processEvents()


def _jumps(ops):
    return [d for a, d in ops if a == "map_preview_cell_jump"]


# -- frozen axes -------------------------------------------------------------------------


def test_overlays_follow_the_scroll_and_sit_at_the_viewport_origin(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp)
    t = mgr.preview_table
    top, left, corner = t.overlays()
    assert all(o.isVisibleTo(t) for o in (top, left, corner))
    t.horizontalScrollBar().setValue(t.horizontalScrollBar().maximum())
    t.verticalScrollBar().setValue(t.verticalScrollBar().maximum())
    qapp.processEvents()
    assert t.horizontalScrollBar().value() > 0 and t.verticalScrollBar().value() > 0
    assert top.horizontalScrollBar().value() == t.horizontalScrollBar().value()
    assert top.verticalScrollBar().value() == 0
    assert left.verticalScrollBar().value() == t.verticalScrollBar().value()
    assert left.horizontalScrollBar().value() == 0
    for o in (top, left, corner):
        assert o.pos() == t.viewport().pos()  # top-left of the viewport, not scrolled away
    assert top.height() == t.rowHeight(0) and left.width() == t.columnWidth(0)
    assert corner.width() == t.columnWidth(0) and corner.height() == t.rowHeight(0)
    t.setColumnWidth(0, 90)
    qapp.processEvents()
    assert left.width() == corner.width() == 90 and top.columnWidth(0) == 90


def test_edit_in_the_top_overlay_is_a_table_edit(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp, text=_map_text([0, 10, 20], [0, 5, 10]))
    top = mgr.preview_table.overlays()[0]
    assert top.model() is mgr.preview_table.model()
    assert top.selectionModel() is mgr.preview_table.selectionModel()
    assert not mgr.save_values_button.isEnabled()
    top.model().setData(top.model().index(0, 2), "12")
    assert mgr.preview_table.item(0, 2).text() == "12"
    assert (0, 2) in mgr._changed and mgr.compare_active
    assert mgr.save_values_button.isEnabled() and mgr.revert_button.isEnabled()
    assert mgr.save_values() is True
    assert win.function_registry.maps["mp"].x.tolist() == [0, 12, 20]


def test_current_cell_is_not_hidden_under_the_frozen_part(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp)
    t = mgr.preview_table
    t.horizontalScrollBar().setValue(t.horizontalScrollBar().maximum())
    t.verticalScrollBar().setValue(t.verticalScrollBar().maximum())
    qapp.processEvents()
    t.setCurrentCell(1, 1)
    qapp.processEvents()
    rect = t.visualRect(t.model().index(1, 1))
    assert rect.left() >= t.columnWidth(0) and rect.top() >= t.rowHeight(0)
    # moving with the keys from the far right back to column 1, then onto the axis column
    t.setFocus()
    t.horizontalScrollBar().setValue(t.horizontalScrollBar().maximum())
    t.setCurrentCell(5, 2)
    QTest.keyClick(t, Qt.Key.Key_Left)
    QTest.keyClick(t, Qt.Key.Key_Up)
    qapp.processEvents()
    assert _current(mgr) == (4, 1)
    assert t.visualRect(t.model().index(4, 1)).left() >= t.columnWidth(0)
    QTest.keyClick(t, Qt.Key.Key_Left)
    assert _current(mgr) == (4, 0)


def test_curve_is_not_frozen_and_switching_leaves_one_set_of_overlays(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp)
    assert mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    names = sorted(win.function_registry.maps)
    t = mgr.preview_table
    n_children = len(t.children())
    mgr.map_list.setCurrentRow(names.index("cv"))
    qapp.processEvents()
    assert not any(o.isVisible() for o in t.overlays()) and not t.is_frozen
    mgr.map_list.setCurrentRow(names.index("mp"))
    qapp.processEvents()
    assert t.is_frozen and all(o.isVisibleTo(t) for o in t.overlays())
    assert len(t.children()) == n_children
    top, left, _corner = t.overlays()
    assert top.height() == t.rowHeight(0) and left.width() == t.columnWidth(0)


# -- click on a plot -> table cell --------------------------------------------------------


def test_click_heatmap_table_and_result_plots_selects_the_cell(win, tmp_path, qapp, ops):
    mgr = _open(win, tmp_path, qapp)
    for plot, name in ((mgr.plot_raw, "table"), (mgr.plot_result, "result")):
        ops.clear()
        _click_view(mgr, plot, 7.0, 3.0, qapp)  # grid points are the cell centres
        assert _current(mgr) == (3 + 1, 7 + 1)
        assert _jumps(ops) == [{"name": "mp", "cell": [3, 7], "plot": name}]
        assert mgr.preview_table.hasFocus() or mgr.preview_table.isActiveWindow() is False
    _click_view(mgr, mgr.plot_result, 20.2, 28.4, qapp)
    assert _current(mgr) == (29, 21)


def test_click_scrolls_the_cell_into_view_not_under_the_frozen_part(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp)
    t = mgr.preview_table
    _click_view(mgr, mgr.plot_raw, 28.0, 27.0, qapp)
    assert _current(mgr) == (28, 29)
    rect = t.visualRect(t.model().index(28, 29))
    assert rect.left() >= t.columnWidth(0) and rect.top() >= t.rowHeight(0)
    assert rect.right() <= t.viewport().width() and rect.bottom() <= t.viewport().height()


def test_click_uses_cell_boundaries_of_a_non_uniform_axis(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, qapp, text=_map_text([0, 1, 3, 10], [0, 5, 6]))
    # x edges: -0.5, 0.5, 2, 6.5, 13.5 -> the boundary between 1 and 3 is 2
    _click_view(mgr, mgr.plot_raw, 1.9, 0.0, qapp)
    assert _current(mgr)[1] == 2
    _click_view(mgr, mgr.plot_raw, 2.1, 0.0, qapp)
    assert _current(mgr)[1] == 3
    # y edges: -2.5, 2.5, 5.5, 6.5
    _click_view(mgr, mgr.plot_raw, 0.0, 5.4, qapp)
    assert _current(mgr)[0] == 2
    _click_view(mgr, mgr.plot_raw, 0.0, 5.6, qapp)
    assert _current(mgr)[0] == 3
    # the interpolated plot draws a finer grid but jumps to the table's cell
    _click_view(mgr, mgr.plot_result, 9.0, 6.0, qapp)
    assert _current(mgr) == (3, 4)


def test_click_outside_the_grid_does_nothing(win, tmp_path, qapp, ops):
    mgr = _open(win, tmp_path, qapp)
    mgr.preview_table.setCurrentCell(2, 2)
    ops.clear()
    _click_view(mgr, mgr.plot_result, 31.0, 10.0, qapp)  # the preview extends past the table
    _click_view(mgr, mgr.plot_result, 10.0, -3.0, qapp)
    assert _current(mgr) == (2, 2)
    assert not _jumps(ops)


def test_right_click_and_drag_do_not_jump(win, tmp_path, qapp, ops):
    mgr = _open(win, tmp_path, qapp)
    mgr.preview_table.setCurrentCell(2, 2)
    ops.clear()
    _click_view(mgr, mgr.plot_raw, 7.0, 3.0, qapp, button=Qt.MouseButton.RightButton)
    assert _current(mgr) == (2, 2)
    plot = mgr.plot_raw
    a = _view_pos(plot, 7.0, 3.0)
    b = a + QPoint(60, 25)
    QTest.mousePress(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for step in (0.25, 0.5, 1.0):
        QTest.mouseMove(plot.viewport(), a + (b - a) * step)
        qapp.processEvents()
    QTest.mouseRelease(plot.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    qapp.processEvents()
    assert _current(mgr) == (2, 2)
    assert not _jumps(ops)


def test_click_while_editing_keeps_edits_and_uses_the_edited_axis_for_after(win, tmp_path, qapp, ops):
    mgr = _open(win, tmp_path, qapp, text=_map_text([0, 10, 20], [0, 5, 10]))
    t = mgr.preview_table
    t.item(0, 2).setText("14")  # x axis 0, 10, 20 -> 0, 14, 20: edges -7, 7, 17, 23 (saved: -5, 5, 15, 25)
    assert mgr.compare_active
    changed = set(mgr._changed)
    old_plots = (mgr.plot_raw, mgr.plot_result)
    ops.clear()
    _click_view(mgr, mgr.plot_result, 5.5, 5.0, qapp)  # "After": inside the edited cell of x=0
    assert _current(mgr) == (2, 1)
    _click_view(mgr, mgr.plot_raw, 5.5, 5.0, qapp)  # "Before": saved axis, cell of x=10
    assert _current(mgr) == (2, 2)
    _click_view(mgr, mgr.plot_result, 16.0, 5.0, qapp)  # "After": still the cell of x=14
    assert _current(mgr) == (2, 2)
    _click_view(mgr, mgr.plot_raw, 16.0, 5.0, qapp)  # "Before": already the cell of x=20
    assert _current(mgr) == (2, 3)
    assert mgr._changed == changed and t.item(0, 2).text() == "14"
    assert (mgr.plot_raw, mgr.plot_result) == old_plots  # nothing was redrawn
    assert [d["plot"] for d in _jumps(ops)] == ["after", "before", "after", "before"]


def test_click_on_the_curve_selects_the_x_cell(win, tmp_path, qapp, ops):
    mgr = _open(win, tmp_path, qapp, "cv", CURVE)
    _click_view(mgr, mgr.plot_raw, 10.0, 300.0, qapp)  # y is ignored
    assert _current(mgr) == (1, 1)
    _click_view(mgr, mgr.plot_raw, 18.0, 0.0, qapp)
    assert _current(mgr) == (2, 1)
    _click_view(mgr, mgr.plot_raw, 40.0, 0.0, qapp)  # outside -> unchanged
    assert _current(mgr) == (2, 1)
    assert _jumps(ops) == [{"name": "cv", "cell": [1], "plot": "curve"}, {"name": "cv", "cell": [2], "plot": "curve"}]
    # while editing the curve uses the edited X axis
    mgr.preview_table.item(1, 0).setText("4")  # x 0, 4, 20 -> edges -2, 2, 12, 28
    assert mgr.compare_active
    QTest.qWait(50)  # the fresh plot auto-ranges asynchronously
    _click_view(mgr, mgr.plot_raw, 3.0, 200.0, qapp)
    assert _current(mgr) == (1, 1)
    _click_view(mgr, mgr.plot_raw, 15.0, 200.0, qapp)
    assert _current(mgr) == (2, 1)
    assert mgr.has_unsaved_edits()
