"""ui/scroll_zoom.py: the wheel logic shared by the time plots and the histogram plots
(plots ch.4, analysis 42.16)."""

from __future__ import annotations

import itertools

import pyqtgraph as pg
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from parquet_analyzer.ui.scroll_zoom import (
    PAN_STEP_FRACTION,
    apply_wheel,
    modifier_key,
    wheel_action,
)

_MOD = Qt.KeyboardModifier


def _wheel(dy, modifiers=_MOD.NoModifier, dx=0, pos=(50, 50)) -> QWheelEvent:
    p = QPointF(*pos)
    return QWheelEvent(p, p, QPoint(0, 0), QPoint(dx, dy), Qt.MouseButton.NoButton, modifiers,
                       Qt.ScrollPhase.NoScrollPhase, False)


def _widget(qapp):
    w = pg.PlotWidget()
    w.resize(400, 300)
    w.show()
    w.getViewBox().disableAutoRange()
    w.getViewBox().setRange(xRange=(0, 100), yRange=(0, 10), padding=0)
    return w


def test_modifier_key_and_precedence():
    assert modifier_key(_MOD.NoModifier) == "none"
    assert modifier_key(_MOD.ControlModifier) == "ctrl"
    assert modifier_key(_MOD.MetaModifier) == "ctrl"  # macOS: physical Ctrl
    assert modifier_key(_MOD.ShiftModifier) == "shift"
    assert modifier_key(_MOD.ShiftModifier | _MOD.ControlModifier) == "shift"


@pytest.mark.parametrize(
    "bindings",
    [dict(zip(("none", "ctrl", "shift"), p, strict=True))
     for p in itertools.permutations(("x_zoom", "y_zoom", "pan"))],
)
def test_wheel_action_follows_every_binding(bindings):
    assert wheel_action(_MOD.NoModifier, bindings) == bindings["none"]
    assert wheel_action(_MOD.ControlModifier, bindings) == bindings["ctrl"]
    assert wheel_action(_MOD.MetaModifier, bindings) == bindings["ctrl"]
    assert wheel_action(_MOD.ShiftModifier, bindings) == bindings["shift"]


def test_x_zoom_keeps_y_and_y_zoom_keeps_x(qapp):
    w = _widget(qapp)
    vb = w.getViewBox()
    ev = _wheel(120)
    assert apply_wheel(w, ev, {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}) == "x_zoom"
    assert ev.isAccepted()
    (x0, x1), (y0, y1) = vb.viewRange()
    assert (x1 - x0) < 100 and (y0, y1) == pytest.approx((0, 10))
    vb.setRange(xRange=(0, 100), yRange=(0, 10), padding=0)
    apply_wheel(w, _wheel(120, _MOD.ControlModifier), {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"})
    (x0, x1), (y0, y1) = vb.viewRange()
    assert (x0, x1) == pytest.approx((0, 100)) and (y1 - y0) < 10


def test_pan_moves_by_fraction_of_the_width_per_notch(qapp):
    w = _widget(qapp)
    vb = w.getViewBox()
    apply_wheel(w, _wheel(120, _MOD.ShiftModifier), {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"})
    x0, x1 = vb.viewRange()[0]
    assert (x0, x1) == pytest.approx((-100 * PAN_STEP_FRACTION, 100 - 100 * PAN_STEP_FRACTION))


def test_horizontal_wheel_delta_is_used_when_vertical_is_zero(qapp):
    w = _widget(qapp)
    apply_wheel(w, _wheel(0, _MOD.ShiftModifier, dx=120), {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"})
    assert w.getViewBox().viewRange()[0][0] < 0


def test_zoom_centre_is_the_cursor_position(qapp):
    w = _widget(qapp)
    vb = w.getViewBox()
    pos = (60, 50)
    scene = w.mapToScene(QPoint(*pos))
    cx = vb.mapSceneToView(scene).x()
    apply_wheel(w, _wheel(120, pos=pos), {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"})
    # the data coordinate under the cursor does not move
    assert vb.mapSceneToView(scene).x() == pytest.approx(cx)
