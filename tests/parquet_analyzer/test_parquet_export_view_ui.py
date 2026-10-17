"""Parquet export from a saved view through the UI (export.ja.md 57.7.1, 57.7.5, 57.7.6).

Real routing where it is input handling (button clicks with QTest); the modal dialogs' `exec`, the
file dialogs and the message boxes are replaced. The export runs on its worker; every wait is real time."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QMessageBox
from waiting import wait_until

from parquet_analyzer import config, features
from parquet_analyzer.core import oplog
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.numeric import to_numeric
from parquet_analyzer.core.view_check import classify_derived
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.i18n import tr
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.parquet_export_dialog import ParquetExportDialog
from parquet_analyzer.ui.view_manager import ViewManagerDialog


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
def flag_on(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)


@pytest.fixture
def empty_win(qapp, isolated_config, flag_on, boxes):
    """A window with no file open."""
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


@pytest.fixture
def win(empty_win, sample_parquet_path):
    assert empty_win.load_parquet(str(sample_parquet_path))
    return empty_win


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


def save_view(name, source, plots, derived=(), x_range=None, time_column="time"):
    view_io.save_view(
        View(
            view_name=name,
            source=SourceDef(str(source), "absolute"),
            plots=[PlotDef(f"p{i}", [SeriesDef(n, "#ff0000") for n in names]) for i, names in enumerate(plots)],
            derived_variables=[DerivedVariableDef(n, e) for n, e in derived],
            x_axis_range=x_range,
            time_column=time_column,
        )
    )


class Driver:
    """Replaces the export dialog's exec and the save dialog; clicks through the real buttons."""

    def __init__(self, monkeypatch, out):
        self.out = out
        self.pick: str | None = None  # the source (view name) to choose in the combo
        self.other_file: str | None = None  # clicks "Other file..." and answers this path
        self.names: list[str] | None = None
        self.saved_range = False
        self.accept = True
        self.opened = 0
        self.save_args: tuple | None = None
        self.seen: dict = {}

        def exec_(dlg):
            self.opened += 1
            self.dlg = dlg
            self.seen["combo"] = [dlg.source_combo.itemText(i) for i in range(dlg.source_combo.count())]
            self.seen["first"] = self.snapshot(dlg)
            if self.pick is not None:
                dlg.source_combo.setCurrentIndex(dlg.source_combo.findData(self.pick))
                self.seen["picked"] = self.snapshot(dlg)
            if self.other_file is not None:
                picked = [self.other_file] if isinstance(self.other_file, str) else list(self.other_file)
                monkeypatch.setattr(main_window_module, "pick_parquet_files", lambda *a, **k: picked)
                QTest.mouseClick(dlg.other_file_button, Qt.MouseButton.LeftButton)
                self.seen["other"] = self.snapshot(dlg)
            if self.names is not None:
                for i in range(dlg.list.count()):
                    item = dlg.list.item(i)
                    if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        keep = item.data(Qt.ItemDataRole.UserRole) in self.names
                        item.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
            dlg.visible_radio.setChecked(self.saved_range)
            if not self.accept:
                return QDialog.DialogCode.Rejected
            QTest.mouseClick(dlg.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
            return QDialog.DialogCode.Accepted if dlg.result() == QDialog.DialogCode.Accepted else QDialog.DialogCode.Rejected

        def get_save(parent, caption, directory, filter_, **kw):
            self.save_args = (caption, directory, filter_)
            self.save_kw = kw
            return str(self.out), ""

        monkeypatch.setattr(ParquetExportDialog, "exec", exec_)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save))

    @staticmethod
    def snapshot(dlg):
        items = []
        for i in range(dlg.list.count()):
            it = dlg.list.item(i)
            items.append(
                (
                    it.data(Qt.ItemDataRole.UserRole),
                    bool(it.flags() & Qt.ItemFlag.ItemIsUserCheckable),
                    it.checkState() == Qt.CheckState.Checked,
                    it.text(),
                )
            )
        return {
            "items": items,
            "file": dlg.file_label.text(),
            "error": dlg.error_label.text(),
            "ok": dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(),
            "all": dlg.all_radio.text(),
            "saved": dlg.visible_radio.text(),
            "saved_enabled": dlg.visible_radio.isEnabled(),
            "file_visible": not dlg.file_widget.isHidden(),
        }


