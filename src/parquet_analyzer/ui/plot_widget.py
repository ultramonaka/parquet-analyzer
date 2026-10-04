from __future__ import annotations

import html
import itertools
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThreadPool, Qt, QTimer, Signal
from PySide6.QtWidgets import QMenu, QWidget

from ..core.background_worker import WorkerHub
from ..core.column import DerivedColumn
from ..core.downsample import lttb
from ..core.oplog import log_op
from .. import features
from ..io.settings import DEFAULT_SCROLL_BINDINGS, normalize_scroll_bindings
from .i18n import tr

# MIME type of a plot-reorder drag (detailed_specification.md 22.3). Deliberately carries no
# text: the variable drop above accepts anything with hasText().
PLOT_MIME = "application/x-parquet-analyzer-plot"

_COLOR_CYCLE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

_HOVER_COLOR = "#bbbbbb"  # hover line (39.1): light grey (visible on the dark plot background), dotted -- unlike the orange dashed placed cursors
_HOVER_THROTTLE_MS = 16  # ~60 Hz cap on mouse-move handling (39.1)
_HOVER_SETTLE_MS = 100  # mouse idle this long -> replace the approximate values with real samples (39.2)

_CURSOR_COLOR = "#ffaa00"  # distinct from _COLOR_CYCLE so a cursor never blends into a series

_PAN_STEP_FRACTION = 0.15  # of the current visible X span, per wheel "notch" (120 units)
_REDRAW_DEBOUNCE_MS = 120  # coalesce rapid sigRangeChanged bursts (drag-pan, scroll) into one redraw

# Above this many visible rows, a windowed series (Phase D) prefers its pyramid's
# coarse min/max envelope (once built) over a real windowed fetch — a real fetch
# this wide costs close to what materializing the whole column always cost (the
# gap detailed_specification.md 13.5.1's Phase D re-investigation found and this
# constant closes). Chosen as a fixed row count (~40MB at 8 bytes/row for a single
# fetch), not a multiplier of the pixel-derived n_out: n_out varies with window
# width, but the point where a real fetch's own cost/memory becomes the concern
# doesn't. A real fetch below this is fast and small regardless of zoom (measured
# ~14ms for 400,000 rows against the 4GB reproduction file) and still uses full
# per-sample precision, which the pyramid's per-bucket min/max cannot offer.
_PYRAMID_PREFERRED_ROW_THRESHOLD = 5_000_000

# A dedicated pool for pyramid-build jobs was tried here (separate from
# QThreadPool.globalInstance(), which real windowed fetches and LTTB recompute
# use) to fix an observed delay: with 4 large WindowedColumns plotted at once,
# none of their pyramids had finished after 1.5s, and go_home() peak climbed over
# several presses before stabilizing. A controlled A/B measurement against
# data/raw/daily_cycle_4gb.parquet disproved the thread-pool-contention
# hypothesis, though: a dedicated pool with the same effective concurrency as the
# global one finished all 4 pyramids in the same ~3.5s either way, and an
# artificially small dedicated pool (maxThreadCount=2) was *slower* (~3.8s) than
# just using the global pool, from needlessly serializing what could run more in
# parallel. The real explanation is simpler and not fixable by rescheduling:
# each pyramid build streams close to the whole column (~700MB for this file) via
# pyarrow's iter_batches, which just takes a few seconds of wall-clock I/O
# regardless of which pool runs it — see detailed_specification.md 13.5.1's
# pyramid-wiring follow-up for the full comparison. Kept on the shared global
# pool (self._thread_pool, same as real fetches) as a result — no separate pool.


# Legend label of a hidden series (23.1). Clearly dimmer than pyqtgraph's default label
# foreground (#969696), or a hidden series would not look any different.
_HIDDEN_LABEL_COLOR = "#505050"
_SELECTED_PEN_WIDTH = 2.5  # curve width of the selected series (23.2)
_NORMAL_PEN_WIDTH = 1


class _SeriesSample(pg.ItemSample):
    """Legend line sample whose click toggles the series through the plot's own state
    (detailed_specification.md 23.1). pyqtgraph's default ItemSample.mouseClickEvent flips
    the curve's visibility directly, which would bypass TimePlotWidget's `visible` flag
    (the single source of truth) -- so it is overridden to route through it instead."""

    def mouseClickEvent(self, event):  # noqa: N802 (pyqtgraph override)
        if event.button() == Qt.MouseButton.LeftButton:
            owner = getattr(self.item, "_pa_owner", None)
            if owner is not None:
                plot, name = owner
                plot.set_series_visible(name, not plot.is_series_visible(name))
        event.accept()
        self.update()


class _SeriesLegend(pg.LegendItem):
    """LegendItem whose name labels are clickable (-> series selection, 23.2)."""

    def addItem(self, item, name):  # noqa: N802 (pyqtgraph override)
        super().addItem(item, name)
        _sample, label = self.items[-1]

        def on_click(event, item=item):
            if event.button() == Qt.MouseButton.LeftButton:
                owner = getattr(item, "_pa_owner", None)
                if owner is not None:
                    plot, series_name = owner
                    plot.seriesLabelClicked.emit(plot, series_name)
            event.accept()

        label.mouseClickEvent = on_click  # found by the scene's click dispatch via hasattr


class AlignedAxisItem(pg.AxisItem):
    """Left axis whose actual width is set from outside (detailed_specification.md 22.6).

    pyqtgraph sizes an AxisItem to its own tick text, so stacked plots' plot areas start
    at different x. This records that natural width (``_updateWidth`` override), notifies
    when it changes, and takes its real width from ``set_aligned_width`` so
    ``PlotGridWidget`` can give every plot the widest one.
    """

    naturalWidthChanged = Signal()

    # Class-level defaults: pyqtgraph calls _updateWidth from AxisItem.__init__, before
    # our own __init__ body runs.
    _natural_width: float = 0.0
    _aligned_width: float | None = None

    def natural_width(self) -> float:
        return self._natural_width

    def set_aligned_width(self, width: float | None) -> None:
        if width == self._aligned_width:
            return
        self._aligned_width = width
        self._updateWidth()

    def _updateWidth(self) -> None:  # noqa: N802 (pyqtgraph override)
        fixed = self.fixedWidth
        self.fixedWidth = None  # let pyqtgraph compute its own tick-text-based width
        try:
            super()._updateWidth()
        finally:
            self.fixedWidth = fixed
        natural = float(self.maximumWidth())
        if fixed is not None:
            natural = float(fixed)
        changed = natural != self._natural_width
        self._natural_width = natural
        width = natural if self._aligned_width is None else max(natural, self._aligned_width)
        self.setMaximumWidth(width)
        self.setMinimumWidth(width)
        if changed:
            self.naturalWidthChanged.emit()


