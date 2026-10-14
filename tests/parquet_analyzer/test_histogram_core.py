"""core/histogram.py computation (analysis.ja.md 5.33 / 42.3 / 42.6), without any window."""

from __future__ import annotations

import math
import time as _time
from datetime import datetime

import numpy as np
import pytest

from parquet_analyzer.core import histogram as H
from parquet_analyzer.core.histogram import (
    Cancelled,
    HistogramError,
    HistogramSettings,
    bucket_edges,
    compute_histogram,
    validate_bucket,
)


@pytest.fixture
def tz(monkeypatch):
    """Set the process time zone for the test and restore it afterwards."""

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


def run(values, t, names=("v",), chunk_rows=None, cancelled=None, **kw):
    """values: dict name -> array (or one array for 'v'); t: time array."""
    if not isinstance(values, dict):
        values = {"v": values}
    settings = HistogramSettings(**kw)
    return compute_histogram(
        lambda nm, a, b: values[nm][a:b], lambda a, b: t[a:b], list(names), settings,
        0, len(t), chunk_rows=chunk_rows, cancelled=cancelled,
    )


# ---------------------------------------------------------------------------
# plain, counted by rows
# ---------------------------------------------------------------------------


def test_plain_rows_total_equals_valid_and_excluded_counted():
    rng = np.random.default_rng(1)
    v = rng.normal(size=1000)
    v[[3, 50, 700]] = np.nan
    v[[10, 11]] = np.inf
    r = run(v, np.arange(1000.0))
    var = r.variables[0]
    assert var.n == 995 and var.excluded == 5
    assert var.counts.sum() == 995
    assert r.y_unit == "rows"


def test_plain_rows_chunked_equals_whole():
    rng = np.random.default_rng(2)
    v = rng.normal(size=5000)
    v[::97] = np.nan
    whole = run(v, np.arange(5000.0), bins=40)
    chunked = run(v, np.arange(5000.0), bins=40, chunk_rows=777)
    assert np.array_equal(whole.edges, chunked.edges)
    assert np.array_equal(whole.variables[0].counts, chunked.variables[0].counts)
    a, b = whole.variables[0], chunked.variables[0]
    assert a.mean == pytest.approx(b.mean) and a.std == pytest.approx(b.std) and a.median == b.median


def test_plain_rows_stats_match_numpy_median_exact():
    rng = np.random.default_rng(3)
    for n in (999, 1000):
        v = rng.normal(size=n) * 5 + 2
        r = run(v, np.arange(float(n)), chunk_rows=300)
        var = r.variables[0]
        assert var.median == pytest.approx(float(np.median(v)), abs=1e-12)
        assert var.mean == pytest.approx(float(v.mean()))
        assert var.std == pytest.approx(float(v.std()))
        assert not var.median_approx


def test_display_bins_sit_on_fine_bin_boundaries():
    v = np.linspace(0.0, 10.0, 2001)
    r = run(v, np.arange(2001.0), bins=37)
    assert r.bins == 37
    fine_w = (v.max() - v.min()) / H.HIST_FINE_BINS
    k = (r.edges - r.edges[0]) / fine_w
    assert np.allclose(k, np.round(k), atol=1e-6)
    assert r.edges[0] == pytest.approx(0.0) and r.edges[-1] == pytest.approx(10.0)


def test_bins_specified_and_clamped():
    v = np.random.default_rng(4).normal(size=500)
    assert run(v, np.arange(500.0), bins=10).bins == 10
    assert run(v, np.arange(500.0), bins=1).bins == H.HIST_MIN_BINS
    assert run(v, np.arange(500.0), bins=5000).bins == H.HIST_MAX_BINS


def test_auto_bins_follow_freedman_diaconis_within_limits():
    v = np.random.default_rng(5).normal(size=20000)
    r = run(v, np.arange(20000.0))
    iqr = np.subtract(*np.percentile(v, [75, 25]))
    expected = (v.max() - v.min()) / (2 * iqr * len(v) ** (-1 / 3))
    assert abs(r.bins - expected) < 0.05 * expected + 2
    assert H.HIST_MIN_BINS <= r.bins <= H.HIST_MAX_BINS


