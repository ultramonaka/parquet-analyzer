"""Two-stage histogram aggregation and the per-bucket table (analysis.ja.md 5.37 / 42.8), core only.

Synthetic data only. The window/export UI side is in test_histogram_export.py."""

from __future__ import annotations

import math
import time as _time
from datetime import datetime

import numpy as np
import pytest

from parquet_analyzer.core import histogram as H
from parquet_analyzer.core import histogram_export as X
from parquet_analyzer.core.histogram import (
    HistogramError,
    HistogramSettings,
    compute_histogram,
    validate_two_stage,
)


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


def local_ts(y, m, d, hh=0, mm=0, ss=0) -> float:
    return datetime(y, m, d, hh, mm, ss).timestamp()


def run(values, t, names=("v",), chunk_rows=None, **kw):
    if not isinstance(values, dict):
        values = {"v": values}
    return compute_histogram(
        lambda nm, a, b: values[nm][a:b], lambda a, b: t[a:b], list(names), HistogramSettings(**kw),
        0, len(t), chunk_rows=chunk_rows,
    )


def two(values, t, stage1_s, stage1_agg, agg, bucket_s=86400.0, **kw):
    return run(values, t, mode="bucket", two_stage=True, stage1_s=stage1_s, stage1_agg=stage1_agg, agg=agg,
               bucket_s=bucket_s, **kw)


