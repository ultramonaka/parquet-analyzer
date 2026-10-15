"""Excluding outlier points from a measured map (analysis.ja.md 5.39, 44.13).

`compute_xy_map` with `excluded_rows` is tested directly; the window is driven through Qt's real mouse routing
(press / move / release on the plot viewport). OpenGL cannot render offscreen: in 3D only the data handed
to the view is checked.
"""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QMessageBox, QPushButton

from parquet_analyzer import features
from parquet_analyzer.core import map_grid as mg
from parquet_analyzer.ui import map_export_dialog as med
from parquet_analyzer.ui import messages
from parquet_analyzer.ui import xy_exclude as xe
from parquet_analyzer.ui import xy_map as xm
from parquet_analyzer.ui import xy_map_view as xmv
from parquet_analyzer.ui import xy_plot_window as xy_mod
from parquet_analyzer.ui import xyz_3d_window as x3
from parquet_analyzer.ui.analysis_window import CANCELLED
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow

N = 3000
OUT_A, OUT_B = 10, 20  # rows of the two far-away points


def _data():
    rng = np.random.default_rng(1)
    px, py = rng.uniform(0, 10, N), rng.uniform(0, 10, N)
    px[OUT_A], py[OUT_A] = 50.0, 50.0
    px[OUT_B], py[OUT_B] = -40.0, 60.0
    pz = px + py
    return px, py, pz


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        monkeypatch.setattr(messages, name, lambda *a, **k: QMessageBox.StandardButton.Ok, raising=False)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setitem(features._EFFECTIVE, "map_from_xy", True)
    monkeypatch.setitem(features._EFFECTIVE, "maps", True)
    monkeypatch.setitem(features._EFFECTIVE, "map_exclude_points", True)  # 44.13 (8); off tests set False
    x3.reset_gl3d_probe()
    yield
    x3.reset_gl3d_probe()


@pytest.fixture
def ops(monkeypatch):
    recorded = []
    for mod in (xy_mod, xmv, xe, med):
        monkeypatch.setattr(mod, "log_op", lambda *a, **kw: recorded.append((a[0], kw)))
    return recorded


def _drain(qapp, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _wait(qapp, win, timeout=30.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


@pytest.fixture
def mw(qapp, isolated_config, tmp_path):
    px, py, pz = _data()
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(N).astype("int64") * np.timedelta64(100, "ms")
    path = tmp_path / "scatter.parquet"
    pq.write_table(pa.table({"time": pa.array(t, type=pa.timestamp("ms")), "px": px, "py": py, "pz": pz}), path)
    win = MainWindow()
    win.show()
    assert win.load_parquet(str(path))
    win._add_variable_to_plot(win.plot_grid.plots[0], "px")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    yield win
    win.close()


def _open(qapp, mw, x="px", ys=("py",), z="pz", color="z", fill="none", agg="mean"):
    w = mw.open_xy_plot()
    w.resize(1000, 700)
    w.show()
    widgets = (w._x_combo, w._y_picker, w._z_combo, w._color_combo, w._display_combo)
    for widget in widgets:
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_picker.set_selected(list(ys))
    w._z_combo.setCurrentText(z)
    w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    w._display_combo.setCurrentIndex(w._display_combo.findData("map"))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData(agg))
    w._fill_combo.setCurrentIndex(w._fill_combo.findData(fill))
    for widget in widgets:
        widget.blockSignals(False)
    w._apply_color_constraints()
    w._on_setting_changed()
    _wait(qapp, w)
    return w


# -- mouse through Qt's real routing -------------------------------------------------------


def _vp(w, x, y) -> QPoint:
    """Viewport position of the data point (x, y) of the 2D plot."""
    scene = w._plot.vb.mapViewToScene(QPointF(x, y))
    return w._glw.mapFromScene(scene)


def _send(widget, kind, pos, button, buttons):
    ev = QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), button, buttons,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


def _click(qapp, w, x, y):
    QTest.mouseClick(w._glw.viewport(), Qt.MouseButton.LeftButton, pos=_vp(w, x, y))
    _wait(qapp, w)


