"""Registered maps / curves drawn over the XY window (analysis.ja.md 44.7).

Pure helpers shared by the window and the image export: marching-squares contour segments that skip
empty cells (NaN, 27.10), the `IsocurveItem` that draws them in real axis coordinates, and the
matplotlib overlay of an already built figure. The tables come from the main window's registry (27
chapter); their axes are taken as the plot's current X / Y variables (no name matching).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from pyqtgraph import functions as pgfn
from PySide6.QtGui import QPainterPath

from ..core.contour_levels import ContourLevelError, LevelSpec, plan_levels
from ..core.maps import DEFAULT_AXIS_NAMES, LookupTable
from .i18n import tr
from .plot_widget import _COLOR_CYCLE


@dataclass
class OverlayEntry:
    """One overlaid table: its registry name, the table, the colour and the legend text."""

    name: str
    table: LookupTable
    color: str
    label: str


def value_range(table: LookupTable) -> tuple[float, float] | None:
    """(min, max) of the finite values of a map, None if there is none."""
    v = table.values[np.isfinite(table.values)]
    return (float(v.min()), float(v.max())) if v.size else None


def level_plan(spec: LevelSpec, tables: dict[str, LookupTable]) -> dict[str, list[float]]:
    """`plan_levels` over the value ranges of the given maps (curves are not contoured)."""
    ranges = {n: r for n, t in tables.items() if t.kind == "map" and (r := value_range(t)) is not None}
    return plan_levels(spec, ranges)


def levels_error_text(exc: ContourLevelError) -> str:
    try:
        return tr(f"xy.levels_err_{exc.code}", **exc.kwargs)
    except (KeyError, IndexError):
        return tr("xy.levels_err_generic")


def axis_hint(table: LookupTable, x_name: str, y_name: str | None) -> str:
    """"" or "axes: rpm x torque" when the table's axis names (27.7) differ from the plot's X / Y
    variables, so that a mismatch is noticed (44.7). All-default names (X / Y / Z) say nothing."""
    names = table.axis_names[: table.arity]
    if tuple(names) == tuple(DEFAULT_AXIS_NAMES[: table.arity]):
        return ""
    current = (x_name,) if table.kind == "curve" else (x_name, y_name or "")
    if tuple(names) == current:
        return ""
    return tr("xy.overlay_axes", names="×".join(names))


def contour_segments(x_axis, y_axis, z, level: float) -> np.ndarray:
    """Marching-squares segments of `z` (shape (ny, nx)) at `level`, as an (m, 2, 2) array of real
    (x, y) coordinates (linear in the grid index, so axes need not be equally spaced). Cells with an
    empty / non-finite corner give no segment (44.7)."""
    xs, ys = np.asarray(x_axis, dtype=np.float64), np.asarray(y_axis, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    bad = ~np.isfinite(z)
    if len(xs) < 2 or len(ys) < 2 or bad.all():
        return np.empty((0, 2, 2))
    segs = pgfn.isocurve(np.where(bad, 0.0, z).T, float(level), connected=False, extendToEdge=False)
    if not segs:
        return np.empty((0, 2, 2))
    arr = np.asarray(segs, dtype=np.float64) - 0.5  # pyqtgraph puts sample (i, j) at (i + 0.5, j + 0.5)
    if bad.any():
        bad_cell = bad[:-1, :-1] | bad[1:, :-1] | bad[:-1, 1:] | bad[1:, 1:]  # (ny-1, nx-1)
        mid = arr.mean(axis=1)
        ix = np.clip(np.floor(mid[:, 0]).astype(int), 0, len(xs) - 2)
        iy = np.clip(np.floor(mid[:, 1]).astype(int), 0, len(ys) - 2)
        arr = arr[~bad_cell[iy, ix]]
    if not len(arr):
        return np.empty((0, 2, 2))
    out = np.empty_like(arr)
    out[..., 0] = np.interp(arr[..., 0], np.arange(len(xs)), xs)
    out[..., 1] = np.interp(arr[..., 1], np.arange(len(ys)), ys)
    return out


@dataclass
class OverlayState:
    """What the XY window has overlaid (44.7, 44.12): no Qt widgets, so it can be tested on its own.
    The drawn items and the menu live in `XYOverlayController`; this holds the decisions."""

    names: list[str] = field(default_factory=list)  # in the order added
    colors: dict[str, str] = field(default_factory=dict)  # a sticky colour per name
    hidden: set[str] = field(default_factory=set)  # names hidden by a click on the 2D legend
    contour_spec: LevelSpec = field(default_factory=LevelSpec)  # shared by every overlaid map
    segment_cache: dict[tuple[str, float], tuple[object, np.ndarray]] = field(default_factory=dict)
    notice: str = ""  # the status line's addition when the levels are out of range / invalid
    levels: dict[str, list[float]] = field(default_factory=dict)  # what was planned last (tests)

    def add(self, name: str, n_y: int) -> None:
        """Overlay `name` with a colour the displayed Y series do not use (the first overlay is orange
        with one Y), 44.7."""
        used = {self.colors.get(n) for n in self.names} | set(_COLOR_CYCLE[:n_y])
        self.names.append(name)
        self.colors[name] = next(
            (c for c in _COLOR_CYCLE if c not in used),
            _COLOR_CYCLE[(n_y + len(self.names) - 1) % len(_COLOR_CYCLE)],
        )

    def remove(self, name: str) -> None:
        self.names.remove(name)
        self.colors.pop(name, None)
        self.hidden.discard(name)

    def is_hidden(self, name: str, mode: str) -> bool:
        """Hidden by a 2D legend click. 3D has no legend to bring it back, so it ignores that (44.7)."""
        return mode != "3d" and name in self.hidden

    def entries(
        self, tables: dict[str, LookupTable], mode: str, x_name: str, y_name: str | None
    ) -> list[OverlayEntry]:
        """The overlays still valid for `mode`, in order; vanished ones are dropped (and forgotten), a curve
        in 3D is skipped but stays selected, drawn again on return to 2D (44.10)."""
        out = []
        for name in list(self.names):
            t = tables.get(name)
            if t is not None and mode == "3d" and t.kind == "curve":
                continue
            if t is None:
                self.remove(name)
                continue
            hint = axis_hint(t, x_name, y_name)
            out.append(OverlayEntry(name, t, self.colors[name], f"{name} ({hint})" if hint else name))
        return out

    def plan_levels(self, entries: list[OverlayEntry], mode: str) -> dict[str, list[float]]:
        """Contour values for the visible map overlays (44.7); sets `notice` if there is a problem."""
        tables = {e.name: e.table for e in entries if e.table.kind == "map" and not self.is_hidden(e.name, mode)}
        self.notice = ""
        if not tables:
            return {}
        try:
            plan = level_plan(self.contour_spec, tables)
        except ContourLevelError as exc:
            self.notice = levels_error_text(exc)
            return {}
        if not any(plan.values()):
            self.notice = tr("xy.levels_out_of_range")
        return plan

    def segments(self, name: str, table: LookupTable, level: float) -> np.ndarray:
        """The contour segments of `table` at `level`, cached per (name, level); a changed table is a new
        object, so it is recomputed."""
        key = (name, float(level))
        hit = self.segment_cache.get(key)
        if hit is None or hit[0] is not table:
            hit = (table, contour_segments(table.x, table.y, table.z, level))
            self.segment_cache[key] = hit
        return hit[1]

    def prune_cache(self, tables: dict[str, LookupTable]) -> None:
        """Forget cached contours of tables that were overwritten or deleted (registry change, 27.8)."""
        self.segment_cache = {k: v for k, v in self.segment_cache.items() if tables.get(k[0]) is v[0]}


class MapIsocurveItem(pg.IsocurveItem):
    """One contour line of a map at `level`, drawn in the map's own axis coordinates (pyqtgraph's
    `IsocurveItem` works in sample-index coordinates and cannot skip NaN cells)."""

    def __init__(self, x_axis, y_axis, z, level: float, pen, segments: np.ndarray | None = None) -> None:
        super().__init__(None, level, pen)
        self._x_axis, self._y_axis = np.asarray(x_axis, dtype=np.float64), np.asarray(y_axis, dtype=np.float64)
        self._given = segments  # precomputed (cached) segments: no marching squares again
        self.segments = np.empty((0, 2, 2))
        self.setData(np.asarray(z, dtype=np.float64), level)

    def generatePath(self) -> None:  # noqa: N802 (pyqtgraph override)
        self.segments = self._given if self._given is not None else contour_segments(
            self._x_axis, self._y_axis, self.data, self.level)
        self.path = QPainterPath()
        for a, b in self.segments:
            self.path.moveTo(float(a[0]), float(a[1]))
            self.path.lineTo(float(b[0]), float(b[1]))

    def label_position(self) -> tuple[float, float] | None:
        """Where the value label goes: the middle segment of the line."""
        if self.path is None:
            self.generatePath()
        if not len(self.segments):
            return None
        a, b = self.segments[len(self.segments) // 2]
        return float((a[0] + b[0]) / 2), float((a[1] + b[1]) / 2)


# ---------------------------------------------------------------------------
# Image export (matplotlib)
# ---------------------------------------------------------------------------


def _clip_axis(axis: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Indices of the grid points inside [lo, hi] (the displayed range)."""
    lo, hi = min(lo, hi), max(lo, hi)
    return np.flatnonzero((axis >= lo) & (axis <= hi))


