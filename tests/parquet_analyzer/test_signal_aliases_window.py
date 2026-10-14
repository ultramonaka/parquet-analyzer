"""Signal aliases, stage 1: MainWindow integration (persistence 54.2/54.3/54.4/54.5, test plan 54.7)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer import features
from parquet_analyzer.core.column import LazyColumn, WindowedColumn
from parquet_analyzer.core.oplog import diff_states
from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io import signal_aliases as alias_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog

DICT = {"rpm": ["EngSpd", "NE_rpm"], "clock": ["t2"], "plain": ["nothere"]}


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "critical": [], "information": [], "question": []}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: calls["information"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: calls["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: calls["critical"].append(a[2])))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: calls["question"].append(a[2]) or QMessageBox.StandardButton.Yes),
    )
    return calls


@pytest.fixture
def ops(monkeypatch):
    """Captured log_op calls of main_window: [(name, kwargs)]."""
    recorded: list[tuple[str, dict]] = []
    real = main_window_module.log_op

    def fake(op, /, **kw):
        recorded.append((op, kw))
        return real(op, **kw)

    monkeypatch.setattr(main_window_module, "log_op", fake)
    return recorded


def _write(path, cols):
    pq.write_table(pa.table({k: pa.array(v) for k, v in cols.items()}), path)
    return path


def _cols(first_signal="EngSpd", n=2000):
    i = np.arange(n, dtype="float64")
    return {"t": i, "t2": 5000.0 + i * 10.0, first_signal: np.sin(i / 50) * 100, "other": np.cos(i / 30)}


@pytest.fixture
def file_a(tmp_path):
    return _write(tmp_path / "a.parquet", _cols("EngSpd"))


@pytest.fixture
def file_b(tmp_path):
    return _write(tmp_path / "b.parquet", _cols("NE_rpm"))


@pytest.fixture
def make_win(qapp, isolated_config, boxes):
    wins = []

    def make(aliases=DICT):
        if aliases:
            alias_io.save_signal_aliases(aliases)
        w = MainWindow()
        wins.append(w)
        return w

    yield make
    for w in wins:
        w.close()


def _wait(qapp, win):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        qapp.processEvents()
        if not win._busy_sources:
            break


def _panel_names(win):
    return [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]


def _combo_items(win):
    c = win.time_axis_combo
    return [c.itemText(i) for i in range(c.count())]


# -- opening a file ------------------------------------------------------------------------


def test_alias_listed_instead_of_source_and_same_column_object(make_win, file_a):
    win = make_win()
    assert win.load_parquet(str(file_a))
    # the source name disappears from the list; the alias takes its position (t2 is aliased "clock")
    assert _panel_names(win) == ["clock", "rpm", "other"]
    assert _combo_items(win) == ["t", "clock", "rpm", "other"]
    assert "plain" not in win._file_columns  # no candidate in this file
    assert win._alias_sources == {"rpm": "EngSpd", "clock": "t2"}
    assert win._file_columns["rpm"] is win._file_columns["EngSpd"]  # the same object, not a copy
    assert win._raw_variables["rpm"] is win._raw_variables["EngSpd"]  # sources stay usable
    # item text is the alias itself; tooltip names the source column
    item = win.variable_panel.item(1)
    assert item.text() == "rpm" and item.toolTip() == tr("variable.alias_source", source="EngSpd")
    assert win.variable_panel.item(2).toolTip() == ""


def test_candidate_order_picks_whichever_the_file_has(make_win, file_a, file_b):
    win = make_win()
    win.load_parquet(str(file_b))
    assert win._alias_sources["rpm"] == "NE_rpm"
    assert win.variable_panel.item(1).toolTip() == tr("variable.alias_source", source="NE_rpm")
    win.load_parquet(str(file_a))
    assert win._alias_sources["rpm"] == "EngSpd"


def test_alias_equal_to_column_name_is_not_attached(make_win, file_a):
    win = make_win({"other": ["EngSpd"]})
    win.load_parquet(str(file_a))
    assert win._alias_sources == {}
    assert win._file_columns["other"] is not win._file_columns["EngSpd"]
    assert "EngSpd" in _panel_names(win)


def test_plot_alias_values_equal_source_and_expressions_accept_both(make_win, file_a, qapp):
    win = make_win()
    win.load_parquet(str(file_a))
    win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
    _wait(qapp, win)
    assert win.plot_grid.plots[0].series_names() == ["rpm"]  # legend / series name is the alias
    np.testing.assert_array_equal(win.variable_values("rpm"), win.variable_values("EngSpd"))
    win._on_add_derived_variable("d1", "rpm * 2")
    win._on_add_derived_variable("d2", "EngSpd * 2")
    np.testing.assert_array_equal(win.variable_values("d1"), win.variable_values("d2"))


def test_windowed_columns_share_the_alias_too(make_win, file_a):
    win = make_win()
    win.settings.eager_load_limit_mb = 0  # every column is over the threshold -> WindowedColumn
    win.load_parquet(str(file_a))
    col = win._file_columns["rpm"]
    assert isinstance(col, WindowedColumn) and col is win._file_columns["EngSpd"]
    np.testing.assert_array_equal(col.window(10, 20), win._file_columns["EngSpd"].window(10, 20))
    win._on_add_derived_variable("d1", "rpm + 1")  # a derived variable over a windowed alias
    np.testing.assert_allclose(win.variable_window("d1", 5, 9), win.variable_window("EngSpd", 5, 9) + 1)


def test_lazy_column_is_shared_and_not_loaded_by_listing(make_win, file_a):
    win = make_win()
    win.load_parquet(str(file_a))
    col = win._file_columns["rpm"]
    assert isinstance(col, LazyColumn) and col is win._file_columns["EngSpd"]


def test_alias_cannot_chain(make_win, file_a):
    win = make_win({"A": ["B"], "B": ["EngSpd"]})
    win.load_parquet(str(file_a))
    assert win._alias_sources == {"B": "EngSpd"}


def test_derived_variable_named_like_an_alias_is_refused(make_win, file_a):
    win = make_win()
    win.load_parquet(str(file_a))
    from parquet_analyzer.core.expression import ExpressionError

    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("rpm", "other + 1")
    # an alias of the time column is refused too (it is not in _raw_variables)
    win.set_time_column("clock")
    assert "clock" not in win._raw_variables
    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("clock", "other + 1")


# -- time axis -----------------------------------------------------------------------------


def test_time_axis_by_alias(make_win, file_a, qapp, monkeypatch, tmp_path, ops):
    win = make_win()
    win.load_parquet(str(file_a))
    win.time_axis_combo.setCurrentText("clock")  # what the user does; goes through currentTextChanged
    assert win._time_column == "t2"  # the data itself is the source column
    assert win.time_axis_combo.currentText() == "clock"
    assert ("set_time_column", {"name": "clock"}) in ops
    # neither the source nor any alias of the time column is a variable
    for name in ("t2", "clock"):
        assert name not in win._raw_variables and name not in win.visible_column_names()
    assert "t" in win._raw_variables and "t" in _panel_names(win)
    assert _combo_items(win) == ["t", "clock", "rpm", "other"]
    # saved views carry the displayed name
    win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
    _wait(qapp, win)
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v_alias", True)))
    win.save_view_dialog()
    assert json.loads(view_io.view_path("v_alias").read_text())["time_column"] == "clock"
    assert win.state_snapshot()["file"]["time_column"] == "clock"


def test_picking_the_source_name_programmatically_maps_to_the_same_column(make_win, file_a):
    win = make_win()
    win.load_parquet(str(file_a))
    win.set_time_column("clock")
    win.set_time_column("t2")  # a no-op: already the time column
    assert win._time_column == "t2" and win.time_axis_combo.currentText() == "clock"


def test_view_with_alias_time_column_restores_in_a_file_with_other_names(make_win, file_a, file_b, qapp, monkeypatch):
    win = make_win()
    win.load_parquet(str(file_a))
    win.set_time_column("clock")
    win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
    win._add_variable_to_plot(win.plot_grid.plots[0], "other")
    win._on_add_derived_variable("twice", "rpm * 2")
    win._add_variable_to_plot(win.plot_grid.plots[0], "twice")
    _wait(qapp, win)
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v1", True)))
    win.save_view_dialog()
    saved = json.loads(view_io.view_path("v1").read_text())
    assert [s["variable"] for s in saved["plots"][0]["series"]] == ["rpm", "other", "twice"]

    win2 = make_win(None)
    win2.load_parquet(str(file_b))  # NE_rpm instead of EngSpd
    warnings = win2_warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: win2_warnings.append(a[2])))
    win2.load_view("v1")
    _wait(qapp, win2)
    assert warnings == []  # nothing skipped
    assert win2._time_column == "t2"
    assert win2.plot_grid.plots[0].series_names() == ["rpm", "other", "twice"]
    assert win2._alias_sources["rpm"] == "NE_rpm"


def test_view_with_source_name_time_column_still_loads(make_win, file_a, monkeypatch):
    """An older view stored the source name of a column that has an alias now (_file_columns keeps it)."""
    from parquet_analyzer.io.view import PlotDef, SeriesDef, SourceDef, View

    view_io.save_view(View("old", SourceDef(str(file_a), "absolute"), plots=[PlotDef("p1", [SeriesDef("EngSpd", "#ff0000")])], time_column="t2"))
    win = make_win()
    win.load_parquet(str(file_a))
    win.load_view("old")
    assert win._time_column == "t2" and win.time_axis_combo.currentText() == "clock"
    assert win.plot_grid.plots[0].series_names() == ["EngSpd"]


def test_xy_histogram_choices_use_aliases_only(make_win, file_a, qapp, monkeypatch):
    win = make_win()
    win.load_parquet(str(file_a))
    win.set_time_column("clock")
    win._on_add_derived_variable("twice", "rpm * 2")
    xy = win.open_xy_plot()
    try:
        xs = [xy._x_combo.itemText(i) for i in range(xy._x_combo.count())]
        assert xs[0] == "clock"  # the time axis is offered first, under its alias
        assert "EngSpd" not in xs and "t2" not in xs
        assert xs[1:] == ["t", "rpm", "other", "twice"] == xy._data_names()
        assert xy._is_time("clock")  # compared by source column (the display name may change)
    finally:
        xy.close()
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)
    hist = win.open_histogram()
    try:
        assert hist._candidates() == ["t", "rpm", "other", "twice"]
    finally:
        hist.close()


# -- name clashes --------------------------------------------------------------------------


def test_functions_and_maps_cannot_take_any_dictionary_alias(make_win, file_a, tmp_path):
    win = make_win()  # no file open: "plain" is attached nowhere, yet it is reserved
    assert {"rpm", "clock", "plain"} <= win.reserved_names()
    with pytest.raises(maps_io.MapError):
        maps_io.validate_map_name("plain", win.reserved_names())
    # the alias itself may not take a function / map name
    assert "plain" not in win.callable_names()
    problems = win.save_signal_aliases({**DICT, "sin": ["x"]})
    assert tr("alias.err_reserved", alias="sin") == problems


def test_function_manager_refuses_alias_name(make_win, file_a):
    win = make_win()
    win.open_function_manager()
    mgr = win._function_manager
    mgr.new_function()
    mgr.name_edit.setText("plain")
    mgr.params_edit.setText("x")
    mgr.expression_edit.setText("x + 1")
    assert mgr.save_function() is False
    assert "plain" not in win.function_registry.user_functions


def test_save_refused_when_it_clashes_with_a_derived_variable(make_win, file_a, ops):
    win = make_win()
    win.load_parquet(str(file_a))
    win._on_add_derived_variable("fresh", "other + 1")
    reason = win.save_signal_aliases({**DICT, "fresh": ["EngSpd"]})
    assert reason == tr("alias.clash_derived", alias="fresh")
    assert "fresh" not in win._alias_sources and "fresh" not in win.signal_aliases
    assert [o for o in ops if o[0] == "signal_alias_save_refused"]


# -- saving the dictionary with a file open ------------------------------------------------


def test_save_applies_to_the_open_file(make_win, file_a, ops):
    win = make_win()
    win.load_parquet(str(file_a))
    new = {**DICT, "speed_too": ["EngSpd"]}
    assert win.save_signal_aliases(new) == ""
    assert win._alias_sources["speed_too"] == "EngSpd"
    assert _panel_names(win) == ["clock", "rpm", "speed_too", "other"]  # name order within one column ... see below
    assert win._file_columns["speed_too"] is win._file_columns["EngSpd"]
    assert json.loads(alias_io.signal_aliases_path().read_text())["aliases"][-1] == {"alias": "speed_too", "sources": ["EngSpd"]}
    name, kw = next(o for o in ops if o[0] == "signal_alias_save")
    assert kw["added"] == ["speed_too"] and kw["removed"] == [] and kw["changed"] == []


@pytest.mark.parametrize("how", ["series", "derived", "time"])
def test_in_use_alias_cannot_be_removed_or_retargeted(make_win, file_a, qapp, how, ops):
    win = make_win()
    win.load_parquet(str(file_a))
    if how == "series":
        win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
        _wait(qapp, win)
        expect = tr("alias.used_series", alias="rpm", plot=1)
    elif how == "derived":
        win._on_add_derived_variable("d", "rpm + 1")
        expect = tr("alias.used_derived", alias="rpm", name="d")
    else:
        win.set_time_column("clock")
        expect = tr("alias.used_time", alias="clock")
    target = "rpm" if how != "time" else "clock"
    before_file = alias_io.signal_aliases_path().read_text()
    for new in (
        {a: s for a, s in DICT.items() if a != target},  # removed
        {**DICT, target: ["other"]},  # retargeted
    ):
        reason = win.save_signal_aliases(new)
        assert expect in reason
        assert win.signal_aliases == DICT and alias_io.signal_aliases_path().read_text() == before_file
        assert win._alias_sources[target] in ("EngSpd", "t2")
    assert any(o[0] == "signal_alias_save_refused" and expect in o[1]["used_in"] for o in ops)
    # an unrelated change is fine
    assert win.save_signal_aliases({**DICT, "plain": ["other_candidate"]}) == ""


def test_removing_an_unused_alias_updates_lists_and_closes_windows_using_it(make_win, file_a, qapp, monkeypatch):
    win = make_win()
    win.load_parquet(str(file_a))
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)
    try:
        hist = win.open_histogram()
        keep = win.open_histogram()
        assert "rpm" in hist._candidates()
        # select rpm in `hist` only
        for i in range(hist._var_list.count()):
            hist._var_list.item(i).setSelected(hist._var_list.item(i).text() == "rpm")
        for i in range(keep._var_list.count()):
            keep._var_list.item(i).setSelected(keep._var_list.item(i).text() == "other")
        assert hist.used_variable_names() == {"rpm"}
        assert win.save_signal_aliases({a: s for a, s in DICT.items() if a != "rpm"}) == ""
        assert hist not in win._analysis_windows and keep in win._analysis_windows
        assert "rpm" not in _panel_names(win) and "EngSpd" in _panel_names(win)
        assert "rpm" not in win._file_columns and "EngSpd" in win._raw_variables
    finally:
        for w in list(win._analysis_windows):
            w.close()


def test_failed_write_keeps_the_dictionary_and_the_file(make_win, file_a, monkeypatch):
    win = make_win()
    win.load_parquet(str(file_a))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(alias_io, "save_signal_aliases", boom)
    reason = win.save_signal_aliases({**DICT, "new": ["other"]})
    assert "disk full" in reason
    assert win.signal_aliases == DICT and "new" not in win._alias_sources


def test_saving_is_disabled_when_the_file_was_unreadable(qapp, isolated_config, monkeypatch, boxes):
    def boom(reserved=()):
        raise alias_io.SignalAliasesUnreadable("locked")

    monkeypatch.setattr(alias_io, "load_signal_aliases_ex", boom)
    w = MainWindow()
    try:
        assert not w.signal_aliases_saveable and any("locked" in m for m in w.startup_warnings)
        assert w.save_signal_aliases({"a": ["x"]}) == tr("alias.save_disabled")
        assert not alias_io.signal_aliases_path().exists()
    finally:
        w.close()


def test_startup_warning_for_bad_items_is_collected_once(qapp, isolated_config, boxes):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "aliases": [{"alias": "", "sources": ["x"]}, {"alias": "ok", "sources": ["y"]}]}))
    w = MainWindow()
    try:
        assert w.signal_aliases == {"ok": ["y"]}
        assert len(w.startup_warnings) == 1 and boxes["warning"] == []  # a notice, not a modal
    finally:
        w.close()


# -- op log state --------------------------------------------------------------------------


def test_state_snapshot_lists_the_attached_aliases(make_win, file_a, file_b):
    win = make_win()
    assert "aliases" not in win.state_snapshot()["file"]
    win.load_parquet(str(file_a))
    assert win.state_snapshot()["file"]["aliases"] == {"rpm": "EngSpd", "clock": "t2"}
    win.load_parquet(str(file_b))
    assert win.state_snapshot()["file"]["aliases"]["rpm"] == "NE_rpm"


def test_alias_load_start_record_does_not_carry_aliases(make_win, file_a, ops):
    win = make_win()
    win.load_parquet(str(file_a))
    assert ("load_parquet", {"path": str(file_a)}) in ops  # started before aliases are known


def test_alias_save_changes_only_file_info_aliases(make_win, file_a, qapp):
    """app 45: saving the dictionary must not touch plots / navigator / anything unrelated."""
    win = make_win()
    win.load_parquet(str(file_a))
    win._add_variable_to_plot(win.plot_grid.plots[0], "other")
    _wait(qapp, win)
    before = win.state_snapshot()
    assert win.save_signal_aliases({**DICT, "more": ["other"]}) == ""
    _wait(qapp, win)
    changes = diff_states(before, win.state_snapshot())
    assert set(changes) == {"file.aliases.more"}


# -- view manager marks (54.5) --------------------------------------------------------------


def _rows(dialog):
    out = []

    def walk(item):
        out.append((item.text(0), item))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i))
    return out


def _is_red(item):
    return item.foreground(0).color().name() == "#d32f2f"


@pytest.mark.parametrize("with_aliases", [True, False], ids=["aliased", "plain"])
def test_missing_marks_match_what_load_view_skips(make_win, file_a, qapp, monkeypatch, with_aliases, boxes):
    from parquet_analyzer.io.view import PlotDef, SeriesDef, SourceDef, View

    plot = PlotDef("p1", [SeriesDef(n, "#ff0000") for n in ("rpm", "clock", "t", "other", "EngSpd", "ghost")])
    view_io.save_view(View("m", SourceDef(str(file_a), "absolute"), plots=[plot]))
    win = make_win(DICT if with_aliases else None)
    win.load_parquet(str(file_a))
    win.set_time_column("t2")  # its alias is "clock" when aliased
    seen = {}

    def fake_exec(self):
        suffix = " — not in the open file"
        seen["missing"] = {t.removesuffix(suffix) for t, i in _rows(self) if t.endswith(suffix) and _is_red(i)}
        return 0

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    marked = seen["missing"]
    win.load_view("m")
    _wait(qapp, win)
    plotted = set(win.plot_grid.plots[0].series_names())
    skipped = {"rpm", "clock", "t", "other", "EngSpd", "ghost"} - plotted
    assert marked == skipped
    if with_aliases:
        assert "clock" in skipped and "rpm" not in skipped  # the time column's own alias is skipped
    else:
        assert {"rpm", "clock", "ghost"} <= skipped


def test_missing_marks_with_a_saved_time_axis_alias(make_win, file_a, qapp, monkeypatch):
    """The view's own time axis (an alias) removes the column and all its aliases from the
    available set, exactly as load_view does (54.5)."""
    from parquet_analyzer.io.view import PlotDef, SeriesDef, SourceDef, View

    names = ("t", "t2", "clock", "rpm", "other")
    view_io.save_view(
        View("tx", SourceDef(str(file_a), "absolute"), plots=[PlotDef("p1", [SeriesDef(n, "#ff0000") for n in names])],
             time_column="clock")
    )
    win = make_win()
    win.load_parquet(str(file_a))  # the time axis is "t" now
    seen = {}

    def fake_exec(self):
        suffix = " — not in the open file"
        seen["missing"] = {t.removesuffix(suffix) for t, i in _rows(self) if t.endswith(suffix) and _is_red(i)}
        return 0

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    win.load_view("tx")
    _wait(qapp, win)
    skipped = set(names) - set(win.plot_grid.plots[0].series_names())
    assert skipped == {"t2", "clock"} and seen["missing"] == skipped


# -- review fixes ---------------------------------------------------------------------------------


def test_xy_time_axis_survives_a_display_name_change(make_win, file_a, qapp):
    """The display name of the time column changes when an earlier-sorting alias is added; an
    open XY window that has X = the old name must still treat it as the time axis."""
    win = make_win({"clock": ["t"], "zz": ["t"]})
    win.load_parquet(str(file_a))
    xy = win.open_xy_plot()
    try:
        xy._x_combo.setCurrentText("clock")
        assert xy._is_time("clock")
        assert win.save_signal_aliases({"aaa": ["t"], "clock": ["t"], "zz": ["t"]}) == ""
        assert win._display_name("t") == "aaa"
        assert xy in win._analysis_windows  # still open
        assert xy._is_time("clock") and xy._is_time("aaa") and not xy._is_time("other")
        cols = xy._resolve_columns(["clock"])  # no "Variable clock is no longer available"
        assert len(cols["clock"].values()) == len(win._time_values)
    finally:
        xy.close()


def test_bad_item_startup_warning_is_translated(qapp, isolated_config, boxes):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "aliases": [{"alias": "sin", "sources": ["x"]}]}))
    w = MainWindow()
    try:
        (msg,) = w.startup_warnings
        assert "alias.err" not in msg and "{" not in msg
        assert tr("alias.err_reserved", alias="sin") in msg
    finally:
        w.close()


def test_startup_notice_combines_functions_maps_and_aliases(qapp, isolated_config, boxes):
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    d.mkdir(parents=True)
    (d / "bad.csv").write_text("x,y\n1,1\n", encoding="utf-8")
    (d / "bad.json").write_text(
        '{"schema_version":"1","name":"bad","kind":"curve","interpolation":"linear","extrapolation":"clamp"}',
        encoding="utf-8",
    )
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "aliases": [{"alias": "", "sources": ["x"]}]}))
    w = MainWindow()
    try:
        assert len(w.startup_warnings) == 2
        text = w._notice_label.text()
        assert all(m in text for m in w.startup_warnings)
    finally:
        w.close()


def test_derived_name_may_not_be_any_dictionary_alias(make_win, file_a):
    from parquet_analyzer.core.expression import ExpressionError

    win = make_win()
    win.load_parquet(str(file_a))
    assert "plain" not in win._alias_sources  # in the dictionary, not attached in this file
    with pytest.raises(ExpressionError) as ei:
        win._on_add_derived_variable("plain", "other + 1")
    assert str(ei.value) == tr("error.derived_alias_name", name="plain")
    # and the dictionary cannot take the name of an existing derived variable, attached or not
    win._on_add_derived_variable("mine", "other + 1")
    assert win.save_signal_aliases({**DICT, "mine": ["nothere2"]}) == tr("alias.clash_derived", alias="mine")


def test_view_derived_variable_named_like_an_unattached_alias_is_skipped_and_marked(make_win, file_a, qapp, monkeypatch, boxes):
    from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View

    view_io.save_view(
        View("dv", SourceDef(str(file_a), "absolute"),
             plots=[PlotDef("p1", [SeriesDef("plain", "#ff0000"), SeriesDef("other", "#00ff00")])],
             derived_variables=[DerivedVariableDef("plain", "other + 1")])
    )
    win = make_win()
    win.load_parquet(str(file_a))
    seen = {}

    def fake_exec(self):
        suffix = " — not in the open file"
        seen["missing"] = {t.removesuffix(suffix) for t, i in _rows(self) if t.endswith(suffix) and _is_red(i)}
        return 0

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    win.load_view("dv")
    _wait(qapp, win)
    assert win.plot_grid.plots[0].series_names() == ["other"] and "plain" not in win._derived
    assert any("plain" in w for w in boxes["warning"])
    assert "plain" in seen["missing"]  # the derived definition and its series are marked


def test_time_axis_failure_notice_uses_the_display_name(make_win, tmp_path):
    cols = _cols()
    cols["label_src"] = ["a"] * 2000  # a string column cannot be a time axis
    p = _write(tmp_path / "s.parquet", cols)
    win = make_win({"label": ["label_src"]})
    win.load_parquet(str(p))
    win.set_time_column("label")
    text = win._notice_label.text()
    assert text.startswith(tr("time_axis.failed", name="label", message="")[:20]) and 'Cannot use "label"' in text
    assert win._time_column == "t" and win.time_axis_combo.currentText() == "t"


def test_time_combo_lists_one_alias_of_the_time_column(make_win, file_a):
    win = make_win({"b_clock": ["t2"], "a_clock": ["t2"], "rpm": ["EngSpd"]})
    win.load_parquet(str(file_a))
    assert _combo_items(win) == ["t", "a_clock", "b_clock", "rpm", "other"]  # t is the axis; both aliases of t2 listed
    win.time_axis_combo.setCurrentText("b_clock")  # switch to t2 through another alias
    assert win._time_column == "t2"
    assert _combo_items(win) == ["t", "a_clock", "rpm", "other"]  # one entry for the time column
    assert win.time_axis_combo.currentText() == "a_clock"
    # the hidden alias still works as a view's time_column / series name
    assert win._alias_sources["b_clock"] == "t2"
