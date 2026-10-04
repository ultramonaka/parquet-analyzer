"""Abnormal input to lookup tables (expressions.ja.md 6.1 / 6.2): calling, loading, metadata."""

from __future__ import annotations

import json

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, evaluate_expression
from parquet_analyzer.core.functions import FunctionRegistry
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import maps as mio
from parquet_analyzer.io.maps import MapError

NAN = float("nan")
INF = float("inf")


def curve(interp="linear", extrap="clamp"):
    return LookupTable("c", "curve", [0, 1, 2, 4], [0, 10, 0, 20], None, interp, extrap)


def mp(interp="linear", extrap="clamp"):
    x, y = [0.0, 1.0, 3.0], [0.0, 2.0]
    return LookupTable("m", "map", x, y, [[10 * xi + yi for xi in x] for yi in y], interp, extrap)


@pytest.fixture
def reg():
    return FunctionRegistry(maps={"c": curve(), "m": mp()})


def ev(expr, reg, v=None):
    return evaluate_expression(expr, v or {"a": np.array([0.5, 1.5, 3.0]), "e": np.array([])}, reg)


# -- calling a table ----------------------------------------------------------------------------


@pytest.mark.parametrize("interp", ["linear", "nearest", "previous", "spline"])
@pytest.mark.parametrize("extrap", ["clamp", "zero", "linear", "nan"])
def test_curve_never_raises_on_nan_inf_empty_scalar(interp, extrap):
    c = curve(interp, extrap)
    r = c(np.array([NAN, INF, -INF, 0.0, 1e308, -1e308]))
    assert r.shape == (6,) and np.isnan(r[0])
    assert c(np.array([])).shape == (0,)
    assert np.ndim(c(NAN)) == 0 and np.isnan(c(NAN))
    assert np.ndim(c(1.0)) == 0


@pytest.mark.parametrize("interp", ["linear", "nearest", "previous"])
@pytest.mark.parametrize("extrap", ["clamp", "zero", "linear", "nan"])
def test_map_never_raises_on_nan_inf_empty_scalar(interp, extrap):
    m = mp(interp, extrap)
    r = m(np.array([NAN, INF, -INF, 1.0]), np.array([1.0, NAN, 1.0, INF]))
    assert r.shape == (4,) and np.isnan(r[0]) and np.isnan(r[1])
    assert m(np.array([]), np.array([])).shape == (0,)
    assert m(np.array([]), 1.0).shape == (0,)  # scalar broadcast against an empty array
    assert np.ndim(m(1.0, 1.0)) == 0
    assert np.isnan(m(NAN, 1.0))


def test_map_mixed_scalar_and_array_and_shapes():
    m = mp()
    np.testing.assert_allclose(m(np.array([0.0, 1.0]), 2.0), [2, 12])
    np.testing.assert_allclose(m(1.0, np.array([0.0, 2.0])), [10, 12])
    assert m(np.ones((2, 3)), np.ones((2, 3))).shape == (2, 3)
    assert m(np.ones((2, 1)), np.ones((1, 3))).shape == (2, 3)  # numpy broadcasting


def test_map_incompatible_shapes_raise_value_error_which_expressions_wrap(reg):
    with pytest.raises(ValueError):
        mp()(np.ones(3), np.ones(2))
    v = {"a": np.ones(3), "b": np.ones(2)}
    with pytest.raises(ExpressionError, match="m"):
        ev("m(a, b)", reg, v)  # used to leak a raw broadcast ValueError


def test_operator_length_mismatch_is_not_a_raw_value_error(reg):
    # a + b with different lengths: numpy ValueError from a BinOp (not a function call)
    v = {"a": np.ones(3), "b": np.ones(2)}
    with pytest.raises(ExpressionError):
        ev("a + b", reg, v)


@pytest.mark.parametrize("args", [(), (1.0, 2.0)])
def test_curve_wrong_arity_direct_call_is_type_error(args):
    with pytest.raises(TypeError):
        curve()(*args)


@pytest.mark.parametrize(
    "expr",
    ["c()", "c(a, a)", "c(x=a)", "c(a, x=a)", "c(*a)", "c(**a)", "m(a)", "m()", "m(a, a, a)", "m(a, y=a)"],
)
def test_wrong_argument_count_and_keywords_are_expression_errors(expr, reg):
    with pytest.raises(ExpressionError):
        ev(expr, reg)


def test_map_results_in_expressions_are_arrays_of_the_right_length(reg):
    r = ev("c(a) + m(a, a * 0)", reg)
    assert r.shape == (3,)
    assert ev("c(2)", reg) == 0.0  # scalar in, scalar out


def test_nan_and_inf_pass_through_expressions(reg):
    r = ev("c(a / 0 - a / 0)", reg)  # NaN
    assert np.all(np.isnan(r))
    r = ev("c(a / 0)", reg)  # inf: clamp gives the edge value
    assert np.all(r == 20.0)


# -- construction -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(kind="other", x=[0, 1], y=[0, 1]),
        dict(kind="curve", x=[0, 1, 2], y=[0, 1]),
        dict(kind="curve", x=[0, 1], y=[0, 1], interpolation="cubic"),
        dict(kind="curve", x=[0, 1], y=[0, 1], extrapolation="wrap"),
        dict(kind="map", x=[0, 1], y=[0, 1], z=[[0, 1, 2], [1, 2, 3]]),
        dict(kind="map", x=[0, 1], y=[0, 1], z=[[0, 1], [1, 2]], interpolation="spline"),
    ],
)
def test_bad_table_construction_is_value_error(kwargs):
    with pytest.raises(ValueError):
        LookupTable("t", **kwargs)