def _drag(qapp, w, p0, p1, steps=6):
    vp = w._glw.viewport()
    a, b = _vp(w, *p0), _vp(w, *p1)
    left = Qt.MouseButton.LeftButton
    _send(vp, QEvent.Type.MouseButtonPress, a, left, left)
    for i in range(1, steps + 1):
        pos = QPoint(a.x() + (b.x() - a.x()) * i // steps, a.y() + (b.y() - a.y()) * i // steps)
        time.sleep(0.03)  # pyqtgraph drops mouse moves that follow each other too closely (mouseRateLimit)
        _send(vp, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, left)
        qapp.processEvents()
    _send(vp, QEvent.Type.MouseButtonRelease, b, left, Qt.MouseButton.NoButton)
    _drain(qapp)
    _wait(qapp, w)


def _turn_on(w):
    w._exclude.mode_button.setChecked(True)


# -- compute_xy_map ------------------------------------------------------------------------


def _fetcher(cols):
    return lambda name, a, b: cols[name][a:b]


def _compute(excluded=None, chunk_rows=10_000, scatter_limit=100_000, kind="map", ys=("py",), **kw):
    px, py, pz = _data()
    cols = {"px": px, "py": py, "pz": pz}
    req = ("count", None)
    return xm.compute_xy_map(
        _fetcher(cols), "px", list(ys), "pz" if kind == "map" else None, kind, req, req if kind == "map" else None,
        "mean", 1, "none", 0, N, chunk_rows=chunk_rows, scatter_limit=scatter_limit, excluded_rows=excluded, **kw,
    )


def test_excluded_rows_do_not_affect_bounds_counts_and_values():
    base = _compute()
    assert base.x_auto[1] >= 50 and base.x_auto[0] <= -40  # the outliers stretch the automatic range
    r = _compute(np.array([OUT_A, OUT_B]))
    assert r.x_auto[1] < 12 and r.x_auto[0] > -1 and r.y_auto[1] < 12  # ... and no longer do
    assert r.n_valid == N - 2 and base.n_valid == N
    assert r.n_user_excluded == 2 and r.n_excluded == 0
    assert r.grids[0].count.sum() + r.n_outside == N - 2
    assert not any(np.array_equal(p, [50.0]) for p in r.points[0][:1])
    assert 50.0 not in r.points[0][0] and 60.0 not in r.points[0][1]


def test_point_rows_match_points():
    px, py, pz = _data()
    r = _compute(np.array([OUT_A, 5, 2999]), chunk_rows=700, scatter_limit=400)  # thinned and chunked
    rows = r.point_rows[0]
    assert len(rows) == len(r.points[0][0]) < N - 3
    np.testing.assert_array_equal(px[rows], r.points[0][0])
    np.testing.assert_array_equal(py[rows], r.points[0][1])
    np.testing.assert_array_equal(pz[rows], r.points[0][2])
    assert not {OUT_A, 5, 2999} & set(rows.tolist())
    assert np.all(np.diff(rows) > 0)


def test_excluded_points_hold_the_values_of_the_excluded_rows():
    px, py, pz = _data()
    r = _compute(np.array([OUT_A, OUT_B]))
    ex, ey, ez = r.excluded_points[0]
    np.testing.assert_array_equal(r.excluded_point_rows[0], [OUT_A, OUT_B])
    np.testing.assert_array_equal(ex, px[[OUT_A, OUT_B]])
    np.testing.assert_array_equal(ey, py[[OUT_A, OUT_B]])
    np.testing.assert_array_equal(ez, pz[[OUT_A, OUT_B]])
    assert not r.excluded_thinned


def test_non_finite_excluded_rows_are_counted_but_not_drawn():
    px, py, pz = _data()
    px = px.copy()
    px[7] = np.nan
    cols = {"px": px, "py": py, "pz": pz}
    req = ("count", None)
    r = xm.compute_xy_map(_fetcher(cols), "px", ["py"], "pz", "map", req, req, "mean", 1, "none", 0, N,
                          chunk_rows=1000, scatter_limit=100_000, excluded_rows=np.array([7, 8]))
    assert r.n_user_excluded == 2 and r.n_excluded == 0  # not counted as non-finite either
    np.testing.assert_array_equal(r.excluded_point_rows[0], [8])


def test_excluded_points_are_thinned_beyond_the_draw_limit(monkeypatch):
    monkeypatch.setattr(xm, "XY_EXCLUDED_DRAW_LIMIT", 50)
    rows = np.arange(100, 400)
    r = _compute(rows)
    assert r.n_user_excluded == 300 and r.excluded_thinned
    assert len(r.excluded_point_rows[0]) == 50 and len(r.excluded_points[0][0]) == 50
    assert r.excluded_point_rows[0][0] == 100 and r.excluded_point_rows[0][-1] == 399


def test_curve_exclusion_removes_the_row_from_every_y():
    r = _compute(np.array([OUT_A]), kind="curve", ys=("py", "pz"))
    base = _compute(kind="curve", ys=("py", "pz"))
    assert r.n_user_excluded == 1 and r.n_valid == N - 1
    for k in (0, 1):
        assert OUT_A not in r.point_rows[k]
        assert OUT_A in base.point_rows[k]
        np.testing.assert_array_equal(r.excluded_point_rows[k], [OUT_A])
        assert r.grids[k].count.sum() + r.grids[k].n_outside == N - 1


def test_excluded_rows_outside_the_range_change_nothing():
    base = _compute()
    px, py, pz = _data()
    cols = {"px": px, "py": py, "pz": pz}
    req = ("count", None)
    kw = dict(chunk_rows=500, scatter_limit=100_000)
    a = xm.compute_xy_map(_fetcher(cols), "px", ["py"], "pz", "map", req, req, "mean", 1, "none", 1000, 2000, **kw)
    b = xm.compute_xy_map(_fetcher(cols), "px", ["py"], "pz", "map", req, req, "mean", 1, "none", 1000, 2000,
                          excluded_rows=np.array([5, OUT_A, 2500, 2999]), **kw)
    assert b.n_user_excluded == 0 and not b.excluded_points
    assert b.n_valid == a.n_valid and b.x_auto == a.x_auto
    np.testing.assert_array_equal(b.grids[0].value, a.grids[0].value)
    np.testing.assert_array_equal(b.point_rows[0], a.point_rows[0])
    assert base.n_valid == N


@pytest.mark.parametrize("chunk_rows", [1, 7, 10, 11, 20, 21, 3000])
def test_exclusion_at_chunk_boundaries(chunk_rows):
    rows = np.array([0, 9, 10, 11, 19, 20, 2999])
    r = _compute(rows, chunk_rows=chunk_rows)
    full = _compute(rows, chunk_rows=10_000)
    assert r.n_valid == N - len(rows) and r.n_user_excluded == len(rows)
    np.testing.assert_array_equal(r.excluded_point_rows[0], rows)
    np.testing.assert_array_equal(r.point_rows[0], full.point_rows[0])
    np.testing.assert_array_equal(r.grids[0].count, full.grids[0].count)


def test_keep_sparse_empty_reaches_the_result_and_hover():
    px, py, pz = _data()
    cols = {"px": px, "py": py, "pz": pz}
    req = ("step", mg.GridAxisSpec("step", 0, 10, step=2.5))
    r = xm.compute_xy_map(_fetcher(cols), "px", ["py"], "pz", "map", req, req, "mean", 50, "smooth", 0, N,
                          chunk_rows=1000, scatter_limit=1000, keep_sparse_empty=True)
    g = r.grids[0]
    assert r.keep_sparse_empty and g.sparse_mask.any()
    iy, ix = [int(v[0]) for v in np.nonzero(g.sparse_mask)]
    assert np.isnan(g.value[iy, ix]) and not g.filled_mask[iy, ix]
    text = xm.map_hover_text(r, g.x_axis[ix], g.y_axis[iy])
    assert xm.tr("xy.map_cell_sparse") in text and f"{int(g.count[iy, ix])}" in text


# -- rows_in_rect ----------------------------------------------------------------------------


def test_rows_in_rect_selects_by_value_over_all_rows():
    px, py, _ = _data()
    cols = {"px": px, "py": py}
    f = _fetcher(cols)
    rows = xe.rows_in_rect(f, "px", ["py"], (45, 55), (45, 55), 0, N, 400)
    np.testing.assert_array_equal(rows, [OUT_A])
    want = np.flatnonzero((px >= 2) & (px <= 3) & (py >= 4) & (py <= 5))
    np.testing.assert_array_equal(xe.rows_in_rect(f, "px", ["py"], (2, 3), (4, 5), 0, N, 101), want)
    # the range [row_start, row_end) limits the rows
    sub = xe.rows_in_rect(f, "px", ["py"], (0, 10), (0, 10), 100, 200, 30)
    np.testing.assert_array_equal(sub, np.arange(100, 200))


def test_rows_in_rect_curve_takes_any_y_and_skips_non_finite():
    x = np.array([1.0, 1.0, 1.0, np.nan, 1.0])
    a = np.array([0.0, 5.0, 9.0, 0.0, np.nan])
    b = np.array([9.0, 9.0, 0.0, 0.0, 0.0])
    f = _fetcher({"x": x, "a": a, "b": b})
    np.testing.assert_array_equal(xe.rows_in_rect(f, "x", ["a"], (0, 2), (-1, 1), 0, 5, 2), [0])
    np.testing.assert_array_equal(xe.rows_in_rect(f, "x", ["a", "b"], (0, 2), (-1, 1), 0, 5, 2), [0, 2, 4])


def test_rows_in_rect_cancelled():
    f = _fetcher({"x": np.arange(10.0), "y": np.arange(10.0)})
    assert xe.rows_in_rect(f, "x", ["y"], (0, 9), (0, 9), 0, 10, 3, cancelled=lambda: True) is CANCELLED


def test_nearest_point_radius_and_choice():
    cand = [("point", np.array([0.0, 10.0]), np.array([0.0, 10.0]), np.array([4, 5])),
            ("cross", np.array([10.0]), np.array([10.5]), np.array([9]))]
    assert xe.nearest_point(cand, 0.1, 0.0, (0.1, 0.1)) == ("point", 4)  # 1 px away
    assert xe.nearest_point(cand, 0.0, 0.8, (0.1, 0.1)) is None  # 8 px away
    assert xe.nearest_point(cand, 10.0, 10.4, (0.1, 0.1)) == ("cross", 9)  # closer than the point
    assert xe.nearest_point(cand, 10.0, 10.4, (0.0, 0.1)) is None  # degenerate pixel size


# -- the window: click / rectangle / modes -------------------------------------------------


def test_buttons_state(qapp, mw):
    w = _open(qapp, mw)
    b, reset = w._exclude.mode_button, w._exclude.reset_button
    assert b.isCheckable() and not b.isChecked() and b.isEnabled()
    assert not reset.isEnabled()  # nothing excluded
    assert not w._exclude.active()
    assert not b.isHidden() and not reset.isHidden()  # both shown while the map is the display
    assert w._exclude.group in w._map_groups


def test_mode_on_turns_the_point_overlay_on(qapp, mw):
    w = _open(qapp, mw)
    w._points_check.setChecked(False)
    _wait(qapp, w)
    _turn_on(w)
    assert w._points_check.isChecked()
    assert w._exclude.active()
    w._points_check.setChecked(False)  # hiding the points leaves the mode (nothing to pick)
    assert not w._exclude.mode_button.isChecked()


def test_click_excludes_nearest_point_recomputes_and_draws_a_cross(qapp, mw, ops):
    w = _open(qapp, mw)
    r0 = w._result
    assert r0.x_auto[1] >= 50
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)  # the outlier A
    assert w._exclude.rows.tolist() == [OUT_A]
    r = w._result
    assert r is not r0 and r.n_user_excluded == 1 and r.n_valid == N - 1
    assert r.excluded_point_rows[0].tolist() == [OUT_A]
    assert "excluded" in w._map_items  # the grey cross
    assert w._map_items["excluded"].opts["symbol"] == "x"
    assert w._exclude.reset_button.isEnabled()
    assert "1" in w.status_text() and xm.tr("xy.map_status_user_excluded", n=1) in w.status_text()
    name, kw = next((n, k) for n, k in ops if n == "xy_map_exclude")
    assert kw["kind"] == "click_exclude" and kw["changed"] == 1 and kw["total"] == 1
    compute = [k for n, k in ops if n == "xy_plot_compute"][-1]
    assert compute["excluded_rows"] == 1 and compute["keep_sparse_empty"] is False


