"""One view applied to several files, and the export queue's window (export.ja.md 57.8.2, 57.9.4,
57.9.7, 57.9.8, 57.9.9).

Real routing where it is input handling (button clicks with QTest); the modal dialogs' `exec`, the
file dialogs and the message boxes are replaced. Workers run for real; every wait is in real time."""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
)
from waiting import wait_until

from parquet_analyzer import config, features
from parquet_analyzer.core import export_queue as eq
from parquet_analyzer.core import oplog
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.export_queue import ItemState as S
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui import main_window as main_window_module
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
def win(qapp, isolated_config, monkeypatch, boxes):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
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


def make_file(path: Path, n: int, columns=("time", "a", "b", "c"), seed=0) -> Path:
    rng = np.random.default_rng(seed)
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    data = {columns[0]: pa.array(t)}
    for c in columns[1:]:
        data[c] = rng.normal(size=n)
    pq.write_table(pa.table(data), path)
    return path


def save_view(name, source, plots, derived=(), time_column="time"):
    view_io.save_view(
        View(
            view_name=name,
            source=SourceDef(str(source), "absolute"),
            plots=[PlotDef(f"p{i}", [SeriesDef(n, "#ff0000") for n in names]) for i, names in enumerate(plots)],
            derived_variables=[DerivedVariableDef(n, e) for n, e in derived],
            time_column=time_column,
        )
    )


@pytest.fixture
def files(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    return [make_file(d / "f1.parquet", 4000, seed=1), make_file(d / "f2.parquet", 3000, seed=2),
            make_file(d / "f3.parquet", 2000, seed=3)]


class Driver:
    """Replaces the export dialog's exec and the folder / save dialogs; clicks the real buttons."""

    def __init__(self, monkeypatch, out_dir: Path):
        self.out_dir = out_dir
        self.view = "v1"
        self.other_files: list[str] = []
        self.names: list[str] | None = None
        self.accept = True
        self.opened = 0
        self.seen: dict = {}
        self.folder_args = None
        self.save_args = None
        self.time_choice: str | None = None
        self.dtype: str | None = None
        self.saved_range = False

        def exec_(dlg):
            self.opened += 1
            self.dlg = dlg
            dlg.source_combo.setCurrentIndex(dlg.source_combo.findData(self.view))
            if self.other_files:
                monkeypatch.setattr(main_window_module, "pick_parquet_files", lambda *a, **k: list(self.other_files))
                QTest.mouseClick(dlg.other_file_button, Qt.MouseButton.LeftButton)
            self.seen["time_items"] = [dlg.time_combo.itemText(i) for i in range(dlg.time_combo.count())]
            self.seen["time_visible"] = not dlg.time_widget.isHidden()
            if self.time_choice is not None:
                idx = dlg.time_combo.findData(self.time_choice)
                dlg.time_combo.setCurrentIndex(idx)
                dlg.time_combo.activated.emit(idx)  # what a click on the entry does
            if self.dtype is not None:
                dlg.dtype_combo.setCurrentIndex(dlg.dtype_combo.findData(self.dtype))
            if self.names is not None:
                for i in range(dlg.list.count()):
                    item = dlg.list.item(i)
                    if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        keep = item.data(Qt.ItemDataRole.UserRole) in self.names
                        item.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
            dlg.visible_radio.setChecked(self.saved_range)
            self.seen["files"] = [dlg.files_list.item(i).text() for i in range(dlg.files_list.count())]
            self.seen["files_visible"] = not dlg.files_list.isHidden()
            self.seen["file_label"] = dlg.file_label.text()
            self.seen["all"] = dlg.all_radio.text()
            self.seen["ok"] = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
            if not self.accept:
                return QDialog.DialogCode.Rejected
            QTest.mouseClick(dlg.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
            return QDialog.DialogCode.Accepted if dlg.result() == QDialog.DialogCode.Accepted else QDialog.DialogCode.Rejected

        def get_dir(parent, caption, directory="", *a, **k):
            self.folder_args = (caption, directory)
            return str(self.out_dir)

        def get_save(parent, caption, directory, filter_, **kw):
            self.save_args = (caption, directory)
            return str(self.out_dir / "single.parquet"), ""

        monkeypatch.setattr(ParquetExportDialog, "exec", exec_)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(get_dir))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save))


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


@pytest.fixture
def driver(monkeypatch, out_dir):
    return Driver(monkeypatch, out_dir)


def wait_idle(win, timeout=30.0):
    assert wait_until(lambda: win._export.queue.is_idle(), timeout), "the exports never finished"
    QApplication.processEvents()


