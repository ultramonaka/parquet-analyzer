"""Property tests for core/analysis.py (app 46.2, A1): chunked_stats vs basic_stats, and the
centered rolling means vs a naive per-point implementation (expressions.ja.md 31.1)."""
from __future__ import annotations

import math
import os

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.analysis import (
    basic_stats,
    chunked_stats,
    rolling_mean,
    rolling_nanmean,
    trailing_mean,
    trailing_nanmean,
)


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


# --- chunked_stats == basic_stats ----------------------------------------------------------------

_KINDS = ["uniform", "constant", "almost_constant", "huge", "denormal", "with_nonfinite", "all_nonfinite", "ints"]


def _values(seed: int, n: int, kind: str) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "uniform":
        y = rng.normal(0.0, 100.0, n)
    elif kind == "constant":
        y = np.full(n, rng.normal())
    elif kind == "almost_constant":
        y = 5.0 + rng.integers(0, 2, n) * 1e-9
    elif kind == "huge":
        y = rng.choice([-1.0, 1.0], n) * rng.uniform(0.5, 1.0, n) * 1.7e308
    elif kind == "denormal":
        y = rng.integers(-5, 6, n) * 5e-324
    elif kind == "with_nonfinite":
        y = rng.normal(0.0, 10.0, n)
        mask = rng.random(n) < 0.3
        y[mask] = rng.choice([np.nan, np.inf, -np.inf], int(mask.sum()))
    elif kind == "all_nonfinite":
        y = rng.choice([np.nan, np.inf, -np.inf], n)
    else:  # few distinct integers: many ties around the median
        y = rng.integers(-3, 4, n).astype(np.float64)
    return y.astype(np.float64)


def _same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


_SHAPE = dict(
    seed=st.integers(0, 2**32 - 1),
    n=st.one_of(st.integers(0, 60), st.integers(0, 50_000)),
    chunk_rows=st.one_of(st.integers(1, 17), st.integers(1, 60_000)),
    bins=st.one_of(st.integers(2, 8), st.integers(2, 1 << 16)),
    lo_frac=st.floats(0, 1),
    hi_frac=st.floats(0, 1),
)


@settings(max_examples=_examples(200))
@given(kind=st.sampled_from([k for k in _KINDS if k != "huge"]), **_SHAPE)
def test_chunked_stats_matches_basic_stats(seed, n, kind, chunk_rows, bins, lo_frac, hi_frac):
    _check_chunked_stats(seed, n, kind, chunk_rows, bins, lo_frac, hi_frac)


# A1 finding: with values near +-1.7e308 chunked_stats' moment merge overflows (std is NaN even for a
# single value: d*d*nfin*k/tot = inf*0) and mean/std differ from basic_stats, which overflows
# differently itself (mean inf). Minimal example: y=[1.7e308], chunk_rows=1 -> std nan, basic_stats 0.0.
@pytest.mark.xfail(strict=True, reason="A1 finding: chunked_stats mean/std overflow near +-1e308")
@settings(max_examples=_examples(100))
@given(**_SHAPE)
def test_chunked_stats_matches_basic_stats_huge_values(seed, n, chunk_rows, bins, lo_frac, hi_frac):
    _check_chunked_stats(seed, n, "huge", chunk_rows, bins, lo_frac, hi_frac)


