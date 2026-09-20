from __future__ import annotations

from PySide6.QtCore import Qt

from parquet_analyzer import config
from parquet_analyzer.ui.main_window import MainWindow


def test_variable_dock_defaults_to_left(qapp, isolated_config):
    win = MainWindow()
    assert win.dockWidgetArea(win._variable_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    win.close()


def test_moved_dock_persists_across_windows(qapp, isolated_config):
    win = MainWindow()
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, win._variable_dock)
    win.close()  # closeEvent saves geometry/state to settings

    win2 = MainWindow()
    assert win2.dockWidgetArea(win2._variable_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    win2.close()


def test_reset_layout_restores_default_and_clears_saved_state(qapp, isolated_config):
    win = MainWindow()
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, win._variable_dock)
    win.close()

    win2 = MainWindow()
    assert win2.dockWidgetArea(win2._variable_dock) == Qt.DockWidgetArea.RightDockWidgetArea

    win2.reset_layout()
    assert win2.dockWidgetArea(win2._variable_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert win2.settings.window_geometry is None
    assert win2.settings.window_state is None

    # a third window (loading the now-cleared settings) should also default to the left
    win2.close()
    win3 = MainWindow()
    assert win3.dockWidgetArea(win3._variable_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    win3.close()


def test_corrupted_window_geometry_does_not_prevent_startup(qapp, isolated_config):
    """Regression test: _restore_layout() called base64.b64decode() on
    settings.window_geometry/window_state with no error handling, so a corrupted or
    incompatible value (bad base64 padding, truncated write, old format) raised
    uncaught during MainWindow.__init__ — the app crashed before any window ever
    appeared, i.e. it "wouldn't start" at all.
    """
    path = config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version": "1", "window_geometry": "not-valid-base64!!!", "window_state": "AAAA"}',
        encoding="utf-8",
    )

    win = MainWindow()  # must not raise
    assert win.settings.window_geometry is None  # corrupted value cleared, not retried forever
    assert win.settings.window_state is None
    win.close()


def test_off_screen_saved_geometry_falls_back_to_default(qapp, isolated_config):
    """Regression test: a window position saved while on a monitor that's since been
    disconnected (or any other now-unreachable position) must never leave the restored
    window invisible/unreachable — the process running with no visible window is
    indistinguishable from the app "not starting" at all.

    Qt's own restoreGeometry() already clamps wildly out-of-bounds positions back
    on-screen in practice, so this mainly pins down that behavior (via
    _is_visible_on_some_screen(), the safety net _restore_layout() also checks) rather
    than exercising our own fallback branch specifically.
    """
    win1 = MainWindow()
    win1.show()
    win1.move(50_000, 50_000)  # move somewhere no real screen would ever be
    assert not win1._is_visible_on_some_screen()
    win1.close()  # closeEvent saves this off-screen geometry

    win2 = MainWindow()
    assert win2._is_visible_on_some_screen()
    win2.close()
