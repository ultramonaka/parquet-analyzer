"""Lookup tables, CSV import and storage (detailed_specification.md 27.1-27.3 / 27.6)."""
from __future__ import annotations

import json
import time

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, evaluate_expression
from parquet_analyzer.core.functions import FunctionRegistry
from parquet_analyzer.core.maps import EXTRAPOLATIONS, INTERPOLATIONS, LookupTable
from parquet_analyzer.io import maps as mio
from parquet_analyzer.io.maps import MapError

NAN = float("nan")


def curve(interp="linear", extrap="clamp", x=(0, 1, 2, 4), y=(0, 10, 0, 20)):
    return LookupTable("c", "curve", x, y, None, interp, extrap)


def mp(interp="linear", extrap="clamp"):
    # z[iy, ix] = 10*x + y over x in {0,1,3}, y in {0,2}
    x, y = [0.0, 1.0, 3.0], [0.0, 2.0]
    z = [[10 * xi + yi for xi in x] for yi in y]
    return LookupTable("m", "map", x, y, z, interp, extrap)


# ---------------------------------------------------------------- interpolation


def test_curve_linear_known():
    c = curve()
    np.testing.assert_allclose(c(np.array([0.5, 1.5, 3.0])), [5, 5, 10])
    np.testing.assert_allclose(c(c.x), c.y)


def test_curve_nearest_and_previous():
    n, p = curve("nearest"), curve("previous")
    q = np.array([0.4, 0.6, 1.9, 3.5])
    np.testing.assert_allclose(n(q), [0, 10, 0, 20])
    np.testing.assert_allclose(p(q), [0, 0, 10, 0])
    np.testing.assert_allclose(p(np.array([1.0, 4.0])), [10, 20])  # on a knot -> that knot


def test_spline_through_cubic_with_zero_end_curvature():
    # natural spline reproduces knots exactly; for data with nonzero end curvature (cubic)
    # it differs from the cubic away from the knots, but is C2 and has M=0 at both ends.
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.5, 6.0])
    y = x**3 - 2 * x
    s = curve("spline", x=x, y=y)
    np.testing.assert_allclose(s(x), y, atol=1e-12)
    h = 1e-4

    def f(v):
        return float(s(np.array(v)))

    def d2(v):
        return (f(v + h) - 2 * f(v) + f(v - h)) / h**2

    assert abs(d2(x[0] + 2 * h)) < 1e-2  # ~0 at the left end
    assert abs(d2(x[-1] - 2 * h)) < 1e-2
    # continuity of 1st and 2nd derivative across an interior knot
    k = x[2]

    def d1(v):
        return (f(v + h) - f(v - h)) / (2 * h)

    assert d1(k - 1e-4) == pytest.approx(d1(k + 1e-4), abs=1e-2)
    assert d2(k - 1e-3) == pytest.approx(d2(k + 1e-3), abs=5e-2)


def test_spline_matches_linear_data_and_known_value():
    # collinear points -> straight line; 3 points with a known natural-spline midpoint
    s = curve("spline", x=[0, 1, 2], y=[0, 1, 2])
    np.testing.assert_allclose(s(np.array([0.25, 1.5])), [0.25, 1.5], atol=1e-12)
    s = curve("spline", x=[0, 1, 2], y=[0, 1, 0])
    # M = [0, -3, 0]; at 0.5: a=b=.5 -> .5*0+.5*1 + (b^3-b)(-3)/6 = .5 + (-.375)(-.5) = 0.6875
    assert float(s(np.array(0.5))) == pytest.approx(0.6875)


def test_spline_two_points_falls_back_to_linear_with_warning():
    s = curve("spline", x=[0, 2], y=[0, 4])
    assert s.warnings()
    assert float(s(np.array(1.0))) == pytest.approx(2.0)


def test_map_linear_nearest_previous_and_grid():
    m = mp()
    assert float(m(np.array(2.0), np.array(1.0))) == pytest.approx(10 * 2 + 1)  # bilinear of a plane is exact
    gx, gy = np.meshgrid(m.x, m.y)
    np.testing.assert_allclose(m(gx, gy), m.z)
    n = mp("nearest")
    assert float(n(np.array(2.4), np.array(0.9))) == pytest.approx(10 * 3 + 0)  # x->3 (|2.4-3|<|2.4-1|), y->0
    p = mp("previous")
    assert float(p(np.array(2.9), np.array(1.9))) == pytest.approx(10 * 1 + 0)


def test_spline_not_allowed_for_map():
    with pytest.raises(ValueError):
        mp("spline")


# ---------------------------------------------------------------- extrapolation


