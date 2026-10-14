"""Histogram time filter: equivalence with the unfiltered computation, chunk independence, and the
review fixes (analysis.ja.md 42.10 / 42.14): completeness at the range edges, values held across a
target start, the excluded count, huge numeric time axes."""

from __future__ import annotations

import calendar
import os
import time as _time

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from parquet_analyzer.core import histogram as H
from parquet_analyzer.core.histogram import (
    HistogramError,
    HistogramSettings,
    TimeFilter,
    compute_histogram,
)
from parquet_analyzer.ui.i18n import tr

HOUR, DAY = 3600, 86400
MONDAY = float(calendar.timegm((2026, 3, 2, 0, 0, 0)))  # UTC midnight; the module runs in UTC
ALL_DAY = TimeFilter(True, 0, 0, tuple(range(7)))


@pytest.fixture(autouse=True)
def _utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    _time.tzset()
    yield
    monkeypatch.undo()
    _time.tzset()


def run(values, t, is_dt=True, chunk_rows=None, **kw):
    if not isinstance(values, dict):
        values = {"v": values}
    return compute_histogram(
        lambda nm, a, b: values[nm][a:b], lambda a, b: t[a:b], list(values), HistogramSettings(**kw),
        0, len(t), chunk_rows=chunk_rows, time_is_datetime=is_dt,
    )


def _close(a, b, what):
    if a is None or b is None:
        assert a is None and b is None, what
        return
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape, what
    np.testing.assert_allclose(a, b, rtol=1e-6, atol=1e-6, equal_nan=True, err_msg=what)


def assert_same(a, b):
    """Every field of two HistogramResults agrees (floats to a tolerance, flags exactly)."""
    assert a.bucket_count == b.bucket_count and a.gap_count == b.gap_count
    assert a.y_unit == b.y_unit and a.bins == b.bins
    _close(a.edges, b.edges, "edges")
    _close(a.bucket_start, b.bucket_start, "bucket_start")
    _close(a.bucket_end, b.bucket_end, "bucket_end")
    for va, vb in zip(a.variables, b.variables, strict=True):
        for f in ("n", "excluded", "incomplete", "excluded_buckets", "buckets_total", "stage1_incomplete"):
            assert getattr(va, f) == getattr(vb, f), f
        for f in ("counts", "bucket_values", "bucket_at", "mean", "std", "median", "duration_x", "duration_v", "vmax"):
            _close(getattr(va, f), getattr(vb, f), f)
        if va.bucket_complete is None:
            assert vb.bucket_complete is None
        else:
            assert np.array_equal(va.bucket_complete, vb.bucket_complete), "bucket_complete"


# ---------------------------------------------------------------------------
# a whole-day, all-weekday filter changes nothing (the range edges included)
# ---------------------------------------------------------------------------


def _mid_bucket_series():
    """1-minute data from Mon 03:17 to the Wednesday of the following week 23:17, a hole of 5 hours and a
    few NaN values: every bucket kind starts and ends mid-bucket."""
    t = MONDAY + 3 * HOUR + 17 * 60 + np.arange(0, 9 * DAY + 20 * HOUR, 60.0)
    v = 10.0 + np.sin(np.arange(len(t)) / 37.0) * 4 + (np.arange(len(t)) % 11)
    keep = ~((t > MONDAY + 3 * DAY + 2 * HOUR) & (t < MONDAY + 3 * DAY + 7 * HOUR))
    v[::997] = np.nan
    return v[keep], t[keep]


CASES = [
    dict(mode="bucket", bucket_s=DAY, agg="mean"),
    dict(mode="bucket", bucket_s=DAY, agg="integral", origin_s=6 * HOUR),  # start 6:00
    dict(mode="bucket", bucket_s=7 * DAY, agg="mean"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="integral"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="max"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="min", origin_s=6 * HOUR),
    dict(mode="bucket", bucket_s=7 * DAY, agg="sum"),
    dict(mode="bucket", bucket_s=HOUR, agg="mean"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="max", two_stage=True, stage1_s=HOUR, stage1_agg="mean"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="mean", two_stage=True, stage1_s=HOUR, stage1_agg="max"),
    dict(mode="bucket", bucket_s=DAY, agg="max", two_stage=True, stage1_s=180.0, stage1_agg="mean", origin_s=6 * HOUR),
    dict(mode="bucket", bucket_s=7 * DAY, agg="mean", exclude_incomplete=True),
    dict(mode="bucket", bucket_s=7 * DAY, agg="mean", view="duration"),
    dict(mode="plain", weight="rows"),
    dict(mode="plain", weight="time"),
    dict(mode="plain", weight="time", view="duration"),
]


