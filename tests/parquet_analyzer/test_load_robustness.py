from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _suppress_message_boxes(monkeypatch):
    """QMessageBox.exec() blocks on a real event loop waiting for a click; these tests
    intentionally trigger error/warning dialogs, so replace them with no-ops.
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))


def _write_parquet(path, columns: dict, n: int = 200):
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    table = pa.table({"time": pa.array(times), **columns})
    pq.write_table(table, path)


def test_single_column_parquet_shows_error_instead_of_crashing(qapp, isolated_config, tmp_path):
    """Regression test: a time-only parquet (no other columns) used to raise an
    uncaught StopIteration from `next(iter(self._raw_variables.values()))`.
    """
    path = tmp_path / "single.parquet"
    _write_parquet(path, {})

    win = MainWindow()
    assert win.load_parquet(str(path)) is False
    assert win._time_values is None  # state not left half-updated
    win.close()


def test_opening_a_second_file_clears_the_previous_plots(qapp, isolated_config, tmp_path):
    """Regression test: load_parquet updated the variable panel/navigator/title for the
    new file but never cleared plot_grid, leaving old curves rendering the previous
    file's now-orphaned arrays.
    """
    path_a = tmp_path / "a.parquet"
    _write_parquet(path_a, {"x": np.arange(200, dtype=float), "y": np.arange(200, dtype=float)})
    path_b = tmp_path / "b.parquet"
    _write_parquet(path_b, {"z": np.arange(200, dtype=float)})

    win = MainWindow()
    win.load_parquet(str(path_a))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "x")
    win._on_variable_dropped(plot, "y")
    assert plot.series_names() == ["x", "y"]

    win.load_parquet(str(path_b))
    assert win.plot_grid.plots[0].series_names() == []
    assert [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())] == ["z"]
    win.close()


def test_opening_a_file_shows_its_name_in_the_title_and_status_bar(qapp, isolated_config, tmp_path):
    """The window title alone is easy to miss (maximized windows, some window
    managers/remote desktops hide it) -- the currently open file's name is also
    mirrored into a status-bar label, with the full resolved path as its tooltip.
    """
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"x": np.arange(200, dtype=float)})

    win = MainWindow()
    assert win._file_label.text() == ""  # nothing open yet

    win.load_parquet(str(path))
    assert win._file_label.text() == "File: a.parquet"
    assert win._file_label.toolTip() == str(path.resolve())
    assert "a.parquet" in win.windowTitle()
    win.close()


def test_view_referencing_a_missing_column_loads_data_without_crashing(qapp, isolated_config, tmp_path):
    """Regression test: load_view tore the plot grid down before resolving series
    names, so a view referencing a column no longer in the (regenerated) source
    parquet raised an uncaught KeyError, leaving an emptied grid and a traceback.
    """
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"x": np.arange(200, dtype=float), "y": np.arange(200, dtype=float)})

    view = view_io.View(
        view_name="missing_col_view",
        source=view_io.SourceDef(parquet_path=str(path), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="x", color="#111111")])],
    )
    view_io.save_view(view)

    # regenerate the source without column "x"
    _write_parquet(path, {"y": np.arange(200, dtype=float)})

    win = MainWindow()
    win.load_view("missing_col_view")  # must not raise

    assert win._time_values is not None  # the parquet itself still loaded successfully
    assert [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())] == ["y"]
    win.close()


def test_load_view_applies_layout_only_when_a_different_file_is_already_open(qapp, isolated_config, tmp_path):
    """A view records the absolute path of the file it was saved against, but if a
    *different* file is already open, load_view should reuse the saved plot layout on
    that file instead of switching files out from under the user (e.g. re-applying a
    layout across several parquet files that happen to share column names).
    """
    path_a = tmp_path / "a.parquet"
    _write_parquet(path_a, {"x": np.arange(200, dtype=float)})
    view = view_io.View(
        view_name="reusable_layout",
        source=view_io.SourceDef(parquet_path=str(path_a), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="x", color="#111111")])],
    )
    view_io.save_view(view)

    path_b = tmp_path / "b.parquet"
    _write_parquet(path_b, {"x": np.arange(200, 400, dtype=float)})  # same column name, different data

    win = MainWindow()
    win.load_parquet(str(path_b))
    win.load_view("reusable_layout")

    assert win._data_source.path == path_b  # did not switch to a.parquet
    plot = win.plot_grid.plots[0]
    assert plot.series_names() == ["x"]
    x, y, _color = plot.series_data("x")
    np.testing.assert_array_equal(y, np.arange(200, 400, dtype=float))  # b's data, not a's
    win.close()


def test_load_view_does_not_reopen_a_file_already_open(qapp, isolated_config, tmp_path, monkeypatch):
    """Regression test: load_view used to always call load_parquet() to (re)read the
    file from disk even when the currently open file already *is* the view's own
    recorded source -- a wasted full re-read (undoing whatever LazyColumns/
    WindowedColumns had already materialized, 13.5.1) for no behavioral benefit, since
    the view is about to fully replace the plot layout/derived variables anyway. Only
    the saved layout needs applying in that case, exactly like the
    already-open-elsewhere path above.
    """
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"x": np.arange(200, dtype=float)})
    view = view_io.View(
        view_name="same_file_view",
        source=view_io.SourceDef(parquet_path=str(path), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="x", color="#111111")])],
    )
    view_io.save_view(view)

    win = MainWindow()
    win.load_parquet(str(path))
    data_source_before = win._data_source

    monkeypatch.setattr(
        win, "load_parquet", lambda *a, **k: pytest.fail("load_view should not re-open a file already open")
    )
    win.load_view("same_file_view")

    assert win._data_source is data_source_before  # not replaced by a fresh reopen
    assert win.plot_grid.plots[0].series_names() == ["x"]
    win.close()


def test_load_view_layout_only_path_warns_without_crashing_on_missing_variable(qapp, isolated_config, tmp_path):
    """Same "different file already open" path as above, but the view references a
    variable that doesn't exist at all in the currently open file (not just a
    regenerated version of the same file, as in
    test_view_referencing_a_missing_column_loads_data_without_crashing). Must not
    switch away from the open file or crash -- just warn that the layout couldn't be
    fully applied.
    """
    path_a = tmp_path / "a.parquet"
    _write_parquet(path_a, {"y": np.arange(200, dtype=float)})
    view = view_io.View(
        view_name="v_needs_y",
        source=view_io.SourceDef(parquet_path=str(path_a), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="y", color="#111111")])],
    )
    view_io.save_view(view)

    path_b = tmp_path / "b.parquet"
    _write_parquet(path_b, {"x": np.arange(200, dtype=float)})  # no "y" column at all

    win = MainWindow()
    win.load_parquet(str(path_b))
    win.load_view("v_needs_y")  # must not raise

    assert win._data_source.path == path_b  # still the file that was open, not a.parquet
    assert win.plot_grid.plots[0].series_names() == []  # the one series failed to apply
    win.close()


def test_load_view_applies_matching_variables_even_when_others_are_missing(qapp, isolated_config, tmp_path):
    """Regression test: a view with multiple plots/derived variables used to abort
    applying *everything* as soon as the first missing variable was hit (an exception
    from one derived variable/series propagated out of the whole rebuild), so e.g. a
    view with plot1=x, plot2=y, derived z=x+1 loaded against a file that only has "y"
    ended up showing nothing at all, even though "y" matched fine. Each derived
    variable/series should now be applied independently: only the parts that
    genuinely can't be resolved in the currently open file are skipped.
    """
    path_a = tmp_path / "a.parquet"
    _write_parquet(path_a, {"x": np.arange(200, dtype=float), "y": np.arange(200, dtype=float)})
    view = view_io.View(
        view_name="mixed_view",
        source=view_io.SourceDef(parquet_path=str(path_a), path_type="absolute"),
        plots=[
            view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="x", color="#111111")]),
            view_io.PlotDef(plot_id="p2", series=[view_io.SeriesDef(variable="y", color="#222222")]),
        ],
        derived_variables=[view_io.DerivedVariableDef(name="z", expression="x + 1")],
    )
    view_io.save_view(view)

    path_b = tmp_path / "b.parquet"
    _write_parquet(path_b, {"y": np.arange(1000, 1200, dtype=float)})  # has "y", not "x"

    win = MainWindow()
    win.load_parquet(str(path_b))
    win.load_view("mixed_view")  # must not raise

    assert "z" not in win._derived  # its source "x" doesn't exist in b -> correctly skipped
    all_series = [name for plot in win.plot_grid.plots for name in plot.series_names()]
    assert all_series == ["y"]  # "y" still applied despite "x"/"z" failing elsewhere in the view
    win.close()


def test_load_view_recomputes_derived_variables_from_the_newly_opened_file(qapp, isolated_config, tmp_path):
    """A derived variable is saved as its defining formula, not computed values
    (specification.md 5.5/5.8) -- confirm it's actually recomputed from the newly
    opened file's own data when reused across files, not left stale from the file the
    view was originally saved against.
    """
    path_a = tmp_path / "a.parquet"
    _write_parquet(path_a, {"x": np.arange(200, dtype=float)})
    view = view_io.View(
        view_name="derived_reuse",
        source=view_io.SourceDef(parquet_path=str(path_a), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="z", color="#111111")])],
        derived_variables=[view_io.DerivedVariableDef(name="z", expression="x + 1")],
    )
    view_io.save_view(view)

    path_b = tmp_path / "b.parquet"
    _write_parquet(path_b, {"x": np.arange(1000, 1200, dtype=float)})  # same column name, different data

    win = MainWindow()
    win.load_parquet(str(path_b))
    win.load_view("derived_reuse")

    assert "z" in win._derived
    _x, y, _color = win.plot_grid.plots[0].series_data("z")
    np.testing.assert_array_equal(y, np.arange(1000, 1200, dtype=float) + 1)  # b's x + 1, not a's
    win.close()


def test_load_view_with_missing_source_parquet_shows_error_instead_of_crashing(qapp, isolated_config):
    win = MainWindow()
    win.load_view("this_view_does_not_exist")  # must not raise
    win.close()


def test_save_view_dialog_rejects_unsafe_view_name(qapp, isolated_config, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog

    path = tmp_path / "a.parquet"
    _write_parquet(path, {"x": np.arange(200, dtype=float)})
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("/tmp/evil", True)))

    win = MainWindow()
    win.load_parquet(str(path))
    win.save_view_dialog()  # must not raise; shows a warning dialog (mocked to no-op)

    assert view_io.list_views() == []
    win.close()


def test_time_axis_defaults_to_first_column_regardless_of_its_name(qapp, isolated_config, tmp_path):
    """Regression test: the time axis used to be picked by matching a column name
    against ("time", "timestamp", "datetime"), with no way to override the choice —
    effectively fixed. It now always defaults to the file's first column, whatever
    it's named, and the toolbar combo lists every column with that one selected.
    """
    path = tmp_path / "reordered.parquet"
    table = pa.table({
        "idx": np.arange(200, dtype=float),
        "value": np.arange(200, dtype=float) * 2,
        "time": np.datetime64("2026-01-01", "ms") + np.arange(200, dtype="int64") * np.timedelta64(10, "ms"),
    })
    pq.write_table(table, path)

    win = MainWindow()
    assert win.load_parquet(str(path)) is True
    assert win._time_column == "idx"
    np.testing.assert_array_equal(win._time_values, np.arange(200, dtype=float))
    assert set(win._raw_variables.keys()) == {"value", "time"}
    assert [win.time_axis_combo.itemText(i) for i in range(win.time_axis_combo.count())] == [
        "idx",
        "value",
        "time",
    ]
    assert win.time_axis_combo.currentText() == "idx"
    win.close()


def test_set_time_column_switches_axis_and_restores_old_one_as_variable(qapp, isolated_config, tmp_path):
    path = tmp_path / "a.parquet"
    x_values = np.arange(200, dtype=float)
    _write_parquet(path, {"x": x_values, "y": x_values * 3})

    win = MainWindow()
    win.load_parquet(str(path))
    assert win._time_column == "time"
    old_time_values = win._time_values.copy()

    win.set_time_column("x")

    assert win._time_column == "x"
    np.testing.assert_array_equal(win._time_values, x_values)
    assert "time" in win._raw_variables  # the old time column comes back as an ordinary variable
    np.testing.assert_array_equal(win.variable_values("time"), old_time_values)
    assert "x" not in win._raw_variables  # the new time column is no longer a plottable variable
    assert win.time_axis_combo.currentText() == "x"
    win.close()


def test_set_time_column_refits_an_already_plotted_series_to_the_new_x_data(qapp, isolated_config, tmp_path):
    """Regression test: switching the time axis after already plotting a variable
    used to leave that series' X array pointing at the old time column's data, so
    the curve stayed anchored to a range unrelated to the axis now shown.
    """
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"y": np.linspace(0.0, 100.0, 200)}, n=200)

    win = MainWindow()
    win.load_parquet(str(path))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "y")  # series "y", x = the original "time" column

    win.set_time_column("y")  # "y" becomes the time axis; "time" becomes a plain variable

    x_data, _, _ = plot.series_data("y")
    np.testing.assert_array_equal(x_data, win._time_values)
    win.close()


def test_navigator_shows_the_first_plots_first_series_not_an_arbitrary_column(qapp, isolated_config, tmp_path):
    """Regression test: the navigator used to always render whatever raw variable
    happened to be first in file-column order (`next(iter(self._raw_variables...))`),
    regardless of what the user actually plotted. It should track plot 1's first
    series instead, falling back to that arbitrary-but-deterministic choice only when
    nothing has been plotted anywhere yet.
    """
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"first_col": np.arange(200, dtype=float), "second_col": np.arange(200, dtype=float) * -1})

    win = MainWindow()
    win.load_parquet(str(path))
    # Nothing plotted yet: falls back to the first raw variable (file column order).
    _, overview_y = win.navigator._curve.getData()
    np.testing.assert_array_equal(overview_y, win.variable_values("first_col"))

    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "second_col")  # not the file's first column
    _, overview_y = win.navigator._curve.getData()
    np.testing.assert_array_equal(overview_y, win.variable_values("second_col"))
    win.close()


def test_navigator_tracks_plot_1_after_reordering_plots(qapp, isolated_config, tmp_path):
    path = tmp_path / "a.parquet"
    _write_parquet(path, {"a": np.arange(200, dtype=float), "b": np.arange(200, dtype=float) * -1})

    win = MainWindow()
    win.load_parquet(str(path))
    plot1 = win.plot_grid.plots[0]
    win._on_variable_dropped(plot1, "a")
    plot2 = win.plot_grid.add_plot()
    win._on_variable_dropped(plot2, "b")

    _, overview_y = win.navigator._curve.getData()
    np.testing.assert_array_equal(overview_y, win.variable_values("a"))

    win.plot_grid.move_plot_down(plot1)  # plot2 (series "b") is now first

    assert win.plot_grid.plots[0] is plot2
    _, overview_y = win.navigator._curve.getData()
    np.testing.assert_array_equal(overview_y, win.variable_values("b"))
    win.close()


def test_save_view_preserves_the_current_zoom_range(qapp, isolated_config, monkeypatch, tmp_path):
    """Regression test: PlotDef.y_axis_range / View.x_axis_range round-tripped through
    the JSON schema but save_view_dialog never populated them from the live plots and
    load_view never applied them — so "saving a view" preserved which variables were
    plotted where, but not what range the user was actually looking at.
    """
    from PySide6.QtWidgets import QInputDialog

    path = tmp_path / "a.parquet"
    _write_parquet(path, {"x": np.sin(np.linspace(0.0, 10.0, 1000))}, n=1000)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("myview", True)))

    win = MainWindow()
    win.load_parquet(str(path))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "x")
    plot.setXRange(100.0, 500.0, padding=0)
    plot.getViewBox().setYRange(-0.5, 0.5, padding=0)

    win.save_view_dialog()
    win.close()

    win2 = MainWindow()
    win2.load_view("myview")
    plot2 = win2.plot_grid.plots[0]
    x_range, y_range = plot2.getViewBox().viewRange()
    assert x_range == pytest.approx([100.0, 500.0])
    assert y_range == pytest.approx([-0.5, 0.5])
    win2.close()
