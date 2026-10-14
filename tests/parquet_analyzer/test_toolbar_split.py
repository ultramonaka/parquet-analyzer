"""Two toolbars (app 5.41 / chapter 48): layout, drop-down menus, flags, saved-state versioning."""
from __future__ import annotations

import base64

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QComboBox, QLabel, QMainWindow, QToolBar, QToolButton

from parquet_analyzer import features
from parquet_analyzer.io import settings as settings_io
from parquet_analyzer.ui import i18n
from parquet_analyzer.ui.main_window import MainWindow


def _layout(toolbar: QToolBar, win: MainWindow) -> list[str]:
    """Item names in order: `_actions` key, widget id for widgets, '|' for a separator."""
    by_action = {id(a): k for k, a in win._actions.items()}
    out = []
    for action in toolbar.actions():
        if action.isSeparator():
            out.append("|")
            continue
        w = toolbar.widgetForAction(action)
        if isinstance(w, QToolButton) and w.menu() is not None:
            out.append(f"[{w.objectName()}]")
        elif isinstance(w, QLabel):
            out.append("label")
        elif isinstance(w, QComboBox):
            out.append("combo")
        else:
            out.append(by_action.get(id(action), action.text()))
    return out


@pytest.fixture
def win(qapp, isolated_config):
    w = MainWindow()
    yield w
    w.close()


def test_two_toolbars_in_order_and_no_main_toolbar(win):
    assert win.findChild(QToolBar, "main_toolbar") is None
    fv = win.findChild(QToolBar, "file_view_toolbar")
    an = win.findChild(QToolBar, "analysis_toolbar")
    assert fv is not None and an is not None
    assert win.toolBarArea(fv) == win.toolBarArea(an) == Qt.ToolBarArea.TopToolBarArea
    assert _layout(fv, win) == [
        "open_parquet", "|", "label", "combo", "|", "[view_button]", "export_image", "|",
        "home", "redraw", "|", "add_plot", "|", "cursor", "hover_values",
    ]
    layout_an = _layout(an, win)
    assert layout_an[0] == "stats"
    assert layout_an[-2:] == ["|", "[settings_button]"]
    i = layout_an.index("user_functions")
    assert layout_an[i - 1] == "|" and layout_an[i + 1] == "maps"
    assert layout_an[i + 2] == "signal_aliases" and layout_an[i + 3] == "|"  # persistence 54.4
    assert win._actions["save_view"] not in fv.actions()  # moved into the View menu
    assert win._actions["toggle_downsample"] not in an.actions()  # moved into the gear menu


def test_view_and_settings_menus(win):
    view_button = win.findChild(QToolButton, "view_button")
    assert view_button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert win.view_menu.objectName() == "view_menu"
    assert win.view_menu.actions() == [win._actions["save_view"], win._actions["load_view"]]
    assert [a.text() for a in win.view_menu.actions()] == ["Save...", "Manage..."]
    sb = win.findChild(QToolButton, "settings_button")
    assert sb.text() == "⚙" and sb.toolTip() == "Settings and display options"
    assert sb.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert win.settings_menu.objectName() == "settings_menu"
    assert win.settings_menu.toolTipsVisible()
    acts = win.settings_menu.actions()
    assert [a.isSeparator() for a in acts] == [False, False, True, False, False]
    assert acts[0] is win._actions["toggle_downsample"] and acts[0].isCheckable() and acts[0].isChecked()
    assert acts[1] is win._actions["toggle_signal_aliases"] and acts[1].isCheckable()  # 54.9
    assert [a.text() for a in acts[3:]] == ["Settings...", "Reset layout"]


def test_menu_actions_call_the_same_handlers(qapp, isolated_config, monkeypatch):
    calls = []
    monkeypatch.setattr(MainWindow, "save_view_dialog", lambda self: calls.append("save_view"))
    monkeypatch.setattr(MainWindow, "load_view_dialog", lambda self: calls.append("load_view"))
    monkeypatch.setattr(MainWindow, "open_settings_dialog", lambda self: calls.append("settings"))
    monkeypatch.setattr(MainWindow, "reset_layout", lambda self: calls.append("reset"))
    w = MainWindow()
    try:
        w.view_menu.actions()[0].trigger()
        w.view_menu.actions()[1].trigger()
        w.settings_menu.actions()[3].trigger()
        w.settings_menu.actions()[4].trigger()
        assert calls == ["save_view", "load_view", "settings", "reset"]
        ds = w.settings_menu.actions()[0]
        assert w._downsample_enabled is True
        ds.trigger()
        assert ds.isChecked() is False and w._downsample_enabled is False
        ds.trigger()
        assert ds.isChecked() is True and w._downsample_enabled is True
    finally:
        w.close()