@pytest.fixture
def driver(monkeypatch, tmp_path):
    return Driver(monkeypatch, tmp_path / "out.parquet")


def finish(win, timeout=30.0):
    assert wait_until(lambda: win._parquet_export is None, timeout), "the export never finished"
    QApplication.processEvents()


def epoch_of(path, row):
    t = pq.read_table(path, columns=["time"])["time"].to_numpy()
    return float(to_numeric(t[row : row + 1])[0])


# -- entry points (57.7.1) -------------------------------------------------------------------------


def test_view_manager_button_follows_the_feature_flag(qapp, isolated_config, monkeypatch, sample_parquet_path):
    save_view("v1", sample_parquet_path, [["a"]])
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", False)
    off = ViewManagerDialog(None)
    assert off.export_button.isHidden()
    monkeypatch.setitem(features._EFFECTIVE, "parquet_export", True)
    on = ViewManagerDialog(None)  # no file open: still usable
    on.show()
    assert not on.export_button.isHidden() and on.export_button.isEnabled()


def test_view_manager_button_opens_the_export_for_that_view_without_a_file(
    empty_win, sample_parquet_path, monkeypatch, driver
):
    save_view("v1", sample_parquet_path, [["a"]])
    save_view("v2", sample_parquet_path, [["b", "c"]])

    def manager_exec(dlg):
        dlg.view_list.setCurrentRow(1)  # v2
        QTest.mouseClick(dlg.export_button, Qt.MouseButton.LeftButton)
        return dlg.result()

    monkeypatch.setattr(ViewManagerDialog, "exec", manager_exec)
    loaded: list[str] = []
    monkeypatch.setattr(MainWindow, "load_view", lambda self, name: loaded.append(name))
    driver.accept = False
    empty_win.load_view_dialog()
    assert driver.opened == 1 and loaded == []  # export, not load
    assert driver.dlg.selected_source() == "v2"
    assert [i[0] for i in driver.seen["first"]["items"]] == ["b", "c"]
    assert empty_win._data_source is None


def test_toolbar_entry_without_a_file(empty_win, boxes, driver):
    empty_win._actions["export_parquet"].trigger()
    assert boxes.info == [tr("error.open_file_first")] and driver.opened == 0


def test_toolbar_entry_without_a_file_uses_saved_views(empty_win, sample_parquet_path, driver):
    save_view("v1", sample_parquet_path, [["a", "b"]])
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    assert driver.opened == 1
    assert driver.seen["combo"] == [tr("export_parquet.source_view", name="v1")]  # no "current screen"
    assert driver.dlg.selected_source() == "v1"
    assert driver.seen["first"]["file_visible"]


def test_dialog_offers_current_screen_and_views_and_switching_changes_the_content(win, sample_parquet_path, driver):
    win._on_variable_dropped(win.plot_grid.plots[0], "a")
    wait_until(lambda: not win._busy_sources)
    save_view("v1", sample_parquet_path, [["b", "c"]], x_range=(epoch_of(sample_parquet_path, 10), epoch_of(sample_parquet_path, 99)))
    driver.pick = "v1"
    driver.accept = False
    win._actions["export_parquet"].trigger()
    assert driver.seen["combo"] == [tr("export_parquet.source_current"), tr("export_parquet.source_view", name="v1")]
    first, picked = driver.seen["first"], driver.seen["picked"]
    assert [i[0] for i in first["items"]] == ["a"] and not first["file_visible"]
    assert [i[0] for i in picked["items"]] == ["b", "c"] and picked["file_visible"]
    assert picked["file"] == str(sample_parquet_path) and picked["ok"] and not picked["error"]
    assert picked["all"] == tr("export_parquet.range_all", rows=f"{50_000:,}")
    assert picked["saved_enabled"] and picked["saved"].startswith(tr("export_parquet.range_saved", range="")[:3])


def test_saved_range_choice_is_disabled_for_a_view_without_a_range(empty_win, sample_parquet_path, driver):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    assert not driver.seen["first"]["saved_enabled"]


