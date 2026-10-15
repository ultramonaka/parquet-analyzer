"""core/histogram.py + histogram_export.py: the time-of-day view (analysis.ja.md 42.13, 42.14)."""

from __future__ import annotations

import calendar
import json
import time as _time
from datetime import datetime

import numpy as np
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import histogram_export as hexport
from parquet_analyzer.core.histogram import (
    HistogramError,
    HistogramSettings,
    TimeFilter,
    compute_histogram,
    format_clock,
)

HOUR = 3600
DAY = 86400
MONDAY = float(calendar.timegm((2026, 3, 2, 0, 0, 0)))  # the tests run in UTC unless a test sets a zone


@pytest.fixture
def tz(monkeypatch):
    def set_tz(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        _time.tzset()

    set_tz("UTC")
    yield set_tz
    monkeypatch.undo()
    _time.tzset()


def run(values, t, is_dt=True, chunk_rows=None, **kw):
    if not isinstance(values, dict):
        values = {"v": values}
    kw.setdefault("view", "timeofday")
    return compute_histogram(
        lambda nm, a, b: values[nm][a:b], lambda a, b: t[a:b], list(values), HistogramSettings(**kw),
        0, len(t), chunk_rows=chunk_rows, time_is_datetime=is_dt,
    )


def hourly_week(days=3, step=60, start=MONDAY):
    """`days` days from `start`, one row per `step` s; value = the hour of day (0..23)."""
    t = start + np.arange(days * DAY // step) * float(step)
    return t, (np.mod(t, DAY) // HOUR).astype(float)


def test_hour_slots_mean_is_the_hour(tz):
    t, v = hourly_week()
    r = run(v, t, slot_s=3600.0)
    var = r.variables[0]
    assert len(r.slot_start) == 24
    np.testing.assert_allclose(var.slot_values, np.arange(24))
    assert (var.slot_days == 3).all()
    np.testing.assert_allclose(var.slot_valid_s, 3 * 3600.0, atol=61)  # the last row has no successor (60 s lost)
    assert r.y_unit == "slots"


def test_slot_shorter_than_sample_interval(tz):
    # 30-min samples, a row holds its value until the next row; 10-min slots see it unchanged
    t = MONDAY + np.arange(2 * 48) * 1800.0
    v = np.tile(np.arange(48.0), 2)
    r = run(v, t, slot_s=600.0)
    var = r.variables[0]
    assert len(var.slot_values) == 144
    expected = np.repeat(np.arange(48.0), 3)
    # the very last row lasts 0 s (no successor), so the final half hour is absent on the 2nd day
    np.testing.assert_allclose(var.slot_values[:-3], expected[:-3])
    assert (var.slot_days[:-3] == 2).all()


def test_integral_per_day_does_not_count_missing_days(tz):
    t, _ = hourly_week(step=60)
    v = np.full(len(t), 5.0)
    keep = ~((t >= MONDAY + DAY + 12 * HOUR) & (t < MONDAY + DAY + 18 * HOUR))  # day 2 lacks 12:00-18:00
    t, v = t[keep], v[keep]
    per_day = run(v, t, slot_s=3600.0, tod_agg="integral", tod_per_day=True, integral_unit_s=3600.0)
    total = run(v, t, slot_s=3600.0, tod_agg="integral", tod_per_day=False, integral_unit_s=3600.0)
    pv, tv = per_day.variables[0], total.variables[0]
    assert pv.slot_days[13] == 2 and pv.slot_days[3] == 3
    # 5 kW held for one hour = 5 kWh per day, whatever the number of valid days
    assert pv.slot_values[13] == pytest.approx(5.0, rel=0.01)
    assert pv.slot_values[3] == pytest.approx(5.0, rel=0.01)
    assert tv.slot_values[13] == pytest.approx(10.0, rel=0.01)  # 2 days
    assert tv.slot_values[3] == pytest.approx(15.0, rel=0.01)   # 3 days


def test_integral_unit_applies(tz):
    t, _ = hourly_week(days=1)
    v = np.full(len(t), 2.0)
    r = run(v, t, slot_s=3600.0, tod_agg="integral", tod_per_day=False, integral_unit_s=60.0)
    assert r.variables[0].slot_values[5] == pytest.approx(2.0 * 60.0, rel=0.02)  # 2 per s x 3600 s / 60


def test_origin_shifts_the_axis(tz):
    t, v = hourly_week()
    r = run(v, t, slot_s=3600.0, origin_s=6 * HOUR)
    var = r.variables[0]
    assert var.slot_values[0] == pytest.approx(6.0)   # the first slot is 06:00
    assert var.slot_values[18] == pytest.approx(0.0)  # 06:00 + 18 h = 00:00 (the next calendar day)
    assert var.slot_values[23] == pytest.approx(5.0)
    assert format_clock(6 * HOUR + r.slot_start[0]) == "06:00"


def test_time_filter_leaves_other_slots_without_value(tz):
    t, v = hourly_week(days=7)
    tf = TimeFilter(True, 9 * HOUR, 17 * HOUR, (0, 1, 2, 3, 4))
    r = run(v, t, slot_s=3600.0, time_filter=tf)
    var = r.variables[0]
    inside = np.arange(9, 17)
    assert np.isfinite(var.slot_values[inside]).all()
    outside = np.setdiff1d(np.arange(24), inside)
    assert np.isnan(var.slot_values[outside]).all()
    assert (var.slot_days[inside] == 5).all()  # weekdays only
    assert (var.slot_days[outside] == 0).all()
    np.testing.assert_allclose(var.slot_values[inside], inside)
    assert r.target_seconds == pytest.approx(5 * 8 * HOUR, rel=0.01)


def test_numeric_time_axis_uses_axis_zero_as_day_start():
    t = np.arange(2 * DAY, dtype=np.float64)
    t = t[::60]
    v = (np.mod(t, DAY) // HOUR).astype(float)
    r = run(v, t, is_dt=False, slot_s=3600.0)
    np.testing.assert_allclose(r.variables[0].slot_values, np.arange(24))


def test_chunk_size_does_not_change_the_result(tz):
    t, v = hourly_week(days=3, step=300)
    whole = run(v, t, slot_s=900.0, tod_agg="integral", tod_per_day=False)
    chunked = run(v, t, slot_s=900.0, tod_agg="integral", tod_per_day=False, chunk_rows=37)
    a, b = whole.variables[0], chunked.variables[0]
    np.testing.assert_allclose(a.slot_values, b.slot_values)
    np.testing.assert_array_equal(a.slot_days, b.slot_days)


def test_nan_values_are_excluded_and_counted(tz):
    t, v = hourly_week(days=2, step=300)
    v[::7] = np.nan
    r = run(v, t, slot_s=3600.0)
    assert r.variables[0].excluded == len(v[::7])
    assert np.isfinite(r.variables[0].slot_values).all()


def test_dst_autumn_25h_day_folds_equal_wall_clock_times(tz):
    tz("Europe/Berlin")
    t0 = datetime(2026, 10, 24, 0, 0).timestamp()
    n = 49 * 60  # 24th, the 25 h day of the 25th, up to the 26th 00:00
    t = t0 + np.arange(n) * 60.0
    wall = np.array([datetime.fromtimestamp(x).hour for x in t], dtype=float)
    r = run(wall, t, slot_s=3600.0)
    var = r.variables[0]
    np.testing.assert_allclose(var.slot_values, np.arange(24))
    assert var.slot_days[2] == 3  # 24th once, the 25th twice (02:00 happens twice)
    assert var.slot_days[5] == 2


def test_dst_spring_23h_day_has_no_02_slot_that_day(tz):
    tz("Europe/Berlin")
    t0 = datetime(2026, 3, 28, 0, 0).timestamp()
    t = t0 + np.arange(70 * 60) * 60.0  # up to the evening of 2026-03-30
    wall = np.array([datetime.fromtimestamp(x).hour for x in t], dtype=float)
    r = run(wall, t, slot_s=3600.0)
    var = r.variables[0]
    assert var.slot_days[2] == 2  # 28th and 30th; the 29th has no 02:00
    assert var.slot_days[5] == 3  # 28th, 29th, 30th
    np.testing.assert_allclose(var.slot_values[:22], np.arange(22))


def test_bad_inputs_are_refused_not_crashed(tz):
    t, v = hourly_week(days=1)
    with pytest.raises(HistogramError):
        run(v, t, slot_s=7 * 60.0)  # 7 min does not divide a day
    with pytest.raises(HistogramError):
        run(v, t, tod_agg="max")
    bad = t.copy()
    bad[100], bad[101] = bad[101], bad[100]
    with pytest.raises(HistogramError) as e:
        run(v, bad)
    assert e.value.code == "time_not_ascending"


def test_all_time_nan_gives_empty_slots(tz):
    t = np.full(10, np.nan)
    r = run(np.arange(10.0), t, slot_s=3600.0)
    assert np.isnan(r.variables[0].slot_values).all()
    assert r.variables[0].excluded == 10


def test_two_variables_share_the_slots(tz):
    t, v = hourly_week()
    r = run({"a": v, "b": v * 2}, t, slot_s=3600.0)
    np.testing.assert_allclose(r.variables[1].slot_values, np.arange(24) * 2.0)


# -- export -----------------------------------------------------------------------


def test_export_slot_rows_parquet_and_csv(tz, tmp_path):
    t, v = hourly_week()
    r = run(v, t, slot_s=3600.0, origin_s=6 * HOUR)
    cols, mapping = hexport.build_columns(r, True)
    assert list(cols) == ["slot_start", "v", "v_valid_s", "v_days"]
    assert cols["slot_start"][0] == "06:00" and cols["slot_start"][-1] == "05:00"
    assert len(cols["slot_start"]) == 24
    meta = hexport.build_metadata(r, source_path="x.parquet", time_column="time", time_is_datetime=True,
                                  range_s=None, app_version="t", columns=mapping)
    assert meta["time_of_day"]["slot_s"] == 3600.0 and meta["view"] == "timeofday"
    pq_path = tmp_path / "slots.parquet"
    assert hexport.write_parquet(pq_path, cols, meta) == 24
    table = pq.read_table(pq_path)
    assert table.column("slot_start").to_pylist()[0] == "06:00"
    assert table.column("v_days").to_pylist()[0] == 3
    assert json.loads(table.schema.metadata[b"parquet_analyzer"])["time_of_day"]["aggregation"] == "mean"
    csv_path = tmp_path / "slots.csv"
    assert hexport.write_csv(csv_path, cols, meta) == 24
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "slot_start,v,v_valid_s,v_days"
    assert lines[1].startswith("06:00,6.0,")
    assert hexport.sidecar_path(csv_path).exists()


def test_export_empty_slot_value_is_null(tz, tmp_path):
    t, v = hourly_week()
    tf = TimeFilter(True, 9 * HOUR, 10 * HOUR, tuple(range(7)))
    r = run(v, t, slot_s=3600.0, time_filter=tf)
    cols, _mapping = hexport.build_columns(r, True)
    path = tmp_path / "s.parquet"
    hexport.write_parquet(path, cols, {"x": 1})
    vals = pq.read_table(path).column("v").to_pylist()
    assert vals[0] is None and vals[9] == pytest.approx(9.0)


def test_default_file_stem_for_slots(tz):
    t, v = hourly_week()
    r = run(v, t, slot_s=1800.0)
    assert hexport.default_file_stem(r) == "v_tod_30min_mean"


def test_metadata_for_slots_has_no_bucket_block_and_lists_all_columns(tz):
    t, v = hourly_week()
    r = run({"v": v, "v_days": v}, t, slot_s=3600.0)  # a variable named like a derived column: de-dup renames
    cols, mapping = hexport.build_columns(r, True)
    meta = hexport.build_metadata(r, source_path="x", time_column="time", time_is_datetime=True,
                                  range_s=None, app_version="t", columns=mapping)
    assert meta["buckets"] is None and "bucket_count" not in meta and meta["slot_count"] == 24
    assert set(meta["columns"]) == {"slot_start", "variables", "valid_s", "days"}
    for kind in ("variables", "valid_s", "days"):
        for col in meta["columns"][kind].values():
            assert col in cols
    assert len(set(meta["columns"]["days"].values())) == 2  # the clash was de-duplicated


def test_metadata_for_buckets_unchanged(tz):
    t, v = hourly_week()
    r = run(v, t, view="dist", mode="bucket", bucket_s=86400.0, agg="mean")
    cols, mapping = hexport.build_columns(r, True)
    meta = hexport.build_metadata(r, source_path="x", time_column="time", time_is_datetime=True,
                                  range_s=None, app_version="t", columns=mapping)
    assert meta["buckets"]["length_s"] == 86400.0 and meta["bucket_count"] == r.bucket_count and "slot_count" not in meta


def test_dst_spring_day_with_3h_slots_pins_the_documented_behaviour(tz):
    """23 h day, 3 h slots: windows start at wall 0, 4, 7, 10, ... and go to the slot containing their
    wall-clock start (42.13), so every slot is filled once and slot 1 holds the window 04:00-07:00."""
    tz("Europe/Berlin")
    t0 = datetime(2026, 3, 29, 0, 0).timestamp()
    t = t0 + np.arange(23 * 60) * 60.0
    wall = np.array([datetime.fromtimestamp(x).hour for x in t], dtype=float)
    r = run(wall, t, slot_s=3 * 3600.0)
    var = r.variables[0]
    assert (var.slot_days[:7] == 1).all()
    assert var.slot_values[0] == pytest.approx((0 + 1 + 3) / 3, rel=0.02)  # wall 00-02 then 03 (02:00 does not exist)
    assert var.slot_values[1] == pytest.approx(5.0, rel=0.02)  # wall 04:00-07:00


# -- partly covered days (ユーザー判断 2026-10-15, 42.13: counted as whole days, reported separately) --


def test_partial_days_only_where_a_day_covers_the_slot_partly(tz):
    t, v = hourly_week(days=3)
    var = run(v, t, slot_s=3600.0).variables[0]
    # the last row lasts 0 s, so only the final slot of the final day is partly covered
    assert var.slot_partial_days[:23].sum() == 0
    assert var.slot_partial_days[23] == 1
    assert var.slot_days[23] == 3


def test_partial_days_at_the_edge_of_a_gap(tz):
    t, _ = hourly_week(step=60)
    v = np.full(len(t), 5.0)
    keep = ~((t >= MONDAY + DAY + 12 * HOUR + 30 * 60) & (t < MONDAY + DAY + 18 * HOUR))  # day 2 stops at 12:30
    t, v = t[keep], v[keep]
    var = run(v, t, slot_s=3600.0, tod_agg="integral", tod_per_day=True, integral_unit_s=3600.0).variables[0]
    assert var.slot_days[12] == 3 and var.slot_partial_days[12] == 1  # half an hour on day 2
    assert var.slot_partial_days[11] == 0 and var.slot_partial_days[13] == 0
    # a partial day still counts as a day: (1 + 1 + 0.5) h x 5 / 3 days, a little below 5
    assert var.slot_values[12] == pytest.approx(5.0 * 2.5 / 3, rel=0.02)


def test_partial_days_use_the_target_time_under_a_time_filter(tz):
    t, v = hourly_week(days=7)
    tf = TimeFilter(True, 9 * HOUR + 30 * 60, 17 * HOUR, (0, 1, 2, 3, 4))
    var = run(v, t, slot_s=3600.0, time_filter=tf).variables[0]
    # 09:30-10:00 is the whole target of the 09:00 slot: fully covered, not partial
    assert var.slot_days[9] == 5 and var.slot_partial_days[9] == 0
    assert var.slot_partial_days[10:17].sum() == 0
