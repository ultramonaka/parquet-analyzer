"""Review follow-ups for chapters 28/29 (detailed_specification.md 28.2, 29.1, 29.2)."""
from __future__ import annotations

import logging
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QMessageBox, QTableWidget

from parquet_analyzer.core import numeric as numeric_module
from parquet_analyzer.core.column import MAX_PYRAMID_FAILURES, WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.numeric import check_time_order
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
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.01)
    _wait(qapp, lambda: not win._busy_sources)


def _write(path, columns):
    pq.write_table(pa.table(columns), path)
    return str(path)


def _windowed_win(tmp_path, n=2000):
    rng = np.random.default_rng(0)
    path = _write(tmp_path / "w.parquet", {"t": np.arange(n, dtype=float), "a": rng.normal(size=n)})
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0
    win.resize(1200, 900)
    win.show()
    assert win.load_parquet(path)
    return win


def test_check_time_order_chunked_equals_unchunked(monkeypatch):
    rng = np.random.default_rng(7)
    v = np.cumsum(rng.random(5000))
    v[rng.integers(0, 5000, 200)] -= 50.0  # decreases, some across boundaries
    v[rng.integers(0, 5000, 100)] = np.nan
    v[[0, 99, 100, 101, 4999]] = np.nan  # NaN on/around chunk edges
    full = check_time_order(v)
    for chunk in (1, 2, 7, 100, 333):
        monkeypatch.setattr(numeric_module, "_TIME_ORDER_CHUNK_ROWS", chunk)
        assert check_time_order(v) == full, chunk
    monkeypatch.setattr(numeric_module, "_TIME_ORDER_CHUNK_ROWS", 3)
    r = check_time_order(np.array([1.0, 2.0, 3.0, 2.5, 4.0, 5.0]))  # decrease across a boundary
    assert (r.decreasing_count, r.first_decreasing_index) == (1, 3)


def test_notice_does_not_hide_file_or_time_order_labels(qapp, isolated_config, tmp_path):
    path = _write(tmp_path / "n.parquet", {"t": np.array([1.0, 3.0, 2.0]), "v": np.arange(3.0)})
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    win._on_render_failed(None, "v", "boom")
    qapp.processEvents()
    assert win._notice_label.isVisible() and "boom" in win._notice_label.text()
    assert win._file_label.isVisible() and win._time_order_label.isVisible()
    win._show_notice("Image saved: x", 10_000)
    assert win._file_label.isVisible() and win._time_order_label.isVisible()
    win._notice_timer.timeout.emit()
    assert not win._notice_label.isVisible()
    assert win._file_label.isVisible() and win._time_order_label.isVisible()
    win._show_notice("again", 20)  # real timer expiry
    _wait(qapp, lambda: not win._notice_label.isVisible())
    win._show_notice("old failure", 60_000)
    assert win.load_parquet(path)  # a successful load clears a pending notice
    assert not win._notice_label.isVisible()
    win.close()


def test_stats_and_fft_survive_a_failing_synchronous_read(qapp, isolated_config, tmp_path, monkeypatch):
    win = _windowed_win(tmp_path)
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    _settle(qapp, win)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))

    def bad_window(self, s, e):
        raise OSError("disk gone")

    monkeypatch.setattr(WindowedColumn, "window", bad_window)
    win.variable_panel.selectAll()
    win._on_stats_requested()  # must not raise
    _wait(qapp, lambda: win.findChildren(StatsDialog))
    assert not win._busy_sources and not win._stats_workers
    table = win.findChildren(StatsDialog)[0].findChild(QTableWidget)
    assert "disk gone" in table.item(0, 1).text()
    win.variable_panel.clearSelection()
    win.variable_panel.item(0).setSelected(True)
    win._on_fft_requested()
    assert warnings and "disk gone" in warnings[0]
    assert not win._fft_workers and not win._busy_sources
    win.close()


def test_stale_or_removed_series_failure_emits_no_render_failed(qapp, isolated_config, tmp_path):
    win = _windowed_win(tmp_path)
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    _settle(qapp, win)
    seen = []
    plot.renderFailed.connect(lambda p, n, m: seen.append(n))
    gen = plot._series["a"]["generation"]
    plot._on_series_worker_failed("a", gen - 1, "stale")
    plot._on_series_worker_failed("gone", 1, "removed")
    assert seen == []
    plot._on_series_worker_failed("a", gen, "current")
    assert seen == ["a"]
    win.close()


def test_pyramid_giving_up_logs_once(tmp_path, caplog):
    path = _write(tmp_path / "p.parquet", {"a": np.arange(100, dtype=float)})
    col = WindowedColumn(ParquetDataSource(path), "a")
    with caplog.at_level(logging.WARNING):
        for _ in range(MAX_PYRAMID_FAILURES):
            col.request_pyramid()
            col.pyramid_failed()
    assert sum("giving up" in r.getMessage() for r in caplog.records) == 1
