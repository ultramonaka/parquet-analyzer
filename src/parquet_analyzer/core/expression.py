from __future__ import annotations

import ast
import unicodedata
from typing import TYPE_CHECKING, Mapping

import numpy as np

from .analysis import delta, rolling_mean, rolling_nanmean

if TYPE_CHECKING:
    from .functions import FunctionRegistry

_ALLOWED_BINOPS = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.true_divide,
    ast.Pow: np.power,
    ast.Mod: np.mod,
    ast.FloorDiv: np.floor_divide,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: np.positive,
    ast.USub: np.negative,
}

def _delta_expr(y):
    """delta() for expressions: a scalar means "the same value at every time", so its
    difference is 0 (expressions.ja.md 6.1). core.analysis.delta stays array-only."""
    if np.ndim(y) == 0:
        return 0.0
    return delta(y)


def _window_arg(name: str, window) -> int:
    """Validate a rolling_* window (expressions.ja.md 6.1): a finite scalar, a positive
    integer (3.0 is fine, 1.5 is not). Checked here so core.analysis keeps its own contract."""
    if np.ndim(window) != 0:
        raise ExpressionError(f"{name}(): window must be a scalar, not an array")
    w = float(window)
    if not np.isfinite(w) or w != int(w):
        raise ExpressionError(f"{name}(): window must be a finite integer, got {w:g}")
    if w < 1:
        raise ExpressionError(f"{name}(): window must be a positive integer, got {w:g}")
    return int(w)


def _rolling_mean_expr(y, window):
    return rolling_mean(y, _window_arg("rolling_mean", window))


def _rolling_nanmean_expr(y, window):
    return rolling_nanmean(y, _window_arg("rolling_nanmean", window))


# name -> (expected positional arg count, implementation).
# The arity is enforced *before* calling, not just the name whitelisted: abs/sqrt are
# numpy ufuncs whose 2nd positional argument is the `out=` buffer, so an unchecked
# `sqrt(a, b)` would silently overwrite b's array in place instead of raising.
#
# min/max/clip are *elementwise* (numpy semantics), not Python's reduction min/max —
# `max(a, 0)` clamps every sample of `a` to be at least 0, it does not return a single
# scalar. That's deliberate: a derived variable must stay the same length as the time
# axis to be plottable.
_ALLOWED_FUNCS: dict[str, tuple[int, object]] = {
    "abs": (1, np.abs),
    "sqrt": (1, np.sqrt),
    "delta": (1, _delta_expr),
    "sin": (1, np.sin),
    "cos": (1, np.cos),
    "tan": (1, np.tan),
    "exp": (1, np.exp),
    "log": (1, np.log),
    "log10": (1, np.log10),
    "sign": (1, np.sign),
    "floor": (1, np.floor),
    "ceil": (1, np.ceil),
    "round": (1, np.round),
    "min": (2, np.minimum),
    "max": (2, np.maximum),
    "clip": (3, np.clip),
    "rolling_mean": (2, _rolling_mean_expr),
    "rolling_nanmean": (2, _rolling_nanmean_expr),
}


# detailed_specification.md 36.6 C: rows of *input* needed before/after an output row, per builtin.
# An entry is a callable taking the call's argument nodes and returning (before, after), or None
# when the margin cannot be known statically (the expression then falls back to full
# computation). Builtins without an entry here are never windowed.
_ELEMENTWISE = (0, 0)


def _const_number(node: ast.AST):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _const_number(node.operand)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    return None


def _rolling_margin(args):
    w = _const_number(args[1]) if len(args) == 2 else None
    if w is None or not np.isfinite(w) or int(w) < 1:
        return None  # non-constant / invalid window: full evaluation (and its error)
    # A centered window needs w//2 before and (w-1)//2 after; w-1 on both sides also keeps the
    # fetched slice at least `w` long (the implementation clamps w to the length it is given).
    return (int(w) - 1, int(w) - 1)


