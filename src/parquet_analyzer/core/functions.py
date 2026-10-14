"""Function registry and user-defined functions (detailed_specification.md 26.1/26.2).

The registry bundles the builtins (`core/expression.py`'s `_ALLOWED_FUNCS`), user functions
and maps (`core/maps.py`) so `evaluate_expression` can resolve any callable name.
"""
from __future__ import annotations

import ast
import keyword
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from .expression import (
    _ALLOWED_FUNCS,
    ExpressionError,
    UnknownFunctionError,
    _eval_node,
    _parse,
    check_allowed_syntax,
    referenced_functions,
    referenced_names,
)

MAX_CALL_DEPTH = 32  # evaluation-time cap on nested user-function calls (26.2)


@dataclass
class UserFunction:
    """A reusable named expression with parameters and default-valued constants (26.2)."""

    name: str
    params: list[str]
    constants: list[tuple[str, float]] = field(default_factory=list)
    expression: str = ""
    description: str = ""


class FunctionRegistry:
    """builtins + user functions + maps. Lookup order builtin -> user -> map (26.1).

    `user_functions` / `maps` are plain mutable dicts so the owner can swap contents.
    """

    def __init__(self, user_functions: Mapping[str, UserFunction] | None = None, maps: Mapping[str, object] | None = None):
        self.builtins = _ALLOWED_FUNCS
        self.user_functions: dict[str, UserFunction] = dict(user_functions or {})
        self.maps: dict[str, object] = dict(maps or {})

    def kind(self, name: str) -> str | None:
        if name in self.builtins:
            return "builtin"
        if name in self.user_functions:
            return "user"
        if name in self.maps:
            return "map"
        return None

    def resolve(self, name: str):
        """The builtin `(arity, callable)`, the `UserFunction`, the `LookupTable`, or None."""
        if name in self.builtins:
            return self.builtins[name]
        if name in self.user_functions:
            return self.user_functions[name]
        return self.maps.get(name)

    def all_names(self) -> set[str]:
        return set(self.builtins) | set(self.user_functions) | set(self.maps)


def _is_ident(s: object) -> bool:
    return isinstance(s, str) and s.isidentifier() and not keyword.iskeyword(s)


def _static_check_calls(fn: UserFunction, registry: FunctionRegistry, old: str | None) -> None:
    """Arity / keyword checks for every call in the body, so errors surface at save time."""
    tree = _parse(fn.expression)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("function not allowed in body")
        c = node.func.id
        if c == fn.name:
            raise ExpressionError(f"circular definition: {fn.name}")
        if c == old and c in registry.user_functions:
            raise UnknownFunctionError(c)
        kind = registry.kind(c)
        if kind is None:
            raise UnknownFunctionError(c)
        target = registry.resolve(c)
        if kind == "builtin":
            arity = target[0]
        elif kind == "map":
            arity = target.arity
        else:
            arity = len(target.params)
        if len(node.args) != arity:
            raise ExpressionError(f"{c}() expects {arity} argument(s), got {len(node.args)}")
        if kind != "user":
            if node.keywords:
                raise ExpressionError(f"{c}(): keyword arguments are not allowed")
            continue
        seen_kw: set[str] = set()
        for kw in node.keywords:
            if kw.arg is None or kw.arg not in {n for n, _ in target.constants}:
                raise ExpressionError(f"{c}() has no constant named {kw.arg}")
            if kw.arg in seen_kw:
                raise ExpressionError(f"duplicate keyword argument: {kw.arg}")
            seen_kw.add(kw.arg)


