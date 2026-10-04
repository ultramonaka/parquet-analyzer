"""Abnormal-input tests for the data layer: core/data_source.py, core/column.py,
core/numeric.py (detailed_specification.md 4.2 / 13.5.1 / 29). Files are generated in
tmp_path; nothing under data/raw is used."""
from __future__ import annotations

import datetime
import decimal
import math

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.column import LazyColumn, LazyVariables, MaterializedColumn, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import ExpressionError
from parquet_analyzer.core.numeric import NonNumericColumnError, check_time_order, to_numeric

N = 30


def _write(path, **cols):
    pq.write_table(pa.table(cols), path, row_group_size=10)
    return path


@pytest.fixture
def ds(tmp_path):
    t = np.arange(N, dtype=float)
    return ParquetDataSource(_write(tmp_path / "f.parquet", t=t, v=t * 2), block_rows=7)


# -- files that cannot be opened ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["empty", "garbage", "truncated", "directory", "missing"])
def test_unreadable_paths_raise_instead_of_returning_a_broken_source(tmp_path, kind):
    good = _write(tmp_path / "good.parquet", t=np.arange(10.0), v=np.arange(10.0))
    path = tmp_path / "bad.parquet"
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "garbage":
        path.write_bytes(b"this is not a parquet file at all")
    elif kind == "truncated":
        data = good.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    elif kind == "directory":
        path.mkdir()
    with pytest.raises(Exception):  # noqa: B017 (polars/OS error types vary; load_parquet catches Exception)
        ParquetDataSource(path)


# -- tiny files ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 2])
def test_tiny_files_are_readable_everywhere(tmp_path, n):
    t = np.arange(n, dtype=float)
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=t, v=t))
    assert ds.row_count() == n
    assert ds.read_columns(["t", "v"])["v"].tolist() == t.tolist()
    assert ds.read_window(["v"], 0, 10)["v"].tolist() == t.tolist()
    assert ds.is_monotonic("v")
    col = WindowedColumn(ds, "v")
    assert col.values().tolist() == t.tolist()
    assert LazyColumn(ds, "v").values().tolist() == t.tolist()
    if n == 0:
        assert ds.column_bounds("v") is None
        assert ds.row_range_for_x("t", 0, 1) == (0, 0)
    else:
        assert ds.column_bounds("v") == (0.0, float(n - 1))


# -- read_window / read_columns ranges ---------------------------------------------------


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (0, N, list(range(N))),
        (-5, 5, list(range(5))),  # negative start clamps to 0
        (-10, -2, []),  # entirely before the file
        (25, 10**12, list(range(25, N))),  # huge end clamps (used to walk ~1e7 empty blocks)
        (N, N + 10, []),
        (N + 100, N + 200, []),
        (5, 5, []),
        (8, 3, []),  # reversed
        (6, 15, list(range(6, 15))),  # spans several 7-row blocks
    ],
)
def test_read_window_out_of_range_is_clamped(ds, start, end, expected):
    got = ds.read_window(["t"], start, end)["t"]
    assert got.tolist() == [float(i) for i in expected]


def test_read_window_with_huge_end_is_fast(ds):
    """Regression: row_end=1e12 iterated one block per 7 rows up to the end value."""
    import time

    t0 = time.perf_counter()
    ds.read_window(["v"], 0, 10**12)
    assert time.perf_counter() - t0 < 2.0


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (-5, 5, list(range(5))),  # polars would have read from the end
        (8, 3, []),  # polars raised "negative slice lengths"
        (5, 5, []),
        (N + 5, N + 9, []),
        (3, None, list(range(3, N))),
        (0, None, list(range(N))),
    ],
)
def test_read_columns_odd_ranges(ds, start, end, expected):
    got = ds.read_columns(["t"], start, end)["t"]
    assert got.tolist() == [float(i) for i in expected]


def test_read_window_with_no_columns_and_with_an_empty_range(ds):
    assert ds.read_window([], 0, 5) == {}
    assert ds.read_window(["t"], 0, 0)["t"].size == 0