def stamp(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# known values: a 3-minute marker every day
# ---------------------------------------------------------------------------


def marker_data(days=2):
    t = local_ts(2026, 3, 2) + np.arange(days * 86400, dtype=np.float64)  # 1 s rows
    rng = np.random.default_rng(3)
    v = rng.uniform(0.0, 5.0, len(t))
    for d in range(days):
        s = d * 86400 + 12 * 3600
        v[s:s + 180] = 100.0 + 10.0 * d  # 12:00:00 .. 12:02:59
    return t, v


def test_three_minute_mean_daily_max_is_the_marker_with_its_time():
    t, v = marker_data()
    r = two(v, t, 180.0, "mean", "max")
    var = r.variables[0]
    assert var.bucket_values == pytest.approx([100.0, 110.0], abs=1e-9)
    assert [stamp(a) for a in var.bucket_at] == ["12:00:00", "12:00:00"]
    assert list(var.bucket_complete) == [True, True]
    assert var.n == 2 and var.stage1_incomplete == 0
    # the distribution is built from the stage-2 values
    assert var.mean == pytest.approx(105.0) and var.counts.sum() == 2


def test_max_of_maxima_carries_the_peak_instant_not_the_window_start():
    """Stage 1 max + stage 2 max: `at` is the instant of the peak inside the winning window
    (42.8, user decision 2026-10-07); with a stage-1 mean it stays the window start."""
    t, v = marker_data()
    for d in range(2):
        v[d * 86400 + 12 * 3600 + 100] = 200.0 + d  # peak at 12:01:40, inside the 12:00 window
    var = two(v, t, 180.0, "max", "max").variables[0]
    assert var.bucket_values == pytest.approx([200.0, 201.0])
    assert [stamp(a) for a in var.bucket_at] == ["12:01:40", "12:01:40"]
    var = two(v, t, 180.0, "min", "min").variables[0]
    lows = [v[d * 86400:(d + 1) * 86400].min() for d in range(2)]
    assert var.bucket_values == pytest.approx(lows)
    for d in range(2):
        assert var.bucket_at[d] == t[d * 86400 + int(np.argmin(v[d * 86400:(d + 1) * 86400]))]
    assert [stamp(a) for a in two(v, t, 180.0, "mean", "max").variables[0].bucket_at] == ["12:00:00", "12:00:00"]


def test_four_minute_window_mixes_marker_and_noise():
    t, v = marker_data()
    r = two(v, t, 240.0, "mean", "max")
    for d, val in enumerate(r.variables[0].bucket_values):
        s = d * 86400 + 12 * 3600
        assert val == pytest.approx(v[s:s + 240].mean(), rel=1e-12)  # 3/4 marker + 1/4 noise
        assert 100 + 10 * d > val > 0.75 * (100 + 10 * d)


def test_two_stage_chunked_equals_whole():
    t, v = marker_data()
    whole = two(v, t, 180.0, "mean", "max").variables[0]
    chunked = two(v, t, 180.0, "mean", "max", chunk_rows=7001).variables[0]
    np.testing.assert_allclose(whole.bucket_values, chunked.bucket_values, rtol=1e-9)
    np.testing.assert_array_equal(whole.bucket_at, chunked.bucket_at)


# ---------------------------------------------------------------------------
# rows are split at window boundaries (cumulative method) in stage 1
# ---------------------------------------------------------------------------


def test_rows_straddling_stage1_windows_are_split_in_time():
    step = 40.0  # a row lasts 40 s, windows are 60 s: most rows straddle a boundary
    n = 90  # one hour
    t = local_ts(2026, 3, 2) + np.arange(n) * step
    v = np.arange(n, dtype=np.float64) * 1.5 + 1.0
    sec = np.repeat(v, 40)  # held values as 1 s samples
    win_mean = sec[:3000].reshape(-1, 60).mean(axis=1)  # minutes 0..49 are fully covered
    ref_max = win_mean.reshape(-1, 10).max(axis=1)  # 10-min buckets 0..4
    for chunk in (None, 7):
        r = two(v, t, 60.0, "mean", "max", bucket_s=600.0, gap_s=1e9, chunk_rows=chunk)
        var = r.variables[0]
        assert var.bucket_values[:5] == pytest.approx(ref_max, rel=1e-12)
        assert list(var.bucket_complete[:5]) == [True] * 5


# ---------------------------------------------------------------------------
# incomplete stage-1 windows / incomplete buckets
# ---------------------------------------------------------------------------


def test_incomplete_stage1_window_does_not_enter_stage2_and_is_counted():
    t, v = marker_data(1)
    hole_a, hole_b = 10 * 3600 + 60, 10 * 3600 + 160  # 10:01:00 .. 10:02:40 missing: the 10:00-10:03 window is cut
    keep = np.ones(len(t), dtype=bool)
    keep[hole_a:hole_b] = False
    v2 = v.copy()
    v2[10 * 3600:10 * 3600 + 60] = 1000.0  # a spike inside the cut window (rows 10:00:00 .. 10:00:59)
    r = two(v2[keep], t[keep], 180.0, "mean", "max")
    var = r.variables[0]
    assert var.stage1_incomplete >= 1
    assert var.bucket_values[0] == pytest.approx(100.0)  # not the spike's cut-window mean (~333)
    assert [stamp(a) for a in var.bucket_at] == ["12:00:00"]


def test_range_edge_windows_are_incomplete_and_a_bucket_below_ninety_percent_is_incomplete():
    t, v = marker_data(2)
    keep = np.ones(len(t), dtype=bool)
    keep[86400 + 3 * 3600:86400 + 6 * 3600] = False  # day 2 loses 3 h = 60 of 480 windows (12.5 %)
    r = two(v[keep], t[keep], 180.0, "mean", "max")
    var = r.variables[0]
    assert list(var.bucket_complete) == [True, False]
    assert var.bucket_values[1] == pytest.approx(110.0)  # the value is still there, just flagged
    assert var.incomplete == 1
    # "exclude incomplete" leaves it out of the distribution only; the table keeps both
    r2 = two(v[keep], t[keep], 180.0, "mean", "max", exclude_incomplete=True)
    v2 = r2.variables[0]
    assert v2.n == 1 and v2.excluded_buckets == 1
    assert len(v2.bucket_values) == 2 and list(v2.bucket_complete) == [True, False]
    assert v2.counts.sum() == 1


def test_range_starting_mid_window_has_an_incomplete_first_window():
    t, v = marker_data(1)
    start = 12 * 3600 + 60  # begins inside the marker's 12:00 window
    r = two(v[start:], t[start:], 180.0, "mean", "max")
    var = r.variables[0]
    assert var.stage1_incomplete >= 1
    assert var.bucket_values[0] < 100.0  # the cut marker window (2 of 3 minutes) cannot win


def test_bucket_with_no_usable_window_has_no_value():
    t, v = marker_data(2)
    keep = np.ones(len(t), dtype=bool)
    keep[86400 + 1:] = False  # day 2: a single row -> its window is incomplete
    r = two(v[keep], t[keep], 180.0, "mean", "max")
    var = r.variables[0]
    assert math.isnan(var.bucket_values[1]) and not var.bucket_complete[1] and math.isnan(var.bucket_at[1])
    assert r.bucket_count == 2 and var.n == 1


# ---------------------------------------------------------------------------
# stage-2 reductions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agg", ["max", "min", "mean", "sum"])
def test_stage2_aggregations_match_numpy(agg):
    t, v = marker_data(2)
    r = two(v, t, 180.0, "mean", agg)
    var = r.variables[0]
    w = np.ones(len(v))
    w[-1] = 0.0  # the last row of the range lasts 0 s
    mean3 = ((v * w).reshape(-1, 180).sum(axis=1) / w.reshape(-1, 180).sum(axis=1)).reshape(2, 480)
    ref = {"max": mean3.max(axis=1), "min": mean3.min(axis=1), "mean": mean3.mean(axis=1), "sum": mean3.sum(axis=1)}[agg]
    assert var.bucket_values == pytest.approx(ref, rel=1e-9)
    if agg in ("max", "min"):
        pick = (mean3.argmax(axis=1) if agg == "max" else mean3.argmin(axis=1)) * 180
        expect = [local_ts(2026, 3, 2) + d * 86400 + p for d, p in enumerate(pick)]
        assert list(var.bucket_at) == expect
    else:
        assert var.bucket_at is None


def test_stage1_aggregations_sum_integral_max_min():
    t, v = marker_data(1)
    sec = v.reshape(-1, 180)
    for agg1, ref in (("sum", sec.sum(axis=1).max()), ("max", sec.max()), ("min", sec.min(axis=1).max())):
        got = two(v, t, 180.0, agg1, "max").variables[0].bucket_values[0]
        assert got == pytest.approx(ref, rel=1e-9), agg1
    # integral (value x time, unit h) per window, then summed over the day = the day's integral
    # (the last row of the range lasts 0 s)
    r = two(v, t, 180.0, "integral", "sum", integral_unit_s=3600.0)
    assert r.variables[0].bucket_values[0] == pytest.approx(v[:-1].sum() / 3600.0, rel=1e-9)


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage1,bucket,reason", [
    (180.0, 86400.0, None),
    (60.0, 600.0, None),
    (420.0, 86400.0, "stage1_not_divisor"),     # 7 min does not divide a day
    (86400.0 * 2, 86400.0 * 4, "stage1_not_divisor"),
    (960.0, 3600.0, "stage1_not_divide_stage2"),  # 16 min divides a day but not 1 h
    (3600.0, 3600.0, "stage2_not_longer"),
    (3600.0, 600.0, "stage2_not_longer"),
    (0.0, 3600.0, "stage1_not_positive"),
    (180.0, 700.0, "bucket_not_divisor"),
    (180.0, 86400.0 * 1.5, "bucket_not_whole_days"),
])
def test_validate_two_stage(stage1, bucket, reason):
    assert validate_two_stage(stage1, bucket) == reason


