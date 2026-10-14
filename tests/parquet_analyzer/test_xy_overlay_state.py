"""`OverlayState` (Qt-free, analysis.ja.md 44.12) and the structure of the XY window split.

No widgets are created here: the state is tested on its own, and the structural test reads the sources.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from parquet_analyzer.core.contour_levels import LevelSpec
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.ui import xy_overlay as xo
from parquet_analyzer.ui.plot_widget import _COLOR_CYCLE

UI = Path(xo.__file__).parent


def bowl(name="bowl", n=21, scale=1.0):
    x = np.linspace(-1, 1, n)
    return LookupTable(name, "map", x, x.copy(), (x[None, :] ** 2 + x[:, None] ** 2) * scale)


def curve(name="line"):
    x = np.linspace(-1, 1, 11)
    return LookupTable(name, "curve", x, x * 0.5)


# -- colours -----------------------------------------------------------------------------------


def test_first_overlay_with_one_y_takes_the_second_colour():
    s = xo.OverlayState()
    s.add("a", n_y=1)
    assert s.names == ["a"] and s.colors["a"] == _COLOR_CYCLE[1]
    s.add("b", n_y=1)
    assert s.colors["b"] == _COLOR_CYCLE[2]


def test_removed_overlay_frees_its_colour_for_the_next_one():
    s = xo.OverlayState()
    s.add("a", 1)
    s.add("b", 1)
    s.remove("a")
    s.add("c", 1)
    assert s.colors["c"] == _COLOR_CYCLE[1] and s.names == ["b", "c"]
    assert "a" not in s.colors


def test_colours_used_by_the_y_series_are_skipped():
    s = xo.OverlayState()
    s.add("a", n_y=3)  # Y series use colours 0..2
    assert s.colors["a"] == _COLOR_CYCLE[3]
    s2 = xo.OverlayState()
    s2.add("a", n_y=0)  # 3D: no Y series colours
    assert s2.colors["a"] == _COLOR_CYCLE[0]


def test_colour_wraps_when_all_are_taken():
    s = xo.OverlayState()
    for i in range(len(_COLOR_CYCLE) + 1):
        s.add(f"n{i}", 0)
    assert s.colors[f"n{len(_COLOR_CYCLE)}"] in _COLOR_CYCLE


def test_remove_also_forgets_the_hidden_mark():
    s = xo.OverlayState()
    s.add("a", 1)
    s.hidden.add("a")
    s.remove("a")
    assert s.hidden == set() and s.names == []


# -- hidden ------------------------------------------------------------------------------------


def test_is_hidden_follows_the_legend_in_2d_but_not_in_3d():
    s = xo.OverlayState()
    s.hidden.add("a")
    assert s.is_hidden("a", "2d") and not s.is_hidden("b", "2d")
    assert not s.is_hidden("a", "3d")  # 3D has no legend to bring it back


# -- entries -----------------------------------------------------------------------------------


def test_entries_drop_vanished_tables_and_forget_them():
    s = xo.OverlayState()
    s.add("bowl", 1)
    s.add("gone", 1)
    s.hidden.add("gone")
    out = s.entries({"bowl": bowl()}, "2d", "x", "y")
    assert [e.name for e in out] == ["bowl"]
    assert s.names == ["bowl"] and "gone" not in s.colors and s.hidden == set()


def test_entries_skip_curves_in_3d_but_keep_the_selection():
    s = xo.OverlayState()
    s.add("bowl", 0)
    s.add("line", 0)
    tables = {"bowl": bowl(), "line": curve()}
    out3 = s.entries(tables, "3d", "x", "y")
    assert [e.name for e in out3] == ["bowl"] and s.names == ["bowl", "line"]
    out2 = s.entries(tables, "2d", "x", "y")
    assert [e.name for e in out2] == ["bowl", "line"]
    assert out2[0].color == s.colors["bowl"]


def test_entry_label_carries_the_axis_hint_only_when_names_differ():
    s = xo.OverlayState()
    s.add("named", 1)
    s.add("plain", 1)
    named = bowl("named")
    named.axis_names = ("rpm", "torque", "eff")
    out = s.entries({"named": named, "plain": bowl("plain")}, "2d", "x", "y")
    assert "rpm" in out[0].label and out[0].label.startswith("named (")
    assert out[1].label == "plain"


# -- level planning ----------------------------------------------------------------------------


def test_plan_levels_sets_no_notice_for_a_valid_spec():
    s = xo.OverlayState(contour_spec=LevelSpec("list", values=(0.25, 0.49)))
    s.add("bowl", 1)
    entries = s.entries({"bowl": bowl()}, "2d", "x", "y")
    assert s.plan_levels(entries, "2d") == {"bowl": [0.25, 0.49]} and s.notice == ""


def test_plan_levels_notices_out_of_range_and_invalid_specs():
    s = xo.OverlayState(contour_spec=LevelSpec("list", values=(50.0,)))
    s.add("bowl", 1)
    entries = s.entries({"bowl": bowl()}, "2d", "x", "y")
    assert s.plan_levels(entries, "2d") == {"bowl": []} and s.notice  # outside the map's range
    s.contour_spec = LevelSpec("step", step=-1.0)
    assert s.plan_levels(entries, "2d") == {} and s.notice  # invalid: nothing planned, a message
    s.contour_spec = LevelSpec("list", values=(0.5,))
    assert s.plan_levels(entries, "2d") == {"bowl": [0.5]} and s.notice == ""  # cleared again


def test_plan_levels_ignores_curves_and_hidden_maps_in_2d_only():
    s = xo.OverlayState(contour_spec=LevelSpec("list", values=(0.5,)))
    s.add("bowl", 1)
    s.add("line", 1)
    s.hidden.add("bowl")
    tables = {"bowl": bowl(), "line": curve()}
    entries = s.entries(tables, "2d", "x", "y")
    assert s.plan_levels(entries, "2d") == {} and s.notice == ""  # no visible map: nothing to say
    assert s.plan_levels(entries, "3d") == {"bowl": [0.5]}  # 3D ignores the legend state


# -- contour cache -----------------------------------------------------------------------------


def test_segments_are_cached_per_table_object_and_level(monkeypatch):
    calls = []
    real = xo.contour_segments
    monkeypatch.setattr(xo, "contour_segments", lambda *a, **k: calls.append(1) or real(*a, **k))
    s = xo.OverlayState()
    t = bowl()
    a = s.segments("bowl", t, 0.5)
    b = s.segments("bowl", t, 0.5)
    assert a is b and len(calls) == 1
    s.segments("bowl", t, 1.0)
    assert len(calls) == 2  # another level
    s.segments("bowl", bowl(), 0.5)
    assert len(calls) == 3  # a changed table (a new object) is recomputed
    assert len(a) > 0


def test_prune_cache_keeps_current_tables_and_drops_replaced_or_deleted_ones():
    s = xo.OverlayState()
    keep, replaced, deleted = bowl("keep"), bowl("replaced"), bowl("deleted")
    for t in (keep, replaced, deleted):
        s.segments(t.name, t, 0.5)
    s.prune_cache({"keep": keep, "replaced": bowl("replaced")})
    assert set(s.segment_cache) == {("keep", 0.5)}


# -- structure ---------------------------------------------------------------------------------


def test_the_window_keeps_no_overlay_state_attributes():
    src = (UI / "xy_plot_window.py").read_text(encoding="utf-8")
    for attr in ("_overlay_names", "_overlay_colors", "_hidden_overlay", "_segment_cache", "_overlay_notice",
                 "_overlay_levels", "_overlay_items", "_overlay_proxies", "_overlay_menu", "_contour_spec"):
        assert f"self.{attr}" not in src, attr
    assert "XYMapViewMixin" in src and "XYOverlayController" in src


def test_the_controller_and_state_do_not_reach_into_the_window():
    ctl = (UI / "xy_overlay_controller.py").read_text(encoding="utf-8")
    assert "xy_plot_window" not in ctl and "host._" not in ctl  # only the OverlayHost interface
    state_src = (UI / "xy_overlay.py").read_text(encoding="utf-8")
    assert "QWidget" not in state_src and "QMenu" not in state_src
