"""Chapters 28 (background-task failure handling) and 29 (time-axis order check)
of the spec: detailed_specification.md 28.3 / 29.3, requirement 5.22."""
from __future__ import annotations

import logging
import time
import weakref

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QMessageBox, QTableWidget

from parquet_analyzer.core import column as column_module
from parquet_analyzer.core.background_worker import WorkerHub, _Task
from parquet_analyzer.core.column import MAX_PYRAMID_FAILURES, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core import numeric as numeric_module
from parquet_analyzer.core.numeric import check_time_order
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _wait(qapp, cond, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert cond(), "timed out"


def _settle(qapp, win):
    # let debounce timers fire, then wait for every background job to finish
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.01)
    _wait(qapp, lambda: not win._busy_sources)


# -- 28.1 WorkerHub (37.1) ------------------------------------------------------------


def _run_worker(qapp, func):
    hub = WorkerHub()
    events = []
    hub.submit(func, lambda r: events.append(("finished", r)), lambda m: events.append(("failed", m)))
    _wait(qapp, lambda: events)
    for _ in range(20):  # a second emission, if any, would arrive here
        qapp.processEvents()
        time.sleep(0.005)
    return events


def test_worker_success_emits_only_finished(qapp):
    assert _run_worker(qapp, lambda: 42) == [("finished", 42)]


def test_worker_exception_emits_only_failed_and_logs_traceback(qapp, caplog):
    def boom():
        raise ValueError("kaboom")

    with caplog.at_level(logging.ERROR):
        events = _run_worker(qapp, boom)
    assert events == [("failed", "ValueError: kaboom")]
    assert any(r.exc_info and "kaboom" in str(r.exc_info[1]) for r in caplog.records)


def test_worker_does_not_catch_keyboard_interrupt():
    def interrupt():
        raise KeyboardInterrupt()

    hub = WorkerHub()
    task = _Task(interrupt, weakref.ref(hub), 1, "task", None)
    with pytest.raises(KeyboardInterrupt):
        task()


# -- helpers: small parquet files -----------------------------------------------------


def _write(path, columns: dict, row_group_size=None):
    pq.write_table(pa.table(columns), path, row_group_size=row_group_size)
    return str(path)


def _windowed_win(tmp_path, n=2000):
    rng = np.random.default_rng(0)
    path = _write(
        tmp_path / "w.parquet",
        {"t": np.arange(n, dtype=float), "a": rng.normal(size=n), "b": rng.normal(size=n) * 10},
    )
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0  # every column windowed
    win.resize(1200, 900)
    win.show()
    assert win.load_parquet(path)
    return win


# -- 28.2 downsample ------------------------------------------------------------------


def test_downsample_failure_clears_busy_keeps_curve_and_recovers(qapp, isolated_config, tmp_path, monkeypatch):
    win = _windowed_win(tmp_path)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    _settle(qapp, win)
    failures = []
    plot.renderFailed.connect(lambda p, n, m: failures.append((n, m)))
    before = plot._series["a"]["curve"].getData()

    real_window = WindowedColumn.window

    def bad_window(self, s, e):
        raise OSError("disk gone")

    monkeypatch.setattr(WindowedColumn, "window", bad_window)
    plot.setXRange(500.0, 900.0, padding=0)
    _settle(qapp, win)

    assert failures and failures[0][0] == "a" and "disk gone" in failures[0][1]
    assert plot.pending_downsample_count() == 0
    assert not plot._active_workers
    assert "disk gone" in win._notice_label.text()
    after = plot._series["a"]["curve"].getData()
    assert np.array_equal(before[0], after[0]), "previous curve should be kept"

    monkeypatch.setattr(WindowedColumn, "window", real_window)
    plot.setXRange(100.0, 300.0, padding=0)
    _settle(qapp, win)
    redrawn = plot._series["a"]["curve"].getData()
    assert redrawn[0].min() >= 90 and redrawn[0].max() <= 310
    win.close()


# -- 28.2 pyramid ---------------------------------------------------------------------


def test_pyramid_failed_resets_flag_and_gives_up_after_limit(tmp_path):
    path = _write(tmp_path / "p.parquet", {"a": np.arange(100, dtype=float)})
    col = WindowedColumn(ParquetDataSource(path), "a")
    for i in range(MAX_PYRAMID_FAILURES):
        job = col.request_pyramid()
        assert job is not None, f"retry {i} should still be offered"
        assert col.request_pyramid() is None  # in flight
        col.pyramid_failed()
        assert not col._pyramid_building
    assert col.request_pyramid() is None
    assert col.pyramid_given_up