def test_menu_shortcuts_work_with_menu_closed(qapp, isolated_config):
    s = settings_io.load_settings()
    s.shortcuts["toggle_downsample"] = "Ctrl+Shift+D"
    s.shortcuts["save_view"] = "Ctrl+Shift+V"
    settings_io.save_settings(s)
    w = MainWindow()
    try:
        for key, seq in (("toggle_downsample", "Ctrl+Shift+D"), ("save_view", "Ctrl+Shift+V")):
            a = w._actions[key]
            assert a.shortcut() == QKeySequence(seq)
            assert a in w.actions()  # live although the menu is closed
    finally:
        w.close()


def test_flags_off_leave_no_doubled_separator(qapp, isolated_config, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "user_functions", False)
    monkeypatch.setitem(features._EFFECTIVE, "maps", False)
    w = MainWindow()
    try:
        names = _layout(w.findChild(QToolBar, "analysis_toolbar"), w)
        assert "user_functions" not in names and "maps" not in names
        assert names[-2:] == ["|", "[settings_button]"]
        assert not any(a == b == "|" for a, b in zip(names, names[1:]))
        assert names[0] != "|"
    finally:
        w.close()


def test_old_state_with_main_toolbar_is_discarded_but_geometry_restored(qapp, isolated_config):
    old = QMainWindow()  # a state as the old build saved it: version 0, with `main_toolbar`
    tb = old.addToolBar("main")
    tb.setObjectName("main_toolbar")
    old_state = bytes(old.saveState())
    old.close()
    w = MainWindow()
    w.resize(777, 555)
    geo = bytes(w.saveGeometry())
    w.close()
    s = settings_io.load_settings()
    s.window_geometry = base64.b64encode(geo).decode("ascii")
    s.window_state = base64.b64encode(old_state).decode("ascii")
    settings_io.save_settings(s)
    w2 = MainWindow()  # must not raise
    try:
        for name in ("file_view_toolbar", "analysis_toolbar"):
            tb2 = w2.findChild(QToolBar, name)
            assert tb2 is not None
            assert w2.toolBarArea(tb2) == Qt.ToolBarArea.TopToolBarArea
        assert w2.settings.window_geometry == base64.b64encode(geo).decode("ascii")  # not discarded
        assert (w2.width(), w2.height()) == (777, 555)
    finally:
        w2.close()


def test_saved_state_round_trips_toolbar_positions(qapp, isolated_config):
    w = MainWindow()
    an = w.findChild(QToolBar, "analysis_toolbar")
    w.removeToolBar(an)
    w.addToolBar(Qt.ToolBarArea.BottomToolBarArea, an)
    an.show()
    w.close()  # saves
    w2 = MainWindow()
    try:
        an2 = w2.findChild(QToolBar, "analysis_toolbar")
        assert w2.toolBarArea(an2) == Qt.ToolBarArea.BottomToolBarArea
        assert w2.toolBarArea(w2.findChild(QToolBar, "file_view_toolbar")) == Qt.ToolBarArea.TopToolBarArea
        w2.reset_layout()
        assert w2.toolBarArea(an2) == Qt.ToolBarArea.TopToolBarArea
    finally:
        w2.close()


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_new_keys_translated_and_titles(qapp, isolated_config, lang):
    prev = i18n.get_language()
    try:
        st = settings_io.load_settings()
        st.language = lang
        settings_io.save_settings(st)
        i18n.set_language(lang)
        for key in (
            "toolbar.home", "toolbar.home_tooltip", "toolbar.save_view", "toolbar.load_view", "toolbar.functions",
            "toolbar.settings_item", "toolbar.view_menu", "toolbar.view_menu_tooltip",
            "toolbar.settings_menu_tooltip", "toolbar.file_view_title", "toolbar.analysis_title", "fnref.button",
            "shortcut.home", "shortcut.toggle_downsample", "shortcut.user_functions",
        ):
            assert i18n._STRINGS[key][lang]
        w = MainWindow()
        try:
            if lang == "ja":
                assert i18n.tr("toolbar.home") == "全体表示"
                assert i18n.tr("toolbar.functions") == "ユーザー関数"
                assert w.findChild(QToolBar, "file_view_toolbar").windowTitle() == "ファイル・表示"
                assert w.findChild(QToolBar, "analysis_toolbar").windowTitle() == "解析"
                assert w._actions["home"].toolTip() == "すべてのプロットをデータ全体の範囲に戻します"
            else:
                assert i18n.tr("fnref.button") == "Function list..."
                assert w.findChild(QToolBar, "file_view_toolbar").windowTitle() == "File && view"
        finally:
            w.close()
    finally:
        i18n.set_language(prev)
