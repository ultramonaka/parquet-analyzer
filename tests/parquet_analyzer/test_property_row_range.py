"""Property tests for ParquetDataSource.row_range_for_x (app 46.2, A1; core/data_source.py, data 13.5.1/29.5).

The returned row range covers every row whose time value lies in [x_lo, x_hi] (it works per row
group, so it may be wider); reversed / NaN bounds give (0, 0)."""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.data_source import ParquetDataSource


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


@settings(max_examples=_examples(30))
@given(
    seed=st.integers(0, 2**32 - 1),
    n=st.integers(1, 6000),
    row_group=st.integers(1, 7000),
    dup_fraction=st.sampled_from([0.0, 0.5, 1.0]),
    gappy=st.booleans(),
)
def test_row_range_covers_every_row_in_range(seed, n, row_group, dup_fraction, gappy):
    rng = np.random.default_rng(seed)
    steps = rng.choice([0.5, 1.0, 2.0], n)
    steps[rng.random(n) < dup_fraction] = 0.0
    if gappy:
        steps[rng.random(n) < 0.02] = 500.0  # big jumps: bounds that fall into a gap
    t = np.cumsum(steps) + 100.0
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.parquet"
        pq.write_table(pa.table({"time": t}), path, row_group_size=row_group)
        ds = ParquetDataSource(str(path))
        groups = ds.row_groups()
        group_bounds = [(t[o], t[o + k - 1]) for o, k in groups]  # (min, max) of each row group

        lo_t, hi_t = float(t[0]), float(t[-1])
        span = max(hi_t - lo_t, 1.0)
        values = list(rng.choice(t, 12)) + list(rng.uniform(lo_t - span / 10, hi_t + span / 10, 12))
        values += [lo_t - 1.0, hi_t + 1.0, lo_t, hi_t, math.nan, math.inf, -math.inf]
        for x_lo in values:
            for x_hi in values:
                s, e = ds.row_range_for_x("time", float(x_lo), float(x_hi))
                if not (x_lo <= x_hi):  # reversed or NaN bound
                    assert (s, e) == (0, 0), (x_lo, x_hi)
                    continue
                assert 0 <= s <= e <= n, (s, e, x_lo, x_hi)
                inside = np.flatnonzero((t >= x_lo) & (t <= x_hi))
                if len(inside):
                    assert s <= inside[0] and inside[-1] < e, (x_lo, x_hi, s, e, inside[0], inside[-1])
                overlap = any(mn <= x_hi and mx >= x_lo for mn, mx in group_bounds)
                if not overlap:
                    assert (s, e) == (0, 0), (x_lo, x_hi, s, e)
