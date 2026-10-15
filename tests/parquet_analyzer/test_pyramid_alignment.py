"""data 59.2: pyramid buckets are aligned to the bucket grid by our own code (not by whatever batch
sizes pyarrow happens to return), and `set_pyramid` refuses a pyramid that is not on the
``ANCHOR_ROWS`` grid (counted as a build failure, 28.2)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.column import MAX_PYRAMID_FAILURES, DerivedColumn, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.pyramid import DEFAULT_BUCKET_ROWS, ColumnPyramid, build_pyramid
from parquet_analyzer.core.time_index import ANCHOR_ROWS


def _write(path, columns: dict, row_group_size: int):
    pq.write_table(pa.table({k: pa.array(v) for k, v in columns.items()}), path, row_group_size=row_group_size)
    return path


def _reference(v, bucket):
    starts = np.arange(0, len(v), bucket)
    mins, maxs = [], []
    for s in starts:
        seg = v[s : s + bucket]
        seg = seg[np.isfinite(seg)]
        mins.append(seg.min() if len(seg) else np.nan)
        maxs.append(seg.max() if len(seg) else np.nan)
    return starts, np.array(mins), np.array(maxs)


def _assert_matches_reference(p, v, bucket=DEFAULT_BUCKET_ROWS):
    starts, mins, maxs = _reference(v, bucket)
    np.testing.assert_array_equal(p.row_starts, starts)
    np.testing.assert_array_equal(p.mins, mins)
    np.testing.assert_array_equal(p.maxs, maxs)


def _irregular_iter_batches(path, sizes):
    """A stand-in for ParquetFile.iter_batches returning batches of the given (cycled) sizes,
    ignoring batch_size and row-group boundaries."""

    def iter_batches(self, batch_size=65536, row_groups=None, columns=None, **kw):
        table = pq.read_table(path, columns=columns)
        pos, k = 0, 0
        while pos < table.num_rows:
            size = sizes[k % len(sizes)]
            k += 1
            yield from table.slice(pos, size).to_batches()
            pos += size

    return iter_batches


@pytest.mark.parametrize(
    "n, row_group",
    [(50_000, 5000), (50_000, 7000), (50_000, 8192), (1_000_000, 100_000), (1_000_000, 1_000_000),
     (30_001, 999), (8193, 8192), (8191, 100), (20_000, 20_000)],
    ids=lambda x: str(x),
)
def test_pyramid_starts_are_anchor_aligned_for_any_row_group_size(tmp_path, n, row_group):
    rng = np.random.default_rng(n + row_group)
    v = rng.normal(size=n)
    v[rng.integers(0, n, 20)] = np.nan
    p = build_pyramid(_write(tmp_path / "a.parquet", {"v": v}, row_group), ["v"])["v"]
    assert np.all(p.row_starts % ANCHOR_ROWS == 0)
    _assert_matches_reference(p, v)


def test_pyramid_aligned_when_batches_are_irregular(tmp_path, monkeypatch):
    """Regression (59.2): the bucket grid must not depend on the batch sizes pyarrow returns.
    Batches of 5000, 3192, 7000, ... used to become one bucket each (starts 0, 5000, 8192, ...)."""
    v = np.random.default_rng(1).normal(size=60_000)
    path = _write(tmp_path / "irr.parquet", {"v": v}, 7000)
    monkeypatch.setattr(
        pq.ParquetFile, "iter_batches", _irregular_iter_batches(path, [5000, 3192, 7000, 1, 8191, 12_345, 20_000])
    )
    p = build_pyramid(path, ["v"])["v"]
    assert np.array_equal(p.row_starts, np.arange(len(p)) * ANCHOR_ROWS)
    _assert_matches_reference(p, v)


def test_pyramid_small_buckets_irregular_batches_multi_column(tmp_path, monkeypatch):
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=1234), rng.normal(size=1234) * 5
    path = _write(tmp_path / "m.parquet", {"a": a, "b": b}, 100)
    monkeypatch.setattr(pq.ParquetFile, "iter_batches", _irregular_iter_batches(path, [7, 50, 301]))
    pyr = build_pyramid(path, ["a", "b"], 100)
    _assert_matches_reference(pyr["a"], a, 100)
    _assert_matches_reference(pyr["b"], b, 100)


# -- the guard in set_pyramid ----------------------------------------------------------------------

_Z3 = np.zeros(3)
_BAD = {
    "gap": ColumnPyramid(np.array([0, 8192, 24576], dtype=np.int64), _Z3, _Z3),
    "misaligned": ColumnPyramid(np.array([0, 5000, 10000], dtype=np.int64), _Z3, _Z3),
    "offset": ColumnPyramid(np.array([1, 8193, 16385], dtype=np.int64), _Z3, _Z3),
    "length_mismatch": ColumnPyramid(np.array([0, 8192], dtype=np.int64), _Z3, _Z3),
}


@pytest.fixture
def windowed(tmp_path):
    path = _write(tmp_path / "w.parquet", {"a": np.arange(30_000.0)}, 5000)
    return WindowedColumn(ParquetDataSource(path), "a")


@pytest.mark.parametrize("kind", list(_BAD))
def test_set_pyramid_rejects_a_misaligned_pyramid_as_a_build_failure(windowed, kind, caplog):
    assert windowed.request_pyramid() is not None  # a build is in flight
    windowed.set_pyramid(_BAD[kind])
    assert windowed.pyramid is None
    assert windowed._pyramid_building is False  # not stuck: the outcome was settled exactly once
    assert windowed._pyramid_failures == 1
    assert any("pyramid" in r.getMessage() for r in caplog.records)
    assert windowed.request_pyramid() is not None  # may retry; drawing uses real reads meanwhile


def test_rejections_count_towards_giving_up(windowed):
    for _ in range(MAX_PYRAMID_FAILURES):
        assert windowed.request_pyramid() is not None
        windowed.set_pyramid(_BAD["gap"])
    assert windowed.pyramid_given_up
    assert windowed.request_pyramid() is None


def test_set_pyramid_accepts_a_built_pyramid(windowed):
    good = windowed.request_pyramid()()
    windowed.set_pyramid(good)
    assert windowed.pyramid is good and windowed._pyramid_failures == 0


def test_derived_column_rejection_tells_listeners_it_failed():
    d = DerivedColumn("a", {}, 30_000, None, (0, 0))
    told = []
    d.add_pyramid_listener("k", told.append)
    d._pyramid_building = True
    d.set_pyramid(_BAD["misaligned"])
    assert told == [False]
    assert d.pyramid is None and d._pyramid_building is False and d._pyramid_failures == 1


def test_derived_column_accepts_aligned_pyramid():
    d = DerivedColumn("a", {}, 30_000, None, (0, 0))
    told = []
    d.add_pyramid_listener("k", told.append)
    good = ColumnPyramid(np.arange(4, dtype=np.int64) * ANCHOR_ROWS, np.zeros(4), np.ones(4))
    d.set_pyramid(good)
    assert told == [True] and d.pyramid is good
