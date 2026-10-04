"""Hover value readout (specification 5.32, detailed_specification.md / plots domain ch.39)."""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from parquet_analyzer.io.settings import DEFAULT_SHORTCUTS, Settings, load_settings, save_settings
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.plot_grid import PlotGridWidget
from parquet_analyzer.ui.plot_widget import TimePlotWidget

X0 = 1_767_225_600.0  # epoch seconds


def _pump(qapp, until=None, timeout_s=3.0, min_s=0.0):
    """Process events for at least `min_s`, and until `until()` is true (if given)."""
    start = time.monotonic()
    while True:
        qapp.processEvents()
        elapsed = time.monotonic() - start
        if elapsed >= min_s and (until is None or until()):
            return True
        if elapsed > timeout_s:
            return until is None
        time.sleep(0.005)


def _wait_drawn(qapp, *plots):
    assert _pump(qapp, lambda: all(p.pending_downsample_count() == 0 for p in plots), timeout_s=10.0)


def _move_to(qapp, plot, x, frac_y=0.5):
    """A real mouse move over the plot's viewport at data X `x` (Qt routing, not a handler call)."""
    vb = plot.getViewBox()
    rect = vb.sceneBoundingRect()
    scene_pt = vb.mapViewToScene(QPointF(x, 0.0))
    scene_pt.setY(rect.top() + rect.height() * frac_y)
    # QTest.mouseMove only delivers the first move on the offscreen platform, so send real
    # MouseMove events to the viewport (the same path Qt's own delivery takes into the scene).
    viewport = plot.viewport()
    local = QPointF(plot.mapFromScene(scene_pt))
    ev = QMouseEvent(
        QEvent.Type.MouseMove, local, viewport.mapToGlobal(local), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, ev)
    qapp.processEvents()


def _leave(qapp, plot):
    QApplication.sendEvent(plot, QEvent(QEvent.Type.Leave))
    qapp.processEvents()


def _make_grid(qapp, n_plots=2):
    grid = PlotGridWidget()
    grid.resize(900, 700)
    grid.show()
    qapp.processEvents()
    x = X0 + np.arange(2000, dtype=float) * 0.5
    plots = [grid.plots[0]]
    for _ in range(n_plots - 1):
        plots.append(grid.add_plot())
    for i, p in enumerate(plots):
        p.add_series(f"s{i}", x, np.sin(x / 50.0) + i)
    qapp.processEvents()
    for p in plots:
        p.getViewBox().setRange(xRange=(X0, X0 + 1000.0), padding=0)
    qapp.processEvents()
    return grid, plots, x


class _FakeWindowSource:
    def __init__(self, full):
        self._full = full
        self.calls = []
        self.pyramid = None

    def window(self, a, b):
        self.calls.append((a, b))
        return self._full[a:b]

    def request_pyramid(self):
        return None

    def set_pyramid(self, p):
        self.pyramid = p


# -- linked display -----------------------------------------------------------------


def test_move_shows_line_and_values_on_all_linked_plots_and_leave_hides(qapp):
    grid, (p1, p2), x = _make_grid(qapp)
    _move_to(qapp, p1, X0 + 400.0)
    assert p1.hover_x() == pytest.approx(X0 + 400.0, abs=3.0)
    # the linked plot shows the same time and its own series' value
    assert p2.hover_x() == pytest.approx(p1.hover_x())
    assert p2._hover_line.isVisible() and p2._hover_label.isVisible()
    t1, t2 = p1.hover_text(), p2.hover_text()
    assert t1.splitlines()[0] == p1.format_x(p1.hover_x()) == t2.splitlines()[0]
    assert "s0: " in t1 and "s1: " in t2
    row = int(np.searchsorted(x, p1.hover_x()))
    row = row - 1 if abs(x[row - 1] - p1.hover_x()) <= abs(x[row] - p1.hover_x()) else row
    assert p1.hover_text().splitlines()[1] == f"s0: {np.sin(x[row] / 50.0):.6g}"  # same format as cursors
    assert "≈" not in t1  # resident column: real samples from the start
    # leaving the plot hides the line and the linked plot's too
    _leave(qapp, p1)
    assert p1.hover_x() is None and p2.hover_x() is None
    assert not p2._hover_line.isVisible() and p2.hover_text() == ""
    grid.close()


