from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from parquet_analyzer.ui.plot_widget import TimePlotWidget

# The original fixed v2 scheme (detailed_specification.md 4章's history note) -- used
# explicitly by tests below that assert specific modifier->action semantics (plain
# scroll zooms X, etc.), so they stay correct regardless of whatever
# Settings.DEFAULT_SCROLL_BINDINGS currently ships as the default (which is itself
# user-configurable and has already changed once since scroll_bindings was added).
_V2_SCROLL_SCHEME = {"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}


def _make_plot(qapp, width=900, scroll_bindings=None):
    plot = TimePlotWidget("p1", scroll_bindings=scroll_bindings)
    plot.resize(width, 400)
    plot.show()
    return plot


def _drain(qapp, iterations=50, delay=0.005):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def _wheel_event(
    angle_delta_y: int, modifiers=Qt.KeyboardModifier.NoModifier, angle_delta_x: int = 0
) -> QWheelEvent:
    pos = QPointF(50, 50)
    return QWheelEvent(
        pos,
        pos,
        QPoint(0, 0),
        QPoint(angle_delta_x, angle_delta_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_view_range_matches_true_data_extent_after_first_series(qapp):
    """Regression test: adding the first series used to leave the view stuck at a
    near-empty range (view autoRange fighting with the visible-window cropping
    done in _redraw_series), because pyqtgraph's built-in autoRange was still
    enabled. See TimePlotWidget.__init__ (enableAutoRange) and add_series.
    """
    plot = _make_plot(qapp)
    x = np.linspace(1_767_225_600.0, 1_767_226_100.0, 50_000)
    y = np.sin(x)
    plot.add_series("a", x, y)
    qapp.processEvents()

    x_lo, x_hi = plot.getViewBox().viewRange()[0]
    assert x_lo <= x[0] and x_hi >= x[-1]
    assert (x_hi - x_lo) > 0.9 * (x[-1] - x[0])  # not collapsed to a sliver


def test_go_home_restores_true_full_extent_after_zooming_in(qapp):
    plot = _make_plot(qapp)
    x = np.linspace(0.0, 1000.0, 20_000)
    y = np.cos(x)
    plot.add_series("a", x, y)
    qapp.processEvents()

    plot.setXRange(0, 1, padding=0)
    qapp.processEvents()
    x_lo, x_hi = plot.getViewBox().viewRange()[0]
    assert x_hi - x_lo < 5  # genuinely zoomed in

    plot.go_home()
    qapp.processEvents()
    x_lo, x_hi = plot.getViewBox().viewRange()[0]
    assert x_lo <= 0.0 and x_hi >= 1000.0


def test_legend_tracks_series_add_and_remove(qapp):
    plot = _make_plot(qapp)
    x = np.arange(100, dtype=float)
    plot.add_series("sin_wave", x, np.sin(x))
    plot.add_series("cos_wave", x, np.cos(x))
    qapp.processEvents()

    legend_names = {item[1].text for item in plot.plotItem.legend.items}
    assert legend_names == {"sin_wave", "cos_wave"}

    plot.remove_series("sin_wave")
    qapp.processEvents()
    legend_names = {item[1].text for item in plot.plotItem.legend.items}
    assert legend_names == {"cos_wave"}


def test_auto_redraw_off_suppresses_recompute_on_pan(qapp):
    plot = TimePlotWidget("p1", auto_redraw=False)
    plot.resize(200, 200)  # small width -> low n_out -> downsampling triggers easily
    plot.show()
    x = np.linspace(0.0, 1000.0, 50_000)
    y = np.sin(x)
    plot.add_series("a", x, y)  # initial display always happens, even with auto_redraw off
    _drain(qapp)
    assert plot.pending_downsample_count() == 0

    started = []
    plot.downsampleStarted.connect(lambda: started.append(1))

    plot.setXRange(100, 200, padding=0)
    _drain(qapp)
    assert not started  # no job queued from the pan itself: auto_redraw is off

    plot.redraw()  # manual R still works
    _drain(qapp)
    assert started
    assert plot.pending_downsample_count() == 0


def test_downsampling_runs_off_thread_and_progress_signals_fire(qapp):
    plot = _make_plot(qapp, width=200)
    x = np.linspace(0.0, 1000.0, 100_000)
    y = np.sin(x)

    events = []
    plot.downsampleStarted.connect(lambda: events.append("start"))
    plot.downsampleFinished.connect(lambda: events.append("finish"))

    plot.add_series("a", x, y)  # first series: triggers the initial full-range redraw
    for _ in range(50):
        qapp.processEvents()

    assert events.count("start") >= 1
    assert events.count("start") == events.count("finish")
    assert plot.pending_downsample_count() == 0


def test_plain_scroll_zooms_time_axis_only(qapp):
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()

    plot.wheelEvent(_wheel_event(120))  # zoom in
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()

    assert (x_after[1] - x_after[0]) < (x_before[1] - x_before[0])  # X narrowed
    assert y_after == pytest.approx(y_before)  # Y untouched


def test_ctrl_scroll_zooms_amp_axis_only(qapp):
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()

    plot.wheelEvent(_wheel_event(120, Qt.KeyboardModifier.ControlModifier))  # zoom in
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()

    assert x_after == pytest.approx(x_before)  # X untouched
    assert (y_after[1] - y_after[0]) < (y_before[1] - y_before[0])  # Y (amp) narrowed


def test_shift_scroll_pans_time_axis_without_zooming(qapp):
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()
    span_before = x_before[1] - x_before[0]

    plot.wheelEvent(_wheel_event(120, Qt.KeyboardModifier.ShiftModifier))
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()
    span_after = x_after[1] - x_after[0]

    assert x_after != x_before  # moved
    assert abs(span_after - span_before) < 1e-6  # but not zoomed (same width)
    assert y_after == pytest.approx(y_before)  # Y untouched


def test_custom_scroll_bindings_swap_which_modifier_zooms_which_axis(qapp):
    """User-requested: which modifier performs which action is configurable, not
    fixed. Use an explicit none=y_zoom, ctrl=x_zoom binding (the inverse of the
    original v2 scheme's none=x_zoom, ctrl=y_zoom) and confirm plain scroll now
    zooms Y and Ctrl+scroll now zooms X.
    """
    plot = TimePlotWidget(
        "p1", scroll_bindings={"none": "y_zoom", "ctrl": "x_zoom", "shift": "pan"}
    )
    plot.resize(900, 400)
    plot.show()
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()

    plot.wheelEvent(_wheel_event(120))  # plain scroll -> now bound to y_zoom
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()
    assert x_after == pytest.approx(x_before)  # X untouched
    assert (y_after[1] - y_after[0]) < (y_before[1] - y_before[0])  # Y narrowed

    x_before, y_before = x_after, y_after
    plot.wheelEvent(_wheel_event(120, Qt.KeyboardModifier.ControlModifier))  # now bound to x_zoom
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()
    assert (x_after[1] - x_after[0]) < (x_before[1] - x_before[0])  # X narrowed
    assert y_after == pytest.approx(y_before)  # Y untouched


def test_set_scroll_bindings_updates_behavior_live(qapp):
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()

    plot.set_scroll_bindings({"none": "pan", "ctrl": "y_zoom", "shift": "x_zoom"})
    x_before, y_before = plot.getViewBox().viewRange()
    span_before = x_before[1] - x_before[0]

    plot.wheelEvent(_wheel_event(120))  # plain scroll now pans instead of zooming
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()
    span_after = x_after[1] - x_after[0]

    assert x_after != x_before  # moved
    assert abs(span_after - span_before) < 1e-6  # not zoomed
    assert y_after == pytest.approx(y_before)


def test_macos_physical_ctrl_arriving_as_meta_still_zooms_amp(qapp):
    """Regression test: on macOS, Qt reports the physical Ctrl key as
    Qt.KeyboardModifier.MetaModifier, not ControlModifier (Cmd/Ctrl are swapped
    for cross-platform shortcut consistency). Before this fix, physical Ctrl+scroll
    fell through to the plain-scroll (time zoom) branch instead of amp zoom.
    """
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()

    plot.wheelEvent(_wheel_event(120, Qt.KeyboardModifier.MetaModifier))
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()

    assert x_after == pytest.approx(x_before)  # X untouched (not mistaken for plain scroll)
    assert (y_after[1] - y_after[0]) < (y_before[1] - y_before[0])  # Y (amp) narrowed


def test_shift_scroll_works_even_when_delta_reported_on_x_axis(qapp):
    """Regression test: some trackpads report a Shift+vertical-scroll gesture as a
    horizontal wheel event (delta lands in angleDelta().x() instead of .y()). Before
    this fix, that meant angleDelta().y() was 0 and the pan silently moved nothing.
    """
    plot = _make_plot(qapp, scroll_bindings=_V2_SCROLL_SCHEME)
    plot.add_series("a", np.linspace(0.0, 1000.0, 1000), np.sin(np.linspace(0.0, 1000.0, 1000)))
    qapp.processEvents()
    x_before, y_before = plot.getViewBox().viewRange()

    plot.wheelEvent(_wheel_event(0, Qt.KeyboardModifier.ShiftModifier, angle_delta_x=120))
    qapp.processEvents()
    x_after, y_after = plot.getViewBox().viewRange()

    assert x_after != x_before  # actually moved, not a silent no-op
    assert y_after == pytest.approx(y_before)


def test_time_axis_uses_date_axis_item_by_default(qapp):
    import pyqtgraph as pg

    plot = _make_plot(qapp)
    assert isinstance(plot.getAxis("bottom"), pg.DateAxisItem)


def test_non_datetime_axis_opt_out_for_frequency_plots(qapp):
    import pyqtgraph as pg

    plot = TimePlotWidget("p1", x_axis_datetime=False)
    plot.resize(900, 400)
    plot.show()
    assert not isinstance(plot.getAxis("bottom"), pg.DateAxisItem)


def test_date_axis_tick_granularity_adapts_to_zoom_level(qapp):
    """Zoomed out shows date/month-scale ticks; zoomed into a few seconds shows
    sub-second ticks (specification.md: date+time when zoomed out, finer when
    zoomed in).
    """
    plot = _make_plot(qapp)
    x0 = 1_767_225_600.0  # 2026-01-01
    x = np.linspace(x0, x0 + 4 * 365 * 86400, 10_000)  # 4-year span
    plot.add_series("a", x, np.sin(np.linspace(0, 1000, 10_000)))
    qapp.processEvents()

    axis = plot.getAxis("bottom")

    def finest_tick_spacing(lo, hi):
        spacing, _values = axis.tickValues(lo, hi, 900)[-1]
        return spacing

    wide_spacing = finest_tick_spacing(x0, x0 + 4 * 365 * 86400)

    plot.setXRange(x0, x0 + 3, padding=0)  # zoom into a 3-second window
    qapp.processEvents()
    narrow_spacing = finest_tick_spacing(x0, x0 + 3)

    assert narrow_spacing < wide_spacing  # finer ticks when zoomed in


def test_panning_updates_every_overlaid_series_not_just_the_last(qapp):
    """Regression test: TimePlotWidget used to track one `_generation` counter per
    *plot*, bumped once per *series* redrawn. With 2+ overlaid series, every redraw
    batch (e.g. from a pan) incremented the counter once per series, so by the time
    any single series' background job finished, the shared counter already matched
    only the *last* series processed — every other series' valid result was
    discarded as "stale" and its curve silently never updated.
    """
    plot = _make_plot(qapp, width=200)  # small width -> low n_out -> async path triggers
    x = np.linspace(0.0, 1000.0, 50_000)
    plot.add_series("a", x, np.sin(x))
    plot.add_series("b", x, np.cos(x))
    _drain(qapp)

    plot.setXRange(100, 900, padding=0)
    _drain(qapp)

    xa, ya = plot._series["a"]["curve"].getData()
    xb, yb = plot._series["b"]["curve"].getData()
    assert ya is not None and len(ya) > 0, "series 'a' never received its downsampled data"
    assert yb is not None and len(yb) > 0, "series 'b' never received its downsampled data"
    assert xa.min() == pytest.approx(100, abs=1) and xa.max() == pytest.approx(900, abs=1)
    assert xb.min() == pytest.approx(100, abs=1) and xb.max() == pytest.approx(900, abs=1)


def test_series_containing_nan_does_not_crash_add_series_or_go_home(qapp):
    """Regression test: add_series/go_home used raw .min()/.max() (not nan-aware),
    so ViewBox.setRange([nan, nan]) raised for any column containing NaN/Inf — e.g. a
    real sensor log with missing readings, or sqrt() of a series that goes negative.
    """
    plot = _make_plot(qapp)
    x = np.linspace(-5.0, 5.0, 1000)
    with np.errstate(invalid="ignore"):
        y = np.sqrt(x)  # negative x -> NaN for part of the series

    plot.add_series("v", x, y)  # must not raise
    qapp.processEvents()

    x_range, y_range = plot.getViewBox().viewRange()
    assert all(np.isfinite(x_range)) and all(np.isfinite(y_range))

    plot.go_home()  # must not raise either
    qapp.processEvents()
    x_range, y_range = plot.getViewBox().viewRange()
    assert all(np.isfinite(x_range)) and all(np.isfinite(y_range))


def test_all_nan_series_falls_back_to_default_range(qapp):
    plot = _make_plot(qapp)
    x = np.linspace(0.0, 10.0, 100)
    y = np.full(100, np.nan)

    plot.add_series("v", x, y)  # must not raise even with zero finite y-values
    qapp.processEvents()
    x_range, y_range = plot.getViewBox().viewRange()
    assert all(np.isfinite(x_range)) and all(np.isfinite(y_range))
