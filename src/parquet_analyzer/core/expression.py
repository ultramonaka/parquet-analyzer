from __future__ import annotations

import ast
import unicodedata
from typing import Mapping

import numpy as np

from .analysis import delta, rolling_mean

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
    "delta": (1, delta),
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
    "rolling_mean": (2, rolling_mean),
}


class ExpressionError(ValueError):
    """Raised when an expression fails to parse or uses a disallowed construct.

    Not a security boundary (this is a local desktop app) — it exists to turn
    typos into a clear error message instead of a stack trace (detailed_specification.md 6章).
    """


def evaluate_expression(expr: str, variables: Mapping[str, np.ndarray]) -> np.ndarray:
    # Japanese IME full-width input mode turns "+" into "＋" (U+FF0B) etc. without the
    # user noticing — ast.parse rejects those as invalid characters. NFKC normalization
    # maps the full-width Latin/digit/symbol/space block back to their ASCII equivalents
    # (e.g. "＋" -> "+", "（" -> "(", U+3000 -> " ") before parsing.
    expr = unicodedata.normalize("NFKC", expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"invalid syntax: {e}") from e
    return _eval_node(tree.body, variables)


def _eval_node(node: ast.AST, variables: Mapping[str, np.ndarray]):
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        return _ALLOWED_BINOPS[type(node.op)](left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand, variables))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ExpressionError(f"function not allowed: {ast.dump(node.func)}")
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        arity, func = _ALLOWED_FUNCS[node.func.id]
        if len(node.args) != arity:
            raise ExpressionError(
                f"{node.func.id}() expects {arity} argument(s), got {len(node.args)}"
            )
        args = [_eval_node(a, variables) for a in node.args]
        return func(*args)

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ExpressionError(f"unknown variable: {node.id}")
        return variables[node.id]

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    raise ExpressionError(f"expression not allowed: {ast.dump(node)}")