def outputs(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*.parquet"))


def by_source(win) -> dict[str, eq.ExportItem]:
    return {Path(i.source).name: i for i in win._export.queue.items}


# -- several files, one view (57.8.2) --------------------------------------------------------------


def test_three_files_give_three_outputs_with_the_right_source_signals_and_rows(win, files, driver, out_dir, boxes):
    save_view("v1", files[0], [["a", "dd"]], derived=[("dd", "a * 2 + b")])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert driver.opened == 1 and driver.folder_args is not None and driver.save_args is None
    assert driver.seen["files_visible"] and len(driver.seen["files"]) == 3
    outs = outputs(out_dir)
    assert len(outs) == 3
    items = by_source(win)
    for f, n in zip(files, (4000, 3000, 2000), strict=True):
        item = items[f.name]
        out = Path(item.output)
        assert out.parent == out_dir and out.name.startswith(f"{f.stem}_v1_") and out.name.endswith(".parquet")
        table = pq.read_table(out)
        src = pq.read_table(f)
        assert table.column_names == ["time", "a", "dd"] and table.num_rows == n
        np.testing.assert_array_equal(table["a"].to_numpy(), src["a"].to_numpy())
        np.testing.assert_array_equal(table["dd"].to_numpy(), src["a"].to_numpy() * 2 + src["b"].to_numpy())
        meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
        assert meta["source"] == str(f.resolve()) and meta["view"] == "v1"
        assert item.state is S.DONE
    assert boxes.warn == []  # all fine: a status-bar line only
    assert "3" in win._notice_label.text() and str(out_dir) in win._notice_label.text()
    assert win.settings.last_export_folder == str(out_dir.resolve())


def test_the_output_name_is_source_view_and_timestamp(win, files, driver, out_dir):
    save_view("my view 1", files[0], [["a"]])
    driver.view = "my view 1"
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    for item in win._export.queue.items:
        name = Path(item.output).name
        prefix = f"{Path(item.source).stem}_my view 1_"
        assert name.startswith(prefix)
        datetime.strptime(name[len(prefix) : -len(".parquet")], "%Y%m%d_%H%M%S")


def test_unsafe_characters_in_a_name_become_underscores():
    assert MainWindow._safe_name('a/b:c*d') == "a_b_c_d"
    assert MainWindow._safe_name("  ..") == "view"


def test_files_with_different_column_names_are_resolved_by_aliases(win, tmp_path, files, driver, out_dir):
    odd = make_file(tmp_path / "in" / "odd.parquet", 1500, columns=("ts", "A_raw", "b", "c"), seed=9)
    win.save_signal_aliases({"a": ["A_raw"], "time": ["ts"]})
    save_view("v1", files[0], [["a", "b"]])
    driver.other_files = [str(files[0]), str(odd)]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    table = pq.read_table(by_source(win)["odd.parquet"].output)
    assert table.column_names == ["time", "a", "b"] and table.num_rows == 1500  # the view's names
    np.testing.assert_array_equal(table["a"].to_numpy(), pq.read_table(odd)["A_raw"].to_numpy())
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["aliases"] == {"a": "A_raw", "time": "ts"}


def test_a_signal_a_file_lacks_is_left_out_and_named_before_and_after(win, tmp_path, files, driver, out_dir, boxes):
    short = make_file(tmp_path / "in" / "short.parquet", 1200, columns=("time", "a", "c"), seed=5)  # no b
    save_view("v1", files[0], [["a", "b", "c"]])
    driver.other_files = [str(files[0]), str(short)]
    win._actions["export_parquet"].trigger()
    # before the run: the dialog's per-file list names the missing signal
    texts = driver.seen["files"]
    assert len(texts) == 2 and "b" in texts[1] and short.name in texts[1]
    assert texts[0] == tr("export_parquet.file_ok", name="f1.parquet")
    wait_idle(win)
    item = by_source(win)["short.parquet"]
    assert item.state is S.DONE and item.missing == ("b",)
    assert pq.read_table(item.output).column_names == ["time", "a", "c"]
    assert pq.read_table(by_source(win)["f1.parquet"].output).column_names == ["time", "a", "b", "c"]
    assert len(boxes.warn) == 1  # the final notice
    assert short.name in boxes.warn[0] and "b" in boxes.warn[0] and tr("export_parquet.batch_missing_head") in boxes.warn[0]


def test_a_file_with_no_usable_signal_fails_alone_and_the_rest_are_written(win, tmp_path, files, driver, out_dir, boxes):
    other = make_file(tmp_path / "in" / "other.parquet", 900, columns=("time", "x", "y"), seed=6)
    save_view("v1", files[0], [["a", "b"]])
    driver.other_files = [str(other), str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    items = by_source(win)
    assert items["other.parquet"].state is S.FAILED
    assert items["other.parquet"].error == tr("export_parquet.no_usable_signal")
    assert items["f1.parquet"].state is S.DONE and items["f2.parquet"].state is S.DONE
    assert len(outputs(out_dir)) == 2 and not list(out_dir.glob("*.part"))
    assert len(boxes.warn) == 1 and "other.parquet" in boxes.warn[0] and tr("export_parquet.no_usable_signal") in boxes.warn[0]


def test_an_unreadable_file_fails_alone(win, tmp_path, files, driver, out_dir, boxes):
    gone = tmp_path / "in" / "gone.parquet"
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(gone), str(files[0])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    items = by_source(win)
    assert items["gone.parquet"].state is S.FAILED and str(gone) in items["gone.parquet"].error
    assert items["f1.parquet"].state is S.DONE
    assert len(boxes.warn) == 1 and "gone.parquet" in boxes.warn[0]


def test_a_batch_never_overwrites_and_the_same_name_gets_a_counter(win, tmp_path, files, driver, out_dir, monkeypatch):
    """Two sources of the same name in one second collide on `<name>_<view>_<stamp>`; existing files stay untouched."""

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 4, 5, 6, 7)

    monkeypatch.setattr(main_window_module, "datetime", FixedNow)
    other_dir = tmp_path / "in2"
    other_dir.mkdir()
    twin = make_file(other_dir / "f1.parquet", 700, seed=11)
    save_view("v1", files[0], [["a"]])
    existing = out_dir / "f1_v1_20260304_050607.parquet"
    existing.write_bytes(b"keep me")
    driver.other_files = [str(files[0]), str(twin)]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    names = sorted(p.name for p in outputs(out_dir))
    assert names == ["f1_v1_20260304_050607.parquet", "f1_v1_20260304_050607_2.parquet", "f1_v1_20260304_050607_3.parquet"]
    assert existing.read_bytes() == b"keep me"
    assert pq.read_table(out_dir / "f1_v1_20260304_050607_2.parquet").num_rows == 4000
    assert pq.read_table(out_dir / "f1_v1_20260304_050607_3.parquet").num_rows == 700


def test_the_view_is_not_opened_and_the_open_file_is_untouched(win, files, driver, out_dir, monkeypatch, sample_parquet_path):
    assert win.load_parquet(str(sample_parquet_path))
    before = (win._data_source, dict(win._derived), set(win._raw_variables))
    save_view("v1", files[0], [["a", "dd"]], derived=[("dd", "a + 1")])
    monkeypatch.setattr(MainWindow, "load_view", lambda *a: pytest.fail("load_view must not be called"))
    monkeypatch.setattr(MainWindow, "load_parquet", lambda *a: pytest.fail("the file must not be opened"))
    monkeypatch.setattr(main_window_module, "ParquetDataSource", lambda *a, **k: pytest.fail("no data source"))
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert len(outputs(out_dir)) == 3
    assert (win._data_source, dict(win._derived), set(win._raw_variables)) == before


def test_one_file_keeps_the_single_file_flow(win, files, driver, out_dir):
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[1])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert driver.save_args is not None and driver.folder_args is None  # the save dialog, not the folder
    assert driver.seen["files_visible"] is False
    assert [p.name for p in outputs(out_dir)] == ["single.parquet"]
    assert json.loads(pq.read_schema(out_dir / "single.parquet").metadata[pe.METADATA_KEY])["source"] == str(files[1].resolve())
    assert win._export.panel is None  # one file does not open the list


