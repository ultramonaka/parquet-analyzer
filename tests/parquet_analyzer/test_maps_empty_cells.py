"""Maps / curves with empty cells (expressions.ja.md 27.10)."""
from __future__ import annotations

import logging

import numpy as np
import pytest
from test_functions_maps_ui import MAP, _csv, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)

from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import maps as mio
from parquet_analyzer.io.maps import MapError

NAN = float("nan")
X = [0.0, 1.0, 2.0, 3.0]
Y = [0.0, 10.0, 20.0, 30.0]


def _z():
    z = np.array([[10.0 * yi + xi * xi + 1 for xi in X] for yi in Y])
    z[1, 1] = NAN  # the one empty cell: x=1, y=10
    return z


def _map(interp="linear", extrap="clamp", z=None):
    return LookupTable("m", "map", X, Y, _z() if z is None else z, interp, extrap)


# ---------------------------------------------------------------- CSV


def test_map_csv_blank_and_nan_cells_read_as_nan(tmp_path):
    p = _csv(tmp_path, "m.csv", ",0,1,2\n0,1,,3\n10,4,nan,NaN\n20,7,8,9\n")
    t = mio.parse_table_csv(p)
    assert t.kind == "map" and t.z.shape == (3, 3)
    assert np.isnan(t.z[0, 1]) and np.isnan(t.z[1, 1]) and np.isnan(t.z[1, 2])
    assert t.z[2, 2] == 9 and t.n_empty == 3


def test_curve_csv_blank_and_nan_values(tmp_path):
    t = mio.parse_table_csv(_csv(tmp_path, "c.csv", "x,y\n0,1\n1,\n2,nan\n3,4\n"))
    assert t.kind == "curve" and np.isnan(t.y[1]) and np.isnan(t.y[2]) and t.y[3] == 4


@pytest.mark.parametrize(
    "text, fragment",
    [
        (",0,1\n0,,\n10,,\n", "every cell is empty"),  # no finite value in the map
        ("x,y\n0,\n1,nan\n", "every cell is empty"),  # nor in the curve
        (",0,\n0,1,2\n10,3,4\n", "row 1, column 3"),  # an empty X axis value
        (",0,1\n,1,2\n10,3,4\n", "row 2, column 1"),  # an empty Y axis value
        ("x,y\n,1\n1,2\n", "row 2, column 1"),  # an empty curve X
        ("x,y\n0,1\n1,inf\n", "non-finite"),  # inf is still an error
        (",0,1\n0,1,-inf\n10,3,4\n", "non-finite"),
        (",0,1\n0,1,abc\n10,3,4\n", "not a number"),
        (",0,nan\n0,1,2\n10,3,4\n", "non-finite"),  # axes stay finite
    ],
)
def test_empty_cell_csv_errors(tmp_path, text, fragment):
    with pytest.raises(MapError, match=fragment):
        mio.parse_table_csv(_csv(tmp_path, "bad.csv", text))


def test_import_warns_once_about_empty_cells(isolated_config, tmp_path, caplog):
    p = _csv(tmp_path, "m.csv", ",0,1,2\n0,1,,3\n10,4,5,\n20,7,8,9\n")
    with caplog.at_level(logging.WARNING, logger="parquet_analyzer.io.maps"):
        t = mio.import_map(p, "gap")
    assert len([r for r in caplog.records if "empty cell" in r.getMessage()]) == 1
    warns = t.warnings()
    assert len(warns) == 1 and "2 empty cell" in warns[0]
    assert mio.load_map("gap").n_empty == 2  # the stored copy keeps them


def test_spline_refused_with_empty_cells(isolated_config, tmp_path):
    p = _csv(tmp_path, "c.csv", "0,0\n1,\n2,4\n3,9\n")
    with pytest.raises(MapError, match="linear"):
        mio.parse_table_csv(p, interpolation="spline")
    with pytest.raises(ValueError, match="linear"):
        LookupTable("c", "curve", [0, 1, 2], [0, NAN, 2], None, "spline")
    mio.import_map(p, "cv")  # linear is fine
    with pytest.raises(MapError, match="linear"):
        mio.save_map_settings("cv", "spline", "clamp")
    assert mio.load_map("cv").interpolation == "linear"  # nothing was written


