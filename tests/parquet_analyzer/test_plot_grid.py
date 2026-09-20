from __future__ import annotations

import time

import numpy as np
import pytest

from parquet_analyzer.ui.plot_grid import PlotGridWidget


def _drain(qapp, iterations=100, delay=0.01):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


def test_remove_plot_defers_deletion_until_pending_jobs_finish(qapp):
    """Regression test: deleting a plot immediately (deleteLater()) while it still had
    in-flight downsample workers risked the worker's queued `finished` signal arriving
    after the C++ widget was actually destroyed, crashing when the slot touched it (the
    same class of bug fixed for BackgroundWorker's own lifetime — see its __init__).
    remove_plot must not call deleteLater() until pending_downsample_count() is 0.
    """
    grid = PlotGridWidget()
    grid.resize(200, 400)
    grid.show()
    plot = grid.add_plot()
    x = np.linspace(0.0, 1000.0, 3_000_000)
    plot.add_series("a", x, np.sin(x))
    assert plot.pending_downsample_count() > 0

    delete_calls = []
    plot.deleteLater = lambda: delete_calls.append(1)

    grid.remove_plot(plot)
    assert not delete_calls, "deleteLater() must not fire while jobs are still in flight"

    _drain(qapp)
    assert delete_calls, "deleteLater() must fire once all in-flight jobs have finished"
    assert plot.pending_downsample_count() == 0


def test_fft_plot_range_is_not_forwarded_to_navigator(qapp):
    """Regression test: PlotGridWidget used to connect *every* plot's rangeChanged
    signal to the grid-level aggregate, including x_axis_datetime=False (FFT,
    frequency-domain) plots. A Hz range then got forwarded and misinterpreted as a
    time range, corrupting the navigator's selected region.
    """
    grid = PlotGridWidget()
    time_plot = grid.plots[0]
    x = np.linspace(1_767_225_600.0, 1_767_225_800.0, 1000)
    time_plot.add_series("a", x, np.sin(x))

    events = []
    grid.rangeChanged.connect(events.append)

    fft_plot = grid.add_plot(x_axis_datetime=False)
    freqs = np.linspace(0.0, 50.0, 500)
    fft_plot.add_series("a (FFT)", freqs, np.abs(np.sin(freqs)))
    fft_plot.setXRange(10, 20, padding=0)

    assert events == [], "FFT (frequency-domain) plot range must not reach the navigator"

    time_plot.setXRange(1_767_225_650.0, 1_767_225_700.0, padding=0)
    assert events == [(1_767_225_650.0, 1_767_225_700.0)]


def test_removing_the_anchor_plot_relinks_survivors_to_a_new_anchor(qapp):
    """Regression test: new time-domain plots are always X-linked to the *first*
    time-domain plot (a star topology). Removing that anchor without re-linking the
    survivors left them each still pointing at the removed plot's ViewBox; once it was
    actually garbage collected, pyqtgraph's weakref-based link silently went stale and
    the survivors desynced from each other with no error.
    """
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    x = np.linspace(0.0, 1000.0, 1000)
    p1.add_series("a", x, np.sin(x))
    p2 = grid.add_plot()
    p2.add_series("b", x, np.cos(x))
    p3 = grid.add_plot()
    p3.add_series("c", x, np.sin(x / 10))

    assert p2.getViewBox().linkedView(0) is p1.getViewBox()
    assert p3.getViewBox().linkedView(0) is p1.getViewBox()

    grid.remove_plot(p1)

    # p3 must now be linked to the new anchor (p2), not left pointing at the removed p1.
    assert p3.getViewBox().linkedView(0) is p2.getViewBox()

    p2.setXRange(300, 400, padding=0)
    assert p2.getViewBox().viewRange()[0] == [300, 400]
    assert p3.getViewBox().viewRange()[0] == [300, 400]


def test_move_plot_up_and_down_reorders_and_relinks_anchor(qapp):
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    x = np.linspace(0.0, 1000.0, 1000)
    p1.add_series("a", x, np.sin(x))
    p2 = grid.add_plot()
    p2.add_series("b", x, np.cos(x))
    p3 = grid.add_plot()
    p3.add_series("c", x, np.sin(x / 10))
    assert grid.plots == [p1, p2, p3]

    events = []
    grid.layoutChanged.connect(lambda: events.append(1))

    grid.move_plot_down(p1)
    assert grid.plots == [p2, p1, p3]
    assert events == [1]
    # p2 is the new anchor (plots[0]); p1 and p3 must both now link to it, not to the
    # old anchor p1 (which would leave p1 linked to itself, or p3 stuck on a stale link).
    assert p1.getViewBox().linkedView(0) is p2.getViewBox()
    assert p3.getViewBox().linkedView(0) is p2.getViewBox()
    # Regression: the promoted anchor itself must not keep its own old outbound link
    # (setXLink only clears the link *on the view you call it on*, not on views
    # already pointing at that view) -- p2 was previously linked to p1.
    assert p2.getViewBox().linkedView(0) is None

    grid.move_plot_up(p3)
    assert grid.plots == [p2, p3, p1]


def test_move_plot_cannot_cross_the_time_domain_frequency_domain_boundary(qapp):
    """Regression test: several places (navigator overview, set_x_range, load_view's
    plot-reuse logic) assume plots[0] is time-domain. Letting an FFT plot move above
    the last time-domain plot (or vice versa) would break that assumption silently.
    """
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    fft_plot = grid.add_plot(x_axis_datetime=False)
    assert grid.plots == [p1, fft_plot]

    events = []
    grid.layoutChanged.connect(lambda: events.append(1))

    grid.move_plot_up(fft_plot)  # would put the FFT plot at index 0
    assert grid.plots == [p1, fft_plot]  # refused
    grid.move_plot_down(p1)  # would put the time-domain plot after the FFT one
    assert grid.plots == [p1, fft_plot]  # refused
    assert events == []


def test_remove_plot_refuses_to_remove_the_last_time_domain_plot(qapp):
    """Regression test: removing the sole time-domain plot while an FFT plot remains
    would leave plots[0] as an FFT plot, which navigator overview/set_x_range/
    load_view aren't written to handle.
    """
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    fft_plot = grid.add_plot(x_axis_datetime=False)

    grid.remove_plot(p1)

    assert grid.plots == [p1, fft_plot]  # refused; p1 was not removed
    assert grid.anchor_plot() is p1


def test_move_plot_at_either_end_of_the_stack_is_a_no_op(qapp):
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    p2 = grid.add_plot()

    events = []
    grid.layoutChanged.connect(lambda: events.append(1))

    grid.move_plot_up(p1)  # already at the top
    assert grid.plots == [p1, p2]
    grid.move_plot_down(p2)  # already at the bottom
    assert grid.plots == [p1, p2]
    assert events == []  # neither no-op should have fired the signal


def test_set_x_range_targets_the_anchor_not_an_fft_plot(qapp):
    grid = PlotGridWidget()
    p1 = grid.plots[0]
    fft_plot = grid.add_plot(x_axis_datetime=False)
    fft_plot.setXRange(10, 20, padding=0)

    grid.set_x_range(100.0, 200.0)

    assert p1.getViewBox().viewRange()[0] == pytest.approx([100.0, 200.0])
    assert fft_plot.getViewBox().viewRange()[0] == pytest.approx([10, 20])  # untouched