def test_hover_is_not_a_placed_cursor(qapp):
    grid, (p1, p2), _ = _make_grid(qapp)
    _move_to(qapp, p1, X0 + 300.0)
    assert grid.cursor_times() == [] and p1.cursor_ids() == []
    grid.close()


def test_hover_shown_in_cursor_mode_and_label_avoids_cursor_label(qapp):
    grid, (p1, p2), _ = _make_grid(qapp)
    grid.set_cursor_mode_enabled(True)
    p1.add_cursor("c1", X0 + 990.0)  # right edge: its label is right-anchored near the top
    qapp.processEvents()
    _move_to(qapp, p1, X0 + 200.0)
    assert p1.hover_x() is not None
    label, cursor_label = p1._hover_label, p1._cursors["c1"]["label"]
    assert not label.sceneBoundingRect().intersects(cursor_label.sceneBoundingRect())
    grid.close()


def test_fft_plot_does_not_link_and_shows_hz(qapp):
    grid, (p1, p2), _ = _make_grid(qapp)
    fft = grid.add_plot(x_axis_datetime=False)
    f = np.linspace(0.0, 100.0, 500)
    fft.add_series("spec", f, np.abs(np.sin(f)))
    qapp.processEvents()
    fft.getViewBox().setRange(xRange=(0.0, 100.0), padding=0)
    qapp.processEvents()
    _move_to(qapp, fft, 40.0)
    assert fft.hover_x() == pytest.approx(40.0, abs=2.0)
    assert fft.hover_text().splitlines()[0].endswith("Hz") and "spec: " in fft.hover_text()
    assert p1.hover_x() is None and p2.hover_x() is None
    # and a time plot's hover doesn't reach the FFT plot
    _leave(qapp, fft)
    _move_to(qapp, p1, X0 + 100.0)
    assert fft.hover_x() is None
    grid.close()


def test_independent_plot_does_not_link(qapp):
    grid, (p1, p2), _ = _make_grid(qapp)
    assert grid.set_plot_x_linked(p2, False)
    _move_to(qapp, p1, X0 + 100.0)
    assert p1.hover_x() is not None and p2.hover_x() is None
    grid.close()


# -- values -------------------------------------------------------------------------


def test_hidden_series_not_listed_and_selected_is_bold(qapp):
    grid, (p1, _), x = _make_grid(qapp)
    p1.add_series("other", x, np.cos(x / 30.0))
    p1.set_series_visible("other", False)
    p1.set_series_selected("s0", True)
    _move_to(qapp, p1, X0 + 400.0)
    assert "s0: " in p1.hover_text() and "other" not in p1.hover_text()
    assert "font-weight" in p1._hover_label.toHtml()
    p1.set_series_visible("other", True)
    _move_to(qapp, p1, X0 + 410.0)
    assert "other: " in p1.hover_text()
    grid.close()


def test_windowed_series_is_approximate_while_moving_then_exact_after_stop(qapp):
    plot = TimePlotWidget("p1")
    plot.resize(900, 400)
    plot.show()
    plot.set_hover_enabled(True)
    n = 200_000
    x = X0 + np.arange(n, dtype=float) * 0.01
    y = np.sin(x * 3.0) * 100.0
    src = _FakeWindowSource(y)
    plot.add_windowed_series("big", x, src, (-100.0, 100.0))
    _wait_drawn(qapp, plot)
    _pump(qapp, min_s=0.3)
    _wait_drawn(qapp, plot)
    assert len(plot._series["big"]["curve"].getData()[0]) < n  # really downsampled
    src.calls.clear()

    _move_to(qapp, plot, plot.getViewBox().viewRange()[0][0] + 300.0)
    assert plot.hover_x() is not None
    assert "≈" in plot.hover_text()
    assert src.calls == []  # moving reads nothing from disk

    assert _pump(qapp, lambda: "≈" not in plot.hover_text(), timeout_s=2.0)
    row = plot._nearest_row(x, plot.hover_x())
    assert plot.hover_text().splitlines()[1] == f"big: {y[row]:.6g}"
    assert src.calls == [(row, row + 1)]  # exactly one single-row read after the pause

    # moving again restarts the cycle: approximate first, exact after another pause
    _move_to(qapp, plot, plot.getViewBox().viewRange()[0][0] + 600.0)
    assert "≈" in plot.hover_text()
    assert _pump(qapp, lambda: "≈" not in plot.hover_text(), timeout_s=2.0)
    plot.close()


