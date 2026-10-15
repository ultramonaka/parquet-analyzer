"""Chapters 33/34: views store the time-axis column and a relative source path
(persistence.ja.md 33/34, requirements 5.26/5.27)."""

from __future__ import annotations

import json
import os
import shutil
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from parquet_analyzer import config
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui import messages
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog


@pytest.fixture
def boxes(monkeypatch):
    calls = {"warning": [], "critical": []}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: calls["warning"].append(a[2])))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: calls["critical"].append(a[2])))
    # a missing signal adds the "assign substitute signals" button (persistence 54.6): same warning text
    monkeypatch.setattr(
        messages, "warning_with_button", lambda _p, _t, text, _b: calls["warning"].append(text) or False
    )
    return calls


def _write(path, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({k: pa.array(v) for k, v in cols.items()}), path)
    return path


def _cols(n=2000):
    i = np.arange(n, dtype="float64")
    return {"t1": i, "t2": 5000.0 + i * 10.0, "v": np.sin(i / 50)}


def _wait(qapp, win):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        qapp.processEvents()
        if not win._busy_sources:
            break


def _save(win, monkeypatch, name):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (name, True)))
    win.save_view_dialog()


def _open(qapp, path):
    win = MainWindow()
    assert win.load_parquet(str(path))
    return win


def _xr(win):
    return win.plot_grid.plots[0].getViewBox().viewRange()[0]


def test_time_column_saved_and_restored(qapp, isolated_config, tmp_path, monkeypatch, boxes):
    p = _write(tmp_path / "f.parquet", _cols())
    win = _open(qapp, p)
    win.set_time_column("t2")
    win._add_variable_to_plot(win.plot_grid.plots[0], "v", "#ff0000")
    win.plot_grid.set_x_range(6000.0, 7000.0)
    _wait(qapp, win)
    _save(win, monkeypatch, "tc")
    assert json.loads(view_io.view_path("tc").read_text())["time_column"] == "t2"

    win2 = _open(qapp, p)  # default axis is t1
    assert win2._time_column == "t1"
    win2.load_view("tc")
    _wait(qapp, win2)
    assert win2._time_column == "t2"
    assert win2.time_axis_combo.currentText() == "t2"
    lo, hi = _xr(win2)
    assert (round(lo), round(hi)) == (6000, 7000)  # interpreted in t2's units
    assert not boxes["warning"]


def test_missing_time_column_warns_and_keeps_axis(qapp, isolated_config, tmp_path, monkeypatch, boxes):
    p = _write(tmp_path / "f.parquet", _cols())
    win = _open(qapp, p)
    win.set_time_column("t2")
    win._add_variable_to_plot(win.plot_grid.plots[0], "v", "#ff0000")
    _save(win, monkeypatch, "tc")
    other = _write(tmp_path / "g.parquet", {"t1": _cols()["t1"], "v": _cols()["v"]})
    win2 = _open(qapp, other)
    win2.load_view("tc")
    assert win2._time_column == "t1"
    assert len(boxes["warning"]) == 1 and "t2" in boxes["warning"][0]


def test_old_view_without_new_keys(qapp, isolated_config, tmp_path, boxes):
    p = _write(tmp_path / "f.parquet", _cols())
    data = config.PARQUET_ANALYZER_DATA_DIR
    data.mkdir(parents=True)
    (data / "old.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "view_name": "old",
                "source": {"parquet_path": str(p), "path_type": "absolute"},
                "plots": [{"plot_id": "p1", "series": [{"variable": "v", "color": "#ff0000"}]}],
                "future_key": 1,
            }
        )
    )
    v = view_io.load_view("old")
    assert v.time_column is None and v.source.relative_path is None
    win = _open(qapp, p)
    win.set_time_column("t2")
    win.load_view("old")
    assert win._time_column == "t2"  # None -> untouched
    assert not boxes["warning"]


def _top(d):
    return [d.details.topLevelItem(i) for i in range(d.details.topLevelItemCount())]


