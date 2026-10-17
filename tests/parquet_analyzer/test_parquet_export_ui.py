"""Parquet export through MainWindow (export.ja.md 57.1, 57.3, 57.5).

Real routing: the toolbar action is triggered, the status-bar cancel button is clicked with QTest, the
settings dialog's `exec` and the file dialog are replaced. The worker is slowed or blocked by patching
`core.parquet_export.evaluate_expression`. Every wait is in real time with a deadline."""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QToolBar
from waiting import wait_until

from parquet_analyzer import config, features
from parquet_analyzer.core import oplog
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.ui import parquet_export_dialog as dialog_module
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.parquet_export_dialog import ParquetExportDialog


class Boxes:
    def __init__(self):
        self.info: list[str] = []
        self.warn: list[str] = []


@pytest.fixture
def boxes(monkeypatch):
    b = Boxes()
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, text, *a, **k: b.info.append(text)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: b.warn.append(text)))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, t, text, *a, **k: b.warn.append(text)))
    return b


@pytest.fixture
def win(qapp, isolated_config, monkeypatch, boxes, sample_parquet_path):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    assert w.load_parquet(str(sample_parquet_path))
    yield w
    w.close()


@pytest.fixture
def log(qapp, isolated_config):
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


def op_records(action: str) -> list[dict]:
    oplog.flush()
    path = oplog.log_path_for(date.today())
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == "op" and r.get("action") == action]


def plot_signals(win, first=("a", "d1"), second=("b", "a")):
    """Plot 1: a + derived d1 = a * 2; plot 2 (linked): b and a again (a duplicate)."""
    win._on_add_derived_variable("d1", "a * 2")
    plot1 = win.plot_grid.plots[0]
    for n in first:
        win._on_variable_dropped(plot1, n)
    if second:
        win.plot_grid.add_plot()
        plot2 = win.plot_grid.plots[1]
        for n in second:
            win._on_variable_dropped(plot2, n)
    wait_until(lambda: not win._busy_sources)


class Dialogs:
    """Replaces the settings dialog's exec and the file dialog; records what they were given."""

    def __init__(self, monkeypatch, out: Path):
        self.out = out
        self.dialog: ParquetExportDialog | None = None
        self.names: list[str] | None = None  # set to deselect: the names to keep
        self.visible = False
        self.accept = True
        self.file_args: tuple | None = None
        self.file_answer = str(out)

        def exec_(dlg):
            self.dialog = dlg
            self.labels = (dlg.all_radio.text(), dlg.visible_radio.text())
            self.items = [dlg.list.item(i).text() for i in range(dlg.list.count())]
            if self.names is not None:
                for i in range(dlg.list.count()):
                    item = dlg.list.item(i)
                    item.setCheckState(Qt.CheckState.Checked if item.text() in self.names else Qt.CheckState.Unchecked)
            dlg.visible_radio.setChecked(self.visible)
            if not self.accept:
                return QDialog.DialogCode.Rejected
            dlg.accept()
            return QDialog.DialogCode.Accepted

        def get_save(parent, caption, directory, filter_, **kw):
            self.file_args = (caption, directory, filter_)
            self.file_kw = kw
            return self.file_answer, ""

        monkeypatch.setattr(ParquetExportDialog, "exec", exec_)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save))


@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    return Dialogs(monkeypatch, tmp_path / "out.parquet")


def finish(win, timeout=30.0):
    assert wait_until(lambda: win._parquet_export is None, timeout), "the export never finished"
    QApplication.processEvents()


# -- entry points -------------------------------------------------------------------------------


