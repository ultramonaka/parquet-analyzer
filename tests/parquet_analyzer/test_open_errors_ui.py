"""MainWindow file-open / time-axis-switch / drop paths fed abnormal data
(detailed_specification.md 4.2, 29, 28.2). Every modal dialog is replaced by a recorder."""
from __future__ import annotations

import datetime
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow

N = 100
T = np.arange(N, dtype=float)


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    shown: list[str] = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append(_n)))
    return shown


@pytest.fixture
def win(qapp, isolated_config):
    w = MainWindow()
    yield w
    w.close()


def _mk(tmp_path, name="f.parquet", **cols):
    path = tmp_path / name
    pq.write_table(pa.table(cols), path)
    return str(path)


def _settle(qapp, win, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if not win._busy_sources:
            return
        time.sleep(0.005)
    raise AssertionError("background work did not finish")


def _combo(win):
    return win.time_axis_combo.currentText()


# -- opening ------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 2])
def test_tiny_files_open_and_plot(qapp, win, tmp_path, n):
    t = np.arange(n, dtype=float)
    assert win.load_parquet(_mk(tmp_path, t=t, v=t)) is True
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    _settle(qapp, win)
    assert win._time_values.shape == (n,)


@pytest.mark.parametrize("bad", ["empty", "garbage", "truncated", "directory", "missing", "blank_path"])
def test_unopenable_paths_report_once_and_keep_the_previous_file(qapp, win, tmp_path, dialogs, bad):
    good = _mk(tmp_path, "good.parquet", t=T, v=T)
    assert win.load_parquet(good)
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    path = tmp_path / "bad.parquet"
    if bad == "empty":
        path.write_bytes(b"")
    elif bad == "garbage":
        path.write_bytes(b"not parquet")
    elif bad == "truncated":
        data = (tmp_path / "good.parquet").read_bytes()
        path.write_bytes(data[:40])
    elif bad == "directory":
        path.mkdir()
    dialogs.clear()
    target = "" if bad == "blank_path" else str(path)
    assert win.load_parquet(target) is False
    assert dialogs == ["critical"]
    # the old file is still fully in place
    assert win._data_source.path.name == "good.parquet"
    assert win.plot_grid.plots[0].series_names() == ["v"]


def test_string_time_column_is_refused_cleanly(win, tmp_path, dialogs):
    assert win.load_parquet(_mk(tmp_path, t=pa.array(["a"] * N), v=T)) is False
    assert dialogs == ["critical"] and win._time_values is None


@pytest.mark.parametrize(
    "arr",
    [pa.array(["a"] * N), pa.array([{"a": 1}] * N), pa.array([[1]] * N), pa.array([1] * N, type=pa.time32("s"))],
    ids=["string", "struct", "list", "time_of_day"],
)
def test_a_non_numeric_column_ahead_of_the_signals_does_not_break_opening(win, tmp_path, arr):
    """Regression: a file whose first non-time column was a string/list column raised a raw
    ValueError out of load_parquet *after* committing the new file (title/state half-updated)."""
    path = _mk(tmp_path, "mixed.parquet", t=T, label=arr, v=T)
    assert win.load_parquet(path) is True
    assert win._data_source.path.name == "mixed.parquet"
    assert list(win._raw_variables) == ["label", "v"]
    assert "mixed.parquet" in win.windowTitle()


def test_only_non_numeric_data_columns_still_opens(win, tmp_path):
    assert win.load_parquet(_mk(tmp_path, t=T, label=pa.array(["a"] * N))) is True


def test_windowed_file_with_unsupported_statistics_opens(qapp, win, tmp_path):
    """Over the eager-load threshold every column is a WindowedColumn whose bounds() come from
    footer statistics; string/float16 statistics used to raise out of column_bounds()."""
    win.settings.eager_load_limit_mb = 0
    path = _mk(tmp_path, t=T, h=pa.array(np.arange(N, dtype=np.float16)), s=pa.array(["a"] * N), v=T)
    assert win.load_parquet(path) is True
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    _settle(qapp, win)
    assert win.plot_grid.plots[0].series_names() == ["v"]


def test_windowed_column_with_infinite_values_keeps_a_finite_y_range(qapp, win, tmp_path):
    """Footer statistics can say max=inf; that must not become the plot's Y range (29.4)."""
    win.settings.eager_load_limit_mb = 0
    v = T.copy()
    v[10] = np.inf
    assert win.load_parquet(_mk(tmp_path, t=T, v=v))
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    _settle(qapp, win)
    y_lo, y_hi = win.plot_grid.plots[0].getViewBox().viewRange()[1]
    assert np.isfinite(y_lo) and np.isfinite(y_hi)


@pytest.mark.parametrize(
    "time_values, ok",
    [
        (np.full(N, np.nan), False),
        (np.where(np.arange(N) % 10 == 5, np.nan, T), False),
        (np.where(np.arange(N) == 7, np.inf, T), False),
        (T[::-1].copy(), False),
        (np.ones(N), True),  # duplicates are allowed
    ],
    ids=["all_nan", "some_nan", "inf", "descending", "constant"],
)
def test_bad_time_axes_open_warn_and_plot_without_hanging(qapp, win, tmp_path, time_values, ok):
    assert win.load_parquet(_mk(tmp_path, t=time_values, v=T))
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    _settle(qapp, win)
    assert win.plot_grid.plots[0].series_names() == ["v"]
    assert win._time_order_report.ok is ok
    # a constant axis is ok but has duplicates (56.2): its label is a note, still shown
    assert win._time_order_label.isVisibleTo(win) is (not ok or win._time_order_report.duplicate_count > 0)
    row_start, row_end = win._visible_row_range()  # must not raise on any of them
    assert 0 <= row_start <= row_end <= N


def test_null_timestamps_in_the_time_column_are_reported_as_non_finite(win, tmp_path):
    """Regression: a null timestamp became ~-9.2e15 s (finite), so no warning was shown."""
    d0 = datetime.datetime(2026, 1, 1)
    ts = pa.array([d0 + datetime.timedelta(seconds=i) if i != 4 else None for i in range(N)], type=pa.timestamp("ms"))
    assert win.load_parquet(_mk(tmp_path, t=ts, v=T))
    assert win._time_order_report.nan_count == 1
    assert win._time_order_report.decreasing_count == 0
    assert win._time_order_label.isVisibleTo(win)


@pytest.mark.parametrize("name", ["", "a b", "1x", "a.b", "x" * 400, "日本語", "a-b", "for", " "])
def test_unusual_column_names_open_and_plot(qapp, win, tmp_path, name):
    assert win.load_parquet(_mk(tmp_path, **{"t": T, name: T}))
    win._on_variable_dropped(win.plot_grid.plots[0], name)
    _settle(qapp, win)
    assert win.plot_grid.plots[0].series_names() == [name]


# -- dropping a variable ------------------------------------------------------------------


@pytest.mark.parametrize("arr", [pa.array(["a"] * N), pa.array([{"a": 1}] * N), pa.array([1] * N, type=pa.time32("s"))], ids=["string", "struct", "time_of_day"])
def test_dropping_a_non_numeric_variable_shows_a_notice_and_leaves_the_plot_alone(win, tmp_path, arr):
    """Regression: the raw ValueError escaped the drop handler (nothing shown to the user)."""
    assert win.load_parquet(_mk(tmp_path, t=T, good=T, x=arr))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "good")
    win._on_variable_dropped(plot, "x")  # must not raise
    assert plot.series_names() == ["good"]
    assert "x" in win._notice_label.text()


