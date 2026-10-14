"""Property tests for TimeIndex.searchsorted (app 46.2, A1; core/time_index.py, data 36.5/36.6).

The index answers exactly what np.searchsorted over the whole axis answers, also across long
runs of duplicates that straddle the anchor boundaries."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.time_index import ANCHOR_ROWS, TimeIndex


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


def _axis(seed: int, factor: float, dup_fraction: float, n_runs: int, run_len: int) -> np.ndarray:
    """Ascending finite float64: random small steps (a share of them 0), plus `n_runs` long runs
    of one repeated value that are placed around anchor boundaries."""
    rng = np.random.default_rng(seed)
    n = max(1, int(ANCHOR_ROWS * factor))
    steps = rng.choice([0.25, 0.5, 1.0, 3.0], size=n)
    steps[rng.random(n) < dup_fraction] = 0.0
    for r in range(n_runs):
        centre = (rng.integers(1, max(2, n // ANCHOR_ROWS + 1)) * ANCHOR_ROWS) if n > ANCHOR_ROWS else n // 2
        centre = int(centre) + int(rng.integers(-run_len // 2, run_len // 2 + 1)) if r % 2 else int(centre)
        lo = max(1, centre - run_len // 2)
        steps[lo : lo + run_len] = 0.0
    steps[0] = 0.0
    return 1_000.0 + np.cumsum(steps)


def _probes(t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    anchors = t[::ANCHOR_ROWS]
    parts = [
        rng.choice(t, 40),  # exact hits (incl. duplicates)
        rng.uniform(t[0], t[-1], 40) if t[0] < t[-1] else np.array([t[0]]),  # between values
        np.array([t[0], t[-1], t[0] - 1.0, t[-1] + 1.0, np.inf, -np.inf, np.nan]),
        anchors[:20], np.nextafter(anchors[:20], -np.inf), np.nextafter(anchors[:20], np.inf),
    ]
    return np.concatenate(parts)


@settings(max_examples=_examples(30))
@given(
    seed=st.integers(0, 2**32 - 1),
    factor=st.sampled_from([0.5, 1.0, 1.0001, 2.0, 2.5, 4.0]),
    dup_fraction=st.sampled_from([0.0, 0.3, 0.9, 1.0]),
    n_runs=st.integers(0, 3),
    run_len=st.sampled_from([10, 3000, 9000]),
    row_group=st.sampled_from([1000, 5000, 8192, 100_000]),
    pins=st.booleans(),
)
def test_searchsorted_equals_numpy(seed, factor, dup_fraction, n_runs, run_len, row_group, pins):
    t = _axis(seed, factor, dup_fraction, n_runs, run_len)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.parquet"
        pq.write_table(pa.table({"time": t}), path, row_group_size=row_group)
        ds = ParquetDataSource(str(path))
        pin_rows = [o for o, _n in ds.row_groups()] + [7, len(t) - 1] if pins else ()
        idx = TimeIndex.build(ds, "time", pin_rows=pin_rows)
        assert idx is not None
        assert len(idx) == len(t)
        probes = _probes(t, np.random.default_rng(seed ^ 0x5EED))
        for side in ("left", "right"):
            expected = np.searchsorted(t, probes, side=side)
            got = idx.searchsorted(probes, side)  # array form
            assert np.array_equal(got, expected), (side, probes[got != expected][:3])
            for v in probes[::7]:  # scalar form
                assert int(idx.searchsorted(float(v), side)) == int(np.searchsorted(t, v, side=side)), (float(v), side)


@settings(max_examples=_examples(10))
@given(seed=st.integers(0, 2**32 - 1), kind=st.sampled_from(["decreasing_step", "nan", "inf", "all_desc"]))
def test_build_returns_none_for_unusable_axis(seed, kind):
    rng = np.random.default_rng(seed)
    n = 3000
    t = np.cumsum(rng.random(n) + 0.1)
    pos = int(rng.integers(1, n - 1))
    if kind == "decreasing_step":
        t[pos] = t[pos - 1] - 1.0
    elif kind == "nan":
        t[pos] = np.nan
    elif kind == "inf":
        t[pos] = np.inf
    else:
        t = t[::-1].copy()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.parquet"
        pq.write_table(pa.table({"time": t}), path, row_group_size=1000)
        assert TimeIndex.build(ParquetDataSource(str(path)), "time") is None
