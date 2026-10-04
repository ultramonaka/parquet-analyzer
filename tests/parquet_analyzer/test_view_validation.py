"""Chapter 35: view file shape validation (persistence.ja.md 35, requirements 5.28)."""

from __future__ import annotations

import copy
import json

import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer import config
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog, view_error_text

GOOD = {
    "schema_version": "1",
    "view_name": "v",
    "source": {"parquet_path": "/x.parquet", "path_type": "absolute"},
    "derived_variables": [{"name": "d", "expression": "a+1"}],
    "plots": [
        {
            "plot_id": "p1",
            "series": [{"variable": "a", "color": "#ff0000"}],
            "y_axis_range": [None, 3],
            "x_axis_range": None,
        }
    ],
    "x_axis_range": [1, 2],
    "downsample": {"enabled": True},
}


def _put(name, payload):
    d = config.PARQUET_ANALYZER_DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))


def _mut(fn):
    p = copy.deepcopy(GOOD)
    fn(p)
    return p


def _walk(p, path):
    o = p
    for k in path[:-1]:
        o = o[k]
    return o


def _set(path, value):
    return lambda p: _walk(p, path).__setitem__(path[-1], value)


def _del(path):
    return lambda p: _walk(p, path).__delitem__(path[-1])


# (mutation, expected item path, expected kind)
BAD = [
    (_set(["plots"], "hello"), "plots", "list"),
    (_set(["plots"], None), "plots", "list"),
    (_del(["plots"]), "plots", "required"),
    (_set(["source"], []), "source", "object"),
    (_del(["source"]), "source", "required"),
    (_set(["source", "parquet_path"], 5), "source.parquet_path", "string"),
    (_del(["source", "parquet_path"]), "source.parquet_path", "required"),
    (_set(["source", "path_type"], 1), "source.path_type", "string"),
    (_set(["view_name"], 3), "view_name", "string"),
    (_del(["view_name"]), "view_name", "required"),
    (_set(["plots", 0], "x"), "plots[0]", "object"),
    (_del(["plots", 0, "plot_id"]), "plots[0].plot_id", "required"),
    (_set(["plots", 0, "plot_id"], 1), "plots[0].plot_id", "string"),
    (_set(["plots", 0, "series"], {}), "plots[0].series", "list"),
    (_del(["plots", 0, "series"]), "plots[0].series", "required"),
    (_set(["plots", 0, "series", 0], "a"), "plots[0].series[0]", "object"),
    (_set(["plots", 0, "series", 0, "variable"], None), "plots[0].series[0].variable", "string"),
    (_del(["plots", 0, "series", 0, "color"]), "plots[0].series[0].color", "required"),
    (_set(["plots", 0, "y_axis_range"], "ab"), "plots[0].y_axis_range", "list"),
    (_set(["plots", 0, "y_axis_range"], [1]), "plots[0].y_axis_range", "pair"),
    (_set(["plots", 0, "y_axis_range"], ["a", 1]), "plots[0].y_axis_range[0]", "number"),
    (_set(["derived_variables"], "d"), "derived_variables", "list"),
    (_set(["derived_variables", 0], 4), "derived_variables[0]", "object"),
    (_del(["derived_variables", 0, "name"]), "derived_variables[0].name", "required"),
    (_set(["derived_variables", 0, "expression"], 7), "derived_variables[0].expression", "string"),
    (_set(["x_axis_range"], "ab"), "x_axis_range", "list"),
    (_set(["x_axis_range"], [1, "b"]), "x_axis_range[1]", "number"),
    (_set(["x_axis_range"], [None, 2]), "x_axis_range[0]", "number"),
    (_set(["downsample"], True), "downsample", "object"),
]


@pytest.mark.parametrize("mutation,path,kind", BAD)
def test_bad_shape_raises_with_path(isolated_config, mutation, path, kind):
    _put("bad", _mut(mutation))
    with pytest.raises(view_io.ViewFormatError) as ei:
        view_io.load_view("bad")
    assert ei.value.path == path and ei.value.expected == kind
    assert isinstance(ei.value, ValueError)


@pytest.mark.parametrize("raw", ["[]", '"s"', "5", "null"])
def test_top_level_not_object(isolated_config, raw):
    _put("bad", raw)
    with pytest.raises(view_io.ViewFormatError) as ei:
        view_io.load_view("bad")
    assert ei.value.path == "(top level)"


def test_malformed_json_is_not_a_format_error(isolated_config):
    _put("bad", "{not json")
    with pytest.raises(ValueError) as ei:
        view_io.load_view("bad")
    assert not isinstance(ei.value, view_io.ViewFormatError)
    assert view_error_text(ei.value) == str(ei.value)


def test_good_view_and_null_optionals_load(isolated_config):
    _put("good", GOOD)
    v = view_io.load_view("good")
    assert v.plots[0].y_axis_range == (None, 3) and v.x_axis_range == (1, 2)
    p = copy.deepcopy(GOOD)
    for k in ("derived_variables", "x_axis_range", "downsample", "time_column"):
        p[k] = None
    p["source"]["path_type"] = None
    p["plots"][0]["y_axis_range"] = None
    p["plots"][0]["height_ratio"] = None
    p["future"] = {"x": 1}
    _put("nulls", p)
    v = view_io.load_view("nulls")
    assert v.derived_variables == [] and v.x_axis_range is None and v.downsample_enabled
    assert v.plots[0].y_axis_range == (None, None) and v.source.path_type == "relative"


def test_message_text_is_translated_and_names_item(qapp, isolated_config):
    from parquet_analyzer.ui import i18n

    _put("bad", _mut(_set(["plots"], "hello")))
    with pytest.raises(view_io.ViewFormatError) as ei:
        view_io.load_view("bad")
    old = i18n.get_language()
    try:
        i18n.set_language("ja")
        assert view_error_text(ei.value) == "plots: リストが必要です（文字列）"
        i18n.set_language("en")
        assert view_error_text(ei.value) == "plots: a list required (a string)"
    finally:
        i18n.set_language(old)


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "critical": []}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: calls["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: calls["critical"].append(a[2])))
    return calls


@pytest.mark.parametrize("mutation,path,kind", BAD[:6] + BAD[10:12])
def test_main_window_load_view_shows_item_path(qapp, isolated_config, boxes, mutation, path, kind):
    _put("bad", _mut(mutation))
    win = MainWindow()
    win.load_view("bad")
    assert len(boxes["critical"]) == 1
    assert path in boxes["critical"][0]
    assert "KeyError" not in boxes["critical"][0]


def test_main_window_malformed_json_still_reports(qapp, isolated_config, boxes):
    _put("bad", "{oops")
    win = MainWindow()
    win.load_view("bad")
    assert len(boxes["critical"]) == 1


def test_view_manager_lists_broken_views_and_disables_load(qapp, isolated_config):
    _put("a_bad", _mut(_set(["plots"], "hello")))
    _put("b_json", "{oops")
    _put("c_good", GOOD)
    d = ViewManagerDialog({"a"}, None)
    assert [d.view_list.item(i).text() for i in range(d.view_list.count())] == ["a_bad", "b_json", "c_good"]

    def detail():
        return " | ".join(d.details.topLevelItem(i).text(0) for i in range(d.details.topLevelItemCount()))

    d.view_list.setCurrentRow(0)
    assert "plots" in detail() and not d.load_button.isEnabled() and d.delete_button.isEnabled()
    d.view_list.setCurrentRow(1)
    assert not d.load_button.isEnabled()
    d.view_list.setCurrentRow(2)
    assert d.load_button.isEnabled()