@pytest.mark.parametrize("stage1,bucket,code", [
    (420.0, 86400.0, "stage1_not_divisor"),
    (960.0, 3600.0, "stage1_not_divide_stage2"),
    (3600.0, 600.0, "stage2_not_longer"),
])
def test_violation_is_refused_with_the_reason(stage1, bucket, code):
    t, v = marker_data(1)
    with pytest.raises(HistogramError) as exc:
        two(v, t, stage1, "mean", "max", bucket_s=bucket)
    assert exc.value.code == code


def test_too_many_stage1_windows_is_refused(monkeypatch):
    t, v = marker_data(1)
    monkeypatch.setattr(H, "HIST_MAX_BUCKETS", 2000)
    with pytest.raises(HistogramError) as exc:
        two(v, t, 60.0, "mean", "max")  # 1440+ one-minute windows
    assert exc.value.code == "too_many_windows"
    two(v, t, 180.0, "mean", "max")  # 480+ windows: fine


def test_stage2_has_no_integral():
    t, v = marker_data(1)
    with pytest.raises(HistogramError) as exc:
        two(v, t, 180.0, "mean", "integral")
    assert exc.value.code == "bad_aggregation"


# ---------------------------------------------------------------------------
# DST days
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day,hours", [((2026, 10, 25), 25), ((2026, 3, 29), 23)])
def test_stage1_windows_line_up_inside_dst_days(tz, day, hours):
    tz("Europe/Berlin")
    y, m, d = day
    day_start, day_end = local_ts(y, m, d), local_ts(y, m, d + 1)
    assert (day_end - day_start) / 3600 == hours
    t = day_start + np.arange(0, hours * 360) * 10.0  # 10 s rows over the whole day
    v = np.floor((t - day_start) / 3600.0)  # the hour of the day: window means are 0, 1, 2, ...
    r = two(v, t, 3600.0, "mean", "max", bucket_s=3 * 3600.0, gap_s=1e9)
    var = r.variables[0]
    n_buckets = math.ceil(hours / 3)
    # the day's buckets (the range ends at the day's last row, so no bucket of the next day)
    assert len(var.bucket_values) == n_buckets
    # bucket j holds hours 3j .. 3j+2, the last (short) one only the leftover hours
    expect = [min(3 * j + 2, hours - 1) for j in range(n_buckets)]
    assert list(var.bucket_values) == pytest.approx(expect)
    # the short last bucket (2 h on a 23 h day, 1 h on a 25 h day) is incomplete, the others are complete
    short = hours % 3
    assert list(var.bucket_complete) == [True] * (n_buckets - 1) + [False]
    assert r.bucket_end[-1] - r.bucket_start[-1] == short * 3600.0
    assert var.stage1_incomplete == 0  # every hour window is covered (the last row's 0 s is < 10 %)