def test_blocked_while_a_view_is_being_computed(win, sample_parquet_path, driver, boxes, monkeypatch):
    save_view("v1", sample_parquet_path, [["a"]])
    monkeypatch.setattr(win, "_view_compute_active", lambda: True)
    win._actions["export_parquet"].trigger()
    assert boxes.info == [tr("export_parquet.busy_view_load")] and driver.opened == 0
    assert not win.export_parquet_from_view(str(driver.out), "v1", ["a"])
    assert win._parquet_export is None


# -- the source file (57.7.2) ---------------------------------------------------------------------


def test_missing_source_file_is_red_and_disables_export(empty_win, tmp_path, driver):
    save_view("v1", tmp_path / "gone.parquet", [["a"]])
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    first = driver.seen["first"]
    assert first["error"] == tr("export_parquet.source_missing", path=tmp_path / "gone.parquet")
    assert not first["ok"] and first["items"] == []
    assert "d32f2f" in driver.dlg.error_label.styleSheet()
    assert "d32f2f" in driver.dlg.file_label.styleSheet()


def test_other_file_changes_the_usable_items(empty_win, sample_parquet_path, tmp_path, driver):
    other = tmp_path / "other.parquet"
    pq.write_table(
        pa.table({"time": pa.array(np.arange(100, dtype="int64")), "a": np.arange(100.0), "z": np.arange(100.0)}), other
    )
    save_view("v1", sample_parquet_path, [["a", "b"]], derived=[("d", "b + c")], time_column="time")
    driver.other_file = str(other)
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    first, other_seen = driver.seen["first"], driver.seen["other"]
    usable = {n: ok for n, ok, _c, _t in first["items"]}
    assert usable == {"a": True, "b": True, "d": True}
    after = {n: ok for n, ok, _c, _t in other_seen["items"]}
    assert after == {"a": True, "b": False, "d": False}  # b / c are not in the other file
    assert other_seen["file"] == str(other) and other_seen["all"] == tr("export_parquet.range_all", rows="100")
    assert other_seen["ok"]
    reasons = {n: t for n, _ok, _c, t in other_seen["items"]}
    assert tr("view.problem_missing_column", name="b") in reasons["b"]
    # going back to the view's own file: the picked file is forgotten
    QTest.mouseClick(driver.dlg.view_file_button, Qt.MouseButton.LeftButton)
    assert driver.dlg.source_file_override() is None
    assert driver.dlg.file_label.text() == str(sample_parquet_path)


def test_unusable_items_are_greyed_and_cannot_be_checked(empty_win, sample_parquet_path, driver):
    save_view("v1", sample_parquet_path, [["a", "ghost"]], derived=[("bad", "nofn(a)"), ("good", "a * 2")])
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    items = {n: (ok, checked, text) for n, ok, checked, text in driver.seen["first"]["items"]}
    assert items["a"][:2] == (True, True)
    assert items["ghost"][0] is False and not items["ghost"][1]
    assert items["bad"][0] is False and tr("error.unknown_function", name="nofn") in items["bad"][2]
    assert items["good"][:2] == (True, False)  # an unplotted derived variable: listed, not checked
    # "All" never checks an unusable row
    QTest.mouseClick(driver.dlg.select_all_button, Qt.MouseButton.LeftButton)
    assert sorted(driver.dlg.selected_names()) == ["a", "good"]


# -- export without opening the view (57.7.5) -------------------------------------------------------


def test_exports_a_view_with_no_file_open_and_never_opens_it(empty_win, sample_parquet_path, driver, monkeypatch):
    save_view(
        "v1", sample_parquet_path, [["a", "d1"]], derived=[("d1", "a * 2 + b"), ("unselected", "c * 5")]
    )
    monkeypatch.setattr(MainWindow, "load_view", lambda *a: pytest.fail("load_view must not be called"))
    monkeypatch.setattr(MainWindow, "load_parquet", lambda *a: pytest.fail("the file must not be opened"))
    monkeypatch.setattr(main_window_module, "ParquetDataSource", lambda *a, **k: pytest.fail("no data source"))
    driver.accept = True
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    assert driver.save_args[1].endswith(".parquet") and "sample_" in driver.save_args[1]
    table = pq.read_table(driver.out)
    assert table.column_names == ["time", "a", "d1"]  # the checked ones: plotted signals; "unselected" is not
    src = pq.read_table(sample_parquet_path).to_pandas() if False else pq.read_table(sample_parquet_path)
    a, b = src["a"].to_numpy(), src["b"].to_numpy()
    np.testing.assert_array_equal(table["d1"].to_numpy(), a * 2 + b)
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["view"] == "v1" and meta["view_file"].endswith("v1.json")
    assert empty_win._data_source is None and empty_win._derived == {} and empty_win._raw_variables == {}
    assert empty_win.settings.last_export_folder == str(driver.out.parent.resolve())


