"""Deferred computation of a loaded view's derived variables, through MainWindow (expressions 55.3-55.7).

Real routing: a real Parquet file, a view saved with `view_io.save_view`, `MainWindow.load_view`,
the status-bar cancel button clicked with QTest. Jobs are slowed / blocked by patching
`DeferredColumn.compute`. Every wait is in real time with a deadline."""

from __future__ import annotations

import json
import threading
import time
from datetime import date

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from view_load_helper import wait_view_loaded

from parquet_analyzer import config
from parquet_analyzer.core import oplog
from parquet_analyzer.core.column import DeferredColumn, MaterializedColumn
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow


def _wait(cond, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.002)


class Warnings:
    def __init__(self):
        self.texts: list[str] = []


@pytest.fixture
def warnings(monkeypatch):
    w = Warnings()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: w.texts.append(text)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: w.texts.append("CRITICAL")))
    return w


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, warnings):
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    assert w.load_parquet(str(sample_parquet_path))
    yield w
    w.close()


def _save(path, name="v", derived=None, plots=None):
    derived = derived if derived is not None else [
        ("d1", "a * 2"),
        ("d2", "d1 + b"),  # plotted, depends on d1
        ("d3", "b + c"),  # not plotted
        ("d4", "d3 * 2"),  # not plotted, depends on d3
    ]
    plots = plots if plots is not None else [PlotDef("p1", [SeriesDef("a", "#ff0000"), SeriesDef("d1", "#00aa00")]),
                                             PlotDef("p2", [SeriesDef("d2", "#0000ff")])]
    view_io.save_view(
        View(
            view_name=name,
            source=SourceDef(str(path), "absolute"),
            plots=plots,
            derived_variables=[DerivedVariableDef(n, e) for n, e in derived],
        )
    )


@pytest.fixture
def gate(monkeypatch):
    """Block DeferredColumn.compute() of the named variables until released."""

    class Gate:
        def __init__(self):
            self.event = threading.Event()
            self.blocked: set[str] = set()
            self.entered: list[str] = []
            self.delay = 0.0
            self.fail: set[str] = set()

        def release(self):
            self.event.set()

    g = Gate()
    real = DeferredColumn.compute

    def compute(self):
        name = self.expression
        g.entered.append(name)
        if name in g.blocked:
            assert g.event.wait(30), "gate never released"
        if g.delay:
            time.sleep(g.delay)
        if name in g.fail:
            raise RuntimeError("boom in " + name)
        return real(self)

    monkeypatch.setattr(DeferredColumn, "compute", compute)
    yield g
    g.event.set()


def _series(win):
    return [p.series_names() for p in win.plot_grid.plots]


# -- which variables are computed ------------------------------------------------------------


def test_only_plotted_derived_variables_and_their_inputs_are_computed(win, sample_parquet_path):
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    assert _series(win) == [["a", "d1"], ["d2"]]
    raw = win._raw_variables
    assert raw["d1"].is_ready and raw["d2"].is_ready
    assert isinstance(raw["d3"], DeferredColumn) and not raw["d3"].is_ready
    assert isinstance(raw["d4"], DeferredColumn) and not raw["d4"].is_ready
    assert set(win._derived) == {"d1", "d2", "d3", "d4"}  # the definitions are all there
    a, b = win.variable_values("a"), win.variable_values("b")
    np.testing.assert_allclose(win.plot_grid.plots[0].series_data("d1")[1], a * 2)
    np.testing.assert_allclose(win.plot_grid.plots[1].series_data("d2")[1], a * 2 + b)
    assert not win._raw_variables["d3"].is_ready  # looking at the plots did not compute it


def test_unplotted_variable_is_computed_on_first_use(win, sample_parquet_path):
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "d4")
    assert win._raw_variables["d4"].is_ready and win._raw_variables["d3"].is_ready
    np.testing.assert_allclose(win.variable_values("d4"), (win.variable_values("b") + win.variable_values("c")) * 2)
    assert "d4" in plot.series_names()