# ---------------------------------------------------------------------------
# single stage: the time of max / min
# ---------------------------------------------------------------------------


def test_single_stage_max_min_time_is_the_first_row_reaching_it():
    t = local_ts(2026, 3, 2) + np.arange(0, 2 * 86400, 600.0)  # 10 min rows, 2 days
    v = np.zeros(len(t))
    v[10], v[20], v[30] = 5.0, 5.0, -3.0  # day 1: max 5 first at row 10; min -3 at row 30
    v[144 + 7], v[144 + 8] = 9.0, 9.0  # day 2: tie -> the first
    for chunk in (None, 11):
        r = run(v, t, mode="bucket", bucket_s=86400.0, agg="max", chunk_rows=chunk)
        at = r.variables[0].bucket_at
        assert list(at) == [t[10], t[144 + 7]]
        r = run(v, t, mode="bucket", bucket_s=86400.0, agg="min", chunk_rows=chunk)
        assert r.variables[0].bucket_at[0] == t[30]


def test_single_stage_time_of_a_value_held_across_the_bucket_start_is_the_bucket_start():
    base = local_ts(2026, 3, 2)
    t = base + np.array([0.0, 330.0, 3600.0])  # rows at 0:00, 0:05:30, 1:00
    v = np.array([7.0, 7.0, 1.0])
    r = run(v, t, mode="bucket", bucket_s=60.0, agg="max", gap_s=1e9)
    var = r.variables[0]
    starts = r.bucket_start
    k = int(np.searchsorted(starts, base + 300.0))  # the bucket 0:05:00 .. 0:06:00
    assert starts[k] == base + 300.0
    # it holds the value 7 carried in from the row at 0:00 and the row at 0:05:30 (also 7): the
    # earlier time, the bucket start, wins
    assert var.bucket_at[k] == base + 300.0
    # a bucket with only a carried-in value (0:02) -> its start; the bucket of the row itself -> the row time
    assert var.bucket_at[int(np.searchsorted(starts, base + 120.0))] == base + 120.0
    assert var.bucket_at[int(np.searchsorted(starts, base))] == base


def test_time_of_max_across_a_chunk_boundary_keeps_the_earlier_tie():
    t = local_ts(2026, 3, 2) + np.arange(200) * 60.0
    v = np.zeros(200)
    v[40], v[150] = 4.0, 4.0
    r = run(v, t, mode="bucket", bucket_s=86400.0, agg="max", chunk_rows=50)
    assert r.variables[0].bucket_at[0] == t[40]