_FUNC_MARGINS: dict[str, object] = {
    **{n: (lambda args: _ELEMENTWISE) for n in (
        "abs", "sqrt", "sin", "cos", "tan", "exp", "log", "log10", "sign", "floor", "ceil", "round", "min", "max", "clip"
    )},
    "delta": lambda args: (1, 0),
    "rolling_mean": _rolling_margin,
    "rolling_nanmean": _rolling_margin,
}


class ExpressionError(ValueError):
    """Raised when an expression fails to parse or uses a disallowed construct.

    Not a security boundary (this is a local desktop app) — it exists to turn
    typos into a clear error message instead of a stack trace (detailed_specification.md 6章).
    """


class UnknownFunctionError(ExpressionError):
    """A called function name is neither a builtin, a user function nor a map (26.1).

    `.name` lets the UI show a translated "function/map <name> not found" without parsing text.
    """

    def __init__(self, name: str):
        super().__init__(f"unknown function: {name}")
        self.name = name


def _parse(expr: str) -> ast.Expression:
    # Japanese IME full-width input mode turns "+" into "＋" (U+FF0B) etc. without the
    # user noticing — ast.parse rejects those as invalid characters. NFKC normalization
    # maps the full-width Latin/digit/symbol/space block back to their ASCII equivalents
    # (e.g. "＋" -> "+", "（" -> "(", U+3000 -> " ") before parsing.
    expr = unicodedata.normalize("NFKC", expr)
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"invalid syntax: {e}") from e
    except (MemoryError, RecursionError) as e:  # pathological hand-edited input
        raise ExpressionError("expression too complex") from e