def test_other_derived_variables_can_be_checked(empty_win, sample_parquet_path, driver):
    save_view("v1", sample_parquet_path, [["a"]], derived=[("extra", "b - c")])
    driver.names = ["a", "extra"]
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    table = pq.read_table(driver.out)
    assert table.column_names == ["time", "a", "extra"]
    src = pq.read_table(sample_parquet_path)
    np.testing.assert_array_equal(table["extra"].to_numpy(), src["b"].to_numpy() - src["c"].to_numpy())


def test_saved_range_export_of_the_view(empty_win, sample_parquet_path, driver):
    lo, hi = epoch_of(sample_parquet_path, 1000), epoch_of(sample_parquet_path, 1999)
    save_view("v1", sample_parquet_path, [["a"]], derived=[("dd", "delta(a)")], x_range=(lo, hi))
    driver.names = ["a", "dd"]
    driver.saved_range = True
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    table = pq.read_table(driver.out)
    assert table.num_rows == 1000
    src = pq.read_table(sample_parquet_path)
    a = src["a"].to_numpy()
    np.testing.assert_array_equal(table["a"].to_numpy(), a[1000:2000])
    np.testing.assert_array_equal(table["dd"].to_numpy(), np.concatenate([[a[1000] - a[999]], np.diff(a)[1000:1999]]))


def test_saved_range_without_rows_is_reported_as_a_failure(empty_win, sample_parquet_path, driver, boxes):
    last = epoch_of(sample_parquet_path, 49_999)
    save_view("v1", sample_parquet_path, [["a"]], x_range=(last + 100, last + 200))
    driver.saved_range = True
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    assert boxes.warn == [tr("export_parquet.failed", error=tr("export_parquet.no_rows"))]
    assert not driver.out.exists() and not driver.out.with_name("out.parquet.part").exists()
    assert not empty_win._parquet_export_progress.isVisible()


def test_open_file_view_and_plots_stay_untouched(win, sample_parquet_path, driver, monkeypatch):
    win._on_add_derived_variable("mine", "a + 1")
    win._on_variable_dropped(win.plot_grid.plots[0], "mine")
    wait_until(lambda: not win._busy_sources)
    save_view("v1", sample_parquet_path, [["a", "dv"]], derived=[("dv", "b * 3")])
    data_source = win._data_source
    before = (
        dict(win._derived), set(win._raw_variables), win._time_column, win._view_name,
        [p.series_names() for p in win.plot_grid.plots],
    )
    monkeypatch.setattr(main_window_module, "ParquetDataSource", lambda *a, **k: pytest.fail("no new data source"))
    monkeypatch.setattr(MainWindow, "load_view", lambda *a: pytest.fail("load_view must not be called"))
    driver.pick = "v1"
    win._actions["export_parquet"].trigger()
    finish(win)
    after = (
        dict(win._derived), set(win._raw_variables), win._time_column, win._view_name,
        [p.series_names() for p in win.plot_grid.plots],
    )
    assert after == before and win._data_source is data_source
    assert "dv" not in win._derived and "dv" not in win._raw_variables
    assert pq.read_table(driver.out).column_names == ["time", "a", "dv"]


def test_output_equal_to_the_source_is_refused(empty_win, sample_parquet_path, driver, boxes):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.out = sample_parquet_path
    before = sample_parquet_path.stat().st_size
    empty_win._actions["export_parquet"].trigger()
    assert boxes.warn == [tr("export_parquet.same_as_source")]
    assert empty_win._parquet_export is None and sample_parquet_path.stat().st_size == before