def test_auto_bins_iqr_zero_uses_sqrt_n():
    v = np.zeros(10000)
    v[:3] = [5.0, -5.0, 7.0]  # IQR is 0 but the range is not
    assert run(v, np.arange(10000.0)).bins == 100  # sqrt(10000)


def test_all_same_value_and_empty_range():
    r = run(np.full(100, 3.0), np.arange(100.0))
    var = r.variables[0]
    assert var.counts.sum() == 100 and var.mean == 3.0 and var.median == 3.0 and var.std == 0.0
    assert r.edges[0] < 3.0 < r.edges[-1]
    empty = compute_histogram(lambda *a: np.empty(0), lambda *a: np.empty(0), ["v"], HistogramSettings(), 0, 0)
    assert empty.variables[0].n == 0 and empty.variables[0].counts.sum() == 0
    allnan = run(np.full(10, np.nan), np.arange(10.0))
    assert allnan.variables[0].n == 0 and allnan.variables[0].excluded == 10 and math.isnan(allnan.variables[0].mean)


def test_variables_share_range_and_bins():
    t = np.arange(1000.0)
    r = run({"a": np.linspace(0, 1, 1000), "b": np.linspace(5, 6, 1000)}, t, names=("a", "b"), bins=20)
    assert r.edges[0] == pytest.approx(0.0) and r.edges[-1] == pytest.approx(6.0)
    a, b = r.variables
    assert len(a.counts) == len(b.counts) == 20
    assert a.counts.sum() == b.counts.sum() == 1000


def test_cancel_raises_between_chunks():
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(Cancelled):
        run(np.arange(1000.0), np.arange(1000.0), chunk_rows=10, cancelled=cancelled)
    assert calls["n"] < 10  # stopped early, not after 100 chunks * passes


# ---------------------------------------------------------------------------
# plain, counted by time
# ---------------------------------------------------------------------------


def test_time_weight_is_proportional_to_rows_for_even_sampling():
    v = np.tile([1.0, 2.0, 3.0, 4.0], 250)
    t = np.arange(1000) * 10.0
    r = run(v, t, weight="time", bins=4)
    c = r.variables[0].counts
    assert r.y_unit == "seconds"
    # each row lasts 10 s, except the last row (0)
    assert c.sum() == pytest.approx(999 * 10.0)
    assert c[0] == pytest.approx(250 * 10.0) and c[3] == pytest.approx(249 * 10.0)  # the last row is a "4"


def test_time_weight_gap_auto_and_explicit_and_counted():
    t = np.concatenate([np.arange(100) * 1.0, 1000.0 + np.arange(100) * 1.0])  # one 901 s gap
    v = np.ones(200)
    auto = run(v, t, weight="time", bins=2)
    var = auto.variables[0]
    assert auto.gap_count == 1
    assert var.counts.sum() == pytest.approx(198.0)  # 99 + 99 s; the gap row and the last row are 0
    assert auto.gap_s == pytest.approx(5.0)
    explicit = run(v, t, weight="time", bins=2, gap_s=2000.0)
    assert explicit.gap_count == 0
    assert explicit.variables[0].counts.sum() == pytest.approx(99 + 901 + 99)
    tight = run(v, t, weight="time", bins=2, gap_s=0.5)
    assert tight.gap_count == 199  # every interval exceeds 0.5 s


def test_time_weight_chunk_boundary_uses_next_row_time():
    t = np.arange(100) * 2.0
    v = np.arange(100.0)
    whole = run(v, t, weight="time", bins=10)
    chunked = run(v, t, weight="time", bins=10, chunk_rows=7)
    assert np.allclose(whole.variables[0].counts, chunked.variables[0].counts)
    assert chunked.variables[0].counts.sum() == pytest.approx(99 * 2.0)


