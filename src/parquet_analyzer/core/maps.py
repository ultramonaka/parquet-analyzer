"""Lookup tables (curves / maps) callable from expressions (detailed_specification.md 27.1).

Pure numpy, no scipy (the natural cubic spline is a tridiagonal solve written here).
Interpolation and extrapolation are name -> spec registries: adding a behaviour means
adding one entry to `INTERPOLATIONS` / `EXTRAPOLATIONS`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- spline helpers


def natural_spline_second_derivatives(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Second derivatives M at the knots of the natural cubic spline (M[0] = M[-1] = 0)."""
    n = len(x)
    m = np.zeros(n)
    if n < 3:
        return m
    h = np.diff(x)
    # tridiagonal system for the interior knots (Thomas algorithm)
    sub = h[:-1].copy()
    diag = 2.0 * (h[:-1] + h[1:])
    sup = h[1:].copy()
    rhs = 6.0 * (np.diff(y)[1:] / h[1:] - np.diff(y)[:-1] / h[:-1])
    k = n - 2
    for i in range(1, k):
        w = sub[i] / diag[i - 1]
        diag[i] -= w * sup[i - 1]
        rhs[i] -= w * rhs[i - 1]
    sol = np.zeros(k)
    sol[-1] = rhs[-1] / diag[-1]
    for i in range(k - 2, -1, -1):
        sol[i] = (rhs[i] - sup[i] * sol[i + 1]) / diag[i]
    m[1:-1] = sol
    return m


# ---------------------------------------------------------------- index helpers


def _uniform_step(grid: np.ndarray) -> float | None:
    """The step of a uniformly spaced axis (every diff within relative 1e-9 of it), else None.

    Such an axis finds its cell by arithmetic instead of `searchsorted` (expressions 55.8)."""
    n = len(grid)
    if n < 2 or not np.isfinite(grid).all():
        return None
    dx = (grid[-1] - grid[0]) / (n - 1)
    if not (np.isfinite(dx) and dx > 0):
        return None
    if np.all(np.abs(np.diff(grid) - dx) <= 1e-9 * dx):
        return float(dx)
    return None


def _count_right(grid: np.ndarray, dx: float | None, q: np.ndarray) -> np.ndarray:
    """`np.searchsorted(grid, q, side="right")`; for a uniform axis (`dx` given) computed
    from the step and corrected by one cell, so the result is identical (NaN -> len(grid))."""
    if dx is None:
        return np.searchsorted(grid, q, side="right")
    n = len(grid)
    q = np.asarray(q, dtype=np.float64)
    nan = np.isnan(q)
    f = np.floor((np.clip(np.where(nan, 0.0, q), grid[0] - dx, grid[0] + n * dx) - grid[0]) / dx)
    c = np.clip(f, -1, n - 1).astype(np.intp) + 1  # candidate count of grid points <= q
    # too high: grid[c-1] > q; too low: grid[c] <= q
    c = c - ((c > 0) & (grid[np.maximum(c - 1, 0)] > q))
    c = c + ((c < n) & (grid[np.minimum(c, n - 1)] <= q))
    return np.where(nan, n, c)


def _count_left(grid: np.ndarray, dx: float | None, q: np.ndarray) -> np.ndarray:
    """`np.searchsorted(grid, q, side="left")` by the same means (NaN -> len(grid))."""
    if dx is None:
        return np.searchsorted(grid, q, side="left")
    n = len(grid)
    q = np.asarray(q, dtype=np.float64)
    nan = np.isnan(q)
    f = np.ceil((np.clip(np.where(nan, 0.0, q), grid[0] - dx, grid[0] + n * dx) - grid[0]) / dx)
    c = np.clip(f, 0, n).astype(np.intp)  # candidate count of grid points < q
    c = c - ((c > 0) & (grid[np.maximum(c - 1, 0)] >= q))
    c = c + ((c < n) & (grid[np.minimum(c, n - 1)] < q))
    return np.where(nan, n, c)


