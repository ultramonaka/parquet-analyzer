"""Conditionals: comparisons, and/or/not, `a if c else b`, where(), isnan()
(expressions.ja.md 5.34 / 43; test plan 43.4)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from parquet_analyzer.core.expression import (
    _ALLOWED_FUNCS,
    ExpressionError,
    _parse,
    check_allowed_syntax,
    evaluate_expression,
    expression_margin,
)
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction, validate_user_function
from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.io import user_functions as uf_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow

NAN = float("nan")
INF = float("inf")
A = np.array([-INF, -2.0, 0.0, 1.0, 5.0, INF, NAN])
B = np.array([1.0, -2.0, 0.0, 3.0, 5.0, INF, 1.0])
V = {"a": A, "b": B}
CMPS = [("<", np.less), ("<=", np.less_equal), (">", np.greater), (">=", np.greater_equal),
        ("==", np.equal), ("!=", np.not_equal)]


def ev(e, v=V, registry=None):
    return evaluate_expression(e, v, registry)


def _ref(op, x, y):
    """Expected comparison: 1/0, NaN where either side is NaN."""
    out = np.where(op(x, y), 1.0, 0.0)
    return np.where(np.isnan(x) | np.isnan(y), np.nan, out)


# -- values ---------------------------------------------------------------------------------


@pytest.mark.parametrize("sym,op", CMPS)
def test_comparison_array_array_with_nan_and_inf(sym, op):
    out = ev(f"a {sym} b")
    assert out.dtype == np.float64
    np.testing.assert_array_equal(out, _ref(op, A, B))


@pytest.mark.parametrize("sym,op", CMPS)
def test_comparison_array_scalar_and_scalar_scalar(sym, op):
    np.testing.assert_array_equal(ev(f"a {sym} 1"), _ref(op, A, 1.0))
    np.testing.assert_array_equal(ev(f"1 {sym} a"), _ref(op, 1.0, A))
    r = ev(f"2 {sym} 3")
    assert isinstance(r, float) and r == float(op(2.0, 3.0))


def test_inf_compares_as_a_number():
    np.testing.assert_array_equal(ev("a > 1")[[0, 5]], [0.0, 1.0])
    assert ev("1 / 0 > 1") == 1.0


def test_chained_comparison_is_and_of_pairs():
    got = ev("0 < a < 5")
    want = np.where((A > 0) & (A < 5), 1.0, 0.0)
    want[np.isnan(A)] = np.nan
    np.testing.assert_array_equal(got, want)
    np.testing.assert_array_equal(ev("a < b <= 5"), ev("(a < b) and (b <= 5)"))


def test_chained_comparison_middle_operand_evaluated_once(monkeypatch):
    calls = []

    def probe(x):
        calls.append(1)
        return np.abs(x)

    monkeypatch.setitem(_ALLOWED_FUNCS, "probe", (1, probe))
    ev("0 < probe(a) < 5")
    assert len(calls) == 1


def test_and_or_not_values_and_many_operands():
    x = np.array([0.0, 1.0, 2.0, 0.0])
    y = np.array([0.0, 0.0, 3.0, 1.0])
    z = np.array([1.0, 1.0, 0.0, 1.0])
    v = {"x": x, "y": y, "z": z}
    np.testing.assert_array_equal(ev("x and y", v), [0, 0, 1, 0])  # 1/0, not the operand
    np.testing.assert_array_equal(ev("x or y", v), [0, 1, 1, 1])
    np.testing.assert_array_equal(ev("not x", v), [1, 0, 0, 1])
    np.testing.assert_array_equal(ev("x or y or z", v), [1, 1, 1, 1])
    np.testing.assert_array_equal(ev("x and y and z", v), [0, 0, 0, 0])
    np.testing.assert_array_equal(ev("not (x > 0 or y > 0)", v), [1, 0, 0, 0])
    assert ev("1 and 2") == 1.0 and ev("0 or 0") == 0.0 and ev("not 0") == 1.0


def test_inf_is_truthy_as_a_condition():
    np.testing.assert_array_equal(ev("where(a, 1, 0)")[[0, 1, 2, 5]], [1, 1, 0, 1])


def test_ifexp_equals_where():
    for c, a_, b_ in [("a > 0", "a", "b"), ("b", "a * 2", "-1"), ("a < b", "1", "2"), ("a", "a", "a")]:
        np.testing.assert_array_equal(ev(f"{a_} if {c} else {b_}"), ev(f"where({c}, {a_}, {b_})"))


def test_where_mixed_scalar_and_array_and_all_scalar():
    np.testing.assert_array_equal(ev("where(b > 0, 7, a)")[[3, 4, 6]], [7, 7, 7])
    np.testing.assert_array_equal(ev("where(1, a, 0)"), A)
    np.testing.assert_array_equal(ev("where(0, 0, b)"), B)
    r = ev("where(1 > 0, 2, 3)")
    assert isinstance(r, float) and r == 2.0
    assert ev("where(1 > 2, 2, 3)") == 3.0


def test_both_branches_computed_without_warning(recwarn):
    out = ev("where(a > 0, log(a), 0)", {"a": np.array([-1.0, 0.0, 1.0, np.e])})
    np.testing.assert_allclose(out, [0.0, 0.0, 0.0, 1.0])
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]
    np.testing.assert_array_equal(ev("where(b != 0, 1 / b, 0)", {"b": np.array([0.0, 2.0])}), [0.0, 0.5])


def test_isnan():
    np.testing.assert_array_equal(ev("isnan(a)"), [0, 0, 0, 0, 0, 0, 1])  # +-inf is not NaN
    assert ev("isnan(1)") == 0.0 and isinstance(ev("isnan(1)"), float)
    np.testing.assert_array_equal(ev("where(isnan(a), 0, a)")[[2, 6]], [0.0, 0.0])


# -- missing values -----------------------------------------------------------------------------


def test_nan_propagates_through_compare_logic_and_condition():
    n = np.isnan(A)
    for e in ["a < 1", "a == a", "a != 1", "a > b", "0 < a < 5", "not a", "a and 1", "1 and a",
              "a or 0", "0 or a", "a > 0 and b > 0", "where(a, 1, 2)"]:
        out = ev(e)
        assert np.isnan(out[n]).all(), e  # NaN in -> NaN out (and/or: only when not decided by the other side, 43.5)
        assert not np.isnan(out[~n]).any(), e
    assert np.isnan(ev("a if a > 0 else 5")[6])


def test_branch_nan_passes_through_and_isnan_does_not_propagate():
    out = ev("where(b > 0, a, 9)")  # b=1 at the NaN position of a: the branch NaN stays
    assert np.isnan(out[6]) and out[3] == 1.0
    assert ev("where(b > 0, 1, a)")[6] == 1.0
    assert not np.isnan(ev("isnan(a)")).any() and ev("isnan(a)")[6] == 1.0


def test_result_is_always_float64():
    for e in ["a > 0", "not a", "a and b", "where(a > 0, 1, 2)", "a if b else 0", "isnan(a)", "(a > 0) * a",
              "where(a > 0, a, 0) + 1"]:
        assert ev(e).dtype == np.float64, e
    for e in ["1 < 2", "not 1", "isnan(3)", "where(1, 2, 3)"]:
        assert type(ev(e)) is float, e
    np.testing.assert_array_equal(ev("(b > 0) * b")[[3, 4]], [3.0, 5.0])


# -- errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "e",
    ["a is b", "a is not b", "a in b", "a not in b", "a < b is b", "True", "1 if True else 2",
     "where(a > 0, a)", "where(a, b, a, b)", "where()", "isnan()", "isnan(a, b)", "where(c=a, a=1, b=2)",
     "isnan(x=a)", "a < ", "a == [1]", "a < (1, 2)"],
)
def test_conditional_misuse_is_expression_error(e):
    with pytest.raises(ExpressionError):
        ev(e)


def test_length_mismatch_is_expression_error():
    v = {"a": np.arange(3.0), "b": np.arange(4.0)}
    for e in ["a < b", "a and b", "where(a, b, a)", "a if b else a", "0 < a < b"]:
        with pytest.raises(ExpressionError):
            ev(e, v)


def test_fullwidth_operators_are_normalized():
    np.testing.assert_array_equal(ev("ａ ＞ ０"), ev("a > 0"))
    np.testing.assert_array_equal(ev("ａ ＜＝ ｂ"), ev("a <= b"))


# -- shared whitelist / margin ------------------------------------------------------------------


@pytest.mark.parametrize("e", ["a < b", "0 < a < 1", "a and b or not a", "a if b else 1", "where(a, b, 1)",
                               "isnan(a)", "a == b != 1"])
def test_check_allowed_syntax_accepts(e):
    check_allowed_syntax(_parse(e))


@pytest.mark.parametrize("e", ["a is b", "a in b", "a not in b", "a is not b", "a < b is c"])
def test_check_allowed_syntax_rejects_is_and_in(e):
    with pytest.raises(ExpressionError):
        check_allowed_syntax(_parse(e))


@pytest.mark.parametrize("e", ["a < b", "a and b", "not a", "a if b else 1", "where(a, b, 1)", "isnan(a)",
                               "0 < a < 3 or isnan(b)"])
def test_margin_of_conditionals_is_elementwise(e):
    assert expression_margin(e, None, {"a": (0, 0), "b": (0, 0)}) == (0, 0)


def test_margin_combines_children():
    env = {"a": (0, 0), "b": (0, 0)}
    assert expression_margin("delta(a) > 0", None, env) == (1, 0)
    assert expression_margin("a if rolling_mean(b, 5) > 0 else delta(a)", None, env) == (4, 4)
    assert expression_margin("where(delta(a) > 0 and rolling_mean(b, 3) < 1, a, 0)", None, env) == (2, 2)
    assert expression_margin("a is b", None, env) is None  # unsupported compare op -> full computation


# -- user functions and maps ----------------------------------------------------------------------


def test_user_function_body_with_conditionals_saves_and_evaluates():
    bodies = {
        "clamp0": "where(p > 0, p, 0)",
        "pos": "p if p > 0 else 0",
        "inrange": "0 < p < 5",
        "either": "p > 1 or not p",
        "nanfix": "where(isnan(p), 0, p)",
    }
    reg = FunctionRegistry()
    for name, body in bodies.items():
        fn = UserFunction(name, ["p"], [], body)
        validate_user_function(fn, reg)
        reg.user_functions[name] = fn
    v = {"x": np.array([-1.0, 2.0, NAN, 7.0])}
    np.testing.assert_array_equal(ev("clamp0(x)", v, reg)[[0, 1, 3]], [0, 2, 7])
    assert np.isnan(ev("clamp0(x)", v, reg)[2])
    np.testing.assert_array_equal(ev("nanfix(x)", v, reg), [-1, 2, 0, 7])
    np.testing.assert_array_equal(ev("inrange(x)", v, reg)[[0, 1, 3]], [0, 1, 0])
    assert expression_margin("clamp0(a)", reg, {"a": (0, 0)}) == (0, 0)  # via the body


@pytest.mark.parametrize("name", ["where", "isnan"])
def test_function_or_map_named_like_new_builtin_cannot_be_created(name):
    with pytest.raises(ExpressionError):
        validate_user_function(UserFunction(name, ["p"], [], "p"), FunctionRegistry())
    with pytest.raises(maps_io.MapError):
        maps_io.validate_map_name(name)


def test_saved_user_function_named_where_is_skipped_with_warning_on_load(isolated_config):
    path = uf_io.user_functions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    good = {"name": "good", "params": ["v"], "constants": [], "expression": "v"}
    old = [{"name": n, "params": ["v"], "constants": [], "expression": "v + 1"} for n in ("where", "isnan")]
    path.write_text(json.dumps({"functions": [*old, good]}), encoding="utf-8")
    fns, warns, skipped = uf_io.load_user_functions_ex()
    assert [f.name for f in fns] == ["good"]
    assert len(warns) == 2 and len(skipped) == 2


def test_saved_map_named_where_is_skipped_with_warning_on_load(isolated_config, tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text("x,y\n0,0\n1,1\n", encoding="utf-8")
    maps_io.import_map(csv, "fine")
    # simulate a map saved before `where` became a builtin: copy the stored files under the new name
    d = maps_io.maps_dir()
    for p in list(d.glob("fine.*")):
        (d / p.name.replace("fine", "where", 1)).write_bytes(p.read_bytes())
    tables, warns = maps_io.load_all_maps()
    assert "fine" in tables and "where" not in tables
    assert any("where" in w for w in warns)


# -- MainWindow: plot, view round trip ---------------------------------------------------------


def test_expression_bar_where_plots_and_survives_view_round_trip(qapp, isolated_config, sample_parquet_path, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v", True)))
    for n in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win = MainWindow()
    fresh = None
    try:
        win.load_parquet(str(sample_parquet_path))
        win._on_add_derived_variable("pos", "where(a > 0, a, 0)")
        win._on_add_derived_variable("alt", "a if a > 0 else 0")
        a = win.variable_values("a")
        np.testing.assert_array_equal(win.variable_values("pos"), np.where(a > 0, a, 0.0))
        np.testing.assert_array_equal(win.variable_values("alt"), win.variable_values("pos"))
        win._add_variable_to_plot(win.plot_grid.plots[0], "pos")
        win.save_view_dialog()
        assert {d.name: d.expression for d in view_io.load_view("v").derived_variables}["pos"] == "where(a > 0, a, 0)"
        fresh = MainWindow()
        fresh.load_view("v")
        np.testing.assert_array_equal(fresh.variable_values("pos"), np.where(a > 0, a, 0.0))
        assert fresh.plot_grid.plots[0].series_names() == ["pos"]
    finally:
        win.close()
        if fresh is not None:
            fresh.close()


def test_function_reference_lists_where_and_isnan_in_conditional_category():
    from parquet_analyzer.ui import function_reference as fr

    assert fr.missing_builtin_docs() == []
    entries = fr.build_entries(FunctionRegistry())
    assert {e.name for e in entries if e.category == "conditional"} == {"where", "isnan"}
    ops = {e.name for e in entries if e.category == "operator"}
    assert {"< <= > >= == !=", "and / or / not", "a if c else b"} <= ops
