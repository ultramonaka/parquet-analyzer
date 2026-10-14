"""The XY window's "map" display: computation, hover text, hatch item and image export
(analysis.ja.md 44.3). `compute_xy_map` is separate from the window so it can be tested directly.

The map is built from the same windowed reads as the density view (`XY_CHUNK_ROWS` rows at a time)
and the aggregation itself lives in `core/map_grid.py` (Qt-free).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QWidget

from ..core import map_grid as mg
from ..core.map_grid import GridAxisSpec, GridCancelled, GridResult, MapGridError
from .analysis_common import chunks
from .analysis_window import CANCELLED
from .i18n import tr
from .image_export import _FIG_DPI, _font_context
from .plot_widget import _COLOR_CYCLE

AXIS_MODES = ("step", "count")
XY_EXCLUDED_DRAW_LIMIT = 20_000  # excluded points (grey crosses) drawn at most; more are thinned evenly (44.13)


@dataclass
class XYMapResult:
    """What the map display shows. `kind` "map": one grid (`grids[0]`, Z = `z_name`); "curve": one
    grid per Y (`names` = the Y names). `x_auto` / `y_auto` = the round (lo, hi, step, count) the
    first scan suggests, for the settings panel's initial values."""

    kind: str
    x_name: str
    y_names: list[str]
    z_name: str | None
    row_start: int
    row_end: int
    agg: str
    min_count: int
    fill: str
    is_3d: bool = False
    time_name: str | None = None
    names: list[str] = field(default_factory=list)
    grids: list[GridResult] = field(default_factory=list)
    points: list[tuple[np.ndarray, ...]] = field(default_factory=list)  # overlay: (x, y, z) or (x, v) per grid
    thinned: tuple[int, int] | None = None
    x_spec: GridAxisSpec | None = None
    y_spec: GridAxisSpec | None = None
    x_auto: tuple[float, float, float, int] | None = None
    y_auto: tuple[float, float, float, int] | None = None
    n_valid: int = 0
    n_excluded: int = 0
    n_outside: int = 0
    median_refused: bool = False  # the median needs <= MAP_MEDIAN_MAX_POINTS valid points (44.2)
    keep_sparse_empty: bool = False  # cells below the minimum point count stay empty (44.13)
    # 44.13: file row numbers of the drawn points (same layout as `points`), the user-excluded rows inside
    # [row_start, row_end), and the finite ones of those (drawn as grey crosses; same layout as `points`).
    point_rows: list[np.ndarray] = field(default_factory=list)
    n_user_excluded: int = 0
    excluded_points: list[tuple[np.ndarray, ...]] = field(default_factory=list)
    excluded_point_rows: list[np.ndarray] = field(default_factory=list)
    excluded_thinned: bool = False  # more excluded points than XY_EXCLUDED_DRAW_LIMIT: some are not drawn

    @property
    def is_curve(self) -> bool:
        return self.kind == "curve"

    @property
    def no_data(self) -> bool:
        return not self.grids or all(g.no_data for g in self.grids)

    def value_range(self, hidden=frozenset()) -> tuple[float, float] | None:
        """(min, max) of the finite values of the shown (non-hidden) grids, for the colour bar."""
        parts = [g.value[np.isfinite(g.value)] for n, g in zip(self.names, self.grids) if n not in hidden]
        parts = [p for p in parts if p.size]
        if not parts:
            return None
        return min(float(p.min()) for p in parts), max(float(p.max()) for p in parts)

    def color_range(self, hidden=frozenset()) -> tuple[float, float] | None:
        """`value_range` under the minimum-width rule (44.10): the heat map's colour levels, shared by
        the screen and the image export, so rounding noise (Z = sine on a constant X) is not stretched
        into fake contrast."""
        rng = self.value_range(hidden)
        return None if rng is None else min_width_range(*rng)

    def cell_counts(self) -> tuple[int, int, int]:
        """(measured, filled, empty) cell totals over the shown grids."""
        measured = sum(g.n_measured for g in self.grids)
        filled = sum(g.n_filled for g in self.grids)
        total = sum(g.value.size for g in self.grids)
        return measured, filled, total - measured - filled

    def describe_grid(self) -> str:
        """The image stamp's "Map: grid X ..." line (44.3)."""
        def axis(spec: GridAxisSpec, name: str) -> str:
            how = f"{tr('xy.grid_step')} {spec.step:g}" if spec.mode == "step" else f"{tr('xy.grid_count')} {spec.count}"
            return f"{name} {spec.lo:g} - {spec.hi:g} ({how})"

        parts = [axis(self.x_spec, "X")]
        if self.y_spec is not None:
            parts.append(axis(self.y_spec, "Y"))
        parts.append(tr("xy.map_agg") + ": " + tr(f"xy.agg_{self.agg}"))
        parts.append(tr("xy.map_min_count") + f": {self.min_count}")
        parts.append(tr("xy.map_fill") + ": " + tr(f"xy.fill_{self.fill}"))
        if self.keep_sparse_empty:
            parts.append(tr("xy.map_keep_sparse"))
        if self.n_user_excluded:
            parts.append(tr("xy.map_stamp_excluded", n=self.n_user_excluded))
        return tr("xy.map_stamp", text=", ".join(parts))


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def grid_error_text(exc: MapGridError) -> str:
    """The translated sentence of a grid error (`xy.grid_err_<code>`, 44.1); core only gives codes."""
    key = f"xy.grid_err_{exc.code}"
    try:
        return tr(key, **exc.kwargs)
    except (KeyError, IndexError):
        return tr("xy.grid_err_generic")


