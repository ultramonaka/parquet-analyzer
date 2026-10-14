"""NavigatorSync / NavigatorWidget keep_region (plots 24.9; dev notes task 20).

1. NavigatorWidget alone: set_overview_data keeps/clamps/resets the region and never emits
   rangeSelected.
2. Every synchronous path leaves the navigator on the linked group's range.
3. Asynchronous refinements (windowed column, derived pyramid) keep it too; stale results are
   dropped; a failure leaves the coarse overview and clears busy.
4. Structure: set_overview_data is called only from NavigatorSync._apply.
5. A real navigator drag after those paths does not send the plots to the full range.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from waiting import wait_until

from parquet_analyzer import features
from parquet_analyzer.ui import navigator_sync as ns_module
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.navigator import NavigatorWidget

N = 4000


# ---------------------------------------------------------------- 1. widget alone


@pytest.fixture
def nav(qapp):
    w = NavigatorWidget()
    got: list[tuple[float, float]] = []
    w.rangeSelected.connect(lambda lo, hi: got.append((lo, hi)))
    w.got = got
    yield w
    w.close()


def _x(lo, hi, n=50):
    x = np.linspace(lo, hi, n)
    return x, np.sin(x)


def test_first_overview_is_full_extent(nav):
    nav.set_overview_data(*_x(0, 100))
    assert nav.selected_range() == pytest.approx((0, 100))
    assert nav.got == []


def test_default_keeps_region_and_home_resets(nav):
    nav.set_overview_data(*_x(0, 100))
    nav.set_selected_range(20, 40)
    nav.set_overview_data(*_x(0, 100))
    assert nav.selected_range() == pytest.approx((20, 40))
    nav.set_overview_data(*_x(0, 100), keep_region=False)
    assert nav.selected_range() == pytest.approx((0, 100))
    assert nav.got == []


def test_shrinking_bounds_clamp_region_without_rangeSelected(nav):
    nav.set_overview_data(*_x(0, 100))
    nav.set_selected_range(10, 90)
    nav.set_overview_data(*_x(0, 50))  # new bounds narrower than the region
    assert nav.selected_range() == pytest.approx((10, 50))
    nav.set_overview_data(*_x(20, 30))
    assert nav.selected_range() == pytest.approx((20, 30))
    assert nav.got == []  # the old code let setBounds emit sigRegionChanged here


def test_no_overlap_goes_to_full_extent(nav):
    nav.set_overview_data(*_x(0, 100))
    nav.set_selected_range(10, 20)
    nav.set_overview_data(*_x(500, 600))
    assert nav.selected_range() == pytest.approx((500, 600))
    assert nav.got == []


def test_no_finite_x_leaves_region_alone(nav):
    nav.set_overview_data(*_x(0, 100))
    nav.set_selected_range(10, 20)
    nav.set_overview_data(np.full(5, np.nan), np.arange(5.0))
    assert nav.selected_range() == pytest.approx((10, 20))
    assert nav.got == []


def test_user_drag_still_emits(nav):
    nav.set_overview_data(*_x(0, 100))
    nav._region.setRegion((10, 20))  # the real (unblocked) path a drag takes
    assert nav.got and nav.got[-1] == pytest.approx((10, 20))


# ---------------------------------------------------------------- window fixtures


def _write(path: Path, row_group_size=500):
    times = np.datetime64("2026-01-01", "ms") + np.arange(N, dtype="int64") * np.timedelta64(10, "ms")
    rng = np.random.default_rng(3)
    pq.write_table(
        pa.table(
            {
                "time": pa.array(times),
                "t2": pa.array(times + np.timedelta64(1, "h")),
                "a": rng.normal(size=N),
                "b": rng.normal(size=N) * 5,
            }
        ),
        path,
        row_group_size=row_group_size,
    )


def _open(tmp_path, limit=None):
    path = tmp_path / "s.parquet"
    _write(path)
    w = MainWindow()
    if limit is not None:
        w.settings.eager_load_limit_mb = limit
    assert w.load_parquet(str(path))
    return w


def _drain(w, timeout=15.0):
    from PySide6.QtWidgets import QApplication

    wait_until(lambda: not w._busy_sources, timeout)
    for _ in range(30):
        QApplication.processEvents()
    wait_until(lambda: not w._busy_sources, timeout)


def _zoom(w):
    plot = w.plot_grid.anchor_plot()
    lo, hi = plot.getViewBox().viewRange()[0]
    mid, q = (lo + hi) / 2, (hi - lo) / 20
    w.plot_grid.set_x_range(mid - q, mid + q)
    _drain(w)
    return plot.getViewBox().viewRange()[0]


def _synced(w, zoomed):
    _drain(w)
    group = w._group_x_range()
    assert group is not None
    assert (group[1], group[2]) == pytest.approx(tuple(zoomed), rel=0, abs=1e-3)
    assert w.navigator.selected_range() == pytest.approx(tuple(zoomed), rel=0, abs=1e-3)


def _full_extent(w):
    ox = w.navigator._curve.getData()[0]
    return (float(np.min(ox)), float(np.max(ox)))


@pytest.fixture
def win(qapp, isolated_config, tmp_path):
    w = _open(tmp_path)
    plot = w.plot_grid.plots[0]
    w._add_variable_to_plot(plot, "a")
    _drain(w)
    yield w
    w.close()


# ---------------------------------------------------------------- 2. synchronous paths


def test_series_add_remove_and_reorder_keep_group_range(win):
    z = _zoom(win)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "b")
    _synced(win, z)
    win.plot_grid.add_plot()
    _synced(win, z)
    win.plot_grid.remove_plot(win.plot_grid.plots[-1])
    _synced(win, z)
    plot.remove_series("b")
    win._on_grid_layout_changed()
    _synced(win, z)


def test_reorder_changing_the_anchor_keeps_group_range(win):
    win.plot_grid.add_plot()
    win._add_variable_to_plot(win.plot_grid.plots[1], "b")
    _drain(win)
    z = _zoom(win)
    second = win.plot_grid.plots[1]
    win.plot_grid.move_plot_to(second, 0)
    assert win.plot_grid.plots[0] is second  # the anchor changed
    _synced(win, z)


def test_link_toggle_keeps_group_range(win):
    win.plot_grid.add_plot()
    win._add_variable_to_plot(win.plot_grid.plots[1], "b")
    _drain(win)
    z = _zoom(win)
    assert win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], False)
    _synced(win, z)
    assert win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], True)
    _synced(win, z)


def test_derived_add_edit_delete_keep_group_range(win):
    plot = win.plot_grid.plots[0]
    win._on_add_derived_variable("d", "a * 2")
    win._add_variable_to_plot(plot, "d")
    z = _zoom(win)
    win.edit_derived_variable("d", "a * 3")
    _synced(win, z)
    win.delete_derived_variable("d")
    _synced(win, z)


def test_open_and_time_axis_change_go_home(win):
    _zoom(win)
    win.set_time_column("t2")
    _drain(win)
    assert win.navigator.selected_range() == pytest.approx(_full_extent(win), rel=0, abs=1e-3)
    z = _zoom(win)
    assert win.navigator.selected_range() == pytest.approx(tuple(z), rel=0, abs=1e-3)  # sync follows again
    win.load_parquet(str(win._data_source.path))
    _drain(win)
    assert win.navigator.selected_range() == pytest.approx(_full_extent(win), rel=0, abs=1e-3)


def test_time_axis_switch_region_is_never_zero_width_before_plots_follow(win):
    """set_time_column refreshes the navigator (home) BEFORE the plots get the new axis; the
    region must already be the new axis' full extent then, not the old group range clamped."""
    _zoom(win)
    seen = []
    orig = win.plot_grid.set_time_axis_data

    def spy(x):
        seen.append(win.navigator.selected_range())
        return orig(x)

    win.plot_grid.set_time_axis_data = spy
    win.set_time_column("t2")
    _drain(win)
    assert seen
    lo, hi = seen[0]
    assert hi - lo > 0
    assert (lo, hi) == pytest.approx(_full_extent(win), rel=0, abs=1e-3)