def test_stop_timer_is_restarted_by_every_move(qapp):
    plot = TimePlotWidget("p1")
    plot.resize(900, 400)
    plot.show()
    plot.set_hover_enabled(True)
    x = X0 + np.arange(50_000, dtype=float) * 0.01
    plot.add_windowed_series("big", x, _FakeWindowSource(np.cos(x)), (-1.0, 1.0))
    _wait_drawn(qapp, plot)
    _pump(qapp, min_s=0.3)
    _wait_drawn(qapp, plot)
    lo = plot.getViewBox().viewRange()[0][0]
    for i in range(8):  # keeps moving for ~240 ms (> 100 ms) -- must stay approximate
        _move_to(qapp, plot, lo + 100.0 + 20.0 * i)
        _pump(qapp, min_s=0.03)
        assert "≈" in plot.hover_text()
    plot.close()


def test_nan_and_non_ascending_time_axis_does_not_raise(qapp):
    plot = TimePlotWidget("p1")
    plot.resize(900, 400)
    plot.show()
    plot.set_hover_enabled(True)
    x = X0 + np.arange(1000, dtype=float)
    x[100:110] = np.nan
    x[500:520] = x[500:520][::-1]
    x[700] = X0 - 5000.0
    plot.add_series("res", x, np.arange(1000, dtype=float))
    plot.add_windowed_series("win", x, _FakeWindowSource(np.arange(1000, dtype=float)), (0.0, 999.0))
    plot.getViewBox().setRange(xRange=(X0, X0 + 999.0), padding=0)
    qapp.processEvents()
    for xv in (X0 + 5.0, X0 + 105.0, X0 + 510.0, X0 + 698.0, X0 + 990.0):
        _move_to(qapp, plot, xv)
        _pump(qapp, min_s=0.12)  # approx and settled paths both run
        assert plot.hover_text() != ""
    # a non-finite X handed in directly (e.g. from a linked plot) just hides
    plot.set_hover_x(float("nan"))
    plot.set_hover_x(float("inf"))
    assert plot.hover_x() is None
    plot.close()


def test_failed_single_row_read_shows_dash(qapp):
    plot = TimePlotWidget("p1")
    plot.resize(900, 400)
    plot.show()
    plot.set_hover_enabled(True)
    x = X0 + np.arange(1000, dtype=float)

    class Boom(_FakeWindowSource):
        def window(self, a, b):
            if b - a == 1:
                raise OSError("disk")
            return super().window(a, b)

    plot.add_windowed_series("bad", x, Boom(np.zeros(1000)), (0.0, 1.0))
    plot.getViewBox().setRange(xRange=(X0, X0 + 999.0), padding=0)
    _wait_drawn(qapp, plot)
    _move_to(qapp, plot, X0 + 500.0)
    _pump(qapp, min_s=0.2)
    assert "bad: —" in plot.hover_text()
    plot.close()


# -- toggle / settings --------------------------------------------------------------