def test_pyramid_build_failure_retries_then_falls_back_to_real_fetch(qapp, isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    calls = []

    def bad_build(*a, **k):
        calls.append(1)
        raise RuntimeError("no pyramid")

    monkeypatch.setattr(column_module, "build_pyramid", bad_build)
    win = _windowed_win(tmp_path)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    _settle(qapp, win)
    col = win._raw_variables["a"]
    assert not col._pyramid_building and 1 <= len(calls) <= MAX_PYRAMID_FAILURES
    for _ in range(5):
        plot._redraw_series("a")
        _settle(qapp, win)
    assert len(calls) == MAX_PYRAMID_FAILURES, "must stop retrying after the limit"
    assert col.pyramid is None and not plot._active_workers
    # still drawn from real data
    xs, ys = plot._series["a"]["curve"].getData()
    assert xs is not None and len(xs) > 0
    win.close()


# -- 28.2 stats / FFT -----------------------------------------------------------------


def test_stats_partial_failure_shows_error_row_and_others_normal(qapp, isolated_config, tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    path = _write(
        tmp_path / "s.parquet",
        {"t": np.arange(500, dtype=float), "a": rng.normal(size=500), "b": rng.normal(size=500) + 100.0},
    )
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    real = main_window_module.basic_stats

    def flaky(y):
        if np.nanmean(y) > 50:
            raise ZeroDivisionError("bad variable")
        return real(y)

    monkeypatch.setattr(main_window_module, "basic_stats", flaky)
    win.variable_panel.selectAll()
    win._on_stats_requested()
    _wait(qapp, lambda: win.findChildren(StatsDialog))
    _settle(qapp, win)
    assert not win._stats_workers and "stats" not in win._busy_sources
    table = win.findChildren(StatsDialog)[0].findChild(QTableWidget)
    rows = {table.item(r, 0).text(): table.item(r, 1).text() for r in range(table.rowCount())}
    assert "ZeroDivisionError: bad variable" in rows["b"]
    assert rows["a"] == "500"
    win.close()


def test_fft_failure_clears_busy_and_warns(qapp, isolated_config, sample_parquet_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))

    def bad_fft(x, y):
        raise RuntimeError("fft broke")

    monkeypatch.setattr(main_window_module, "fft", bad_fft)
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    win.variable_panel.setCurrentItem(win.variable_panel.item(0))
    win.variable_panel.item(0).setSelected(True)
    n_plots = len(win.plot_grid.plots)
    win._on_fft_requested()
    _wait(qapp, lambda: warnings)
    _settle(qapp, win)
    assert "fft broke" in warnings[0]
    assert not win._fft_workers and "fft" not in win._busy_sources
    assert len(win.plot_grid.plots) == n_plots
    win.close()


# -- 29.1 check_time_order ------------------------------------------------------------


def _rep(values):
    return check_time_order(np.array(values, dtype=float))


def test_check_time_order_cases():
    assert _rep([1, 2, 3]).ok
    assert _rep([1, 1, 2, 2]).ok  # duplicates are fine
    r = _rep([1, 2, 3, 2, 5])
    assert (r.ok, r.decreasing_count, r.first_decreasing_index) == (False, 1, 3)
    r = _rep([5, 4, 3, 2])
    assert (r.decreasing_count, r.first_decreasing_index) == (3, 1)
    for vals in ([np.nan, 1, 2], [1, np.nan, 2], [1, 2, np.nan]):
        r = _rep(vals)
        assert r.nan_count == 1 and r.decreasing_count == 0 and not r.ok
    r = _rep([1, np.inf, 2])
    assert r.nan_count == 1 and r.decreasing_count == 0
    # decreases are judged over finite values only, reported with the original row index
    r = _rep([1, np.nan, 5, np.nan, 3])
    assert (r.nan_count, r.decreasing_count, r.first_decreasing_index) == (2, 1, 4)
    assert _rep([]).ok and _rep([7]).ok
    r = _rep([np.nan, np.nan])
    assert r.nan_count == 2 and r.decreasing_count == 0 and not r.ok


# -- 29.2 MainWindow label ------------------------------------------------------------


def test_time_order_label_shows_hides_and_follows_time_column(qapp, isolated_config, tmp_path):
    path = _write(
        tmp_path / "t.parquet",
        {
            "bad": np.array([1.0, 2.0, 3.0, 2.0, 5.0, 6.0]),
            "good": np.arange(6, dtype=float),
            "nan_t": np.array([0.0, 1.0, np.nan, 3.0, 4.0, 5.0]),
            "v": np.arange(6, dtype=float),
        },
    )
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)  # first column "bad" is the time axis
    label = win._time_order_label
    assert label.isVisible()
    assert "(1" in label.text()
    assert "row 4" in label.toolTip()  # index 3, shown 1-based
    win.set_time_column("good")
    assert not label.isVisible()
    win.set_time_column("nan_t")
    assert label.isVisible() and "1" in label.text()
    win.set_time_column("good")
    assert not label.isVisible()
    win.close()


