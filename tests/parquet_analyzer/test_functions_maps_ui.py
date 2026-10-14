"""UI + wiring of user functions and maps (detailed_specification.md 26.4-26.6, 27.4-27.6)."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog, QMessageBox

from parquet_analyzer import features
from parquet_analyzer.core.functions import UserFunction
from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io import user_functions as uf_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui.function_manager import (
    DropLineEdit,
    FunctionManagerWindow,
    build_call_expression,
)
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.map_manager import MapImportDialog, default_map_name
from parquet_analyzer.ui.view_manager import ViewManagerDialog

POWER = UserFunction("Power_fn", ["p", "q"], [("k", 2.0)], "p * q / k", "demo")


@pytest.fixture
def boxes(monkeypatch):
    """Every modal answered Yes / recorded; nothing may block."""
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: calls.append("q") or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls.append(("w", a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: calls.append("i"))
    return calls


@pytest.fixture
def flags_on(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "user_functions", True)
    monkeypatch.setitem(features._EFFECTIVE, "maps", True)


def _csv(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, flags_on, boxes):
    uf_io.save_user_functions([POWER])
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    yield w
    w.close()


def _drop(field: DropLineEdit, text: str):
    mime = QMimeData()
    mime.setText(text)
    ev = QDropEvent(QPointF(1, 1), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    field.dropEvent(ev)


def _fill(mgr: FunctionManagerWindow, name, params, expr, consts=(), desc=""):
    mgr.new_function()
    mgr.name_edit.setText(name)
    mgr.params_edit.setText(params)
    mgr.expression_edit.setText(expr)
    mgr.description_edit.setText(desc)
    from PySide6.QtWidgets import QTableWidgetItem

    for n, v in consts:
        r = mgr.constants_table.rowCount()
        mgr.constants_table.insertRow(r)
        mgr.constants_table.setItem(r, 0, QTableWidgetItem(n))
        mgr.constants_table.setItem(r, 1, QTableWidgetItem(str(v)))


# -- startup / registry --------------------------------------------------------------------


def test_startup_loads_functions_and_maps_and_evaluates(qapp, isolated_config, sample_parquet_path, tmp_path, boxes):
    maps_io.import_map(_csv(tmp_path, "m.csv", "x,y\n0,0\n10,100\n"), "mc")
    uf_io.save_user_functions([POWER])
    w = MainWindow()
    try:
        w.load_parquet(str(sample_parquet_path))
        assert set(w.function_registry.user_functions) == {"Power_fn"}
        assert set(w.function_registry.maps) == {"mc"}
        w._on_add_derived_variable("d", "Power_fn(a, b) + mc(a)")  # registry passed to evaluation
        a, b = w.variable_values("a"), w.variable_values("b")
        np.testing.assert_allclose(w.variable_values("d"), a * b / 2 + np.clip(a, 0, 10) * 10)
        assert w.startup_warnings == []
    finally:
        w.close()


def test_startup_warnings_nonblocking_and_unreadable_disables_saving(qapp, isolated_config, monkeypatch, boxes):
    def boom(maps=None):
        raise uf_io.UserFunctionsUnreadable("locked")

    monkeypatch.setattr(uf_io, "load_user_functions_ex", boom)
    w = MainWindow()
    try:
        assert not w.user_functions_saveable
        assert any("locked" in m for m in w.startup_warnings)
        assert boxes == []  # no modal
        w.open_function_manager()
        mgr = w._function_manager
        _fill(mgr, "f", "x", "x + 1")
        assert mgr.save_function() is False
        assert not uf_io.user_functions_path().exists()  # never written
    finally:
        w.close()


def test_corrupt_map_reported_once_at_startup(qapp, isolated_config, boxes):
    d = isolated_config.PARQUET_ANALYZER_DATA_DIR / "maps"
    d.mkdir(parents=True)
    (d / "bad.csv").write_text("x,y\n1,1\n", encoding="utf-8")  # one point: unreadable
    (d / "bad.json").write_text(
        '{"schema_version":"1","name":"bad","kind":"curve","interpolation":"linear","extrapolation":"clamp"}',
        encoding="utf-8",
    )
    w = MainWindow()
    try:
        assert len(w.startup_warnings) == 1 and "bad" in w.startup_warnings[0]
        assert (d / "bad.csv").exists()  # file kept
    finally:
        w.close()


def test_flags_gate_only_toolbar_entries(qapp, isolated_config, sample_parquet_path, monkeypatch, boxes):
    uf_io.save_user_functions([POWER])
    for flag in ("user_functions", "maps"):
        monkeypatch.setitem(features._EFFECTIVE, flag, False)
    w = MainWindow()
    try:
        assert "user_functions" not in w._actions and "maps" not in w._actions
        w.load_parquet(str(sample_parquet_path))
        w._on_add_derived_variable("d", "Power_fn(a, b)")  # still evaluates
        assert "d" in w._derived
    finally:
        w.close()
    monkeypatch.setitem(features._EFFECTIVE, "user_functions", True)
    monkeypatch.setitem(features._EFFECTIVE, "maps", True)
    w2 = MainWindow()
    try:
        assert "user_functions" in w2._actions and "maps" in w2._actions
    finally:
        w2.close()


def test_manager_windows_are_nonmodal_singletons(win):
    win.open_function_manager()
    first = win._function_manager
    assert first.isVisible() and not first.isModal()
    win.open_function_manager()
    assert win._function_manager is first
    win.open_map_manager()
    m = win._map_manager
    win.open_map_manager()
    assert win._map_manager is m and not m.isModal()


# -- function manager: save / apply ---------------------------------------------------------


def test_save_new_function_persists_and_lists(win):
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "twice", "v", "v * 2", desc="x2")
    assert mgr.save_function()
    assert "twice" in win.function_registry.user_functions
    assert [f.name for f in uf_io.load_user_functions()[0]] == ["Power_fn", "twice"]
    assert mgr.function_list.findItems("twice", Qt.MatchFlag.MatchExactly)


@pytest.mark.parametrize(
    "name,params,expr,consts",
    [
        ("sqrt", "v", "v", ()),  # clashes with a builtin
        ("bad name", "v", "v", ()),
        ("g", "v", "w + 1", ()),  # direct column reference
        ("g", "v", "nofunc(v)", ()),
        # disallowed syntax is refused at save time (expressions 6.1)
        ("g", "v", "v is 1", ()),
        ("g", "v", "v.real", ()),
        ("g", "v", "v[0]", ()),
        ("g", "v", "v in v", ()),
        ("g", "v", "[v]", ()),
        ("g", "v", "v + True", ()),
    ],
)
def test_save_validation_errors_are_inline_not_modal(win, boxes, name, params, expr, consts):
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, name, params, expr, consts)
    assert mgr.save_function() is False
    assert mgr.definition_message.text()
    assert boxes == []
    assert list(win.function_registry.user_functions) == ["Power_fn"]


def test_new_screen_cannot_overwrite_existing_function(win):
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "Power_fn", "v", "v")
    assert mgr.save_function() is False  # replaces=None for "new"
    assert win.function_registry.user_functions["Power_fn"].expression == "p * q / k"


def test_apply_tab_drop_builds_expression_and_omits_default_constants(win):
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    f0, f1 = mgr._param_fields
    _drop(f0, "a")
    _drop(f1, "b")
    mgr.result_name_edit.setText("pw")
    assert mgr.apply_to_signals()
    assert win._derived["pw"].expression == "Power_fn(a, b)"  # k at default -> not written
    np.testing.assert_allclose(win.variable_values("pw"), win.variable_values("a") * win.variable_values("b") / 2)

    mgr._const_fields["k"].setText("4")
    mgr.result_name_edit.setText("pw2")
    assert mgr.apply_to_signals()
    assert win._derived["pw2"].expression == "Power_fn(a, b, k=4)"
    np.testing.assert_allclose(win.variable_values("pw2"), win.variable_values("a") * win.variable_values("b") / 4)


def test_drop_replaces_text_and_variable_panel_drag_mime(win):
    win.open_function_manager()
    mgr = win._function_manager
    f0 = mgr._param_fields[0]
    _drop(f0, "a")
    _drop(f0, "b")
    assert f0.text() == "b"
    item = win.variable_panel.item(0)
    mime = win.variable_panel.mimeData([item])
    assert mime.text() == item.text()  # what a real drag carries


def test_apply_errors_show_in_tab(win, boxes):
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.result_name_edit.setText("x")
    assert mgr.apply_to_signals() is False  # empty args
    assert mgr.apply_message.text()
    _drop(mgr._param_fields[0], "nothere")
    _drop(mgr._param_fields[1], "b")
    mgr.result_name_edit.setText("x")
    assert mgr.apply_to_signals() is False
    assert "x" not in win._derived and boxes == []


def test_build_call_expression_only_non_default_constants():
    fn = UserFunction("F", ["a"], [("k", 1.0), ("m", 5.0)], "a")
    assert build_call_expression(fn, ["s"], {"k": 1.0, "m": 5.0}) == "F(s)"
    assert build_call_expression(fn, ["s"], {"k": 1.0, "m": 7.5}) == "F(s, m=7.5)"


# -- recompute / rollback / in-use -----------------------------------------------------------


def _chain(win):
    win._on_add_derived_variable("d1", "Power_fn(a, b)")
    win._on_add_derived_variable("d2", "d1 + 1")  # via another derived variable
    win._on_add_derived_variable("plain", "a * 2")
    plot = win.plot_grid.plots[0]
    for n in ("d1", "d2", "plain"):
        win._add_variable_to_plot(plot, n)
    return plot


def test_function_edit_recomputes_direct_and_dependent_and_updates_curves(win):
    plot = _chain(win)
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.expression_edit.setText("p * q * k")
    assert mgr.save_function()
    a, b = win.variable_values("a"), win.variable_values("b")
    np.testing.assert_allclose(win.variable_values("d1"), a * b * 2)
    np.testing.assert_allclose(win.variable_values("d2"), a * b * 2 + 1)
    np.testing.assert_allclose(plot.series_data("d1")[1], a * b * 2)
    np.testing.assert_allclose(plot.series_data("d2")[1], a * b * 2 + 1)
    np.testing.assert_allclose(plot.series_data("plain")[1], a * 2)  # untouched
    assert uf_io.load_user_functions()[0][0].expression == "p * q * k"


def test_function_edit_reaches_derived_via_other_function(win):
    uf = UserFunction("outer_fn", ["v"], [], "Power_fn(v, v)")
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "outer_fn", "v", "Power_fn(v, v)")
    assert mgr.save_function()
    win._on_add_derived_variable("o", "outer_fn(a)")
    mgr.function_list.setCurrentRow(0)  # Power_fn
    mgr.expression_edit.setText("p * q")
    assert mgr.save_function()
    a = win.variable_values("a")
    np.testing.assert_allclose(win.variable_values("o"), a * a)
    del uf


def test_function_edit_that_breaks_a_user_rolls_back_everything(win):
    _chain(win)
    before = {n: win.variable_values(n).copy() for n in ("d1", "d2")}
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.params_edit.setText("p, q, r")  # existing call Power_fn(a, b) now has too few args
    mgr.expression_edit.setText("p * q * r / k")
    assert mgr.save_function() is False
    assert "d1" in mgr.definition_message.text()
    assert win.function_registry.user_functions["Power_fn"].params == ["p", "q"]
    assert uf_io.load_user_functions()[0][0].params == ["p", "q"]  # file untouched
    for n, v in before.items():
        np.testing.assert_array_equal(win.variable_values(n), v)


def test_edit_breaking_another_function_is_refused(win):
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "wrap", "v", "Power_fn(v, v)")
    assert mgr.save_function()
    mgr.function_list.setCurrentRow(0)
    mgr.params_edit.setText("p")
    mgr.expression_edit.setText("p / k")
    assert mgr.save_function() is False
    assert "wrap" in mgr.definition_message.text()


def test_rename_and_delete_refused_while_used_listing_users(win, boxes):
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "wrap", "v", "Power_fn(v, v)")
    mgr.save_function()
    win._on_add_derived_variable("d1", "Power_fn(a, b)")
    mgr.function_list.setCurrentItem(mgr.function_list.findItems("Power_fn", Qt.MatchFlag.MatchExactly)[0])
    mgr.name_edit.setText("Renamed")
    assert mgr.save_function() is False
    text = mgr.definition_message.text()
    assert "d1" in text and "wrap" in text
    assert "Power_fn" in win.function_registry.user_functions
    mgr.name_edit.setText("Power_fn")
    assert mgr.delete_function() is False
    assert "d1" in mgr.definition_message.text() and "Power_fn" in win.function_registry.user_functions
    assert "q" not in boxes  # refused before any confirmation


def test_rename_unused_and_delete_unused_work(win, boxes):
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.name_edit.setText("Renamed")
    assert mgr.save_function()
    assert list(win.function_registry.user_functions) == ["Renamed"]
    assert mgr.delete_function()
    assert win.function_registry.user_functions == {} and uf_io.load_user_functions()[0] == []
    assert "q" in boxes


def test_save_failure_leaves_state_untouched(win, monkeypatch):
    _chain(win)
    before = win.variable_values("d1").copy()

    def boom(fns, skipped=None):
        raise OSError("disk full")

    monkeypatch.setattr(uf_io, "save_user_functions", boom)
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.expression_edit.setText("p * q * k")
    assert mgr.save_function() is False
    assert "disk full" in mgr.definition_message.text()
    assert win.function_registry.user_functions["Power_fn"].expression == "p * q / k"
    np.testing.assert_array_equal(win.variable_values("d1"), before)


# -- maps ------------------------------------------------------------------------------------


def test_default_map_name_is_an_identifier():
    assert default_map_name("/x/eff map-1.csv") == "eff_map_1"
    assert default_map_name("/x/2024.csv") == "_2024"
    assert default_map_name("/x/日本.csv") == "map"
    assert default_map_name("/x/ok.csv").isidentifier()


CURVE = "x,y\n0,0\n10,100\n20,400\n"
MAP = "x,0,10\n0,0,10\n10,100,110\n"


def test_import_dialog_defaults_and_spline_not_offered_for_maps(win, tmp_path):
    d = MapImportDialog()
    d.set_file(str(tmp_path / "my curve.csv"))
    assert d.values()["name"] == "my_curve" and d.values()["kind"] == "auto"
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "mp.csv", MAP)), "mp")
    assert mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index("mp"))
    assert [mgr.interp_combo.itemData(i) for i in range(mgr.interp_combo.count())] == ["linear", "nearest", "previous"]
    mgr.map_list.setCurrentRow(sorted(win.function_registry.maps).index("cv"))
    assert "spline" in [mgr.interp_combo.itemData(i) for i in range(mgr.interp_combo.count())]


def test_import_dialog_through_manager(win, tmp_path, monkeypatch):
    p = str(_csv(tmp_path, "cv.csv", CURVE))

    def fake_exec(self):
        self.set_file(p)
        self.interp_combo.setCurrentIndex(self.interp_combo.findData("nearest"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(MapImportDialog, "exec", fake_exec)
    win.open_map_manager()
    win._map_manager.import_button.click()
    t = win.function_registry.maps["cv"]
    assert t.kind == "curve" and t.interpolation == "nearest"


def test_import_errors_inline_and_duplicate_names(win, tmp_path, boxes):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "bad.csv", "x,y\n1,1\n")), "bad") is False  # one point
    assert mgr.message.text() and boxes == []
    assert mgr.do_import(str(_csv(tmp_path, "c.csv", CURVE)), "Power_fn") is False  # clashes with a function
    assert mgr.do_import(str(_csv(tmp_path, "c.csv", CURVE)), "sqrt") is False
    assert mgr.do_import(str(_csv(tmp_path, "c.csv", CURVE)), "ok")
    assert mgr.do_import(str(_csv(tmp_path, "c.csv", CURVE)), "ok") is False
    assert win.function_registry.maps.keys() == {"ok"}


def test_map_settings_change_updates_derived_and_curves(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    win._on_add_derived_variable("m", "cv(a)")
    win._on_add_derived_variable("m2", "m * 2")
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "m")
    win._add_variable_to_plot(plot, "m2")
    a = win.variable_values("a")
    np.testing.assert_allclose(win.variable_values("m"), np.interp(np.clip(a, 0, 20), [0, 10, 20], [0, 100, 400]))
    mgr.interp_combo.setCurrentIndex(mgr.interp_combo.findData("previous"))
    mgr.extrap_combo.setCurrentIndex(mgr.extrap_combo.findData("zero"))
    assert mgr.apply_settings()
    expect = np.where((a < 0) | (a > 20), 0.0, np.array([0, 100, 400])[np.clip(np.searchsorted([0, 10, 20], a, side="right") - 1, 0, 2)])
    np.testing.assert_allclose(win.variable_values("m"), expect)
    np.testing.assert_allclose(plot.series_data("m")[1], expect)
    np.testing.assert_allclose(plot.series_data("m2")[1], expect * 2)
    assert maps_io.load_map("cv").interpolation == "previous"


def test_map_settings_persist_failure_rolls_back(win, tmp_path, monkeypatch):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    win._on_add_derived_variable("m", "cv(a)")
    before = win.variable_values("m").copy()
    monkeypatch.setattr(maps_io, "save_map_settings", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    mgr.interp_combo.setCurrentIndex(mgr.interp_combo.findData("nearest"))
    assert mgr.apply_settings() is False
    assert win.function_registry.maps["cv"].interpolation == "linear"
    np.testing.assert_array_equal(win.variable_values("m"), before)


def test_reimport_changes_values_and_missing_source_errors(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    src = _csv(tmp_path, "cv.csv", CURVE)
    mgr.do_import(str(src), "cv")
    win._on_add_derived_variable("m", "cv(a)")
    src.write_text("x,y\n0,1000\n20,1000\n", encoding="utf-8")
    assert mgr.reimport_selected()
    np.testing.assert_allclose(win.variable_values("m"), 1000.0)
    src.unlink()
    assert mgr.reimport_selected() is False and mgr.message.text()
    assert win.function_registry.maps["cv"] is not None


def test_map_in_use_cannot_be_deleted(win, tmp_path, boxes):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    win._on_add_derived_variable("m", "cv(a)")
    assert mgr.delete_selected() is False
    assert "m" in mgr.message.text() and "cv" in win.function_registry.maps and "q" not in boxes
    win.delete_derived_variable("m")
    # a user function calling the map also blocks deletion
    win.open_function_manager()
    _fill(win._function_manager, "usecv", "v", "cv(v)")
    assert win._function_manager.save_function()
    assert mgr.delete_selected() is False and "usecv" in mgr.message.text()


def test_map_delete_unused_removes_files(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    assert mgr.delete_selected()
    assert win.function_registry.maps == {} and maps_io.list_maps() == []


def test_map_previews_render_curve_and_map(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    mgr.do_import(str(_csv(tmp_path, "mp.csv", MAP)), "mp")
    names = sorted(win.function_registry.maps)
    mgr.map_list.setCurrentRow(names.index("cv"))
    assert mgr.preview_table.rowCount() == 3
    assert len(mgr.plot_raw.getPlotItem().listDataItems()) == 2  # interpolated + table points
    assert not mgr.plot_result.isVisibleTo(mgr)
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert (mgr.preview_table.rowCount(), mgr.preview_table.columnCount()) == (3, 3)  # axes = first row and column (27.8)
    assert mgr.plot_result.isVisibleTo(mgr)


# -- views -------------------------------------------------------------------------------------


def _save_view(name, plots, derived):
    view_io.save_view(
        View(view_name=name, source=SourceDef("/data/f.parquet", "absolute"), plots=plots, derived_variables=derived)
    )


def _marked_names(dialog):
    marked = set()

    def walk(item):
        if " — " in item.text(0) and item.foreground(0).color().name() == "#d32f2f":
            marked.add(item.text(0).split(" — ")[0])
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i))
    return marked


def test_view_with_missing_function_skips_chain_and_manager_marks_match(win, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    maps_io.import_map(_csv(tmp_path, "cv.csv", CURVE), "cv")
    win.function_registry.maps["cv"] = maps_io.load_map("cv")
    _save_view(
        "v",
        [PlotDef("p1", [SeriesDef(n, "#ff0000") for n in ("a", "d1", "d2", "dm", "ok", "ghost")])],
        [
            DerivedVariableDef("d1", "Power_fn(a, b)"),
            DerivedVariableDef("d2", "d1 + 1"),
            DerivedVariableDef("dm", "cv(a)"),
            DerivedVariableDef("ok", "a * 2"),
        ],
    )
    # the view is opened where the function and the map are gone
    win.function_registry.user_functions.clear()
    del win.function_registry.maps["cv"]
    available = {c for c in win._file_columns if c != win._time_column}
    dialog = ViewManagerDialog(available, available_functions=win.function_registry.all_names())
    marked = _marked_names(dialog)
    win.load_view("v")
    loaded = set(win.plot_grid.plots[0].series_names()) | set(win._derived)
    view = view_io.load_view("v")
    names = {s.variable for p in view.plots for s in p.series} | {d.name for d in view.derived_variables}
    for n in names:
        assert (n in marked) == (n not in loaded), n
    assert {"d1", "d2", "dm", "ghost"} <= marked and not ({"a", "ok"} & marked)
    # one consolidated warning that names the missing function and the map
    assert len(seen) == 1 and "Power_fn" in seen[0] and "cv" in seen[0]
    assert "ok" in win._derived and "d1" not in win._derived


def test_load_view_warning_names_missing_function(win, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    _save_view("v", [PlotDef("p1", [SeriesDef("a", "#ff0000")])], [DerivedVariableDef("d1", "Gone_fn(a)")])
    win.load_view("v")
    assert len(seen) == 1 and "Gone_fn" in seen[0] and "d1" in seen[0]


def test_view_manager_marker_text_names_function(win):
    _save_view("v", [PlotDef("p1", [SeriesDef("a", "#ff0000")])], [DerivedVariableDef("d1", "Gone_fn(a)")])
    available = {c for c in win._file_columns if c != win._time_column}
    dialog = ViewManagerDialog(available, available_functions=win.function_registry.all_names())
    rows = []

    def walk(item):
        rows.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i))
    assert any(r.startswith("d1 = Gone_fn(a)") and "Gone_fn" in r.split(" — ", 1)[1] for r in rows)


def test_view_with_present_function_loads_fully(win):
    _save_view("v", [PlotDef("p1", [SeriesDef("d1", "#ff0000")])], [DerivedVariableDef("d1", "Power_fn(a, b)")])
    win.load_view("v")
    assert "d1" in win._derived and win.plot_grid.plots[0].series_names() == ["d1"]


# -- review round 2 ----------------------------------------------------------------------------


def _raw_names(path):
    import json

    return [f["name"] for f in json.loads(path.read_text(encoding="utf-8"))["functions"]]


def _write_with_broken_entry(isolated_config):
    """F is fine; H calls a map that does not exist, so it is skipped at startup."""
    uf_io.save_user_functions([UserFunction("F", ["p"], [], "p * 2"), UserFunction("H", ["p"], [], "missing_map(p)")])


def test_skipped_functions_survive_a_save(qapp, isolated_config, boxes):
    _write_with_broken_entry(isolated_config)
    before = [f for f in __import__("json").loads(uf_io.user_functions_path().read_text())["functions"] if f["name"] == "H"]
    w = MainWindow()
    try:
        assert set(w.function_registry.user_functions) == {"F"} and w.startup_warnings
        w.open_function_manager()
        mgr = w._function_manager
        _fill(mgr, "NewFn", "x", "x + 1")
        assert mgr.save_function()
        assert _raw_names(uf_io.user_functions_path()) == ["F", "NewFn", "H"]
        after = [f for f in __import__("json").loads(uf_io.user_functions_path().read_text())["functions"] if f["name"] == "H"]
        assert after == before  # verbatim
        mgr.function_list.setCurrentItem(mgr.function_list.findItems("F", Qt.MatchFlag.MatchExactly)[0])
        assert mgr.delete_function()
        assert _raw_names(uf_io.user_functions_path()) == ["NewFn", "H"]
    finally:
        w.close()


def test_skipped_function_names_stay_reserved_for_functions_and_maps(qapp, isolated_config, tmp_path, boxes):
    _write_with_broken_entry(isolated_config)
    w = MainWindow()
    try:
        w.open_function_manager()
        mgr = w._function_manager
        _fill(mgr, "H", "x", "x")
        assert mgr.save_function() is False and "H" in mgr.definition_message.text()
        w.open_map_manager()
        mm = w._map_manager
        assert mm.do_import(str(_csv(tmp_path, "h.csv", CURVE)), "H") is False
        assert maps_io.list_maps() == []
        # rename onto a reserved name is refused too
        mgr.function_list.setCurrentRow(0)
        mgr.name_edit.setText("H")
        assert mgr.save_function() is False
    finally:
        w.close()


def test_load_user_functions_ex_returns_raw_skipped(isolated_config):
    _write_with_broken_entry(isolated_config)
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [f.name for f in fns] == ["F"] and warns and [e["name"] for e in skipped] == ["H"]
    uf_io.save_user_functions(fns, skipped)
    assert _raw_names(uf_io.user_functions_path()) == ["F", "H"]


@pytest.fixture
def plotted_win(win):
    win._on_add_derived_variable("d", "Power_fn(a, b)")
    win._add_variable_to_plot(win.plot_grid.plots[0], "d")
    return win


def test_scalar_body_via_function_edit_is_a_constant_curve(plotted_win):
    win = plotted_win
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.expression_edit.setText("k")  # ignores the parameters: a scalar
    assert mgr.save_function()
    n = len(win._time_values)
    np.testing.assert_allclose(win.variable_values("d"), 2.0)
    assert len(win.variable_values("d")) == n
    np.testing.assert_allclose(win.plot_grid.plots[0].series_data("d")[1], 2.0)


def test_edit_derived_scalar_and_add_scalar_then_plot(win):
    win._on_add_derived_variable("d", "a")
    win._add_variable_to_plot(win.plot_grid.plots[0], "d")
    win.edit_derived_variable("d", "2")
    np.testing.assert_allclose(win.plot_grid.plots[0].series_data("d")[1], 2.0)
    win._on_add_derived_variable("two", "2")
    win._add_variable_to_plot(win.plot_grid.plots[0], "two")
    y = win.plot_grid.plots[0].series_data("two")[1]
    assert y.shape == win._time_values.shape and np.all(y == 2.0)


def test_bad_shape_result_rolls_back_before_commit(plotted_win, monkeypatch):
    win = plotted_win
    before = win.variable_values("d").copy()
    from parquet_analyzer.core import functions as fmod

    orig = win._series_value
    monkeypatch.setattr(win, "_series_value", lambda v: orig(np.zeros(3)))
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    mgr.expression_edit.setText("p * q")
    assert mgr.save_function() is False
    assert win.function_registry.user_functions["Power_fn"].expression == "p * q / k"
    assert uf_io.load_user_functions()[0][0].expression == "p * q / k"
    np.testing.assert_array_equal(win.variable_values("d"), before)
    del fmod


def test_heatmap_cells_contain_their_nodes_on_non_uniform_axes(win, tmp_path):
    from parquet_analyzer.ui.map_manager import cell_edges

    xs = np.array([0.0, 10.0, 100.0])
    e = cell_edges(xs)
    assert len(e) == 4
    for i, x in enumerate(xs):
        assert e[i] <= x <= e[i + 1]
    ys = np.array([0.0, 1.0, 10.0])
    ey = cell_edges(ys)
    assert all(ey[i] <= y <= ey[i + 1] for i, y in enumerate(ys))
    win.open_map_manager()
    mm = win._map_manager
    assert mm.do_import(str(_csv(tmp_path, "nu.csv", "x,0,10,100\n0,0,1,2\n1,3,4,5\n10,6,7,8\n")), "nu")
    import pyqtgraph as pg

    items = [i for i in mm.plot_raw.getPlotItem().items if isinstance(i, pg.PColorMeshItem)]
    assert len(items) == 1
    rect = items[0].boundingRect()
    # the drawn extent covers the real node coordinates (node (10, 1) is inside, not at ~(50, 5))
    assert rect.left() <= 0 and rect.right() >= 100 and rect.top() <= 0 and rect.bottom() >= 10


def test_apply_tab_refuses_existing_result_name(win):
    win.open_function_manager()
    mgr = win._function_manager
    mgr.function_list.setCurrentRow(0)
    _drop(mgr._param_fields[0], "a")
    _drop(mgr._param_fields[1], "b")
    for existing in ("c", "d_exists"):
        if existing == "d_exists":
            win._on_add_derived_variable("d_exists", "a")
        mgr.result_name_edit.setText(existing)
        assert mgr.apply_to_signals() is False
        assert "already" in mgr.apply_message.text().lower() or mgr.apply_message.text()
    assert win._derived["d_exists"].expression == "a"  # not silently redefined


def test_ops_are_logged_at_start_even_when_refused(win, monkeypatch):
    from parquet_analyzer.ui import function_manager as fm_mod
    from parquet_analyzer.ui import map_manager as mm_mod

    calls = []
    monkeypatch.setattr(fm_mod, "log_op", lambda action, **k: calls.append(action))
    monkeypatch.setattr(mm_mod, "log_op", lambda action, **k: calls.append(action))
    win.open_function_manager()
    mgr = win._function_manager
    _fill(mgr, "sqrt", "v", "v")
    mgr.save_function()
    assert calls[:2] == ["user_function_save", "user_function_save_refused"]
    calls.clear()
    mgr.function_list.setCurrentRow(0)
    _drop(mgr._param_fields[0], "a")
    _drop(mgr._param_fields[1], "b")
    mgr.result_name_edit.setText("zz")
    mgr.apply_to_signals()
    assert calls[0] == "user_function_apply"  # before add_derived_variable would log its own
    calls.clear()
    win.open_map_manager()
    win._map_manager.do_import("/nonexistent.csv", "q")
    assert calls[:2] == ["map_import", "map_import_refused"]


def test_view_manager_marks_signature_mismatch_like_load_view(win, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    _save_view(
        "v",
        [PlotDef("p1", [SeriesDef(n, "#ff0000") for n in ("d1", "d2", "d3", "d4", "ok")])],
        [
            DerivedVariableDef("d1", "Power_fn(a)"),  # too few args
            DerivedVariableDef("d2", "sqrt(a, b)"),  # builtin arity
            DerivedVariableDef("d3", "Power_fn(a, b, nope=1)"),  # unknown constant
            DerivedVariableDef("d4", "d1 + 1"),
            DerivedVariableDef("ok", "Power_fn(a, b, k=3)"),
        ],
    )
    available = {c for c in win._file_columns if c != win._time_column}
    dialog = ViewManagerDialog(available, available_functions=win.function_registry.all_names(),
                               registry=win.function_registry)
    marked = _marked_names(dialog)
    win.load_view("v")
    loaded = set(win.plot_grid.plots[0].series_names()) | set(win._derived)
    for n in ("d1", "d2", "d3", "d4", "ok"):
        assert (n in marked) == (n not in loaded), n
    assert {"d1", "d2", "d3", "d4"} <= marked and "ok" not in marked


def test_registry_labels_have_en_and_ja_and_fallback():
    from parquet_analyzer.core.maps import EXTRAPOLATIONS, INTERPOLATIONS
    from parquet_analyzer.ui import i18n
    from parquet_analyzer.ui.map_manager import _label

    for key in INTERPOLATIONS:
        assert {"en", "ja"} <= set(i18n._STRINGS[f"map.interp_{key}"])
    for key in EXTRAPOLATIONS:
        assert {"en", "ja"} <= set(i18n._STRINGS[f"map.extrap_{key}"])
    assert _label("map.interp_", "brand_new") == "brand_new"


def test_import_dialog_hides_spline_for_maps_and_combos_revert_on_rollback(win, tmp_path, monkeypatch):
    d = MapImportDialog()
    assert "spline" in [d.interp_combo.itemData(i) for i in range(d.interp_combo.count())]
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("map"))
    assert "spline" not in [d.interp_combo.itemData(i) for i in range(d.interp_combo.count())]
    win.open_map_manager()
    mgr = win._map_manager
    mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    monkeypatch.setattr(maps_io, "save_map_settings", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    mgr.interp_combo.setCurrentIndex(mgr.interp_combo.findData("nearest"))
    assert mgr.apply_settings() is False
    assert mgr.interp_combo.currentData() == "linear"


# -- 26.4 (2026-10-03): input examples in the definition tab -----------------------------

class TestFunctionExamples:
    @pytest.mark.parametrize("lang", ["en", "ja"])
    def test_placeholders_and_tooltip(self, win, lang):
        from parquet_analyzer.ui import i18n
        i18n.set_language(lang)
        try:
            m = FunctionManagerWindow(win)
            assert "Power_flow_by_press" in m.name_edit.placeholderText()
            assert "flow_lpm, press_kpa" in m.params_edit.placeholderText()
            assert "flow_lpm * press_kpa / k" in m.expression_edit.placeholderText()
            assert ("流量×圧力" in m.description_edit.placeholderText()) == (lang == "ja")
            assert "k = 60000" in m.constants_hint.text()
            tip = m.expression_edit.toolTip()
            for tok in ("**", "sqrt", "clip", "rolling_nanmean"):
                assert tok in tip
            assert m.example_button.text() == ("例を入れる" if lang == "ja" else "Insert example")
        finally:
            i18n.set_language("en")

    def test_button_fills_everything_without_saving(self, win, monkeypatch):
        m = FunctionManagerWindow(win)
        m.new_function()
        asked = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)
        stored = dict(win.function_registry.user_functions)
        m.example_button.click()
        assert not asked  # empty fields: no confirmation
        assert m.name_edit.text() == "Power_flow_by_press"
        assert m.params_edit.text() == "flow_lpm, press_kpa"
        assert m.expression_edit.text() == "flow_lpm * press_kpa / k"
        assert m.description_edit.text()
        assert m.constants_table.rowCount() == 1
        assert m.constants_table.item(0, 0).text() == "k" and m.constants_table.item(0, 1).text() == "60000"
        assert dict(win.function_registry.user_functions) == stored
        assert [f.name for f in uf_io.load_user_functions()[0]] == list(stored)

    def test_confirmation_when_non_empty(self, win, monkeypatch):
        m = FunctionManagerWindow(win)
        m.new_function()
        m.name_edit.setText("mine")
        answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))
        m.example_button.click()
        assert m.name_edit.text() == "mine"
        m.example_button.click()
        assert m.name_edit.text() == "Power_flow_by_press"

    def test_constants_row_alone_counts_as_content(self, win, monkeypatch):
        from PySide6.QtWidgets import QTableWidgetItem
        m = FunctionManagerWindow(win)
        m.new_function()
        m.constants_table.insertRow(0)
        m.constants_table.setItem(0, 0, QTableWidgetItem("z"))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        m.example_button.click()
        assert m.name_edit.text() == ""

    def test_example_over_selected_function_saves_as_new(self, win):
        m = FunctionManagerWindow(win)  # boxes fixture answers Yes
        assert m.selected_function().name == "Power_fn"
        m.example_button.click()
        assert m._current is None
        assert m.save_function() is True
        assert set(win.function_registry.user_functions) == {"Power_fn", "Power_flow_by_press"}

    def test_example_validates_and_saves(self, win):
        m = FunctionManagerWindow(win)
        m.new_function()
        m.example_button.click()
        assert m.save_function() is True
        fn = win.function_registry.user_functions["Power_flow_by_press"]
        assert fn.constants == [("k", 60000.0)]