def apply_overlays_to_figure(fig, entries: list[OverlayEntry], plan: dict[str, list[float]], is_3d: bool) -> None:
    """Draw the overlays onto the main axes of a finished export figure (44.7): 2D curves as lines
    with markers, maps as `contour` lines at the planned values with value labels; 3D maps as
    translucent surfaces clipped to the axes' range. The axis limits stay as they were."""
    from matplotlib.lines import Line2D

    from .image_export import _font_context

    if not entries or not fig.axes:
        return
    with _font_context():  # the legend / labels made here need the CJK fallback too (19.2)
        _apply_overlays(fig, entries, plan, is_3d, Line2D)


def _apply_overlays(fig, entries, plan, is_3d, Line2D) -> None:
    ax = fig.axes[0]
    handles: list = []
    if is_3d:
        limits = (ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d())
        for e in entries:
            t = e.table
            if t.kind != "map":
                continue
            ix, iy = _clip_axis(t.x, *limits[0]), _clip_axis(t.y, *limits[1])
            if len(ix) >= 2 and len(iy) >= 2:
                z = t.z[np.ix_(iy, ix)].copy()
                if np.isfinite(z).any():  # the Z range grows to hold the surface (as on screen, 44.7)
                    zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
                    limits = (limits[0], limits[1], (min(min(limits[2]), zmin), max(max(limits[2]), zmax)))
                gx, gy = np.meshgrid(t.x[ix], t.y[iy])
                ax.plot_surface(gx, gy, z, color=e.color, alpha=0.4, linewidth=0, shade=False)
            handles.append(Line2D([], [], color=e.color, label=e.label))
        ax.set_xlim3d(*limits[0])
        ax.set_ylim3d(*limits[1])
        ax.set_zlim3d(*limits[2])
    else:
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        for e in entries:
            t = e.table
            if t.kind == "curve":
                ax.plot(t.x, t.y, "-o", color=e.color, markersize=3, linewidth=1.3)
            else:
                levels = sorted(plan.get(e.name, []))
                if levels:
                    cs = ax.contour(t.x, t.y, np.ma.masked_invalid(t.z), levels=levels, colors=[e.color],
                                    linewidths=1.0)
                    ax.clabel(cs, fmt="%g", fontsize=7)
            handles.append(Line2D([], [], color=e.color, label=e.label))
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
    old = ax.get_legend()
    existing, _labels = ax.get_legend_handles_labels()
    ax.legend(handles=[*existing, *handles], loc="upper right", fontsize=8, framealpha=0.7,
              markerscale=4 if old is not None else 1)