def test_unknown_column_raises(ds):
    with pytest.raises(Exception):  # noqa: B017 (polars ColumnNotFoundError)
        ds.read_columns(["nope"])
    with pytest.raises(KeyError):
        ds.column_stats("nope")


def test_cached_blocks_survive_file_deletion_but_uncached_reads_fail_cleanly(tmp_path):
    path = _write(tmp_path / "f.parquet", t=np.arange(20.0), v=np.arange(20.0))
    ds = ParquetDataSource(path, block_rows=10)
    first = ds.read_window(["v"], 0, 10)["v"]
    path.unlink()
    assert ds.read_window(["v"], 2, 8)["v"].tolist() == first[2:8].tolist()  # cached block
    with pytest.raises(FileNotFoundError):
        ds.read_window(["v"], 10, 20)  # uncached block: a real error, not a wrong value


# -- footer statistics ---------------------------------------------------------------------


def test_row_range_for_x_multi_group_and_outside(tmp_path):
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(float(N)), v=np.arange(float(N))))
    assert ds.row_range_for_x("t", 12, 17) == (10, 20)
    assert ds.row_range_for_x("t", -50, -10) == (0, 0)
    assert ds.row_range_for_x("t", 500, 1000) == (0, 0)
    assert ds.row_range_for_x("t", -math.inf, math.inf) == (0, N)


def test_stats_with_nan_inf_and_all_nan_groups(tmp_path):
    v = np.arange(float(N))
    v[:10] = np.nan  # group 0: all NaN -> no usable min/max
    v[12] = np.inf
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(float(N)), v=v))
    stats = ds.column_stats("v")
    assert stats[0][:2] == (None, None)
    assert stats[1][1] == math.inf
    # bounds feed a plot's Y range: never non-finite (29.4). group 1 (max inf) is skipped.
    assert ds.column_bounds("v") == (20.0, 29.0)


def test_all_nan_column_has_no_bounds(tmp_path):
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(float(N)), v=np.full(N, np.nan)))
    assert ds.column_bounds("v") is None


@pytest.mark.parametrize(
    "arr",
    [
        pa.array(["a"] * N),
        pa.array([b"a"] * N),
        pa.array(np.arange(N, dtype=np.float16)),
        pa.array([datetime.time(1, 2, 3)] * N),
    ],
    ids=["string", "binary", "float16", "time_of_day"],
)
def test_column_bounds_never_raises_for_types_without_a_float_scale(tmp_path, arr):
    """Regression: column_bounds()/column_stats() raised ValueError/TypeError for these (a
    WindowedColumn of such a column broke opening the file); now: unknown range."""
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(float(N)), x=arr))
    stats = ds.column_stats("x")
    assert all(lo is None or isinstance(lo, float) for lo, _hi, _n in stats)
    bounds = ds.column_bounds("x")
    assert bounds is None or all(math.isfinite(b) for b in bounds)
    assert isinstance(ds.is_monotonic("x"), bool)


# -- numeric conversion of every sort of column --------------------------------------------

_D = datetime.datetime(2026, 1, 1)


