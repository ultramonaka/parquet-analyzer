"""Map edit: rename + axis names (detailed_specification.md 27.7)."""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QDialog
from test_functions_maps_ui import CURVE, MAP, _csv, _fill, boxes, flags_on, win  # noqa: F401  (fixtures/helpers)

from parquet_analyzer.io import maps as maps_io
from parquet_analyzer.ui.map_manager import MapEditDialog


def _open(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "cv.csv", CURVE)), "cv")
    return mgr


def _files(name):
    d = maps_io.maps_dir()
    return (d / f"{name}.csv").exists(), (d / f"{name}.json").exists()


def test_rename_unused_changes_files_registry_and_expression(win, tmp_path):
    mgr = _open(win, tmp_path)
    assert mgr.apply_edit("cv", "cv2")
    assert "cv2" in win.function_registry.maps and "cv" not in win.function_registry.maps
    assert win.function_registry.maps["cv2"].name == "cv2"
    assert _files("cv2") == (True, True) and _files("cv") == (False, False)
    assert json.loads((maps_io.maps_dir() / "cv2.json").read_text(encoding="utf-8"))["name"] == "cv2"
    assert maps_io.load_map("cv2").kind == "curve"
    assert mgr.selected_name() == "cv2"
    win._on_add_derived_variable("m", "cv2(a)")  # the new name works in an expression
    assert "m" in win._derived


def test_rename_in_use_refused_with_users(win, tmp_path):
    mgr = _open(win, tmp_path)
    win._on_add_derived_variable("m", "cv(a)")
    assert mgr.apply_edit("cv", "cv2") is False
    assert "m" in mgr.message.text()
    assert "cv" in win.function_registry.maps and _files("cv") == (True, True) and _files("cv2") == (False, False)
    win.delete_derived_variable("m")
    win.open_function_manager()
    _fill(win._function_manager, "usecv", "v", "cv(v)")
    assert win._function_manager.save_function()
    assert mgr.apply_edit("cv", "cv2") is False and "usecv" in mgr.message.text()


@pytest.mark.parametrize("bad", ["sqrt", "Power_fn", "1abc", "a b", "", "../x", "other", "class"])
def test_rename_duplicate_or_invalid_refused(win, tmp_path, bad):
    mgr = _open(win, tmp_path)
    assert mgr.do_import(str(_csv(tmp_path, "o.csv", CURVE)), "other")
    assert mgr.apply_edit("cv", bad) is False
    assert mgr.message.text()
    assert set(win.function_registry.maps) == {"cv", "other"} and _files("cv") == (True, True)


def test_rename_midway_failure_rolls_back(win, tmp_path, monkeypatch):
    mgr = _open(win, tmp_path)
    path_cls = type(maps_io.maps_dir())
    original = path_cls.rename

    def flaky(self, target):
        if self.suffix == ".json" and self.stem == "cv":  # the second rename fails
            raise OSError("disk says no")
        return original(self, target)

    monkeypatch.setattr(path_cls, "rename", flaky)
    assert mgr.apply_edit("cv", "cv2") is False
    monkeypatch.setattr(path_cls, "rename", original)
    assert _files("cv") == (True, True) and _files("cv2") == (False, False)
    assert "cv" in win.function_registry.maps and "cv2" not in win.function_registry.maps
    assert maps_io.load_map("cv").name == "cv"


def test_rename_failure_after_both_renames_rolls_back(tmp_path, isolated_config, monkeypatch):
    maps_io.import_map(_csv(tmp_path, "cv.csv", CURVE), "cv")

    real_load = maps_io.load_map

    def boom(name):
        raise maps_io.MapError("cannot reload")

    monkeypatch.setattr(maps_io, "load_map", boom)
    with pytest.raises(maps_io.MapError):
        maps_io.rename_map("cv", "cv2")
    monkeypatch.setattr(maps_io, "load_map", real_load)
    assert _files("cv") == (True, True) and _files("cv2") == (False, False)
    assert json.loads((maps_io.maps_dir() / "cv.json").read_text(encoding="utf-8"))["name"] == "cv"
    assert maps_io.load_map("cv").name == "cv"