def _auto_axis(lo: float, hi: float) -> tuple[float, float, float, int]:
    nlo, nhi, step = mg.nice_axis(lo, hi)
    count = max(2, min(mg.MAP_GRID_MAX_POINTS, int(round((nhi - nlo) / step)) + 1))
    return nlo, nhi, step, count


def resolve_axis(request: tuple[str, GridAxisSpec | None], auto) -> GridAxisSpec:
    """The effective axis: the user's spec, or the round range (`auto`) as a step / count grid."""
    mode, spec = request
    if spec is not None:
        return spec
    lo, hi, step, count = auto
    return GridAxisSpec("count", lo, hi, count=count) if mode == "count" else GridAxisSpec("step", lo, hi, step=step)


def compute_xy_map(
    fetch: Callable[[str, int, int], np.ndarray],
    x_name: str,
    y_names: list[str],
    z_name: str | None,
    kind: str,
    x_request: tuple[str, GridAxisSpec | None],
    y_request: tuple[str, GridAxisSpec | None] | None,
    agg: str,
    min_count: int,
    fill: str,
    row_start: int,
    row_end: int,
    *,
    chunk_rows: int,
    scatter_limit: int,
    cancelled: Callable[[], bool] | None = None,
    time_name: str | None = None,
    median_limit: int | None = None,
    is_3d: bool = False,
    excluded_rows: np.ndarray | None = None,
    keep_sparse_empty: bool = False,
):
    """Aggregate rows [row_start, row_end) into a map (X, Y -> Z) or one curve per Y (X -> Y).

    `x_request` / `y_request` = (mode "step"|"count", spec or None for the round automatic range).
    Rows where any used variable is non-finite are excluded (counted). Returns an `XYMapResult`
    (`median_refused` set when the median has too many points), or `CANCELLED`.

    `excluded_rows` (44.13): sorted unique file row numbers the user took out of the map; they are removed
    inside `chunk_iter`, so the bounds scan, the counts and the values never see them."""
    ex = np.empty(0, dtype=np.int64) if excluded_rows is None else np.asarray(excluded_rows, dtype=np.int64)
    ex = ex[(ex >= row_start) & (ex < row_end)]  # rows outside the range do not take part
    median_limit = mg.MAP_MEDIAN_MAX_POINTS if median_limit is None else median_limit
    n_rows = max(0, row_end - row_start)
    cache: dict[tuple[str, int, int], np.ndarray] = {}

    def get(name: str, a: int, b: int) -> np.ndarray:
        if n_rows <= chunk_rows:  # one chunk: read once, reuse in every pass
            if (name, a, b) not in cache:
                cache[(name, a, b)] = np.asarray(fetch(name, a, b), dtype=np.float64)
            return cache[(name, a, b)]
        return np.asarray(fetch(name, a, b), dtype=np.float64)

    series: list[tuple[str, list[str]]]
    if kind == "map":
        series = [(z_name, [x_name, y_names[0], z_name])]
    else:
        series = [(y, [x_name, y]) for y in y_names]

    def split_chunks(cols: list[str]):
        """(first row of the chunk, keep mask or None, arrays) with the user-excluded rows dropped."""
        for a, b in chunks(row_start, row_end, chunk_rows):
            arrs = tuple(get(c, a, b) for c in cols)
            keep = None
            if len(ex):
                lo, hi = np.searchsorted(ex, [a, b], side="left")
                if hi > lo:
                    keep = np.ones(b - a, dtype=bool)
                    keep[ex[lo:hi] - a] = False
                    arrs = tuple(x[keep] for x in arrs)
            yield a, keep, arrs

    def chunk_iter(cols: list[str]):
        for _a, _keep, arrs in split_chunks(cols):
            yield arrs

    result = XYMapResult(kind, x_name, list(y_names), z_name if kind == "map" else None, row_start, row_end,
                         agg, min_count, fill, is_3d=is_3d, time_name=time_name, names=[n for n, _ in series],
                         keep_sparse_empty=keep_sparse_empty, n_user_excluded=int(len(ex)))
    try:
        if len(ex):  # before the early returns: the crosses are drawn even with no valid point (44.13 (7))
            _read_excluded_points(result, series, get, ex, row_start, row_end, chunk_rows, cancelled)
        # pass 1: counts and bounds (round default ranges, valid-point total for the median)
        scans = [mg.scan_bounds(chunk_iter(cols), cancelled) for _, cols in series]
        # Points are rows, not rows x series (44.10): the series with the most valid rows, with its own
        # excluded count (so valid + excluded = rows), stands for the status line.
        best = max(scans, key=lambda s: s.n_valid)
        result.n_valid = best.n_valid
        result.n_excluded = best.n_excluded
        if result.n_valid == 0:
            return result
        live = [s for s in scans if s.n_valid]
        x_lo, x_hi = min(s.lo[0] for s in live), max(s.hi[0] for s in live)
        result.x_auto = _auto_axis(x_lo, x_hi)
        if kind == "map":
            result.y_auto = _auto_axis(live[0].lo[1], live[0].hi[1])
        if agg == "median" and result.n_valid > median_limit:
            result.median_refused = True
            return result
        result.x_spec = resolve_axis(x_request, result.x_auto)
        x_axis = result.x_spec.points()
        y_axis = None
        if kind == "map":
            result.y_spec = resolve_axis(y_request, result.y_auto)
            y_axis = result.y_spec.points()
        stride = math.ceil(result.n_valid / scatter_limit) if result.n_valid > scatter_limit else 1
        shown = 0
        for (name, cols), scan in zip(series, scans):
            kept: list[tuple[np.ndarray, ...]] = []
            kept_rows: list[np.ndarray] = []
            counter = [0]

            def sampled(it, kept=kept, kept_rows=kept_rows, counter=counter):
                for first, keep, chunk in it:
                    arrs = [np.asarray(c, dtype=np.float64) for c in chunk]
                    ok = np.isfinite(arrs[0])
                    for a in arrs[1:]:
                        ok &= np.isfinite(a)
                    idx = np.flatnonzero(ok)
                    if len(idx):
                        sel = idx[((counter[0] + np.arange(len(idx))) % stride) == 0]
                        counter[0] += len(idx)
                        kept.append(tuple(a[sel] for a in arrs))
                        # file row numbers of the drawn points (44.13): positions in the filtered chunk
                        # map back through the keep mask
                        kept_rows.append(first + (sel if keep is None else np.flatnonzero(keep)[sel]).astype(np.int64))
                    yield chunk

            grid = mg.grid_aggregate(sampled(split_chunks(cols)), x_axis, y_axis, agg, min_count, fill,
                                     cancelled, median_limit, keep_sparse_empty)
            result.grids.append(grid)
            result.n_outside += grid.n_outside
            pts = tuple(np.concatenate([k[j] for k in kept]) if kept else np.empty(0) for j in range(len(cols)))
            result.points.append(pts)
            result.point_rows.append(np.concatenate(kept_rows) if kept_rows else np.empty(0, dtype=np.int64))
            shown = max(shown, len(pts[0]))  # rows, like n_valid (44.10)
        if shown < result.n_valid:
            result.thinned = (shown, result.n_valid)
    except GridCancelled:
        return CANCELLED
    return result


