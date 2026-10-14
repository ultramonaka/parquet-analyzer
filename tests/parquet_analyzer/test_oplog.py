"""Always-on operation log with results (detailed_specification.md 30, specification 5.23)."""

from __future__ import annotations

import io
import json
import logging
import statistics
import threading
import time
import weakref
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from parquet_analyzer import config
from parquet_analyzer.core import oplog, oplog_view
from parquet_analyzer.core.background_worker import WorkerHub, _Task
from parquet_analyzer.io import view as view_io
from parquet_analyzer.io.settings import (
    DEFAULT_LOG_RETENTION_DAYS,
    Settings,
    load_settings,
    normalize_log_retention_days,
    save_settings,
)
from parquet_analyzer.io.view import PlotDef, SeriesDef, SourceDef, View
from parquet_analyzer.ui import messages
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.settings_dialog import SettingsDialog


@pytest.fixture
def log(qapp, isolated_config):
    """Recording on, into the tmp log dir; fully reset afterwards (no leaked state/hooks)."""
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


@pytest.fixture
def win(log, monkeypatch, sample_parquet_path):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    w = MainWindow()
    settle()  # let the constructor's own ops finish: a real file open is a separate event-loop turn
    w.load_parquet(str(sample_parquet_path))
    settle()
    yield w
    w.close()


def settle(timeout: float = 5.0) -> None:
    """Run the event loop until the pending result has been written."""
    deadline = time.monotonic() + timeout
    while oplog.has_pending() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    oplog.flush()


def records(kind: str | None = None) -> list[dict]:
    oplog.flush()
    path = oplog.log_path_for(date.today())
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in out if kind is None or r["kind"] == kind]


def result_for(op_seq: int) -> dict:
    found = [r for r in records("result") if r["op"] == op_seq]
    assert len(found) == 1, f"expected exactly one result for op {op_seq}, got {len(found)}"
    return found[0]


def last_op(action: str) -> dict:
    return [r for r in records("op") if r["action"] == action][-1]


# -- format ----------------------------------------------------------------------------


