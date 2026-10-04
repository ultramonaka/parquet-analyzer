"""3D view, stage 2 (detailed_specification.md 25.3/25.4, 27.4).

OpenGL cannot render offscreen: these tests cover data preparation, thinning, colour mapping,
the matplotlib export, the availability probe / disabling, flag gating and the map-manager
toggle. Actual GL rendering and mouse rotation are checked on a real machine.
"""

from __future__ import annotations

import builtins
import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QListWidget, QMessageBox, QToolBar, QToolButton

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer import features  # noqa: E402
from parquet_analyzer.ui import main_window as mw_mod  # noqa: E402
from parquet_analyzer.ui import messages  # noqa: E402
from parquet_analyzer.ui import xy_plot_window as xy_mod  # noqa: E402
from parquet_analyzer.ui import xyz_3d_window as x3  # noqa: E402
from parquet_analyzer.ui.flow_layout import FlowLayout  # noqa: E402
from parquet_analyzer.ui.analysis_window import CANCELLED  # noqa: E402
from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402
from parquet_analyzer.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """No modal dialog may block, and the session-wide probe cache never leaks between tests."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    x3.reset_gl3d_probe()
    yield
    x3.reset_gl3d_probe()


def _wait(qapp, win, timeout=20.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


@pytest.fixture
def feature_path(tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    return str(path)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)


@pytest.fixture
def mw(qapp, isolated_config, feature_path, flag_on):
    win = MainWindow()
    win.show()
    assert win.load_parquet(feature_path)
    yield win
    win.close()


def _fake_probe_ok(monkeypatch):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (True, "ok"))


def _fetch_from(data):
    return lambda name, a, b: data[name][a:b]


# -- pure data preparation -------------------------------------------------------------


def test_normalize_maps_to_unit_interval_and_degenerate_span():
    assert np.allclose(x3.normalize([10, 15, 20], 10, 20), [0, 0.5, 1])
    assert np.allclose(x3.normalize([5, 5], 5, 5), [0.5, 0.5])


def test_nice_ticks_are_round_and_inside_range():
    ticks = x3.nice_ticks(0, 65)
    assert ticks == [0, 20, 40, 60]
    assert all(0 <= t <= 65 for t in ticks)
    assert x3.nice_ticks(-1, 1) == [-1, -0.5, 0, 0.5, 1]
    assert x3.nice_ticks(3, 3) == [3]
    assert x3.nice_ticks(float("nan"), 1) == []


def test_axis_tick_labels_have_real_values_at_normalized_positions():
    labels = x3.axis_tick_labels({"x": (0, 100), "y": (-1, 1), "z": (10, 10)})
    zs = [(p, t) for a, p, t in labels if a == "z"]
    assert zs == [(0.5, "10")]  # constant axis: one tick in the middle
    xs = {t: p for a, p, t in labels if a == "x"}
    assert xs["0"] == 0.0 and xs["100"] == 1.0 and xs["40"] == pytest.approx(0.4)
    ys = {t: p for a, p, t in labels if a == "y"}
    assert ys["0"] == pytest.approx(0.5)


def test_colors_for_uses_colormap_endpoints_and_clips():
    c = x3.colors_for([0, 5, 10, 99, -9], 0, 10)
    assert c.shape == (5, 4) and c.dtype == np.float32
    assert np.allclose(c[0], c[4]) and np.allclose(c[2], c[3])  # clipped to the ends
    assert not np.allclose(c[0], c[2])
    assert np.allclose(c[:, 3], 1.0)


def test_compute_xyz_excludes_non_finite_rows_and_counts_them():
    n = 100
    x = np.arange(n, dtype=float)
    y = x * 2
    z = x * 3
    y[3] = np.nan
    z[[7, 8]] = np.inf
    r = x3.compute_xyz(_fetch_from({"x": x, "y": y, "z": z}), "x", "y", "z", "z", 0, n)
    assert r.valid == 97 and r.excluded == 3 and r.thinned is None
    assert len(r.x) == len(r.y) == len(r.z) == len(r.c) == 97
    assert 3 not in r.x and 7 not in r.x
    assert np.array_equal(r.c, r.z)  # colour = Z
    assert r.bounds == {"x": (0.0, 99.0), "y": (0.0, 198.0), "z": (0.0, 297.0)}
    assert r.color_range == (0.0, 297.0)


def test_compute_xyz_color_by_time_uses_time_values():
    n = 50
    t = 1000.0 + np.arange(n)
    d = {k: np.arange(n, dtype=float) for k in "xyz"}
    r = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "time", 0, n, times=t)
    assert r.color == "time" and np.array_equal(r.c, t)
    assert r.color_range == (1000.0, 1049.0)


def test_compute_xyz_thins_evenly_above_limit_but_bounds_cover_all():
    n = 10_000
    d = {k: np.arange(n, dtype=float) for k in "xyz"}
    r = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 0, n, limit=1000)
    assert r.thinned == (len(r.x), 10_000) and len(r.x) <= 1000
    assert len(r.x) >= 900  # close to the limit, not a drastic over-thinning
    gaps = np.diff(r.x)
    assert gaps.min() == gaps.max()  # even stride
    assert r.bounds["x"] == (0.0, 9999.0)  # bounds are over every valid point, not the shown ones
    # at the limit exactly: no thinning
    r2 = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 0, 1000, limit=1000)
    assert r2.thinned is None and len(r2.x) == 1000


def test_compute_xyz_chunked_equals_single_pass():
    rng = np.random.default_rng(1)
    n = 5000
    d = {k: rng.normal(size=n) for k in "xyz"}
    d["y"][::97] = np.nan
    a = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 0, n, limit=700)
    b = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 0, n, limit=700, chunk_rows=333)
    assert a.valid == b.valid and a.excluded == b.excluded
    assert np.array_equal(a.x, b.x) and np.array_equal(a.z, b.z)
    assert a.bounds == b.bounds


def test_compute_xyz_empty_range_and_cancel():
    d = {k: np.arange(10, dtype=float) for k in "xyz"}
    r = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 4, 4)
    assert r.valid == 0 and len(r.x) == 0 and r.color_range is None
    assert x3.compute_xyz(_fetch_from(d), "x", "y", "z", "z", 0, 10, cancelled=lambda: True) is CANCELLED


# -- availability probe ----------------------------------------------------------------


def test_probe_is_false_offscreen_with_a_reason(qapp):
    ok, reason = x3.probe_gl3d()  # headless: no GL context can be created
    assert ok is False and reason


def test_probe_reports_import_failure(qapp, monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "OpenGL" or name.startswith("pyqtgraph.opengl"):
            raise RuntimeError("platform library missing")  # not an ImportError on purpose
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    ok, reason = x3.probe_gl3d()
    assert ok is False and "import failed" in reason and "platform library missing" in reason


def test_probe_detects_silent_context_failure(qapp, monkeypatch):
    """GLViewWidget() does not raise without GL: only the context check catches it (25.3)."""
    import pyqtgraph.opengl as gl

    created = []
    real = gl.GLViewWidget

    class NoCtx(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)

        def context(self):
            return None

    monkeypatch.setattr(gl, "GLViewWidget", NoCtx)
    ok, reason = x3.probe_gl3d()
    assert created and ok is False and "context" in reason


def test_pyqtgraph_opengl_not_imported_by_core_path():
    import subprocess

    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import parquet_analyzer.ui.main_window, parquet_analyzer.ui.xyz_3d_window;"
        "bad = [m for m in sys.modules if m == 'OpenGL' or m.startswith('pyqtgraph.opengl')];"
        "print('BAD', bad) if bad else print('CLEAN')"
    )
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                         env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"})
    assert "CLEAN" in out.stdout, out.stdout + out.stderr


# -- entry points / gating (25.5) --------------------------------------------------------


def _graphs_entries(win):
    button = win.findChild(QToolButton, "graphs_button")
    return None if button is None else [a.text() for a in button.menu().actions()]


def test_graphs_menu_entries_follow_flags(qapp, isolated_config, monkeypatch):
    def entries(xy, xyz, fft):
        for key, on in (("xy_plot", xy), ("xyz_3d", xyz), ("fft", fft)):
            monkeypatch.setitem(features._EFFECTIVE, key, on)
        return _graphs_entries(MainWindow())

    assert entries(True, False, False) == ["XY plot"]
    assert entries(True, True, False) == ["XY / 3D plot"]
    assert entries(True, True, True) == ["XY / 3D plot", "FFT"]
    assert entries(False, False, True) == ["FFT"]
    assert entries(False, False, False) is None  # nothing to offer: no button at all


def test_graphs_button_replaces_the_standalone_toolbar_buttons(qapp, isolated_config, monkeypatch):
    for key in ("xy_plot", "xyz_3d", "fft"):
        monkeypatch.setitem(features._EFFECTIVE, key, True)
    win = MainWindow()
    toolbar = win.findChild(QToolBar, "main_toolbar")
    button = win.findChild(QToolButton, "graphs_button")
    assert button is not None and button.objectName() == "graphs_button"
    assert toolbar.widgetForAction(toolbar.actions()[0]) is not None
    assert win._actions["xy_plot"] not in toolbar.actions()  # lives in the menu only
    assert win._actions["fft"] not in toolbar.actions()
    assert "xyz_3d" not in win._actions
    assert win._actions["xy_plot"].text() == "XY / 3D plot"
    assert win.settings.shortcuts.get("xy_plot", "") == ""  # the shortcut id is kept


def test_menu_entry_opens_the_window_and_keeps_its_shortcut(mw, monkeypatch):
    from PySide6.QtGui import QKeySequence

    monkeypatch.setitem(features._EFFECTIVE, "xy_plot", True)
    from parquet_analyzer.io import settings as settings_io

    mw.settings.shortcuts["xy_plot"] = "Ctrl+Shift+Y"
    settings_io.save_settings(mw.settings)
    win = MainWindow()
    action = win._actions["xy_plot"]
    assert action.shortcut() == QKeySequence("Ctrl+Shift+Y")
    assert action in win.actions()  # live although it sits in a closed menu
    assert "xy_plot" in SettingsDialog(win.settings)._shortcut_edits
    assert mw.findChild(QToolButton, "graphs_button").menu().actions()[0].trigger() is None
    assert len(mw._analysis_windows) == 1  # the menu entry opened a window


def test_old_xyz_3d_shortcut_in_settings_is_ignored_gracefully(qapp, isolated_config):
    from parquet_analyzer.io import settings as settings_io

    path = settings_io.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"shortcuts": {"xyz_3d": "Ctrl+3", "xy_plot": "Ctrl+Y", "home": "H"}}), encoding="utf-8")
    loaded = settings_io.load_settings()
    assert "xyz_3d" not in loaded.shortcuts
    assert loaded.shortcuts["xy_plot"] == "Ctrl+Y"
    win = MainWindow()  # starts fine
    assert "xyz_3d" not in win._actions and "xyz_3d" not in win.settings.shortcuts
    settings_io.save_settings(win.settings)
    assert "xyz_3d" not in json.loads(path.read_text(encoding="utf-8"))["shortcuts"]
    assert "xyz_3d" not in settings_io.DEFAULT_SHORTCUTS


def test_settings_dialog_has_no_3d_shortcut_row(qapp, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)
    assert "xyz_3d" not in SettingsDialog(MainWindow().settings)._shortcut_edits


def test_open_without_file_asks_to_open_one(qapp, isolated_config, flag_on, monkeypatch):
    shown = []
    monkeypatch.setattr(messages, "information", lambda parent, title, text, *a, **k: shown.append(text))
    win = MainWindow()
    assert win.open_xy_plot() is None and len(shown) == 1
    assert x3.gl3d_status() is None  # nothing probed yet


# -- the 2D/3D toggle in the XY window (25.5) -------------------------------------------


def _toggle_3d(w):
    QTest.mouseClick(w._mode_buttons["3d"], Qt.MouseButton.LeftButton)


def _configure_3d(w, x="sine", y="cosine", z="ramp", color="z"):
    for widget in (w._x_combo, w._y_list, w._z_combo, w._color_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_list.clearSelection()
    for i in range(w._y_list.count()):
        if w._y_list.item(i).text() == y:
            w._y_list.item(i).setSelected(True)
    w._z_combo.setCurrentText(z)
    w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    for widget in (w._x_combo, w._y_list, w._z_combo, w._color_combo):
        widget.blockSignals(False)
    w._apply_color_constraints()


def _open_window(qapp, mw, monkeypatch, x="sine", y="cosine", z="ramp", color="z"):
    """An XY window switched to 3D with the given axes, computed (GL item creation works
    offscreen; only drawing does not)."""
    _fake_probe_ok(monkeypatch)
    w = mw.open_xy_plot()
    assert w is not None
    _toggle_3d(w)
    _wait(qapp, w)
    _configure_3d(w, x, y, z, color)
    w.recompute(keep_range=True)
    _wait(qapp, w)
    return w


def test_toggle_hidden_while_flag_off_and_shown_when_on(qapp, mw, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", False)
    w = mw.open_xy_plot()
    assert w._mode_box.isHidden() and w.mode == "2d"
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)
    _fake_probe_ok(monkeypatch)
    w2 = mw.open_xy_plot()
    assert not w2._mode_box.isHidden() and w2._mode_buttons["3d"].isEnabled()


def test_failed_probe_disables_the_toggle_with_the_reason_and_logs_once(mw, monkeypatch):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (False, "no GL here"))
    shown, ops = [], []
    monkeypatch.setattr(messages, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    monkeypatch.setattr(xy_mod, "log_op", lambda name, **kw: ops.append((name, kw)))
    w = mw.open_xy_plot()
    b3 = w._mode_buttons["3d"]
    assert not b3.isEnabled() and "no GL here" in b3.toolTip()
    QTest.mouseClick(b3, Qt.MouseButton.LeftButton)  # a disabled button does nothing
    assert w.mode == "2d" and w._view3d is None
    w._set_mode("3d")  # and the code path refuses as well
    assert w.mode == "2d" and w._mode_buttons["2d"].isChecked()
    assert shown == []  # the reason is a tooltip, not a dialog
    mw.open_xy_plot()
    assert [n for n, _ in ops].count("xyz_3d_unavailable") == 1  # probed once per session
    assert ("xyz_3d_unavailable", {"reason": "no GL here"}) in ops


def test_import_error_disables_the_toggle(mw, monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "OpenGL":
            raise ImportError("No module named 'OpenGL'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    w = mw.open_xy_plot()
    b3 = w._mode_buttons["3d"]
    assert not b3.isEnabled() and "import failed" in b3.toolTip()


def test_toggle_carries_selections_across(qapp, mw, monkeypatch):
    _fake_probe_ok(monkeypatch)
    ops = []
    monkeypatch.setattr(xy_mod, "log_op", lambda name, **kw: ops.append((name, kw)))
    w = mw.open_xy_plot()
    _configure_2d_multi(w)
    assert w.selected_y() == ["cosine", "ramp"]
    _toggle_3d(w)
    _wait(qapp, w)
    assert w.mode == "3d"
    assert w._x_combo.currentText() == "sine" and w.selected_y() == ["cosine"]  # first of several
    assert w._z_combo.currentText() == "offset_sine" and w._z_combo.isEnabled()  # Z = third axis now
    assert w._color_combo.currentData() == "z"  # single colour is not offered in 3D
    assert not w._color_combo.model().item(0).isEnabled()
    assert w._y_list.selectionMode() == QListWidget.SelectionMode.SingleSelection
    assert w._display_group.isHidden() and w._view3d is not None and w._stack.currentWidget() is w._view3d
    assert w._result is not None and isinstance(w._result, x3.Xyz3DResult)
    # back to 2D: X/Y/Z stay (Y is the one that was kept)
    QTest.mouseClick(w._mode_buttons["2d"], Qt.MouseButton.LeftButton)
    _wait(qapp, w)
    assert w.mode == "2d" and w._x_combo.currentText() == "sine" and w.selected_y() == ["cosine"]
    assert w._z_combo.currentText() == "offset_sine"
    assert not w._display_group.isHidden() and w._stack.currentIndex() == 0
    assert w._color_combo.model().item(0).isEnabled()
    assert isinstance(w._result, xy_mod.XYResult)
    assert [kw["mode"] for n, kw in ops if n == "xy_plot_mode"] == ["3d", "2d"]


def _configure_2d_multi(w):
    w._x_combo.blockSignals(True)
    w._y_list.blockSignals(True)
    w._x_combo.setCurrentText("sine")
    w._y_list.clearSelection()
    for i in range(w._y_list.count()):
        if w._y_list.item(i).text() in ("cosine", "ramp"):
            w._y_list.item(i).setSelected(True)
    w._x_combo.blockSignals(False)
    w._y_list.blockSignals(False)


def test_open_in_3d_computes_with_prefilled_selection(qapp, mw, monkeypatch):
    _fake_probe_ok(monkeypatch)
    ops = []
    monkeypatch.setattr(xy_mod, "log_op", lambda name, **kw: ops.append((name, kw)))
    w = mw.open_xy_plot("3d")
    _wait(qapp, w)
    assert w.mode == "3d" and w._result is not None and w._result.valid > 0  # not a blank window
    assert w._x_combo.currentText() and w._z_combo.currentText() and w.selected_y()
    opened = [kw for n, kw in ops if n == "xy_plot_open"]
    assert len(opened) == 1 and opened[0]["mode"] == "3d"
    assert [n for n, _ in ops].index("xy_plot_open") < [n for n, _ in ops].index("xyz_3d_compute")
    assert "xyz_3d_open" not in [n for n, _ in ops]
    # asking for 3D where it is unavailable falls back to 2D
    monkeypatch.setattr(x3, "_PROBE", (False, "nope"))
    assert mw.open_xy_plot("3d").mode == "2d"


def test_open_logs_mode_2d_by_default(qapp, mw, monkeypatch):
    ops = []
    monkeypatch.setattr(xy_mod, "log_op", lambda name, **kw: ops.append((name, kw)))
    mw.open_xy_plot()
    opened = [kw for n, kw in ops if n == "xy_plot_open"]
    assert len(opened) == 1 and opened[0]["mode"] == "2d"


def test_window_computes_and_feeds_the_view(qapp, mw, monkeypatch):
    w = _open_window(qapp, mw, monkeypatch)
    r = w._result
    assert r.valid == 6000 and r.thinned is None
    scene = w._view3d.scene
    assert scene["kind"] == "scatter" and scene["pos"].shape == (6000, 3)
    assert scene["pos"].min() >= 0.0 and scene["pos"].max() <= 1.0  # normalized to the unit cube
    assert scene["labels"] == ("sine", "cosine", "ramp")
    assert "6000" in w.status_text()
    assert w in mw._analysis_windows


def test_window_thins_above_point_limit_and_says_so(qapp, mw, monkeypatch):
    monkeypatch.setattr(x3, "XYZ_POINT_LIMIT", 1000)
    w = _open_window(qapp, mw, monkeypatch)
    assert w._result.thinned is not None and w._result.thinned[1] == 6000
    assert len(w._view3d.scene["pos"]) == w._result.thinned[0] <= 1000
    assert "6000" in w.status_text() and str(w._result.thinned[0]) in w.status_text()


def test_window_logs_compute_and_mode(qapp, mw, monkeypatch):
    ops = []
    monkeypatch.setattr(xy_mod, "log_op", lambda name, **kw: ops.append(name))
    _open_window(qapp, mw, monkeypatch)
    assert {"xy_plot_open", "xy_plot_mode", "xyz_3d_compute"} <= set(ops)


# -- time as an axis (25.5) -----------------------------------------------------------------


def test_time_column_is_a_candidate_for_every_axis(qapp, mw):
    w = mw.open_xy_plot()
    tc = mw._time_column
    for combo in (w._x_combo, w._z_combo):
        assert combo.findText(tc) >= 0
    assert w._y_list.findItems(tc, Qt.MatchFlag.MatchExactly)
    # defaults skip it: the first window does not start on the clock
    assert w._x_combo.currentText() != tc and w._z_combo.currentText() != tc


def test_time_as_x_in_2d_computes_and_ticks_are_local_time(qapp, mw):
    tc = mw._time_column
    w = mw.open_xy_plot()
    _configure_2d(w, tc, ["sine"])
    w.recompute()
    _wait(qapp, w)
    r = w._result
    assert isinstance(r, xy_mod.XYResult) and r.valid["sine"] == 6000 and r.time_name == tc
    assert np.allclose(r.scatter[0].x, mw._time_values[r.row_start:r.row_end])
    axis = w._plot.getAxis("bottom")
    assert axis.time_mode and not w._plot.getAxis("left").time_mode
    lo, hi = r.x_bounds
    labels = axis.tickStrings([lo, (lo + hi) / 2], 1.0, (hi - lo) / 5)
    assert labels == [xy_mod.format_time_tick(lo, hi - lo), xy_mod.format_time_tick((lo + hi) / 2, hi - lo)]
    assert all(":" in lab or "-" in lab for lab in labels)  # a clock, never "1.767e9"
    # a normal axis next time
    _configure_2d(w, "sine", ["cosine"])
    w.recompute()
    _wait(qapp, w)
    assert not w._plot.getAxis("bottom").time_mode
    assert w._plot.getAxis("bottom").tickStrings([0.5], 1.0, 0.5) == ["0.5"]


def test_time_as_y_and_cursor_highlight_reads_time(qapp, mw):
    tc = mw._time_column
    w = mw.open_xy_plot()
    _configure_2d(w, "sine", [tc])
    w.recompute()
    _wait(qapp, w)
    assert w._plot.getAxis("left").time_mode and not w._plot.getAxis("bottom").time_mode
    t0 = float(mw._time_values[100])
    w._on_cursors_changed([t0])
    pts = w.cursor_highlight_points()
    assert len(pts) == 1 and pts[0][1] == pytest.approx(t0)


def test_time_as_colour_variable_gives_time_ticks_on_the_colour_bar(qapp, mw):
    tc = mw._time_column
    w = mw.open_xy_plot()
    _configure_2d(w, "sine", ["cosine"], color="z", z=tc)
    w.recompute()
    _wait(qapp, w)
    assert w._result.color == "z" and w._result.z_name == tc
    axis = w._colorbar.axis
    lo, hi = w._result.color_range
    assert axis.tickStrings([lo], 1.0, 1.0) == [xy_mod.format_time_tick(lo, hi - lo)]


def test_time_axis_job_closure_holds_no_ndarray(qapp, mw):
    w = mw.open_xy_plot()
    _configure_2d(w, mw._time_column, ["sine"])
    job = w.make_job(0, 100, lambda: False)
    cells = [c.cell_contents for c in (job.__closure__ or ())]
    assert not [c for c in cells if isinstance(c, np.ndarray)] or all(
        isinstance(c, np.ndarray) and c is mw._time_values for c in cells if isinstance(c, np.ndarray)
    )  # at most the main window's own (long-lived) time array, as before 25.5


def test_time_as_3d_axis_ticks_hover_and_export_are_local_time(qapp, mw, monkeypatch, tmp_path):
    tc = mw._time_column
    w = _open_window(qapp, mw, monkeypatch, x=tc, y="cosine", z="sine", color="z")
    r = w._result
    assert r.valid == 6000 and r.time_name == tc
    v = w._view3d
    assert v.scene["time_axes"] == ("x",) and v.scene["labels"][0] == tc
    lo, hi = v.scene["bounds"]["x"]
    from pyqtgraph.opengl import GLTextItem

    texts = [i.text for i in v._frame_items if isinstance(i, GLTextItem)]
    x_ticks = [t for a, _p, t in x3.axis_tick_labels(v.scene["bounds"], ("x",)) if a == "x"]
    assert x_ticks and all(t in texts for t in x_ticks)
    assert all(":" in t or "-" in t for t in x_ticks)
    # hover value of a time axis reads as a clock
    v.resize(600, 600)
    v.show()
    qapp.processEvents()
    i = 10
    text = v.hover_label_text(i)
    assert text.startswith(f"{tc}: " + xy_mod.format_time_tick(float(v.scene["real"][i][0]), hi - lo))
    # image export: the mplot3d x axis shows the same clock
    seen = {}
    real = xy_mod.build_xyz_figure

    def spy(*a, **k):
        fig = real(*a, **k)
        seen["fig"] = fig
        return fig

    monkeypatch.setattr(xy_mod, "build_xyz_figure", spy)
    out = tmp_path / "t.png"
    assert w.export_image(str(out))
    ax = [a for a in seen["fig"].axes if a.name == "3d"][0]
    seen["fig"].canvas.draw()
    labels = [t.get_text() for t in ax.get_xticklabels() if t.get_text()]
    assert labels and all(":" in lab or "-" in lab for lab in labels)


def test_time_as_2d_axis_export_is_local_time(qapp, mw, monkeypatch, tmp_path):
    tc = mw._time_column
    w = mw.open_xy_plot()
    _configure_2d(w, tc, ["sine"])
    w.recompute()
    _wait(qapp, w)
    fig = xy_mod.build_xy_figure(w._result, ["stamp"], (800, 600))
    fig.canvas.draw()
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels() if t.get_text()]
    assert labels and all(":" in lab or "-" in lab for lab in labels)
    ylabels = [t.get_text() for t in fig.axes[0].get_yticklabels() if t.get_text()]
    assert ylabels and not any(":" in lab for lab in ylabels)  # only the time axis changes


def _configure_2d(w, x, ys, color=None, z=None):
    for widget in (w._x_combo, w._y_list, w._color_combo, w._z_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_list.clearSelection()
    for i in range(w._y_list.count()):
        if w._y_list.item(i).text() in ys:
            w._y_list.item(i).setSelected(True)
    if color:
        w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    if z:
        w._z_combo.setCurrentText(z)
    for widget in (w._x_combo, w._y_list, w._color_combo, w._z_combo):
        widget.blockSignals(False)
    w._apply_color_constraints()


# -- settings row (25.5) ---------------------------------------------------------------------


def test_settings_are_one_wrapping_row(qapp, mw, monkeypatch):
    _fake_probe_ok(monkeypatch)
    w = mw.open_xy_plot()
    w.resize(1500, 700)
    w.show()
    qapp.processEvents()
    panel = w._settings_panel
    groups = [w._x_combo.parentWidget(), w._y_group, w._z_group, w._color_combo.parentWidget(), w._mode_box,
              w._display_group]
    tops = {g.mapTo(panel, QPoint(0, 0)).y() for g in groups}
    assert len(tops) == 1  # X, Y, Z, colour, 2D/3D, display: all on one row (not a vertical form)
    xs = [g.mapTo(panel, QPoint(0, 0)).x() for g in groups]
    assert xs == sorted(xs)
    w.resize(420, 700)
    qapp.processEvents()
    assert len({g.mapTo(panel, QPoint(0, 0)).y() for g in groups if not g.isHidden()}) > 1  # wrapped
    assert panel.height() >= panel.layout().heightForWidth(panel.width()) - 1  # the panel grew to fit


def test_flow_layout_hides_empty_items_and_wraps(qapp):
    from PySide6.QtWidgets import QWidget as W

    host = W()
    flow = FlowLayout(host, 0, 10, 5)
    boxes = []
    for _ in range(4):
        b = QLabel("x" * 10)
        boxes.append(b)
        flow.addWidget(b)
    boxes[1].hide()
    host.resize(10_000, 100)
    host.show()
    qapp.processEvents()
    ys = {b.y() for i, b in enumerate(boxes) if i != 1}
    assert len(ys) == 1
    narrow = flow.heightForWidth(boxes[0].sizeHint().width() * 2 + 10)
    wide = flow.heightForWidth(10_000)
    assert narrow > wide


def test_view_reports_angles_and_surface_shape(qapp):
    v = x3.Xyz3DView()
    elev, azim = v.view_angles()
    assert (elev, azim) == (25.0, -60.0)
    v.view.setCameraPosition(elevation=10, azimuth=75)
    assert v.view_angles() == (10.0, 75.0)
    xs, ys = [0, 1, 4], [0, 10]
    z = np.array([[0, 1, 2], [3, np.nan, 5.0]])  # (len(y), len(x)); NaN must not break it
    v.set_surface(xs, ys, z)
    s = v.scene
    assert s["kind"] == "surface" and s["z"].shape == (3, 2)  # pyqtgraph wants (nx, ny)
    assert np.allclose(s["x"], [0, 0.25, 1]) and np.allclose(s["y"], [0, 1])
    assert s["colors"].shape == (6, 4)  # flat per-vertex colours, as MeshData needs
    assert np.isfinite(s["z"]).all() and s["bounds"]["z"] == (0.0, 5.0)
    with pytest.raises(ValueError):
        v.set_surface(xs, ys, z.T)


# -- export ----------------------------------------------------------------------------


def _result(color="z", n=300):
    t = 1_767_225_600.0 + np.arange(n)  # 2026-01-01 UTC-ish epoch seconds
    d = {"x": np.sin(np.arange(n) / 10), "y": np.cos(np.arange(n) / 10), "z": np.arange(n, dtype=float)}
    return x3.compute_xyz(_fetch_from(d), "x", "y", "z", color, 0, n, times=t)


@pytest.mark.parametrize("color", ["z", "time"])
def test_export_figure_is_mplot3d_with_view_angles_and_rasterized(color):
    fig = x3.build_xyz_figure(_result(color), ["stamp A", "X: x  Y: y  Z: z"], (800, 600), 33.0, 117.0)
    ax3 = [a for a in fig.axes if a.name == "3d"]
    assert len(ax3) == 1
    ax = ax3[0]
    assert (ax.elev, ax.azim) == (33.0, 117.0)
    assert ax.collections[0].get_rasterized()
    assert ax.get_xlabel() == "x" and ax.get_ylabel() == "y" and ax.get_zlabel() == "z"
    texts = [t.get_text() for t in fig.texts]
    assert any("X: x  Y: y  Z: z" in t for t in texts)
    cbar_ax = [a for a in fig.axes if a is not ax][0]
    if color == "time":
        fig.canvas.draw()
        labels = [t.get_text() for t in cbar_ax.get_yticklabels() if t.get_text()]
        assert labels and all(":" in lab or "-" in lab for lab in labels)  # local-time labels
    else:
        assert cbar_ax.get_ylabel() == "z"


def test_window_export_uses_current_gl_angles_and_stamp_line(qapp, mw, monkeypatch, tmp_path):
    w = _open_window(qapp, mw, monkeypatch)
    w._view3d.view.setCameraPosition(elevation=12, azimuth=44)
    seen = {}
    real = x3.build_xyz_figure

    def spy(r, lines, size, elev, azim):
        seen.update(lines=lines, angles=(elev, azim))
        return real(r, lines, size, elev, azim)

    monkeypatch.setattr(xy_mod, "build_xyz_figure", spy)
    path = tmp_path / "out.png"
    assert w.export_image(str(path)) and path.stat().st_size > 1000
    assert seen["angles"] == (12.0, 44.0)
    assert any(line == "X: sine  Y: cosine  Z: ramp" for line in seen["lines"])
    # the variable line sits before the "exported at" line (25.1)
    assert seen["lines"].index("X: sine  Y: cosine  Z: ramp") == len(seen["lines"]) - 2


def test_export_with_no_points_reports_nothing_to_export(qapp, mw, monkeypatch, tmp_path):
    w = _open_window(qapp, mw, monkeypatch)
    w._result = x3.compute_xyz(_fetch_from({k: np.arange(3.0) for k in "xyz"}), "x", "y", "z", "z", 0, 0)
    shown = []
    monkeypatch.setattr(messages, "information", lambda parent, title, text, *a, **k: shown.append(text))
    assert w.export_image(str(tmp_path / "e.png")) is False and shown


# -- map manager 3D toggle (27.4) ---------------------------------------------------------

MAP = "x,0,10\n0,0,10\n10,100,110\n"
CURVE = "x,y\n0,0\n10,100\n20,400\n"


def _manager(mw, tmp_path):
    mw.open_map_manager()
    mgr = mw._map_manager
    (tmp_path / "mp.csv").write_text(MAP, encoding="utf-8")
    (tmp_path / "cv.csv").write_text(CURVE, encoding="utf-8")
    assert mgr.do_import(str(tmp_path / "mp.csv"), "mp") and mgr.do_import(str(tmp_path / "cv.csv"), "cv")
    return mgr


def test_map_toggle_hidden_when_flag_off(mw, tmp_path, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", False)
    mgr = _manager(mw, tmp_path)
    mgr.map_list.setCurrentRow(sorted(mw.function_registry.maps).index("mp"))
    assert mgr.toggle_3d.isHidden()


def test_map_toggle_only_for_maps_and_shows_surface(mw, tmp_path, monkeypatch):
    _fake_probe_ok(monkeypatch)
    mgr = _manager(mw, tmp_path)
    names = sorted(mw.function_registry.maps)
    mgr.map_list.setCurrentRow(names.index("cv"))
    assert mgr.toggle_3d.isHidden()  # curves have no surface
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert not mgr.toggle_3d.isHidden()
    mgr.toggle_3d.setChecked(True)
    assert mgr.view3d is not None
    scene = mgr.view3d.scene
    assert scene["kind"] == "surface" and scene["z"].shape == (2, 2)
    assert scene["bounds"]["z"] == (0.0, 110.0)
    mgr.toggle_3d.setChecked(False)
    QApplication.processEvents()  # a widget added to a shown layout is shown on the next event pass
    assert mgr.view3d is None and mgr.plot_raw.isVisible() and mgr.plot_raw.window() is mgr  # the 2D plots are back
    # toggled on, then switching to a curve drops the 3D view and unchecks it
    mgr.toggle_3d.setChecked(True)
    mgr.map_list.setCurrentRow(names.index("cv"))
    assert mgr.view3d is None and not mgr.toggle_3d.isChecked()


def test_map_toggle_disappears_when_probe_fails(mw, tmp_path, monkeypatch):
    monkeypatch.setattr(x3, "probe_gl3d", lambda: (False, "no GL"))
    mgr = _manager(mw, tmp_path)
    mgr.map_list.setCurrentRow(sorted(mw.function_registry.maps).index("mp"))
    assert not mgr.toggle_3d.isHidden()  # unknown until the first press
    mgr.toggle_3d.setChecked(True)
    assert mgr.toggle_3d.isHidden() and not mgr.toggle_3d.isChecked() and mgr.view3d is None
    assert x3.gl3d_status() == (False, "no GL")  # one shared verdict for the session (the XY window too)
    mgr.map_list.setCurrentRow(sorted(mw.function_registry.maps).index("cv"))
    mgr.map_list.setCurrentRow(sorted(mw.function_registry.maps).index("mp"))
    assert mgr.toggle_3d.isHidden()



# -- review fixes -------------------------------------------------------------------------


def test_main_sets_share_gl_contexts_before_creating_the_app(monkeypatch):
    from PySide6.QtCore import QCoreApplication, Qt

    from parquet_analyzer import __main__ as entry

    order = []
    monkeypatch.setattr(QCoreApplication, "setAttribute",
                        staticmethod(lambda attr, *a: order.append(("attr", attr))))

    class StopHere(Exception):
        pass

    def fake_app(*a, **k):
        order.append(("app",))
        raise StopHere

    monkeypatch.setattr(entry, "QApplication", fake_app)
    monkeypatch.setattr(entry, "_configure_logging", lambda debug: None)
    with pytest.raises(StopHere):
        entry.main([])
    assert order == [("attr", Qt.ApplicationAttribute.AA_ShareOpenGLContexts), ("app",)]


def test_axis_lines_are_translucent_not_additive(qapp):
    v = x3.Xyz3DView()
    v.set_scatter([0, 1], [0, 1], [0, 1], np.ones((2, 4)))
    from pyqtgraph.opengl import GLLinePlotItem

    lines = [i for i in v._frame_items if isinstance(i, GLLinePlotItem)]
    assert lines
    for item in lines:  # the default "additive" blend makes black lines vanish on white
        from OpenGL import GL

        # "additive" blends with (SRC_ALPHA, ONE); ours is ordinary alpha blending
        assert item._GLGraphicsItem__glOpts.get("glBlendFunc") != (GL.GL_SRC_ALPHA, GL.GL_ONE)


def test_camera_distance_scales_with_aspect_and_keeps_zoom(qapp):
    v = x3.Xyz3DView()
    v.show()
    _size_view(v, 400, 400)
    base = float(v.view.opts["distance"])
    _size_view(v, 1200, 300)  # wide and short (width/height = 4)
    assert float(v.view.opts["distance"]) == pytest.approx(base * 4, rel=0.05)
    v.view.opts["distance"] = 3.0  # user zoomed
    _size_view(v, 1200, 600)
    assert float(v.view.opts["distance"]) < 3.0 * 4  # scaled relative to the zoom, not reset


# ---------------------------------------------------------------------------
# 25.3 (2026-10-03): home / view presets / hover readout / axes
# ---------------------------------------------------------------------------

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QVector4D  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def _size_view(v, w, h):
    """Resize the component so that its GL view (not the whole widget with its button row and
    readout label) is w x h."""
    from PySide6.QtWidgets import QApplication

    v.resize(w, h + 200)
    QApplication.processEvents()
    v.resize(w, h + (v.height() - v.view.height()))
    QApplication.processEvents()
    assert (v.view.width(), v.view.height()) == (w, h)


def _shown_view(w=400, h=300):
    v = x3.Xyz3DView()
    v.show()
    _size_view(v, w, h)
    return v


def _screen_of(v, pos):
    """Screen positions under the view's real projection x view matrices."""
    w, h = v.view.width(), v.view.height()
    reg = (0, 0, w, h)
    proj = x3.matrix_to_numpy(v.view.projectionMatrix(reg, reg))
    vm = x3.matrix_to_numpy(v.view.viewMatrix())
    return x3.project_points(pos, proj, vm, w, h)


