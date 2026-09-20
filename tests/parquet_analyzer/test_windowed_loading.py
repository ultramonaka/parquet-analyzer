from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.column import LazyColumn, WindowedColumn
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog

"""Regression tests for detailed_specification.md 13.5.1 Phase D: a raw column
over Settings.eager_load_limit_mb becomes a WindowedColumn instead of a
LazyColumn, so plotting/FFT/stats on it only ever reads the row range actually
needed, never the whole column. Forces the windowed path on small test fixtures
by setting eager_load_limit_mb to 0 before opening the file (every non-time
column then exceeds the threshold regardless of its real size).
"""


def _write_parquet(path, columns: dict, n: int = 2000, row_group_size: int = 200):
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    table = pa.table({"time": pa.array(times), **columns})
    pq.write_table(table, path, row_group_size=row_group_size)


@pytest.fixture
def windowed_win(qapp, isolated_config, tmp_path):
    path = tmp_path / "multi.parquet"
    rng = np.random.default_rng(0)
    _write_parquet(
        path,
        {
            "a": rng.normal(size=2000),
            "b": rng.normal(size=2000) * 10,
        },
    )
    win = MainWindow()
    win.settings.eager_load_limit_mb = 0  # force every raw column over threshold
    win.load_parquet(str(path))
    yield win
    win.close()


def test_over_threshold_columns_become_windowed(windowed_win):
    assert isinstance(windowed_win._raw_variables["a"], WindowedColumn)
    assert isinstance(windowed_win._raw_variables["b"], WindowedColumn)


def test_under_threshold_stays_on_the_phase_c_lazy_path(qapp, isolated_config, tmp_path):
    path = tmp_path / "small.parquet"
    _write_parquet(path, {"a": np.arange(2000, dtype=float)})
    win = MainWindow()
    assert win.settings.eager_load_limit_mb > 0  # default, not forced small
    win.load_parquet(str(path))
    assert isinstance(win._raw_variables["a"], LazyColumn)
    win.close()


def test_dropping_a_windowed_variable_uses_add_windowed_series(windowed_win, qapp):
    plot = windowed_win.plot_grid.plots[0]
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()

    source = plot.series_source("a")
    assert source is not None  # windowed, not resident


def test_windowed_series_plots_correct_values(windowed_win, qapp):
    plot = windowed_win.plot_grid.plots[0]
    plot.resize(2000, 400)
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()
    import time

    for _ in range(50):
        qapp.processEvents()
        time.sleep(0.005)

    real_a = windowed_win.variable_values("a")
    displayed_x, displayed_y = plot._series["a"]["curve"].getData()
    assert len(displayed_y) > 0
    for yv in displayed_y[:5]:
        assert np.any(np.isclose(real_a, yv))


def test_fft_on_windowed_variable_uses_variable_window(windowed_win, qapp, monkeypatch):
    plot = windowed_win.plot_grid.plots[0]
    plot.resize(2000, 400)
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()

    calls = []
    original = windowed_win.variable_window

    def spy(name, row_start, row_end):
        calls.append((name, row_start, row_end))
        return original(name, row_start, row_end)

    monkeypatch.setattr(windowed_win, "variable_window", spy)

    from parquet_analyzer.ui import main_window as main_window_module

    monkeypatch.setattr(main_window_module, "_FFT_MAX_ROWS", 10_000_000)

    class _Item:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    monkeypatch.setattr(windowed_win.variable_panel, "selectedItems", lambda: [_Item("a")])
    windowed_win._on_fft_requested()

    assert calls
    assert calls[0][0] == "a"


def test_stats_on_windowed_variable_matches_full_column(windowed_win, qapp, monkeypatch):
    class _Item:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    monkeypatch.setattr(windowed_win.variable_panel, "selectedItems", lambda: [_Item("a")])
    windowed_win._on_stats_requested()
    import time

    for _ in range(50):
        qapp.processEvents()
        time.sleep(0.005)

    dialogs = windowed_win.findChildren(StatsDialog)
    assert dialogs
    from parquet_analyzer.core.analysis import basic_stats

    expected = basic_stats(windowed_win.variable_values("a"))
    from PySide6.QtWidgets import QTableWidget

    table = dialogs[0].findChild(QTableWidget)
    assert table.item(0, 1).text() == str(expected["count"])


def test_navigator_overview_for_windowed_column_before_anything_plotted(windowed_win, qapp):
    _x, overview_y = windowed_win.navigator._curve.getData()
    assert len(overview_y) > 0  # a coarse envelope was built, not a crash/empty plot


