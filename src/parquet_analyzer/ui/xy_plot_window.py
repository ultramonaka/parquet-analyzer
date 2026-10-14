"""XY plot window (detailed_specification.md 25.2; analysis.ja.md 25.2, spec 5.19 stage 1).

X variable against one or more Y variables over the visible time range, optionally coloured
by time or by a Z variable. Large ranges switch to a 256x256 density heat map. The pure
computation (`compute_xy`) is separate from the window so it can be tested directly.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import features
from ..core.column import MaterializedColumn
from ..core.contour_levels import LevelSpec
from ..core.oplog import log_op
from . import xy_map as xm
from . import xy_overlay as xo
from . import xyz_3d_window as x3
from .analysis_common import chunks, format_time_tick
from .analysis_window import CANCELLED, AnalysisWindow
from .flow_layout import FlowLayout
from .i18n import tr
from .image_export import _FIG_DPI, _font_context
from .name_picker import NamePicker
from .plot_widget import _COLOR_CYCLE
from .xy_exclude import ExcludeViewBox, XYExcludeController
from .xy_map_view import XYMapViewMixin
from .xy_overlay_controller import XYOverlayController
from .xyz_3d_window import Xyz3DResult, Xyz3DView, build_xyz_figure, colors_for, compute_xyz

logger = logging.getLogger(__name__)

XY_SCATTER_LIMIT = 200_000  # total points above which "auto" switches to density (25.2)
XY_CHUNK_ROWS = 5_000_000  # rows fetched per chunk for bounds/density passes (25.2)
XY_DENSITY_BINS = 256
LEGEND_MARKER_SIZE = 8
_COLOR_LEVELS = 64  # colour quantisation for per-point brushes


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


@dataclass
class XYScatterSeries:
    name: str
    x: np.ndarray
    y: np.ndarray
    c: np.ndarray | None  # colour values (time or Z) per point, or None for single colour


@dataclass
class XYResult:
    mode: str  # "scatter" | "density"
    x_name: str
    y_names: list[str]
    z_name: str | None
    color: str  # "single" | "time" | "z" (effective)
    row_start: int
    row_end: int
    time_name: str | None = None  # the time-axis column's name when it may be an axis/Z (25.5)
    valid: dict[str, int] = field(default_factory=dict)  # per Y
    excluded: int = 0  # sum over Y of rows dropped for NaN/Inf
    excluded_per_y: dict[str, int] = field(default_factory=dict)
    thinned: tuple[int, int] | None = None  # (shown, total) when scatter was thinned
    color_range: tuple[float, float] | None = None
    scatter: list[XYScatterSeries] = field(default_factory=list)
    xedges: np.ndarray | None = None
    yedges: np.ndarray | None = None
    counts: np.ndarray | None = None  # (nx, ny)
    zmean: np.ndarray | None = None  # (nx, ny), NaN where empty; only when color == "z"
    x_bounds: tuple[float, float] | None = None
    y_bounds: tuple[float, float] | None = None

    @property
    def total_valid(self) -> int:
        return sum(self.valid.values())


def effective_color(color: str, n_y: int, z_name: str | None) -> str:
    """Z colouring needs exactly one Y and a Z; time colouring exactly one Y (25.2)."""
    if n_y != 1:
        return "single"
    if color == "z" and z_name:
        return "z"
    if color == "time":
        return "time"
    return "single"


def compute_xy(
    fetch: Callable[[str, int, int], np.ndarray],
    x_name: str,
    y_names: list[str],
    z_name: str | None,
    color: str,
    display: str,
    row_start: int,
    row_end: int,
    times: np.ndarray | None = None,
    chunk_rows: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    time_name: str | None = None,
    hidden=frozenset(),
) -> XYResult:
    """Compute what the XY window shows. `fetch(name, a, b)` returns rows [a, b) of a
    variable (also of the time column when it is chosen as X/Y/Z, 25.5; `time_name` only
    records which name that is, for the display). Rows where any variable used for a Y is
    non-finite are excluded per Y. `hidden` Ys (legend click, 44.5) are ignored: a scatter keeps
    their points (showing them again needs no recompute) and the density, which has no legend to
    bring a Y back, draws every selected Y."""
    chunk_rows = chunk_rows or XY_CHUNK_ROWS
    color = effective_color(color, len(y_names), z_name)
    use_z = color == "z"
    n_rows = max(0, row_end - row_start)

    # A range that fits one chunk is read once and reused by the second pass.
    cache: dict[tuple[str, int, int], np.ndarray] = {}

    def get(name: str, a: int, b: int) -> np.ndarray:
        key = (name, a, b)
        if n_rows <= chunk_rows:
            if key not in cache:
                cache[key] = np.asarray(fetch(name, a, b), dtype=np.float64)
            return cache[key]
        return np.asarray(fetch(name, a, b), dtype=np.float64)

    def valid_points(a: int, b: int, y_name: str):
        x = get(x_name, a, b)
        y = get(y_name, a, b)
        mask = np.isfinite(x) & np.isfinite(y)
        c = None
        if use_z:
            c = get(z_name, a, b)
            mask &= np.isfinite(c)
        elif color == "time":
            c = np.asarray(times[a:b], dtype=np.float64)
            mask &= np.isfinite(c)
        return x, y, c, mask

    result = XYResult("scatter", x_name, list(y_names), z_name if use_z else None, color, row_start, row_end,
                      time_name)
    result.valid = {y: 0 for y in y_names}
    result.excluded_per_y = {y: 0 for y in y_names}

    def stop() -> bool:
        return cancelled is not None and cancelled()

    # Pass 1: valid counts and bounds.
    xmin = ymin = cmin = math.inf
    xmax = ymax = cmax = -math.inf
    for a, b in chunks(row_start, row_end, chunk_rows):
        if stop():
            return CANCELLED
        for y_name in y_names:
            x, y, c, mask = valid_points(a, b, y_name)
            nv = int(mask.sum())
            result.valid[y_name] += nv
            result.excluded += (b - a) - nv
            result.excluded_per_y[y_name] += (b - a) - nv
            if nv:
                xv, yv = x[mask], y[mask]
                xmin, xmax = min(xmin, float(xv.min())), max(xmax, float(xv.max()))
                ymin, ymax = min(ymin, float(yv.min())), max(ymax, float(yv.max()))
                if c is not None:
                    cv = c[mask]
                    cmin, cmax = min(cmin, float(cv.min())), max(cmax, float(cv.max()))
    total = result.total_valid
    if total == 0:
        return result  # empty range / nothing valid: an empty scatter, not an error
    result.x_bounds, result.y_bounds = (xmin, xmax), (ymin, ymax)
    if cmin <= cmax:
        result.color_range = (cmin, cmax)

    if display == "auto":
        display = "density" if total > XY_SCATTER_LIMIT else "scatter"

    if display == "density":
        result.mode = "density"
        xlo, xhi = (xmin - 0.5, xmax + 0.5) if xmin == xmax else (xmin, xmax)
        ylo, yhi = (ymin - 0.5, ymax + 0.5) if ymin == ymax else (ymin, ymax)
        rng = [[xlo, xhi], [ylo, yhi]]
        counts = np.zeros((XY_DENSITY_BINS, XY_DENSITY_BINS), dtype=np.float64)
        zsum = np.zeros_like(counts) if use_z else None
        xedges, yedges = np.linspace(xlo, xhi, XY_DENSITY_BINS + 1), np.linspace(ylo, yhi, XY_DENSITY_BINS + 1)
        for a, b in chunks(row_start, row_end, chunk_rows):
            if stop():
                return CANCELLED
            for y_name in y_names:  # `hidden` is ignored: the density has no legend to bring a Y back (44.5)
                x, y, c, mask = valid_points(a, b, y_name)
                if not mask.any():
                    continue
                h, xedges, yedges = np.histogram2d(x[mask], y[mask], bins=XY_DENSITY_BINS, range=rng)
                counts += h
                if use_z:
                    hz, _, _ = np.histogram2d(x[mask], y[mask], bins=XY_DENSITY_BINS, range=rng, weights=c[mask])
                    zsum += hz
        result.xedges, result.yedges, result.counts = xedges, yedges, counts
        if use_z:
            with np.errstate(invalid="ignore", divide="ignore"):
                result.zmean = np.where(counts > 0, zsum / np.where(counts > 0, counts, 1), np.nan)
        return result

    # Scatter, evenly thinned per Y if forced beyond the limit.
    strides = {}
    for y_name in y_names:
        v = result.valid[y_name]
        if total > XY_SCATTER_LIMIT and v:
            quota = max(1, XY_SCATTER_LIMIT * v // total)
            strides[y_name] = math.ceil(v / quota)
        else:
            strides[y_name] = 1
    shown_total = 0
    for y_name in y_names:
        xs, ys, cs = [], [], []
        seen = 0  # valid points of this Y so far (global index for the stride)
        stride = strides[y_name]
        for a, b in chunks(row_start, row_end, chunk_rows):
            if stop():
                return CANCELLED
            x, y, c, mask = valid_points(a, b, y_name)
            idx = np.flatnonzero(mask)
            if not len(idx):
                continue
            sel = idx[((seen + np.arange(len(idx))) % stride) == 0]
            seen += len(idx)
            xs.append(x[sel])
            ys.append(y[sel])
            if c is not None:
                cs.append(c[sel])
        sx = np.concatenate(xs) if xs else np.empty(0)
        sy = np.concatenate(ys) if ys else np.empty(0)
        sc = np.concatenate(cs) if cs else (None if color == "single" else np.empty(0))
        shown_total += len(sx)
        result.scatter.append(XYScatterSeries(y_name, sx, sy, sc))
    if shown_total < total:
        result.thinned = (shown_total, total)
    return result


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

_DISPLAY_MODES = ("auto", "scatter", "density")
_COLOR_MODES = ("single", "time", "z")


_MODES = ("2d", "3d")


def _colormap() -> pg.ColorMap:
    return pg.colormap.get("viridis")


class _TimeAxis(pg.AxisItem):
    """An axis that can show its values as local time (25.5): used when the time-axis column
    is chosen as X or Y. The label precision follows the visible span, as for the colour bar."""

    def __init__(self, orientation: str) -> None:
        super().__init__(orientation)
        self.time_mode = False

    def set_time_mode(self, on: bool) -> None:
        if on != self.time_mode:
            self.time_mode = on
            self.enableAutoSIPrefix(not on)  # "+1.767e9" prefixes make no sense for a clock
            self.picture = None
            self.update()

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph override)
        if not self.time_mode:
            return super().tickStrings(values, scale, spacing)
        lo, hi = self.range
        span = abs(hi - lo)
        return [format_time_tick(v, span) for v in values]


class XYPlotWindow(XYMapViewMixin, AnalysisWindow):
    """The XY plot window, with a 2D/3D toggle (25.5): X, Y (2D: several, 3D: one), Z (2D: colour
    variable, 3D: third axis), colour and display mode in one wrapping row; the time-axis column is
    a candidate for every axis."""

    def __init__(self, main, x_name: str = "", y_names: list[str] | None = None, mode: str = "2d"):
        self._init_x = x_name
        self._init_y = list(y_names or [])
        self._result: XYResult | Xyz3DResult | None = None
        self._grid_invalid = False  # the grid settings were refused at the last compute: export disabled (44.1)
        self._mode = "2d"
        self._view3d = None  # Xyz3DView, created on the first switch to 3D (needs OpenGL)
        self._gl3d_ok = False
        self._gl3d_reason = ""
        # Series hidden by a click on their legend entry (44.5). Names, not items: it survives a
        # redraw/recompute and is pruned to the selected Y's. The legend's own click only toggled
        # its proxy swatch (the real points are separate items), which is why nothing happened.
        self._hidden_y: set[str] = set()
        self._series_items: dict[str, object] = {}  # Y name -> the plotted item(s)
        self._legend_proxies: dict[object, str] = {}  # legend swatch item -> Y name
        self._map_items: dict = {}  # the heat map's image / hatch / points items (tests, hover)
        self._map_notice = ""  # why the map display was switched off (time axis chosen), shown in the status
        self._median_total: int | None = None  # valid points of the last map computation (median limit, 44.2)
        # Overlaid registered maps / curves (44.7, 44.12): state + menu + drawing are in the controller
        # (its `state.hidden` / `state.notice` are read by the legend click and the status line below).
        self._overlays = XYOverlayController(self)
        self._keep_view_gen = -1  # generation of a click / rectangle recompute: its result keeps the view (44.13)
        self._exclude = XYExcludeController(self)  # points taken out of the map by click / rectangle (44.13)
        self._status_core = ""
        super().__init__(main, tr("xy.title"))
        self._apply_color_constraints()
        self._cursor_item = pg.ScatterPlotItem(
            size=12, pen=pg.mkPen("w", width=2), brush=pg.mkBrush(None), pxMode=True
        )
        self._cursor_item.setZValue(100)
        self._plot.addItem(self._cursor_item)
        main.plot_grid.cursorsChanged.connect(self._on_cursors_changed)
        self._add_map_actions()
        self._update_map_controls()
        self._exclude.remember_variables()
        self._refresh_pending = False
        model = main.variable_panel.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            sig.connect(self._schedule_refresh)
        if mode == "3d" and self._gl3d_ok:
            self._set_mode("3d", log=False, recompute=False)  # the opener computes
        log_op(
            "xy_plot_open", mode=self._mode, x=self._x_combo.currentText(), y=self.selected_y(),
            window_count=len(main._analysis_windows) + 1,
        )

    # -- candidates ----------------------------------------------------------------

    def _candidates(self) -> list[str]:
        """Every variable plus the time-axis column (first), 25.5."""
        names = self._main.visible_column_names()  # aliases instead of their sources (persistence 54.2)
        tc = self._time_name()
        return ([tc] if tc and tc not in names else []) + names

    def _data_names(self) -> list[str]:
        """The candidates without the time column: what the combos default to."""
        return self._main.visible_column_names()

    def _time_name(self) -> str | None:
        """The time axis as the combos show it (its alias when it has one)."""
        return self._main._display_name(self._main._time_column)

    def _is_time(self, name: str | None) -> bool:
        # by source column: the display name can change while the window is open (54.4)
        return bool(name) and self._main._alias_sources.get(name, name) == self._main._time_column

    def used_variable_names(self) -> set[str]:
        """Names this window currently plots (for MainWindow's alias re-resolution, 54.4)."""
        return {self._x_combo.currentText(), self._z_combo.currentText(), *self.selected_y()} - {""}

    # -- settings panel ------------------------------------------------------------

    def _probe_3d(self) -> None:
        """3D needs the xyz_3d flag and a working OpenGL (25.5). The probe runs once per session
        (cached); a failure is logged once and shown as the toggle's tooltip, not as a dialog."""
        self._gl3d_ok, self._gl3d_reason = False, ""
        if not features.is_enabled("xyz_3d"):
            return
        first = x3.gl3d_status() is None
        ok, reason = x3.check_gl3d()
        self._gl3d_ok, self._gl3d_reason = ok, reason
        if not ok and first:
            logger.warning("3D view disabled: %s", reason)
            log_op("xyz_3d_unavailable", reason=reason)

    def _group(self, flow, label_text: str, widget: QWidget) -> tuple[QWidget, QLabel]:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        lay.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(widget)
        flow.addWidget(box)
        return box, label

    def build_settings_panel(self) -> QWidget:
        self._probe_3d()
        names = self._candidates()
        data_names = self._data_names()
        panel = QWidget()
        flow = FlowLayout(panel, 0, 14, 4)
        self._flow = flow
        self._x_combo = QComboBox()
        self._x_combo.addItems(names)
        self._x_combo.setCurrentText(self._init_x if self._init_x in names else (data_names[0] if data_names else ""))
        self._group(flow, tr("xy.x"), self._x_combo)
        self._y_picker = NamePicker()  # button -> searchable checkbox list (44.5)
        self._y_picker.set_candidates(names)
        self._y_picker.set_selected(self._init_y)
        self._y_group, self._y_label = self._group(flow, tr("xy.y"), self._y_picker)
        self._z_combo = QComboBox()
        self._z_combo.addItems(names)
        self._z_combo.setCurrentText(self._default_z(data_names))
        self._z_group, self._z_label = self._group(flow, tr("xy.z"), self._z_combo)
        self._color_combo = QComboBox()
        for mode in _COLOR_MODES:
            self._color_combo.addItem(tr(f"xy.color_{mode}"), mode)
        self._group(flow, tr("xy.color"), self._color_combo)
        # 2D / 3D toggle: only offered with the xyz_3d flag; usable only when OpenGL works (25.5)
        self._mode_group = QWidget()
        mlay = QHBoxLayout(self._mode_group)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(0)
        self._mode_buttons: dict[str, QPushButton] = {}
        bg = QButtonGroup(self._mode_group)
        bg.setExclusive(True)
        for mode in _MODES:
            b = QPushButton(tr(f"xy.mode_{mode}"))
            b.setCheckable(True)
            b.setChecked(mode == "2d")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            bg.addButton(b)
            mlay.addWidget(b)
            self._mode_buttons[mode] = b
            b.clicked.connect(lambda _c=False, m=mode: self._on_mode_clicked(m))
        self._mode_button_group = bg
        if not self._gl3d_ok:
            b3 = self._mode_buttons["3d"]
            b3.setEnabled(False)
            b3.setToolTip(tr("xy.mode_3d_unavailable", reason=self._gl3d_reason or "-"))
        box = QWidget()
        blay = QHBoxLayout(box)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.addWidget(QLabel(tr("xy.mode")))
        blay.addWidget(self._mode_group)
        box.setVisible(features.is_enabled("xyz_3d"))
        flow.addWidget(box)
        self._mode_box = box
        self._display_combo = QComboBox()
        for mode in _DISPLAY_MODES:
            self._display_combo.addItem(tr(f"xy.display_{mode}"), mode)
        self._display_group, _lab = self._group(flow, tr("xy.display"), self._display_combo)
        self._build_map_controls(flow)

        self._x_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._y_picker.selectionChanged.connect(self._on_setting_changed)
        self._color_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._z_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._display_combo.currentIndexChanged.connect(self._on_setting_changed)
        return panel

    @staticmethod
    def _default_z(data_names: list[str]) -> str:
        """The third variable (the old 3D view's prefill) when there is one, else the first."""
        if not data_names:
            return ""
        return data_names[2] if len(data_names) > 2 else data_names[0]

    def _schedule_refresh(self, *_a) -> None:
        if not self._refresh_pending and not self._closed:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh_candidates)

    def _refresh_candidates(self) -> None:
        """Variable list changed (derived variable added/deleted): refresh the X/Y/Z
        candidates, keeping each selection that still exists (a vanished one is left
        empty rather than silently replaced; the next recompute says so in the status)."""
        self._refresh_pending = False
        if self._closed:
            return
        names = self._candidates()
        x, z, ys = self._x_combo.currentText(), self._z_combo.currentText(), set(self.selected_y())
        widgets = (self._x_combo, self._y_picker, self._z_combo)
        for wdg in widgets:
            wdg.blockSignals(True)
        self._x_combo.clear()
        self._x_combo.addItems(names)
        self._x_combo.setCurrentIndex(names.index(x) if x in names else -1)
        self._z_combo.clear()
        self._z_combo.addItems(names)
        self._z_combo.setCurrentIndex(names.index(z) if z in names else -1)
        self._y_picker.set_candidates(names)
        self._y_picker.set_selected(ys)
        for wdg in widgets:
            wdg.blockSignals(False)
        self._apply_color_constraints()

    def selected_y(self) -> list[str]:
        return self._y_picker.selected()

    def _color_mode(self) -> str:
        return self._color_combo.currentData()

    @property
    def mode(self) -> str:
        return self._mode

    def _apply_color_constraints(self) -> None:
        """2D: colour by time/Z only with exactly one Y (25.2); the Z combo only when colour = Z.
        3D: Z is the third axis (always enabled) and a single colour is not offered, the points
        are coloured by Z or time (25.5)."""
        mode3d = self._mode == "3d"
        single_y = len(self.selected_y()) == 1
        model = self._color_combo.model()
        model.item(0).setEnabled(not mode3d)
        for i in (1, 2):
            model.item(i).setEnabled(mode3d or single_y)
        if mode3d and self._color_mode() == "single":
            self._color_combo.blockSignals(True)
            self._color_combo.setCurrentIndex(_COLOR_MODES.index("z"))
            self._color_combo.blockSignals(False)
        elif not mode3d and not single_y and self._color_mode() != "single":
            self._color_combo.blockSignals(True)
            self._color_combo.setCurrentIndex(0)
            self._color_combo.blockSignals(False)
        self._z_combo.setEnabled(mode3d or (single_y and self._color_mode() == "z"))

    def _on_setting_changed(self, *_a) -> None:
        self._forget_grids_if_variables_changed()
        self._apply_color_constraints()
        self._apply_map_constraints()
        self._exclude.on_variables_maybe_changed()
        self._update_map_controls()
        self.recompute(keep_range=True)

    # -- 2D / 3D toggle (25.5) -----------------------------------------------------

    def _on_mode_clicked(self, mode: str) -> None:
        self._set_mode(mode)

    def _set_mode(self, mode: str, log: bool = True, recompute: bool = True) -> None:
        """Switch between 2D and 3D, keeping the X/Y/Z/colour selections (to 3D with several Y:
        the first one; with no Y: the second variable, as the old 3D view prefilled)."""
        if mode == self._mode:
            return
        if mode == "3d" and not self._gl3d_ok:
            self._mode_buttons["2d"].setChecked(True)
            return
        if log:
            log_op("xy_plot_mode", mode=mode, x=self._x_combo.currentText(), y=self.selected_y())
        self._mode = mode
        self._mode_buttons[mode].setChecked(True)
        three = mode == "3d"
        self._y_picker.blockSignals(True)
        if three:
            ys = self.selected_y()
            keep = ys[0] if ys else None
            if keep is None:
                data = self._data_names()
                keep = data[1] if len(data) > 1 else None
            self._y_picker.set_single(True)
            self._y_picker.set_selected([keep] if keep else [])
        else:
            self._y_picker.set_single(False)
        self._y_picker.blockSignals(False)
        self._y_label.setText(tr("xy.y_single") if three else tr("xy.y"))
        self._z_label.setText(tr("xy.z_axis") if three else tr("xy.z"))
        self._display_group.setVisible(not three)
        self._apply_color_constraints()
        self._apply_map_constraints()
        self._exclude.remember_variables()  # the 2D/3D switch itself keeps the excluded rows (44.13)
        self._update_map_controls()
        self._stack.setCurrentIndex(1 if three else 0)
        if three:
            self._ensure_view3d()
            self._cursor_item.setData([], [])
        self._overlays.rebuild_menu()
        self._overlays.remove_items()
        self._overlays.entries()  # curves stay selected but are not drawn in 3D (44.10); redrawn after the recompute
        if recompute:
            self.recompute(keep_range=True)

    def _ensure_view3d(self):
        if self._view3d is None:
            self._view3d = Xyz3DView()
            self._stack.addWidget(self._view3d)
            self._stack.setCurrentWidget(self._view3d)
        return self._view3d

    # -- view ----------------------------------------------------------------------

    def build_view(self) -> QWidget:
        self._stack = QStackedWidget()
        self._glw = pg.GraphicsLayoutWidget()
        self._plot = self._glw.addPlot(
            row=0, col=0, axisItems={"bottom": _TimeAxis("bottom"), "left": _TimeAxis("left")},
            viewBox=ExcludeViewBox(),
        )
        self._plot.vb.exclude_handler = self._exclude  # left click / drag pick points while the mode is on (44.13)
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._legend = self._plot.addLegend()
        self._legend.sigSampleClicked.connect(self._on_legend_clicked)
        self._colorbar = pg.ColorBarItem(values=(0, 1), colorMap=_colormap(), interactive=False)
        self._glw.addItem(self._colorbar, row=0, col=1)
        self._colorbar.setVisible(False)
        page = QWidget()  # the 2D plot with the map's hover readout in a normal label below (44.3)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self._glw, 1)
        self._hover_label = QLabel("")
        self._hover_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._hover_label)
        self._stack.addWidget(page)
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        return self._stack

    # -- compute -------------------------------------------------------------------

    def _resolve_columns(self, needed: list[str]) -> dict:
        """Names -> Column objects, resolved here on the UI thread (the worker must never look
        names up on MainWindow, whose state may change -- time-column switch, file reopen --
        while the job runs). The time-axis column maps to a Column over the main window's
        time values (25.5)."""
        main = self._main
        columns = {}
        for name in needed:
            if self._is_time(name):
                columns[name] = MaterializedColumn(main._time_values)
                continue
            col = main._raw_variables.get(name)
            if col is None:
                raise ValueError(tr("xy.unknown_variable", name=name))
            columns[name] = col
        return columns

    def make_job(self, row_start: int, row_end: int, cancelled):
        if self._map_selected():
            return self._make_job_map(row_start, row_end, cancelled)
        if self._mode == "3d":
            return self._make_job_3d(row_start, row_end, cancelled)
        x_name = self._x_combo.currentText()
        y_names = self.selected_y()
        if not x_name or not y_names:
            raise ValueError(tr("xy.need_xy"))
        z_name = self._z_combo.currentText() or None
        color = self._color_mode()
        display = self._display_combo.currentData()
        main = self._main
        use_z = effective_color(color, len(y_names), z_name) == "z"
        needed = [x_name, *y_names, *([z_name] if use_z else [])]
        columns = self._resolve_columns(needed)
        times = main._time_values
        time_name = self._time_name()
        hidden = set(self._hidden_y) & set(y_names)

        def fetch(name: str, a: int, b: int) -> np.ndarray:
            return columns[name].window(a, b)

        log_op(
            "xy_plot_compute", x=x_name, y=y_names, z=z_name if use_z else None,
            color=color, display=display, row_start=row_start, row_end=row_end,
        )
        return lambda: compute_xy(
            fetch, x_name, y_names, z_name, color, display, row_start, row_end, times, cancelled=cancelled,
            time_name=time_name, hidden=frozenset(hidden),
        )

    def _make_job_3d(self, row_start: int, row_end: int, cancelled):
        names = (self._x_combo.currentText(), (self.selected_y() or [""])[0], self._z_combo.currentText())
        if not all(names):
            raise ValueError(tr("xyz.need_xyz"))
        color = self._color_mode()
        main = self._main
        columns = self._resolve_columns(list(names))
        times = main._time_values
        time_name = self._time_name()
        log_op("xyz_3d_compute", x=names[0], y=names[1], z=names[2], color=color,
               row_start=row_start, row_end=row_end)
        return lambda: compute_xyz(
            lambda n, a, b: columns[n].window(a, b), *names, color, row_start, row_end, times,
            cancelled=cancelled, time_name=time_name,
        )

    def _prune_hidden(self, y_names: list[str], colour_mapped: bool) -> None:
        """Hidden series follow the selection (a deselected Y is forgotten); with a colour-mapped
        single series there is no legend entry to bring it back, so nothing stays hidden."""
        self._hidden_y &= set(y_names)
        if colour_mapped:
            self._hidden_y.clear()

    def on_result(self, result) -> None:
        self._result = result
        if isinstance(result, xm.XYMapResult):
            self._show_map(result)
            return
        self._hover_label.setText("")
        self._update_export_action()
        if isinstance(result, XYResult):
            self._prune_hidden(result.y_names, result.color != "single")
            self._draw(result)
            self._update_status(result)
            self._on_cursors_changed(self._main.plot_grid.cursor_times())
        else:
            self._show_3d(result)

    def _range_text(self, r) -> str:
        main = self._main
        times = main._time_values
        if r.row_end > r.row_start and times is not None and len(times):
            lo, hi = float(times[r.row_start]), float(times[min(r.row_end, len(times)) - 1])
            anchor = main.plot_grid.anchor_plot()
            fmt = anchor.format_x if anchor is not None else (lambda v: f"{v:.6g}")
            return f"{fmt(lo)} - {fmt(hi)}"
        return tr("stats.empty_range")

    def _update_status(self, r: XYResult) -> None:
        if len(r.y_names) > 1:
            excluded = ", ".join(f"{n}: {r.excluded_per_y.get(n, 0)}" for n in r.y_names)
        else:
            excluded = str(r.excluded)
        parts = [
            tr("xy.status_range", range=self._range_text(r), rows=r.row_end - r.row_start),
            tr("xy.status_points", points=r.total_valid, excluded=excluded),
        ]
        if r.total_valid == 0:
            parts.append(tr("xy.status_no_points"))
        elif r.mode == "density":
            parts.append(tr("xy.status_density"))
        if r.thinned:
            parts.append(tr("xy.status_thinned", shown=r.thinned[0], total=r.thinned[1]))
        if self._map_notice:
            parts.append(self._map_notice)
        self.set_status("  |  ".join(parts))

    # -- 3D display ----------------------------------------------------------------

    def _show_3d(self, r: Xyz3DResult) -> None:
        self._draw_scene_3d(r)
        self._overlays.refresh(rebase=False)
        self._update_status_3d(r)

    def _draw_scene_3d(self, r) -> None:
        """The 3D base scene of a points or map result; its Z range is widened to fit the overlaid maps (44.7)."""
        if isinstance(r, xm.XYMapResult):
            view = self._ensure_view3d()
            if not r.grids or r.n_valid == 0 or r.no_data:
                view.clear()
            else:
                self._show_map_3d_scene(r, view)
            return
        view = self._ensure_view3d()
        labels = (r.x_name, r.y_name, r.z_name)
        if r.valid:
            time_axes = tuple(k for k, n in zip("xyz", labels) if n == r.time_name)
            if r.color == "single":
                rgba = QColor(_COLOR_CYCLE[0]).getRgbF()
                colors = np.tile(np.asarray(rgba, dtype=np.float32), (len(r.x), 1))
                view.set_colorbar(None)
            else:
                lo, hi = r.color_range
                colors = colors_for(r.c, lo, hi)
                c_is_time = r.color == "time" or (r.color == "z" and r.z_name == r.time_name)
                view.set_colorbar(r.color_range, time_ticks=c_is_time)
            view.set_scatter(r.x, r.y, r.z, colors, bounds=self._overlays.widened_bounds(r.bounds), labels=labels,
                             times=r.c if r.color == "time" else None, time_axes=time_axes)
        else:
            view.clear()

    def _update_status_3d(self, r: Xyz3DResult) -> None:
        parts = [
            tr("xy.status_range", range=self._range_text(r), rows=r.row_end - r.row_start),
            tr("xyz.status_points", points=r.valid, excluded=r.excluded),
        ]
        if r.valid == 0:
            parts.append(tr("xy.status_no_points"))
        if r.thinned:
            parts.append(tr("xy.status_thinned", shown=r.thinned[0], total=r.thinned[1]))
        self.set_status("  |  ".join(parts))

    # -- overlay of registered maps / curves (analysis.ja.md 44.7, 44.12) ---------------------------
    # State, menu, drawing and levels live in `XYOverlayController` (`self._overlays`); these are the
    # public entry points the main window, the image export and the tests call, plus the `OverlayHost`
    # interface the controller draws through.

    def overlay_names(self) -> list[str]:
        return self._overlays.names()

    def set_overlay(self, name: str, on: bool) -> None:
        """Add / remove a registered table as an overlay (44.7)."""
        self._overlays.set_overlay(name, on)

    def set_contour_spec(self, spec: LevelSpec) -> None:
        self._overlays.set_contour_spec(spec)

    def contour_spec(self) -> LevelSpec:
        return self._overlays.state.contour_spec

    def choose_contour_levels(self) -> bool:
        """The "Contour levels..." dialog; the accepted spec applies to every overlaid map (and the image)."""
        return self._overlays.choose_contour_levels()

    def contour_plan(self) -> dict[str, list[float]]:
        """The contour values per visible overlaid map, as drawn and as exported."""
        return self._overlays.contour_plan()

    def on_registry_changed(self) -> None:
        """The main window's maps changed (edit / delete / overwrite, 27.8): redraw or drop overlays."""
        self._overlays.on_registry_changed()

    # OverlayHost (xy_overlay_controller.py)

    @property
    def result(self):
        return self._result

    @property
    def plot(self):
        return self._plot

    @property
    def legend(self):
        return self._legend

    @property
    def view3d(self):
        return self._view3d

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def status_core(self) -> str:
        return self._status_core

    def x_name(self) -> str:
        return self._x_combo.currentText()

    def registry_tables(self) -> dict:
        return dict(self._main.function_registry.maps)

    def add_legend_entry(self, proxy, label: str) -> None:
        self._add_legend_entry(proxy, label)

    def redraw_scene_3d(self, r) -> None:
        self._draw_scene_3d(r)

    def map_bounds_3d(self, r) -> dict:
        return self._map_bounds_3d(r)

    def dialog_parent(self):
        return self


    def set_status(self, text: str) -> None:
        """The status line = the display's own text plus the overlay notice (44.7)."""
        self._status_core = text
        notice = self._overlays.state.notice
        if notice and text and self._result is not None:
            text = f"{text}  |  {notice}"
        super().set_status(text)

    # -- 2D display ----------------------------------------------------------------

    def _draw(self, r: XYResult) -> None:
        plot = self._plot
        self._clear_plot_items()
        self._legend.clear()
        self._series_items.clear()
        self._legend_proxies.clear()
        self._colorbar.setVisible(False)
        plot.setLabel("bottom", r.x_name)
        plot.setLabel("left", ", ".join(r.y_names))
        tc = r.time_name
        plot.getAxis("bottom").set_time_mode(tc is not None and r.x_name == tc)
        plot.getAxis("left").set_time_mode(tc is not None and all(n == tc for n in r.y_names))
        cmap = _colormap()
        if r.mode == "density" and r.counts is not None:
            self._draw_density(r, cmap)
        else:
            self._draw_scatter(r, cmap)
        if r.x_bounds and r.y_bounds:
            plot.setRange(xRange=self._pad(r.x_bounds), yRange=self._pad(r.y_bounds), padding=0)
        self._overlays.refresh()

    @staticmethod
    def _pad(bounds: tuple[float, float]) -> tuple[float, float]:
        lo, hi = bounds
        span = (hi - lo) or 1.0
        return lo - 0.03 * span, hi + 0.03 * span

    @staticmethod
    def _color_is_time(r: XYResult) -> bool:
        """The colour bar reads as time when colouring by time, or by a Z that is the time column."""
        return r.color == "time" or (r.color == "z" and r.z_name is not None and r.z_name == r.time_name)

    def _legend_series_visible(self, name: str) -> bool:
        return name not in self._hidden_y

    def _add_legend_entry(self, proxy, name: str) -> None:
        """Legend entry whose name text toggles the series like the swatch does (44.5; pyqtgraph's
        `sigSampleClicked` only fires on the swatch)."""
        self._legend.addItem(proxy, name)
        sample, label = self._legend.items[-1]

        def on_click(event, proxy=proxy, sample=sample) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                proxy.setVisible(not proxy.isVisible())  # what the swatch's own click does first
                sample.update()
                self._on_legend_clicked(proxy)
            event.accept()

        label.mouseClickEvent = on_click  # found by the scene's click dispatch via hasattr

    def _on_legend_clicked(self, proxy) -> None:
        """A click on a legend entry (44.5). pyqtgraph's `ItemSample` has already flipped the
        swatch's visibility; mirror that onto the real series and remember it by name."""
        oname = self._overlays.proxies.get(proxy)
        if oname is not None:
            hide = not proxy.isVisible()
            log_op("xy_plot_series_visibility", y=oname, hidden=hide, overlay=True)
            self.set_overlay_hidden(oname, hide)
            return
        name = self._legend_proxies.get(proxy)
        if name is None:
            return
        hide = not proxy.isVisible()
        log_op("xy_plot_series_visibility", y=name, hidden=hide)
        self.set_series_hidden(name, hide)

    def set_series_hidden(self, name: str, hidden: bool) -> None:
        """Hide / show one Y's series: not drawn, not highlighted by the cursors, not in the image."""
        if hidden:
            self._hidden_y.add(name)
        else:
            self._hidden_y.discard(name)
        for proxy, n in self._legend_proxies.items():
            if n == name:
                proxy.setVisible(not hidden)
        item = self._series_items.get(name)
        if item is not None:
            for it in item if isinstance(item, (list, tuple)) else [item]:
                it.setVisible(not hidden)
        for sample, _label in self._legend.items:  # repaint the swatch's eye icon
            sample.update()
        self._on_cursors_changed(self._main.plot_grid.cursor_times())

    def set_overlay_hidden(self, name: str, hidden: bool) -> None:
        """Hide / show one overlay (legend click, 44.7). Levels are planned over the visible maps only,
        so the lines are redrawn."""
        self._overlays.set_hidden(name, hidden)

    def hidden_series(self) -> set[str]:
        return set(self._hidden_y)

    def _draw_scatter(self, r: XYResult, cmap: pg.ColorMap) -> None:
        for i, s in enumerate(r.scatter):
            base = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
            kwargs = {}
            if s.c is not None and len(s.c) and r.color_range:
                lo, hi = r.color_range
                norm = (s.c - lo) / ((hi - lo) or 1.0)
                level = np.clip((norm * (_COLOR_LEVELS - 1)).astype(int), 0, _COLOR_LEVELS - 1)
                brushes = [QBrush(cmap.map(k / (_COLOR_LEVELS - 1), mode="qcolor")) for k in range(_COLOR_LEVELS)]
                kwargs["brush"] = [brushes[k] for k in level]
            else:
                kwargs["brush"] = pg.mkBrush(base)
            item = pg.ScatterPlotItem(s.x, s.y, size=3, pen=None, pxMode=True, **kwargs)
            self._plot.addItem(item)
            self._series_items[s.name] = item
            item.setVisible(self._legend_series_visible(s.name))
            if not isinstance(kwargs["brush"], list):  # colour-mapped: the colour bar says it
                # Legend swatch from a data-less proxy with a bigger marker: the plotted
                # points are 3 px, too small to read as a legend sample.
                proxy = pg.ScatterPlotItem(size=LEGEND_MARKER_SIZE, pen=None, brush=pg.mkBrush(base))
                proxy.setVisible(self._legend_series_visible(s.name))
                self._legend_proxies[proxy] = s.name
                self._add_legend_entry(proxy, s.name)
        if r.color != "single" and r.color_range:
            self._set_colorbar(r.color_range, time_ticks=self._color_is_time(r))

    def _density_image(self, r: XYResult, cmap: pg.ColorMap):
        """(RGBA uint8 (nx, ny, 4), (lo, hi) of the colour scale)."""
        if r.color == "z" and r.zmean is not None:
            data = r.zmean
            valid = np.isfinite(data)
            lo, hi = (float(np.nanmin(data)), float(np.nanmax(data))) if valid.any() else (0.0, 1.0)
        else:
            data = np.log1p(r.counts)
            valid = r.counts > 0
            lo, hi = 0.0, float(data.max()) or 1.0
        norm = np.where(valid, (np.nan_to_num(data) - lo) / ((hi - lo) or 1.0), 0.0)
        rgba = cmap.map(np.clip(norm, 0, 1), mode="byte").reshape(data.shape + (4,)).copy()
        rgba[~valid, 3] = 0
        return rgba, (lo, hi)

    def _draw_density(self, r: XYResult, cmap: pg.ColorMap) -> None:
        rgba, levels = self._density_image(r, cmap)
        img = pg.ImageItem(rgba)
        xe, ye = r.xedges, r.yedges
        img.setRect(QRectF(xe[0], ye[0], xe[-1] - xe[0], ye[-1] - ye[0]))
        self._plot.addItem(img)
        self._set_colorbar(levels, time_ticks=r.color == "z" and self._color_is_time(r))

    def _set_colorbar(self, values: tuple[float, float], time_ticks: bool = False) -> None:
        lo, hi = values
        axis = self._colorbar.axis
        if time_ticks:
            span = hi - lo
            axis.tickStrings = lambda vals, scale, spacing: [format_time_tick(v, span) for v in vals]
            axis.setWidth(110)
        else:
            axis.tickStrings = type(axis).tickStrings.__get__(axis)
            axis.setWidth(45)
        axis.picture = None
        axis.update()
        self._colorbar.setLevels((lo, hi if hi > lo else lo + 1.0))
        self._colorbar.setVisible(True)

    # -- cursors -------------------------------------------------------------------

    def _value_at(self, name: str, row: int) -> float:
        """One value of a variable at `row`; the time column reads the main window's time values."""
        main = self._main
        if self._is_time(name):
            return float(main._time_values[row])
        return float(main.variable_window(name, row, row + 1)[0])

    def _on_cursors_changed(self, times_list) -> None:
        """Highlight the point at each cursor's time (25.2). Cursors outside the computed
        row range are ignored. Only the 2D display has the highlight (the 3D view has none); a 2D
        map / curve shows the cursor row's measured (x, y) too (44.3)."""
        if self._closed:
            return
        r = self._result
        pts_x, pts_y = [], []
        main = self._main
        tv = main._time_values
        # a scatter / density, or the 2D map / curve (44.3: the same marker; the 3D view has none)
        shown = isinstance(r, XYResult) or (isinstance(r, xm.XYMapResult) and not r.is_3d and not r.no_data)
        if shown and tv is not None and len(tv) and r.row_end > r.row_start:
            skip_hidden = not (isinstance(r, XYResult) and r.mode == "density")
            y_list = r.y_names[:1] if isinstance(r, xm.XYMapResult) and not r.is_curve else r.y_names
            for t in times_list:
                row = self.nearest_row(tv, t)
                if row is None or not (r.row_start <= row < r.row_end):
                    continue
                try:
                    xv = self._value_at(r.x_name, row)
                    for y_name in y_list:
                        if y_name in self._hidden_y and skip_hidden:
                            continue
                        yv = self._value_at(y_name, row)
                        if np.isfinite(xv) and np.isfinite(yv):
                            pts_x.append(xv)
                            pts_y.append(yv)
                except Exception:  # noqa: BLE001 - a failed single-row read just skips the highlight
                    logger.exception("xy cursor highlight read failed")
        self._cursor_item.setData(pts_x, pts_y)

    def cursor_highlight_points(self) -> list[tuple[float, float]]:
        d = self._cursor_item.getData()
        return list(zip(map(float, d[0]), map(float, d[1])))

    @staticmethod
    def nearest_row(tv: np.ndarray, x: float) -> int | None:
        idx = int(np.searchsorted(tv, x))
        if idx <= 0:
            return 0
        if idx >= len(tv):
            return len(tv) - 1
        return idx - 1 if (x - tv[idx - 1]) <= (tv[idx] - x) else idx

    def on_close(self) -> None:
        model = self._main.variable_panel.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            try:
                sig.disconnect(self._schedule_refresh)
            except (RuntimeError, TypeError):
                pass
        try:
            self._main.plot_grid.cursorsChanged.disconnect(self._on_cursors_changed)
        except (RuntimeError, TypeError):
            pass

    # -- export --------------------------------------------------------------------

    def export_stamp_extra(self):
        lines = self._export_stamp_base()
        names = [n for n in self.overlay_names() if not self._overlays.is_hidden(n)]
        if lines is None or not names:
            return lines
        lines = [lines] if isinstance(lines, str) else list(lines)
        return [*lines, tr("xy.overlay_stamp", names=", ".join(names))]

    def _export_stamp_base(self):
        r = self._result
        if r is None:
            return None
        if isinstance(r, xm.XYMapResult):
            text = f"X: {r.x_name}  Y: {', '.join(r.y_names)}"
            if r.z_name:
                text += f"  Z: {r.z_name}"
            return [text, r.describe_grid()]
        if isinstance(r, Xyz3DResult):
            return f"X: {r.x_name}  Y: {r.y_name}  Z: {r.z_name}"
        text = f"X: {r.x_name}  Y: {', '.join(r.y_names)}"
        if r.z_name:
            text += f"  Z: {r.z_name}"
        return text

    def build_export_figure(self, stamp_lines: list[str], size_px: tuple[int, int]):
        fig = self._build_base_figure(stamp_lines, size_px)
        if fig is None or not self.overlay_names():
            return fig
        entries = [e for e in self._overlays.entries() if not self._overlays.is_hidden(e.name)]
        plan = self._overlays.planned_levels(entries)  # the same levels as on screen (44.7)
        xo.apply_overlays_to_figure(fig, entries, plan, is_3d=self._mode == "3d")
        return fig

    def _build_base_figure(self, stamp_lines: list[str], size_px: tuple[int, int]):
        r = self._result
        if r is None:
            return None
        if isinstance(r, xm.XYMapResult):
            if r.no_data or not r.grids:
                return None
            angles = self._view3d.view_angles() if (r.is_3d and self._view3d is not None) else (25.0, -60.0)
            return xm.build_xy_map_figure(r, stamp_lines, size_px, self._hidden_y, angles,
                                          show_points=self._points_check.isChecked())
        if isinstance(r, Xyz3DResult):
            if not r.valid or self._view3d is None:
                return None
            elev, azim = self._view3d.view_angles()
            return build_xyz_figure(r, stamp_lines, size_px, elev, azim)
        return build_xy_figure(r, stamp_lines, size_px, hidden=self._hidden_y)


