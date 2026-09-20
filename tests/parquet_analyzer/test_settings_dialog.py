from __future__ import annotations

from PySide6.QtGui import QKeySequence

from parquet_analyzer.io.settings import DEFAULT_SCROLL_BINDINGS, SCROLL_ACTIONS, Settings
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.settings_dialog import SettingsDialog


def test_settings_dialog_result_reflects_edits(qapp):
    settings = Settings()
    dialog = SettingsDialog(settings)

    dialog.auto_redraw_check.setChecked(False)
    dialog.downsample_spin.setValue(7)
    dialog.eager_load_limit_spin.setValue(256)
    dialog._shortcut_edits["redraw"].setKeySequence("Ctrl+R")

    result = dialog.result_settings()
    assert result.auto_redraw is False
    assert result.downsample_pixel_ratio == 7
    assert result.eager_load_limit_mb == 256
    assert result.shortcuts["redraw"] == "Ctrl+R"
    assert result.shortcuts["home"] == "H"  # untouched fields keep their defaults
    assert result.scroll_bindings == DEFAULT_SCROLL_BINDINGS  # untouched


def test_settings_dialog_scroll_binding_combo_swaps_the_conflicting_modifier(qapp):
    """User-requested: freely reassign which modifier performs which scroll action.
    Picking an action already assigned to another modifier's combo must swap the two
    (never leave two modifiers bound to the same action, which would make the third
    action unreachable by scroll at all).

    Derives its expectations from DEFAULT_SCROLL_BINDINGS rather than hardcoding a
    specific mapping, so it stays correct across whatever the shipped default
    currently is (itself user-configurable, and already changed once).
    """
    dialog = SettingsDialog(Settings())
    assert dialog._scroll_bindings == DEFAULT_SCROLL_BINDINGS

    none_old_action = DEFAULT_SCROLL_BINDINGS["none"]
    new_action = next(a for a in SCROLL_ACTIONS if a != none_old_action)
    other_modifier = next(m for m, a in DEFAULT_SCROLL_BINDINGS.items() if a == new_action)

    # Assign "none" an action some other modifier currently holds -- that other
    # modifier must swap down to "none"'s previous action.
    none_combo = dialog._scroll_combos["none"]
    none_combo.setCurrentIndex(SCROLL_ACTIONS.index(new_action))

    assert dialog._scroll_bindings["none"] == new_action
    assert dialog._scroll_bindings[other_modifier] == none_old_action
    assert dialog._scroll_combos[other_modifier].currentData() == none_old_action

    result = dialog.result_settings()
    assert result.scroll_bindings == dialog._scroll_bindings
    # Still a valid permutation -- every action still reachable by exactly one modifier.
    assert sorted(result.scroll_bindings.values()) == sorted(SCROLL_ACTIONS)


def test_apply_settings_propagates_to_plots_and_actions(qapp, isolated_config):
    win = MainWindow()

    custom_bindings = {"none": "y_zoom", "ctrl": "pan", "shift": "x_zoom"}  # deliberately != default
    new_settings = Settings(
        auto_redraw=False,
        downsample_pixel_ratio=9,
        scroll_bindings=custom_bindings,
        shortcuts={**win.settings.shortcuts, "redraw": "Ctrl+Shift+R", "home": ""},
    )
    win.apply_settings(new_settings)

    assert win.plot_grid.auto_redraw is False
    assert win.plot_grid.downsample_pixel_ratio == 9
    assert win.plot_grid.scroll_bindings == custom_bindings
    for plot in win.plot_grid.plots:
        assert plot.auto_redraw is False
        assert plot.downsample_pixel_ratio == 9
        assert plot.scroll_bindings == custom_bindings

    assert win._actions["redraw"].shortcut() == QKeySequence("Ctrl+Shift+R")
    assert win._actions["home"].shortcut().isEmpty()

    win.close()


def test_progress_bar_visible_while_downsampling(qapp, isolated_config, sample_parquet_path):
    win = MainWindow()
    win.resize(200, 200)  # small width -> low n_out -> downsampling triggers easily
    win.show()
    win.load_parquet(str(sample_parquet_path))
    qapp.processEvents()

    assert win._progress_bar.isVisible() is False

    visibility_events = []
    win.plot_grid.busyChanged.connect(visibility_events.append)

    name = win.variable_panel.item(0).text()
    win._on_variable_dropped(win.plot_grid.plots[0], name)
    for _ in range(50):
        qapp.processEvents()

    assert True in visibility_events
    assert visibility_events[-1] is False
    assert win._progress_bar.isVisible() is False

    win.close()