def test_every_kind_is_json_with_common_fields_and_seq_increases(win):
    plot = win.plot_grid.plots[0]
    oplog.log_op("overlay_variable", plot=plot.plot_id, variable="a")
    win._add_variable_to_plot(plot, "a")
    messages.status_notice("hello")
    oplog.record_job("stats", "start", target="a")
    settle()
    recs = records()
    kinds = {r["kind"] for r in recs}
    assert {"session", "op", "result", "message", "job"} <= kinds
    seqs = [r["seq"] for r in recs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    for r in recs:
        assert {"ts", "seq", "kind", "op", "session"} <= r.keys()
        assert len(r["ts"]) >= 23  # ISO 8601 with milliseconds
    op = last_op("overlay_variable")
    assert op["op"] == op["seq"]  # an operation is its own number
    assert result_for(op["seq"])["op"] == op["seq"]
    session = records("session")[0]
    assert session["event"] == "start" and "features" in session and session["debug"] is False


def test_lists_are_capped_with_a_remainder_marker(log):
    oplog.log_op("many", names=[f"v{i}" for i in range(80)])
    rec = last_op("many")
    assert len(rec["details"]["names"]) == 51
    assert rec["details"]["names"][-1] == "...(+30)"


def test_nothing_is_written_until_configured(qapp, isolated_config):
    oplog.reset()
    oplog.log_op("noop")
    assert not config.PARQUET_ANALYZER_LOG_DIR.exists() or not list(config.PARQUET_ANALYZER_LOG_DIR.glob("*ops*"))


def test_works_without_debug_mode_and_adds_no_console_handler(log):
    oplog.log_op("something")
    assert oplog.log_path_for(date.today()).exists()
    assert logging.getLogger("parquet_analyzer.ops").handlers == []


# -- results ---------------------------------------------------------------------------


def test_open_file_result_has_file_and_plot_changes(win, sample_parquet_path):
    op = last_op("load_parquet")
    res = result_for(op["seq"])
    assert res["settled"] is True
    assert res["state"]["file"]["time_column"] == "time"
    assert res["state"]["file"]["rows"] == 50_000
    assert res["changes"]["file.path"][0] is None
    assert str(sample_parquet_path) in res["changes"]["file.path"][1]
    assert res["state"]["anchor"] == win.plot_grid.plots[0].plot_id


def test_add_series_zoom_delete_series_each_get_one_result_with_changes(win):
    plot = win.plot_grid.plots[0]
    pid = plot.plot_id

    win._on_variable_dropped(plot, "a")  # logs overlay_variable
    settle()
    add = result_for(last_op("overlay_variable")["seq"])
    assert add["changes"][f"plots.{pid}.series"] == [[], ["a"]]
    assert add["state"]["plots"][pid]["series_info"]["a"]["points"] > 0
    assert f"plots.{pid}.x_range" in add["changes"]  # first series sets the extent
    # the range_changed caused by adding the series is a child of the op, not a new op
    children = [r for r in records("op") if r.get("parent") == add["op"]]
    assert children and all(c["action"] == "range_changed" for c in children)

    lo, hi = plot.getViewBox().viewRange()[0]
    oplog.log_op("zoom_test")
    plot.setXRange(lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.5, padding=0)
    settle()
    zoom = result_for(last_op("zoom_test")["seq"])
    assert f"plots.{pid}.x_range" in zoom["changes"]
    assert zoom["changes"][f"plots.{pid}.x_range_text"][0] != zoom["changes"][f"plots.{pid}.x_range_text"][1]

    # delete the series; the navigator range jumping at the same time must show up as a
    # navigator.region diff (the chapter-23 bug class this log exists to expose)
    oplog.log_op("delete_series", plot=pid, name="a")
    plot.remove_series("a")
    win.navigator.set_selected_range(lo - 5.0, hi + 5.0)
    settle()
    dele = result_for(last_op("delete_series")["seq"])
    assert dele["changes"][f"plots.{pid}.series"] == [["a"], []]
    assert dele["changes"]["navigator.region"][1] == [pytest.approx(lo - 5.0), pytest.approx(hi + 5.0)]
    assert len([r for r in records("result") if r["op"] == dele["op"]]) == 1


def test_unchanged_floats_within_relative_1e9_are_not_changes():
    assert oplog.diff_states({"x": [1.0, 2.0]}, {"x": [1.0 + 1e-12, 2.0]}) == {}
    assert oplog.diff_states({"x": 1.0}, {"x": 1.0 + 1e-6}) == {"x": [1.0, 1.0 + 1e-6]}
    assert oplog.diff_states({"p": {"a": 1}}, {"p": {"a": 1, "b": 2}}) == {"p.b": [None, 2]}


def test_rapid_zoom_events_coalesce_into_one_result(win):
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    settle()
    before = len(records("result"))
    lo, hi = plot.getViewBox().viewRange()[0]
    span = hi - lo
    for i in range(6):
        plot.setXRange(lo + span * 0.05 * i, hi, padding=0)  # real range_changed ops
        QApplication.processEvents()
    settle()
    new_results = records("result")[before:]
    assert len(new_results) == 1
    assert new_results[0]["merged"] >= 2 and new_results[0]["settled"] is True
    assert new_results[0]["op"] == records("op")[-1]["seq"]


def test_new_op_flushes_the_previous_pending_result_first(log):
    oplog.set_state_provider(lambda: {"v": 1})
    oplog.log_op("first")
    first = last_op("first")
    QApplication.processEvents()  # one event-loop turn: "second" is a new operation, not a child
    oplog.set_state_provider(lambda: {"v": 2})
    oplog.log_op("second")
    recs = records()
    i_result = next(i for i, r in enumerate(recs) if r["kind"] == "result" and r["op"] == first["seq"])
    i_second = next(i for i, r in enumerate(recs) if r["kind"] == "op" and r["action"] == "second")
    assert i_result < i_second
    assert recs[i_result]["state"] == {"v": 2}  # state at the time of the flush


def test_pending_result_waits_for_busy_then_reports_unsettled_on_timeout(log, monkeypatch):
    oplog.set_state_provider(lambda: {}, lambda: True)  # always busy
    monkeypatch.setattr(oplog, "SETTLE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(oplog, "QUIET_NORMAL_S", 0.01)
    oplog.log_op("stuck")
    settle()
    res = records("result")[-1]
    assert res["settled"] is False


def test_load_view_is_one_operation_with_children_and_one_result(win, sample_parquet_path):
    view_io.save_view(
        View(
            view_name="v1",
            source=SourceDef(str(sample_parquet_path), "absolute"),
            plots=[PlotDef("p1", [SeriesDef("a", "#ff0000")]), PlotDef("p2", [SeriesDef("b", "#00ff00")])],
        )
    )
    n_ops = len(records("op"))
    win.load_view("v1")
    settle()
    new_ops = records("op")[n_ops:]
    top = [r for r in new_ops if r.get("parent") is None]
    assert [r["action"] for r in top] == ["load_view"]
    assert any(r["action"] == "add_plot" and r["parent"] == top[0]["seq"] for r in new_ops)
    res = result_for(top[0]["seq"])
    assert len(res["state"]["plots"]) == 2
    assert any(k.endswith(".series") for k in res["changes"])


# -- jobs ------------------------------------------------------------------------------


def _run_task(func, job, target, on_failed=None):
    """Run a hub task's body synchronously on this thread (recording happens in __call__)."""
    hub = WorkerHub()
    hub.pending[1] = (lambda r: None, (on_failed.append if on_failed is not None else (lambda m: None)))
    _Task(func, weakref.ref(hub), 1, job, target)()
    QApplication.processEvents()  # the relay's queued delivery


def test_failing_job_is_recorded_with_the_triggering_op_number(log):
    oplog.log_op("zoom_that_fails")
    op_seq = last_op("zoom_that_fails")["seq"]

    def boom():
        raise RuntimeError("disk gone")

    failed = []
    _run_task(boom, "downsample", "p1/a", on_failed=failed)  # no re-raise: reported as a failure
    assert len(failed) == 1 and "disk gone" in failed[0]
    assert not records("exception")
    jobs = records("job")
    assert len(jobs) == 1  # downsample: failures only (no start/finish record)
    assert jobs[0]["event"] == "fail" and jobs[0]["op"] == op_seq
    assert jobs[0]["job"] == "downsample" and jobs[0]["target"] == "p1/a"
    assert "disk gone" in jobs[0]["error"] and "ms" in jobs[0]


def test_non_downsample_jobs_record_start_and_finish_but_downsample_only_in_debug(log):
    oplog.log_op("stats")
    _run_task(lambda: 1, "stats", "a")
    _run_task(lambda: 1, "downsample", "p1/a")
    events = [(r["job"], r["event"]) for r in records("job")]
    assert events == [("stats", "start"), ("stats", "finish")]
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=True)
    _run_task(lambda: 1, "downsample", "p1/a")
    assert [(r["job"], r["event"]) for r in records("job")][-2:] == [("downsample", "start"), ("downsample", "finish")]


def test_worker_still_emits_finished_with_the_result_and_runs_on_the_pool(qapp, log):
    got = []
    hub = WorkerHub()
    hub.submit(lambda: 42, got.append, lambda m: None, job="stats", target="x")
    deadline = time.monotonic() + 5
    while not got and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert got == [42]
    assert [r["event"] for r in records("job")] == ["start", "finish"]


# -- messages --------------------------------------------------------------------------


def test_warning_dialog_and_status_notice_are_recorded_as_messages(win, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    messages.warning(win, "Title", "something odd")
    messages.status_notice("Saved x")
    assert shown == ["something odd"]  # behaviour unchanged: the dialog call still happens
    msgs = records("message")
    assert (msgs[-2]["level"], msgs[-2]["where"], msgs[-2]["text"], msgs[-2]["title"]) == (
        "warning", "dialog", "something odd", "Title")
    assert (msgs[-1]["where"], msgs[-1]["text"]) == ("status", "Saved x")


def test_load_error_dialog_is_recorded_under_its_operation(win, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    assert win.load_parquet(str(tmp_path / "missing.parquet")) is False
    settle()
    op = last_op("load_parquet")
    err = [m for m in records("message") if m["level"] == "error"][-1]
    assert err["op"] == op["seq"] and err["where"] == "dialog"


# -- exceptions ------------------------------------------------------------------------


class _Emitter(QObject):
    fired = Signal()


def test_uncaught_slot_exception_is_recorded_and_app_keeps_running(win, capsys):
    oplog.install_excepthooks()
    emitter = _Emitter()

    def bad_slot():
        raise ValueError("slot exploded")

    emitter.fired.connect(bad_slot)
    emitter.fired.emit()
    QApplication.processEvents()
    exc = records("exception")
    assert exc and exc[-1]["type"] == "ValueError" and "slot exploded" in exc[-1]["message"]
    assert "bad_slot" in exc[-1]["traceback"]
    # still alive: further operations keep being recorded and the window works
    oplog.log_op("after_exception")
    assert win.state_snapshot()["file"]["rows"] == 50_000
    assert last_op("after_exception")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_exceptions_are_recorded(log):
    oplog.install_excepthooks()
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(KeyError("in thread")))
    t.start()
    t.join()
    assert any(r["source"] == "thread" and r["type"] == "KeyError" for r in records("exception"))


def test_hooks_are_restored_on_reset(qapp, isolated_config):
    import sys

    before = sys.excepthook
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR)
    oplog.install_excepthooks()
    assert sys.excepthook is not before
    oplog.reset()
    assert sys.excepthook is before


# -- retention / robustness ------------------------------------------------------------


def _touch(dirpath, name):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text("x")


def test_retention_deletes_old_main_and_ops_logs_only(tmp_path):
    d = tmp_path / "log"
    today = date.today()
    fmt = lambda days: (today - timedelta(days=days)).strftime("%Y%m%d")  # noqa: E731
    for name in (
        f"parquet_analyzer_{fmt(30)}.log", f"parquet_analyzer_ops_{fmt(30)}.log",  # old: deleted
        f"parquet_analyzer_{fmt(3)}.log", f"parquet_analyzer_ops_{fmt(3)}.log",  # in range: kept
        f"parquet_analyzer_{fmt(0)}.log", f"parquet_analyzer_ops_{fmt(0)}.log",  # today: kept
        "notes_20200101.log", "parquet_analyzer_20200101.txt", "other.log",  # other patterns: untouched
    ):
        _touch(d, name)
    removed = oplog.purge_old_logs(d, 7)
    assert sorted(p.name for p in removed) == sorted(
        [f"parquet_analyzer_{fmt(30)}.log", f"parquet_analyzer_ops_{fmt(30)}.log"])
    left = {p.name for p in d.iterdir()}
    assert {"notes_20200101.log", "parquet_analyzer_20200101.txt", "other.log"} <= left
    assert f"parquet_analyzer_{fmt(0)}.log" in left and f"parquet_analyzer_ops_{fmt(3)}.log" in left


def test_today_is_never_deleted_even_with_a_tiny_retention(tmp_path):
    _touch(tmp_path, f"parquet_analyzer_{date.today():%Y%m%d}.log")
    assert oplog.purge_old_logs(tmp_path, 1) == []


def test_configure_runs_retention_and_writes_session_start(qapp, isolated_config):
    d = config.PARQUET_ANALYZER_LOG_DIR
    old = f"parquet_analyzer_ops_{(date.today() - timedelta(days=20)):%Y%m%d}.log"
    _touch(d, old)
    oplog.configure(d, retention_days=7)
    try:
        assert not (d / old).exists()
        assert records("session")[0]["event"] == "start"
    finally:
        oplog.reset()


def test_retention_setting_default_roundtrip_and_validation(isolated_config):
    assert Settings().log_retention_days == DEFAULT_LOG_RETENTION_DAYS == 7
    s = Settings(log_retention_days=30)
    save_settings(s)
    assert load_settings().log_retention_days == 30
    for bad in (0, 366, -3, "9", 2.5, None, True):
        assert normalize_log_retention_days(bad) == 7
    assert normalize_log_retention_days(1) == 1 and normalize_log_retention_days(365) == 365
    save_settings(Settings(log_retention_days=999))
    assert load_settings().log_retention_days == 7


def test_settings_dialog_has_retention_spin_and_open_log_folder(qapp, isolated_config, monkeypatch):
    opened = []
    from parquet_analyzer.ui import settings_dialog

    monkeypatch.setattr(settings_dialog.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url) or True))
    dlg = SettingsDialog(Settings(log_retention_days=12))
    assert (dlg.log_retention_spin.minimum(), dlg.log_retention_spin.maximum()) == (1, 365)
    assert dlg.log_retention_spin.value() == 12
    dlg.log_retention_spin.setValue(30)
    assert dlg.result_settings().log_retention_days == 30
    dlg.open_log_folder_button.click()
    assert len(opened) == 1 and Path(opened[0].toLocalFile()) == config.PARQUET_ANALYZER_LOG_DIR


