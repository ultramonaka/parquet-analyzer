from __future__ import annotations

import numpy as np

_DATETIME_UNIT_TO_SECONDS_DIVISOR = {"s": 1.0, "ms": 1e3, "us": 1e6}


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