def test_write_read_roundtrip_with_empty_cells(tmp_path):
    t = _map()
    p = tmp_path / "out.csv"
    mio.write_table_csv(t, p)
    text = p.read_text(encoding="utf-8")
    assert "1.0,,3.0" not in text and ",," in text.replace("\n", "")  # blank, not "nan"
    assert "nan" not in text.lower()
    back = mio.parse_table_csv(p)
    np.testing.assert_array_equal(back.x, t.x)
    np.testing.assert_array_equal(back.y, t.y)
    np.testing.assert_array_equal(back.z, t.z)  # NaN positions compare equal
    c = LookupTable("c", "curve", [0.0, 1.0, 2.0], [1.5, NAN, 2.5], None)
    mio.write_table_csv(c, p)
    back = mio.parse_table_csv(p)
    np.testing.assert_array_equal(back.y, c.y)


# ---------------------------------------------------------------- interpolation


def test_bilinear_nan_only_in_the_four_cells_touching_the_empty_point():
    t, full = _map(), _map(z=np.nan_to_num(_z(), nan=7.0))
    qx, qy = np.meshgrid(np.linspace(0, 3, 61), np.linspace(0, 30, 61))
    r, ref = t(qx, qy), full(qx, qy)
    # empty point at x=1, y=10: NaN exactly for 0 < x < 2 and 0 < y < 20 (open: the grid lines of
    # the neighbouring points give the neighbours zero weight)
    inside = (qx > 0) & (qx < 2) & (qy > 0) & (qy < 20)
    assert np.array_equal(np.isnan(r), inside)
    np.testing.assert_array_equal(r[~inside], ref[~inside])  # the rest is the unchanged table's value


def test_bilinear_on_grid_points_and_lines():
    t = _map()
    assert np.isnan(t(1.0, 10.0))  # the empty point itself
    assert t(0.0, 10.0) == _z()[1, 0] and t(2.0, 10.0) == _z()[1, 2]  # neighbours stay finite
    assert np.isfinite(t(1.0, 0.0)) and np.isfinite(t(1.0, 20.0))  # weight 0 on the empty point
    assert np.isnan(t(1.0, 5.0)) and np.isnan(t(0.5, 10.0))  # on a line to it: weight > 0


def test_nearest_and_previous_pick_the_chosen_point():
    n, p = _map("nearest"), _map("previous")
    assert np.isnan(n(1.2, 9.0)) and np.isnan(n(0.8, 11.0))  # nearest point = the empty one
    assert n(0.4, 9.0) == _z()[1, 0]
    assert np.isnan(p(1.5, 15.0)) and p(2.5, 15.0) == _z()[1, 2]


def test_curve_linear_nearest_previous_with_empty_value():
    y = [0.0, 10.0, NAN, 30.0, 40.0]
    c = lambda i: LookupTable("c", "curve", [0, 1, 2, 3, 4], y, None, i)  # noqa: E731
    lin = c("linear")
    assert lin(0.5) == 5.0 and lin(3.5) == 35.0
    assert np.isnan(lin(1.5)) and np.isnan(lin(2.5)) and np.isnan(lin(2.0))
    assert lin(1.0) == 10.0 and lin(3.0) == 30.0  # grid points either side stay finite
    assert np.isnan(c("nearest")(2.2)) and c("nearest")(2.7) == 30.0
    assert np.isnan(c("previous")(2.9)) and c("previous")(3.0) == 30.0


def test_extrapolation_with_empty_edge_values():
    edge = LookupTable("c", "curve", [0, 1, 2], [NAN, 10.0, 20.0], None, "linear", "clamp")
    assert np.isnan(edge(-5.0)) and edge(5.0) == 20.0  # clamp: the edge value, empty -> empty
    ext = LookupTable("c", "curve", [0, 1, 2], [NAN, 10.0, 20.0], None, "linear", "linear")
    assert np.isnan(ext(-1.0)) and ext(3.0) == 30.0  # linear: the end slope (needs both end points)
    zero = LookupTable("c", "curve", [0, 1, 2], [NAN, 10.0, 20.0], None, "linear", "zero")
    nan = LookupTable("c", "curve", [0, 1, 2], [NAN, 10.0, 20.0], None, "linear", "nan")
    assert zero(-1.0) == 0.0 and np.isnan(nan(-1.0)) and zero(1.5) == 15.0
    m = _map(extrap="clamp", z=np.where(np.isnan(_z()), 1.0, _z()))
    m.z[0, 0] = NAN
    assert np.isnan(m(-5.0, -5.0)) and np.isfinite(m(-5.0, 15.0))