def test_write_failure_never_raises_and_warns_once(qapp, isolated_config, tmp_path, caplog):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file in the way")
    oplog.reset()
    with caplog.at_level(logging.WARNING, logger="parquet_analyzer"):
        oplog.configure(blocker / "sub")  # cannot be created/opened
        for i in range(5):
            oplog.log_op("still_fine", i=i)
        oplog.record_message("info", "status", "x")
        oplog.flush_pending()
    oplog.reset()
    warnings = [r for r in caplog.records if "operation log write failed" in r.getMessage()]
    assert len(warnings) == 1


def test_provider_exception_does_not_break_logging(log):
    def bad():
        raise RuntimeError("no state")

    oplog.set_state_provider(bad)
    oplog.log_op("x")
    oplog.flush_pending()
    assert "snapshot_error" in records("result")[-1]["state"]


# -- viewer ----------------------------------------------------------------------------


def test_viewer_prints_timeline_with_changes_jobs_messages_and_exceptions(win):
    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")
    settle()
    messages.status_notice("note")
    oplog.record_exception(ValueError, ValueError("oops"), None)
    path = oplog.log_path_for(date.today())
    out = io.StringIO()
    assert oplog_view.main([str(path)], out=out) == 0
    text = out.getvalue()
    lines = text.splitlines()
    assert any(ln.startswith("#") and "load_parquet" in ln for ln in lines)
    i_op = next(i for i, ln in enumerate(lines) if "overlay_variable" in ln and ln.startswith("#"))
    # the op's consequences (child ops) and its result are indented under it
    i_res = next(i for i in range(i_op + 1, len(lines)) if lines[i].startswith("    -> result"))
    assert all(ln.startswith("    ") for ln in lines[i_op + 1 : i_res])
    assert any(ln.startswith("        plots.") and ".series" in ln for ln in lines[i_res:])
    assert "message [info/status] note" in text and "EXCEPTION ValueError: oops" in text
    assert text.splitlines()[0].startswith("=== session start")

    out = io.StringIO()
    oplog_view.main([str(path), "--last", "1"], out=out)
    last_ops = [ln for ln in out.getvalue().splitlines() if ln.startswith("#")]
    assert len(last_ops) == 1
    out = io.StringIO()
    oplog_view.main([str(path), "--session"], out=out)
    assert "load_parquet" in out.getvalue()


