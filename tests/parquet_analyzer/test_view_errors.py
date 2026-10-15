"""Abnormal input for view persistence (persistence.ja.md 8/20/35, app.ja.md 12).

Hand-edited / damaged view files, odd view names, failing writes, and what the view
manager and `MainWindow.load_view` / `save_view_dialog` do about them.
"""

from __future__ import annotations

import copy
import json
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from parquet_analyzer import config
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog

NAN = float("nan")
INF = float("inf")

GOOD = {
    "schema_version": "1",
    "view_name": "t",
    "source": {"parquet_path": "x.parquet", "path_type": "relative"},
    "plots": [{"plot_id": "p1", "series": [{"variable": "a", "color": "#ff0000"}]}],
}


def _put(name, payload):
    d = config.PARQUET_ANALYZER_DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _good(**overrides):
    d = copy.deepcopy(GOOD)
    d.update(overrides)
    return d


def _with_plot(**plot_keys):
    d = copy.deepcopy(GOOD)
    d["plots"][0].update(plot_keys)
    return d


# -- io/view.py: unreadable files ------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "",  # empty file
        "   \n",
        '{"view_name":',  # truncated
        "{'view_name': 'x'}",  # not JSON (single quotes)
        "\x00\x01garbage",
        b"\xff\xfe\x00\x00",  # not UTF-8
        "[" * 5000 + "]" * 5000,  # absurdly nested
    ],
)
def test_unreadable_view_file_raises_and_never_returns_a_view(isolated_config, content):
    _put("t", content)
    with pytest.raises(Exception):  # JSONDecodeError / UnicodeDecodeError / RecursionError
        view_io.load_view("t")


@pytest.mark.parametrize("payload", ["[]", "null", "5", '"str"', "true"])
def test_non_object_top_level_is_a_format_error(isolated_config, payload):
    _put("t", payload)
    with pytest.raises(view_io.ViewFormatError) as ei:
        view_io.load_view("t")
    assert ei.value.path == "(top level)" and ei.value.expected == "object"


def test_utf8_bom_is_accepted(isolated_config):
    """Windows editors add a BOM; it used to fail the whole view with a JSON error."""
    _put("t", b"\xef\xbb\xbf" + json.dumps(GOOD).encode("utf-8"))
    assert view_io.load_view("t").view_name == "t"


def test_missing_view_file_raises_file_not_found(isolated_config):
    with pytest.raises(FileNotFoundError):
        view_io.load_view("nope")


def test_view_name_inside_the_file_need_not_match_the_file_name(isolated_config):
    _put("t", _good(view_name="something else"))
    assert view_io.load_view("t").view_name == "something else"


def test_unknown_keys_are_ignored_at_every_level(isolated_config):
    d = _good(future_key=[1, 2], source={**GOOD["source"], "extra": 1})
    d["plots"][0]["new_plot_key"] = {"a": 1}
    d["plots"][0]["series"][0]["new_series_key"] = None
    _put("t", d)
    assert view_io.load_view("t").plots[0].series[0].variable == "a"


# -- io/view.py: values that parse but must not reach pyqtgraph --------------------------


@pytest.mark.parametrize("rng", [[NAN, 1], [1, NAN], [-INF, INF], [NAN, NAN], [0, INF]])
def test_non_finite_x_axis_range_is_dropped(isolated_config, rng):
    """pyqtgraph raises on a NaN/inf range, which aborted the rest of the view load
   ."""
    _put("t", _good(x_axis_range=rng))
    assert view_io.load_view("t").x_axis_range is None


def test_finite_x_axis_range_is_kept_even_when_inverted_or_equal(isolated_config):
    for rng in ([5, 1], [3, 3], [-1e12, 1e12]):
        _put("t", _good(x_axis_range=rng))
        assert view_io.load_view("t").x_axis_range == (float(rng[0]), float(rng[1]))


