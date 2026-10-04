"""Abnormal-input tests for core/pyramid.py and core/downsample.py (LTTB)
(detailed_specification.md 5.4 / 13.5.1 / 28.2 / 40)."""
from __future__ import annotations

import datetime
import math

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.downsample import lttb
from parquet_analyzer.core.numeric import NonNumericColumnError
from parquet_analyzer.core.pyramid import ColumnPyramid, build_pyramid, pyramid_bounds, thin_column


def _write(path, row_group_size=7, **cols):
    pq.write_table(pa.table(cols), path, row_group_size=row_group_size)
    return path


@pytest.fixture
def path(tmp_path):
    t = np.arange(25.0)
    return _write(tmp_path / "a.parquet", t=t, v=t)


# -- build_pyramid -----------------------------------------------------------------------


@pytest.mark.parametrize("bucket, n_buckets", [(1, 25), (5, 5), (7, 4), (24, 2), (25, 1), (1000, 1)])
def test_pyramid_bucket_sizes(path, bucket, n_buckets):
    p = build_pyramid(path, ["v"], bucket)["v"]
    assert len(p) == n_buckets
    assert pyramid_bounds(p) == (0.0, 24.0)
    assert p.row_starts[0] == 0 and np.all(np.diff(p.row_starts) > 0)


@pytest.mark.parametrize("bucket", [0, -1])
def test_pyramid_rejects_a_non_positive_bucket(path, bucket):
    with pytest.raises(ValueError):
        build_pyramid(path, ["v"], bucket)


def test_pyramid_empty_file_has_no_buckets(tmp_path):
    p = build_pyramid(_write(tmp_path / "e.parquet", t=np.array([]), v=np.array([])), ["v"])["v"]
    assert len(p) == 0 and pyramid_bounds(p) is None


def test_pyramid_single_row(tmp_path):
    p = build_pyramid(_write(tmp_path / "o.parquet", t=np.array([3.0]), v=np.array([4.0])), ["v"])["v"]
    assert pyramid_bounds(p) == (4.0, 4.0)


def test_pyramid_unknown_column_and_missing_file(path, tmp_path):
    with pytest.raises(KeyError):
        build_pyramid(path, ["nope"])
    with pytest.raises(FileNotFoundError):
        build_pyramid(tmp_path / "gone.parquet", ["v"])
    assert build_pyramid(path, []) == {}


def test_pyramid_ignores_nan_and_inf_and_marks_all_nan_buckets(tmp_path):
    v = np.arange(20.0)
    v[3] = np.nan
    v[8] = np.inf
    v[10:15] = np.nan  # a whole bucket without any finite value
    p = build_pyramid(_write(tmp_path / "n.parquet", t=np.arange(20.0), v=v), ["v"], 5)["v"]
    assert np.isnan(p.mins[2]) and np.isnan(p.maxs[2])
    assert p.mins[0] == 0.0 and p.maxs[0] == 4.0  # NaN skipped
    assert p.maxs[1] == 9.0  # Inf skipped
    assert pyramid_bounds(p) == (0.0, 19.0)


def test_pyramid_all_nan_column_has_no_bounds(tmp_path):
    p = build_pyramid(_write(tmp_path / "n.parquet", t=np.arange(9.0), v=np.full(9, np.nan)), ["v"], 4)["v"]
    assert len(p) == 3 and pyramid_bounds(p) is None


def test_pyramid_datetime_and_nullable_columns(tmp_path):
    d = pa.array(np.arange(25).astype("datetime64[ms]"))
    n = pa.array([None if i % 3 == 0 else i for i in range(25)], type=pa.int32())
    path = _write(tmp_path / "d.parquet", t=np.arange(25.0), d=d, n=n)
    pyr = build_pyramid(path, ["d", "n"], 10)
    assert pyramid_bounds(pyr["d"]) == (0.0, 0.024)
    assert pyramid_bounds(pyr["n"]) == (1.0, 23.0)


def test_pyramid_null_timestamps_are_not_finite_values(tmp_path):
    """Regression: NaT was ~-9.2e15 s and became the bucket minimum."""
    d0 = datetime.datetime(2026, 1, 1)
    ts = pa.array([d0, None, d0 + datetime.timedelta(seconds=2)] * 3, type=pa.timestamp("ms"))
    p = build_pyramid(_write(tmp_path / "t.parquet", t=np.arange(9.0), x=ts), ["x"], 4)["x"]
    lo, hi = pyramid_bounds(p)
    assert lo == 1767225600.0 and hi == 1767225602.0


@pytest.mark.parametrize("arr", [pa.array(["a"] * 9), pa.array([{"a": 1}] * 9), pa.array([[1]] * 9)], ids=["str", "struct", "list"])
def test_pyramid_and_thin_raise_the_domain_error_for_non_numeric(tmp_path, arr):
    """These run in a background job; the failure must be a recognisable error (28.2), not a
    numpy/TypeError from deep inside."""
    path = _write(tmp_path / "s.parquet", t=np.arange(9.0), x=arr)
    with pytest.raises(NonNumericColumnError):
        build_pyramid(path, ["x"])
    with pytest.raises(NonNumericColumnError):
        thin_column(path, "x")