def test_refresh_emits_no_rangeSelected(win):
    got = []
    win.navigator.rangeSelected.connect(lambda lo, hi: got.append((lo, hi)))
    z = _zoom(win)
    win._nav_sync.refresh()
    win._nav_sync.refresh(home=True)
    _drain(win)
    assert got == []
    plot = win.plot_grid.anchor_plot()
    assert plot.getViewBox().viewRange()[0] == pytest.approx(tuple(z), rel=0, abs=1e-3)  # home on the navigator only


# ---------------------------------------------------------------- 3. asynchronous paths


@pytest.fixture
def wwin(qapp, isolated_config, tmp_path):
    """Windowed raw columns (eager limit 0), few row groups -> the thinned refinement runs."""
    w = _open(tmp_path, limit=0)
    plot = w.plot_grid.plots[0]
    w._on_variable_dropped(plot, "a")
    _drain(w)
    yield w
    w.close()


def test_windowed_refinement_keeps_group_range(wwin):
    from parquet_analyzer.core.column import WindowedColumn

    assert isinstance(wwin._raw_variables["a"], WindowedColumn)
    z = _zoom(wwin)
    got = []
    wwin.navigator.rangeSelected.connect(lambda lo, hi: got.append((lo, hi)))
    applied = []
    orig = wwin._nav_sync._apply
    wwin._nav_sync._apply = lambda x, y: (applied.append(len(x)), orig(x, y))[1]
    wwin._nav_sync.refresh()
    wwin.navigator.set_selected_range(z[0] - 1, z[1] + 1)  # displaced: only the re-sync can bring it back
    assert wait_until(lambda: len(applied) >= 2 and "navigator" not in wwin._busy_sources, 15)
    _synced(wwin, z)  # the refinement landed and the region is still the zoomed range
    assert got == []