def test_time_order_label_hidden_for_normal_file(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    assert not win._time_order_label.isVisible()
    win.close()


# -- 29.3 abnormal data (#12) ---------------------------------------------------------


def _exercise_all_columns(qapp, win, names):
    plot = win.plot_grid.plots[0]
    for n in names:
        win._on_variable_dropped(plot, n)
    _settle(qapp, win)
    win.variable_panel.selectAll()
    win._on_stats_requested()
    _wait(qapp, lambda: win.findChildren(StatsDialog))
    _settle(qapp, win)
    for d in win.findChildren(StatsDialog):
        d.close()


@pytest.mark.parametrize("limit_mb", [None, 0])
def test_odd_dtype_and_degenerate_columns_open_plot_and_stats(qapp, isolated_config, tmp_path, limit_mb):
    n = 3000
    path = _write(
        tmp_path / "odd.parquet",
        {
            "t": np.arange(n, dtype=float),
            "u32": np.arange(n, dtype=np.uint32),
            "i64": np.arange(n, dtype=np.int64) - 1500,
            "f32": np.sin(np.arange(n)).astype(np.float32),
            "f64": np.cos(np.arange(n)),
            "all_nan": np.full(n, np.nan),
            "const": np.full(n, 3.5),
        },
    )
    win = MainWindow()
    if limit_mb is not None:
        win.settings.eager_load_limit_mb = limit_mb
    win.resize(1200, 900)
    win.show()
    assert win.load_parquet(path)
    _exercise_all_columns(qapp, win, ["u32", "i64", "f32", "f64", "all_nan", "const"])
    assert not win._busy_sources
    assert set(win.plot_grid.plots[0].series_names()) == {"u32", "i64", "f32", "f64", "all_nan", "const"}
    win.close()


def test_single_row_file_opens_plots_and_stats(qapp, isolated_config, tmp_path):
    path = _write(tmp_path / "one.parquet", {"t": np.array([5.0]), "a": np.array([2.0])})
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    _exercise_all_columns(qapp, win, ["a"])
    assert not win._busy_sources
    win.close()


@pytest.mark.parametrize("limit_mb", [None, 0])
def test_many_tiny_row_groups_visible_range_stats_equal_full(qapp, isolated_config, tmp_path, monkeypatch, limit_mb):
    n, rg = 100_000, 1000
    rng = np.random.default_rng(3)
    a = rng.normal(size=n)
    path = _write(tmp_path / "rg.parquet", {"t": np.arange(n, dtype=float), "a": a}, row_group_size=rg)
    assert pq.ParquetFile(path).num_row_groups == 100
    win = MainWindow()
    if limit_mb is not None:
        win.settings.eager_load_limit_mb = limit_mb
    win.resize(1200, 900)
    win.show()
    assert win.load_parquet(path)
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    _settle(qapp, win)
    assert win._visible_row_range() == (0, n)
    captured = {}
    real_init = StatsDialog.__init__

    def spy(self, label, results, parent=None):
        captured.update(results)
        real_init(self, label, results, parent)

    monkeypatch.setattr(StatsDialog, "__init__", spy)
    win.variable_panel.clearSelection()
    win._on_stats_requested()
    _wait(qapp, lambda: captured)
    _settle(qapp, win)
    s = captured["a"]
    assert s["count"] == n
    assert s["mean"] == pytest.approx(a.mean()) and s["min"] == a.min() and s["max"] == a.max()
    for d in win.findChildren(StatsDialog):
        d.close()
    win.close()