def test_flag_off_hides_the_entry_points(qapp, isolated_config, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", False)
    w = MainWindow()
    try:
        assert "export_parquet" not in w._actions
        from parquet_analyzer.ui.settings_dialog import SettingsDialog

        assert "export_parquet" not in SettingsDialog(w.settings)._shortcut_edits
    finally:
        w.close()


def test_flag_on_shows_the_action_and_the_shortcut_row(win):
    from parquet_analyzer.ui.settings_dialog import SettingsDialog

    action = win._actions["export_parquet"]
    assert action.text() == tr("toolbar.export_parquet")
    assert "export_parquet" in SettingsDialog(win.settings)._shortcut_edits
    assert win.settings.shortcuts["export_parquet"] == ""
    toolbar = win.findChild(QToolBar, "file_view_toolbar")
    actions = toolbar.actions()
    assert actions[actions.index(win._actions["export_image"]) + 1] is action  # next to the image save


def test_no_file_opened(qapp, isolated_config, monkeypatch, boxes, dialogs):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    w = MainWindow()
    try:
        w._actions["export_parquet"].trigger()
        assert boxes.info == [tr("error.open_file_first")]
        assert dialogs.dialog is None
    finally:
        w.close()


def test_no_signal_plotted(win, boxes, dialogs):
    win._actions["export_parquet"].trigger()
    assert boxes.info == [tr("export.nothing_to_export")]
    assert dialogs.dialog is None


def test_rejecting_the_dialog_or_the_file_chooser_does_nothing(win, dialogs, tmp_path):
    plot_signals(win)
    dialogs.accept = False
    win._actions["export_parquet"].trigger()
    assert win._parquet_export is None and dialogs.file_args is None
    dialogs.accept = True
    dialogs.file_answer = ""
    win._actions["export_parquet"].trigger()
    assert win._parquet_export is None and not (tmp_path / "out.parquet").exists()


# -- the dialog's contents ------------------------------------------------------------------------


def test_signal_list_is_in_plot_order_without_duplicates_and_includes_hidden(win, dialogs):
    plot_signals(win)  # plot 1: a, d1 / plot 2: b, a
    win.plot_grid.plots[0].set_series_visible("d1", False)
    dialogs.accept = False
    win._actions["export_parquet"].trigger()
    assert dialogs.items == ["a", "d1", "b"]  # hidden d1 included, the second `a` once, no time axis
    assert "time" not in dialogs.items
    dlg = dialogs.dialog
    assert dlg.selected_names() == ["a", "d1", "b"]  # all checked by default
    dlg.select_none_button.click()
    assert dlg.selected_names() == []
    dlg.select_all_button.click()
    assert dlg.selected_names() == ["a", "d1", "b"]


def test_range_labels_carry_the_row_counts(win, dialogs):
    plot_signals(win)
    tv = win._time_values
    win.plot_grid.set_x_range(float(tv[1000]), float(tv[1999]))
    wait_until(lambda: not win._busy_sources)
    v0, v1 = win._visible_row_range()
    dialogs.accept = False
    win._actions["export_parquet"].trigger()
    assert f"{len(tv):,}" in dialogs.labels[0]
    assert f"{v1 - v0:,}" in dialogs.labels[1]
    assert 0 < v1 - v0 < len(tv)


def test_ok_without_a_selected_signal_asks_for_one(win, dialogs, boxes):
    plot_signals(win)
    dlg = ParquetExportDialog(["a", "b"], 10, 5, "time", win)
    dlg.select_none_button.click()
    dlg.buttons.accepted.emit()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert boxes.info == [tr("export_parquet.nothing_selected")]
    dlg.close()


# -- running ---------------------------------------------------------------------------------------


def test_full_export_writes_the_selected_signals(win, dialogs, tmp_path):
    plot_signals(win)
    win._view_name = "my_view"
    dialogs.names = ["d1", "b"]
    win._actions["export_parquet"].trigger()
    finish(win)
    out = tmp_path / "out.parquet"
    t = pq.read_table(out)
    assert t.column_names == ["time", "d1", "b"]  # the time axis first, then the selection in list order
    a = win.variable_values("a")
    np.testing.assert_array_equal(t.column("d1").to_numpy(), a * 2)
    np.testing.assert_array_equal(t.column("b").to_numpy(), win.variable_values("b"))
    assert t.num_rows == len(win._time_values)
    assert str(t.schema.field("time").type) == "timestamp[ms]"
    info = json.loads(pq.read_schema(out).metadata[pe.METADATA_KEY])
    assert info["view"] == "my_view" and info["derived"] == {"d1": "a * 2"}
    assert info["source"] == str(win._data_source.path)
    assert not list(tmp_path.glob("*.part"))


def test_default_file_name_and_suffix(win, dialogs, tmp_path, sample_parquet_path):
    plot_signals(win)
    dialogs.file_answer = str(tmp_path / "noext")
    win._actions["export_parquet"].trigger()
    finish(win)
    assert (tmp_path / "noext.parquet").exists()
    caption, directory, filter_ = dialogs.file_args
    assert Path(directory).parent == Path(sample_parquet_path).parent  # first time: the Parquet file's folder
    assert Path(directory).name.startswith(Path(sample_parquet_path).stem + "_")
    assert directory.endswith(".parquet") and "*.parquet" in filter_


def test_visible_range_export_has_the_visible_rows(win, dialogs, tmp_path):
    plot_signals(win)
    tv = win._time_values
    win.plot_grid.set_x_range(float(tv[1000]), float(tv[1999]))
    wait_until(lambda: not win._busy_sources)
    v0, v1 = win._visible_row_range()
    dialogs.visible = True
    win._actions["export_parquet"].trigger()
    finish(win)
    t = pq.read_table(tmp_path / "out.parquet")
    assert t.num_rows == v1 - v0
    np.testing.assert_array_equal(t.column("a").to_numpy(), win.variable_values("a")[v0:v1])
    info = json.loads(pq.read_schema(tmp_path / "out.parquet").metadata[pe.METADATA_KEY])
    assert info["range"]["rows"] == [v0, v1]


def test_last_export_folder_is_recorded_and_used_next_time(win, dialogs, tmp_path):
    plot_signals(win)
    sub = tmp_path / "sub"
    sub.mkdir()
    dialogs.file_answer = str(sub / "x.parquet")
    win._actions["export_parquet"].trigger()
    finish(win)
    assert win.settings.last_export_folder == str(sub.resolve())
    win._actions["export_parquet"].trigger()  # a second export, to the same path
    finish(win)
    assert Path(dialogs.file_args[1]).parent == sub.resolve()


class SlowEval:
    """Patches the worker's evaluate: optional sleep per call and a gate that blocks until released."""

    def __init__(self, monkeypatch):
        self.release = threading.Event()
        self.block = False
        self.delay = 0.0
        self.entered = threading.Event()
        self.calls = 0
        real = pe.evaluate_expression

        def evaluate(expr, variables, registry=None):
            self.calls += 1
            self.entered.set()
            if self.block:
                assert self.release.wait(30), "gate never released"
            if self.delay:
                time.sleep(self.delay)
            return real(expr, variables, registry)

        monkeypatch.setattr(pe, "evaluate_expression", evaluate)

    def wait_entered(self):
        assert wait_until(self.entered.is_set)


@pytest.fixture
def slow(monkeypatch):
    s = SlowEval(monkeypatch)
    yield s
    s.release.set()


def test_runs_in_the_background_with_progress_and_the_ui_stays_responsive(win, dialogs, tmp_path, monkeypatch, slow):
    monkeypatch.setattr(MainWindow, "_PARQUET_EXPORT_CHUNK_ROWS", 5000)  # 50_000 rows -> 10 chunks
    plot_signals(win)
    slow.delay = 0.05
    win._actions["export_parquet"].trigger()
    progress = win._parquet_export_progress
    assert not progress.isHidden() and win._parquet_export is not None
    ticks = 0
    seen: list[int] = []
    deadline = time.monotonic() + 30
    while win._parquet_export is not None and time.monotonic() < deadline:
        QApplication.processEvents()  # the UI thread keeps running while the worker computes
        ticks += 1
        seen.append(progress.bar.value())
        time.sleep(0.002)
    assert win._parquet_export is None
    assert ticks > 5
    assert seen == sorted(seen)
    assert any(0 < v < 100 for v in seen), seen  # an intermediate value was shown
    assert progress.isHidden()
    assert pq.read_metadata(tmp_path / "out.parquet").num_row_groups == 10
    assert tr("export_parquet.saved", path="", rows="", seconds="").split(":")[0] in win._notice_label.text()


def test_full_phase_shows_an_indeterminate_bar(win):
    progress = win._parquet_export_progress
    progress.begin()
    progress.set_progress(0, -1)
    assert progress.bar.minimum() == 0 and progress.bar.maximum() == 0
    assert progress.label.text() == tr("export_parquet.progress_full")
    progress.set_progress(10, 100)
    assert progress.bar.maximum() == 100 and progress.bar.value() == 10
    progress.end()


def test_target_equal_to_the_source_is_refused_before_starting(win, dialogs, boxes, sample_parquet_path):
    plot_signals(win)
    before = Path(sample_parquet_path).read_bytes()
    dialogs.file_answer = str(sample_parquet_path)
    win._actions["export_parquet"].trigger()
    assert win._parquet_export is None
    assert boxes.warn == [tr("export_parquet.same_as_source")]
    assert Path(sample_parquet_path).read_bytes() == before
    # a not yet existing path that resolves to the source (".." segments)
    via = Path(sample_parquet_path).parent / "sub" / ".." / Path(sample_parquet_path).name
    assert win._is_source_file(str(via))
    assert not win._is_source_file(str(Path(sample_parquet_path).parent / "other.parquet"))


def test_hard_link_to_the_source_is_refused(win, tmp_path, sample_parquet_path):
    import os

    link = tmp_path / "link.parquet"
    os.link(sample_parquet_path, link)
    assert win._is_source_file(str(link))


def test_progress_text_and_bar_values(win):
    progress = win._parquet_export_progress
    progress.begin()
    progress.set_progress(25, 100)
    assert progress.bar.value() == 25 and progress.label.text() == tr("export_parquet.progress", percent=25)
    progress.set_progress(100, 100)
    assert progress.bar.value() == 100
    progress.end()
    assert progress.isHidden()


def test_cancel_button_stops_the_export_and_leaves_no_file(win, dialogs, tmp_path, monkeypatch, slow, log, choice):
    choice.answer = "overwrite"
    monkeypatch.setattr(MainWindow, "_PARQUET_EXPORT_CHUNK_ROWS", 5000)
    plot_signals(win)
    out = tmp_path / "out.parquet"
    out.write_bytes(b"previous")
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    job = win._parquet_export
    QTest.mouseClick(win._parquet_export_progress.cancel_button, Qt.MouseButton.LeftButton)
    assert win._parquet_export is None and win._parquet_export_progress.isHidden()
    assert win._notice_label.text() == tr("export_parquet.cancel_requested")  # a request: it may still finish
    slow.release.set()
    assert wait_until(job.worker_done.is_set)
    assert out.read_bytes() == b"previous"  # the existing file survives
    assert not list(tmp_path.glob("*.part"))
    QApplication.processEvents()
    assert win._notice_label.text() == tr("export_parquet.cancelled")  # the late result changed nothing
    assert len(op_records("export_parquet_cancel")) == 1
    assert op_records("export_parquet")[0]["details"]["columns"] == 3


def test_a_second_export_joins_the_queue_instead_of_being_refused(win, dialogs, slow, boxes, tmp_path):
    """57.9.2 no. 4: an export started while one runs is appended and starts when a slot is free."""
    win.settings.export_parallel_files = 1
    plot_signals(win)
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    two = tmp_path / "two.parquet"
    assert win.export_parquet(str(two), ["a"], (0, 10)) is True
    assert boxes.info == []  # no "already running" refusal
    items = win._export.queue.items
    assert [i.state.value for i in items] == ["running", "waiting"]
    assert not two.exists()
    slow.release.set()
    assert wait_until(lambda: win._export.queue.is_idle())
    assert two.exists() and (tmp_path / "out.parquet").exists()
    assert pq.read_table(two).num_rows == 10


def test_opening_another_file_does_not_stop_the_export(win, dialogs, tmp_path, slow, sample_parquet_path):
    import pyarrow as pa

    plot_signals(win)
    a = win.variable_values("a").copy()
    win._view_name = "orig_view"
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    job = win._parquet_export
    other = tmp_path / "other.parquet"
    tbl = pq.read_table(sample_parquet_path)
    pq.write_table(tbl.set_column(tbl.schema.get_field_index("a"), "a", pa.array(np.full(tbl.num_rows, 7.0))), other)
    assert win.load_parquet(str(other))
    assert win._parquet_export is job  # still running; the progress widget stays in the status bar
    assert not win._parquet_export_progress.isHidden()
    slow.release.set()
    finish(win)
    assert win._parquet_export_progress.isHidden()
    t = pq.read_table(tmp_path / "out.parquet")
    np.testing.assert_array_equal(t.column("d1").to_numpy(), a * 2)  # the original file's data
    np.testing.assert_array_equal(t.column("a").to_numpy(), a)
    info = json.loads(pq.read_schema(tmp_path / "out.parquet").metadata[pe.METADATA_KEY])
    assert info["view"] == "orig_view" and info["source"] == str(Path(sample_parquet_path).resolve())
    assert "out.parquet" in win._notice_label.text()  # the completion message appears after the switch
    assert not list(tmp_path.glob("*.part"))


def test_loading_a_view_does_not_stop_the_export(win, dialogs, tmp_path, slow):
    from parquet_analyzer.io import view as view_io

    plot_signals(win)
    a = win.variable_values("a").copy()
    view_io.save_view(
        view_io.View(
            view_name="v1",
            source=view_io.SourceDef(str(win._data_source.path), "absolute"),
            plots=[view_io.PlotDef("p1", [view_io.SeriesDef("a", "#ff0000")])],
        )
    )
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    job = win._parquet_export
    win.load_view("v1")
    assert win._parquet_export is job and not win._parquet_export_progress.isHidden()
    slow.release.set()
    finish(win)
    t = pq.read_table(tmp_path / "out.parquet")
    np.testing.assert_array_equal(t.column("d1").to_numpy(), a * 2)
    assert "out.parquet" in win._notice_label.text()


def test_closing_the_window_cancels_the_export(win, dialogs, tmp_path, slow):
    plot_signals(win)
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    job = win._parquet_export
    win.close()
    slow.release.set()
    assert win._parquet_export is None
    assert wait_until(job.worker_done.is_set)
    assert not (tmp_path / "out.parquet").exists() and not list(tmp_path.glob("*.part"))


def test_editing_a_variable_during_the_export_does_not_change_it(win, dialogs, tmp_path, slow):
    plot_signals(win)
    a = win.variable_values("a").copy()
    slow.block = True
    dialogs.names = ["d1"]
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    win.edit_derived_variable("d1", "a * 100")  # while the worker is inside the first evaluation
    win._on_add_derived_variable("d1b", "d1 + 1")
    slow.release.set()
    finish(win)
    t = pq.read_table(tmp_path / "out.parquet")
    np.testing.assert_array_equal(t.column("d1").to_numpy(), a * 2)  # the expression of the start


def test_a_function_change_during_the_export_does_not_change_it(win, dialogs, tmp_path, slow):
    from parquet_analyzer.core.functions import UserFunction

    win.function_registry.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 2")
    win._on_add_derived_variable("d2", "twice(a)")
    plot1 = win.plot_grid.plots[0]
    win._on_variable_dropped(plot1, "d2")
    wait_until(lambda: not win._busy_sources)
    a = win.variable_values("a").copy()
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    win.function_registry.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 3")
    slow.release.set()
    finish(win)
    t = pq.read_table(tmp_path / "out.parquet")
    np.testing.assert_array_equal(t.column("d2").to_numpy(), a * 2)


def test_failure_is_shown_as_a_warning_and_leaves_nothing(win, dialogs, tmp_path, boxes):
    plot_signals(win)
    dialogs.file_answer = str(tmp_path / "no_such_dir" / "out.parquet")
    win._actions["export_parquet"].trigger()
    finish(win)
    assert len(boxes.warn) == 1 and boxes.warn[0].startswith(tr("export_parquet.failed", error="").split("\n")[0])
    assert win._parquet_export_progress.isHidden()
    assert list(tmp_path.rglob("*.part")) == []
    assert win._parquet_export_busy() is False or wait_until(lambda: not win._parquet_export_busy())


def test_the_export_does_not_mark_the_window_busy(win, dialogs, slow):
    plot_signals(win)
    slow.block = True
    win._actions["export_parquet"].trigger()
    slow.wait_entered()
    assert "parquet_export" not in win._busy_sources
    slow.release.set()
    finish(win)


def test_aliased_and_derived_with_dependencies_are_exported(win, dialogs, tmp_path):
    win._on_add_derived_variable("d1", "a * 2")
    win._on_add_derived_variable("d3", "d1 + b")
    win._on_add_derived_variable("unrelated", "c * 5")
    plot1 = win.plot_grid.plots[0]
    win._on_variable_dropped(plot1, "d3")
    win._on_variable_dropped(plot1, "unrelated")
    wait_until(lambda: not win._busy_sources)
    dialogs.names = ["d3"]
    win._actions["export_parquet"].trigger()
    finish(win)
    t = pq.read_table(tmp_path / "out.parquet")
    assert t.column_names == ["time", "d3"]
    np.testing.assert_array_equal(t.column("d3").to_numpy(), win.variable_values("a") * 2 + win.variable_values("b"))
    info = json.loads(pq.read_schema(tmp_path / "out.parquet").metadata[pe.METADATA_KEY])
    assert info["derived"] == {"d1": "a * 2", "d3": "d1 + b"}  # the unrelated variable is not in it


def test_progress_widget_class_is_the_status_bar_widget(win):
    assert isinstance(win._parquet_export_progress, dialog_module.ParquetExportProgress)
    assert win._parquet_export_progress.objectName() == "parquet_export_progress"
    assert win.statusBar().findChild(dialog_module.ParquetExportProgress) is win._parquet_export_progress


# -- overwrite protection (export 57.8.1) ------------------------------------------------------------


@pytest.fixture
def choice(monkeypatch):
    """Replaces the three-way "file exists" box; `answer` is "timestamp" / "overwrite" / None."""
    from parquet_analyzer.ui import messages

    class Choice:
        answer = None

    c = Choice()
    c.asked = []

    def fake(parent, title, text, timestamp_text, overwrite_text, cancel_text):
        c.asked.append(text)
        return c.answer

    monkeypatch.setattr(messages, "choose_export_overwrite", fake)
    return c


def test_file_dialog_does_not_confirm_overwrite_itself(win, dialogs, tmp_path):
    plot_signals(win)
    win._actions["export_parquet"].trigger()
    finish(win)
    assert dialogs.file_kw["options"] & QFileDialog.Option.DontConfirmOverwrite


def test_existing_file_asks_and_cancel_does_nothing(win, dialogs, choice, tmp_path):
    plot_signals(win)
    out = tmp_path / "out.parquet"
    out.write_bytes(b"precious")
    choice.answer = None
    win._actions["export_parquet"].trigger()
    assert len(choice.asked) == 1 and "out.parquet" in choice.asked[0]
    assert win._parquet_export is None
    assert out.read_bytes() == b"precious"
    assert not list(tmp_path.glob("out_*.parquet")) and not list(tmp_path.glob("*.part"))


def test_name_without_extension_whose_completed_name_exists_asks(win, dialogs, choice, tmp_path):
    """The data-loss path: the suffix is added after the file dialog, so only our question can notice."""
    plot_signals(win)
    out = tmp_path / "noext.parquet"
    out.write_bytes(b"precious")
    dialogs.file_answer = str(tmp_path / "noext")
    win._actions["export_parquet"].trigger()
    assert len(choice.asked) == 1 and "noext.parquet" in choice.asked[0]
    assert out.read_bytes() == b"precious"


def test_save_with_timestamp_makes_a_new_file(win, dialogs, choice, tmp_path, log):
    plot_signals(win)
    out = tmp_path / "out.parquet"
    out.write_bytes(b"precious")
    choice.answer = "timestamp"
    win._actions["export_parquet"].trigger()
    finish(win)
    assert out.read_bytes() == b"precious"
    new = list(tmp_path.glob("out_*.parquet"))
    assert len(new) == 1 and pq.read_table(new[0]).num_rows == len(win._time_values)
    rec = op_records("export_parquet")[-1]["details"]
    assert rec["overwrite"] is False and rec["renamed"] is True


def test_timestamp_name_collision_gets_a_counter(win, dialogs, choice, tmp_path, monkeypatch):
    from parquet_analyzer.ui import main_window as mw

    class FixedDatetime(mw.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(mw, "datetime", FixedDatetime)
    plot_signals(win)
    (tmp_path / "out.parquet").write_bytes(b"precious")
    (tmp_path / "out_20260102_030405.parquet").write_bytes(b"other")
    choice.answer = "timestamp"
    win._actions["export_parquet"].trigger()
    finish(win)
    assert (tmp_path / "out_20260102_030405.parquet").read_bytes() == b"other"
    assert pq.read_table(tmp_path / "out_20260102_030405_2.parquet").num_rows == len(win._time_values)


def test_overwrite_replaces_the_file(win, dialogs, choice, tmp_path, log):
    plot_signals(win)
    out = tmp_path / "out.parquet"
    out.write_bytes(b"precious")
    choice.answer = "overwrite"
    win._actions["export_parquet"].trigger()
    finish(win)
    assert pq.read_table(out).num_rows == len(win._time_values)
    rec = op_records("export_parquet")[-1]["details"]
    assert rec["overwrite"] is True and rec["renamed"] is False


def test_no_question_for_a_new_file(win, dialogs, choice, tmp_path):
    plot_signals(win)
    win._actions["export_parquet"].trigger()
    finish(win)
    assert choice.asked == []


def test_source_file_is_refused_without_the_question(win, dialogs, choice, boxes, sample_parquet_path):
    plot_signals(win)
    dialogs.file_answer = str(sample_parquet_path)
    win._actions["export_parquet"].trigger()
    assert choice.asked == [] and boxes.warn == [tr("export_parquet.same_as_source")]


def test_direct_call_to_an_existing_file_is_refused_by_the_core(win, boxes, tmp_path):
    plot_signals(win)
    out = tmp_path / "out.parquet"
    out.write_bytes(b"precious")
    win.export_parquet(str(out), ["a"], (0, len(win._time_values)))
    finish(win)
    assert out.read_bytes() == b"precious"
    assert boxes.warn and "already exists" in boxes.warn[-1]
    assert not list(tmp_path.glob("*.part"))


def test_the_question_box_maps_its_buttons(qapp, monkeypatch):
    from parquet_analyzer.ui import messages

    for pick, expected in (("stamp", "timestamp"), ("over", "overwrite"), ("cancel", None), (None, None)):
        state = {}

        def fake_exec(box, pick=pick, state=state):
            texts = {b.text(): b for b in box.buttons()}
            state["default"] = box.defaultButton().text()
            box._clicked = {"stamp": texts["S"], "over": texts["O"], "cancel": texts["C"], None: None}[pick]
            return 0

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self._clicked)
        assert messages.choose_export_overwrite(None, "t", "x", "S", "O", "C") == expected
        assert state["default"] == "S"


# -- disk / memory checks and the responsive screen (export 57.9.6a, 57.9.9) ---------------------------


@pytest.fixture
def start_question(monkeypatch):
    """Replaces the "may not fit" question; `answer` is what the user presses."""
    from parquet_analyzer.ui import messages

    class Q:
        answer = False
        asked: list[str] = []

    q = Q()
    q.asked = []
    monkeypatch.setattr(
        messages, "confirm_export_start", lambda parent, title, text, start, cancel: (q.asked.append(text), q.answer)[1]
    )
    return q


def short_disk(monkeypatch, free):
    from parquet_analyzer.core import export_resources as res

    monkeypatch.setattr(res, "disk_free_bytes", lambda p: free)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 64 * res.GB)


