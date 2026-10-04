"""Helpers shared by the XY/3D window and the 3D view (detailed_specification.md 25.2, 25.5).

Kept apart so `xy_plot_window` (which owns the 2D/3D toggle) and `xyz_3d_window` (the GL view and
3D computation) can import each other's public parts without a cycle.
"""

from __future__ import annotations

from datetime import datetime


def chunks(row_start: int, row_end: int, chunk_rows: int):
    """Consecutive (a, b) row ranges of at most `chunk_rows` rows covering [row_start, row_end)."""
    a = row_start
    while a < row_end:
        b = min(a + chunk_rows, row_end)
        yield a, b
        a = b


def format_time_tick(value: float, span: float) -> str:
    """Local-time label for an epoch-seconds value; precision follows the span so neighbouring
    ticks stay distinguishable (a plain number would read as "1.76722e9" with an offset)."""
    try:
        dt = datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        return f"{value:.6g}"
    if span < 2:
        return dt.strftime("%H:%M:%S.%f")[:-3]
    if span < 2 * 3600:
        return dt.strftime("%H:%M:%S")
    if span < 3 * 86400:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")