def test_time_weighted_mean_and_approx_median():
    # value 0 lasts 10 s, value 10 lasts 90 s: weighted mean 9
    t = np.array([0.0, 10.0, 100.0])
    v = np.array([0.0, 10.0, 99.0])
    r = run(v, t, weight="time", bins=10, gap_s=1000.0)
    var = r.variables[0]
    assert var.mean == pytest.approx(9.0)
    assert var.median_approx
    assert 9.0 < var.median <= 10.5


def test_time_weight_excludes_nonfinite_value_rows_and_their_time():
    t = np.arange(10) * 1.0
    v = np.ones(10)
    v[4] = np.nan
    var = run(v, t, weight="time", bins=2).variables[0]
    assert var.excluded == 1
    assert var.counts.sum() == pytest.approx(8.0)  # rows 0..8 minus row 4 (the last row, 9, is 0)


def test_time_not_ascending_is_refused_and_nonfinite_time_rows_excluded():
    t = np.arange(10) * 1.0
    t[5] = 2.0
    with pytest.raises(HistogramError) as exc:
        run(np.ones(10), t, weight="time")
    assert exc.value.code == "time_not_ascending"
    with pytest.raises(HistogramError):
        run(np.ones(10), t, mode="bucket", bucket_s=H.DAY_S)
    t2 = np.arange(10) * 1.0
    t2[3] = np.nan
    var = run(np.ones(10), t2, weight="time", bins=2).variables[0]
    assert var.excluded == 1 and var.n == 9
    # row counting never looks at the time axis
    assert run(np.ones(10), t[::-1].copy(), bins=2).variables[0].n == 10


# ---------------------------------------------------------------------------
# bucket edges / validation
# ---------------------------------------------------------------------------


def test_validate_bucket_rules():
    for ok in (60, 600, 3600, 6 * 3600, 86400, 2 * 86400, 7 * 86400, 20 * 60, 12 * 3600):
        assert validate_bucket(ok) is None, ok
    assert validate_bucket(7 * 60) == "bucket_not_divisor"
    assert validate_bucket(5 * 3600) == "bucket_not_divisor"
    assert validate_bucket(1.5 * 86400) == "bucket_not_whole_days"
    assert validate_bucket(0) == "bucket_not_positive"
    assert validate_bucket(float("nan")) == "bucket_not_positive"
    with pytest.raises(HistogramError):
        bucket_edges(0.0, 1e5, 7 * 60, 0)


def test_bucket_edges_origin_and_cover():
    t0, t1 = local_ts(2026, 3, 2, 12), local_ts(2026, 3, 4, 3)
    e = bucket_edges(t0, t1, 86400, 6 * 3600)
    assert e[0] == local_ts(2026, 3, 2, 6)  # before 12:00 and after the 06:00 start
    assert e[-1] > t1 and np.all(np.diff(e) == 86400)
    # before the start time of the day: the previous day's 06:00
    e2 = bucket_edges(local_ts(2026, 3, 2, 3), local_ts(2026, 3, 2, 4), 86400, 6 * 3600)
    assert e2[0] == local_ts(2026, 3, 1, 6)


def test_bucket_edges_too_many_buckets():
    with pytest.raises(HistogramError) as exc:
        bucket_edges(0.0, 3000 * 86400.0, 60, 0)  # 4.3M minute buckets
    assert exc.value.code == "too_many_buckets"
    assert bucket_edges(0.0, 100 * 86400.0, 60, 0).size > 100 * 1440


def test_bucket_edges_multi_day_are_calendar_days():
    e = bucket_edges(local_ts(2026, 3, 2, 10), local_ts(2026, 3, 20), 7 * 86400, 0)
    assert e[0] == local_ts(2026, 3, 2)
    assert np.all(np.diff(e) == 7 * 86400)


