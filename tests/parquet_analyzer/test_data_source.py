from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.numeric import to_numeric


@pytest.fixture
def multi_row_group_parquet(tmp_path):
    """10 row groups of 5000 rows each, one monotonic time column and one
    non-monotonic (random) column -- exercises row-group-spanning behavior
    the default single-row-group sample_parquet_path fixture can't.
    """
    n = 50_000
    rng = np.random.default_rng(0)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    a = rng.normal(size=n)
    table = pa.table({"time": pa.array(times), "a": pa.array(a)})
    path = tmp_path / "multi_rg.parquet"
    pq.write_table(table, path, row_group_size=5000)
    return path, times, a


def test_row_groups_cover_the_whole_file_contiguously(multi_row_group_parquet):
    path, times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    groups = ds.row_groups()
    assert len(groups) == 10
    assert groups[0] == (0, 5000)
    assert sum(n for _, n in groups) == len(times)
    # contiguous: each group's offset is the previous one's offset + count
    offset = 0
    for start, count in groups:
        assert start == offset
        offset += count


def test_column_bounds_matches_the_real_column(multi_row_group_parquet):
    path, times, a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    lo, hi = ds.column_bounds("a")
    assert lo == pytest.approx(a.min())
    assert hi == pytest.approx(a.max())

    t_lo, t_hi = ds.column_bounds("time")
    real_t = to_numeric(times)
    assert t_lo == pytest.approx(real_t.min())
    assert t_hi == pytest.approx(real_t.max())


def test_column_bounds_unknown_column_raises_keyerror(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    with pytest.raises(KeyError):
        ds.column_stats("does_not_exist")


def test_is_monotonic(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    assert ds.is_monotonic("time") is True
    assert ds.is_monotonic("a") is False


def test_row_range_for_x_covers_the_exact_brute_force_range(multi_row_group_parquet):
    path, times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    real_t = to_numeric(times)

    x_lo, x_hi = real_t[12345], real_t[34567]
    row_start, row_end = ds.row_range_for_x("time", x_lo, x_hi)

    brute_lo = int(np.searchsorted(real_t, x_lo, side="left"))
    brute_hi = int(np.searchsorted(real_t, x_hi, side="right"))
    # row_range_for_x snaps to row-group boundaries, so it may return a wider
    # range than the brute-force exact one, but must never be narrower.
    assert row_start <= brute_lo
    assert row_end >= brute_hi
    # and it should be row-group-boundary-aligned
    group_starts = {start for start, _n in ds.row_groups()}
    group_ends = {start + n for start, n in ds.row_groups()}
    assert row_start in group_starts
    assert row_end in group_ends


def test_row_range_for_x_no_overlap_returns_empty(multi_row_group_parquet):
    path, times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    real_t = to_numeric(times)
    row_start, row_end = ds.row_range_for_x("time", real_t.max() + 1000.0, real_t.max() + 2000.0)
    assert (row_start, row_end) == (0, 0)


def test_read_window_matches_read_columns(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    windowed = ds.read_window(["a", "time"], 12000, 15000)
    direct = ds.read_columns(["a", "time"], 12000, 15000)
    assert np.array_equal(windowed["a"], direct["a"])
    assert np.array_equal(windowed["time"], direct["time"])


def test_read_window_spans_multiple_cache_blocks(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path, block_rows=1000)
    windowed = ds.read_window(["a"], 1500, 3500)
    direct = ds.read_columns(["a"], 1500, 3500)
    assert len(windowed["a"]) == 2000
    assert np.array_equal(windowed["a"], direct["a"])


def test_read_window_empty_range(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path)
    out = ds.read_window(["a"], 100, 100)
    assert len(out["a"]) == 0


def test_read_window_reuses_cached_blocks(multi_row_group_parquet):
    path, _times, _a = multi_row_group_parquet
    ds = ParquetDataSource(path, block_rows=1000)
    first = ds._read_block_cached(("a",), 1)
    second = ds._read_block_cached(("a",), 1)
    assert first["a"] is second["a"]
