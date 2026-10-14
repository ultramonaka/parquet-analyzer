from __future__ import annotations

import itertools

import numpy as np
from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import features
from ..core.oplog import log_op
from ..io.settings import DEFAULT_SCROLL_BINDINGS
from .i18n import tr
from .plot_widget import PLOT_MIME, TimePlotWidget

MIN_PANE_HEIGHT = 60
HANDLE_WIDTH = 14
HANDLE_TOP_RESERVED = 24  # room for the chain-link toggle (24.3)


class ChainButton(QToolButton):
    """Checkable chain-link toggle for the X-axis link state (detailed_specification.md
    24.3): checked = linked (closed chain), unchecked = independent (broken chain). Drawn by
    hand, so it needs no icon resource and follows the palette."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setCursor(Qt.ArrowCursor)
        self.setFixedSize(HANDLE_WIDTH, 20)
        self.setToolTip(tr("plot.link_x_tooltip"))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        enabled = self.isEnabled()
        # Hover / pressed feedback (paintEvent is fully overridden, so draw it here).
        if enabled and (self.isDown() or self.underMouse()):
            bg = QColor(pal.highlight().color())
            bg.setAlpha(110 if self.isDown() else 60)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        on = self.isChecked()
        color = pal.mid().color() if not enabled else (pal.windowText().color() if on else pal.highlight().color())
        pen = QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(-45)  # two pill-shaped links on a diagonal, like the usual chain glyph
        # Pills are 7x4 px. Linked: they overlap (interlocked). Independent: pulled apart,
        # with a perpendicular break line between them.
        off = 2.2 if on else 5.2
        for sign in (-1, 1):
            p.drawRoundedRect(QRectF(sign * off - 3.5, -2, 7, 4), 2, 2)
        if not on:
            p.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(0, -4.5, 0, 4.5)


class PlotHandle(QWidget):
    """Thin grip strip at a pane's left edge; dragging it reorders the plot
    (detailed_specification.md 22.1/22.3). Its reserved top area holds the X-link chain
    toggle (24.3)."""

    linkToggled = Signal(bool)  # user clicked the chain: desired linked state

    def __init__(self, plot_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.plot_id = plot_id
        self.setFixedWidth(HANDLE_WIDTH)
        self.setCursor(Qt.OpenHandCursor)
        self._press_pos: QPoint | None = None
        self.x_linked = True  # only affects the background tint (24.3)
        self.chain = ChainButton(self)
        self.chain.move(0, 2)
        self.chain.clicked.connect(self.linkToggled)
        self.chain.hide()  # shown only when the independent_x_axis flag is on (24.3)

    def set_link_state(self, linked: bool, available: bool, toggle_allowed: bool) -> None:
        """Sync the chain button and tint to the plot's link state. `available`: show the
        button at all (feature flag, time-domain plot); `toggle_allowed`: clickable."""
        self.x_linked = linked
        self.chain.setVisible(available)
        self.chain.blockSignals(True)  # a programmatic sync is not a user click
        self.chain.setChecked(linked)
        self.chain.blockSignals(False)
        self.chain.setEnabled(toggle_allowed)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        if not self.x_linked:  # unlinked panes get a subtly different handle background (24.3)
            tint = QColor(self.palette().highlight().color())
            tint.setAlpha(55)
            p.fillRect(self.rect(), tint)
        c = self.palette().mid().color()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        mid_y = HANDLE_TOP_RESERVED + (self.height() - HANDLE_TOP_RESERVED) // 2
        for dy in range(-12, 13, 6):  # 2 columns x 5 rows of grip dots
            for x in (4, 9):
                p.drawEllipse(QPoint(x, mid_y + dy), 1, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_pos = None
        mime = QMimeData()
        mime.setData(PLOT_MIME, self.plot_id.encode("utf-8"))  # no text: see PLOT_MIME
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.setCursor(Qt.ClosedHandCursor)
        try:
            drag.exec(Qt.MoveAction)
        finally:
            self.setCursor(Qt.OpenHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._press_pos = None


class PlotPane(QWidget):
    """One splitter child: [PlotHandle | TimePlotWidget] (detailed_specification.md 22.1)."""

    def __init__(self, plot: TimePlotWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.plot = plot
        self.handle = PlotHandle(plot.plot_id, self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.handle)
        lay.addWidget(plot, 1)
        self.setMinimumHeight(MIN_PANE_HEIGHT)


class PlotGridWidget(QWidget):
    """Vertically stacked multi-plot area (specification.md 5.1, detailed_specification.md 5章).

    Plots share their X range with each other and with the navigator.
    """

    rangeChanged = Signal(tuple)  # (x_min, x_max), from whichever plot the user interacted with
    plotAdded = Signal(object)  # TimePlotWidget
    renderFailed = Signal(object, str, str)  # plot, series name, message (28.2); forwarded from each plot
    busyChanged = Signal(bool)  # True while >=1 background downsample job is in flight
    layoutChanged = Signal()  # plots added/removed/reordered, or a plot's series changed
    coarseRenderingChanged = Signal(bool)  # True while >=1 plot shows >=1 series as a pyramid envelope
    selectionChanged = Signal(object)  # (plot, name) of the selected series, or None (23.2)
    statsForRangeRequested = Signal(object)  # plot -- its context menu "Stats for this range" (24.2)
    cursorsChanged = Signal(list)  # time-domain cursor X positions (empty while cursor mode is off); 25.2

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
        self._downsample_enabled = True  # owned here so new plots inherit it (plots 50.1)
        self._hover_enabled = True  # detailed_specification.md 39.3; MainWindow applies the saved setting
        # The one selected series across the whole grid (detailed_specification.md 23.2).
        self._selected: tuple[TimePlotWidget, str] | None = None
        self._cursor_id_counter = itertools.count(1)
        # Source of truth for which cursors exist and where, keyed by domain
        # (x_axis_datetime) so a newly added plot only gets seeded with cursors from
        # its own domain (a time-domain X position means nothing on an FFT plot).
        self._cursor_positions: dict[str, tuple[bool, float]] = {}  # cursor_id -> (domain, x)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._splitter = QSplitter(Qt.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        self._layout.addWidget(self._splitter)
        self._panes: dict[TimePlotWidget, PlotPane] = {}
        # Sizes set while the splitter has no height yet would be computed off 0 and lose
        # their ratios, so they wait for the first resize/show (22.2).
        self._pending_ratios: list[float] | None = None
        self._pending_equalize = False
        # Plot-reorder drag (22.3): a thin line overlaid on the splitter marks the drop slot.
        self._drop_line = QFrame(self)
        self._drop_line.setFixedHeight(2)
        self._drop_line.setStyleSheet("background-color: palette(highlight);")
        self._drop_line.hide()
        self.setAcceptDrops(True)

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
        plot.linkXRequested.connect(self._on_link_x_requested)
        plot.statsForRangeRequested.connect(self.statsForRangeRequested)
        plot.separateRequested.connect(self._on_separate_requested)
        plot.moveUpRequested.connect(self.move_plot_up)
        plot.moveDownRequested.connect(self.move_plot_down)
        plot.seriesLabelClicked.connect(self.select_series)
        plot.seriesRemoved.connect(self._on_series_removed)
        plot.seriesChanged.connect(self._on_series_changed)
        plot.seriesVisibilityChanged.connect(self._on_series_visibility_changed)
        plot.removeSeriesRequested.connect(self._on_remove_series_requested)
        plot.separateSeriesRequested.connect(self._on_separate_series_requested)
        plot.backgroundClicked.connect(lambda _plot: self.clear_selection())
        if x_axis_datetime:
            # A frequency-domain (FFT) plot's X range is Hz, not time — forwarding it
            # here would corrupt the navigator's time-range selection.
            plot.rangeChanged.connect(self._on_plot_range_changed)
        plot.downsampleStarted.connect(self._on_job_started)
        plot.downsampleFinished.connect(self._on_job_finished)
        plot.renderFailed.connect(self.renderFailed)
        plot.coarseRenderingChanged.connect(self._on_plot_coarse_changed)
        plot.cursorAddRequested.connect(self._on_cursor_add_requested)
        plot.hoverXChanged.connect(self._on_hover_x_changed)
        plot.set_hover_enabled(self._hover_enabled)
        plot.set_downsample_enabled(self._downsample_enabled)  # (plots 50.1)
        plot.cursorMoveRequested.connect(self._on_cursor_move_requested)
        plot.cursorRemoveRequested.connect(self._on_cursor_remove_requested)
        plot.set_cursor_mode_enabled(self._cursor_mode_enabled)
        for cursor_id, (domain, x) in self._cursor_positions.items():
            if domain == x_axis_datetime:
                plot.add_cursor(cursor_id, x)
        if x_axis_datetime:
            # Only link plots that share the same X domain (time). A frequency-domain
            # plot (FFT, x_axis_datetime=False) must not be linked to a time axis.
            anchor = self.anchor_plot()
            if anchor is not None:
                plot.setXLink(anchor)
        plot.getAxis("left").naturalWidthChanged.connect(self._align_y_axes)
        pane = PlotPane(plot)
        pane.handle.linkToggled.connect(lambda linked, p=plot: self._on_link_x_requested(p, linked))
        self._panes[plot] = pane
        # Time-domain plots stay in front of FFT plots (overview 2章): a new time plot
        # goes before the first FFT pane, not at the end.
        index = len(self._plots)
        if x_axis_datetime:
            index = next((i for i, p in enumerate(self._plots) if not p.x_axis_datetime), index)
        self._splitter.insertWidget(index, pane)
        self._plots.insert(index, plot)
        self._equalize()
        self._update_x_tick_labels()
        self._refresh_x_link_ui()
        self._align_y_axes()
        log_op("add_plot", plot=plot.plot_id, total=len(self._plots))
        self.plotAdded.emit(plot)
        return plot

    def _align_y_axes(self) -> None:
        """Give every plot's left axis the widest natural width (22.6)."""
        if not self._plots:
            return
        widest = max(p.getAxis("left").natural_width() for p in self._plots)
        for p in self._plots:
            p.getAxis("left").set_aligned_width(widest)

    def remove_plot(self, plot: TimePlotWidget) -> None:
        if plot not in self._plots:
            return  # already removed (e.g. a second queued "remove" request for the same pane)
        if len(self._plots) <= 1:
            return  # always keep at least one plot
        if plot.x_axis_datetime and sum(1 for p in self._plots if p.x_axis_datetime) <= 1:
            # Navigator overview, set_x_range, and load_view's plot-reuse logic all
            # assume at least one time-domain plot exists — losing the last one would
            # leave only frequency-domain (FFT) plots, which they aren't written to
            # handle (specification.md 5.1/5.2).
            return
        if self._selected is not None and self._selected[0] is plot:
            self.clear_selection()
        self._plots.remove(plot)
        if plot.x_axis_datetime and not self.linked_plots():
            # The removed plot was the last linked one but independent time plots remain:
            # keep the "at least one linked time plot" invariant (24.1) by re-linking the
            # first of them (it keeps its own range; the group is just that one plot).
            next(p for p in self._plots if p.x_axis_datetime).x_linked = True
        pane = self._panes.pop(plot)
        # Out of the splitter right away (a hidden child would still occupy a slot in
        # splitter.sizes()); the pane object is kept alive below until deletion is safe.
        pane.hide()
        pane.setParent(None)
        self._on_plot_coarse_changed(plot, False)  # drop it from the aggregate regardless
        # New time-domain plots are always X-linked to the *first* time-domain plot
        # (see add_plot) — a star topology. If the removed plot was that anchor,
        # re-link the survivors to the new first time plot, rather than leaving them
        # each still pointing at a soon-to-be-dead ViewBox (whose weakref silently goes
        # stale, desyncing every other plot from each other).
        self._relink_anchor()
        self._equalize()
        self._update_x_tick_labels()
        self._refresh_x_link_ui()
        self._align_y_axes()
        log_op("remove_plot", plot=plot.plot_id, total=len(self._plots))
        # If a downsample job for this plot is still in flight, its `finished` signal
        # (queued, cross-thread) could still be delivered after deleteLater() actually
        # destroys the C++ widget, crashing when the slot touches the dead object (the
        # same class of bug fixed for WorkerHub — see data ch.37).
        # Deleting only once every job has actually finished sidesteps it entirely.
        # (Applies to the whole pane, which owns the plot.)
        if plot.pending_downsample_count() == 0:
            pane.deleteLater()
        else:
            plot.downsampleFinished.connect(lambda: self._delete_if_idle(plot, pane))
        self.layoutChanged.emit()

    def move_plot_up(self, plot: TimePlotWidget) -> None:
        self._move_plot(plot, -1)

    def move_plot_down(self, plot: TimePlotWidget) -> None:
        self._move_plot(plot, 1)

    def _move_plot(self, plot: TimePlotWidget, direction: int) -> None:
        self.move_plot_to(plot, self._plots.index(plot) + direction)

    def _move_allowed(self, plot: TimePlotWidget, new_index: int) -> bool:
        """Whether moving `plot` to final index `new_index` is a real, legal move. Legal =
        the time-domain plots stay contiguous at the front and FFT plots after them.
        Several places (navigator overview, set_x_range, load_view's plot-reuse logic)
        assume plots[0] is time-domain; keeping that is what makes the assumption safe
        instead of an invariant reordering could silently break."""
        if plot not in self._plots:
            return False
        idx = self._plots.index(plot)
        if not (0 <= new_index < len(self._plots)) or new_index == idx:
            return False
        order = list(self._plots)
        order.insert(new_index, order.pop(idx))
        flags = [p.x_axis_datetime for p in order]
        return flags == sorted(flags, reverse=True)

    def move_plot_to(self, plot: TimePlotWidget, new_index: int, via: str | None = None) -> None:
        """Move `plot` to final index `new_index`, keeping every pane's height
        (detailed_specification.md 22.1). `via="drag"` for a handle drag."""
        if not self._move_allowed(plot, new_index):
            return
        idx = self._plots.index(plot)
        sizes = self._splitter.sizes()
        self._plots.insert(new_index, self._plots.pop(idx))
        sizes.insert(new_index, sizes.pop(idx))
        if self._pending_ratios is not None and len(self._pending_ratios) == len(sizes):
            self._pending_ratios.insert(new_index, self._pending_ratios.pop(idx))
        self._splitter.insertWidget(new_index, self._panes[plot])
        if self._sizing_ready() and sum(sizes) > 0:
            self._splitter.setSizes(sizes)
        self._relink_anchor()  # plots[0] may now be a different widget
        self._update_x_tick_labels()
        self._refresh_x_link_ui()
        extra = {"via": via} if via else {}
        log_op("move_plot", plot=plot.plot_id, new_index=new_index, **extra)
        self.layoutChanged.emit()

    # -- heights (22.1 / 22.2) ---------------------------------------------------

    def _sizing_ready(self) -> bool:
        """False until the splitter has been shown and laid out; an unshown widget has a
        meaningless default height that setSizes() would split (and then lose)."""
        return self._splitter.isVisible() and self._splitter.height() > 0

    def _equalize(self) -> None:
        n = len(self._plots)
        total = self._splitter.height()
        self._pending_ratios = None
        if not self._sizing_ready():
            self._pending_equalize = True
            return
        self._pending_equalize = False
        self._splitter.setSizes([total // n] * n)

    def plot_height_ratios(self) -> list[float]:
        n = len(self._plots)
        sizes = self._splitter.sizes()
        total = sum(sizes)
        if not self._sizing_ready() or total <= 0 or len(sizes) != n:
            if self._pending_ratios is not None:
                return list(self._pending_ratios)
            return [1.0 / n] * n
        return [s / total for s in sizes]

    def set_plot_height_ratios(self, ratios: list[float]) -> None:
        """Apply per-plot height shares. Wrong length, a negative value or a zero sum is
        ignored (equal split stays). Before the splitter has a height they are held
        and applied on the first resize/show (22.2)."""
        if len(ratios) != len(self._plots) or any(r < 0 for r in ratios) or sum(ratios) <= 0:
            return
        total_r = sum(ratios)
        norm = [r / total_r for r in ratios]
        if not self._sizing_ready():
            self._pending_ratios = norm
            self._pending_equalize = False
            return
        self._apply_ratios(norm)

    def _apply_ratios(self, norm: list[float]) -> None:
        total = self._splitter.height()
        self._pending_ratios = None
        self._splitter.setSizes([max(1, int(round(total * r))) for r in norm])

    def _apply_pending_sizes(self) -> None:
        if not self._sizing_ready():
            return
        if self._pending_ratios is not None and len(self._pending_ratios) == len(self._plots):
            self._apply_ratios(self._pending_ratios)
        elif self._pending_equalize:
            self._equalize()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._apply_pending_sizes()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._apply_pending_sizes()

    # -- X tick labels (22.4) ------------------------------------------------------

    def _is_x_linked(self, plot: TimePlotWidget) -> bool:
        """Whether a time-domain plot is in the X-linked group (24.1); frequency-domain
        plots never are."""
        return plot.x_axis_datetime and plot.x_linked

    def _update_x_tick_labels(self) -> None:
        linked = [p for p in self._plots if p.x_axis_datetime and self._is_x_linked(p)]
        last_linked = linked[-1] if linked else None
        for p in self._plots:
            if not p.x_axis_datetime or not self._is_x_linked(p):
                p.set_x_tick_labels_visible(True)  # FFT / independent plots keep their own labels
            else:
                p.set_x_tick_labels_visible(p is last_linked)

    # -- drag to reorder (22.3) ------------------------------------------------------

    def _insertion_index_at(self, y: int) -> int:
        """Slot 0..n (in this widget's coordinates) the pointer is closest to."""
        slot = 0
        for p in self._plots:
            pane = self._panes[p]
            center = pane.mapTo(self, QPoint(0, pane.height() // 2)).y()
            if y > center:
                slot += 1
        return slot

    def _drop_target_index(self, plot: TimePlotWidget, slot: int) -> int | None:
        """Final index for dropping `plot` at insertion slot `slot`, or None if that
        slot is not allowed (domain boundary) or would not change anything."""
        idx = self._plots.index(plot)
        new_index = slot if slot <= idx else slot - 1
        return new_index if self._move_allowed(plot, new_index) else None

    def _plot_from_mime(self, mime) -> TimePlotWidget | None:
        if not mime.hasFormat(PLOT_MIME):
            return None
        pid = bytes(mime.data(PLOT_MIME)).decode("utf-8")
        return next((p for p in self._plots if p.plot_id == pid), None)

    def _show_drop_line(self, slot: int) -> None:
        if slot < len(self._plots):
            pane = self._panes[self._plots[slot]]
            y = pane.mapTo(self, QPoint(0, 0)).y()
        else:
            pane = self._panes[self._plots[-1]]
            y = pane.mapTo(self, QPoint(0, pane.height())).y()
        self._drop_line.setGeometry(0, max(0, min(y - 1, self.height() - 2)), self.width(), 2)
        self._drop_line.raise_()
        self._drop_line.show()

    def _handle_plot_drag(self, event) -> None:
        """DragEnter/DragMove of a plot-reorder drag. DragEnter is accepted whenever the
        payload is one of our plots: once Qt sees an ignored enter it sends no further
        move/drop until the pointer reaches another drop target, and a drag always starts
        over its own grip (a disallowed slot). Allowed/disallowed is decided per move."""
        plot = self._plot_from_mime(event.mimeData())
        if plot is None:
            self._drop_line.hide()
            event.ignore()
            return
        slot = self._insertion_index_at(int(event.position().y()))
        if self._drop_target_index(plot, slot) is None:
            self._drop_line.hide()
            if event.type() == QEvent.DragEnter:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        self._show_drop_line(slot)
        event.acceptProposedAction()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._handle_plot_drag(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._handle_plot_drag(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._drop_line.hide()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._finish_plot_drop(event)

    def _finish_plot_drop(self, event) -> None:
        self._drop_line.hide()
        plot = self._plot_from_mime(event.mimeData())
        if plot is None:
            event.ignore()
            return
        target = self._drop_target_index(plot, self._insertion_index_at(int(event.position().y())))
        if target is None:
            event.ignore()
            return
        self.move_plot_to(plot, target, via="drag")
        event.acceptProposedAction()

    def anchor_plot(self) -> TimePlotWidget | None:
        """The plot every other X-linked plot is linked to (specification.md 5.1, 24.1):
        the first *linked* time-domain plot. Looked up explicitly (rather than assumed to
        be plots[0]) since a frequency-domain (FFT) plot or an independent plot must never
        be the anchor.
        """
        return next((p for p in self._plots if self._is_x_linked(p)), None)

    def linked_plots(self) -> list[TimePlotWidget]:
        return [p for p in self._plots if self._is_x_linked(p)]

    def can_unlink(self, plot: TimePlotWidget) -> bool:
        """Whether `plot` may leave the linked group: at least one linked time plot must
        remain (24.1)."""
        return self._is_x_linked(plot) and len(self.linked_plots()) >= 2

    def set_plot_x_linked(self, plot: TimePlotWidget, linked: bool) -> bool:
        """Join/leave the X-linked group (detailed_specification.md 24.1). Returns False
        (nothing changes) for an FFT plot or when unlinking the last linked plot; True
        otherwise (including "already in that state")."""
        if plot not in self._plots or not plot.x_axis_datetime:
            return False
        if plot.x_linked == linked:
            return True
        if not linked and not self.can_unlink(plot):
            return False
        if linked:
            # Snap to the group's current range before linking, so re-linking changes
            # *this* plot, not the group (even when it is about to become the anchor).
            anchor = self.anchor_plot()
            if anchor is not None:
                plot.setXRange(*anchor.getViewBox().viewRange()[0], padding=0)
        plot.x_linked = linked
        self._relink_anchor()
        self._update_x_tick_labels()
        self._refresh_x_link_ui()
        log_op("set_x_linked", plot=plot.plot_id, linked=linked)
        anchor = self.anchor_plot()
        if anchor is not None:
            # The anchor (and so what the navigator should show) may have changed.
            self.rangeChanged.emit(tuple(anchor.getViewBox().viewRange()[0]))
        self.layoutChanged.emit()
        return True

    def _on_link_x_requested(self, plot: TimePlotWidget, linked: bool) -> None:
        self.set_plot_x_linked(plot, linked)
        self._refresh_x_link_ui()  # also reverts a chain click that was refused

    def _refresh_x_link_ui(self) -> None:
        """Sync every plot's menu hints and handle chain/tint with the link state (24.3).
        The menu item and chain button only exist while the independent_x_axis flag is on;
        the tint shows whenever a plot is independent (e.g. loaded from a view)."""
        enabled = features.is_enabled("independent_x_axis")
        for p in self._plots:
            toggle_allowed = p.x_axis_datetime and (not p.x_linked or self.can_unlink(p))
            p.x_link_menu_visible = enabled and p.x_axis_datetime
            p.x_link_toggle_allowed = toggle_allowed
            pane = self._panes.get(p)
            if pane is not None:
                pane.handle.set_link_state(
                    self._is_x_linked(p) or not p.x_axis_datetime, enabled and p.x_axis_datetime, toggle_allowed
                )

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
                # An independent time plot (24.1) must be explicitly detached: it may
                # still point at the old anchor.
                p.setXLink(anchor if p.x_linked else None)

    def _delete_if_idle(self, plot: TimePlotWidget, pane: PlotPane) -> None:
        if plot.pending_downsample_count() == 0:
            pane.deleteLater()

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
        """The grid owns the setting so plots created later inherit it (plots 50.1)."""
        self._downsample_enabled = enabled
        for plot in self._plots:
            plot.set_downsample_enabled(enabled)

    def downsample_enabled(self) -> bool:
        return self._downsample_enabled

    def set_x_range(self, x_min: float, x_max: float) -> None:
        # Never hand pyqtgraph a non-finite bound (data 29.4, plots 24.8): it raises from inside the
        # navigator's signal slot, and a NaN/Inf range must not reach the plots at all.
        if not (np.isfinite(x_min) and np.isfinite(x_max)):
            return
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
        self._emit_cursors_changed()
        self.clear_selection()
        for plot in self._plots:
            if plot.x_axis_datetime:
                plot.set_x_data(x)

    def set_cursor_mode_enabled(self, enabled: bool) -> None:
        self._cursor_mode_enabled = enabled
        for plot in self._plots:
            plot.set_cursor_mode_enabled(enabled)
        self._emit_cursors_changed()

    def set_hover_enabled(self, enabled: bool) -> None:
        """Hover value readout on/off for every plot (39.3)."""
        self._hover_enabled = bool(enabled)
        for plot in self._plots:
            plot.set_hover_enabled(self._hover_enabled)

    def _on_hover_x_changed(self, plot: TimePlotWidget, x) -> None:
        """Mirror the hover time onto the other plots of the X-linked group (39.1); an FFT or
        independent plot hovers on its own."""
        if not (plot.x_axis_datetime and plot.x_linked):
            return
        for other in self.linked_plots():
            if other is not plot:
                other.set_hover_x(x)

    def cursor_times(self) -> list[float]:
        """X positions of the time-domain cursors while cursor mode is on (25.2); the
        XY window highlights the data point at each."""
        if not self._cursor_mode_enabled:
            return []
        return [x for (is_time, x) in self._cursor_positions.values() if is_time]

    def _emit_cursors_changed(self) -> None:
        self.cursorsChanged.emit(self.cursor_times())

    def clear_cursors(self) -> None:
        """Drop every cursor -- e.g. opening a new file makes any existing cursor's
        X position meaningless (it referred to a position on the previous file's
        time column)."""
        self._cursor_positions = {}
        for plot in self._plots:
            plot.clear_cursors()
        self._emit_cursors_changed()

    # -- internal --------------------------------------------------------------

    def _on_remove_requested(self, plot: TimePlotWidget) -> None:
        self.remove_plot(plot)

    def _group_range(self) -> tuple[float, float] | None:
        anchor = self.anchor_plot()
        return tuple(anchor.getViewBox().viewRange()[0]) if anchor is not None else None

    def _restore_group_range(self, rng) -> None:
        """Put the linked group's X range back to `rng` after a separate (23.4). pyqtgraph
        maps an X link through the two ViewBoxes' *screen geometry*, and a just-created pane
        has not been laid out yet (its ViewBox is offset/narrower than its siblings'), so
        seeding the new plot's range shifts every linked plot by a few percent of the span.
        Re-applying the range afterwards (layout activated first) undoes that. If the source
        plot was independent, the new (linked) plot must not drag the group to the source's
        range either, which this also covers since `rng` is the group's own pre-separate range."""
        self.layout().activate()
        anchor = self.anchor_plot()
        if anchor is not None and rng is not None:
            anchor.setXRange(*rng, padding=0)

    def _on_separate_requested(self, plot: TimePlotWidget) -> None:
        if len(plot.series_names()) <= 1:
            return  # nothing to separate out
        rng = tuple(plot.getViewBox().viewRange()[0])
        group_rng = self._group_range()
        new_plot = self.add_plot(x_axis_datetime=plot.x_axis_datetime)
        new_plot._seed_x_range = rng  # keep the zoom (23.4)
        # Move all but the first series to the new plot. transfer_series(), not
        # series_data()+add_series(): a windowed series (Phase D) has no resident y
        # array for series_data() to hand back, so the internal entry (whichever
        # kind it is) must move directly instead.
        for name in plot.series_names()[1:]:
            plot.transfer_series(name, new_plot)
        self._restore_group_range(group_rng)

    # -- per-series selection / delete / separate (detailed_specification.md 23) ---------

    def selected_series(self) -> tuple[TimePlotWidget, str] | None:
        return self._selected

    def select_series(self, plot: TimePlotWidget, name: str) -> None:
        """Select `name` of `plot` (single selection grid-wide); selecting the already
        selected series again clears it (23.2)."""
        if plot not in self._plots or name not in plot.series_names():
            return
        if self._selected == (plot, name):
            self.clear_selection()
            return
        self.clear_selection()
        self._selected = (plot, name)
        plot.set_series_selected(name, True)
        log_op("select_series", plot=plot.plot_id, name=name)
        self.selectionChanged.emit(self._selected)

    def clear_selection(self) -> None:
        if self._selected is None:
            return
        plot, name = self._selected
        self._selected = None
        if plot in self._plots:
            plot.set_series_selected(name, False)
        self.selectionChanged.emit(None)

    def remove_selected_series(self) -> bool:
        """Remove the selected series from its plot (the plot itself stays, even if it
        becomes empty). Nothing selected: does nothing and returns False (23.3)."""
        if self._selected is None:
            return False
        plot, name = self._selected
        log_op("remove_series", plot=plot.plot_id, name=name)
        plot.remove_series(name)  # -> seriesRemoved -> clears the selection
        self.clear_selection()
        self.layoutChanged.emit()
        return True

    def remove_series_everywhere(self, name: str) -> bool:
        """Remove `name` from every time-domain plot (a deleted derived variable, expressions
        18.3); one layoutChanged afterwards re-syncs the navigator (plots 50.2). True if any
        plot had it."""
        removed = False
        for plot in self._plots:
            if plot.x_axis_datetime and name in plot.series_names():
                plot.remove_series(name)
                removed = True
        if removed:
            self.layoutChanged.emit()
        return removed

    def _on_series_removed(self, plot: TimePlotWidget, name: str) -> None:
        if self._selected == (plot, name):
            self.clear_selection()

    def _on_series_changed(self, plot: TimePlotWidget, name: str) -> None:
        """A series was added/replaced: the navigator overview only depends on the anchor's first
        visible series (24.9), so signal a layout change only then (plots 50.2)."""
        if plot is not self.anchor_plot():
            return
        visible = plot.visible_series_names()
        if visible and visible[0] == name:
            self.layoutChanged.emit()

    def _on_series_visibility_changed(self, plot: TimePlotWidget, name: str, visible: bool) -> None:
        self.layoutChanged.emit()  # the navigator overview follows the first *visible* series

    def _on_remove_series_requested(self, plot: TimePlotWidget, name: str) -> None:
        log_op("remove_series", plot=plot.plot_id, name=name)
        plot.remove_series(name)
        self.layoutChanged.emit()

    def _on_separate_series_requested(self, plot: TimePlotWidget, name: str) -> None:
        """Move just this one series into a new plot of the same domain (23.4); color,
        visibility and windowed-vs-resident kind travel with it (transfer_series)."""
        if name not in plot.series_names() or len(plot.series_names()) <= 1:
            return  # nothing to separate out
        rng = tuple(plot.getViewBox().viewRange()[0])
        group_rng = self._group_range()
        new_plot = self.add_plot(x_axis_datetime=plot.x_axis_datetime)
        new_plot._seed_x_range = rng  # keep the zoom (23.4)
        plot.transfer_series(name, new_plot)
        self._restore_group_range(group_rng)
        self.layoutChanged.emit()

    def _on_plot_range_changed(self, source: TimePlotWidget, x_range: tuple[float, float]) -> None:
        if not source.x_linked:
            return  # an independent plot never drives the navigator (24.1)
        if source not in self._plots:
            # A plot just removed from the grid is still X-linked until it is deleted, and its
            # pane is re-laid-out outside the splitter (its range jumps): that must not drive
            # the navigator away from the real group's range (24.1, dev notes 2026-10-02).
            return
        self.rangeChanged.emit(x_range)

    def _on_cursor_add_requested(self, source: TimePlotWidget, x: float) -> None:
        if not np.isfinite(x):
            return  # a non-finite X must never reach pyqtgraph (data 29.4) nor the XY highlight
        cursor_id = f"c{next(self._cursor_id_counter)}"
        self._cursor_positions[cursor_id] = (source.x_axis_datetime, x)
        for plot in self._plots:
            if plot.x_axis_datetime == source.x_axis_datetime:
                plot.add_cursor(cursor_id, x)
        self._emit_cursors_changed()
        self.layoutChanged.emit()

    def _on_cursor_move_requested(self, source: TimePlotWidget, cursor_id: str, x: float) -> None:
        if cursor_id not in self._cursor_positions or not np.isfinite(x):
            return  # a cursor already removed must not reappear as a phantom position; no NaN/Inf X
        self._cursor_positions[cursor_id] = (source.x_axis_datetime, x)
        for plot in self._plots:
            if plot is not source and plot.x_axis_datetime == source.x_axis_datetime:
                plot.set_cursor_x(cursor_id, x)
        self._emit_cursors_changed()

    def _on_cursor_remove_requested(self, source: TimePlotWidget, cursor_id: str) -> None:
        self._cursor_positions.pop(cursor_id, None)
        for plot in self._plots:
            if plot.x_axis_datetime == source.x_axis_datetime:
                plot.remove_cursor(cursor_id)
        self._emit_cursors_changed()
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
