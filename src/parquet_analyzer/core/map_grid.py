"""Grid aggregation of XY measurements into a map / curve (analysis.ja.md 44.1, 44.2).

Qt-free and numpy-only (no scipy, as for the lookup tables, expressions 27.1). Measured points are
assigned to the nearest grid point of each axis (cell borders at the midpoints between grid
points; the outermost cells extend half a spacing outwards, points beyond are counted as "outside"),
aggregated chunk by chunk (count / sum / max / min / mean, median only for a bounded number of
points; "time": the summed row durations of the points in a cell, 44.14) and the empty cells are optionally filled: `smooth` (Laplace equation solved by a
matrix-free conjugate gradient in index space), `nearest` (nearest measured cell) or `none`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np

MAP_GRID_MAX_POINTS = 500  # grid points per axis (manager table / interpolation cost), 44.1
MAP_MEDIAN_MAX_POINTS = 20_000_000  # above this many valid points the median is not offered, 44.2
AGGREGATIONS = ("mean", "median", "max", "min", "count", "time")
FILLS = ("smooth", "nearest", "none")
_NEAREST_BLOCK_ELEMENTS = 4_000_000  # empty x measured pairs handled per block of the nearest fill
_CG_CHECK_EVERY = 100  # cancellation is polled every this many CG iterations (25.1)


class MapGridError(ValueError):
    """An invalid grid specification or aggregation request (44.1). It carries a reason `code`
    (`unknown_mode`, `non_finite`, `lo_ge_hi`, `not_integer`, `too_few`, `too_many` (limit),
    `step_not_positive`, `too_fine` (neighbouring grid points are equal in floating point),
    `unknown_agg`, `unknown_fill`, `median_too_many` (limit)) and `kwargs`;
    the UI turns them into a translated sentence (`xy.grid_err_<code>`), core holds no display text."""

    def __init__(self, code: str, **kwargs) -> None:
        super().__init__(code)
        self.code = code
        self.kwargs = kwargs


class GridCancelled(Exception):
    """Raised inside a computation when `cancelled()` turned true (the caller drops the result)."""


# ---------------------------------------------------------------------------
# Grid axis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridAxisSpec:
    """One axis of the grid: `mode` "step" (grid points `lo + k*step`, never past `hi`) or "count"
    (`count` equally spaced points from `lo` to `hi`)."""

    mode: str
    lo: float
    hi: float
    step: float | None = None
    count: int | None = None

    def points(self) -> np.ndarray:
        """The grid points, validated (`MapGridError`): finite `lo < hi`, positive step, 2..500 points."""
        if self.mode not in ("step", "count"):
            raise MapGridError("unknown_mode", mode=str(self.mode))
        lo, hi = self.lo, self.hi
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise MapGridError("non_finite")
        if not lo < hi:
            raise MapGridError("lo_ge_hi")
        if self.mode == "count":
            n = self.count
            if n is None or int(n) != n:
                raise MapGridError("not_integer")
            n = int(n)
            if n < 2:
                raise MapGridError("too_few")
            if n > MAP_GRID_MAX_POINTS:
                raise MapGridError("too_many", limit=MAP_GRID_MAX_POINTS)
            return _checked_ascending(_round_points(np.linspace(lo, hi, n), (hi - lo) / (n - 1)))
        step = self.step
        if step is None or not (math.isfinite(step) and step > 0):
            raise MapGridError("step_not_positive")
        ratio = (hi - lo) / step
        if not math.isfinite(ratio) or ratio > MAP_GRID_MAX_POINTS * 4:
            raise MapGridError("too_many", limit=MAP_GRID_MAX_POINTS)
        # `hi` counts as reached when it is within step*1e-9 (float error must not drop it)
        k = int(math.floor(ratio + 1e-9))
        if k + 1 < 2:
            raise MapGridError("too_few")
        if k + 1 > MAP_GRID_MAX_POINTS:
            raise MapGridError("too_many", limit=MAP_GRID_MAX_POINTS)
        # lo + k*step, never accumulated sums
        return _checked_ascending(_round_points(lo + np.arange(k + 1) * step, step))


def _checked_ascending(points: np.ndarray) -> np.ndarray:
    """The points must be strictly ascending: a spec too fine for the range (`[1e15, 1e15+1]` with 400
    points) repeats float values, giving zero-width cells (44.10)."""
    if not np.all(np.diff(points) > 0):
        raise MapGridError("too_fine")
    return points


def _round_points(points: np.ndarray, step: float) -> np.ndarray:
    """Round grid points to the step's decimal resolution + 3 digits, so `lo + k*step` noise such as
    -0.3999999999999999 never reaches the CSV or the stored map (44.1)."""
    digits = max(0, -math.floor(math.log10(step)) + 3)
    return np.round(points, min(digits, 15))


def _decimal(mantissa: int, exponent: int) -> float:
    return float(f"{mantissa}e{exponent}")  # the float nearest to m*10^e (no k*step rounding noise)


def nice_step(raw: float) -> tuple[int, int]:
    """(mantissa in 1/2/5/10, exponent) with `mantissa * 10**exponent >= raw` (a "round" step)."""
    exp = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if _decimal(m, exp) >= raw * (1 - 1e-12):
            return m, exp
    return 1, exp + 1  # pragma: no cover - 10 always qualifies


def nice_axis(lo: float, hi: float, target: int = 20) -> tuple[float, float, float]:
    """A round range `(lo', hi', step)` covering [lo, hi] with about `target` divisions: the step is
    1/2/5 x 10^k and the bounds are multiples of it, rounded outwards (44.1). A single value
    (`lo == hi`) gets a range of +-10 % of it (+-1 around 0)."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise MapGridError("non_finite")
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    m, e = nice_step((hi - lo) / max(target, 1))
    step = _decimal(m, e)
    k_lo, k_hi = math.floor(lo / step), math.ceil(hi / step)
    nlo, nhi = _decimal(k_lo * m, e), _decimal(k_hi * m, e)
    while nlo > lo:  # rounding noise must never cut into the data
        k_lo -= 1
        nlo = _decimal(k_lo * m, e)
    while nhi < hi:
        k_hi += 1
        nhi = _decimal(k_hi * m, e)
    return nlo, nhi, step