def _read_excluded_points(result, series, get, ex, row_start, row_end, chunk_rows, cancelled) -> None:
    """The finite values of the user-excluded rows, per series, for the grey crosses (44.13); at most
    `XY_EXCLUDED_DRAW_LIMIT` per series, thinned evenly beyond that. Only chunks that hold such a row are read."""
    for name, cols in series:
        rows_parts: list[np.ndarray] = []
        val_parts: list[list[np.ndarray]] = [[] for _ in cols]
        for a, b in chunks(row_start, row_end, chunk_rows):
            if cancelled is not None and cancelled():
                raise GridCancelled()
            lo, hi = np.searchsorted(ex, [a, b], side="left")
            if hi == lo:
                continue
            local = ex[lo:hi] - a
            arrs = [get(c, a, b)[local] for c in cols]
            ok = np.isfinite(arrs[0])
            for v in arrs[1:]:
                ok &= np.isfinite(v)
            rows_parts.append(ex[lo:hi][ok])
            for j, v in enumerate(arrs):
                val_parts[j].append(v[ok])
        rows = np.concatenate(rows_parts) if rows_parts else np.empty(0, dtype=np.int64)
        vals = [np.concatenate(p) if p else np.empty(0) for p in val_parts]
        if len(rows) > XY_EXCLUDED_DRAW_LIMIT:
            pick = np.linspace(0, len(rows) - 1, XY_EXCLUDED_DRAW_LIMIT).astype(np.int64)
            rows, vals = rows[pick], [v[pick] for v in vals]
            result.excluded_thinned = True
        result.excluded_point_rows.append(rows)
        result.excluded_points.append(tuple(vals))


