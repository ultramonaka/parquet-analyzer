"""Property tests: windowed columns equal the whole-array answer (app 46.6 (1), A3).

The oracle never goes through the classes under test: the whole column is read with
polars/pyarrow and turned into numbers with `to_numeric`; expressions are evaluated with
`evaluate_expression` over those whole arrays. Small `block_rows` / `cache_blocks` make the block
cache hit and evict within one example.

`trailing_mean` (expressions 31.2) is intentionally not in the expression list: it is added on
another branch (dev notes 28), which adds it here.
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

from parquet_analyzer.core.column import (
    DerivedColumn,
    LazyVariables,
    MaterializedColumn,
    WindowedColumn,
    make_derived_column,
)
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import evaluate_expression
from parquet_analyzer.core.numeric import to_numeric
from parquet_analyzer.core.time_index import ANCHOR_ROWS, TimeIndex

BLOCK = 1000


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


def _write(path: Path, n: int, seed: int, row_group: int) -> dict[str, np.ndarray]:
    """float64 (with NaN), int64 and a NaN-heavy column; returns the arrays that were written."""
    rng = np.random.default_rng(seed)
    f = rng.normal(size=n) * 100
    f[rng.random(n) < 0.05] = np.nan
    g = rng.integers(-1000, 1000, size=n).astype(np.int64)
    h = np.cumsum(rng.normal(size=n))
    h[rng.random(n) < 0.3] = np.nan
    cols = {"f": f, "g": g, "h": h}
    pq.write_table(pa.table(cols), path, row_group_size=max(1, row_group))
    return cols


def _oracle(path: Path, name: str) -> np.ndarray:
    """The whole column, read by polars (not by ParquetDataSource), as float64."""
    return to_numeric(pl.read_parquet(path, columns=[name]).get_column(name).to_numpy())


def _ranges(rng: np.random.Generator, n: int) -> list[tuple[int, int]]:
    """A *sequence* of reads: repeats, adjacent, block-straddling, empty, past the end, negative."""
    out: list[tuple[int, int]] = []
    for _ in range(25):
        kind = int(rng.integers(0, 7))
        if kind == 0 and out:
            out.append(out[-1])  # same range again (cache hit)
        elif kind == 1 and out:
            out.append((out[-1][1], out[-1][1] + int(rng.integers(1, 3 * BLOCK))))  # adjacent
        elif kind == 2:
            b = int(rng.integers(1, max(2, n // BLOCK + 1))) * BLOCK  # straddles a block boundary
            out.append((b - int(rng.integers(1, 300)), b + int(rng.integers(1, 300))))
        elif kind == 3:
            p = int(rng.integers(0, n + 1))
            out.append((p, p))  # lo == hi
        elif kind == 4:
            out.append((int(rng.integers(0, n + 1)), n + int(rng.integers(1, 5000))))  # hi > rows
        elif kind == 5:
            out.append((-int(rng.integers(1, 5000)), int(rng.integers(0, n + 1))))  # lo < 0
        else:
            a, b = sorted(int(x) for x in rng.integers(0, n + 1, size=2))
            out.append((a, b))
    return out


def _clamp(lo: int, hi: int, n: int) -> tuple[int, int]:
    lo, hi = max(0, lo), min(n, hi)
    return (lo, max(lo, hi))


@settings(max_examples=_examples(30))
@given(
    seed=st.integers(0, 2**32 - 1),
    blocks_x10=st.integers(5, 200),  # rows = 0.5..20 blocks
    row_group=st.sampled_from([137, 999, 1000, 4096, 50_000]),
    cache_blocks=st.integers(2, 4),
)
def test_read_window_and_windowed_column_equal_whole(seed, blocks_x10, row_group, cache_blocks):
    n = blocks_x10 * BLOCK // 10
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "w.parquet"
        _write(path, n, seed, row_group)
        ds = ParquetDataSource(path, block_rows=BLOCK, cache_blocks=cache_blocks)
        whole = {c: _oracle(path, c) for c in ("f", "g", "h")}
        raw_whole = {c: pl.read_parquet(path, columns=[c]).get_column(c).to_numpy() for c in whole}
        wcols = {c: WindowedColumn(ds, c) for c in whole}
        for lo, hi in _ranges(np.random.default_rng(seed ^ 0xA5), n):
            a, b = _clamp(lo, hi, n)
            got = ds.read_window(["f", "g", "h"], lo, hi)  # several columns in one read
            for c in whole:
                np.testing.assert_array_equal(got[c], raw_whole[c][a:b], err_msg=f"read_window {c} {(lo, hi)}")
                w = wcols[c].window(lo, hi)
                # WindowedColumn.window for lo<0 / hi>n follows the data source's clamping
                np.testing.assert_array_equal(w, whole[c][a:b], err_msg=f"window {c} {(lo, hi)}")
        # single-column reads use a different cache key than the 3-column one
        for lo, hi in _ranges(np.random.default_rng(seed ^ 0x5A), n)[:10]:
            a, b = _clamp(lo, hi, n)
            np.testing.assert_array_equal(ds.read_window(["h"], lo, hi)["h"], raw_whole["h"][a:b])


_EXPRS_ELEMENTWISE = ["f * 2 + h", "abs(f) - h / 3", "where(f > 0, f, h)", "-f + g"]


@settings(max_examples=_examples(50))
@given(
    seed=st.integers(0, 2**32 - 1),
    blocks_x10=st.integers(5, 120),
    cache_blocks=st.integers(2, 4),
    kind=st.sampled_from(["elementwise", "delta", "rolling_mean", "rolling_nanmean", "nested", "on_derived"]),
    elem=st.integers(0, len(_EXPRS_ELEMENTWISE) - 1),
    nwin=st.sampled_from([1, 2, 5, 50, 999, 1001, 200_000]),
)
def test_derived_column_window_equals_whole(seed, blocks_x10, cache_blocks, kind, elem, nwin):
    n = blocks_x10 * BLOCK // 10
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "w.parquet"
        _write(path, n, seed, 3000)
        ds = ParquetDataSource(path, block_rows=BLOCK, cache_blocks=cache_blocks)
        whole = {c: _oracle(path, c) for c in ("f", "g", "h")}
        wcols = {c: WindowedColumn(ds, c) for c in whole}
        rolling_n = min(nwin, 5 * n)
        expr = {
            "elementwise": _EXPRS_ELEMENTWISE[elem],
            "delta": "delta(f)",
            "rolling_mean": f"rolling_mean(f, {rolling_n})",
            "rolling_nanmean": f"rolling_nanmean(h, {rolling_n})",
            "nested": f"delta(rolling_mean(f, {rolling_n}))",
            "on_derived": "rolling_nanmean(inner, 7) + f",
        }[kind]
        cols: dict = dict(wcols)
        full_vars = dict(whole)
        if kind == "on_derived":
            inner = make_derived_column("delta(h) * 2", wcols, n, None)
            assert isinstance(inner, DerivedColumn)
            cols["inner"] = inner
            full_vars["inner"] = np.asarray(
                evaluate_expression("delta(h) * 2", LazyVariables({k: MaterializedColumn(v) for k, v in whole.items()}), None),
                dtype=np.float64,
            )
        col = make_derived_column(expr, cols, n, None)
        assert isinstance(col, DerivedColumn), expr
        expected = np.asarray(
            evaluate_expression(expr, LazyVariables({k: MaterializedColumn(v) for k, v in full_vars.items()}), None),
            dtype=np.float64,
        )
        assert expected.shape == (n,)
        for lo, hi in _ranges(np.random.default_rng(seed ^ 0x77), n):
            a, b = _clamp(lo, hi, n)
            np.testing.assert_allclose(
                col.window(lo, hi), expected[a:b], rtol=1e-12, equal_nan=True, err_msg=f"{expr} {(lo, hi)}"
            )


def test_make_derived_column_none_when_margin_unknown():
    """An expression whose margin is not known stays a full evaluation (returns None)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "w.parquet"
        _write(path, 3000, 1, 1000)
        ds = ParquetDataSource(path, block_rows=BLOCK)
        cols = {c: WindowedColumn(ds, c) for c in ("f", "g", "h")}
        # window width that is not a plain number -> no margin
        assert make_derived_column("rolling_mean(f, g)", cols, 3000, None) is None
        assert make_derived_column("f + unknown_name", cols, 3000, None) is None