def test_not_enough_disk_asks_and_the_default_answer_does_not_export(
    win, dialogs, start_question, tmp_path, monkeypatch, log
):
    plot_signals(win)
    short_disk(monkeypatch, 2 * 1024**3)  # the output (upper bound) needs more than 2 GB minus the margin
    monkeypatch.setattr(pe, "estimate_output_bytes", lambda plan: 5 * 1024**3)
    win._actions["export_parquet"].trigger()
    assert len(start_question.asked) == 1 and "5.0 GB" in start_question.asked[0]
    assert win._parquet_export is None and not (tmp_path / "out.parquet").exists()
    assert op_records("export_parquet_preflight")


def test_not_enough_disk_can_be_started_anyway(win, dialogs, start_question, tmp_path, monkeypatch):
    plot_signals(win)
    from parquet_analyzer.core import export_resources as res

    monkeypatch.setattr(pe, "estimate_output_bytes", lambda plan: 5 * 1024**3)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 3 * 1024**3)  # a question, but above the 1 GB stop
    start_question.answer = True
    win._actions["export_parquet"].trigger()
    finish(win)
    assert len(start_question.asked) == 1
    assert pq.read_table(tmp_path / "out.parquet").num_rows == len(win._time_values)


def test_enough_room_asks_nothing(win, dialogs, start_question, tmp_path):
    plot_signals(win)
    win._actions["export_parquet"].trigger()
    finish(win)
    assert start_question.asked == [] and (tmp_path / "out.parquet").exists()


