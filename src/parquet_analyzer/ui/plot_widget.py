from __future__ import annotations

import itertools
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThreadPool, Qt, QTimer, Signal
from PySide6.QtWidgets import QMenu, QWidget

from ..core.background_worker import BackgroundWorker
from ..core.downsample import lttb
from ..core.oplog import log_op
from ..io.settings import DEFAULT_SCROLL_BINDINGS, normalize_scroll_bindings
from .i18n import tr

_COLOR_CYCLE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

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
    coarseRenderingChanged = Signal(object, bool)  # self, True while >=1 series shows a pyramid envelope
    cursorAddRequested = Signal(object, float)  # self, x -- background click while cursor mode is on
    cursorMoveRequested = Signal(object, str, float)  # self, cursor_id, new x -- dragged in this plot
    cursorRemoveRequested = Signal(object, str)  # self, cursor_id -- clicked (not dragged) in this plot

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
        axis_items = {"bottom": pg.DateAxisItem()} if x_axis_datetime else None
        super().__init__(parent, axisItems=axis_items)
        self.plot_id = plot_id
        self.x_axis_datetime = x_axis_datetime
        self.auto_redraw = auto_redraw
        self.downsample_pixel_ratio = downsample_pixel_ratio
        self._downsample_enabled = True
        # normalize_scroll_bindings(None) already falls back to a default copy, so
        # this is safe whether or not a caller passes one (e.g. direct construction
        # in tests, which most don't bother with).
        self.scroll_bindings = normalize_scroll_bindings(scroll_bindings)

        self._series: dict[str, dict] = {}  # name -> {x, y, curve, color, generation}
        self._coarse_series: set[str] = set()  # names currently shown via a pyramid envelope
        self._color_cycle = itertools.cycle(_COLOR_CYCLE)
        self._cursor_mode_enabled = False
        self._cursors: dict[str, dict] = {}  # cursor_id -> {"line": InfiniteLine, "label": TextItem}
        self.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)
        self._thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[BackgroundWorker] = set()  # keep alive until `finished` is handled
        self._last_redraw_x_range: tuple[float, float] | None = None
        self._redraw_debounce = QTimer(self)
        self._redraw_debounce.setSingleShot(True)
        self._redraw_debounce.timeout.connect(self._redraw_all_series)

        self.showGrid(x=True, y=True, alpha=0.3)
        self.addLegend()
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
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasText():
            event.acceptProposedAction()

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
        entry = {**data, "curve": curve, "color": color, "generation": 0}
        self._series[name] = entry
        x = entry["x"]
        if was_empty and len(x):
            # First series in this plot: show its full extent. (Later overlays keep
            # whatever range the user is currently looking at.) This also triggers
            # sigRangeChanged, mainly so the navigator picks up the initial range.
            x_lo, x_hi = self._finite_bounds(x)
            y_lo, y_hi = self._series_y_bounds(entry)
            if y_lo > y_hi:  # this one series had no finite data at all
                y_lo, y_hi = 0.0, 1.0
            self.getViewBox().setRange(xRange=(x_lo, x_hi), yRange=(y_lo, y_hi), padding=0.02)
        # Always draw explicitly (not just via the sigRangeChanged cascade above):
        # this is the initial display of newly added data, not a pan/zoom-triggered
        # recompute, so it must happen even when auto_redraw is off.
        self._redraw_series(name)
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
        target._install_series(
            name, {"x": entry["x"], "y": entry["y"], "y_source": entry["y_source"], "y_bounds": entry["y_bounds"]},
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
        for entry in self._series.values():
            entry["x"] = x
        if self._series:
            self.go_home()

    def pending_downsample_count(self) -> int:
        """Number of in-flight background downsample jobs (for tests/debugging)."""
        return len(self._active_workers)

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
        if not self._cursor_mode_enabled or ev.button() != Qt.MouseButton.LeftButton:
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

    def _format_cursor_x(self, x: float) -> str:
        if self.x_axis_datetime:
            try:
                return datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except (OverflowError, OSError, ValueError):
                return f"{x:.6g}"
        return f"{x:.6g}"

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
            # BackgroundWorker like a real visible-range fetch does.
            values = entry["y_source"].window(row, row + 1)
            return float(values[0]) if len(values) else None
        return float(entry["y"][row])

    def _update_cursor_label(self, cursor_id: str) -> None:
        entry = self._cursors.get(cursor_id)
        if entry is None:
            return
        x = entry["line"].value()
        lines = [self._format_cursor_x(x)]
        for name, series_entry in self._series.items():
            value = self._series_value_at(series_entry, x)
            if value is not None:
                lines.append(f"{name}: {value:.6g}")
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
        remove_action = menu.addAction(tr("plot.remove"))
        separate_action = menu.addAction(tr("plot.separate"))
        chosen = menu.exec(event.globalPos())
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
        x_lo, x_hi = self._finite_bounds(*(entry["x"] for entry in self._series.values()))
        y_bounds = [self._series_y_bounds(entry) for entry in self._series.values()]
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
        if entry is None:
            return
        x = entry["x"]
        if len(x) == 0:
            return

        x_min, x_max = self.getViewBox().viewRange()[0]
        lo = max(0, np.searchsorted(x, x_min, side="left") - 1)
        hi = min(len(x), np.searchsorted(x, x_max, side="right") + 1)
        x_visible = x[lo:hi]

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
                # No pyramid yet: fall through to the real (expensive but
                # correct) fetch below for this redraw; the background build
                # just kicked off will make the next one at this zoom level cheap.

            def job(y_source=y_source, lo=lo, hi=hi, x_visible=x_visible):
                y_visible = y_source.window(lo, hi)
                if not downsample_enabled or len(x_visible) <= n_out:
                    return x_visible, y_visible
                return lttb(x_visible, y_visible, n_out)

            self._start_series_worker(name, entry, job)
            return

        y_visible = entry["y"][lo:hi]
        if not downsample_enabled or len(x_visible) <= n_out:
            entry["curve"].setData(x_visible, y_visible)
            return
        self._start_series_worker(name, entry, lambda: lttb(x_visible, y_visible, n_out))

    def _start_series_worker(self, name: str, entry: dict, job) -> None:
        # Generation is tracked per series (not per plot): with 2+ overlaid series,
        # redrawing all of them in one pass (e.g. from _on_range_changed) used to bump
        # a single plot-wide counter once per series, so every series but the last
        # processed always looked "stale" by the time its own result came back and got
        # silently discarded. Each series now has its own counter — reused as-is for
        # the windowed-fetch job below, not just LTTB recompute.
        entry["generation"] += 1
        generation = entry["generation"]
        worker = BackgroundWorker(job)
        self._active_workers.add(worker)  # see BackgroundWorker for why
        worker.signals.finished.connect(
            lambda result, n=name, g=generation, w=worker: self._on_downsampled(n, g, *result, w)
        )
        self.downsampleStarted.emit()  # drives the main-window progress indicator
        self._thread_pool.start(worker)

    def _on_downsampled(
        self, name: str, generation: int, x_ds: np.ndarray, y_ds: np.ndarray, worker: BackgroundWorker
    ) -> None:
        self._active_workers.discard(worker)
        self.downsampleFinished.emit()
        entry = self._series.get(name)
        if entry is None:
            return  # series was removed while this job was in flight
        if generation != entry["generation"]:
            return  # stale result superseded by a newer zoom/pan for *this* series
        entry["curve"].setData(x_ds, y_ds)
        self._set_series_coarse(name, False)  # a real/precise result, whichever path produced it

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
        entry["curve"].setData(xs, ys)

    def _maybe_start_pyramid_build(self, name: str, y_source) -> None:
        job = y_source.request_pyramid()
        if job is None:
            return  # already built, or a build is already in flight
        worker = BackgroundWorker(job)
        self._active_workers.add(worker)  # see BackgroundWorker for why
        worker.signals.finished.connect(
            lambda pyramid, n=name, src=y_source, w=worker: self._on_pyramid_ready(n, src, pyramid, w)
        )
        self.downsampleStarted.emit()  # drives the main-window progress indicator
        # Shares the same pool as real fetches/LTTB (self._thread_pool) — a
        # dedicated pool for this job was tried and measured not to help; see the
        # comment on _PYRAMID_PREFERRED_ROW_THRESHOLD above.
        self._thread_pool.start(worker)

    def _on_pyramid_ready(self, name: str, y_source, pyramid, worker: BackgroundWorker) -> None:
        self._active_workers.discard(worker)
        self.downsampleFinished.emit()
        y_source.set_pyramid(pyramid)
        entry = self._series.get(name)
        if entry is None or entry["y_source"] is not y_source:
            return  # series removed, or now shows a different source, while this was building
        self._redraw_series(name)  # re-check: the pyramid may now satisfy the current view
