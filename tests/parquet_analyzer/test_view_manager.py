"""View management window (specification.md 5.8, detailed_specification.md 20章)."""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from parquet_analyzer import config
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.view_manager import ViewManagerDialog


def _save(name, plots, derived=(), x_range=None, downsample=True):
    view_io.save_view(
        View(
            view_name=name,
            source=SourceDef("/data/f.parquet", "absolute"),
            plots=plots,
            derived_variables=list(derived),
            x_axis_range=x_range,
            downsample_enabled=downsample,
        )
    )


def _plot(pid, *vars_):
    return PlotDef(pid, [SeriesDef(v, "#ff0000") for v in vars_])


def _rows(dialog):
    """All (depth, text, item) tuples of the details tree, in order."""
    out = []

    def walk(item, depth):
        out.append((depth, item.text(0), item))
        for i in range(item.childCount()):
            walk(item.child(i), depth + 1)

    for i in range(dialog.details.topLevelItemCount()):
        walk(dialog.details.topLevelItem(i), 0)
    return out


def _texts(dialog):
    return [t for _, t, _ in _rows(dialog)]


def _red(item):
    return item.foreground(0).color() == QColor("#d32f2f")


@pytest.fixture
def no_boxes(monkeypatch):
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: calls.append("q") or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls.append("w"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: calls.append("i"))
    return calls


# -- io/view.py ------------------------------------------------------------------------


def test_delete_view_removes_file_and_validates_name(isolated_config):
    _save("v", [_plot("p1", "a")])
    view_io.delete_view("v")
    assert view_io.list_views() == []
    with pytest.raises(FileNotFoundError):
        view_io.delete_view("v")
    for bad in ["../x", "/abs/x", "", "  ", "a/b"]:
        with pytest.raises(ValueError):
            view_io.delete_view(bad)


def test_view_saved_at_is_file_mtime(isolated_config):
    _save("v", [_plot("p1", "a")])
    path = view_io.view_path("v")
    ts = datetime(2026, 3, 4, 5, 6, 7).timestamp()
    os.utime(path, (ts, ts))
    assert view_io.view_saved_at("v") == datetime(2026, 3, 4, 5, 6, 7)


# -- dialog ----------------------------------------------------------------------------


def test_lists_views_and_selects_first(qapp, isolated_config):
    _save("b", [_plot("p1", "a")])
    _save("a", [_plot("p1", "a")])
    d = ViewManagerDialog(None)
    assert [d.view_list.item(i).text() for i in range(d.view_list.count())] == ["a", "b"]
    assert d.selected_view_name() == "a"
    assert d.load_button.isEnabled() and d.delete_button.isEnabled()


def test_details_content(qapp, isolated_config):
    t0 = datetime(2026, 1, 2, 3, 4, 5, 678000)
    t1 = datetime(2026, 1, 2, 4, 0, 0)
    _save(
        "demo",
        [_plot("p1", "sine", "cosine"), _plot("p2", "diff")],
        derived=[DerivedVariableDef("diff", "sine - cosine")],
        x_range=(t0.timestamp(), t1.timestamp()),
        downsample=False,
    )
    ts = datetime(2026, 5, 6, 7, 8, 9).timestamp()
    os.utime(view_io.view_path("demo"), (ts, ts))
    d = ViewManagerDialog(None)
    texts = _texts(d)
    assert "Source: /data/f.parquet" in texts
    assert "Saved: 2026-05-06 07:08:09" in texts
    assert "Time range: 2026-01-02 03:04:05.678 – 2026-01-02 04:00:00.000" in texts
    assert "Plot 1 (2 channels)" in texts and "Plot 2 (1 channels)" in texts
    assert "Derived variables (1)" in texts
    assert "diff = sine - cosine" in texts
    assert "Downsampling: off" in texts
    rows = _rows(d)
    sine = next(i for _, t, i in rows if t == "sine")
    assert not sine.icon(0).isNull()
    assert sine.icon(0).pixmap(12, 12).toImage().pixelColor(5, 5) == QColor("#ff0000")
    assert not any("not in the open file" in t for t in texts)


def test_no_time_range_row_when_unset(qapp, isolated_config):
    _save("v", [_plot("p1", "a")])
    assert not any(t.startswith("Time range") for t in _texts(ViewManagerDialog(None)))


