from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variable:
    """A plain column loaded from the Parquet source."""

    name: str


@dataclass(frozen=True)
class DerivedVariable:
    """A variable computed from an expression over other variables (specification.md 5.5)."""

    name: str
    expression: str