@settings(max_examples=_examples(30))
@given(
    seed=st.integers(0, 2**32 - 1),
    factor=st.sampled_from([0.5, 1.0, 2.0, 2.5, 4.0]),
    dup_fraction=st.sampled_from([0.0, 0.5, 1.0]),
    run_len=st.sampled_from([10, 3000, 9000]),
    cache_blocks=st.integers(2, 4),
)
def test_time_index_window_and_value_at_equal_whole(seed, factor, dup_fraction, run_len, cache_blocks):
    rng = np.random.default_rng(seed)
    n = max(1, int(ANCHOR_ROWS * factor))
    steps = rng.choice([0.25, 0.5, 1.0, 3.0], size=n)
    steps[rng.random(n) < dup_fraction] = 0.0
    centre = int(rng.integers(1, max(2, n // ANCHOR_ROWS + 1))) * ANCHOR_ROWS if n > ANCHOR_ROWS else n // 2
    lo0 = max(1, centre - run_len // 2)
    steps[lo0 : lo0 + run_len] = 0.0  # a run of one value around an anchor boundary
    steps[0] = 0.0
    t = 1_000.0 + np.cumsum(steps)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.parquet"
        pq.write_table(pa.table({"time": t}), path, row_group_size=5000)
        ds = ParquetDataSource(path, block_rows=2048, cache_blocks=cache_blocks)
        oracle = to_numeric(pl.read_parquet(path).get_column("time").to_numpy())
        idx = TimeIndex.build(ds, "time", pin_rows=[3, n - 1])
        assert idx is not None
        for lo, hi in _ranges(np.random.default_rng(seed ^ 1), n):
            a, b = _clamp(lo, hi, n)
            np.testing.assert_array_equal(idx.window(lo, hi), oracle[a:b], err_msg=str((lo, hi)))
        rows = np.concatenate([
            rng.integers(0, n, size=30),
            [0, n - 1, ANCHOR_ROWS - 1, ANCHOR_ROWS, ANCHOR_ROWS + 1, 3, 2 * ANCHOR_ROWS],
        ])
        for r in rows:
            r = int(r)
            if 0 <= r < n:
                assert idx.value_at(r) == oracle[r], r
        assert idx.value_at(-1) == oracle[-1]  # negative index counts from the end
        for bad in (n, n + 5):
            try:
                idx.value_at(bad)
            except IndexError:
                pass
            else:  # pragma: no cover
                raise AssertionError(f"value_at({bad}) should raise IndexError")
