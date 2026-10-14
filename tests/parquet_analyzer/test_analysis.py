from __future__ import annotations

import numpy as np
import pytest

from parquet_analyzer.core.analysis import (
    basic_stats,
    delta,
    fft,
    rolling_mean,
    rolling_nanmean,
    trailing_mean,
    trailing_nanmean,
)


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


# --- rolling_nanmean (expressions.ja.md 31) ---
_NAN = float("nan")


def test_rolling_nanmean_equals_rolling_mean_without_nan():
    y = np.random.default_rng(0).normal(size=50)
    for w in (1, 3, 4, 10):
        np.testing.assert_allclose(rolling_nanmean(y, w), rolling_mean(y, w))


def test_rolling_nanmean_nan_in_middle_ignored_and_rolling_mean_propagates():
    y = np.array([1.0, 2.0, _NAN, 4.0, 5.0])
    np.testing.assert_allclose(rolling_nanmean(y, 3), [1.5, 1.5, 3.0, 4.5, 4.5])
    assert np.isnan(rolling_mean(y, 3)[1:4]).all()


def test_rolling_nanmean_nan_at_start_and_end():
    y = np.array([_NAN, 2.0, 4.0, 6.0, _NAN])
    np.testing.assert_allclose(rolling_nanmean(y, 3), [2.0, 3.0, 4.0, 5.0, 6.0])


def test_rolling_nanmean_all_nan_window_is_nan():
    y = np.array([1.0, _NAN, _NAN, _NAN, 5.0])
    r = rolling_nanmean(y, 3)
    assert np.isnan(r[2])
    np.testing.assert_allclose(r[[0, 1, 3, 4]], [1.0, 1.0, 5.0, 5.0])
    assert np.isnan(rolling_nanmean(np.array([_NAN, _NAN]), 2)).all()


def test_rolling_nanmean_window_one():
    r = rolling_nanmean(np.array([1.0, _NAN, 3.0]), 1)
    np.testing.assert_allclose(r[[0, 2]], [1.0, 3.0])
    assert np.isnan(r[1])


def test_rolling_nanmean_window_longer_than_data():
    y = np.array([1.0, _NAN, 3.0])
    # Window is clamped to len(y) like rolling_mean (edges see only part of it).
    np.testing.assert_allclose(rolling_nanmean(y, 100), [1.0, 2.0, 3.0])
    np.testing.assert_allclose(rolling_nanmean(y, 100), rolling_nanmean(y, 3))


def test_rolling_nanmean_integer_array_and_empty():
    a = np.array([1, 2, 3, 4])
    np.testing.assert_allclose(rolling_nanmean(a, 3), rolling_mean(a, 3))
    assert len(rolling_nanmean(np.array([]), 5)) == 0


def test_rolling_nanmean_rejects_non_positive_window():
    with pytest.raises(ValueError):
        rolling_nanmean(np.array([1.0, 2.0]), 0)


# --- rolling_mean/rolling_nanmean window position (expressions.ja.md 31.1) ---
def test_rolling_mean_window_position_odd_n():
    """Verify that rolling_mean uses a centered window for odd n.
    For n=3, output i averages y[i-1..i+1]; a single 1.0 at index 5 should
    affect outputs at indices 4, 5, 6 (giving 1/3 each).
    For n=5, output i averages y[i-2..i+2]; a single 1.0 at index 5 should
    affect outputs at indices 3, 4, 5, 6, 7 (giving 1/5 each).
    """
    y = np.zeros(11)
    y[5] = 1.0

    # n=3: window center at i spans [i-1, i, i+1]
    result_n3 = rolling_mean(y, 3)
    assert np.isclose(result_n3[4], 1.0 / 3)
    assert np.isclose(result_n3[5], 1.0 / 3)
    assert np.isclose(result_n3[6], 1.0 / 3)
    assert result_n3[3] == 0.0
    assert result_n3[7] == 0.0

    # n=5: window center at i spans [i-2, i-1, i, i+1, i+2]
    result_n5 = rolling_mean(y, 5)
    assert np.isclose(result_n5[3], 1.0 / 5)
    assert np.isclose(result_n5[4], 1.0 / 5)
    assert np.isclose(result_n5[5], 1.0 / 5)
    assert np.isclose(result_n5[6], 1.0 / 5)
    assert np.isclose(result_n5[7], 1.0 / 5)
    assert result_n5[2] == 0.0
    assert result_n5[8] == 0.0