@pytest.mark.parametrize(
    "rng,expected",
    [
        ([NAN, NAN], (None, None)),
        ([-INF, INF], (None, None)),
        ([1, INF], (1.0, None)),
        ([None, 3], (None, 3.0)),
        ([None, None], (None, None)),
        ([-2, 5], (-2.0, 5.0)),
    ],
)
def test_non_finite_y_axis_range_ends_become_none(isolated_config, rng, expected):
    _put("t", _with_plot(y_axis_range=rng))
    assert view_io.load_view("t").plots[0].y_axis_range == expected


@pytest.mark.parametrize("bad", ["yes", "no", 0, 1, [], {}, None, "false"])
def test_downsample_enabled_must_be_a_real_bool(isolated_config, bad):
    """A str like "no" was passed through and later crashed QAction.setChecked, aborting the view load."""
    _put("t", _good(downsample={"enabled": bad}))
    assert view_io.load_view("t").downsample_enabled is True


@pytest.mark.parametrize("flag", [True, False])
def test_downsample_enabled_bool_is_kept(isolated_config, flag):
    _put("t", _good(downsample={"enabled": flag}))
    assert view_io.load_view("t").downsample_enabled is flag


@pytest.mark.parametrize("bad", [True, 3, [1], {}, None, ""])
def test_time_column_must_be_a_non_empty_string(isolated_config, bad):
    _put("t", _good(time_column=bad))
    assert view_io.load_view("t").time_column is None


@pytest.mark.parametrize("bad", [NAN, INF, -1, "x", True, None, [1]])
def test_height_ratio_garbage_becomes_none(isolated_config, bad):
    _put("t", _with_plot(height_ratio=bad))
    assert view_io.load_view("t").plots[0].height_ratio is None


@pytest.mark.parametrize("bad", ["no", 0, None, []])
def test_visible_and_x_linked_only_false_means_false(isolated_config, bad):
    d = _with_plot(x_linked=bad)
    d["plots"][0]["series"][0]["visible"] = bad
    _put("t", d)
    v = view_io.load_view("t")
    assert v.plots[0].x_linked is (bad is not False)  # only a literal false switches it off
    assert v.plots[0].series[0].visible is (bad is not False)


@pytest.mark.parametrize("bad", [[NAN, 1], ["a", 2], [1], [1, 2, 3], "ab", 5, [True, 2]])
def test_plot_x_axis_range_garbage_becomes_none(isolated_config, bad):
    _put("t", _with_plot(x_axis_range=bad))
    assert view_io.load_view("t").plots[0].x_axis_range is None


def test_relative_path_non_string_is_ignored(isolated_config):
    for bad in (5, "", None, [], {}):
        _put("t", _good(source={"parquet_path": "/x.parquet", "path_type": "absolute", "relative_path": bad}))
        assert view_io.load_view("t").source.relative_path is None


def test_path_type_null_defaults_to_relative(isolated_config):
    _put("t", _good(source={"parquet_path": "x.parquet", "path_type": None}))
    assert view_io.load_view("t").source.path_type == "relative"


def test_empty_plots_list_and_null_derived_are_fine(isolated_config):
    _put("t", _good(plots=[], derived_variables=None))
    v = view_io.load_view("t")
    assert v.plots == [] and v.derived_variables == []


def test_non_string_schema_version_does_not_break_loading(isolated_config):
    _put("t", _good(schema_version=2))
    assert view_io.load_view("t").view_name == "t"


# -- io/view.py: names -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["", " ", "\t\n", ".", "a/b", "../x", "/abs", "a/../b", "a\x00b"])
def test_invalid_view_names_are_rejected_before_touching_disk(isolated_config, name):
    with pytest.raises(ValueError):
        view_io.view_path(name)
    with pytest.raises(ValueError):
        view_io.save_view(view_io.View(name, view_io.SourceDef("x"), []))
    with pytest.raises(ValueError):
        view_io.delete_view(name)
    assert not config.PARQUET_ANALYZER_DATA_DIR.exists()  # nothing was created


