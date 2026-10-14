"""Property tests for the expression evaluator (app 46.2, A1; core/expression.py).

(1) Whatever string goes in, only ExpressionError may come out (and a value is an ndarray or a
scalar). (2) A grammatically correct expression built from parts evaluates to what the same tree
computed directly with numpy gives (expressions.ja.md 6.1/43)."""
from __future__ import annotations

import os

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from parquet_analyzer.core.expression import ExpressionError, evaluate_expression


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


# --- (1) arbitrary strings -------------------------------------------------------------------

_TOKENS = [
    "a", "b", "c", "x", "0", "1", "2.5", "1e308", "1e999", "0.5", "3", "+", "-", "*", "/", "**", "%", "//",
    "(", ")", ",", "abs", "sqrt", "sin", "exp", "log", "min", "max", "clip", "where", "isnan", "delta",
    "rolling_mean", "rolling_nanmean", "if", "else", "and", "or", "not", "<", "<=", ">", ">=", "==", "!=",
    " ", "  ", "[", "]", ".", "'", "lambda", "=", "_", "__import__",
]
_VARS = {
    "a": np.array([1.0, -2.0, 0.0, np.nan, np.inf]),
    "b": np.array([0.5, 4.0, -1.0, 2.0, -np.inf]),
    "c": np.array([3.0, 3.0, 3.0, 3.0, 3.0]),
}


def _check_outcome(expr: str) -> None:
    try:
        out = evaluate_expression(expr, _VARS)
    except ExpressionError:
        return
    assert isinstance(out, (np.ndarray, float, np.floating)), (expr, type(out))
    if isinstance(out, np.ndarray):
        assert out.dtype.kind == "f"


@pytest.mark.filterwarnings("ignore::SyntaxWarning")  # ast.parse warns on e.g. '1a'
@settings(max_examples=_examples(300))
@given(st.text(max_size=60))
def test_random_text_only_raises_expression_error(expr):
    _check_outcome(expr)


@pytest.mark.filterwarnings("ignore::SyntaxWarning")
@settings(max_examples=_examples(300))
@given(st.lists(st.sampled_from(_TOKENS), max_size=30), st.sampled_from(["", " "]))
def test_random_token_soup_only_raises_expression_error(tokens, sep):
    _check_outcome(sep.join(tokens))


# --- (2) well-formed expressions vs. numpy -------------------------------------------------------

_NUMS = [0.0, 1.0, 2.0, 3.0, 0.5, 0.1, 10.0, 100.0]
_CMPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal,
         "==": np.equal, "!=": np.not_equal}
_FUNC1 = {"abs": np.abs, "sqrt": np.sqrt, "sin": np.sin, "exp": np.exp, "log": np.log}


def _tf(x):
    """(truth, unknown) of a condition value: nonzero = true, 0 = false, NaN = unknown (43.1)."""
    x = np.asarray(x, dtype=np.float64)
    unknown = np.isnan(x)
    return (x != 0) & ~unknown, unknown


def _flag(truth, unknown):
    return np.where(unknown, np.nan, np.where(truth, 1.0, 0.0))


# A tree is (source_text, reference_function(variables) -> float64 array / 0-d), built bottom-up.
def _leaf_var(name):
    return (name, lambda v, n=name: v[n])


def _leaf_num(x):
    return (repr(float(x)), lambda v, x=x: np.float64(x))


