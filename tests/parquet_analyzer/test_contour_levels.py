"""Contour level computation (analysis.ja.md 44.7), Qt-free."""

from __future__ import annotations

import numpy as np
import pytest

from parquet_analyzer.core import contour_levels as cl
from parquet_analyzer.core.contour_levels import ContourLevelError, LevelSpec


@pytest.mark.parametrize(
    ("lo", "hi", "step"),
    [(0.0, 2.0, 0.2), (0.0, 1.0, 0.1), (0.0, 100.0, 10.0), (-5.0, 5.0, 1.0), (0.0, 0.4, 0.05), (0.0, 7.0, 1.0)],
    ids=["0-2", "0-1", "0-100", "neg-pos", "small", "0-7"],
)
def test_auto_step_is_a_round_step_for_about_ten_lines(lo, hi, step):
    s = cl.auto_step(lo, hi)
    assert s == pytest.approx(step)
    levels = cl.plan_levels(LevelSpec(), {"m": (lo, hi)})["m"]
    assert 5 <= len(levels) <= 12
    mantissa = s / 10 ** np.floor(np.log10(s))
    assert round(mantissa, 6) in (1.0, 2.0, 5.0)


def test_auto_uses_the_union_of_the_ranges_and_each_map_gets_its_own_lines():
    plan = cl.plan_levels(LevelSpec(), {"a": (0.5, 3.5), "b": (0.0, 10.0)})
    assert plan["b"][1] - plan["b"][0] == pytest.approx(1.0)  # step from the union (0..10)
    assert plan["a"] == [1.0, 2.0, 3.0]  # only the multiples inside a's own range


def test_auto_constant_map_has_no_lines():
    assert cl.plan_levels(LevelSpec(), {"c": (3.0, 3.0)}) == {"c": []}


def test_step_gives_multiples_inside_the_range_without_float_noise():
    plan = cl.plan_levels(LevelSpec("step", step=0.1), {"m": (0.05, 0.45)})
    assert plan["m"] == [0.1, 0.2, 0.3, 0.4]


def test_list_is_filtered_by_each_maps_range():
    spec = LevelSpec("list", values=(0.8, 0.9, 5.0))
    assert cl.plan_levels(spec, {"m": (0.0, 1.0)}) == {"m": [0.8, 0.9]}
    assert cl.plan_levels(spec, {"m": (10.0, 20.0)}) == {"m": []}


def test_parse_values_dedups_and_sorts():
    assert cl.parse_values("0.95, 0.8,0.9, 0.8") == (0.8, 0.9, 0.95)
    assert cl.parse_values("1，2") == (1.0, 2.0)


@pytest.mark.parametrize("text", ["", " , ", "a", "1, x", "inf", "nan"], ids=["empty", "commas", "word", "mixed", "inf", "nan"])
def test_parse_values_rejects_bad_input(text):
    with pytest.raises(ContourLevelError):
        cl.parse_values(text)


@pytest.mark.parametrize("text", ["0", "-1", "abc", "inf", ""], ids=["zero", "negative", "word", "inf", "empty"])
def test_parse_step_rejects_bad_input(text):
    with pytest.raises(ContourLevelError):
        cl.parse_step(text)


def test_too_many_lines_for_one_map_is_refused():
    with pytest.raises(ContourLevelError) as e:
        cl.plan_levels(LevelSpec("step", step=0.001), {"m": (0.0, 1.0)})
    assert e.value.code == "too_many" and e.value.kwargs["limit"] == 200
    with pytest.raises(ContourLevelError):  # 0, 0.005, ..., 1 = 201 lines
        cl.plan_levels(LevelSpec("step", step=0.005), {"m": (-0.001, 1.001)})
    # the boundary: 200 lines are fine
    assert len(cl.plan_levels(LevelSpec("step", step=0.01), {"m": (-0.001, 2.0)})["m"]) == 200


def test_non_finite_ranges_are_left_out():
    assert cl.plan_levels(LevelSpec(), {"m": (float("nan"), 1.0)}) == {}


@pytest.mark.parametrize("spec", [LevelSpec(), LevelSpec("step", step=0.5), LevelSpec("list", values=(0.0, 1.0, 2.0))],
                         ids=["auto", "step", "list"])
def test_a_level_exactly_at_the_minimum_or_maximum_is_left_out(spec):
    levels = cl.plan_levels(spec, {"m": (0.0, 2.0)})["m"]
    assert levels and 0.0 not in levels and 2.0 not in levels  # the image's contour omits them too


def test_near_constant_range_gives_distinct_levels_and_no_negative_zero():
    import math

    levels = cl.plan_levels(LevelSpec(), {"m": (-1e-17, 1e-17)})["m"]
    assert levels == sorted(set(levels)) and len(levels) >= 3
    zeros = [v for v in levels if v == 0.0]
    assert zeros == [0.0] and math.copysign(1.0, zeros[0]) > 0  # exactly one zero, not -0.0


def test_a_step_so_small_that_it_overflows_is_too_many_not_a_crash():
    with pytest.raises(ContourLevelError) as e:
        cl.plan_levels(LevelSpec("step", step=1e-320), {"m": (0.0, 1.0)})
    assert e.value.code == "too_many" and e.value.kwargs["name"] == "m"