def test_cancelling_the_folder_dialog_exports_nothing(win, files, driver, out_dir, monkeypatch):
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    win._actions["export_parquet"].trigger()
    assert win._export.queue.items == [] and outputs(out_dir) == []


def test_the_per_file_list_follows_the_checked_signals(win, tmp_path, files, driver):
    short = make_file(tmp_path / "in" / "short.parquet", 100, columns=("time", "a", "c"), seed=5)
    save_view("v1", files[0], [["a", "b", "c"]])
    driver.other_files = [str(files[0]), str(short)]
    driver.accept = False
    win._actions["export_parquet"].trigger()
    dlg = driver.dlg
    assert dlg.source_files() == [str(files[0]), str(short)] and dlg.source_file_override() is None
    texts = lambda: [dlg.files_list.item(i).text() for i in range(dlg.files_list.count())]  # noqa: E731
    assert tr("export_parquet.file_missing", name="short.parquet", n=1, names="b") == texts()[1]
    assert texts()[0] == tr("export_parquet.file_ok", name="f1.parquet")
    for i in range(dlg.list.count()):  # uncheck b: nothing is missing any more
        if dlg.list.item(i).data(Qt.ItemDataRole.UserRole) == "b":
            dlg.list.item(i).setCheckState(Qt.CheckState.Unchecked)
    assert texts()[1] == tr("export_parquet.file_ok", name="short.parquet")
    assert dlg.file_label.text() == tr("export_parquet.files_count", n=2)
    QTest.mouseClick(dlg.view_file_button, Qt.MouseButton.LeftButton)  # back to the view's own file
    assert dlg.source_files() == [] and dlg.files_list.isHidden()


def test_op_log_has_the_batch_record_and_one_record_per_file(win, files, driver, out_dir, log):
    win.settings.export_parallel_files = 3
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    (batch,) = op_records("export_parquet_batch")
    d = batch["details"]
    assert d["view"] == "v1" and d["folder"] == str(out_dir) and d["parallel"] == 3
    assert d["files"] == [str(f) for f in files]
    per_file = op_records("export_parquet")
    assert sorted(r["details"]["source"] for r in per_file) == sorted(str(f) for f in files)


# -- parallelism and the queue in the window (57.9) -----------------------------------------------


class Concurrency:
    """Wraps `export_parquet` to count how many run at once (inside the workers) and to optionally block."""

    def __init__(self, monkeypatch, delay=0.0):
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.delay = delay
        self.gate: threading.Event | None = None
        self.started: list[str] = []
        real = pe.export_parquet

        def wrapped(plan, path, progress=None, cancelled=None, overwrite=False, writer=None, **kw):
            with self.lock:
                self.now += 1
                self.peak = max(self.peak, self.now)
                self.started.append(Path(path).name)
            try:
                if self.gate is not None:
                    assert self.gate.wait(30)
                if self.delay:
                    time.sleep(self.delay)
                return real(plan, path, progress, cancelled, overwrite, writer, **kw)
            finally:
                with self.lock:
                    self.now -= 1

        monkeypatch.setattr(pe, "export_parquet", wrapped)