def _ops(monkeypatch):
    ops = []
    monkeypatch.setattr(x3, "log_op", lambda name, **kw: ops.append((name, kw)))
    return ops


def _home_key():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier)


def test_project_points_known_matrices():
    proj = np.eye(4)  # x, y straight to NDC, w = 1
    xy, front = x3.project_points([[0, 0, 0], [1, 1, 0], [-1, -1, 0]], proj, np.eye(4), 200, 100)
    assert np.allclose(xy, [[100, 50], [200, 0], [0, 100]])  # NDC up = screen up (y flips)
    assert front.all()
    behind = np.eye(4)
    behind[3] = [0, 0, 0, -1]  # w = -1: behind the camera
    _xy, front = x3.project_points([[0, 0, 0]], proj, behind, 10, 10)
    assert not front[0]


def test_pick_nearest_radius_and_behind():
    proj, view = np.eye(4), np.eye(4)
    pos = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]])  # screen (100, 50) and (150, 50) in 200x100
    assert x3.pick_nearest(pos, proj, view, 200, 100, 105, 52) == 0
    assert x3.pick_nearest(pos, proj, view, 200, 100, 140, 50) == 1
    assert x3.pick_nearest(pos, proj, view, 200, 100, 100 + 13, 50) is None  # outside 12 px
    assert x3.pick_nearest(pos, proj, view, 200, 100, 100 + 11, 50) == 0
    assert x3.pick_nearest(np.empty((0, 3)), proj, view, 200, 100, 0, 0) is None
    behind = np.eye(4)
    behind[3] = [0, 0, 0, -1]
    assert x3.pick_nearest(pos, proj, behind, 200, 100, 100, 50) is None  # points behind the camera never match