@pytest.mark.parametrize("zone,day,hours", [
    ("Europe/Berlin", (2026, 3, 29), 23),
    ("Europe/Berlin", (2026, 10, 25), 25),
    ("America/New_York", (2026, 3, 8), 23),
    ("America/New_York", (2026, 11, 1), 25),
])
def test_bucket_edges_dst_days(tz, zone, day, hours):
    tz(zone)
    y, m, d = day
    t0 = local_ts(y, m, d, 0, 30)
    t1 = t0 + 3600.0  # inside the day
    one_day = bucket_edges(t0, t1, 86400, 0)
    assert len(one_day) == 2 and (one_day[1] - one_day[0]) / 3600 == hours  # a calendar day, 23/25 h
    hourly = bucket_edges(t0, one_day[1] - 1.0, 3600, 0)
    inside = hourly[hourly < one_day[1]]
    assert len(inside) == hours  # 23 or 25 one-hour buckets inside the day
    assert inside[0] == one_day[0] and np.all(np.diff(inside) == 3600)
    assert hourly[-1] == one_day[1]  # the day's end closes the last bucket


@pytest.mark.parametrize("hours", [2, 3, 4, 12])
def test_dst_25h_day_last_bucket_is_one_hour_and_incomplete(tz, hours):
    tz("Europe/Berlin")
    t0 = local_ts(2026, 10, 25, 0, 0)
    day_end = local_ts(2026, 10, 26)
    assert (day_end - t0) / 3600 == 25
    t = t0 + np.arange(0, 25 * 60) * 60.0  # the whole 25 h day at 1-minute rows
    e = bucket_edges(t[0], t[-1], hours * 3600.0, 0)
    inside = e[e <= day_end]
    assert inside[-1] == day_end and inside[-1] - inside[-2] == 3600.0  # the last bucket is 1 h (<= L)
    r = run(np.ones(len(t)), t, mode="bucket", bucket_s=hours * 3600.0, agg="sum", gap_s=1e9)
    var = r.variables[0]
    assert var.incomplete == 1  # only the short last bucket (1 h / L < 0.9)


# ---------------------------------------------------------------------------
# bucket aggregation
# ---------------------------------------------------------------------------

# 3 days of 10-minute data starting at local midnight, value = row index mod 100 + 1
DT = 600.0
N = 3 * 144


def make_week():
    t0 = local_ts(2026, 3, 2)
    t = t0 + np.arange(N) * DT
    v = (np.arange(N) % 100 + 1).astype(float)
    return t, v


def day_slices():
    return [slice(d * 144, (d + 1) * 144) for d in range(3)]


def test_bucket_daily_aggregates_match_hand_calc():
    t, v = make_week()
    sl = day_slices()
    w = np.full(N, DT)
    w[-1] = 0.0  # the last row of the range has no duration
    expect = {
        "sum": [v[s].sum() for s in sl],
        "max": [v[s].max() for s in sl],
        "min": [v[s].min() for s in sl],
        "integral": [(v[s] * w[s]).sum() / 3600.0 for s in sl],
        "mean": [(v[s] * w[s]).sum() / w[s].sum() for s in sl],
    }
    for agg, want in expect.items():
        r = run(v, t, mode="bucket", bucket_s=86400, agg=agg)
        got = r.variables[0].values
        assert np.allclose(got, want), agg
        assert r.variables[0].counts.sum() == 3 and r.y_unit == "buckets"
        assert r.bucket_count == 3


def test_bucket_integral_units():
    t, v = make_week()
    base = run(v, t, mode="bucket", bucket_s=86400, agg="integral", integral_unit_s=1.0).variables[0].values
    hours = run(v, t, mode="bucket", bucket_s=86400, agg="integral", integral_unit_s=3600.0).variables[0].values
    minutes = run(v, t, mode="bucket", bucket_s=86400, agg="integral", integral_unit_s=60.0).variables[0].values
    assert np.allclose(base / 3600.0, hours) and np.allclose(base / 60.0, minutes)
    assert base[0] == pytest.approx(v[:144].sum() * DT)