def test_missing_channels_marked(qapp, isolated_config):
    _save(
        "v",
        [_plot("p1", "a", "pressure", "dd", "d_bad")],
        derived=[
            DerivedVariableDef("dd", "a * 2"),  # fine
            DerivedVariableDef("d_missing", "a + nothere"),  # source missing
            DerivedVariableDef("d_chain", "dd + 1"),  # refers to another derived: fine
            DerivedVariableDef("d_bad", "a +"),  # unparseable
        ],
    )
    d = ViewManagerDialog({"time", "a", "b"})
    rows = {t: i for _, t, i in _rows(d)}
    suffix = " — not in the open file"
    assert "4 channel(s) not in the open file" in rows
    assert _red(rows["4 channel(s) not in the open file"])
    assert _red(rows["pressure" + suffix])
    assert _red(rows["d_bad" + suffix])  # a series plotting a missing derived var is skipped too
    assert _red(rows["d_missing = a + nothere" + suffix])
    assert _red(rows["d_bad = a +" + suffix])
    # ok ones are untouched: plain series, series referring to a view-defined derived var
    assert "a" in rows and not _red(rows["a"])
    assert "dd" in rows and not _red(rows["dd"])
    assert "dd = a * 2" in rows and "d_chain = dd + 1" in rows
    assert rows["pressure" + suffix].toolTip(0)


def test_missing_marking_cascades_through_derived_variables(qapp, isolated_config):
    """load_view skips d2 = d1 + 1 when d1 itself can't be built, and any series
    plotting either — the dialog must predict that, not just check direct refs."""
    _save(
        "v",
        [_plot("p1", "d1", "d2", "d3")],
        derived=[
            DerivedVariableDef("d2", "d1 + 1"),  # listed before d1: order must not matter
            DerivedVariableDef("d1", "nothere * 2"),
            DerivedVariableDef("d3", "a"),
        ],
    )
    rows = {t: i for _, t, i in _rows(ViewManagerDialog({"a"}))}
    suffix = " — not in the open file"
    for text in ("d1" + suffix, "d2" + suffix, "d1 = nothere * 2" + suffix, "d2 = d1 + 1" + suffix):
        assert _red(rows[text]), text
    assert not _red(rows["d3"]) and not _red(rows["d3 = a"])
    assert "4 channel(s) not in the open file" in rows


def test_marking_matches_what_load_view_actually_skips(win, monkeypatch, no_boxes):
    """End-to-end: every series the dialog marks is absent after load_view, and every
    unmarked one is present. The time column counts as not available (20.2)."""
    _save(
        "v",
        [_plot("p1", "a", "time", "d2", "nothere")],
        derived=[DerivedVariableDef("d1", "nothere + a"), DerivedVariableDef("d2", "d1 * 2"),
                 DerivedVariableDef("ok", "b - c")],
    )
    available = {c for c in win._file_columns if c != win._time_column}
    rows = {t: i for _, t, i in _rows(ViewManagerDialog(available))}
    marked = {t.split(" — ")[0] for t, i in rows.items() if _red(i) and " — " in t}
    win.load_view("v")
    loaded = set(win.plot_grid.plots[0].series_names()) | set(win._derived)
    view = view_io.load_view("v")
    all_names = {s.variable for p in view.plots for s in p.series} | {
        f"{d.name} = {d.expression}" for d in view.derived_variables
    }
    for name in all_names:
        key = name.split(" = ")[0]
        assert (name in marked) == (key not in loaded), name


def test_no_marking_without_open_file(qapp, isolated_config):
    _save("v", [_plot("p1", "pressure")], derived=[DerivedVariableDef("x", "nothere")])
    assert not any("not in the open file" in t for t in _texts(ViewManagerDialog(None)))


def test_corrupt_view_shows_error_load_disabled_delete_works(qapp, isolated_config, no_boxes):
    config.PARQUET_ANALYZER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.PARQUET_ANALYZER_DATA_DIR / "broken.json").write_text("{not json", encoding="utf-8")
    d = ViewManagerDialog(None)
    assert d.selected_view_name() == "broken"
    rows = _rows(d)
    assert len(rows) == 1 and rows[0][1].startswith("Could not read this view:")
    assert _red(rows[0][2])
    assert not d.load_button.isEnabled()
    assert d.delete_button.isEnabled()
    d.load_selected()  # no-op while disabled
    assert d.result() != QDialog.DialogCode.Accepted
    d.delete_selected()
    assert view_io.list_views() == []