def test_curve_extrapolations():
    q = np.array([-1.0, 5.0])
    np.testing.assert_allclose(curve(extrap="clamp")(q), [0, 20])
    np.testing.assert_allclose(curve(extrap="zero")(q), [0, 0])
    np.testing.assert_allclose(curve(extrap="linear")(q), [-10, 20 + (20 - 0) / 2])
    out = curve(extrap="nan")(q)
    assert np.isnan(out).all()
    np.testing.assert_allclose(curve(extrap="nan")(np.array([4.0])), [20])  # edge itself is inside


def test_curve_extrapolation_other_interpolations():
    q = np.array([-1.0, 9.0])
    np.testing.assert_allclose(curve("nearest", "linear")(q), [0, 20])  # nothing to extend
    np.testing.assert_allclose(curve("previous", "linear")(q), [0, 20])
    s = curve("spline", "linear", x=[0, 1, 2], y=[0, 1, 0])
    np.testing.assert_allclose(s(np.array([-1.0, 3.0])), [-1.5, -1.5])  # end tangents: M=[0,-3,0]
    sz = curve("spline", "zero", x=[0, 1, 2], y=[0, 1, 0])
    np.testing.assert_allclose(sz(np.array([-1.0, 1.0])), [0, 1])


def test_map_extrapolations_each_axis_and_corner():
    # in-range reference: plane z = 10x + y
    pts = [(-1, 1), (5, 1), (1, -2), (1, 5), (-1, -2), (5, 5)]  # left, right, below, above, corners
    qx = np.array([p[0] for p in pts], dtype=float)
    qy = np.array([p[1] for p in pts], dtype=float)
    plane = 10 * qx + qy
    np.testing.assert_allclose(mp(extrap="linear")(qx, qy), plane)
    cl = mp(extrap="clamp")(qx, qy)
    np.testing.assert_allclose(cl, 10 * np.clip(qx, 0, 3) + np.clip(qy, 0, 2))
    np.testing.assert_allclose(mp(extrap="zero")(qx, qy), 0)
    assert np.isnan(mp(extrap="nan")(qx, qy)).all()
    # inside stays untouched by zero/nan
    assert float(mp(extrap="zero")(np.array(2.0), np.array(1.0))) == pytest.approx(21)
    assert float(mp(extrap="nan")(np.array(2.0), np.array(1.0))) == pytest.approx(21)


def test_registries_are_extensible_tables():
    assert {"linear", "nearest", "previous", "spline"} <= set(INTERPOLATIONS)
    assert {"clamp", "zero", "linear", "nan"} <= set(EXTRAPOLATIONS)
    assert INTERPOLATIONS["spline"].kinds == ("curve",)


# ---------------------------------------------------------------- shapes / nan / perf


def test_nan_scalar_broadcast_empty():
    c = curve()
    out = c(np.array([0.5, NAN, 1.5]))
    assert out[0] == pytest.approx(5) and np.isnan(out[1]) and out[2] == pytest.approx(5)
    assert np.isnan(curve(extrap="zero")(np.array([NAN]))[0])
    assert np.ndim(c(0.5)) == 0 and float(c(0.5)) == pytest.approx(5)
    assert c(np.array([])).shape == (0,)
    m = mp()
    r = m(np.array([0.0, 1.0, 3.0]), 2.0)  # broadcast scalar over array
    np.testing.assert_allclose(r, [2, 12, 32])
    r2 = m(np.array([[0.0], [1.0]]), np.array([0.0, 2.0]))  # (2,1) x (2,) -> (2,2)
    assert r2.shape == (2, 2)
    out = m(np.array([NAN, 1.0]), np.array([1.0, NAN]))
    assert np.isnan(out).all()


def test_wrong_arity_raises():
    with pytest.raises(TypeError):
        curve()(1.0, 2.0)
    with pytest.raises(TypeError):
        mp()(1.0)


@pytest.mark.parametrize("interp", ["linear", "nearest", "previous", "spline"])
def test_perf_curve_10m(interp):
    rng = np.random.default_rng(0)
    c = LookupTable("c", "curve", np.linspace(0, 100, 1000), rng.normal(size=1000), None, interp)
    q = rng.uniform(-10, 110, 10_000_000)
    t0 = time.perf_counter()
    r = c(q)
    assert r.shape == q.shape
    assert time.perf_counter() - t0 < 10


@pytest.mark.parametrize("interp", ["linear", "nearest", "previous"])
def test_perf_map_10m(interp):
    rng = np.random.default_rng(0)
    m = LookupTable("m", "map", np.linspace(0, 100, 50), np.linspace(0, 10, 40), rng.normal(size=(40, 50)), interp)
    q = rng.uniform(-1, 101, 10_000_000)
    t0 = time.perf_counter()
    r = m(q, q / 10)
    assert r.shape == q.shape
    assert time.perf_counter() - t0 < 10


