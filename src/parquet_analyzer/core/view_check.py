"""Which items of a saved view are usable against a set of file columns (no Qt, no window state).

One pure rule used by three callers so they cannot drift apart: `MainWindow.load_view` (what it
skips), the view manager's "missing" marks (persistence 20.2, 54.5) and the Parquet export from a
saved view (export 57.7.3). Syntax and call (arity / keyword) checks only; no trial computation.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from .expression import (
    ExpressionError,
    UnknownFunctionError,
    referenced_functions,
    referenced_names,
)
from .functions import FunctionRegistry, check_expression_calls
from .variable import dependency_order


@dataclass(frozen=True)
class Problem:
    """Why an item is unusable. `kind`: alias_name (a derived variable named like a signal alias),
    column_name (named like a column), syntax, unknown_function (detail = the name), call (detail =
    the arity / keyword message), missing_column (detail = the name no column provides),
    dependency (detail = the unusable derived variable it uses), time (the time axis is missing),
    time_name (a derived variable named like the exported time column)."""

    kind: str
    detail: str = ""


def effective_available(
    all_names: Iterable[str], alias_sources: Mapping[str, str], time_name: str | None
) -> set[str]:
    """The names a view's series / formulas resolve against once `time_name` is the time axis: every
    column and alias except the axis' source and all aliases of that source (persistence 54.5, the
    rule `load_view` ends up with after switching the axis)."""
    names = set(all_names)
    if time_name is None or time_name not in names:
        return names
    source = alias_sources.get(time_name, time_name)
    axis = {time_name, source, *(a for a, src in alias_sources.items() if src == source)}
    return names - axis


def classify_derived(
    expressions: Mapping[str, str],
    available: Collection[str],
    *,
    alias_names: Collection[str] = (),
    registry: FunctionRegistry | None = None,
    functions: Collection[str] | None = None,
    reserved: Collection[str] = (),
) -> dict[str, Problem | None]:
    """`{derived name: Problem or None (usable)}` in dependency order, cascading: a variable using an
    unusable one is unusable. `available`: the variable names a formula may reference (columns and
    aliases minus the time axis). `alias_names`: every alias of the dictionary. `registry` enables
    the unknown-function / arity / keyword check; `functions` (names that exist) is the older,
    name-only check used when no registry is given. `reserved`: names a derived variable cannot take
    in the caller's context (the export's output time column, 57.7.3); load_view passes none."""
    available = set(available)
    out: dict[str, Problem | None] = {}
    for name in dependency_order(expressions):
        expr = expressions[name]
        if name in alias_names:
            out[name] = Problem("alias_name")
            continue
        if name in reserved:
            out[name] = Problem("time_name")
            continue
        if name in available:  # load_view: "duplicate variable name"
            out[name] = Problem("column_name")
            continue
        try:
            refs = referenced_names(expr)  # also the syntax check
            absent = sorted(referenced_functions(expr) - set(functions)) if functions is not None else []
        except ExpressionError:
            out[name] = Problem("syntax")
            continue
        if registry is not None:
            try:
                check_expression_calls(expr, registry)
            except UnknownFunctionError as e:
                out[name] = Problem("unknown_function", e.name)
                continue
            except ExpressionError as e:
                out[name] = Problem("call", str(e))
                continue
        if absent:
            out[name] = Problem("unknown_function", absent[0])
            continue
        problem: Problem | None = None
        for ref in sorted(refs):
            if ref in available:
                continue
            if ref in out and out[ref] is None:
                continue
            problem = Problem("dependency", ref) if ref in expressions else Problem("missing_column", ref)
            break
        out[name] = problem
    return out