def test_not_enough_memory_asks(win, dialogs, start_question, monkeypatch):
    from parquet_analyzer.core import export_resources as res

    plot_signals(win)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 100 * 1024**2)
    win._actions["export_parquet"].trigger()
    assert len(start_question.asked) == 1 and win._parquet_export is None


def test_disk_running_out_fails_the_export_with_a_clear_message(win, dialogs, start_question, boxes, tmp_path, monkeypatch):
    plot_signals(win)
    from parquet_analyzer.core import export_resources as res

    start_question.answer = True
    monkeypatch.setattr(pe, "estimate_output_bytes", lambda plan: 1)  # no question; the run-time guard acts
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: res.MB)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 64 * res.GB)
    win._actions["export_parquet"].trigger()
    finish(win)
    assert boxes.warn and tr("export_parquet.disk_full") in boxes.warn[-1]
    assert list(tmp_path.glob("out*")) == []


def test_the_screen_keeps_running_while_exporting(win, dialogs, tmp_path, monkeypatch, slow):
    """A 10 ms timer on the UI thread keeps firing during an export that takes well over a second
    (57.9.9). Run on the UI thread, that same work would block the timer for the whole second, far
    beyond the 250 ms limit: the work is slowed on purpose and the thread it runs on is recorded."""
    from PySide6.QtCore import QTimer

    plot_signals(win)
    monkeypatch.setattr(MainWindow, "_PARQUET_EXPORT_CHUNK_ROWS", 2_000)  # 25 chunks of 2,000 rows
    slow.delay = 0.08
    threads: set[int] = set()
    inner = pe.evaluate_expression

    def spy(expr, variables, registry=None):
        threads.add(threading.get_ident())
        return inner(expr, variables, registry)

    monkeypatch.setattr(pe, "evaluate_expression", spy)
    stamps: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: stamps.append(time.perf_counter()))
    timer.start()
    t0 = time.perf_counter()
    win._actions["export_parquet"].trigger()
    finish(win)
    elapsed = time.perf_counter() - t0
    timer.stop()
    assert elapsed > 1.0  # the work is long enough that a UI-thread run could not hide
    assert threading.get_ident() not in threads and threads  # it ran off the UI thread
    assert len(stamps) >= 30
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert max(gaps) < 0.25 < elapsed  # on the UI thread the gap would be the whole export
    assert pq.read_table(tmp_path / "out.parquet").num_rows == len(win._time_values)


def test_a_finished_export_logs_the_writer_and_the_memory_guard(win, dialogs, tmp_path, log):
    plot_signals(win)
    win._actions["export_parquet"].trigger()
    finish(win)
    oplog.flush()
    rows = [json.loads(line) for line in oplog.log_path_for(date.today()).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    texts = [r["text"] for r in rows if r.get("kind") == "message" and r.get("where") == "export_parquet"]
    assert len(texts) == 1
    assert "writer=polars" in texts[0] and "chunk_halvings=0" in texts[0] and "memory_wait=0.0s" in texts[0]
