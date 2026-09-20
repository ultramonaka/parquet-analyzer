from __future__ import annotations

import gc

import numpy as np
import pytest

from parquet_analyzer.core.expression import ExpressionError
from parquet_analyzer.ui.main_window import MainWindow


def test_adding_many_plots_and_waveforms_does_not_crash(qapp, isolated_config, sample_parquet_path):
    """Regression test: repeatedly adding plots and overlaying variables used to
    risk a crash from the background worker being garbage-collected (along with its
    QObject signal) before its queued cross-thread `finished` signal was
    delivered — see BackgroundWorker.__init__ and TimePlotWidget._active_workers.
    """
    win = MainWindow()
    win.resize(800, 600)
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    names = [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]
    assert names

    plots = []
    for _ in range(15):
        plot = win.plot_grid.add_plot()
        plots.append(plot)
        for name in names:
            win._on_variable_dropped(plot, name)

    for _ in range(100):
        qapp.processEvents()

    gc.collect()
    for plot in plots:
        assert plot.pending_downsample_count() == 0, "a downsample worker was never released"

    win.close()


def test_removing_the_anchor_plot_does_not_break_linked_plots(qapp, isolated_config, sample_parquet_path):
    """New plots are X-linked to plots[0]; removing that plot must not leave
    other plots linked to a dangling ViewBox.
    """
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()
    name = win.variable_panel.item(0).text()

    p1 = win.plot_grid.plots[0]
    win._on_variable_dropped(p1, name)
    p2 = win.plot_grid.add_plot()
    win._on_variable_dropped(p2, name)
    qapp.processEvents()

    win.plot_grid.remove_plot(p1)
    qapp.processEvents()

    p2.setXRange(0, 100, padding=0)
    qapp.processEvents()

    win.close()


def test_separate_and_remove_plot_via_context_menu_actions(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()
    names = [win.variable_panel.item(i).text() for i in range(win.variable_panel.count())]

    plot = win.plot_grid.plots[0]
    for name in names:
        win._on_variable_dropped(plot, name)
    qapp.processEvents()

    plot.separateRequested.emit(plot)
    qapp.processEvents()
    assert len(win.plot_grid.plots) == 2

    win.plot_grid.plots[-1].removeRequested.emit(win.plot_grid.plots[-1])
    qapp.processEvents()
    assert len(win.plot_grid.plots) == 1

    win.close()


def test_derived_variable_name_colliding_with_raw_column_is_rejected(qapp, isolated_config, sample_parquet_path):
    """Regression test: naming a derived variable after an existing raw column used to
    silently overwrite that column's real data in self._raw_variables, with no error and
    no visible change in the variable panel (which dedupes by name).
    """
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))
    original_a = win.variable_values("a").copy()

    with pytest.raises(ExpressionError):
        win._on_add_derived_variable("a", "a * 2")

    np.testing.assert_array_equal(win.variable_values("a"), original_a)
    assert "a" not in win._derived
    win.close()


def test_redefining_an_existing_derived_variable_is_still_allowed(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.load_parquet(str(sample_parquet_path))

    win._on_add_derived_variable("k", "a * 2")
    np.testing.assert_array_equal(win.variable_values("k"), win.variable_values("a") * 2)

    win._on_add_derived_variable("k", "a * 3")  # redefine own derived variable: allowed
    np.testing.assert_array_equal(win.variable_values("k"), win.variable_values("a") * 3)
    win.close()