def test_matrix_to_numpy_matches_qt(qapp):
    v = _shown_view()
    vm = v.view.viewMatrix()
    q = vm.map(QVector4D(0.2, 0.7, 0.4, 1.0))
    assert np.allclose(x3.matrix_to_numpy(vm) @ [0.2, 0.7, 0.4, 1.0], [q.x(), q.y(), q.z(), q.w()], atol=1e-5)


def test_reset_camera_home_params_and_aspect(qapp):
    v = _shown_view(800, 400)
    v.view.setCameraPosition(distance=1.0, elevation=3, azimuth=4)
    v.reset_camera()
    assert v.view_angles() == (25.0, -60.0)
    assert float(v.view.opts["distance"]) == pytest.approx(x3._BASE_DISTANCE * 2.0)


def test_presets_camera_params_and_log(qapp, monkeypatch):
    ops = _ops(monkeypatch)
    v = _shown_view(600, 600)
    expect = {"xy": (90.0, -90.0), "yz": (0.0, 0.0), "zx": (0.0, -90.0), "home": (25.0, -60.0)}
    for name, angles in expect.items():
        v.apply_preset(name)
        assert v.view_angles() == angles
        assert float(v.view.opts["distance"]) == pytest.approx(x3._BASE_DISTANCE)  # the home distance
    assert [kw["preset"] for n, kw in ops if n == "xyz_3d_view"] == ["xy", "yz", "zx", "home"]


