"""Signal aliases: per-alias enabled flag and the global switch (persistence 54.9)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.core.expression import ExpressionError
from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io import signal_aliases as alias_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.settings import Settings, load_settings
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog

DICT = {"rpm": ["EngSpd"], "temp": ["T_c"]}


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "question": []}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: calls["warning"].append(a[2])))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: calls["question"].append(a[2]) or QMessageBox.StandardButton.Yes),
    )
    return calls


@pytest.fixture
def ops(monkeypatch):
    recorded = []
    real = main_window_module.log_op

    def fake(op, /, **kw):
        recorded.append((op, kw))
        return real(op, **kw)

    monkeypatch.setattr(main_window_module, "log_op", fake)
    return recorded


@pytest.fixture
def datafile(tmp_path):
    i = np.arange(300, dtype="float64")
    pq.write_table(pa.table({"t": i, "EngSpd": i * 2, "T_c": i + 1}), tmp_path / "f.parquet")
    return tmp_path / "f.parquet"


@pytest.fixture
def make_win(qapp, isolated_config, boxes):
    wins = []

    def make(aliases=DICT, disabled=()):
        if aliases:
            alias_io.save_signal_aliases(aliases, disabled=disabled)
        w = MainWindow()
        wins.append(w)
        return w

    yield make
    for w in wins:
        w.close()


def _names(win):
    return [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]


def _wait(qapp, win):
    end = time.monotonic() + 10
    while time.monotonic() < end and win._busy_sources:
        qapp.processEvents()


# -- file format --------------------------------------------------------------------------------


def test_enabled_roundtrip_only_false_is_written(isolated_config):
    alias_io.save_signal_aliases({"a": ["x"], "b": ["y"]}, disabled={"b"})
    raw = json.loads(alias_io.signal_aliases_path().read_text())
    assert raw["version"] == 1
    assert raw["aliases"] == [{"alias": "a", "sources": ["x"]}, {"alias": "b", "sources": ["y"], "enabled": False}]
    aliases, disabled, warnings, skipped = alias_io.load_signal_aliases_ex()
    assert aliases == {"a": ["x"], "b": ["y"]} and disabled == {"b"} and warnings == [] and skipped == []
    assert alias_io.load_signal_aliases()[0] == aliases  # the old entry point still works


def test_missing_enabled_key_means_enabled_and_non_bool_is_skipped(isolated_config):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    items = [
        {"alias": "a", "sources": ["x"]},
        {"alias": "b", "sources": ["y"], "enabled": "no"},
        {"alias": "c", "sources": ["z"], "enabled": 0},
        {"alias": "d", "sources": ["w"], "enabled": True},
    ]
    path.write_text(json.dumps({"version": 1, "aliases": items}))
    aliases, disabled, warnings, skipped = alias_io.load_signal_aliases_ex()
    assert list(aliases) == ["a", "d"] and disabled == set()
    assert len(warnings) == 2 and skipped == items[1:3]
    assert warnings[0][1].key == "alias.err_bad_enabled"


def test_setting_default_and_validation(isolated_config):
    assert Settings().signal_aliases_enabled is True
    path = isolated_config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"signal_aliases_enabled": "yes"}))
    assert load_settings().signal_aliases_enabled is True
    path.write_text(json.dumps({"signal_aliases_enabled": False}))
    assert load_settings().signal_aliases_enabled is False


# -- resolution ---------------------------------------------------------------------------------


def test_disabled_alias_is_not_attached_and_source_name_is_listed(make_win, datafile):
    win = make_win(disabled={"rpm"})
    win.load_parquet(str(datafile))
    assert win._alias_sources == {"temp": "T_c"}
    assert _names(win) == ["EngSpd", "temp"]
    assert "rpm" not in win._file_columns
    assert win.signal_aliases_disabled == {"rpm"}


def test_enabling_attaches_and_disabling_detaches(make_win, datafile):
    win = make_win(disabled={"rpm"})
    win.load_parquet(str(datafile))
    assert win.save_signal_aliases(win.signal_aliases, set()) == ""
    assert win._alias_sources["rpm"] == "EngSpd" and _names(win) == ["rpm", "temp"]
    assert json.loads(alias_io.signal_aliases_path().read_text())["aliases"][0] == {"alias": "rpm", "sources": ["EngSpd"]}
    assert win.save_signal_aliases(win.signal_aliases, {"rpm"}) == ""
    assert "rpm" not in win._alias_sources and _names(win) == ["EngSpd", "temp"]


def test_global_switch_off_detaches_all_and_is_persisted(make_win, datafile, qapp):
    win = make_win()
    win.load_parquet(str(datafile))
    assert win.set_signal_aliases_enabled(False) == ""
    assert win._alias_sources == {} and _names(win) == ["EngSpd", "T_c"]
    assert win._actions["toggle_signal_aliases"].isChecked() is False
    assert load_settings().signal_aliases_enabled is False
    win2 = make_win(None)  # a new window: the setting survives a restart
    win2.load_parquet(str(datafile))
    assert win2._alias_sources == {} and not win2._actions["toggle_signal_aliases"].isChecked()
    assert win.set_signal_aliases_enabled(True) == ""
    assert win._alias_sources == {"rpm": "EngSpd", "temp": "T_c"}


def test_clash_checks_still_use_all_aliases_when_disabled_or_off(make_win, datafile):
    win = make_win(disabled={"rpm"})
    win.load_parquet(str(datafile))
    win.set_signal_aliases_enabled(False)
    assert {"rpm", "temp"} <= win.reserved_names()
    with pytest.raises(maps_io.MapError):
        maps_io.validate_map_name("rpm", win.reserved_names())
    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("rpm", "EngSpd + 1")
    win._on_add_derived_variable("mine", "EngSpd + 1")
    assert win.save_signal_aliases({**DICT, "mine": ["EngSpd"]}) == tr("alias.clash_derived", alias="mine")


# -- in-use refusals ----------------------------------------------------------------------------


def _use_rpm(win, how, qapp):
    if how == "series":
        win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
        _wait(qapp, win)
        return tr("alias.used_series", alias="rpm", plot=1)
    win._on_add_derived_variable("d", "rpm + 1")
    return tr("alias.used_derived", alias="rpm", name="d")


@pytest.mark.parametrize("how", ["series", "derived"])
def test_disabling_an_in_use_alias_is_refused(make_win, datafile, qapp, how, ops):
    win = make_win()
    win.load_parquet(str(datafile))
    expect = _use_rpm(win, how, qapp)
    before = alias_io.signal_aliases_path().read_text()
    reason = win.save_signal_aliases(win.signal_aliases, {"rpm"})
    assert expect in reason
    assert win.signal_aliases_disabled == set() and win._alias_sources["rpm"] == "EngSpd"
    assert alias_io.signal_aliases_path().read_text() == before
    assert any(o[0] == "signal_alias_save_refused" for o in ops)


@pytest.mark.parametrize("how", ["series", "derived"])
def test_global_off_with_an_in_use_alias_is_refused(make_win, datafile, qapp, how, ops):
    win = make_win()
    win.load_parquet(str(datafile))
    expect = _use_rpm(win, how, qapp)
    reason = win.set_signal_aliases_enabled(False)
    assert expect in reason
    assert win.settings.signal_aliases_enabled is True and win._alias_sources["rpm"] == "EngSpd"
    assert load_settings().signal_aliases_enabled is True
    assert ("signal_aliases_toggle", {"enabled": False}) in ops
    ref = [kw for op, kw in ops if op == "signal_alias_save_refused"]
    assert ref and ref[-1]["reason"] == "toggle" and expect in ref[-1]["used_in"]


def test_time_axis_alias_counts_as_in_use(make_win, datafile):
    win = make_win({"clock": ["t"], "rpm": ["EngSpd"]})
    win.load_parquet(str(datafile))  # the time axis t is shown as "clock"
    assert tr("alias.used_time", alias="clock") in win.set_signal_aliases_enabled(False)


def test_gear_menu_action_refusal_reverts_the_check(make_win, datafile, qapp, boxes):
    win = make_win()
    win.load_parquet(str(datafile))
    _use_rpm(win, "derived", qapp)
    action = win._actions["toggle_signal_aliases"]
    win.open_signal_alias_manager()
    action.trigger()  # unchecks, is refused
    assert action.isChecked() and win._alias_manager.global_checkbox.isChecked()
    assert boxes["warning"] and win.settings.signal_aliases_enabled
    win._alias_manager.close()


# -- editor -------------------------------------------------------------------------------------


def test_gear_menu_and_editor_checkbox_stay_in_sync(make_win, datafile):
    win = make_win()
    win.load_parquet(str(datafile))
    win.open_signal_alias_manager()
    m = win._alias_manager
    action = win._actions["toggle_signal_aliases"]
    assert action.isChecked() and m.global_checkbox.isChecked() and win.settings_menu.actions()[1] is action
    action.trigger()
    assert not m.global_checkbox.isChecked() and win._alias_sources == {}
    m.global_checkbox.click()
    assert action.isChecked() and win._alias_sources["rpm"] == "EngSpd"
    m.global_checkbox.click()
    assert not action.isChecked() and win.settings.signal_aliases_enabled is False
    m.close()


def test_editor_global_checkbox_refusal_reverts_and_explains(make_win, datafile, qapp):
    win = make_win()
    win.load_parquet(str(datafile))
    _use_rpm(win, "series", qapp)
    win.open_signal_alias_manager()
    m = win._alias_manager
    m.global_checkbox.click()
    assert m.global_checkbox.isChecked() and win._actions["toggle_signal_aliases"].isChecked()
    assert tr("alias.used_series", alias="rpm", plot=1) in m.message.text()
    m.close()


def test_editor_shows_disabled_and_off_status(make_win, datafile):
    win = make_win(disabled={"rpm"})
    win.load_parquet(str(datafile))
    win.open_signal_alias_manager()
    m = win._alias_manager
    texts = [m.alias_list.item(i).text() for i in range(m.alias_list.count())]
    assert texts[0] == tr("alias.list_item", alias=f"rpm {tr('alias.disabled_mark')}", source=tr("alias.status_disabled"))
    assert texts[1] == tr("alias.list_item", alias="temp", source="T_c")
    assert m.alias_list.item(0).foreground().color().name() == "#9a9a9a"  # faded
    win.set_signal_aliases_enabled(False)
    texts = [m.alias_list.item(i).text() for i in range(m.alias_list.count())]
    assert all(t.endswith(tr("alias.status_off")) for t in texts)
    m.alias_list.setCurrentRow(1)
    m.name_edit.setText("temp2")  # editing and saving keep working while the switch is off
    assert m.save() and "temp2" in win.signal_aliases
    m.close()


def test_enabled_checkbox_is_part_of_the_dirty_form(make_win, datafile, boxes):
    win = make_win()
    win.load_parquet(str(datafile))
    win.open_signal_alias_manager()
    m = win._alias_manager
    assert m.enabled_checkbox.isChecked() and not m.save_button.isEnabled()
    m.enabled_checkbox.setChecked(False)
    assert m.save_button.isEnabled() and m.revert_button.isEnabled()
    m.revert()
    assert m.enabled_checkbox.isChecked() and not m.save_button.isEnabled()
    m.enabled_checkbox.setChecked(False)
    m.alias_list.setCurrentRow(1)  # leaving asks (the stubbed answer is Yes = save)
    assert boxes["question"] and win.signal_aliases_disabled == {"rpm"}
    assert "rpm" not in win._alias_sources
    m.alias_list.setCurrentRow(0)
    assert not m.enabled_checkbox.isChecked() and not m.save_button.isEnabled()
    m.enabled_checkbox.setChecked(True)
    assert m.save() and win.signal_aliases_disabled == set() and win._alias_sources["rpm"] == "EngSpd"
    m.close()


def test_renaming_and_deleting_keep_the_disabled_set_consistent(make_win, datafile, boxes):
    win = make_win(disabled={"rpm"})
    win.load_parquet(str(datafile))
    win.open_signal_alias_manager()
    m = win._alias_manager
    m.name_edit.setText("rpm2")
    assert m.save() and win.signal_aliases_disabled == {"rpm2"} and not m.enabled_checkbox.isChecked()
    m.delete_selected()
    assert win.signal_aliases_disabled == set() and list(win.signal_aliases) == ["temp"]
    m.close()


def test_save_log_has_enabled_changed(make_win, datafile, ops):
    win = make_win()
    win.load_parquet(str(datafile))
    win.save_signal_aliases(win.signal_aliases, {"temp"})
    kw = next(kw for op, kw in ops if op == "signal_alias_save")
    assert kw["enabled_changed"] == ["temp"] and kw["added"] == [] and kw["changed"] == []


def test_toggle_to_the_same_value_changes_nothing_but_is_logged(make_win, datafile, ops):
    win = make_win()
    win.load_parquet(str(datafile))
    assert win.set_signal_aliases_enabled(True) == ""
    assert ("signal_aliases_toggle", {"enabled": True}) in ops


# -- views --------------------------------------------------------------------------------------


def _rows(dialog):
    out = []

    def walk(item):
        out.append((item.text(0), item))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i))
    return out


def test_view_with_alias_series_loaded_with_global_off_warns_and_marks_match(make_win, datafile, qapp, monkeypatch, boxes):
    from parquet_analyzer.io.view import PlotDef, SeriesDef, SourceDef, View

    view_io.save_view(
        View("v", SourceDef(str(datafile), "absolute"),
             plots=[PlotDef("p1", [SeriesDef("rpm", "#ff0000"), SeriesDef("T_c", "#00ff00")])])
    )
    win = make_win()
    win.load_parquet(str(datafile))
    win.set_signal_aliases_enabled(False)
    seen = {}

    def fake_exec(self):
        suffix = " — not in the open file"
        seen["missing"] = {
            t.removesuffix(suffix) for t, i in _rows(self)
            if t.endswith(suffix) and i.foreground(0).color().name() == "#d32f2f"
        }
        return 0

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    win.load_view("v")
    _wait(qapp, win)
    assert win.plot_grid.plots[0].series_names() == ["T_c"]
    assert any("rpm" in w for w in boxes["warning"])  # the consolidated warning
    assert seen["missing"] == {"rpm"}


def test_i18n_has_en_and_ja_for_54_9():
    from parquet_analyzer.ui.i18n import _STRINGS

    for k in ("toolbar.toggle_signal_aliases", "alias.use_aliases", "alias.enabled", "alias.disabled_mark",
              "alias.status_disabled", "alias.status_off", "alias.err_bad_enabled"):
        assert _STRINGS[k]["en"] and _STRINGS[k]["ja"]