def test_bucket_hourly_and_ten_minute():
    t, v = make_week()
    r = run(v, t, mode="bucket", bucket_s=3600, agg="sum")
    vals = r.variables[0].values
    assert vals.shape[0] == 72
    assert np.allclose(vals, v.reshape(72, 6).sum(axis=1))
    r10 = run(v, t, mode="bucket", bucket_s=600, agg="max")
    assert np.array_equal(r10.variables[0].values, v)  # one row per bucket


def test_bucket_origin_shifts_boundaries():
    t, v = make_week()
    r = run(v, t, mode="bucket", bucket_s=86400, agg="sum", origin_s=6 * 3600)
    # first bucket starts the previous day 06:00 and holds only the first 6 h (36 rows)
    vals = r.variables[0].values
    assert len(vals) == 4
    assert vals[0] == pytest.approx(v[:36].sum())
    assert vals[1] == pytest.approx(v[36:36 + 144].sum())
    assert vals[3] == pytest.approx(v[36 + 288:].sum())


def test_bucket_incomplete_detection_and_exclusion():
    t, v = make_week()
    sel = np.ones(N, dtype=bool)
    sel[:72] = False  # first 12 h missing
    sel[144 + 20:144 + 100] = False  # a 13 h hole in day 2 (also a gap)
    r = run(v[sel], t[sel], mode="bucket", bucket_s=86400, agg="sum")
    var = r.variables[0]
    assert var.buckets_total == 3
    assert var.incomplete == 2 and r.gap_count >= 1  # day 1 (half) and day 2 (hole); day 3 is whole
    assert var.n == 3
    rx = run(v[sel], t[sel], mode="bucket", bucket_s=86400, agg="sum", exclude_incomplete=True)
    assert rx.variables[0].n == 1 and rx.variables[0].excluded_buckets == 2
    assert rx.variables[0].incomplete == 2


def test_bucket_small_gap_is_not_incomplete():
    n = 3 * 1440
    t = local_ts(2026, 3, 2) + np.arange(n) * 60.0
    keep = np.ones(n, dtype=bool)
    keep[100:130] = False  # a 30 min gap in day 1: 1410/1440 = 0.98 >= 0.9
    r = run(np.ones(n)[keep], t[keep], mode="bucket", bucket_s=86400, agg="sum")
    assert r.variables[0].incomplete == 0
    assert r.gap_count == 1


def test_bucket_empty_buckets_are_not_counted():
    t0 = local_ts(2026, 3, 2)
    t = np.concatenate([t0 + np.arange(144) * DT, t0 + 5 * 86400 + np.arange(144) * DT])
    r = run(np.ones(len(t)), t, mode="bucket", bucket_s=86400, agg="sum", gap_s=1e9)
    assert r.bucket_count == 6 and r.variables[0].buckets_total == 2 and r.variables[0].n == 2


def test_bucket_chunked_equals_whole():
    t, v = make_week()
    v[5] = np.nan
    for agg in H.AGGREGATIONS:
        a = run(v, t, mode="bucket", bucket_s=3600, agg=agg)
        b = run(v, t, mode="bucket", bucket_s=3600, agg=agg, chunk_rows=37)
        assert np.allclose(a.variables[0].values, b.variables[0].values), agg
        assert np.array_equal(a.variables[0].counts, b.variables[0].counts)


def test_bucket_nonfinite_values_excluded_and_stats_from_bucket_values():
    t, v = make_week()
    v[[1, 2]] = np.nan
    var = run(v, t, mode="bucket", bucket_s=86400, agg="sum").variables[0]
    assert var.excluded == 2
    assert var.n == 3
    assert var.mean == pytest.approx(float(var.values.mean()))
    assert var.median == pytest.approx(float(np.median(var.values)))
    assert var.std == pytest.approx(float(var.values.std()))


def test_bucket_multiple_variables_share_edges():
    t, v = make_week()
    r = run({"a": v, "b": v * 2}, t, names=("a", "b"), mode="bucket", bucket_s=86400, agg="sum", bins=5)
    a, b = r.variables
    assert np.allclose(b.values, a.values * 2)
    assert len(a.counts) == len(b.counts) == 5 and a.counts.sum() == b.counts.sum() == 3
    assert r.edges[-1] == pytest.approx(b.values.max())