def test_disabled_shows_nothing_and_ignores_moves(qapp):
    grid, (p1, p2), _ = _make_grid(qapp)
    grid.set_hover_enabled(False)
    _move_to(qapp, p1, X0 + 400.0)
    assert p1.hover_x() is None and p2.hover_x() is None
    _move_to(qapp, p1, X0 + 450.0)
    assert p1._hover_pending is None and p1.hover_x() is None
    grid.set_hover_enabled(True)
    _move_to(qapp, p1, X0 + 500.0)
    assert p1.hover_x() is not None
    grid.set_hover_enabled(False)  # turning off while showing hides immediately
    assert p1.hover_x() is None and p2.hover_x() is None
    # a plot added later follows the grid's current flag
    assert grid.add_plot().hover_enabled() is False
    grid.close()


def test_settings_round_trip_and_default(isolated_config):
    assert Settings().hover_values_enabled is True
    assert "hover_values" in DEFAULT_SHORTCUTS and DEFAULT_SHORTCUTS["hover_values"] == ""
    s = Settings(hover_values_enabled=False)
    save_settings(s)
    assert load_settings().hover_values_enabled is False
    save_settings(Settings())
    assert load_settings().hover_values_enabled is True


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    w = MainWindow()
    w.resize(1000, 800)
    w.show()
    w.load_parquet(str(sample_parquet_path))
    w._add_variable_to_plot(w.plot_grid.plots[0], "a")
    w.plot_grid.add_plot()
    w._add_variable_to_plot(w.plot_grid.plots[1], "b")
    deadline = time.monotonic() + 10
    while w._busy_sources and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    yield w
    w.close()


@pytest.fixture
def log(qapp, isolated_config):
    from parquet_analyzer import config
    from parquet_analyzer.core import oplog

    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


def _ops() -> list[dict]:
    import json
    from datetime import date

    from parquet_analyzer.core import oplog

    oplog.flush()
    text = oplog.log_path_for(date.today()).read_text(encoding="utf-8")
    return [r for r in map(json.loads, text.splitlines()) if r.get("kind") == "op"]


def test_toolbar_toggle_saves_setting_and_logs(win, qapp, log):
    action = win._actions["hover_values"]
    assert action.isCheckable() and action.isChecked()
    p1, p2 = win.plot_grid.plots
    _pump(qapp, min_s=0.4)  # let layout-settle ops (range_changed) land first
    n_ops = len(_ops())
    for i in range(5):
        _move_to(qapp, p1, p1.getViewBox().viewRange()[0][0] + 1.0 + i)
    assert p1.hover_x() is not None and p2.hover_x() is not None
    assert [r["action"] for r in _ops()[n_ops:]] == []  # mouse moves are not logged (39.3)

    action.trigger()
    assert not action.isChecked()
    assert p1.hover_x() is None and not p1.hover_enabled() and not p2.hover_enabled()
    assert load_settings().hover_values_enabled is False
    assert any(r.get("action") == "hover_values" and r.get("details", {}).get("enabled") is False for r in _ops())
    action.trigger()
    assert load_settings().hover_values_enabled is True and p1.hover_enabled()


def test_saved_off_state_applies_at_startup(qapp, isolated_config, sample_parquet_path):
    save_settings(Settings(hover_values_enabled=False))
    w = MainWindow()
    try:
        assert not w._actions["hover_values"].isChecked()
        assert not w.plot_grid.plots[0].hover_enabled()
        assert not w.plot_grid.add_plot().hover_enabled()
    finally:
        w.close()


def test_hover_does_not_change_the_navigator_or_exports(win, qapp, tmp_path):
    p1, p2 = win.plot_grid.plots
    before = win.navigator.selected_range()
    snap_before = p1.export_snapshot()
    range_before = p1.getViewBox().viewRange()
    _move_to(qapp, p1, p1.getViewBox().viewRange()[0][0] + 5.0)
    _pump(qapp, min_s=0.15)
    assert p1.hover_x() is not None
    assert win.navigator.selected_range() == before
    snap = p1.export_snapshot()
    assert snap.cursors == [] and len(snap.series) == len(snap_before.series) == 1
    assert p1.getViewBox().viewRange() == range_before  # hover items never touch the view range
    out = tmp_path / "hover.png"
    assert win.export_image(str(out))
    assert out.exists() and out.stat().st_size > 0
    assert p1.hover_x() is not None  # exporting leaves the live hover alone