def test_rolling_mean_window_position_even_n():
    """Verify that rolling_mean uses a centered window for even n.
    For even n, the window has n/2 samples before and n/2-1 after.
    For n=4, output i averages y[i-2..i+1]; a single 1.0 at index 5 should
    affect outputs at indices 4, 5, 6, 7 (giving 1/4 each).
    For n=6, output i averages y[i-3..i+2]; a single 1.0 at index 5 should
    affect outputs at indices 3, 4, 5, 6, 7, 8 (giving 1/6 each).
    """
    y = np.zeros(12)
    y[5] = 1.0

    # n=4: window center at i spans [i-2, i-1, i, i+1]
    result_n4 = rolling_mean(y, 4)
    assert np.isclose(result_n4[4], 1.0 / 4)
    assert np.isclose(result_n4[5], 1.0 / 4)
    assert np.isclose(result_n4[6], 1.0 / 4)
    assert np.isclose(result_n4[7], 1.0 / 4)
    assert result_n4[3] == 0.0
    assert result_n4[8] == 0.0

    # n=6: window center at i spans [i-3, i-2, i-1, i, i+1, i+2]
    result_n6 = rolling_mean(y, 6)
    assert np.isclose(result_n6[3], 1.0 / 6)
    assert np.isclose(result_n6[4], 1.0 / 6)
    assert np.isclose(result_n6[5], 1.0 / 6)
    assert np.isclose(result_n6[6], 1.0 / 6)
    assert np.isclose(result_n6[7], 1.0 / 6)
    assert np.isclose(result_n6[8], 1.0 / 6)
    assert result_n6[2] == 0.0
    assert result_n6[9] == 0.0


def test_rolling_nanmean_window_position_odd_n():
    """Verify that rolling_nanmean uses the same centered window as rolling_mean for odd n."""
    y = np.zeros(11)
    y[5] = 1.0

    # n=3: window spans [i-1, i, i+1]
    result_n3 = rolling_nanmean(y, 3)
    assert np.isclose(result_n3[4], 1.0 / 3)
    assert np.isclose(result_n3[5], 1.0 / 3)
    assert np.isclose(result_n3[6], 1.0 / 3)
    assert result_n3[3] == 0.0
    assert result_n3[7] == 0.0

    # n=5: window spans [i-2, i-1, i, i+1, i+2]
    result_n5 = rolling_nanmean(y, 5)
    assert np.isclose(result_n5[3], 1.0 / 5)
    assert np.isclose(result_n5[4], 1.0 / 5)
    assert np.isclose(result_n5[5], 1.0 / 5)
    assert np.isclose(result_n5[6], 1.0 / 5)
    assert np.isclose(result_n5[7], 1.0 / 5)
    assert result_n5[2] == 0.0
    assert result_n5[8] == 0.0


def test_rolling_nanmean_window_position_even_n():
    """Verify that rolling_nanmean uses the same centered window as rolling_mean for even n."""
    y = np.zeros(12)
    y[5] = 1.0

    # n=4: window spans [i-2, i-1, i, i+1]
    result_n4 = rolling_nanmean(y, 4)
    assert np.isclose(result_n4[4], 1.0 / 4)
    assert np.isclose(result_n4[5], 1.0 / 4)
    assert np.isclose(result_n4[6], 1.0 / 4)
    assert np.isclose(result_n4[7], 1.0 / 4)
    assert result_n4[3] == 0.0
    assert result_n4[8] == 0.0

    # n=6: window spans [i-3, i-2, i-1, i, i+1, i+2]
    result_n6 = rolling_nanmean(y, 6)
    assert np.isclose(result_n6[3], 1.0 / 6)
    assert np.isclose(result_n6[4], 1.0 / 6)
    assert np.isclose(result_n6[5], 1.0 / 6)
    assert np.isclose(result_n6[6], 1.0 / 6)
    assert np.isclose(result_n6[7], 1.0 / 6)
    assert np.isclose(result_n6[8], 1.0 / 6)
    assert result_n6[2] == 0.0
    assert result_n6[9] == 0.0


# --- trailing_mean / trailing_nanmean (expressions.ja.md 31.2) ---

def test_trailing_mean_start_example():
    """The specification example: trailing_mean([1,2,3,4], 3) == [1, 1.5, 2, 3]."""
    y = np.array([1.0, 2.0, 3.0, 4.0])
    result = trailing_mean(y, 3)
    np.testing.assert_allclose(result, [1.0, 1.5, 2.0, 3.0])


def test_trailing_mean_preserves_length():
    """Output has the same length as input."""
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = trailing_mean(y, 3)
    assert len(result) == len(y)


def test_trailing_mean_on_empty_array_does_not_crash():
    result = trailing_mean(np.array([]), 5)
    assert len(result) == 0


def test_trailing_mean_rejects_non_positive_window():
    with pytest.raises(ValueError):
        trailing_mean(np.array([1.0, 2.0, 3.0]), 0)


def test_trailing_mean_window_one():
    """Window size 1 should give the values themselves."""
    y = np.array([1.0, 2.0, 3.0, 4.0])
    result = trailing_mean(y, 1)
    np.testing.assert_allclose(result, y)