@pytest.fixture
def conc(monkeypatch):
    c = Concurrency(monkeypatch, delay=0.15)
    yield c
    if c.gate is not None:
        c.gate.set()


@pytest.mark.parametrize("parallel", [1, 2])
def test_no_more_files_run_at_once_than_the_setting(win, files, driver, out_dir, conc, parallel):
    win.settings.export_parallel_files = parallel
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert len(outputs(out_dir)) == 3
    assert conc.peak == parallel  # reached, never exceeded (the workers count themselves)
    assert win._export.pool.maxThreadCount() == parallel


def test_the_setting_is_clamped(win):
    for value, expect in ((0, 1), (1, 1), (4, 4), (9, 4), ("x", 2)):
        win.settings.export_parallel_files = value
        assert win._export.parallel() == expect


def test_waiting_files_start_in_order_as_slots_free(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    assert [i.state for i in win._export.queue.items] == [S.RUNNING, S.WAITING, S.WAITING]
    conc.gate.set()
    wait_idle(win)
    assert [Path(i.source).name for i in win._export.queue.items] == ["f1.parquet", "f2.parquet", "f3.parquet"]
    assert [n.split("_v1_")[0] for n in conc.started] == ["f1", "f2", "f3"]


def test_the_list_opens_for_two_files_and_closing_it_does_not_stop_the_export(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    panel = win._export.panel
    assert panel is not None and panel.objectName() == "parquet_export_panel" and panel.isVisible()
    assert panel.table.rowCount() == 3
    panel.close()
    assert not panel.isVisible()
    conc.gate.set()
    wait_idle(win)
    assert len(outputs(out_dir)) == 3  # it kept going
    # the status bar: hidden again, one summary line
    assert win._parquet_export_progress.isHidden()


def test_the_list_rows_show_state_percent_counts_and_the_status_bar_summary(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    win._export.refresh()
    panel = win._export.panel
    states = [panel.table.item(r, 2).text() for r in range(3)]
    assert states[0] == tr("export_parquet.state_writing") and states[1:] == [tr("export_parquet.state_waiting")] * 2
    assert panel.table.item(0, 0).text() == "f1.parquet" and panel.table.item(1, 0).text() == "f2.parquet"
    assert panel.table.item(0, 1).text().startswith("f1_v1_")
    assert panel.counts_label.text() == tr("export_parquet.panel_counts", done=0, failed=0, cancelled=0, total=3)
    status = win._parquet_export_progress
    assert not status.isHidden()
    assert status.label.text() == tr("export_parquet.progress_files", done=0, files=3, percent=0)
    assert status.cancel_button.text() == tr("export_parquet.cancel_all")
    conc.gate.set()
    assert wait_until(lambda: win._export.queue.counts()["done"] == 1 or win._export.queue.is_idle())
    wait_idle(win)
    win._export.refresh()
    assert [panel.table.item(r, 2).text() for r in range(3)] == [tr("export_parquet.state_done")] * 3
    assert panel.counts_label.text() == tr("export_parquet.panel_counts", done=3, failed=0, cancelled=0, total=3)
    assert [panel.table.cellWidget(r, 3).value() for r in range(3)] == [100] * 3


def test_the_percent_stays_below_100_until_the_job_is_done(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    item = win._export.queue.items[0]
    win._export._on_progress(item, item.total_rows, item.total_rows)  # the last chunk handed over
    win._export.refresh()
    assert win._parquet_export_progress.bar.value() <= 99
    assert win._export.panel.table.cellWidget(0, 3).value() <= 99
    conc.gate.set()
    wait_idle(win)


def test_failed_rows_show_the_reason_and_finished_rows_can_be_cleared(win, tmp_path, files, driver, out_dir, boxes):
    other = make_file(tmp_path / "in" / "other.parquet", 500, columns=("time", "x"), seed=6)
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(other), str(files[0])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    panel = win._export.panel
    win._export.refresh()
    assert panel.table.item(0, 2).text() == tr("export_parquet.state_failed")
    assert tr("export_parquet.no_usable_signal") in panel.table.item(0, 2).toolTip()
    panel.table.selectRow(0)
    assert tr("export_parquet.no_usable_signal") in panel.detail_label.text()  # the full text under the table
    QTest.mouseClick(panel.clear_button, Qt.MouseButton.LeftButton)
    assert win._export.queue.items == [] and panel.table.rowCount() == 0


def test_one_file_can_be_cancelled_from_the_list_and_the_others_finish(win, files, driver, out_dir, conc, log):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    panel = win._export.panel
    middle = win._export.queue.items[1]
    button = panel.table.cellWidget(1, 8)
    assert isinstance(button, QPushButton) and button.text() == tr("export_parquet.cancel")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)  # a waiting file: cancelled at once, never started
    assert middle.state is S.CANCELLED
    conc.gate.set()
    wait_idle(win)
    items = win._export.queue.items
    assert [i.state for i in items] == [S.DONE, S.CANCELLED, S.DONE]
    assert [Path(i.source).name for i in items if i.state is S.DONE] == ["f1.parquet", "f3.parquet"]
    assert len(outputs(out_dir)) == 2 and not list(out_dir.glob("*.part"))
    (rec,) = op_records("export_parquet_cancel")
    assert rec["details"]["target"] == middle.output


def test_cancel_all_stops_the_running_file_and_starts_none_of_the_rest(win, files, driver, out_dir, conc, log):
    win.settings.export_parallel_files = 1
    save_view("v1", files[0], [["a", "dd"]], derived=[("dd", "a * 2")])
    blocked = threading.Event()
    real = pe.evaluate_expression
    entered = threading.Event()

    def hold(expr, variables, registry=None):
        entered.set()
        assert blocked.wait(30)
        return real(expr, variables, registry)

    pe_eval = pe.evaluate_expression
    try:
        pe.evaluate_expression = hold
        driver.other_files = [str(f) for f in files]
        win._actions["export_parquet"].trigger()
        assert wait_until(entered.is_set)
        first = win._export.queue.items[0]
        QTest.mouseClick(win._parquet_export_progress.cancel_button, Qt.MouseButton.LeftButton)
        assert [i.state for i in win._export.queue.items] == [S.CANCELLING, S.CANCELLED, S.CANCELLED]
        assert win._parquet_export_progress.isHidden()
        assert first.cancel_event.is_set()
    finally:
        blocked.set()
        pe.evaluate_expression = pe_eval
    wait_idle(win)
    assert [i.state for i in win._export.queue.items] == [S.CANCELLED] * 3
    assert outputs(out_dir) == [] and not list(out_dir.glob("*.part"))
    assert len(conc.started) == 1  # the others never started
    assert win._notice_label.text() == tr("export_parquet.cancelled")
    (rec,) = op_records("export_parquet_cancel")
    assert rec["details"]["target"] == "all"


def test_files_finished_before_a_cancel_stay(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.delay = 0.0
    conc.gate = None
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    state = {"n": 0}
    real_done = win._export._on_finished

    def after_first(item, result):
        real_done(item, result)
        state["n"] += 1
        if state["n"] == 1:
            win._export.cancel_all(None)

    win._export._on_finished = after_first
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    states = [i.state for i in win._export.queue.items]
    assert states[0] is S.DONE and states[1:] == [S.CANCELLED, S.CANCELLED]
    assert len(outputs(out_dir)) == 1


def test_closing_the_window_cancels_everything_and_leaves_no_part_file(qapp, isolated_config, monkeypatch, boxes, files, driver, out_dir, conc):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    w = MainWindow()
    w.show()
    w.settings.export_parallel_files = 2
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    w._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 2)
    threading.Timer(0.3, conc.gate.set).start()  # the workers wake up after the close began
    w.close()
    assert w._export.queue.in_state(S.WAITING) == []
    assert wait_until(lambda: not list(out_dir.glob("*.part")))
    assert outputs(out_dir) == [] or all(not p.name.endswith(".part") for p in outputs(out_dir))
    assert len(conc.started) == 2  # the third never started


def test_a_full_disk_in_a_row_fails_the_rest_with_one_notice(win, files, driver, out_dir, boxes, monkeypatch):
    win.settings.export_parallel_files = 1
    four = [*files, make_file(files[0].parent / "f4.parquet", 500, seed=4)]

    def full(plan, path, progress=None, cancelled=None, overwrite=False, writer=None, **kw):
        raise pe.DiskFull()

    monkeypatch.setattr(pe, "export_parquet", full)
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in four]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    states = [i.state for i in win._export.queue.items]
    assert states == [S.FAILED] * 4
    assert [i.started_at is not None for i in win._export.queue.items] == [True, True, False, False]
    assert win._export.queue.items[3].error == eq.DISK_ERROR
    assert len(boxes.warn) == 1
    assert tr("export_parquet.disk_full") in boxes.warn[0]


def test_an_export_added_while_others_run_goes_to_the_end(win, tmp_path, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    extra = tmp_path / "extra.parquet"
    assert win.export_parquet_from_view(str(extra), "v1", ["a"], source_file=str(files[2]))
    assert [Path(i.source).name for i in win._export.queue.items] == ["f1.parquet", "f2.parquet", "f3.parquet"]
    assert win._export.queue.items[-1].output == str(extra)
    conc.gate.set()
    wait_idle(win)
    assert extra.exists() and len(outputs(out_dir)) == 2


def test_an_open_folder_button_asks_the_os_for_the_output_folder(win, files, driver, out_dir):
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[0]), str(files[1])]
    opened: list[str] = []
    win._export.open_folder = opened.append
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    win._export.refresh()
    button = win._export.panel.table.cellWidget(0, 8)
    assert button.text() == tr("export_parquet.open_folder")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert opened == [str(out_dir)]


# -- the screen never stops (57.9.9) ---------------------------------------------------------------


def slow_evaluate(monkeypatch, delay):
    real = pe.evaluate_expression

    def slow(expr, variables, registry=None):
        time.sleep(delay)
        return real(expr, variables, registry)

    monkeypatch.setattr(pe, "evaluate_expression", slow)


def measure_gaps(run, until, timeout=60.0) -> tuple[float, int]:
    """(largest gap between two ticks of a 10 ms timer on the UI thread, ticks) while `run()` has started
    and until `until()` is true."""
    stamps: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: stamps.append(time.monotonic()))
    timer.start()
    stamps.append(time.monotonic())
    run()
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    stamps.append(time.monotonic())
    timer.stop()
    return max(b - a for a, b in zip(stamps, stamps[1:], strict=False)), len(stamps)