def _check_chunked_stats(seed, n, kind, chunk_rows, bins, lo_frac, hi_frac):
    y = _values(seed, n, kind)
    lo, hi = sorted((int(lo_frac * n), int(hi_frac * n)))
    if lo_frac == hi_frac:
        hi = lo  # lo == hi: empty range
    # many tiny chunks over a long range make the pure-Python merge loop slow; keep the chunk count modest
    chunk_rows = max(chunk_rows, (hi - lo) // 400 + 1)
    with np.errstate(all="ignore"):
        want = basic_stats(y[lo:hi])
        got = chunked_stats(lambda a, b: y[a:b], lo, hi, chunk_rows, bins)
    ctx = (kind, n, lo, hi, chunk_rows, bins, got, want)
    for key in ("count", "nan_count"):
        assert got[key] == want[key], ctx
    for key in ("min", "max", "median"):
        assert _same(got[key], want[key]), (key, ctx)
    finite = y[lo:hi][np.isfinite(y[lo:hi])]
    scale = float(np.abs(finite).max()) if len(finite) else 0.0
    for key in ("mean", "std"):
        a, b = got[key], want[key]
        if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
            assert _same(a, b), (key, ctx)
        else:
            assert abs(a - b) <= 1e-9 * abs(b) + 1e-12 * scale + 1e-322, (key, ctx)  # 1e-322 = a few denormal steps


# --- rolling_mean / rolling_nanmean --------------------------------------------------------------

def _naive_rolling(y: np.ndarray, window: int, ignore_nan: bool) -> np.ndarray:
    """Per-point slice and mean; the window position is expressions.ja.md 31.1."""
    n = len(y)
    w = min(window, n)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = np.asarray(y[max(0, i - w // 2) : min(n, i + (w + 1) // 2)], dtype=np.float64)
        if ignore_nan:
            seg = seg[~np.isnan(seg)]
            out[i] = seg.mean() if len(seg) else np.nan
        else:
            out[i] = np.nan if np.isnan(seg).any() else seg.mean()
    return out


@st.composite
def _rolling_case(draw):
    n = draw(st.integers(0, 300))
    seed = draw(st.integers(0, 2**32 - 1))
    rng = np.random.default_rng(seed)
    integer = draw(st.booleans())
    if integer:
        y = rng.integers(-1_000_000, 1_000_000, n)
    else:
        y = rng.uniform(-1e6, 1e6, n)
        p = draw(st.sampled_from([0.0, 0.05, 0.5, 0.95, 1.0]))
        y[rng.random(n) < p] = np.nan
    window = draw(st.integers(1, n + 5))
    return y, window


@settings(max_examples=_examples(200))
@given(_rolling_case())
def test_rolling_mean_matches_naive(case):
    y, window = case
    np.testing.assert_allclose(rolling_mean(y, window), _naive_rolling(y, window, False),
                               rtol=1e-9, atol=1e-12, equal_nan=True)


@settings(max_examples=_examples(200))
@given(_rolling_case())
def test_rolling_nanmean_matches_naive(case):
    y, window = case
    np.testing.assert_allclose(rolling_nanmean(y, window), _naive_rolling(y, window, True),
                               rtol=1e-9, atol=1e-12, equal_nan=True)


# --- trailing_mean / trailing_nanmean ---------------------------------------------------------------

def _naive_trailing(y: np.ndarray, window: int, ignore_nan: bool) -> np.ndarray:
    """Per-point slice and mean; the window position is expressions.ja.md 31.2 (past only)."""
    n = len(y)
    w = min(window, n)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = np.asarray(y[max(0, i - w + 1) : i + 1], dtype=np.float64)
        if ignore_nan:
            seg = seg[~np.isnan(seg)]
            out[i] = seg.mean() if len(seg) else np.nan
        else:
            out[i] = np.nan if np.isnan(seg).any() else seg.mean()
    return out


@st.composite
def _trailing_case(draw):
    n = draw(st.integers(0, 300))
    seed = draw(st.integers(0, 2**32 - 1))
    rng = np.random.default_rng(seed)
    integer = draw(st.booleans())
    if integer:
        y = rng.integers(-1_000_000, 1_000_000, n)
    else:
        y = rng.uniform(-1e6, 1e6, n)
        p = draw(st.sampled_from([0.0, 0.05, 0.5, 0.95, 1.0]))
        y[rng.random(n) < p] = np.nan
        if draw(st.booleans()):  # a few +-Inf: they must reach only the windows holding them
            y[rng.random(n) < 0.02] = np.inf
            y[rng.random(n) < 0.02] = -np.inf
    window = draw(st.integers(1, n + 5))
    return y, window


@settings(max_examples=_examples(200))
@given(_trailing_case())
def test_trailing_mean_matches_naive(case):
    y, window = case
    np.testing.assert_allclose(trailing_mean(y, window), _naive_trailing(y, window, False),
                               rtol=1e-9, atol=1e-12, equal_nan=True)


@settings(max_examples=_examples(200))
@given(_trailing_case())
def test_trailing_nanmean_matches_naive(case):
    y, window = case
    np.testing.assert_allclose(trailing_nanmean(y, window), _naive_trailing(y, window, True),
                               rtol=1e-9, atol=1e-12, equal_nan=True)