# ---------------------------------------------------------------------------
# Hover
# ---------------------------------------------------------------------------


def _g(v: float) -> str:
    return f"{v:.6g}"


def cell_state(grid: GridResult, idx: tuple[int, ...]) -> str:
    if grid.filled_mask[idx]:
        return tr("xy.map_cell_filled")
    if grid.measured_mask[idx]:
        return tr("xy.map_cell_measured")
    if grid.sparse_mask is not None and grid.sparse_mask[idx]:
        return tr("xy.map_cell_sparse")  # points exist but fewer than the minimum (44.13); the count is shown beside
    return tr("xy.map_cell_empty")


def map_hover_text(r: XYMapResult, x: float, y: float, hidden=frozenset()) -> str:
    """"X: .. Y: .. value: .. count: n (measured / filled / empty)" for the cell under (x, y) of the
    drawn map or curves; "" outside the grid. Hidden series are skipped (44.3 / 44.5)."""
    if not (math.isfinite(x) and math.isfinite(y)) or not r.grids:
        return ""
    if r.kind == "map":
        g = r.grids[0]
        ix, ox = mg.assign_axis(np.array([x]), g.x_axis)
        iy, oy = mg.assign_axis(np.array([y]), g.y_axis)
        if ox[0] or oy[0]:
            return ""
        idx = (int(iy[0]), int(ix[0]))
        v = g.value[idx]
        return tr("xy.map_hover", x=_g(g.x_axis[idx[1]]), y=_g(g.y_axis[idx[0]]), z=r.z_name or "Z",
                  value="-" if not np.isfinite(v) else _g(v), count=int(g.count[idx]), state=cell_state(g, idx))
    parts = []
    for name, g in zip(r.names, r.grids):
        if name in hidden:
            continue
        ix, ox = mg.assign_axis(np.array([x]), g.x_axis)
        if ox[0]:
            continue
        i = int(ix[0])
        v = g.value[i]
        parts.append(tr("xy.curve_hover", name=name, x=_g(g.x_axis[i]), value="-" if not np.isfinite(v) else _g(v),
                        count=int(g.count[i]), state=cell_state(g, (i,))))
    return "   ".join(parts)


# ---------------------------------------------------------------------------
# Drawing helpers (pyqtgraph)
# ---------------------------------------------------------------------------


def filled_runs(mask: np.ndarray) -> list[tuple[int, int, int]]:
    """Runs of True cells along each row of a (ny, nx) mask as `(iy, ix_first, ix_last)`; the hatch
    is drawn from these (a few rectangles instead of one per cell)."""
    runs: list[tuple[int, int, int]] = []
    for iy in range(mask.shape[0]):
        row = mask[iy]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            runs.append((iy, int(a), int(b) - 1))
    return runs


