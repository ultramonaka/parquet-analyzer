"""Signal alias editor window (persistence 54.3/54.4, test plan 54.7)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QMenu, QMessageBox

from parquet_analyzer.io import signal_aliases as alias_io
from parquet_analyzer.io.settings import DEFAULT_SHORTCUTS, Settings
from parquet_analyzer.ui import variable_panel
from parquet_analyzer.ui.i18n import _STRINGS, tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.settings_dialog import SettingsDialog

DICT = {"rpm": ["EngSpd", "NE_rpm"], "other_name": ["nothere"]}


@pytest.fixture
def answers(monkeypatch):
    """Records question/warning boxes; `answers.reply` is the answer of every question."""

    class A:
        reply = QMessageBox.StandardButton.Yes
        questions: list = []
        warnings: list = []

    A.questions, A.warnings = [], []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: A.questions.append(a[2]) or A.reply)
    )
    for name in ("information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: A.warnings.append(a[2])))
    return A


@pytest.fixture
def datafile(tmp_path):
    i = np.arange(500, dtype="float64")
    pq.write_table(pa.table({"t": i, "EngSpd": i * 2, "other": i + 1}), tmp_path / "f.parquet")
    return tmp_path / "f.parquet"


@pytest.fixture
def win(qapp, isolated_config, answers, datafile):
    alias_io.save_signal_aliases(DICT)
    w = MainWindow()
    w.load_parquet(str(datafile))
    yield w
    w.close()


def _open(win):
    win.open_signal_alias_manager()
    return win._alias_manager


def _list_texts(m):
    return [m.alias_list.item(i).text() for i in range(m.alias_list.count())]


def _sources(m):
    return [m.sources_list.item(i).text() for i in range(m.sources_list.count())]


def _stored(path=None):
    return json.loads(alias_io.signal_aliases_path().read_text(encoding="utf-8"))["aliases"]


def test_toolbar_entry_shortcut_and_singleton(win):
    action = win._actions["signal_aliases"]
    assert action.text() == tr("toolbar.signal_aliases")
    assert "signal_aliases" in DEFAULT_SHORTCUTS and DEFAULT_SHORTCUTS["signal_aliases"] == ""
    assert "signal_aliases" in SettingsDialog(Settings())._shortcut_edits  # a row in the shortcut settings
    action.trigger()
    first = win._alias_manager
    assert first is not None and first.isVisible() and not first.isModal()
    win.open_signal_alias_manager()
    assert win._alias_manager is first  # one at a time
    first.close()


def test_left_list_shows_current_source_dash_and_same_name(win):
    m = _open(win)
    assert _list_texts(m) == [
        tr("alias.list_item", alias="rpm", source="EngSpd"),
        tr("alias.list_item", alias="other_name", source=tr("alias.status_none")),
    ]
    assert win.save_signal_aliases({**DICT, "other": ["EngSpd"]}) == ""  # a real column has this name
    assert _list_texts(m)[-1] == tr("alias.list_item", alias="other", source=tr("alias.status_same_name"))
    m.close()


def test_editing_form_enables_save_revert_only_while_dirty(win):
    m = _open(win)
    assert m.name_edit.text() == "rpm" and _sources(m) == ["EngSpd", "NE_rpm"]
    assert not m.save_button.isEnabled() and not m.revert_button.isEnabled()
    m.name_edit.setText("engine_rpm")
    assert m.save_button.isEnabled() and m.revert_button.isEnabled()
    m.revert()
    assert m.name_edit.text() == "rpm" and not m.save_button.isEnabled()
    m.close()


def test_reorder_remove_add_sources_and_save(win):
    m = _open(win)
    m.sources_list.setCurrentRow(1)
    m.up_button.click()
    assert _sources(m) == ["NE_rpm", "EngSpd"] and m.sources_list.currentRow() == 0
    m.down_button.click()
    m.down_button.click()  # at the end: no-op
    assert _sources(m) == ["EngSpd", "NE_rpm"]
    m.sources_list.setCurrentRow(1)
    m.remove_source_button.click()
    assert _sources(m) == ["EngSpd"]
    m.source_combo.setEditText("typed_col")  # a typed name, not from the file
    m.add_source_button.click()
    assert [m.source_combo.itemText(i) for i in range(m.source_combo.count())] == ["t", "EngSpd", "other"]
    m.source_combo.setEditText("other")
    m.add_source_button.click()
    assert _sources(m) == ["EngSpd", "typed_col", "other"]
    assert m.save()
    assert _stored()[0] == {"alias": "rpm", "sources": ["EngSpd", "typed_col", "other"]}
    assert not m.save_button.isEnabled() and m.message.text() == tr("alias.saved")
    m.close()


def test_rename_keeps_position_and_applies_to_the_open_file(win):
    m = _open(win)
    m.name_edit.setText("engine_rpm")
    assert m.save()
    assert list(win.signal_aliases) == ["engine_rpm", "other_name"]
    assert win._alias_sources == {"engine_rpm": "EngSpd"}
    names = [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]
    assert names == ["engine_rpm", "other"]
    m.close()


def test_add_new_alias_and_validation_errors(win):
    m = _open(win)
    m.add_button.click()
    assert m.name_edit.text() == "" and _sources(m) == [] and tr("alias.new_entry") in _list_texts(m)
    assert not m.save_button.isEnabled()  # untouched blank entry
    m.name_edit.setText("x")
    assert m.save() is False and m.message.text() == tr("alias.err_no_sources", alias="x")
    m.source_combo.setEditText("other")
    m.add_source_button.click()
    m.name_edit.setText("rpm")  # a name already taken
    assert m.save() is False and m.message.text() == tr("alias.err_duplicate_name", alias="rpm")
    m.name_edit.setText("sin")  # a built-in function
    assert m.save() is False and m.message.text() == tr("alias.err_reserved", alias="sin")
    m.name_edit.setText("   ")
    assert m.save() is False and m.message.text() == tr("alias.err_empty_name")
    m.source_combo.setEditText("other")
    m.add_source_button.click()
    m.name_edit.setText("x")
    assert m.save() is False and m.message.text() == tr("alias.err_duplicate_source", alias="x", source="other")
    assert "x" not in win.signal_aliases
    m.close()


def test_not_identifier_alias_gets_a_note_but_saves(win):
    m = _open(win)
    m.add_button.click()
    m.name_edit.setText("engine speed [rpm]")
    assert tr("alias.not_identifier") in m.note_label.text()
    m.source_combo.setEditText("other")
    m.add_source_button.click()
    assert m.save() is True
    assert "engine speed [rpm]" in win.signal_aliases
    m.name_edit.setText("fine_name")
    assert m.note_label.text() == ""
    m.close()


def test_same_name_note(win):
    m = _open(win)
    m.add_button.click()
    m.name_edit.setText("other")
    assert tr("alias.same_name_note") in m.note_label.text()
    m.revert()
    m.close()


def test_switching_with_unsaved_changes_asks(win, answers):
    m = _open(win)
    m.name_edit.setText("rpm2")
    # Cancel: stay on the entry, keep the edit
    answers.reply = QMessageBox.StandardButton.Cancel
    m.alias_list.setCurrentRow(1)
    assert answers.questions and m.name_edit.text() == "rpm2" and m.alias_list.currentRow() == 0
    # No: discard and move on
    answers.reply = QMessageBox.StandardButton.No
    m.alias_list.setCurrentRow(1)
    assert m.name_edit.text() == "other_name" and "rpm2" not in win.signal_aliases
    # Yes: save and move on
    m.alias_list.setCurrentRow(0)
    m.name_edit.setText("rpm3")
    answers.reply = QMessageBox.StandardButton.Yes
    m.alias_list.setCurrentRow(1)
    assert "rpm3" in win.signal_aliases and "rpm" not in win.signal_aliases
    assert m.name_edit.text() == "other_name"
    m.close()


def test_closing_with_unsaved_changes_asks_and_cancel_keeps_it_open(win, answers):
    m = _open(win)
    m.name_edit.setText("changed")
    answers.reply = QMessageBox.StandardButton.Cancel
    m.close()
    assert m.isVisible()
    answers.reply = QMessageBox.StandardButton.No
    m.close()
    assert not m.isVisible() and "changed" not in win.signal_aliases
    assert win._alias_manager is None or not win._alias_manager.isVisible()


def test_delete_with_confirmation_and_refusal_when_in_use(win, answers, qapp):
    m = _open(win)
    m.alias_list.setCurrentRow(1)
    answers.reply = QMessageBox.StandardButton.No
    m.delete_button.click()
    assert "other_name" in win.signal_aliases
    answers.reply = QMessageBox.StandardButton.Yes
    m.delete_button.click()
    assert list(win.signal_aliases) == ["rpm"] and [a["alias"] for a in _stored()] == ["rpm"]
    assert m.name_edit.text() == "rpm"
    # an alias in use cannot be deleted; the reason is shown in the editor
    win._add_variable_to_plot(win.plot_grid.plots[0], "rpm")
    end = time.monotonic() + 10
    while time.monotonic() < end and win._busy_sources:
        qapp.processEvents()
    m.delete_button.click()
    assert list(win.signal_aliases) == ["rpm"]
    assert tr("alias.used_series", alias="rpm", plot=1) in m.message.text()
    m.close()


def test_in_use_alias_cannot_be_renamed_from_the_editor(win, qapp):
    win._on_add_derived_variable("d", "rpm + 1")
    m = _open(win)
    m.name_edit.setText("renamed")
    assert m.save() is False
    assert tr("alias.used_derived", alias="rpm", name="d") in m.message.text()
    assert "rpm" in win.signal_aliases
    m.close()


def test_saving_without_a_file_just_writes_the_dictionary(qapp, isolated_config, answers):
    w = MainWindow()
    try:
        w.open_signal_alias_manager()
        m = w._alias_manager
        m.add_button.click()
        m.name_edit.setText("speed")
        m.source_combo.setEditText("EngSpd")  # no file: typing is the only way
        m.add_source_button.click()
        assert m.save()
        assert w.signal_aliases == {"speed": ["EngSpd"]}
        assert _list_texts(m) == [tr("alias.list_item", alias="speed", source=tr("alias.status_none"))]
        m.close()
    finally:
        w.close()


def test_opening_another_file_refreshes_the_status_texts(win, tmp_path):
    m = _open(win)
    i = np.arange(10, dtype="float64")
    pq.write_table(pa.table({"t": i, "NE_rpm": i}), tmp_path / "g.parquet")
    win.load_parquet(str(tmp_path / "g.parquet"))
    assert _list_texts(m)[0] == tr("alias.list_item", alias="rpm", source="NE_rpm")
    m.close()


# -- variable list context menu (54.3) ---------------------------------------------------------


def _ctx_actions(panel, name, monkeypatch):
    menus = []

    class _Menu(QMenu):
        def exec(self, *a, **k):  # noqa: A003
            menus.append(self.actions())

    monkeypatch.setattr(variable_panel, "QMenu", _Menu)
    item = panel.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    point = panel.visualItemRect(item).center()
    panel.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, panel.mapToGlobal(point)))
    return menus[-1] if menus else None


def test_context_menu_add_and_edit_alias_open_the_editor(win, monkeypatch):
    win.show()
    acts = _ctx_actions(win.variable_panel, "other", monkeypatch)
    assert [a.text() for a in acts] == [tr("variable.alias_add")]
    acts[0].trigger()
    m = win._alias_manager
    assert m.name_edit.text() == "" and _sources(m) == ["other"]  # a new alias with this column as candidate
    assert not m.save_button.isEnabled()  # untouched: nothing to save yet
    m.close()

    acts = _ctx_actions(win.variable_panel, "rpm", monkeypatch)
    assert [a.text() for a in acts] == [tr("variable.alias_edit")]
    acts[0].trigger()
    m = win._alias_manager
    assert m.name_edit.text() == "rpm" and _sources(m) == ["EngSpd", "NE_rpm"]
    m.close()


def test_derived_variables_have_no_alias_item(win, monkeypatch):
    win.show()
    win._on_add_derived_variable("dd", "other + 1")
    acts = _ctx_actions(win.variable_panel, "dd", monkeypatch)
    assert len(acts) == 2 and tr("variable.alias_add") not in [a.text() for a in acts]


# -- translations --------------------------------------------------------------------------------


def test_all_alias_strings_have_en_and_ja():
    keys = [k for k in _STRINGS if k.startswith(("alias.", "startup.alias", "startup.aliases", "variable.alias"))]
    keys += ["toolbar.signal_aliases", "shortcut.signal_aliases"]
    assert len(keys) > 30
    for k in keys:
        assert _STRINGS[k].get("en") and _STRINGS[k].get("ja"), k


def test_saved_message_clears_when_the_form_is_edited_again(win):
    m = _open(win)
    m.name_edit.setText("rpm_b")
    assert m.save() and m.message.text() == tr("alias.saved")
    m.name_edit.setText("rpm_c")
    assert m.message.text() == ""
    m.close()