@pytest.mark.parametrize(
    "arr, expected",
    [
        (pa.array([True, False, True]), [1.0, 0.0, 1.0]),
        (pa.array(np.array([1, 2, 3], dtype=np.float32)), [1.0, 2.0, 3.0]),
        (pa.array(np.array([1, 2, 3], dtype=np.float16)), [1.0, 2.0, 3.0]),
        (pa.array([decimal.Decimal("1.5")] * 3), [1.5, 1.5, 1.5]),
        (pa.array([1, None, 3]), [1.0, math.nan, 3.0]),
        (pa.array([None, None, None]), [math.nan] * 3),
        (pa.array([1, 2, 3], type=pa.duration("ms")), [1.0, 2.0, 3.0]),
        (pa.array([_D] * 3), [1767225600.0] * 3),
        (pa.array([_D.replace(tzinfo=datetime.timezone.utc)] * 3, type=pa.timestamp("ns", tz="UTC")), [1767225600.0] * 3),
        (pa.array([datetime.date(2026, 1, 1)] * 3), [1767225600.0] * 3),
        (pa.array([2**63 - 1, 2**63 - 2, 5], type=pa.int64()), [2.0**63, 2.0**63, 5.0]),  # precision loss is inherent to float64
        (pa.array([2**64 - 1, 1, 2], type=pa.uint64()), [2.0**64, 1.0, 2.0]),
    ],
    ids=["bool", "f32", "f16", "decimal", "nullable_int", "null_type", "duration", "timestamp", "timestamp_tz", "date", "int64_max", "uint64_max"],
)
def test_supported_dtypes_become_float64(tmp_path, arr, expected):
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(3.0), x=arr))
    for col in (LazyColumn(ds, "x"), WindowedColumn(ds, "x")):
        got = col.values()
        assert got.dtype == np.float64 and got.ndim == 1
        np.testing.assert_array_equal(got, np.array(expected))


@pytest.mark.parametrize(
    "arr",
    [
        pa.array(["a", "b", "c"]),
        pa.array(["a", "b", "a"]).dictionary_encode(),
        pa.array([b"a", b"b", b"c"]),
        pa.array([[1], [2, 3], []]),
        pa.array([{"a": 1}, {"a": 2}, {"a": 3}]),
        pa.array([1, 2, 3], type=pa.time32("s")),
    ],
    ids=["string", "dictionary", "binary", "list", "struct", "time_of_day"],
)
def test_non_numeric_columns_raise_the_domain_error_naming_the_column(tmp_path, arr):
    """Regression: bare ValueError/TypeError from numpy/polars (and, for a struct column, a
    silently 2-D (n, 1) 'series') escaped to every caller."""
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(3.0), x=arr))
    for col in (LazyColumn(ds, "x"), WindowedColumn(ds, "x")):
        with pytest.raises(NonNumericColumnError, match="'x'"):
            col.values()
    with pytest.raises(NonNumericColumnError):
        WindowedColumn(ds, "x").window(0, 2)


def test_lazy_variables_turns_non_numeric_into_expression_error(tmp_path):
    """An expression that names a string column must fail with ExpressionError (what the
    derived-variable / view-load callers catch), not a raw ValueError."""
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(3.0), s=pa.array(["a", "b", "c"])))
    lv = LazyVariables({"s": LazyColumn(ds, "s"), "m": MaterializedColumn(np.arange(3.0))})
    with pytest.raises(ExpressionError, match="'s'"):
        lv["s"]
    assert lv["m"].tolist() == [0.0, 1.0, 2.0]


def test_null_timestamps_become_nan_not_huge_negative_numbers(tmp_path):
    """Regression: a null timestamp (NaT) converted to ~-9.2e15 s, a finite value that skewed
    ranges and escaped the non-finite time-axis warning (29.2)."""
    ts = pa.array([_D, None, _D + datetime.timedelta(seconds=2)], type=pa.timestamp("ms"))
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(3.0), x=ts))
    got = LazyColumn(ds, "x").values()
    assert got[0] == 1767225600.0 and got[2] == 1767225602.0
    assert math.isnan(got[1])
    assert check_time_order(got).nan_count == 1


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns", "D"])
def test_to_numeric_nat_is_nan_for_every_unit(unit):
    arr = np.array(["NaT", "2026-01-01"], dtype=f"datetime64[{unit}]")
    got = to_numeric(arr)
    assert math.isnan(got[0]) and got[1] == 1767225600.0


def test_to_numeric_nat_timedelta_and_does_not_mutate_input():
    td = np.array([np.timedelta64(1, "ms"), np.timedelta64("NaT", "ms")])
    got = to_numeric(td)
    assert got[0] == 1.0 and math.isnan(got[1])
    assert np.isnat(td[1])  # the caller's array is untouched