def test_preset_screen_orientations(qapp):
    """The documented directions hold in the actual projection (also confirmed by rendering)."""
    v = _shown_view(600, 600)
    pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def deltas(preset):
        v.apply_preset(preset)
        xy, _ = _screen_of(v, pts)
        return xy[1] - xy[0], xy[2] - xy[0], xy[3] - xy[0]  # +X, +Y, +Z on screen (y grows downward)

    dx, dy, _ = deltas("xy")
    assert dx[0] > 50 and abs(dx[1]) < 5  # X right
    assert dy[1] < -50 and abs(dy[0]) < 5  # Y up
    _, dy, dz = deltas("yz")
    assert dy[0] > 50 and abs(dy[1]) < 5  # Y right
    assert dz[1] < -50 and abs(dz[0]) < 5  # Z up
    dx, _, dz = deltas("zx")
    assert dx[0] > 50 and abs(dx[1]) < 5  # X right
    assert dz[1] < -50 and abs(dz[0]) < 5  # Z up


def test_view_buttons_wired_in_a_normal_row_above_the_gl_view(qapp, monkeypatch):
    ops = _ops(monkeypatch)
    v = _shown_view()
    qapp.processEvents()
    assert list(v._preset_buttons) == ["home", "xy", "yz", "zx"]
    for name in ("xy", "yz", "zx"):
        QTest.mouseClick(v._preset_buttons[name], Qt.MouseButton.LeftButton)
        assert v.view_angles() == x3.VIEW_PRESETS[name]
    QTest.mouseClick(v._preset_buttons["home"], Qt.MouseButton.LeftButton)
    assert v.view_angles() == (25.0, -60.0)
    assert [kw["preset"] for n, kw in ops if n == "xyz_3d_view"] == ["xy", "yz", "zx", "home"]
    # 25.5: child widgets laid over a QOpenGLWidget do not render their text on a real display,
    # so the buttons are siblings of the GL view in a row above it, never its children
    view_top = v.view.mapTo(v, QPoint(0, 0)).y()
    for b in v._preset_buttons.values():
        assert not v.view.isAncestorOf(b) and b.parentWidget() is v
        assert b.mapTo(v, QPoint(0, 0)).y() + b.height() <= view_top  # above, not overlapping
        assert b.text()  # a real label
    xs = [b.x() for b in v._preset_buttons.values()]
    assert xs == sorted(xs) and xs[0] < 20