class HatchItem(pg.GraphicsObject):
    """White diagonal hatch over the filled cells (44.3). The pattern brush is drawn in device space,
    so the stripes keep their pitch whatever the zoom."""

    def __init__(self, xe: np.ndarray, ye: np.ndarray, mask: np.ndarray) -> None:
        super().__init__()
        self._path = QPainterPath()
        for iy, a, b in filled_runs(mask):
            self._path.addRect(QRectF(xe[a], ye[iy], xe[b + 1] - xe[a], ye[iy + 1] - ye[iy]))
        self._rect = self._path.boundingRect()
        self.n_runs = len(filled_runs(mask))

    def boundingRect(self):  # noqa: N802 (Qt override)
        return self._rect

    def paint(self, p, *args):
        p.setPen(QPen(Qt.PenStyle.NoPen))
        p.setBrush(QBrush(QColor(255, 255, 255, 230), Qt.BrushStyle.BDiagPattern))
        p.drawPath(self._path)


def map_rgba(value: np.ndarray, levels: tuple[float, float], cmap: pg.ColorMap) -> np.ndarray:
    """(nx, ny, 4) uint8 image of a (ny, nx) value grid: NaN cells are fully transparent."""
    lo, hi = levels
    valid = np.isfinite(value)
    norm = np.where(valid, (np.nan_to_num(value) - lo) / ((hi - lo) or 1.0), 0.0)
    rgba = cmap.map(np.clip(norm, 0, 1), mode="byte").reshape(value.shape + (4,)).copy()
    rgba[~valid, 3] = 0
    return np.ascontiguousarray(rgba.transpose(1, 0, 2))  # ImageItem's default axis order is (x, y)


# ---------------------------------------------------------------------------
# Image export
# ---------------------------------------------------------------------------


def min_width_range(lo: float, hi: float) -> tuple[float, float]:
    """(lo, hi) with the minimum-width rule of analysis 44.9 / 44.10: a width that is tiny against
    the magnitude (<= 1e-9 x max(|lo|, |hi|)) or zero counts as that magnitude (1 when everything is
    0), centred on the midpoint. Used for the curve's Y range and the heat map's colour range, on
    screen and in the image export alike."""
    mag = max(abs(lo), abs(hi))
    width = hi - lo
    if width <= 1e-9 * mag or width == 0:
        width = mag if mag > 0 else 1.0
        mid = (lo + hi) / 2
        lo, hi = mid - width / 2, mid + width / 2
    return lo, hi


def curve_y_range(r: XYMapResult, include_points: bool) -> tuple[float, float] | None:
    """Vertical range of the curve display, shared by the screen and the image export (analysis 44.9).

    min..max of the finite values of ALL curves (hidden series included, so hiding does not move the
    range) plus, with `include_points`, the finite overlay points; padded by 3 % per side. A width that
    is tiny against the magnitude (<= 1e-9 x max(|lo|, |hi|)) or zero counts as that magnitude (1 when
    everything is 0), so a constant curve is a centred horizontal line. None = no finite value."""
    parts = [g.value[np.isfinite(g.value)] for g in r.grids]
    if include_points:
        for pt in r.points:
            v = np.asarray(pt[-1], dtype=float)
            parts.append(v[np.isfinite(v)])
    vals = np.concatenate(parts) if parts else np.empty(0)
    if not vals.size:
        return None
    lo, hi = float(vals.min()), float(vals.max())
    lo, hi = min_width_range(lo, hi)
    pad = 0.03 * (hi - lo)
    out = (lo - pad, hi + pad)
    return out if all(math.isfinite(v) for v in out) else None


