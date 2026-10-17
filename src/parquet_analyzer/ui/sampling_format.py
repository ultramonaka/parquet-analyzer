"""Formatting of the sampling rate / interval for the status bar and the stats table
(data 60.2): 3 significant digits, SI units chosen so the number stays below 1000."""

from __future__ import annotations

import math

# (unit, size in seconds / Hz), ascending
_INTERVAL_UNITS = [("ns", 1e-9), ("µs", 1e-6), ("ms", 1e-3), ("s", 1.0), ("min", 60.0)]
_RATE_UNITS = [("Hz", 1.0), ("kHz", 1e3), ("MHz", 1e6)]


def _format_scaled(value: float, units: list[tuple[str, float]]) -> str:
    """Pick the largest unit not above `value` (the smallest unit takes anything below it), format
    with 3 significant digits, and step to the next unit when rounding reaches the next unit's size."""
    idx = 0
    for i, (_, size) in enumerate(units):
        if value >= size:
            idx = i
    while idx + 1 < len(units) and float(f"{value / units[idx][1]:.3g}") * units[idx][1] >= units[idx + 1][1]:
        idx += 1
    unit, size = units[idx]
    mantissa = float(f"{value / size:.3g}")
    text = f"{mantissa:.3g}"
    if "e" in text:  # the top unit can exceed 3 digits (e.g. 5000 MHz): print it plainly
        text = f"{mantissa:.0f}"
    return f"{text} {unit}"


def format_rate(rate_hz: float) -> str:
    """'100 Hz', '2.5 kHz'. nan / non-positive -> '—'."""
    if not (math.isfinite(rate_hz) and rate_hz > 0):
        return "—"
    return _format_scaled(rate_hz, _RATE_UNITS)


def format_interval(dt_s: float) -> str:
    """'10 ms', '1.5 s', '2 min'. nan / non-positive -> '—'."""
    if not (math.isfinite(dt_s) and dt_s > 0):
        return "—"
    return _format_scaled(dt_s, _INTERVAL_UNITS)