def test_axis_names_saved_and_applied(win, tmp_path):
    mgr = _open(win, tmp_path)
    assert win.function_registry.maps["cv"].axis_names == ("x", "y", "Z")  # CSV header default
    assert mgr.apply_edit("cv", "cv", ("rpm", "", "")) is True
    t = win.function_registry.maps["cv"]
    assert t.axis_names == ("rpm", "Y", "Z")
    raw = json.loads((maps_io.maps_dir() / "cv.json").read_text(encoding="utf-8"))
    assert raw["axis_names"] == {"x": "rpm", "y": "Y", "z": "Z"}
    assert maps_io.load_map("cv").axis_names == ("rpm", "Y", "Z")
    # the preview uses them
    assert mgr.preview_table.horizontalHeaderItem(0).text() == "rpm"
    assert mgr.plot_raw.getPlotItem().getAxis("bottom").labelText == "rpm"


def test_rename_and_axis_names_together(win, tmp_path):
    mgr = _open(win, tmp_path)
    assert mgr.apply_edit("cv", "cv2", ("a", "b", "c"))
    assert maps_io.load_map("cv2").axis_names == ("a", "b", "c") and _files("cv") == (False, False)


def test_old_json_without_axis_names_loads_with_defaults(tmp_path, isolated_config):
    maps_io.import_map(_csv(tmp_path, "m.csv", MAP), "mp")
    p = maps_io.maps_dir() / "mp.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("axis_names")
    p.write_text(json.dumps(d), encoding="utf-8")
    assert maps_io.load_map("mp").axis_names == ("X", "Y", "Z")
    assert maps_io.list_maps()[0].axis_names == ("X", "Y", "Z")


def test_curve_header_row_is_the_import_default(tmp_path, isolated_config):
    t = maps_io.import_map(_csv(tmp_path, "c.csv", "rpm,gain\n0,0\n10,5\n"), "c")
    assert t.axis_names == ("rpm", "gain", "Z")
    assert maps_io.load_map("c").axis_names == ("rpm", "gain", "Z")
    t2 = maps_io.import_map(_csv(tmp_path, "c2.csv", "0,0\n10,5\n"), "c2")  # no header
    assert t2.axis_names == ("X", "Y", "Z")


def test_edit_dialog_and_double_click(win, tmp_path, monkeypatch):
    mgr = _open(win, tmp_path)
    d = MapEditDialog(win.function_registry.maps["cv"])
    assert d.name_edit.text() == "cv" and d.values()["axis_names"][:2] == ("x", "y")

    def fake_exec(self):
        self.name_edit.setText("cv3")
        self.axis_edits[0].setText("T")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(MapEditDialog, "exec", fake_exec)
    mgr.map_list.itemDoubleClicked.emit(mgr.map_list.currentItem())
    assert "cv3" in win.function_registry.maps and win.function_registry.maps["cv3"].axis_names[0] == "T"
    monkeypatch.setattr(MapEditDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    mgr.edit_button.click()
    assert "cv3" in win.function_registry.maps


def test_map_preview_uses_axis_names(win, tmp_path):
    win.open_map_manager()
    mgr = win._map_manager
    assert mgr.do_import(str(_csv(tmp_path, "mp.csv", MAP)), "mp")
    assert mgr.apply_edit("mp", "mp", ("rpm", "torque", "eff"))
    assert mgr.preview_table.item(0, 0).text() == "torque \\ rpm"  # corner cell names the axes (27.8)
    assert mgr.plot_raw.getPlotItem().getAxis("left").labelText == "torque"
    assert mgr.plot_result.getPlotItem().getAxis("bottom").labelText == "rpm"