def test_the_screen_keeps_running_while_three_files_are_written(win, tmp_path, driver, out_dir, monkeypatch):
    srcs = [make_file(tmp_path / "in" / f"s{i}.parquet", 30_000, seed=i) for i in range(3)] if (tmp_path / "in").mkdir() is None else []
    monkeypatch.setattr(MainWindow, "_PARQUET_EXPORT_CHUNK_ROWS", 2_000)  # 15 chunks per file
    slow_evaluate(monkeypatch, 0.04)
    save_view("v1", srcs[0], [["a", "dd"]], derived=[("dd", "a * 2")])
    driver.other_files = [str(s) for s in srcs]
    seen_percent: set[int] = set()
    refreshes_before = 0

    def run():
        nonlocal refreshes_before
        refreshes_before = win._export.refreshes
        win._actions["export_parquet"].trigger()

    def until():
        seen_percent.add(win._parquet_export_progress.bar.value())
        return win._export.queue.is_idle()

    gap, ticks = measure_gaps(run, until)
    assert len(outputs(out_dir)) == 3
    assert gap < 0.25, f"the screen stalled for {gap:.3f}s"
    assert ticks > 20
    assert win._export.refreshes - refreshes_before >= 2  # progress reached the screen during the run
    assert any(0 < p < 100 for p in seen_percent), seen_percent