def test_click_on_a_cross_restores_it(qapp, mw, ops):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.tolist() == [OUT_A]
    # the view now fits the smaller grid; zoom out to see the cross of the excluded outlier, then click it
    w._plot.setRange(xRange=(-45, 55), yRange=(-5, 65), padding=0)
    _click(qapp, w, 50.0, 50.0)  # the cross
    assert w._exclude.rows.size == 0
    assert w._result.n_user_excluded == 0 and "excluded" not in w._map_items
    assert [k["kind"] for n, k in ops if n == "xy_map_exclude"] == ["click_exclude", "click_restore"]


def test_click_far_from_any_point_does_nothing(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    before = w._result
    _click(qapp, w, 30.0, -20.0)  # empty space
    assert w._exclude.rows.size == 0 and w._result is before


def test_click_with_the_mode_off_excludes_nothing(qapp, mw):
    w = _open(qapp, mw)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.size == 0


def test_rectangle_drag_excludes_all_rows_in_the_value_range_even_if_not_drawn(qapp, mw, monkeypatch, ops):
    monkeypatch.setattr(xy_mod, "XY_SCATTER_LIMIT", 300)  # the points are thinned: most rows are not drawn
    w = _open(qapp, mw)
    assert w._result.thinned is not None
    px, py, _ = _data()
    want = np.flatnonzero((px >= 4) & (px <= 6) & (py >= 4) & (py <= 6))
    drawn = set(w._result.point_rows[0].tolist())
    assert len(want) > 50 and len(set(want.tolist()) - drawn) > 0  # some selected rows are not drawn points
    _turn_on(w)
    _drag(qapp, w, (3.99, 3.99), (6.01, 6.01))
    # the rectangle is quantised to pixels (~0.1 unit here): everything clearly inside is out, nothing clearly outside
    got = set(w._exclude.rows.tolist())
    inner = np.flatnonzero((px >= 4.3) & (px <= 5.7) & (py >= 4.3) & (py <= 5.7))
    outer = np.flatnonzero((px >= 3.7) & (px <= 6.3) & (py >= 3.7) & (py <= 6.3))
    assert set(inner.tolist()) <= got <= set(outer.tolist()) and len(inner) > 50
    assert len(got - drawn) > 0  # rows that were not drawn points were selected too
    assert w._result.n_user_excluded == len(got)
    assert any(k["kind"] == "rect" for n, k in ops if n == "xy_map_exclude")


def test_rectangle_drag_does_not_pan_and_off_mode_drag_pans(qapp, mw):
    w = _open(qapp, mw)
    vb = w._plot.vb
    xr0 = vb.viewRange()[0]
    _turn_on(w)
    _drag(qapp, w, (3.0, 3.0), (5.0, 5.0))
    assert vb.viewRange()[0] == pytest.approx(xr0)  # no pan
    w._exclude.mode_button.setChecked(False)
    rows_before = w._exclude.rows.copy()
    xr1 = vb.viewRange()[0]
    _drag(qapp, w, (3.0, 3.0), (5.0, 5.0))
    xr2 = vb.viewRange()[0]
    assert xr2[0] != pytest.approx(xr1[0])  # the drag panned the view
    np.testing.assert_array_equal(w._exclude.rows, rows_before)


def test_rectangle_around_nothing_reports_it(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    before = w._result
    _drag(qapp, w, (20.0, 20.0), (25.0, 25.0))
    assert w._exclude.rows.size == 0 and w._result is before
    assert xm.tr("xy.exclude_none_in_rect") in w.status_text()


def test_reset_restores_everything(qapp, mw, ops):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    _click(qapp, w, -40.0, 60.0)
    assert w._exclude.rows.tolist() == [OUT_A, OUT_B]
    w._exclude.reset_button.click()
    _wait(qapp, w)
    assert w._exclude.rows.size == 0 and w._result.n_user_excluded == 0
    assert not w._exclude.reset_button.isEnabled()
    assert w._result.x_auto[1] >= 50
    reset = [k for n, k in ops if n == "xy_map_exclude" and k["kind"] == "reset"]
    assert reset and reset[0]["changed"] == 2


def test_excluding_shrinks_the_automatic_range_in_the_settings(qapp, mw):
    w = _open(qapp, mw)
    hi_before = float(w._x_grid.hi_edit.text())
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    _click(qapp, w, -40.0, 60.0)
    assert float(w._x_grid.hi_edit.text()) < hi_before and float(w._x_grid.hi_edit.text()) < 12


def test_changing_the_variables_clears_the_excluded_rows(qapp, mw, ops):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.size == 1
    w._y_picker.set_selected(["pz"])
    _wait(qapp, w)
    assert w._exclude.rows.size == 0 and w._result.n_user_excluded == 0
    assert xm.tr("xy.exclude_cleared") in w.status_text()
    assert any(k["kind"] == "cleared" for n, k in ops if n == "xy_map_exclude")
    # the notice is shown once
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    assert xm.tr("xy.exclude_cleared") not in w.status_text()


def test_grid_aggregate_and_fill_changes_keep_the_excluded_rows(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    for combo, key in ((w._agg_combo, "max"), (w._fill_combo, "nearest")):
        combo.setCurrentIndex(combo.findData(key))
        _wait(qapp, w)
        assert w._exclude.rows.tolist() == [OUT_A]
    w._min_count_spin.setValue(2)
    _wait(qapp, w)
    assert w._exclude.rows.tolist() == [OUT_A] and w._result.n_user_excluded == 1
    w._x_grid.lo_edit.setText("0")
    w._x_grid.hi_edit.setText("10")
    w._x_grid.value_edit.setText("1")
    w._x_grid._mark_touched()
    w._x_grid._on_edit_finished()
    _wait(qapp, w)
    assert w._exclude.rows.tolist() == [OUT_A]
    w._exclude.mode_button.setChecked(False)
    assert w._exclude.rows.tolist() == [OUT_A]  # leaving the mode keeps them


def test_curve_click_excludes_the_row_from_all_curves(qapp, mw):
    w = _open(qapp, mw, x="px", ys=("py", "pz"), color="single")
    r = w._result
    assert r.is_curve
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)  # outlier A of the py curve
    assert w._exclude.rows.tolist() == [OUT_A]
    r = w._result
    assert all(OUT_A not in pr for pr in r.point_rows)
    assert [er.tolist() for er in r.excluded_point_rows] == [[OUT_A], [OUT_A]]


def test_hidden_curve_points_cannot_be_picked(qapp, mw):
    w = _open(qapp, mw, x="px", ys=("py", "pz"), color="single")
    _turn_on(w)
    w.set_series_hidden("py", True)
    w.set_series_hidden("pz", True)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.size == 0


def test_3d_disables_the_mode_but_applies_excluded_rows(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.tolist() == [OUT_A]
    if not w._gl3d_ok:  # no OpenGL here: check what the window offers without it
        pytest.skip("OpenGL is not available")
    w._set_mode("3d")
    _wait(qapp, w)
    assert not w._exclude.mode_button.isEnabled() and not w._exclude.mode_button.isChecked()
    assert w._exclude.mode_button.toolTip() == xm.tr("xy.exclude_mode_3d")
    assert w._exclude.reset_button.isEnabled()
    assert w._exclude.rows.tolist() == [OUT_A]  # the 2D/3D switch keeps them
    r = w._result
    assert r.is_3d and r.n_user_excluded == 1 and r.n_valid == N - 1


def test_mode_button_disabled_in_3d_state(qapp, mw):
    """The 3D branch of the state logic without needing OpenGL."""
    w = _open(qapp, mw)
    _turn_on(w)
    w._mode = "3d"
    w._exclude.update_buttons()
    assert not w._exclude.mode_button.isEnabled() and not w._exclude.mode_button.isChecked()
    assert not w._exclude.active()
    w._mode = "2d"
    w._exclude.update_buttons()
    assert w._exclude.mode_button.isEnabled()


def test_in_flight_rectangle_is_dropped_when_the_set_changes(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    gate_started = []
    real = xe.rows_in_rect

    def slow(*a, **k):
        gate_started.append(1)
        time.sleep(0.2)
        return real(*a, **k)

    xe.rows_in_rect, saved = slow, real
    try:
        w._exclude.on_rect(3.0, 3.0, 6.0, 6.0)
        assert w.is_computing()
        w._exclude.reset()  # nothing to reset: no change, so the job survives
        w._exclude._set_rows(np.array([OUT_B], dtype=np.int64))  # a change of the set: the job is stale
        _wait(qapp, w)
    finally:
        xe.rows_in_rect = saved
    assert w._exclude.rows.tolist() == [OUT_B]  # the stale rectangle result was dropped


def test_closing_with_a_selection_in_flight_is_safe(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    w._exclude.on_rect(3.0, 3.0, 6.0, 6.0)
    w.close()
    _drain(qapp, 0.3)


# -- keep sparse empty (UI) ------------------------------------------------------------------


def test_keep_sparse_checkbox_state_and_effect(qapp, mw, ops):
    w = _open(qapp, mw, fill="smooth")
    chk = w._keep_sparse_check
    assert not chk.isChecked() and chk.isEnabled()
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("none"))
    assert not chk.isEnabled()
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("smooth"))
    assert chk.isEnabled()
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("count"))
    assert not chk.isEnabled()
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("mean"))
    _wait(qapp, w)
    w._min_count_spin.setValue(8)
    _wait(qapp, w)
    off = w._result.grids[0]
    chk.setChecked(True)
    _wait(qapp, w)
    on = w._result.grids[0]
    assert w._result.keep_sparse_empty and on.sparse_mask.any()
    assert off.filled_mask[on.sparse_mask].all() and not on.filled_mask[on.sparse_mask].any()
    assert np.isnan(on.value[on.sparse_mask]).all()
    assert [k for n, k in ops if n == "xy_plot_compute"][-1]["keep_sparse_empty"] is True
    # disabled again: the stale checked state must not leak into the computation
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("none"))
    _wait(qapp, w)
    assert not w._result.keep_sparse_empty


