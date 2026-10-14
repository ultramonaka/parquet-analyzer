"""Fast correctness check for generate_power_sample_parquet.py: verify documented
relations (consumption/regeneration bounds, marker plateaus, gaps) hold on generated data."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from generate_power_sample_parquet import build_table  # noqa: E402


def test_builds_with_default_args():
    """Quick sanity check: can build a small 3-day file."""
    table = build_table(days=3, period_s=10)
    assert table.num_rows > 0
    assert set(table.column_names) == {
        "time",
        "power_kw",
        "consumption_kw",
        "regeneration_kw",
        "temperature_c",
    }


def test_time_is_ascending():
    """Time column must be strictly ascending."""
    table = build_table(days=10, period_s=10)
    t_ms = table["time"].to_numpy().astype("int64")
    diffs = np.diff(t_ms)
    assert (diffs > 0).all(), "time must be strictly ascending"


def test_gaps_on_days_40_41():
    """Days 40 and 41 are entirely missing from the dataset."""
    table = build_table(days=50, period_s=10)

    # Extract all dates present in the dataset
    dates_present = set()
    start_date = datetime(2025, 1, 1).date()
    for ts in table["time"].to_pylist():
        ts_date = ts.date()
        day_idx = (ts_date - start_date).days
        dates_present.add(day_idx)

    # Days 40 and 41 should be completely absent
    assert 40 not in dates_present, "day 40 should have no samples"
    assert 41 not in dates_present, "day 41 should have no samples"


def test_gap_on_day_100():
    """Day 100 has a 3-hour gap (10:00-13:00)."""
    table = build_table(days=105, period_s=10)

    # Extract samples from day 100 only
    start_date = datetime(2025, 1, 1).date()
    day_100_times = []
    for ts in table["time"].to_pylist():
        ts_date = ts.date()
        day_idx = (ts_date - start_date).days
        if day_idx == 100:
            day_100_times.append(ts)

    if len(day_100_times) > 1:
        # Check that there is a gap of at least 3 hours
        times_s = [ts.timestamp() for ts in day_100_times]
        gaps_s = np.diff(times_s)
        max_gap = np.max(gaps_s)
        assert max_gap > 3 * 3600, f"day 100 should have a gap > 3h, got max gap {max_gap}s"


def test_consumption_regeneration_relations():
    """consumption_kw = max(power_kw, 0); regeneration_kw = max(-power_kw, 0)."""
    table = build_table(days=5, period_s=10)
    power = table["power_kw"].to_numpy()
    consumption = table["consumption_kw"].to_numpy()
    regeneration = table["regeneration_kw"].to_numpy()

    np.testing.assert_allclose(consumption, np.maximum(power, 0), rtol=1e-10)
    np.testing.assert_allclose(regeneration, np.maximum(-power, 0), rtol=1e-10)

    # Also check: power = consumption - regeneration
    np.testing.assert_allclose(power, consumption - regeneration, rtol=1e-10)


def test_both_consumption_and_regeneration_present():
    """Dataset must have both positive (consumption) and negative (regeneration) power."""
    table = build_table(days=10, period_s=10)
    power = table["power_kw"].to_numpy()

    assert np.any(power > 0), "should have consumption (positive power)"
    assert np.any(power < 0), "should have regeneration (negative power)"


def test_marker_is_daily_peak_and_daily_max_of_3min_means():
    """Each day's peak and max 3-minute mean are exactly 100 + (day % 7) * 10 kW, at 12:00."""
    import polars as pl

    df = pl.from_arrow(build_table(days=14, period_s=10))
    df = df.with_columns(day=pl.col("time").dt.date())
    means = (
        df.group_by_dynamic("time", every="3m")
        .agg(pl.col("power_kw").mean().alias("m"))
        .with_columns(day=pl.col("time").dt.date())
    )
    start = datetime(2025, 1, 1).date()
    for (day,), g in df.group_by("day"):
        expected = 100.0 + ((day - start).days % 7) * 10
        peak_row = g.sort("power_kw", descending=True).row(0, named=True)
        assert peak_row["power_kw"] == expected
        assert peak_row["time"].hour == 12 and peak_row["time"].minute < 3
        day_means = means.filter(pl.col("day") == day)
        assert day_means["m"].max() == expected
        assert g.filter(pl.col("time").dt.hour() != 12)["power_kw"].max() <= 95.0


def test_regeneration_every_day():
    """Every day has regeneration, so a daily regeneration histogram is meaningful."""
    import polars as pl

    df = pl.from_arrow(build_table(days=14, period_s=10))
    per_day = df.group_by(pl.col("time").dt.date()).agg(pl.col("regeneration_kw").max())
    assert per_day["regeneration_kw"].min() >= 9.0


def test_row_count_for_small_dataset():
    """Row count should be reasonable for a small dataset."""
    days = 5
    period_s = 10
    table = build_table(days=days, period_s=period_s)

    # Expected: 5 days * (86400 / 10) = 5 * 8640 = 43200 rows (no gaps in day 0-4)
    samples_per_day = 24 * 3600 // period_s
    expected_rows = days * samples_per_day

    assert table.num_rows == expected_rows, f"expected {expected_rows} rows, got {table.num_rows}"