def test_viewer_skips_garbage_lines_and_reports_missing_file(tmp_path, capsys):
    f = tmp_path / "x.log"
    f.write_text('not json\n{"kind":"op","seq":1,"op":1,"ts":"2026-01-01T09:00:00.000","action":"a","details":{}}\n')
    out = io.StringIO()
    assert oplog_view.main([str(f)], out=out) == 0
    assert "a" in out.getvalue()
    assert oplog_view.main([str(tmp_path / "nope.log")]) == 1


# -- performance -----------------------------------------------------------------------


def test_state_snapshot_is_fast_for_4_plots_x_3_series(win):
    n = 20_000
    x = np.arange(n, dtype=float)
    while len(win.plot_grid.plots) < 4:
        win.plot_grid.add_plot()
    for plot in win.plot_grid.plots:
        for name in ("s1", "s2", "s3"):
            plot.add_series(name, x, np.sin(x / 100))
    win.state_snapshot()  # warm up
    times = []
    for _ in range(30):
        t0 = time.perf_counter()
        snap = win.state_snapshot()
        times.append((time.perf_counter() - t0) * 1000)
    assert len(snap["plots"]) == 4 and all(len(p["series"]) == 3 for p in snap["plots"].values())
    json.dumps(oplog._sanitize(snap))  # serializable
    assert statistics.median(times) < 5.0


