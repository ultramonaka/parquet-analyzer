"""Property tests for LTTB (app 46.2, A1; core/downsample.py, spec 5.4 / data 29.5)."""
from __future__ import annotations

import os

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.downsample import lttb


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


def _canon(a: np.ndarray) -> np.ndarray:
    """Bit patterns with every NaN collapsed to one, so that 'same point' is a plain int compare
    (and -0.0 stays distinct from 0.0, which is fine: points are copied, never recomputed)."""
    a = np.where(np.isnan(a), np.nan, a).astype(np.float64)
    return a.view(np.int64)


def _is_subsequence_of_points(x, y, ox, oy) -> bool:
    """Every output (x, y) pair is an input pair, taken in input order (greedy match)."""
    ids = {}
    kx, ky = _canon(x), _canon(y)
    in_ids = [ids.setdefault((a, b), len(ids)) for a, b in zip(kx.tolist(), ky.tolist())]
    out_ids = [ids.get((a, b), -1) for a, b in zip(_canon(ox).tolist(), _canon(oy).tolist())]
    j = 0
    for o in out_ids:
        if o < 0:
            return False
        while j < len(in_ids) and in_ids[j] != o:
            j += 1
        if j == len(in_ids):
            return False
        j += 1
    return True


@settings(max_examples=_examples(200))
@given(
    seed=st.integers(0, 2**32 - 1),
    n=st.one_of(st.integers(0, 40), st.integers(0, 20_000)),
    n_out_offset=st.integers(-1_000_000, 10),  # n_out = clamp(n + offset, 0, ...) (below n, mostly)
    n_out_small=st.one_of(st.none(), st.integers(0, 12)),
    dup_fraction=st.sampled_from([0.0, 0.5, 1.0]),
    nonfinite=st.sampled_from(["none", "some_nan", "some_inf", "mixed", "all_nan", "all_inf"]),
)
def test_lttb_properties(seed, n, n_out_offset, n_out_small, dup_fraction, nonfinite):
    rng = np.random.default_rng(seed)
    steps = rng.random(n)
    steps[rng.random(n) < dup_fraction] = 0.0
    x = np.cumsum(steps)  # ascending, with duplicates
    y = rng.normal(0.0, 10.0, n)
    if nonfinite == "all_nan":
        y[:] = np.nan
    elif nonfinite == "all_inf":
        y[:] = np.inf
    elif nonfinite != "none":
        mask = rng.random(n) < 0.2
        bad = {"some_nan": [np.nan], "some_inf": [np.inf, -np.inf]}.get(nonfinite, [np.nan, np.inf, -np.inf])
        y[mask] = rng.choice(bad, int(mask.sum()))
    n_out = n_out_small if n_out_small is not None else max(0, n + n_out_offset)

    ox, oy = lttb(x, y, n_out)

    assert len(ox) == len(oy)
    if max(n_out, 3) >= n:
        assert np.array_equal(_canon(ox), _canon(x)) and np.array_equal(_canon(oy), _canon(y))
        return
    assert len(ox) <= max(n_out, 3)
    assert len(ox) == max(n_out, 3)  # it fills the budget when it downsamples
    assert ox[0] == x[0] and ox[-1] == x[-1]
    assert _canon(oy)[0] == _canon(y)[0] and _canon(oy)[-1] == _canon(y)[-1]
    assert _is_subsequence_of_points(x, y, ox, oy)
    assert np.all(np.diff(ox) >= 0)  # ascending in, ascending out