def _cell(grid: np.ndarray, q: np.ndarray, dx: float | None = None) -> np.ndarray:
    return np.clip(_count_right(grid, dx, q) - 1, 0, len(grid) - 2)


def _prev_index(grid: np.ndarray, q: np.ndarray, dx: float | None = None) -> np.ndarray:
    return np.clip(_count_right(grid, dx, q) - 1, 0, len(grid) - 1)


def _nearest_index(grid: np.ndarray, q: np.ndarray, dx: float | None = None) -> np.ndarray:
    q = np.clip(q, grid[0], grid[-1])
    hi = np.clip(_count_left(grid, dx, q), 1, len(grid) - 1)
    lo = hi - 1
    # a tie goes to the upper grid point
    return np.where(q - grid[lo] < grid[hi] - q, lo, hi)


# ---------------------------------------------------------------- 1-D kernels
# Each takes (table, query, extend). `query` is already clipped into the axis range unless
# `extend` (linear extrapolation requested); kernels that cannot extend just give the edge value.


def _curve_linear(t: LookupTable, q: np.ndarray, extend: bool) -> np.ndarray:
    x, y = t.x, t.y
    i = _cell(x, q, t.dx)
    f = (q - x[i]) / (x[i + 1] - x[i])
    r = y[i] + f * (y[i + 1] - y[i])
    if t.has_empty:
        # Empty (NaN) grid values (27.10): a point exactly on a grid point is that point's value
        # (NaN if empty); between two points NaN propagates through the arithmetic above.
        # Only tables with empty cells take this path, so every other table is bit-identical.
        r = np.where(f == 0, y[i], np.where(f == 1, y[i + 1], r))
    return r


def _curve_nearest(t: LookupTable, q: np.ndarray, extend: bool) -> np.ndarray:
    return t.y[_nearest_index(t.x, q, t.dx)]


def _curve_previous(t: LookupTable, q: np.ndarray, extend: bool) -> np.ndarray:
    return t.y[_prev_index(t.x, q, t.dx)]


def _curve_spline(t: LookupTable, q: np.ndarray, extend: bool) -> np.ndarray:
    x, y = t.x, t.y
    if len(x) < 3:
        return _curve_linear(t, q, extend)
    m = t._spline_m()
    qc = np.clip(q, x[0], x[-1])
    i = _cell(x, qc, t.dx)
    h = x[i + 1] - x[i]
    a = (x[i + 1] - qc) / h
    b = (qc - x[i]) / h
    r = a * y[i] + b * y[i + 1] + ((a**3 - a) * m[i] + (b**3 - b) * m[i + 1]) * h * h / 6.0
    if extend:  # continue along the end tangent, so the extension is C1 (and C2) at the edge
        h0, h1 = x[1] - x[0], x[-1] - x[-2]
        s0 = (y[1] - y[0]) / h0 - h0 * m[1] / 6.0
        s1 = (y[-1] - y[-2]) / h1 + h1 * m[-2] / 6.0
        r = np.where(q < x[0], y[0] + (q - x[0]) * s0, r)
        r = np.where(q > x[-1], y[-1] + (q - x[-1]) * s1, r)
    return r


# ---------------------------------------------------------------- 2-D kernels


