"""Common start state for the side-effect tests (app 45.1) and the shared fixtures."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication
from side_effects_helper import SideEffectHarness, settle

from parquet_analyzer import config, features
from parquet_analyzer.core import oplog
from parquet_analyzer.ui.main_window import MainWindow

CURVE_CSV = "x,y\n-10,100\n0,0\n10,100\n"  # the map the derived variable uses: cv(a)


class Ctx:
    """What a table row's setup needs: the window, the harness and a scratch folder."""

    def __init__(self, win, harness, tmp_path):
        self.win = win
        self.harness = harness
        self.tmp_path = tmp_path

    @property
    def plot1(self):
        return self.win.plot_grid.plots[0]

    @property
    def plot2(self):
        return self.win.plot_grid.plots[1]

    def run(self, name, action, **kw):
        return self.harness.run(name, action, **kw)


def build_start_state(win, sample_path, tmp_path) -> None:
    """2 linked time plots; plot 1: a, b, m = cv(a) (a registered map); plot 2: c; zoomed to
    ~20-60% of the data (not home); cursor on with one cursor inside the range."""
    win.resize(1200, 800)
    win.show()
    settle(win)
    assert win.load_parquet(str(sample_path))
    settle(win)

    csv = tmp_path / "cv.csv"
    csv.write_text(CURVE_CSV, encoding="utf-8")
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(csv), "cv")
    mgr.close()
    win._on_add_derived_variable("m", "cv(a)")

    win.plot_grid.add_plot()
    plot1, plot2 = win.plot_grid.plots
    for plot, names in ((plot1, ("a", "b", "m")), (plot2, ("c",))):
        for name in names:
            win._on_variable_dropped(plot, name)
    settle(win)

    tv = win._time_values
    n = len(tv)
    lo, hi = float(tv[int(n * 0.2)]), float(tv[int(n * 0.6)])
    win.plot_grid.set_x_range(lo, hi)
    settle(win)

    win._actions["cursor"].trigger()  # cursor mode on
    win.plot_grid._on_cursor_add_requested(plot1, (lo + hi) / 2)
    settle(win)
    QApplication.processEvents()


@pytest.fixture
def log(qapp, isolated_config):
    """Recording on, into the tmp log dir; fully reset afterwards."""
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


@pytest.fixture
def ctx(log, monkeypatch, sample_parquet_path, tmp_path):
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)  # export 57 (45.2 table rows)
    # the harness patches dialogs/loads first, so the start state's own opens are not counted
    win = MainWindow()
    harness = SideEffectHarness(win, monkeypatch)
    build_start_state(win, sample_parquet_path, tmp_path)
    oplog.install_excepthooks()  # uncaught exceptions (also from Qt slots) become `exception` records
    yield Ctx(win, harness, tmp_path)
    for w in list(win._analysis_windows):
        w.close()
    oplog.uninstall_excepthooks()
    win.close()
