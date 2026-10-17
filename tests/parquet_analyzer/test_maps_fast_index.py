"""Uniform-axis fast path of the lookup-table index search (expressions.ja.md 55.8).

The reference below is the previous implementation (`np.searchsorted` everywhere); the fast path
must give bit-identical indices and interpolated values.
"""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from parquet_analyzer.core import maps
from parquet_analyzer.core.maps import INTERPOLATIONS, LookupTable

NAN, INF = float("nan"), float("inf")
_SET = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# ---------------------------------------------------------------- reference (old) implementation


def old_cell(grid, q):
    return np.clip(np.searchsorted(grid, q, side="right") - 1, 0, len(grid) - 2)


def old_prev(grid, q):
    return np.clip(np.searchsorted(grid, q, side="right") - 1, 0, len(grid) - 1)


def old_nearest(grid, q):
    q = np.clip(q, grid[0], grid[-1])
    hi = np.clip(np.searchsorted(grid, q, side="left"), 1, len(grid) - 1)
    lo = hi - 1
    return np.where(q - grid[lo] < grid[hi] - q, lo, hi)


class OldIndex:
    """Swap the three index helpers for the old ones (ignoring the step argument)."""

    def __enter__(self):
        self._saved = (maps._cell, maps._prev_index, maps._nearest_index)
        maps._cell = lambda g, q, dx=None: old_cell(g, q)
        maps._prev_index = lambda g, q, dx=None: old_prev(g, q)
        maps._nearest_index = lambda g, q, dx=None: old_nearest(g, q)

    def __exit__(self, *a):
        maps._cell, maps._prev_index, maps._nearest_index = self._saved


def same(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return a.shape == b.shape and np.array_equal(a.view(np.int64), b.view(np.int64))


# ---------------------------------------------------------------- strategies

_finite = st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False)


@st.composite
def grids(draw, kind):
    n = draw(st.integers(1 if kind != "uniform" else 2, 40))
    if kind == "uniform":
        x0 = draw(st.floats(-1e4, 1e4))
        dx = draw(st.sampled_from([1.0, 0.1, 0.01, 0.3, 1e-3, 7.0, 250.0, 1 / 3]))
        return x0 + dx * np.arange(n)
    if kind == "nearly":  # linspace-like with rounding noise well below 1e-9
        x0 = draw(st.floats(-1e4, 1e4))
        dx = draw(st.sampled_from([0.1, 0.01, 0.3, 1 / 3, 7.0]))
        g = np.sort(x0 + dx * np.arange(n) * (1 + 1e-13 * draw(st.floats(-1, 1))))
        return g + np.array([draw(st.integers(-2, 2)) * np.spacing(abs(v)) for v in g])
    steps = draw(st.lists(st.floats(0.01, 50), min_size=n, max_size=n))
    return draw(_finite) + np.cumsum(steps)


@st.composite
def queries(draw, grid):
    lo, hi = grid[0], grid[-1]
    span = max(hi - lo, 1.0)
    specials = [NAN, INF, -INF, lo, hi, lo - span, hi + span, lo - 1e-12, hi + 1e-12]
    pts = list(grid) + [np.nextafter(g, INF) for g in grid] + [np.nextafter(g, -INF) for g in grid]
    mids = [(a + b) / 2 for a, b in zip(grid[:-1], grid[1:])]
    rnd = draw(st.lists(st.floats(float(lo - span), float(hi + span)), max_size=20))
    return np.array(specials + pts + mids + rnd)


kinds = pytest.mark.parametrize("kind", ["uniform", "nearly", "random"])


# ---------------------------------------------------------------- indices


@kinds
@_SET
@given(data=st.data())
def test_indices_identical(kind, data):
    g = data.draw(grids(kind))
    q = data.draw(queries(g))
    dx = maps._uniform_step(g)
    if kind == "uniform":
        assert dx is not None
    if len(g) < 2:
        assert dx is None
        return
    assert same(maps._cell(g, q, dx), old_cell(g, q))
    assert same(maps._prev_index(g, q, dx), old_prev(g, q))
    assert same(maps._nearest_index(g, q, dx), old_nearest(g, q))


def test_scalar_and_special_queries():
    g = np.arange(5.0)
    dx = maps._uniform_step(g)
    for v in (NAN, INF, -INF, 0.0, 4.0, 2.0, 1e300, -1e300):
        assert same(maps._cell(g, np.float64(v), dx), old_cell(g, np.float64(v)))
        assert same(maps._nearest_index(g, np.float64(v), dx), old_nearest(g, np.float64(v)))