def build_xy_map_figure(r: XYMapResult, stamp_lines, size_px, hidden=frozenset(), view_angles=(25.0, -60.0),
                        show_points=True):
    """matplotlib Figure of an XYMapResult (44.3): `pcolormesh` with empty cells masked and the filled
    cells hatched, a colour bar; curves as lines with filled (measured) / open (filled) markers; 3D as
    `plot_surface` (empty cells are NaN: no face). `show_points` mirrors the window's "overlay measured
    points" check (the result always carries the points)."""
    show_points = show_points and bool(r.points)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PatchCollection
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    with _font_context():
        width, height = max(int(size_px[0]), 200), max(int(size_px[1]), 150)
        fig = Figure(figsize=(width / _FIG_DPI, height / _FIG_DPI), dpi=_FIG_DPI, facecolor="white",
                     layout="constrained")
        FigureCanvasAgg(fig)
        n_stamp = max(len(stamp_lines), 1)
        stamp_h = (n_stamp * 7 * 1.25 + 4) / 72 / (height / _FIG_DPI)
        fig.get_layout_engine().set(rect=(0, stamp_h, 1, 1 - stamp_h))
        if r.is_curve:
            ax = fig.subplots()
            for i, (name, g) in enumerate(zip(r.names, r.grids)):
                if name in hidden:
                    continue
                color = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
                ax.plot(g.x_axis, g.value, "-", color=color, linewidth=1.2, label=name)  # NaN breaks the line
                m, f = g.measured_mask, g.filled_mask
                ax.plot(g.x_axis[m], g.value[m], "o", color=color, markersize=4)
                ax.plot(g.x_axis[f], g.value[f], "o", markerfacecolor="white", color=color, markersize=4)
                if show_points:
                    px, pv = r.points[i]
                    ax.scatter(px, pv, s=2, color=color, alpha=0.4, rasterized=True)
                    if i < len(r.excluded_points) and len(r.excluded_points[i][0]):  # 44.13
                        ex_x, ex_v = r.excluded_points[i]
                        ax.scatter(ex_x, ex_v, s=14, marker="x", color="0.3", linewidths=1.2)
            ax.set_xlabel(r.x_name)
            ax.set_ylabel(", ".join(n for n in r.names if n not in hidden))
            if any(n not in hidden for n in r.names):
                ax.legend(loc="upper right", fontsize=8, framealpha=0.7)
            ax.grid(True, alpha=0.3)
            xe = mg.cell_edges(r.grids[0].x_axis)  # same ranges as the screen (44.9)
            ax.set_xlim(float(xe[0]), float(xe[-1]))
            yr = curve_y_range(r, show_points)
            if yr is not None:
                ax.set_ylim(*yr)
        elif not r.is_3d:
            ax = fig.subplots()
            g = r.grids[0]
            xe, ye = mg.cell_edges(g.x_axis), mg.cell_edges(g.y_axis)
            levels = r.color_range() or (0.0, 1.0)  # same levels as the screen (44.10)
            mesh = ax.pcolormesh(xe, ye, np.ma.masked_invalid(g.value), cmap="viridis", vmin=levels[0],
                                 vmax=levels[1] if levels[1] > levels[0] else levels[0] + 1.0, rasterized=True)
            fig.colorbar(mesh, ax=ax, label=r.z_name or "Z")
            rects = [Rectangle((xe[a], ye[iy]), xe[b + 1] - xe[a], ye[iy + 1] - ye[iy])
                     for iy, a, b in filled_runs(g.filled_mask)]
            if rects:  # hatched cells = values filled in, not measured (44.3)
                ax.add_collection(PatchCollection(rects, facecolor="none", edgecolor="white", hatch="//",
                                                  linewidth=0))
            if show_points:
                ax.scatter(r.points[0][0], r.points[0][1], s=1.5, color="0.45", alpha=0.6, rasterized=True)
                if r.excluded_points and len(r.excluded_points[0][0]):  # excluded points as crosses (44.13)
                    ax.scatter(r.excluded_points[0][0], r.excluded_points[0][1], s=14, marker="x", color="0.3",
                               linewidths=1.2)
            ax.set_xlim(xe[0], xe[-1])
            ax.set_ylim(ye[0], ye[-1])
            ax.set_xlabel(r.x_name)
            ax.set_ylabel(r.y_names[0])
        else:
            ax = fig.add_subplot(projection="3d")
            g = r.grids[0]
            gx, gy = np.meshgrid(g.x_axis, g.y_axis)
            levels = r.color_range() or (0.0, 1.0)  # same range as the screen's colours and Z axis (44.11)
            surf = ax.plot_surface(gx, gy, g.value, cmap="viridis", vmin=levels[0],
                                   vmax=levels[1] if levels[1] > levels[0] else levels[0] + 1.0)
            ax.set_zlim(levels[0], levels[1] if levels[1] > levels[0] else levels[0] + 1.0)
            ax.view_init(elev=view_angles[0], azim=view_angles[1])
            ax.set_xlabel(r.x_name)
            ax.set_ylabel(r.y_names[0])
            ax.set_zlabel(r.z_name or "Z")
            fig.colorbar(surf, ax=ax, shrink=0.7, pad=0.1, label=r.z_name or "Z")
        if stamp_lines:
            fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
    return fig


