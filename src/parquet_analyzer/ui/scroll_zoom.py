"""Mouse-wheel handling shared by every plot that follows ``Settings.scroll_bindings``
(plots.ja.md ch.4 wheel table; analysis.ja.md 42.16).

Used by ``TimePlotWidget.wheelEvent`` and by the histogram plots; meant to be reusable for the
XY map view as well. Pure logic on a pyqtgraph ``ViewBox`` -- no state of its own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ..io.settings import DEFAULT_SCROLL_BINDINGS

PAN_STEP_FRACTION = 0.15  # of the current visible X span, per wheel "notch" (120 units)


def modifier_key(modifiers) -> str:
    """"none" / "ctrl" / "shift" for the held keyboard modifiers.

    On macOS, Qt swaps Cmd/Ctrl so the *physical* Ctrl key arrives as MetaModifier -- either counts
    as "Ctrl". Shift wins when both are held (the original fixed scheme's precedence).
    """
    ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    return "shift" if shift else "ctrl" if ctrl else "none"


def wheel_action(modifiers, bindings: dict[str, str]) -> str:
    """The configured action ("x_zoom" / "y_zoom" / "pan") for the held modifiers."""
    key = modifier_key(modifiers)
    return bindings.get(key, DEFAULT_SCROLL_BINDINGS[key])


def apply_wheel(widget, ev, bindings: dict[str, str]) -> str:
    """Perform the bound action of wheel event ``ev`` on ``widget``'s view box and accept the event.

    ``widget`` is a ``pg.PlotWidget`` (needs ``getViewBox()`` and ``mapToScene``); ``bindings`` is a
    full {"none", "ctrl", "shift"} -> action mapping. Some trackpads report Shift+vertical-scroll as
    a horizontal wheel event, so the delta is whichever of angleDelta().y()/.x() is non-zero.
    Returns the action performed.
    """
    action = wheel_action(ev.modifiers(), bindings)
    angle_delta = ev.angleDelta()
    delta = angle_delta.y() or angle_delta.x()
    vb = widget.getViewBox()

    if action == "pan":
        x_min, x_max = vb.viewRange()[0]
        shift_amount = -delta / 120.0 * (x_max - x_min) * PAN_STEP_FRACTION
        vb.setXRange(x_min + shift_amount, x_max + shift_amount, padding=0)
        ev.accept()
        return action

    scale = 0.999**delta
    scene_pos = widget.mapToScene(ev.position().toPoint())
    center = vb.mapSceneToView(scene_pos)
    vb.scaleBy((1.0, scale) if action == "y_zoom" else (scale, 1.0), center=center)
    ev.accept()
    return action
