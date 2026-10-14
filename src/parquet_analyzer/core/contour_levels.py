"""Contour line values for the XY window's map overlay (analysis.ja.md 44.7).

Qt-free. A `LevelSpec` is "auto" (about 10 lines at a round step 1/2/5 x 10^n over the value range of
the overlaid maps), "step" (multiples of a step) or "list" (explicit values). `plan_levels` turns a spec
into the lines each map gets (those inside the map's own value range); invalid input or more than
`MAX_LEVELS_PER_MAP` lines for one map raises `ContourLevelError` (a reason code + kwargs, the UI
turns it into a translated sentence: core holds no display text).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .map_grid import _decimal, nice_step

MAX_LEVELS_PER_MAP = 200
AUTO_LINES = 10
MODES = ("auto", "step", "list")


class ContourLevelError(ValueError):
    """Invalid level input (`not_number`, `step_not_positive`, `empty_list`, `too_many` (limit, name, n),
    `unknown_mode`)."""

    def __init__(self, code: str, **kwargs) -> None:
        super().__init__(code)
        self.code = code
        self.kwargs = kwargs


@dataclass(frozen=True)
class LevelSpec:
    mode: str = "auto"
    step: float | None = None
    values: tuple[float, ...] = ()

    def describe(self) -> dict:
        """For the operation log."""
        if self.mode == "step":
            return {"mode": "step", "step": self.step}
        if self.mode == "list":
            return {"mode": "list", "values": list(self.values)}
        return {"mode": "auto"}


def parse_step(text: str) -> float:
    """The step field: a finite number > 0."""
    try:
        v = float(text.strip())
    except ValueError:
        raise ContourLevelError("not_number") from None
    if not math.isfinite(v):
        raise ContourLevelError("not_number")
    if v <= 0:
        raise ContourLevelError("step_not_positive")
    return v


def parse_values(text: str) -> tuple[float, ...]:
    """Comma separated numbers: duplicates dropped, ascending (a full-width comma is accepted too)."""
    parts = [p.strip() for p in text.replace("，", ",").replace("、", ",").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        raise ContourLevelError("empty_list")
    out: set[float] = set()
    for p in parts:
        try:
            v = float(p)
        except ValueError:
            raise ContourLevelError("not_number") from None
        if not math.isfinite(v):
            raise ContourLevelError("not_number")
        out.add(v)
    return tuple(sorted(out))


def _round_level(value: float, step: float) -> float:
    """`k * step` without binary noise (0.30000000000000004 -> 0.3), relative to the step (so a range
    around 1e-17 keeps its lines apart) and never -0.0."""
    digits = max(0, -math.floor(math.log10(step)) + 3)
    return round(value, digits) + 0.0


def auto_step(lo: float, hi: float, target: int = AUTO_LINES) -> float | None:
    """A round step (1/2/5 x 10^n) giving at most about `target` lines over [lo, hi]; None for an
    empty or non-finite range."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    m, e = nice_step((hi - lo) / target)
    return _decimal(m, e)


def multiples_in_range(step: float, lo: float, hi: float) -> list[float]:
    """The multiples of `step` inside [lo, hi] (a tolerance of 1e-9 steps keeps a bound that float
    noise would otherwise drop). The list is cut at 10x the per-map limit: callers only need to see
    that it is too long."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi < lo or step <= 0:
        return []
    try:
        k_lo = math.ceil(lo / step - 1e-9)
        k_hi = min(math.floor(hi / step + 1e-9), k_lo + 10 * MAX_LEVELS_PER_MAP)
    except OverflowError:  # a step so small that lo / step overflows
        raise ContourLevelError("too_many", limit=MAX_LEVELS_PER_MAP, name="", n="\u221e") from None
    return list(dict.fromkeys(_round_level(k * step, step) for k in range(k_lo, k_hi + 1)))


def plan_levels(spec: LevelSpec, ranges: dict[str, tuple[float, float]]) -> dict[str, list[float]]:
    """The contour values per overlaid map. `ranges` = name -> (min, max) of the map's finite values
    (a map without finite values is left out of it). Auto uses the union of the ranges for the step,
    then each map gets the lines inside its own range. More than 200 lines for one map raises
    `ContourLevelError("too_many", limit=, name=, n=)`."""
    ranges = {n: r for n, r in ranges.items() if all(math.isfinite(v) for v in r)}
    out: dict[str, list[float]] = {}
    try:
        _plan(spec, ranges, out)
    except ContourLevelError as exc:
        if exc.code == "too_many" and not exc.kwargs.get("name"):  # raised below the per-map loop
            exc.kwargs["name"] = next((n for n in ranges if n not in out), "")
        raise
    for name, levels in out.items():
        if len(levels) > MAX_LEVELS_PER_MAP:
            raise ContourLevelError("too_many", limit=MAX_LEVELS_PER_MAP, name=name, n=len(levels))
    return out


def _plan(spec: LevelSpec, ranges, out: dict) -> None:
    """Fill `out` with the lines strictly inside each map's range. A level exactly at the minimum or
    maximum is left out in every mode: it would be a degenerate line, and matplotlib's `contour` (the
    image) omits it while the screen would draw it."""
    if spec.mode == "auto":
        if not ranges:
            return
        step = auto_step(min(r[0] for r in ranges.values()), max(r[1] for r in ranges.values()))
        for name, (lo, hi) in ranges.items():
            out[name] = _inside(multiples_in_range(step, lo, hi) if step else [], lo, hi)
    elif spec.mode == "step":
        if spec.step is None or not math.isfinite(spec.step) or spec.step <= 0:
            raise ContourLevelError("step_not_positive")
        for name, (lo, hi) in ranges.items():
            out[name] = _inside(multiples_in_range(spec.step, lo, hi), lo, hi)
    elif spec.mode == "list":
        if not spec.values:
            raise ContourLevelError("empty_list")
        for name, (lo, hi) in ranges.items():
            out[name] = _inside(list(spec.values), lo, hi)
    else:
        raise ContourLevelError("unknown_mode", mode=spec.mode)


def _inside(levels: list[float], lo: float, hi: float) -> list[float]:
    return [v for v in levels if lo < v < hi]