@pytest.mark.parametrize("name", ["日本語のビュー", "with space", "a.b.c", ".hidden", "ünïcode", "x" * 100, "a'b\"c", "100%"])
def test_unusual_but_legal_names_round_trip(isolated_config, name):
    if sys.platform == "win32" and '"' in name:  # reserved in Windows file names -> clear ValueError
        with pytest.raises(ValueError):
            view_io.view_path(name)
        return
    view_io.save_view(view_io.View(name, view_io.SourceDef("/x.parquet", "absolute"), []))
    assert name in view_io.list_views()
    assert view_io.load_view(name).view_name == name
    view_io.delete_view(name)
    assert name not in view_io.list_views()


def test_name_longer_than_the_file_system_allows_raises_oserror_not_valueerror(isolated_config):
    with pytest.raises(OSError):
        view_io.save_view(view_io.View("x" * 400, view_io.SourceDef("/x.parquet", "absolute"), []))


def test_list_views_on_missing_data_dir_is_empty_and_ignores_other_files(isolated_config):
    assert view_io.list_views() == []
    _put("b", GOOD)
    _put("a", GOOD)
    (config.PARQUET_ANALYZER_DATA_DIR / "notes.txt").write_text("x")
    (config.PARQUET_ANALYZER_DATA_DIR / "leftover.json.tmp").write_text("x")
    assert view_io.list_views() == ["a", "b"]


def test_delete_missing_view_raises_file_not_found(isolated_config):
    config.PARQUET_ANALYZER_DATA_DIR.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        view_io.delete_view("ghost")


# -- io/view.py: saving ------------------------------------------------------------------


def _view(name="keep", var="a"):
    return view_io.View(
        name,
        view_io.SourceDef("/x.parquet", "absolute"),
        [view_io.PlotDef("p", [view_io.SeriesDef(var, "#123456")])],
    )


def test_failed_write_keeps_the_previous_view_and_leaves_no_temp_file(isolated_config, monkeypatch):
    """The old save_view wrote straight over the target: a disk-full error half way left a
    truncated, unreadable view and destroyed the one that was there (app 12)."""
    path = view_io.save_view(_view())
    before = path.read_text(encoding="utf-8")

    real = type(path).write_text

    def half_write(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(type(path), "write_text", half_write)
        with pytest.raises(OSError):
            view_io.save_view(_view(var="CHANGED"))

    assert path.read_text(encoding="utf-8") == before
    assert view_io.load_view("keep").plots[0].series[0].variable == "a"
    assert sorted(p.name for p in path.parent.iterdir()) == ["keep.json"]


def test_failed_replace_keeps_the_previous_view(isolated_config, monkeypatch):
    path = view_io.save_view(_view())
    before = path.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise PermissionError("locked")

    with monkeypatch.context() as m:
        m.setattr(view_io.os, "replace", boom)
        with pytest.raises(PermissionError):
            view_io.save_view(_view(var="CHANGED"))
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in path.parent.iterdir()] == ["keep.json"]


def test_data_dir_that_is_a_file_raises_oserror(isolated_config):
    config.PARQUET_ANALYZER_DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PARQUET_ANALYZER_DATA_DIR.write_text("i am a file")
    with pytest.raises(OSError):
        view_io.save_view(_view())
    assert view_io.list_views() == []  # a file where the folder should be: listing does not crash


def test_save_with_non_finite_ranges_round_trips_to_something_loadable(isolated_config):
    v = _view()
    v.x_axis_range = (NAN, INF)
    v.plots[0].y_axis_range = (NAN, 1.0)
    view_io.save_view(v)
    loaded = view_io.load_view("keep")
    assert loaded.x_axis_range is None
    assert loaded.plots[0].y_axis_range == (None, 1.0)


# -- io/view.py: source resolution -------------------------------------------------------


def test_resolve_source_unresolved_returns_the_saved_absolute_path(isolated_config, tmp_path):
    v = view_io.View("t", view_io.SourceDef(str(tmp_path / "gone.parquet"), "absolute", "../nope.parquet"), [])
    path, how = view_io.resolve_source(v)
    assert how == "unresolved" and path == tmp_path / "gone.parquet"


