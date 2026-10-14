"""Map manager: edit values in the table + splitter (detailed_specification.md 27.8)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QMessageBox, QSplitter
from test_functions_maps_ui import CURVE, MAP, _csv, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)

from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io.settings import Settings, load_settings, save_settings
from parquet_analyzer.ui import map_manager

Yes, No, Cancel = (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No, QMessageBox.StandardButton.Cancel)


@pytest.fixture
def ask(monkeypatch):
    """Answer for `messages.question`; set `ask.answer`; `ask.calls` records the texts."""

    class Ask:
        answer = Yes

    a = Ask()
    a.calls = []

    def fake(parent, title, text, *args, **kwargs):
        a.calls.append(text)
        return a.answer

    monkeypatch.setattr(map_manager.messages, "question", fake)
    return a


@pytest.fixture
def ops(monkeypatch):
    recorded = []
    monkeypatch.setattr(map_manager, "log_op", lambda action, **d: recorded.append((action, d)))
    return recorded


def _open(win, tmp_path, name="cv", text=CURVE):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, f"{name}.csv", text)), name)
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index(name))
    return mgr


def _set(mgr, r, c, text):
    mgr.preview_table.item(r, c).setText(text)


def _stored_csv(name):
    return (maps_io.maps_dir() / f"{name}.csv").read_text(encoding="utf-8")


def _stored_json(name):
    return (maps_io.maps_dir() / f"{name}.json").read_text(encoding="utf-8")


def test_edit_curve_cell_saves_csv_registry_and_recomputes(win, tmp_path):
    mgr = _open(win, tmp_path)
    win._on_add_derived_variable("m", "cv(a)")
    before = win.variable_values("m").copy()
    assert not mgr.save_values_button.isEnabled() and not mgr.revert_button.isEnabled()
    _set(mgr, 1, 1, "200")  # (10, 100) -> (10, 200)
    assert mgr.save_values_button.isEnabled() and mgr.revert_button.isEnabled()
    assert mgr.save_values() is True
    t = win.function_registry.maps["cv"]
    np.testing.assert_array_equal(t.y, [0, 200, 400])
    np.testing.assert_allclose(win.variable_values("m"), t(win.variable_values("a")))
    assert not np.allclose(win.variable_values("m"), before)
    # stored copy rewritten in the 27.2 format and loads back; edited_at is set
    assert maps_io.load_map("cv").y.tolist() == [0, 200, 400]
    assert "10.0,200.0" in _stored_csv("cv")
    info = maps_io.list_maps()[0]
    assert info.edited_at.startswith("20")
    assert json.loads(_stored_json("cv"))["edited_at"] == info.edited_at
    assert not mgr.save_values_button.isEnabled()
    assert mgr.preview_table.item(1, 1).text() == "200"


def test_edit_through_real_editor_keyboard(win, tmp_path, qapp):
    mgr = _open(win, tmp_path)
    mgr.show()
    qapp.processEvents()
    table = mgr.preview_table
    table.setCurrentCell(2, 1)
    table.edit(table.model().index(2, 1))
    editor = table.viewport().findChild(QLineEdit)
    qapp.processEvents()
    assert editor is not None
    editor.selectAll()
    QTest.keyClicks(editor, "123")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    qapp.processEvents()
    assert table.item(2, 1).text() == "123"
    assert mgr.save_values_button.isEnabled()
    assert mgr.save_values() is True
    assert win.function_registry.maps["cv"].y[2] == 123


def test_edit_map_cells_and_axes_and_edited_cell_highlight(win, tmp_path):
    mgr = _open(win, tmp_path, "mp", MAP)
    t = mgr.preview_table
    assert (t.rowCount(), t.columnCount()) == (3, 3)  # axes are the first row / column
    assert not (t.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)
    _set(mgr, 2, 2, "999")  # z
    _set(mgr, 0, 2, "20")  # X axis value 10 -> 20
    _set(mgr, 2, 0, "30")  # Y axis value 10 -> 30
    assert {t.item(r, c).background().color().name() for r, c in ((2, 2), (0, 2), (2, 0))} == {"#ffcc80"}
    assert t.item(1, 1).background().color().name() != "#ffcc80"
    assert mgr.save_values()
    m = win.function_registry.maps["mp"]
    assert m.x.tolist() == [0, 20] and m.y.tolist() == [0, 30] and m.z[1, 1] == 999
    assert maps_io.load_map("mp").z[1, 1] == 999
    assert _stored_csv("mp").splitlines()[0] == ",0.0,20.0"


def test_unchanged_cells_keep_full_precision(win, tmp_path):
    mgr = _open(win, tmp_path, "pr", "x,y\n0,0.123456789012\n1,2\n")
    _set(mgr, 1, 1, "3")
    assert mgr.save_values()
    assert win.function_registry.maps["pr"].y.tolist() == [0.123456789012, 3.0]


def test_edit_back_to_original_clears_changed_state(win, tmp_path):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "55")
    assert mgr.has_unsaved_edits()
    _set(mgr, 1, 1, "100.0")  # same number as the original
    assert not mgr.has_unsaved_edits() and not mgr.save_values_button.isEnabled()
    assert mgr.preview_table.item(1, 1).background().color().name() != "#ffcc80"


@pytest.mark.parametrize(
    "r, c, text, needle",
    [
        (1, 1, "abc", "row 2, column 2"),
        # "nan" / blank in a value cell is now an empty cell (27.10): see test_maps_empty_cells.py
        (1, 1, "inf", "non-finite"),
        (1, 0, "-5", "row 2, column 1"),  # x of row 2 becomes -5 < 0: descending
        (2, 0, "10", "duplicate"),
    ],
)
def test_invalid_curve_edits_are_not_saved(win, tmp_path, r, c, text, needle):
    mgr = _open(win, tmp_path)
    before = _stored_csv("cv")
    _set(mgr, r, c, text)
    assert mgr.save_values() is False
    assert needle in mgr.message.text()
    assert _stored_csv("cv") == before
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    assert mgr.preview_table.item(r, c).text() == text  # the edit stays on screen
    assert mgr.has_unsaved_edits()


def test_descending_map_axis_names_row_and_column(win, tmp_path):
    mgr = _open(win, tmp_path, "mp", MAP)
    _set(mgr, 0, 2, "-1")  # X axis: 0, -1
    assert mgr.save_values() is False
    assert "row 1, column 3" in mgr.message.text()
    mgr.revert_values()
    _set(mgr, 2, 0, "-4")  # Y axis
    assert mgr.save_values() is False
    assert "row 3, column 1" in mgr.message.text()
    assert win.function_registry.maps["mp"].x.tolist() == [0, 10]


def test_recompute_failure_rolls_back_and_keeps_files(win, tmp_path, monkeypatch):
    mgr = _open(win, tmp_path)
    win._on_add_derived_variable("m", "cv(a)")
    before = win.variable_values("m").copy()
    csv_before, json_before = _stored_csv("cv"), _stored_json("cv")

    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr("parquet_analyzer.ui.main_window.evaluate_expression", boom)
    _set(mgr, 1, 1, "200")
    assert mgr.save_values() is False
    assert "m" in mgr.message.text() and "boom" in mgr.message.text()
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    assert _stored_csv("cv") == csv_before and _stored_json("cv") == json_before
    np.testing.assert_array_equal(win.variable_values("m"), before)
    assert mgr.has_unsaved_edits()


def test_write_failure_restores_stored_files(win, tmp_path, monkeypatch):
    mgr = _open(win, tmp_path)
    csv_before, json_before = _stored_csv("cv"), _stored_json("cv")

    def flaky(info):
        raise OSError("disk full")

    original = maps_io._write_meta
    monkeypatch.setattr(maps_io, "_write_meta", flaky)  # fails after the CSV was rewritten
    _set(mgr, 1, 1, "200")
    assert mgr.save_values() is False
    monkeypatch.setattr(maps_io, "_write_meta", original)
    assert _stored_csv("cv") == csv_before and _stored_json("cv") == json_before
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]


def test_revert_discards_edits_and_logs(win, tmp_path, ops):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "7")
    _set(mgr, 2, 1, "8")
    mgr.revert_button.click()
    assert [mgr.preview_table.item(i, 1).text() for i in range(3)] == ["0", "100", "400"]
    assert not mgr.has_unsaved_edits() and not mgr.revert_button.isEnabled()
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    assert [a for a, _ in ops if a == "map_values_revert"] == ["map_values_revert"]


def test_edit_logged_with_changed_cell_count(win, tmp_path, ops):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "7")
    _set(mgr, 2, 1, "8")
    mgr.save_values()
    rec = [d for a, d in ops if a == "map_values_edit"]
    assert len(rec) == 1 and rec[0]["cells"] == 2 and rec[0]["name"] == "cv"


def test_switching_map_with_unsaved_edits_asks(win, tmp_path, ask):
    mgr = _open(win, tmp_path, "cv")
    assert mgr.do_import(str(_csv(tmp_path, "mp.csv", MAP)), "mp")
    names = sorted(win.function_registry.maps)
    mgr.map_list.setCurrentRow(names.index("cv"))
    _set(mgr, 1, 1, "200")
    # Cancel: stay on cv, edits kept
    ask.answer = Cancel
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert ask.calls and mgr.selected_name() == "cv" and mgr.has_unsaved_edits()
    assert mgr.preview_table.item(1, 1).text() == "200"
    # No: discard, switch
    ask.answer = No
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert mgr.selected_name() == "mp" and not mgr.has_unsaved_edits()
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    # Yes: save, then switch
    mgr.map_list.setCurrentRow(names.index("cv"))
    _set(mgr, 1, 1, "300")
    ask.answer = Yes
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert mgr.selected_name() == "mp"
    assert win.function_registry.maps["cv"].y.tolist() == [0, 300, 400]
    assert mgr.preview_table.columnCount() == 3  # the map's table is shown


def test_switch_with_failing_save_stays(win, tmp_path, ask):
    mgr = _open(win, tmp_path, "cv")
    assert mgr.do_import(str(_csv(tmp_path, "mp.csv", MAP)), "mp")
    names = sorted(win.function_registry.maps)
    mgr.map_list.setCurrentRow(names.index("cv"))
    _set(mgr, 1, 1, "bad")
    ask.answer = Yes
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert mgr.selected_name() == "cv" and mgr.has_unsaved_edits()


def test_closing_with_unsaved_edits_asks(win, tmp_path, ask):
    mgr = _open(win, tmp_path)
    mgr.show()
    _set(mgr, 1, 1, "200")
    ask.answer = Cancel
    mgr.close()
    assert ask.calls and mgr.isVisible()
    ask.answer = No
    mgr.close()
    assert not mgr.isVisible()
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    mgr.show()
    mgr.map_list.setCurrentRow(0)
    _set(mgr, 1, 1, "250")
    ask.answer = Yes
    mgr.reject()
    assert not mgr.isVisible() and win.function_registry.maps["cv"].y[1] == 250


def test_no_confirm_when_nothing_edited(win, tmp_path, ask):
    mgr = _open(win, tmp_path)
    mgr.show()
    mgr.close()
    assert ask.calls == []


def test_reimport_of_edited_map_asks_and_restores_source(win, tmp_path, ask):
    mgr = _open(win, tmp_path)
    assert mgr.reimport_selected() is True  # never edited: no question
    assert ask.calls == []
    _set(mgr, 1, 1, "200")
    assert mgr.save_values()
    ask.answer = No
    assert mgr.reimport_selected() is False
    assert len(ask.calls) == 1 and win.function_registry.maps["cv"].y[1] == 200
    ask.answer = Yes
    assert mgr.reimport_selected() is True
    assert win.function_registry.maps["cv"].y.tolist() == [0, 100, 400]
    assert maps_io.list_maps()[0].edited_at == ""
    assert mgr.preview_table.item(1, 1).text() == "100"


def test_reimport_with_unsaved_edits_asks(win, tmp_path, ask):
    mgr = _open(win, tmp_path)
    _set(mgr, 1, 1, "200")
    ask.answer = No
    assert mgr.reimport_selected() is False and ask.calls
    assert mgr.has_unsaved_edits()


def test_old_json_without_edited_at_loads(win, tmp_path):
    _open(win, tmp_path)
    p = maps_io.maps_dir() / "cv.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("edited_at", None)
    p.write_text(json.dumps(d), encoding="utf-8")
    assert maps_io.list_maps()[0].edited_at == ""
    assert maps_io.load_map("cv").y.tolist() == [0, 100, 400]


# -- splitter -----------------------------------------------------------------------------


def test_splitter_exists_with_object_name(win, tmp_path):
    mgr = _open(win, tmp_path)
    assert isinstance(mgr.splitter, QSplitter) and mgr.splitter.objectName() == "map_manager_splitter"
    assert mgr.splitter.orientation() == Qt.Orientation.Vertical
    assert mgr.splitter.count() == 2
    assert mgr.splitter.widget(0).isAncestorOf(mgr.preview_table)
    assert mgr.splitter.widget(1).isAncestorOf(mgr.plot_raw) and mgr.splitter.widget(1).isAncestorOf(mgr.plot_result)


def test_splitter_ratio_saved_and_restored(win, tmp_path, qapp):
    mgr = _open(win, tmp_path)
    mgr.show()
    qapp.processEvents()
    mgr.splitter.setSizes([100, 400])
    mgr.splitter.splitterMoved.emit(100, 1)
    mgr.close()
    saved = load_settings().map_manager_splitter
    assert saved and len(saved) == 2 and saved[1] > saved[0] * 2
    assert win.settings.map_manager_splitter == saved
    # a fresh manager restores it
    win._map_manager = None
    win.open_map_manager()
    mgr2 = win._map_manager
    mgr2.show()
    qapp.processEvents()
    a, b = mgr2.splitter.sizes()
    assert b > a * 2


def test_splitter_settings_round_trip_and_garbage(isolated_config):
    s = Settings()
    assert s.map_manager_splitter is None
    s.map_manager_splitter = [120, 360]
    save_settings(s)
    assert load_settings().map_manager_splitter == [120, 360]
    path = isolated_config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    for bad in ([1], [0, 5], "x", [1.5, 2], [-1, 3], [True, 2]):
        d = json.loads(path.read_text(encoding="utf-8"))
        d["map_manager_splitter"] = bad
        path.write_text(json.dumps(d), encoding="utf-8")
        assert load_settings().map_manager_splitter is None
