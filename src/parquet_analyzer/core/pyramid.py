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


def _bucket_minmax(arr: np.ndarray, bucket_rows: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-bucket (min, max) of the finite values of `arr`, split into consecutive buckets of
    `bucket_rows` rows (the last one may be shorter). A bucket without a finite value gives NaN.
    Shared by build_pyramid() and build_pyramid_from_windows() (data 59.2)."""
    n = len(arr)
    n_buckets = -(-n // bucket_rows)
    padded = np.full(n_buckets * bucket_rows, np.nan)
    padded[:n] = arr
    b = padded.reshape(n_buckets, bucket_rows)
    fin = np.isfinite(b)
    mn = np.where(fin, b, np.inf).min(axis=1)
    mx = np.where(fin, b, -np.inf).max(axis=1)
    empty = ~fin.any(axis=1)
    mn[empty] = np.nan
    mx[empty] = np.nan
    return mn, mx


#: Buckets per read batch in build_pyramid() (fewer calls into pyarrow; memory stays small).
_BUCKETS_PER_BATCH = 64


def build_pyramid(
    path: str | Path,
    columns: list[str],
    bucket_rows: int = DEFAULT_BUCKET_ROWS,
) -> dict[str, ColumnPyramid]:
    """Stream `path` once (pyarrow's iter_batches — no full-column
    materialization) and compute a per-column, per-bucket min/max envelope.

    The rows are re-packed into buckets of exactly `bucket_rows` rows (the remainder of one batch
    is joined with the start of the next), so bucket starts are always `k * bucket_rows`
    whatever batch sizes pyarrow returns -- TimeIndex anchors rely on this (detailed_specification.md
    59.2, 36.6 D). Only the last bucket may be shorter.

    Wired into the UI as of detailed_specification.md 13.5.1 Phase D:
    `core.column.WindowedColumn.request_pyramid()` returns this as a zero-arg
    job, which `TimePlotWidget._maybe_start_pyramid_build` runs on a background
    thread via `core/background_worker.py`'s `WorkerHub`, the same way
    downsampling/FFT/stats already do — this function itself has no threading of
    its own, by design, so it stays trivially testable.
    """
    if bucket_rows <= 0:
        raise ValueError(f"bucket_rows must be positive, got {bucket_rows}")
    pf = pq.ParquetFile(path)
    mins: dict[str, list[np.ndarray]] = {c: [] for c in columns}
    maxs: dict[str, list[np.ndarray]] = {c: [] for c in columns}
    carry: dict[str, np.ndarray] = {c: np.empty(0, dtype=np.float64) for c in columns}
    total = 0

    for batch in pf.iter_batches(columns=columns, batch_size=bucket_rows * _BUCKETS_PER_BATCH):
        n = batch.num_rows
        if n == 0:
            continue
        total += n
        for c in columns:
            arr = to_numeric(batch.column(c).to_numpy(zero_copy_only=False))
            if len(carry[c]):
                arr = np.concatenate([carry[c], arr])
            n_full = (len(arr) // bucket_rows) * bucket_rows
            if n_full:
                mn, mx = _bucket_minmax(arr[:n_full], bucket_rows)
                mins[c].append(mn)
                maxs[c].append(mx)
            carry[c] = arr[n_full:]

    for c in columns:
        if len(carry[c]):  # the last, shorter bucket
            mn, mx = _bucket_minmax(carry[c], bucket_rows)
            mins[c].append(mn)
            maxs[c].append(mx)

    def cat(parts: list[np.ndarray]) -> np.ndarray:
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.float64)

    result: dict[str, ColumnPyramid] = {}
    for c in columns:
        mn, mx = cat(mins[c]), cat(maxs[c])
        result[c] = ColumnPyramid(
            row_starts=np.arange(len(mn), dtype=np.int64) * bucket_rows, mins=mn, maxs=mx
        )
    return result


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


def thin_column(
    path: str | Path,
    name: str,
    target_points: int = 2000,
    row_start: int = 0,
    row_end: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Evenly thinned (row indices, float64 values) of one column, about `target_points` of
    them (detailed_specification.md 40): reads one row group at a time and keeps every
    `stride`-th row, so the whole column is never resident. Meant for a background job.

    `row_start`/`row_end` (36.6 A) restrict it to the rows [row_start, row_end): only the row
    groups overlapping the range are read, and the stride grid starts at `row_start`. Returned
    indices are global row numbers."""
    pf = pq.ParquetFile(path)
    total = pf.metadata.num_rows
    row_end = total if row_end is None else min(int(row_end), total)
    row_start = max(0, int(row_start))
    stride = max(1, (row_end - row_start) // max(1, target_points))
    idx_parts: list[np.ndarray] = []
    val_parts: list[np.ndarray] = []
    offset = 0
    for i in range(pf.metadata.num_row_groups):
        n = pf.metadata.row_group(i).num_rows
        g_lo, g_hi = max(offset, row_start), min(offset + n, row_end)  # overlap with the range, global rows
        if g_lo < g_hi:
            first = (-(g_lo - row_start)) % stride  # offset of the first grid row at/after g_lo
            if g_lo + first < g_hi:
                col = pf.read_row_group(i, columns=[name]).column(0).to_numpy()
                local = np.arange(g_lo - offset + first, g_hi - offset, stride, dtype=np.int64)
                idx_parts.append(local + offset)
                val_parts.append(to_numeric(col[local]))
        offset += n
        if offset >= row_end:
            break
    if not idx_parts:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)
    return np.concatenate(idx_parts), np.concatenate(val_parts)


def build_pyramid_from_windows(
    window,
    n_rows: int,
    bucket_rows: int = DEFAULT_BUCKET_ROWS,
    chunk_rows: int = DEFAULT_BUCKET_ROWS * 256,
) -> ColumnPyramid:
    """Like build_pyramid() but for a column that exists only as `window(lo, hi)` (a derived
    variable computed per window, detailed_specification.md 36.6 C): walks the rows in chunks
    of `chunk_rows` (rounded to a multiple of `bucket_rows`) so the whole column is never resident."""
    chunk_rows = max(bucket_rows, (chunk_rows // bucket_rows) * bucket_rows)
    starts: list[np.ndarray] = []
    mins: list[np.ndarray] = []
    maxs: list[np.ndarray] = []
    for lo in range(0, n_rows, chunk_rows):
        hi = min(n_rows, lo + chunk_rows)
        arr = np.asarray(window(lo, hi), dtype=np.float64)
        n_buckets = -(-len(arr) // bucket_rows)
        mn, mx = _bucket_minmax(arr, bucket_rows)
        starts.append(lo + np.arange(n_buckets, dtype=np.int64) * bucket_rows)
        mins.append(mn)
        maxs.append(mx)
    if not starts:
        return ColumnPyramid(np.array([], dtype=np.int64), np.array([]), np.array([]))
    return ColumnPyramid(np.concatenate(starts), np.concatenate(mins), np.concatenate(maxs))
