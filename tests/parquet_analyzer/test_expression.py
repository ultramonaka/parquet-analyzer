from __future__ import annotations

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, evaluate_expression


def test_arithmetic():
    variables = {"a": np.array([1.0, 2.0, 3.0]), "b": np.array([4.0, 5.0, 6.0])}
    np.testing.assert_array_equal(evaluate_expression("a + b", variables), [5.0, 7.0, 9.0])
    np.testing.assert_array_equal(evaluate_expression("b - a", variables), [3.0, 3.0, 3.0])
    np.testing.assert_array_equal(evaluate_expression("a * b", variables), [4.0, 10.0, 18.0])
    np.testing.assert_array_equal(evaluate_expression("b / a", variables), [4.0, 2.5, 2.0])


def test_abs_sqrt_delta():
    variables = {"a": np.array([-4.0, 9.0, 16.0])}
    np.testing.assert_array_equal(evaluate_expression("abs(a)", variables), [4.0, 9.0, 16.0])
    np.testing.assert_allclose(evaluate_expression("sqrt(abs(a))", variables), [2.0, 3.0, 4.0])


def test_delta_function():
    variables = {"a": np.array([1.0, 3.0, 6.0])}
    np.testing.assert_array_equal(evaluate_expression("delta(a)", variables), [0.0, 2.0, 3.0])


def test_unknown_variable_raises():
    with pytest.raises(ExpressionError):
        evaluate_expression("unknown_var + 1", {"a": np.array([1.0])})


def test_disallowed_call_raises():
    with pytest.raises(ExpressionError):
        evaluate_expression("__import__('os')", {})


def test_syntax_error_raises():
    with pytest.raises(ExpressionError):
        evaluate_expression("a +* b", {"a": np.array([1.0]), "b": np.array([1.0])})


def test_power_mod_floordiv_operators():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([2.0, 2.0, 2.0, 2.0])
    np.testing.assert_array_equal(evaluate_expression("a ** 2", {"a": a}), a**2)
    np.testing.assert_array_equal(evaluate_expression("a % b", {"a": a, "b": b}), a % b)
    np.testing.assert_array_equal(evaluate_expression("a // b", {"a": a, "b": b}), a // b)


def test_trig_exp_log_functions():
    a = np.array([1.0, 2.0, 3.0])
    variables = {"a": a}
    np.testing.assert_allclose(evaluate_expression("sin(a)", variables), np.sin(a))
    np.testing.assert_allclose(evaluate_expression("cos(a)", variables), np.cos(a))
    np.testing.assert_allclose(evaluate_expression("tan(a)", variables), np.tan(a))
    np.testing.assert_allclose(evaluate_expression("exp(a)", variables), np.exp(a))
    np.testing.assert_allclose(evaluate_expression("log(a)", variables), np.log(a))
    np.testing.assert_allclose(evaluate_expression("log10(a)", variables), np.log10(a))


def test_sign_floor_ceil_round():
    a = np.array([-2.7, 0.0, 2.3])
    variables = {"a": a}
    np.testing.assert_array_equal(evaluate_expression("sign(a)", variables), np.sign(a))
    np.testing.assert_array_equal(evaluate_expression("floor(a)", variables), np.floor(a))
    np.testing.assert_array_equal(evaluate_expression("ceil(a)", variables), np.ceil(a))
    np.testing.assert_array_equal(evaluate_expression("round(a)", variables), np.round(a))


def test_elementwise_min_max_clip():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    variables = {"a": a}
    np.testing.assert_array_equal(evaluate_expression("max(a, 2)", variables), np.maximum(a, 2))
    np.testing.assert_array_equal(evaluate_expression("min(a, 2)", variables), np.minimum(a, 2))
    np.testing.assert_array_equal(
        evaluate_expression("clip(a, 1.5, 3.5)", variables), np.clip(a, 1.5, 3.5)
    )


def test_rolling_mean_smooths_a_spike_and_keeps_length():
    y = np.array([1.0, 2.0, 100.0, 2.0, 1.0])  # a single spike in the middle
    result = evaluate_expression("rolling_mean(a, 3)", {"a": y})
    assert len(result) == len(y)
    assert result[2] < y[2]  # the spike is smoothed down, not passed through raw
    np.testing.assert_allclose(result, [1.5, 34 + 1 / 3, 34 + 2 / 3, 34 + 1 / 3, 1.5])


def test_new_functions_still_enforce_arity():
    """Regression test: the arity check (added for abs/sqrt after the ufunc out=
    incident) must also cover every newly added function, not just the original two.
    """
    a = np.array([1.0, 2.0, 3.0])
    with pytest.raises(ExpressionError):
        evaluate_expression("max(a)", {"a": a})
    with pytest.raises(ExpressionError):
        evaluate_expression("clip(a, 1)", {"a": a})
    with pytest.raises(ExpressionError):
        evaluate_expression("sin(a, a)", {"a": a})


def test_wrong_arity_raises_instead_of_corrupting_data():
    """Regression test: abs/sqrt are numpy ufuncs whose 2nd positional argument is the
    `out=` buffer. Without an arity check, `sqrt(a, b)` would silently overwrite `b`'s
    array in place (numpy writes the result into `out`) instead of raising a clear error.
    """
    a = np.array([4.0, 9.0])
    b = np.array([1.0, 2.0])
    with pytest.raises(ExpressionError):
        evaluate_expression("sqrt(a, b)", {"a": a, "b": b})
    np.testing.assert_array_equal(b, [1.0, 2.0])  # untouched

    with pytest.raises(ExpressionError):
        evaluate_expression("abs(a, b)", {"a": a, "b": b})
    np.testing.assert_array_equal(b, [1.0, 2.0])  # still untouched

    with pytest.raises(ExpressionError):
        evaluate_expression("delta(a, b)", {"a": a, "b": b})

    with pytest.raises(ExpressionError):
        evaluate_expression("sqrt()", {"a": a})


def test_fullwidth_characters_from_japanese_ime_are_normalized():
    """Regression test: Japanese IME full-width input mode can turn "+" into "＋"
    (U+FF0B) etc. without the user noticing, which ast.parse rejects outright
    ("invalid character '＋' (U+FF0B)"). NFKC-normalize before parsing instead of
    surfacing that as a confusing error.
    """
    variables = {"a": np.array([1.0, 2.0, 3.0]), "b": np.array([4.0, 5.0, 6.0])}
    np.testing.assert_array_equal(evaluate_expression("a＋b", variables), [5.0, 7.0, 9.0])
    np.testing.assert_array_equal(evaluate_expression("sqrt（a）", {"a": np.array([4.0, 9.0])}), [2.0, 3.0])
    np.testing.assert_array_equal(evaluate_expression("a　+　b", variables), [5.0, 7.0, 9.0])
