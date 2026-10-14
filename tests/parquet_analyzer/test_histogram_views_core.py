"""core/histogram.py: time-window filter and duration curve (analysis.ja.md 5.48 / 42.10, 42.11, 42.14)."""

from __future__ import annotations

import calendar
import time as _time
from datetime import datetime

import numpy as np
import pytest

from parquet_analyzer.core import histogram as H
from parquet_analyzer.core.histogram import (
    HistogramError,
    HistogramSettings,
    TimeFilter,
    build_target,
    compute_histogram,
)

HOUR = 3600
DAY = 86400


@pytest.fixture
def tz(monkeypatch):
    def set_tz(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        _time.tzset()

    yield set_tz
    monkeypatch.undo()
    _time.tzset()


@pytest.fixture(autouse=True)
def _utc(tz):
    tz("UTC")


def ts(y, m, d, hh=0, mm=0) -> float:
    return datetime(y, m, d, hh, mm).timestamp()


MONDAY = float(calendar.timegm((2026, 3, 2, 0, 0, 0)))  # a Monday, UTC midnight (the tests run in UTC)


def run(values, t, names=("v",), is_dt=True, chunk_rows=None, **kw):
    if not isinstance(values, dict):
        values = {"v": values}
    return compute_histogram(
        lambda nm, a, b: values[nm][a:b], lambda a, b: t[a:b], list(names), HistogramSettings(**kw),
        0, len(t), chunk_rows=chunk_rows, time_is_datetime=is_dt,
    )


def tfilter(start_h=0.0, end_h=0.0, days=(0, 1, 2, 3, 4, 5, 6)) -> TimeFilter:
    return TimeFilter(True, int(start_h * HOUR), int(end_h * HOUR), tuple(days))


def minute_week():
    """One row per minute for 7 days from Monday 00:00; the value is the hour of day (0..23)."""
    t = MONDAY + np.arange(7 * 1440) * 60.0
    return (np.arange(7 * 1440) // 60 % 24).astype(float), t


# ---------------------------------------------------------------------------
# plain
# ---------------------------------------------------------------------------


def test_filter_rows_hours_9_to_17():
    v, t = minute_week()
    r = run(v, t, bins=24, time_filter=tfilter(9, 17))
    var = r.variables[0]
    assert var.n == 7 * 8 * 60
    assert r.edges[0] >= 9 - 1e-9 and r.edges[-1] <= 16 + 1e-9  # only the values 9..16 exist
    assert var.counts.sum() == 7 * 8 * 60
    assert r.target_seconds == pytest.approx(7 * 8 * HOUR, abs=DAY)  # inside [first, last row]


def test_filter_time_weight_total_is_target_time():
    v, t = minute_week()
    r = run(v, t, weight="time", time_filter=tfilter(9, 17))
    assert r.variables[0].counts.sum() == pytest.approx(7 * 8 * HOUR)


def test_filter_weekdays_only():
    v, t = minute_week()
    r = run(v, t, weight="time", time_filter=tfilter(9, 17, (0, 1, 2, 3, 4)))
    assert r.variables[0].counts.sum() == pytest.approx(5 * 8 * HOUR)
    r = run(v, t, time_filter=tfilter(9, 17, (5, 6)))
    assert r.variables[0].n == 2 * 8 * 60


def test_filter_crossing_midnight_uses_start_day():
    v, t = minute_week()
    r = run(v, t, weight="time", time_filter=tfilter(22, 6, (4,)))  # Friday only: Fri 22:00 - Sat 06:00
    assert r.variables[0].counts.sum() == pytest.approx(8 * HOUR)
    var = run(v, t, time_filter=tfilter(22, 6, (4,))).variables[0]
    assert var.n == 8 * 60
    # the values seen are 22, 23 (Friday) and 0..5 (Saturday)
    assert var.mean == pytest.approx((22 * 60 + 23 * 60 + sum(range(6)) * 60) / (8 * 60))


def test_filter_start_equals_end_is_all_day():
    v, t = minute_week()
    r = run(v, t, weight="time", time_filter=tfilter(0, 0, (0, 1, 2, 3, 4)))
    assert r.variables[0].counts.sum() == pytest.approx(5 * DAY)


def test_filter_row_starting_outside_counts_only_the_inside_part():
    t = MONDAY + np.arange(0, 24) * float(HOUR)  # hourly, value = hour
    v = np.arange(24, dtype=float)
    r = run(v, t, weight="time", bins=24, time_filter=TimeFilter(True, 9 * HOUR + 1800, 12 * HOUR, tuple(range(7))))
    var = r.variables[0]
    # row 9:00 lasts to 10:00 but only 9:30-10:00 is target (1800 s); rows 10, 11 whole
    assert var.counts.sum() == pytest.approx(1800 + 3600 + 3600)
    assert var.n == 3  # rows 9, 10, 11


def test_filter_gap_test_stays_on_original_interval():
    # hourly data with a 4 h hole between 10:00 and 14:00; gap = 2 h. The 10:00 row would be inside
    # 10:00-12:00 but its original 4 h interval is a gap -> weight 0.
    t = MONDAY + np.array([8, 9, 10, 14, 15, 16], dtype=float) * HOUR
    v = np.ones(6)
    r = run(v, t, weight="time", gap_s=2 * HOUR, time_filter=TimeFilter(True, 8 * HOUR, 12 * HOUR, tuple(range(7))))
    assert r.variables[0].counts.sum() == pytest.approx(2 * HOUR)  # rows 8 and 9 only
    assert r.gap_count == 1


def test_filter_off_equals_none():
    v, t = minute_week()
    a = run(v, t, weight="time", bins=20)
    b = run(v, t, weight="time", bins=20, time_filter=TimeFilter(False, 9 * HOUR, 17 * HOUR))
    assert np.array_equal(a.variables[0].counts, b.variables[0].counts)
    assert a.target_seconds is None and b.target_seconds is None


def test_filter_chunks_equal_one_shot():
    v, t = minute_week()
    kw = dict(weight="time", bins=24, time_filter=tfilter(9, 17, (0, 2, 4)))
    one = run(v, t, **kw)
    many = run(v, t, chunk_rows=777, **kw)
    assert np.allclose(one.variables[0].counts, many.variables[0].counts)
    assert one.variables[0].n == many.variables[0].n


def test_filter_no_weekday_is_refused():
    v, t = minute_week()
    with pytest.raises(HistogramError) as e:
        run(v, t, time_filter=tfilter(9, 17, ()))
    assert e.value.code == "no_weekdays"


def test_filter_numeric_axis_ignores_weekdays():
    t = np.arange(0, 3 * DAY, 60, dtype=float)  # axis 0 = day start
    v = (t // HOUR % 24)
    r = run(v, t, weight="time", is_dt=False, time_filter=tfilter(9, 17, ()))
    assert r.variables[0].counts.sum() == pytest.approx(3 * 8 * HOUR, abs=60.0)


def test_filter_excluded_counts_only_eligible_rows():
    v, t = minute_week()
    v = v.copy()
    v[10] = np.nan  # 00:10 on Monday: outside 9-17 -> not "excluded"
    v[9 * 60 + 5] = np.nan  # 09:05: inside
    r = run(v, t, time_filter=tfilter(9, 17))
    assert r.variables[0].excluded == 1


# ---------------------------------------------------------------------------
# DST wall clock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "day, start, end, hours",
    [
        ((2026, 3, 29), (0, 0), (0, 0), 23),  # spring: 23 h day
        ((2026, 10, 25), (0, 0), (0, 0), 25),  # autumn: 25 h day
        ((2026, 3, 29), (2, 0), (3, 0), 0),  # the missing hour
        ((2026, 10, 25), (2, 0), (3, 0), 2),  # the repeated hour counts twice
        ((2026, 10, 25), (1, 0), (4, 0), 4),
        ((2026, 3, 29), (1, 0), (4, 0), 2),
        ((2026, 3, 29), (1, 30), (2, 30), 0.5),
    ],
    ids=["spring_day", "autumn_day", "spring_missing_hour", "autumn_repeated_hour", "autumn_1_4",
         "spring_1_4", "spring_partial_gap"],
)
def test_dst_wall_clock(tz, day, start, end, hours):
    tz("Europe/Berlin")
    d = datetime(*day)
    t0, t1 = d.timestamp() - 3 * DAY, d.timestamp() + 3 * DAY
    tf = TimeFilter(True, start[0] * HOUR + start[1] * 60, end[0] * HOUR + end[1] * 60, (d.weekday(),))
    # only this weekday is chosen, and the +-3 day range holds no other day of it
    assert build_target(tf, t0, t1).total == pytest.approx(hours * HOUR)


# ---------------------------------------------------------------------------
# bucket
# ---------------------------------------------------------------------------


def test_bucket_integral_and_absent_weekend_days():
    v = np.ones(7 * 1440)
    t = MONDAY + np.arange(7 * 1440) * 60.0
    r = run(v, t, mode="bucket", bucket_s=DAY, agg="integral", integral_unit_s=HOUR,
            time_filter=tfilter(9, 17, (0, 1, 2, 3, 4)))
    var = r.variables[0]
    assert r.bucket_count == 5  # Sat / Sun have no target time: absent
    assert len(r.bucket_start) == 5 and len(var.bucket_values) == 5
    assert np.allclose(var.bucket_values, 8.0)
    assert var.incomplete == 0
    assert var.n == 5
    assert r.target_seconds == pytest.approx(5 * 8 * HOUR)


def test_bucket_completeness_denominator_is_target_time():
    # data only during 9:00-17:00 each day: incomplete without the filter, complete with it
    parts = [MONDAY + d * DAY + np.arange(9 * 60, 17 * 60 + 1) * 60.0 for d in range(3)]
    t = np.concatenate(parts)
    v = np.ones(len(t))
    plain = run(v, t, mode="bucket", bucket_s=DAY, agg="mean", gap_s=2 * HOUR)
    assert plain.variables[0].incomplete == 3
    filt = run(v, t, mode="bucket", bucket_s=DAY, agg="mean", gap_s=2 * HOUR, time_filter=tfilter(9, 17))
    assert filt.variables[0].incomplete == 0
    assert all(filt.variables[0].bucket_complete)


def test_bucket_incomplete_when_target_time_missing():
    t = MONDAY + np.arange(2 * 1440) * 60.0
    keep = np.ones(len(t), dtype=bool)
    keep[1440 + 10 * 60: 1440 + 14 * 60] = False  # day 2: a 4 h hole inside 9-17
    v = np.ones(len(t))
    r = run(v[keep], t[keep], mode="bucket", bucket_s=DAY, agg="mean", gap_s=600, time_filter=tfilter(9, 17))
    assert list(r.variables[0].bucket_complete) == [True, False]


def test_bucket_max_uses_target_rows_only():
    t = MONDAY + np.arange(1440) * 60.0
    v = np.zeros(1440)
    v[3 * 60] = 100.0  # 03:00 spike, outside 9-17
    v[12 * 60] = 5.0
    r = run(v, t, mode="bucket", bucket_s=DAY, agg="max", time_filter=tfilter(9, 17))
    assert r.variables[0].bucket_values[0] == 5.0
    assert r.variables[0].bucket_at[0] == MONDAY + 12 * HOUR


def test_bucket_max_value_held_at_bucket_start_only_if_start_in_target():
    # a 9 kW value that began an hour before midnight is still held at the bucket start (00:00)
    t = np.concatenate([[MONDAY - 3600.0, MONDAY + 1800.0], MONDAY + np.arange(1, 24) * 3600.0])
    v = np.concatenate([[9.0], np.ones(len(t) - 1)])
    out = run(v, t, mode="bucket", bucket_s=DAY, agg="max", time_filter=tfilter(9, 17), gap_s=2 * DAY)
    assert out.variables[0].bucket_values[-1] == 1.0  # 00:00 is not target time: 9.0 is not carried in
    inside = run(v, t, mode="bucket", bucket_s=DAY, agg="max", time_filter=tfilter(0, 0), gap_s=2 * DAY)
    assert inside.variables[0].bucket_values[-1] == 9.0


def test_two_stage_skips_buckets_without_target_and_counts_target_windows():
    t = MONDAY + np.arange(7 * 1440) * 60.0
    v = np.ones(len(t))
    r = run(v, t, mode="bucket", bucket_s=DAY, agg="max", two_stage=True, stage1_s=HOUR, stage1_agg="mean",
            time_filter=tfilter(9, 17, (0, 1, 2, 3, 4)))
    var = r.variables[0]
    assert r.bucket_count == 5
    assert list(var.bucket_complete) == [True] * 5  # 8 of the 8 windows with target time
    assert var.stage1_incomplete == 0


def test_bucket_filter_chunks_equal_one_shot():
    t = MONDAY + np.arange(7 * 1440) * 60.0
    v = np.sin(np.arange(len(t)) / 100.0) + 2
    kw = dict(mode="bucket", bucket_s=HOUR * 6, agg="integral", time_filter=tfilter(9, 17, (0, 1, 2)))
    a, b = run(v, t, **kw), run(v, t, chunk_rows=500, **kw)
    assert np.allclose(a.variables[0].bucket_values, b.variables[0].bucket_values, equal_nan=True)
    assert np.array_equal(a.variables[0].bucket_complete, b.variables[0].bucket_complete)


def test_filter_numeric_axis_bucket():
    t = np.arange(0, 3 * DAY, 60, dtype=float)
    v = np.ones(len(t))
    r = run(v, t, is_dt=False, mode="bucket", bucket_s=DAY, agg="integral", integral_unit_s=HOUR,
            time_filter=tfilter(9, 17, ()))
    assert np.allclose(r.variables[0].bucket_values, 8.0)


# ---------------------------------------------------------------------------
# duration curve
# ---------------------------------------------------------------------------


def _two_level_series():
    t = np.arange(241) * 60.0
    v = np.concatenate([np.full(180, 1.0), np.full(61, 2.0)])
    return v, t


def test_duration_curve_time_weight_known_values():
    v, t = _two_level_series()
    var = run(v, t, weight="time", view="duration").variables[0]
    assert var.duration_x[0] == pytest.approx(3600.0)  # value 2: 60 rows with a duration (the last has none)
    assert var.duration_x[-1] == pytest.approx(4 * HOUR)  # value >= 1 for 4 h
    assert var.duration_v[-1] == pytest.approx(1.0)
    assert var.duration_v[0] == pytest.approx(2.0, abs=1e-3)
    assert np.all(np.diff(var.duration_x) > 0) and np.all(np.diff(var.duration_v) < 0)
    assert len(var.duration_x) == 2  # empty fine bins dropped


def test_duration_curve_rows():
    v, t = _two_level_series()
    var = run(v, t, weight="rows", view="duration").variables[0]
    assert var.duration_x[0] == 61 and var.duration_x[-1] == 241


def test_duration_curve_not_computed_for_distribution_view():
    v, t = _two_level_series()
    assert run(v, t, weight="time").variables[0].duration_x is None


def test_duration_curve_bucketed_sorted_descending():
    t = MONDAY + np.arange(4 * 1440) * 60.0
    v = np.repeat([3.0, 1.0, 4.0, 2.0], 1440)
    var = run(v, t, mode="bucket", bucket_s=DAY, agg="max", view="duration").variables[0]
    assert list(var.duration_v) == [4.0, 3.0, 2.0, 1.0]
    assert list(var.duration_x) == [1.0, 2.0, 3.0, 4.0]


def test_duration_curve_bucketed_excludes_incomplete():
    t = MONDAY + np.arange(2 * 1440 - 600) * 60.0  # the second day is cut at 14:00
    v = np.concatenate([np.full(1440, 5.0), np.full(len(t) - 1440, 9.0)])
    r = run(v, t, mode="bucket", bucket_s=DAY, agg="max", view="duration", exclude_incomplete=True)
    assert list(r.variables[0].duration_v) == [5.0]


def test_duration_curve_with_filter_uses_target_time():
    v, t = minute_week()
    var = run(v, t, weight="time", view="duration", time_filter=tfilter(9, 17)).variables[0]
    assert var.duration_x[-1] == pytest.approx(7 * 8 * HOUR)
    assert var.duration_v[-1] == pytest.approx(9.0, abs=0.01)


def test_duration_curve_fine_bin_accuracy():
    rng = np.random.default_rng(0)
    t = np.arange(20000) * 1.0
    v = rng.normal(10, 3, len(t))
    var = run(v, t, weight="rows", view="duration").variables[0]
    width = (v.max() - v.min()) / H.HIST_FINE_BINS
    for k in (10, 1000, 10000, 19000):
        thr = np.sort(v)[::-1][k - 1]  # k values are >= thr
        j = int(np.searchsorted(var.duration_x, k))  # first curve point holding >= k rows
        assert abs(var.duration_v[j] - thr) <= width * 1.01


def test_rows_count_reads_the_time_axis_only_with_the_filter():
    v, t = minute_week()
    reads = {"n": 0}

    def fetch_time(a, b):
        reads["n"] += 1
        return t[a:b]

    def go(**kw):
        return compute_histogram(lambda nm, a, b: v[a:b], fetch_time, ["v"], HistogramSettings(**kw), 0, len(t))

    go(weight="rows")
    assert reads["n"] == 0  # 42.3: counting rows never touches the time axis
    go(weight="rows", time_filter=tfilter(9, 17))
    assert reads["n"] > 0  # the filter needs the times (42.10)
