from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

_RECENT_FOLDERS_MAX = 5

# The FFT toolbar button / shortcut row are gated by features.is_enabled("fft")
# (detailed_specification.md 21); the "fft" entry stays in DEFAULT_SHORTCUTS below.

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
    "xy_plot": "",  # the "Graphs" menu's XY / 3D entry; gated by features "xy_plot" (25.2, 25.5)
    "save_view": "",
    "load_view": "",
    "toggle_downsample": "",
    "cursor": "",
    "hover_values": "",  # hover value readout toggle (plots 39.3)
    "export_image": "",
    "user_functions": "",  # toolbar entries gated by feature flags (21)
    "maps": "",
    "remove_series": "Del",  # removes the selected series (detailed_specification.md 23.3)
}

# Shortcut ids removed from the app; a settings.json that still has them loads fine and loses them.
# "xyz_3d": the 3D view became part of the XY plot window (detailed_specification.md 25.5).
RETIRED_SHORTCUTS: tuple[str, ...] = ("xyz_3d",)

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


DEFAULT_LOG_RETENTION_DAYS = 7


def normalize_log_retention_days(value: object) -> int:
    """An int in 1..365, else the default (detailed_specification.md 30.1)."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 365:
        return value
    return DEFAULT_LOG_RETENTION_DAYS


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
    last_export_folder: str | None = None  # detailed_specification.md 19.2
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
    # Main-log and operation-log files older than this many days are deleted at startup
    # (detailed_specification.md 30.1). Always normalize via normalize_log_retention_days().
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    # Toolbar "show values" toggle: hover line + value readout (detailed_specification.md 39.3).
    hover_values_enabled: bool = True
    # Map manager: sizes of the [table, graphs] splitter (detailed_specification.md 27.8).
    # Normalize with normalize_splitter_sizes(); None = default proportions.
    map_manager_splitter: list[int] | None = None


# Value ranges of the settings dialog's spin boxes (detailed_specification.md 9.1); a stored
# value outside them (hand-edited file) is replaced by the default.
DOWNSAMPLE_RATIO_RANGE = (1, 20)
EAGER_LOAD_LIMIT_MB_RANGE = (1, 100_000)


def _int_in_range(value: object, lo_hi: tuple[int, int], default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and lo_hi[0] <= value <= lo_hi[1]:
        return value
    return default


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def normalize_splitter_sizes(value: object) -> list[int] | None:
    """Two positive ints, else None (27.8)."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value)
    ):
        return list(value)
    return None


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
        payload = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM
        known_fields = {f for f in Settings.__dataclass_fields__}
        settings = Settings(**{k: v for k, v in payload.items() if k in known_fields})
        if not isinstance(settings.shortcuts, dict):
            settings.shortcuts = {}
        # Backfill any shortcut ids added since this file was last saved.
        settings.shortcuts = {**DEFAULT_SHORTCUTS, **settings.shortcuts}
        for retired in RETIRED_SHORTCUTS:  # ids that no longer exist: drop them rather than keep them forever
            settings.shortcuts.pop(retired, None)
        settings.scroll_bindings = normalize_scroll_bindings(settings.scroll_bindings)
        settings.log_retention_days = normalize_log_retention_days(settings.log_retention_days)
        if not isinstance(settings.hover_values_enabled, bool):
            settings.hover_values_enabled = True
        settings.map_manager_splitter = normalize_splitter_sizes(settings.map_manager_splitter)
        if settings.language not in LANGUAGES:
            settings.language = DEFAULT_LANGUAGE
        _normalize_types(settings)
        return settings
    except Exception:
        logger.exception("failed to load %s; falling back to default settings", path)
        return Settings()


def _normalize_types(s: Settings) -> None:
    """Replace every field whose JSON type/range is wrong by its default (persistence 35.1 / app 12: a type-corrupt
    settings.json must not make the app fail later, e.g. a str `downsample_pixel_ratio`
    raised in the settings dialog and a 0 broke the downsampler)."""
    d = Settings()
    s.downsample_pixel_ratio = _int_in_range(s.downsample_pixel_ratio, DOWNSAMPLE_RATIO_RANGE, d.downsample_pixel_ratio)
    s.eager_load_limit_mb = _int_in_range(s.eager_load_limit_mb, EAGER_LOAD_LIMIT_MB_RANGE, d.eager_load_limit_mb)
    if not isinstance(s.auto_redraw, bool):
        s.auto_redraw = d.auto_redraw
    if not isinstance(s.default_overlay, bool):
        s.default_overlay = d.default_overlay
    s.shortcuts = {k: v for k, v in s.shortcuts.items() if isinstance(v, str)}
    s.shortcuts = {**DEFAULT_SHORTCUTS, **s.shortcuts}  # a dropped (non-str) entry gets its default back
    for retired in RETIRED_SHORTCUTS:
        s.shortcuts.pop(retired, None)
    s.window_geometry = _opt_str(s.window_geometry)
    s.window_state = _opt_str(s.window_state)
    s.last_data_folder = _opt_str(s.last_data_folder)
    s.last_export_folder = _opt_str(s.last_export_folder)
    folders = s.recent_data_folders if isinstance(s.recent_data_folders, list) else []
    s.recent_data_folders = [f for f in folders if isinstance(f, str)][:_RECENT_FOLDERS_MAX]
    if not isinstance(s.schema_version, str):
        s.schema_version = d.schema_version


def save_settings(settings: Settings) -> bool:
    """Write settings.json; True on success. A failure (read-only/missing cfg dir, disk full)
    is logged and returns False instead of raising: callers include `closeEvent` and slots,
    where an exception either aborts the close or surfaces as an unhandled error, and losing
    one settings write is not worth that (persistence 35.1). The previous file is left untouched."""
    path = settings_path()
    # Write to a temp file and rename over the target so a crash/power-loss mid-write
    # can't leave a truncated, unparseable settings.json behind (os.replace is atomic
    # on both POSIX and Windows).
    tmp_path = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        logger.exception("failed to save %s; settings were not written", path)
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return False
    return True


def push_recent_folder(settings: Settings, folder: str) -> None:
    """Move `folder` to the front of the MRU list, capped at 5 entries (specification.md 5.9)."""
    folders = [f for f in settings.recent_data_folders if f != folder]
    folders.insert(0, folder)
    settings.recent_data_folders = folders[:_RECENT_FOLDERS_MAX]
    settings.last_data_folder = folder