def test_bucket_in_dst_range_uses_calendar_days(tz):
    tz("Europe/Berlin")
    start = local_ts(2026, 3, 28)
    t = start + np.arange(0, 3 * 1440 - 60) * 60.0  # 1-min rows for 71 h of elapsed time
    r = run(np.ones(len(t)), t, mode="bucket", bucket_s=86400, agg="sum", gap_s=1e9)
    vals = r.variables[0].values
    assert vals[0] == 1440 and vals[1] == 1380  # the 29th has 23 h
    e = bucket_edges(t[0], t[-1], 86400, 0)
    assert e[1] - e[0] == 86400 and e[2] - e[1] == 23 * 3600


def test_bucket_too_many_buckets_via_compute():
    t = np.array([0.0, 4000 * 86400.0])
    with pytest.raises(HistogramError) as exc:
        run(np.ones(2), t, mode="bucket", bucket_s=60)
    assert exc.value.code == "too_many_buckets"


def test_bucket_illegal_length_raises_and_empty_range():
    t, v = make_week()
    with pytest.raises(HistogramError):
        run(v, t, mode="bucket", bucket_s=7 * 60)
    empty = compute_histogram(lambda *a: np.empty(0), lambda *a: np.empty(0), ["v"],
                              HistogramSettings(mode="bucket"), 0, 0)
    assert empty.variables[0].n == 0 and empty.bucket_count == 0


def test_sine_distribution_is_u_shaped():
    t = np.arange(60_000) * 0.01
    v = np.sin(2 * np.pi * t / 5.0)
    c = run(v, t, bins=20).variables[0].counts
    assert c[0] > 2 * c[10] and c[-1] > 2 * c[10]
    assert c[:3].sum() > c[8:12].sum()


