from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt

from parquet_analyzer.core.analysis import fft
from parquet_analyzer.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _suppress_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))


def _drain(qapp, iterations=100, delay=0.01):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _select_variable(win: MainWindow, name: str) -> None:
    items = win.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)
    win.variable_panel.setCurrentItem(items[0])


def test_fft_runs_off_thread_and_produces_a_plot(qapp, isolated_config, sample_parquet_path):
    """Regression test: FFT used to run synchronously on the UI thread
    (detailed_specification.md 13.2, since resolved via FFTWorker/13.5.1 Step 0).
    """
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    _select_variable(win, "a")
    n_plots_before = len(win.plot_grid.plots)

    win._on_fft_requested()
    assert win._progress_bar.isVisible(), "FFT never reported itself as busy (still running synchronously?)"
    _drain(qapp)

    assert not win._progress_bar.isVisible()
    assert len(win.plot_grid.plots) == n_plots_before + 1
    fft_plot = win.plot_grid.plots[-1]
    assert not fft_plot.x_axis_datetime
    assert fft_plot.series_names()
    assert not win._fft_workers, "FFTWorker was never released"


def test_fft_is_scoped_to_the_visible_time_range(qapp, isolated_config, sample_parquet_path):
    """FFT must analyze only the currently visible X range, not the whole file
    (specification.md 5.6, changed 2026-09-17) — both for memory (13.5.1) and because
    the app's target data has sampling gaps that make a whole-file dt meaningless.
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    time_plot = win.plot_grid.plots[0]
    time_plot.add_series("a", win._time_values, win.variable_values("a"))
    qapp.processEvents()

    full_x = win._time_values
    x_lo = float(full_x[0])
    x_hi = float(full_x[0] + (full_x[-1] - full_x[0]) * 0.1)  # first ~10% of the file
    time_plot.setXRange(x_lo, x_hi, padding=0)
    qapp.processEvents()

    _select_variable(win, "a")
    win._on_fft_requested()
    _drain(qapp)

    fft_plot = win.plot_grid.plots[-1]
    freqs, _amps, _color = fft_plot.series_data(fft_plot.series_names()[0])
    # A shorter analyzed window has coarser frequency resolution (fewer/larger bins)
    # than the full file would — a cheap, robust proxy for "it didn't use everything".
    full_freqs, _ = fft(full_x, win.variable_values("a"))
    assert len(freqs) < len(full_freqs)


def test_fft_over_too_wide_a_range_shows_a_message_instead_of_computing(qapp, isolated_config, sample_parquet_path, monkeypatch):
    from parquet_analyzer.ui import main_window as main_window_module

    monkeypatch.setattr(main_window_module, "_FFT_MAX_ROWS", 10)

    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    informed = []
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: informed.append(1)))

    _select_variable(win, "a")
    win._on_fft_requested()

    assert informed
    assert not win._fft_workers