def _map_linear(t: LookupTable, qx: np.ndarray, qy: np.ndarray, extend: bool) -> np.ndarray:
    x, y, z = t.x, t.y, t.z
    ix, iy = _cell(x, qx, t.dx), _cell(y, qy, t.dy)
    fx = (qx - x[ix]) / (x[ix + 1] - x[ix])
    fy = (qy - y[iy]) / (y[iy + 1] - y[iy])
    if not t.has_empty:
        return (
            z[iy, ix] * (1 - fx) * (1 - fy)
            + z[iy, ix + 1] * fx * (1 - fy)
            + z[iy + 1, ix] * (1 - fx) * fy
            + z[iy + 1, ix + 1] * fx * fy
        )
    # Empty (NaN) cells (27.10): a grid point with weight 0 does not take part, so only the
    # corners that really contribute can make the result empty. Same summation order as above.
    out = 0.0
    for z_c, wx, wy in (
        (z[iy, ix], 1 - fx, 1 - fy),
        (z[iy, ix + 1], fx, 1 - fy),
        (z[iy + 1, ix], 1 - fx, fy),
        (z[iy + 1, ix + 1], fx, fy),
    ):
        out = out + np.where((wx == 0) | (wy == 0), 0.0, z_c * wx * wy)
    return out


def _map_nearest(t: LookupTable, qx: np.ndarray, qy: np.ndarray, extend: bool) -> np.ndarray:
    return t.z[_nearest_index(t.y, qy, t.dy), _nearest_index(t.x, qx, t.dx)]


def _map_previous(t: LookupTable, qx: np.ndarray, qy: np.ndarray, extend: bool) -> np.ndarray:
    return t.z[_prev_index(t.y, qy, t.dy), _prev_index(t.x, qx, t.dx)]


@dataclass(frozen=True)
class InterpolationSpec:
    name: str
    kinds: tuple[str, ...]  # which of "curve" / "map" it supports
    curve: Callable | None = None
    map: Callable | None = None


INTERPOLATIONS: dict[str, InterpolationSpec] = {
    "linear": InterpolationSpec("linear", ("curve", "map"), _curve_linear, _map_linear),
    "nearest": InterpolationSpec("nearest", ("curve", "map"), _curve_nearest, _map_nearest),
    "previous": InterpolationSpec("previous", ("curve", "map"), _curve_previous, _map_previous),
    "spline": InterpolationSpec("spline", ("curve",), _curve_spline, None),
}


@dataclass(frozen=True)
class ExtrapolationSpec:
    """How a query outside an axis' range is treated (applied per axis).

    extend: let the interpolation continue along the end slope (linear / spline; nearest
        and previous have nothing to extend and give the edge value). Otherwise the query
        is clipped into the axis range first, i.e. the edge value is returned.
    fill: if not None, the result is replaced by this value when any axis is out of range.
    """

    name: str
    extend: bool = False
    fill: float | None = None


EXTRAPOLATIONS: dict[str, ExtrapolationSpec] = {
    "clamp": ExtrapolationSpec("clamp"),
    "zero": ExtrapolationSpec("zero", fill=0.0),
    "linear": ExtrapolationSpec("linear", extend=True),
    "nan": ExtrapolationSpec("nan", fill=float("nan")),
}


DEFAULT_AXIS_NAMES = ("X", "Y", "Z")


def normalize_axis_names(names) -> tuple[str, str, str]:
    """Axis display names (27.7): each blank/missing entry falls back to "X" / "Y" / "Z"."""
    names = list(names or ())[:3]
    names += [""] * (3 - len(names))
    return tuple((str(n).strip() or d) for n, d in zip(names, DEFAULT_AXIS_NAMES))  # type: ignore[return-value]