def test_dropping_after_the_file_was_deleted_is_a_reported_render_failure(qapp, win, tmp_path):
    win.settings.eager_load_limit_mb = 0  # windowed: reads the disk when drawing
    path = _mk(tmp_path, t=T, v=T, w=T)
    assert win.load_parquet(path)
    (tmp_path / "f.parquet").unlink()
    win._on_variable_dropped(win.plot_grid.plots[0], "w")
    _settle(qapp, win)  # job failures must clear the busy flag (28.2)
    assert not win._busy_sources


# -- switching the time axis ---------------------------------------------------------------


@pytest.mark.parametrize(
    "arr", [pa.array(["a"] * N), pa.array([{"a": 1}] * N), pa.array([[1]] * N), pa.array([1] * N, type=pa.time32("s"))],
    ids=["string", "struct", "list", "time_of_day"],
)
def test_switching_the_time_axis_to_a_non_numeric_column_changes_nothing(win, tmp_path, arr):
    """Regression: set_time_column() assigned self._time_column first, then raised from
    values(): the window kept the old axis array but believed it was the new column (and for a
    struct column left a 2-D 'time axis'), the combo showed the old name."""
    assert win.load_parquet(_mk(tmp_path, t=T, v=T, x=arr))
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "v")
    before = win._time_values
    win.set_time_column("x")  # must not raise
    assert win._time_column == "t"
    assert win._time_values is before and win._time_values.ndim == 1
    assert _combo(win) == "t"
    assert "x" in win._notice_label.text()
    assert list(win._raw_variables) == ["v", "x"]  # unchanged: "t" is still the axis
    # and the window is still fully usable
    win.set_time_column("v")
    assert win._time_column == "v" and _combo(win) == "v"
    win.set_time_column("t")
    assert win._time_column == "t"


