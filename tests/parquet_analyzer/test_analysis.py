from __future__ import annotations

import numpy as np
import pytest

from parquet_analyzer.core.analysis import basic_stats, delta, fft, rolling_mean


def test_delta_first_value_zero_and_length_matches():
    y = np.array([1.0, 3.0, 6.0, 10.0])
    d = delta(y)
    assert len(d) == len(y)
    assert d[0] == 0
    np.testing.assert_array_equal(d[1:], [2.0, 3.0, 4.0])


def test_delta_on_empty_array_returns_empty_instead_of_crashing():
    """Regression test: delta() did `out[0] = 0` unconditionally, raising IndexError
    on a zero-length array (e.g. a zero-row parquet, or a fully-filtered selection).
    """
    result = delta(np.array([]))
    assert len(result) == 0


def test_fft_on_empty_array_returns_empty_instead_of_crashing():
    """Regression test: fft() divided by `n`, raising ZeroDivisionError when n == 0."""
    freqs, amps = fft(np.array([]), np.array([]))
    assert len(freqs) == 0
    assert len(amps) == 0


def test_fft_detects_dominant_frequency():
    fs = 1000.0
    t = np.arange(0, 1, 1 / fs)
    freq = 50.0
    y = np.sin(2 * np.pi * freq * t)
    freqs, amps = fft(t, y)
    peak_freq = freqs[np.argmax(amps)]
    assert abs(peak_freq - freq) < 2.0


def test_rolling_mean_preserves_length_and_smooths_noise():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 1000)
    smoothed = rolling_mean(y, 21)
    assert len(smoothed) == len(y)
    assert np.std(smoothed) < np.std(y)  # smoothing reduces variance


def test_rolling_mean_flat_constant_series_stays_constant():
    y = np.full(50, 3.0)
    np.testing.assert_allclose(rolling_mean(y, 5), y)


def test_rolling_mean_window_larger_than_series_does_not_crash():
    # window is clamped to len(y); still a sliding (not global) average, so edges
    # only average over however many real neighbors they actually have.
    y = np.array([1.0, 2.0, 3.0])
    result = rolling_mean(y, 100)
    assert len(result) == len(y)
    np.testing.assert_allclose(result, [1.5, 2.0, 2.5])


def test_rolling_mean_rejects_non_positive_window():
    with pytest.raises(ValueError):
        rolling_mean(np.array([1.0, 2.0, 3.0]), 0)


def test_rolling_mean_on_empty_array_does_not_crash():
    result = rolling_mean(np.array([]), 5)
    assert len(result) == 0


def test_basic_stats_matches_plain_numpy_on_clean_data():
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    stats = basic_stats(y)
    assert stats == {
        "count": 5,
        "nan_count": 0,
        "min": 1.0,
        "max": 5.0,
        "mean": 3.0,
        "std": pytest.approx(np.std(y)),
        "median": 3.0,
    }


def test_basic_stats_excludes_nan_and_inf_but_counts_them():
    y = np.array([1.0, np.nan, 3.0, np.inf, 5.0])
    stats = basic_stats(y)
    assert stats["count"] == 5
    assert stats["nan_count"] == 2  # NaN and Inf are both excluded from the numeric stats
    assert stats["min"] == 1.0
    assert stats["max"] == 5.0
    assert stats["mean"] == pytest.approx(3.0)


def test_basic_stats_on_all_nan_array_does_not_crash():
    """Regression-style edge case: no finite samples at all (e.g. the visible window
    falls entirely inside a gap in a burst-sampled log) must not raise.
    """
    stats = basic_stats(np.array([np.nan, np.nan]))
    assert stats["count"] == 2
    assert stats["nan_count"] == 2
    assert np.isnan(stats["mean"])


def test_basic_stats_on_empty_array_does_not_crash():
    stats = basic_stats(np.array([]))
    assert stats["count"] == 0
    assert stats["nan_count"] == 0
    assert np.isnan(stats["min"])
