"""NavigatorSync: keeps the navigator's overview curve and selection window in line with the
plots (detailed_specification.md 24.1 / 24.9, 36.6 C, 40). Moved out of main_window.py.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from ..core.background_worker import WorkerHub
from ..core.column import DerivedColumn, WindowedColumn
from ..core.numeric import NonNumericColumnError
from ..core.pyramid import thin_column
from ..core.time_index import ANCHOR_ROWS, TimeIndex
from .navigator import NavigatorWidget

logger = logging.getLogger(__name__)

# Navigator overview of a windowed column: fewer row groups than this -> refine with a thinned read (data 40).
_NAV_FEW_ROW_GROUPS = 200
_DERIVED_NAV_DIRECT_ROWS = 2_000_000  # 36.6 C: a windowed derived series up to this long is computed directly for the navigator


class NavigatorSync:
    def __init__(
        self,
        navigator: NavigatorWidget,
        hub: WorkerHub,
        *,
        time_values: Callable,
        raw_variables: Callable,
        data_source: Callable,
        anchor_plot: Callable,
        group_range: Callable,
        set_busy: Callable[[str, bool], None],
        time_finite_bounds: Callable,
    ) -> None:
        self.navigator = navigator
        self._hub = hub
        self._time_values = time_values
        self._raw_variables = raw_variables
        self._data_source = data_source
        self._anchor_plot = anchor_plot
        self._group_x_range = group_range
        self._set_busy = set_busy
        self._time_finite_bounds = time_finite_bounds
        self._gen = 0
        self._home = False  # true only while the synchronous part of refresh(home=True) runs
        self._pyramid_column: DerivedColumn | None = None  # 36.6 C: waited on for the overview

    def refresh(self, home: bool = False) -> None:
        """Rebuild the navigator overview. The selection window stays on the linked group's
        current range (24.1 re-sync rule) -- every overview, synchronous or asynchronous,
        reaches the navigator only through _apply(), so no path can forget the restore (the
        24.1 bug recurred 6 times; dev notes 2026-10-06). home=True ("go home": file load,
        time-axis change) gives the full extent instead -- for the synchronous part of this
        call only; refinements that land later keep the region (24.9)."""
        self._home = home
        try:
            self._refresh_impl()
        finally:
            self._home = False

    def _apply(self, x: np.ndarray, y: np.ndarray) -> None:
        """The one place an overview is handed to the navigator (24.9): set it, keeping the
        region (or full extent while homing), then put the region on the linked group's range
        when there is one. Neither step emits rangeSelected. While homing the second step is
        skipped: the group's range may still be the old axis's (set_time_column refreshes
        before the plots get the new time data), and clamping it into the new bounds would give
        a zero-width region; the plots' go_home reports the new range afterwards."""
        self.navigator.set_overview_data(x, y, keep_region=not self._home)
        if self._home:
            return
        group = self._group_x_range()
        if group is not None and np.isfinite(group[1]) and np.isfinite(group[2]):
            self.navigator.set_selected_range(group[1], group[2])

    def _refresh_impl(self) -> None:
        """The navigator shows an example waveform just for overall shape/scale, not
        any specific analysis — prefer the anchor (topmost time-domain) plot's first
        series (what the user is actually looking at) over an arbitrary file column,
        falling back to the file's first raw variable when nothing has been plotted at
        all yet. Uses plot_grid.anchor_plot(), not plots[0]: a frequency-domain (FFT)
        plot's series are in Hz, not aligned with self._time_values() at all.
        """
        self._detach_nav_pyramid_listener()
        # Every re-decision of the overview makes the previous request's result stale and
        # releases its busy flag, whichever path follows (data 28.4 / plots 24.9).
        self._gen += 1
        self._set_busy("navigator", False)
        if self._time_values() is None:
            return
        first_plot = self._anchor_plot()
        series_names = first_plot.visible_series_names() if first_plot is not None else []
        if series_names:
            # Read back the plotted series' own stored data rather than re-resolving
            # the name through variable_values: a plotted series stays valid even if
            # its name is no longer a "known variable" (e.g. the user just switched
            # the time axis to a column that's also currently plotted as a series —
            # it drops out of _raw_variables, but the already-plotted series is fine).
            name = series_names[0]
            _x, y, _color = first_plot.series_data(name)
            if y is None:  # windowed series (Phase D): no resident y to show as-is
                source = first_plot.series_source(name)
                if source is None:
                    return  # shouldn't happen (y is None only for a windowed series)
                y_source, _y_bounds = source
                if isinstance(y_source, DerivedColumn):
                    self._set_derived_navigator_overview(y_source)
                    return
                self._set_windowed_navigator_overview(y_source)
                return
        elif self._raw_variables():
            column = self._first_plottable_column()
            if column is None:  # every raw column is non-numeric: nothing to preview
                self._apply(np.array([], dtype=np.float64), np.array([], dtype=np.float64))
                return
            if isinstance(column, DerivedColumn):
                self._set_derived_navigator_overview(column)
                return
            if isinstance(column, WindowedColumn):
                # Coarse row-group min/max envelope from footer statistics (Phase
                # B), not the flat single (min, max) column_bounds() would give —
                # keeps real shape (e.g. this test data's daily on/off bursts)
                # visible without materializing this WindowedColumn's whole self
                # just to preview it (detailed_specification.md 13.5.1 Phase D).
                self._set_windowed_navigator_overview(column)
                return
            # Materializes one column (the file's first raw variable) even though
            # nothing has been plotted yet — an eager read Phase C doesn't remove
            # for a LazyColumn under the size threshold. Wiring this to Phase B's
            # footer-stats/pyramid unconditionally (not just for the
            # over-threshold case above) was deliberately deferred rather than
            # folded into this already-broad change — see detailed_specification.md
            # 13.5.1's Phase B note.
            y = column.values()
        else:
            return
        tv = self._time_values()
        if isinstance(tv, TimeIndex):
            # Lazy axis: per-anchor-interval min/max envelope of the resident y at the held anchor times.
            y = np.asarray(y, dtype=np.float64)
            starts = np.arange(0, min(len(y), len(tv)), ANCHOR_ROWS)
            if len(starts):
                mins, maxs = np.fmin.reduceat(y, starts), np.fmax.reduceat(y, starts)
                t = tv.take(starts)
                x = np.repeat(t, 2)
                yy = np.empty(len(t) * 2)
                yy[0::2], yy[1::2] = mins, maxs
                lo, hi = tv.finite_min_max()  # end points so the bounds cover the whole axis
                x = np.concatenate([[lo], x, [hi]])
                yy = np.concatenate([[yy[0]], yy, [yy[-1]]])
                self._apply(x, yy)
            return
        self._apply(tv, y)

    def _first_plottable_column(self):
        """The first raw variable whose data can be a numeric series, for the navigator's
        "nothing plotted yet" preview. A file whose second column is a string/list column
        (common: a label next to the signals) must still open (data 29.5),
        so an unusable column is skipped rather than raised; None if there is none."""
        for column in self._raw_variables().values():
            if isinstance(column, (WindowedColumn, DerivedColumn)):
                return column  # footer statistics only; an unusable dtype just has no envelope
            try:
                column.values()
            except NonNumericColumnError:
                continue
            return column
        return None

    def _set_derived_navigator_overview(self, column: DerivedColumn) -> None:
        """Navigator overview of a windowed derived series (36.6 C). A computed column has no
        footer statistics: show its pyramid envelope once one exists (the plot builds it when
        zoomed far out; otherwise a background build is requested here), until then a flat line
        spanning the time axis; a short file is just computed."""
        self._gen += 1
        self._set_busy("navigator", False)
        tv = self._time_values()
        lazy_t = isinstance(tv, TimeIndex)  # 36.6 D: lazy axis, no resident array (D x C)
        if lazy_t:
            t_lo, t_hi = tv.finite_min_max()
        else:
            finite_t = tv[np.isfinite(tv)]
            if not len(finite_t):
                self._apply(np.array([], dtype=np.float64), np.array([], dtype=np.float64))
                return
            t_lo, t_hi = float(finite_t.min()), float(finite_t.max())
        pyramid = column.pyramid
        if pyramid is None and len(tv) <= _DERIVED_NAV_DIRECT_ROWS:
            self._apply(tv if not lazy_t else tv[0:len(tv)], column.values())
            return
        if pyramid is None:
            self._apply(
                np.array([t_lo, t_hi], dtype=np.float64), np.array([0.0, 0.0], dtype=np.float64)
            )
            column.add_pyramid_listener("navigator", self._on_derived_pyramid_for_navigator)  # one-shot
            self._pyramid_column = column
            job = column.request_pyramid()
            if job is not None:
                gen = self._gen
                self._set_busy("navigator", True)
                self._hub.submit(
                    job,
                    lambda p, c=column, g=gen: self._on_derived_overview_built(c, g, p),
                    lambda msg, c=column, g=gen: self._on_derived_overview_failed(c, g, msg),
                    job="navigator_overview",
                    target="derived",
                )
            return
        xs: list[float] = []
        ys: list[float] = []
        keep = (pyramid.row_starts < len(tv)) & np.isfinite(pyramid.mins) & np.isfinite(pyramid.maxs)
        t_starts = tv.take(pyramid.row_starts[keep]) if lazy_t else tv[pyramid.row_starts[keep]]  # anchors: O(1)
        for t_start, mn, mx in zip(t_starts, pyramid.mins[keep], pyramid.maxs[keep]):
            xs += [float(t_start), float(t_start)]
            ys += [mn, mx]
        y_first = ys[0] if ys else 0.0
        y_last = ys[-1] if ys else 0.0
        self._apply(
            np.array([t_lo, *xs, t_hi], dtype=np.float64), np.array([y_first, *ys, y_last], dtype=np.float64)
        )

    def _on_derived_pyramid_for_navigator(self, ok: bool) -> None:
        # The pyramid of a derived series landed (or its build failed). One-shot listener: it is
        # gone now. Refresh through the full re-sync, which keeps the region on the
        # linked group's range (24.1).
        self._pyramid_column = None
        if ok and self._time_values() is not None:
            self.refresh()

    def _detach_nav_pyramid_listener(self) -> None:
        """The overview is being re-decided: the column that was waiting for its pyramid on the
        navigator's behalf only gets to keep waiting if _set_derived_navigator_overview asks again."""
        column, self._pyramid_column = self._pyramid_column, None
        if column is not None:
            column.remove_pyramid_listener("navigator")

    def _on_derived_overview_built(self, column: DerivedColumn, gen: int, pyramid) -> None:
        if gen == self._gen:  # a newer request owns the busy flag
            self._set_busy("navigator", False)
        column.set_pyramid(pyramid)  # fires on_pyramid_ready -> refresh (a no-op if superseded)

    def _on_derived_overview_failed(self, column: DerivedColumn, gen: int, message: str) -> None:
        if gen == self._gen:
            self._set_busy("navigator", False)
        column.pyramid_failed()
        logger.warning("derived navigator overview failed: %s", message)

    def _set_windowed_navigator_overview(self, column: WindowedColumn) -> None:
        """Show `column`'s overview in the navigator at once (no data I/O), then -- for a
        file with few row groups and no pyramid yet -- refine it with an evenly thinned
        read on a background job (detailed_specification.md 40)."""
        self._gen += 1  # drops a still-running refinement for an older request
        self._set_busy("navigator", False)
        x, y = self._windowed_navigator_overview(column)
        self._apply(x, y)
        if column.pyramid is not None or self._data_source() is None:
            return
        row_groups = self._data_source().row_groups()
        if not (0 < len(row_groups) < _NAV_FEW_ROW_GROUPS):
            return  # many row groups: the footer-stats envelope is already the overview
        gen = self._gen
        time_values = self._time_values()
        path = self._data_source().path
        name = column.name

        def job() -> tuple[np.ndarray, np.ndarray]:
            return thin_column(path, name)  # captures no resident array (37.3)

        self._set_busy("navigator", True)
        self._hub.submit(
            job,
            lambda result, g=gen, tv=time_values: self._on_navigator_overview_ready(g, tv, result),
            lambda msg, g=gen: self._on_navigator_overview_failed(g, msg),
            job="navigator_overview",
            target=name,
        )

    def _on_navigator_overview_ready(self, gen: int, time_values, result) -> None:
        if gen != self._gen or time_values is not self._time_values():
            # Superseded. The busy flag belongs to the newer request (or was cleared by the
            # _refresh_impl that replaced this one), so it is not touched here. The time_values
            # check is a safety net: with _gen bumped on every refresh it should not differ
            # while gen matches.
            return
        self._set_busy("navigator", False)
        idx, vals = result
        keep = idx < len(time_values)
        if isinstance(time_values, TimeIndex):
            x, y = time_values.approx_take(idx[keep]), vals[keep]  # coarse overview: no per-row disk reads
            bounds = time_values.finite_min_max()
        else:
            x, y = time_values[idx[keep]], vals[keep]
            fin = time_values[np.isfinite(time_values)]
            bounds = (fin.min(), fin.max()) if len(fin) else None
        if bounds is not None and len(y):  # end points: bounds = the time axis' finite min/max
            x = np.concatenate([[bounds[0]], x, [bounds[1]]])
            y = np.concatenate([[y[0]], y, [y[-1]]])
        self._apply(x, y)

    def _on_navigator_overview_failed(self, gen: int, message: str) -> None:
        if gen != self._gen:
            return
        self._set_busy("navigator", False)
        logger.warning("navigator overview read failed: %s", message)  # the coarse overview stays (28.2)

    def _windowed_navigator_overview(self, column: WindowedColumn) -> tuple[np.ndarray, np.ndarray]:
        """A coarse overview for `column` without data I/O (detailed_specification.md 40):
        the built pyramid's per-bucket (min, max) if there is one, otherwise the footer
        per-row-group (min, max) envelope. x comes from the resident time column; the
        overview always spans the time axis' finite min/max (end points added), so the
        navigator's bounds / initial selection never collapse to a point.
        """
        tv = self._time_values()
        bounds = self._time_finite_bounds()
        if bounds is None:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        t_lo, t_hi = bounds
        xs: list[float] = []
        ys: list[float] = []
        pyramid = column.pyramid
        lazy_t = isinstance(tv, TimeIndex)  # 36.6 D: lazy axis, no resident array
        if pyramid is not None:
            keep = pyramid.row_starts < len(tv)
            # a bucket start is a multiple of DEFAULT_BUCKET_ROWS == ANCHOR_ROWS: an O(1) anchor lookup
            t_starts = tv.take(pyramid.row_starts[keep]) if lazy_t else tv[pyramid.row_starts[keep]]
            for t_start, mn, mx in zip(t_starts, pyramid.mins[keep], pyramid.maxs[keep]):
                xs += [float(t_start), float(t_start)]
                ys += [mn, mx]
        else:
            stats = self._data_source().column_stats(column.name)
            for (row_offset, _row_count), (mn, mx, _null_count) in zip(self._data_source().row_groups(), stats):
                if mn is None or row_offset >= len(tv):
                    continue
                t_off = float(tv[row_offset])  # a TimeIndex pins every row-group start (O(1))
                xs += [t_off, t_off]
                ys += [mn, mx]
        bounds = column.bounds()
        mid = (bounds[0] + bounds[1]) / 2 if bounds else 0.0
        y_first = ys[0] if ys else mid
        y_last = ys[-1] if ys else mid
        x = np.array([t_lo, *xs, t_hi], dtype=np.float64)
        y = np.array([y_first, *ys, y_last], dtype=np.float64)
        return x, y