def build_xy_figure(r: XYResult, stamp_lines, size_px, hidden=frozenset()):
    """matplotlib Figure of an XYResult (25.2). Scatter is rasterized so SVG/PDF stay small.
    A time-axis column used as X/Y (or as the colour) reads as local time (25.5)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    if r.mode == "density":
        hidden = frozenset()  # no legend in the density: every selected Y is drawn (44.5)

    def time_formatter(bounds):
        lo, hi = bounds if bounds else (0.0, 1.0)
        return FuncFormatter(lambda v, _pos, span=hi - lo: format_time_tick(v, span))

    with _font_context():
        width, height = max(int(size_px[0]), 200), max(int(size_px[1]), 150)
        fig = Figure(figsize=(width / _FIG_DPI, height / _FIG_DPI), dpi=_FIG_DPI, facecolor="white", layout="constrained")
        FigureCanvasAgg(fig)
        n_stamp = max(len(stamp_lines), 1)
        stamp_h = (n_stamp * 7 * 1.25 + 4) / 72 / (height / _FIG_DPI)
        fig.get_layout_engine().set(rect=(0, stamp_h, 1, 1 - stamp_h))
        ax = fig.subplots()
        c_is_time = XYPlotWindow._color_is_time(r)
        if r.mode == "density" and r.counts is not None:
            if r.color == "z" and r.zmean is not None:
                data, label = np.ma.masked_invalid(r.zmean), r.z_name or "Z"
            else:
                data, label = np.ma.masked_where(r.counts <= 0, np.log1p(r.counts)), "log(1 + count)"
            mesh = ax.pcolormesh(r.xedges, r.yedges, data.T, cmap="viridis", rasterized=True)
            cb = fig.colorbar(mesh, ax=ax, label=label)
            if r.color == "z" and r.zmean is not None and c_is_time and np.isfinite(r.zmean).any():
                zlo, zhi = float(np.nanmin(r.zmean)), float(np.nanmax(r.zmean))
                cb.formatter = time_formatter((zlo, zhi))
                cb.update_ticks()
        else:
            for i, s in enumerate(r.scatter):
                if s.name in hidden:  # hidden by a legend click (44.5)
                    continue
                color = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
                if s.c is not None and len(s.c):
                    sc = ax.scatter(s.x, s.y, s=2, c=s.c, cmap="viridis", vmin=r.color_range[0], vmax=r.color_range[1],
                                    rasterized=True, label=s.name)
                else:
                    sc = ax.scatter(s.x, s.y, s=2, color=color, rasterized=True, label=s.name)
            if r.color != "single" and r.scatter and r.scatter[0].c is not None and len(r.scatter[0].c):
                cb = fig.colorbar(sc, ax=ax, label=r.z_name if r.color == "z" else tr("xy.color_time"))
                if c_is_time and r.color_range:
                    cb.formatter = time_formatter(r.color_range)
                    cb.update_ticks()
            elif any(s.name not in hidden for s in r.scatter):
                ax.legend(loc="upper right", fontsize=8, framealpha=0.7, markerscale=4)
        ax.set_xlabel(r.x_name)
        ax.set_ylabel(", ".join(n for n in r.y_names if n not in hidden) or ", ".join(r.y_names))
        if r.time_name is not None:
            if r.x_name == r.time_name:
                ax.xaxis.set_major_formatter(time_formatter(r.x_bounds))
            if all(n == r.time_name for n in r.y_names):
                ax.yaxis.set_major_formatter(time_formatter(r.y_bounds))
        ax.grid(True, alpha=0.3)
        if stamp_lines:
            fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
    return fig
