"""Views saved by older versions still load (app 46.7, A4; persistence 8/20/33/35).

The JSON files under `tests/parquet_analyzer/data/views/<version>/` were saved by the *real* code
of each version (0.1.0 = f5573e9^, 0.2.0 = f5573e9, 0.3.0 = v0.3.0), never written by hand. To
regenerate them (needs the git history; the tests do not run it):

    uv run python tests/parquet_analyzer/data/views/make_old_views.py [VERSION ...]

The script makes a temporary `git worktree` per version, runs that version's MainWindow offscreen
over the Parquet file `make_old_views.write_sample_parquet` writes, builds views with the features
that version has, and saves them through that version's own `save_view_dialog`. When the view
format changes, run it from the `main` *before* the change and add the new version there.

What is checked for each file: it loads without an exception or a ViewFormatError; the plots,
series, visibility, derived variables and time axis are what was saved, except for the items in
`EXPECTED_SKIPPED` (whose single consolidated warning is checked); the view manager's "missing"
marks equal what `load_view` really skipped; and saving again and loading that gives the same state.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox
from waiting import wait_until

from parquet_analyzer import config
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog

VIEWS_DIR = Path(__file__).resolve().parent / "data" / "views"
_spec = importlib.util.spec_from_file_location("make_old_views", VIEWS_DIR / "make_old_views.py")
make_old_views = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_old_views)

# Items the *current* code skips when loading (and says so in one warning). Everything not listed
# must apply fully and silently. Confirmed against persistence 8/20/26.5/27.5: a derived variable
# calling a user function or map that does not exist now is skipped (with the series plotting it);
# nothing else in an old view is incompatible.
EXPECTED_SKIPPED: dict[str, set[str]] = {
    "0.3.0/functions_maps": {"d_fn", "d_map"},
}

CASES = sorted(f"{p.parent.name}/{p.stem}" for p in VIEWS_DIR.glob("*/*.json"))


def test_the_expected_versions_are_present():
    assert {c.split("/")[0] for c in CASES} == {"0.1.0", "0.2.0", "0.3.0"}
    assert {"0.1.0/basic", "0.3.0/independent_x", "0.3.0/functions_maps"} <= set(CASES)


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "critical": [], "information": []}
    for kind in calls:
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, _k=kind, **k: calls[_k].append(a[2])))
    return calls


def _wait(win):
    wait_until(lambda: not win._busy_sources, 20.0)


def _open(tmp_path):
    parquet = tmp_path / "sample.parquet"
    if not parquet.exists():
        make_old_views.write_sample_parquet(parquet)
    win = MainWindow()
    assert win.load_parquet(str(parquet))
    _wait(win)
    return win


def _install(case: str) -> tuple[str, dict]:
    """Copy the saved JSON into the (isolated) views folder; returns (view name, parsed JSON)."""
    version, stem = case.split("/")
    src = VIEWS_DIR / version / f"{stem}.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    config.PARQUET_ANALYZER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, config.PARQUET_ANALYZER_DATA_DIR / src.name)
    return payload["view_name"], payload


def _state(win) -> dict:
    """What a view stores, as read back from the live window."""
    return {
        "plots": [
            [(n, p.is_series_visible(n)) for n in p.series_names()] for p in win.plot_grid.plots
        ],
        "x_linked": [p.x_linked for p in win.plot_grid.plots],
        "derived": {n: d.expression for n, d in win._derived.items()},
        "time_column": win._time_column,
        "downsample": win._downsample_enabled,
    }


def _red_marks(dialog) -> tuple[set[str], set[str]]:
    """(series names, derived names) the dialog's details tree marks as missing (red)."""
    series, derived = set(), set()
    for i in range(dialog.details.topLevelItemCount()):
        top = dialog.details.topLevelItem(i)
        for j in range(top.childCount()):
            child = top.child(j)
            if child.foreground(0).color().name() != "#d32f2f":
                continue
            text = child.text(0).split(" — ")[0]
            if " = " in text and top.text(0).startswith(("Derived", "派生")):
                derived.add(text.split(" = ")[0])
            else:
                series.add(text)
    return series, derived


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_old_view_loads_with_only_the_expected_warnings(qapp, isolated_config, tmp_path, boxes, monkeypatch, case):
    name, saved = _install(case)
    expected_skipped = EXPECTED_SKIPPED.get(case, set())
    win = _open(tmp_path)
    view_io.load_view(name)  # parses and validates: no exception, no ViewFormatError

    # the view manager's marks, taken before the load (what the dialog shows for the open file)
    view = view_io.load_view(name)
    dialog = ViewManagerDialog(
        {c for c in win._file_columns if c != win._time_column},
        win,
        all_columns=set(win._file_columns),
        available_functions=win._function_registry.all_names(),
        registry=win._function_registry,
    )
    dialog.view_list.setCurrentRow([dialog.view_list.item(i).text() for i in range(dialog.view_list.count())].index(name))
    missing_series, missing_derived = dialog._missing(view)

    win.load_view(name)
    _wait(win)

    # --- warnings: exactly the expected ones, as one consolidated message
    assert boxes["critical"] == []
    if expected_skipped:
        assert len(boxes["warning"]) == 1, boxes["warning"]
        for item in expected_skipped:
            assert item in boxes["warning"][0]
    else:
        assert boxes["warning"] == []

    # --- structure: what was saved, minus what is expected to be skipped
    expected_plots = []
    for p in saved["plots"]:
        kept = [(s["variable"], s.get("visible", True)) for s in p["series"] if s["variable"] not in expected_skipped]
        expected_plots.append(kept)
    expected_plots = [p for i, p in enumerate(expected_plots) if p or i == 0]  # an emptied plot is dropped
    state = _state(win)
    assert state["plots"] == expected_plots
    assert state["derived"] == {
        d["name"]: d["expression"] for d in saved["derived_variables"] if d["name"] not in expected_skipped
    }
    assert state["time_column"] == (saved.get("time_column") or "time")
    assert state["downsample"] == saved["downsample"]["enabled"]
    saved_linked = [p.get("x_linked", True) for p in saved["plots"] if any(s["variable"] not in expected_skipped for s in p["series"])]
    assert state["x_linked"] == saved_linked or not saved_linked

    # --- the view manager's marks mirror what load_view skipped
    plotted = {n for p in state["plots"] for n, _ in p}
    really_skipped_series = {s["variable"] for p in saved["plots"] for s in p["series"]} - plotted
    really_skipped_derived = {d["name"] for d in saved["derived_variables"]} - set(state["derived"])
    assert missing_series == really_skipped_series
    assert missing_derived == really_skipped_derived
    assert really_skipped_derived == expected_skipped & {d["name"] for d in saved["derived_variables"]}
    red_series, red_derived = _red_marks(dialog)
    assert red_series == really_skipped_series
    assert red_derived == really_skipped_derived

    # --- round trip: save with the current code, load that into a fresh window, same state
    monkey_name = "roundtrip"
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (monkey_name, True)))
    win.save_view_dialog()
    boxes["warning"].clear()
    win2 = _open(tmp_path)
    win2.load_view(monkey_name)
    _wait(win2)
    assert boxes["warning"] == [] and boxes["critical"] == []
    assert _state(win2) == state
    resaved = json.loads((config.PARQUET_ANALYZER_DATA_DIR / f"{monkey_name}.json").read_text(encoding="utf-8"))
    assert [d["name"] for d in resaved["derived_variables"]] == list(state["derived"])
    assert resaved["time_column"] == state["time_column"]
    win.close()
    win2.close()


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0"])
def test_pre_0_3_view_has_no_time_column_and_loads_on_the_default_axis(qapp, isolated_config, tmp_path, boxes, version):
    _name, saved = _install(f"{version}/time_axis")
    assert "time_column" not in saved  # that is what makes it an "old" view
    win = _open(tmp_path)
    win.load_view("time_axis")
    _wait(win)
    assert win._time_column == "time"
    assert boxes["warning"] == [] and boxes["critical"] == []
    win.close()