def test_a_view_without_derived_series_does_not_start_a_computation(win, sample_parquet_path):
    _save(sample_parquet_path, plots=[PlotDef("p1", [SeriesDef("a", "#ff0000")])])
    win.load_view("v")
    assert win._view_compute is None and "view_compute" not in win._busy_sources
    assert _series(win) == [["a"]]
    assert all(not win._raw_variables[n].is_ready for n in ("d1", "d2", "d3", "d4"))


def test_layout_matches_the_synchronous_result(win, sample_parquet_path):
    """Colours, order, visibility and the number of plots after the background phase."""
    _save(
        sample_parquet_path,
        plots=[
            PlotDef("p1", [SeriesDef("a", "#ff0000"), SeriesDef("d1", "#00aa00", visible=False)], height_ratio=2.0),
            PlotDef("p2", [SeriesDef("d2", "#0000ff")], height_ratio=1.0),
        ],
    )
    win.load_view("v")
    wait_view_loaded(win)
    p1, p2 = win.plot_grid.plots
    assert p1.series_names() == ["a", "d1"] and p2.series_names() == ["d2"]
    assert p1.series_data("d1")[2].lower() == "#00aa00"
    assert p1.is_series_visible("d1") is False
    ratios = win.plot_grid.plot_height_ratios()
    assert ratios[0] == pytest.approx(2 * ratios[1], rel=0.05)
    assert win.plot_grid.plots[0].x_linked and win.plot_grid.plots[1].x_linked


# -- the UI stays alive, busy bookkeeping -------------------------------------------------------


def test_ui_thread_keeps_processing_events_while_computing(win, sample_parquet_path, gate):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    win.load_view("v")
    assert win._view_compute is not None  # load_view returned before the computation ended
    ticks = []
    from PySide6.QtCore import QTimer

    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    end = time.monotonic() + 0.4
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)
    timer.stop()
    assert len(ticks) >= 10, "the event loop was blocked while the job was running"
    assert "view_compute" in win._busy_sources and win._is_settling()
    assert win._progress_bar.isHidden()  # the "rendering" bar is not the one shown for this
    assert not win.plot_grid.isEnabled() and not win.variable_panel.isEnabled()
    assert not win.expression_bar.isEnabled()
    gate.release()
    wait_view_loaded(win)
    assert win.plot_grid.isEnabled() and win.variable_panel.isEnabled() and win.expression_bar.isEnabled()
    assert "view_compute" not in win._busy_sources
    assert _series(win) == [["a", "d1"], ["d2"]]


def test_dependency_order_is_kept_when_loading(win, sample_parquet_path, gate):
    gate.delay = 0.02
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    assert gate.entered.index("a * 2") < gate.entered.index("d1 + b")
    assert "b + c" not in gate.entered and "d3 * 2" not in gate.entered


# -- progress widgets (55.4) ------------------------------------------------------------------


def test_progress_widgets_appear_after_a_while_and_disappear_at_the_end(win, sample_parquet_path, gate):
    gate.blocked = {"d1 + b"}
    _save(sample_parquet_path)
    win.load_view("v")
    prog = win._view_compute_progress
    assert prog.isHidden()  # not at once (no flicker)
    _wait(lambda: not prog.isHidden())
    assert prog.cancel_button.objectName() == "view_compute_cancel_button"
    assert prog.bar.maximum() == 2
    _wait(lambda: "1 / 2" in prog.label.text())  # a1 finished, d2 is blocked
    gate.release()
    wait_view_loaded(win)
    assert prog.isHidden()


def test_progress_estimating_text_before_the_first_one_finishes(win, sample_parquet_path, gate):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    win.load_view("v")
    prog = win._view_compute_progress
    _wait(lambda: not prog.isHidden())
    assert prog.label.text() == tr("view_compute.estimating", done=0, total=2)
    gate.release()
    wait_view_loaded(win)


def test_no_progress_widget_when_it_ends_within_the_delay(win, sample_parquet_path):
    _save(sample_parquet_path)
    win._view_compute_show_timer.setInterval(5_000)  # far longer than any run, even on a slow CI machine
    prog = win._view_compute_progress
    win.load_view("v")
    assert win._view_compute_show_timer.isActive()
    wait_view_loaded(win)
    assert prog.isHidden()
    assert not win._view_compute_show_timer.isActive()  # stopped at the end: it can never fire later