def test_at_is_only_present_for_max_and_min():
    t, v = marker_data(1)
    assert run(v, t, mode="bucket", agg="sum").variables[0].bucket_at is None
    assert run(v, t, mode="bucket", agg="mean").variables[0].bucket_at is None
    assert run(v, t, mode="bucket", agg="max").variables[0].bucket_at is not None


# ---------------------------------------------------------------------------
# the per-bucket arrays
# ---------------------------------------------------------------------------


def test_bucket_arrays_cover_only_buckets_overlapping_the_range():
    t = local_ts(2026, 3, 2, 6) + np.arange(0, 18 * 3600, 60.0)  # 6:00 .. 23:59 of one day
    r = run(np.ones(len(t)), t, mode="bucket", bucket_s=86400.0, agg="sum")
    assert len(r.bucket_start) == 1 and r.bucket_start[0] == local_ts(2026, 3, 2)
    assert r.bucket_end[0] == local_ts(2026, 3, 3)
    var = r.variables[0]
    assert not var.bucket_complete[0]  # 18 h of 24 h: incomplete, but present
    assert var.bucket_values[0] == len(t)


def test_plain_mode_has_no_bucket_table():
    t, v = marker_data(1)
    assert run(v, t).bucket_start is None


def test_multiple_variables_get_their_own_table_columns():
    t, v = marker_data(2)
    r = run({"a": v, "b": v * 2}, t, names=("a", "b"), mode="bucket", two_stage=True, stage1_s=180.0,
            stage1_agg="mean", agg="max")
    a, b = r.variables
    assert b.bucket_values == pytest.approx(a.bucket_values * 2)


# ---------------------------------------------------------------------------
# export module: local time, columns, collisions
# ---------------------------------------------------------------------------


def test_local_datetime_matches_fromtimestamp_across_a_dst_change(tz):
    tz("Europe/Berlin")
    x = local_ts(2026, 10, 24, 12) + np.arange(0, 3 * 86400, 1800.0)  # crosses the fall-back
    got = X.local_datetime_ms(x)
    want = np.array([datetime.fromtimestamp(v) for v in x], dtype="datetime64[ms]")
    np.testing.assert_array_equal(got, want)
    assert np.isnat(X.local_datetime_ms(np.array([np.nan, np.inf]))).all()


def test_local_datetime_sparse_span_falls_back_per_value(tz):
    tz("Asia/Tokyo")
    x = np.array([0.0, 1.7e9, 9e9])
    got = X.local_datetime_ms(x)
    np.testing.assert_array_equal(got, np.array([datetime.fromtimestamp(v) for v in x], dtype="datetime64[ms]"))


def test_columns_names_and_collisions():
    t, v = marker_data(2)
    r = run({"window_start": v, "x": v, "x_complete": v}, t, names=("window_start", "x", "x_complete"),
            mode="bucket", agg="max")
    cols, mapping = X.build_columns(r, True)
    names = list(cols)
    assert len(names) == len(set(names))
    assert names[:2] == ["window_start", "window_end"]
    assert mapping["window_start"] == "window_start" and mapping["variables"]["window_start"] == "window_start_2"
    assert mapping["variables"]["x"] == "x" and "x_complete" in names
    assert mapping["variables"]["x_complete"] == "x_complete_2"  # `x_complete` is taken by x's flag column
    assert cols["window_start"].dtype == np.dtype("datetime64[ms]")
    assert cols["x"].dtype == np.float64 and cols["x_complete"].dtype == bool
    assert any(n.endswith("_at") for n in names)


def test_numeric_time_axis_columns_are_float_seconds():
    t, v = marker_data(1)
    r = run(v, t, mode="bucket", agg="max")
    cols, _ = X.build_columns(r, False)
    assert cols["window_start"].dtype == np.float64 and cols["window_start"][0] == r.bucket_start[0]
    assert cols["v_at"].dtype == np.float64


def test_default_file_stem():
    t, v = marker_data(1)
    r = two(v, t, 180.0, "mean", "max")
    r.variables[0].name = "power_kw"
    assert X.default_file_stem(r) == "power_kw_1d_3min-mean-max"
    r1 = run(v, t, mode="bucket", bucket_s=3600.0, agg="integral")
    assert X.default_file_stem(r1) == "v_1h_integral"
    r1.variables[0].name = "a b/c"
    assert X.default_file_stem(r1) == "a_b_c_1h_integral"