# ---------------------------------------------------------------- regression: no empty cells


def _old_bilinear(t, qx, qy):
    from parquet_analyzer.core.maps import _cell

    x, y, z = t.x, t.y, t.z
    ix, iy = _cell(x, qx), _cell(y, qy)
    fx = (qx - x[ix]) / (x[ix + 1] - x[ix])
    fy = (qy - y[iy]) / (y[iy + 1] - y[iy])
    return (
        z[iy, ix] * (1 - fx) * (1 - fy)
        + z[iy, ix + 1] * fx * (1 - fy)
        + z[iy + 1, ix] * (1 - fx) * fy
        + z[iy + 1, ix + 1] * fx * fy
    )


def test_tables_without_empty_cells_are_bit_identical_to_before():
    rng = np.random.default_rng(3)
    x = np.cumsum(rng.uniform(0.3, 2.0, 12))
    y = np.cumsum(rng.uniform(0.3, 2.0, 9))
    z = rng.normal(size=(9, 12))
    t = LookupTable("m", "map", x, y, z)
    qx, qy = rng.uniform(x[0], x[-1], 5000), rng.uniform(y[0], y[-1], 5000)
    np.testing.assert_array_equal(t(qx, qy), _old_bilinear(t, qx, qy))
    c = LookupTable("c", "curve", x, z[0])
    q = rng.uniform(x[0], x[-1], 5000)
    i = np.clip(np.searchsorted(x, q, side="right") - 1, 0, len(x) - 2)
    old = z[0][i] + ((q - x[i]) / (x[i + 1] - x[i])) * (z[0][i + 1] - z[0][i])
    np.testing.assert_array_equal(c(q), old)
    assert t.warnings() == [] and not t.has_empty


# ---------------------------------------------------------------- manager UI


def _open(win, tmp_path, text, name="mp"):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, f"{name}.csv", text)), name)
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index(name))
    return mgr


GAPPY = ",0,1,2\n0,1,,3\n10,4,5,6\n20,7,8,9\n"


def test_manager_table_shows_blank_and_previews_draw(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, GAPPY)
    assert mgr.preview_table.item(1, 2).text() == ""  # the empty cell is blank
    assert "empty cell" in mgr.message.text()
    assert mgr.preview_table.item(1, 1).text() == "1"
    qapp.processEvents()
    assert mgr.plot_raw.getPlotItem().items  # drawn without an exception
    # the heat map skips the empty cell: the PColorMeshItem got a NaN Z
    import pyqtgraph as pg

    mesh = [i for i in mgr.plot_raw.getPlotItem().items if isinstance(i, pg.PColorMeshItem)]
    assert mesh and np.isnan(mesh[0].z).sum() == 1


def test_manager_import_message_warns_once(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "g.csv", GAPPY)), "gap")
    assert mgr.message.text().count("empty cell") == 1


def test_manager_curve_with_empty_point_draws(win, tmp_path, qapp):
    mgr = _open(win, tmp_path, "x,y\n0,0\n1,\n2,4\n3,9\n", "cg")
    qapp.processEvents()
    assert mgr.preview_table.item(1, 1).text() == ""
    assert mgr.plot_raw.getPlotItem().items


def test_manager_edit_cell_to_empty_saves_blank_and_reloads(win, tmp_path):
    mgr = _open(win, tmp_path, ",0,1\n0,1,2\n10,3,4\n20,5,6\n", "mp")
    mgr.preview_table.item(2, 2).setText("")
    assert mgr.save_values_button.isEnabled()
    assert mgr.save_values() is True
    t = win.function_registry.maps["mp"]
    assert np.isnan(t.z[1, 1]) and t.z[0, 0] == 1
    text = (mio.maps_dir() / "mp.csv").read_text(encoding="utf-8")
    assert "10.0,3.0," in text.splitlines()[2] and text.splitlines()[2].endswith(",")  # blank cell, not "nan"
    again = mio.load_map("mp")
    np.testing.assert_array_equal(again.z, t.z)
    assert mgr.preview_table.item(2, 2).text() == ""