def test_hover_label_is_a_normal_label_below_the_gl_view(qapp):
    v = _shown_view(600, 600)
    qapp.processEvents()
    lab = v._hover_label
    assert not v.view.isAncestorOf(lab) and lab.parentWidget() is v
    assert lab.mapTo(v, QPoint(0, 0)).y() >= v.view.mapTo(v, QPoint(0, v.view.height())).y()
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)), labels=("A", "B", "C"))
    xy, _ = _screen_of(v, v.scene["pos"])
    v.hover_at(*xy[1])
    assert lab.text() == "A: 1   B: 1   C: 1" and lab.isVisible()
    v.hover_at(0.0, 0.0)
    assert lab.text() == "" and v.hover_text() == ""


def test_double_click_and_home_key_go_home(qapp, monkeypatch):
    ops = _ops(monkeypatch)
    v = _shown_view()
    v.apply_preset("xy")
    ops.clear()
    QTest.mouseDClick(v.view, Qt.MouseButton.LeftButton, pos=QPoint(200, 150))  # real Qt routing
    assert v.view_angles() == (25.0, -60.0)
    assert ("xyz_3d_home", {}) in ops
    v.apply_preset("yz")
    ops.clear()
    qapp.sendEvent(v.view, _home_key())  # through the GL view's event filter
    assert v.view_angles() == (25.0, -60.0)
    assert ("xyz_3d_home", {}) in ops
    v.apply_preset("zx")
    qapp.sendEvent(v, _home_key())  # the key reaching the container
    assert v.view_angles() == (25.0, -60.0)
    v.apply_preset("xy")
    qapp.sendEvent(v.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))
    assert v.view_angles() == (90.0, -90.0)  # other keys do nothing