# -- flag off: no exclusion UI (44.13 (8)) ----------------------------------------------------


def test_flag_off_hides_the_exclusion_ui_and_keep_sparse(qapp, mw, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "map_exclude_points", False)
    w = _open(qapp, mw, fill="smooth")
    assert w._map_enabled and w._result is not None  # a map is displayed
    assert w._exclude.mode_button is None and w._exclude.reset_button is None and w._exclude.group is None
    assert not w._exclude.active()
    chk = w._keep_sparse_check
    assert not chk.isChecked() and chk.parentWidget() is None and not chk.isVisible()
    assert chk not in w._map_groups
    texts = [b.text() for b in w.findChildren(QPushButton)]
    assert all(t not in (tr("xy.exclude_mode"), tr("xy.exclude_reset")) for t in texts)
    assert not any(c.text() == tr("xy.map_keep_sparse") and c.isVisible() for c in w.findChildren(QCheckBox))


def test_flag_off_map_request_keeps_no_sparse_and_no_excluded_rows(qapp, mw, monkeypatch, ops):
    monkeypatch.setitem(features._EFFECTIVE, "map_exclude_points", False)
    w = _open(qapp, mw, fill="smooth")
    _wait(qapp, w)
    assert w._result.keep_sparse_empty is False
    assert w._result.n_user_excluded == 0
    assert w._exclude.rows.size == 0
    assert [k for n, k in ops if n == "xy_plot_compute"][-1]["keep_sparse_empty"] is False