def test_an_export_started_while_one_runs_joins_the_queue(win, sample_parquet_path, driver, boxes, monkeypatch):
    import threading

    win.settings.export_parallel_files = 1
    save_view("v1", sample_parquet_path, [["a", "dd"]], derived=[("dd", "a * 2")])
    entered, release = threading.Event(), threading.Event()
    real = pe.evaluate_expression

    def blocked(expr, variables, registry=None):
        entered.set()
        assert release.wait(30)
        return real(expr, variables, registry)

    monkeypatch.setattr(pe, "evaluate_expression", blocked)
    driver.pick = "v1"
    win._actions["export_parquet"].trigger()
    assert wait_until(entered.is_set, 30)
    try:
        two = driver.out.with_name("two.parquet")
        assert win.export_parquet_from_view(str(two), "v1", ["a"])  # queued, not refused
        assert boxes.info == []
        assert [i.state.value for i in win._export.queue.items] == ["running", "waiting"]
    finally:
        release.set()
    assert wait_until(lambda: win._export.queue.is_idle(), 30)
    assert driver.out.exists() and two.exists()


def test_view_export_with_aliases_names_and_metadata(empty_win, tmp_path, driver):
    path = tmp_path / "vehicle.parquet"
    pq.write_table(
        pa.table({"ts": pa.array(np.arange(500, dtype="int64")), "Vehicle_Speed": np.arange(500.0) * 0.1}), path
    )
    empty_win.save_signal_aliases({"speed": ["Vehicle_Speed"], "T": ["ts"]})
    save_view("v1", path, [["speed", "twice_speed"]], derived=[("twice_speed", "speed * 2")], time_column="T")
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    table = pq.read_table(driver.out)
    assert table.column_names == ["T", "speed", "twice_speed"]
    np.testing.assert_array_equal(table["speed"].to_numpy(), np.arange(500.0) * 0.1)
    np.testing.assert_array_equal(table["twice_speed"].to_numpy(), np.arange(500.0) * 0.1 * 2)
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["aliases"] == {"T": "ts", "speed": "Vehicle_Speed"}


def test_alias_global_switch_off_is_respected(empty_win, tmp_path, driver):
    path = tmp_path / "vehicle.parquet"
    pq.write_table(pa.table({"ts": pa.array(np.arange(50, dtype="int64")), "Vehicle_Speed": np.arange(50.0)}), path)
    empty_win.save_signal_aliases({"speed": ["Vehicle_Speed"], "T": ["ts"]})
    empty_win.set_signal_aliases_enabled(False)
    save_view("v1", path, [["speed"]], time_column="T")
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    assert not driver.seen["first"]["ok"]
    assert driver.seen["first"]["error"] == tr("export_parquet.time_missing", name="T")


def test_op_log_records_the_view_and_the_source(empty_win, sample_parquet_path, driver, log):
    save_view("v1", sample_parquet_path, [["a"]])
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    (record,) = op_records("export_parquet")
    args = record["details"]
    assert args["from_view"] == "v1" and args["source"] == str(sample_parquet_path)
    assert args["path"] == str(driver.out)


def test_cancel_works_like_the_current_screen_export(empty_win, sample_parquet_path, driver, monkeypatch):
    import threading

    save_view("v1", sample_parquet_path, [["a", "dd"]], derived=[("dd", "a * 2")])
    entered, release = threading.Event(), threading.Event()
    real = pe.evaluate_expression

    def blocked(expr, variables, registry=None):
        entered.set()
        assert release.wait(30)
        return real(expr, variables, registry)

    monkeypatch.setattr(pe, "evaluate_expression", blocked)
    empty_win._actions["export_parquet"].trigger()
    assert wait_until(entered.is_set, 30)
    job = empty_win._parquet_export
    QTest.mouseClick(empty_win._parquet_export_progress.cancel_button, Qt.MouseButton.LeftButton)
    release.set()
    assert wait_until(job.worker_done.is_set, 30)
    QApplication.processEvents()
    assert not driver.out.exists() and not driver.out.with_name("out.parquet.part").exists()


# -- one rule for load_view, the view manager's marks and the export list (57.7.3) -----------------------

