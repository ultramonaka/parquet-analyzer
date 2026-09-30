"""Generate a small synthetic Parquet file for manually checking app features.

Unlike generate_test_parquet.py (large files for memory/performance testing), this
writes a few MB in well under a second, with columns whose relationships are known
exactly, so the result of a derived-variable expression can be checked by eye:

    column        definition                         expression to try -> expected
    ------------  ---------------------------------  ------------------------------------
    sine          sin(2*pi * 0.1Hz * t)              sine**2 + cosine**2   -> 1 everywhere
    cosine        cos(2*pi * 0.1Hz * t)
    offset_sine   sine + 10                          offset_sine - sine    -> 10 everywhere
    ramp          t in seconds since start           delta(ramp)           -> 1/rate (0.01 at 100Hz)
    step          0/1 square wave, 60 s period       step * sine           -> sine only while step is 1
    noise         Gaussian, mean 0, std 1            rolling_mean(noise, 100) -> ~0
    with_nan      sine, NaN for 5 s every minute     abs(with_nan)         -> gaps stay gaps

The time axis also has one real gap (no rows at all, default 30 s in the middle),
to check that plots/navigator/cursors handle discontinuous sampling.

Output goes to data/raw/ by default (gitignored, per-machine test data).

Usage:
    uv run python tests/parquet_analyzer/generate_feature_test_parquet.py
    uv run python tests/parquet_analyzer/generate_feature_test_parquet.py \\
        --output data/raw/feature_test.parquet --duration-s 600 --rate-hz 100
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SINE_HZ = 0.1
STEP_PERIOD_S = 60.0
NAN_EVERY_S = 60.0
NAN_LENGTH_S = 5.0


def build_table(
    duration_s: float = 600.0,
    rate_hz: float = 100.0,
    gap_s: float = 30.0,
    start: str = "2026-01-01T00:00:00",
    seed: int = 0,
) -> pa.Table:
    n = int(duration_s * rate_hz)
    t = np.arange(n, dtype=np.float64) / rate_hz  # seconds since start, before the gap
    if gap_s > 0:
        t[n // 2 :] += gap_s  # no rows at all during the gap
    rng = np.random.default_rng(seed)

    sine = np.sin(2 * np.pi * SINE_HZ * t)
    with_nan = sine.copy()
    with_nan[(t % NAN_EVERY_S) < NAN_LENGTH_S] = np.nan

    time = np.datetime64(start, "ms") + np.round(t * 1000).astype("int64") * np.timedelta64(1, "ms")
    return pa.table(
        {
            "time": pa.array(time, type=pa.timestamp("ms")),
            "sine": sine,
            "cosine": np.cos(2 * np.pi * SINE_HZ * t),
            "offset_sine": sine + 10.0,
            "ramp": t,
            "step": ((t % STEP_PERIOD_S) < STEP_PERIOD_S / 2).astype(np.float64),
            "noise": rng.normal(size=n),
            "with_nan": with_nan,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=Path("data/raw/feature_test.parquet"))
    parser.add_argument("--duration-s", type=float, default=600.0, help="sampled duration (excluding the gap)")
    parser.add_argument("--rate-hz", type=float, default=100.0)
    parser.add_argument("--gap-s", type=float, default=30.0, help="length of the no-rows gap; 0 for none")
    parser.add_argument("--start", default="2026-01-01T00:00:00")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    table = build_table(args.duration_s, args.rate_hz, args.gap_s, args.start, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.output)
    size_kb = args.output.stat().st_size / 1024
    print(f"wrote {args.output} ({table.num_rows:,} rows, {len(table.column_names)} columns, {size_kb:,.0f} KiB)")


if __name__ == "__main__":
    main()
