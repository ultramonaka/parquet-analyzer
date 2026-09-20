from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.column import LazyColumn, LazyVariables, MaterializedColumn, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.numeric import to_numeric


def test_materialized_column_returns_the_same_array_object():
    arr = np.array([1.0, 2.0, 3.0])
    col = MaterializedColumn(arr)
    assert col.values() is arr


@pytest.fixture
def two_column_parquet(tmp_path):
    n = 1000
    rng = np.random.default_rng(0)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    a = rng.normal(size=n)
    table = pa.table({"time": pa.array(times), "a": pa.array(a)})
    path = tmp_path / "two_col.parquet"
    pq.write_table(table, path)
    return path, times, a


def test_lazy_column_does_not_read_until_values_is_called(two_column_parquet):
    path, _times, a = two_column_parquet
    ds = ParquetDataSource(path)
    col = LazyColumn(ds, "a")
    assert col._array is None  # not read yet

    out = col.values()
    np.testing.assert_array_equal(out, a)
    assert col._array is not None


def test_lazy_column_caches_after_first_read(two_column_parquet):
    path, _times, _a = two_column_parquet
    ds = ParquetDataSource(path)
    col = LazyColumn(ds, "a")
    first = col.values()
    second = col.values()
    assert first is second  # cached, not re-read from disk


def test_lazy_column_converts_datetime_columns(two_column_parquet):
    path, times, _a = two_column_parquet
    ds = ParquetDataSource(path)
    col = LazyColumn(ds, "time")
    out = col.values()
    np.testing.assert_array_equal(out, to_numeric(times))


@pytest.fixture
def multi_row_group_parquet(tmp_path):
    n = 50_000
    rng = np.random.default_rng(0)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    a = rng.normal(size=n)
    table = pa.table({"time": pa.array(times), "a": pa.array(a)})
    path = tmp_path / "multi_rg.parquet"
    pq.write_table(table, path, row_group_size=5000)
    return path, times, a


def test_windowed_column_window_matches_the_real_slice(multi_row_group_parquet):
    path, _times, a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")
    out = col.window(1234, 5678)
    np.testing.assert_array_equal(out, a[1234:5678])


def test_windowed_column_window_does_not_read_the_whole_file(multi_row_group_parquet, monkeypatch):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")

    original_read_columns = ds.read_columns
    seen_ranges = []

    def spy(columns, row_start=0, row_end=None):
        seen_ranges.append((row_start, row_end))
        return original_read_columns(columns, row_start, row_end)

    monkeypatch.setattr(ds, "read_columns", spy)
    col.window(100, 200)
    assert seen_ranges  # at least one read happened
    for _start, end in seen_ranges:
        assert end is None or end <= ds._block_rows  # never the whole 50,000-row file


def test_windowed_column_values_falls_back_to_the_whole_column(multi_row_group_parquet):
    path, _times, a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")
    np.testing.assert_array_equal(col.values(), a)


def test_windowed_column_bounds_matches_the_real_column(multi_row_group_parquet):
    path, _times, a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")
    lo, hi = col.bounds()
    assert lo == pytest.approx(a.min())
    assert hi == pytest.approx(a.max())


def test_windowed_column_pyramid_starts_unset(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")
    assert col.pyramid is None


def test_windowed_column_request_pyramid_returns_a_working_job(multi_row_group_parquet):
    path, _times, a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")

    job = col.request_pyramid()
    assert job is not None
    assert col.pyramid is None  # not set until the caller calls set_pyramid()

    pyramid = job()  # what a BackgroundWorker would run off the UI thread
    col.set_pyramid(pyramid)
    assert col.pyramid is pyramid
    assert pyramid.mins.min() == pytest.approx(a.min())
    assert pyramid.maxs.max() == pytest.approx(a.max())


def test_windowed_column_request_pyramid_returns_none_while_building_or_once_built(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    col = WindowedColumn(ds, "a")

    job = col.request_pyramid()
    assert job is not None
    assert col.request_pyramid() is None  # already building; no second job

    col.set_pyramid(job())
    assert col.request_pyramid() is None  # already built; no rebuild needed


def test_windowed_column_empty_window():
    ds = object()  # never touched; row_end <= row_start short-circuits before any I/O
    col = WindowedColumn(ds, "a")
    out = col.window(100, 100)
    assert len(out) == 0


class _CountingColumn:
    """Wraps a MaterializedColumn and counts values() calls, to prove
    LazyVariables never touches a column it wasn't asked for."""

    def __init__(self, array):
        self._inner = MaterializedColumn(array)
        self.call_count = 0

    def values(self):
        self.call_count += 1
        return self._inner.values()


def test_lazy_variables_only_materializes_looked_up_columns():
    a = _CountingColumn(np.array([1.0, 2.0]))
    b = _CountingColumn(np.array([10.0, 20.0]))
    mapping = LazyVariables({"a": a, "b": b})

    assert "a" in mapping  # __contains__ must not materialize
    assert a.call_count == 0
    assert b.call_count == 0

    np.testing.assert_array_equal(mapping["a"], [1.0, 2.0])
    assert a.call_count == 1
    assert b.call_count == 0  # never looked up, never materialized


def test_lazy_variables_supports_expression_evaluation():
    from parquet_analyzer.core.expression import evaluate_expression

    a = _CountingColumn(np.array([1.0, 2.0, 3.0]))
    b = _CountingColumn(np.array([10.0, 20.0, 30.0]))
    mapping = LazyVariables({"a": a, "b": b})

    result = evaluate_expression("a * 2", mapping)
    np.testing.assert_array_equal(result, [2.0, 4.0, 6.0])
    assert a.call_count == 1
    assert b.call_count == 0  # the expression never referenced b