def test_the_same_export_on_the_ui_thread_does_stall_the_screen(win, tmp_path, monkeypatch):
    """The amount of work in the test above is enough to be noticed when done on the UI thread (the
    regression check for the test above: it fails when the export is not in the background)."""
    src = make_file(tmp_path / "s.parquet", 30_000, seed=1)
    monkeypatch.setattr(MainWindow, "_PARQUET_EXPORT_CHUNK_ROWS", 2_000)
    slow_evaluate(monkeypatch, 0.04)
    save_view("v1", src, [["a", "dd"]], derived=[("dd", "a * 2")])
    ctx = win._view_export_context("v1", None)
    plan = win._build_view_export_plan(ctx, ["a", "dd"], False)
    done = {"v": False}

    def run():
        pe.export_parquet(plan, str(tmp_path / "direct.parquet"))
        done["v"] = True

    gap, _ticks = measure_gaps(run, lambda: done["v"])
    assert gap >= 0.25


def test_the_display_is_redrawn_about_four_times_a_second_however_many_reports_arrive(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    item = win._export.queue.items[0]
    before = win._export.refreshes
    start = time.monotonic()
    for i in range(3000):  # a flood of reports: they are only stored
        win._export._on_progress(item, i % item.total_rows, item.total_rows)
    assert win._export.refreshes == before
    wait_until(lambda: time.monotonic() - start >= 1.0, timeout=3.0)
    per_second = (win._export.refreshes - before) / (time.monotonic() - start)
    assert 2.0 <= per_second <= 6.0, per_second
    conc.gate.set()
    wait_idle(win)


# -- stage B2: time axis, float32, settings (57.8.3, 57.9.6b) ---------------------------------------------


def make_time_file(path: Path, n: int, time_name="time", other="t2"):
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    big = np.full(n, 1e30)
    big[0] = 3e38
    pq.write_table(
        pa.table({
            time_name: pa.array(t), other: pa.array(np.arange(n, dtype="int64") * 7), "a": np.arange(n, dtype="float64"),
            "big": big, "label": pa.array(["x"] * n),
        }),
        path,
    )
    return path


def test_the_time_axis_combo_lists_the_views_axis_then_usable_columns(win, tmp_path, driver, out_dir):
    f = make_time_file(tmp_path / "in" / "tf.parquet", 500) if (tmp_path / "in").mkdir() is None else None
    save_view("v1", f, [["a"]])
    driver.accept = False
    win._actions["export_parquet"].trigger()
    assert driver.seen["time_visible"]
    assert driver.seen["time_items"] == [tr("export_parquet.time_axis_view", name="time"), "time", "t2", "a", "big"]  # no string column


def test_the_time_axis_combo_is_not_shown_for_the_current_screen(win, driver, sample_parquet_path):
    assert win.load_parquet(str(sample_parquet_path))
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    wait_until(lambda: not win._busy_sources)
    driver.view = None
    driver.accept = False
    win._actions["export_parquet"].trigger()
    assert driver.seen["time_visible"] is False and driver.dlg.selected_time_column() is None


def test_a_chosen_time_axis_is_the_first_output_column_and_the_default_is_unchanged(win, tmp_path, driver, out_dir):
    (tmp_path / "in").mkdir()
    f = make_time_file(tmp_path / "in" / "tf.parquet", 500)
    save_view("v1", f, [["a"]])
    win._actions["export_parquet"].trigger()  # the default
    wait_idle(win)
    assert pq.read_schema(out_dir / "single.parquet").names == ["time", "a"]
    (out_dir / "single.parquet").unlink()
    driver.time_choice = "t2"
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    table = pq.read_table(out_dir / "single.parquet")
    assert table.column_names == ["t2", "a"] and table["t2"].to_pylist() == [i * 7 for i in range(500)]
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["time_column"] == "t2"


def test_a_time_axis_chosen_for_several_files_fails_the_file_that_lacks_it(win, tmp_path, driver, out_dir, boxes):
    (tmp_path / "in").mkdir()
    a = make_time_file(tmp_path / "in" / "a.parquet", 400)
    b = make_time_file(tmp_path / "in" / "b.parquet", 300, time_name="ts", other="t2")
    c = make_time_file(tmp_path / "in" / "c.parquet", 200, other="other")  # no t2
    save_view("v1", a, [["a"]])
    driver.other_files = [str(a), str(b), str(c)]
    driver.time_choice = "t2"
    driver.accept = True
    win._actions["export_parquet"].trigger()
    assert any(tr("export_parquet.time_missing", name="t2") in t for t in driver.seen["files"])  # said before the run
    wait_idle(win)
    items = by_source(win)
    assert items["a.parquet"].state is S.DONE and items["b.parquet"].state is S.DONE
    assert items["c.parquet"].state is S.FAILED and tr("export_parquet.time_missing", name="t2") in items["c.parquet"].error
    assert pq.read_schema(items["b.parquet"].output).names == ["t2", "a"]
    assert len(boxes.warn) == 1 and "c.parquet" in boxes.warn[0]


def test_float32_through_the_dialog_with_a_warning_for_values_that_do_not_fit(win, tmp_path, driver, out_dir, boxes):
    (tmp_path / "in").mkdir()
    f = make_time_file(tmp_path / "in" / "tf.parquet", 500)
    save_view("v1", f, [["a", "dd", "dbig"]], derived=[("dd", "a * 0.5"), ("dbig", "big * 2")])
    driver.dtype = "float32"
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    schema = pq.read_schema(out_dir / "single.parquet")
    assert [str(schema.field(n).type) for n in ("time", "a", "dd", "dbig")][1:] == ["double", "float", "float"]
    assert str(schema.field("time").type).startswith("timestamp")
    assert json.loads(schema.metadata[pe.METADATA_KEY])["derived_dtype"] == "float32"
    assert len(boxes.warn) == 1 and "dbig" in boxes.warn[0] and "dd" not in boxes.warn[0].replace("dbig", "")


def test_float64_is_the_default_in_the_dialog(win, tmp_path, driver, out_dir):
    (tmp_path / "in").mkdir()
    f = make_time_file(tmp_path / "in" / "tf.parquet", 100)
    save_view("v1", f, [["a", "dd"]], derived=[("dd", "a * 0.5")])
    driver.accept = False
    win._actions["export_parquet"].trigger()
    assert driver.dlg.selected_dtype() == "float64"
    assert [driver.dlg.dtype_combo.itemData(i) for i in range(2)] == ["float64", "float32"]


def test_float32_for_several_files_warns_once_with_the_file_names(win, tmp_path, driver, out_dir, boxes):
    (tmp_path / "in").mkdir()
    a = make_time_file(tmp_path / "in" / "a.parquet", 100)
    b = make_time_file(tmp_path / "in" / "b.parquet", 100)
    save_view("v1", a, [["dbig"]], derived=[("dbig", "big * 2")])
    driver.other_files = [str(a), str(b)]
    driver.dtype = "float32"
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert len(boxes.warn) == 1
    assert tr("export_parquet.batch_overflow_head") in boxes.warn[0] and "a.parquet" in boxes.warn[0] and "b.parquet" in boxes.warn[0]


def test_the_settings_dialog_edits_the_number_of_files_at_once(win, monkeypatch):
    from parquet_analyzer.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win.settings)
    assert dlg.export_parallel_spin.minimum() == 1 and dlg.export_parallel_spin.maximum() == 4
    assert dlg.export_parallel_spin.value() == 2
    dlg.export_parallel_spin.setValue(3)
    assert dlg.result_settings().export_parallel_files == 3
    assert dlg.result_settings().export_disk_margin_mb == win.settings.export_disk_margin_mb  # no UI; kept
    win.apply_settings(dlg.result_settings())
    assert win._export.parallel() == 3