# -- failures ---------------------------------------------------------------------------------


def test_failure_skips_that_variable_and_its_dependents_with_one_warning(win, sample_parquet_path, gate, warnings):
    gate.fail = {"a * 2"}
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    assert _series(win) == [["a"]]  # the plot left empty by the failure is dropped
    assert "d1" not in win._derived and "d2" not in win._derived  # dropped like unregistered ones
    assert {"d3", "d4"} <= set(win._derived)
    assert len(warnings.texts) == 1
    text = warnings.texts[0]
    assert "d1" in text and "d2" in text and "boom in a * 2" in text
    assert "- d2" in text and text.count("d2") == 1  # listed once, with its reason
    assert win.plot_grid.isEnabled()
    names = [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]
    assert "d1" not in names and "d3" in names


# -- another view / file while computing (55.3) --------------------------------------------------


def test_opening_another_view_drops_the_running_one(win, sample_parquet_path, gate, warnings):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path, name="slow")
    _save(sample_parquet_path, name="plain", derived=[], plots=[PlotDef("p1", [SeriesDef("c", "#123456")])])
    win.load_view("slow")
    assert win._view_compute is not None
    win.load_view("plain")
    assert win._view_compute is None and win.plot_grid.isEnabled()
    assert _series(win) == [["c"]]
    gate.release()
    time.sleep(0.2)
    for _ in range(20):
        QApplication.processEvents()
    assert _series(win) == [["c"]]  # the stale result changed nothing
    assert win._derived == {}
    assert warnings.texts == []


def test_opening_a_file_drops_the_running_view(win, sample_parquet_path, gate):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    win.load_view("v")
    assert win._view_compute is not None
    assert win.load_parquet(str(sample_parquet_path))
    assert win._view_compute is None and win.plot_grid.isEnabled() and win.expression_bar.isEnabled()
    assert "view_compute" not in win._busy_sources
    gate.release()
    time.sleep(0.2)
    for _ in range(20):
        QApplication.processEvents()
    assert _series(win) == [[]]
    assert win._derived == {}


def test_closing_the_window_while_computing_is_clean(qapp, isolated_config, sample_parquet_path, gate, warnings):
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    w.load_view("v")
    assert w._view_compute is not None
    w.close()
    assert w._view_compute is None
    gate.release()
    time.sleep(0.2)
    for _ in range(10):
        QApplication.processEvents()


# -- cancel (55.5) ------------------------------------------------------------------------------


def test_cancel_lays_out_what_is_computed_and_lists_the_rest(win, sample_parquet_path, gate, warnings):
    gate.blocked = {"d1 + b"}  # d1 finishes, d2 stays blocked
    _save(sample_parquet_path)
    win.load_view("v")
    prog = win._view_compute_progress
    _wait(lambda: not prog.isHidden())
    _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
    QTest.mouseClick(prog.cancel_button, Qt.MouseButton.LeftButton)  # the real button
    assert win._view_compute is None and prog.isHidden()
    assert win.plot_grid.isEnabled() and "view_compute" not in win._busy_sources
    assert _series(win)[0] == ["a", "d1"]
    assert "d2" not in sum(_series(win), [])
    assert len(warnings.texts) == 1
    assert f"d2 ({tr('view.skipped_cancelled')})" in warnings.texts[0]
    assert "d1" not in warnings.texts[0].replace("d1 + b", "")  # d1 itself was laid out
    assert "d2" in win._derived and "d4" in win._derived  # definitions stay
    item_names = [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]
    assert "d2" in item_names

    gate.release()  # the background job ends later: nothing changes on screen
    time.sleep(0.2)
    for _ in range(20):
        QApplication.processEvents()
    assert _series(win)[0] == ["a", "d1"] and len(warnings.texts) == 1

    # ... and the variable can still be plotted afterwards (computed on demand, or already done)
    win._add_variable_to_plot(win.plot_grid.plots[0], "d2")
    np.testing.assert_allclose(
        win.plot_grid.plots[0].series_data("d2")[1], win.variable_values("a") * 2 + win.variable_values("b")
    )


