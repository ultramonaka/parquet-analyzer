"""Self-tests of the side-effect mechanism (app 45.3): the watchers must really fail."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QMessageBox
from side_effects_helper import SideEffectFailure, path_allowed, settle, unexpected_changes
from side_effects_helper_start import ctx, log  # noqa: F401  (fixtures)

from parquet_analyzer.core import oplog


def test_noop_passes(ctx):
    assert ctx.run("noop", lambda: None, allowed=()) == {}


def test_moving_the_range_fails_with_path_and_values(ctx):
    lo, hi = ctx.win.navigator.selected_range()
    span = hi - lo

    def fake():  # a "button" that nudges the navigator region
        ctx.win.navigator.set_selected_range(lo + span * 0.1, hi + span * 0.1)

    with pytest.raises(SideEffectFailure) as e:
        ctx.run("fake_range_move", fake, allowed=())
    msg = str(e.value)
    assert "fake_range_move" in msg and "navigator.region" in msg
    assert repr(lo) in msg  # the old value is listed


def test_range_move_passes_when_allowed(ctx):
    lo, hi = ctx.win.navigator.selected_range()
    ctx.run("fake", lambda: ctx.win.navigator.set_selected_range(lo + 1, hi + 1), allowed=["navigator.region*"])


def test_reload_fails(ctx):
    path = ctx.win.state_snapshot()["file"]["path"]
    with pytest.raises(SideEffectFailure) as e:
        # even "anything may change" must not excuse a reload
        ctx.run("fake_reload", lambda: ctx.win.load_parquet(path), allowed=["*"])
    assert "load_parquet" in str(e.value) and "data source re-created" in str(e.value)


def test_unexpected_dialog_fails_and_declared_one_passes(ctx):
    def warn():
        QMessageBox.warning(ctx.win, "t", "x")

    with pytest.raises(SideEffectFailure) as e:
        ctx.run("fake_warning", warn, allowed=())
    assert "unexpected dialog: QMessageBox.warning" in str(e.value)
    ctx.run("declared", warn, allowed=(), expected_dialogs=["QMessageBox.warning"])


def test_exception_record_fails(ctx):
    def boom():
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            oplog.record_exception(*sys.exc_info())

    with pytest.raises(SideEffectFailure) as e:
        ctx.run("fake_exception", boom, allowed=())
    assert "exception record" in str(e.value) and "boom" in str(e.value)


def test_uncaught_exception_is_recorded_through_the_excepthook(ctx):
    """The ctx fixture installs the excepthooks, so an exception that reaches sys.excepthook
    (what an uncaught error in a Qt slot does) becomes a failure."""

    def boom():
        try:
            raise ValueError("from a slot")
        except ValueError:
            sys.excepthook(*sys.exc_info())

    with pytest.raises(SideEffectFailure) as e:
        ctx.run("fake_uncaught", boom, allowed=())
    assert "from a slot" in str(e.value)


def test_pattern_matching():
    assert path_allowed("plots.p1.x_range", ["plots.*.x_range"])
    assert not path_allowed("plots.p1.series", ["plots.*.x_range"])
    assert path_allowed("plots.p1.series_info.a.points", ["plots.*.series_info.*.points", "navigator.region"])
    assert not path_allowed("navigator.region", ["plots.*.x_range"])
    changes = {"plots.p1.x_range": [1, 2], "plots.p1.series": [["a"], []]}
    assert list(unexpected_changes(changes, ["plots.*.x_range"])) == ["plots.p1.series"]


def test_settle_times_out(ctx):
    ctx.win._busy_sources.add("fake")  # never clears
    try:
        with pytest.raises(AssertionError, match="did not settle"):
            settle(ctx.win, timeout=0.3)
    finally:
        ctx.win._busy_sources.discard("fake")