DERIVED_CASES = [
    ("ok1", "a * 2"),
    ("ok2", "ok1 + b"),
    ("unknown_fn", "nofn(a)"),
    ("arity", "abs(a, b)"),
    ("syntax", "a +"),
    ("missing", "zz + 1"),
    ("child_of_bad", "unknown_fn * 2"),
    ("c", "a + 1"),  # named like a column
    ("spd", "a * 3"),  # named like a signal alias of the dictionary
    ("chain_ok", "ok2 - 1"),
]


def test_three_call_sites_agree_on_what_is_unusable(win, sample_parquet_path, monkeypatch):
    win._aliases = {"spd": ["not_in_the_file"]}  # an alias of the dictionary that attaches to nothing here
    save_view("v1", sample_parquet_path, [["a"]], derived=DERIVED_CASES)
    view = view_io.load_view("v1")
    cols = list(pq.read_schema(sample_parquet_path).names)

    # (a) the export list
    info = pe.view_export_info(view, cols, {}, win._registry_snapshot(), alias_names=set(win._aliases))
    from_export = {i.name for i in info.items if i.kind == "derived" and not i.usable}

    # (b) the view manager's "missing" marks
    excluded = win._time_excluded_names()
    dialog = ViewManagerDialog(
        {c for c in win._file_columns if c not in excluded},
        None,
        all_columns=set(win._file_columns),
        available_functions=win._function_registry.all_names(),
        registry=win._function_registry,
        aliases=win._alias_sources,
        alias_names=set(win._aliases),
    )
    _series, from_manager = dialog._missing(view)

    # (c) what load_view skips
    win.load_view("v1")
    wait_until(lambda: not win._busy_sources and not win._view_compute_active())
    from_load = {n for n, _e in DERIVED_CASES} - set(win._derived)

    expected = {"unknown_fn", "arity", "syntax", "missing", "child_of_bad", "c", "spd"}
    assert from_export == from_manager == from_load == expected
    # and the shared function itself
    problems = classify_derived(
        dict(DERIVED_CASES), {"time", "a", "b", "c"} - {"time"}, alias_names={"spd"}, registry=win._function_registry
    )
    assert {n for n, p in problems.items() if p is not None} == expected


# -- review fixes ---------------------------------------------------------------------------------------