def test_scatter_hover_picks_nearest_and_formats_values(qapp, monkeypatch):
    ops = _ops(monkeypatch)
    v = _shown_view(600, 600)
    v.set_scatter([0.0, 10.0, 20.0], [100.0, 200.0, 300.0], [-1.0, 0.5, 2.0], np.ones((3, 4)),
                  labels=("Spd", "Trq", "Tmp"))
    xy, _ = _screen_of(v, v.scene["pos"])
    assert v.hover_at(xy[1][0] + 4, xy[1][1] - 3) == 1
    assert v.hover_text() == "Spd: 10   Trq: 200   Tmp: 0.5"  # real values, not normalized
    assert v._hover_items and all(it in v.view.items for it in v._hover_items)
    assert v.hover_at(xy[1][0] + 4, xy[1][1] + 40) is None  # more than 12 px from every point
    assert v.hover_text() == ""
    assert not any(it in v.view.items for it in v._hover_items)
    assert not any("hover" in n for n, _kw in ops)  # no op log per mouse move


def test_hover_includes_time_only_when_coloured_by_time(qapp):
    v = _shown_view(600, 600)
    time_prefix = x3.tr("xyz.hover_time", value="").split(":")[0]
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)), times=np.array([1.7e9, 1.7e9 + 10, 1.7e9 + 20]))
    xy, _ = _screen_of(v, v.scene["pos"])
    assert v.hover_at(*xy[2]) == 2
    assert "X: 2" in v.hover_text() and time_prefix in v.hover_text()
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)))
    xy, _ = _screen_of(v, v.scene["pos"])
    v.hover_at(*xy[2])
    assert "X: 2" in v.hover_text() and time_prefix not in v.hover_text()


