"""New plots inherit the downsample setting; the navigator follows the anchor's first series
(plots 5.43 / 50; test plan 50.3)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QApplication, QInputDialog
from waiting import wait_until

from parquet_analyzer.ui import main_window as mw_module
from parquet_analyzer.ui import messages
from parquet_analyzer.ui.main_window import MainWindow

N = 4000


def _drain(w, timeout=15.0):
    wait_until(lambda: not w._busy_sources, timeout)
    for _ in range(30):
        QApplication.processEvents()
    wait_until(lambda: not w._busy_sources, timeout)


@pytest.fixture
def path(tmp_path) -> Path:
    p = tmp_path / "s.parquet"
    times = np.datetime64("2026-01-01", "ms") + np.arange(N, dtype="int64") * np.timedelta64(10, "ms")
    x = np.linspace(0, 20 * np.pi, N)
    pq.write_table(
        pa.table({"time": pa.array(times), "a": np.sin(x), "b": 100.0 * np.cos(x), "c": np.sin(x / 3)}),
        p,
        row_group_size=500,
    )
    return p


@pytest.fixture
def win(qapp, isolated_config, path, monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(messages, name, lambda *a, **k: None)
    monkeypatch.setattr(messages, "question", lambda *a, **k: messages.QMessageBox.StandardButton.Yes)
    w = MainWindow()
    assert w.load_parquet(str(path))
    _drain(w)
    yield w
    w.close()


def _flags(w):
    return [p._downsample_enabled for p in w.plot_grid.plots]


def _nav_ymax(w) -> float:
    return float(np.nanmax(np.abs(w.navigator._curve.getData()[1])))


def _toggle_off(w):
    action = w._actions["toggle_downsample"]
    assert action.isChecked()
    action.trigger()
    assert not action.isChecked() and not w._downsample_enabled


# ------------------------------------------------------------------ downsample (50.1)


def test_add_plot_action_inherits_downsample_off(win):
    _toggle_off(win)
    win._actions["add_plot"].trigger()
    assert len(win.plot_grid.plots) == 2
    assert _flags(win) == [False, False]
    assert win.plot_grid.downsample_enabled() is False


def test_separate_inherits_downsample_off(win):
    p0 = win.plot_grid.plots[0]
    for n in ("a", "b", "c"):
        win._add_variable_to_plot(p0, n)
    _toggle_off(win)
    win.plot_grid._on_separate_requested(p0)
    assert len(win.plot_grid.plots) == 2 and _flags(win) == [False, False]


def test_separate_series_inherits_downsample_off(win):
    p0 = win.plot_grid.plots[0]
    for n in ("a", "b"):
        win._add_variable_to_plot(p0, n)
    _toggle_off(win)
    win.plot_grid._on_separate_series_requested(p0, "b")
    assert len(win.plot_grid.plots) == 2 and _flags(win) == [False, False]


def _save_view(w, monkeypatch, name):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (name, True))
    w.save_view_dialog()


def _three_plots(w):
    for n in ("a", "b", "c"):
        if n != "a":
            w.plot_grid.add_plot()
        w._add_variable_to_plot(w.plot_grid.plots[-1], n)
    assert len(w.plot_grid.plots) == 3


@pytest.mark.parametrize("off", [True, False], ids=["off", "on"])
def test_view_round_trip_keeps_downsample(win, path, monkeypatch, off):
    _three_plots(win)
    if off:
        _toggle_off(win)
    _save_view(win, monkeypatch, "v")
    assert win.load_parquet(str(path))  # reopening resets the setting (50.3 item 4)
    _drain(win)
    assert win._downsample_enabled and _flags(win) == [True]
    win.load_view("v")
    _drain(win)
    assert len(win.plot_grid.plots) == 3
    assert _flags(win) == [not off] * 3
    assert win._downsample_enabled is (not off)


def test_open_file_resets_downsample(win, path):
    win._actions["add_plot"].trigger()
    _toggle_off(win)
    assert win.load_parquet(str(path))
    _drain(win)
    assert win._downsample_enabled is True and win.plot_grid.downsample_enabled() is True
    assert all(_flags(win))


# ------------------------------------------------------------------ navigator (50.2)


def _two_plots_anchor_second(w):
    w._add_variable_to_plot(w.plot_grid.plots[0], "a")
    w.plot_grid.add_plot()
    _drain(w)
    w.plot_grid.set_plot_x_linked(w.plot_grid.plots[0], False)
    assert w.plot_grid.anchor_plot() is w.plot_grid.plots[1]


def test_drop_on_anchor_that_is_not_first_updates_navigator(win):
    _two_plots_anchor_second(win)
    anchor = win.plot_grid.anchor_plot()
    assert _nav_ymax(win) < 2  # still the picture of plots[0]'s "a"
    anchor.variableDropped.emit(anchor, "b")  # the plot's own signal, as a real drop emits it
    _drain(win)
    assert _nav_ymax(win) > 50


def test_drop_on_empty_anchor_shows_series(win):
    win.plot_grid.add_plot()
    win.plot_grid.set_plot_x_linked(win.plot_grid.plots[0], False)
    anchor = win.plot_grid.anchor_plot()
    assert anchor is win.plot_grid.plots[1]
    before = _nav_ymax(win)
    win._on_variable_dropped(anchor, "b")
    _drain(win)
    assert _nav_ymax(win) > 50 and _nav_ymax(win) != before


def test_second_series_on_anchor_does_not_rebuild_overview(win):
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    _drain(win)
    gen = win._nav_sync._gen
    win._add_variable_to_plot(win.plot_grid.plots[0], "b")
    _drain(win)
    assert win._nav_sync._gen == gen
    assert _nav_ymax(win) < 2


def test_hidden_first_series_then_new_first_visible_rebuilds(win):
    p0 = win.plot_grid.plots[0]
    win._add_variable_to_plot(p0, "a")
    _drain(win)
    p0.set_series_visible("a", False)
    _drain(win)
    gen = win._nav_sync._gen
    win._add_variable_to_plot(p0, "b")  # now the first visible series
    _drain(win)
    assert win._nav_sync._gen > gen
    assert _nav_ymax(win) > 50


def test_non_anchor_plot_series_does_not_rebuild(win):
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    win.plot_grid.add_plot()
    _drain(win)
    gen = win._nav_sync._gen
    win._add_variable_to_plot(win.plot_grid.plots[1], "b")
    _drain(win)
    assert win._nav_sync._gen == gen


def test_derived_edit_and_delete_follow_anchor(win):
    win._on_add_derived_variable("d", "a * 1")
    p0 = win.plot_grid.plots[0]
    win._add_variable_to_plot(p0, "d")
    win._add_variable_to_plot(p0, "b")
    win.plot_grid.add_plot()
    win._add_variable_to_plot(win.plot_grid.plots[1], "d")
    _drain(win)
    assert _nav_ymax(win) < 2
    win.edit_derived_variable("d", "a * 30")
    _drain(win)
    assert 20 < _nav_ymax(win) < 40
    # the derived variable only on a non-anchor plot: no rebuild
    win._on_add_derived_variable("e", "c * 1")
    win._add_variable_to_plot(win.plot_grid.plots[1], "e")
    _drain(win)
    gen = win._nav_sync._gen
    win.edit_derived_variable("e", "c * 2")
    _drain(win)
    assert win._nav_sync._gen == gen
    # delete the anchor's first series: the picture becomes the next series ("b")
    win.delete_derived_variable("d")
    _drain(win)
    assert _nav_ymax(win) > 50


# ------------------------------------------------------------------ structure


def test_structure_of_manual_refreshes():
    src = inspect.getsource(mw_module)
    assert "anchor_affected" not in src
    assert not re.search(r"plots\[0\][^\n]*\n\s*self\._nav_sync\.refresh", src)
    assert src.count("_nav_sync.refresh(") == 3
    for fn in ("_on_variable_dropped", "edit_derived_variable", "update_registry", "delete_derived_variable"):
        assert "_nav_sync.refresh(" not in inspect.getsource(getattr(MainWindow, fn))
    assert "_nav_sync.refresh(" in inspect.getsource(MainWindow._on_grid_layout_changed)


# ------------------------------------------------------------------ view load


def test_view_load_syncs_navigator_to_anchor_and_group_range(win, path, monkeypatch):
    p0 = win.plot_grid.plots[0]
    for n in ("b", "a", "c"):
        win._add_variable_to_plot(p0, n)
    win.plot_grid.add_plot()
    p1 = win.plot_grid.plots[1]
    for n in ("c", "a", "b"):
        win._add_variable_to_plot(p1, n)
    _drain(win)
    lo, hi = p0.getViewBox().viewRange()[0]
    q = (hi - lo) / 10
    win.plot_grid.set_x_range(lo + q, hi - q)
    _drain(win)
    _save_view(win, monkeypatch, "v2")
    win.load_view("v2")
    _drain(win)
    assert len(win.plot_grid.plots) == 2
    anchor = win.plot_grid.anchor_plot()
    assert anchor.visible_series_names()[0] == "b"
    assert _nav_ymax(win) > 50
    group = win._group_x_range()
    assert win.navigator.selected_range() == pytest.approx((group[1], group[2]), abs=1e-3)
