"""Grid aggregation for maps from XY measurements (analysis.ja.md 44.1 / 44.2 / 44.6)."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from parquet_analyzer.core import map_grid as mg
from parquet_analyzer.core.map_grid import GridAxisSpec, GridCancelled, MapGridError, grid_aggregate

NAN = float("nan")


# ---------------------------------------------------------------- grid axis


def test_step_grid_includes_hi_when_it_fits_exactly():
    p = GridAxisSpec("step", 0, 1000, step=500).points()
    np.testing.assert_array_equal(p, [0, 500, 1000])


def test_step_grid_hi_not_reached_stops_before_it():
    p = GridAxisSpec("step", 0, 1, step=0.3).points()
    np.testing.assert_allclose(p, [0, 0.3, 0.6, 0.9])


def test_step_grid_float_error_does_not_drop_hi():
    p = GridAxisSpec("step", 0, 1.0, step=0.1).points()  # 1.0/0.1 = 10.000000000000002 or 9.99..: still 11 points
    assert len(p) == 11 and p[-1] == pytest.approx(1.0, abs=1e-12)
    p = GridAxisSpec("step", 0.0, 0.7, step=0.1).points()  # 0.7/0.1 = 6.999999999999999
    assert len(p) == 8
    p = GridAxisSpec("step", 0.1, 0.7, step=0.2).points()
    assert len(p) == 4  # 0.1 0.3 0.5 0.7


def test_step_grid_is_computed_not_accumulated():
    p = GridAxisSpec("step", 0, 40, step=0.1).points()
    assert p[-1] == 40.0 and p[7] == 0.7 and p[3] == 0.3  # lo + k*step (rounded), no running sum


def test_count_grid_is_linspace():
    p = GridAxisSpec("count", -2, 2, count=5).points()
    np.testing.assert_array_equal(p, np.linspace(-2, 2, 5))


@pytest.mark.parametrize(
    "spec",
    [
        GridAxisSpec("count", 0, 1, count=1),  # < 2 points
        GridAxisSpec("count", 0, 1, count=501),  # > 500
        GridAxisSpec("count", 0, 1, count=None),
        GridAxisSpec("count", 0, 1, count=2.5),
        GridAxisSpec("step", 0, 1, step=2.0),  # only one point fits
        GridAxisSpec("step", 0, 1000, step=1.0),  # 1001 points
        GridAxisSpec("step", 0, 1, step=0.0),
        GridAxisSpec("step", 0, 1, step=-0.1),
        GridAxisSpec("step", 0, 1, step=NAN),
        GridAxisSpec("step", 0, 1, step=None),
        GridAxisSpec("count", 1, 1, count=3),  # lo >= hi
        GridAxisSpec("count", 2, 1, count=3),
        GridAxisSpec("count", NAN, 1, count=3),
        GridAxisSpec("count", 0, math.inf, count=3),
        GridAxisSpec("step", 0, 1e300, step=1e-300),  # absurd ratio
        GridAxisSpec("bogus", 0, 1, count=3),
    ],
)
def test_invalid_grid_specs_are_errors(spec):
    with pytest.raises(MapGridError):
        spec.points()


def test_500_points_is_the_limit():
    assert len(GridAxisSpec("count", 0, 1, count=500).points()) == 500
    assert len(GridAxisSpec("step", 0, 499, step=1).points()) == 500


@pytest.mark.parametrize(
    "lo, hi",
    [(0.3, 97.0), (-37.0, -3.0), (-5.0, 5.0), (1.0000001, 1.0000002), (1e-12, 3e-12), (1e9, 1.2e9), (0.0, 0.0), (7.5, 7.5),
     (0.1, 0.7), (-0.35, 0.35)],
)
def test_nice_axis_covers_data_with_round_bounds(lo, hi):
    nlo, nhi, step = mg.nice_axis(lo, hi)
    assert nlo <= lo and nhi >= hi and nlo < nhi
    mantissa = step / 10 ** math.floor(math.log10(step))
    assert any(math.isclose(mantissa, m, rel_tol=1e-9) for m in (1, 2, 5, 10))
    assert abs(nlo / step - round(nlo / step)) < 1e-6 and abs(nhi / step - round(nhi / step)) < 1e-6
    n = round((nhi - nlo) / step)
    assert 1 <= n <= 60  # about 20 divisions (a tiny relative width rounds to the same step)


def test_nice_axis_known_values():
    assert mg.nice_axis(0.3, 97.0) == (0.0, 100.0, 5.0)
    assert mg.nice_axis(-37.0, -3.0) == (-38.0, -2.0, 2.0)
    nlo, nhi, step = mg.nice_axis(0.0, 0.0)
    assert (nlo, nhi) == (-1.0, 1.0)
    nlo, nhi, step = mg.nice_axis(10.0, 10.0)
    assert nlo < 10 < nhi
    assert mg.nice_axis(5.0, 1.0) == mg.nice_axis(1.0, 5.0)  # swapped bounds
    with pytest.raises(MapGridError):
        mg.nice_axis(0.0, math.inf)


def test_nice_axis_feeds_a_valid_grid():
    nlo, nhi, step = mg.nice_axis(123.4, 4567.8)
    p = GridAxisSpec("step", nlo, nhi, step=step).points()
    assert 2 <= len(p) <= 500 and p[0] <= 123.4 and p[-1] >= 4567.8


# ---------------------------------------------------------------- assignment


def test_midpoint_goes_to_the_larger_grid_point():
    pts = np.array([0.0, 10.0, 20.0])
    idx, out = mg.assign_axis(np.array([4.999, 5.0, 5.001, 15.0, 14.999]), pts)
    np.testing.assert_array_equal(idx, [0, 1, 1, 2, 1])
    assert not out.any()


def test_half_spacing_outside_the_ends_is_in_and_beyond_is_outside():
    pts = np.array([0.0, 10.0, 20.0])
    v = np.array([-5.0, -4.99, -5.01, 24.99, 25.0, 25.01, 0.0, 20.0])
    idx, out = mg.assign_axis(v, pts)
    np.testing.assert_array_equal(out, [False, False, True, False, True, True, False, False])
    np.testing.assert_array_equal(idx[~out], [0, 0, 2, 0, 2])


def test_assignment_matches_nearest_by_brute_force():
    rng = np.random.default_rng(1)
    pts = np.linspace(0, 9, 10)
    v = rng.uniform(-0.5, 9.5 - 1e-9, 5000)
    idx, out = mg.assign_axis(v, pts)
    brute = np.abs(v[:, None] - pts[None, :]).argmin(axis=1)
    mism = idx != brute
    # only exact ties may differ (the brute force takes the smaller one)
    assert np.all(np.isclose(np.abs(v[mism] - pts[idx[mism]]), np.abs(v[mism] - pts[brute[mism]])))
    assert not out.any()


# ---------------------------------------------------------------- aggregation


def _chunks(arrs, n):
    """Split equally long arrays into `n` chunks."""
    parts = [np.array_split(a, n) for a in arrs]
    return [tuple(p[i] for p in parts) for i in range(n)]


XA = np.array([0.0, 10.0, 20.0])
YA = np.array([0.0, 1.0])


def _known():
    # cell (x=0,y=0): 1, 3, 5   cell (x=10,y=0): 10   cell (x=20,y=1): 7, 9 -- everything else empty
    x = np.array([0, 0, 0, 10, 20, 20.0]) + np.array([-1, 0, 1, 2, 0, -2.0])
    y = np.array([0, 0, 0, 0, 1, 1.0]) + np.array([0, 0.2, -0.2, 0, 0.1, -0.3])
    z = np.array([1, 3, 5, 10, 7, 9.0])
    return x, y, z


@pytest.mark.parametrize(
    "agg, expect",
    [("mean", {(0, 0): 3, (0, 1): 10, (1, 2): 8}), ("median", {(0, 0): 3, (0, 1): 10, (1, 2): 8}),
     ("max", {(0, 0): 5, (0, 1): 10, (1, 2): 9}), ("min", {(0, 0): 1, (0, 1): 10, (1, 2): 7})],
)
def test_aggregates_match_known_points(agg, expect):
    r = grid_aggregate([_known()], XA, YA, agg, 1, "none")
    assert r.value.shape == (2, 3)
    for (iy, ix), v in expect.items():
        assert r.value[iy, ix] == v
    assert int(np.isnan(r.value).sum()) == 6 - len(expect)
    assert r.count[0, 0] == 3 and r.count[1, 2] == 2 and r.n_valid == 6 and r.n_outside == 0
    assert r.measured_mask.sum() == 3 and not r.filled_mask.any()


def test_median_of_even_count_is_the_mean_of_the_middle_two():
    x = np.zeros(4)
    z = np.array([1.0, 100.0, 3.0, 4.0])
    r = grid_aggregate([(x, np.zeros(4), z)], XA, YA, "median", 1, "none")
    assert r.value[0, 0] == 3.5


def test_count_aggregate_is_the_count_everywhere_and_never_nan():
    r = grid_aggregate([_known()], XA, YA, "count", 2, "smooth")
    np.testing.assert_array_equal(r.value, [[3, 1, 0], [0, 0, 2]])
    assert not np.isnan(r.value).any() and not r.filled_mask.any()
    np.testing.assert_array_equal(r.measured_mask, [[True, False, False], [False, False, True]])  # >= min_count 2


def test_min_count_turns_thin_cells_into_no_data():
    r = grid_aggregate([_known()], XA, YA, "mean", 3, "none")
    assert r.measured_mask.sum() == 1 and r.value[0, 0] == 3
    assert np.isnan(r.value[1, 2]) and np.isnan(r.value[0, 1])
    assert r.count[1, 2] == 2  # the count itself is still reported


@pytest.mark.parametrize("agg", ["mean", "median", "max", "min", "count"])
def test_chunked_equals_single_pass(agg):
    rng = np.random.default_rng(5)
    x, y = rng.uniform(-3, 23, 4000), rng.uniform(-0.4, 1.4, 4000)
    z = rng.normal(size=4000)
    one = grid_aggregate([(x, y, z)], XA, YA, agg, 1, "smooth")
    many = grid_aggregate(_chunks((x, y, z), 17), XA, YA, agg, 1, "smooth")
    np.testing.assert_allclose(many.value, one.value, rtol=1e-12, atol=1e-12)
    np.testing.assert_array_equal(many.count, one.count)
    assert (many.n_valid, many.n_outside) == (one.n_valid, one.n_outside)


def test_non_finite_rows_are_excluded_and_counted_and_outside_counted():
    x = np.array([0, 0, 0, np.inf, 0, 100.0, -100.0])
    y = np.array([0, 0, np.nan, 0, 0, 0, 0.0])
    z = np.array([2, 4, 6, 8, np.nan, 10, 12.0])
    r = grid_aggregate([(x, y, z)], XA, YA, "mean", 1, "none")
    assert r.n_excluded_nonfinite == 3  # nan y, inf x, nan z
    assert r.n_outside == 2 and r.n_valid == 4
    assert r.value[0, 0] == 3 and r.count.sum() == 2


def test_median_beyond_the_limit_is_refused():
    x = np.zeros(10)
    with pytest.raises(MapGridError, match="median"):
        grid_aggregate([(x, x, x)], XA, YA, "median", median_limit=5)
    assert mg.MAP_MEDIAN_MAX_POINTS == 20_000_000


def test_scan_bounds_counts_and_extremes():
    chunks = [(np.array([1.0, np.nan, 3.0]), np.array([5.0, 6.0, 7.0])), (np.array([-2.0]), np.array([np.inf]))]
    s = mg.scan_bounds(chunks)
    assert (s.n_valid, s.n_excluded) == (2, 2)
    assert s.lo == [1.0, 5.0] and s.hi == [3.0, 7.0]
    assert mg.scan_bounds([]).n_valid == 0


def test_curve_aggregate():
    x = np.array([0.1, -0.2, 10.4, 19.0, 19.5])
    v = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    r = grid_aggregate([(x, v)], XA, None, "mean", 1, "smooth")
    assert r.is_curve and r.value.shape == (3,)
    np.testing.assert_allclose(r.value, [3, 6, 9])
    r = grid_aggregate([(x[:2], v[:2])], XA, None, "mean", 1, "smooth")
    np.testing.assert_allclose(r.value, [3, 3, 3])  # constant beyond the ends
    assert r.filled_mask.tolist() == [False, True, True]


def test_cancel_during_aggregation():
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 2

    chunks = _chunks(_known(), 6)
    with pytest.raises(GridCancelled):
        grid_aggregate(chunks, XA, YA, "mean", 1, "none", cancelled=cancelled)


# ---------------------------------------------------------------- filling


def _plane(ny=20, nx=30, a=0.5, b=-0.25):
    iy, ix = np.mgrid[0:ny, 0:nx]
    return a * ix + b * iy + 3.0


def test_smooth_keeps_measured_cells_and_recovers_a_plane_inside_a_hole():
    z = _plane()
    measured = np.ones(z.shape, dtype=bool)
    measured[5:12, 8:20] = False
    measured[15, 3] = False
    v = np.where(measured, z, np.nan)
    out, filled = mg.fill_empty(v, measured, "smooth")
    np.testing.assert_array_equal(out[measured], z[measured])  # untouched
    np.testing.assert_array_equal(filled, ~measured)
    np.testing.assert_allclose(out, z, atol=1e-6)  # a linear surface is harmonic: exact back


def test_smooth_extends_beyond_the_measured_region_with_zero_gradient_at_the_border():
    v = np.full((12, 12), np.nan)
    measured = np.zeros((12, 12), bool)
    measured[4:8, 4:8] = True
    v[measured] = 5.0
    out, filled = mg.fill_empty(v, measured, "smooth")
    assert np.isfinite(out).all() and filled.sum() == 144 - 16
    np.testing.assert_allclose(out, 5.0, atol=1e-6)  # constant data: constant everywhere (Neumann border)
    v2 = np.full((12, 12), np.nan)
    v2[0, 0], v2[0, 11] = 0.0, 10.0
    m2 = np.zeros((12, 12), bool)
    m2[0, 0] = m2[0, 11] = True
    out2, _ = mg.fill_empty(v2, m2, "smooth")
    assert np.all((out2 >= -1e-9) & (out2 <= 10 + 1e-9))  # maximum principle: never beyond the data
    assert out2[0, 1] < out2[0, 10] and out2[5, 1] < out2[5, 10]  # still a smooth ramp from 0 towards 10


def test_smooth_residual_reaches_tolerance():
    rng = np.random.default_rng(2)
    v = np.full((40, 40), np.nan)
    measured = rng.random((40, 40)) < 0.15
    v[measured] = rng.normal(size=measured.sum())
    out, filled = mg.fill_empty(v, measured, "smooth")
    # discrete Laplace equation holds at every filled cell (zero-gradient border)
    lap = np.zeros_like(out)
    mg._laplacian(out, lap)
    assert np.abs(lap[filled]).max() < 1e-5


def test_nearest_fill_known_and_tie_goes_to_smaller_index():
    v = np.full((3, 5), np.nan)
    measured = np.zeros((3, 5), bool)
    measured[1, 0], measured[1, 4] = True, True
    v[1, 0], v[1, 4] = 1.0, 9.0
    out, filled = mg.fill_empty(v, measured, "nearest")
    assert out[1, 1] == 1.0 and out[1, 3] == 9.0
    assert out[1, 2] == 1.0  # equidistant: the smaller flat index (the left one)
    assert out[0, 2] == 1.0 and out[2, 2] == 1.0  # the same tie on the other rows
    assert out[0, 3] == 9.0
    assert filled.sum() == 13 and out[1, 0] == 1.0


def test_nearest_fill_blockwise_equals_unblocked(monkeypatch):
    rng = np.random.default_rng(4)
    v = np.full((30, 40), np.nan)
    measured = rng.random((30, 40)) < 0.05
    v[measured] = rng.normal(size=measured.sum())
    ref, _ = mg.fill_empty(v, measured, "nearest")
    monkeypatch.setattr(mg, "_NEAREST_BLOCK_ELEMENTS", 50)  # forces many tiny blocks
    small, _ = mg.fill_empty(v, measured, "nearest")
    np.testing.assert_array_equal(small, ref)
    # brute-force check of the value picked
    my, mx = np.nonzero(measured)
    for (iy, ix) in [(0, 0), (15, 20), (29, 39), (7, 33)]:
        if measured[iy, ix]:
            continue
        d = (my - iy) ** 2 + (mx - ix) ** 2
        assert ref[iy, ix] == v[my[np.argmin(d)], mx[np.argmin(d)]]


def test_fill_none_and_zero_measured_cells():
    v = np.full((4, 4), np.nan)
    measured = np.zeros((4, 4), bool)
    out, filled = mg.fill_empty(v, measured, "smooth")
    assert np.isnan(out).all() and not filled.any()
    measured[1, 1] = True
    v[1, 1] = 2.0
    out, filled = mg.fill_empty(v, measured, "none")
    assert np.isnan(out).sum() == 15 and not filled.any()
    r = grid_aggregate([(np.array([5.0]), np.array([5.0]), np.array([1.0]))], XA, YA, "mean", 1, "smooth")
    assert r.no_data and np.isnan(r.value).all() and r.n_outside == 1 and r.n_filled == 0


def test_grid_aggregate_result_with_fill_marks_filled_cells():
    r = grid_aggregate([_known()], XA, YA, "mean", 1, "smooth")
    assert not np.isnan(r.value).any()
    np.testing.assert_array_equal(r.filled_mask, ~r.measured_mask)
    assert r.n_measured == 3 and r.n_filled == 3 and r.n_empty == 0
    assert r.value[0, 0] == 3 and r.value[1, 2] == 8  # measured values are not smoothed
    r = grid_aggregate([_known()], XA, YA, "mean", 1, "nearest")
    assert not np.isnan(r.value).any() and r.fill == "nearest"


def test_cancel_during_smooth_iterations():
    v = np.full((200, 200), np.nan)
    measured = np.zeros((200, 200), bool)
    measured[0, 0] = measured[199, 199] = True
    v[0, 0], v[199, 199] = 0.0, 1.0
    calls = []

    def cancelled():
        calls.append(1)
        return True

    with pytest.raises(GridCancelled):
        mg.fill_empty(v, measured, "smooth", cancelled)
    assert len(calls) == 1  # polled every 100 iterations: the first poll (iteration 100) stopped it


def test_smooth_fill_500x500_sparse_is_fast():
    rng = np.random.default_rng(0)
    ny = nx = 500
    measured = rng.random((ny, nx)) < 0.02
    iy, ix = np.mgrid[0:ny, 0:nx]
    v = np.where(measured, np.sin(ix / 40.0) + np.cos(iy / 70.0), np.nan)
    t0 = time.perf_counter()
    out, filled = mg.fill_empty(v, measured, "smooth")
    dt = time.perf_counter() - t0
    print(f"smooth fill 500x500 (2% measured): {dt:.2f}s")
    assert np.isfinite(out).all() and dt < 20  # "a few seconds" on a laptop; generous for slow machines


# -- review follow-up (44.1) ------------------------------------------------------------


def test_errors_carry_a_code_and_arguments():
    cases = [
        (GridAxisSpec("step", 1, 1, step=1), "lo_ge_hi", {}),
        (GridAxisSpec("step", 0, float("inf"), step=1), "non_finite", {}),
        (GridAxisSpec("step", 0, 1, step=-1), "step_not_positive", {}),
        (GridAxisSpec("step", 0, 1, step=2), "too_few", {}),
        (GridAxisSpec("step", 0, 1, step=1e-5), "too_many", {"limit": 500}),
        (GridAxisSpec("count", 0, 1, count=1), "too_few", {}),
        (GridAxisSpec("count", 0, 1, count=501), "too_many", {"limit": 500}),
        (GridAxisSpec("count", 0, 1, count=2.5), "not_integer", {}),
        (GridAxisSpec("bogus", 0, 1), "unknown_mode", {"mode": "bogus"}),
    ]
    for spec, code, kw in cases:
        with pytest.raises(MapGridError) as ei:
            spec.points()
        assert ei.value.code == code and ei.value.kwargs == kw, (spec, ei.value.code)
        assert str(ei.value) == code  # no display text in core


def test_grid_points_have_no_float_noise():
    p = GridAxisSpec("step", -1, 1, step=0.1).points()
    assert len(p) == 21
    assert p[6] == -0.4 and p[3] == -0.7 and p[13] == 0.3 and p[-1] == 1.0
    assert all(repr(float(v)) == repr(round(float(v), 10)) for v in p)
    c = GridAxisSpec("count", -1, 1, count=21).points()
    np.testing.assert_array_equal(c, p)
    lo, hi, step = mg.nice_axis(-0.37, 0.93)
    auto = GridAxisSpec("step", lo, hi, step=step).points()
    assert all(repr(float(v)) == repr(round(float(v), 10)) for v in auto)


def test_median_limit_error_has_a_code():
    x = np.arange(10.0)
    with pytest.raises(MapGridError) as ei:
        grid_aggregate([(x, x)], np.arange(10.0), None, agg="median", median_limit=3)
    assert ei.value.code == "median_too_many" and ei.value.kwargs == {"limit": 3}


# ---------------------------------------------------------------- keep sparse cells empty (44.13)


def _sparse_chunk():
    """3x3 grid (axis 0, 1, 2): cell (0,0) has 3 points, (1,1) has 1 point (sparse for min 2), (2,2) has 2."""
    x = np.array([0, 0, 0, 1, 2, 2], dtype=float)
    y = np.array([0, 0, 0, 1, 2, 2], dtype=float)
    z = np.array([1, 1, 1, 50, 5, 5], dtype=float)
    return x, y, z


AX3 = np.array([0.0, 1.0, 2.0])


@pytest.mark.parametrize("fill", ["smooth", "nearest"])
def test_keep_sparse_empty_leaves_sparse_cells_nan_and_unfilled(fill):
    r = grid_aggregate([_sparse_chunk()], AX3, AX3, "mean", 2, fill, keep_sparse_empty=True)
    assert r.sparse_mask[1, 1] and r.sparse_mask.sum() == 1  # only count 1 <= n < 2
    assert np.isnan(r.value[1, 1]) and not r.filled_mask[1, 1] and not r.measured_mask[1, 1]
    # zero-count cells are still filled
    assert not np.isnan(r.value[0, 2]) and r.filled_mask[0, 2]
    assert r.keep_sparse_empty


@pytest.mark.parametrize("fill", ["smooth", "nearest", "none"])
def test_keep_sparse_empty_off_is_unchanged(fill):
    base = grid_aggregate([_sparse_chunk()], AX3, AX3, "mean", 2, fill)
    off = grid_aggregate([_sparse_chunk()], AX3, AX3, "mean", 2, fill, keep_sparse_empty=False)
    np.testing.assert_array_equal(base.value, off.value)
    np.testing.assert_array_equal(base.filled_mask, off.filled_mask)
    assert base.sparse_mask[1, 1]  # the mask is reported either way
    if fill != "none":
        assert base.filled_mask[1, 1] and not np.isnan(base.value[1, 1])  # filled as before


def test_keep_sparse_empty_no_effect_for_fill_none_or_count():
    a = grid_aggregate([_sparse_chunk()], AX3, AX3, "mean", 2, "none", keep_sparse_empty=True)
    b = grid_aggregate([_sparse_chunk()], AX3, AX3, "mean", 2, "none")
    np.testing.assert_array_equal(a.value, b.value)
    assert not a.keep_sparse_empty
    c = grid_aggregate([_sparse_chunk()], AX3, AX3, "count", 2, "smooth", keep_sparse_empty=True)
    d = grid_aggregate([_sparse_chunk()], AX3, AX3, "count", 2, "smooth")
    np.testing.assert_array_equal(c.value, d.value)
    assert not c.keep_sparse_empty


@pytest.mark.parametrize("fill", ["smooth", "nearest"])
def test_keep_sparse_empty_curve(fill):
    x = np.array([0, 0, 0, 1, 3, 3], dtype=float)
    v = np.array([1, 1, 1, 9, 4, 4], dtype=float)
    ax = np.array([0.0, 1.0, 2.0, 3.0])
    r = grid_aggregate([(x, v)], ax, None, "mean", 2, fill, keep_sparse_empty=True)
    assert r.sparse_mask.tolist() == [False, True, False, False]
    assert np.isnan(r.value[1]) and not r.filled_mask[1]
    assert r.filled_mask[2] and not np.isnan(r.value[2])  # count 0: filled between the measured ends