def test_hover_through_real_mouse_events_not_while_dragging(qapp):
    v = _shown_view(600, 600)
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)))
    xy, _ = _screen_of(v, v.scene["pos"])
    target = QPointF(float(xy[2][0]), float(xy[2][1]))

    def move(buttons):
        qapp.sendEvent(v.view, QMouseEvent(QEvent.Type.MouseMove, target, target, Qt.MouseButton.NoButton,
                                           buttons, Qt.KeyboardModifier.NoModifier))

    move(Qt.MouseButton.NoButton)
    assert v._hover_index == 2 and "X: 2" in v.hover_text()
    move(Qt.MouseButton.LeftButton)  # rotating: no picking, readout cleared
    assert v._hover_index is None and v.hover_text() == ""
    move(Qt.MouseButton.NoButton)  # released: picking resumes
    assert v._hover_index == 2
    qapp.sendEvent(v.view, QEvent(QEvent.Type.Leave))
    assert v._hover_index is None


def test_hover_cleared_by_camera_change_and_clear(qapp):
    v = _shown_view(600, 600)
    v.set_scatter([0, 1, 2], [0, 1, 2], [0, 1, 2], np.ones((3, 4)))
    v.hover_at(*_screen_of(v, v.scene["pos"])[0][0])
    assert v._hover_index == 0
    v.apply_preset("xy")
    assert v._hover_index is None
    v.hover_at(*_screen_of(v, v.scene["pos"])[0][0])
    v.clear()
    assert v._hover_index is None and v.pick_at(10, 10) is None


