from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .expression import ExpressionError, referenced_names


@dataclass(frozen=True)
class Variable:
    """A plain column loaded from the Parquet source."""

    name: str


@dataclass(frozen=True)
class DerivedVariable:
    """A variable computed from an expression over other variables (specification.md 5.5)."""

    name: str
    expression: str


def _refs_within(exprs: Mapping[str, str]) -> dict[str, set[str] | None]:
    """{name: the derived names its expression references}, or None if unparsable."""
    refs: dict[str, set[str] | None] = {}
    for name, expr in exprs.items():
        try:
            refs[name] = referenced_names(expr) & exprs.keys()
        except ExpressionError:
            refs[name] = None
    return refs


def dependency_order(exprs: Mapping[str, str]) -> list[str]:
    """`{name: expression}` ordered so each derived variable comes after the derived
    variables it references; names outside the mapping (file columns) are ignored.
    Stable where dependencies allow. Cyclic/unparsable leftovers are appended in input
    order rather than raising here — the caller gets a normal evaluation error for
    them (detailed_specification.md 18.1).
    """
    refs = _refs_within(exprs)
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(exprs)
    progress = True
    while remaining and progress:
        progress = False
        for name in list(remaining):
            deps = refs[name]
            if deps is not None and deps - {name} <= placed and name not in deps:
                ordered.append(name)
                placed.add(name)
                remaining.remove(name)
                progress = True
                break  # restart from the front to keep the input order stable
    return ordered + remaining


def dependents_of(name: str, exprs: Mapping[str, str]) -> list[str]:
    """Every derived variable depending on `name` directly or transitively, in
    dependency_order."""
    refs = _refs_within(exprs)
    found: set[str] = set()
    frontier = {name}
    while frontier:
        frontier = {n for n, deps in refs.items() if deps and deps & frontier and n not in found and n != name}
        found |= frontier
    return [n for n in dependency_order(exprs) if n in found]
