from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .analysis import FFT_JITTER_TOLERANCE

_DATETIME_UNIT_TO_SECONDS_DIVISOR = {"s": 1.0, "ms": 1e3, "us": 1e6}


class NonNumericColumnError(ValueError):
    """A file column cannot be used as a 1-D float64 series (a string/binary/time-of-day
    column, or a list/struct column that numpy turns into a 2-D or object array). A
    ValueError subclass so older `except ValueError` callers keep working; callers that
    take a column from the user (drop on a plot, time-axis switch, view load) catch this
    one specifically and report it instead of leaking a raw numpy/polars exception
    (data 29.5: nothing may be left half-switched)."""


def to_numeric(values: np.ndarray) -> np.ndarray:
    """Coerce a raw column array into the float64 (epoch-seconds for datetimes,
    passthrough for float64, upcast otherwise) form the rest of the app plots,
    evaluates expressions over, and runs FFT/stats on.

    Lives in core/ (not ui/, despite MainWindow being the original/main caller)
    because core/pyramid.py needs the exact same conversion and core/ must not
    depend on ui/. `MainWindow._to_numeric` re-exports this
    unchanged so existing references to that name keep working.

    Datetime conversion is a single allocation (`view` + one `true_divide`)
    rather than astype(us) -> astype(int64) / 1e6 (three full-array copies);
    verified bit-identical to that old 3-copy result via `np.array_equal` for
    every real datetime64 unit (s/ms/us/ns) — must be `true_divide`, not
    `multiply` by `1/divisor`, which is *not* bit-identical (max diff 2.4e-7).
    Note this does not reduce steady-state resident memory (CPython frees the
    old chain's intermediates immediately either way) — only the transient peak
    during conversion (detailed_specification.md 13.5.1's 2026-09-19 addendum).
    """
    try:
        result = _to_numeric(values)
        if values.dtype.kind in "mM" and values.ndim == 1:
            # A null timestamp/duration is NaT = int64 minimum; unconverted it became a
            # huge finite negative number (e.g. -9.2e15 s) that blew up Y ranges and slipped
            # past the time-order/non-finite check. Missing values are NaN (data 29.5).
            nat = np.isnat(values)
            if nat.any():
                result[nat] = np.nan
    except (ValueError, TypeError) as e:
        raise NonNumericColumnError(str(e)) from e
    if result.ndim != 1:
        raise NonNumericColumnError(f"expected a 1-D column, got shape {result.shape}")
    return result