def test_go_home_after_plotting_a_windowed_variable(windowed_win, qapp):
    plot = windowed_win.plot_grid.plots[0]
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()

    plot.go_home()  # must not raise
    x_lo, x_hi = plot.getViewBox().viewRange()[0]
    assert x_lo <= windowed_win._time_values[0]
    assert x_hi >= windowed_win._time_values[-1]


def test_precision_indicator_shows_when_a_series_falls_back_to_pyramid(windowed_win, qapp, monkeypatch):
    """Phase E: the status-bar label should reflect coarse (pyramid) rendering."""
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    plot = windowed_win.plot_grid.plots[0]
    plot.resize(1200, 400)
    assert windowed_win._precision_label.text() == ""

    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()
    import time

    for _ in range(80):
        qapp.processEvents()
        time.sleep(0.005)

    assert windowed_win._precision_label.text() != ""


def test_precision_indicator_clears_once_zoomed_to_a_precise_range(windowed_win, qapp, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    plot = windowed_win.plot_grid.plots[0]
    plot.resize(1200, 400)
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()
    import time

    for _ in range(80):
        qapp.processEvents()
        time.sleep(0.005)
    assert windowed_win._precision_label.text() != ""  # coarse at full zoom-out

    x = windowed_win._time_values
    plot.setXRange(x[0], x[0] + (x[-1] - x[0]) * 0.01, padding=0)  # well under the threshold
    for _ in range(50):
        qapp.processEvents()
        time.sleep(0.005)

    assert windowed_win._precision_label.text() == ""


def test_downsample_toggle_action_applies_to_every_plot(windowed_win, qapp):
    action = windowed_win._actions["toggle_downsample"]
    assert action.isChecked() is True
    assert windowed_win._downsample_enabled is True

    action.trigger()  # simulates a user click: toggles state and emits triggered(checked)

    assert action.isChecked() is False
    assert windowed_win._downsample_enabled is False
    for plot in windowed_win.plot_grid.plots:
        assert plot._downsample_enabled is False


def test_downsample_enabled_round_trips_through_a_saved_view(qapp, isolated_config, tmp_path):
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"a": np.arange(200, dtype=float)}, n=200)

    win = MainWindow()
    win.load_parquet(str(path))
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    win._actions["toggle_downsample"].trigger()
    assert win._downsample_enabled is False

    from parquet_analyzer.io import view as view_io

    view = view_io.View(
        view_name="downsample_off_test",
        source=view_io.SourceDef(parquet_path=str(win._data_source.path), path_type="absolute"),
        plots=[
            view_io.PlotDef(
                plot_id=win.plot_grid.plots[0].plot_id,
                series=[view_io.SeriesDef(variable="a", color="#1f77b4")],
            )
        ],
        downsample_enabled=False,
    )
    view_io.save_view(view)

    win2 = MainWindow()
    win2.load_view("downsample_off_test")
    qapp.processEvents()

    assert win2._downsample_enabled is False
    assert win2._actions["toggle_downsample"].isChecked() is False
    for plot in win2.plot_grid.plots:
        assert plot._downsample_enabled is False

    win.close()
    win2.close()


def test_opening_a_new_file_resets_downsample_enabled_to_default(qapp, isolated_config, tmp_path):
    path1 = tmp_path / "a.parquet"
    _write_parquet(path1, {"a": np.arange(200, dtype=float)}, n=200)
    path2 = tmp_path / "b.parquet"
    _write_parquet(path2, {"a": np.arange(200, dtype=float)}, n=200)

    win = MainWindow()
    win.load_parquet(str(path1))
    win._actions["toggle_downsample"].trigger()
    assert win._downsample_enabled is False

    win.load_parquet(str(path2))

    assert win._downsample_enabled is True
    assert win._actions["toggle_downsample"].isChecked() is True
    win.close()


def test_switching_files_while_a_pyramid_build_is_in_flight_does_not_crash(windowed_win, qapp, monkeypatch, tmp_path):
    """Phase E: true cancellation of an in-flight pyramid build was not implemented
    (see detailed_specification.md 13.5.1's pyramid-wiring follow-up), but the
    existing y_source-identity guard in _on_pyramid_ready must still make this safe.
    """
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 500)
    plot = windowed_win.plot_grid.plots[0]
    plot.resize(1200, 400)
    windowed_win._on_variable_dropped(plot, "a")
    qapp.processEvents()  # kicks off a real fetch + a pyramid build, both in flight

    other_path = tmp_path / "other.parquet"
    _write_parquet(other_path, {"c": np.arange(200, dtype=float)}, n=200)
    assert windowed_win.load_parquet(str(other_path)) is True  # must not raise

    import time

    for _ in range(80):  # let the stale pyramid build's finished signal arrive, if any
        qapp.processEvents()
        time.sleep(0.005)

    assert windowed_win._data_source is not None
    assert "c" in windowed_win._raw_variables