def test_cancel_when_nothing_is_computed_still_lays_out_raw_series(win, sample_parquet_path, gate, warnings):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    win.load_view("v")
    prog = win._view_compute_progress
    _wait(lambda: not prog.isHidden())
    QTest.mouseClick(prog.cancel_button, Qt.MouseButton.LeftButton)
    assert _series(win)[0] == ["a"]
    text = warnings.texts[0]
    assert f"d1 ({tr('view.skipped_cancelled')})" in text and f"d2 ({tr('view.skipped_cancelled')})" in text
    gate.release()


# -- registry changes / edits do not force the computation (55.2) ---------------------------------


def test_update_registry_rebuilds_uncomputed_columns_without_computing(win, gate):
    win.function_registry.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 2")
    win._on_add_derived_variable("u1", "twice(b)", deferred=True)  # never computed
    win._on_add_derived_variable("u2", "twice(a)")  # eager: computed
    win._on_add_derived_variable("u3", "u1 + 1", deferred=True)  # depends on u1, never computed
    assert isinstance(win._raw_variables["u2"], MaterializedColumn)
    gate.entered.clear()
    new = UserFunction("twice", ["x"], [], "x * 3")
    failures = win.update_registry(user_functions={"twice": new}, touched={"twice"})
    assert failures == []
    u1, u3 = win._raw_variables["u1"], win._raw_variables["u3"]
    assert isinstance(u1, DeferredColumn) and not u1.is_ready
    assert isinstance(u3, DeferredColumn) and not u3.is_ready
    assert u1.registry.user_functions["twice"] is new  # the new snapshot
    assert gate.entered == []  # nothing was computed by the rebuild
    np.testing.assert_allclose(win.variable_values("u2"), win.variable_values("a") * 3)  # computed ones: as before
    np.testing.assert_allclose(win.variable_values("u3"), win.variable_values("b") * 3 + 1)


def test_update_registry_failure_is_still_all_or_nothing_for_uncomputed(win):
    win.function_registry.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 2")
    win._on_add_derived_variable("u1", "twice(b)", deferred=True)
    old = win._raw_variables["u1"]
    bad = UserFunction("twice", ["x"], [], "nosuchname + x")
    failures = win.update_registry(user_functions={"twice": bad}, touched={"twice"})
    assert [n for n, _ in failures] == ["u1"]
    assert win._raw_variables["u1"] is old
    assert win.function_registry.user_functions["twice"].expression == "x * 2"


def test_editing_a_variable_keeps_its_uncomputed_dependents_uncomputed(win, sample_parquet_path):
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    assert not win._raw_variables["d4"].is_ready
    win.edit_derived_variable("d3", "b - c")  # d4 = d3 * 2 depends on it
    assert isinstance(win._raw_variables["d3"], MaterializedColumn)  # interactive edit: eager
    d4 = win._raw_variables["d4"]
    assert isinstance(d4, DeferredColumn) and not d4.is_ready
    np.testing.assert_allclose(win.variable_values("d4"), (win.variable_values("b") - win.variable_values("c")) * 2)


def test_registry_snapshot_is_independent_of_the_live_registry(win, sample_parquet_path):
    _save(sample_parquet_path)
    win.load_view("v")
    wait_view_loaded(win)
    col = win._raw_variables["d3"]
    assert isinstance(col.registry, FunctionRegistry) and col.registry is not win.function_registry


# -- operation log (55.3, 55.5) -----------------------------------------------------------------


def _settle_log(timeout=10.0):
    deadline = time.monotonic() + timeout
    while oplog.has_pending() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _records():
    oplog.flush()
    path = oplog.log_path_for(date.today())
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def log(qapp, isolated_config):
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


