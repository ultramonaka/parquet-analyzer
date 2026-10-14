"""Review fixes of 2026-10-07 (expressions.ja.md 43.5): Kleene and/or, error types kept."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.core.expression import (
    ExpressionError,
    UnknownFunctionError,
    evaluate_expression,
)
from parquet_analyzer.ui import messages
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow

NAN = float("nan")
VALS = [1.0, 0.0, NAN]  # true, false, unknown


def _kleene(op, vs):
    if op == "or":
        if any(v == 1.0 for v in vs):
            return 1.0
        return NAN if any(np.isnan(v) for v in vs) else 0.0
    if any(v == 0.0 for v in vs):
        return 0.0
    return NAN if any(np.isnan(v) for v in vs) else 1.0


@pytest.mark.parametrize("op", ["and", "or"])
@pytest.mark.parametrize("n", [2, 3])
def test_kleene_truth_table_arrays(op, n):
    combos = list(itertools.product(VALS, repeat=n))
    cols = {name: np.array([c[i] for c in combos]) for i, name in enumerate("xyz"[:n])}
    out = evaluate_expression(f" {op} ".join(cols), cols)
    want = np.array([_kleene(op, c) for c in combos])
    np.testing.assert_array_equal(out, want)


def test_kleene_scalars_and_mixed():
    a = {"a": np.array([NAN])}
    assert evaluate_expression("1 or a", a)[0] == 1.0
    assert evaluate_expression("0 and a", a)[0] == 0.0
    assert np.isnan(evaluate_expression("0 or a", a)[0])
    assert np.isnan(evaluate_expression("1 and a", a)[0])
    assert evaluate_expression("1 or 0", {}) == 1.0 and evaluate_expression("1 and 0", {}) == 0.0
    assert np.isnan(evaluate_expression("not a", a)[0])  # unchanged


def test_where_isnan_or_replaces_nan_rows():
    x = np.array([1.0, NAN, -2.0, 3.0])
    out = evaluate_expression("where(isnan(x) or x < 0, 0, x)", {"x": x})
    np.testing.assert_array_equal(out, [1.0, 0.0, 0.0, 3.0])


@pytest.mark.parametrize("expr", ["1 < foo(x)", "x > 0 and foo(x)", "x > 0 or foo(x)", "not foo(x)", "foo(x) < 1"])
def test_unknown_function_keeps_its_type(expr):
    with pytest.raises(UnknownFunctionError):
        evaluate_expression(expr, {"x": np.arange(3.0)})


def test_no_comparison_prefix_on_inner_error():
    with pytest.raises(ExpressionError) as ei:
        evaluate_expression("x < (1 < y)", {"x": np.arange(3.0)})
    assert "comparison:" not in str(ei.value) and "unknown variable" in str(ei.value)
    with pytest.raises(ExpressionError) as ei:
        evaluate_expression("x and y", {"x": np.arange(3.0)})
    assert not str(ei.value).startswith("and:")


def test_length_mismatch_still_reported():
    with pytest.raises(ExpressionError, match="comparison"):
        evaluate_expression("x < y", {"x": np.arange(3.0), "y": np.arange(4.0)})
    with pytest.raises(ExpressionError, match="or"):
        evaluate_expression("x or y", {"x": np.arange(3.0), "y": np.arange(4.0)})


def test_expression_bar_shows_translated_unknown_function(qapp, isolated_config, sample_parquet_path, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    monkeypatch.setattr(messages, "warning", lambda parent, title, text, *a, **k: seen.append(text))
    w = MainWindow()
    try:
        w.load_parquet(str(sample_parquet_path))
        w.expression_bar.name_edit.setText("bad")
        w.expression_bar.expr_edit.setText("1 < foo(sine)")
        w.expression_bar._on_add_clicked()
        assert seen == [tr("error.unknown_function", name="foo")]
    finally:
        w.close()
