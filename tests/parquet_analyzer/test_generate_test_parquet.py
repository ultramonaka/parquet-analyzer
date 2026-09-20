from __future__ import annotations

import pyarrow.parquet as pq

from generate_test_parquet import COLUMNS, generate, parse_size


def test_parse_size():
    assert parse_size("500MB") == 500_000_000
    assert parse_size("4GB") == 4_000_000_000
    assert parse_size("3.9GiB") == int(3.9 * 1024**3)
    assert parse_size("1024") == 1024


def test_generate_writes_schema_and_stops_near_target(tmp_path):
    path = tmp_path / "sample.parquet"
    out = generate(
        path,
        target_bytes=20_000,
        interval_ms=60_000,  # 1 sample/minute
        active_hours=8,
        window_start_hour=8,
        row_group_rows=100,
        seed=1,
    )

    assert out == path
    assert path.stat().st_size >= 20_000

    table = pq.read_table(path)
    assert table.column_names == ["time"] + list(COLUMNS)
    assert table.num_rows > 0


def test_generate_only_produces_rows_inside_daily_window(tmp_path):
    path = tmp_path / "sample.parquet"
    generate(
        path,
        target_bytes=20_000,
        interval_ms=60_000,
        active_hours=8,
        window_start_hour=8,
        row_group_rows=100,
        seed=1,
    )

    times = pq.read_table(path, columns=["time"]).column("time").to_pylist()
    hours = [t.hour + t.minute / 60.0 for t in times]
    assert all(h >= 8 for h in hours)
    assert all(h < 16 for h in hours)  # 8 active hours starting at 08:00 -> window is [08:00, 16:00)


def test_generate_leaves_a_gap_between_days(tmp_path):
    path = tmp_path / "sample.parquet"
    generate(
        path,
        target_bytes=8_000,  # bigger than one day's ~4KB, so at least 2 days get written
        interval_ms=60_000,
        active_hours=1,
        window_start_hour=8,
        row_group_rows=1000,
        seed=1,
    )

    times = pq.read_table(path, columns=["time"]).column("time").to_pylist()
    assert len(times) >= 2
    # crossing from day N's 09:00 cutoff to day N+1's 08:00 start is a ~23h gap,
    # much larger than the 1-minute sampling interval within a day
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    assert max(gaps) > 3600 * 20
