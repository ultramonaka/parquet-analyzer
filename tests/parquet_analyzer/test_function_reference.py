"""Function reference window (expressions ch.38 / requirement 5.31)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.core.expression import _ALLOWED_FUNCS, evaluate_expression
from parquet_analyzer.core.functions import UserFunction
from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io import user_functions as uf_io
from parquet_analyzer.ui import function_reference as fr
from parquet_analyzer.ui.function_manager import FunctionManagerWindow
from parquet_analyzer.ui.i18n import _STRINGS, set_language
from parquet_analyzer.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).parent))
import generate_sample_maps  # noqa: E402

POWER = UserFunction("Power_fn", ["p", "q"], [("k", 2.0)], "p * q / k", "demo power")


@pytest.fixture(autouse=True)
def _boxes(monkeypatch):
    for n in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: QMessageBox.StandardButton.Yes)


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, tmp_path):
    uf_io.save_user_functions([POWER])
    csvs = generate_sample_maps.write(tmp_path / "csv")
    for p in csvs:
        maps_io.import_map(p, p.stem)
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    w.show()
    yield w
    w.close()


def _select(dlg, name):
    dlg.search_edit.setText(name)
    for r in range(dlg.table.rowCount()):
        if dlg._row_entry(r).name == name:
            dlg.table.selectRow(r)
            return
    raise AssertionError(f"{name} not listed")


def test_every_builtin_documented_en_and_ja():
    assert fr.missing_builtin_docs() == []
    for name in _ALLOWED_FUNCS:
        assert name in fr.BUILTIN_DOCS
        entry = _STRINGS[f"fnref.desc.{name}"]
        assert entry["en"] and entry["ja"]


def test_missing_doc_is_detected(monkeypatch):
    monkeypatch.setitem(_ALLOWED_FUNCS, "zzz_new", (1, np.abs))
    assert fr.missing_builtin_docs() == ["zzz_new"]


def test_lists_builtins_user_functions_maps_operators(win):
    win.open_function_reference()
    dlg = win._function_reference
    names = dlg.visible_names()
    assert set(_ALLOWED_FUNCS) <= set(names)
    assert {"Power_fn", "sample_curve", "sample_map"} <= set(names)
    assert {"+", "-", "*", "/", "**", "%", "//"} <= set(names)
    by_name = {e.name: e for e in dlg._entries}
    assert by_name["Power_fn"].signature == "Power_fn(p, q, k=2)"
    assert by_name["Power_fn"].description == "demo power"
    assert by_name["Power_fn"].example == "Power_fn(p, q)"
    assert by_name["sample_curve"].signature == "sample_curve(x)"
    assert by_name["sample_map"].signature == "sample_map(x, y)"
    assert by_name["sample_map"].category == "map" and by_name["sample_curve"].category == "curve"


def test_singleton(win):
    win.open_function_reference()
    first = win._function_reference
    win.open_function_reference()
    assert win._function_reference is first


def test_search_and_category_filter(win):
    win.open_function_reference()
    dlg = win._function_reference
    dlg.search_edit.setText("ROLLING")
    assert dlg.visible_names() == ["rolling_mean", "rolling_nanmean"]
    dlg.search_edit.setText("demo power")  # matches the description
    assert dlg.visible_names() == ["Power_fn"]
    dlg.search_edit.setText("")
    dlg.category_combo.setCurrentIndex(dlg.category_combo.findData("user"))
    assert dlg.visible_names() == ["Power_fn"]
    dlg.category_combo.setCurrentIndex(dlg.category_combo.findData("compare"))
    assert dlg.visible_names() == ["min", "max", "clip"]
    dlg.search_edit.setText("zzz-nothing")
    assert dlg.visible_names() == []


def test_updates_on_user_function_and_map_changes(win):
    win.open_function_reference()
    dlg = win._function_reference
    new = {**win.function_registry.user_functions, "Other_fn": UserFunction("Other_fn", ["a"], [], "a * 2", "")}
    assert win.update_registry(user_functions=new, touched={"Other_fn"}) == []
    assert "Other_fn" in dlg.visible_names()
    maps = {n: t for n, t in win.function_registry.maps.items() if n != "sample_map"}
    assert win.update_registry(maps=maps, touched={"sample_map"}) == []
    assert "sample_map" not in dlg.visible_names()
    assert "Power_fn" in dlg.visible_names()


def test_function_manager_save_refreshes_reference(win):
    win.open_function_manager()
    win.open_function_reference()
    m = win._function_manager
    m.new_function()
    m.name_edit.setText("Tripled")
    m.params_edit.setText("a")
    m.expression_edit.setText("a * 3")
    assert m.save_function() is True
    assert "Tripled" in win._function_reference.visible_names()


def test_insert_into_expression_bar_at_cursor(win):
    win.open_function_reference()
    dlg = win._function_reference
    edit = win.expression_bar.expr_edit
    edit.setText("1 + 2")
    edit.setCursorPosition(1)
    _select(dlg, "rolling_mean")
    assert dlg.insert_button.isEnabled()
    QTest.mouseClick(dlg.insert_button, Qt.MouseButton.LeftButton)
    assert edit.text() == "1rolling_mean(sine, 20) + 2"


def test_double_click_inserts(win):
    win.open_function_reference()
    dlg = win._function_reference
    dlg.search_edit.setText("clip")
    item = dlg.table.item(0, 0)
    pos = dlg.table.visualItemRect(item).center()
    QTest.mouseClick(dlg.table.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseDClick(dlg.table.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert win.expression_bar.expr_edit.text() == "clip(sine, -0.5, 0.5)"


def test_expression_bar_button_opens_reference(win):
    QTest.mouseClick(win.expression_bar.reference_button, Qt.MouseButton.LeftButton)
    assert win._function_reference is not None and win._function_reference.isVisible()


def test_insert_into_function_manager_and_disabled_when_closed(win):
    win.open_function_manager()
    m: FunctionManagerWindow = win._function_manager
    m.new_function()
    m.show()
    QTest.mouseClick(m.reference_button, Qt.MouseButton.LeftButton)
    dlg = win._function_reference
    _select(dlg, "abs")
    assert dlg.insert_button.isEnabled()
    assert dlg.insert_selected() is True
    assert m.expression_edit.text() == "abs(sine)"
    assert win.expression_bar.expr_edit.text() == ""
    m.close()
    dlg._update_insert_enabled()
    assert not dlg.insert_button.isEnabled()
    assert dlg.insert_selected() is False
    assert m.expression_edit.text() == "abs(sine)"
    # reopening from the expression bar retargets
    win.open_function_reference()
    assert dlg.insert_button.isEnabled()


def test_insert_logs_op(win, monkeypatch):
    calls = []
    monkeypatch.setattr(fr, "log_op", lambda action, **k: calls.append((action, k)))
    win.open_function_reference()
    _select(win._function_reference, "abs")
    win._function_reference.insert_selected()
    assert ("function_reference_insert", {"name": "abs"}) in calls


def test_builtin_examples_evaluate_on_feature_test_columns():
    t = np.linspace(0, 60, 600)
    sine = np.sin(2 * np.pi * 0.1 * t)
    with_nan = sine.copy()
    with_nan[100:120] = np.nan
    arrays = {"sine": sine, "cosine": np.cos(2 * np.pi * 0.1 * t), "offset_sine": sine + 10.0,
              "ramp": t, "with_nan": with_nan}
    for name, (_cat, sig, example) in fr.BUILTIN_DOCS.items():
        assert example.startswith(name + "(") and sig.startswith(name + "(")
        out = evaluate_expression(example, arrays)
        assert np.shape(out) == t.shape, name
    for op, (_k, example) in fr.OPERATORS.items():
        assert np.shape(evaluate_expression(example, arrays)) == t.shape, op


def test_user_function_and_map_examples_evaluate(win):
    win.open_function_reference()
    arrays = {"p": np.arange(5.0), "q": np.arange(5.0), "x": np.linspace(-1, 1, 5), "y": np.linspace(-1, 1, 5)}
    for e in win._function_reference._entries:
        if e.category in ("user", "curve", "map"):
            out = evaluate_expression(e.example, arrays, win.function_registry)
            assert np.shape(out) == (5,), e.name


def test_japanese_labels(win):
    set_language("ja")
    try:
        win.open_function_reference()
        dlg = win._function_reference
        by_name = {e.name: e for e in dlg._entries}
        assert "欠損" in by_name["rolling_mean"].description
        assert "補間" in by_name["sample_curve"].description
        assert dlg.windowTitle() == "関数一覧"
    finally:
        set_language("en")