def test_no_warnings_on_nonfinite():
    g = np.linspace(0, 1, 11)
    q = np.array([NAN, INF, -INF, 1e308, -1e308])
    with np.errstate(all="raise"):
        maps._cell(g, q, maps._uniform_step(g))
        maps._nearest_index(g, q, maps._uniform_step(g))


# ---------------------------------------------------------------- fast path detection


def test_uniform_detection():
    assert maps._uniform_step(np.linspace(0, 1, 11)) == pytest.approx(0.1)
    assert maps._uniform_step(np.array([0.0, 1.0])) == 1.0
    assert maps._uniform_step(np.array([1.0])) is None
    assert maps._uniform_step(np.array([])) is None
    assert maps._uniform_step(np.array([0.0, 1.0, 2.0, 4.0])) is None
    assert maps._uniform_step(np.array([0.0, 1.0, 2.0 + 1e-6])) is None
    assert maps._uniform_step(np.array([0.0, 1.0, 2.0 + 1e-12])) is not None


def test_table_flags():
    c = LookupTable("c", "curve", [0, 1, 2, 3], [0, 1, 4, 9])
    assert c.dx == 1.0 and c.dy is None  # a curve's y is values, not an axis
    c2 = LookupTable("c", "curve", [0, 1, 2, 4], [0, 1, 4, 9])
    assert c2.dx is None
    m = LookupTable("m", "map", [0, 1, 2], [0, 5, 10, 20], np.zeros((4, 3)))
    assert m.dx == 1.0 and m.dy is None


def test_nonuniform_uses_searchsorted_and_uniform_does_not(monkeypatch):
    calls = []
    real = np.searchsorted
    monkeypatch.setattr(np, "searchsorted", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    uni = LookupTable("u", "curve", [0, 1, 2, 3], [0, 1, 4, 9])
    uni([0.5, 2.5])
    assert not calls
    non = LookupTable("n", "curve", [0, 1, 2, 4], [0, 1, 4, 9])
    non([0.5, 2.5])
    assert calls


# ---------------------------------------------------------------- interpolated values

_MODES = [(i, k) for i, s in INTERPOLATIONS.items() for k in s.kinds]
_EXTRAP = ["clamp", "zero", "linear", "nan"]


def _tables(kind, interp, extrap, gx, gy, rng_seed):
    rng = np.random.default_rng(rng_seed)
    if kind == "curve":
        return LookupTable("t", "curve", gx, rng.normal(size=len(gx)), None, interp, extrap)
    z = rng.normal(size=(len(gy), len(gx)))
    return LookupTable("t", "map", gx, gy, z, interp, extrap)


@pytest.mark.parametrize("extrap", _EXTRAP)
@pytest.mark.parametrize("interp,kind", _MODES, ids=[f"{i}-{k}" for i, k in _MODES])
@pytest.mark.parametrize("gkind", ["uniform", "nearly", "random"])
@_SET
@given(data=st.data())
def test_values_identical(gkind, interp, kind, extrap, data):
    gx, gy = data.draw(grids(gkind)), data.draw(grids(gkind))
    if len(gx) < 2 or len(gy) < 2:
        return
    t = _tables(kind, interp, extrap, gx, gy, data.draw(st.integers(0, 10)))
    qx, qy = data.draw(queries(gx)), data.draw(queries(gy))
    n = min(len(qx), len(qy))
    args = (qx[:n],) if kind == "curve" else (qx[:n], qy[:n])
    new = t(*args)
    with OldIndex():
        old = t(*args)
    assert same(new, old)


def test_values_identical_with_empty_cells():
    z = np.arange(20.0).reshape(4, 5)
    z[1, 2] = NAN
    t = LookupTable("m", "map", np.arange(5.0), np.arange(4.0), z)
    q = np.linspace(-1, 5, 301)
    new = t(q, q[::-1] * 0.7)
    with OldIndex():
        old = t(q, q[::-1] * 0.7)
    assert same(new, old)


def test_fast_path_is_what_the_kernels_use(monkeypatch):
    """Regression: with the step forced off the kernels still agree (searchsorted route)."""
    t = LookupTable("c", "curve", np.linspace(0, 10, 101), np.sin(np.linspace(0, 10, 101)))
    q = np.linspace(-1, 11, 5000)
    fast = t(q)
    t.dx = None
    assert same(fast, t(q))