# ---------------------------------------------------------------- expressions


def test_map_in_expression_and_errors():
    reg = FunctionRegistry(maps={"map_eff": mp(), "curve_k": curve()})
    v = {"rpm": np.array([0.0, 2.0]), "tq": np.array([0.0, 1.0])}
    np.testing.assert_allclose(evaluate_expression("map_eff(rpm, tq)", v, reg), [0, 21])
    np.testing.assert_allclose(evaluate_expression("curve_k(rpm) + 1", v, reg), [1, 1 + 5 * 0 + 0])
    for bad in ("map_eff(rpm)", "curve_k(rpm, tq)", "map_eff(rpm, y=tq)"):
        with pytest.raises(ExpressionError):
            evaluate_expression(bad, v, reg)


# ---------------------------------------------------------------- CSV


def write(path, text, enc="utf-8"):
    path.write_bytes(text.encode(enc))
    return path


def test_csv_curve_with_and_without_header(tmp_path):
    a = mio.parse_table_csv(write(tmp_path / "a.csv", "x,y\n0,0\n1,2\n"), "a")
    b = mio.parse_table_csv(write(tmp_path / "b.csv", "0,0\n1,2\n"), "b")
    for t in (a, b):
        assert t.kind == "curve"
        np.testing.assert_array_equal(t.x, [0, 1])
        np.testing.assert_array_equal(t.y, [0, 2])


def test_csv_map_and_blank_lines_whitespace(tmp_path):
    p = write(tmp_path / "m.csv", "\n  , 0 , 1, 3\n\n0, 0, 10, 30 \n2,2,12,32\n\n")
    t = mio.parse_table_csv(p, "m")
    assert t.kind == "map"
    np.testing.assert_array_equal(t.x, [0, 1, 3])
    np.testing.assert_array_equal(t.y, [0, 2])
    np.testing.assert_array_equal(t.z, [[0, 10, 30], [2, 12, 32]])


def test_csv_utf8_bom_and_cp932(tmp_path):
    t = mio.parse_table_csv(write(tmp_path / "a.csv", "x,y\n0,0\n1,2\n", "utf-8-sig"), "a")
    assert len(t.x) == 2  # BOM must not turn the header into garbage / the first cell non-numeric
    p = write(tmp_path / "b.csv", "回転数,トルク\n0,0\n1,2\n", "cp932")
    assert len(mio.parse_table_csv(p, "b").x) == 2
    p = write(tmp_path / "c.csv", "0,0\n1,2\n", "utf-8-sig")  # BOM + headerless
    np.testing.assert_array_equal(mio.parse_table_csv(p, "c").x, [0, 1])


def test_csv_kind_override(tmp_path):
    p = write(tmp_path / "a.csv", "0,0\n1,2\n")
    with pytest.raises(MapError):
        mio.parse_table_csv(p, "a", kind="map")  # a map needs 2 rows+2 axis values


@pytest.mark.parametrize(
    "text, kind, needle",
    [
        ("0,0\n1,abc\n", "auto", "row 2, column 2"),
        ("0,0\n1,2\n1,3\n", "auto", "row 3, column 1"),  # duplicate
        ("0,0\n2,2\n1,3\n", "auto", "row 3, column 1"),  # descending (not sorted)
        ("0,0\n1,-inf\n", "auto", "row 2, column 2"),  # (nan / blank are empty cells since 27.10)
        ("0,0\ninf,1\n", "auto", "row 2, column 1"),
        ("0,0\n", "auto", "at least 2"),
        ("x,y\n0,0\n", "auto", "at least 2"),
        ("0,0\n1,2,3\n", "curve", "row 2"),  # ragged
        ("", "auto", "empty"),
        ("a,0,1\n0,1,2\n1,3\n", "auto", "row 3"),  # ragged map
        ("a,0,1\n0,1,x\n1,3,4\n", "auto", "row 2, column 3"),
        ("a,0,0\n0,1,2\n1,3,4\n", "auto", "row 1, column 3"),  # dup X axis
        ("a,0,1\n1,1,2\n0,3,4\n", "auto", "row 3, column 1"),  # descending Y axis
        ("a,0,1\n0,1,inf\n1,3,4\n", "auto", "row 2, column 3"),
        ("a,0,1\n0,1,2\n", "auto", "at least 2"),  # one Y
        ("a,0\n0,1\n1,2\n", "map", "at least 2"),  # one X
    ],
)
def test_csv_errors(tmp_path, text, kind, needle):
    with pytest.raises(MapError, match=needle):
        mio.parse_table_csv(write(tmp_path / "e.csv", text), "e", kind=kind)