def test_the_settings_entry_follows_the_feature_flag(qapp, isolated_config, monkeypatch):
    from parquet_analyzer.io.settings import Settings
    from parquet_analyzer.ui.settings_dialog import SettingsDialog

    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", False)
    off = SettingsDialog(Settings())
    assert off.export_parallel_spin.parent() is None or not off.export_parallel_spin.isVisibleTo(off)
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    on = SettingsDialog(Settings())
    assert on.export_parallel_spin.parent() is not None


def test_the_window_applies_the_disk_margin_setting(qapp, isolated_config, monkeypatch):
    import dataclasses

    from parquet_analyzer.core import export_resources as res
    from parquet_analyzer.io import settings as S

    S.save_settings(dataclasses.replace(S.Settings(), export_disk_margin_mb=321))
    w = MainWindow()
    try:
        assert res.disk_margin() == 321 * res.MB
        w.apply_settings(dataclasses.replace(w.settings, export_disk_margin_mb=654))
        assert res.disk_margin() == 654 * res.MB
    finally:
        w.close()
        res.set_disk_margin_mb(None)


# -- review fixes ------------------------------------------------------------------------------------------


def test_room_made_after_a_full_disk_lets_the_next_group_run(win, files, driver, out_dir, boxes, monkeypatch):
    """The full-disk streak belongs to its own group: after two DiskFull failures and some cleaning the next
    export must start, not fail at once (fails without `queue.new_batch()` in `submit`)."""
    win.settings.export_parallel_files = 1
    real = pe.export_parquet
    state = {"full": True}

    def maybe_full(plan, path, *a, **kw):
        if state["full"]:
            raise pe.DiskFull()
        return real(plan, path, *a, **kw)

    monkeypatch.setattr(pe, "export_parquet", maybe_full)
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert [i.state for i in win._export.queue.items] == [S.FAILED] * 3
    assert win._export.queue.disk_streak >= 2
    state["full"] = False  # the user made room
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    assert [i.state for i in win._export.queue.items] == [S.DONE, S.DONE]
    assert len(outputs(out_dir)) == 2