@pytest.mark.parametrize("kw", CASES, ids=[str(i) for i in range(len(CASES))])
def test_bucket_whole_day_filter_equals_no_filter(kw):
    v, t = _mid_bucket_series()
    off = run(v, t, **kw)
    on = run(v, t, time_filter=ALL_DAY, **kw)
    assert_same(off, on)


def test_edge_buckets_are_incomplete_with_a_whole_day_filter():
    """The review's repro: data from Monday 6:00 to 23:59, 1-day buckets from 6:00 -> incomplete either way."""
    t = MONDAY + 6 * HOUR + np.arange(0, 18 * HOUR, 60.0)
    v = np.ones(len(t))
    for kw in (dict(bucket_s=DAY, origin_s=6 * HOUR), dict(bucket_s=7 * DAY)):
        off = run(v, t, mode="bucket", agg="mean", **kw)
        on = run(v, t, mode="bucket", agg="mean", time_filter=ALL_DAY, **kw)
        assert list(off.variables[0].bucket_complete) == list(on.variables[0].bucket_complete)
    t7 = MONDAY + np.arange(0, 9 * DAY, 60.0)  # Monday -> the next Tuesday end: the second week is short
    r = run(np.ones(len(t7)), t7, mode="bucket", bucket_s=7 * DAY, agg="mean", time_filter=ALL_DAY)
    assert list(r.variables[0].bucket_complete) == [True, False]
    r = run(np.ones(len(t7)), t7, mode="bucket", bucket_s=7 * DAY, agg="mean", time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR))
    assert list(r.variables[0].bucket_complete) == [True, False]


# ---------------------------------------------------------------------------
# property tests
# ---------------------------------------------------------------------------


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


@st.composite
def series(draw):
    n = draw(st.integers(3, 120))
    steps = draw(st.lists(st.sampled_from([1, 30, 60, 600, 3600, 5 * HOUR, 20 * HOUR]), min_size=n, max_size=n))
    t = MONDAY + draw(st.integers(0, DAY)) + np.cumsum(np.asarray(steps, dtype=float))
    v = np.asarray(draw(st.lists(st.integers(-40, 40), min_size=n, max_size=n)), dtype=float)
    nan = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    v[np.asarray(nan)] = np.nan
    return v, t


