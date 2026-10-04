"""Abnormal input / abnormal operation sequences for the plot UI (plots.ja.md, data 29.4).

Plots, plot grid, navigator, variable panel and expression bar are driven with degenerate
data (all-NaN, one sample, constant, Inf, huge/tiny magnitudes), nonsensical ranges
(zero-width, reversed, far outside the data, NaN/Inf), stale ids and repeated or out-of-order
operations. Invariants checked (CLAUDE.md): no exception escapes a Qt slot, pyqtgraph is never
handed a non-finite X coordinate/range/bound, the navigator follows layout changes, and the
"at least one linked time plot first" structure survives.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox

from parquet_analyzer.core.expression import ExpressionError
from parquet_analyzer.ui.expression_bar import ExpressionBar
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.navigator import NavigatorWidget
from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.variable_panel import VariablePanel

N = 400


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))


@pytest.fixture
def slot_errors(monkeypatch):
    """Exceptions raised inside Qt slots/events are printed through sys.excepthook and swallowed;
    collect them so a test can assert there were none."""
    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(v))
    return errors


def _pump(qapp, seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _settle(qapp, win, timeout=20.0):
    end = time.monotonic() + timeout
    while win._busy_sources and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win._busy_sources


def _write(tmp_path, name, cols):
    path = tmp_path / f"{name}.parquet"
    pq.write_table(pa.table({k: pa.array(v) for k, v in cols.items()}), path)
    return str(path)


def _open(qapp, path):
    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    assert win.load_parquet(path)
    _pump(qapp, 0.05)
    return win


def _assert_finite_everywhere(win):
    """data 29.4: nothing handed to pyqtgraph may be non-finite."""
    for plot in win.plot_grid.plots:
        assert np.isfinite(plot.viewRange()).all(), plot.viewRange()
        for name in plot.series_names():
            cx = plot._series[name]["curve"].xData
            assert cx is None or np.isfinite(cx).all()
    lo, hi = win.navigator._region.getRegion()
    assert np.isfinite(lo) and np.isfinite(hi)


@pytest.fixture
def degenerate_files(tmp_path):
    t = np.arange(N, dtype=float)
    return {
        "allnan": _write(tmp_path, "allnan", {"time": t, "v": np.full(N, np.nan)}),
        "one": _write(tmp_path, "one", {"time": [1.0], "v": [2.0]}),
        "two": _write(tmp_path, "two", {"time": [1.0, 2.0], "v": [2.0, 3.0]}),
        "const": _write(tmp_path, "const", {"time": t, "v": np.full(N, 5.0)}),
        "inf": _write(tmp_path, "inf", {"time": t, "v": np.where(t % 10 == 0, np.inf, np.sin(t))}),
        "huge": _write(tmp_path, "huge", {"time": t, "v": np.sin(t) * 1e300}),
        "tiny": _write(tmp_path, "tiny", {"time": t, "v": np.sin(t) * 1e-300}),
        "ninf": _write(tmp_path, "ninf", {"time": t, "v": np.full(N, -np.inf)}),
    }


# ---------------------------------------------------------------------------
# Degenerate data through the whole plot pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["allnan", "one", "two", "const", "inf", "huge", "tiny", "ninf"])
def test_degenerate_series_plot_home_and_zoom(qapp, isolated_config, degenerate_files, slot_errors, kind):
    win = _open(qapp, degenerate_files[kind])
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "v")
    _settle(qapp, win)
    _assert_finite_everywhere(win)

    win.plot_grid.go_home()
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    # zero-width, reversed, far outside, and Y-only extremes
    for lo, hi in [(5.0, 5.0), (50.0, 10.0), (1e6, 2e6), (-1e6, -1e5)]:
        plot.setXRange(lo, hi, padding=0)
        _settle(qapp, win)
        _assert_finite_everywhere(win)
    win.navigator.rangeSelected.emit(10.0, 3.0)  # navigator reversed
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    assert not slot_errors, slot_errors


@pytest.mark.parametrize("bad", [(float("nan"), float("nan")), (float("-inf"), float("inf")), (0.0, float("nan"))])
def test_navigator_nonfinite_range_is_ignored(qapp, isolated_config, degenerate_files, slot_errors, bad):
    """Regression: a NaN/Inf range from the navigator used to reach ViewBox.setRange, which
    raised `Cannot set range [nan, nan]` from inside the signal slot (data 29.4)."""
    win = _open(qapp, degenerate_files["const"])
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "v")
    _settle(qapp, win)
    before = plot.viewRange()[0]
    win.navigator.rangeSelected.emit(*bad)
    win.plot_grid.set_x_range(*bad)
    assert plot.viewRange()[0] == before
    assert not slot_errors, slot_errors


def test_navigator_set_selected_range_ignores_nonfinite(qapp):
    """Regression: set_selected_range(NaN, ...) stored NaN in the region item."""
    nav = NavigatorWidget()
    nav.set_overview_data(np.arange(10.0), np.arange(10.0))
    nav.set_selected_range(2.0, 4.0)
    for bad in [(math.nan, 1.0), (1.0, math.nan), (-math.inf, math.inf)]:
        nav.set_selected_range(*bad)
        assert nav.selected_range() == (2.0, 4.0)


@pytest.mark.parametrize(
    "x, y",
    [
        (np.array([]), np.array([])),
        (np.arange(10.0), np.arange(3.0)),  # length mismatch
        (np.full(10, np.nan), np.arange(10.0)),  # no finite x at all
        (np.arange(10.0), np.full(10, np.nan)),  # no finite y
        (np.array([5.0]), np.array([1.0])),
        (np.array([np.inf, -np.inf, 1.0]), np.array([1.0, 2.0, 3.0])),
    ],
)
def test_navigator_overview_degenerate_inputs(qapp, x, y):
    nav = NavigatorWidget()
    nav.set_overview_data(x, y)
    xd = nav._curve.xData
    assert xd is None or np.isfinite(xd).all()
    lo, hi = nav.selected_range()
    assert np.isfinite(lo) and np.isfinite(hi)


def test_navigator_overview_keeps_range_when_nothing_finite(qapp):
    nav = NavigatorWidget()
    nav.set_overview_data(np.arange(10.0), np.arange(10.0))
    nav.set_selected_range(2.0, 4.0)
    nav.set_overview_data(np.full(5, np.nan), np.arange(5.0))
    assert nav.selected_range() == (2.0, 4.0)  # "no finite x -> range left alone" (29.4)


def test_navigator_region_drag_emits_range(qapp):
    nav = NavigatorWidget()
    nav.set_overview_data(np.arange(100.0), np.arange(100.0))
    got = []
    nav.rangeSelected.connect(lambda lo, hi: got.append((lo, hi)))
    nav._region.setRegion((10.0, 20.0))  # as a user drag does (emits sigRegionChanged)
    assert got and got[-1] == (10.0, 20.0)
    nav.set_selected_range(30.0, 40.0)  # programmatic sync must not echo
    assert got[-1] == (10.0, 20.0)


# ---------------------------------------------------------------------------
# Drops / variable panel / expression bar
# ---------------------------------------------------------------------------


def _drop_text(plot, text):
    mime = QMimeData()
    mime.setText(text)
    ev = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    plot.dropEvent(ev)


def test_dropping_unknown_text_is_ignored(qapp, isolated_config, degenerate_files, slot_errors):
    """Regression: any text dropped on a plot (another application's selection, a stale name)
    raised ExpressionError('unknown variable') from the drop slot."""
    win = _open(qapp, degenerate_files["const"])
    plot = win.plot_grid.plots[0]
    for text in ["not_a_variable", "multi\nline text", "time", "  v  ", "a" * 10000]:
        _drop_text(plot, text)
    _settle(qapp, win)
    assert plot.series_names() == []
    assert not slot_errors, slot_errors
    _drop_text(plot, "")  # empty text is ignored by dropEvent itself
    _drop_text(plot, "v")  # a real name still works afterwards
    _settle(qapp, win)
    assert plot.series_names() == ["v"]
    assert not slot_errors


def test_dropping_same_variable_twice_keeps_one_series(qapp, isolated_config, degenerate_files, slot_errors):
    win = _open(qapp, degenerate_files["const"])
    plot = win.plot_grid.plots[0]
    _drop_text(plot, "v")
    _drop_text(plot, "v")
    _settle(qapp, win)
    assert plot.series_names() == ["v"]
    assert len(plot.getPlotItem().listDataItems()) == 1
    assert not slot_errors


def test_dropped_plot_variable_after_time_axis_switch(qapp, isolated_config, tmp_path, slot_errors):
    """The old time column becomes a variable, the new one stops being droppable."""
    t = np.arange(N, dtype=float)
    path = _write(tmp_path, "two_axes", {"time": t, "t2": t * 2, "v": np.sin(t)})
    win = _open(qapp, path)
    win.set_time_column("t2")
    plot = win.plot_grid.plots[0]
    _drop_text(plot, "t2")  # now the axis: not a variable
    assert plot.series_names() == []
    _drop_text(plot, "time")  # now a variable
    _settle(qapp, win)
    assert plot.series_names() == ["time"]
    assert not slot_errors


def test_variable_panel_duplicates_and_unknown(qapp, monkeypatch):
    panel = VariablePanel()
    panel.set_variables(["a", "b", "a"], {"d": "a + b"})
    assert panel.is_derived("d") and not panel.is_derived("a")
    panel.add_variable("d", "a - b")  # redefine in place, no second row
    assert len(panel.findItems("d", Qt.MatchFlag.MatchExactly)) == 1
    assert panel._derived["d"] == "a - b"
    panel.add_variable("a")  # existing file column: no duplicate, stays non-derived
    assert not panel.is_derived("a")
    panel.remove_variable("nope")  # unknown: nothing happens
    panel.remove_variable("d")
    panel.remove_variable("d")  # twice
    assert not panel.is_derived("d")
    assert panel.findItems("d", Qt.MatchFlag.MatchExactly) == []
    panel.set_variables([])
    assert panel.count() == 0


def test_variable_panel_context_menu(qapp, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent

    panel = VariablePanel()
    panel.resize(200, 200)
    panel.show()
    panel.set_variables(["a"], {"d": "a * 2"})
    menus = []
    from parquet_analyzer.ui import variable_panel

    class _Menu(QMenu):  # patching QMenu.exec itself is not honoured by PySide6
        def exec(self, *a, **k):  # noqa: A003
            menus.append([x.text() for x in self.actions()])

    monkeypatch.setattr(variable_panel, "QMenu", _Menu)
    rect_a = panel.visualItemRect(panel.findItems("a", Qt.MatchFlag.MatchExactly)[0])
    rect_d = panel.visualItemRect(panel.findItems("d", Qt.MatchFlag.MatchExactly)[0])

    def ctx(point):
        panel.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, panel.mapToGlobal(point)))

    ctx(rect_a.center())  # file column: no menu
    ctx(panel.viewport().rect().bottomRight())  # empty area
    assert menus == []
    ctx(rect_d.center())
    assert len(menus) == 1 and len(menus[0]) == 2


def test_variable_panel_mime_text_is_first_item(qapp):
    panel = VariablePanel()
    panel.set_variables(["a", "b"])
    items = panel.findItems("b", Qt.MatchFlag.MatchExactly)
    assert panel.mimeData(items).text() == "b"


def test_expression_bar_empty_and_failing_input(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    calls = []

    def evaluate(name, expr):
        calls.append((name, expr))
        if "bad" in expr:
            raise ExpressionError("boom")

    bar = ExpressionBar(evaluate)
    bar._on_add_clicked()  # both empty
    bar.name_edit.setText("   ")
    bar.expr_edit.setText("a + 1")
    bar._on_add_clicked()  # blank name
    bar.name_edit.setText("x")
    bar.expr_edit.setText("   ")
    bar._on_add_clicked()  # blank expression
    assert len(warnings) == 3 and calls == []

    bar.expr_edit.setText("bad + 1")
    bar._on_add_clicked()  # evaluation fails: warned, the user's text stays
    assert warnings[-1] == "boom"
    assert bar.name_edit.text() == "x" and bar.expr_edit.text() == "bad + 1"

    bar.expr_edit.setText("  a + 1  ")
    bar._on_add_clicked()  # success: whitespace trimmed, fields cleared
    assert calls[-1] == ("x", "a + 1")
    assert bar.name_edit.text() == "" and bar.expr_edit.text() == ""


def test_expression_bar_edit_mode_transitions(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    def evaluate(name, expr):
        if expr == "bad":
            raise ExpressionError("x")

    bar = ExpressionBar(evaluate)
    bar.cancel_edit()  # not editing: no-op
    assert bar.editing_name is None
    bar.start_edit("d1", "a + b")
    bar.start_edit("d2", "c")  # a second edit replaces the first without cancelling
    assert bar.editing_name == "d2" and bar.name_edit.isReadOnly()
    assert bar.name_edit.text() == "d2" and bar.expr_edit.text() == "c"
    bar.expr_edit.setText("bad")
    bar._on_add_clicked()
    assert bar.editing_name == "d2", "a failed update must stay in edit mode"
    bar.cancel_edit()
    bar.cancel_edit()  # twice
    assert bar.editing_name is None and not bar.name_edit.isReadOnly()
    assert bar.name_edit.text() == "" and bar.expr_edit.text() == ""
    bar.insert_variable_name("a")  # inserting works with no editing state
    bar.insert_variable_name("b")
    assert bar.expr_edit.text() == "ab"


def test_expression_bar_unexpected_exception_propagates_not_swallowed(qapp):
    """Only ExpressionError is a user error; anything else must stay visible (a bug), and the
    bar must not be left half-reset."""
    def evaluate(name, expr):
        raise RuntimeError("bug")

    bar = ExpressionBar(evaluate)
    bar.name_edit.setText("x")
    bar.expr_edit.setText("a")
    with pytest.raises(RuntimeError):
        bar._on_add_clicked()
    assert bar.expr_edit.text() == "a"


# ---------------------------------------------------------------------------
# Plot grid: layout operations
# ---------------------------------------------------------------------------


@pytest.fixture
def grid(qapp):
    g = PlotGridWidget()
    g.resize(400, 600)
    g.show()
    return g


def test_remove_plot_twice_and_unknown(qapp, grid, slot_errors):
    """Regression: removing a plot that is already gone raised ValueError (list.remove)."""
    p0, p1, p2 = grid.plots[0], grid.add_plot(), grid.add_plot()
    grid.remove_plot(p2)
    grid.remove_plot(p2)  # second (queued) request for the same pane
    assert grid.plots == [p0, p1]
    grid.move_plot_to(p2, 0)  # moving a removed plot
    grid.move_plot_up(p1)
    assert grid.plots == [p1, p0]
    grid.set_plot_x_linked(p2, True)
    assert not slot_errors


def test_cannot_remove_last_or_last_time_plot(qapp, grid):
    only = grid.plots[0]
    grid.remove_plot(only)
    assert grid.plots == [only]
    fft = grid.add_plot(x_axis_datetime=False)
    grid.remove_plot(only)  # the last time-domain plot stays even with an FFT plot around
    assert only in grid.plots
    assert grid.anchor_plot() is only
    grid.remove_plot(fft)
    assert grid.plots == [only]


def test_remove_anchor_relinks_survivors(qapp, grid):
    p0, p1, p2 = grid.plots[0], grid.add_plot(), grid.add_plot()
    for p in (p0, p1, p2):
        p.add_series("a", np.arange(10.0), np.arange(10.0))
    grid.remove_plot(p0)
    assert grid.anchor_plot() is p1
    assert grid.linked_plots() == [p1, p2]
    p1.setXRange(2, 4, padding=0)
    assert p2.viewRange()[0] == pytest.approx([2, 4])  # still linked to the new anchor


def test_move_out_of_range_and_across_domains(qapp, grid):
    p0 = grid.plots[0]
    fft = grid.add_plot(x_axis_datetime=False)
    layout = []
    grid.layoutChanged.connect(lambda: layout.append(1))
    for idx in (-5, 99, 1, 0):  # out of range, past the end, same slot, to-same index
        grid.move_plot_to(p0, idx)
    grid.move_plot_to(fft, 0)  # an FFT plot may not precede time plots
    assert grid.plots == [p0, fft]
    assert layout == []  # nothing legal happened, so nothing was announced


def test_unlink_only_linked_plot_refused(qapp, grid):
    p0, p1 = grid.plots[0], grid.add_plot()
    assert grid.set_plot_x_linked(p1, False) is True
    assert grid.set_plot_x_linked(p0, False) is False  # last linked plot: refused, nothing changes
    assert p0.x_linked
    fft = grid.add_plot(x_axis_datetime=False)
    assert grid.set_plot_x_linked(fft, True) is False  # FFT plots have no X link


def test_set_x_range_variants(qapp, grid):
    p0 = grid.plots[0]
    p0.add_series("a", np.arange(100.0), np.arange(100.0))
    grid.set_x_range(100.0, 10.0)  # reversed: pyqtgraph orders it
    lo, hi = p0.viewRange()[0]
    assert lo <= hi and np.isfinite([lo, hi]).all()
    grid.set_x_range(10.0, 10.0)  # zero width: widened by pyqtgraph, never degenerate
    lo, hi = p0.viewRange()[0]
    assert np.isfinite([lo, hi]).all() and hi > lo
    grid.set_x_range(float("nan"), 5.0)  # ignored
    assert p0.viewRange()[0] == [lo, hi]


def test_height_ratios_garbage_is_survivable(qapp, grid, slot_errors):
    grid.add_plot()
    for ratios in ([0, 0], [], [-1, 5, float("nan")], [1.0], [1, 2, 3, 4, 5]):
        grid.set_plot_height_ratios(ratios)
        r = grid.plot_height_ratios()
        assert len(r) == 2 and all(math.isfinite(x) and x > 0 for x in r), (ratios, r)
    assert not slot_errors


def test_many_plots_and_series(qapp, grid, slot_errors):
    x = np.arange(50.0)
    for _ in range(25):
        grid.add_plot()
    assert len(grid.plots) == 26
    for i, p in enumerate(grid.plots):
        for j in range(4):
            p.add_series(f"s{i}_{j}", x, np.sin(x + j))
    _pump(qapp, 0.3)
    grid.go_home()
    grid.set_x_range(5, 10)
    for p in grid.plots[:20]:
        grid.remove_plot(p)
    _pump(qapp, 0.3)
    assert len(grid.plots) == 6
    assert grid.anchor_plot() is grid.plots[0]
    assert not slot_errors


# ---------------------------------------------------------------------------
# Cursors, hover, selection
# ---------------------------------------------------------------------------


def test_cursor_requests_with_stale_ids_and_nonfinite_x(qapp, grid, slot_errors):
    """Regression: a NaN/Inf cursor X reached pyqtgraph and the XY highlight, and a move for an
    already-removed cursor re-created its position (phantom cursor in cursor_times)."""
    p0 = grid.plots[0]
    p0.add_series("a", np.arange(10.0), np.arange(10.0))
    grid.set_cursor_mode_enabled(True)
    grid._on_cursor_add_requested(p0, float("nan"))
    grid._on_cursor_add_requested(p0, float("inf"))
    assert grid.cursor_times() == [] and p0.cursor_ids() == []
    grid._on_cursor_add_requested(p0, 3.0)
    (cid,) = p0.cursor_ids()
    grid._on_cursor_move_requested(p0, cid, float("nan"))
    assert grid.cursor_times() == [3.0]
    grid._on_cursor_remove_requested(p0, cid)
    grid._on_cursor_move_requested(p0, cid, 7.0)  # the drag of a cursor removed meanwhile
    grid._on_cursor_remove_requested(p0, cid)  # and a second remove
    grid._on_cursor_remove_requested(p0, "never_existed")
    assert grid.cursor_times() == [] and p0.cursor_ids() == []
    assert not slot_errors


def test_cursor_outside_data_and_on_empty_or_nan_series(qapp, grid, slot_errors):
    p0 = grid.plots[0]
    p0.add_cursor("c0", 5.0)  # no series at all
    p0.add_series("nan", np.arange(10.0), np.full(10, np.nan))
    p0.add_series("one", np.array([1.0]), np.array([4.0]))
    p0.add_cursor("c1", 1e9)  # far outside the data
    p0.add_cursor("c2", -1e9)
    p0.add_cursor("c0", 6.0)  # same id again repositions
    assert sorted(p0.cursor_ids()) == ["c0", "c1", "c2"]
    p0.set_cursor_x("zz", 1.0)  # unknown ids are ignored
    p0.remove_cursor("zz")
    p0.refresh_cursor_labels()
    text = p0._cursors["c0"]["label"].toPlainText()
    assert "nan" in text
    p0.clear_cursors()
    assert p0.cursor_ids() == []
    assert not slot_errors


def test_hover_without_data_and_with_extreme_x(qapp, grid, slot_errors):
    p0 = grid.plots[0]
    p0.set_hover_enabled(True)
    p0.set_hover_x(5.0)  # no series
    p0.set_hover_x(float("nan"))
    assert p0.hover_text() == ""
    p0.set_hover_x(float("inf"))
    assert p0.hover_text() == ""
    p0.add_series("a", np.arange(10.0), np.arange(10.0))
    p0.set_hover_x(1e9)  # far outside the data: nearest end, no exception
    p0.set_hover_x(-1e9)
    p0.set_hover_x(None)
    assert p0.hover_x() is None
    p0.set_hover_enabled(False)
    p0.set_hover_x(3.0)  # disabled: ignored
    assert not slot_errors


def test_series_operations_on_unknown_names(qapp, grid, slot_errors):
    p0 = grid.plots[0]
    p0.add_series("a", np.arange(10.0), np.arange(10.0))
    p0.remove_series("zz")
    p0.replace_series_y("zz", np.zeros(10))
    p0.set_series_visible("zz", False)
    p0.set_series_selected("zz", True)
    p0.transfer_series("zz", grid.add_plot())
    assert p0.is_series_visible("zz") is False
    grid.select_series(p0, "zz")
    assert grid.selected_series() is None
    assert grid.remove_selected_series() is False
    grid._on_remove_series_requested(p0, "zz")
    grid._on_separate_series_requested(p0, "zz")
    assert p0.series_names() == ["a"]
    assert not slot_errors


def test_add_series_with_same_name_replaces(qapp, grid):
    p0 = grid.plots[0]
    x = np.arange(10.0)
    p0.add_series("a", x, x)
    p0.add_series("a", x, x * 2)
    assert p0.series_names() == ["a"]
    assert len(p0.getPlotItem().listDataItems()) == 1
    assert p0.series_data("a")[1][3] == 6.0


def test_series_all_hidden_then_home(qapp, grid):
    p0 = grid.plots[0]
    p0.add_series("a", np.arange(10.0), np.arange(10.0))
    p0.set_series_visible("a", False)
    p0.go_home()  # all hidden: falls back to all series, never an empty range
    assert np.isfinite(p0.viewRange()).all()
    assert p0.visible_series_names() == []


def test_home_on_empty_plot_is_a_noop(qapp, grid):
    p0 = grid.plots[0]
    before = p0.viewRange()
    p0.go_home()
    p0.redraw()
    assert p0.viewRange() == before


def test_time_axis_data_swap_drops_cursors_and_stays_finite(qapp, grid, slot_errors):
    p0 = grid.plots[0]
    p0.add_series("a", np.arange(10.0), np.arange(10.0))
    grid.set_cursor_mode_enabled(True)
    grid._on_cursor_add_requested(p0, 4.0)
    for new_x in (np.full(10, np.nan), np.full(10, 3.0), np.array([np.inf] * 5 + [1.0] * 5), np.arange(10.0)[::-1].copy()):
        grid.set_time_axis_data(new_x)
        assert np.isfinite(p0.viewRange()).all()
        assert grid.cursor_times() == []
    assert not slot_errors


# ---------------------------------------------------------------------------
# MainWindow-level sequences
# ---------------------------------------------------------------------------


@pytest.fixture
def two_axes_file(tmp_path):
    t = np.arange(N, dtype=float)
    nan_axis = t.copy()
    nan_axis[10:20] = np.nan
    return _write(
        tmp_path,
        "axes",
        {
            "time": t,
            "a": np.sin(t / 10),
            "b": np.cos(t / 10),
            "nanax": nan_axis,
            "desc": t[::-1].copy(),
            "const": np.full(N, 3.0),
            "infax": np.where(t == 5, np.inf, t),
            "allnan": np.full(N, np.nan),
        },
    )


@pytest.mark.parametrize("axis", ["nanax", "desc", "const", "infax", "allnan", "a"])
def test_switch_time_axis_with_everything_open(qapp, isolated_config, two_axes_file, slot_errors, axis):
    """Switching to an awkward time column while plots, cursors, hover and an analysis window
    are open: invariants hold, stats/FFT/XY on the new axis do not raise."""
    win = _open(qapp, two_axes_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win._add_variable_to_plot(plot, "b")
    win.plot_grid.set_cursor_mode_enabled(True)
    win.plot_grid.set_hover_enabled(True)
    win.plot_grid._on_cursor_add_requested(plot, 100.0)
    for name in ("a", "b"):
        win.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    xy = win.open_xy_plot("2d")
    _settle(qapp, win)
    win.set_time_column(axis)
    assert xy not in win._analysis_windows  # row ranges are stale: closed (25.1)
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    assert win.plot_grid.cursor_times() == []
    win.plot_grid.go_home()
    win.plot_grid.plots[0].set_hover_x(50.0)
    win._on_stats_requested()
    _settle(qapp, win)
    win.variable_panel.clearSelection()
    win.variable_panel.findItems("b", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    win._on_fft_requested()
    _settle(qapp, win)
    win.set_time_column("time")
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    assert not slot_errors, slot_errors


def test_layout_sequences_keep_invariants(qapp, isolated_config, two_axes_file, slot_errors):
    win = _open(qapp, two_axes_file)
    grid = win.plot_grid
    win._add_variable_to_plot(grid.plots[0], "a")
    _settle(qapp, win)
    for _ in range(6):
        grid.add_plot()
    win._add_variable_to_plot(grid.plots[3], "b")
    grid.remove_plot(grid.plots[0])  # the anchor
    _settle(qapp, win)
    assert grid.anchor_plot() is grid.plots[0] and grid.plots[0].x_axis_datetime
    _assert_finite_everywhere(win)
    for p in list(grid.plots):  # remove everything removable
        grid.remove_plot(p)
    assert len(grid.plots) == 1
    plot = grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win._add_variable_to_plot(plot, "a")  # same variable twice
    assert plot.series_names() == ["a"]
    plot.remove_series("a")
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    assert not slot_errors, slot_errors


def test_navigator_follows_zoom_after_layout_changes(qapp, isolated_config, two_axes_file):
    win = _open(qapp, two_axes_file)
    grid = win.plot_grid
    win._add_variable_to_plot(grid.plots[0], "a")
    _settle(qapp, win)
    grid.plots[0].setXRange(100, 200, padding=0)
    grid.add_plot()
    grid.remove_plot(grid.plots[1])
    _settle(qapp, win)
    lo, hi = win.navigator.selected_range()
    assert (lo, hi) == pytest.approx((100, 200))


def test_remove_all_series_then_navigator_and_stats(qapp, isolated_config, two_axes_file, slot_errors):
    win = _open(qapp, two_axes_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win._add_variable_to_plot(plot, "b")
    for name in plot.series_names():
        plot.remove_series(name)
    _settle(qapp, win)
    _assert_finite_everywhere(win)
    win._on_stats_requested()  # nothing selected nor plotted: informational message only
    assert not win._stats_workers
    assert not slot_errors


def test_open_new_file_while_jobs_in_flight(qapp, isolated_config, two_axes_file, tmp_path, slot_errors):
    win = _open(qapp, two_axes_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win.variable_panel.findItems("a", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    win._on_stats_requested()
    win._on_fft_requested()
    other = _write(tmp_path, "other", {"time": np.arange(50, dtype=float), "z": np.arange(50, dtype=float)})
    assert win.load_parquet(other)
    _settle(qapp, win)
    assert win.variable_panel.count() == 1
    assert win.plot_grid.plots[0].series_names() == []
    _assert_finite_everywhere(win)
    assert not slot_errors, slot_errors


def test_failed_open_leaves_current_state(qapp, isolated_config, two_axes_file, tmp_path, slot_errors):
    win = _open(qapp, two_axes_file)
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    bad = tmp_path / "garbage.parquet"
    bad.write_bytes(b"not a parquet file")
    assert win.load_parquet(str(bad)) is False
    assert win.load_parquet(str(tmp_path / "missing.parquet")) is False
    only_time = _write(tmp_path, "only_time", {"time": np.arange(5, dtype=float)})
    assert win.load_parquet(only_time) is False  # no data column
    assert win.plot_grid.plots[0].series_names() == ["a"]
    assert "b" in win._raw_variables
    assert not slot_errors


def test_wheel_and_mouse_on_degenerate_plot(qapp, isolated_config, degenerate_files, slot_errors):
    """Real event routing on a one-sample / constant / all-NaN plot: scroll and click must not
    raise or produce a non-finite range."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    for kind in ("one", "const", "allnan"):
        win = _open(qapp, degenerate_files[kind])
        plot = win.plot_grid.plots[0]
        win._add_variable_to_plot(plot, "v")
        _settle(qapp, win)
        win.plot_grid.set_cursor_mode_enabled(True)
        center = plot.viewport().rect().center()
        QTest.mouseClick(plot.viewport(), Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(plot.viewport(), center)
        for dy in (120, -120, 600):
            ev = QWheelEvent(
                QPointF(center), QPointF(plot.viewport().mapToGlobal(center)), QPoint(0, 0), QPoint(0, dy),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
            )
            plot.wheelEvent(ev)
        _settle(qapp, win)
        _assert_finite_everywhere(win)
        win.close()
    assert not slot_errors, slot_errors


def test_series_data_unknown_name_keeps_key_error(qapp, grid):
    """plots 24.8: internal accessor, the name is always known; a KeyError is intended."""
    with pytest.raises(KeyError):
        grid.plots[0].series_data("nope")
