"""TimeIndex: an ascending time axis that is never held fully in memory
(detailed_specification.md 36.5/36.6 stage D).

The axis column of a big file is scanned once, in chunks, while the file is opened. The scan
(a) runs the 29.1 order check on the fly, (b) keeps the value at every ``ANCHOR_ROWS``-th row
(the "anchors", ~12k floats for 100M rows) plus the value at a few "pinned" rows (the row-group
starts the navigator wants) and (c) remembers the first and last value.  Afterwards:

* ``searchsorted`` is exact (same result as ``np.searchsorted`` on the whole array, including
  duplicates and side=left/right): a binary search over the anchors narrows the answer to one
  anchor interval, which is read (a small windowed read, block-cached by the data source) and
  searched exactly.  Because the axis is ascending and finite, the anchors are enough -- the
  row-group statistics the spec mentions would give the same answer more coarsely.
* ``value_at`` / scalar indexing / ``take`` are O(1) for anchors and pins, one small windowed
  read otherwise.
* slicing reads the requested window; ``np.asarray(index)`` materializes everything (the
  deliberate, correctness-first fallback for code that was not converted).

Only usable when the axis is ascending and finite (29.1 ``ok``); ``TimeIndex.build`` returns
``None`` otherwise and the caller keeps the old fully resident path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

import numpy as np

from .numeric import TimeOrderReport, TimeOrderScanner, to_numeric
from .pyramid import DEFAULT_BUCKET_ROWS

if TYPE_CHECKING:
    from .data_source import ParquetDataSource

logger = logging.getLogger(__name__)

ANCHOR_ROWS = DEFAULT_BUCKET_ROWS  # the pyramid's bucket size (shared constant), so pyramid bucket starts are all anchors (O(1) take)
_SCAN_CHUNK_ROWS = 2_000_000


class TimeIndex:
    """Read-only 1-D float64 array look-alike over an ascending, finite time column."""

    ndim = 1
    dtype = np.dtype(np.float64)

    def __init__(
        self,
        data_source: "ParquetDataSource",
        column: str,
        n: int,
        anchors: np.ndarray,
        pins: dict[int, float],
        last: float,
    ):
        self._ds = data_source
        self.column = column
        self._n = int(n)
        self._anchors = anchors  # anchors[k] = value at row k * ANCHOR_ROWS
        self._pins = pins
        self._last = float(last)
        self.report = TimeOrderReport(0, 0, None)

    # -- construction -------------------------------------------------------------

    @classmethod
    def build(
        cls,
        data_source: "ParquetDataSource",
        column: str,
        pin_rows: Iterable[int] = (),
        chunk_rows: int = _SCAN_CHUNK_ROWS,
    ) -> "TimeIndex | None":
        """One streaming pass over `column`. None if it is empty, not ascending or has
        non-finite values (the caller then loads it fully, as before)."""
        n = data_source.row_count()
        if n == 0:
            return None
        pins_wanted = sorted({int(r) for r in pin_rows if 0 <= int(r) < n and int(r) % ANCHOR_ROWS})
        scanner = TimeOrderScanner()
        anchors: list[np.ndarray] = []
        pins: dict[int, float] = {}
        last = 0.0
        pi = 0
        for start in range(0, n, chunk_rows):
            end = min(n, start + chunk_rows)
            raw = data_source.read_columns([column], start, end)[column]
            v = to_numeric(raw)
            if len(v) != end - start:
                return None  # file shorter than its metadata claims: give up on the index
            scanner.feed(v, start)
            if scanner.nan_count or scanner.decreasing:
                return None
            first_anchor = -start % ANCHOR_ROWS  # chunk offset of the first multiple of ANCHOR_ROWS
            if first_anchor < len(v):
                anchors.append(np.array(v[first_anchor::ANCHOR_ROWS], dtype=np.float64))
            while pi < len(pins_wanted) and pins_wanted[pi] < end:
                pins[pins_wanted[pi]] = float(v[pins_wanted[pi] - start])
                pi += 1
            last = float(v[-1])
            del raw, v
        return cls(data_source, column, n, np.concatenate(anchors), pins, last)

    # -- array look-alike ---------------------------------------------------------

    def __len__(self) -> int:
        return self._n

    @property
    def size(self) -> int:
        return self._n

    @property
    def shape(self) -> tuple[int]:
        return (self._n,)

    def window(self, lo: int, hi: int) -> np.ndarray:
        """Rows [lo, hi) as float64 (a small windowed read)."""
        lo = max(0, int(lo))
        hi = min(self._n, int(hi))
        if hi <= lo:
            return np.array([], dtype=np.float64)
        return to_numeric(self._ds.read_window([self.column], lo, hi)[self.column])

    def value_at(self, row: int) -> float:
        row = int(row)
        if row < 0:
            row += self._n
        if not 0 <= row < self._n:
            raise IndexError(f"row {row} out of range for {self._n} rows")
        if row % ANCHOR_ROWS == 0:
            return float(self._anchors[row // ANCHOR_ROWS])
        if row == self._n - 1:
            return self._last
        pinned = self._pins.get(row)
        if pinned is not None:
            return pinned
        return float(self.window(row, row + 1)[0])

    @property
    def path(self):
        return self._ds.path

    def take(self, rows) -> np.ndarray:
        """Values at `rows`. Anchor rows (multiples of ANCHOR_ROWS, e.g. every pyramid bucket
        start) are looked up vectorised without any I/O; every other row costs one windowed
        read, so call this off the UI thread unless the rows are anchors."""
        rows = np.asarray(rows, dtype=np.int64)
        out = np.empty(rows.shape, dtype=np.float64)
        flat_rows, flat_out = rows.reshape(-1), out.reshape(-1)
        neg = flat_rows < 0
        if neg.any():
            flat_rows = np.where(neg, flat_rows + self._n, flat_rows)
        if len(flat_rows) and (flat_rows.min() < 0 or flat_rows.max() >= self._n):
            raise IndexError(f"rows out of range for {self._n} rows")
        is_anchor = (flat_rows % ANCHOR_ROWS == 0)
        flat_out[is_anchor] = self._anchors[flat_rows[is_anchor] // ANCHOR_ROWS]
        for i in np.flatnonzero(~is_anchor):
            flat_out[i] = self.value_at(flat_rows[i])
        return out

    def __getitem__(self, key):
        if isinstance(key, (int, np.integer)):
            return self.value_at(key)
        if isinstance(key, slice):
            lo, hi, step = key.indices(self._n)
            if step == 1:
                return self.window(lo, hi)
            return self.window(0, self._n)[key]
        arr = np.asarray(key)
        if arr.dtype == bool:
            return self.window(0, self._n)[arr]
        return self.take(arr)

    def __array__(self, dtype=None, copy=None):
        # The fallback for any caller that wants the whole axis: costs what the old
        # fully resident path did (transiently). Logged so unconverted users are findable.
        logger.debug("TimeIndex materialized in full (%d rows)", self._n)
        arr = self.window(0, self._n)
        return arr.astype(dtype) if dtype is not None else arr

    def finite_min_max(self) -> tuple[float, float]:
        """(min, max): ascending and finite, so the first and the last value."""
        return float(self._anchors[0]), self._last

    def min(self) -> float:  # ndarray look-alike (ascending and finite)
        return self.finite_min_max()[0]

    def max(self) -> float:
        return self.finite_min_max()[1]

    def approx_take(self, rows) -> np.ndarray:
        """Values at `rows` by linear interpolation between the held anchors: exact at an
        anchor, off by at most the unevenness within one ANCHOR_ROWS interval elsewhere. Only
        for coarse overview curves (the navigator), never for readouts or ranges."""
        return np.interp(np.asarray(rows, dtype=np.float64), self.anchor_rows(), self.anchor_values())

    def anchor_rows(self) -> np.ndarray:
        """Rows whose value is held (every ANCHOR_ROWS-th, plus the last row)."""
        rows = np.arange(len(self._anchors), dtype=np.int64) * ANCHOR_ROWS
        if rows[-1] != self._n - 1:
            rows = np.append(rows, self._n - 1)
        return rows

    def anchor_values(self) -> np.ndarray:
        vals = self._anchors
        if (len(vals) - 1) * ANCHOR_ROWS != self._n - 1:
            vals = np.append(vals, self._last)
        return vals

    # -- searching ----------------------------------------------------------------

    def _search_one(self, v: float, side: str) -> int:
        if v != v:
            return self._n  # NaN sorts last, like np.searchsorted
        m = len(self._anchors)
        j = int(np.searchsorted(self._anchors, v, side=side))
        if j == 0:
            return 0
        # anchors[j-1] is on the "before" side of v, so the answer is > lo; anchors[j] (if any)
        # is on the "after" side, so the answer is <= hi.
        lo = (j - 1) * ANCHOR_ROWS
        hi = min(j * ANCHOR_ROWS, self._n) if j < m else self._n
        window = self.window(lo, hi)  # rows lo..hi-1; row lo is "before" v
        return lo + int(np.searchsorted(window, v, side=side))

    def searchsorted(self, v, side: str = "left", sorter=None):
        """Exactly np.searchsorted(whole_axis, v, side) without the whole axis."""
        if sorter is not None:
            raise NotImplementedError("sorter")
        if side not in ("left", "right"):
            raise ValueError(f"side must be 'left' or 'right', got {side!r}")
        arr = np.asarray(v, dtype=np.float64)
        if arr.ndim == 0:
            return np.intp(self._search_one(float(arr), side))
        return np.array([self._search_one(float(x), side) for x in arr.reshape(-1)], dtype=np.intp).reshape(arr.shape)
