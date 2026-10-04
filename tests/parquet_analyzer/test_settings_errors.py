"""Abnormal input for settings, config paths, the settings dialog and i18n
(persistence.ja.md 9, app.ja.md 12)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer import config
from parquet_analyzer.io import settings as S
from parquet_analyzer.io.settings import Settings, load_settings, save_settings
from parquet_analyzer.ui import i18n
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.settings_dialog import SettingsDialog

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _write(payload):
    path = S.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


# -- load_settings: unreadable files -------------------------------------------------------


@pytest.mark.parametrize(
    "content", ["", "   ", "{", "[]", "null", "5", '"x"', b"\x00\xff\xfe", "{'a': 1}", "[" * 5000 + "]" * 5000]
)
def test_unreadable_or_non_object_settings_fall_back_to_defaults(isolated_config, content):
    _write(content)
    assert load_settings() == Settings()


def test_missing_settings_file_gives_defaults(isolated_config):
    assert load_settings() == Settings()


def test_settings_path_is_a_directory_gives_defaults(isolated_config):
    S.settings_path().mkdir(parents=True)
    assert load_settings() == Settings()


def test_bom_is_tolerated_and_keeps_the_user_values(isolated_config):
    """A BOM used to make the whole file unreadable: every setting silently reset."""
    _write(b"\xef\xbb\xbf" + json.dumps({"language": "ja", "downsample_pixel_ratio": 5}).encode())
    s = load_settings()
    assert s.language == "ja" and s.downsample_pixel_ratio == 5


def test_unknown_keys_are_ignored(isolated_config):
    _write({"zzz": 1, "language": "ja"})
    assert load_settings().language == "ja"


# -- load_settings: wrong types / ranges, field by field --------------------------------------

D = Settings()


@pytest.mark.parametrize(
    "key,value",
    [
        ("downsample_pixel_ratio", "x"),
        ("downsample_pixel_ratio", 0),
        ("downsample_pixel_ratio", -5),
        ("downsample_pixel_ratio", 21),
        ("downsample_pixel_ratio", 10**9),
        ("downsample_pixel_ratio", 2.5),
        ("downsample_pixel_ratio", None),
        ("downsample_pixel_ratio", True),
        ("downsample_pixel_ratio", [2]),
        ("eager_load_limit_mb", "abc"),
        ("eager_load_limit_mb", 0),
        ("eager_load_limit_mb", -1),
        ("eager_load_limit_mb", 10**15),
        ("eager_load_limit_mb", None),
        ("eager_load_limit_mb", 1.5),
        ("auto_redraw", "false"),
        ("auto_redraw", None),
        ("auto_redraw", 0),
        ("default_overlay", "yes"),
        ("default_overlay", 1),
        ("hover_values_enabled", 1),
        ("hover_values_enabled", "no"),
        ("language", 5),
        ("language", None),
        ("language", ["en"]),
        ("language", "fr"),
        ("log_retention_days", "7"),
        ("log_retention_days", 0),
        ("log_retention_days", 366),
        ("log_retention_days", 1e9),
        ("log_retention_days", True),
        ("map_manager_splitter", [0, 5]),
        ("map_manager_splitter", [1, 2, 3]),
        ("map_manager_splitter", "ab"),
        ("scroll_bindings", "x"),
        ("scroll_bindings", {"none": "pan"}),
        ("scroll_bindings", {"none": "pan", "ctrl": "pan", "shift": "pan"}),
        ("schema_version", 5),
        ("shortcuts", ["x"]),
        ("shortcuts", None),
    ],
)
def test_bad_field_value_is_replaced_by_the_default(isolated_config, key, value):
    _write({key: value})
    got = load_settings()
    if key == "schema_version":
        assert got.schema_version == "1"
    elif key == "shortcuts":
        assert got.shortcuts == D.shortcuts
    else:
        assert getattr(got, key) == getattr(D, key)


@pytest.mark.parametrize(
    "key,value",
    [
        ("last_data_folder", 5),
        ("last_data_folder", ["a"]),
        ("last_export_folder", ["a"]),
        ("last_export_folder", {}),
        ("window_geometry", 123),
        ("window_state", ["a"]),
    ],
)
def test_bad_optional_string_becomes_none(isolated_config, key, value):
    _write({key: value})
    assert getattr(load_settings(), key) is None


@pytest.mark.parametrize("bad", ["abc", None, 5, {"a": 1}])
def test_recent_folders_not_a_list_becomes_empty(isolated_config, bad):
    _write({"recent_data_folders": bad})
    assert load_settings().recent_data_folders == []


def test_recent_folders_keep_only_strings_and_cap_at_five(isolated_config):
    _write({"recent_data_folders": [1, None, "a", "b", ["x"], "c", "d", "e", "f", "g"]})
    assert load_settings().recent_data_folders == ["a", "b", "c", "d", "e"]


def test_shortcut_entries_with_non_string_values_get_their_default_back(isolated_config):
    _write({"shortcuts": {"home": 5, "redraw": None, "stats": "Ctrl+T", "custom_unknown": "Z"}})
    sc = load_settings().shortcuts
    assert sc["home"] == S.DEFAULT_SHORTCUTS["home"]
    assert sc["redraw"] == S.DEFAULT_SHORTCUTS["redraw"]
    assert sc["stats"] == "Ctrl+T" and sc["custom_unknown"] == "Z"


def test_valid_values_survive_unchanged(isolated_config):
    _write({"downsample_pixel_ratio": 20, "eager_load_limit_mb": 1, "log_retention_days": 365, "language": "ja"})
    s = load_settings()
    assert (s.downsample_pixel_ratio, s.eager_load_limit_mb, s.log_retention_days, s.language) == (20, 1, 365, "ja")


def test_one_bad_field_does_not_reset_the_others(isolated_config):
    _write({"downsample_pixel_ratio": "x", "language": "ja", "last_data_folder": "/data"})
    s = load_settings()
    assert s.language == "ja" and s.last_data_folder == "/data" and s.downsample_pixel_ratio == 2


def test_retired_shortcut_is_dropped(isolated_config):
    _write({"shortcuts": {"xyz_3d": "Ctrl+3"}})
    assert "xyz_3d" not in load_settings().shortcuts


# -- save_settings ----------------------------------------------------------------------------


def test_save_then_load_round_trips(isolated_config):
    s = Settings(language="ja", recent_data_folders=["/a", "/日本語"], last_export_folder="/e")
    assert save_settings(s) is True
    assert load_settings() == s


def test_failed_write_keeps_the_old_file_and_leaves_no_temp_file(isolated_config, monkeypatch):
    save_settings(Settings(language="ja"))
    path = S.settings_path()
    before = path.read_text(encoding="utf-8")
    real = Path.write_text

    def half(self, data, *a, **k):
        real(self, data[:5], *a, **k)
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", half)
        assert save_settings(Settings(language="en")) is False
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_failed_replace_returns_false_and_keeps_old_file(isolated_config, monkeypatch):
    save_settings(Settings(language="ja"))
    with monkeypatch.context() as m:
        m.setattr(S.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
        assert save_settings(Settings(language="en")) is False
    assert load_settings().language == "ja"
    assert [p.name for p in S.settings_path().parent.iterdir()] == ["settings.json"]


def test_cfg_dir_that_is_a_file_does_not_raise(isolated_config):
    """Used to raise FileExistsError straight out of closeEvent / slots."""
    config.PARQUET_ANALYZER_CFG_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PARQUET_ANALYZER_CFG_DIR.write_text("a file")
    assert save_settings(Settings()) is False
    assert load_settings() == Settings()


def test_window_close_survives_a_failing_settings_write(qapp, isolated_config, monkeypatch):
    """closeEvent called save_settings unguarded: an OSError escaped the Qt override."""
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    w = MainWindow()
    with monkeypatch.context() as m:
        m.setattr(S.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
        errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda *a: errors.append(a)
        try:
            w.close()
        finally:
            sys.excepthook = old_hook
    assert errors == []


def test_push_recent_folder_caps_dedups_and_sets_last(isolated_config):
    s = Settings()
    for f in ["a", "b", "c", "d", "e", "f", "c"]:
        S.push_recent_folder(s, f)
    assert s.recent_data_folders == ["c", "f", "e", "d", "b"]
    assert s.last_data_folder == "c"


@pytest.mark.parametrize("v", [0, -1, 366, "7", None, 7.0, True, [7]])
def test_normalize_log_retention_days_rejects_bad_values(v):
    assert S.normalize_log_retention_days(v) == S.DEFAULT_LOG_RETENTION_DAYS


@pytest.mark.parametrize("v", [None, "x", [1], [0, 1], [-1, 5], [1.5, 2], [True, 2], [1, 2, 3], {"a": 1}])
def test_normalize_splitter_sizes_rejects_bad_values(v):
    assert S.normalize_splitter_sizes(v) is None


# -- settings dialog -------------------------------------------------------------------------


@pytest.mark.parametrize("ratio,expected", [(1, 1), (20, 20)])
def test_dialog_shows_boundary_values(qapp, ratio, expected):
    d = SettingsDialog(Settings(downsample_pixel_ratio=ratio))
    assert d.downsample_spin.value() == expected


@pytest.mark.parametrize("val", [0, -3, 999, 10**9])
def test_dialog_clamps_out_of_range_ints(qapp, val):
    d = SettingsDialog(Settings(downsample_pixel_ratio=val, eager_load_limit_mb=val))
    assert 1 <= d.downsample_spin.value() <= 20
    assert 1 <= d.eager_load_limit_spin.value() <= 100_000
    r = d.result_settings()
    assert 1 <= r.downsample_pixel_ratio <= 20


def test_dialog_opens_on_a_settings_file_full_of_garbage(qapp, isolated_config):
    _write(
        {
            "downsample_pixel_ratio": "x",
            "eager_load_limit_mb": None,
            "language": 5,
            "scroll_bindings": "q",
            "shortcuts": {"home": 5},
            "log_retention_days": "x",
        }
    )
    d = SettingsDialog(load_settings())
    r = d.result_settings()
    assert r.language == "en" and r.downsample_pixel_ratio == 2


def test_dialog_unknown_shortcut_ids_in_settings_do_not_break_it(qapp):
    s = Settings()
    s.shortcuts = {**s.shortcuts, "bogus": "Ctrl+Q"}
    d = SettingsDialog(s)
    assert "bogus" not in d.result_settings().shortcuts


def test_main_window_starts_with_corrupt_settings_and_corrupt_layout(qapp, isolated_config, monkeypatch):
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    _write({"window_geometry": "!!!not base64!!!", "window_state": "AAAA", "downsample_pixel_ratio": "x", "language": "xx"})
    w = MainWindow()
    assert w.settings.downsample_pixel_ratio == 2
    w.close()


# -- config.py -------------------------------------------------------------------------------


def _run_config(env_extra, code):
    env = {**os.environ, "PYTHONPATH": SRC, **env_extra}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)


def test_root_env_var_redirects_all_dirs_even_to_a_missing_path(tmp_path):
    root = tmp_path / "does" / "not" / "exist"
    r = _run_config(
        {"PARQUET_ANALYZER_ROOT": str(root)},
        "from parquet_analyzer import config as c; print(c.PARQUET_ANALYZER_CFG_DIR); print(c.PARQUET_ANALYZER_DATA_DIR);"
        "print(c.PARQUET_ANALYZER_LOG_DIR); import pathlib; print(pathlib.Path(c.REPO_ROOT).exists())",
    )
    assert r.returncode == 0, r.stderr
    cfg, data, log, exists = r.stdout.split("\n")[:4]
    resolved = root.resolve()
    assert Path(cfg) == resolved / "cfg" / "parquet_analyzer"
    assert Path(data) == resolved / "data" / "parquet_analyzer"
    assert Path(log) == resolved / "log" / "parquet_analyzer"
    assert exists == "False"  # importing never creates anything


def test_ensure_dirs_creates_all_three_and_is_idempotent(tmp_path):
    r = _run_config(
        {"PARQUET_ANALYZER_ROOT": str(tmp_path / "r")},
        "from parquet_analyzer import config as c; c.ensure_dirs(); c.ensure_dirs()",
    )
    assert r.returncode == 0, r.stderr
    for sub in ("cfg", "data", "log"):
        assert (tmp_path / "r" / sub / "parquet_analyzer").is_dir()


def test_ensure_dirs_with_a_file_in_the_way_raises_oserror(tmp_path):
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "cfg").write_text("a file")
    r = _run_config(
        {"PARQUET_ANALYZER_ROOT": str(tmp_path / "r")},
        "from parquet_analyzer import config as c\n"
        "try:\n c.ensure_dirs()\nexcept OSError as e:\n print('OSError')\n",
    )
    assert r.stdout.strip() == "OSError"


def test_empty_root_env_var_is_ignored(tmp_path):
    r = _run_config({"PARQUET_ANALYZER_ROOT": ""}, "from parquet_analyzer import config as c; print(c.REPO_ROOT)")
    assert r.returncode == 0 and Path(r.stdout.strip()) == Path(SRC).parent


def test_config_import_pulls_in_no_gui_modules():
    r = _run_config(
        {},
        "import sys; from parquet_analyzer import config; print(any(m.startswith(('PySide6','pyqtgraph','polars')) for m in sys.modules))",
    )
    assert r.stdout.strip() == "False"


# -- i18n ------------------------------------------------------------------------------------

_FIELD = re.compile(r"\{([^{}!:]*)[^{}]*\}")


def test_every_string_has_non_empty_en_and_ja():
    for key, entry in i18n._STRINGS.items():
        assert entry.get("en") and entry.get("ja"), key


def test_en_and_ja_use_the_same_placeholders():
    """A mismatched placeholder raises KeyError at runtime in one language only."""
    for key, entry in i18n._STRINGS.items():
        assert set(_FIELD.findall(entry["en"])) == set(_FIELD.findall(entry["ja"])), key


def test_unknown_key_raises_keyerror_and_missing_placeholder_arg_raises():
    with pytest.raises(KeyError):
        i18n.tr("no.such.key")
    with pytest.raises(KeyError):
        i18n.tr("view.saved", wrong=1)  # the template needs {path}


def test_tr_without_kwargs_returns_the_raw_template_and_extra_kwargs_are_ignored():
    assert i18n.tr("view.saved") == i18n._STRINGS["view.saved"][i18n.get_language()]
    assert "x" in i18n.tr("view.saved", path="x", unused=1)


def test_tr_with_braces_in_argument_is_not_reformatted():
    assert "{y}" in i18n.tr("view.saved", path="{y}")


@pytest.mark.parametrize("lang", ["fr", "", None, 5, "EN"])
def test_set_language_falls_back_to_default(lang):
    keep = i18n.get_language()
    try:
        i18n.set_language(lang)  # type: ignore[arg-type]
        assert i18n.get_language() == i18n.DEFAULT_LANGUAGE
    finally:
        i18n.set_language(keep)


def test_missing_language_entry_falls_back_to_english(monkeypatch):
    monkeypatch.setitem(i18n._STRINGS, "t.only_en", {"en": "hello"})
    keep = i18n.get_language()
    try:
        i18n.set_language("ja")
        assert i18n.tr("t.only_en") == "hello"
    finally:
        i18n.set_language(keep)


def test_all_label_ids_have_translations():
    for sid in S.DEFAULT_SHORTCUTS:
        assert i18n.shortcut_label(sid)
    for a in S.SCROLL_ACTIONS:
        assert i18n.scroll_action_label(a)
    for m in S.SCROLL_MODIFIERS:
        assert i18n.scroll_modifier_label(m)
    for lang in S.LANGUAGES:
        assert i18n.LANGUAGE_LABELS[lang]


# -- persistence 35.1: the dialog's OK warns when the save fails; other saves stay silent -------


def test_settings_dialog_ok_warns_when_the_save_fails(qapp, isolated_config, monkeypatch):
    from PySide6.QtWidgets import QDialog

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(str(a[2]))))
    for n in ("information", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    w = MainWindow()
    config.PARQUET_ANALYZER_CFG_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PARQUET_ANALYZER_CFG_DIR.write_text("a file, so settings.json cannot be written")
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    w.open_settings_dialog()
    assert len(warnings) == 1 and "could not be saved" in warnings[0]
    w.close()


def test_settings_dialog_ok_is_silent_when_the_save_works(qapp, isolated_config, monkeypatch):
    from PySide6.QtWidgets import QDialog

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(1)))
    w = MainWindow()
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    w.open_settings_dialog()
    assert warnings == [] and S.settings_path().exists()
    w.close()


def test_closing_with_a_failing_save_shows_no_dialog(qapp, isolated_config, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(1)))
    w = MainWindow()
    config.PARQUET_ANALYZER_CFG_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PARQUET_ANALYZER_CFG_DIR.write_text("a file")
    w.close()
    assert warnings == []
