"""Property tests for boundary conditions at scale (data 59.3; app 46.1 settings).

Random sizes (1..50k rows), random row-group sizes (1..20k, many not multiples of 8192), value
types float64 / float32 / int32 (int32 with nulls), and NaN / +Inf / -Inf / duplicate runs /
all-NaN buckets placed right at the boundaries that matter: multiples of the bucket / anchor size
(8192), row-group boundaries, and the block boundaries of the window cache. The oracle never goes
through the code under test: whole columns are read with polars and turned into numbers with
`to_numeric`; expressions are evaluated on those whole arrays.

What the other test_property_* files already cover is not repeated here: searchsorted on float64
axes with duplicate runs (time_index), float64/int64 windows with random NaN (windowed),
chunked_stats with random chunk sizes and non-finite values (analysis).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.analysis import basic_stats, chunked_stats
from parquet_analyzer.core.column import (
    LazyVariables,
    MaterializedColumn,
    WindowedColumn,
    make_derived_column,
)
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import evaluate_expression
from parquet_analyzer.core.numeric import to_numeric
from parquet_analyzer.core.pyramid import DEFAULT_BUCKET_ROWS, build_pyramid, pyramid_bounds
from parquet_analyzer.core.time_index import ANCHOR_ROWS, TimeIndex

BLOCK = 1024
DTYPES = ["float64", "float32", "int32"]


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


def _row_group(n: int, row_group: int) -> int:
    """Keep the number of row groups modest (a file of 50k one-row groups is slow to write/read)."""
    return max(1, row_group, n // 300)


def _boundaries(n: int, row_group: int) -> list[int]:
    """Rows next to which the special values are put."""
    out = {0, n - 1}
    for step in (ANCHOR_ROWS, row_group, BLOCK):
        out.update(range(step, n, step))
    return sorted(b for b in out if 0 <= b < n)


def _column(n, dtype, seed, row_group, specials):
    """(arrow array, whole-column oracle as float64). `specials` in 'none' / 'edges' / 'nan_bucket' /
    'inf_edges' picks which special values go next to the boundaries."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.normal(size=n)) * 10.0
    mask = np.zeros(n, dtype=bool)  # nulls (int32 only)
    if dtype == "int32":
        v = np.round(base).astype(np.int32)
    else:
        v = base.astype(dtype)
    if specials != "none":
        bounds = _boundaries(n, row_group)
        pick = [b for b in bounds if rng.random() < 0.6] or bounds[:1]
        for b in pick:
            for r in (b - 1, b, b + 1):
                if not 0 <= r < n:
                    continue
                kind = rng.integers(0, 3 if specials == "inf_edges" else 1)
                if dtype == "int32":
                    mask[r] = True
                elif kind == 0:
                    v[r] = np.nan
                else:
                    v[r] = np.inf if kind == 1 else -np.inf
        if specials == "nan_bucket":  # a whole bucket without a finite value
            k = int(rng.integers(0, -(-n // ANCHOR_ROWS)))
            lo, hi = k * ANCHOR_ROWS, min(n, (k + 1) * ANCHOR_ROWS)
            if dtype == "int32":
                mask[lo:hi] = True
            else:
                v[lo:hi] = rng.choice([np.nan, np.inf, -np.inf], hi - lo)
    arr = pa.array(v, mask=mask if mask.any() else None)
    return arr


def _write(path: Path, columns: dict, row_group: int) -> None:
    pq.write_table(pa.table(columns), path, row_group_size=row_group)


def _oracle(path: Path, name: str) -> np.ndarray:
    return to_numeric(pl.read_parquet(path, columns=[name]).get_column(name).to_numpy())


def _windows(rng, n, row_group):
    """Windows straddling the interesting boundaries, plus random ones."""
    out = [(0, n), (0, min(n, 1)), (max(0, n - 1), n)]
    for b in _boundaries(n, row_group):
        w = int(rng.integers(1, 40))
        out.append((max(0, b - w), min(n, b + w)))
    for _ in range(5):
        a, b = sorted(int(x) for x in rng.integers(0, n + 1, size=2))
        out.append((a, b))
    return out[:40]


_SIZES = dict(
    seed=st.integers(0, 2**32 - 1),
    n=st.one_of(st.integers(1, 50_000), st.sampled_from([1, 2, 8191, 8192, 8193, 16384, 16385, 30_000])),
    row_group=st.one_of(st.integers(1, 20_000), st.sampled_from([1000, 5000, 7000, 8192, 10_000])),
    dtype=st.sampled_from(DTYPES),
    specials=st.sampled_from(["none", "edges", "nan_bucket", "inf_edges"]),
)


# --- pyramid ------------------------------------------------------------------------------------

@settings(max_examples=_examples(100))
@given(**_SIZES)
def test_pyramid_aligned_and_exact_at_boundaries(seed, n, row_group, dtype, specials):
    rg = _row_group(n, row_group)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "p.parquet"
        _write(path, {"v": _column(n, dtype, seed, rg, specials)}, rg)
        whole = _oracle(path, "v")
        p = build_pyramid(path, ["v"])["v"]
    n_buckets = -(-n // DEFAULT_BUCKET_ROWS)
    assert np.array_equal(p.row_starts, np.arange(n_buckets) * ANCHOR_ROWS)
    for k in range(n_buckets):
        seg = whole[k * DEFAULT_BUCKET_ROWS : (k + 1) * DEFAULT_BUCKET_ROWS]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            assert (p.mins[k], p.maxs[k]) == (seg.min(), seg.max()), k
        else:
            assert np.isnan(p.mins[k]) and np.isnan(p.maxs[k]), k
    fin = whole[np.isfinite(whole)]
    bounds = pyramid_bounds(p)
    if len(fin):
        assert bounds == (fin.min(), fin.max())
    else:
        assert bounds is None


@settings(max_examples=_examples(60))
@given(
    seed=st.integers(0, 2**32 - 1),
    n=st.integers(1, 40_000),
    dtype=st.sampled_from(DTYPES),
    specials=st.sampled_from(["none", "edges", "nan_bucket", "inf_edges"]),
    sizes=st.lists(st.integers(1, 20_000), min_size=1, max_size=6),
)
def test_pyramid_aligned_for_any_batch_sizes(seed, n, dtype, specials, sizes):
    """The batches iter_batches returns may have any size (59.2); the buckets must not follow them."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "p.parquet"
        _write(path, {"v": _column(n, dtype, seed, 5000, specials)}, 5000)
        whole = _oracle(path, "v")

        def iter_batches(self, batch_size=65536, row_groups=None, columns=None, **kw):
            table = pq.read_table(path, columns=columns)
            pos, k = 0, 0
            while pos < n:
                size = sizes[k % len(sizes)]
                k += 1
                yield from table.slice(pos, size).to_batches()
                pos += size

        orig = pq.ParquetFile.iter_batches
        pq.ParquetFile.iter_batches = iter_batches
        try:
            p = build_pyramid(path, ["v"])["v"]
        finally:
            pq.ParquetFile.iter_batches = orig
    n_buckets = -(-n // DEFAULT_BUCKET_ROWS)
    assert np.array_equal(p.row_starts, np.arange(n_buckets) * ANCHOR_ROWS)
    for k in range(n_buckets):
        seg = whole[k * DEFAULT_BUCKET_ROWS : (k + 1) * DEFAULT_BUCKET_ROWS]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            assert (p.mins[k], p.maxs[k]) == (seg.min(), seg.max()), k
        else:
            assert np.isnan(p.mins[k]) and np.isnan(p.maxs[k]), k


# --- windowed columns of other value types ----------------------------------------------------------

@settings(max_examples=_examples(100))
@given(**_SIZES, cache_blocks=st.integers(2, 4))
def test_windowed_column_other_types_equal_whole(seed, n, row_group, dtype, specials, cache_blocks):
    rg = _row_group(n, row_group)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "w.parquet"
        _write(path, {"v": _column(n, dtype, seed, rg, specials)}, rg)
        whole = _oracle(path, "v")
        col = WindowedColumn(ParquetDataSource(path, block_rows=BLOCK, cache_blocks=cache_blocks), "v")
        for lo, hi in _windows(np.random.default_rng(seed ^ 0xB0), n, rg):
            np.testing.assert_array_equal(col.window(lo, hi), whole[lo:hi], err_msg=str((lo, hi)))


# --- derived (window-computed) variables with non-finite values at the seams -------------------------

_EXPRS = ["delta(v)", "rolling_mean(v, 7)", "rolling_nanmean(v, 6)", "trailing_mean(v, 9)",
          "trailing_nanmean(v, 4)", "where(v > 0, v, w)", "delta(trailing_mean(v, 5)) + w"]


@settings(max_examples=_examples(100))
@given(**_SIZES, expr=st.sampled_from(_EXPRS))
def test_derived_window_equals_whole_with_nonfinite_at_seams(seed, n, row_group, dtype, specials, expr):
    rg = _row_group(n, row_group)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "d.parquet"
        _write(path, {"v": _column(n, dtype, seed, rg, specials), "w": _column(n, "float64", seed ^ 1, rg, specials)}, rg)
        whole = {c: _oracle(path, c) for c in ("v", "w")}
        ds = ParquetDataSource(path, block_rows=BLOCK)
        cols = {c: WindowedColumn(ds, c) for c in whole}
        col = make_derived_column(expr, cols, n, None)
        assert col is not None, expr
        with np.errstate(all="ignore"):
            expected = np.asarray(
                evaluate_expression(expr, LazyVariables({k: MaterializedColumn(v) for k, v in whole.items()}), None),
                dtype=np.float64,
            )
            for lo, hi in _windows(np.random.default_rng(seed ^ 0xD0), n, rg):
                np.testing.assert_allclose(
                    col.window(lo, hi), expected[lo:hi], rtol=1e-12, equal_nan=True, err_msg=f"{expr} {(lo, hi)}"
                )


# --- TimeIndex on float32 / int32 axes, take(), and unusable axes at the seams --------------------------

def _axis(n, dtype, seed, dup_fraction, row_group):
    """Ascending (non-decreasing) axis of the given type; a run of equal values around a boundary."""
    rng = np.random.default_rng(seed)
    steps = rng.integers(1, 4, size=n).astype(np.int64)
    steps[rng.random(n) < dup_fraction] = 0
    bounds = _boundaries(n, row_group)
    b = bounds[int(rng.integers(0, len(bounds)))]
    steps[max(1, b - 300) : b + 300] = 0  # duplicates straddling a seam
    steps[0] = 0
    t = 1000 + np.cumsum(steps)  # < 2**24: exact in float32
    return t.astype(dtype), t.astype(np.float64)


@settings(max_examples=_examples(100))
@given(
    seed=st.integers(0, 2**32 - 1),
    n=st.one_of(st.integers(1, 50_000), st.sampled_from([1, 2, 8192, 8193, 16385])),
    row_group=st.one_of(st.integers(1, 20_000), st.sampled_from([5000, 7000, 8192])),
    dtype=st.sampled_from(DTYPES),
    dup_fraction=st.sampled_from([0.0, 0.5, 1.0]),
)
def test_time_index_other_types_take_search_value_at(seed, n, row_group, dtype, dup_fraction):
    rg = _row_group(n, row_group)
    arr, oracle = _axis(n, dtype, seed, dup_fraction, rg)
    rng = np.random.default_rng(seed ^ 0xAA)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.parquet"
        _write(path, {"time": pa.array(arr)}, rg)
        idx = TimeIndex.build(ParquetDataSource(str(path), block_rows=2048), "time")
        assert idx is not None and len(idx) == n
        assert np.array_equal(to_numeric(pl.read_parquet(path).get_column("time").to_numpy()), oracle)
        rows = np.concatenate([
            rng.integers(0, n, size=30), _boundaries(n, rg)[:40],
            [0, n - 1, -1, -n], np.arange(0, n, ANCHOR_ROWS),
        ]).astype(np.int64)
        np.testing.assert_array_equal(idx.take(rows), oracle[rows])
        assert idx.take(np.array([], dtype=np.int64)).shape == (0,)
        for r in rows[:15]:
            assert idx.value_at(int(r)) == oracle[int(r)]
        probes = np.concatenate([
            rng.choice(oracle, 30), oracle[::ANCHOR_ROWS] - 1, oracle[::ANCHOR_ROWS] + 1,
            [oracle[0] - 5, oracle[-1] + 5, -np.inf, np.inf, np.nan],
        ])
        for side in ("left", "right"):
            np.testing.assert_array_equal(idx.searchsorted(probes, side), np.searchsorted(oracle, probes, side=side))
        anchors = idx.anchor_rows()
        assert anchors[0] == 0 and anchors[-1] == n - 1
        np.testing.assert_array_equal(idx.approx_take(anchors), oracle[anchors])  # exact at held rows


@settings(max_examples=_examples(100))
@given(
    seed=st.integers(0, 2**32 - 1),
    n=st.integers(3, 50_000),
    row_group=st.integers(1, 20_000),
    dtype=st.sampled_from(["float64", "float32"]),
    flaw=st.sampled_from(["nan", "inf", "-inf", "decreasing"]),
)
def test_time_index_refuses_unusable_axis_flaw_at_a_seam(seed, n, row_group, dtype, flaw):
    """A NaN / Inf / step back exactly on a boundary row (anchor, row-group start, last row) means
    no index (29, 36.6 D): the caller keeps the full-load path and its warning."""
    rg = _row_group(n, row_group)
    arr, _ = _axis(n, dtype, seed, 0.0, rg)
    rng = np.random.default_rng(seed)
    bounds = [b for b in _boundaries(n, rg) if b >= 1]
    pos = bounds[int(rng.integers(0, len(bounds)))]
    arr = arr.copy()
    arr[pos] = {"nan": np.nan, "inf": np.inf, "-inf": -np.inf, "decreasing": arr[pos - 1] - 10}[flaw]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.parquet"
        _write(path, {"time": pa.array(arr)}, rg)
        assert TimeIndex.build(ParquetDataSource(str(path)), "time") is None


# --- chunked_stats with whole chunks of NaN / Inf and float32 sources -----------------------------------

@settings(max_examples=_examples(100))
@given(
    seed=st.integers(0, 2**32 - 1),
    chunk_rows=st.one_of(st.integers(1, 40), st.sampled_from([64, 1000, 8192])),
    n_chunks=st.integers(1, 12),
    plan=st.lists(st.sampled_from(["data", "nan", "inf", "ninf", "mixed"]), min_size=1, max_size=12),
    dtype=st.sampled_from(["float64", "float32"]),
)
def test_chunked_stats_with_whole_nonfinite_chunks(seed, chunk_rows, n_chunks, plan, dtype):
    """Chunks that are entirely NaN / +Inf / -Inf sit between chunks of data; the sizes of the chunks
    used to read and the plan (which chunk is what) vary."""
    rng = np.random.default_rng(seed)
    parts = []
    for i in range(n_chunks):
        kind = plan[i % len(plan)]
        part = rng.normal(0, 50, chunk_rows)
        if kind == "nan":
            part[:] = np.nan
        elif kind == "inf":
            part[:] = np.inf
        elif kind == "ninf":
            part[:] = -np.inf
        elif kind == "mixed":
            part[::2] = np.nan
        parts.append(part)
    y = np.concatenate(parts).astype(dtype).astype(np.float64)
    n = len(y)
    with np.errstate(all="ignore"):
        want = basic_stats(y)
        got = chunked_stats(lambda a, b: y[a:b], 0, n, chunk_rows, 1 << 10)
    for key in ("count", "nan_count"):
        assert got[key] == want[key], (key, got, want)
    for key in ("min", "max", "median"):
        assert (np.isnan(got[key]) and np.isnan(want[key])) or got[key] == want[key], (key, got, want)
    for key in ("mean", "std"):
        a, b = got[key], want[key]
        if not (np.isfinite(a) and np.isfinite(b)):
            assert (np.isnan(a) and np.isnan(b)) or a == b, (key, got, want)
        else:
            assert abs(a - b) <= 1e-9 * abs(b) + 1e-9, (key, got, want)