def test_axis_is_not_sorted_silently(tmp_path):
    with pytest.raises(MapError, match="ascending"):
        mio.parse_table_csv(write(tmp_path / "e.csv", "3,1\n2,1\n1,1\n"), "e")


# ---------------------------------------------------------------- storage


def src(tmp_path, text="x,y\n0,0\n10,100\n", name="src.csv"):
    return write(tmp_path / name, text)


def test_import_round_trip_and_original_deleted(isolated_config, tmp_path):
    p = src(tmp_path)
    t = mio.import_map(p, "curve_k", interpolation="nearest", extrapolation="zero")
    assert t.interpolation == "nearest"
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    assert (d / "curve_k.csv").read_bytes() == p.read_bytes()
    meta = json.loads((d / "curve_k.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == "1" and meta["kind"] == "curve"
    assert meta["interpolation"] == "nearest" and meta["extrapolation"] == "zero"
    assert meta["source_path"] == str(p.resolve()) and meta["imported_at"]
    p.unlink()
    loaded = mio.load_map("curve_k")
    assert loaded.kind == "curve" and loaded.extrapolation == "zero"
    np.testing.assert_array_equal(loaded.y, [0, 100])
    assert [i.name for i in mio.list_maps()] == ["curve_k"]


def test_list_empty(isolated_config):
    assert mio.list_maps() == []
    assert mio.load_all_maps() == ({}, [])


def test_failed_import_writes_nothing(isolated_config, tmp_path):
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path, "0,0\n0,1\n"), "bad")
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path), "bad", kind="curve", interpolation="bogus")
    assert mio.list_maps() == []
    assert not (isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps" / "bad.csv").exists()


def test_settings_round_trip(isolated_config, tmp_path):
    mio.import_map(src(tmp_path), "c1")
    t = mio.save_map_settings("c1", "previous", "linear")
    assert (t.interpolation, t.extrapolation) == ("previous", "linear")
    again = mio.load_map("c1")
    assert (again.interpolation, again.extrapolation) == ("previous", "linear")
    with pytest.raises(MapError):
        mio.save_map_settings("c1", "nope", "clamp")
    with pytest.raises(MapError):
        mio.save_map_settings("c1", "linear", "nope")


def test_spline_rejected_for_map_settings(isolated_config, tmp_path):
    m = src(tmp_path, ",0,1\n0,1,2\n1,3,4\n", "m.csv")
    mio.import_map(m, "m1")
    with pytest.raises(MapError):
        mio.save_map_settings("m1", "spline", "clamp")
    with pytest.raises(MapError):
        mio.import_map(m, "m2", interpolation="spline")


def test_reimport_changes_content_and_missing_source(isolated_config, tmp_path):
    p = src(tmp_path)
    mio.import_map(p, "c1")
    write(p, "x,y\n0,0\n10,500\n")
    t = mio.reimport_map("c1")
    assert t.y[-1] == 500
    assert mio.load_map("c1").y[-1] == 500
    p.unlink()
    with pytest.raises(MapError, match="source"):
        mio.reimport_map("c1")
    assert mio.load_map("c1").y[-1] == 500  # still usable


def test_reimport_invalid_keeps_old_copy(isolated_config, tmp_path):
    p = src(tmp_path)
    mio.import_map(p, "c1")
    write(p, "x,y\n0,0\n0,5\n")
    with pytest.raises(MapError):
        mio.reimport_map("c1")
    assert mio.load_map("c1").y[-1] == 100


def test_delete(isolated_config, tmp_path):
    mio.import_map(src(tmp_path), "c1")
    mio.delete_map("c1")
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    assert not list(d.iterdir())
    with pytest.raises(MapError):
        mio.delete_map("c1")
    with pytest.raises(MapError):
        mio.load_map("c1")


@pytest.mark.parametrize("name", ["", "1x", "a b", "../x", "/abs", "a/b", "class", "x.y"])
def test_invalid_names(isolated_config, tmp_path, name):
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path), name)


def test_name_clashes(isolated_config, tmp_path):
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path), "sqrt")  # builtin
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path), "myfunc", existing_names={"myfunc"})  # user function
    mio.import_map(src(tmp_path), "c1")
    with pytest.raises(MapError):
        mio.import_map(src(tmp_path), "c1")  # other map (on disk)


def test_load_all_maps_skips_unreadable_but_keeps_files(isolated_config, tmp_path):
    mio.import_map(src(tmp_path), "good")
    mio.import_map(src(tmp_path), "broken")
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    (d / "broken.csv").write_text("x,y\n0,0\n0,0\n")
    tables, warns = mio.load_all_maps()
    assert list(tables) == ["good"] and len(warns) == 1 and "broken" in warns[0]
    assert (d / "broken.csv").exists() and (d / "broken.json").exists()