def test_toolbar_action_passes_no_view_name(win, sample_parquet_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(MainWindow, "export_parquet_dialog", lambda self, *a, **k: seen.append((a, k)))
    win._actions["export_parquet"].trigger()
    assert seen == [((), {})]  # not checked=False as a view name


def test_no_rows_is_an_outcome_not_a_logged_error(empty_win, sample_parquet_path, driver, boxes, caplog):
    last = epoch_of(sample_parquet_path, 49_999)
    save_view("v1", sample_parquet_path, [["a"]], x_range=(last + 100, last + 200))
    driver.saved_range = True
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    assert boxes.warn == [tr("export_parquet.failed", error=tr("export_parquet.no_rows"))]
    assert not [r for r in caplog.records if r.levelname == "ERROR"]  # no traceback for an expected outcome


def test_range_search_phase_has_its_own_label(qapp):
    from parquet_analyzer.ui.parquet_export_dialog import ParquetExportProgress

    widget = ParquetExportProgress(lambda: None)
    widget.set_progress(0, pe.PROGRESS_RANGE)
    assert widget.label.text() == tr("export_parquet.progress_range")
    widget.set_progress(0, pe.PROGRESS_FULL)
    assert widget.label.text() == tr("export_parquet.progress_full")
    assert tr("export_parquet.progress_range") != tr("export_parquet.progress_full")


def test_dialog_range_text_and_metadata_range_use_the_same_form(empty_win, tmp_path, driver):
    path = tmp_path / "tz.parquet"
    t = np.datetime64("2026-03-01T00:00:00", "us") + np.arange(100, dtype="int64") * np.timedelta64(1_000_000, "us")
    pq.write_table(
        pa.table({"time": pa.array(t, type=pa.timestamp("us", tz="Asia/Tokyo")), "a": np.arange(100.0)}), path
    )
    lo, hi = epoch_of(path, 10), epoch_of(path, 20)
    save_view("v1", path, [["a"]], x_range=(lo, hi))
    driver.saved_range = True
    empty_win._actions["export_parquet"].trigger()
    finish(empty_win)
    shown = driver.seen["first"]["saved"]
    meta = json.loads(pq.read_table(driver.out).schema.metadata[pe.METADATA_KEY])
    assert f"{meta['range']['start']} - {meta['range']['end']}" in shown
    assert "+0900" in shown


def test_the_op_log_is_written_before_the_footer_is_read(empty_win, sample_parquet_path, driver, log, monkeypatch):
    save_view("v1", sample_parquet_path, [["a"]])
    logged_when_read: list[int] = []
    real = main_window_module.pq.read_schema

    def spy(*a, **k):
        logged_when_read.append(len(op_records("export_parquet")))
        return real(*a, **k)

    monkeypatch.setattr(main_window_module.pq, "read_schema", spy)
    empty_win.export_parquet_from_view(str(driver.out), "v1", ["a"])
    finish(empty_win)
    assert logged_when_read and logged_when_read[0] == 1
    assert len(op_records("export_parquet")) == 1  # one record, with the source in it
    assert op_records("export_parquet")[0]["details"]["source"] == str(sample_parquet_path)


def test_derived_named_like_the_time_column_is_greyed_in_the_dialog(empty_win, sample_parquet_path, driver):
    save_view("v1", sample_parquet_path, [["a", "time"]], derived=[("time", "a * 2")])
    driver.accept = False
    empty_win._actions["export_parquet"].trigger()
    items = {n: (ok, text) for n, ok, _c, text in driver.seen["first"]["items"]}
    assert items["time"][0] is False and tr("view.problem_time_name") in items["time"][1]
    assert items["a"][0] is True


# -- overwrite protection (export 57.8.1) ------------------------------------------------------------


@pytest.fixture
def choice(monkeypatch):
    from parquet_analyzer.ui import messages

    class Choice:
        answer = None

    c = Choice()
    c.asked = []

    def fake(parent, title, text, *texts):
        c.asked.append(text)
        return c.answer

    monkeypatch.setattr(messages, "choose_export_overwrite", fake)
    return c


def test_view_export_to_an_existing_file_asks(win, sample_parquet_path, driver, choice):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.pick = "v1"
    driver.out.write_bytes(b"precious")
    choice.answer = None
    win._actions["export_parquet"].trigger()
    assert len(choice.asked) == 1 and win._parquet_export is None
    assert driver.out.read_bytes() == b"precious"


def test_view_export_without_extension_asks_and_timestamp_keeps_the_original(
    win, sample_parquet_path, driver, choice, tmp_path, log
):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.pick = "v1"
    driver.out = tmp_path / "noext"
    (tmp_path / "noext.parquet").write_bytes(b"precious")
    choice.answer = "timestamp"
    win._actions["export_parquet"].trigger()
    finish(win)
    assert len(choice.asked) == 1
    assert (tmp_path / "noext.parquet").read_bytes() == b"precious"
    assert len(list(tmp_path.glob("noext_*.parquet"))) == 1
    assert op_records("export_parquet")[-1]["details"]["renamed"] is True


def test_view_export_overwrite_replaces(win, sample_parquet_path, driver, choice):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.pick = "v1"
    driver.out.write_bytes(b"precious")
    choice.answer = "overwrite"
    win._actions["export_parquet"].trigger()
    finish(win)
    assert pq.read_table(driver.out).num_rows == 50_000


def test_view_export_source_file_is_refused_without_the_question(win, sample_parquet_path, driver, choice, boxes):
    save_view("v1", sample_parquet_path, [["a"]])
    driver.pick = "v1"
    driver.out = sample_parquet_path
    win._actions["export_parquet"].trigger()
    assert choice.asked == [] and boxes.warn == [tr("export_parquet.same_as_source")]


def test_view_export_direct_call_to_an_existing_file_is_refused(win, sample_parquet_path, boxes, tmp_path):
    save_view("v1", sample_parquet_path, [["a"]])
    out = tmp_path / "x.parquet"
    out.write_bytes(b"precious")
    assert win.export_parquet_from_view(str(out), "v1", ["a"])  # starts; the core refuses
    finish(win)
    assert out.read_bytes() == b"precious" and boxes.warn