def test_trailing_mean_nan_propagates_and_stops():
    """NaN in the window propagates to output, but stops after window-1 points."""
    y = np.array([1.0, 2.0, _NAN, 4.0, 5.0, 6.0])
    # window = 3, so NaN at position 2
    # pos 0: window [1] -> 1
    # pos 1: window [1, 2] -> 1.5
    # pos 2: window [1, 2, NaN] -> NaN
    # pos 3: window [2, NaN, 4] -> NaN
    # pos 4: window [NaN, 4, 5] -> NaN (NaN still in window)
    # pos 5: window [4, 5, 6] -> 5 (NaN has left the window)
    result = trailing_mean(y, 3)
    assert np.isclose(result[0], 1.0)
    assert np.isclose(result[1], 1.5)
    assert np.isnan(result[2])
    assert np.isnan(result[3])
    assert np.isnan(result[4])
    assert np.isclose(result[5], 5.0)


def test_trailing_mean_nan_at_start():
    y = np.array([_NAN, 2.0, 4.0, 6.0])
    result = trailing_mean(y, 3)
    assert np.isnan(result[0])  # window [NaN]
    assert np.isnan(result[1])  # window [NaN, 2]
    assert np.isnan(result[2])  # window [NaN, 2, 4]
    assert np.isclose(result[3], 4.0)  # window [2, 4, 6]


def test_trailing_mean_window_longer_than_data():
    y = np.array([1.0, 2.0, 3.0])
    # Window is clamped to len(y)
    result = trailing_mean(y, 100)
    assert len(result) == len(y)
    np.testing.assert_allclose(result, [1.0, 1.5, 2.0])


def test_trailing_mean_integer_array():
    a = np.array([1, 2, 3, 4])
    result = trailing_mean(a, 3)
    np.testing.assert_allclose(result, [1.0, 1.5, 2.0, 3.0])


def test_trailing_mean_window_position():
    """Verify trailing_mean uses past-only window.
    For window n=3, output i averages y[max(0, i-2):i+1].
    A single 1.0 at index 5 should affect outputs at indices 5, 6, 7 (not 3, 4).
    """
    y = np.zeros(11)
    y[5] = 1.0

    # n=3: window for output i is [i-2, i-1, i]
    result_n3 = trailing_mean(y, 3)
    assert result_n3[4] == 0.0  # window [0, 0, 0]
    assert np.isclose(result_n3[5], 1.0 / 3)  # window [0, 0, 1]
    assert np.isclose(result_n3[6], 1.0 / 3)  # window [0, 1, 0]
    assert np.isclose(result_n3[7], 1.0 / 3)  # window [1, 0, 0]
    assert result_n3[8] == 0.0  # window [0, 0, 0]


# --- trailing_nanmean ---

def test_trailing_nanmean_equals_trailing_mean_without_nan():
    y = np.random.default_rng(0).normal(size=50)
    for w in (1, 3, 4, 10):
        np.testing.assert_allclose(trailing_nanmean(y, w), trailing_mean(y, w))


def test_trailing_nanmean_nan_in_window_ignored():
    y = np.array([1.0, 2.0, _NAN, 4.0, 5.0])
    result = trailing_nanmean(y, 3)
    # pos 0: window [1] -> 1
    # pos 1: window [1, 2] -> 1.5
    # pos 2: window [1, 2, NaN] -> (1+2)/2 = 1.5
    # pos 3: window [2, NaN, 4] -> (2+4)/2 = 3
    # pos 4: window [NaN, 4, 5] -> (4+5)/2 = 4.5
    np.testing.assert_allclose(result, [1.0, 1.5, 1.5, 3.0, 4.5])


def test_trailing_nanmean_all_nan_window_is_nan():
    y = np.array([1.0, _NAN, _NAN, _NAN, 5.0])
    result = trailing_nanmean(y, 3)
    # pos 2: window [1, NaN, NaN] -> 1
    # pos 3: window [NaN, NaN, NaN] -> NaN
    assert np.isclose(result[0], 1.0)
    assert np.isclose(result[1], 1.0)
    assert np.isclose(result[2], 1.0)
    assert np.isnan(result[3])
    assert np.isclose(result[4], 5.0)


def test_trailing_nanmean_window_one():
    r = trailing_nanmean(np.array([1.0, _NAN, 3.0]), 1)
    np.testing.assert_allclose(r[[0, 2]], [1.0, 3.0])
    assert np.isnan(r[1])


def test_trailing_nanmean_window_longer_than_data():
    y = np.array([1.0, _NAN, 3.0])
    result = trailing_nanmean(y, 100)
    assert len(result) == len(y)
    # Window is clamped to len(y) = 3
    # pos 0: [1.0] -> 1.0
    # pos 1: [1.0, NaN] -> 1.0 (NaN ignored)
    # pos 2: [1.0, NaN, 3.0] -> (1.0 + 3.0) / 2 = 2.0
    np.testing.assert_allclose(result, [1.0, 1.0, 2.0])


def test_trailing_nanmean_integer_array():
    a = np.array([1, 2, 3, 4])
    result = trailing_nanmean(a, 3)
    np.testing.assert_allclose(result, [1.0, 1.5, 2.0, 3.0])


def test_trailing_nanmean_rejects_non_positive_window():
    with pytest.raises(ValueError):
        trailing_nanmean(np.array([1.0, 2.0]), 0)
