from __future__ import annotations

import bisect
import datetime
import math
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from .numeric import to_numeric


def _stat_to_numeric(value: object) -> float | None:
    """Convert one Parquet row-group statistic (a Python float/int, or a
    datetime.date/datetime for a timestamp/date column) to the same
    epoch-seconds float scale core.numeric.to_numeric produces for the real
    column data. Routes datetimes through to_numeric (not
    datetime.timestamp(), which would apply the *local* timezone) so a
    statistic and the array it summarizes are always directly comparable.
    """
    if value is None:
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        with warnings.catch_warnings():
            # numpy warns when given a tz-aware datetime ("no explicit representation of
            # timezones"), but it converts to UTC, which is the value we want (data 29.5)
            warnings.simplefilter("ignore", UserWarning)
            return float(to_numeric(np.array([np.datetime64(value)]))[0])
    return float(value)


class ParquetDataSource:
    """Lazy wrapper around a Parquet file for schema inspection and range reads
    (detailed_specification.md 1章/2章). Only the requested columns/rows are
    materialized, via polars' lazy scan.

    Also exposes Parquet-footer-only metadata (row_groups/column_stats/
    column_bounds/is_monotonic/row_range_for_x) and a block-cached read_window,
    added for detailed_specification.md 13.5.1 Phase B. None of these are called
    from the UI yet — they exist so a later phase can wire them in without
    touching this class's read path again.
    """

    #: Block size for read_window()'s LRU cache. Not tied to the file's actual
    #: row-group size (which varies per file) — a fixed size keeps cache keys
    #: simple and adjacent windowed reads (e.g. a pan) share blocks regardless
    #: of how the source file happens to be row-grouped.
    _DEFAULT_BLOCK_ROWS = 100_000

    def __init__(self, path: str | Path, block_rows: int = _DEFAULT_BLOCK_ROWS, cache_blocks: int = 64):
        self.path = Path(path)
        self._lazy = pl.scan_parquet(self.path)
        self._schema = self._lazy.collect_schema()
        self._footer: pq.FileMetaData | None = None  # read lazily; footer-only cost, no handle kept
        self._block_rows = block_rows
        self._read_block_cached = lru_cache(maxsize=cache_blocks)(self._read_block_uncached)

    @property
    def columns(self) -> list[str]:
        return list(self._schema.names())

    def is_datetime(self, name: str) -> bool:
        """True when column `name` is a date / datetime column (its time axis shows as wall-clock
        time; a numeric column is just seconds). Used by the histogram's bucket export (42.8)."""
        dtype = self._schema.get(name)
        return dtype is not None and dtype.base_type() in (pl.Datetime, pl.Date)

    def row_count(self) -> int:
        return self._lazy.select(pl.len()).collect().item()

    def read_columns(
        self,
        columns: list[str],
        row_start: int = 0,
        row_end: int | None = None,
    ) -> dict[str, np.ndarray]:
        # One column at a time rather than a single collect() over all of them: on a
        # real (multi-row-group) file, collect() yields a chunked Arrow column and
        # to_numpy() must rechunk it into a fresh contiguous buffer — a real second
        # copy, not the zero-copy view it is for a single-chunk column (see
        # detailed_specification.md 13.5.1's 2026-09-19 addendum). Reading column by
        # column bounds how many of those chunked-plus-rechunked pairs are ever alive
        # at once to one, instead of all len(columns) of them (measured: peak RSS at
        # open 8.05 -> 4.89 GiB on a 4GB/5-column file, same wall time).
        # Clamp a negative start / reversed range (data 29.5; polars would count from the end or
        # raise "negative slice lengths") to an empty or in-file slice.
        row_start = max(0, row_start)
        if row_end is not None:
            row_end = max(row_end, row_start)
        result: dict[str, np.ndarray] = {}
        for c in columns:
            lf = self._lazy.select(c)
            if row_end is not None:
                lf = lf.slice(row_start, row_end - row_start)
            elif row_start:
                lf = lf.slice(row_start, None)
            df = lf.collect()
            result[c] = df[c].to_numpy()
            del df
        return result

    # -- Footer-only metadata (no data I/O); detailed_specification.md 13.5.1 Phase B --

    def _footer_metadata(self) -> pq.FileMetaData:
        # read_metadata closes the file again: a held handle would block deleting/overwriting
        # the Parquet file while it is open in the app on Windows (WinError 32)
        if self._footer is None:
            self._footer = pq.read_metadata(self.path)
        return self._footer

    def row_groups(self) -> list[tuple[int, int]]:
        """Per-row-group (row_offset, num_rows), from the Parquet footer alone —
        no data pages are read (measured ~7ms for 872 row groups)."""
        meta = self._footer_metadata()
        groups: list[tuple[int, int]] = []
        offset = 0
        for i in range(meta.num_row_groups):
            n = meta.row_group(i).num_rows
            groups.append((offset, n))
            offset += n
        return groups

    def column_stats(self, name: str) -> list[tuple[float | None, float | None, int]]:
        """Per-row-group (min, max, null_count) for one column, footer-only.
        min/max are None for a row group whose statistics are unavailable
        (`has_min_max=False`) — treat that row group as "unknown range", not as
        an all-null one; null_count is still reported when available.
        """
        meta = self._footer_metadata()
        field_index = meta.schema.to_arrow_schema().get_field_index(name)
        if field_index < 0:
            raise KeyError(name)
        result: list[tuple[float | None, float | None, int]] = []
        for i in range(meta.num_row_groups):
            stats = meta.row_group(i).column(field_index).statistics
            if stats is None or not stats.has_min_max:
                result.append((None, None, (stats.null_count or 0) if stats is not None else 0))
                continue
            try:
                lo, hi = _stat_to_numeric(stats.min), _stat_to_numeric(stats.max)
            except (ValueError, TypeError):
                # a string/binary/float16/time-of-day column: Parquet has statistics but they
                # have no float scale -> "unknown range", like has_min_max=False (never raise
                # out of the footer-only metadata path; data 29.5)
                lo = hi = None
            result.append((lo, hi, stats.null_count or 0))
        return result

    def column_bounds(self, name: str) -> tuple[float, float] | None:
        """Global (min, max) for one column from footer statistics alone. None
        if no row group has usable statistics (e.g. an all-null column, or a
        dtype Parquet doesn't compute min/max for)."""
        lo = hi = None
        for mn, mx, _null_count in self.column_stats(name):
            # +-Inf statistics (a column holding Inf values) are skipped: bounds feed the
            # plot's Y range, and a non-finite range must never reach pyqtgraph (29.4, data 29.5)
            if mn is None or mx is None or not (math.isfinite(mn) and math.isfinite(mx)):
                continue
            lo = mn if lo is None else min(lo, mn)
            hi = mx if hi is None else max(hi, mx)
        return None if lo is None else (lo, hi)

    def is_monotonic(self, name: str) -> bool:
        """True if per-row-group min/max are non-decreasing across row groups
        (footer-only — no data I/O, so this cannot see disorder *within* a
        single row group). Necessary but not sufficient for true global
        monotonicity; still strictly more than the zero checking
        `MainWindow._visible_row_range`/`TimePlotWidget._redraw_series` do today
        (they assume a sorted time column outright via `np.searchsorted`) — see
        detailed_specification.md 13.5.1's "monotonicity assumption" note.
        Row groups with no usable statistics are treated as satisfying the
        check locally (skipped) rather than failing it outright.
        """
        prev_max = None
        for mn, mx, _null_count in self.column_stats(name):
            if mn is None:
                continue
            if mn > mx:
                return False
            if prev_max is not None and mn < prev_max:
                return False
            prev_max = mx
        return True

    def row_range_for_x(self, column: str, x_lo: float, x_hi: float) -> tuple[int, int]:
        """Row range [row_start, row_end) spanning every row group whose
        [min, max] on `column` could overlap [x_lo, x_hi] — a binary search over
        footer statistics, no data I/O. Assumes `column` is at least
        row-group-monotonic; callers should check is_monotonic(column) first and
        fall back to a materialized/eager read otherwise — this method does not
        check it itself, to keep it a cheap pure lookup (13.5.1's monotonicity
        note). Returns (0, 0) if nothing overlaps.
        """
        if not (x_lo <= x_hi):  # reversed or NaN bound: no overlap (data 29.5)
            return (0, 0)
        groups = self.row_groups()
        stats = self.column_stats(column)
        maxs = [mx if mx is not None else float("-inf") for _mn, mx, _n in stats]
        mins = [mn if mn is not None else float("inf") for mn, _mx, _n in stats]
        start_idx = bisect.bisect_left(maxs, x_lo)
        end_idx = bisect.bisect_right(mins, x_hi)
        if start_idx >= len(groups) or end_idx <= start_idx:
            return (0, 0)
        row_start = groups[start_idx][0]
        last_offset, last_n = groups[end_idx - 1]
        return (row_start, last_offset + last_n)

    # -- Block-cached windowed reads; detailed_specification.md 13.5.1 Phase B --

    def _read_block_uncached(self, columns: tuple[str, ...], block_index: int) -> dict[str, np.ndarray]:
        row_start = block_index * self._block_rows
        return self.read_columns(list(columns), row_start, row_start + self._block_rows)

    def read_window(self, columns: list[str], row_start: int, row_end: int) -> dict[str, np.ndarray]:
        """Like read_columns(columns, row_start, row_end), but backed by an LRU
        cache of fixed-size blocks so overlapping requests (e.g. adjacent pans)
        reuse already-fetched blocks instead of re-reading from disk every time.
        Cache size is a block *count* (see __init__'s cache_blocks), not a byte
        budget — a fine simplification while nothing calls this yet; revisit if
        Phase D's real usage pattern wants tighter memory control.
        """
        # Clamp to the file's rows: a window past the end must not walk millions of empty
        # blocks, and a negative start must not read from the end (data 29.5).
        total = self._footer_metadata().num_rows
        row_start = max(0, row_start)
        row_end = min(total, row_end)
        if row_end <= row_start:
            return {c: np.array([], dtype=np.float64) for c in columns}
        columns_key = tuple(columns)
        first_block = row_start // self._block_rows
        last_block = (row_end - 1) // self._block_rows
        parts: dict[str, list[np.ndarray]] = {c: [] for c in columns}
        for block_index in range(first_block, last_block + 1):
            block = self._read_block_cached(columns_key, block_index)
            block_start = block_index * self._block_rows
            block_len = len(next(iter(block.values()))) if block else 0
            lo = max(row_start, block_start) - block_start
            hi = min(row_end, block_start + block_len) - block_start
            for c in columns:
                parts[c].append(block[c][lo:hi])
        return {c: (v[0] if len(v) == 1 else np.concatenate(v)) for c, v in parts.items()}