def _is_number_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def check_allowed_syntax(tree: ast.AST) -> None:
    """Raise ExpressionError for any node the evaluator would reject (expressions.ja.md 6.1).

    The same whitelist as `_eval_node` (binary/unary operators from `_ALLOWED_*`, calls to a
    plain name, names, numeric non-bool constants), so save-time validation of a user function
    body cannot drift from evaluation. Operator/context/keyword nodes ride along with those.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            ok = type(node.op) in _ALLOWED_BINOPS
        elif isinstance(node, ast.UnaryOp):
            ok = type(node.op) in _ALLOWED_UNARYOPS
        elif isinstance(node, ast.Call):
            ok = isinstance(node.func, ast.Name)
        elif isinstance(node, ast.Constant):
            ok = _is_number_constant(node)
        else:
            ok = isinstance(node, (ast.Expression, ast.Name, ast.Load, ast.keyword, ast.operator, ast.unaryop))
        if not ok:
            raise ExpressionError(f"expression not allowed: {type(node).__name__}")


def evaluate_expression(
    expr: str,
    variables: Mapping[str, np.ndarray],
    registry: "FunctionRegistry | None" = None,
) -> np.ndarray:
    """Evaluate `expr` over `variables`.

    `registry` (core/functions.py, detailed_specification.md 26.1) adds user functions and
    maps on top of the builtins; None means builtins only, exactly as before.
    """
    tree = _parse(expr)
    try:
        # inf/NaN from a/0, log(-1) ... are results, not warnings (expressions.ja.md 6.1)
        with np.errstate(all="ignore"):
            return _eval_node(tree.body, variables, registry, 0)
    except RecursionError as e:  # e.g. "-" * 5000 + "a"; also covers user-function bodies
        raise ExpressionError("expression too complex") from e


def referenced_names(expr: str) -> set[str]:
    """Variable names `expr` refers to — every ast.Name except a call's function name
    (detailed_specification.md 18.1). Used for derived-variable dependency tracking.
    """
    tree = _parse(expr)
    func_names = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in func_names}


def referenced_functions(expr: str) -> set[str]:
    """Names called in `expr` that are not builtins (detailed_specification.md 26.1):
    the candidates for user functions / maps, used for dependency tracking and validation.
    """
    tree = _parse(expr)
    return {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in _ALLOWED_FUNCS
    }


def expression_margin(
    expr: str,
    registry: "FunctionRegistry | None",
    name_margins: Mapping[str, "tuple[int, int] | None"],
) -> "tuple[int, int] | None":
    """(before, after): how many rows of the variables around an output row `expr` needs
    (detailed_specification.md 36.6 C), or None if that cannot be determined statically
    (unknown name/function, builtin without a margin entry, non-constant rolling window...).
    `name_margins` gives each variable's own margin ((0, 0) for a plain column; a windowed
    derived column's margin otherwise). Margins add up along nested calls and take the max
    across siblings.
    """
    try:
        body = _parse(expr).body
    except ExpressionError:
        return None
    return _margin(body, registry, name_margins, 0)


def _combine(margins):
    if any(m is None for m in margins):
        return None
    return (max((m[0] for m in margins), default=0), max((m[1] for m in margins), default=0))


def _margin(node, registry, env, depth):
    if depth > 40:
        return None
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _combine([_margin(node.left, registry, env, depth), _margin(node.right, registry, env, depth)])
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _margin(node.operand, registry, env, depth)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return (0, 0)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        kind = "builtin" if name in _ALLOWED_FUNCS else (registry.kind(name) if registry is not None else None)
        if kind is None:
            return None
        if kind == "builtin":
            own_fn = _FUNC_MARGINS.get(name)
            if own_fn is None or node.keywords:
                return None
            own = own_fn(node.args)
            args = _combine([_margin(a, registry, env, depth) for a in node.args])
            if own is None or args is None:
                return None
            return (own[0] + args[0], own[1] + args[1])
        if kind == "map":  # lookup tables are elementwise (27)
            if node.keywords:
                return None
            return _combine([_margin(a, registry, env, depth) for a in node.args])
        # user function: margin of its body, the parameters standing for the arguments' margins
        fn = registry.resolve(name)
        if len(node.args) != len(fn.params):
            return None
        scope = {p: _margin(a, registry, env, depth) for p, a in zip(fn.params, node.args)}
        for kw in node.keywords:
            if kw.arg is None or any(isinstance(n, ast.Name) for n in ast.walk(kw.value)):
                return None  # constant overrides must be plain numbers here
        for cname, _default in fn.constants:
            scope[cname] = (0, 0)
        try:
            body = _parse(fn.expression).body
        except ExpressionError:
            return None
        return _margin(body, registry, scope, depth + 1)
    return None


def _eval_node(node: ast.AST, variables: Mapping[str, np.ndarray], registry=None, depth: int = 0):
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left, variables, registry, depth)
        right = _eval_node(node.right, variables, registry, depth)
        try:
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        except (TypeError, ValueError) as e:  # e.g. arrays of different lengths (6.1)
            raise ExpressionError(f"operator {type(node.op).__name__}: {e}") from e

    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand, variables, registry, depth))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError(f"function not allowed: {ast.dump(node.func)}")
        name = node.func.id
        kind = "builtin" if name in _ALLOWED_FUNCS else (registry.kind(name) if registry is not None else None)
        if kind is None:
            raise UnknownFunctionError(name)
        if kind == "user":
            from .functions import call_user_function  # lazy: functions.py imports this module

            return call_user_function(registry.resolve(name), node, variables, registry, depth)
        # builtins and maps: positional only, exact arity (see _ALLOWED_FUNCS comment)
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        if kind == "builtin":
            arity, func = _ALLOWED_FUNCS[name]
        else:
            func = registry.resolve(name)
            arity = func.arity
        if len(node.args) != arity:
            raise ExpressionError(f"{name}() expects {arity} argument(s), got {len(node.args)}")
        args = [_eval_node(a, variables, registry, depth) for a in node.args]
        try:
            return func(*args)
        except ExpressionError:
            raise
        except (TypeError, ValueError) as e:  # last resort: callers only catch ExpressionError
            raise ExpressionError(f"{name}(): {e}") from e

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ExpressionError(f"unknown variable: {node.id}")
        return variables[node.id]

    if _is_number_constant(node):
        # always float: int64 would raise on 2**-1 and silently wrap on 2**10000 (6.1)
        try:
            return float(node.value)
        except OverflowError as e:  # a 400-digit integer literal
            raise ExpressionError("number too large") from e

    raise ExpressionError(f"expression not allowed: {ast.dump(node)}")
