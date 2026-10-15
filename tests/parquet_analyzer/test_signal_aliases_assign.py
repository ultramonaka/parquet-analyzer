"""Signal aliases, stage 2: assign substitute signals from the view-load warning
(persistence 54.6 / 54.10, test plan 54.7)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from parquet_analyzer.io import signal_aliases as alias_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui import messages
from parquet_analyzer.ui.i18n import _STRINGS, tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.signal_alias_assign_dialog import SignalAliasAssignDialog


def _write(path, cols):
    pq.write_table(pa.table({k: pa.array(v) for k, v in cols.items()}), path)
    return path


def _cols(signal="EngSpd", time2="t2", n=2000):
    i = np.arange(n, dtype="float64")
    return {"t": i, time2: 5000.0 + i * 10.0, signal: np.sin(i / 50) * 100, "other": np.cos(i / 30)}


@pytest.fixture
def file_a(tmp_path):
    return _write(tmp_path / "a.parquet", _cols("EngSpd", "t2"))


@pytest.fixture
def file_c(tmp_path):
    """Other signal name AND other time-axis name than file_a."""
    return _write(tmp_path / "c.parquet", _cols("NE_rpm", "tt"))


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "critical": [], "information": [], "question": [], "button": []}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: calls["information"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: calls["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: calls["critical"].append(a[2])))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: calls["question"].append(a[2]) or QMessageBox.StandardButton.Yes),
    )
    return calls


@pytest.fixture
def press(monkeypatch, boxes):
    """Replace the warning-with-button helper: records (text, button label), answers `state['press']`."""
    state = {"press": True}

    def fake(parent, title, text, button_text):
        boxes["button"].append((text, button_text))
        return state["press"]

    monkeypatch.setattr(messages, "warning_with_button", fake)
    return state


@pytest.fixture
def pick(monkeypatch):
    """Answer the assignment dialog: `state['choices']` = {missing name: listed name}; records it."""
    state = {"choices": {}, "opened": [], "accept": True}

    def fake_exec(self):
        state["opened"].append(
            {n: ([c.itemText(i) for i in range(c.count())], c.isEnabled()) for n, c in self.combos.items()}
        )
        for name, text in state["choices"].items():
            self.combos[name].setCurrentIndex(self.combos[name].findText(text))
        return QDialog.DialogCode.Accepted if state["accept"] else QDialog.DialogCode.Rejected

    monkeypatch.setattr(SignalAliasAssignDialog, "exec", fake_exec)
    return state


@pytest.fixture
def ops(monkeypatch):
    recorded: list[tuple[str, dict]] = []
    real = main_window_module.log_op

    def fake(op, /, **kw):
        recorded.append((op, kw))
        return real(op, **kw)

    monkeypatch.setattr(main_window_module, "log_op", fake)
    return recorded


@pytest.fixture
def make_win(qapp, isolated_config, boxes):
    wins = []

    def make(aliases=None, disabled=()):
        if aliases:
            alias_io.save_signal_aliases(aliases, disabled=disabled)
        w = MainWindow()
        wins.append(w)
        return w

    yield make
    for w in wins:
        w.close()


def _wait(qapp, win, minimum=0.0):
    """Process events until the singleShot / background work of the window has settled."""
    start = time.monotonic()
    while time.monotonic() - start < 10:
        qapp.processEvents()
        if not win._busy_sources and time.monotonic() - start >= minimum:
            break
        time.sleep(0.005)


def _save_view(file_a, name="v1", extra=()):
    view_io.save_view(
        View(
            name, SourceDef(str(file_a), "absolute"),
            plots=[PlotDef("p1", [SeriesDef("EngSpd", "#ff0000"), SeriesDef("other", "#00ff00"), SeriesDef("twice", "#0000ff")])],
            derived_variables=[DerivedVariableDef("twice", "EngSpd * 2"), *extra],
            time_column="t2",
        )
    )


def _dict_on_disk():
    return json.loads(alias_io.signal_aliases_path().read_text(encoding="utf-8"))["aliases"]


def _load(win, qapp, file, name="v1"):
    win.load_parquet(str(file))
    win.load_view(name)
    _wait(qapp, win, 0.05)  # the singleShot of the assignment fires on the first event loop pass
    _wait(qapp, win)


def test_button_offered_when_a_signal_is_missing(make_win, file_a, file_c, qapp, press, boxes):
    _save_view(file_a)
    win = make_win()
    press["press"] = False
    _load(win, qapp, file_c)
    assert len(boxes["button"]) == 1 and boxes["warning"] == []
    text, label = boxes["button"][0]
    assert label == tr("alias.assign_button") and "EngSpd" in text


def test_no_button_when_nothing_is_missing_for_a_skipped_item(make_win, file_a, qapp, press, boxes):
    # skipped only because of an unknown function: no missing signal
    view_io.save_view(
        View("f", SourceDef(str(file_a), "absolute"),
             plots=[PlotDef("p1", [SeriesDef("other", "#00ff00"), SeriesDef("bad", "#ff0000")])],
             derived_variables=[DerivedVariableDef("bad", "nosuchfunc(other)")])
    )
    win = make_win()
    _load(win, qapp, file_a, "f")
    assert boxes["button"] == [] and len(boxes["warning"]) == 1


def test_no_button_while_the_global_switch_is_off(make_win, file_a, file_c, qapp, press, boxes):
    _save_view(file_a)
    win = make_win()
    win.set_signal_aliases_enabled(False)
    _load(win, qapp, file_c)
    assert boxes["button"] == [] and len(boxes["warning"]) == 1


def test_no_button_when_the_dictionary_is_not_writable(make_win, file_a, file_c, qapp, press, boxes):
    _save_view(file_a)
    win = make_win()
    win._aliases_saveable = False
    _load(win, qapp, file_c)
    assert boxes["button"] == [] and len(boxes["warning"]) == 1


def test_missing_names_include_time_axis_series_and_formula_names(make_win, file_a, file_c, qapp, press):
    # "unused" is not plotted, so only its formula says that "ghost" is missing
    _save_view(file_a, extra=[DerivedVariableDef("unused", "ghost + other")])
    win = make_win()
    press["press"] = False
    seen = []
    orig = win._view_missing_signals
    win._view_missing_signals = lambda ctx: seen.append(orig(ctx)) or seen[-1]
    _load(win, qapp, file_c)
    assert sorted(seen[0]) == ["EngSpd", "ghost", "t2"] and len(seen[0]) == 3


def test_assign_then_reload_restores_the_view(make_win, file_a, file_c, qapp, press, pick, boxes, ops):
    _save_view(file_a)
    win = make_win()
    pick["choices"] = {"EngSpd": "NE_rpm", "t2": "tt"}
    _load(win, qapp, file_c)
    assert alias_io.load_signal_aliases()[0] == {"EngSpd": ["NE_rpm"], "t2": ["tt"]}
    assert win._alias_sources == {"EngSpd": "NE_rpm", "t2": "tt"}
    assert win.plot_grid.plots[0].series_names() == ["EngSpd", "other", "twice"]
    assert win._time_column == "tt" and win.time_axis_combo.currentText() == "t2"
    assert "twice" in win._derived
    assert len(boxes["button"]) == 1  # the reloaded view restored fully: no second warning
    assert boxes["warning"] == []
    rec = [kw for op, kw in ops if op == "signal_alias_assign_from_view"]
    assert len(rec) == 1 and rec[0]["view"] == "v1"
    assert sorted(rec[0]["assigned"]) == [["EngSpd", "NE_rpm"], ["t2", "tt"]]


def test_dialog_offers_listed_and_original_names_default_none(make_win, file_a, file_c, qapp, press, pick):
    _save_view(file_a)
    win = make_win({"sig": ["NE_rpm"]})
    pick["accept"] = False
    _load(win, qapp, file_c)
    items, enabled = pick["opened"][0]["EngSpd"]
    assert items[0] == tr("alias.assign_none")
    assert "sig" in items and "NE_rpm" in items and "other" in items  # alias name AND original column
    assert enabled


def test_choosing_a_listed_alias_assigns_its_column(make_win, file_a, file_c, qapp, press, pick):
    _save_view(file_a)
    win = make_win({"sig": ["NE_rpm"]})
    pick["choices"] = {"EngSpd": "sig"}
    _load(win, qapp, file_c)
    assert alias_io.load_signal_aliases()[0]["EngSpd"] == ["NE_rpm"]  # resolved to the column, no chain
    assert win._alias_sources["EngSpd"] == "NE_rpm"
    assert win.plot_grid.plots[0].series_names()[0] == "EngSpd"


def test_existing_alias_gets_the_column_appended(make_win, file_a, file_c, qapp, press, pick):
    _save_view(file_a)
    win = make_win({"EngSpd": ["old_name"], "t2": ["tt"]})
    pick["choices"] = {"EngSpd": "NE_rpm"}
    _load(win, qapp, file_c)
    assert alias_io.load_signal_aliases()[0]["EngSpd"] == ["old_name", "NE_rpm"]
    assert win._alias_sources["EngSpd"] == "NE_rpm"


def test_disabled_alias_is_enabled_again_and_the_row_says_so(make_win, file_a, file_c, qapp, press, pick):
    _save_view(file_a)
    win = make_win({"EngSpd": ["old_name"], "t2": ["tt"]}, disabled={"EngSpd"})
    pick["choices"] = {"EngSpd": "NE_rpm"}
    rows = {r.name: r for r in win._alias_assign_rows(["EngSpd", "t2", "zzz"])}
    assert rows["EngSpd"].note == tr("alias.assign_note_reenable", name="EngSpd")
    assert rows["t2"].note == tr("alias.assign_note_append", name="t2")
    assert rows["zzz"].note == tr("alias.assign_note_new", name="zzz")
    _load(win, qapp, file_c)
    assert win.signal_aliases_disabled == set()
    assert "enabled" not in json.dumps(_dict_on_disk())
    assert win._alias_sources["EngSpd"] == "NE_rpm"


def test_function_or_map_name_row_is_not_selectable(make_win, file_a, qapp, press, pick):
    win = make_win()
    win.load_parquet(str(file_a))
    rows = win._alias_assign_rows(["sin", "ghost"])
    assert [(r.name, r.selectable) for r in rows] == [("sin", False), ("ghost", True)]
    assert rows[0].note == tr("alias.assign_note_reserved", name="sin")
    pick["choices"] = {"ghost": "other"}
    win._offer_alias_assignment("v", ["sin", "ghost"])
    assert pick["opened"][0]["sin"][1] is False and pick["opened"][0]["ghost"][1] is True
    assert alias_io.load_signal_aliases()[0] == {"ghost": ["other"]}


def test_do_not_assign_everywhere_changes_nothing_and_does_not_reload(
    make_win, file_a, file_c, qapp, press, pick, ops, monkeypatch
):
    _save_view(file_a)
    win = make_win({"keep": ["other"]})
    before = alias_io.signal_aliases_path().read_text(encoding="utf-8")
    calls = []
    real = win.load_view
    monkeypatch.setattr(win, "load_view", lambda n: calls.append(n) or real(n))
    _load(win, qapp, file_c)  # the "do not assign" answer: pick["choices"] stays empty
    assert len(calls) == 1  # only the load the test itself started: no reload
    assert alias_io.signal_aliases_path().read_text(encoding="utf-8") == before
    assert [op for op, _ in ops if op in ("signal_alias_assign_from_view", "signal_alias_save")] == []


def test_cancel_changes_nothing(make_win, file_a, file_c, qapp, press, pick):
    _save_view(file_a)
    win = make_win()
    pick["choices"] = {"EngSpd": "NE_rpm"}
    pick["accept"] = False
    _load(win, qapp, file_c)
    assert not alias_io.signal_aliases_path().exists()


def test_refusal_shows_the_reason_and_does_not_reload(make_win, file_a, qapp, pick, boxes, monkeypatch):
    win = make_win()
    win.load_parquet(str(file_a))
    win._on_add_derived_variable("mine", "other + 1")
    pick["choices"] = {"mine": "other"}
    calls = []
    monkeypatch.setattr(win, "load_view", lambda n: calls.append(n))
    win._offer_alias_assignment("v", ["mine"])
    assert boxes["warning"] == [tr("alias.clash_derived", alias="mine")]
    assert calls == [] and not alias_io.signal_aliases_path().exists()


def test_failed_write_keeps_everything(make_win, file_a, qapp, pick, boxes, monkeypatch):
    win = make_win({"keep": ["other"]})
    win.load_parquet(str(file_a))
    before = alias_io.signal_aliases_path().read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(alias_io, "save_signal_aliases", boom)
    calls = []
    monkeypatch.setattr(win, "load_view", lambda n: calls.append(n))
    pick["choices"] = {"ghost": "other"}
    win._offer_alias_assignment("v", ["ghost"])
    assert len(boxes["warning"]) == 1 and "disk full" in boxes["warning"][0]
    assert win.signal_aliases == {"keep": ["other"]} and calls == []
    assert alias_io.signal_aliases_path().read_text(encoding="utf-8") == before


def test_open_alias_manager_shows_the_new_entry_and_keeps_unsaved_edits(make_win, file_a, qapp):
    win = make_win({"EngSpd": ["x1"], "other_alias": ["other"]})
    win.load_parquet(str(file_a))
    win.open_signal_alias_manager(select="other_alias")
    mgr = win._alias_manager
    # clean form: shows the stored data after an outside change
    assert win.assign_aliases_from_view({"other_alias": "t"}) == ""
    assert [mgr.sources_list.item(i).text() for i in range(mgr.sources_list.count())] == ["other", "t"]
    assert not mgr._dirty()
    # a new alias appears in the list
    assert win.assign_aliases_from_view({"brand_new": "t2"}) == ""
    rows = [mgr.alias_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(mgr.alias_list.count())]
    assert "brand_new" in rows
    # an unsaved edit is kept
    mgr.name_edit.setText("renamed_unsaved")
    assert win.assign_aliases_from_view({"brand_new": "other"}) == ""
    assert mgr.name_edit.text() == "renamed_unsaved" and mgr._dirty()
    mgr._baseline = mgr._form()  # discard the edit so closing does not ask
    mgr._baseline_enabled = mgr.enabled_checkbox.isChecked()
    mgr.close()


def test_helper_returns_whether_the_extra_button_was_pressed(qapp, monkeypatch):
    def click(label):
        def fake_exec(self):
            for b in self.buttons():
                if b.text() == label:
                    b.click()
            return 0

        return fake_exec

    monkeypatch.setattr(QMessageBox, "exec", click("Go"))
    assert messages.warning_with_button(None, "t", "x", "Go") is True
    monkeypatch.setattr(QMessageBox, "exec", click("OK"))
    assert messages.warning_with_button(None, "t", "x", "Go") is False
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)  # closed without a click
    assert messages.warning_with_button(None, "t", "x", "Go") is False


def test_strings_exist_in_both_languages():
    keys = [k for k in _STRINGS if k.startswith("alias.assign_")]
    assert len(keys) >= 8
    for k in keys:
        assert _STRINGS[k]["en"] and _STRINGS[k]["ja"], k