# ---------------------------------------------------------------------------
# Settings widget: one grid axis (44.3)
# ---------------------------------------------------------------------------


class GridAxisEdit(QWidget):
    """One axis of the grid in the settings row: [step | count] combo, `lo` - `hi`, and the step or
    the number of divisions. The round default range comes from the first computation (`set_auto`);
    once the user types in a field the range is theirs and is no longer replaced (`touched`) until
    `reset_auto()` ("Auto" button, 44.3)."""

    edited = Signal()  # the user changed the mode or a value (-> recompute)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.mode_combo = QComboBox()
        for mode in AXIS_MODES:
            self.mode_combo.addItem(tr(f"xy.grid_{mode}"), mode)
        self.lo_edit, self.hi_edit, self.value_edit = QLineEdit(), QLineEdit(), QLineEdit()
        for e, w in ((self.lo_edit, 62), (self.hi_edit, 62), (self.value_edit, 50)):
            e.setFixedWidth(w)
        self.lo_edit.setToolTip(tr("xy.grid_lo_tooltip"))
        self.hi_edit.setToolTip(tr("xy.grid_hi_tooltip"))
        lay.addWidget(self.mode_combo)
        lay.addWidget(self.value_edit)
        lay.addWidget(QLabel(tr("xy.grid_range")))
        lay.addWidget(self.lo_edit)
        lay.addWidget(QLabel("-"))
        lay.addWidget(self.hi_edit)
        self.touched = False
        self._dirty = False  # typed since the last `edited`
        self._auto: tuple[float, float, float, int] | None = None
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        for e in (self.lo_edit, self.hi_edit, self.value_edit):
            e.editingFinished.connect(self._on_edit_finished)
            e.textEdited.connect(self._mark_touched)

    def mode(self) -> str:
        return self.mode_combo.currentData()

    def _mark_touched(self, *_a) -> None:
        self.touched = True
        self._dirty = True

    def _on_edit_finished(self) -> None:
        if self._dirty:
            self._dirty = False
            self.edited.emit()

    def _on_mode_changed(self, *_a) -> None:
        self._fill_value_from_auto()
        self.edited.emit()

    def _fill_value_from_auto(self) -> None:
        if self._auto is None:
            return
        lo, hi, step, count = self._auto
        text = f"{step:g}" if self.mode() == "step" else str(count)
        self.value_edit.setText(text)

    @staticmethod
    def _fmt(v: float) -> str:
        return f"{v:.10g}"

    def set_auto(self, auto: tuple[float, float, float, int] | None) -> None:
        """Remember the round default range; show it unless the user has set their own."""
        self._auto = auto
        if auto is None or self.touched:
            return
        for e in (self.lo_edit, self.hi_edit, self.value_edit):
            e.blockSignals(True)
        self.lo_edit.setText(self._fmt(auto[0]))
        self.hi_edit.setText(self._fmt(auto[1]))
        self._fill_value_from_auto()
        for e in (self.lo_edit, self.hi_edit, self.value_edit):
            e.blockSignals(False)

    def reset_auto(self) -> None:
        """Back to the round default range ("Auto")."""
        self.touched = False
        self.set_auto(self._auto)

    def forget(self) -> None:
        """A different variable: the old range means nothing (the next computation fills it)."""
        self.touched = False
        self._auto = None
        for e in (self.lo_edit, self.hi_edit, self.value_edit):
            e.clear()

    def request(self) -> tuple[str, GridAxisSpec | None]:
        """(mode, spec): spec None = automatic; touched fields are parsed (a translated `ValueError` on a bad grid)."""
        mode = self.mode()
        if not self.touched:
            return mode, None
        try:
            lo, hi = float(self.lo_edit.text()), float(self.hi_edit.text())
            value = float(self.value_edit.text())
        except ValueError:
            raise ValueError(tr("xy.grid_not_number")) from None
        spec = GridAxisSpec("step", lo, hi, step=value) if mode == "step" else GridAxisSpec("count", lo, hi, count=value)
        try:
            spec.points()  # validate now: a bad grid is reported in the status, nothing is computed (44.1)
        except MapGridError as exc:
            raise ValueError(grid_error_text(exc)) from exc
        return mode, spec

    def describe(self) -> dict:
        return {"mode": self.mode(), "lo": self.lo_edit.text(), "hi": self.hi_edit.text(),
                "value": self.value_edit.text(), "auto": not self.touched}