_MODES = [
    dict(mode="plain", weight="rows"),
    dict(mode="plain", weight="time"),
    dict(mode="plain", weight="time", view="duration"),
    dict(mode="bucket", bucket_s=DAY, agg="sum"),
    dict(mode="bucket", bucket_s=DAY, agg="integral", origin_s=6 * HOUR),
    dict(mode="bucket", bucket_s=7 * DAY, agg="mean"),
    dict(mode="bucket", bucket_s=HOUR * 6, agg="max"),
    dict(mode="bucket", bucket_s=DAY, agg="min", view="duration"),
    dict(mode="bucket", bucket_s=7 * DAY, agg="max", two_stage=True, stage1_s=HOUR, stage1_agg="mean"),
    dict(mode="bucket", bucket_s=DAY, agg="mean", two_stage=True, stage1_s=HOUR * 3, stage1_agg="min"),
]
_PROP = dict(
    max_examples=_examples(40), derandomize=True, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


@settings(**_PROP)
@given(data=series(), mode=st.sampled_from(range(len(_MODES))), gap=st.sampled_from([None, 7200.0]))
def test_property_whole_day_filter_equals_filter_off(data, mode, gap):
    v, t = data
    kw = dict(_MODES[mode], gap_s=gap)
    assert_same(run(v, t, **kw), run(v, t, time_filter=ALL_DAY, **kw))


@settings(**_PROP)
@given(data=series(), mode=st.sampled_from(range(len(_MODES))), chunk=st.integers(2, 17))
def test_property_filter_results_do_not_depend_on_the_chunk_size(data, mode, chunk):
    v, t = data
    tf = TimeFilter(True, 22 * HOUR, 6 * HOUR, (0, 4, 5, 6))  # 22-6, Mon / Fri / Sat / Sun
    kw = dict(_MODES[mode], gap_s=7200.0, time_filter=tf)
    whole = run(v, t, **kw)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(H, "HIST_CHUNK_ROWS", chunk)
        small = run(v, t, **kw)
    assert_same(whole, small)


# ---------------------------------------------------------------------------
# review fixes
# ---------------------------------------------------------------------------


def test_max_min_include_the_value_held_across_a_target_start():
    # hourly rows at :30; 50 at 8:30 is still running at 9:00 (the target start) until 9:30
    t = MONDAY + (np.arange(0, 24) + 0.5) * HOUR
    v = np.ones(24)
    v[8] = 50.0
    v[9:] = 1.0
    kw = dict(mode="bucket", bucket_s=DAY, time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR), gap_s=2 * HOUR)
    mx = run(v, t, agg="max", **kw).variables[0]
    assert mx.bucket_values[0] == 50.0 and mx.bucket_at[0] == MONDAY + 9 * HOUR
    assert run(v, t, agg="max", chunk_rows=3, **kw).variables[0].bucket_values[0] == 50.0
    v2 = -v
    mn = run(v2, t, agg="min", **kw).variables[0]
    assert mn.bucket_values[0] == -50.0 and mn.bucket_at[0] == MONDAY + 9 * HOUR
    # consistent with the mean: the value is also part of the time-weighted mean
    assert run(v, t, agg="mean", **kw).variables[0].bucket_values[0] > 1.0


def test_target_start_inside_a_bucket_holds_each_day():
    # the 8:30 spike holds into 9:00 every day: with 1-hour buckets only the 9:00 bucket sees it
    t = MONDAY + (np.arange(0, 48) + 0.5) * HOUR
    v = np.ones(48)
    v[8] = v[32] = 50.0
    r = run(v, t, mode="bucket", bucket_s=HOUR, agg="max", gap_s=2 * HOUR,
            time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR)).variables[0]
    starts = run(v, t, mode="bucket", bucket_s=HOUR, agg="max", gap_s=2 * HOUR,
                 time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR))
    nine = [i for i, s in enumerate(starts.bucket_start) if s % DAY == 9 * HOUR]
    assert [r.bucket_values[i] for i in nine] == [50.0, 50.0]


@pytest.mark.parametrize("mode", ["rows", "time", "bucket"])
def test_excluded_count_ignores_rows_outside_the_target(mode):
    t = MONDAY + np.arange(7 * 1440) * 60.0
    v = np.ones(len(t))
    v[5 * 1440 + 600] = np.nan  # Saturday 10:00; the filter only counts Monday-Friday
    tf = TimeFilter(True, 0, 0, (0, 1, 2, 3, 4))
    if mode == "bucket":
        r = run(v, t, mode="bucket", bucket_s=DAY, agg="mean", time_filter=tf)
    else:
        r = run(v, t, mode="plain", weight=mode, time_filter=tf)
    assert r.variables[0].excluded == 0
    v[600] = np.nan  # a Monday NaN does count
    if mode == "bucket":
        r = run(v, t, mode="bucket", bucket_s=DAY, agg="mean", time_filter=tf)
    else:
        r = run(v, t, mode="plain", weight=mode, time_filter=tf)
    assert r.variables[0].excluded == 1


@pytest.mark.parametrize("kw", [
    dict(mode="plain", weight="rows", time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR)),
    dict(mode="bucket", bucket_s=DAY, agg="mean", time_filter=TimeFilter(True, 9 * HOUR, 17 * HOUR)),
    dict(mode="bucket", bucket_s=DAY, agg="mean"),
], ids=["plain_filter", "bucket_filter", "bucket_nofilter"])
def test_huge_numeric_time_axis_is_a_reason_not_a_crash(kw):
    t = 3e11 + np.arange(10) * 60.0  # values no calendar date can hold (year > 9999)
    with pytest.raises(HistogramError) as e:
        run(np.ones(10), t, is_dt=False, **kw)
    assert e.value.code == "time_too_large"
    assert tr("hist.err_time_too_large")
