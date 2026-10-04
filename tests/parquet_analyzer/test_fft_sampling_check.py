"""detailed_specification.md 32: FFT sampling-interval check (analysis.sampling_report +
_on_fft_requested refusing non-uniform ranges)."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer import features
from parquet_analyzer.core.analysis import FFT_JITTER_TOLERANCE, fft, sampling_report
from parquet_analyzer.ui.main_window import MainWindow


# ---- core -------------------------------------------------------------------------------

def test_uniform_is_ok():
    r = sampling_report(np.arange(1000) * 0.01)
    assert r.uniform and r.gap_count == 0 and r.reason == ""
    assert r.dt_median == pytest.approx(0.01)


def test_small_jitter_is_tolerated():
    rng = np.random.default_rng(0)
    x = np.cumsum(0.01 * (1 + rng.uniform(-0.004, 0.004, 1000)))
    r = sampling_report(x)
    assert r.max_rel_deviation <= FFT_JITTER_TOLERANCE
    assert r.uniform


def test_five_percent_jitter_is_rejected():
    rng = np.random.default_rng(0)
    x = np.cumsum(0.01 * (1 + rng.uniform(-0.05, 0.05, 1000)))
    r = sampling_report(x)
    assert not r.uniform and r.reason == "non_uniform"
    assert r.gap_count == 0
    assert r.max_rel_deviation > FFT_JITTER_TOLERANCE


def test_gap_is_counted_and_rejected():
    x = np.arange(1000) * 0.01
    x[500:] += 30.0
    r = sampling_report(x)
    assert not r.uniform and r.gap_count == 1 and r.dt_max == pytest.approx(30.01)


@pytest.mark.parametrize(
    "x, reason",
    [
        (np.array([0.0, 2.0, 1.0, 3.0]), "not_ascending"),
        (np.array([0.0, 1.0, 1.0, 2.0]), "not_ascending"),
        (np.array([0.0, 1.0, np.nan, 3.0]), "not_finite"),
        (np.array([0.0, np.inf, 2.0, 3.0]), "not_finite"),
        (np.array([0.0, 1.0]), "too_few"),
        (np.array([0.0]), "too_few"),
        (np.array([]), "too_few"),
    ],
)
def test_bad_axes_never_raise(x, reason):
    r = sampling_report(x)
    assert not r.uniform and r.reason == reason


def test_fft_still_returns_peak_for_uniform_input():
    x = np.arange(1000) * 0.01
    freqs, amps = fft(x, np.sin(2 * np.pi * 5 * x))
    assert freqs[np.argmax(amps)] == pytest.approx(5.0, abs=0.2)


# ---- window -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fft_on_and_no_dialogs(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "fft", True)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


def _drain(qapp, n=60):
    for _ in range(n):
        qapp.processEvents()
        time.sleep(0.01)


def _make_window(qapp, path):
    win = MainWindow()
    win.load_parquet(str(path))
    qapp.processEvents()
    items = win.variable_panel.findItems("a", Qt.MatchFlag.MatchExactly)
    win.variable_panel.setCurrentItem(items[0])
    return win


def _write(tmp_path, t):
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"t": t, "a": np.sin(np.arange(len(t)) * 0.1)}), path)
    return path


def test_window_runs_on_uniform_axis(qapp, isolated_config, tmp_path):
    win = _make_window(qapp, _write(tmp_path, np.arange(2000) * 0.01))
    n = len(win.plot_grid.plots)
    win._on_fft_requested()
    _drain(qapp)
    assert len(win.plot_grid.plots) == n + 1


def test_window_refuses_gapped_axis_without_modal(qapp, isolated_config, tmp_path, monkeypatch):
    t = np.arange(2000) * 0.01
    t[1000:] += 30.0
    win = _make_window(qapp, _write(tmp_path, t))
    modal = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: modal.append(a)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: modal.append(a)))
    n = len(win.plot_grid.plots)
    win._on_fft_requested()
    _drain(qapp, 10)
    assert len(win.plot_grid.plots) == n
    assert not win._fft_workers and not win._progress_bar.isVisible()
    assert not modal
    msg = win.statusBar().currentMessage()
    assert "FFT" in msg and "1 gap" in msg and "%" not in msg  # gap count, not a huge deviation


def test_window_refuses_non_ascending_and_nan_axes(qapp, isolated_config, tmp_path):
    win = _make_window(qapp, _write(tmp_path, np.arange(2000) * 0.01))
    n = len(win.plot_grid.plots)
    for bad in (-np.arange(2000) * 0.01, np.where(np.arange(2000) == 5, np.nan, np.arange(2000) * 0.01)):
        win._time_values = bad
        win._on_fft_requested()
        _drain(qapp, 5)
        assert len(win.plot_grid.plots) == n
        assert not win._fft_workers
