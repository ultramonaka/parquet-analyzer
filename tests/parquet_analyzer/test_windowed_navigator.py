"""detailed_specification.md ch.40: a windowed column's navigator overview spans the time
axis' finite min/max and has well over 2 points, for 1 / few / many row groups."""
from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.ui.main_window import MainWindow

N = 40_000


def _make(tmp_path, row_group_size):
    times = np.datetime64("2026-01-01", "ms") + np.arange(N, dtype="int64") * np.timedelta64(10, "ms")
    t = np.arange(N) / 1000.0
    path = tmp_path / "f.parquet"
    pq.write_table(pa.table({"time": pa.array(times), "s": np.sin(t)}), path, row_group_size=row_group_size)
    return str(path)


def _wait(qapp, win):
    for _ in range(400):
        qapp.processEvents()
        if not win._busy_sources and not win.plot_grid.plots[0]._redraw_debounce.isActive():
            break
        time.sleep(0.005)
    qapp.processEvents()


def _open_and_plot(qapp, tmp_path, rg_size):
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0
    win.load_parquet(_make(tmp_path, rg_size))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "s")
    _wait(qapp, win)
    return win, plot


# 1 row group / 10 (few: thinned read) / 400 (many: footer stats, >= 200)
@pytest.mark.parametrize("rg_size,min_points", [(N, 500), (N // 10, 500), (100, 100)])
def test_windowed_overview_spans_time_axis(qapp, isolated_config, tmp_path, rg_size, min_points):
    win, _plot = _open_and_plot(qapp, tmp_path, rg_size)
    nav = win.navigator
    lo, hi = float(win._time_values.min()), float(win._time_values.max())
    r = nav._region.getRegion()
    assert hi > lo
    assert len(nav._curve.xData) > min_points
    assert r[0] == pytest.approx(lo, abs=1e-3) and r[1] == pytest.approx(hi, abs=1e-3)
    win.close()


def test_region_follows_group_range_after_refinement(qapp, isolated_config, tmp_path):
    win, plot = _open_and_plot(qapp, tmp_path, N)
    lo, hi = float(win._time_values.min()), float(win._time_values.max())
    mid = (lo + hi) / 2
    plot.getViewBox().setXRange(lo, mid, padding=0)
    win._on_grid_layout_changed()
    _wait(qapp, win)
    assert win.navigator._region.getRegion()[1] == pytest.approx(mid, abs=1e-3)
    assert "navigator" not in win._busy_sources
    win.close()