def validate_user_function(fn: UserFunction, registry: FunctionRegistry, *, replaces: str | None = None) -> None:
    """Raise ExpressionError if `fn` is not a valid definition (26.2).

    `registry` is what the function will live in. `replaces=None` means `fn` is new, so any
    existing user function with the same name is a clash. `replaces="old"` means `fn` edits
    (or renames) the function `old`, which is ignored when checking clashes, cycles and calls.
    """
    old = replaces
    if not _is_ident(fn.name):
        raise ExpressionError(f"invalid function name: {fn.name!r}")
    if not fn.params:
        raise ExpressionError("a function needs at least one parameter")
    const_names = [c[0] for c in fn.constants]
    seen: set[str] = set()
    for n in [*fn.params, *const_names]:
        if not _is_ident(n):
            raise ExpressionError(f"invalid parameter/constant name: {n!r}")
        if n in seen:
            raise ExpressionError(f"duplicate parameter/constant name: {n}")
        seen.add(n)
    if fn.name in registry.builtins or fn.name in registry.maps or (
        fn.name in registry.user_functions and fn.name != old
    ):
        raise ExpressionError(f"name already in use: {fn.name}")
    for cname, default in fn.constants:
        try:
            ok = not isinstance(default, bool) and isinstance(default, (int, float)) and math.isfinite(float(default))
        except (OverflowError, ValueError):  # e.g. a 400-digit int
            ok = False
        if not ok:
            raise ExpressionError(f"default of constant {cname} must be a finite number")

    names = referenced_names(fn.expression)  # also syntax-checks
    check_allowed_syntax(_parse(fn.expression))  # same whitelist as evaluation (6.1)
    unknown = sorted(names - seen)
    if unknown:
        raise ExpressionError(f"unknown name in function body (only parameters and constants allowed): {', '.join(unknown)}")
    called = referenced_functions(fn.expression)
    if fn.name in called:
        raise ExpressionError(f"circular definition: {fn.name}")
    _static_check_calls(fn, registry, old)
    # cycle through other user functions: walk the call graph with `fn` replacing its old self
    graph: dict[str, set[str]] = {}
    for n, f in registry.user_functions.items():
        if n in (fn.name, old):
            continue
        try:
            graph[n] = referenced_functions(f.expression)
        except ExpressionError:  # another function's broken body is that function's problem
            graph[n] = set()
    graph[fn.name] = called
    stack, visited = list(called), set()
    while stack:
        n = stack.pop()
        if n == fn.name:
            raise ExpressionError(f"circular definition: {fn.name}")
        if n in visited:
            continue
        visited.add(n)
        stack.extend(graph.get(n, ()))
    # nesting depth must fit the evaluation-time cap (the graph is acyclic from fn here)
    memo: dict[str, int] = {}

    def chain(n: str, active: frozenset) -> int:
        if n in memo:
            return memo[n]
        if n in active or n not in graph:
            return 0
        d = 1 + max((chain(c, active | {n}) for c in graph[n]), default=0)
        memo[n] = d
        return d

    if chain(fn.name, frozenset()) > MAX_CALL_DEPTH:
        raise ExpressionError(f"call chain deeper than {MAX_CALL_DEPTH} user functions")


def call_user_function(fn: UserFunction, node: ast.Call, variables, registry: FunctionRegistry, depth: int):
    """Evaluate a call node to user function `fn` (26.2), called from expression._eval_node."""
    if depth + 1 > MAX_CALL_DEPTH:
        raise ExpressionError(f"user function call depth exceeded ({MAX_CALL_DEPTH}) at {fn.name}")
    if len(node.args) != len(fn.params):
        raise ExpressionError(f"{fn.name}() expects {len(fn.params)} argument(s), got {len(node.args)}")
    consts = dict(fn.constants)
    overrides: dict[str, float] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise ExpressionError("**kwargs are not allowed")
        if kw.arg not in consts:
            raise ExpressionError(f"{fn.name}() has no constant named {kw.arg}")
        if kw.arg in overrides:
            raise ExpressionError(f"duplicate keyword argument: {kw.arg}")
        v = _eval_node(kw.value, variables, registry, depth)
        if np.ndim(v) != 0 or not np.isfinite(v):
            raise ExpressionError(f"constant {kw.arg} must be a finite scalar")
        overrides[kw.arg] = float(v)
    scope = {p: _eval_node(a, variables, registry, depth) for p, a in zip(fn.params, node.args)}
    scope.update(consts)
    scope.update(overrides)
    return _eval_node(_parse(fn.expression).body, scope, registry, depth + 1)


def check_expression_calls(expr: str, registry: FunctionRegistry) -> None:
    """Static arity / keyword-name check of every call in `expr` against `registry`, with the
    same rules evaluation applies (used by the view manager to predict what `load_view` skips,
    26.5). Raises UnknownFunctionError / ExpressionError."""
    for node in ast.walk(_parse(expr)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("function not allowed")
        name = node.func.id
        kind = registry.kind(name)
        if kind is None:
            raise UnknownFunctionError(name)
        target = registry.resolve(name)
        arity = target[0] if kind == "builtin" else target.arity if kind == "map" else len(target.params)
        if len(node.args) != arity:
            raise ExpressionError(f"{name}() expects {arity} argument(s), got {len(node.args)}")
        if kind != "user":
            if node.keywords:
                raise ExpressionError(f"{name}(): keyword arguments are not allowed")
            continue
        consts = {n for n, _ in target.constants}
        seen: set[str] = set()
        for kw in node.keywords:
            if kw.arg is None or kw.arg not in consts:
                raise ExpressionError(f"{name}() has no constant named {kw.arg}")
            if kw.arg in seen:
                raise ExpressionError(f"duplicate keyword argument: {kw.arg}")
            seen.add(kw.arg)
