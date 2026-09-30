"""Editing/deleting derived variables (specification.md 5.5, detailed_specification.md 18章)."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt

from parquet_analyzer.core.expression import ExpressionError, referenced_names
from parquet_analyzer.core.variable import dependency_order, dependents_of
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow


# -- core ------------------------------------------------------------------------------


def test_referenced_names_excludes_function_names():
    assert referenced_names("abs(a) + rolling_mean(b, 3) * c") == {"a", "b", "c"}
    assert referenced_names("ａ＋ｂ") == {"a", "b"}  # same NFKC normalization as evaluation


def test_dependency_order_puts_dependencies_first_and_is_stable():
    exprs = {"x": "y + 1", "p": "a", "y": "a * 2", "z": "x + y"}
    assert dependency_order(exprs) == ["p", "y", "x", "z"]
    assert dependency_order({"p": "a", "q": "b"}) == ["p", "q"]


def test_dependency_order_appends_cycles_and_bad_syntax_instead_of_raising():
    assert dependency_order({"ok": "a", "c1": "c2", "c2": "c1", "bad": "a +"}) == ["ok", "c1", "c2", "bad"]


def test_dependents_of_is_transitive():
    exprs = {"d1": "a", "d2": "d1 * 2", "d3": "d2 + b", "other": "b"}
    assert dependents_of("d1", exprs) == ["d2", "d3"]
    assert dependents_of("d3", exprs) == []


# -- MainWindow ------------------------------------------------------------------------


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path):
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    yield w
    w.close()


def test_edit_recomputes_dependents_and_updates_plotted_curves_in_place(win):
    win._on_add_derived_variable("d1", "a * 2")
    win._on_add_derived_variable("d2", "d1 + 1")
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "d1")
    win._add_variable_to_plot(plot, "d2")
    _, _, color_before = plot.series_data("d1")

    win.edit_derived_variable("d1", "a * 3")

    a = win.variable_values("a")
    np.testing.assert_allclose(win.variable_values("d1"), a * 3)
    np.testing.assert_allclose(win.variable_values("d2"), a * 3 + 1)  # used to stay a*2+1
    _, y1, color_after = plot.series_data("d1")
    _, y2, _ = plot.series_data("d2")
    np.testing.assert_allclose(y1, a * 3)
    np.testing.assert_allclose(y2, a * 3 + 1)
    assert color_after == color_before
    assert plot.series_names() == ["d1", "d2"]  # order kept, not re-appended


def test_redefining_via_expression_bar_also_updates_dependents(win):
    win._on_add_derived_variable("d1", "a")
    win._on_add_derived_variable("d2", "d1 * 10")
    win._on_add_derived_variable("d1", "b")
    np.testing.assert_allclose(win.variable_values("d2"), win.variable_values("b") * 10)


def test_edit_rejects_circular_definitions_and_leaves_state_unchanged(win):
    win._on_add_derived_variable("d1", "a")
    win._on_add_derived_variable("d2", "d1 * 2")
    for bad in ("d1 + 1", "d2 + a"):
        with pytest.raises(ExpressionError):
            win.edit_derived_variable("d1", bad)
    assert win._derived["d1"].expression == "a"
    np.testing.assert_allclose(win.variable_values("d2"), win.variable_values("a") * 2)


def test_failed_edit_is_all_or_nothing(win):
    win._on_add_derived_variable("d1", "a")
    with pytest.raises(ExpressionError):
        win.edit_derived_variable("d1", "no_such_column + 1")
    assert win._derived["d1"].expression == "a"
    np.testing.assert_allclose(win.variable_values("d1"), win.variable_values("a"))


def test_edit_and_delete_refuse_raw_columns(win):
    with pytest.raises(ExpressionError):
        win.edit_derived_variable("a", "b")
    with pytest.raises(ExpressionError):
        win.delete_derived_variable("a")
    assert "a" in win._raw_variables


def test_delete_removes_from_panel_and_plots(win):
    win._on_add_derived_variable("d1", "a * 2")
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win._add_variable_to_plot(plot, "d1")

    win.delete_derived_variable("d1")

    assert "d1" not in win._derived and "d1" not in win._raw_variables
    assert plot.series_names() == ["a"]
    assert not win.variable_panel.findItems("d1", Qt.MatchFlag.MatchExactly)
    assert not win.variable_panel.is_derived("d1")


def test_delete_is_refused_while_referenced(win):
    win._on_add_derived_variable("d1", "a")
    win._on_add_derived_variable("d2", "d1 * 2")
    with pytest.raises(ExpressionError, match="d2"):
        win.delete_derived_variable("d1")
    assert "d1" in win._derived
    win.delete_derived_variable("d2")
    win.delete_derived_variable("d1")
    assert win._derived == {}


def test_panel_marks_derived_variables_and_keeps_marks_across_time_axis_switch(win):
    win._on_add_derived_variable("d1", "a * 2")
    panel = win.variable_panel
    assert panel.is_derived("d1") and not panel.is_derived("a")
    win.set_time_column("b")
    assert panel.is_derived("d1")
    item = next(panel.item(i) for i in range(panel.count()) if panel.item(i).text() == "d1")
    assert item.font().italic() and item.toolTip() == "d1 = a * 2"


def test_view_saves_edited_variables_in_dependency_order_and_reloads(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("v", True)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    win._on_add_derived_variable("d1", "a")
    win._on_add_derived_variable("d2", "b * 2")
    win.edit_derived_variable("d1", "d2 + 1")  # d1 now depends on the later-defined d2
    win._add_variable_to_plot(win.plot_grid.plots[0], "d1")
    win.save_view_dialog()

    assert [d.name for d in view_io.load_view("v").derived_variables] == ["d2", "d1"]

    fresh = MainWindow()
    fresh.load_view("v")
    np.testing.assert_allclose(fresh.variable_values("d1"), fresh.variable_values("b") * 2 + 1)
    assert fresh.plot_grid.plots[0].series_names() == ["d1"]
    fresh.close()


def test_load_view_tolerates_a_file_saved_out_of_dependency_order(win, sample_parquet_path):
    view_io.save_view(
        view_io.View(
            view_name="old",
            source=view_io.SourceDef(parquet_path=str(sample_parquet_path), path_type="absolute"),
            plots=[],
            derived_variables=[
                view_io.DerivedVariableDef(name="d1", expression="d2 + 1"),
                view_io.DerivedVariableDef(name="d2", expression="a"),
            ],
        )
    )
    win.load_view("old")
    assert set(win._derived) == {"d1", "d2"}


# -- editing in the expression bar (18.3) ----------------------------------------------


def test_edit_request_puts_expression_bar_in_edit_mode(win):
    win._on_add_derived_variable("d1", "a * 2")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    assert bar.editing_name == "d1"
    assert bar.name_edit.text() == "d1" and bar.name_edit.isReadOnly()
    assert bar.expr_edit.text() == "a * 2"
    assert not bar.cancel_button.isHidden()


def test_variable_names_can_be_inserted_while_editing(win, qapp):
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDropEvent

    win._on_add_derived_variable("d1", "a")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    bar.expr_edit.setText("a + ")
    bar.expr_edit.end(False)

    win.variable_panel.variableDoubleClicked.emit("b")  # double-click insertion
    assert bar.expr_edit.text() == "a + b"

    items = win.variable_panel.findItems("c", Qt.MatchFlag.MatchExactly)
    mime = win.variable_panel.mimeData(items)  # exactly what a drag from the panel carries
    assert mime.text() == "c"
    bar.expr_edit.setText("a + b * ")
    pos = QPointF(bar.expr_edit.cursorRect().center())
    event = QDropEvent(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bar.expr_edit.dropEvent(event)
    assert bar.expr_edit.text() == "a + b * c"
    assert bar.editing_name == "d1"  # still editing


def test_update_button_applies_edit_and_returns_to_add_mode(win):
    win._on_add_derived_variable("d1", "a * 2")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    bar.expr_edit.setText("a * 5")
    bar.add_button.click()
    np.testing.assert_allclose(win.variable_values("d1"), win.variable_values("a") * 5)
    assert bar.editing_name is None and not bar.name_edit.isReadOnly()
    assert bar.name_edit.text() == "" and bar.cancel_button.isHidden()


def test_update_error_keeps_edit_mode_and_text(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    win._on_add_derived_variable("d1", "a")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    bar.expr_edit.setText("d1 + 1")  # circular
    bar.add_button.click()
    assert warnings and bar.editing_name == "d1" and bar.expr_edit.text() == "d1 + 1"
    assert win._derived["d1"].expression == "a"


def test_cancel_returns_to_add_mode_without_changes(win):
    win._on_add_derived_variable("d1", "a")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    bar.expr_edit.setText("b")
    bar.cancel_button.click()
    assert bar.editing_name is None and bar.expr_edit.text() == "" and not bar.name_edit.isReadOnly()
    assert win._derived["d1"].expression == "a"


def test_edit_mode_is_cancelled_when_the_variable_goes_away(win, sample_parquet_path):
    win._on_add_derived_variable("d1", "a")
    win.variable_panel.editDerivedRequested.emit("d1")
    win.delete_derived_variable("d1")
    assert win.expression_bar.editing_name is None

    win._on_add_derived_variable("d2", "a")
    win.variable_panel.editDerivedRequested.emit("d2")
    win.load_parquet(str(sample_parquet_path))
    assert win.expression_bar.editing_name is None
