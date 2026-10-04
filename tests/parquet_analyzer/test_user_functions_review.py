"""Review follow-ups for user functions (detailed_specification.md 26.1-26.3):
static call checks, depth cap, hostile hand-edited files, read-error handling."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError, UnknownFunctionError, evaluate_expression
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction, validate_user_function
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.io import user_functions as uf_io

A = np.array([1.0, 2.0, 3.0])


def check(fn, reg=None, **kw):
    validate_user_function(fn, reg or FunctionRegistry(), **kw)


def test_unknown_function_error_has_name():
    with pytest.raises(UnknownFunctionError) as ei:
        evaluate_expression("nope(a)", {"a": A}, FunctionRegistry())
    assert ei.value.name == "nope"
    with pytest.raises(UnknownFunctionError) as ei:
        evaluate_expression("nope(a)", {"a": A})
    assert ei.value.name == "nope"
    with pytest.raises(UnknownFunctionError) as ei:
        check(UserFunction("f", ["v"], [], "ghost(v)"))
    assert ei.value.name == "ghost"


@pytest.mark.parametrize(
    "body",
    [
        "abs(v, v)",
        "abs(x=v)",
        "m(v, v)",
        "m(x=v)",
        "g(v)",
        "g(v, v, z=1)",
        "g(v, v, k=1, k=2)",
        "g(v, v, 1)",
    ],
)
def test_static_call_checks(body):
    g = UserFunction("g", ["a", "b"], [("k", 1.0)], "a + b + k")
    m = LookupTable("m", "curve", [0, 1], [0, 1])
    with pytest.raises(ExpressionError):
        check(UserFunction("f", ["v"], [], body), FunctionRegistry({"g": g}, {"m": m}))


def test_static_call_ok():
    g = UserFunction("g", ["a", "b"], [("k", 1.0)], "a + b + k")
    check(UserFunction("f", ["v"], [], "g(v, abs(v), k=2)"), FunctionRegistry({"g": g}))


def chain(n):
    fns = {"f0": UserFunction("f0", ["v"], [], "v")}
    for i in range(1, n):
        fns[f"f{i}"] = UserFunction(f"f{i}", ["v"], [], f"f{i - 1}(v)")
    return fns


def test_chain_depth_cap_matches_evaluation():
    fns = chain(32)
    reg = FunctionRegistry(fns)
    check(fns["f31"], reg, replaces="f31")
    np.testing.assert_allclose(evaluate_expression("f31(a)", {"a": A}, reg), A)  # 32 nested calls evaluate
    with pytest.raises(ExpressionError, match="deeper"):
        check(UserFunction("top", ["v"], [], "f31(v)"), reg)


def test_huge_default_does_not_crash():
    with pytest.raises(ExpressionError):
        check(UserFunction("f", ["v"], [("k", 10**400)], "v"))


def test_loader_survives_hostile_entries(isolated_config):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True)
    good = {"name": "good", "params": ["v"], "constants": [], "expression": "v"}
    huge = {"name": "huge", "params": ["v"], "constants": [{"name": "k", "default": 10**400}], "expression": "v"}
    deep = {"name": "deep", "params": ["v"], "constants": [], "expression": "-" * 100_000 + "v"}
    path.write_text(json.dumps({"functions": [huge, deep, good]}))
    fns, warns = uf_io.load_user_functions()
    assert [f.name for f in fns] == ["good"]
    assert len(warns) == 2


def test_corrupt_notice_in_warnings(isolated_config):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True)
    path.write_text("{oops")
    fns, warns = uf_io.load_user_functions()
    assert fns == [] and len(warns) == 1 and ".bad-" in warns[0]


def test_read_error_does_not_rename_and_signals(isolated_config, monkeypatch):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"functions": []}')
    real = Path.read_text

    def boom(self, *a, **k):
        if self.name == "user_functions.json":
            raise PermissionError("denied")
        return real(self, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", boom)
        with pytest.raises(uf_io.UserFunctionsUnreadable):
            uf_io.load_user_functions()
    assert path.exists() and not list(path.parent.glob("*.bad-*"))
