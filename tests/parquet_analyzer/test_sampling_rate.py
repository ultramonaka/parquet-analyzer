"""Data chapter 60 (requirement 5.54): sampling rate of the time axis -- core scan (60.1),
status-bar label and setting (60.2), stats column (60.3)."""
from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QMessageBox, QTableWidget

from parquet_analyzer.core.numeric import TimeOrderScanner, check_time_order
from parquet_analyzer.io import settings as settings_io
from parquet_analyzer.io.settings import Settings, load_settings, save_settings
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.sampling_format import format_interval, format_rate
from parquet_analyzer.ui.settings_dialog import SettingsDialog
from parquet_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _rep(values):
    return check_time_order(np.array(values, dtype=float))


# -- 60.1 core ---------------------------------------------------------------------------


def test_uniform_axis_median_min_max_agree():
    r = _rep(np.arange(1000) * 0.01)
    assert r.dt_median == pytest.approx(0.01)
    assert r.dt_min == pytest.approx(0.01)
    assert r.dt_max == pytest.approx(0.01)
    assert r.rate_hz == pytest.approx(100.0)
    assert not r.irregular


def test_difference_across_chunk_boundary_is_counted():
    s = TimeOrderScanner()
    s.feed(np.array([0.0, 1.0, 2.0]), 0)
    s.feed(np.array([12.0, 13.0]), 3)  # the 2 -> 12 step crosses the boundary
    r = s.report()
    assert r.dt_max == pytest.approx(10.0)
    assert r.dt_min == pytest.approx(1.0)


def test_chunked_equals_one_shot():
    t = np.cumsum(np.random.default_rng(1).uniform(0.9, 1.1, 5000))
    one = _rep(t)
    s = TimeOrderScanner()
    for a in range(0, t.size, 700):
        s.feed(t[a : a + 700], a)
    r = s.report()
    assert r.dt_min == one.dt_min and r.dt_max == one.dt_max
    assert r.dt_median == pytest.approx(one.dt_median, rel=0.05)  # median of chunk medians


@pytest.mark.parametrize(("jitter", "irregular"), [(0.005, False), (0.05, True)])
def test_irregular_threshold(jitter, irregular):
    dt = np.ones(999)
    dt[::2] += jitter
    r = _rep(np.concatenate(([0.0], np.cumsum(dt))))
    assert r.irregular is irregular


def test_nan_inf_skipped_and_duplicates_not_counted():
    r = _rep([0.0, 1.0, np.nan, 2.0, np.inf, 2.0, 3.0])
    assert r.dt_min == pytest.approx(1.0)
    assert r.dt_max == pytest.approx(1.0)  # 1 -> 2 spans the NaN: still one step of 1.0
    assert r.dt_median == pytest.approx(1.0)


@pytest.mark.parametrize("values", [[], [1.0], [np.nan, np.nan], [3.0, 2.0, 1.0], [5.0, 5.0, 5.0]])
def test_no_usable_interval_is_nan(values):
    r = _rep(values)
    assert np.isnan(r.dt_median) and np.isnan(r.rate_hz)
    assert not r.irregular


def test_existing_order_result_unchanged():
    r = _rep([0.0, 1.0, 0.5, np.nan, 2.0, 2.0])
    assert (r.nan_count, r.decreasing_count, r.duplicate_count) == (1, 1, 1)
    assert r.first_decreasing_index == 2


def test_single_scan_no_second_pass(monkeypatch):
    """The interval comes out of the one feed() pass: check_time_order feeds each element once."""
    fed = []
    orig = TimeOrderScanner.feed

    def spy(self, chunk, start):
        fed.append(chunk.size)
        return orig(self, chunk, start)

    monkeypatch.setattr(TimeOrderScanner, "feed", spy)
    _rep(np.arange(100.0))
    assert sum(fed) == 100


# -- formatting --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "text"),
    [(100.0, "100 Hz"), (2500.0, "2.5 kHz"), (1e6, "1 MHz"), (0.1, "0.1 Hz"), (999.9, "1 kHz"), (float("nan"), "—")],
)
def test_format_rate(rate, text):
    assert format_rate(rate) == text


@pytest.mark.parametrize(
    ("dt", "text"),
    [(0.01, "10 ms"), (1.5, "1.5 s"), (120.0, "2 min"), (2e-6, "2 µs"), (5e-9, "5 ns"), (0.9996, "1 s"), (0.0, "—")],
)
def test_format_interval(dt, text):
    assert format_interval(dt) == text


