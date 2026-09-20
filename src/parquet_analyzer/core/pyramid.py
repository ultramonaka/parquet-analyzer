from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from .numeric import to_numeric

#: Rows per bucket. Chosen to match detailed_specification.md 13.5.1's
#: feasibility measurement (~1.74s / <1GiB peak / ~165KiB stored per column for
#: all 4 value columns of the 86.6M-row/4GB reproduction file).
DEFAULT_BUCKET_ROWS = 8192


@dataclass(frozen=True)
class ColumnPyramid:
    """A coarse min/max envelope for one column: one (min, max) per fixed-size
    row bucket, built by streaming the file once instead of materializing the
    whole column. Enough to render the overall shape of a file too large to
    keep resident (e.g. the navigator, or a zoomed-far-out plot) — not a
    replacement for LTTB at real zoom levels, since a bucket's min/max says
    nothing about *where* within the bucket those extremes fall.
    """

    row_starts: np.ndarray  # int64, first row index of each bucket
    mins: np.ndarray  # float64, NaN if the bucket has no finite values
    maxs: np.ndarray  # float64, NaN if the bucket has no finite values

    def __len__(self) -> int:
        return len(self.row_starts)


def build_pyramid(
    path: str | Path,
    columns: list[str],
    bucket_rows: int = DEFAULT_BUCKET_ROWS,
) -> dict[str, ColumnPyramid]:
    """Stream `path` once (pyarrow's iter_batches — no full-column
    materialization) and compute a per-column, per-bucket min/max envelope.

    Wired into the UI as of detailed_specification.md 13.5.1 Phase D:
    `core.column.WindowedColumn.request_pyramid()` returns this as a zero-arg
    job, which `TimePlotWidget._maybe_start_pyramid_build` runs on a background
    thread via `core/background_worker.py`'s `BackgroundWorker`, the same way
    downsampling/FFT/stats already do — this function itself has no threading of
    its own, by design, so it stays trivially testable.
    """
    pf = pq.ParquetFile(path)
    starts: dict[str, list[int]] = {c: [] for c in columns}
    mins: dict[str, list[float]] = {c: [] for c in columns}
    maxs: dict[str, list[float]] = {c: [] for c in columns}

    row_offset = 0
    for batch in pf.iter_batches(columns=columns, batch_size=bucket_rows):
        n = batch.num_rows
        if n == 0:
            continue
        for c in columns:
            arr = to_numeric(batch.column(c).to_numpy(zero_copy_only=False))
            finite = arr[np.isfinite(arr)]
            starts[c].append(row_offset)
            if len(finite) == 0:
                mins[c].append(np.nan)
                maxs[c].append(np.nan)
            else:
                mins[c].append(float(finite.min()))
                maxs[c].append(float(finite.max()))
        row_offset += n

    return {
        c: ColumnPyramid(
            row_starts=np.array(starts[c], dtype=np.int64),
            mins=np.array(mins[c], dtype=np.float64),
            maxs=np.array(maxs[c], dtype=np.float64),
        )
        for c in columns
    }


def pyramid_bounds(pyramid: ColumnPyramid) -> tuple[float, float] | None:
    """Global (min, max) across every bucket, NaN-safe. None if every bucket
    was empty/non-finite (e.g. an all-NaN column)."""
    if len(pyramid) == 0:
        return None
    finite_mins = pyramid.mins[np.isfinite(pyramid.mins)]
    finite_maxs = pyramid.maxs[np.isfinite(pyramid.maxs)]
    if len(finite_mins) == 0 or len(finite_maxs) == 0:
        return None
    return float(finite_mins.min()), float(finite_maxs.max())
