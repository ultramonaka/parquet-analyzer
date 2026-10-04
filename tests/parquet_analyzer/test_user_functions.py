"""User functions: registry, evaluation, validation, persistence
(detailed_specification.md 26.1-26.3 / 26.6)."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, evaluate_expression, referenced_functions
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction, validate_user_function
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import user_functions as uf_io


def power() -> UserFunction:
    return UserFunction(
        "Power_flow_by_press", ["flow_lpm", "press_kpa"], [("k", 60000)], "flow_lpm * press_kpa / k", "desc"
    )


@pytest.fixture
def reg() -> FunctionRegistry:
    return FunctionRegistry({"Power_flow_by_press": power()})


A = np.array([1.0, 2.0, 3.0])
B = np.array([60000.0, 120000.0, 30000.0])
VARS = {"a": A, "b": B}


def ev(expr, reg=None, v=VARS):
    return evaluate_expression(expr, v, reg)


# ---------------------------------------------------------------- evaluation


def test_none_registry_is_unchanged():
    np.testing.assert_allclose(ev("abs(a) + max(a, 2)"), [3, 4, 6])
    with pytest.raises(ExpressionError, match="unknown function"):
        ev("nope(a)")
    with pytest.raises(ExpressionError, match="keyword"):
        ev("abs(x=a)")


def test_default_constant(reg):
    np.testing.assert_allclose(ev("Power_flow_by_press(a, b)", reg), A * B / 60000)


def test_override_constant_variants(reg):
    np.testing.assert_allclose(ev("Power_flow_by_press(a, b, k=1000)", reg), A * B / 1000)
    np.testing.assert_allclose(ev("Power_flow_by_press(a, b, k=-1.5)", reg), A * B / -1.5)
    np.testing.assert_allclose(ev("Power_flow_by_press(a, b, k=2*3)", reg), A * B / 6)


@pytest.mark.parametrize(
    "expr",
    [
        "Power_flow_by_press(a, b, k=a)",  # array constant
        "Power_flow_by_press(a, b, z=1)",  # unknown keyword
        "Power_flow_by_press(a, b, k=1, k=2)",  # duplicate
        "Power_flow_by_press(a)",  # too few
        "Power_flow_by_press(a, b, 5)",  # constant passed positionally
        "Power_flow_by_press(a, b, k=1/0.0)",  # non-finite
        "Power_flow_by_press(a, b, **{})",
    ],
)
def test_call_errors(reg, expr):
    with pytest.raises(ExpressionError):
        ev(expr, reg)


def test_body_calls_builtin_and_other_user_function():
    inner = UserFunction("twice", ["v"], [], "v * 2")
    outer = UserFunction("outer", ["v"], [("c", 1.0)], "abs(twice(v)) + c")
    reg = FunctionRegistry({"twice": inner, "outer": outer})
    np.testing.assert_allclose(ev("outer(a - 5, c=10)", reg), np.abs((A - 5) * 2) + 10)


def test_builtin_keyword_still_error(reg):
    with pytest.raises(ExpressionError, match="keyword"):
        ev("abs(x=a)", reg)


def test_unknown_function_message(reg):
    with pytest.raises(ExpressionError, match="unknown function: nope"):
        ev("nope(a)", reg)


def test_body_cannot_see_caller_variables():
    reg = FunctionRegistry({"f": UserFunction("f", ["v"], [], "v + a")})  # unvalidated on purpose
    with pytest.raises(ExpressionError, match="unknown variable"):
        ev("f(b)", reg)


def test_depth_cap():
    # Deliberately cyclic (bypasses validation, as a broken file would).
    f = UserFunction("f", ["v"], [], "f(v)")
    with pytest.raises(ExpressionError, match="depth"):
        ev("f(a)", FunctionRegistry({"f": f}))


def test_depth_chain_within_cap_ok():
    fns = {"f0": UserFunction("f0", ["v"], [], "v + 1")}
    for i in range(1, 20):
        fns[f"f{i}"] = UserFunction(f"f{i}", ["v"], [], f"f{i - 1}(v)")
    np.testing.assert_allclose(ev("f19(a)", FunctionRegistry(fns)), A + 1)


def test_referenced_functions():
    assert referenced_functions("abs(a) + foo(b, k=2) * bar(baz(a))") == {"foo", "bar", "baz"}


def test_map_call_from_expression_and_body():
    t = LookupTable("curve_k", "curve", [0, 10], [0, 100])
    reg = FunctionRegistry({"scaled": UserFunction("scaled", ["v"], [], "curve_k(v) * 2")}, {"curve_k": t})
    np.testing.assert_allclose(ev("curve_k(a)", reg), [10, 20, 30])
    np.testing.assert_allclose(ev("scaled(a)", reg), [20, 40, 60])
    with pytest.raises(ExpressionError, match="expects 1"):
        ev("curve_k(a, b)", reg)
    with pytest.raises(ExpressionError, match="keyword"):
        ev("curve_k(x=a)", reg)


# ---------------------------------------------------------------- validation


def check(fn, reg=None, **kw):
    validate_user_function(fn, reg or FunctionRegistry(), **kw)


def test_valid_function_passes(reg):
    check(power(), reg, replaces="Power_flow_by_press")  # editing itself is fine
    with pytest.raises(ExpressionError, match="in use"):
        check(power(), reg)  # as a new function it clashes


@pytest.mark.parametrize(
    "fn",
    [
        UserFunction("1bad", ["a"], [], "a"),
        UserFunction("has space", ["a"], [], "a"),
        UserFunction("class", ["a"], [], "a"),
        UserFunction("f", [], [], "1"),  # no params
        UserFunction("f", ["a b"], [], "1"),
        UserFunction("f", ["a"], [("a", 1.0)], "a"),  # param/const dup
        UserFunction("f", ["a"], [("k", float("nan"))], "a"),
        UserFunction("f", ["a"], [("k", float("inf"))], "a"),
        UserFunction("f", ["a"], [], "a +"),  # syntax
        UserFunction("abs", ["a"], [], "a"),  # builtin clash
        UserFunction("f", ["a"], [], "column_x + a"),  # file column
        UserFunction("f", ["a"], [], "missing(a)"),  # unknown function
        UserFunction("f", ["a"], [], "f(a)"),  # self recursion
    ],
)
def test_invalid_functions(fn):
    with pytest.raises(ExpressionError):
        check(fn)


def test_name_clash_with_map_and_user_function(reg):
    t = LookupTable("m", "curve", [0, 1], [0, 1])
    reg.maps["m"] = t
    with pytest.raises(ExpressionError):
        check(UserFunction("m", ["a"], [], "a"), reg)
    with pytest.raises(ExpressionError):
        check(UserFunction("Power_flow_by_press", ["a"], [], "a"), reg)


def test_indirect_cycle_rejected():
    a = UserFunction("a_fn", ["v"], [], "b_fn(v)")
    b = UserFunction("b_fn", ["v"], [], "v")
    reg = FunctionRegistry({"a_fn": a, "b_fn": b})
    check(a, reg, replaces="a_fn")
    with pytest.raises(ExpressionError, match="circular"):
        check(UserFunction("b_fn", ["v"], [], "a_fn(v)"), reg, replaces="b_fn")


def test_rename_edit_old_name_gone():
    f = UserFunction("old", ["v"], [], "v")
    reg = FunctionRegistry({"old": f})
    check(UserFunction("new", ["v"], [], "v"), reg, replaces="old")
    with pytest.raises(ExpressionError, match="unknown function"):
        check(UserFunction("new", ["v"], [], "old(v)"), reg, replaces="old")


def test_map_call_in_body_validates():
    t = LookupTable("m", "curve", [0, 1], [0, 1])
    check(UserFunction("f", ["v"], [], "m(v)"), FunctionRegistry(maps={"m": t}))


def test_registry_kind_and_order(reg):
    assert reg.kind("abs") == "builtin"
    assert reg.kind("Power_flow_by_press") == "user"
    assert reg.kind("zzz") is None
    assert reg.resolve("zzz") is None
    reg.maps["m"] = LookupTable("m", "curve", [0, 1], [0, 1])
    assert reg.kind("m") == "map"
    assert isinstance(reg.resolve("Power_flow_by_press"), UserFunction)


# ---------------------------------------------------------------- persistence


def test_missing_file_is_empty(isolated_config):
    assert uf_io.load_user_functions() == ([], [])


def test_round_trip(isolated_config):
    fns = [power(), UserFunction("g", ["v"], [], "Power_flow_by_press(v, v, k=3)", "")]
    uf_io.save_user_functions(fns)
    loaded, warns = uf_io.load_user_functions()
    assert warns == []
    assert loaded == fns
    data = json.loads(uf_io.user_functions_path().read_text(encoding="utf-8"))
    assert data["schema_version"] == "1"
    assert data["functions"][0]["constants"] == [{"name": "k", "default": 60000}]


def test_save_uses_temp_and_replace(isolated_config, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append((str(a), str(b))), real(a, b))[1])
    uf_io.save_user_functions([power()])
    assert len(calls) == 1
    src, dst = calls[0]
    assert src != dst and dst.endswith("user_functions.json")
    assert not os.path.exists(src)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"functions": 3}', ""])
def test_corrupt_file_is_backed_up(isolated_config, content):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    fns, warns = uf_io.load_user_functions()
    assert fns == [] and len(warns) == 1 and ".bad-" in warns[0]
    assert not path.exists()
    backups = list(path.parent.glob("user_functions.json.bad-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content


def test_one_bad_function_skipped_others_load(isolated_config):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True)
    good = {"name": "good", "params": ["v"], "constants": [], "expression": "v + 1", "description": ""}
    bad_ref = {"name": "bad", "params": ["v"], "constants": [], "expression": "v + column", "description": ""}
    malformed = {"name": "oops"}
    caller = {"name": "caller", "params": ["v"], "constants": [], "expression": "bad(v)"}
    later = {"name": "uses_later", "params": ["v"], "constants": [], "expression": "good(v)"}
    path.write_text(json.dumps({"schema_version": "1", "functions": [later, bad_ref, malformed, caller, good]}))
    fns, warns = uf_io.load_user_functions()
    assert sorted(f.name for f in fns) == ["good", "uses_later"]
    assert len(warns) == 3
    assert any("bad" in w for w in warns)
    assert path.exists()  # no backup for per-function problems


def test_load_with_maps(isolated_config):
    t = LookupTable("m", "curve", [0, 1], [0, 1])
    uf_io.save_user_functions([UserFunction("f", ["v"], [], "m(v)")])
    assert uf_io.load_user_functions()[0] == []  # unknown without maps
    fns, warns = uf_io.load_user_functions({"m": t})
    assert [f.name for f in fns] == ["f"] and warns == []