def test_delete_no_keeps_file_yes_removes_and_selects_neighbour(qapp, isolated_config, monkeypatch):
    for n in "abc":
        _save(n, [_plot("p1", "a")])
    d = ViewManagerDialog(None)
    deleted = []
    d.viewDeleted.connect(deleted.append)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    d.delete_selected()
    assert view_io.list_views() == ["a", "b", "c"] and deleted == []

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    d.delete_selected()  # deletes "a" -> next ("b") selected
    assert deleted == ["a"] and view_io.list_views() == ["b", "c"]
    assert d.selected_view_name() == "b"
    d.view_list.setCurrentRow(1)
    d.delete_selected()  # deletes last "c" -> previous selected
    assert d.selected_view_name() == "b"


def test_delete_last_view_shows_empty_state(qapp, isolated_config, no_boxes):
    _save("only", [_plot("p1", "a")])
    d = ViewManagerDialog(None)
    d.delete_selected()
    assert d.view_list.count() == 0 and d.selected_view_name() is None
    assert _texts(d) == ["There are no saved views."]
    assert not d.load_button.isEnabled() and not d.delete_button.isEnabled()


def test_empty_on_open(qapp, isolated_config):
    d = ViewManagerDialog(None)
    assert _texts(d) == ["There are no saved views."]
    assert not d.load_button.isEnabled() and not d.delete_button.isEnabled()


def test_delete_oserror_warns_and_keeps_list(qapp, isolated_config, monkeypatch, no_boxes):
    _save("v", [_plot("p1", "a")])
    d = ViewManagerDialog(None)

    def boom(name):
        raise PermissionError("nope")

    monkeypatch.setattr(view_io, "delete_view", boom)
    d.delete_selected()
    assert "w" in no_boxes and d.view_list.count() == 1


def test_load_accepts_with_name(qapp, isolated_config):
    _save("v", [_plot("p1", "a")])
    d = ViewManagerDialog(None)
    d.load_selected()
    assert d.result() == QDialog.DialogCode.Accepted and d.selected_view_name() == "v"


# -- MainWindow ------------------------------------------------------------------------


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path):
    w = MainWindow()
    w.load_parquet(str(sample_parquet_path))
    yield w
    w.close()


def test_load_view_dialog_accept_loads_view(win, monkeypatch, no_boxes):
    _save("v", [_plot("p1", "a")])
    loaded = []
    monkeypatch.setattr(win, "load_view", loaded.append)
    seen = {}

    def fake_exec(self):
        seen["available"] = self._available
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    assert loaded == ["v"]
    assert seen["available"] == {"a", "b", "c"}  # minus the current time column


def test_load_view_dialog_reject_does_nothing(win, monkeypatch, no_boxes):
    _save("v", [_plot("p1", "a")])
    loaded = []
    monkeypatch.setattr(win, "load_view", loaded.append)
    monkeypatch.setattr(ViewManagerDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    win.load_view_dialog()
    assert loaded == []


def test_load_view_dialog_without_file_passes_none(qapp, isolated_config, monkeypatch):
    w = MainWindow()
    seen = {}

    def fake_exec(self):
        seen["available"] = self._available
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    w.load_view_dialog()
    assert seen["available"] is None
    w.close()


def test_deleting_current_view_clears_view_name(win, monkeypatch, no_boxes):
    _save("cur", [_plot("p1", "a")])
    _save("other", [_plot("p1", "a")])
    win._view_name = "cur"

    def fake_exec(self):
        self.view_list.setCurrentRow(0)  # "cur"
        self.delete_selected()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    assert view_io.list_views() == ["other"]
    assert win._view_name is None


def test_deleting_other_view_keeps_view_name(win, monkeypatch, no_boxes):
    _save("cur", [_plot("p1", "a")])
    _save("other", [_plot("p1", "a")])
    win._view_name = "cur"

    def fake_exec(self):
        self.view_list.setCurrentRow(1)  # "other"
        self.delete_selected()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ViewManagerDialog, "exec", fake_exec)
    win.load_view_dialog()
    assert win._view_name == "cur"