@dataclass
class LookupTable:
    """A curve `name(x)` or map `name(x, y)` (27.1). Axes must be strictly ascending."""

    name: str
    kind: str  # "curve" | "map"
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray | None = None
    interpolation: str = "linear"
    extrapolation: str = "clamp"
    # display names of the X / Y / Z axes (27.7); empty -> "X" / "Y" / "Z". Presentation only.
    axis_names: tuple[str, str, str] = ("X", "Y", "Z")

    def __post_init__(self):
        self.axis_names = normalize_axis_names(self.axis_names)
        if self.kind not in ("curve", "map"):
            raise ValueError(f"unknown table kind: {self.kind}")
        self.x = np.asarray(self.x, dtype=np.float64)
        self.y = np.asarray(self.y, dtype=np.float64)
        if self.kind == "map":
            self.z = np.asarray(self.z, dtype=np.float64)
            if self.z.shape != (len(self.y), len(self.x)):
                raise ValueError(f"z must have shape {(len(self.y), len(self.x))}, got {self.z.shape}")
        elif len(self.x) != len(self.y):
            raise ValueError("curve x and y must have the same length")
        if self.interpolation not in INTERPOLATIONS:
            raise ValueError(f"unknown interpolation: {self.interpolation}")
        if self.kind not in INTERPOLATIONS[self.interpolation].kinds:
            raise ValueError(f"interpolation {self.interpolation} is not available for a {self.kind}")
        if self.interpolation == "spline" and self.has_empty:
            raise ValueError(f"{self.name}: spline cannot be used with empty cells; use linear")
        if self.extrapolation not in EXTRAPOLATIONS:
            raise ValueError(f"unknown extrapolation: {self.extrapolation}")
        self._m: np.ndarray | None = None
        # steps of uniformly spaced axes (None: searchsorted); the curve's y is values, not an axis
        self.dx = _uniform_step(self.x)
        self.dy = _uniform_step(self.y) if self.kind == "map" else None

    @property
    def values(self) -> np.ndarray:
        """The table's value array: Z of a map, Y of a curve."""
        return self.z if self.kind == "map" else self.y

    @property
    def has_empty(self) -> bool:
        """Whether any value is empty (NaN, 27.10). Checked per call: tables are small
        (<= 500x500) and edits build new tables."""
        return bool(np.isnan(self.values).any())

    @property
    def n_empty(self) -> int:
        return int(np.isnan(self.values).sum())

    @property
    def arity(self) -> int:
        return 1 if self.kind == "curve" else 2

    def warnings(self) -> list[str]:
        """Import-time notes, e.g. spline with fewer than 3 points falling back to linear."""
        out = []
        if self.interpolation == "spline" and len(self.x) < 3:
            out.append(f"{self.name}: spline needs at least 3 points; using linear")
        n = self.n_empty
        if n:
            out.append(f"{self.name}: {n} empty cell(s); values falling on them become NaN")
        return out

    def _spline_m(self) -> np.ndarray:
        if self._m is None:
            self._m = natural_spline_second_derivatives(self.x, self.y)
        return self._m

    def __call__(self, *args):
        """Evaluate at array/scalar arguments (NaN in -> NaN out).

        +-inf is not special-cased: with clamp/zero/nan extrapolation it is simply out of
        range; with `linear` it yields +-inf (or NaN if the end slope is 0), never an exception.
        """
        with np.errstate(invalid="ignore", over="ignore"):
            return self._call(args)

    def _call(self, args):
        if len(args) != self.arity:
            raise TypeError(f"{self.name}() expects {self.arity} argument(s), got {len(args)}")
        arrs = np.broadcast_arrays(*[np.asarray(a, dtype=np.float64) for a in args])
        shape = arrs[0].shape
        ext = EXTRAPOLATIONS[self.extrapolation]
        nan_in = np.zeros(shape, dtype=bool)
        out = np.zeros(shape, dtype=bool)
        qs = []
        for a, grid in zip(arrs, (self.x, self.y)):
            bad = np.isnan(a)
            nan_in |= bad
            a = np.where(bad, grid[0], a)  # placeholder; result forced to NaN below
            out |= (a < grid[0]) | (a > grid[-1])
            qs.append(a if ext.extend else np.clip(a, grid[0], grid[-1]))
        spec = INTERPOLATIONS[self.interpolation]
        if self.kind == "curve":
            r = spec.curve(self, qs[0], ext.extend)
        else:
            r = spec.map(self, qs[0], qs[1], ext.extend)
        r = np.asarray(r, dtype=np.float64)
        if ext.fill is not None:
            r = np.where(out, ext.fill, r)
        r = np.where(nan_in, np.nan, r)
        return r[()] if r.ndim == 0 else r