def _to_numeric(values: np.ndarray) -> np.ndarray:
    if np.issubdtype(values.dtype, np.datetime64):
        unit = np.datetime_data(values.dtype)[0]
        divisor = _DATETIME_UNIT_TO_SECONDS_DIVISOR.get(unit)
        if divisor is not None:
            return np.true_divide(values.view(np.int64), divisor, dtype=np.float64)
        if unit == "ns":
            # astype("datetime64[us]") below truncates to whole microseconds
            # before converting to seconds; replicate that truncation (integer
            # floor-division) instead of dividing the raw ns count directly,
            # which would keep sub-microsecond precision the old path drops.
            return np.true_divide(values.view(np.int64) // 1000, 1e6, dtype=np.float64)
        # An uncommon datetime64 unit (Y/M/D/h/m, ...): fall back to the
        # original conversion rather than risk a wrong scale factor.
        return values.astype("datetime64[us]").astype(np.int64) / 1e6
    if values.dtype == np.float64:
        return values
    return values.astype(np.float64)


@dataclass(frozen=True)
class TimeOrderReport:
    """Result of check_time_order (detailed_specification.md 29.1)."""

    nan_count: int  # non-finite values (NaN / +-Inf)
    decreasing_count: int  # places where a finite value is smaller than the previous finite one
    first_decreasing_index: int | None  # original row index of the first such value, if any
    # 56.2: finite values equal to the previous finite one (NaN/Inf between them are skipped,
    # the same rule as decreasing_count). Does not affect `ok`: duplicates keep ranges correct.
    duplicate_count: int = 0
    first_duplicate_index: int | None = None  # original row index of the first such value, if any
    # 60.1: sampling interval from the positive finite differences of the same scan. dt_median is the
    # median of the per-chunk medians; nan = no usable difference (< 2 points, or no ascending pair).
    dt_median: float = float("nan")
    dt_min: float = float("nan")
    dt_max: float = float("nan")

    @property
    def ok(self) -> bool:
        return self.nan_count == 0 and self.decreasing_count == 0

    @property
    def rate_hz(self) -> float:
        """1 / dt_median (per time-axis unit); nan when there is no usable interval (60.1)."""
        dt = self.dt_median
        return 1.0 / dt if np.isfinite(dt) and dt > 0 else float("nan")

    @property
    def irregular(self) -> bool:
        """True when the interval varies by more than FFT_JITTER_TOLERANCE around the median (60.1)."""
        dt = self.dt_median
        if not (np.isfinite(dt) and dt > 0 and np.isfinite(self.dt_min) and np.isfinite(self.dt_max)):
            return False
        return (self.dt_max - dt) / dt > FFT_JITTER_TOLERANCE or (dt - self.dt_min) / dt > FFT_JITTER_TOLERANCE


_TIME_ORDER_CHUNK_ROWS = 2_000_000  # chunked so a 100M-row axis needs only a small temporary


class TimeOrderScanner:
    """Incremental form of check_time_order: feed consecutive chunks (with the row index
    of each chunk's first element) and read `report()`. Carries the last finite value
    across chunk boundaries, so the result equals a one-shot check (29.1). Also used by
    the streaming TimeIndex build (36.6 D) that never holds the whole axis."""

    def __init__(self) -> None:
        self.nan_count = 0
        self.decreasing = 0
        self.first_bad: int | None = None
        self.duplicates = 0  # 56.2
        self.first_dup: int | None = None
        self._prev: float | None = None  # last finite value of the previous chunks
        # 60.1: sampling interval, from the same pass (no second scan)
        self._dt_medians: list[float] = []  # median of the positive differences of each chunk
        self._dt_min = math.inf
        self._dt_max = -math.inf

    def feed(self, chunk: np.ndarray, start: int) -> None:
        finite = np.isfinite(chunk)
        nfin = int(np.count_nonzero(finite))
        self.nan_count += chunk.size - nfin
        if nfin == chunk.size:
            idx = None
            v = chunk
        else:
            idx = np.nonzero(finite)[0]
            v = chunk[idx]
        if v.size == 0:
            return
        self._feed_intervals(v)
        if self._prev is not None and v[0] < self._prev:
            self.decreasing += 1
            if self.first_bad is None:
                self.first_bad = start + (int(idx[0]) if idx is not None else 0)
        if self._prev is not None and v[0] == self._prev:
            self.duplicates += 1
            if self.first_dup is None:
                self.first_dup = start + (int(idx[0]) if idx is not None else 0)
        if v.size > 1:
            bad = np.nonzero(np.diff(v) < 0)[0]
            if bad.size:
                self.decreasing += int(bad.size)
                if self.first_bad is None:
                    j = int(bad[0]) + 1
                    self.first_bad = start + (int(idx[j]) if idx is not None else j)
            dup = np.nonzero(np.diff(v) == 0)[0]
            if dup.size:
                self.duplicates += int(dup.size)
                if self.first_dup is None:
                    j = int(dup[0]) + 1
                    self.first_dup = start + (int(idx[j]) if idx is not None else j)
        self._prev = v[-1]

    def _feed_intervals(self, v: np.ndarray) -> None:
        """60.1: positive differences of the finite values (including the one across the chunk
        boundary); keeps the chunk's median and the running min / max."""
        d = np.diff(v)
        if self._prev is not None:
            d = np.concatenate(([v[0] - self._prev], d))
        d = d[d > 0]
        if d.size == 0:
            return
        self._dt_medians.append(float(np.median(d)))
        self._dt_min = min(self._dt_min, float(d.min()))
        self._dt_max = max(self._dt_max, float(d.max()))

    def report(self) -> TimeOrderReport:
        if self._dt_medians:
            dt_med = float(np.median(self._dt_medians))
            dt_min, dt_max = self._dt_min, self._dt_max
            if not (math.isfinite(dt_med) and math.isfinite(dt_max)):  # overflowed differences
                dt_med = dt_min = dt_max = float("nan")
        else:
            dt_med = dt_min = dt_max = float("nan")
        return TimeOrderReport(
            self.nan_count, self.decreasing, self.first_bad, self.duplicates, self.first_dup, dt_med, dt_min, dt_max
        )


def check_time_order(values: np.ndarray) -> TimeOrderReport:
    """Check that a time axis is ascending (equal neighbours allowed) and finite
    (detailed_specification.md 29.1). Range lookups use np.searchsorted, which
    silently assumes this. Non-finite values are skipped when looking for
    decreases, so one NaN doesn't also count as an ordering fault. Processed in
    chunks of _TIME_ORDER_CHUNK_ROWS carrying the last finite value across chunk
    boundaries, so temporary memory stays small for huge columns."""
    values = np.asarray(values)
    scanner = TimeOrderScanner()
    for start in range(0, values.size, _TIME_ORDER_CHUNK_ROWS):
        scanner.feed(values[start : start + _TIME_ORDER_CHUNK_ROWS], start)
    return scanner.report()