# -- review fixes ------------------------------------------------------------------------


def _turn():
    """One event-loop turn: afterwards a new log_op is a new operation, not a child."""
    QApplication.processEvents()


def _assert_children_point_at_ops(first_new_seq=0):
    recs = records()
    op_seqs = {r["seq"] for r in recs if r["kind"] == "op"}
    kinds = {r["seq"]: r["kind"] for r in recs}
    children = [r for r in recs if r["kind"] == "op" and r.get("parent") is not None and r["seq"] > first_new_seq]
    assert children
    for c in children:
        assert c["parent"] in op_seqs and kinds[c["parent"]] == "op", c
        assert c["parent"] < c["seq"]
    return children


def test_load_view_and_open_dialog_children_point_at_op_records_not_results(win, sample_parquet_path, monkeypatch):
    view_io.save_view(
        View(
            view_name="v2",
            source=SourceDef(str(sample_parquet_path), "absolute"),
            plots=[PlotDef("p1", [SeriesDef("a", "#ff0000")]), PlotDef("p2", [SeriesDef("b", "#00ff00")])],
        )
    )
    oplog.log_op("pending_before")
    _turn()  # pending op that the next top-level op will flush -> a result record is written first
    n = oplog._S.seq
    win.load_view("v2")
    _assert_children_point_at_ops(n)
    settle()

    from parquet_analyzer.ui import main_window as mw

    monkeypatch.setattr(mw, "pick_parquet_file", lambda *a, **k: str(sample_parquet_path))
    oplog.log_op("pending_before2")
    _turn()
    n = oplog._S.seq
    win.open_parquet_dialog()
    kids = _assert_children_point_at_ops(n)
    dialog_op = last_op("open_parquet_dialog")
    assert dialog_op.get("parent") is None
    assert any(k["action"] == "load_parquet" and k["parent"] == dialog_op["seq"] for k in kids)


