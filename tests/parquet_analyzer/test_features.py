"""Developer feature flags (detailed_specification.md 21.3)."""

import logging
import sys

import pytest
from PySide6.QtWidgets import QToolBar

from parquet_analyzer import features
from parquet_analyzer.io.settings import Settings
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.settings_dialog import SettingsDialog

ENV = features.ENV_VAR


def test_defaults_match_spec():
    assert features.DEFAULT_FEATURES == {
        "fft": False, "histogram": True, "independent_x_axis": True, "xy_plot": True,
        "xyz_3d": True, "user_functions": True, "maps": True,
        "big_a_thin_first_draw": True,
        "big_c_windowed_derived": True,
        "big_d_time_index": True,
        "map_from_xy": True,
        "map_exclude_points": False,
    }


@pytest.mark.parametrize("text,expected", [
    ("fft=on", True), ("fft=off", False), ("fft=ON", True), ("FFT=on", None),
    ("fft=1", True), ("fft=0", False), ("fft=yes", True), ("fft=No", False),
    ("fft=true", True), ("fft=FALSE", False),
])
def test_parse_values(text, expected):
    got = features._parse_overrides(text)
    assert got.get("fft") == expected if expected is not None else got == {}


def test_parse_multiple_and_whitespace():
    assert features._parse_overrides(" fft=on , xy_plot=1 ") == {"fft": True, "xy_plot": True}


def test_unknown_name_and_bad_value_ignored_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=features.logger.name):
        got = features._parse_overrides("typo=on,fft=maybe,xy_plot=on,novalue")
    assert got == {"xy_plot": True}
    assert len(caplog.records) == 3


def test_empty_or_unset_gives_defaults():
    assert features._compute_effective({}, False) == features.DEFAULT_FEATURES
    assert features._compute_effective({ENV: ""}, False) == features.DEFAULT_FEATURES


def test_env_overrides_applied():
    eff = features._compute_effective({ENV: "fft=on"}, False)
    assert eff["fft"] is True and eff["maps"] is features.DEFAULT_FEATURES["maps"]  # untouched


def test_frozen_ignores_env(monkeypatch):
    assert features._compute_effective({ENV: "fft=on"}, True) == features.DEFAULT_FEATURES
    # Import-time path: the real module reads sys.frozen, so exercise that expression too.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert features._compute_effective({ENV: "fft=on"}, bool(getattr(sys, "frozen", False)))["fft"] is False


def test_is_enabled_unknown_raises():
    with pytest.raises(KeyError):
        features.is_enabled("typo")


def test_effective_features_returns_copy():
    d = features.effective_features()
    d["fft"] = not d["fft"]
    assert features.effective_features()["fft"] == features.is_enabled("fft")


def _toolbar_texts(win):
    return [a.text() for tb in win.findChildren(QToolBar) for a in tb.actions()]


@pytest.mark.parametrize("enabled", [False, True])
def test_fft_menu_entry_follows_flag(qapp, isolated_config, monkeypatch, enabled):
    """FFT is an entry of the toolbar's "Graphs" menu (25.5), not a standalone button."""
    monkeypatch.setitem(features._EFFECTIVE, "fft", enabled)
    win = MainWindow()
    assert tr("toolbar.fft") not in _toolbar_texts(win)
    button = win.graphs_button if hasattr(win, "graphs_button") else None
    entries = [a.text() for a in button.menu().actions()] if button else []
    assert (tr("graphs.fft") in entries) == enabled
    assert ("fft" in win._actions) == enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_fft_shortcut_row_follows_flag(qapp, monkeypatch, enabled):
    monkeypatch.setitem(features._EFFECTIVE, "fft", enabled)
    dialog = SettingsDialog(Settings())
    assert ("fft" in dialog._shortcut_edits) == enabled