def test_resolve_source_relative_path_pointing_outside_is_only_used_if_it_exists(isolated_config, tmp_path):
    target = tmp_path / "moved.parquet"
    target.write_bytes(b"x")
    rel = view_io.relative_source_path("t", str(target))
    v = view_io.View("t", view_io.SourceDef("/definitely/missing.parquet", "absolute", rel), [])
    assert view_io.resolve_source(v) == (target.resolve(), "relative_path")


def test_resolve_source_empty_absolute_path_does_not_crash(isolated_config):
    v = view_io.View("t", view_io.SourceDef("", "absolute"), [])
    view_io.resolve_source(v)  # whatever it resolves to, the caller's open error names it


# -- ViewManagerDialog -------------------------------------------------------------------


@pytest.fixture
def boxes(monkeypatch):
    calls = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: calls.append((_n, str(a[2])))))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: calls.append(("question", str(a[2]))) or QMessageBox.StandardButton.Yes)
    )
    return calls


def _texts(dialog):
    out = []

    def walk(item):
        out.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i))
    return out


@pytest.mark.parametrize(
    "content",
    ["", "{", "[]", "null", b"\xff\xfe", json.dumps({"view_name": "x"}), json.dumps(_good(plots="hello"))],
)
def test_view_manager_shows_an_unreadable_view_without_raising(qapp, isolated_config, boxes, content):
    _put("broken", content)
    dialog = ViewManagerDialog({"a"})
    assert dialog.selected_view_name() == "broken"
    assert not dialog.load_button.isEnabled()
    assert dialog.delete_button.isEnabled()  # a broken view can still be deleted
    assert len(_texts(dialog)) == 1  # one red line: "cannot load this view: ..."
    assert boxes == []  # shown in the details, no dialog


def test_view_manager_directory_named_like_a_view_is_listed_but_unloadable(qapp, isolated_config, boxes):
    (config.PARQUET_ANALYZER_DATA_DIR / "folder.json").mkdir(parents=True)
    dialog = ViewManagerDialog(None)
    assert dialog.selected_view_name() == "folder"
    assert not dialog.load_button.isEnabled()


def test_view_manager_broken_view_does_not_hide_the_good_ones(qapp, isolated_config, boxes):
    _put("a_broken", "{")
    _put("b_good", _good(view_name="b_good"))
    dialog = ViewManagerDialog({"a"})
    assert [dialog.view_list.item(i).text() for i in range(dialog.view_list.count())] == ["a_broken", "b_good"]
    dialog.view_list.setCurrentRow(1)
    assert dialog.load_button.isEnabled()


@pytest.mark.parametrize("rng", [[NAN, 1], [-INF, INF], [1e300, 1e301], [-1e300, 1e300]])
def test_view_manager_detail_for_extreme_x_range_does_not_raise(qapp, isolated_config, boxes, rng):
    _put("v", _good(x_axis_range=rng))
    dialog = ViewManagerDialog({"a"})
    assert dialog.load_button.isEnabled()


def test_view_manager_view_with_cyclic_and_garbage_derived_variables(qapp, isolated_config, boxes):
    d = _good(
        derived_variables=[
            {"name": "d1", "expression": "d2 + 1"},
            {"name": "d2", "expression": "d1 + 1"},
            {"name": "d3", "expression": "((("},
            {"name": "d4", "expression": ""},
            {"name": "d5", "expression": "import os"},
            {"name": "", "expression": "a"},
        ]
    )
    _put("v", d)
    dialog = ViewManagerDialog({"a"})
    assert dialog.load_button.isEnabled()
    assert any("d1" in t for t in _texts(dialog))


