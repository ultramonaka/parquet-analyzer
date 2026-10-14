"""Taking measured points out of a map by clicking / rectangle-dragging them (analysis.ja.md 5.39, 44.13).

`XYExcludeController` keeps the excluded file rows of one XY window, the two buttons ("Exclude points",
"Restore all"), the selection by click / rectangle on the 2D plot and the worker job that selects the
rows of a rectangle. `ExcludeViewBox` hands the left mouse button to the controller while the mode is
on. Every change of the excluded set re-runs the map computation, which skips those rows (`compute_xy_map`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..core.oplog import log_op
from . import xy_map as xm
from .analysis_common import chunks
from .analysis_window import CANCELLED
from .i18n import tr

logger = logging.getLogger(__name__)

HIT_RADIUS_PX = 6  # a click takes the nearest point within this many pixels (44.13)
CROSS_SIZE = 7
CROSS_COLOR = (120, 120, 120)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def rows_in_rect(
    fetch: Callable[[str, int, int], np.ndarray],
    x_name: str,
    y_names: list[str],
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    row_start: int,
    row_end: int,
    chunk_rows: int,
    cancelled: Callable[[], bool] | None = None,
):
    """File rows of [row_start, row_end) whose X is inside `x_range` and (any of) `y_names` inside
    `y_range` (both closed, ascending), as a sorted int64 array; `CANCELLED` when `cancelled()` turned
    true. Selection is by value over all rows, not over the (thinned) drawn points (44.13)."""
    x0, x1 = x_range
    y0, y1 = y_range
    parts: list[np.ndarray] = []
    for a, b in chunks(row_start, row_end, chunk_rows):
        if cancelled is not None and cancelled():
            return CANCELLED
        x = np.asarray(fetch(x_name, a, b), dtype=np.float64)
        in_x = (x >= x0) & (x <= x1)  # NaN compares False
        if not in_x.any():
            continue
        hit = np.zeros(len(x), dtype=bool)
        for name in y_names:
            y = np.asarray(fetch(name, a, b), dtype=np.float64)
            hit |= in_x & (y >= y0) & (y <= y1)
        idx = np.flatnonzero(hit)
        if len(idx):
            parts.append(idx.astype(np.int64) + a)
    return np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)


def nearest_point(
    candidates: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]],
    cx: float,
    cy: float,
    pixel: tuple[float, float],
    radius_px: float = HIT_RADIUS_PX,
):
    """The candidate point nearest to (cx, cy) in pixels, if within `radius_px`. `candidates` =
    `(kind, x, y, rows)` per series (kind "point" or "cross"); returns `(kind, row)` or None."""
    dx, dy = pixel
    if not (dx > 0 and dy > 0):
        return None
    best: tuple[float, str, int] | None = None
    for kind, x, y, rows in candidates:
        if not len(x):
            continue
        d2 = ((x - cx) / dx) ** 2 + ((y - cy) / dy) ** 2
        i = int(np.argmin(d2))
        if d2[i] <= radius_px**2 and (best is None or d2[i] < best[0]):
            best = (float(d2[i]), kind, int(rows[i]))
    return None if best is None else (best[1], best[2])


class PassiveScatter(pg.ScatterPlotItem):
    """A scatter item that lets mouse clicks fall through to the ViewBox (pyqtgraph's own scatter accepts a click
    on a point, which would swallow the click that picks it, 44.13)."""

    def mouseClickEvent(self, ev):  # noqa: N802 (pyqtgraph override)
        ev.ignore()


def excluded_item(x: np.ndarray, y: np.ndarray) -> pg.ScatterPlotItem:
    """The grey crosses of the excluded points (44.13)."""
    item = PassiveScatter(np.asarray(x), np.asarray(y), symbol="x", size=CROSS_SIZE, pxMode=True,
                              pen=pg.mkPen(CROSS_COLOR, width=1.5), brush=None)
    item.setZValue(3)
    return item


# ---------------------------------------------------------------------------
# View box
# ---------------------------------------------------------------------------


class ExcludeViewBox(pg.ViewBox):
    """A ViewBox whose left click / left drag go to `exclude_handler` while it is `active()` (44.13);
    wheel zoom, right drag and everything else stay pyqtgraph's. Otherwise it behaves like a ViewBox."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.exclude_handler = None

    def _taking(self, ev) -> bool:
        h = self.exclude_handler
        return h is not None and ev.button() == Qt.MouseButton.LeftButton and h.active()

    def mouseClickEvent(self, ev):  # noqa: N802 (pyqtgraph override)
        if self._taking(ev):
            ev.accept()
            p = self.mapToView(ev.pos())
            self.exclude_handler.on_click(float(p.x()), float(p.y()))
            return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 (pyqtgraph override)
        if self._taking(ev):
            ev.accept()
            self.updateScaleBox(ev.buttonDownPos(), ev.pos())  # the rubber band
            if ev.isFinish():
                self.rbScaleBox.hide()
                a, b = self.mapToView(ev.buttonDownPos()), self.mapToView(ev.pos())
                self.exclude_handler.on_rect(float(a.x()), float(a.y()), float(b.x()), float(b.y()))
            return
        super().mouseDragEvent(ev, axis)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class XYExcludeController:
    """State and operations of "exclude points" for one `XYPlotWindow` (`window`). Needs from the window:
    `_main`, `_mode`, `_result`, `_plot`, `_hidden_y`, `_points_check`, `_workers`, `_closed`, `_map_notice`,
    `_map_enabled`, `_map_selected`, `selected_y`, `_x_combo`, `_z_combo`, `_resolve_columns`, `recompute`,
    `set_status`."""

    def __init__(self, window) -> None:
        self._w = window
        self.rows = np.empty(0, dtype=np.int64)  # sorted unique file rows, replaced (never mutated) on change
        self.group: QWidget | None = None
        self.mode_button: QPushButton | None = None
        self.reset_button: QPushButton | None = None
        self._select_generation = 0
        self._vars: tuple | None = None
        self._notice = ""  # shown once, in the next status update (44.13 (7))

    # -- widgets ---------------------------------------------------------------------

    def build_controls(self, flow) -> QWidget:
        """The two buttons in one group for the map settings row."""
        group = QWidget()
        lay = QHBoxLayout(group)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.mode_button = QPushButton(tr("xy.exclude_mode"))
        self.mode_button.setCheckable(True)
        self.mode_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_button.setToolTip(tr("xy.exclude_mode_tooltip"))
        self.reset_button = QPushButton(tr("xy.exclude_reset"))
        self.reset_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reset_button.setToolTip(tr("xy.exclude_reset_tooltip"))
        self.reset_button.setEnabled(False)
        lay.addWidget(self.mode_button)
        lay.addWidget(self.reset_button)
        flow.addWidget(group)
        self.group = group
        self.mode_button.toggled.connect(self._on_mode_toggled)
        self.reset_button.clicked.connect(lambda *_: self.reset())
        return group

    def update_buttons(self) -> None:
        """Enable / disable by the 2D/3D state and the excluded count (called with the map controls)."""
        if self.mode_button is None:
            return
        three = self._w._mode == "3d"
        if three and self.mode_button.isChecked():
            self.mode_button.blockSignals(True)
            self.mode_button.setChecked(False)
            self.mode_button.blockSignals(False)
        self.mode_button.setEnabled(not three)
        self.mode_button.setToolTip(tr("xy.exclude_mode_3d") if three else tr("xy.exclude_mode_tooltip"))
        self.reset_button.setEnabled(len(self.rows) > 0)

    def active(self) -> bool:
        """Whether the left mouse button selects points now (mode on, 2D map / curve with points shown)."""
        w = self._w
        return bool(
            self.mode_button is not None and self.mode_button.isChecked() and w._mode == "2d"
            and w._map_enabled and w._map_selected() and isinstance(w._result, xm.XYMapResult)
            and not w._result.is_3d
        )

    def _on_mode_toggled(self, on: bool) -> None:
        log_op("xy_map_exclude_mode", enabled=bool(on))
        if on and not self._w._points_check.isChecked():
            self._w._points_check.setChecked(True)  # invisible points cannot be picked (44.13)

    def points_hidden(self) -> None:
        """The measured points were switched off: nothing to pick, leave the mode."""
        if self.mode_button is not None and self.mode_button.isChecked():
            self.mode_button.setChecked(False)

    # -- variables (44.13 (1)) -------------------------------------------------------

    def _signature(self) -> tuple:
        w = self._w
        return (w._x_combo.currentText(), tuple(w.selected_y()), w._z_combo.currentText())

    def remember_variables(self) -> None:
        self._vars = self._signature()

    def on_variables_maybe_changed(self) -> None:
        """A different X / Y / Z makes the excluded rows meaningless: restore them all and say so once. A
        rectangle still being selected is stale whatever the set holds (44.13 (7))."""
        now = self._signature()
        changed = self._vars is not None and now != self._vars
        self._vars = now
        if changed:
            self._select_generation += 1
        if changed and len(self.rows):
            log_op("xy_map_exclude", kind="cleared", changed=int(len(self.rows)), total=0)
            self._set_rows(np.empty(0, dtype=np.int64))
            self._notice = tr("xy.exclude_cleared")

    def take_notice(self) -> str:
        """The one-shot notice for the status line ("" when none)."""
        text, self._notice = self._notice, ""
        return text

    def result_is_current(self) -> bool:
        """Whether the shown result belongs to the variables chosen now (false while a recompute after a
        change of X / Y / Z is pending): then clicks and rectangles are not taken (44.13 (7))."""
        w = self._w
        r = w._result
        if not isinstance(r, xm.XYMapResult):
            return False
        return (r.x_name == w._x_combo.currentText() and list(r.y_names) == list(w.selected_y())
                and (r.kind != "map" or r.z_name == w._z_combo.currentText()))

    # -- changes of the set ----------------------------------------------------------

    def _set_rows(self, rows: np.ndarray) -> None:
        self.rows = rows
        self._select_generation += 1  # a rectangle still being selected belongs to the old set
        self.update_buttons()

    def _apply(self, new_rows: np.ndarray, keep_view: bool = True) -> None:
        """Set the excluded rows and recompute; a click / rectangle keeps the view range (44.13 (7)), tied to
        that recompute's generation (a settings recompute coming later re-fits as usual)."""
        self._set_rows(new_rows)
        w = self._w
        w.recompute(keep_range=True)
        if keep_view:
            w._keep_view_gen = w._generation

    def reset(self) -> None:
        if not len(self.rows):
            return
        log_op("xy_map_exclude", kind="reset", changed=int(len(self.rows)), total=0)
        self._apply(np.empty(0, dtype=np.int64), keep_view=False)

    # -- mouse (called by ExcludeViewBox) --------------------------------------------

    def on_click(self, cx: float, cy: float) -> None:
        w = self._w
        r = w._result
        if not isinstance(r, xm.XYMapResult) or r.is_3d or not self.result_is_current():
            return
        hidden = w._hidden_y
        cand = []
        for i, name in enumerate(r.names):
            if r.is_curve and name in hidden:
                continue
            if i < len(r.points) and i < len(r.point_rows) and len(r.points[i][0]):
                pts = r.points[i]
                cand.append(("point", np.asarray(pts[0]), np.asarray(pts[1]), r.point_rows[i]))
            if i < len(r.excluded_points) and len(r.excluded_points[i][0]):
                pts = r.excluded_points[i]
                cand.append(("cross", np.asarray(pts[0]), np.asarray(pts[1]), r.excluded_point_rows[i]))
        hit = nearest_point(cand, cx, cy, w._plot.vb.viewPixelSize())
        if hit is None:
            return
        kind, row = hit
        if kind == "cross" and not np.any(self.rows == row):
            return  # a stale cross: that row is already back in
        if kind == "point":
            if np.any(self.rows == row):
                return  # already out (a stale drawing before the recompute arrives)
            log_op("xy_map_exclude", kind="click_exclude", changed=1, total=int(len(self.rows)) + 1, row=row)
            self._apply(np.union1d(self.rows, [row]).astype(np.int64))
        else:
            log_op("xy_map_exclude", kind="click_restore", changed=1, total=max(int(len(self.rows)) - 1, 0), row=row)
            self._apply(self.rows[self.rows != row])

    def on_rect(self, xa: float, ya: float, xb: float, yb: float) -> None:
        """Select every row of the shown range whose values fall inside the rectangle (worker job)."""
        w = self._w
        r = w._result
        if w._closed or not isinstance(r, xm.XYMapResult) or r.is_3d or not self.result_is_current():
            return
        coords = (xa, ya, xb, yb)
        if not all(np.isfinite(c) for c in coords):
            return  # never hand non-finite coordinates on (data 29.4)
        x_rng, y_rng = (min(xa, xb), max(xa, xb)), (min(ya, yb), max(ya, yb))
        names = [n for n in r.names if not (r.is_curve and n in w._hidden_y)]
        if not names:
            return
        y_names = list(r.y_names[:1]) if not r.is_curve else names
        try:
            columns = w._resolve_columns([r.x_name, *y_names])
        except ValueError as exc:
            w.set_status(str(exc))
            return
        from . import xy_plot_window as xpw  # late: tests patch the module's constants

        chunk_rows = xpw.XY_CHUNK_ROWS
        row_start, row_end, x_name = r.row_start, r.row_end, r.x_name
        self._select_generation += 1
        generation = self._select_generation
        signature = self._signature()
        snapshot = self.rows  # replaced, never mutated: the union is made in the worker
        w.set_status(tr("xy.exclude_selecting"))
        log_op("xy_map_exclude", kind="rect", x=x_name, y=y_names, x_range=list(x_rng), y_range=list(y_rng),
               row_start=row_start, row_end=row_end)

        def cancelled() -> bool:
            return w._closed or self._select_generation != generation

        def job():
            found = rows_in_rect(lambda n, a, b: columns[n].window(a, b), x_name, y_names, x_rng, y_rng,
                                 row_start, row_end, chunk_rows, cancelled)
            if found is CANCELLED:
                return CANCELLED
            new = np.union1d(snapshot, found).astype(np.int64)
            return new, int(len(new) - len(snapshot))

        box: list[int] = []
        token = w._main._analysis_hub.submit(
            job,
            lambda res, g=generation, sig=signature: self._on_selected(res, box[0], g, sig),
            lambda msg, g=generation: self._on_select_failed(msg, box[0], g),
            job="analysis",
            target=type(w).__name__,
        )
        box.append(token)
        w._workers.add(token)

    def _on_selected(self, result, key: int, generation: int, signature: tuple) -> None:
        w = self._w
        if w._closed:
            return
        w._workers.discard(key)
        if generation != self._select_generation or result is CANCELLED or signature != self._signature():
            return
        new, added = result
        log_op("xy_map_exclude", kind="rect_done", changed=added, total=int(len(new)))
        if added == 0:
            w.set_status(tr("xy.exclude_none_in_rect"))
            return
        self._apply(new)

    def _on_select_failed(self, message: str, key: int, generation: int) -> None:
        w = self._w
        if w._closed:
            return
        w._workers.discard(key)
        if generation != self._select_generation:
            return
        logger.error("excluding points failed: %s", message)
        w.set_status(tr("xy.exclude_failed", error=message))
