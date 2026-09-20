from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

_RECENT_FOLDERS_MAX = 5

# FFT is temporarily hidden from the GUI while still under debugging (2026-09-19,
# user-requested). Flip back to True to restore its toolbar button
# (ui/main_window.py's _build_actions) and its settings-dialog shortcut row
# (ui/settings_dialog.py) -- nothing else needs to change. The underlying feature
# (MainWindow._on_fft_requested, core/analysis.py's fft()) is untouched and still
# covered by tests/parquet_analyzer/test_fft.py, which calls it directly rather than
# through the toolbar.
FFT_ENABLED = False

# Configurable-shortcut actions and their default key sequences ("" = unbound).
# Keys are stable ids referenced by ui/main_window.py's self._actions and by
# the settings dialog; values are the Qt key sequence string (QKeySequence).
DEFAULT_SHORTCUTS: dict[str, str] = {
    "home": "H",
    "redraw": "R",
    "open_parquet": "",
    "add_plot": "",
    "fft": "",
    "stats": "",
    "save_view": "",
    "load_view": "",
    "toggle_downsample": "",
    "cursor": "",
}

# Display labels for these ids (shortcuts, scroll actions/modifiers) live in
# ui/i18n.py, not here -- they're presentation strings translated per
# Settings.language, and io/ has no business holding UI text.

# Scroll-wheel modifier -> action assignment (detailed_specification.md 4章). "action"
# names, not Qt modifier constants directly, so io/ (which Settings lives in) stays
# GUI-toolkit-agnostic like the rest of this module.
SCROLL_ACTIONS: tuple[str, ...] = ("x_zoom", "y_zoom", "pan")
SCROLL_MODIFIERS: tuple[str, ...] = ("none", "ctrl", "shift")

# UI display language (ui/i18n.py). "en" is the default for a fresh install;
# existing users keep whatever they already had once this field round-trips through
# their settings.json.
LANGUAGES: tuple[str, ...] = ("en", "ja")
DEFAULT_LANGUAGE = "en"
# Changed 2026-09-19 to match the user's own real-world settings.json (read directly
# rather than guessed) after scroll_bindings became configurable -- no longer the
# original v2 fixed scheme (plain=time zoom, Ctrl=amp zoom, Shift=pan; see
# detailed_specification.md 4章's v3 history note for that scheme and why it's no
# longer what a fresh install gets).
DEFAULT_SCROLL_BINDINGS: dict[str, str] = {"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"}


def normalize_scroll_bindings(bindings: object) -> dict[str, str]:
    """Falls back to a copy of DEFAULT_SCROLL_BINDINGS wholesale unless `bindings` is
    exactly a permutation of SCROLL_ACTIONS across SCROLL_MODIFIERS (every modifier
    present exactly once, every action assigned exactly once) -- a partial/corrupt
    mapping (missing modifier, unknown action, two modifiers sharing one action) would
    leave some action unreachable by scroll at all, and TimePlotWidget.wheelEvent has
    no way to detect or recover from that at the point it actually needs a binding, so
    it's rejected wholesale here instead of trying to patch just the bad part.
    """
    if (
        isinstance(bindings, dict)
        and set(bindings.keys()) == set(SCROLL_MODIFIERS)
        and sorted(bindings.values()) == sorted(SCROLL_ACTIONS)
    ):
        return {k: bindings[k] for k in SCROLL_MODIFIERS}
    return dict(DEFAULT_SCROLL_BINDINGS)


@dataclass
class Settings:
    """cfg/parquet_analyzer/settings.json (detailed_specification.md 9章)."""

    schema_version: str = "1"
    downsample_pixel_ratio: int = 2
    auto_redraw: bool = True
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    window_geometry: str | None = None  # base64 QMainWindow.saveGeometry(), see ui/main_window.py
    window_state: str | None = None  # base64 QMainWindow.saveState() (dock/toolbar layout)
    last_data_folder: str | None = None
    recent_data_folders: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    recent_views: list[str] = field(default_factory=list)
    default_overlay: bool = False
    # A raw column whose materialized size (row_count * 8 bytes) would exceed this
    # stays a WindowedColumn (Phase D: fetches only the visible range from disk,
    # re-fetching on pan/zoom) instead of a LazyColumn (Phase C: reads the whole
    # column once, on first use, then keeps it resident) — detailed_specification.md
    # 13.5.1. Has a SettingsDialog entry (Phase E); only affects files opened after
    # the change, since a Column choice is made once, at open time.
    eager_load_limit_mb: int = 512
    # Which scroll-wheel modifier performs which action (detailed_specification.md
    # 4章). Always normalize with normalize_scroll_bindings() before trusting this --
    # e.g. a hand-edited settings.json could have a duplicate/missing entry.
    scroll_bindings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SCROLL_BINDINGS))
    language: str = DEFAULT_LANGUAGE


def settings_path() -> Path:
    return config.PARQUET_ANALYZER_CFG_DIR / "settings.json"


def load_settings() -> Settings:
    """Falls back to defaults (rather than raising) on any malformed/corrupt
    settings.json — this is called unguarded as the very first thing MainWindow does,
    so an exception here would prevent the app from starting at all with no in-app way
    to recover short of manually deleting the file.
    """
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        known_fields = {f for f in Settings.__dataclass_fields__}
        settings = Settings(**{k: v for k, v in payload.items() if k in known_fields})
        if not isinstance(settings.shortcuts, dict):
            settings.shortcuts = {}
        # Backfill any shortcut ids added since this file was last saved.
        settings.shortcuts = {**DEFAULT_SHORTCUTS, **settings.shortcuts}
        settings.scroll_bindings = normalize_scroll_bindings(settings.scroll_bindings)
        if settings.language not in LANGUAGES:
            settings.language = DEFAULT_LANGUAGE
        return settings
    except Exception:
        logger.exception("failed to load %s; falling back to default settings", path)
        return Settings()


def save_settings(settings: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename over the target so a crash/power-loss mid-write
    # can't leave a truncated, unparseable settings.json behind (os.replace is atomic
    # on both POSIX and Windows).
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def push_recent_folder(settings: Settings, folder: str) -> None:
    """Move `folder` to the front of the MRU list, capped at 5 entries (specification.md 5.9)."""
    folders = [f for f in settings.recent_data_folders if f != folder]
    folders.insert(0, folder)
    settings.recent_data_folders = folders[:_RECENT_FOLDERS_MAX]
    settings.last_data_folder = folder