def test_view_manager_delete_failure_warns_and_keeps_the_list(qapp, isolated_config, boxes, monkeypatch):
    _put("v", GOOD)

    def deny(name):
        raise PermissionError("read-only")

    monkeypatch.setattr(view_io, "delete_view", deny)
    dialog = ViewManagerDialog({"a"})
    deleted = []
    dialog.viewDeleted.connect(deleted.append)
    dialog.delete_selected()
    assert [c[0] for c in boxes] == ["question", "warning"]
    assert dialog.view_list.count() == 1 and deleted == []


def test_view_manager_delete_of_a_file_that_vanished_warns_instead_of_raising(qapp, isolated_config, boxes):
    path = _put("v", GOOD)
    dialog = ViewManagerDialog({"a"})
    path.unlink()  # removed behind the dialog's back
    dialog.delete_selected()
    assert [c[0] for c in boxes] == ["question", "warning"]
    assert dialog.view_list.count() == 1


def test_view_manager_delete_declined_does_nothing(qapp, isolated_config, boxes, monkeypatch):
    path = _put("v", GOOD)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dialog = ViewManagerDialog({"a"})
    dialog.delete_selected()
    assert path.exists() and dialog.view_list.count() == 1


def test_view_manager_without_any_views_has_nothing_to_load_or_delete(qapp, isolated_config, boxes):
    dialog = ViewManagerDialog(None)
    assert dialog.selected_view_name() is None
    assert not dialog.load_button.isEnabled() and not dialog.delete_button.isEnabled()
    dialog.load_selected()  # no-op
    dialog.delete_selected()  # no-op
    assert boxes == []


# -- MainWindow.load_view / save_view_dialog ---------------------------------------------


def _wait(win, qapp, timeout=10.0):
    end = time.monotonic() + timeout
    while win._busy_sources and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


@pytest.fixture
def win(qapp, isolated_config, tmp_path, boxes):
    n = 5000
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    path = tmp_path / "s.parquet"
    pq.write_table(
        pa.table({"time": pa.array(t), "a": np.sin(np.arange(n) / 50), "b": np.cos(np.arange(n) / 50)}), path
    )
    w = MainWindow()
    w.resize(800, 600)
    w.load_parquet(str(path))
    _wait(w, qapp)
    w._test_source = str(path)
    yield w
    w.close()


def _view_payload(win, **kw):
    d = {
        "view_name": "t",
        "source": {"parquet_path": win._test_source, "path_type": "absolute"},
        "plots": [{"plot_id": "p", "series": [{"variable": "a", "color": "#ff0000"}]}],
    }
    d.update(kw)
    return d


def _series(win):
    return [p.series_names() for p in win.plot_grid.plots]


def test_load_view_with_nan_ranges_still_restores_series_and_does_not_warn(win, qapp, boxes):
    """x/y NaN used to make pyqtgraph raise inside load_view: the series after it (and
    other plots) were never restored and a 'could not fully restore' warning appeared."""
    d = _view_payload(
        win,
        x_axis_range=[NAN, INF],
        plots=[
            {"plot_id": "p", "series": [{"variable": "a", "color": "#f00"}], "y_axis_range": [NAN, NAN]},
            {"plot_id": "q", "series": [{"variable": "b", "color": "#00f"}], "y_axis_range": [-INF, INF]},
        ],
    )
    _put("t", d)
    win.load_view("t")
    _wait(win, qapp)
    assert _series(win) == [["a"], ["b"]]
    assert boxes == []


@pytest.mark.parametrize("color", ["notacolor", "#12", "rgb(1,2", "ＸＸ"])
def test_load_view_with_unparseable_color_uses_an_automatic_color(win, qapp, boxes, color):
    """A bad colour string raised inside load_view and dropped the series plus everything after it."""
    d = _view_payload(
        win,
        plots=[{"plot_id": "p", "series": [{"variable": "a", "color": color}, {"variable": "b", "color": "#00f"}]}],
    )
    _put("t", d)
    win.load_view("t")
    _wait(win, qapp)
    assert _series(win) == [["a", "b"]]
    assert boxes == []


