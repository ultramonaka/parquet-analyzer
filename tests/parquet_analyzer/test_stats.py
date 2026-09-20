from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget

from parquet_analyzer.core.analysis import basic_stats
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog


@pytest.fixture(autouse=True)
def _suppress_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))


def _drain(qapp, iterations=100, delay=0.01):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _select_variables(win: MainWindow, names: list[str]) -> None:
    win.variable_panel.clearSelection()
    for name in names:
        items = win.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)
        items[0].setSelected(True)


def test_stats_runs_off_thread_and_shows_a_dialog(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    _select_variables(win, ["a"])
    win._on_stats_requested()
    assert win._progress_bar.isVisible(), "stats never reported itself as busy (still running synchronously?)"
    _drain(qapp)

    assert not win._progress_bar.isVisible()
    assert not win._stats_workers, "StatsWorker was never released"
    dialogs = win.findChildren(StatsDialog)
    assert len(dialogs) == 1
    assert dialogs[0].isVisible()


def test_stats_covers_all_selected_variables_in_one_dialog(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    _select_variables(win, ["a", "b", "c"])
    win._on_stats_requested()
    _drain(qapp)

    dialogs = win.findChildren(StatsDialog)
    assert len(dialogs) == 1
    table = dialogs[0].findChild(QTableWidget)
    assert table.rowCount() == 3


def test_stats_uses_the_full_visible_range_not_the_downsampled_curve(qapp, isolated_config, sample_parquet_path, monkeypatch):
    """The whole point of this feature: compute over every raw sample in the visible
    window, not the LTTB-downsampled points actually drawn on the plot.
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    time_plot = win.plot_grid.plots[0]
    # time_plot is layout-managed inside `win`, so a manual resize() wouldn't stick;
    # force a tiny width directly so _redraw_series's pixel-count-based n_out is small
    # enough to guarantee LTTB downsampling actually kicks in, deterministically.
    monkeypatch.setattr(type(time_plot), "width", lambda self: 10)
    time_plot.add_series("a", win._time_values, win.variable_values("a"))
    _drain(qapp)

    # series_data() returns the full backing arrays, not what's actually drawn — the
    # rendered (possibly downsampled) curve lives on the curve item itself.
    displayed_x, displayed_y = time_plot._series["a"]["curve"].getData()
    assert len(displayed_x) < len(win._time_values)  # confirms downsampling actually happened

    _select_variables(win, ["a"])
    win._on_stats_requested()
    _drain(qapp)

    dialogs = win.findChildren(StatsDialog)
    expected = basic_stats(win.variable_values("a"))  # full range, full data
    table = dialogs[0].findChild(QTableWidget)
    assert table.item(0, 1).text() == str(expected["count"])  # 件数 column matches full data, not curve


def test_stats_falls_back_to_plotted_variables_when_none_selected_in_panel(qapp, isolated_config, sample_parquet_path):
    """User-requested: clicking 統計 with nothing selected in the variable panel
    should target whatever's currently overlaid on the plots, instead of requiring a
    separate panel selection for a variable that's already visible.
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    win._on_variable_dropped(plot, "b")
    win.variable_panel.clearSelection()  # nothing explicitly selected

    win._on_stats_requested()
    _drain(qapp)

    dialogs = win.findChildren(StatsDialog)
    assert len(dialogs) == 1
    table = dialogs[0].findChild(QTableWidget)
    assert table.rowCount() == 2  # "a" and "b", both currently plotted


def test_stats_prefers_explicit_panel_selection_over_plotted_variables(qapp, isolated_config, sample_parquet_path):
    """A panel selection is a deliberate choice (e.g. to check a variable that isn't
    plotted at all) and must not be overridden by what happens to already be plotted.
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    win._on_variable_dropped(win.plot_grid.plots[0], "a")  # "a" is plotted...
    _select_variables(win, ["b"])  # ...but only "b" is explicitly selected

    win._on_stats_requested()
    _drain(qapp)

    dialogs = win.findChildren(StatsDialog)
    table = dialogs[0].findChild(QTableWidget)
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "b"


def test_stats_before_anything_is_plotted_falls_back_to_the_full_range(qapp, isolated_config, sample_parquet_path):
    """No time-domain plot has any series yet, so there's no meaningful "visible range"
    (_visible_row_range's empty-plot fallback, shared with FFT) -- must use the whole
    file rather than pyqtgraph's arbitrary default viewRange(), and must not crash.
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    _select_variables(win, ["a"])
    win._on_stats_requested()
    _drain(qapp)

    dialogs = win.findChildren(StatsDialog)
    assert dialogs
    expected = basic_stats(win.variable_values("a"))
    table = dialogs[0].findChild(QTableWidget)
    assert table.item(0, 1).text() == str(expected["count"])