def test_result_cut_short_by_next_op_reports_busy_and_interrupted_by(log):
    busy = {"v": True}
    oplog.set_state_provider(lambda: {}, lambda: busy["v"])
    oplog.log_op("overlay_a")
    _turn()
    oplog.log_op("overlay_b")  # arrives while a's drawing is still in flight
    nxt = last_op("overlay_b")["seq"]
    res = result_for(last_op("overlay_a")["seq"])
    assert res["settled"] is False and res["interrupted_by"] == nxt
    busy["v"] = False
    _turn()
    oplog.log_op("overlay_c")
    res_b = result_for(nxt)
    assert res_b["settled"] is True and res_b["interrupted_by"] == last_op("overlay_c")["seq"]
    out = io.StringIO()
    oplog_view.main([str(oplog.log_path_for(date.today()))], out=out)
    assert "cut short by #" in out.getvalue() and "NOT settled" in out.getvalue()


def test_readable_times_are_local_and_ts_is_timezone_aware(log):
    from datetime import datetime

    x = 1767225600.0
    assert oplog.format_time(x) == datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    assert oplog.format_time(float("nan")) == "nan"
    oplog.log_op("x")
    for r in records():
        assert datetime.fromisoformat(r["ts"]).utcoffset() is not None
    sess = records("session")[0]
    assert sess["utc_offset"] == datetime.now().astimezone().strftime("%z")


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def test_fft_and_stats_workers_record_their_job_kind_and_target(win):
    from PySide6.QtCore import Qt

    win.variable_panel.clearSelection()
    win.variable_panel.findItems("a", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    win._on_stats_requested()
    _wait(lambda: not win._stats_workers)
    jobs = [(r["job"], r["event"], r.get("target")) for r in records("job") if r["job"] != "downsample"]
    assert ("stats", "start", "a") in jobs and ("stats", "finish", "a") in jobs
    assert not any(j[0] == "task" for j in jobs)

    win._add_variable_to_plot(win.plot_grid.plots[0], "a")  # sets a sane visible range
    win.variable_panel.findItems("a", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    win._on_fft_requested()  # FFT UI is flag-gated, but the slot still works
    _wait(lambda: not win._fft_workers)
    assert any(r["job"] == "fft" and r.get("target") == "a" for r in records("job"))


def test_last_n_cuts_by_position_in_a_multi_session_file(tmp_path):
    lines = []
    for sid in ("s1", "s2"):  # seq restarts at 1 in every session
        lines.append({"kind": "session", "seq": 1, "op": 0, "session": sid, "event": "start", "ts": "2026-01-01T00:00:00.000"})
        for seq, act in ((2, f"{sid}_a"), (3, f"{sid}_b")):
            lines.append({"kind": "op", "seq": seq, "op": seq, "session": sid, "action": act, "details": {}, "ts": "2026-01-01T00:00:00.000"})
    f = tmp_path / "m.log"
    f.write_text("\n".join(json.dumps(r) for r in lines))
    out = io.StringIO()
    oplog_view.main([str(f), "--last", "3"], out=out)
    ops = [ln.split()[2] for ln in out.getvalue().splitlines() if ln.startswith("#")]
    assert ops == ["s1_b", "s2_a", "s2_b"]


def test_op_logged_in_the_same_turn_is_a_child_of_any_kind(win):
    plot = win.plot_grid.plots[0]
    oplog.log_op("remove_plot_requested", plot=plot.plot_id)
    oplog.log_op("remove_plot", plot=plot.plot_id, total=1)  # same handler, no loop turn
    child = last_op("remove_plot")
    assert child["parent"] == last_op("remove_plot_requested")["seq"]
    settle()
    assert len([r for r in records("result") if r["action"] == "remove_plot_requested"]) == 1
    assert not [r for r in records("result") if r["action"] == "remove_plot"]


def test_user_zoom_during_a_pending_op_is_not_swallowed(log):
    oplog.set_state_provider(lambda: {}, lambda: True)  # keeps "add" pending
    oplog.log_op("overlay_variable")
    _turn()
    oplog.log_op("range_changed", plot="p1", x_min=0, x_max=1)  # a real later gesture
    z = last_op("range_changed")
    assert z.get("parent") is None
    assert result_for(last_op("overlay_variable")["seq"])["interrupted_by"] == z["seq"]


def test_reset_race_never_falls_back_to_the_real_log_dir(qapp, isolated_config):
    oplog.reset()
    with pytest.raises(RuntimeError):
        oplog._open_file()
    oplog.record_message("info", "status", "x")  # inactive: silently dropped
    assert not config.PARQUET_ANALYZER_LOG_DIR.exists() or not any(config.PARQUET_ANALYZER_LOG_DIR.iterdir())


def test_flush_failure_warns_once(log, caplog):
    class Broken:
        def write(self, s):
            return len(s)

        def flush(self):
            raise OSError("disk full")

        def close(self):
            pass

    oplog.log_op("x")
    oplog._S.fh = Broken()
    with caplog.at_level(logging.WARNING, logger="parquet_analyzer"):
        oplog.flush()
        oplog.flush()
    assert len([r for r in caplog.records if "operation log write failed" in r.getMessage()]) == 1
    oplog._S.fh = None


def test_dicts_are_capped_like_lists(log):
    oplog.log_op("big", d={f"k{i}": i for i in range(80)})
    d = last_op("big")["details"]["d"]
    assert len(d) == 51 and d["..."] == "(+30)"


def test_coalesced_burst_keeps_only_first_and_last_op_line_unless_debug(log):
    for i in range(6):
        oplog.log_op("range_changed", plot="p1", i=i)
    settle()
    ops = [r for r in records("op") if r["action"] == "range_changed"]
    assert [o["details"]["i"] for o in ops] == [0, 5]
    res = records("result")[-1]
    assert res["merged"] == 6 and res["first_op"] == ops[0]["seq"] and res["op"] == ops[1]["seq"]
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=True)
    for i in range(4):
        oplog.log_op("range_changed", plot="p1", i=i)
    settle()
    assert len([r for r in records("op") if r["action"] == "range_changed"]) == 4 + 2


def test_apply_settings_and_open_log_folder_are_logged(win, monkeypatch):
    from parquet_analyzer.ui import settings_dialog

    win.apply_settings(Settings(log_retention_days=11))
    d = last_op("apply_settings")["details"]
    assert d["log_retention_days"] == 11 and "language" in d and "eager_load_limit_mb" in d
    monkeypatch.setattr(settings_dialog.QDesktopServices, "openUrl", staticmethod(lambda url: True))
    _turn()
    settings_dialog.open_log_folder()
    assert last_op("open_log_folder")


def test_snapshot_probes_see_real_values_after_the_merge(win):
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    win.plot_grid._selected = (plot, "a")
    snap = win.state_snapshot()
    info = snap["plots"][plot.plot_id]
    assert info["x_linked"] is True
    assert info["series_info"]["a"]["selected"] is True and info["series_info"]["a"]["visible"] is True
    plot.set_series_visible("a", False)
    assert win.state_snapshot()["plots"][plot.plot_id]["series_info"]["a"]["visible"] is False
    assert "time_order" in snap["file"] and "first_decreasing_index" in snap["file"]["time_order"]
