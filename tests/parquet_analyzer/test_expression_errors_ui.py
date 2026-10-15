"""MainWindow behaviour for abnormal expressions (expressions.ja.md 6.1 / 6.2).

An expression that used to leak a raw exception (here `rolling_mean(a, b)`: array window ->
TypeError) must (1) show a warning from the expression bar and change nothing, and (2) only
skip its own derived variable (and dependents) during load_view instead of failing the whole view.
"""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox
from view_load_helper import wait_view_loaded

from parquet_analyzer.core.expression import ExpressionError
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui.main_window import MainWindow

# each of these used to raise a raw non-ExpressionError out of _on_add_derived_variable
LEAKY = [
    "rolling_mean(a, b)",  # TypeError (array window)
    "rolling_nanmean(a, 0)",  # ValueError
    "-" * 5000 + "a",  # RecursionError
    "a + " + "1" * 400,  # OverflowError-ish int constant
    "a + True",  # bool accepted as a number
]


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text, *a, **k: seen.append(text))
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    w.seen = seen
    yield w
    w.close()


def _save(derived, series):
    view_io.save_view(
        View(
            view_name="v",
            source=SourceDef("/data/f.parquet", "absolute"),
            plots=[PlotDef("p1", [SeriesDef(n, "#ff0000") for n in series])],
            derived_variables=derived,
        )
    )


@pytest.mark.parametrize("expr", LEAKY)
def test_add_derived_variable_raises_only_expression_error(win, expr):
    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("bad", expr)
    assert "bad" not in win._derived and "bad" not in win._raw_variables


@pytest.mark.parametrize("expr", LEAKY)
def test_edit_derived_variable_raises_only_expression_error_and_keeps_state(win, expr):
    win._on_add_derived_variable("d1", "a")
    win._on_add_derived_variable("d2", "d1 + 1")
    with pytest.raises(ExpressionError):
        win.edit_derived_variable("d1", expr)
    assert win._derived["d1"].expression == "a"
    np.testing.assert_allclose(win.variable_values("d2"), win.variable_values("a") + 1)


@pytest.mark.parametrize("expr", LEAKY)
def test_expression_bar_warns_and_changes_nothing(win, expr):
    bar = win.expression_bar
    n_series = len(win.plot_grid.plots[0].series_names())
    n_plots = len(win.plot_grid.plots)
    bar.name_edit.setText("bad")
    bar.expr_edit.setText(expr)
    bar.add_button.click()
    assert len(win.seen) == 1  # the warning
    assert "bad" not in win._derived and "bad" not in win._raw_variables
    assert not win.variable_panel.is_derived("bad")
    assert len(win.plot_grid.plots[0].series_names()) == n_series and len(win.plot_grid.plots) == n_plots
    assert bar.expr_edit.text() == expr  # the user's text stays for correction


def test_expression_bar_edit_with_bad_expression_keeps_edit_mode(win):
    win._on_add_derived_variable("d1", "a")
    win.variable_panel.editDerivedRequested.emit("d1")
    bar = win.expression_bar
    bar.expr_edit.setText("rolling_mean(a, b)")
    bar.add_button.click()
    assert len(win.seen) == 1
    assert bar.editing_name == "d1" and win._derived["d1"].expression == "a"


def test_expression_bar_valid_previously_broken_input_works(win):
    # `2 ** -1` used to be a raw ValueError; now a valid constant series
    bar = win.expression_bar
    bar.name_edit.setText("half")
    bar.expr_edit.setText("a * 2 ** -1")
    bar.add_button.click()
    assert win.seen == []
    np.testing.assert_allclose(win.variable_values("half"), win.variable_values("a") * 0.5)


def test_scalar_result_from_delta_of_constant_broadcasts_to_a_series(win):
    win._on_add_derived_variable("z", "delta(1)")
    v = win.variable_values("z")
    assert v.shape == win.variable_values("a").shape and np.all(v == 0)


def test_view_with_a_bad_expression_skips_only_that_chain(win):
    _save(
        [
            DerivedVariableDef("bad", "rolling_mean(a, b)"),
            DerivedVariableDef("dep", "bad + 1"),
            DerivedVariableDef("ok", "a * 2"),
            DerivedVariableDef("deep", "-" * 5000 + "a"),
            DerivedVariableDef("big", "a + " + "1" * 400),
            DerivedVariableDef("flag", "a + True"),
        ],
        ["a", "bad", "dep", "ok", "deep", "big", "flag"],
    )
    win.load_view("v")  # used to abort with a raw TypeError at the first bad variable
    wait_view_loaded(win)

    assert set(win._derived) == {"ok"}
    assert win.plot_grid.plots[0].series_names() == ["a", "ok"]
    np.testing.assert_allclose(win.variable_values("ok"), win.variable_values("a") * 2)
    # one consolidated warning that lists every skipped item
    assert len(win.seen) == 1
    for n in ("bad", "dep", "deep", "big", "flag"):
        assert f"- {n}" in win.seen[0]
    assert "- ok" not in win.seen[0]


def test_view_load_order_does_not_matter_for_the_surviving_variables(win):
    _save(
        [
            DerivedVariableDef("ok2", "ok1 + 1"),
            DerivedVariableDef("bad", "rolling_mean(a, b)"),
            DerivedVariableDef("ok1", "a * 2"),
        ],
        ["ok2", "ok1"],
    )
    win.load_view("v")
    wait_view_loaded(win)
    assert set(win._derived) == {"ok1", "ok2"}
    assert win.plot_grid.plots[0].series_names() == ["ok2", "ok1"]
    np.testing.assert_allclose(win.variable_values("ok2"), win.variable_values("a") * 2 + 1)
