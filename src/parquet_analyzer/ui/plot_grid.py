from __future__ import annotations

import itertools

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..core.oplog import log_op
from ..io.settings import DEFAULT_SCROLL_BINDINGS
from .plot_widget import TimePlotWidget


class PlotGridWidget(QWidget):
    """Vertically stacked multi-plot area (specification.md 5.1, detailed_specification.md 5章).

    Plots share their X range with each other and with the navigator.
    """

    rangeChanged = Signal(tuple)  # (x_min, x_max), from whichever plot the user interacted with
    plotAdded = Signal(object)  # TimePlotWidget
    busyChanged = Signal(bool)  # True while >=1 background downsample job is in flight
    layoutChanged = Signal()  # plots added/removed/reordered, or a plot's series changed
    coarseRenderingChanged = Signal(bool)  # True while >=1 plot shows >=1 series as a pyramid envelope

    def __init__(
        self,
        auto_redraw: bool = True,
        downsample_pixel_ratio: int = 2,
        scroll_bindings: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.auto_redraw = auto_redraw
        self.downsample_pixel_ratio = downsample_pixel_ratio
        self.scroll_bindings = dict(scroll_bindings) if scroll_bindings else dict(DEFAULT_SCROLL_BINDINGS)
        self._plots: list[TimePlotWidget] = []
        self._plot_id_counter = itertools.count(1)
        self._active_job_count = 0
        self._coarse_plots: set[TimePlotWidget] = set()
        self._cursor_mode_enabled = False
        self._cursor_id_counter = itertools.count(1)
        # Source of truth for which cursors exist and where, keyed by domain
        # (x_axis_datetime) so a newly added plot only gets seeded with cursors from
        # its own domain (a time-domain X position means nothing on an FFT plot).
        self._cursor_positions: dict[str, tuple[bool, float]] = {}  # cursor_id -> (domain, x)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self.add_plot()

    def add_plot(self, x_axis_datetime: bool = True) -> TimePlotWidget:
        plot = TimePlotWidget(
            f"p{next(self._plot_id_counter)}",
            self.auto_redraw,
            self.downsample_pixel_ratio,
            x_axis_datetime,
            self.scroll_bindings,
        )
        plot.removeRequested.connect(self._on_remove_requested)
        plot.separateRequested.connect(self._on_separate_requested)
        plot.moveUpRequested.connect(self.move_plot_up)
        plot.moveDownRequested.connect(self.move_plot_down)
        if x_axis_datetime:
            # A frequency-domain (FFT) plot's X range is Hz, not time — forwarding it
            # here would corrupt the navigator's time-range selection.
            plot.rangeChanged.connect(self._on_plot_range_changed)
        plot.downsampleStarted.connect(self._on_job_started)
        plot.downsampleFinished.connect(self._on_job_finished)
        plot.coarseRenderingChanged.connect(self._on_plot_coarse_changed)
        plot.cursorAddRequested.connect(self._on_cursor_add_requested)
        plot.cursorMoveRequested.connect(self._on_cursor_move_requested)
        plot.cursorRemoveRequested.connect(self._on_cursor_remove_requested)
        plot.set_cursor_mode_enabled(self._cursor_mode_enabled)
        for cursor_id, (domain, x) in self._cursor_positions.items():
            if domain == x_axis_datetime:
                plot.add_cursor(cursor_id, x)
        if x_axis_datetime:
            # Only link plots that share the same X domain (time). A frequency-domain
            # plot (FFT, x_axis_datetime=False) must not be linked to a time axis.
            time_plots = [p for p in self._plots if p.x_axis_datetime]
            if time_plots:
                plot.setXLink(time_plots[0])
        self._layout.addWidget(plot)
        self._plots.append(plot)
        log_op("add_plot", plot=plot.plot_id, total=len(self._plots))
        self.plotAdded.emit(plot)
        return plot

    def remove_plot(self, plot: TimePlotWidget) -> None:
        if len(self._plots) <= 1:
            return  # always keep at least one plot
        if plot.x_axis_datetime and sum(1 for p in self._plots if p.x_axis_datetime) <= 1:
            # Navigator overview, set_x_range, and load_view's plot-reuse logic all
            # assume at least one time-domain plot exists — losing the last one would
            # leave only frequency-domain (FFT) plots, which they aren't written to
            # handle (specification.md 5.1/5.2).
            return
        self._plots.remove(plot)
        self._layout.removeWidget(plot)
        plot.hide()
        self._on_plot_coarse_changed(plot, False)  # drop it from the aggregate regardless
        # New time-domain plots are always X-linked to the *first* time-domain plot
        # (see add_plot) — a star topology. If the removed plot was that anchor,
        # re-link the survivors to the new first time plot, rather than leaving them
        # each still pointing at a soon-to-be-dead ViewBox (whose weakref silently goes
        # stale, desyncing every other plot from each other).
        self._relink_anchor()
        log_op("remove_plot", plot=plot.plot_id, total=len(self._plots))
        # If a downsample job for this plot is still in flight, its `finished` signal
        # (queued, cross-thread) could still be delivered after deleteLater() actually
        # destroys the C++ widget, crashing when the slot touches the dead object (the
        # same class of bug fixed for BackgroundWorker itself — see its __init__).
        # Deleting only once every job has actually finished sidesteps it entirely.
        if plot.pending_downsample_count() == 0:
            plot.deleteLater()
        else:
            plot.downsampleFinished.connect(lambda: self._delete_if_idle(plot))
        self.layoutChanged.emit()

    def move_plot_up(self, plot: TimePlotWidget) -> None:
        self._move_plot(plot, -1)

    def move_plot_down(self, plot: TimePlotWidget) -> None:
        self._move_plot(plot, 1)

    def _move_plot(self, plot: TimePlotWidget, direction: int) -> None:
        idx = self._plots.index(plot)
        new_idx = idx + direction
        if not (0 <= new_idx < len(self._plots)):
            return  # already at that end of the stack
        if plot.x_axis_datetime != self._plots[new_idx].x_axis_datetime:
            # Refuse to move a plot across the time-domain/frequency-domain (FFT)
            # boundary. Several places (navigator overview, set_x_range, load_view's
            # plot-reuse logic) assume plots[0] is time-domain; keeping time-domain
            # plots contiguous at the front and FFT plots contiguous after them is
            # what makes that assumption safe to rely on instead of an invariant that
            # reordering could silently break.
            return
        self._plots[idx], self._plots[new_idx] = self._plots[new_idx], self._plots[idx]
        self._layout.removeWidget(plot)
        self._layout.insertWidget(new_idx, plot)
        self._relink_anchor()  # plots[0] may now be a different widget
        log_op("move_plot", plot=plot.plot_id, new_index=new_idx)
        self.layoutChanged.emit()

    def anchor_plot(self) -> TimePlotWidget | None:
        """The time-domain plot every other time-domain plot is X-linked to
        (specification.md 5.1) — conventionally plots[0], but looked up explicitly
        (rather than assumed) since a frequency-domain (FFT) plot must never be
        treated as the anchor even if something regresses the "time-domain plots stay
        contiguous at the front" invariant _move_plot/remove_plot otherwise maintain.
        """
        return next((p for p in self._plots if p.x_axis_datetime), None)

    def _relink_anchor(self) -> None:
        """X-link every time-domain plot to the *current* anchor. Needed after
        removing or reordering plots — the anchor's identity can change, and
        pyqtgraph's setXLink() points at a specific ViewBox, not a floating "the
        anchor" role, so it doesn't follow along on its own.
        """
        anchor = self.anchor_plot()
        if anchor is None:
            return
        # The newly-promoted anchor may itself still carry a stale outbound link from
        # when *it* wasn't the anchor (setXLink() only clears the link on the view you
        # call it on, not on views already pointing at that view) — clear it before
        # relinking everyone else to it.
        anchor.setXLink(None)
        for p in self._plots:
            if p.x_axis_datetime and p is not anchor:
                p.setXLink(anchor)

    def _delete_if_idle(self, plot: TimePlotWidget) -> None:
        if plot.pending_downsample_count() == 0:
            plot.deleteLater()

    @property
    def plots(self) -> list[TimePlotWidget]:
        return list(self._plots)

    def go_home(self) -> None:
        for plot in self._plots:
            plot.go_home()

    def redraw(self) -> None:
        for plot in self._plots:
            plot.redraw()

    def set_auto_redraw(self, enabled: bool) -> None:
        self.auto_redraw = enabled
        for plot in self._plots:
            plot.auto_redraw = enabled

    def set_downsample_pixel_ratio(self, ratio: int) -> None:
        self.downsample_pixel_ratio = ratio
        for plot in self._plots:
            plot.downsample_pixel_ratio = ratio
        self.redraw()

    def set_scroll_bindings(self, bindings: dict[str, str]) -> None:
        self.scroll_bindings = dict(bindings)
        for plot in self._plots:
            plot.set_scroll_bindings(bindings)

    def set_downsample_enabled(self, enabled: bool) -> None:
        for plot in self._plots:
            plot.set_downsample_enabled(enabled)

    def set_x_range(self, x_min: float, x_max: float) -> None:
        anchor = self.anchor_plot()
        if anchor is not None:
            anchor.setXRange(x_min, x_max, padding=0)

    def set_time_axis_data(self, x: np.ndarray) -> None:
        """Repoint every time-domain plot's series at a new X array (the user changed
        which column is used as the time axis). Frequency-domain (FFT) plots are left
        alone — their X axis is Hz, not time.
        """
        # Time-domain cursors' X values referred to the *old* time column; each
        # plot.set_x_data() below already drops its own, so forget them here too
        # (otherwise a plot added afterward would be seeded with stale positions).
        self._cursor_positions = {cid: v for cid, v in self._cursor_positions.items() if not v[0]}
        for plot in self._plots:
            if plot.x_axis_datetime:
                plot.set_x_data(x)

    def set_cursor_mode_enabled(self, enabled: bool) -> None:
        self._cursor_mode_enabled = enabled
        for plot in self._plots:
            plot.set_cursor_mode_enabled(enabled)

    def clear_cursors(self) -> None:
        """Drop every cursor -- e.g. opening a new file makes any existing cursor's
        X position meaningless (it referred to a position on the previous file's
        time column)."""
        self._cursor_positions = {}
        for plot in self._plots:
            plot.clear_cursors()

    def add_series_to_plot(
        self, plot: TimePlotWidget, name: str, x: np.ndarray, y: np.ndarray, color: str | None = None
    ) -> None:
        plot.add_series(name, x, y, color)

    # -- internal --------------------------------------------------------------

    def _on_remove_requested(self, plot: TimePlotWidget) -> None:
        self.remove_plot(plot)

    def _on_separate_requested(self, plot: TimePlotWidget) -> None:
        if len(plot.series_names()) <= 1:
            return  # nothing to separate out
        new_plot = self.add_plot(x_axis_datetime=plot.x_axis_datetime)
        # Move all but the first series to the new plot. transfer_series(), not
        # series_data()+add_series(): a windowed series (Phase D) has no resident y
        # array for series_data() to hand back, so the internal entry (whichever
        # kind it is) must move directly instead.
        for name in plot.series_names()[1:]:
            plot.transfer_series(name, new_plot)

    def _on_plot_range_changed(self, source: TimePlotWidget, x_range: tuple[float, float]) -> None:
        self.rangeChanged.emit(x_range)

    def _on_cursor_add_requested(self, source: TimePlotWidget, x: float) -> None:
        cursor_id = f"c{next(self._cursor_id_counter)}"
        self._cursor_positions[cursor_id] = (source.x_axis_datetime, x)
        for plot in self._plots:
            if plot.x_axis_datetime == source.x_axis_datetime:
                plot.add_cursor(cursor_id, x)
        self.layoutChanged.emit()

    def _on_cursor_move_requested(self, source: TimePlotWidget, cursor_id: str, x: float) -> None:
        self._cursor_positions[cursor_id] = (source.x_axis_datetime, x)
        for plot in self._plots:
            if plot is not source and plot.x_axis_datetime == source.x_axis_datetime:
                plot.set_cursor_x(cursor_id, x)

    def _on_cursor_remove_requested(self, source: TimePlotWidget, cursor_id: str) -> None:
        self._cursor_positions.pop(cursor_id, None)
        for plot in self._plots:
            if plot.x_axis_datetime == source.x_axis_datetime:
                plot.remove_cursor(cursor_id)
        self.layoutChanged.emit()

    def _on_job_started(self) -> None:
        self._active_job_count += 1
        if self._active_job_count == 1:
            self.busyChanged.emit(True)

    def _on_job_finished(self) -> None:
        self._active_job_count = max(0, self._active_job_count - 1)
        if self._active_job_count == 0:
            self.busyChanged.emit(False)

    def _on_plot_coarse_changed(self, plot: TimePlotWidget, is_coarse: bool) -> None:
        was_any = bool(self._coarse_plots)
        if is_coarse:
            self._coarse_plots.add(plot)
        else:
            self._coarse_plots.discard(plot)
        is_any = bool(self._coarse_plots)
        if was_any != is_any:
            self.coarseRenderingChanged.emit(is_any)