def test_auto_bins_for_variables_of_different_scales_use_the_coarsest_width():
    n = 60_000
    t = np.arange(float(n))
    step = ((np.arange(n) // 500) % 2).astype(float)  # 0 / 1
    noise = np.random.default_rng(7).normal(0.5, 0.01, n)
    both = run({"step": step, "noise": noise}, t, names=("step", "noise"))
    alone_noise = run(noise, t, names=("v",))
    assert 2 <= both.bins <= 60  # not hundreds: the step variable's width wins
    assert alone_noise.bins > both.bins  # the narrow variable alone would want many more
    assert both.variables[0].counts.sum() == n


def test_bucket_auto_bins_capped_by_number_of_values():
    t, v = make_week()
    r = run(v, t, mode="bucket", bucket_s=86400, agg="sum")
    assert r.bins <= 3


def test_extreme_values_give_finite_edges():
    v = np.array([-1e308, 1e308, 0.0])
    r = run(v, np.arange(3.0))
    assert np.isfinite(r.edges).all() and np.all(np.diff(r.edges) > 0)
    assert r.variables[0].counts.sum() == 3
    b = run(np.array([-1e308, 1e308, 0.0, 5.0]), local_ts(2026, 3, 2) + np.arange(4) * 86400.0,
            mode="bucket", bucket_s=86400, agg="max")
    assert np.isfinite(b.edges).all()


def test_exact_median_for_mostly_constant_column():
    rng = np.random.default_rng(11)
    v = np.zeros(20000)
    v[rng.choice(20000, 300, replace=False)] = rng.uniform(1, 9, 300)  # median is 0
    r = run(v, np.arange(20000.0), chunk_rows=3000)
    assert r.variables[0].median == float(np.median(v)) == 0.0


@pytest.mark.parametrize("cap", [50, 1000])
def test_exact_median_when_the_median_bin_exceeds_the_collect_cap(monkeypatch, cap):
    monkeypatch.setattr(H, "HIST_MEDIAN_COLLECT_MAX", cap)
    rng = np.random.default_rng(12)
    # a dense cluster (the median bin holds thousands of distinct values) plus far outliers
    v = np.concatenate([rng.normal(100.0, 1e-3, 9000), rng.uniform(0, 1e6, 1001)])
    rng.shuffle(v)
    for n in (len(v), len(v) - 1):
        r = run(v[:n], np.arange(float(n)), chunk_rows=1700)
        assert r.variables[0].median == pytest.approx(float(np.median(v[:n])), abs=1e-12)


def test_exact_median_refinement_with_ties_and_even_count(monkeypatch):
    monkeypatch.setattr(H, "HIST_MEDIAN_COLLECT_MAX", 10)
    v = np.concatenate([np.full(500, 3.0), np.full(500, 3.0 + 1e-9), [0.0, 10.0]])
    r = run(v, np.arange(float(len(v))), chunk_rows=300)
    assert r.variables[0].median == float(np.median(v))


def test_gap_count_is_one_number_independent_of_value_nans():
    t = np.concatenate([np.arange(100) * 1.0, 1000.0 + np.arange(100) * 1.0])  # the gap follows row 99
    v = np.ones(200)
    v_nan = v.copy()
    v_nan[99] = np.nan  # a NaN value on the gap row
    clean = run({"a": v, "b": v}, t, names=("a", "b"), weight="time", bins=2)
    dirty = run({"a": v_nan, "b": v}, t, names=("a", "b"), weight="time", bins=2)
    assert clean.gap_count == dirty.gap_count == 1
    assert not hasattr(clean.variables[0], "gap_count")
    bucket = run({"a": v_nan, "b": v}, t + local_ts(2026, 3, 2), names=("a", "b"), mode="bucket")
    assert bucket.gap_count == 1


def test_time_chunks_are_read_once_per_pass_not_once_per_variable():
    n = 1000
    t = np.arange(float(n))
    names = ("a", "b", "c")
    values = {nm: np.arange(float(n)) + i for i, nm in enumerate(names)}
    reads = {"n": 0}

    def fetch_time(a, b):
        reads["n"] += 1
        return t[a:b]

    settings = HistogramSettings(weight="time", bins=5)
    compute_histogram(lambda nm, a, b: values[nm][a:b], fetch_time, list(names), settings, 0, n, chunk_rows=250)
    chunks = 4
    # plain + time: pass 1 and pass 2 read each chunk once (the old code read it 3 variables x 3 times)
    assert reads["n"] <= 2 * chunks
    reads["n"] = 0
    compute_histogram(lambda nm, a, b: values[nm][a:b], fetch_time, list(names),
                      HistogramSettings(mode="bucket", bucket_s=3600.0), 0, n, chunk_rows=250)
    assert reads["n"] <= 2 * chunks  # time scan pass + the aggregation pass


def test_bucket_count_is_the_buckets_overlapping_the_range():
    day = local_ts(2026, 3, 2)
    # part of one day: 1 hour buckets from 10:00 to 12:30 -> 10, 11, 12 = 3 buckets (not the 24 of the day)
    t = day + 10 * 3600 + np.arange(0, 2.5 * 3600, 60.0)
    r = run(np.ones(len(t)), t, mode="bucket", bucket_s=3600.0, agg="sum")
    assert r.bucket_count == 3
    # a range ending exactly at local midnight: the last row is the first row of day 3 (3 days, no empty 4th)
    t2 = day + np.arange(0, 2 * 86400 + 1, 3600.0)
    r2 = run(np.ones(len(t2)), t2, mode="bucket", bucket_s=86400.0, agg="sum")
    assert r2.bucket_count == 3
    # data ending just before midnight: 2 days, not 3
    t3 = day + np.arange(0, 2 * 86400, 3600.0)
    assert run(np.ones(len(t3)), t3, mode="bucket", bucket_s=86400.0, agg="sum").bucket_count == 2


# -- buckets shorter than the sampling interval (durations split at the bucket edges, 42.3) --------


def _ten_minute_data(n=60, seed=21):
    t = local_ts(2026, 3, 2) + np.arange(n) * 600.0
    v = np.random.default_rng(seed).uniform(1, 9, n)
    return t, v


@pytest.mark.parametrize("chunk_rows", [None, 7, 10])
def test_one_minute_buckets_on_ten_minute_data(chunk_rows):
    t, v = _ten_minute_data()
    n = len(t)
    held = np.repeat(v[:-1], 10)  # minute k is covered by row k // 10 (the last row lasts 0 s)
    integral = run(v, t, mode="bucket", bucket_s=60.0, agg="integral", chunk_rows=chunk_rows)
    var = integral.variables[0]
    assert np.allclose(var.values, held * 60.0 / 3600.0)
    assert var.incomplete == 0 and var.buckets_total == (n - 1) * 10
    mean = run(v, t, mode="bucket", bucket_s=60.0, agg="mean", chunk_rows=chunk_rows).variables[0]
    assert np.allclose(mean.values, held) and mean.incomplete == 0
    mx = run(v, t, mode="bucket", bucket_s=60.0, agg="max", chunk_rows=chunk_rows).variables[0]
    mn = run(v, t, mode="bucket", bucket_s=60.0, agg="min", chunk_rows=chunk_rows).variables[0]
    # the held value counts for buckets without a row; the very last bucket holds the last row itself
    assert np.allclose(mx.values, np.append(held, v[-1])) and np.allclose(mn.values, np.append(held, v[-1]))
    assert mx.buckets_total == (n - 1) * 10 + 1
    # the per-bucket integrals add up to the plain total (sum of v * 600 s)
    assert var.values.sum() * 3600.0 == pytest.approx(float((v[:-1] * 600.0).sum()))


def test_sum_and_count_stay_row_based_for_short_buckets():
    t, v = _ten_minute_data()
    r = run(v, t, mode="bucket", bucket_s=60.0, agg="sum")
    var = r.variables[0]
    # only the buckets that hold a row have a sum: one per row
    assert var.buckets_total == len(v) and np.allclose(np.sort(var.values), np.sort(v))


def test_hourly_integral_splits_rows_across_buckets():
    # 40-minute rows, 1-hour buckets: row intervals straddle bucket edges
    t = local_ts(2026, 3, 2) + np.arange(10) * 2400.0
    v = np.arange(1.0, 11.0)
    r = run(v, t, mode="bucket", bucket_s=3600.0, agg="integral", integral_unit_s=1.0, gap_s=1e9)
    # reference: sample the held signal finely
    fine = np.arange(t[0], t[-1], 1.0)
    ref = v[np.searchsorted(t, fine, side="right") - 1]
    edges = bucket_edges(t[0], t[-1], 3600.0, 0)
    expected = [ref[(fine >= edges[k]) & (fine < edges[k + 1])].sum() for k in range(len(edges) - 1)]
    got_vals = r.variables[0].values
    expected = [e for e in expected if e > 0]
    assert np.allclose(got_vals, expected)


def test_gap_in_the_data_leaves_buckets_without_value():
    t, v = _ten_minute_data(60)
    keep = np.ones(len(t), dtype=bool)
    keep[20:30] = False  # a 100-minute hole after row 19 (auto gap = 50 min)
    r = run(v[keep], t[keep], mode="bucket", bucket_s=600.0, agg="max")
    rows = int(keep.sum())
    assert r.gap_count == 1
    assert r.variables[0].buckets_total == rows  # the hole has neither rows nor a held value
    mean = run(v[keep], t[keep], mode="bucket", bucket_s=600.0, agg="integral").variables[0]
    # row 19 is a gap row (0 s): its bucket has a row but no valid time
    assert mean.buckets_total == rows - 2  # row 19 (gap) and the last row (0 s)


def test_bucket_mode_excludes_nonfinite_time_rows():
    t, v = make_week()
    t = t.copy()
    t[100] = np.nan
    r = run(v, t, mode="bucket", bucket_s=86400, agg="sum")
    var = r.variables[0]
    assert var.excluded == 1
    # the row's value is not in any bucket sum
    assert var.values.sum() == pytest.approx(float(v.sum() - v[100]))