class TimePlotWidget(pg.PlotWidget):
    """A single time-series plot (specification.md 5.1/5.3/5.4).

    Owns zoom/pan behavior (mouse wheel: which modifier performs which of
    time-zoom/amp-zoom/pan is configurable — see self.scroll_bindings, defaulting to
    plain=time zoom, Ctrl=amp zoom, Shift=time pan — detailed_specification.md 4章),
    holds one or more overlaid series, and recomputes their LTTB-downsampled display
    data off the UI thread whenever the visible X range changes.
    """

    removeRequested = Signal(object)
    separateRequested = Signal(object)
    moveUpRequested = Signal(object)
    moveDownRequested = Signal(object)
    rangeChanged = Signal(object, tuple)  # self, (x_min, x_max)
    variableDropped = Signal(object, str)  # self, variable_name
    downsampleStarted = Signal()
    downsampleFinished = Signal()
    renderFailed = Signal(object, str, str)  # self, series name, message (detailed_specification.md 28.2)
    coarseRenderingChanged = Signal(object, bool)  # self, True while >=1 series shows a pyramid envelope
    cursorAddRequested = Signal(object, float)  # self, x -- background click while cursor mode is on
    cursorMoveRequested = Signal(object, str, float)  # self, cursor_id, new x -- dragged in this plot
    cursorRemoveRequested = Signal(object, str)  # self, cursor_id -- clicked (not dragged) in this plot
    # Per-series operations (detailed_specification.md 23)
    seriesVisibilityChanged = Signal(object, str, bool)  # self, name, visible
    seriesLabelClicked = Signal(object, str)  # self, name -- legend name label clicked
    seriesRemoved = Signal(object, str)  # self, name -- left this plot (removed or transferred)
    removeSeriesRequested = Signal(object, str)  # context menu "Delete series"
    separateSeriesRequested = Signal(object, str)  # context menu "Separate series into new plot"
    backgroundClicked = Signal(object)  # self -- left click on empty plot area
    linkXRequested = Signal(object, bool)  # self, desired linked state -- context menu "Link X axis" (24.3)
    statsForRangeRequested = Signal(object)  # self -- context menu "Stats for this range" (24.2)
    hoverXChanged = Signal(object, object)  # self, x (float) or None -- mouse moved/left (39.1)

    def __init__(
        self,
        plot_id: str,
        auto_redraw: bool = True,
        downsample_pixel_ratio: int = 2,
        x_axis_datetime: bool = True,
        scroll_bindings: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ):
        # DateAxisItem renders X (epoch seconds) as date/time, automatically switching
        # granularity with zoom level (year/month when zoomed out, down to
        # hour:minute:second.millisecond when zoomed into a few seconds). Not used for
        # non-time X axes (e.g. FFT plots, whose X is frequency in Hz).
        # The left axis is an AlignedAxisItem for every plot (time and FFT) so the grid
        # can line up all plot areas (22.6).
        axis_items = {"left": AlignedAxisItem("left")}
        if x_axis_datetime:
            axis_items["bottom"] = pg.DateAxisItem()
        super().__init__(parent, axisItems=axis_items)
        self.plot_id = plot_id
        self.x_axis_datetime = x_axis_datetime
        # Independent X axis (detailed_specification.md 24.1): owned/mutated by PlotGridWidget
        # (set_plot_x_linked); a frequency-domain plot is never part of the linked group.
        self.x_linked = x_axis_datetime
        # UI hints, refreshed by PlotGridWidget._refresh_x_link_ui (24.3): whether the menu
        # shows "Link X axis" at all (feature flag) and whether toggling it is allowed now.
        self.x_link_menu_visible = False
        self.x_link_toggle_allowed = False
        self.auto_redraw = auto_redraw
        self.downsample_pixel_ratio = downsample_pixel_ratio
        self._downsample_enabled = True
        # normalize_scroll_bindings(None) already falls back to a default copy, so
        # this is safe whether or not a caller passes one (e.g. direct construction
        # in tests, which most don't bother with).
        self.scroll_bindings = normalize_scroll_bindings(scroll_bindings)

        self._series: dict[str, dict] = {}  # name -> {x, y, curve, color, generation}
        self._seed_x_range: tuple[float, float] | None = None  # consumed by the next first-series install
        self._coarse_series: set[str] = set()  # names currently shown via a pyramid envelope
        self._color_cycle = itertools.cycle(_COLOR_CYCLE)
        self._cursor_mode_enabled = False
        self._cursors: dict[str, dict] = {}  # cursor_id -> {"line": InfiniteLine, "label": TextItem}
        self.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)
        # Hover value readout (detailed_specification.md 39): items are created lazily.
        self._hover_enabled = False
        self._hover_x: float | None = None
        self._hover_line: pg.InfiniteLine | None = None
        self._hover_label: pg.TextItem | None = None
        self._hover_last_move = 0.0
        self._hover_pending = None
        self._hover_throttle = QTimer(self)
        self._hover_throttle.setSingleShot(True)
        self._hover_throttle.timeout.connect(self._flush_hover_move)
        self._hover_settle = QTimer(self)
        self._hover_settle.setSingleShot(True)
        self._hover_settle.setInterval(_HOVER_SETTLE_MS)
        self._hover_settle.timeout.connect(self._settle_hover)
        # One hub per plot on the global pool (detailed_specification.md 37.1); its `pending`
        # dict is the in-flight bookkeeping (`_active_workers`).
        self._hub = WorkerHub(QThreadPool.globalInstance())
        self._last_redraw_x_range: tuple[float, float] | None = None
        self._redraw_debounce = QTimer(self)
        self._redraw_debounce.setSingleShot(True)
        self._redraw_debounce.timeout.connect(self._redraw_all_series)

        self.showGrid(x=True, y=True, alpha=0.3)
        # Custom legend (23.1/23.2): sample click goes through our visible flag, label click selects.
        legend = _SeriesLegend(offset=(30, 30), sampleType=_SeriesSample)
        self.getPlotItem().legend = legend
        legend.setParentItem(self.getViewBox())
        self.setMenuEnabled(False)  # replaced by our own context menu
        self.setAcceptDrops(True)
        # We render only the visible window's data for performance (_redraw_series), which
        # shrinks the curve's own bounding box — leaving pyqtgraph's built-in autoRange on
        # would then re-fit the view to that shrunken box, triggering another (smaller) crop,
        # and so on, converging to a near-empty view. Range changes are only ever explicit
        # from here on: go_home()/add_series() use the series' true full extent, and the user
        # drives everything else via wheel zoom / mouse drag pan.
        self.getViewBox().enableAutoRange(x=False, y=False)
        self.getViewBox().sigRangeChanged.connect(self._on_range_changed)

    # -- drag & drop (overlay a variable from VariablePanel) --------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._is_plot_drag(event):
            event.ignore()  # a pane reorder drag belongs to PlotGridWidget (22.3)
            return
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._is_plot_drag(event):
            event.ignore()
            return
        if event.mimeData().hasText():
            event.acceptProposedAction()

    @staticmethod
    def _is_plot_drag(event) -> bool:
        return event.mimeData().hasFormat(PLOT_MIME)

    def set_x_tick_labels_visible(self, visible: bool) -> None:
        """Show/hide the bottom axis' tick label text, keeping tick marks and the grid
        (detailed_specification.md 22.4). pyqtgraph shrinks the axis height itself when
        showValues is off, so no explicit setHeight is needed."""
        self.getAxis("bottom").setStyle(showValues=bool(visible))

    def x_tick_labels_visible(self) -> bool:
        return bool(self.getAxis("bottom").style["showValues"])

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        name = event.mimeData().text()
        if name:
            self.variableDropped.emit(self, name)
            event.acceptProposedAction()

    # -- series management ---------------------------------------------------

    def add_series(self, name: str, x: np.ndarray, y: np.ndarray, color: str | None = None) -> None:
        """A fully-resident series: x/y are real arrays held for the plot's whole
        lifetime, exactly as before Phase D. See add_windowed_series() for a large
        file's series, whose y data is instead fetched from disk per visible range.
        """
        self._install_series(name, {"x": x, "y": y, "y_source": None, "y_bounds": None}, color)

    def add_windowed_series(
        self,
        name: str,
        x: np.ndarray,
        y_source,
        y_bounds: tuple[float, float] | None,
        color: str | None = None,
    ) -> None:
        """A series whose Y data is too large to keep fully resident
        (detailed_specification.md 13.5.1 Phase D): `x` is still a real, resident
        array (the shared time axis — kept resident even for large files, see that
        section's scope note), but Y is fetched from `y_source.window(row_start,
        row_end)` (core.column.WindowedColumn) for only the currently-visible range,
        re-fetched on every pan/zoom instead of sliced from a resident array.
        `y_bounds` is the column's true (min, max) — from Parquet footer statistics,
        not from any resident data — used by go_home() since there's no resident Y
        array to compute a true extent from.
        """
        self._install_series(name, {"x": x, "y": None, "y_source": y_source, "y_bounds": y_bounds}, color)

    def _install_series(self, name: str, data: dict, color: str | None) -> None:
        if name in self._series:
            self.remove_series(name)
        was_empty = not self._series
        color = color or next(self._color_cycle)
        curve = self.plot([], [], pen=pg.mkPen(color=color, width=1), name=name)
        curve._pa_owner = (self, name)  # lets the legend sample/label find their series
        visible = bool(data.get("visible", True))
        entry = {
            **{k: v for k, v in data.items() if k != "visible"},
            "curve": curve, "color": color, "generation": 0, "visible": visible, "selected": False,
        }
        self._series[name] = entry
        if not visible:
            curve.setVisible(False)
            self._refresh_legend_entry(name)
        x = entry["x"]
        if was_empty and len(x):
            # First series in this plot: show its full extent. (Later overlays keep
            # whatever range the user is currently looking at.) This also triggers
            # sigRangeChanged, mainly so the navigator picks up the initial range.
            x_lo, x_hi = self._finite_bounds_raw(x)
            y_lo, y_hi = self._series_y_bounds(entry)
            if y_lo > y_hi:  # this one series had no finite data at all
                y_lo, y_hi = 0.0, 1.0
            seed = self._seed_x_range
            self._seed_x_range = None
            if x_lo > x_hi and seed is None:
                pass  # no finite time value at all: leave the range alone (29.4)
            elif seed is not None:
                # A series separated out of another plot (23.4): keep the user's current X
                # range instead of resetting every X-linked plot to the full extent.
                pad = 0.02 * (y_hi - y_lo)
                self.getViewBox().setRange(xRange=seed, yRange=(y_lo - pad, y_hi + pad), padding=0)
            else:
                self.getViewBox().setRange(xRange=(x_lo, x_hi), yRange=(y_lo, y_hi), padding=0.02)
        # Always draw explicitly (not just via the sigRangeChanged cascade above):
        # this is the initial display of newly added data, not a pan/zoom-triggered
        # recompute, so it must happen even when auto_redraw is off.
        self._redraw_series(name)  # no-op for a hidden series (23.1)
        self.refresh_cursor_labels()  # an existing cursor's label should now list this series too

    def _series_y_bounds(self, entry: dict) -> tuple[float, float]:
        """(lo, hi) for one series' Y — its true extent, or the (inf, -inf)
        sentinel (not (0.0, 1.0)) if it has none. The sentinel, not the 0.0/1.0
        fallback, matters when a caller (go_home()) combines several series: an
        all-NaN/empty series' own fallback must not pollute the combined range of
        the *other* series (min(0.0, real_lo) would silently widen it to 0.0).
        """
        if entry["y_source"] is not None:
            return entry["y_bounds"] if entry["y_bounds"] is not None else (np.inf, -np.inf)
        return self._finite_bounds_raw(entry["y"])

    @staticmethod
    def _finite_bounds_raw(*arrays: np.ndarray) -> tuple[float, float]:
        """nan/inf-safe (min, max) across one or more arrays, as the (inf, -inf)
        sentinel if nothing finite is present at all (see _finite_bounds for the
        (0.0, 1.0)-fallback wrapper most callers actually want).

        A per-array `where=`-masked reduction rather than the boolean-mask-index +
        concatenate this used to do: the old version copied every finite value out of
        every array and then copied all of those together again, which on `go_home()`
        with several large overlaid series was measured at +3.9GB / 2.4s on the UI
        thread for a 4GB file's worth of series (detailed_specification.md 13.5.1's
        2026-09-19 addendum). `where=`/`initial=` still allocates one bool mask per
        array (1 byte/element) but never a copy of the data itself.
        """
        lo, hi = np.inf, -np.inf
        for a in arrays:
            if len(a) == 0:
                continue
            if hasattr(a, "finite_min_max"):  # TimeIndex: ascending and finite, no data read (36.6 D)
                a_lo, a_hi = a.finite_min_max()
                lo, hi = min(lo, a_lo), max(hi, a_hi)
                continue
            mask = np.isfinite(a)
            if not mask.any():
                continue
            lo = min(lo, float(np.min(a, where=mask, initial=np.inf)))
            hi = max(hi, float(np.max(a, where=mask, initial=-np.inf)))
        return lo, hi

    @classmethod
    def _finite_bounds(cls, *arrays: np.ndarray) -> tuple[float, float]:
        """nan/inf-safe (min, max) across one or more arrays.

        `ViewBox.setRange` raises if given a NaN/Inf range, which plain `.min()/.max()`
        would produce for any column containing NaN (common in real sensor logs) or an
        expression like `sqrt(a)` where `a` goes negative. Falls back to (0.0, 1.0) if
        nothing finite is present at all.
        """
        lo, hi = cls._finite_bounds_raw(*arrays)
        return (lo, hi) if lo <= hi else (0.0, 1.0)

    def remove_series(self, name: str) -> None:
        entry = self._series.pop(name, None)
        if entry is not None:
            self.removeItem(entry["curve"])
            self._set_series_coarse(name, False)
            self.refresh_cursor_labels()  # drop this series from any existing cursor's label
            self.seriesRemoved.emit(self, name)

    def replace_series_y(self, name: str, y: np.ndarray) -> None:
        """Swap a fully-resident series' Y array in place (a derived variable was
        redefined — detailed_specification.md 18.2), keeping its curve, color and
        legend position rather than remove_series()+add_series() re-adding it last.
        """
        entry = self._series.get(name)
        if entry is None or (entry["y_source"] is not None and not isinstance(entry["y_source"], DerivedColumn)):
            return
        entry["y_source"] = None  # a formerly windowed derived series (36.6 C) becomes resident
        entry["y_bounds"] = None
        entry["y"] = y
        self._redraw_series(name)
        self.refresh_cursor_labels()

    def replace_series_source(self, name: str, y_source, y_bounds: tuple[float, float] | None) -> None:
        """Like replace_series_y() but the new data is a windowed source (a DerivedColumn,
        detailed_specification.md 36.6 C): the series becomes windowed whatever it was before."""
        entry = self._series.get(name)
        if entry is None:
            return
        entry["y"] = None
        entry["y_source"] = y_source
        entry["y_bounds"] = y_bounds
        self._set_series_coarse(name, False)
        self._redraw_series(name)
        self.refresh_cursor_labels()

    def transfer_series(self, name: str, target: "TimePlotWidget") -> None:
        """Move a series to another plot (PlotGridWidget's "separate into a new
        plot"), preserving whether it's windowed or fully resident. series_data()'s
        (x, y, color) can't represent a windowed series (no resident y array to
        return), so this moves the internal entry directly rather than going
        through series_data()+add_series()."""
        entry = self._series.pop(name, None)
        if entry is None:
            return
        self.removeItem(entry["curve"])
        was_coarse = name in self._coarse_series
        self._set_series_coarse(name, False)
        self.refresh_cursor_labels()  # drop this series from this plot's cursor labels
        self.seriesRemoved.emit(self, name)
        target._install_series(
            name,
            {
                "x": entry["x"], "y": entry["y"], "y_source": entry["y_source"], "y_bounds": entry["y_bounds"],
                "visible": entry["visible"],  # hidden stays hidden (23.4)
            },
            entry["color"],
        )
        if was_coarse:
            target._set_series_coarse(name, True)

    def _set_series_coarse(self, name: str, is_coarse: bool) -> None:
        """Tracks which series are currently shown via a pyramid envelope rather
        than real per-sample data, for MainWindow's coarse/precise status-bar
        indicator (detailed_specification.md 13.5.1 Phase E). Only emits on an
        empty<->non-empty transition of the set, not on every call.
        """
        was_any = bool(self._coarse_series)
        if is_coarse:
            self._coarse_series.add(name)
        else:
            self._coarse_series.discard(name)
        is_any = bool(self._coarse_series)
        if was_any != is_any:
            self.coarseRenderingChanged.emit(self, is_any)

    def series_names(self) -> list[str]:
        return list(self._series.keys())

    # -- per-series visibility / selection (detailed_specification.md 23.1 / 23.2) --------

    def is_series_visible(self, name: str) -> bool:
        entry = self._series.get(name)
        return bool(entry and entry["visible"])

    def visible_series_names(self) -> list[str]:
        return [n for n, e in self._series.items() if e["visible"]]

    def set_series_visible(self, name: str, visible: bool) -> None:
        """The one place a series' visibility changes: `entry["visible"]` is the source of
        truth, the curve/legend just follow it. A hidden series is skipped by
        _redraw_series (no downsample job); showing it again redraws it for the current range."""
        entry = self._series.get(name)
        visible = bool(visible)
        if entry is None or entry["visible"] == visible:
            return
        log_op("series_visible", plot=self.plot_id, name=name, visible=visible)
        entry["visible"] = visible
        entry["curve"].setVisible(visible)
        self._refresh_legend_entry(name)
        self.refresh_cursor_labels()
        if visible:
            self._redraw_series(name)
        self.seriesVisibilityChanged.emit(self, name, visible)

    def is_series_selected(self, name: str) -> bool:
        entry = self._series.get(name)
        return bool(entry and entry["selected"])

    def set_series_selected(self, name: str, selected: bool) -> None:
        """Selected = thicker curve + bold legend label. Both are derived from
        `entry["selected"]`, so they survive any later redraw."""
        entry = self._series.get(name)
        if entry is None or entry["selected"] == bool(selected):
            return
        entry["selected"] = bool(selected)
        entry["curve"].setPen(self._series_pen(entry))
        self._refresh_legend_entry(name)

    @staticmethod
    def _series_pen(entry: dict):
        width = _SELECTED_PEN_WIDTH if entry["selected"] else _NORMAL_PEN_WIDTH
        return pg.mkPen(color=entry["color"], width=width)

    def _refresh_legend_entry(self, name: str) -> None:
        """Legend label style from the entry's state: grey when hidden, bold when selected;
        and repaint the sample (pyqtgraph draws an eye icon for a hidden curve)."""
        entry = self._series.get(name)
        legend = self.getPlotItem().legend
        if entry is None or legend is None:
            return
        for sample, label in legend.items:
            if sample.item is entry["curve"]:
                color = None if entry["visible"] else _HIDDEN_LABEL_COLOR
                label.setText(name, color=color, bold=bool(entry["selected"]))
                sample.update()
                legend.updateSize()
                return

    def series_data(self, name: str) -> tuple[np.ndarray, np.ndarray | None, str]:
        """The series' resident backing arrays — (x, y, color) — not what's
        currently drawn (see _redraw_series). For a windowed series
        (add_windowed_series(), Phase D) there is no resident y array by design;
        y is None in that case. Callers that need actual Y values for a series
        that might be windowed should use MainWindow.variable_window() instead.
        """
        entry = self._series[name]
        return entry["x"], entry["y"], entry["color"]

    def series_source(self, name: str):
        """(y_source, y_bounds) for a windowed series (add_windowed_series()), or
        None for a fully-resident one (add_series()) — lets a caller (the
        navigator overview fallback) get at the underlying source without
        depending on MainWindow's variable bookkeeping still knowing this name
        (a plotted series stays valid even after e.g. a time-axis switch renders
        its name no longer a "known variable" — see _refresh_navigator_overview).
        """
        entry = self._series[name]
        if entry["y_source"] is None:
            return None
        return entry["y_source"], entry["y_bounds"]

    def export_snapshot(self):
        """Pure-data copy of what is drawn now, for image export (detailed_specification.md 19.1)."""
        from .image_export import PlotSnapshot, SeriesSnapshot

        series = []
        for name, entry in self._series.items():
            if not entry["visible"]:
                continue  # hidden series are not exported (23.1)
            x, y = entry["curve"].getData()
            x = np.array([] if x is None else x, dtype=float)
            y = np.array([] if y is None else y, dtype=float)
            series.append(SeriesSnapshot(name, str(entry["color"]), x, y))
        x_range, y_range = self.getViewBox().viewRange()
        cursors = [c["line"].value() for c in self._cursors.values() if c["line"].isVisible()]
        return PlotSnapshot(
            self.x_axis_datetime, tuple(x_range), tuple(y_range), series, cursors, x_linked=self.x_linked
        )

    def is_empty(self) -> bool:
        return not self._series

    def clear_cursors(self) -> None:
        for cursor_id in list(self._cursors):
            self.remove_cursor(cursor_id)

    def set_x_data(self, x: np.ndarray) -> None:
        """Repoint every series in this plot at a new shared X array (e.g. the user
        switched which parquet column is used as the time axis) without touching
        their Y data. The new X may have a completely different scale/extent than
        the old one, so the view is refit rather than left at the old zoom/pan.
        """
        # Any existing cursor's X value refers to a position on the *old* time
        # column -- meaningless (and likely off the new column's own scale
        # entirely) once the axis itself changes, so drop them rather than leave
        # stale/nonsensical cursors sitting at the old numeric position.
        self.clear_cursors()
        self.set_hover_x(None)
        for entry in self._series.values():
            entry["x"] = x
        if self._series:
            self.go_home()

    def pending_downsample_count(self) -> int:
        """Number of in-flight background downsample jobs (for tests/debugging)."""
        return len(self._hub.pending)

    @property
    def _active_workers(self) -> dict:
        return self._hub.pending

    # -- cursors -----------------------------------------------------------------

    def set_cursor_mode_enabled(self, enabled: bool) -> None:
        """While enabled, a plain (non-drag) click on empty plot area places a new
        cursor (cursorAddRequested), and every existing cursor is shown (and
        draggable/removable again). While disabled, existing cursors are hidden
        (not removed -- their position/data is kept, so turning the mode back on
        restores them) and mouse clicks/drags on the plot behave as if there were
        no cursors at all.
        """
        self._cursor_mode_enabled = enabled
        for entry in self._cursors.values():
            entry["line"].setMovable(enabled)
            entry["line"].setVisible(enabled)
            entry["label"].setVisible(enabled)

    def _on_scene_mouse_clicked(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if ev.isAccepted():
            return  # a legend sample/label (or a cursor line) already handled it
        if not self._cursor_mode_enabled:
            # Empty-area click clears the series selection (23.2). Not in cursor mode,
            # where the same click places a cursor instead and must leave selection alone.
            self.backgroundClicked.emit(self)
            return
        # A click that landed on one of our own cursor lines is handled by that
        # line's own sigClicked (-> _on_cursor_line_clicked, removal) instead; only a
        # background click (on empty area or on a data curve) places a new cursor.
        current_item = getattr(ev, "currentItem", None)
        if any(current_item in (entry["line"], entry["label"]) for entry in self._cursors.values()):
            return
        view_pos = self.getViewBox().mapSceneToView(ev.scenePos())
        log_op("cursor_add_requested", plot=self.plot_id, x=view_pos.x())
        self.cursorAddRequested.emit(self, view_pos.x())

    def add_cursor(self, cursor_id: str, x: float) -> None:
        """Place (or, if `cursor_id` already exists, reposition) a cursor at `x`:
        a draggable vertical line plus a text overlay giving that X value and every
        current series' value at the nearest sample to it (specification.md's
        cursor feature, added 2026-09-19, user-requested).
        """
        if cursor_id in self._cursors:
            self.set_cursor_x(cursor_id, x)
            return
        line = pg.InfiniteLine(
            pos=x,
            angle=90,
            movable=self._cursor_mode_enabled,
            pen=pg.mkPen(color=_CURSOR_COLOR, width=1, style=Qt.PenStyle.DashLine),
        )
        label = pg.TextItem(anchor=(0, 1), color=_CURSOR_COLOR)
        line.setVisible(self._cursor_mode_enabled)
        label.setVisible(self._cursor_mode_enabled)
        self.addItem(line)
        self.addItem(label)
        self._cursors[cursor_id] = {"line": line, "label": label}
        line.sigClicked.connect(lambda _line, _ev, cid=cursor_id: self._on_cursor_line_clicked(cid))
        line.sigPositionChanged.connect(lambda _line, cid=cursor_id: self._on_cursor_line_moved(cid))
        self._update_cursor_label(cursor_id)

    def remove_cursor(self, cursor_id: str) -> None:
        entry = self._cursors.pop(cursor_id, None)
        if entry is None:
            return
        self.removeItem(entry["line"])
        self.removeItem(entry["label"])

    def set_cursor_x(self, cursor_id: str, x: float) -> None:
        """Reposition an existing cursor without re-emitting cursorMoveRequested --
        used to apply a move that originated on another (X-linked) plot, so the two
        plots don't bounce the same move back and forth at each other.
        """
        entry = self._cursors.get(cursor_id)
        if entry is None or entry["line"].value() == x:
            return
        entry["line"].blockSignals(True)
        entry["line"].setValue(x)
        entry["line"].blockSignals(False)
        self._update_cursor_label(cursor_id)

    def cursor_ids(self) -> list[str]:
        return list(self._cursors)

    def _on_cursor_line_clicked(self, cursor_id: str) -> None:
        if not self._cursor_mode_enabled:
            return
        log_op("cursor_remove_requested", plot=self.plot_id, cursor=cursor_id)
        self.cursorRemoveRequested.emit(self, cursor_id)

    def _on_cursor_line_moved(self, cursor_id: str) -> None:
        entry = self._cursors.get(cursor_id)
        if entry is None:
            return
        x = entry["line"].value()
        self._update_cursor_label(cursor_id)
        self.cursorMoveRequested.emit(self, cursor_id, x)

    def format_x(self, x: float) -> str:
        """An X value as shown in cursor labels / the image-export stamp."""
        if self.x_axis_datetime:
            try:
                return datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except (OverflowError, OSError, ValueError):
                return f"{x:.6g}"
        return f"{x:.6g}"

    @staticmethod
    def format_value(value: float) -> str:
        """A series value as shown in cursor labels and the hover readout (39.2)."""
        return f"{value:.6g}"

    def _nearest_row(self, x_array: np.ndarray, x: float) -> int | None:
        if len(x_array) == 0:
            return None
        idx = int(np.searchsorted(x_array, x))
        if idx <= 0:
            return 0
        if idx >= len(x_array):
            return len(x_array) - 1
        return idx - 1 if (x - x_array[idx - 1]) <= (x_array[idx] - x) else idx

    def _series_value_at(self, entry: dict, x: float) -> float | None:
        row = self._nearest_row(entry["x"], x)
        if row is None:
            return None
        if entry["y_source"] is not None:
            # A single-row window read (Phase D): cheap enough (a handful of bytes)
            # to do synchronously here rather than round-tripping through a
            # WorkerHub job like a real visible-range fetch does.
            values = entry["y_source"].window(row, row + 1)
            return float(values[0]) if len(values) else None
        return float(entry["y"][row])

    def _update_cursor_label(self, cursor_id: str) -> None:
        entry = self._cursors.get(cursor_id)
        if entry is None:
            return
        x = entry["line"].value()
        lines = [self.format_x(x)]
        for name, series_entry in self._series.items():
            if not series_entry["visible"]:
                continue  # hidden series get no value line (23.1)
            value = self._series_value_at(series_entry, x)
            if value is not None:
                lines.append(f"{name}: {self.format_value(value)}")
        entry["label"].setText("\n".join(lines))
        self._reposition_cursor_overlays()

    def refresh_cursor_labels(self) -> None:
        """Recompute every cursor's displayed value(s) -- call after the set of
        series in this plot changes (a series was added/removed), since a cursor's
        label lists every currently-plotted series' value at that X.
        """
        for cursor_id in list(self._cursors):
            self._update_cursor_label(cursor_id)

    def _reposition_cursor_overlays(self) -> None:
        """Keep every cursor label pinned near the top of the current view, without
        recomputing its displayed values -- called on every view-range change (a
        drag-pan fires this continuously), where a real recompute per frame would
        mean a synchronous disk read per WindowedColumn series per frame. Values are
        only recomputed on cursor add/move or a series-list change
        (_update_cursor_label / refresh_cursor_labels).
        """
        if not self._cursors:
            return
        x_lo, x_hi = self.getViewBox().viewRange()[0]
        y_hi = self.getViewBox().viewRange()[1][1]
        for entry in self._cursors.values():
            x = entry["line"].value()
            entry["label"].setPos(x, y_hi)
            entry["label"].setAnchor((0, 1) if x < x_lo + (x_hi - x_lo) * 0.85 else (1, 1))


    # -- hover value readout (detailed_specification.md 39) ---------------------------

    def set_hover_enabled(self, enabled: bool) -> None:
        """Toggle (39.3). Off: no line, no label, and mouseMoveEvent returns before doing
        any work."""
        enabled = bool(enabled)
        if enabled == self._hover_enabled:
            return
        self._hover_enabled = enabled
        if not enabled:
            self._hover_pending = None
            self._hover_throttle.stop()
            self.set_hover_x(None)

    def hover_enabled(self) -> bool:
        return self._hover_enabled

    def hover_x(self) -> float | None:
        """X of the visible hover line, or None."""
        return self._hover_x

    def hover_text(self) -> str:
        """Plain text of the readout ('' while hidden) -- for tests / debugging."""
        if self._hover_x is None or self._hover_label is None:
            return ""
        return self._hover_label.toPlainText()

    def _ensure_hover_items(self) -> None:
        if self._hover_line is not None:
            return
        line = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(color=_HOVER_COLOR, width=1, style=Qt.PenStyle.DotLine),
        )
        line.setZValue(50)
        line.setVisible(False)
        self.addItem(line, ignoreBounds=True)  # never part of the data bounds / autorange
        # Child of the ViewBox itself (like the legend): pixel coordinates, pinned top-right.
        label = pg.TextItem(anchor=(1, 0), color="#000000", fill=pg.mkBrush(255, 255, 255, 200))
        label.setParentItem(self.getViewBox())
        label.setZValue(60)
        label.setVisible(False)
        self._hover_line, self._hover_label = line, label
        self.getViewBox().sigResized.connect(self._position_hover_label)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 (Qt override)
        # Not scene().sigMouseMoved: pyqtgraph drops moves closer together than its
        # `mouseRateLimit` (so the final resting position could be lost); this view gets every
        # move and applies its own ~60 Hz throttle, with a trailing flush.
        super().mouseMoveEvent(ev)
        if self._hover_enabled:
            self._on_scene_mouse_moved(self.mapToScene(ev.position().toPoint()))

    def _on_scene_mouse_moved(self, scene_pos) -> None:
        """Throttled (~60 Hz) hover update for a mouse position in scene coordinates."""
        self._hover_pending = scene_pos
        now = time.monotonic()
        wait_ms = _HOVER_THROTTLE_MS - (now - self._hover_last_move) * 1000.0
        if wait_ms <= 0:
            self._hover_throttle.stop()
            self._flush_hover_move()
        elif not self._hover_throttle.isActive():
            self._hover_throttle.start(int(wait_ms) + 1)

    def _flush_hover_move(self) -> None:
        pos, self._hover_pending = self._hover_pending, None
        if pos is None or not self._hover_enabled:
            return
        self._hover_last_move = time.monotonic()
        vb = self.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            self._hover_leave()  # over an axis / outside the plot area
            return
        x = vb.mapSceneToView(pos).x()
        if not np.isfinite(x):
            return
        self.set_hover_x(x)
        self.hoverXChanged.emit(self, x)

    def leaveEvent(self, ev) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(ev)
        self._hover_leave()

    def _hover_leave(self) -> None:
        self._hover_pending = None
        self._hover_throttle.stop()
        if self._hover_x is not None:
            self.set_hover_x(None)
            self.hoverXChanged.emit(self, None)

    def set_hover_x(self, x: float | None) -> None:
        """Show the hover line + readout at time `x` (None hides). Called for the plot under
        the mouse and, via PlotGridWidget, for every X-linked plot (39.1). Never raises on a
        non-finite X or a non-ascending / NaN-containing time axis (data 29)."""
        if not self._hover_enabled or x is None or not np.isfinite(x):
            self._hover_settle.stop()
            self._hover_x = None
            if self._hover_line is not None:
                self._hover_line.setVisible(False)
                self._hover_label.setVisible(False)
            return
        self._ensure_hover_items()
        self._hover_x = float(x)
        self._hover_line.setValue(self._hover_x)
        self._hover_line.setVisible(True)
        self._render_hover(settled=False)
        self._hover_settle.start()  # restarted by every move

    def _settle_hover(self) -> None:
        if self._hover_x is not None:
            self._render_hover(settled=True)

    def _approx_value(self, entry: dict, x: float) -> float | None:
        """Nearest point of the curve as drawn (downsampled) -- no disk read (39.2)."""
        cx, cy = entry["curve"].getData()
        if cx is None or cy is None or len(cx) == 0 or len(cx) != len(cy):
            return None
        row = self._nearest_row(np.asarray(cx), x)
        return None if row is None else float(cy[row])

    def _render_hover(self, settled: bool) -> None:
        x = self._hover_x
        header = self.format_x(x) + ("" if self.x_axis_datetime else " Hz")
        rows = [f'<span style="color:#000">{html.escape(header)}</span>']
        for name, entry in self._series.items():
            if not entry["visible"]:
                continue  # hidden series get no line (23.1)
            approx = False
            try:
                lazy_axis = hasattr(entry["x"], "finite_min_max")  # TimeIndex: a lookup can read the file
                if settled or (entry["y_source"] is None and not lazy_axis):
                    value = self._series_value_at(entry, x)  # real sample (resident: no I/O)
                else:
                    value, approx = self._approx_value(entry, x), True
            except Exception:  # a failed single-row read must not break mouse handling
                value, approx = None, False
            if value is None:
                text = "\u2014"
            else:
                text = ("\u2248" if approx else "") + self.format_value(value)
            row = f'<span style="color:{entry["color"]}">{html.escape(name)}</span>: {text}'
            rows.append(f"<b>{row}</b>" if entry["selected"] else row)
        self._hover_label.setHtml("<br>".join(rows))
        self._position_hover_label()

    def _position_hover_label(self) -> None:
        label = self._hover_label
        if label is None or self._hover_x is None:
            return
        vb = self.getViewBox()
        label.setPos(vb.width() - 6, 6)
        label.setVisible(True)
        # Don't sit on a placed cursor's label (16): move down just below it.
        rect = label.sceneBoundingRect()
        for entry in self._cursors.values():
            other = entry["label"]
            if other.isVisible() and rect.intersects(other.sceneBoundingRect()):
                label.setPos(vb.width() - 6, 6 + other.sceneBoundingRect().bottom() - rect.top() + 4)
                break

    # -- zoom / pan ------------------------------------------------------------

    def set_scroll_bindings(self, bindings: dict[str, str]) -> None:
        self.scroll_bindings = normalize_scroll_bindings(bindings)

    def wheelEvent(self, ev):  # noqa: N802 (Qt override)
        """Which action (time zoom / amp zoom / pan) each modifier performs is
        configurable — see self.scroll_bindings, always a full {"none", "ctrl",
        "shift"} -> action mapping (normalize_scroll_bindings() guarantees this even
        if a caller passes something partial/corrupt).

        Two cross-platform quirks handled here regardless of binding:
        - On macOS, Qt swaps Cmd/Ctrl so the *physical* Ctrl key arrives as
          Qt.KeyboardModifier.MetaModifier, not ControlModifier — treat either as "Ctrl".
        - Some trackpads report a Shift+vertical-scroll gesture as a horizontal wheel
          event (delta lands in angleDelta().x() instead of .y()) — use whichever is non-zero.
        """
        modifiers = ev.modifiers()
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        # Precedence when both are held at once (unusual, but Qt doesn't prevent it):
        # shift wins over ctrl, matching the original fixed scheme's own precedence.
        modifier_key = "shift" if shift else "ctrl" if ctrl else "none"
        action = self.scroll_bindings.get(modifier_key, DEFAULT_SCROLL_BINDINGS[modifier_key])
        angle_delta = ev.angleDelta()
        delta = angle_delta.y() or angle_delta.x()
        vb = self.getViewBox()

        if action == "pan":
            x_min, x_max = vb.viewRange()[0]
            shift_amount = -delta / 120.0 * (x_max - x_min) * _PAN_STEP_FRACTION
            vb.setXRange(x_min + shift_amount, x_max + shift_amount, padding=0)
            ev.accept()
            return

        scale = 0.999**delta
        scene_pos = self.mapToScene(ev.position().toPoint())
        center = vb.mapSceneToView(scene_pos)
        vb.scaleBy((1.0, scale) if action == "y_zoom" else (scale, 1.0), center=center)
        ev.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        move_up_action = menu.addAction(tr("plot.move_up"))
        move_down_action = menu.addAction(tr("plot.move_down"))
        menu.addSeparator()
        # Per-series submenus (detailed_specification.md 23.4); disabled with no series.
        names = self.series_names()
        delete_menu = menu.addMenu(tr("plot.delete_series"))
        visible_menu = menu.addMenu(tr("plot.toggle_series_visible"))
        separate_series_menu = menu.addMenu(tr("plot.separate_series"))
        delete_actions: dict = {}
        visible_actions: dict = {}
        separate_series_actions: dict = {}
        for n in names:
            delete_actions[delete_menu.addAction(n)] = n
            a = visible_menu.addAction(n)
            a.setCheckable(True)
            a.setChecked(self.is_series_visible(n))
            visible_actions[a] = n
            separate_series_actions[separate_series_menu.addAction(n)] = n
        for m in (delete_menu, visible_menu):
            m.setEnabled(bool(names))
        separate_series_menu.setEnabled(len(names) > 1)  # separating the only series is a no-op
        menu.addSeparator()
        link_x_action = None
        if self.x_axis_datetime and self.x_link_menu_visible:  # feature-flagged (24.3)
            link_x_action = menu.addAction(tr("plot.link_x"))
            link_x_action.setCheckable(True)
            link_x_action.setChecked(self.x_linked)
            link_x_action.setEnabled(self.x_link_toggle_allowed)  # the last linked plot can't be unlinked
        stats_range_action = None
        if self.x_axis_datetime and not self.x_linked:
            stats_range_action = menu.addAction(tr("plot.stats_this_range"))
        if link_x_action is not None or stats_range_action is not None:
            menu.addSeparator()
        remove_action = menu.addAction(tr("plot.remove"))
        separate_action = menu.addAction(tr("plot.separate"))
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == remove_action:
            log_op("remove_plot_requested", plot=self.plot_id)
            self.removeRequested.emit(self)
        elif chosen == separate_action:
            log_op("separate_plot_requested", plot=self.plot_id)
            self.separateRequested.emit(self)
        elif chosen == move_up_action:
            log_op("move_plot_up_requested", plot=self.plot_id)
            self.moveUpRequested.emit(self)
        elif chosen == move_down_action:
            log_op("move_plot_down_requested", plot=self.plot_id)
            self.moveDownRequested.emit(self)
        elif link_x_action is not None and chosen == link_x_action:
            self.linkXRequested.emit(self, not self.x_linked)
        elif stats_range_action is not None and chosen == stats_range_action:
            log_op("stats_this_range_requested", plot=self.plot_id)
            self.statsForRangeRequested.emit(self)
        elif chosen in delete_actions:
            log_op("remove_series_requested", plot=self.plot_id, name=delete_actions[chosen])
            self.removeSeriesRequested.emit(self, delete_actions[chosen])
        elif chosen in visible_actions:
            n = visible_actions[chosen]
            self.set_series_visible(n, not self.is_series_visible(n))
        elif chosen in separate_series_actions:
            log_op("separate_series_requested", plot=self.plot_id, name=separate_series_actions[chosen])
            self.separateSeriesRequested.emit(self, separate_series_actions[chosen])

    def go_home(self) -> None:
        """H button/key: reset to full data range (specification.md 5.3).

        Computed from each series' true underlying data, not pyqtgraph's
        autoRange() — the displayed curve only ever holds the cropped visible
        window (see _redraw_series), so autoRange() would just re-fit to
        whatever was last cropped instead of the real full extent.
        """
        log_op("home", plot=self.plot_id)
        if not self._series:
            return
        x_lo, x_hi = self._finite_bounds_raw(*(entry["x"] for entry in self._series.values()))
        if x_lo > x_hi:
            return  # no finite time value at all: leave the range alone (29.4)
        # Y from the visible series only (23.1) -- all hidden falls back to all, so the
        # range is never empty.
        y_entries = [e for e in self._series.values() if e["visible"]] or list(self._series.values())
        y_bounds = [self._series_y_bounds(entry) for entry in y_entries]
        y_lo = min(b[0] for b in y_bounds)
        y_hi = max(b[1] for b in y_bounds)
        if y_lo > y_hi:  # no series had any finite data at all
            y_lo, y_hi = 0.0, 1.0
        self.getViewBox().setRange(xRange=(x_lo, x_hi), yRange=(y_lo, y_hi), padding=0.02)

    def redraw(self) -> None:
        """R button/key: force a downsample recompute for the current range."""
        log_op("redraw", plot=self.plot_id)
        for name in list(self._series):
            self._redraw_series(name)

    def set_downsample_enabled(self, enabled: bool) -> None:
        self._downsample_enabled = enabled
        self.redraw()

    # -- internal ----------------------------------------------------------------

    def _on_range_changed(self, *_args) -> None:
        x_min, x_max = self.getViewBox().viewRange()[0]
        log_op("range_changed", plot=self.plot_id, x_min=x_min, x_max=x_max)
        self._reposition_cursor_overlays()
        # Kept immediate (not debounced below): the navigator's selection-region sync
        # and X-axis linking between plots must track the view as it moves, not lag
        # behind a redraw debounce.
        self.rangeChanged.emit(self, (x_min, x_max))
        if not self.auto_redraw:
            return  # deferred until the user presses R (settings dialog: 自動再描画)
        x_range = (x_min, x_max)
        if x_range == self._last_redraw_x_range:
            # Only Y (amp) changed — e.g. Ctrl+scroll. _redraw_series crops on X alone,
            # so recomputing here would just reproduce the same curve at real cost
            # (detailed_specification.md 13.2: recompute used to fire on every Y-only change).
            return
        self._last_redraw_x_range = x_range
        # Debounced (not called directly): a mouse-drag pan or scroll fires sigRangeChanged
        # many times in quick succession, each of which would otherwise queue a background
        # downsample job per series — restarting this timer coalesces a burst into one redraw.
        self._redraw_debounce.start(_REDRAW_DEBOUNCE_MS)

    def _redraw_all_series(self) -> None:
        for name in list(self._series):
            self._redraw_series(name)

    def _redraw_series(self, name: str) -> None:
        entry = self._series.get(name)
        if entry is None or not entry["visible"]:
            return  # hidden: no recompute until shown again (23.1)
        x = entry["x"]
        if len(x) == 0:
            return

        x_min, x_max = self.getViewBox().viewRange()[0]
        try:
            lo = max(0, np.searchsorted(x, x_min, side="left") - 1)
            hi = min(len(x), np.searchsorted(x, x_max, side="right") + 1)
        except Exception as e:  # noqa: BLE001
            if not hasattr(x, "finite_min_max"):
                raise
            # A lazy TimeIndex axis (36.6 D) reads a few rows from the file to locate the range;
            # a file that vanished/broke must be a reported render failure, not an exception on
            # the UI thread (28.2, data 29.5).
            self.renderFailed.emit(self, name, f"{type(e).__name__}: {e}")
            return
        # A lazy TimeIndex axis (36.6 D) is read inside the worker job, never on the UI thread;
        # a resident array is sliced here as before (a free view).
        lazy_x = hasattr(x, "finite_min_max")
        x_visible = None if lazy_x else x[lo:hi]
        x_lazy = x if lazy_x else None  # jobs capture the axis only when lazy (a job must not pin a resident one)

        pixel_width = max(int(self.width()), 1)
        n_out = pixel_width * self.downsample_pixel_ratio
        downsample_enabled = self._downsample_enabled

        if entry["y_source"] is not None:
            # Windowed series (Phase D, detailed_specification.md 13.5.1): Y isn't a
            # resident array to slice, so even the "few points, draw as-is" case must
            # still fetch off the UI thread. Fetch and (if needed) downsample run as
            # one background job, so a partially-fetched window is never drawn.
            y_source = entry["y_source"]
            visible_rows = hi - lo

            if downsample_enabled and visible_rows > _PYRAMID_PREFERRED_ROW_THRESHOLD:
                # A real fetch this wide costs close to what materializing the
                # whole column always cost — prefer the pyramid's coarse envelope
                # once it exists (Phase D's pyramid-wiring follow-up). Skipped
                # entirely when downsampling is off: the user explicitly asked
                # for full raw precision, which a pyramid can't offer.
                pyramid = y_source.pyramid
                if pyramid is not None:
                    self._render_from_pyramid(name, entry, pyramid, lo, hi, n_out)
                    return
                self._maybe_start_pyramid_build(name, y_source)
                if isinstance(y_source, DerivedColumn) and not y_source.pyramid_given_up:
                    # 36.6 C: a computed column this wide would be computed in full just to be
                    # thinned; the (chunked) pyramid build just started/running -- by this plot or
                    # by the navigator -- redraws us when it completes (or fails).
                    y_source.add_pyramid_listener(
                        (id(self), name), lambda ok, n=name, src=y_source: self._on_waited_pyramid(n, src, ok)
                    )
                    return
                if features.is_enabled("big_a_thin_first_draw") and hasattr(y_source, "thin_job"):
                    # 36.6 A: draw ~n_out strided points now instead of reading the whole
                    # visible range; the pyramid replaces this when built.
                    self._start_thin_draw(name, entry, y_source, lo, hi, n_out)
                    return
                # No pyramid yet: fall through to the real (expensive but
                # correct) fetch below for this redraw; the background build
                # just kicked off will make the next one at this zoom level cheap.

            def job(y_source=y_source, lo=lo, hi=hi, x_visible=x_visible, x_lazy=x_lazy):
                if x_visible is None:
                    x_visible = x_lazy[lo:hi]
                y_visible = y_source.window(lo, hi)
                if not downsample_enabled or len(x_visible) <= n_out:
                    return x_visible, y_visible
                return lttb(x_visible, y_visible, n_out)

            self._start_series_worker(name, entry, job)
            return

        y_visible = entry["y"][lo:hi]
        if x_visible is None:  # lazy axis with a resident y (e.g. a derived variable)
            def lazy_job(x_lazy=x_lazy, y_visible=y_visible, lo=lo, hi=hi):
                xv = x_lazy[lo:hi]
                if not downsample_enabled or len(xv) <= n_out:
                    return xv, y_visible
                return lttb(xv, y_visible, n_out)

            self._start_series_worker(name, entry, lazy_job)
            return
        if not downsample_enabled or len(x_visible) <= n_out:
            self._set_curve_data(entry, x_visible, y_visible)
            return
        self._start_series_worker(name, entry, lambda: lttb(x_visible, y_visible, n_out))

    def _start_thin_draw(self, name: str, entry: dict, y_source, lo: int, hi: int, n_out: int) -> None:
        """First draw of a big windowed column without a pyramid (36.6 A): a strided read over
        rows [lo, hi) in the background; the result is marked coarse. With a resident time axis
        X is looked up on the UI thread (a free fancy index, so the job closure holds no array,
        37.3); with a lazy TimeIndex axis the job reads the time values at the same stride grid
        itself (36.8: no per-point reads on the UI thread)."""
        entry["generation"] += 1
        generation = entry["generation"]
        self.downsampleStarted.emit()
        job = y_source.thin_job(lo, hi, n_out)
        x = entry["x"]
        if hasattr(x, "finite_min_max"):
            job = self._with_lazy_x(job, x, lo, hi, n_out)
        self._hub.submit(
            job,
            lambda result, n=name, g=generation: self._on_thin_drawn(n, g, *result),
            lambda msg, n=name, g=generation: self._on_series_worker_failed(n, g, msg),
            job="downsample",
            target=f"{self.plot_id}/{name}",
        )

    @staticmethod
    def _with_lazy_x(job, x, lo: int, hi: int, n_out: int):
        """Wrap a thin job so it also returns the X values of its rows: the time column is
        strided over the same grid by `thin_column` (one pass over the overlapping row groups);
        should the grids ever differ the rows are looked up one by one -- still in the worker."""
        from ..core.pyramid import thin_column

        path, column = x.path, x.column

        def with_x():
            idx, y = job()
            idx = np.clip(idx, 0, len(x) - 1)
            tidx, tx = thin_column(path, column, n_out, lo, hi)
            if not np.array_equal(tidx, idx):
                tx = x.take(idx)
            return idx, y, tx

        return with_x

    def _on_thin_drawn(self, name: str, generation: int, idx: np.ndarray, y: np.ndarray, x_values=None) -> None:
        self.downsampleFinished.emit()
        entry = self._series.get(name)
        if entry is None or generation != entry["generation"]:
            return  # removed, or superseded (e.g. by the pyramid render)
        if x_values is None:  # resident axis: a plain fancy index, no I/O
            x = entry["x"]
            x_values = x[np.clip(idx, 0, len(x) - 1)]
        self._set_curve_data(entry, x_values, y)  # drops non-finite X (29.4)
        self._set_series_coarse(name, True)

    @staticmethod
    def _set_curve_data(entry: dict, x: np.ndarray, y: np.ndarray) -> None:
        """curve.setData without non-finite X points: pyqtgraph/Qt can hang painting a
        path with NaN/Inf X coordinates (detailed_specification.md 29.4)."""
        x = np.asarray(x)
        if x.dtype.kind == "f":
            ok = np.isfinite(x)
            if not ok.all():
                x, y = x[ok], np.asarray(y)[ok]
        entry["curve"].setData(x, y)

    def _start_series_worker(self, name: str, entry: dict, job) -> None:
        # Generation is tracked per series (not per plot): with 2+ overlaid series,
        # redrawing all of them in one pass (e.g. from _on_range_changed) used to bump
        # a single plot-wide counter once per series, so every series but the last
        # processed always looked "stale" by the time its own result came back and got
        # silently discarded. Each series now has its own counter — reused as-is for
        # the windowed-fetch job below, not just LTTB recompute.
        entry["generation"] += 1
        generation = entry["generation"]
        self.downsampleStarted.emit()  # drives the main-window progress indicator
        self._hub.submit(
            job,
            lambda result, n=name, g=generation: self._on_downsampled(n, g, *result),
            lambda msg, n=name, g=generation: self._on_series_worker_failed(n, g, msg),
            job="downsample",
            target=f"{self.plot_id}/{name}",
        )

    def _on_downsampled(self, name: str, generation: int, x_ds: np.ndarray, y_ds: np.ndarray) -> None:
        self.downsampleFinished.emit()
        entry = self._series.get(name)
        if entry is None:
            return  # series was removed while this job was in flight
        if generation != entry["generation"]:
            return  # stale result superseded by a newer zoom/pan for *this* series
        self._set_curve_data(entry, x_ds, y_ds)
        self._set_series_coarse(name, False)  # a real/precise result, whichever path produced it

    def _on_series_worker_failed(self, name: str, generation: int, message: str) -> None:
        """A downsample/fetch job raised (detailed_specification.md 28.2): release
        the job and balance downsampleStarted so the busy indicator clears;
        the series keeps its previous curve."""
        self.downsampleFinished.emit()
        entry = self._series.get(name)
        if entry is None or generation != entry["generation"]:
            return  # series removed, or superseded by a newer job: nothing to report
        self.renderFailed.emit(self, name, message)

    def _render_from_pyramid(self, name: str, entry: dict, pyramid, lo: int, hi: int, n_out: int) -> None:
        """Coarse, synchronous redraw from an already-built pyramid (core.pyramid,
        detailed_specification.md 13.5.1 Phase D's pyramid-wiring follow-up): no
        disk I/O here, so unlike a real windowed fetch this doesn't need a
        background worker. Still bumps the generation counter, so a slower,
        still-in-flight real-fetch result from an earlier (more zoomed-in) redraw
        of this same series is correctly discarded as stale if it lands after.
        """
        entry["generation"] += 1
        self._set_series_coarse(name, True)
        x = entry["x"]
        row_starts = pyramid.row_starts
        start_idx = max(0, int(np.searchsorted(row_starts, lo, side="right")) - 1)
        end_idx = int(np.searchsorted(row_starts, hi, side="left"))
        if end_idx <= start_idx:
            entry["curve"].setData([], [])
            return
        bucket_starts = np.clip(row_starts[start_idx:end_idx], 0, len(x) - 1)
        mins = pyramid.mins[start_idx:end_idx]
        maxs = pyramid.maxs[start_idx:end_idx]
        t = x[bucket_starts]
        # Interleave (t, min) then (t, max) per bucket: a cheap envelope showing
        # each bucket's real spread (same technique as MainWindow's navigator
        # overview fallback for a windowed column, _windowed_navigator_overview).
        xs = np.repeat(t, 2)
        ys = np.empty(len(t) * 2, dtype=np.float64)
        ys[0::2] = mins
        ys[1::2] = maxs
        finite = np.isfinite(ys)
        xs, ys = xs[finite], ys[finite]
        if len(xs) > n_out:
            xs, ys = lttb(xs, ys, n_out)
        self._set_curve_data(entry, xs, ys)

    def _maybe_start_pyramid_build(self, name: str, y_source) -> None:
        job = y_source.request_pyramid()
        if job is None:
            return  # already built, or a build is already in flight
        self.downsampleStarted.emit()  # drives the main-window progress indicator
        # Shares the same pool as real fetches/LTTB — a dedicated pool for this job was
        # tried and measured not to help; see the comment on _PYRAMID_PREFERRED_ROW_THRESHOLD.
        self._hub.submit(
            job,
            lambda pyramid, n=name, src=y_source: self._on_pyramid_ready(n, src, pyramid),
            lambda msg, src=y_source: self._on_pyramid_failed(src, msg),
            job="pyramid",
            target=f"{self.plot_id}/{name}",
        )

    def _on_pyramid_failed(self, y_source, message: str) -> None:
        """Envelope build raised (detailed_specification.md 28.2): clear the
        in-flight mark (retried on a later redraw, up to the column's failure
        limit) and balance the busy indicator. A series waiting for this build
        (a DerivedColumn) is told by pyramid_failed() -> _on_waited_pyramid."""
        y_source.pyramid_failed()
        self.downsampleFinished.emit()

    def _on_waited_pyramid(self, name: str, y_source, ok: bool) -> None:
        """A pyramid build this series was waiting for (36.6 C) completed, started by this plot,
        another plot or the navigator. Success: redraw from it. Failure: report it (the series
        must not stay silently blank), and once the column gave up pyramids redraw anyway --
        that now takes the real-fetch path."""
        entry = self._series.get(name)
        if entry is None or entry["y_source"] is not y_source:
            return
        if ok:
            self._redraw_series(name)
            return
        self.renderFailed.emit(self, name, tr("render.pyramid_failed"))
        if y_source.pyramid_given_up:
            self._redraw_series(name)

    def _on_pyramid_ready(self, name: str, y_source, pyramid) -> None:
        self.downsampleFinished.emit()
        y_source.set_pyramid(pyramid)
        entry = self._series.get(name)
        if entry is None or entry["y_source"] is not y_source:
            return  # series removed, or now shows a different source, while this was building
        self._redraw_series(name)  # re-check: the pyramid may now satisfy the current view