@pytest.mark.parametrize(
    "arr",
    [np.array(5.0), np.ones((3, 2)), np.array(["a", "b"]), np.array([b"x"]), np.array([object(), object()], dtype=object)],
    ids=["0d", "2d", "str", "bytes", "objects"],
)
def test_to_numeric_rejects_what_cannot_be_a_1d_series(arr):
    with pytest.raises(NonNumericColumnError):
        to_numeric(arr)


@pytest.mark.parametrize(
    "arr, dtype_in",
    [
        (np.array([], dtype=np.float64), None),
        (np.array([], dtype=np.int64), None),
        (np.array([], dtype="datetime64[ms]"), None),
        (np.array([True, False]), None),
    ],
)
def test_to_numeric_empty_and_bool(arr, dtype_in):
    got = to_numeric(arr)
    assert got.dtype == np.float64 and got.shape == arr.shape


def test_to_numeric_dates_before_the_epoch_and_odd_units():
    assert to_numeric(np.array(["1960-01-01"], dtype="datetime64[ms]"))[0] == -315619200.0
    for unit in "YMWDhm":
        assert to_numeric(np.array(["2026-01-01"], dtype=f"datetime64[{unit}]"))[0] == 1767225600.0


# -- check_time_order ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "values, nan, dec, first",
    [
        ([], 0, 0, None),
        ([1.0], 0, 0, None),
        ([math.nan], 1, 0, None),
        ([math.nan] * 5, 5, 0, None),
        ([3.0, 2.0, 1.0], 0, 2, 1),
        ([1.0] * 4, 0, 0, None),  # duplicates are allowed
        ([1.0, math.inf, 2.0, -math.inf, 3.0], 2, 0, None),
        ([1.0, 3.0, math.nan, 2.0], 1, 1, 3),  # a NaN is not itself an ordering fault
        ([-math.inf, 0.0], 1, 0, None),
    ],
)
def test_check_time_order_edge_cases(values, nan, dec, first):
    r = check_time_order(np.array(values))
    assert (r.nan_count, r.decreasing_count, r.first_decreasing_index) == (nan, dec, first)
    assert r.ok == (nan == 0 and dec == 0)


@pytest.mark.parametrize(
    "values, expected",
    [
        ([1.0, 2, 3, 4, 0, 5, 6, 7, 8, 1], (0, 2, 4)),  # decrease exactly across a chunk boundary
        ([1.0, 2, 3, math.nan, math.nan, math.nan, math.nan, 1, 5], (4, 1, 7)),  # all-NaN chunk between
        ([5.0, 6, 7, 8, math.nan, math.nan, math.nan, math.nan, 1, 2], (4, 1, 8)),
    ],
)
def test_check_time_order_chunk_boundaries(monkeypatch, values, expected):
    from parquet_analyzer.core import numeric

    monkeypatch.setattr(numeric, "_TIME_ORDER_CHUNK_ROWS", 4)
    r = numeric.check_time_order(np.array(values))
    assert (r.nan_count, r.decreasing_count, r.first_decreasing_index) == expected


@pytest.mark.parametrize("lo, hi", [(50.0, 10.0), (15.0, 14.5), (math.nan, 5.0), (5.0, math.nan), (math.nan, math.nan)])
def test_row_range_for_x_reversed_or_nan_range_overlaps_nothing(tmp_path, lo, hi):
    """data 29.5: a reversed range used to return the whole file."""
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(float(N)), v=np.arange(float(N))))
    assert ds.row_range_for_x("t", lo, hi) == (0, 0)


def test_timezone_aware_statistics_convert_without_a_warning(tmp_path):
    """data 29.5: numpy's tz UserWarning is harmless (value is UTC) and must not surface."""
    import warnings

    ts = pa.array([_D.replace(tzinfo=datetime.timezone.utc)] * 3, type=pa.timestamp("ns", tz="UTC"))
    ds = ParquetDataSource(_write(tmp_path / "f.parquet", t=np.arange(3.0), x=ts))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ds.column_bounds("x") == (1767225600.0, 1767225600.0)