def test_switching_the_time_axis_via_the_combo_to_a_bad_column_resets_the_combo(win, tmp_path):
    assert win.load_parquet(_mk(tmp_path, t=T, v=T, s=pa.array(["a"] * N)))
    win.time_axis_combo.setCurrentText("s")  # the real signal path (user picks it)
    assert win._time_column == "t"
    assert _combo(win) == "t"


def test_switching_the_time_axis_after_the_file_vanished_changes_nothing(win, tmp_path):
    path = _mk(tmp_path, t=T, v=T, w=T)
    assert win.load_parquet(path)
    (tmp_path / "f.parquet").unlink()
    win.set_time_column("w")
    assert win._time_column == "t" and _combo(win) == "t"


@pytest.mark.parametrize(
    "arr, finite",
    [(pa.array([None] * N), 0), (pa.array([np.nan] * N), 0), (pa.array([np.inf] * N), 0), (pa.array([True] * N), N)],
    ids=["null_type", "all_nan", "all_inf", "bool"],
)
def test_switching_the_time_axis_to_a_degenerate_numeric_column_warns_and_survives(qapp, win, tmp_path, arr, finite):
    assert win.load_parquet(_mk(tmp_path, t=T, v=T, x=arr))
    win._on_variable_dropped(win.plot_grid.plots[0], "v")
    _settle(qapp, win)
    win.set_time_column("x")
    _settle(qapp, win)
    assert win._time_column == "x"
    assert np.isfinite(win._time_values).sum() == finite
    # all-True 'bool' axis: finite and ascending but every value repeats (56.2 note)
    assert win._time_order_label.isVisibleTo(win) == (finite == 0 or win._time_order_report.duplicate_count > 0)
    win.set_time_column("t")
    _settle(qapp, win)
    assert win._time_column == "t" and win._time_order_report.ok


# -- views ---------------------------------------------------------------------------------


def test_view_with_a_non_numeric_series_loads_the_rest(win, tmp_path, dialogs):
    """Regression: a view naming a string column raised ValueError out of load_view (only
    ExpressionError/KeyError were skipped), aborting the whole load."""
    path = _mk(tmp_path, t=T, good=T, s=pa.array(["a"] * N))
    view = view_io.View(
        view_name="strview",
        source=view_io.SourceDef(parquet_path=path, path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef("s", "#111111"), view_io.SeriesDef("good", "#222222")])],
    )
    view_io.save_view(view)
    assert win.load_parquet(path)
    win.load_view("strview")  # must not raise
    assert win.plot_grid.plots[0].series_names() == ["good"]


def test_view_with_a_non_numeric_time_column_keeps_the_current_axis(win, tmp_path):
    path = _mk(tmp_path, t=T, good=T, s=pa.array(["a"] * N))
    view = view_io.View(
        view_name="strtime",
        source=view_io.SourceDef(parquet_path=path, path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef("good", "#222222")])],
        time_column="s",
    )
    view_io.save_view(view)
    assert win.load_parquet(path)
    win.load_view("strtime")  # must not raise
    assert win._time_column == "t"
    assert win.plot_grid.plots[0].series_names() == ["good"]


def test_derived_variable_from_a_non_numeric_column_is_an_expression_error(win, tmp_path):
    from parquet_analyzer.core.expression import ExpressionError

    assert win.load_parquet(_mk(tmp_path, t=T, v=T, s=pa.array(["a"] * N)))
    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("d", "s + 1")
    assert "d" not in win._derived