def test_pyramid_bounds_of_hand_built_pyramids():
    empty = ColumnPyramid(np.array([], np.int64), np.array([]), np.array([]))
    assert pyramid_bounds(empty) is None
    mixed = ColumnPyramid(np.array([0, 5]), np.array([np.nan, 1.0]), np.array([np.nan, 2.0]))
    assert pyramid_bounds(mixed) == (1.0, 2.0)
    inf = ColumnPyramid(np.array([0]), np.array([-np.inf]), np.array([np.inf]))
    assert pyramid_bounds(inf) is None


# -- thin_column -------------------------------------------------------------------------


@pytest.mark.parametrize("target", [0, -5, 1])
def test_thin_column_degenerate_targets_still_return_a_start_point(path, target):
    idx, vals = thin_column(path, "v", target)
    assert idx.tolist()[0] == 0 and len(idx) == len(vals) >= 1


@pytest.mark.parametrize("target", [2, 10, 25, 1000])
def test_thin_column_is_evenly_strided_across_row_groups(path, target):
    idx, vals = thin_column(path, "v", target)
    stride = max(1, 25 // target)
    assert idx.tolist() == list(range(0, 25, stride))
    assert vals.tolist() == [float(i) for i in idx]  # values match their row index


def test_thin_column_tiny_and_empty_files(tmp_path):
    idx, vals = thin_column(_write(tmp_path / "e.parquet", t=np.array([]), v=np.array([])), "v")
    assert len(idx) == 0 and len(vals) == 0 and idx.dtype == np.int64 and vals.dtype == np.float64
    idx, vals = thin_column(_write(tmp_path / "o.parquet", t=np.array([1.0]), v=np.array([2.0])), "v")
    assert idx.tolist() == [0] and vals.tolist() == [2.0]


def test_thin_column_keeps_nan_and_inf_as_values(tmp_path):
    v = np.arange(10.0)
    v[3], v[8] = np.nan, np.inf
    idx, vals = thin_column(_write(tmp_path / "n.parquet", t=np.arange(10.0), v=v), "v", 100)
    assert math.isnan(vals[3]) and math.isinf(vals[8])  # the caller (navigator) filters non-finite


def test_thin_column_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        thin_column(tmp_path / "gone.parquet", "v")


# -- lttb --------------------------------------------------------------------------------

X = np.arange(20.0)
Y = np.sin(X)


@pytest.mark.parametrize("n_out", [3, 4, 5, 19])
def test_lttb_output_size_and_endpoints(n_out):
    sx, sy = lttb(X, Y, n_out)
    assert len(sx) == len(sy) == n_out
    assert sx[0] == X[0] and sx[-1] == X[-1]
    assert np.all(np.diff(sx) > 0)  # still in x order
    assert set(sx.tolist()) <= set(X.tolist())


@pytest.mark.parametrize("n_out", [20, 21, 1000])
def test_lttb_returns_input_when_nothing_to_reduce(n_out):
    sx, sy = lttb(X, Y, n_out)
    assert sx is X and sy is Y


@pytest.mark.parametrize("n", [0, 1, 2])
def test_lttb_tiny_inputs(n):
    sx, sy = lttb(X[:n], Y[:n], 5)
    assert len(sx) == len(sy) == n


def test_lttb_keeps_a_single_spike():
    y = np.zeros(1000)
    y[437] = 5.0
    sx, sy = lttb(np.arange(1000.0), y, 20)
    assert sy.max() == 5.0


def test_lttb_all_nan_y_does_not_crash_or_warn(recwarn):
    sx, sy = lttb(X, np.full(20, np.nan), 5)
    assert len(sx) == 5 and np.isfinite(sx).all() and np.isnan(sy).all()
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_lttb_single_finite_point_survives():
    y = np.full(20, np.nan)
    y[7] = 3.0
    _sx, sy = lttb(X, y, 5)
    assert 3.0 in sy.tolist()


def test_lttb_infinite_y_values_pass_through_untouched():
    y = Y.copy()
    y[3], y[10] = np.inf, -np.inf
    sx, sy = lttb(X, y, 6)
    assert len(sx) == 6 and np.isfinite(sx).all()  # X stays finite; Y inf is for the drawer to break the line


def test_lttb_nan_in_x_returns_requested_count():
    x = X.copy()
    x[5] = np.nan
    sx, sy = lttb(x, Y, 6)
    assert len(sx) == 6


def test_lttb_constant_x_and_integer_input():
    sx, sy = lttb(np.ones(20), Y, 5)
    assert len(sx) == 5 and (sx == 1).all()
    ix, iy = lttb(np.arange(20), np.arange(20), 5)
    assert ix.dtype.kind == "i" and len(ix) == 5


def test_lttb_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        lttb(X, Y[:10], 5)


@pytest.mark.parametrize("n_out", [-1, 0, 1, 2])
def test_lttb_n_out_below_three_is_treated_as_three(n_out):
    """data 29.5: a zero-width plot used to get every point back."""
    sx, sy = lttb(X, Y, n_out)
    assert len(sx) == len(sy) == 3
    assert sx[0] == X[0] and sx[-1] == X[-1]
    assert X[0] < sx[1] < X[-1]


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_lttb_inputs_of_three_points_or_fewer_are_unchanged(n):
    for n_out in (0, 2, 3):
        sx, sy = lttb(X[:n], Y[:n], n_out)
        assert sx.tolist() == X[:n].tolist() and sy.tolist() == Y[:n].tolist()