def test_manager_edit_all_empty_or_empty_axis_is_rejected(win, tmp_path):
    mgr = _open(win, tmp_path, ",0,1\n0,1,2\n10,3,4\n", "mp")
    mgr.preview_table.item(1, 0).setText("")  # an axis value cannot be empty
    assert mgr.save_values() is False and "row 2, column 1" in mgr.message.text()
    mgr.revert_values()
    for r, c in ((1, 1), (1, 2), (2, 1), (2, 2)):
        mgr.preview_table.item(r, c).setText("")
    assert mgr.save_values() is False and "empty" in mgr.message.text()
    assert not np.isnan(win.function_registry.maps["mp"].z).any()  # nothing changed
    mgr.revert_values()


def test_manager_spline_blocked_for_curve_with_empty(win, tmp_path):
    mgr = _open(win, tmp_path, "0,0\n1,1\n2,4\n3,9\n", "cv")
    mgr.interp_combo.setCurrentIndex(mgr.interp_combo.findData("spline"))
    assert mgr.apply_settings()
    mgr.preview_table.item(1, 1).setText("")
    assert mgr.save_values() is False  # the saved table would be spline + an empty cell
    assert "linear" in mgr.message.text() or "spline" in mgr.message.text()
    mgr.revert_values()


def test_expression_uses_map_with_empty_cells(win, tmp_path):
    _open(win, tmp_path, GAPPY, "gap")
    win._on_add_derived_variable("m", "gap(a * 0 + 0.5, a * 0 + 5)")  # inside the cells around the empty one
    vals = win.variable_values("m")
    assert np.isnan(vals).all()


def test_3d_surface_drops_faces_of_empty_cells(qapp):
    from parquet_analyzer.ui.xyz_3d_window import Xyz3DView

    v = Xyz3DView()
    z = np.arange(12.0).reshape(3, 4)
    z[1, 1] = NAN
    v.set_surface([0, 1, 2, 3], [0, 1, 2], z)
    md = v._data_items[0].opts["meshdata"]
    assert md.faces().shape == (12 - 8, 3)  # the 4 squares around the point go whole (analysis 44.3)
    assert len(v.scene["pos"]) == 11  # the empty point is no hover candidate
    v.set_surface([0, 1, 2, 3], [0, 1, 2], np.arange(12.0).reshape(3, 4))
    assert v._data_items[0].opts["meshdata"].faces().shape == (12, 3)
    v.close()


# -- axis names in the CSV header (analysis 44.4, review 2026-10-07) -------------------------------


def _csv_table(kind, names):
    from parquet_analyzer.core.maps import LookupTable

    if kind == "curve":
        return LookupTable("t", "curve", [0.0, 1.0, 2.0], [1.0, np.nan, 3.0], None, axis_names=names)
    return LookupTable("t", "map", [0.0, 1.0], [0.0, 1.0, 2.0], np.arange(6.0).reshape(3, 2), axis_names=names)


def test_csv_header_round_trips_axis_names_with_commas(tmp_path):
    from parquet_analyzer.io import maps as maps_io

    for kind in ("curve", "map"):
        p = tmp_path / f"{kind}.csv"
        maps_io.write_table_csv(_csv_table(kind, ("rpm, avg", "torque", "z")), p, axis_header=True)
        first = p.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith('"rpm, avg"') or first.startswith('"torque\\rpm, avg"')
        back = maps_io.parse_table_csv(p)
        assert back.axis_names[:2] == ("rpm, avg", "torque")
        np.testing.assert_array_equal(back.values, _csv_table(kind, ("a", "b", "c")).values)


def test_csv_header_is_omitted_for_numeric_names_and_off_by_default(tmp_path):
    from parquet_analyzer.io import maps as maps_io

    p = tmp_path / "n.csv"
    maps_io.write_table_csv(_csv_table("curve", ("1", "y", "z")), p, axis_header=True)
    assert p.read_text(encoding="utf-8").splitlines()[0] == "0.0,1.0"  # no header: it would parse as data
    maps_io.write_table_csv(_csv_table("map", ("x", "2e3", "z")), p, axis_header=True)
    assert p.read_text(encoding="utf-8").splitlines()[0].startswith(",")
    maps_io.write_table_csv(_csv_table("curve", ("a", "b", "z")), p)
    assert p.read_text(encoding="utf-8").splitlines()[0] == "0.0,1.0"  # the manager / generic path: unchanged
