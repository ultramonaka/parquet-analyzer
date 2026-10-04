"""Abnormal input to user functions (expressions.ja.md 6.1 / 6.2).

Every failure must be an ExpressionError (not a raw ValueError / TypeError / RecursionError),
because load_view and the managers only catch that.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, UnknownFunctionError, evaluate_expression
from parquet_analyzer.core.functions import (
    FunctionRegistry,
    UserFunction,
    check_expression_calls,
    validate_user_function,
)
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import user_functions as uf_io

A = np.array([1.0, 2.0, 3.0])
VARS = {"a": A, "b": np.array([4.0, 5.0, 6.0])}


def scale() -> UserFunction:
    return UserFunction("scale", ["p"], [("k", 2.0)], "p * k")


@pytest.fixture
def reg():
    return FunctionRegistry({"scale": scale()})


def ev(expr, reg, v=VARS):
    return evaluate_expression(expr, v, reg)


def check(fn, reg=None, replaces=None):
    validate_user_function(fn, reg or FunctionRegistry(), replaces=replaces)


# -- constant override at the call site -------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "scale(a, k=True)",
        "scale(a, k=False)",
        "scale(a, k=1e999)",  # inf
        "scale(a, k=1 / 0)",  # inf
        "scale(a, k=0 / 0)",  # nan
        "scale(a, k=a)",  # array
        "scale(a, k=nan)",  # not a variable
        "scale(a, k=1 + a)",
        "scale(a, k=" + "9" * 400 + ")",
        "scale(a, k=-1e999)",
        "scale(a, k=1, k=2)",
        "scale(a, z=1)",
        "scale(a, **b)",
        "scale(a, b)",
        "scale()",
        "scale(a, a)",
        "scale(k=2)",
    ],
)
def test_bad_constant_overrides_and_calls_are_expression_errors(call, reg):
    with pytest.raises(ExpressionError):
        ev(call, reg)


def test_override_with_computed_finite_scalar_is_fine(reg):
    np.testing.assert_allclose(ev("scale(a, k=1 + 2)", reg), A * 3)
    np.testing.assert_allclose(ev("scale(a, k=2 ** -1)", reg), A * 0.5)  # float constants (6.1)


def test_scalar_and_empty_arguments(reg):
    assert float(ev("scale(3)", reg)) == 6.0
    assert ev("scale(e)", reg, {"e": np.array([])}).shape == (0,)


# -- body evaluation errors surface as ExpressionError ----------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["rolling_mean(p, 1.5)", "rolling_mean(p, p)", "rolling_nanmean(p, 0)", "zzz + p", "p +", "p < 1", "True + p"],
)
def test_bad_bodies_fail_as_expression_error_at_evaluation(body):
    # bypass validation: a hand-edited file or an older version can hold such a body
    fn = UserFunction("f", ["p"], [], body)
    with pytest.raises(ExpressionError):
        ev("f(a)", FunctionRegistry({"f": fn}))


def test_delta_of_scalar_inside_body():
    fn = UserFunction("f", ["p"], [], "delta(p) + 1")
    r = ev("f(5)", FunctionRegistry({"f": fn}))
    assert float(r) == 1.0


def test_body_cannot_see_caller_variables_even_with_same_name_as_param():
    fn = UserFunction("f", ["p"], [], "p + a")  # `a` exists at the call site only
    with pytest.raises(ExpressionError, match="unknown variable: a"):
        ev("f(b)", FunctionRegistry({"f": fn}))


def test_caller_scope_is_untouched_after_call(reg):
    v = {"a": A.copy(), "p": np.array([9.0, 9.0, 9.0])}
    ev("scale(a, k=5)", reg, v)
    np.testing.assert_array_equal(v["p"], [9.0, 9.0, 9.0])
    assert set(v) == {"a", "p"}


def test_hand_edited_cycle_hits_the_depth_cap_not_recursion_error():
    f = UserFunction("f", ["p"], [], "g(p)")
    g = UserFunction("g", ["p"], [], "f(p)")
    with pytest.raises(ExpressionError, match="depth"):
        ev("f(a)", FunctionRegistry({"f": f, "g": g}))
    selfcall = UserFunction("s", ["p"], [], "s(p) + 1")
    with pytest.raises(ExpressionError, match="depth"):
        ev("s(a)", FunctionRegistry({"s": selfcall}))


def test_hand_edited_body_nested_far_too_deep():
    fn = UserFunction("f", ["p"], [], "-" * 5000 + "p")
    with pytest.raises(ExpressionError):
        ev("f(a)", FunctionRegistry({"f": fn}))
    fn2 = UserFunction("f", ["p"], [], "(" * 5000 + "p" + ")" * 5000)
    with pytest.raises(ExpressionError):
        ev("f(a)", FunctionRegistry({"f": fn2}))


def test_map_error_in_body_is_expression_error():
    class Bad:
        arity = 1

        def __call__(self, x):
            raise ValueError("bad table")

    fn = UserFunction("f", ["p"], [], "m(p)")
    reg = FunctionRegistry({"f": fn}, {"m": Bad()})
    with pytest.raises(ExpressionError, match="m"):
        ev("f(a)", reg)


# -- validate_user_function ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_default", [True, False, float("inf"), float("-inf"), float("nan"), 10**400, "1", None, [1], 1j])
def test_constant_defaults_must_be_finite_numbers(bad_default):
    with pytest.raises(ExpressionError):
        check(UserFunction("f", ["p"], [("k", bad_default)], "p * k"))


def test_numpy_scalar_default_is_accepted_only_if_a_real_number():
    # np.float64 is a float subclass: finite one passes, nan does not
    check(UserFunction("f", ["p"], [("k", np.float64(2.0))], "p * k"))
    with pytest.raises(ExpressionError):
        check(UserFunction("f", ["p"], [("k", np.float64("nan"))], "p * k"))


@pytest.mark.parametrize(
    "fn",
    [
        UserFunction("f", ["p", "p"], [], "p"),  # duplicate parameter
        UserFunction("f", ["p"], [("p", 1.0)], "p"),  # constant shadows parameter
        UserFunction("f", ["p"], [("k", 1.0), ("k", 2.0)], "p * k"),  # duplicate constant
        UserFunction("f", [], [], "1"),  # no parameter
        UserFunction("f", ["p"], [], ""),  # empty body
        UserFunction("f", ["p"], [], "   "),
        UserFunction("f", ["p"], [], "p +"),  # syntax error
        UserFunction("f", ["p"], [], "p; p"),
        UserFunction("f", ["p"], [], "q"),  # unknown name
        UserFunction("f", ["p"], [], "f(p)"),  # self call
        UserFunction("f", ["p"], [], "nope(p)"),  # unknown function
        UserFunction("f", ["p"], [], "abs(p, p)"),  # arity
        UserFunction("f", ["p"], [], "abs(x=p)"),  # keyword on builtin
        UserFunction("f", ["p"], [], "(abs)(p)(1)"),  # non-name call target
        UserFunction("f", ["p"], [], "-" * 100000 + "p"),
    ],
)
def test_invalid_definitions_raise_expression_error(fn):
    with pytest.raises(ExpressionError):
        check(fn)


@pytest.mark.parametrize(
    "body", ["p < 1", "p.real", "p[0]", "p if p else 1", "[p]", "True", "p + True", "p and 1", "'s'", "p @ p", "p & 1", "~p", "(p, p)", "abs(*p)"]
)
def test_disallowed_syntax_in_body_is_rejected_at_save_time(body):
    with pytest.raises(ExpressionError):
        check(UserFunction("f", ["p"], [], body))


@pytest.mark.parametrize("body", ["p + 1", "-p ** 2 // 3 % 4", "abs(p) + 1.5e3", "scale(p, k=2)", "(p)"])
def test_allowed_syntax_in_body_still_validates(body):
    check(UserFunction("f", ["p"], [], body), FunctionRegistry({"scale": scale()}))


def test_loader_skips_previously_saved_unevaluable_bodies_one_by_one(isolated_config):
    entries = [
        {"name": n, "params": ["v"], "constants": [], "expression": body}
        for n, body in [("cmp", "v < 1"), ("attr", "v.real"), ("sub", "v[0]"), ("tern", "v if v else 1"), ("lst", "[v]")]
    ] + [GOOD]
    _write(isolated_config, entries)
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [f.name for f in fns] == ["good"]
    assert len(warns) == 5 and len(skipped) == 5  # kept verbatim for the next save


# -- check_expression_calls (what the view manager uses to predict load_view) ------------------------


@pytest.mark.parametrize(
    "expr,exc",
    [
        ("nope(a)", UnknownFunctionError),
        ("abs(a, b)", ExpressionError),
        ("abs()", ExpressionError),
        ("abs(x=a)", ExpressionError),
        ("scale(a, z=1)", ExpressionError),
        ("scale(a, k=1, k=2)", ExpressionError),
        ("scale(a, **b)", ExpressionError),
        ("scale(a, a)", ExpressionError),
        ("scale()", ExpressionError),
        ("abs(a)(b)", ExpressionError),
        ("a +", ExpressionError),
        ("", ExpressionError),
        ("mm(a)", ExpressionError),  # map arity: 2
        ("mm(a, b, k=1)", ExpressionError),
    ],
)
def test_check_expression_calls_errors(expr, exc, reg):
    reg.maps["mm"] = LookupTable("mm", "map", [0, 1], [0, 1], [[0, 1], [1, 2]])
    with pytest.raises(exc):
        check_expression_calls(expr, reg)


def test_check_expression_calls_accepts_valid_calls(reg):
    reg.maps["mm"] = LookupTable("mm", "map", [0, 1], [0, 1], [[0, 1], [1, 2]])
    check_expression_calls("scale(a, k=3) + mm(a, b) + abs(a)", reg)
    check_expression_calls("a + 1", reg)  # no calls at all


# -- persistence: wrong-typed fields in a hand-edited file ---------------------------------------------


def _write(isolated_config, entries):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"functions": entries}), encoding="utf-8")


GOOD = {"name": "good", "params": ["v"], "constants": [], "expression": "v"}


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "x", "params": "v", "expression": "v"},  # params not a list
        {"name": "x", "params": ["v"], "constants": {"k": 1}, "expression": "v"},  # constants a dict
        {"name": "x", "params": ["v"], "constants": "k", "expression": "v"},
        {"name": "x", "params": ["v"], "constants": ["k"], "expression": "v"},  # entry a string
        {"name": "x", "params": ["v"], "constants": [[ "k", 1]], "expression": "v"},  # entry a list
        {"name": "x", "params": ["v"], "constants": [{"name": "k"}], "expression": "v"},  # no default
        {"name": "x", "params": ["v"], "constants": [{"default": 1}], "expression": "v"},  # no name
        {"name": "x", "params": ["v"], "constants": [{"name": "k", "default": "1"}], "expression": "v"},
        {"name": "x", "params": ["v"], "constants": [{"name": "k", "default": True}], "expression": "v"},
        {"name": "x", "params": ["v"], "constants": [{"name": "k", "default": None}], "expression": "v"},
        {"name": "x", "params": ["v"], "constants": [{"name": "k", "default": 10**400}], "expression": "v"},
        {"name": "x", "params": ["v"], "constants": [{"name": 5, "default": 1}], "expression": "v"},  # numeric const name
        {"name": 5, "params": ["v"], "constants": [], "expression": "v"},  # numeric name
        {"name": None, "params": ["v"], "expression": "v"},
        {"name": ["x"], "params": ["v"], "expression": "v"},  # unhashable name
        {"name": "x", "params": [5], "expression": "v"},  # numeric parameter
        {"name": "x", "params": [["v"]], "expression": "v"},  # unhashable parameter
        {"name": "x", "params": ["v"], "expression": 5},
        {"name": "x", "params": ["v"], "expression": None},
        {"name": "x", "params": ["v"], "expression": "v", "description": 5},
        {"name": "x", "params": ["v"]},  # no expression
        {"params": ["v"], "expression": "v"},  # no name
        {},
        [],
        "x",
        5,
        None,
    ],
)
def test_loader_skips_wrong_typed_entries_and_keeps_the_rest(isolated_config, bad):
    _write(isolated_config, [bad, GOOD])
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [f.name for f in fns] == ["good"]
    assert len(warns) == 1
    assert skipped == [bad]  # kept verbatim for the next save


def test_loader_functions_key_of_wrong_type_is_quarantined(isolated_config):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"functions": {"a": 1}}), encoding="utf-8")
    fns, warns = uf_io.load_user_functions()
    assert fns == [] and len(warns) == 1 and not path.exists()


def test_loader_huge_and_deep_values_do_not_abort(isolated_config):
    huge = {"name": "huge", "params": ["v"], "constants": [{"name": "k", "default": 10**400}], "expression": "v"}
    deep = {"name": "deep", "params": ["v"], "constants": [], "expression": "(" * 100000 + "v" + ")" * 100000}
    long_name = {"name": "n" * 100000, "params": ["v"], "constants": [], "expression": "v"}
    many = {"name": "many", "params": [f"p{i}" for i in range(2000)], "constants": [], "expression": "p0"}
    _write(isolated_config, [huge, deep, long_name, many, GOOD])
    fns, warns = uf_io.load_user_functions()
    names = [f.name for f in fns]
    assert "good" in names and "huge" not in names and "deep" not in names
    assert len(warns) == 4 - (len(names) - 1)  # every entry that did not load has one warning


def test_loader_mutually_recursive_functions_are_both_skipped(isolated_config):
    f = {"name": "f", "params": ["v"], "constants": [], "expression": "g(v)"}
    g = {"name": "g", "params": ["v"], "constants": [], "expression": "f(v)"}
    _write(isolated_config, [f, g, GOOD])
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [x.name for x in fns] == ["good"]
    assert len(skipped) == 2 and len(warns) == 2


def test_loader_duplicate_names_keep_the_first(isolated_config):
    first = dict(GOOD, expression="v + 1")
    second = dict(GOOD, expression="v + 2")
    _write(isolated_config, [first, second])
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [f.expression for f in fns] == ["v + 1"] and skipped == [second] and len(warns) == 1
