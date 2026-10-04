"""Review follow-ups for maps (detailed_specification.md 27.1-27.3): spline reference values,
nearest ties, inf input, CSV header/auto-detect rules, name validation on every entry point."""
from __future__ import annotations

import numpy as np
import pytest

from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import maps as mio
from parquet_analyzer.io.maps import MapError

X = np.array([0.0, 0.5, 2.0, 2.2, 5.0, 9.0])
Y = np.array([1.0, -2.0, 3.0, 3.5, -1.0, 4.0])


def curve(interp="linear", extrap="clamp", x=(0, 1, 2, 4), y=(0, 10, 0, 20)):
    return LookupTable("c", "curve", x, y, None, interp, extrap)


def mp(interp="linear", extrap="clamp"):
    x, y = [0.0, 1.0, 3.0], [0.0, 2.0]
    return LookupTable("m", "map", x, y, [[10 * xi + yi for xi in x] for yi in y], interp, extrap)


def write(path, text):
    path.write_bytes(text.encode("utf-8"))
    return path


def src(tmp_path, text="x,y\n0,0\n10,100\n", name="src.csv"):
    return write(tmp_path / name, text)


def reference_natural_spline(x, y, q):
    """Independent implementation: dense linear solve for the knot second derivatives."""
    n = len(x)
    h = np.diff(x)
    A = np.zeros((n, n))
    rhs = np.zeros(n)
    A[0, 0] = A[-1, -1] = 1
    for i in range(1, n - 1):
        A[i, i - 1], A[i, i], A[i, i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
        rhs[i] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
    M = np.linalg.solve(A, rhs)
    i = np.clip(np.searchsorted(x, q, side="right") - 1, 0, n - 2)
    a, b = (x[i + 1] - q) / h[i], (q - x[i]) / h[i]
    return a * y[i] + b * y[i + 1] + ((a**3 - a) * M[i] + (b**3 - b) * M[i + 1]) * h[i] ** 2 / 6


def test_spline_nonuniform_grid_matches_reference_and_is_c1():
    s = curve("spline", x=X, y=Y)
    q = np.linspace(0, 9, 241)
    np.testing.assert_allclose(s(q), reference_natural_spline(X, Y, q), atol=1e-10)

    def d(v):
        return (float(s(np.array(v + 1e-5))) - float(s(np.array(v - 1e-5)))) / 2e-5

    for k in X[1:-1]:
        assert d(k - 1e-5) == pytest.approx(d(k + 1e-5), abs=1e-3)


def test_spline_linear_extrapolation_is_tangent_continuation():
    s = curve("spline", "linear", x=X, y=Y)
    h = 1e-6

    def f(v):
        return float(s(np.array(v)))

    left = (f(X[0] + h) - f(X[0])) / h
    right = (f(X[-1]) - f(X[-1] - h)) / h
    assert (f(X[0] - 1) - f(X[0])) / -1 == pytest.approx(left, abs=1e-4)
    assert (f(X[-1] + 1) - f(X[-1])) / 1 == pytest.approx(right, abs=1e-4)


def test_nearest_ties_go_to_upper_point():
    n = curve("nearest")  # x = 0, 1, 2, 4
    np.testing.assert_allclose(n(np.array([0.5, 1.5, 3.0])), [10, 0, 20])
    assert float(mp("nearest")(np.array(2.0), np.array(1.0))) == pytest.approx(10 * 3 + 2)


def test_inf_input_with_linear_extrapolation_does_not_raise():
    out = curve(extrap="linear")(np.array([np.inf, -np.inf]))
    assert out[0] == np.inf and out[1] == -np.inf
    flat = LookupTable("f", "curve", [0, 1], [5, 5], None, "linear", "linear")
    assert np.isnan(flat(np.array([np.inf]))[0])
    assert float(curve()(np.array(np.inf))) == 20  # clamp
    mp(extrap="linear")(np.array([np.inf]), np.array([1.0]))  # no exception


def test_csv_mixed_first_row_is_error_not_header(tmp_path):
    with pytest.raises(MapError, match="row 1, column 2"):
        mio.parse_table_csv(write(tmp_path / "a.csv", "0,1.2x\n1,2\n2,3\n"), "a")
    assert len(mio.parse_table_csv(write(tmp_path / "b.csv", "x,y\n1,2\n2,3\n"), "b").x) == 2


def test_auto_detect_numeric_top_left_is_error(tmp_path):
    p = write(tmp_path / "m.csv", "0,1,2\n1,5,6\n2,7,8\n")
    with pytest.raises(MapError, match="auto-detect"):
        mio.parse_table_csv(p, "m")
    t = mio.parse_table_csv(p, "m", kind="map")
    assert t.kind == "map" and t.z.shape == (2, 2)
    assert mio.parse_table_csv(write(tmp_path / "n.csv", "rpm,1,2\n1,5,6\n2,7,8\n"), "n").kind == "map"
    assert mio.parse_table_csv(write(tmp_path / "o.csv", ",1,2\n1,5,6\n2,7,8\n"), "o").kind == "map"


def test_traversal_names_rejected_everywhere(isolated_config, tmp_path):
    data = isolated_config.PARQUET_ANALYZER_DATA_DIR
    mio.import_map(src(tmp_path), "ok")
    # a perfectly valid map one directory above maps/: only name validation can protect it
    (data / "victim.json").write_text('{"kind": "curve", "interpolation": "linear", "extrapolation": "clamp"}')
    (data / "victim.csv").write_text("x,y\n0,0\n1,1\n")
    for bad in ("../victim", "sqrt", "a/b", ""):
        for fn in (
            mio.load_map,
            mio.delete_map,
            mio.reimport_map,
            lambda n: mio.save_map_settings(n, "linear", "clamp"),
        ):
            with pytest.raises(MapError):
                fn(bad)
    assert (data / "victim.json").exists() and (data / "victim.csv").exists()


def test_list_skips_invalid_stems_but_keeps_files(isolated_config, tmp_path):
    mio.import_map(src(tmp_path), "ok")
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    for stem in ("sqrt", "a.b"):
        (d / f"{stem}.json").write_text("{}")
        (d / f"{stem}.csv").write_text("x,y\n0,0\n1,1\n")
    assert [i.name for i in mio.list_maps()] == ["ok"]
    tables, warns = mio.load_all_maps()
    assert list(tables) == ["ok"] and len(warns) == 2
    assert (d / "sqrt.json").exists() and (d / "a.b.csv").exists()