# -- CSV parsing: odd content ---------------------------------------------------------------------


def _csv(tmp_path, text, name="t.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "text",
    [
        "0,0\n1,nan\n",
        "0,0\n1,inf\n",
        "0,0\n1,-inf\n",
        "0,0\n1,1e999\n",
        "0,0\n1e999,1\n",
        "0,0\n1,\n",
        "0,0\n1\n",
        "0,0\n1,2,3\n",
        "0,0\n0,1\n",  # duplicate x
        "0,0\n-1,1\n",  # descending
        "a,b\n",
        "a,b\n0,0\n",  # header + 1 point
        "0,0\n1,0x10\n",
        "0,0\n1,1;2\n",
        "\n\n\n",
        "",
        ",,\n,,\n",
    ],
)
def test_odd_csv_content_is_map_error(tmp_path, text):
    with pytest.raises(MapError):
        mio.parse_table_csv(_csv(tmp_path, text), "t", "auto")


@pytest.mark.parametrize(
    "text",
    [
        ",0,1\n0,1,2\n",  # one Y row
        ",0\n0,1\n1,2\n",  # one X value
        ",0,1\n0,1,2\n1,3\n",  # ragged row
        ",0,1\n0,1,nan\n1,3,4\n",
        ",0,1\n0,1,2\n0,3,4\n",  # duplicate y
        ",1,0\n0,1,2\n1,3,4\n",  # descending x
        ",0,1\n0,1,2\n1,3,x\n",
        ",0,1\n0,1,2\n1,3,1e999\n",
    ],
)
def test_odd_map_csv_content_is_map_error(tmp_path, text):
    with pytest.raises(MapError):
        mio.parse_table_csv(_csv(tmp_path, text), "t", "map")


def test_undecodable_bytes_are_map_error(tmp_path):
    p = tmp_path / "t.csv"
    p.write_bytes(b"\x81\x20,\xff\n0,1\n")  # invalid in UTF-8 and in CP932
    with pytest.raises(MapError):
        mio.parse_table_csv(p, "t", "curve")


def test_huge_but_finite_values_load_and_evaluate_without_exception(tmp_path):
    p = _csv(tmp_path, "0,1e308\n1,-1e308\n2,1e308\n")
    t = mio.parse_table_csv(p, "t", "curve", "spline", "linear")
    r = t(np.array([0.5, 5.0, -5.0]))
    assert r.shape == (3,)  # may be inf/nan, never an exception


# -- stored metadata with wrong types --------------------------------------------------------------


def _import(tmp_path, name="cv"):
    mio.import_map(_csv(tmp_path, "0,0\n1,10\n2,0\n", name + ".csv"), name)


def _patch_meta(name, **changes):
    p = mio.maps_dir() / f"{name}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d.update(changes)
    p.write_text(json.dumps(d), encoding="utf-8")


@pytest.mark.parametrize(
    "changes",
    [
        dict(interpolation=["linear"]),  # unhashable
        dict(extrapolation={"a": 1}),
        dict(interpolation=5),
        dict(extrapolation=None),
        dict(kind=["curve"]),
        dict(kind=5),
        dict(kind="cube"),
        dict(interpolation="spline", kind="map"),
    ],
)
def test_bad_metadata_does_not_leak_raw_exceptions(isolated_config, tmp_path, changes):
    _import(tmp_path, "good")
    _import(tmp_path, "cv")
    _patch_meta("cv", **changes)
    tables, warnings = mio.load_all_maps()  # must neither raise nor lose the other maps
    assert set(tables) == {"good"} and len(warnings) == 1 and warnings[0].startswith("cv:")
    assert (mio.maps_dir() / "cv.csv").exists() and (mio.maps_dir() / "cv.json").exists()  # files kept


@pytest.mark.parametrize("axis_names", [5, ["x"], "xyz", {"x": 5, "y": None}])
def test_bad_axis_names_fall_back_to_defaults(isolated_config, tmp_path, axis_names):
    _import(tmp_path)
    _patch_meta("cv", axis_names=axis_names)
    assert mio.load_map("cv").axis_names == ("X", "Y", "Z")


@pytest.mark.parametrize("bad", ["[]", "5", '"x"', "null", "{", "", '{"kind": 1'])
def test_metadata_of_wrong_shape_is_skipped_with_a_warning(isolated_config, tmp_path, bad):
    _import(tmp_path, "good")
    _import(tmp_path, "cv")
    (mio.maps_dir() / "cv.json").write_text(bad, encoding="utf-8")
    tables, warnings = mio.load_all_maps()
    assert set(tables) == {"good"} and len(warnings) == 1
    assert [m.name for m in mio.list_maps()] == ["good"]


def test_non_string_source_path_does_not_break_reimport(isolated_config, tmp_path):
    _import(tmp_path)
    _patch_meta("cv", source_path=5, imported_at=["x"], edited_at=7)
    assert mio.load_map("cv").kind == "curve"
    with pytest.raises(MapError):
        mio.reimport_map("cv")  # source not a path -> MapError, not TypeError


def test_missing_csv_or_json_half_is_map_error(isolated_config, tmp_path):
    _import(tmp_path)
    (mio.maps_dir() / "cv.csv").unlink()
    with pytest.raises(MapError):
        mio.load_map("cv")
    tables, warnings = mio.load_all_maps()
    assert tables == {} and len(warnings) == 1