def test_stale_refinement_is_dropped(wwin):
    z = _zoom(wwin)
    applied = []
    orig = wwin._nav_sync._apply
    wwin._nav_sync._apply = lambda x, y: (applied.append(1), orig(x, y))[1]
    wwin._nav_sync.refresh()
    wwin._nav_sync.refresh()  # the first job's result is now stale
    assert wait_until(lambda: "navigator" not in wwin._busy_sources, 15)
    time.sleep(0.2)  # a (wrongly) accepted stale result would arrive by now
    _drain(wwin)
    assert len(applied) == 3  # two synchronous coarse overviews + ONE refinement
    _synced(wwin, z)


def test_failed_refinement_keeps_coarse_overview_and_clears_busy(wwin, monkeypatch):
    def boom(path, name):
        raise RuntimeError("read failed")

    monkeypatch.setattr(ns_module, "thin_column", boom)
    z = _zoom(wwin)
    wwin._nav_sync.refresh()
    assert wait_until(lambda: "navigator" not in wwin._busy_sources, 15)
    assert len(wwin.navigator._curve.getData()[0]) > 0  # coarse overview stays
    _synced(wwin, z)


def test_derived_pyramid_arrival_keeps_group_range(qapp, isolated_config, tmp_path, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "big_c_windowed_derived", True)
    monkeypatch.setattr(ns_module, "_DERIVED_NAV_DIRECT_ROWS", 0)  # navigator waits for the pyramid
    w = _open(tmp_path, limit=0)
    try:
        w._on_add_derived_variable("d", "a + b")
        d = w._raw_variables["d"]
        w._on_variable_dropped(w.plot_grid.plots[0], "d")
        _drain(w)
        assert d.pyramid is not None
        d._pyramid = None  # force a fresh build for the navigator
        d._pyramid_building = False
        z = _zoom(w)
        got = []
        w.navigator.rangeSelected.connect(lambda lo, hi: got.append((lo, hi)))
        w._nav_sync.refresh()
        w.navigator.set_selected_range(z[0] - 1, z[1] + 1)  # displaced: only the re-sync can bring it back
        assert wait_until(lambda: d.pyramid is not None and "navigator" not in w._busy_sources, 15)
        _synced(w, z)
        assert got == []
    finally:
        w.close()


# ---------------------------------------------------------------- 4. structure


def test_set_overview_data_only_called_from_apply():
    import parquet_analyzer.ui.main_window as mw

    assert ".set_overview_data(" not in inspect.getsource(mw)
    src = inspect.getsource(ns_module)
    cls = ns_module.NavigatorSync
    for name, fn in inspect.getmembers(cls, inspect.isfunction):
        has = ".set_overview_data(" in inspect.getsource(fn)
        assert has == (name == "_apply"), name
    assert src.count(".set_overview_data(") == 1


# ---------------------------------------------------------------- 5. real drag


def _drag_region(w, qapp):
    """Drag the navigator window (a real mouse press/move/release on the viewport)."""
    nav = w.navigator
    nav.resize(800, 100)
    w.show()
    qapp.processEvents()
    lo, hi = nav.selected_range()
    mid = (lo + hi) / 2
    vb = nav.getViewBox()
    p0 = nav.mapFromScene(vb.mapViewToScene(QPointF(mid, 0)))
    vp = nav.viewport()
    y = nav.height() // 2
    start = p0
    start.setY(y)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for dx in (5, 15, 30):
        QTest.mouseMove(vp, start + QPointF(dx, 0).toPoint())
        qapp.processEvents()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPointF(30, 0).toPoint())
    qapp.processEvents()


@pytest.mark.parametrize("path_name", ["add_series", "derived", "time_axis", "link_toggle", "reorder"], ids=lambda s: s)
def test_drag_after_paths_does_not_jump_to_full_range(qapp, win, path_name):
    plot = win.plot_grid.plots[0]
    if path_name == "add_series":
        z = _zoom(win)
        win._add_variable_to_plot(plot, "b")
    elif path_name == "derived":
        win._on_add_derived_variable("d", "a * 2")
        win._add_variable_to_plot(plot, "d")
        z = _zoom(win)
        win.edit_derived_variable("d", "a * 3")
    elif path_name == "link_toggle":
        win.plot_grid.add_plot()
        win._add_variable_to_plot(win.plot_grid.plots[1], "b")
        _drain(win)
        z = _zoom(win)
        assert win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], False)
        assert win.plot_grid.set_plot_x_linked(win.plot_grid.plots[1], True)
    elif path_name == "reorder":
        win.plot_grid.add_plot()
        win._add_variable_to_plot(win.plot_grid.plots[1], "b")
        _drain(win)
        z = _zoom(win)
        win.plot_grid.move_plot_to(win.plot_grid.plots[1], 0)
        plot = win.plot_grid.anchor_plot()
    else:
        win.set_time_column("t2")
        _drain(win)
        z = _zoom(win)
        win._add_variable_to_plot(plot, "b")
    _drain(win)
    width = z[1] - z[0]
    full = _full_extent(win)
    _drag_region(win, qapp)
    _drain(win)
    got = plot.getViewBox().viewRange()[0]
    assert got[1] - got[0] == pytest.approx(width, rel=1e-3)  # still zoomed, not the full extent
    assert got[1] - got[0] < (full[1] - full[0]) / 2
    assert abs(got[0] - z[0]) > 0.01 * width  # the drag did move it
