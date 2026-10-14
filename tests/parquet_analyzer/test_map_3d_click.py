"""3D surface click -> map table cell (detailed_specification.md 27.12). The projection and the
picking are real (no stubbed `pick_at`); mouse events go through Qt's routing to the GL widget.
GL cannot render offscreen -- click-vs-rotate feel is checked on a real display."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_functions_maps_ui import _csv, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)
from test_map_table_frozen import _current, _jumps, _map_text, ops  # noqa: F401
from test_xyz_3d import _screen_of, _shown_view

from parquet_analyzer import features
from parquet_analyzer.ui import map_manager
from parquet_analyzer.ui import xyz_3d_window as x3


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    monkeypatch.setattr(map_manager.messages, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(map_manager.messages, "warning", lambda *a, **k: None)
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))


def _grid_view(z=None):
    v = _shown_view(600, 600)
    xs, ys = [0.0, 10.0, 40.0], [1.0, 2.0, 5.0, 9.0]
    z = np.arange(12, dtype=float).reshape(4, 3) if z is None else z
    v.set_surface(xs, ys, z)
    return v, xs, ys


def _pt(v, iy, ix, ny=4):
    """Screen position of grid point (iy, ix) (vertex number = ix * ny + iy)."""
    xy, _ = _screen_of(v, v.scene["pos"])
    k = int(np.flatnonzero(v.scene["grid_index"] == ix * ny + iy)[0])
    return QPoint(round(xy[k][0]), round(xy[k][1]))


def _record(v):
    got = []
    v.gridPointClicked.connect(lambda iy, ix: got.append((iy, ix)))
    return got


def test_click_on_a_grid_point_emits_its_index(qapp):
    v, _, _ = _grid_view()
    got = _record(v)
    for iy, ix in ((0, 0), (3, 2), (1, 1), (2, 0)):
        QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(v, iy, ix))
    assert got == [(0, 0), (3, 2), (1, 1), (2, 0)]


def test_drag_far_click_right_button_and_nan_emit_nothing(qapp):
    v, _, _ = _grid_view()
    got = _record(v)
    p = _pt(v, 1, 1)
    far = QPoint(p.x(), p.y())
    QTest.mousePress(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    QTest.mouseMove(v.view, p + QPoint(QApplication.startDragDistance() + 10, 0))
    QTest.mouseRelease(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(QApplication.startDragDistance() + 10, 0))
    assert got == []  # a drag (rotation)
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, 2))
    assert got == []  # far from every point
    QTest.mouseClick(v.view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, far)
    assert got == []
    QTest.mouseRelease(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, far)
    assert got == []  # a release without a press
    z = np.arange(12, dtype=float).reshape(4, 3)
    z[1, 1] = np.nan
    v.set_surface([0.0, 10.0, 40.0], [1.0, 2.0, 5.0, 9.0], z)
    # the NaN point is not a candidate; clicking where it would be (not near a neighbour) picks nothing
    pts = [_pt(v, 0, 0), _pt(v, 3, 2)]
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pts[0])
    assert got == [(0, 0)]
    xy, _ = _screen_of(v, v.scene["pos"])
    assert len(xy) == 11 and all(int(i) != 1 * 4 + 1 for i in v.scene["grid_index"])


def test_scatter_never_emits(qapp):
    v = _shown_view(600, 600)
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)))
    got = _record(v)
    xy, _ = _screen_of(v, v.scene["pos"])
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(xy[0][0]), round(xy[0][1])))
    assert got == []


def test_hover_still_works(qapp):
    v, _, _ = _grid_view()
    p = _pt(v, 2, 1)
    assert v.hover_at(p.x(), p.y()) is not None


# -- map manager ---------------------------------------------------------------------------

def _open_3d(win, tmp_path, qapp, text):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.resize(1100, 800)
    mgr.show()
    assert mgr.do_import(str(_csv(tmp_path, "mp.csv", text)), "mp")
    mgr.map_list.setCurrentRow(0)
    mgr.toggle_3d.setChecked(True)
    qapp.processEvents()
    assert mgr.view3d is not None
    return mgr


def _click(qapp, view, iy, ix, ny):
    xy, _ = _screen_of(view, view.scene["pos"])
    k = int(np.flatnonzero(view.scene["grid_index"] == ix * ny + iy)[0])
    QTest.mouseClick(view.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(xy[k][0]), round(xy[k][1])))
    qapp.processEvents()


def test_map_manager_3d_click_selects_the_table_cell(win, tmp_path, qapp, ops):
    mgr = _open_3d(win, tmp_path, qapp, _map_text([0, 10, 20, 30], [0, 5, 10]))
    ops.clear()
    _click(qapp, mgr.view3d, 2, 1, 3)
    assert _current(mgr) == (3, 2)
    assert [(d["cell"], d["plot"]) for d in _jumps(ops)] == [([2, 1], "3d")]
    assert mgr.preview_table.hasFocus()
    _click(qapp, mgr.view3d, 0, 3, 3)
    assert _current(mgr) == (1, 4)
    assert len(_jumps(ops)) == 2


def test_map_manager_3d_click_while_editing_uses_each_views_own_axes(win, tmp_path, qapp, ops):
    mgr = _open_3d(win, tmp_path, qapp, _map_text([0, 10, 20], [0, 5, 10]))
    mgr.preview_table.item(0, 2).setText("14")  # edit the x axis
    assert mgr.compare_active and mgr.view3d_after is not None
    changed = set(mgr._changed)
    assert mgr.view3d_after.scene["real"][:, 0].max() == 20 and 14 in mgr.view3d_after.scene["real"][:, 0]
    assert 14 not in mgr.view3d.scene["real"][:, 0]
    ops.clear()
    _click(qapp, mgr.view3d_after, 1, 1, 3)
    assert _current(mgr) == (2, 2)
    _click(qapp, mgr.view3d, 2, 0, 3)
    assert _current(mgr) == (3, 1)
    assert [(d["cell"], d["plot"]) for d in _jumps(ops)] == [([1, 1], "3d_after"), ([2, 0], "3d_before")]
    assert mgr._changed == changed and mgr.preview_table.item(0, 2).text() == "14"