def test_flag_off_left_drag_pans_and_excludes_nothing(qapp, mw, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "map_exclude_points", False)
    w = _open(qapp, mw)
    vb = w._plot.vb
    xr0 = vb.viewRange()[0]
    _drag(qapp, w, (3.0, 3.0), (5.0, 5.0))
    assert vb.viewRange()[0][0] != pytest.approx(xr0[0])  # the drag panned the view
    assert w._exclude.rows.size == 0


# -- export and image ------------------------------------------------------------------------


def test_export_origin_and_values_reflect_exclusion_and_sparse_cells(qapp, mw):
    w = _open(qapp, mw, fill="smooth")
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    w._min_count_spin.setValue(8)
    w._keep_sparse_check.setChecked(True)
    _wait(qapp, w)
    r = w._result
    origin = med.origin_info(mw, r, 0)
    assert origin["excluded_rows"] == 1 and origin["keep_sparse_empty"] is True
    assert origin["agg"] == "mean" and origin["fill"] == "smooth"
    table, _filled = med.table_from_result(r, 0, "m", "linear", "clamp")
    assert np.isnan(table.z[r.grids[0].sparse_mask]).all()  # the sparse cells stay empty in the map
    assert med.empty_note_text(r)  # the "empty cells become NaN" note appears for them
    assert med.perform_export(w, r, med.ExportValues(name="m_ex", register=True, save_csv=False, csv_path="",
                                                     interpolation="linear", extrapolation="clamp"))
    from parquet_analyzer.io import maps as maps_io

    info = next(i for i in maps_io.list_maps() if i.name == "m_ex")
    assert info.origin["excluded_rows"] == 1 and info.origin["keep_sparse_empty"] is True


