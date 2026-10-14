"""detailed_specification.md 36.6 stage D: TimeIndex (core/time_index.py) and its wiring in
MainWindow behind the `big_d_time_index` flag."""
from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import features
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.numeric import check_time_order, to_numeric
from parquet_analyzer.core.time_index import ANCHOR_ROWS, TimeIndex
from parquet_analyzer.ui.main_window import MainWindow

N = 60_000


def _times(n=N, seed=0):
    """Ascending float64 with duplicate runs, a gap and several anchor intervals."""
    rng = np.random.default_rng(seed)
    steps = rng.choice([0.0, 0.0, 0.01, 0.02, 0.5], size=n)
    steps[n // 3 : n // 3 + 5] = 0.0  # a flat run
    steps[n // 2] = 300.0  # a gap
    return 1_767_225_600.0 + np.cumsum(steps)


def _write(tmp_path, t, row_group_size=7000, name="f.parquet", extra=None):
    cols = {"time": t, "s": np.sin(np.arange(len(t)) / 100.0)}
    cols.update(extra or {})
    path = tmp_path / name
    pq.write_table(pa.table(cols), path, row_group_size=row_group_size)
    return str(path)


def _index(path, column="time"):
    ds = ParquetDataSource(path)
    idx = TimeIndex.build(ds, column, pin_rows=[o for o, _n in ds.row_groups()])
    assert idx is not None
    return idx


# --- core equivalence -------------------------------------------------------------------

@pytest.mark.parametrize("rg", [1000, 7000, 60_000])
def test_searchsorted_matches_numpy(tmp_path, rg):
    t = _times()
    idx = _index(_write(tmp_path, t, rg))
    assert len(idx) == len(t)
    rng = np.random.default_rng(1)
    lo, hi = t[0] - 5, t[-1] + 5
    probes = list(rng.uniform(lo, hi, 300))
    probes += list(rng.choice(t, 300))  # exact hits, incl. duplicates
    probes += [t[0], t[-1], lo, hi, t[0] - 1e-9, t[-1] + 1e-9, float("inf"), float("-inf"), float("nan")]
    anchors = [t[k * ANCHOR_ROWS] for k in range(len(t) // ANCHOR_ROWS)]
    probes += anchors + [np.nextafter(a, -np.inf) for a in anchors] + [np.nextafter(a, np.inf) for a in anchors]
    for side in ("left", "right"):
        for v in probes:
            assert int(np.searchsorted(idx, v, side=side)) == int(np.searchsorted(t, v, side=side)), (v, side)
    arr = np.array(probes[:50])
    assert list(idx.searchsorted(arr, "right")) == list(np.searchsorted(t, arr, "right"))


def test_searchsorted_all_equal_and_tiny(tmp_path):
    for t in (np.full(20_000, 5.0), np.array([1.0]), np.array([1.0, 1.0, 2.0])):
        idx = _index(_write(tmp_path, t, 5000, name=f"e{len(t)}.parquet"))
        for v in (0.0, 1.0, 1.5, 2.0, 5.0, 6.0):
            for side in ("left", "right"):
                assert int(idx.searchsorted(v, side)) == int(np.searchsorted(t, v, side=side))


def test_value_slice_take_bounds(tmp_path):
    t = _times()
    idx = _index(_write(tmp_path, t, 7000))
    rng = np.random.default_rng(2)
    for r in list(rng.integers(0, len(t), 100)) + [0, len(t) - 1, ANCHOR_ROWS, 7000, 14000]:
        assert idx[int(r)] == t[r]
        assert idx.value_at(int(r) - len(t)) == t[int(r) - len(t)]
    assert np.array_equal(idx[100:9000], t[100:9000])
    assert np.array_equal(idx[:], t)
    assert np.array_equal(idx[len(t) - 5 :], t[-5:])
    assert np.array_equal(idx[5:50:7], t[5:50:7])
    rows = rng.integers(0, len(t), 200)
    assert np.array_equal(idx[rows], t[rows])
    assert np.array_equal(np.asarray(idx), t)
    assert idx.finite_min_max() == (t[0], t[-1]) and idx.min() == t[0] and idx.max() == t[-1]
    with pytest.raises(IndexError):
        idx[len(t)]
    ar = idx.anchor_rows()
    assert np.array_equal(idx.approx_take(ar), t[ar])  # exact at anchors
    assert np.all(np.diff(idx.approx_take(rows[np.argsort(rows)])) >= 0)


def test_build_refuses_unordered_and_nonfinite(tmp_path):
    t = _times()
    bad = t.copy()
    bad[40_000], bad[40_001] = bad[40_001] + 10, bad[40_000] - 10  # decreasing
    nan = t.copy()
    nan[10] = np.nan
    for i, arr in enumerate((bad, nan, np.array([], dtype=np.float64))):
        ds = ParquetDataSource(_write(tmp_path, arr, 7000, name=f"b{i}.parquet"))
        assert TimeIndex.build(ds, "time") is None
    assert check_time_order(bad).decreasing_count > 0


def test_build_chunk_boundaries_and_datetime(tmp_path):
    n = 30_000
    t = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    path = tmp_path / "dt.parquet"
    pq.write_table(pa.table({"time": pa.array(t), "s": np.zeros(n)}), path, row_group_size=4000)
    idx = TimeIndex.build(ParquetDataSource(path), "time", chunk_rows=5000)  # chunk not a multiple of the anchor step
    full = to_numeric(t)
    assert np.array_equal(np.asarray(idx), full)
    for v in (full[0], full[12345], full[-1] + 1, full[0] - 1):
        for side in ("left", "right"):
            assert int(np.searchsorted(idx, v, side=side)) == int(np.searchsorted(full, v, side=side))


# --- MainWindow end to end with the flag on ---------------------------------------------

def _wait(qapp, win):
    for _ in range(600):
        qapp.processEvents()
        if not win._busy_sources and not any(p._redraw_debounce.isActive() for p in win.plot_grid.plots):
            break
        time.sleep(0.005)
    qapp.processEvents()


def _open(monkeypatch, path, flag=True):
    monkeypatch.setitem(features._EFFECTIVE, "big_d_time_index", flag)
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0  # everything is "big"
    assert win.load_parquet(path)
    return win


@pytest.fixture
def big(tmp_path):
    t = _times()
    sec = np.sort(np.random.default_rng(3).uniform(0, 1000, len(t)))  # another ascending column
    dec = t[::-1].copy()
    return _write(tmp_path, t, 7000, extra={"sec": sec, "dec": dec}), t, sec


def test_flag_on_uses_index_and_off_does_not(qapp, isolated_config, monkeypatch, big):
    path, _t, _sec = big
    win = _open(monkeypatch, path, flag=True)
    assert isinstance(win._time_values, TimeIndex)
    assert not win._time_order_label.isVisible()
    win.close()
    win = _open(monkeypatch, path, flag=False)
    assert isinstance(win._time_values, np.ndarray)
    win.close()


def test_under_threshold_stays_resident(qapp, isolated_config, monkeypatch, big):
    monkeypatch.setitem(features._EFFECTIVE, "big_d_time_index", True)
    win = MainWindow()
    assert win.load_parquet(big[0])  # default 512 MB limit: small file
    assert isinstance(win._time_values, np.ndarray)
    win.close()


def test_end_to_end_plot_zoom_stats_cursor_hover_navigator(qapp, isolated_config, monkeypatch, big):
    path, t, _sec = big
    win = _open(monkeypatch, path)
    win.resize(1000, 700)
    win.show()
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "s")
    _wait(qapp, win)
    s = np.sin(np.arange(len(t)) / 100.0)
    vb = plot.getViewBox()
    assert vb.viewRange()[0][0] <= t[0] and vb.viewRange()[0][1] >= t[-1]  # extent without reading the axis
    r = win.navigator._region.getRegion()
    assert r[0] == pytest.approx(t[0], abs=1e-3) and r[1] == pytest.approx(t[-1], abs=1e-3)
    # zoom: the row range and the drawn curve match the resident computation
    vb.setXRange(t[20_000], t[20_400], padding=0)
    _wait(qapp, win)
    lo, hi = vb.viewRange()[0]
    rs, re = win._visible_row_range()
    assert (rs, re) == (int(np.searchsorted(t, lo, "left")), int(np.searchsorted(t, hi, "right")))
    cx, cy = plot._series["s"]["curve"].getData()
    in_view = (t >= lo) & (t <= hi)
    assert len(cx) >= in_view.sum() and np.isin(cx[(cx >= lo) & (cx <= hi)], t[in_view]).all()
    assert win.navigator.selected_range()[0] == pytest.approx(lo, abs=1e-3)
    # stats range
    captured = {}
    monkeypatch.setattr(win, "_show_stats_dialog", lambda batch: captured.update(batch))
    win._on_stats_requested()
    for _ in range(200):
        qapp.processEvents()
        if captured:
            break
        time.sleep(0.005)
    assert captured["results"]["s"]["mean"] == pytest.approx(float(np.mean(s[rs:re])))
    assert str(re - rs) in captured["range_label"].replace(",", "")
    # cursor and hover read the nearest row through the index
    xc = float(t[20_100]) + 0.0001
    plot.add_cursor("c1", xc)
    row = plot._nearest_row(t, xc)  # duplicates in t make argmin ambiguous; compare with the resident answer
    assert plot._nearest_row(win._time_values, xc) == plot._nearest_row(t, xc)
    assert plot._series_value_at(plot._series["s"], xc) == pytest.approx(float(s[row]))
    plot.set_hover_enabled(True)
    plot.set_hover_x(xc)
    assert f"{s[row]:.6g}" in plot.hover_text()
    # go_home restores the full extent from the index
    plot.go_home()
    _wait(qapp, win)
    lo, hi = vb.viewRange()[0]
    assert lo <= t[0] and hi >= t[-1]
    assert win._visible_row_range() == (0, len(t))
    win.close()


def test_derived_series_with_lazy_axis(qapp, isolated_config, monkeypatch, big):
    path, t, _sec = big
    win = _open(monkeypatch, path)
    win._on_add_derived_variable("d", "s * 2")
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "d")
    _wait(qapp, win)
    cx, cy = plot._series["d"]["curve"].getData()
    assert len(cx) > 0 and len(cx) == len(cy) and np.isfinite(cx).all()
    plot.getViewBox().setXRange(t[100], t[300], padding=0)
    _wait(qapp, win)
    cx, cy = plot._series["d"]["curve"].getData()
    assert 150 < len(cx) <= 400
    assert len(cy) == len(cx)
    win.close()


def test_time_switch_and_fallback(qapp, isolated_config, monkeypatch, big):
    path, t, sec = big
    win = _open(monkeypatch, path)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "s")
    _wait(qapp, win)
    win.set_time_column("sec")  # another ascending column: a new index
    _wait(qapp, win)
    assert isinstance(win._time_values, TimeIndex) and win._time_values.column == "sec"
    assert win._time_values.finite_min_max() == (sec[0], sec[-1])
    assert "time" in win._raw_variables  # the old axis is an ordinary variable now
    lo, hi = plot.getViewBox().viewRange()[0]
    assert lo <= sec[0] and hi >= sec[-1]
    win.set_time_column("time")
    assert win._time_values.column == "time"
    win.set_time_column("dec")  # descending: cannot be indexed -> old resident path + warning
    assert isinstance(win._time_values, np.ndarray)
    assert not win._time_order_label.isHidden()
    win.set_time_column("time")
    assert isinstance(win._time_values, TimeIndex)
    assert win._time_order_label.isHidden()
    win.close()


def test_view_save_load_with_index(qapp, isolated_config, monkeypatch, big):
    from parquet_analyzer.io import view as view_io
    from parquet_analyzer.ui import main_window as mw

    path, t, sec = big
    win = _open(monkeypatch, path)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "s")
    win.set_time_column("sec")
    _wait(qapp, win)
    plot.getViewBox().setXRange(sec[1000], sec[2000], padding=0)
    _wait(qapp, win)
    monkeypatch.setattr(mw.QInputDialog, "getItem", lambda *a, **k: ("v1", True))
    monkeypatch.setattr(mw.messages, "information", lambda *a, **k: None)
    win.save_view_dialog()
    saved = view_io.load_view("v1")
    assert saved.time_column == "sec"
    win.set_time_column("time")
    win.load_view("v1")
    _wait(qapp, win)
    assert win._time_values.column == "sec"
    lo, hi = win.plot_grid.plots[0].getViewBox().viewRange()[0]
    assert lo == pytest.approx(sec[1000], abs=1e-6) and hi == pytest.approx(sec[2000], abs=1e-6)
    win.close()
