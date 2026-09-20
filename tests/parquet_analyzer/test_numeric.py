from __future__ import annotations

import numpy as np

from parquet_analyzer.core.numeric import to_numeric


def _old_to_numeric(values: np.ndarray) -> np.ndarray:
    """The pre-2026-09-19 implementation (3 copies for datetime64), kept here
    only as a ground truth for the bit-identical regression check below.
    """
    if np.issubdtype(values.dtype, np.datetime64):
        return values.astype("datetime64[us]").astype(np.int64) / 1e6
    if values.dtype == np.float64:
        return values
    return values.astype(np.float64)


def test_float64_is_passthrough_not_a_copy():
    values = np.array([1.0, 2.0, np.nan])
    assert to_numeric(values) is values


def test_int_upcasts_to_float64():
    values = np.array([1, 2, 3], dtype=np.int32)
    out = to_numeric(values)
    assert out.dtype == np.float64
    assert out.tolist() == [1.0, 2.0, 3.0]


def test_datetime64_units_bit_identical_to_old_three_copy_implementation():
    rng = np.random.default_rng(0)
    for unit in ["s", "ms", "us", "ns"]:
        base = np.datetime64("2026-01-01T00:00:00", unit)
        counts = rng.integers(0, 10**9, size=1000).astype("int64")
        values = base + counts.astype(f"timedelta64[{unit}]")
        assert np.array_equal(to_numeric(values), _old_to_numeric(values)), unit


def test_datetime64_ms_converts_to_epoch_seconds():
    values = np.array(["2026-01-01T00:00:00.000", "2026-01-01T00:00:01.500"], dtype="datetime64[ms]")
    out = to_numeric(values)
    epoch = np.datetime64("2026-01-01T00:00:00.000", "s").astype(np.int64)
    assert out[0] == float(epoch)
    assert out[1] == float(epoch) + 1.5