def test_cancel_and_the_computation_are_recorded(log, win, sample_parquet_path, gate):
    gate.blocked = {"d1 + b"}
    _save(sample_parquet_path)
    _settle_log()  # the fixture's file open is a separate operation
    win.load_view("v")
    prog = win._view_compute_progress
    _wait(lambda: not prog.isHidden())
    # the operation log must not write the load_view result while the computation runs
    assert win._is_settling()
    QTest.mouseClick(prog.cancel_button, Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 10
    while oplog.has_pending() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    recs = _records()
    ops = [r["action"] for r in recs if r["kind"] == "op"]
    assert "load_view" in ops and "view_compute_cancel" in ops
    jobs = [r for r in recs if r["kind"] == "job" and r["job"] == "view_compute"]
    assert any(r["event"] == "cancel" and "1 of 2" in str(r.get("target")) for r in jobs)
    cancel = [r for r in recs if r["kind"] == "op" and r["action"] == "view_compute_cancel"][0]
    assert any(r["kind"] == "result" and r["op"] == cancel["seq"] for r in recs)
    gate.release()


def test_load_view_result_waits_for_the_end_of_the_computation(log, win, sample_parquet_path, gate):
    gate.blocked = {"a * 2"}
    _save(sample_parquet_path)
    _settle_log()
    win.load_view("v")
    load_op = [r for r in _records() if r["kind"] == "op" and r["action"] == "load_view"][-1]
    end = time.monotonic() + 0.8
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not [r for r in _records() if r["kind"] == "result" and r["op"] == load_op["seq"]]
    gate.release()
    wait_view_loaded(win)
    deadline = time.monotonic() + 10
    while oplog.has_pending() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    results = [r for r in _records() if r["kind"] == "result" and r["op"] == load_op["seq"]]
    assert len(results) == 1
    assert len(results[0]["state"]["plots"]) == 2
    # the layout logged after the event loop had turned still belongs to the same operation
    children = [r for r in _records() if r["kind"] == "op" and r.get("parent") == load_op["seq"]]
    assert any(r["action"] == "add_plot" for r in children)


# -- review fixes (expressions 55.3, 55.2, 55.5) ---------------------------------------------------


def test_registry_and_layout_entry_points_are_locked_while_computing(win, sample_parquet_path, gate, warnings):
    win.function_registry.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 2")
    _save(sample_parquet_path, derived=[("d1", "twice(a)"), ("d2", "d1 + b")],
          plots=[PlotDef("p1", [SeriesDef("a", "#ff0000"), SeriesDef("d1", "#00aa00")]),
                 PlotDef("p2", [SeriesDef("d2", "#0000ff")])])
    gate.blocked = {"d1 + b"}
    win.load_view("v")
    _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
    locked = ("user_functions", "maps", "signal_aliases", "toggle_signal_aliases", "add_plot", "save_view")
    assert all(not win._actions[k].isEnabled() for k in locked if k in win._actions)
    assert not win.time_axis_combo.isEnabled()
    # a registry change that slips through (an already open manager) is refused, nothing is swapped
    col = win._raw_variables["d2"]
    failures = win.update_registry(
        user_functions={"twice": UserFunction("twice", ["x"], [], "x * 3")}, touched={"twice"}
    )
    assert failures and win._raw_variables["d2"] is col
    win.set_time_column("c")  # refused as well
    assert win._time_column == "time"
    gate.release()
    wait_view_loaded(win)
    assert _series(win) == [["a", "d1"], ["d2"]]  # d2 was not reported as cancelled
    assert warnings.texts == []
    assert all(win._actions[k].isEnabled() for k in locked if k in win._actions)
    assert win.time_axis_combo.isEnabled()


def test_locks_are_released_on_cancel_failure_and_abort(win, sample_parquet_path, gate, warnings):
    _save(sample_parquet_path)
    for how in ("cancel", "failure", "abort"):
        gate.event.clear()
        gate.blocked = {"d1 + b"} if how != "failure" else set()
        gate.fail = {"a * 2"} if how == "failure" else set()
        win.load_view("v")
        if how == "failure":
            wait_view_loaded(win)
        else:
            _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
            assert not win._actions["add_plot"].isEnabled()
            if how == "cancel":
                win._view_compute_progress.cancel_button.click()
            else:
                win._abort_view_compute()
        assert win._actions["add_plot"].isEnabled() and win._actions["save_view"].isEnabled(), how
        assert win.time_axis_combo.isEnabled(), how
        gate.release()
        time.sleep(0.1)
        QApplication.processEvents()


def test_a_broken_view_does_not_throw_away_the_running_one(win, sample_parquet_path, gate, monkeypatch):
    _save(sample_parquet_path)
    gate.blocked = {"d1 + b"}
    win.load_view("v")
    _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
    runner = win._view_compute
    win.load_view("does_not_exist")
    assert win._view_compute is runner and runner.active
    assert win._view_name == "v"
    gate.release()
    wait_view_loaded(win)
    assert _series(win) == [["a", "d1"], ["d2"]]


def test_abort_leaves_the_navigator_in_step_with_the_grid(win, sample_parquet_path, gate):
    _save(sample_parquet_path)
    gate.blocked = {"d1 + b"}
    win.load_view("v")
    _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
    win._abort_view_compute()
    assert win._view_compute is None and win.plot_grid.isEnabled()
    gate.release()


def test_deferred_input_makes_a_windowed_dependent_deferred_too(qapp, isolated_config, sample_parquet_path, warnings):
    w = MainWindow()
    w.settings.eager_load_limit_mb = 0  # every column windowed
    assert w.load_parquet(str(sample_parquet_path))
    _save(sample_parquet_path, derived=[("d1", "rolling_mean(a, 2+3)"), ("d2", "d1 + a")],
          plots=[PlotDef("p1", [SeriesDef("a", "#ff0000")])])
    w.load_view("v")
    d1, d2 = w._raw_variables["d1"], w._raw_variables["d2"]
    assert isinstance(d1, DeferredColumn) and isinstance(d2, DeferredColumn)
    assert not d1.is_ready and not d2.is_ready  # nothing was computed on the UI thread
    w.close()


def test_grid_shows_the_whole_axis_while_the_view_is_computed(win, sample_parquet_path, gate):
    _save(sample_parquet_path)
    gate.blocked = {"d1 + b"}
    win.load_view("v")
    _wait(lambda: win._view_compute is not None and win._view_compute.done == 1)
    lo, hi = win.plot_grid.plots[0].getViewBox().viewRange()[0]
    nlo, nhi = win.navigator.selected_range()
    tlo, thi = win._time_finite_bounds()
    assert lo == pytest.approx(tlo) and hi == pytest.approx(thi, rel=1e-3)
    assert nlo == pytest.approx(tlo) and nhi == pytest.approx(thi, rel=1e-3)
    gate.release()
    wait_view_loaded(win)


def test_cancel_keeps_a_plot_emptied_by_the_cancel(win, sample_parquet_path, gate, warnings):
    gate.blocked = {"d1 + b"}
    _save(
        sample_parquet_path,
        plots=[
            PlotDef("p1", [SeriesDef("a", "#ff0000")], height_ratio=1.0),
            PlotDef("p2", [SeriesDef("d2", "#0000ff")], height_ratio=3.0, x_linked=False),  # only cancelled
            PlotDef("p3", [SeriesDef("d2", "#0000ff"), SeriesDef("nosuch", "#000000")], height_ratio=1.0),  # cancelled + missing
            PlotDef("p4", [SeriesDef("nosuch2", "#000000")]),  # emptied for another reason
        ],
    )
    win.load_view("v")
    prog = win._view_compute_progress
    _wait(lambda: not prog.isHidden())
    QTest.mouseClick(prog.cancel_button, Qt.MouseButton.LeftButton)
    assert _series(win) == [["a"], [], []]  # p4 is gone, p2 and p3 stay as empty panes
    assert win.plot_grid.plots[1].x_linked is False and win.plot_grid.plots[2].x_linked is True
    ratios = win.plot_grid.plot_height_ratios()
    assert ratios[1] > ratios[0]
    gate.release()
    time.sleep(0.2)
    QApplication.processEvents()
    assert _series(win) == [["a"], [], []]
