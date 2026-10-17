"""Histogram plots: the normal plots' mouse operation (analysis.ja.md 42.16, plots ch.4).

Wheel events go through Qt's real routing (sent to the plot's viewport), not to the handler."""

from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

# fixtures / helpers shared with the other histogram window tests
from test_histogram_views_window import (  # noqa: F401
    _drain,
    _no_modal_dialogs,
    _open,
    _utc,
    _view,
    multiday_path,
    mw,
    ops,
)

from parquet_analyzer.io.settings import DEFAULT_SCROLL_BINDINGS

_MOD = Qt.KeyboardModifier
_V2 = {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}


def _widget(w, view):
    return {"dist": w._plot_widget, "trend": w._trend_widget, "timeofday": w._tod_widget}[view]


def _vb(w, view):
    return {"dist": w._plot, "trend": w._trend_plot, "timeofday": w._tod_plot}[view].getViewBox()


def _wheel(widget, dy, modifiers=_MOD.NoModifier, dx=0):
    pos = QPointF(widget.viewport().width() / 2, widget.viewport().height() / 2)
    ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(dx, dy), Qt.MouseButton.NoButton, modifiers,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget.viewport(), ev)
    QApplication.processEvents()
    return ev


@pytest.fixture(params=["dist", "trend", "timeofday"])
def view_win(request, qapp, mw):
    w = _open(qapp, mw)
    if request.param != "dist":
        _view(qapp, w, request.param)
    _drain(qapp)
    w.set_scroll_bindings(_V2)
    return w, request.param


def test_default_bindings_follow_settings(qapp, mw):
    w = _open(qapp, mw)
    for view in ("dist", "trend", "timeofday"):
        assert _widget(w, view).scroll_bindings == DEFAULT_SCROLL_BINDINGS


def test_wheel_x_zoom_y_zoom_and_pan(view_win):
    w, view = view_win
    wd, vb = _widget(w, view), _vb(w, view)
    x0, y0 = copy.deepcopy(vb.viewRange())
    ev = _wheel(wd, 120)  # none -> x_zoom
    assert ev.isAccepted()
    x1, y1 = vb.viewRange()
    assert (x1[1] - x1[0]) < (x0[1] - x0[0])
    if view != "trend":  # the trend's Y follows the visible values
        assert y1 == pytest.approx(y0)
    span = x1[1] - x1[0]
    _wheel(wd, 120, _MOD.ShiftModifier)  # shift -> pan
    x2 = vb.viewRange()[0]
    assert x2[0] != pytest.approx(x1[0])
    if view != "timeofday":  # (the 0-24 h limit may clamp the pan)
        assert (x2[1] - x2[0]) == pytest.approx(span, rel=1e-6)
    ya = vb.viewRange()[1]
    _wheel(wd, 120, _MOD.ControlModifier)  # ctrl -> y_zoom
    yb = vb.viewRange()[1]
    assert (yb[1] - yb[0]) < (ya[1] - ya[0])


def test_metamodifier_and_horizontal_wheel(view_win):
    w, view = view_win
    wd, vb = _widget(w, view), _vb(w, view)
    ya = vb.viewRange()[1]
    _wheel(wd, 120, _MOD.MetaModifier)  # physical Ctrl on macOS -> "ctrl" -> y_zoom
    yb = vb.viewRange()[1]
    assert (yb[1] - yb[0]) < (ya[1] - ya[0])
    _wheel(wd, 120)  # make room to pan in every view
    _wheel(wd, 120)
    xa = vb.viewRange()[0]
    _wheel(wd, 0, _MOD.ShiftModifier, dx=120)  # Shift+scroll arriving as a horizontal wheel -> pan
    assert vb.viewRange()[0][0] != pytest.approx(xa[0])


def test_changed_bindings_reach_open_windows_through_apply_settings(qapp, mw):
    w = _open(qapp, mw)
    s = copy.deepcopy(mw.settings)
    s.scroll_bindings = {"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"}
    mw.apply_settings(s)
    for view in ("dist", "trend", "timeofday"):
        assert _widget(w, view).scroll_bindings == s.scroll_bindings
    x0 = _vb(w, "dist").viewRange()[0]
    _wheel(w._plot_widget, 120)  # plain wheel now pans
    x1 = _vb(w, "dist").viewRange()[0]
    assert (x1[1] - x1[0]) == pytest.approx(x0[1] - x0[0], rel=1e-6) and x1[0] != pytest.approx(x0[0])


def test_h_key_and_double_click_restore_first_view(view_win, ops):
    w, view = view_win
    wd, vb = _widget(w, view), _vb(w, view)
    home = copy.deepcopy(vb.viewRange())
    _wheel(wd, 240)
    _wheel(wd, 120, _MOD.ControlModifier)
    assert vb.viewRange()[0] != pytest.approx(home[0])
    wd.setFocus()
    QTest.keyClick(wd, Qt.Key.Key_H)
    assert vb.viewRange()[0] == pytest.approx(home[0], rel=1e-6)
    assert vb.viewRange()[1] == pytest.approx(home[1], rel=1e-6)
    assert any(a == "histogram_view_reset" and k["view"] == view for a, k in ops)
    _wheel(wd, 240)
    QTest.mouseDClick(wd.viewport(), Qt.MouseButton.LeftButton)
    assert vb.viewRange()[0] == pytest.approx(home[0], rel=1e-6)


def test_timeofday_never_leaves_0_to_24h(qapp, mw):
    w = _open(qapp, mw)
    _view(qapp, w, "timeofday")
    w.set_scroll_bindings(_V2)
    wd, vb = w._tod_widget, w._tod_vb
    for _ in range(5):
        _wheel(wd, -240)  # zoom out far
    lo, hi = vb.viewRange()[0]
    assert lo >= -1e-6 and hi <= 86400 + 1e-6
    _wheel(wd, 240)
    for _ in range(20):
        _wheel(wd, -120, _MOD.ShiftModifier)  # pan left past the start
    assert vb.viewRange()[0][0] >= -1e-6
    for _ in range(40):
        _wheel(wd, 120, _MOD.ShiftModifier)
    assert vb.viewRange()[0][1] <= 86400 + 1e-6


def test_trend_manual_y_stops_auto_fit_until_h(qapp, mw):
    w = _open(qapp, mw)
    _view(qapp, w, "trend")
    w.set_scroll_bindings(_V2)
    wd, vb = w._trend_widget, w._trend_vb
    assert not w._trend_y_manual
    home_y = copy.deepcopy(vb.viewRange()[1])
    _wheel(wd, 240, _MOD.ControlModifier)  # y_zoom: Y is now by hand
    assert w._trend_y_manual
    manual_y = copy.deepcopy(vb.viewRange()[1])
    assert manual_y != pytest.approx(home_y)
    _wheel(wd, 240)  # X zoom / pan: the visible values change, but Y must stay
    _wheel(wd, 120, _MOD.ShiftModifier)
    assert vb.viewRange()[1] == pytest.approx(manual_y)
    wd.setFocus()
    QTest.keyClick(wd, Qt.Key.Key_H)
    assert not w._trend_y_manual
    assert vb.viewRange()[1] == pytest.approx(home_y)


def test_trend_x_zoom_alone_keeps_auto_fit(qapp, mw):
    w = _open(qapp, mw)
    _view(qapp, w, "trend")
    w.set_scroll_bindings(_V2)
    _wheel(w._trend_widget, 240)
    assert not w._trend_y_manual