def test_surface_hover_uses_table_values_and_axis_names(qapp):
    v = _shown_view(600, 600)
    xs, ys = [0.0, 10.0, 40.0], [1.0, 2.0]
    z = np.array([[5.0, 6.0, 7.0], [8.0, 9.0, 10.0]])  # (len(y), len(x))
    v.set_surface(xs, ys, z, axis_names=("RPM", "Load", "Torque"))
    assert v.scene["labels"] == ("RPM", "Load", "Torque")
    assert v.scene["pos"].shape == (6, 3)
    xy, _ = _screen_of(v, v.scene["pos"])
    i = int(np.flatnonzero((v.scene["real"][:, 0] == 10) & (v.scene["real"][:, 1] == 2))[0])
    assert v.hover_at(*xy[i]) == i  # the grid vertex x=10, y=2 -> the table's 9 (no interpolation)
    assert v.hover_text() == "RPM: 10   Load: 2   Torque: 9"
    v.set_surface(xs, ys, z)
    assert v.scene["labels"] == ("X", "Y", "Z")


def test_axis_titles_and_depth_ticks_hidden_in_presets(qapp):
    from pyqtgraph.opengl import GLTextItem

    v = _shown_view(600, 600)
    v.set_scatter([0, 1, 2], [0, 10, 20], [0, 100, 200], np.ones((3, 4)), labels=("Aa", "Bb", "Cc"))

    def texts():
        return [i.text for i in v._frame_items if isinstance(i, GLTextItem)]

    full = texts()
    assert {"Aa", "Bb", "Cc"} <= set(full) and "200" in full
    v.apply_preset("xy")  # Z points at the camera: its tick labels pile up, so they are dropped
    assert "200" not in texts() and {"Aa", "Bb", "Cc"} <= set(texts())
    v.apply_preset("yz")
    assert "200" in texts() and len(texts()) < len(full)  # X's ticks are dropped instead
    v.apply_preset("home")
    assert texts() == full
    v.apply_preset("zx")
    assert len(texts()) < len(full)
    QTest.mousePress(v.view, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))  # free rotation restores all
    assert texts() == full
    QTest.mouseRelease(v.view, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))


def test_window_passes_time_to_view_when_coloured_by_time(qapp, mw, monkeypatch):
    w = _open_window(qapp, mw, monkeypatch)
    assert w._view3d.scene["time"] is None  # colour = Z
    w._color_combo.setCurrentIndex(w._color_combo.findData("time"))
    _wait(qapp, w)
    t = w._view3d.scene["time"]
    assert t is not None and len(t) == len(w._view3d.scene["pos"])


# ---------------------------------------------------------------------------
# 25.5: map manager surface shows the map's axis names
# ---------------------------------------------------------------------------


def test_map_surface_gets_the_map_axis_names(mw, tmp_path, monkeypatch):
    _fake_probe_ok(monkeypatch)
    mgr = _manager(mw, tmp_path)
    names = sorted(mw.function_registry.maps)
    mgr.map_list.setCurrentRow(names.index("mp"))
    assert mgr.apply_edit("mp", "mp", ("RPM", "Load", "Torque"))
    mgr.map_list.setCurrentRow(names.index("mp"))
    mgr.toggle_3d.setChecked(True)
    assert mgr.view3d.scene["labels"] == ("RPM", "Load", "Torque")
    from pyqtgraph.opengl import GLTextItem

    assert {"RPM", "Load", "Torque"} <= {i.text for i in mgr.view3d._frame_items if isinstance(i, GLTextItem)}
    # the 3D buttons / hover label sit in a normal row/label around the surface here too
    assert not mgr.view3d.view.isAncestorOf(mgr.view3d._preset_buttons["home"])


def test_compute_xyz_records_time_name_and_single_colour():
    n = 20
    d = {k: np.arange(n, dtype=float) for k in "xyz"}
    r = x3.compute_xyz(_fetch_from(d), "x", "y", "z", "single", 0, n, time_name="x")
    assert r.color == "single" and r.time_name == "x" and r.valid == n


def test_axis_tick_labels_time_axes_use_clock_format():
    t0 = 1_767_225_600.0
    labels = x3.axis_tick_labels({"x": (t0, t0 + 60), "y": (0, 10), "z": (0, 1)}, ("x",))
    xs = [t for a, _p, t in labels if a == "x"]
    ys = [t for a, _p, t in labels if a == "y"]
    assert xs and all(t.count(":") == 2 for t in xs)  # HH:MM:SS for a one-minute span
    assert ys == ["0", "2", "4", "6", "8", "10"]