def test_view_manager_time_column_row_and_mark(qapp, isolated_config):
    view_io.save_view(
        view_io.View(
            "m",
            view_io.SourceDef("/x.parquet", "absolute"),
            [view_io.PlotDef("p", [view_io.SeriesDef("t1", "#fff")])],
            time_column="t2",
        )
    )
    d = ViewManagerDialog({"t1", "v"}, None, all_columns={"t1", "t2", "v"})
    row = next(i for i in _top(d) if i.text(0).startswith(("Time axis: t2", "時間軸: t2")))
    assert "—" not in row.text(0)
    # series t1 resolves after switching to t2 (t1 becomes an ordinary variable)
    assert not any("—" in it.child(k).text(0) for it in _top(d) for k in range(it.childCount()))
    d2 = ViewManagerDialog({"t1"}, None, all_columns={"t1", "v"})
    row = next(i for i in _top(d2) if "t2" in i.text(0) and ("Time axis" in i.text(0) or "時間軸" in i.text(0)))
    assert "—" in row.text(0)


def test_relative_path_saved_and_absolute_kept(qapp, isolated_config, tmp_path, monkeypatch, boxes):
    p = _write(tmp_path / "f.parquet", _cols())
    win = _open(qapp, p)
    win._add_variable_to_plot(win.plot_grid.plots[0], "v", "#ff0000")
    _save(win, monkeypatch, "r")
    src = json.loads(view_io.view_path("r").read_text())["source"]
    assert src["path_type"] == "absolute" and src["parquet_path"] == str(p)
    assert src["relative_path"] == os.path.join("..", "f.parquet")
    assert view_io.resolve_source(view_io.load_view("r")) == (p, "absolute")


def test_moved_folder_opens_via_relative(qapp, isolated_config, tmp_path, monkeypatch, boxes):
    # layout: <tmp>/proj/{data/, f.parquet}; the view's data dir is proj/data
    proj = tmp_path / "proj"
    monkeypatch.setattr(config, "PARQUET_ANALYZER_DATA_DIR", proj / "data")
    p = _write(proj / "f.parquet", _cols())
    win = _open(qapp, p)
    win._add_variable_to_plot(win.plot_grid.plots[0], "v", "#ff0000")
    _save(win, monkeypatch, "mv")
    new = tmp_path / "elsewhere" / "proj2"
    shutil.copytree(proj, new)
    shutil.rmtree(proj)  # the absolute path is now dead
    monkeypatch.setattr(config, "PARQUET_ANALYZER_DATA_DIR", new / "data")
    path, how = view_io.resolve_source(view_io.load_view("mv"))
    assert how == "relative_path" and path == (new / "f.parquet").resolve()
    win2 = MainWindow()
    win2.load_view("mv")  # nothing open yet -> load_parquet from the resolved path
    assert win2._data_source is not None and "elsewhere" in str(win2._data_source.path)
    assert win2.plot_grid.plots[0].series_names() == ["v"]
    assert not boxes["critical"]


def test_absolute_preferred_over_relative(isolated_config, tmp_path):
    a = _write(tmp_path / "abs.parquet", _cols())
    _write(config.PARQUET_ANALYZER_DATA_DIR / "rel.parquet", _cols())
    v = view_io.View("x", view_io.SourceDef(str(a), "absolute", "rel.parquet"), [])
    assert view_io.resolve_source(v) == (a, "absolute")


def test_neither_exists(qapp, isolated_config, tmp_path, boxes):
    v = view_io.View("x", view_io.SourceDef(str(tmp_path / "gone.parquet"), "absolute", "nope.parquet"), [])
    assert view_io.resolve_source(v)[1] == "unresolved"
    view_io.save_view(v)
    MainWindow().load_view("x")
    assert boxes["critical"]  # the usual open error


def test_relative_none_when_unmakeable(isolated_config, monkeypatch):
    def boom(*a, **k):
        raise ValueError("different drive")

    monkeypatch.setattr(os.path, "relpath", boom)
    assert view_io.relative_source_path("x", "/a/b.parquet") is None
    view_io.save_view(view_io.View("x", view_io.SourceDef("/a/b.parquet", "absolute", None), []))
    assert json.loads(view_io.view_path("x").read_text())["source"]["relative_path"] is None


def test_legacy_relative_path_type_kept(isolated_config):
    data = config.PARQUET_ANALYZER_DATA_DIR
    v = view_io.View("l", view_io.SourceDef("../f.parquet", "relative"), [])
    assert view_io.resolve_source(v) == ((data / "../f.parquet").resolve(), "relative")