def cell_edges(points: np.ndarray) -> np.ndarray:
    """Cell borders of grid `points`: midpoints, and the outer cells extend half a spacing out."""
    p = np.asarray(points, dtype=np.float64)
    mid = (p[:-1] + p[1:]) / 2
    return np.concatenate([[p[0] - (p[1] - p[0]) / 2], mid, [p[-1] + (p[-1] - p[-2]) / 2]])


def assign_axis(values: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(index of the nearest grid point, outside mask). A value exactly on a midpoint goes to the
    larger grid point (as `nearest` in 27.1); cells are half open `[edge_k, edge_k+1)`, so a value
    at or beyond the outer border counts as outside."""
    edges = cell_edges(points)
    idx = np.searchsorted(edges, values, side="right") - 1
    outside = (idx < 0) | (idx >= len(points))
    return np.clip(idx, 0, len(points) - 1), outside


# ---------------------------------------------------------------------------
# First pass: bounds and counts
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    n_valid: int = 0
    n_excluded: int = 0
    lo: list[float] = field(default_factory=list)  # per column: min over valid rows
    hi: list[float] = field(default_factory=list)


def _poll(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise GridCancelled()


def _finite_rows(cols: tuple[np.ndarray, ...]) -> tuple[tuple[np.ndarray, ...], int]:
    arrs = tuple(np.asarray(c, dtype=np.float64) for c in cols)
    mask = np.isfinite(arrs[0])
    for a in arrs[1:]:
        mask &= np.isfinite(a)
    n_bad = int(mask.size - np.count_nonzero(mask))
    if n_bad:
        arrs = tuple(a[mask] for a in arrs)
    return arrs, n_bad


def scan_bounds(chunks: Iterable[tuple[np.ndarray, ...]], cancelled: Callable[[], bool] | None = None) -> ScanResult:
    """Pass 1 (44.2): count valid / non-finite rows and the min/max of every column over the rows
    where all columns are finite. `chunks` yields tuples of equally long arrays."""
    out = ScanResult()
    for chunk in chunks:
        _poll(cancelled)
        arrs, bad = _finite_rows(tuple(chunk))
        out.n_excluded += bad
        n = len(arrs[0])
        out.n_valid += n
        if n:
            if not out.lo:
                out.lo = [math.inf] * len(arrs)
                out.hi = [-math.inf] * len(arrs)
            for j, a in enumerate(arrs):
                out.lo[j] = min(out.lo[j], float(a.min()))
                out.hi[j] = max(out.hi[j], float(a.max()))
    return out


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class GridResult:
    """The aggregated grid. `value` is `(ny, nx)` for a map and `(nx,)` for a curve (`y_axis` None);
    NaN where a cell has no value. `measured_mask` = cells with count >= `min_count`; `filled_mask`
    = cells given a value by the fill. For agg "count" the value is the count itself everywhere."""

    x_axis: np.ndarray
    y_axis: np.ndarray | None
    value: np.ndarray
    count: np.ndarray
    measured_mask: np.ndarray
    filled_mask: np.ndarray
    n_valid: int
    n_excluded_nonfinite: int
    n_outside: int
    agg: str
    min_count: int
    fill: str
    sparse_mask: np.ndarray | None = None  # cells with 1 <= count < min_count (44.13); None = not computed
    keep_sparse_empty: bool = False
    total_time: float = 0.0  # agg "time": summed weight (in the chosen unit) of the valid points inside the grid (44.14)

    @property
    def is_curve(self) -> bool:
        return self.y_axis is None

    @property
    def n_measured(self) -> int:
        return int(self.measured_mask.sum())

    @property
    def n_filled(self) -> int:
        return int(self.filled_mask.sum())

    @property
    def n_empty(self) -> int:
        return int(np.isnan(self.value).sum()) if self.agg not in ("count", "time") else int((~self.measured_mask).sum())

    @property
    def no_data(self) -> bool:
        """No cell reached `min_count`: nothing to show or export (44.2)."""
        return self.n_measured == 0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def grid_aggregate(
    chunks: Iterable[tuple[np.ndarray, ...]],
    x_axis: np.ndarray,
    y_axis: np.ndarray | None,
    agg: str = "mean",
    min_count: int = 1,
    fill: str = "smooth",
    cancelled: Callable[[], bool] | None = None,
    median_limit: int | None = None,
    keep_sparse_empty: bool = False,
    time_unit_s: float = 1.0,
) -> GridResult:
    """Aggregate `(x, y, z)` chunks (a curve: `(x, v)` with `y_axis` None) into the grid (44.2).
    For agg "time" the last array of a chunk is the row's duration in seconds (44.14): the cell value is
    the summed duration / `time_unit_s`, every cell is valid (0 where no point), min_count and fill are unused.
    Rows with any non-finite column are excluded (counted), points beyond the outer cell borders are
    counted as outside. `GridCancelled` is raised when `cancelled()` turns true. `keep_sparse_empty`
    (44.13): cells with 1 <= count < `min_count` stay empty after the fill instead of being filled
    (no effect for agg "count" or fill "none", where nothing is filled anyway)."""
    if agg not in AGGREGATIONS:
        raise MapGridError("unknown_agg", value=str(agg))
    if fill not in FILLS:
        raise MapGridError("unknown_fill", value=str(fill))
    min_count = 1 if agg == "time" else max(int(min_count), 1)  # time: every cell is valid (44.14)
    median_limit = MAP_MEDIAN_MAX_POINTS if median_limit is None else median_limit
    x_axis = np.asarray(x_axis, dtype=np.float64)
    curve = y_axis is None
    nx = len(x_axis)
    ny = 1 if curve else len(y_axis)
    ncell = nx * ny
    count = np.zeros(ncell, dtype=np.int64)
    total = np.zeros(ncell, dtype=np.float64) if agg in ("mean", "time") else None
    vmax = np.full(ncell, -np.inf) if agg == "max" else None
    vmin = np.full(ncell, np.inf) if agg == "min" else None
    keep_idx: list[np.ndarray] = []
    keep_val: list[np.ndarray] = []
    kept = 0
    res = dict(valid=0, bad=0, outside=0)

    for chunk in chunks:
        _poll(cancelled)
        arrs, bad = _finite_rows(tuple(chunk))
        res["bad"] += bad
        if not len(arrs[0]):
            continue
        ix, out = assign_axis(arrs[0], x_axis)
        if curve:
            v = arrs[1]
        else:
            iy, out_y = assign_axis(arrs[1], y_axis)
            out = out | out_y
            v = arrs[2]
        n_out = int(np.count_nonzero(out))
        res["outside"] += n_out
        res["valid"] += len(arrs[0])
        if n_out:
            ok = ~out
            ix, v = ix[ok], v[ok]
            if not curve:
                iy = iy[ok]
        if not len(v):
            continue
        flat = ix if curve else iy * nx + ix
        count += np.bincount(flat, minlength=ncell)
        if total is not None:
            total += np.bincount(flat, weights=v, minlength=ncell)
        if vmax is not None:
            np.maximum.at(vmax, flat, v)
        if vmin is not None:
            np.minimum.at(vmin, flat, v)
        if agg == "median":
            kept += len(v)
            if kept > median_limit:
                raise MapGridError("median_too_many", limit=median_limit)
            keep_idx.append(flat.astype(np.int32))
            keep_val.append(v.copy())

    timed = agg == "time"
    measured = np.ones(ncell, dtype=bool) if timed else count >= min_count
    with np.errstate(invalid="ignore", divide="ignore"):
        if timed:
            value = total / time_unit_s
        elif agg == "count":
            value = count.astype(np.float64)
        elif agg == "mean":
            value = np.where(count > 0, total / np.maximum(count, 1), np.nan)
        elif agg == "max":
            value = np.where(count > 0, vmax, np.nan)
        elif agg == "min":
            value = np.where(count > 0, vmin, np.nan)
        else:
            value = _grouped_median(keep_idx, keep_val, ncell, count)
    if agg not in ("count", "time"):
        value = np.where(measured, value, np.nan)  # below min_count: no data
    shape = (nx,) if curve else (ny, nx)
    value, measured_2d = value.reshape(shape), measured.reshape(shape)
    filled = np.zeros(shape, dtype=bool)
    if agg not in ("count", "time") and fill != "none" and measured_2d.any() and not measured_2d.all():
        value, filled = fill_empty(value, measured_2d, fill, cancelled)
    count_nd = count.reshape(shape)
    sparse = (count_nd > 0) & (count_nd < min_count)
    keep = bool(keep_sparse_empty) and agg not in ("count", "time") and fill != "none"
    if keep and sparse.any():  # filled like any unknown cell above, then taken back (44.13)
        value = np.where(sparse, np.nan, value)
        filled = filled & ~sparse
    return GridResult(
        x_axis, None if curve else np.asarray(y_axis, dtype=np.float64), value, count_nd, measured_2d,
        filled, res["valid"], res["bad"], res["outside"], agg, min_count, fill, sparse_mask=sparse,
        keep_sparse_empty=keep, total_time=float(total.sum()) / time_unit_s if timed else 0.0,
    )


def _grouped_median(idx_parts: list[np.ndarray], val_parts: list[np.ndarray], ncell: int, count: np.ndarray) -> np.ndarray:
    """Per-cell median of the collected (cell, value) pairs: sort by (cell, value), then read the
    middle element(s) of every occupied cell's run."""
    out = np.full(ncell, np.nan)
    if not idx_parts:
        return out
    idx = np.concatenate(idx_parts)
    val = np.concatenate(val_parts)
    order = np.lexsort((val, idx))
    val = val[order]
    cnt = count  # every kept point is counted; counts of cells equal the run lengths
    start = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    occ = np.flatnonzero(cnt > 0)
    s, c = start[occ], cnt[occ]
    lo_mid = val[s + (c - 1) // 2]
    hi_mid = val[s + c // 2]
    out[occ] = (lo_mid + hi_mid) / 2  # odd count: both are the middle element
    return out


# ---------------------------------------------------------------------------
# Filling empty cells
# ---------------------------------------------------------------------------


def fill_empty(
    value: np.ndarray, measured: np.ndarray, fill: str, cancelled: Callable[[], bool] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Fill the cells where `measured` is False (44.2). Measured cells keep their value. Returns
    `(value, filled_mask)`; needs at least one measured cell (else returned unchanged)."""
    out = np.array(value, dtype=np.float64, copy=True)
    out[~measured] = np.nan
    if fill == "none" or not measured.any() or measured.all():
        return out, np.zeros(measured.shape, dtype=bool)
    if out.ndim == 1:
        filled_vals = _fill_1d(out, measured, fill)
    elif fill == "smooth":
        filled_vals = _fill_smooth_2d(out, measured, cancelled)
    else:
        filled_vals = _fill_nearest_2d(out, measured, cancelled)
    empty = ~measured
    out[empty] = filled_vals[empty]
    return out, empty


def _fill_1d(value: np.ndarray, measured: np.ndarray, fill: str) -> np.ndarray:
    """Curve: `smooth` = linear between the measured neighbours, constant beyond the ends (what the
    Laplace equation gives in 1-D with a zero gradient at the border); `nearest` = nearest measured
    index (the smaller one on a tie)."""
    idx = np.flatnonzero(measured)
    all_i = np.arange(len(value))
    if fill == "smooth":
        return np.interp(all_i, idx, value[idx])
    pos = np.searchsorted(idx, all_i)  # first measured index >= i
    left = idx[np.clip(pos - 1, 0, len(idx) - 1)]
    right = idx[np.clip(pos, 0, len(idx) - 1)]
    pick_left = (all_i - left) <= (right - all_i)  # tie -> the smaller index
    return value[np.where(pick_left, left, right)]


def _laplacian(f: np.ndarray, out: np.ndarray) -> np.ndarray:
    """Graph Laplacian of the grid with zero gradient at the border: `out[i] = sum over existing
    neighbours j of (f[i] - f[j])` (5-point stencil in index space)."""
    out[:] = 0.0
    d = f[:, :-1] - f[:, 1:]
    out[:, :-1] += d
    out[:, 1:] -= d
    d = f[:-1, :] - f[1:, :]
    out[:-1, :] += d
    out[1:, :] -= d
    return out


def _fill_smooth_2d(value: np.ndarray, measured: np.ndarray, cancelled) -> np.ndarray:
    """Solve the Laplace equation on the empty cells with the measured cells fixed (conjugate
    gradient on the SPD system restricted to the empty cells, matrix-free). Stops at a relative
    residual of 1e-8 or after `4*(nx+ny)` iterations."""
    ny, nx = value.shape
    unknown = ~measured
    fixed = np.where(measured, value, 0.0)
    tmp = np.empty_like(fixed)
    # system A u = b with A = (Laplacian restricted to the unknowns), b = -(Laplacian of the fixed part)
    b = -_laplacian(fixed, tmp).copy()
    b[measured] = 0.0
    norm_b = float(np.sqrt(np.dot(b.ravel(), b.ravel())))
    x = np.zeros_like(fixed)
    x[unknown] = float(fixed[measured].mean())  # a flat first guess
    if norm_b == 0.0:
        return x + fixed
    r = b - _apply(x, unknown, tmp)
    p = r.copy()
    rs = float(np.dot(r.ravel(), r.ravel()))
    max_iter = 4 * (nx + ny)
    tol2 = (1e-8 * norm_b) ** 2
    ap = np.empty_like(fixed)
    for it in range(1, max_iter + 1):
        if rs <= tol2:
            break
        if it % _CG_CHECK_EVERY == 0:
            _poll(cancelled)
        ap = _apply(p, unknown, ap)
        denom = float(np.dot(p.ravel(), ap.ravel()))
        if denom <= 0.0:
            break
        alpha = rs / denom
        x += alpha * p
        r -= alpha * ap
        rs_new = float(np.dot(r.ravel(), r.ravel()))
        p *= rs_new / rs
        p += r
        rs = rs_new
    return x + fixed


def _apply(v: np.ndarray, unknown: np.ndarray, out: np.ndarray) -> np.ndarray:
    """A v: the Laplacian of `v` (zero on the measured cells) restricted to the unknown cells."""
    _laplacian(v, out)
    out[~unknown] = 0.0
    return out


def _fill_nearest_2d(value: np.ndarray, measured: np.ndarray, cancelled) -> np.ndarray:
    """Each empty cell takes the value of the nearest measured cell (Euclidean distance in index
    space = grid-width units; the grid is equally spaced). A tie goes to the smaller flat index.
    Processed in blocks so the pair matrix stays small."""
    ny, nx = value.shape
    my, mx = np.nonzero(measured)  # row-major order: ascending flat index
    ey, ex = np.nonzero(~measured)
    mvals = value[my, mx]
    my32, mx32 = my.astype(np.int32), mx.astype(np.int32)
    block = max(1, _NEAREST_BLOCK_ELEMENTS // max(len(my), 1))
    out = np.full(value.shape, np.nan)
    for a in range(0, len(ey), block):
        _poll(cancelled)
        by = ey[a : a + block].astype(np.int32)
        bx = ex[a : a + block].astype(np.int32)
        d2 = (by[:, None] - my32[None, :]) ** 2 + (bx[:, None] - mx32[None, :]) ** 2
        out[ey[a : a + block], ex[a : a + block]] = mvals[np.argmin(d2, axis=1)]  # first minimum = smallest index
    return out
