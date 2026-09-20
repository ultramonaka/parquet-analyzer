from __future__ import annotations

import time

import numpy as np

from parquet_analyzer.core.downsample import lttb


def test_reduces_to_requested_size():
    x = np.arange(10_000, dtype=float)
    y = np.sin(x / 50)
    xd, yd = lttb(x, y, 500)
    assert len(xd) == 500
    assert len(yd) == 500


def test_preserves_endpoints():
    x = np.arange(1000, dtype=float)
    y = np.random.default_rng(0).normal(size=1000)
    xd, yd = lttb(x, y, 100)
    assert xd[0] == x[0] and xd[-1] == x[-1]
    assert yd[0] == y[0] and yd[-1] == y[-1]


def test_x_stays_monotonic():
    x = np.arange(5000, dtype=float)
    y = np.random.default_rng(1).normal(size=5000)
    xd, _ = lttb(x, y, 321)
    assert np.all(np.diff(xd) >= 0)


def test_noop_when_n_out_covers_all_points():
    x = np.arange(10, dtype=float)
    y = np.arange(10, dtype=float)
    xd, yd = lttb(x, y, 20)
    np.testing.assert_array_equal(xd, x)
    np.testing.assert_array_equal(yd, y)


def test_noop_when_n_out_at_or_below_two():
    x = np.arange(10, dtype=float)
    y = np.arange(10, dtype=float)
    xd, yd = lttb(x, y, 2)
    np.testing.assert_array_equal(xd, x)
    np.testing.assert_array_equal(yd, y)


def test_small_and_uneven_bucket_sizes_do_not_crash():
    # n_out close to n forces bucket_size < 1, previously an edge case worth pinning down.
    for n, n_out in [(10, 3), (10, 9), (7, 6), (13, 12)]:
        x = np.arange(n, dtype=float)
        y = np.random.default_rng(n).normal(size=n)
        xd, yd = lttb(x, y, n_out)
        assert len(xd) == len(yd) == n_out


def test_a_single_nan_does_not_blank_a_nearby_real_peak():
    """Regression test: a plain (non-nan-aware) mean for the look-ahead window made
    the whole bucket's candidate area NaN whenever any sample nearby was NaN, so
    np.argmax effectively degenerated to "always pick the bucket's first point" —
    discarding a real, larger peak/trough elsewhere in that bucket.
    """
    n = 2000
    x = np.arange(n, dtype=float)
    y = np.zeros(n)
    y[1000] = 100.0  # a real, large spike
    y[990] = np.nan  # an unrelated missing sample nearby

    with np.errstate(invalid="ignore"):
        xd, yd = lttb(x, y, 40)

    assert 100.0 in yd, "the real spike must survive downsampling despite a nearby NaN"
    assert not np.isnan(yd).any(), "NaN input must not leak a NaN sample into the output"


def test_all_nan_series_does_not_crash():
    x = np.arange(1000, dtype=float)
    y = np.full(1000, np.nan)
    with np.errstate(invalid="ignore"):
        xd, yd = lttb(x, y, 40)
    assert len(xd) == len(yd) == 40


def test_finite_only_data_is_not_slowed_down_by_nan_safety():
    """Regression test: the NaN-safety fix (nanmean, nan_to_num) originally ran
    unconditionally on every bucket regardless of whether the data actually had any
    NaN/Inf, using np.nanmean (~5x slower than np.mean) and re-entering
    warnings.catch_warnings() every iteration — turning a real redraw at the app's
    ~13M-row target scale from ~40ms into 100ms+. Finite-only data (the common case)
    must run close to the original speed; a generous threshold keeps this from being
    flaky on slower CI machines while still catching a real regression.
    """
    n = 3_000_000
    x = np.linspace(0.0, 1000.0, n)
    y = np.sin(x)

    # warm up (first call pays one-time interpreter/cache costs)
    lttb(x, y, 2400)

    t0 = time.perf_counter()
    for _ in range(3):
        lttb(x, y, 2400)
    elapsed_ms = (time.perf_counter() - t0) / 3 * 1000

    assert elapsed_ms < 60, f"lttb() on finite-only data took {elapsed_ms:.1f}ms, expected well under 60ms"
