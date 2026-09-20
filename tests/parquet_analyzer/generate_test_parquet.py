"""Generate a synthetic Parquet file with a daily on/off operating cycle.

One "time" column (millisecond timestamps) plus four numeric columns
(temperature, pressure, rpm, voltage). Samples only exist during a fixed
window each day (default: 8 hours starting at 08:00) -- the remaining 16
hours have no rows at all, simulating equipment that runs one shift a day.

Not a pytest test (see test_generate_test_parquet.py for a fast correctness
check of the same logic on a tiny file); this is a standalone generator for
producing large manual-testing fixtures, in the spirit of
data/raw/sensor_log_4y.parquet. Output is written outside
the repo's tracked tree (data/raw/ is gitignored) since files this size
don't belong in git.

Usage:
    uv run --with numpy --with pyarrow python3 tests/parquet_analyzer/generate_test_parquet.py \\
        --output data/raw/daily_cycle_500mb.parquet --target-size 500MB
    uv run --with numpy --with pyarrow python3 tests/parquet_analyzer/generate_test_parquet.py \\
        --output data/raw/daily_cycle_4gb.parquet --target-size 4GB
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

COLUMNS = ("temperature", "pressure", "rpm", "voltage")

_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}

_MAX_DAYS = 20 * 365  # sanity cap so a miscalculated rate can't loop forever


def parse_size(text: str) -> int:
    match = re.match(r"^\s*([0-9.]+)\s*([A-Za-z]*)\s*$", text)
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    value, unit = match.groups()
    unit = unit.upper() or "B"
    if unit not in _SIZE_UNITS:
        raise ValueError(f"unknown size unit: {unit!r}")
    return int(float(value) * _SIZE_UNITS[unit])


def _make_schema() -> pa.Schema:
    fields = [("time", pa.timestamp("ms"))]
    fields += [(name, pa.float64()) for name in COLUMNS]
    return pa.schema(fields)


def _day_table(day_times: np.ndarray, rng: np.random.Generator, schema: pa.Schema) -> pa.Table:
    n = len(day_times)
    phase = np.linspace(0, 2 * np.pi, n, endpoint=False)
    data = {
        "time": pa.array(day_times),
        "temperature": 20.0 + 5.0 * np.sin(phase) + rng.normal(0, 0.2, n),
        "pressure": 101.3 + rng.normal(0, 0.5, n),
        "rpm": 1500.0 + 300.0 * np.sin(2 * phase) + rng.normal(0, 10.0, n),
        "voltage": 220.0 + rng.normal(0, 1.0, n),
    }
    return pa.table(data, schema=schema)


def generate(
    output_path: str | Path,
    target_bytes: int,
    *,
    start_date: str = "2026-01-01",
    interval_ms: int = 10,
    active_hours: float = 8.0,
    window_start_hour: float = 8.0,
    row_group_rows: int = 100_000,
    seed: int = 0,
) -> Path:
    """Write rows in daily bursts until the file reaches target_bytes, return output_path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    schema = _make_schema()
    start = np.datetime64(f"{start_date}T00:00:00", "ms")
    day_ms = 24 * 3600_000
    window_start_ms = int(round(window_start_hour * 3600_000))
    active_samples_per_day = int(round(active_hours * 3600_000)) // interval_ms

    with open(output_path, "wb") as f:
        writer = pq.ParquetWriter(f, schema)
        try:
            day = 0
            reached_target = False
            while not reached_target:
                day_base_ms = day * day_ms + window_start_ms
                offsets_ms = np.arange(active_samples_per_day, dtype="int64") * interval_ms
                day_times = start + (day_base_ms + offsets_ms).astype("timedelta64[ms]")

                for chunk_start in range(0, active_samples_per_day, row_group_rows):
                    chunk_end = min(chunk_start + row_group_rows, active_samples_per_day)
                    writer.write_table(_day_table(day_times[chunk_start:chunk_end], rng, schema))
                    f.flush()
                    if f.tell() >= target_bytes:
                        reached_target = True
                        break

                day += 1
                if not reached_target and day > _MAX_DAYS:
                    raise RuntimeError(
                        f"generated {day} days ({f.tell()} bytes) without reaching "
                        f"target of {target_bytes} bytes -- check interval_ms/active_hours"
                    )
        finally:
            writer.close()

    return output_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-size", default="500MB", help="e.g. 500MB, 4GB, 3.9GiB")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--interval-ms", type=int, default=10)
    parser.add_argument("--active-hours", type=float, default=8.0)
    parser.add_argument("--window-start-hour", type=float, default=8.0)
    parser.add_argument("--row-group-rows", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    path = generate(
        args.output,
        parse_size(args.target_size),
        start_date=args.start_date,
        interval_ms=args.interval_ms,
        active_hours=args.active_hours,
        window_start_hour=args.window_start_hour,
        row_group_rows=args.row_group_rows,
        seed=args.seed,
    )
    size = path.stat().st_size
    print(f"wrote {path} ({size:,} bytes, {size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    main()
