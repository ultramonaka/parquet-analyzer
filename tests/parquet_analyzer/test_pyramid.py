from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.numeric import to_numeric
from parquet_analyzer.core.pyramid import build_pyramid, pyramid_bounds


@pytest.fixture
def multi_row_group_parquet(tmp_path):
    n = 50_000
    rng = np.random.default_rng(0)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    a = rng.normal(size=n)
    b = rng.normal(size=n) * 10.0
    table = pa.table({"time": pa.array(times), "a": pa.array(a), "b": pa.array(b)})
    path = tmp_path / "multi_rg.parquet"
    pq.write_table(table, path, row_group_size=5000)
    return path, times, a, b


def test_pyramid_has_one_bucket_per_bucket_rows(multi_row_group_parquet):
    path, times, _a, _b = multi_row_group_parquet
    pyramid = build_pyramid(path, ["a"], bucket_rows=1000)
    assert len(pyramid["a"]) == len(times) // 1000


def test_pyramid_bounds_match_the_real_column(multi_row_group_parquet):
    path, _times, a, b = multi_row_group_parquet
    pyramid = build_pyramid(path, ["a", "b"], bucket_rows=1000)

    lo, hi = pyramid_bounds(pyramid["a"])
    assert lo == pytest.approx(a.min())
    assert hi == pytest.approx(a.max())

    lo, hi = pyramid_bounds(pyramid["b"])
    assert lo == pytest.approx(b.min())
    assert hi == pytest.approx(b.max())


def test_pyramid_row_starts_are_monotonic_and_start_at_zero(multi_row_group_parquet):
    path, _times, _a, _b = multi_row_group_parquet
    pyramid = build_pyramid(path, ["a"], bucket_rows=1000)
    starts = pyramid["a"].row_starts
    assert starts[0] == 0
    assert np.all(np.diff(starts) > 0)


def test_pyramid_handles_datetime_column(multi_row_group_parquet):
    path, times, _a, _b = multi_row_group_parquet
    pyramid = build_pyramid(path, ["time"], bucket_rows=1000)
    lo, hi = pyramid_bounds(pyramid["time"])
    real_t = to_numeric(times)
    assert lo == pytest.approx(real_t.min())
    assert hi == pytest.approx(real_t.max())


def test_pyramid_bounds_none_for_all_nan_bucket_data():
    from parquet_analyzer.core.pyramid import ColumnPyramid

    empty = ColumnPyramid(row_starts=np.array([], dtype=np.int64), mins=np.array([]), maxs=np.array([]))
    assert pyramid_bounds(empty) is None

    all_nan = ColumnPyramid(
        row_starts=np.array([0, 10]), mins=np.array([np.nan, np.nan]), maxs=np.array([np.nan, np.nan])
    )
    assert pyramid_bounds(all_nan) is None