def test_image_export_draws_crosses_and_caption(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    fig = w.build_export_figure(["stamp"], (700, 500))
    ax = fig.axes[0]
    crosses = [c for c in ax.collections if c.get_paths() and getattr(c, "get_offsets", None)
               and len(c.get_offsets()) == 1 and tuple(c.get_offsets()[0]) == (50.0, 50.0)]
    assert crosses
    assert xm.tr("xy.map_stamp_excluded", n=1) in w._result.describe_grid()
    w._exclude.reset_button.click()
    _wait(qapp, w)
    assert xm.tr("xy.map_stamp_excluded", n=1) not in w._result.describe_grid()
    fig = w.build_export_figure(["stamp"], (700, 500))
    assert not any(len(c.get_offsets()) == 1 and tuple(c.get_offsets()[0]) == (50.0, 50.0)
                   for c in fig.axes[0].collections if hasattr(c, "get_offsets"))


# -- review fixes (44.13 (7)) --------------------------------------------------------------


def _slow_rect(monkeypatch, delay=0.3):
    real = xe.rows_in_rect

    def slow(*a, **k):
        time.sleep(delay)
        return real(*a, **k)

    monkeypatch.setattr(xe, "rows_in_rect", slow)


@pytest.mark.parametrize("change", ["z", "y", "x"])
def test_rectangle_in_flight_is_dropped_when_a_variable_changes_even_with_nothing_excluded(
        qapp, mw, monkeypatch, change):
    _slow_rect(monkeypatch)
    w = _open(qapp, mw)
    _turn_on(w)
    assert w._exclude.rows.size == 0
    w._exclude.on_rect(3.0, 3.0, 6.0, 6.0)
    assert w.is_computing()
    if change == "z":
        w._z_combo.setCurrentText("px")
    elif change == "y":
        w._y_picker.set_selected(["pz"])
    else:
        w._x_combo.setCurrentText("pz")
    _wait(qapp, w)
    assert w._exclude.rows.size == 0
    assert w._result.n_user_excluded == 0


def test_clicks_and_rectangles_are_refused_while_the_result_is_stale(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    w._x_combo.blockSignals(True)  # the combo moved on, the recompute has not delivered yet
    w._x_combo.setCurrentText("pz")
    w._x_combo.blockSignals(False)
    assert not w._exclude.result_is_current()
    w._exclude.on_click(50.0, 50.0)
    w._exclude.on_rect(-100, -100, 100, 100)
    assert w._exclude.rows.size == 0 and not w.is_computing()


def test_view_range_is_kept_after_a_click_and_reset_refits(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    fitted = list(w._plot.vb.viewRange()[0])
    w._plot.setRange(xRange=(-44, 54), yRange=(-4, 64), padding=0)
    odd = list(w._plot.vb.viewRange()[0])
    assert odd != pytest.approx(fitted)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.tolist() == [OUT_A]
    assert list(w._plot.vb.viewRange()[0]) == pytest.approx(odd)  # kept; the cross is still on screen
    _click(qapp, w, 50.0, 50.0)  # the cross: restore it, no zooming out needed
    assert w._exclude.rows.size == 0
    assert list(w._plot.vb.viewRange()[0]) == pytest.approx(odd)
    _click(qapp, w, 50.0, 50.0)
    w._exclude.reset_button.click()  # "restore all" re-fits to the grid
    _wait(qapp, w)
    assert list(w._plot.vb.viewRange()[0]) == pytest.approx(fitted)


def test_a_settings_recompute_after_a_click_refits(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    assert w._plot.vb.viewRange()[0][1] > 45  # kept wide
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("max"))
    _wait(qapp, w)
    assert w._plot.vb.viewRange()[0][1] < 15  # fitted to the (now small) grid


def test_cleared_notice_is_shown_once(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    w._y_picker.set_selected(["pz"])
    _wait(qapp, w)
    assert xm.tr("xy.exclude_cleared") in w.status_text()
    _click(qapp, w, 5.0, 5.0)  # any next recompute
    assert xm.tr("xy.exclude_cleared") not in w.status_text()


def test_mode_toggle_is_logged(qapp, mw, ops):
    w = _open(qapp, mw)
    _turn_on(w)
    w._exclude.mode_button.setChecked(False)
    assert [k["enabled"] for n, k in ops if n == "xy_map_exclude_mode"] == [True, False]


def test_export_note_for_kept_sparse_cells_names_the_option(qapp, mw):
    w = _open(qapp, mw, fill="smooth")
    w._min_count_spin.setValue(8)
    w._keep_sparse_check.setChecked(True)
    _wait(qapp, w)
    note = med.empty_note_text(w._result)
    assert note == xm.tr("xy.export_empty_note_sparse", percent=int(note.split("%")[0]))
    assert xm.tr("xy.map_keep_sparse") in note
    w._keep_sparse_check.setChecked(False)
    w._fill_combo.setCurrentIndex(w._fill_combo.findData("none"))
    _wait(qapp, w)
    assert xm.tr("xy.map_keep_sparse") not in med.empty_note_text(w._result)


def test_rectangle_union_is_made_in_the_worker_and_shows_selecting(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    seen = []
    real = np.union1d
    import threading

    main_thread = threading.current_thread()
    monkeypatch.setattr(xe.np, "union1d", lambda a, b: (seen.append(threading.current_thread() is main_thread), real(a, b))[1])
    w._exclude.on_rect(-45, -5, 60, 65)  # everything
    assert xm.tr("xy.exclude_selecting") in w.status_text()
    _wait(qapp, w)
    assert w._exclude.rows.size == N
    assert seen and not any(seen)  # never on the UI thread


def test_all_rows_excluded_still_draws_crosses_that_can_be_clicked_back(qapp, mw):
    w = _open(qapp, mw)
    _turn_on(w)
    w._exclude._apply(np.arange(N, dtype=np.int64))
    _wait(qapp, w)
    r = w._result
    assert r.n_valid == 0 and r.n_user_excluded == N and len(r.excluded_point_rows[0]) == N
    assert any(isinstance(i, xe.PassiveScatter) for i in w._plot.items)
    w._exclude.mode_button.setChecked(True)
    assert w._exclude.active()


def test_excluded_crosses_are_read_even_when_nothing_is_valid():
    r = _compute(np.arange(N))
    assert r.n_valid == 0 and len(r.excluded_point_rows[0]) == N


def test_stale_cross_click_does_nothing(qapp, mw, ops):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    w._exclude.rows = np.empty(0, dtype=np.int64)  # the row is back, the drawing is still the old one
    n_ops = len(ops)
    w._exclude.on_click(50.0, 50.0)
    assert not w.is_computing() and len(ops) == n_ops


def test_excluded_rows_survive_a_change_of_the_row_range(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)
    assert w._exclude.rows.tolist() == [OUT_A]
    tv = mw._time_values
    monkeypatch.setattr(mw, "analysis_range", lambda: (0, 5, float(tv[0]), float(tv[4])))  # excludes row 10
    w.recompute()
    _wait(qapp, w)
    assert w._exclude.rows.tolist() == [OUT_A] and w._result.n_user_excluded == 0
    monkeypatch.setattr(mw, "analysis_range", lambda: (0, N, float(tv[0]), float(tv[-1])))
    w.recompute()
    _wait(qapp, w)
    assert w._result.n_user_excluded == 1


# -- aggregate "time" with excluded rows (44.14) -----------------------------------------------

TOTAL_S = (N - 1) * 0.1  # 100 ms rows; the last row of the range weighs 0


def test_time_map_click_excludes_a_row_and_its_time(qapp, mw):
    w = _open(qapp, mw, agg="time")
    r0 = w._result
    assert r0.is_time and r0.time_total * 3600 == pytest.approx(TOTAL_S)
    _turn_on(w)
    _click(qapp, w, 50.0, 50.0)  # the outlier A, through the real mouse routing
    assert w._exclude.rows.tolist() == [OUT_A]
    r = w._result
    assert r is not r0 and r.is_time and r.n_user_excluded == 1 and r.n_valid == N - 1
    assert r.time_total * 3600 == pytest.approx(TOTAL_S - 0.1)  # only the row's own 0.1 s is gone


def test_time_curve_with_excluded_rows_has_no_crosses_and_no_crash(qapp, mw, tmp_path):
    w = _open(qapp, mw, color="single")
    w._exclude._set_rows(np.arange(100, 200, dtype=np.int64))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("time"))
    _wait(qapp, w)
    r = w._result
    assert r.is_curve and r.is_time and r.n_user_excluded == 100 and r.excluded_points == []
    assert w._exclude.rows.size == 100  # still excluded (and left out of the time); "Restore all" works
    assert r.time_total * 3600 == pytest.approx(TOTAL_S - 10.0)
    assert not w._exclude.mode_button.isEnabled()
    assert w._exclude.mode_button.toolTip() == tr("xy.exclude_mode_time_curve")
    assert w._exclude.reset_button.isEnabled()
    assert "no longer available" not in w.status_text()
    out = tmp_path / "curve.png"
    assert w.export_image(str(out)) and out.stat().st_size > 1000
    w._exclude.mode_button.setChecked(True)  # a disabled button can still be set by code: still not active
    assert not w._exclude.active()
    w._exclude.mode_button.setChecked(False)
    w._exclude.reset()
    _wait(qapp, w)
    assert w._exclude.rows.size == 0 and w._result.time_total * 3600 == pytest.approx(TOTAL_S)


def test_time_map_keeps_excluded_rows_when_z_changes(qapp, mw):
    w = _open(qapp, mw)
    w._exclude._set_rows(np.array([OUT_A], dtype=np.int64))
    w._agg_combo.setCurrentIndex(w._agg_combo.findData("time"))
    _wait(qapp, w)
    assert w._exclude.rows.tolist() == [OUT_A]
    first = w._result
    w._z_combo.setCurrentText("px")  # not used by the time: neither clears the rows nor recomputes
    _drain(qapp)
    assert w._exclude.rows.tolist() == [OUT_A] and w._result is first and not w.is_computing()


def test_time_refused_still_reads_the_crosses_of_a_map():
    px, py, _pz = _data()
    t = np.arange(N, dtype=float)
    t[500] = -1.0  # not ascending
    cols = {"px": px, "py": py}
    req = ("count", None)
    r = xm.compute_xy_map(
        lambda n, a, b: cols[n][a:b], "px", ["py"], None, "map", req, req, "time", 1, "none", 0, N,
        chunk_rows=1000, scatter_limit=1000, excluded_rows=np.array([OUT_A, 5]), fetch_time=lambda a, b: t[a:b])
    assert r.time_refused == "time_not_ascending" and not r.grids
    assert len(r.excluded_points) == 1 and len(r.excluded_points[0][0]) == 2
