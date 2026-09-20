from __future__ import annotations

import pytest
from parquet_analyzer import config

from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.settings import (
    DEFAULT_SCROLL_BINDINGS,
    Settings,
    load_settings,
    normalize_scroll_bindings,
    push_recent_folder,
    save_settings,
)


def test_settings_roundtrip(isolated_config):
    settings = load_settings()
    for folder in ["/tmp/a", "/tmp/b", "/tmp/c", "/tmp/d", "/tmp/e", "/tmp/f"]:
        push_recent_folder(settings, folder)

    assert len(settings.recent_data_folders) == 5
    assert settings.recent_data_folders[0] == "/tmp/f"  # most recent first
    assert "/tmp/a" not in settings.recent_data_folders  # oldest evicted past the 5-slot cap

    save_settings(settings)
    assert load_settings() == settings


def test_push_recent_folder_dedupes_and_moves_to_front(isolated_config):
    settings = load_settings()
    push_recent_folder(settings, "/tmp/a")
    push_recent_folder(settings, "/tmp/b")
    push_recent_folder(settings, "/tmp/a")  # re-visit
    assert settings.recent_data_folders == ["/tmp/a", "/tmp/b"]
    assert settings.last_data_folder == "/tmp/a"


def test_load_settings_falls_back_to_defaults_on_malformed_json(isolated_config):
    """Regression test: load_settings() had no error handling and is called unguarded
    as the first thing MainWindow does, so a corrupt settings.json (e.g. from a crash
    mid-write) used to prevent the app from launching at all.
    """
    path = config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"auto_redraw": tru', encoding="utf-8")  # truncated/invalid JSON

    assert load_settings() == Settings()


def test_load_settings_falls_back_when_shortcuts_has_wrong_type(isolated_config):
    path = config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"shortcuts": null}', encoding="utf-8")

    settings = load_settings()
    assert isinstance(settings.shortcuts, dict)
    assert settings.shortcuts  # backfilled from DEFAULT_SHORTCUTS


def test_normalize_scroll_bindings_accepts_a_valid_permutation():
    custom = {"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"}
    assert normalize_scroll_bindings(custom) == custom


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not a dict",
        {},
        {"none": "x_zoom", "ctrl": "y_zoom"},  # missing "shift"
        {"none": "x_zoom", "ctrl": "y_zoom", "shift": "y_zoom"},  # "y_zoom" used twice, "pan" unreachable
        {"none": "x_zoom", "ctrl": "y_zoom", "shift": "not_a_real_action"},
    ],
)
def test_normalize_scroll_bindings_falls_back_to_default_on_anything_invalid(bad):
    """A partial/corrupt mapping (missing modifier, duplicate action, unknown action)
    is rejected wholesale rather than patched -- a partly-invalid mapping could leave
    an action unreachable by scroll at all, which TimePlotWidget.wheelEvent has no way
    to detect or recover from later.
    """
    assert normalize_scroll_bindings(bad) == DEFAULT_SCROLL_BINDINGS


def test_load_settings_falls_back_when_scroll_bindings_is_corrupt(isolated_config):
    path = config.PARQUET_ANALYZER_CFG_DIR / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"scroll_bindings": {"none": "x_zoom", "ctrl": "x_zoom", "shift": "pan"}}', encoding="utf-8")

    settings = load_settings()
    assert settings.scroll_bindings == DEFAULT_SCROLL_BINDINGS


@pytest.mark.parametrize("bad_name", ["/tmp/evil", "../escape", "a/b", "", "   "])
def test_view_path_rejects_unsafe_view_names(isolated_config, bad_name):
    """Regression test: view_path() built the save path directly from the
    user-typed view name. Path.__truediv__ treats a right-hand operand starting with
    "/" as an absolute path, discarding the configured data directory entirely, so a
    view name like "/tmp/evil" silently wrote outside data/parquet_analyzer/.
    """
    with pytest.raises(ValueError):
        view_io.view_path(bad_name)


def test_view_roundtrip(isolated_config, sample_parquet_path):
    view = view_io.View(
        view_name="test_view",
        source=view_io.SourceDef(parquet_path=str(sample_parquet_path), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="p1", series=[view_io.SeriesDef(variable="a", color="#111111")])],
        derived_variables=[view_io.DerivedVariableDef(name="a_delta", expression="delta(a)")],
        x_axis_range=(0, 1000),
    )
    view_io.save_view(view)

    assert "test_view" in view_io.list_views()

    loaded = view_io.load_view("test_view")
    assert loaded == view
    assert view_io.resolve_source_path(loaded) == sample_parquet_path
