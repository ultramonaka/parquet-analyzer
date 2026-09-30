"""Fast correctness check for generate_feature_test_parquet.py: the documented
expression identities in its docstring must actually hold on the generated data."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer.core.expression import evaluate_expression  # noqa: E402


def test_documented_expression_identities_hold():
    table = build_table(duration_s=120, rate_hz=100, gap_s=30)
    cols = {name: table[name].to_numpy() for name in table.column_names if name != "time"}

    np.testing.assert_allclose(evaluate_expression("sine**2 + cosine**2", cols), 1.0)
    np.testing.assert_allclose(evaluate_expression("offset_sine - sine", cols), 10.0)
    assert np.isnan(cols["with_nan"]).any() and not np.isnan(cols["with_nan"]).all()
    assert set(np.unique(cols["step"])) == {0.0, 1.0}


def test_time_axis_is_monotonic_with_one_gap():
    table = build_table(duration_s=120, rate_hz=100, gap_s=30)
    t_ms = table["time"].to_numpy().astype("int64")
    diffs = np.diff(t_ms)
    assert table.num_rows == 12_000
    assert (diffs > 0).all()
    assert (diffs > 1000).sum() == 1  # exactly one gap
    assert diffs.max() == 30_010  # 30 s gap + one 10 ms sample interval