def test_the_panel_columns_are_wide_enough_for_their_text(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(f) for f in files]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    win._export.refresh()
    panel = win._export.panel
    table = panel.table
    fm = table.fontMetrics()
    for row, col in ((0, 0), (0, 2)):  # the file name and the state ("Writing")
        text = table.item(row, col).text()
        assert table.columnWidth(col) >= fm.horizontalAdvance(text), (text, table.columnWidth(col))
    action = table.cellWidget(0, 8)
    probe = QPushButton(tr("export_parquet.open_folder"))
    assert table.columnWidth(8) >= probe.sizeHint().width()
    assert action.width() >= 40
    conc.gate.set()
    wait_idle(win)
    win._export.refresh()
    assert table.columnWidth(8) >= probe.sizeHint().width()
    assert table.columnWidth(2) >= fm.horizontalAdvance(table.item(0, 2).text())


def test_a_saved_range_with_another_output_axis_fails_a_file_without_the_views_axis(win, tmp_path, driver, out_dir, boxes):
    (tmp_path / "in").mkdir()
    ok = make_time_file(tmp_path / "in" / "ok.parquet", 600)
    odd = tmp_path / "in" / "odd.parquet"
    pq.write_table(pa.table({"t2": pa.array(np.arange(300, dtype="int64")), "a": np.arange(300.0)}), odd)  # no "time"
    view_io.save_view(
        View(
            view_name="v1", source=SourceDef(str(ok), "absolute"), plots=[PlotDef("p", [SeriesDef("a", "#f00")])],
            time_column="time", x_axis_range=(1767225600.0 + 1.0, 1767225600.0 + 2.0),  # 2026-01-01 + 1..2 s
        )
    )
    driver.other_files = [str(ok), str(odd)]
    driver.time_choice = "t2"
    driver.saved_range = True
    win._actions["export_parquet"].trigger()
    wait_idle(win)
    items = by_source(win)
    assert items["odd.parquet"].state is S.FAILED
    assert items["odd.parquet"].error == tr("export_parquet.range_axis_missing", name="time")
    assert items["ok.parquet"].state is S.DONE
    table = pq.read_table(items["ok.parquet"].output)
    assert table.column_names == ["t2", "a"] and table.num_rows == 101  # 10 ms steps over 1 s: the view's units


def test_cancel_says_requested_while_it_runs_and_cancelled_at_the_end(win, files, driver, out_dir, conc):
    win.settings.export_parallel_files = 1
    conc.gate = threading.Event()
    save_view("v1", files[0], [["a"]])
    driver.other_files = [str(files[0]), str(files[1])]
    win._actions["export_parquet"].trigger()
    assert wait_until(lambda: len(conc.started) == 1)
    QTest.mouseClick(win._parquet_export_progress.cancel_button, Qt.MouseButton.LeftButton)
    assert win._notice_label.text() == tr("export_parquet.cancel_requested")
    conc.gate.set()
    wait_idle(win)
    assert win._notice_label.text() == tr("export_parquet.cancelled")


def test_the_dialog_reads_each_file_footer_once_per_time_choice(win, files, driver, monkeypatch):
    calls: list[tuple] = []
    real = MainWindow._view_export_choices

    def counting(self, name, override, time_column=None):
        calls.append((override, time_column))
        return real(self, name, override, time_column)

    monkeypatch.setattr(MainWindow, "_view_export_choices", counting)
    save_view("v1", files[0], [["a", "b", "c"]])
    driver.other_files = [str(f) for f in files]
    driver.accept = False
    win._actions["export_parquet"].trigger()
    n = len(calls)
    dlg = driver.dlg
    for i in range(dlg.list.count()):  # ticking signals never reads a footer
        item = dlg.list.item(i)
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
    assert len(calls) == n
    dlg._reload()  # reloading with the same choice is answered from the cache
    assert len(calls) == n