def _combine(children):
    def binop(sym, f):
        return st.tuples(children, children).map(
            lambda t: (f"({t[0][0]} {sym} {t[1][0]})", lambda v, a=t[0][1], b=t[1][1]: f(a(v), b(v))))

    def call1(name, f):
        return children.map(lambda t: (f"{name}({t[0]})", lambda v, a=t[1]: f(a(v))))

    def call2(name, f):
        return st.tuples(children, children).map(
            lambda t: (f"{name}({t[0][0]}, {t[1][0]})", lambda v, a=t[0][1], b=t[1][1]: f(a(v), b(v))))

    def power(t):
        (src, ref), e = t
        return (f"({src} ** {e!r})", lambda v: np.power(ref(v), e))

    def cmp_(t):
        (sa, ra), op, (sb, rb) = t

        def ref(v):
            x, y = np.asarray(ra(v), dtype=float), np.asarray(rb(v), dtype=float)
            return _flag(_CMPS[op](x, y), np.isnan(x) | np.isnan(y))
        return (f"({sa} {op} {sb})", ref)

    def cond(t, spelled):
        (sc, rc), (sa, ra), (sb, rb) = t

        def ref(v):
            tr, un = _tf(rc(v))
            return np.where(un, np.nan, np.where(tr, np.asarray(ra(v), float), np.asarray(rb(v), float)))
        return (spelled(sc, sa, sb), ref)

    def logic(t):
        (sa, ra), op, (sb, rb) = t

        def ref(v):
            ta, ua = _tf(ra(v))
            tb, ub = _tf(rb(v))
            if op == "and":
                decided = (~ta & ~ua) | (~tb & ~ub)  # some operand is definitely false
                return _flag(~decided, (ua | ub) & ~decided)
            decided = ta | tb
            return _flag(decided, (ua | ub) & ~decided)
        return (f"({sa} {op} {sb})", ref)

    def not_(t):
        src, ref = t

        def r(v):
            tr, un = _tf(ref(v))
            return _flag(~tr, un)
        return (f"(not {src})", r)

    def clip_(t):
        (sa, ra), (sl, rl), (sh, rh) = t
        return (f"clip({sa}, {sl}, {sh})", lambda v: np.clip(ra(v), rl(v), rh(v)))

    return st.one_of(
        binop("+", np.add), binop("-", np.subtract), binop("*", np.multiply), binop("/", np.true_divide),
        children.map(lambda t: (f"(-{t[0]})", lambda v, a=t[1]: np.negative(a(v)))),
        st.tuples(children, st.sampled_from([0.0, 1.0, 2.0, 3.0, 0.5, -1.0])).map(power),
        st.sampled_from(sorted(_FUNC1)).flatmap(lambda n: call1(n, _FUNC1[n])),
        call2("min", np.minimum), call2("max", np.maximum),
        children.map(lambda t: (f"isnan({t[0]})", lambda v, a=t[1]: np.isnan(np.asarray(a(v), float)).astype(float))),
        st.tuples(children, children, children).map(clip_),
        st.tuples(children, st.sampled_from(sorted(_CMPS)), children).map(cmp_),
        st.tuples(children, children, children).map(
            lambda t: cond(t, lambda c, a, b: f"where({c}, {a}, {b})")),
        st.tuples(children, children, children).map(
            lambda t: cond(t, lambda c, a, b: f"({a} if {c} else {b})")),
        st.tuples(children, st.sampled_from(["and", "or"]), children).map(logic),
        children.map(not_),
    )


_LEAVES = st.one_of(
    st.sampled_from(["a", "b", "c"]).map(_leaf_var),
    st.sampled_from(_NUMS).map(_leaf_num),
)
_TREES = st.recursive(_LEAVES, _combine, max_leaves=12)  # roughly depth <= 5

_SPECIAL = [np.nan, np.inf, -np.inf, 0.0, -0.0, 1.0, -1.0, 2.5, -3.0, 1e300, -1e300, 1e-300]


@st.composite
def _case(draw):
    n = draw(st.integers(1, 50))
    element = st.one_of(st.sampled_from(_SPECIAL), st.floats(-1e3, 1e3))
    var = {k: np.array(draw(st.lists(element, min_size=n, max_size=n)), dtype=np.float64) for k in "abc"}
    return draw(_TREES), var


@settings(max_examples=_examples(200))
@given(_case())
def test_well_formed_expression_matches_numpy(case):
    (src, ref), var = case
    with np.errstate(all="ignore"):
        expected = np.asarray(ref(var), dtype=np.float64)
    got = evaluate_expression(src, var)
    assert isinstance(got, (np.ndarray, float, np.floating)), src
    got = np.asarray(got, dtype=np.float64)
    np.testing.assert_allclose(np.broadcast_to(got, expected.shape) if got.ndim == 0 else got,
                               expected, rtol=1e-12, equal_nan=True, err_msg=src)