# -- 60.2 status bar ---------------------------------------------------------------------


def _write(path, t, **cols):
    pq.write_table(pa.table({"t": t, **cols}), path)
    return str(path)


def test_label_shows_rate_for_uniform_file(qapp, isolated_config, tmp_path):
    t = np.arange(2000) * 0.01
    win = MainWindow()
    assert win._sampling_label.isHidden()  # no file
    win.load_parquet(_write(tmp_path / "u.parquet", t, y=np.sin(t)))
    assert not win._sampling_label.isHidden()
    assert win._sampling_label.text() == "100 Hz (10 ms)"
    assert "10 ms" in win._sampling_label.toolTip()


def test_label_irregular_and_time_axis_switch(qapp, isolated_config, tmp_path):
    t = np.concatenate(([0.0], np.cumsum(np.where(np.arange(1999) % 2, 0.01, 0.0125))))
    other = np.arange(2000) * 2.0
    win = MainWindow()
    win.load_parquet(_write(tmp_path / "i.parquet", t, other=other))
    text = win._sampling_label.text()
    assert "irregular" in text and text.startswith("~")
    win.set_time_column("other")
    assert win._sampling_label.text() == "0.5 Hz (2 s)"  # updated with the time-order check


def test_setting_off_hides_at_once_and_on_restores(qapp, isolated_config, tmp_path):
    t = np.arange(500) * 0.1
    win = MainWindow()
    win.load_parquet(_write(tmp_path / "s.parquet", t, y=t))
    assert not win._sampling_label.isHidden()
    off = Settings(**{**win.settings.__dict__, "show_sampling_rate": False})
    win.apply_settings(off)
    assert win._sampling_label.isHidden()
    assert np.isfinite(win._time_order_report.dt_median)  # value kept for the stats
    win.apply_settings(Settings(**{**win.settings.__dict__, "show_sampling_rate": True}))
    assert win._sampling_label.text() == "10 Hz (100 ms)"


def test_label_with_order_warning_still_shows_rate(qapp, isolated_config, tmp_path):
    t = np.arange(500) * 0.1
    t[250], t[251] = t[251], t[250]
    win = MainWindow()
    win.load_parquet(_write(tmp_path / "w.parquet", t, y=t))
    assert not win._time_order_label.isHidden()
    assert not win._sampling_label.isHidden()


def test_settings_dialog_checkbox_roundtrip(qapp):
    dlg = SettingsDialog(Settings())
    assert dlg.show_sampling_rate_check.isChecked()
    dlg.show_sampling_rate_check.setChecked(False)
    assert dlg.result_settings().show_sampling_rate is False


def test_setting_persisted_and_unknown_keys_ignored(isolated_config):
    save_settings(Settings(show_sampling_rate=False))
    assert load_settings().show_sampling_rate is False
    path = settings_io.settings_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["some_future_key"] = 1
    payload["show_sampling_rate"] = "yes"  # wrong type -> default
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_settings().show_sampling_rate is True


# -- 60.3 stats column -------------------------------------------------------------------


def test_stats_effective_rate_smaller_for_signal_with_gaps(qapp, isolated_config, tmp_path):
    t = np.arange(2000) * 0.01
    gappy = np.sin(t)
    gappy[::2] = np.nan  # half the points are missing
    win = MainWindow()
    win.load_parquet(_write(tmp_path / "g.parquet", t, full=np.cos(t), gappy=gappy))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "full")
    win._on_variable_dropped(plot, "gappy")
    win.variable_panel.clearSelection()
    win._on_stats_requested()
    deadline = time.monotonic() + 20
    while not win.findChildren(StatsDialog) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    dlg = win.findChildren(StatsDialog)[0]
    table = dlg.findChild(QTableWidget)
    last = table.columnCount() - 1
    assert table.horizontalHeaderItem(last).text() == "Effective sample rate"
    rows = {table.item(r, 0).text(): table.item(r, last).text() for r in range(table.rowCount())}
    assert rows["full"] == "100 Hz"
    assert rows["gappy"] == "50 Hz"


def test_stats_effective_rate_dash_for_zero_width():
    assert StatsDialog._effective_rate_text({"count": 5, "nan_count": 0}, 0.0) == "—"
    assert StatsDialog._effective_rate_text({"count": 5, "nan_count": 0}, None) == "—"
    assert StatsDialog._effective_rate_text({"count": 5, "nan_count": 0}, float("nan")) == "—"