@pytest.mark.parametrize("bad", ["no", 0, [], None])
def test_load_view_with_non_bool_downsample_flag_keeps_the_default(win, qapp, boxes, bad):
    _put("t", _view_payload(win, downsample={"enabled": bad}))
    win.load_view("t")
    _wait(win, qapp)
    assert win._downsample_enabled is True
    assert _series(win) == [["a"]]
    assert boxes == []


def test_load_view_with_inverted_and_degenerate_ranges_still_loads(win, qapp, boxes):
    d = _view_payload(
        win,
        x_axis_range=[1.8e9, 1.7e9],
        plots=[{"plot_id": "p", "series": [{"variable": "a", "color": "#f00"}], "y_axis_range": [5, -5]}],
    )
    _put("t", d)
    win.load_view("t")
    _wait(win, qapp)
    assert _series(win) == [["a"]]
    for lo_hi in (*win.plot_grid.plots[0].getViewBox().viewRange(),):
        assert np.isfinite(lo_hi).all()


@pytest.mark.parametrize(
    "content",
    ["", "{", "[]", b"\xff\xfe", json.dumps({"plots": "hello"})],
)
def test_load_view_of_a_broken_file_shows_one_error_and_keeps_the_open_file(win, qapp, boxes, content):
    win.plot_grid.plots[0].add_series("a", win._time_values, win.variable_values("a"))
    _put("t", content)
    win.load_view("t")
    assert [b[0] for b in boxes] == ["critical"]
    assert win._data_source is not None  # nothing was torn down
    assert _series(win) == [["a"]]


def test_load_view_with_missing_name_shows_error(win, qapp, boxes):
    win.load_view("does_not_exist")
    assert [b[0] for b in boxes] == ["critical"]


def test_load_view_with_unusual_plot_shapes(win, qapp, boxes):
    """Zero plots / a plot with no series / 30 duplicated plots all load without raising."""
    for plots in ([], [{"plot_id": "p", "series": []}], [{"plot_id": f"p{i}", "series": [{"variable": "a", "color": "#f00"}]} for i in range(30)]):
        boxes.clear()
        _put("t", _view_payload(win, plots=plots))
        win.load_view("t")
        _wait(win, qapp)
        assert len(win.plot_grid.plots) >= 1
        assert boxes == []


@pytest.mark.parametrize("name", ["x" * 400])
def test_save_view_dialog_with_too_long_name_warns_instead_of_raising(win, boxes, monkeypatch, name):
    """OSError (File name too long) leaked out of the Qt slot; only ValueError was handled."""
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (name, True)))
    win.save_view_dialog()
    assert [b[0] for b in boxes] == ["warning"]
    assert view_io.list_views() == []


def test_save_view_dialog_with_unwritable_data_dir_warns(win, boxes, monkeypatch):
    config.PARQUET_ANALYZER_DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PARQUET_ANALYZER_DATA_DIR.write_text("a file, not a folder")
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v", True)))
    win.save_view_dialog()
    assert [b[0] for b in boxes] == ["warning"]


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\x00b"])
def test_save_view_dialog_rejects_or_ignores_bad_names(win, boxes, monkeypatch, name):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (name, True)))
    win.save_view_dialog()
    assert view_io.list_views() == []
    assert all(b[0] == "warning" for b in boxes)


def test_save_view_dialog_cancelled_writes_nothing(win, boxes, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v", False)))
    win.save_view_dialog()
    assert view_io.list_views() == [] and boxes == []


def test_failed_save_of_an_existing_view_keeps_it_loadable(win, qapp, boxes, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("v", True)))
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    win.save_view_dialog()
    before = view_io.view_path("v").read_text(encoding="utf-8")
    boxes.clear()
    with monkeypatch.context() as m:
        m.setattr(view_io.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "disk full")))
        win.save_view_dialog()
    assert [b[0] for b in boxes] == ["question", "warning"]  # the overwrite confirm (persistence 49.1), then the failure
    assert view_io.view_path("v").read_text(encoding="utf-8") == before
