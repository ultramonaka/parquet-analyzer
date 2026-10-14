"""Generate a synthetic electric power log with realistic profiles for testing time-window aggregation.

Generates a long-period power consumption log with known daily markers for validating
histogram daily aggregations (peak power, 3-minute-mean daily max, daily energy in kWh)
and consumption vs regeneration tracking. The net power (consumption + regeneration)
has a realistic daily profile with morning and evening peaks, noise, spikes, and solar
regeneration events.

Columns:
- time: timestamps (ascending, with intentional gaps to test incomplete windows)
- power_kw: net power (positive = consumption, negative = regeneration)
- consumption_kw: max(power_kw, 0)
- regeneration_kw: max(-power_kw, 0)
- temperature_c: slow daily/seasonal sinusoid with noise

Known marker for manual verification: at 12:00:00 each day, a plateau of exactly
100 + (day_index % 7) * 10 kW for exactly 3 minutes (no noise, deterministic).
All other consumption is capped at 95 kW, so on every day that has its 12:00 data, the
daily peak and the daily max of 3-minute means are exactly the marker value, at 12:00.
(Day 100's marker falls inside its 10:00–13:00 gap, so that day shows a normal value.)

Gaps (missing data periods):
- All samples for days 40 and 41 (if days > 41).
- 3-hour block (10:00–13:00) on day 100 (if days > 100).

Example expressions to verify:
- sum(consumption_kw) * period_s / 3600  =>  total energy consumed in kWh
- sum(regeneration_kw) * period_s / 3600  =>  total energy regenerated in kWh

Output goes to data/raw/ by default (gitignored, per-machine test data).

Usage:
    uv run python tests/parquet_analyzer/generate_power_sample_parquet.py
    uv run python tests/parquet_analyzer/generate_power_sample_parquet.py \\
        --output data/raw/power_sample.parquet --days 30 --period-s 10 --seed 42
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# Cap for everything except the daily marker (the marker is 100…160 kW).
MAX_OTHER_KW = 95.0


def build_table(
    days: int = 365,
    period_s: float = 10.0,
    start: str = "2025-01-01",
    seed: int = 0,
) -> pa.Table:
    """Build the power log table.

    Args:
        days: number of days to simulate
        period_s: sample period in seconds
        start: start date (YYYY-MM-DD format, local naive time)
        seed: random seed for reproducibility

    Returns:
        PyArrow Table with time, power_kw, consumption_kw, regeneration_kw, temperature_c.
    """
    rng = np.random.default_rng(seed)

    # Build timestamp array with gaps
    timestamps_ms = []
    power_values = []
    temps = []

    start_dt = np.datetime64(start, "D")
    base_ms = start_dt.astype("datetime64[ms]").astype("int64")

    for day_idx in range(days):
        day_start_ms = base_ms + day_idx * 24 * 3600 * 1000

        # Skip days 40 and 41
        if day_idx == 40 or day_idx == 41:
            continue

        # Generate timestamps for this day
        samples_per_day = int(24 * 3600 / period_s)
        day_offsets_s = np.arange(samples_per_day) * period_s

        # Skip 3-hour block on day 100 (10:00–13:00)
        if day_idx == 100:
            skip_start_s = 10 * 3600
            skip_end_s = 13 * 3600
            mask = (day_offsets_s < skip_start_s) | (day_offsets_s >= skip_end_s)
            day_offsets_s = day_offsets_s[mask]

        day_offsets_ms = (day_offsets_s * 1000).astype("int64")
        day_timestamps_ms = day_start_ms + day_offsets_ms
        timestamps_ms.append(day_timestamps_ms)

        # Generate power for this day
        hour_of_day = day_offsets_s / 3600.0
        day_of_year = day_idx % 365

        # Base load (~20 kW at night)
        base_load = 20.0

        # Daily profile: morning (08–10 h) and evening (18–21 h) peaks, weekday/weekend
        is_weekend = (day_idx % 7) >= 5
        weekday_factor = 1.0 if not is_weekend else 0.6

        # Morning peak: 08–10 h, gaussian-like
        morning_peak = (
            40.0 * weekday_factor * np.exp(-((hour_of_day - 9) ** 2) / 0.5)
        )

        # Evening peak: 18–21 h, gaussian-like
        evening_peak = (
            50.0 * weekday_factor * np.exp(-((hour_of_day - 19.5) ** 2) / 0.7)
        )

        daily_profile = base_load + morning_peak + evening_peak

        # Seasonal factor (±25%, higher in summer and winter)
        seasonal_phase = 2 * np.pi * day_of_year / 365
        seasonal_factor = 1.0 + 0.25 * np.sin(seasonal_phase)

        # Noise and spikes
        noise = rng.normal(0, 2.0, len(day_offsets_s))

        # Short consumption spikes: a few per day, 1–5 minutes long, +30…+80 kW
        spikes = np.zeros_like(day_offsets_s, dtype=np.float64)
        num_spikes = rng.integers(2, 5)
        for _ in range(num_spikes):
            spike_start_h = rng.uniform(0, 24)
            spike_duration_s = rng.uniform(60, 300)
            spike_magnitude = rng.uniform(30, 80)
            spike_mask = (hour_of_day >= spike_start_h) & (
                hour_of_day < spike_start_h + spike_duration_s / 3600
            )
            spikes[spike_mask] += spike_magnitude

        # Regeneration events during daytime (07–22 h): 4–10 per day, 30 s – 5 min long,
        # -10…-50 kW. During an event the net power is the (negative) regeneration value.
        regen_mask_all = np.zeros_like(day_offsets_s, dtype=bool)
        regen = np.zeros_like(day_offsets_s, dtype=np.float64)
        for _ in range(int(rng.integers(4, 11))):
            regen_start_h = rng.uniform(7, 22)
            regen_duration_s = rng.uniform(30, 300)
            regen_magnitude = rng.uniform(10, 50)
            regen_mask = (hour_of_day >= regen_start_h) & (
                hour_of_day < regen_start_h + regen_duration_s / 3600
            )
            regen[regen_mask] = -regen_magnitude + rng.normal(0, 1.0, int(regen_mask.sum()))
            regen_mask_all |= regen_mask

        # Known marker at 12:00:00: 100 + (day_index % 7) * 10 kW for 3 minutes
        marker_value = 100.0 + (day_idx % 7) * 10
        marker_mask = (hour_of_day >= 12.0) & (hour_of_day < 12.0 + 3 / 60)
        marker = np.zeros_like(day_offsets_s, dtype=np.float64)
        marker[marker_mask] = marker_value

        # Combine: consumption capped below the marker so the marker is each day's maximum
        # (also of 3-minute means); regeneration events replace the consumption; the marker
        # overrides everything and is never clipped.
        power = np.clip(daily_profile * seasonal_factor + noise + spikes, 0.0, MAX_OTHER_KW)
        power[regen_mask_all] = regen[regen_mask_all]
        power[marker_mask] = marker_value

        power_values.append(power)

        # Temperature: slow daily/seasonal sinusoid with noise
        # Daily cycle: warmer during day, cooler at night
        # Seasonal cycle: warmer in summer, cooler in winter
        daily_temp = 10.0 * np.sin(2 * np.pi * hour_of_day / 24 - np.pi / 2)
        seasonal_temp = 15.0 + 10.0 * np.sin(seasonal_phase)
        temp_noise = rng.normal(0, 0.5, len(day_offsets_s))
        temps.append(seasonal_temp + daily_temp + temp_noise)

    # Flatten and create table
    timestamps_ms = np.concatenate(timestamps_ms)
    power_values = np.concatenate(power_values)
    temps = np.concatenate(temps)

    time_array = timestamps_ms.astype("datetime64[ms]")
    consumption_kw = np.maximum(power_values, 0.0)
    regeneration_kw = np.maximum(-power_values, 0.0)

    return pa.table(
        {
            "time": pa.array(time_array, type=pa.timestamp("ms")),
            "power_kw": power_values,
            "consumption_kw": consumption_kw,
            "regeneration_kw": regeneration_kw,
            "temperature_c": temps,
        }
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/power_sample.parquet"),
        help="output Parquet file path",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="number of days to simulate",
    )
    parser.add_argument(
        "--start",
        default="2025-01-01",
        help="start date (YYYY-MM-DD format)",
    )
    parser.add_argument(
        "--period-s",
        type=float,
        default=10.0,
        help="sample period in seconds",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="random seed",
    )
    args = parser.parse_args(argv)

    start_time = time.time()
    table = build_table(args.days, args.period_s, args.start, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.output)
    elapsed_s = time.time() - start_time

    size_bytes = args.output.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    # Calculate min/max power and energy totals
    power = table["power_kw"].to_numpy()
    consumption = table["consumption_kw"].to_numpy()
    regeneration = table["regeneration_kw"].to_numpy()

    min_power = float(np.min(power))
    max_power = float(np.max(power))
    total_consumption_kwh = float(np.sum(consumption)) * args.period_s / 3600
    total_regeneration_kwh = float(np.sum(regeneration)) * args.period_s / 3600

    print(
        f"wrote {args.output} ({table.num_rows:,} rows, {len(table.column_names)} columns, "
        f"{size_mb:.1f} MB) in {elapsed_s:.1f}s"
    )
    print(
        f"  power: {min_power:+.1f}…{max_power:+.1f} kW | "
        f"consumption {total_consumption_kwh:,.1f} kWh | "
        f"regeneration {total_regeneration_kwh:,.1f} kWh"
    )


if __name__ == "__main__":
    main()
