"""Abnormal input to expressions (expressions.ja.md 6.1 / 6.2).

Principle under test: every failure while parsing/evaluating leaves as ExpressionError
(or UnknownFunctionError) — callers such as MainWindow.load_view only catch that, so any other
exception would abort a whole view load. `pytest.raises(ExpressionError)` is used on purpose:
a raw ValueError/TypeError/RecursionError must not pass.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from parquet_analyzer.core import expression as expr_mod
from parquet_analyzer.core.expression import (
    ExpressionError,
    UnknownFunctionError,
    evaluate_expression,
    referenced_functions,
    referenced_names,
)
from parquet_analyzer.core.functions import FunctionRegistry


@pytest.fixture
def v():
    return {
        "a": np.array([1.0, 2.0, 3.0, 4.0, 5.0]),
        "b": np.array([1.0, 2.0, 0.0, 4.0, 5.0]),
        "n": np.array([1.0, np.nan, 3.0, np.inf, -np.inf]),
        "e": np.array([]),
    }


def ev(expr, v, registry=None):
    return evaluate_expression(expr, v, registry)


# -- the table in 6.1: each row used to leak a raw exception or a silently wrong value -----------


def test_negative_integer_power_is_float(v):
    assert ev("2 ** -1", v) == 0.5  # used to be a raw ValueError (numpy int power)
    np.testing.assert_allclose(ev("a ** -1", v), 1.0 / v["a"])


@pytest.mark.parametrize("expr", ["2 ** 10000", "10 ** 400", "-(10 ** 400)"])
def test_integer_overflow_gives_inf_not_silent_zero(expr, v):
    r = ev(expr, v)  # used to be a silent 0 (int64 wrap)
    assert np.isinf(r)


def test_overflowing_power_sign(v):
    assert ev("-(2 ** 10000)", v) == -np.inf


def test_integer_constant_too_large_for_float_is_expression_error(v):
    with pytest.raises(ExpressionError):
        ev("1" + "0" * 400, v)
    with pytest.raises(ExpressionError):
        ev("a + " + "9" * 400, v)


@pytest.mark.parametrize("expr", ["True", "False", "a + False", "-True", "max(a, True)"])
def test_bool_constants_are_rejected(expr, v):
    with pytest.raises(ExpressionError):
        ev(expr, v)


@pytest.mark.parametrize("depth", [5000, 50000])
def test_deep_unary_nesting_is_expression_error(depth, v):
    with pytest.raises(ExpressionError, match="too complex"):
        ev("-" * depth + "a", v)  # used to be a raw RecursionError


def test_deep_binary_chain_is_expression_error(v):
    with pytest.raises(ExpressionError):
        ev("+".join(["a"] * 20000), v)


def test_deep_nesting_inside_user_function_body(v):
    from parquet_analyzer.core.functions import UserFunction

    # a hand-edited file can carry a body that parses (maybe) but is far too deep
    fn = UserFunction("deep", ["p"], [], "-" * 5000 + "p")
    reg = FunctionRegistry({"deep": fn})
    with pytest.raises(ExpressionError):
        ev("deep(a)", v, reg)


@pytest.mark.parametrize("func", ["rolling_mean", "rolling_nanmean", "trailing_mean", "trailing_nanmean"])
class TestRollingWindow:
    def test_array_window(self, func, v):
        with pytest.raises(ExpressionError, match="scalar"):
            ev(f"{func}(a, b)", v)  # used to be a raw TypeError

    @pytest.mark.parametrize("w", ["1.5", "2.0000001", "0.5"])
    def test_fractional_window(self, func, w, v):
        with pytest.raises(ExpressionError):
            ev(f"{func}(a, {w})", v)  # 1.5 used to be silently truncated to 1

    @pytest.mark.parametrize("w", ["0", "-1", "-3.0", "0.0"])
    def test_non_positive_window(self, func, w, v):
        with pytest.raises(ExpressionError):
            ev(f"{func}(a, {w})", v)  # used to be a raw ValueError

    @pytest.mark.parametrize("w", ["1e999", "n", "a / 0"])
    def test_non_finite_window(self, func, w, v):
        with pytest.raises(ExpressionError):
            ev(f"{func}(a, {w})", v)

    def test_integer_valued_float_window_is_fine(self, func, v):
        np.testing.assert_allclose(ev(f"{func}(a, 3.0)", v), ev(f"{func}(a, 3)", v))

    def test_computed_scalar_window(self, func, v):
        np.testing.assert_allclose(ev(f"{func}(a, 1 + 2)", v), ev(f"{func}(a, 3)", v))

    def test_window_longer_than_data_is_clipped_not_an_error(self, func, v):
        r = ev(f"{func}(a, 100)", v)
        np.testing.assert_allclose(r, ev(f"{func}(a, 5)", v))

    def test_empty_input(self, func, v):
        assert ev(f"{func}(e, 3)", v).shape == (0,)


def test_delta_of_scalar_is_zero(v):
    assert ev("delta(1)", v) == 0  # used to be a raw TypeError
    assert np.ndim(ev("delta(2 * 3)", v)) == 0
    np.testing.assert_array_equal(ev("a + delta(5)", v), v["a"])  # broadcast by the caller


def test_delta_of_array_unchanged(v):
    np.testing.assert_allclose(ev("delta(a)", v), [0, 1, 1, 1, 1])
    assert ev("delta(e)", v).shape == (0,)


def test_builtin_type_or_value_error_is_wrapped_naming_the_function(monkeypatch, v):
    def boom(x):
        raise ValueError("kaboom")

    def boom_t(x):
        raise TypeError("kaboom_t")

    monkeypatch.setitem(expr_mod._ALLOWED_FUNCS, "explode", (1, boom))
    monkeypatch.setitem(expr_mod._ALLOWED_FUNCS, "explode_t", (1, boom_t))
    with pytest.raises(ExpressionError, match="explode"):
        ev("explode(a)", v)
    with pytest.raises(ExpressionError, match="explode_t"):
        ev("explode_t(a)", v)
    # the original exception stays attached for the log
    try:
        ev("explode(a)", v)
    except ExpressionError as e:
        assert isinstance(e.__cause__, ValueError)


def test_expression_error_from_inside_a_builtin_is_not_double_wrapped(v):
    with pytest.raises(ExpressionError) as ei:
        ev("rolling_mean(a, 0)", v)
    assert str(ei.value).count("rolling_mean()") == 1


def test_map_type_error_is_wrapped(monkeypatch, v):
    class Bad:
        arity = 1

        def __call__(self, x):
            raise TypeError("bad map")

    reg = FunctionRegistry(maps={"badmap": Bad()})
    with pytest.raises(ExpressionError, match="badmap"):
        ev("badmap(a)", v, reg)


def test_evaluation_emits_no_runtime_warnings(v):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for e in ["a / 0", "0 / b", "log(-a)", "log10(0)", "sqrt(-a)", "a % 0", "a // 0", "exp(1000 * a)",
                  "a ** 1e308", "1 / 0", "0 / 0", "tan(n)", "n - n", "n * 0"]:
            ev(e, v)


# -- deliberately NOT errors (numpy meaning), pinned --------------------------------------------


def test_division_by_zero_and_domain_errors_give_inf_nan(v):
    assert np.all(np.isposinf(ev("a / 0", v)))
    r = ev("0 / b", v)
    assert np.isnan(r[2]) and r[0] == 0
    assert np.isinf(ev("1 / b", v)[2])
    assert np.all(np.isnan(ev("log(-a)", v)))
    assert np.all(np.isnan(ev("sqrt(-a)", v)))
    assert np.all(np.isnan(ev("a % 0", v)))
    assert np.all(np.isneginf(ev("log(0 * a)", v)))
    assert np.isnan(ev("0 / 0", v)) and np.isinf(ev("1 / 0", v))


def test_clip_with_inverted_bounds_is_numpy_semantics(v):
    np.testing.assert_array_equal(ev("clip(a, 3, 1)", v), np.ones(5))


def test_nan_and_inf_propagate(v):
    r = ev("n + 1", v)
    assert np.isnan(r[1]) and np.isposinf(r[3]) and np.isneginf(r[4])
    assert np.isnan(ev("n - n", v)[3])  # inf - inf
    assert ev("abs(n)", v)[4] == np.inf


def test_zero_length_variables(v):
    assert ev("e + 1", v).shape == (0,)
    assert ev("-e * 2", v).shape == (0,)
    assert ev("sqrt(e) + max(e, 1)", v).shape == (0,)


def test_scalar_only_expressions(v):
    assert ev("1 + 2 * 3", v) == 7
    assert ev("abs(-3)", v) == 3
    assert np.ndim(ev("sqrt(16) + min(1, 2)", v)) == 0


# -- operator precedence / arithmetic semantics --------------------------------------------------


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("-2 ** 2", -4.0),
        ("2 ** 3 ** 2", 512.0),
        ("(-2) ** 2", 4.0),
        ("7 // 2", 3.0),
        ("-7 // 2", -4.0),
        ("-7 % 3", 2.0),
        ("7 % -3", -2.0),
        ("2 * 3 + 4", 10.0),
        ("2 + 3 * 4", 14.0),
        ("10 - 2 - 3", 5.0),
        ("100 / 10 / 5", 2.0),
        ("--3", 3.0),
        ("+-3", -3.0),
        ("2 ** -2", 0.25),
        ("1e3", 1000.0),
        ("0.5 + .5", 1.0),
    ],
)
def test_precedence_and_arithmetic(expr, expected, v):
    assert ev(expr, v) == expected


def test_fullwidth_operators_parentheses_and_spaces_mixed(v):
    r = ev("（ａ　＋　ｂ）＊２－ａ／２", v)
    np.testing.assert_allclose(r, (v["a"] + v["b"]) * 2 - v["a"] / 2)
    np.testing.assert_allclose(ev("ａ ＊＊ ２", v), v["a"] ** 2)
    np.testing.assert_allclose(ev("abs（ａ）＋max（ａ，　３）", v), np.abs(v["a"]) + np.maximum(v["a"], 3))


# -- disallowed syntax: every one an ExpressionError --------------------------------------------

DISALLOWED = [
    # comparisons / and / or / not / conditional expressions are allowed since 43 (see
    # test_expression_conditionals.py); `is` / `in` / True stay rejected
    "a is b", "a in b", "a is not b", "a not in b", "a < b is c", "1 if True else 2",
    "a[0]", "a[1:3]", "a['x']",
    "a.real", "a.sum()", "(1).real",
    "[1, 2]", "(1, 2)", "{1: 2}", "{1, 2}", "[x for x in a]",
    "lambda x: x", "(lambda: 1)()",
    "'text'", "b'x'", "'a' + 'b'",
    "None", "1j", "2 + 3j",
    "a @ b", "a & b", "a | b", "a ^ b", "a << 1", "a >> 1", "~a",
    'f"{a}"', "...", "Ellipsis",
    "x = 1", "a; b", "a\nb", "*a", "**a", "(yield)", "await a", "a := 1",
    "", "   ", "\t",
    "a +", "(a", "a)", "a b", "1 2", "+", "()",
    "abs", "abs()", "abs(a, b)", "abs(*a)", "abs(**a)", "abs(x=a)",
    "a(1)", "abs(a)(1)", "a.abs(1)", "__import__('os')",
]


@pytest.mark.parametrize("expr", DISALLOWED)
def test_disallowed_syntax_is_expression_error(expr, v):
    with pytest.raises(ExpressionError):
        ev(expr, v)


def test_unknown_function_is_unknown_function_error(v):
    with pytest.raises(UnknownFunctionError) as ei:
        ev("__import__('os')", v)
    assert ei.value.name == "__import__"
    with pytest.raises(UnknownFunctionError):
        ev("eval('1')", v)
    with pytest.raises(UnknownFunctionError):
        ev("a(1)", v)  # a variable is not a function


def test_unknown_variable_is_expression_error_not_key_error(v):
    with pytest.raises(ExpressionError, match="zzz"):
        ev("zzz + 1", v)
    with pytest.raises(ExpressionError):
        ev("abs", v)


@pytest.mark.parametrize("junk", ["\x00", "a\x00", "$", "a ?", "a ` b", "１２３ ａｂ", "@", "​"])
def test_odd_characters_are_expression_error(junk, v):
    with pytest.raises(ExpressionError):
        ev(junk, v)


def test_null_byte_in_source_is_expression_error(v):
    # ast.parse raises ValueError (not SyntaxError) for source containing a null byte
    with pytest.raises(ExpressionError):
        ev("a +\x001", v)


def test_huge_expression_text_is_expression_error_or_value(v):
    big = "(" * 100000 + "a" + ")" * 100000
    with pytest.raises(ExpressionError):
        ev(big, v)


# -- referenced_names / referenced_functions on bad input ------------------------------------------


# explicit ids: the 100000-char id would overflow the 32767-char env var pytest sets on Windows
@pytest.mark.parametrize("fn", [referenced_names, referenced_functions])
@pytest.mark.parametrize("bad", ["a +", "", "(", "a b", "\x00", "(" * 100000], ids=["plus", "empty", "paren", "two-names", "nul", "deep-parens"])
def test_reference_helpers_raise_expression_error_on_syntax_errors(fn, bad):
    with pytest.raises(ExpressionError):
        fn(bad)


def test_reference_helpers_on_odd_but_parseable_input():
    # parseable but not evaluable: they only report names, evaluation rejects it
    assert referenced_names("a < b") == {"a", "b"}
    referenced_names("abs(a)(b)")  # must not raise
    assert referenced_functions("1 + 2") == set()
    assert referenced_functions("abs(a) + foo(b)") == {"foo"}
    assert referenced_names("sin(a) + f(a, b)") == {"a", "b"}
