"""Abnormal input for the operation log and its viewer (app.ja.md ch.30)."""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from parquet_analyzer.core import oplog, oplog_view


@pytest.fixture
def log_dir(tmp_path):
    oplog.reset()
    yield tmp_path / "log"
    oplog.reset()


def _records(log_dir):
    files = list(log_dir.glob("*ops*.log"))
    assert len(files) == 1
    return oplog_view.read_records(files[0])


def _ops(log_dir):
    return [r for r in _records(log_dir) if r["kind"] == "op"]


class BadStr:
    def __str__(self):
        raise RuntimeError("no str")


class BadItem:
    def item(self):
        raise RuntimeError("x")

    def __str__(self):
        return "BI"


_circ: list = []
_circ.append(_circ)
_cd: dict = {}
_cd["self"] = _cd


@pytest.mark.parametrize(
    "value",
    [
        BadStr(),
        BadItem(),
        _circ,
        _cd,
        b"\xff\x00",
        float("nan"),
        float("inf"),
        np.float32(1.5),
        np.arange(5),
        np.float64("nan"),
        "x" * 10**6,
        10**400,
        {3, 1},
        {1: "a", "1": "b", None: "c"},
        {(1, 2): 3},
        Path("/x"),
        date.today(),
        object(),
        "\ud800",  # lone surrogate: not UTF-8 encodable
        "bad\udcff-name",  # what surrogateescape makes of an undecodable file name
        {"\ud800": 1},
        {BadStr(): 1},
        [BadStr()] * 3,
        list(range(10_000)),
    ],
    ids=lambda v: type(v).__name__ + str(id(v) % 1000),
)
def test_log_op_with_any_argument_never_raises_and_the_record_is_written(log_dir, value):
    """A broken __str__ or a lone surrogate used to drop the record silently (UnicodeEncodeError
    in the write, or an exception inside the sanitiser)."""
    oplog.configure(log_dir)
    oplog.log_op("probe", v=value)
    oplog.flush_pending()
    oplog.shutdown()
    ops = _ops(log_dir)
    assert [o["action"] for o in ops] == ["probe"]
    json.dumps(ops[0])  # fully JSON-safe


def test_a_bad_record_does_not_stop_later_records(log_dir):
    oplog.configure(log_dir)
    oplog.log_op("first", v="\ud800")
    oplog.flush_pending()
    oplog.log_op("second", v="ok")
    oplog.flush_pending()
    oplog.shutdown()
    assert [o["action"] for o in _ops(log_dir)] == ["first", "second"]


def test_huge_string_and_list_are_capped(log_dir):
    oplog.configure(log_dir)
    oplog.log_op("big", s="x" * 10**6, items=list(range(1000)))
    oplog.flush_pending()
    oplog.shutdown()
    d = _ops(log_dir)[0]["details"]
    assert len(d["s"]) < oplog.MAX_STRING_CHARS + 30 and d["s"].endswith("...(+998000)")
    assert len(d["items"]) == oplog.MAX_LIST_ITEMS + 1


def test_non_finite_floats_are_written_as_text(log_dir):
    oplog.configure(log_dir)
    oplog.log_op("f", a=float("nan"), b=float("-inf"))
    oplog.flush_pending()
    oplog.shutdown()
    raw = next(log_dir.glob("*ops*.log")).read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    assert _ops(log_dir)[0]["details"] == {"a": "nan", "b": "-inf"}


def test_unwritable_log_dir_is_silent_and_the_app_keeps_working(tmp_path, caplog):
    oplog.reset()
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    try:
        oplog.configure(blocker)  # a file where the folder should be
        oplog.log_op("x", a=1)
        oplog.record_message("info", "t", "m")
        oplog.flush_pending()
        oplog.shutdown()
    finally:
        oplog.reset()
    assert blocker.read_text() == "x"


def test_log_dir_below_a_missing_unwritable_parent(tmp_path):
    oplog.reset()
    try:
        oplog.configure(Path("/proc/definitely/not/writable") if Path("/proc").exists() else tmp_path / "f" / "x")
        oplog.log_op("x")
        oplog.shutdown()
    finally:
        oplog.reset()


def test_write_error_mid_session_does_not_raise(log_dir, monkeypatch):
    oplog.configure(log_dir)

    class Boom:
        def write(self, *_):
            raise OSError(28, "disk full")

        def flush(self):
            raise OSError(28, "disk full")

        def close(self):
            pass

    oplog._S.fh = Boom()
    oplog.log_op("x")
    oplog.record_job("j", "start")
    oplog.flush_pending()
    oplog.shutdown()


def test_calls_before_configure_are_noops():
    oplog.reset()
    oplog.log_op("x", a=1)
    oplog.record_job("j", "start")
    oplog.record_message("info", "w", "t")
    oplog.flush_pending()
    oplog.shutdown()
    assert not oplog.is_active()


def test_state_provider_that_raises_or_returns_garbage(log_dir):
    for provider in (lambda: 1 / 0, lambda: [1, 2], lambda: None, lambda: {"a": BadStr()}):
        oplog.reset()
        oplog.configure(log_dir)
        oplog.set_state_provider(provider)
        oplog.log_op("x")
        oplog.flush_pending()
        oplog.shutdown()


def test_excepthook_records_exception_with_unprintable_message(log_dir):
    oplog.configure(log_dir)
    try:
        raise ValueError(BadStr())
    except ValueError as e:
        oplog.record_exception(type(e), e, e.__traceback__)
    oplog.shutdown()
    assert any(r["kind"] == "exception" for r in _records(log_dir))


# -- retention --------------------------------------------------------------------------------


def _touch(d, names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("x")


def test_purge_only_touches_log_files_with_a_valid_old_date(tmp_path):
    today = date.today()
    old = (today - timedelta(days=30)).strftime("%Y%m%d")
    future = (today + timedelta(days=5)).strftime("%Y%m%d")
    keep = [
        "other.log",
        "parquet_analyzer_ops_20269999.log",  # impossible date
        "parquet_analyzer_ops_00000000.log",
        f"parquet_analyzer_ops_{old}.log.bak",
        f"parquet_analyzer_ops_{future}.log",
        f"parquet_analyzer_ops_{today:%Y%m%d}.log",
        f"parquet_analyzer_ops_{old}.txt",
        f"xparquet_analyzer_ops_{old}.log",
        "notes.md",
    ]
    gone = [f"parquet_analyzer_ops_{old}.log", f"parquet_analyzer_{old}.log"]
    _touch(tmp_path, keep + gone)
    removed = oplog.purge_old_logs(tmp_path, 7)
    assert sorted(p.name for p in removed) == sorted(gone)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(keep)


@pytest.mark.parametrize("bad", [0, -1, "x", None, 10**9, 1.5, True, [3]])
def test_purge_with_odd_retention_values_uses_the_default_window(tmp_path, bad):
    """Invalid retention must never mean 'delete everything' (e.g. 0 or negative)."""
    today = date.today()
    recent = f"parquet_analyzer_ops_{(today - timedelta(days=3)).strftime('%Y%m%d')}.log"
    ancient = f"parquet_analyzer_ops_{(today - timedelta(days=4000)).strftime('%Y%m%d')}.log"
    _touch(tmp_path, [recent, ancient])
    oplog.purge_old_logs(tmp_path, bad)
    assert (tmp_path / recent).exists()
    assert not (tmp_path / ancient).exists()


def test_purge_missing_dir_and_directory_with_log_name(tmp_path):
    assert oplog.purge_old_logs(tmp_path / "missing", 7) == []
    old = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    (tmp_path / f"parquet_analyzer_ops_{old}.log").mkdir()
    assert oplog.purge_old_logs(tmp_path, 7) == []  # unlink fails: skipped, no exception


def test_purge_unlink_failure_is_skipped(tmp_path, monkeypatch):
    old = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    _touch(tmp_path, [f"parquet_analyzer_ops_{old}.log"])
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("busy")))
    assert oplog.purge_old_logs(tmp_path, 7) == []


# -- diff / helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (None, None, {}),
        ({}, {}, {}),
        ({"a": 1}, {"a": 1.0}, {}),
        ({"a": True}, {"a": 1}, {}),
        ({"a": 1}, {"a": 2}, {"a": [1, 2]}),
        ({"a": {"b": 1}}, {"a": {"b": 2}}, {"a.b": [1, 2]}),
        ({"a": 1}, {}, {"a": [1, None]}),
        ({}, {"a": 1}, {"a": [None, 1]}),
        ({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [[1, 2], [1, 2, 3]]}),
        ({"a": {"b": 1}}, {"a": 5}, {"a": [{"b": 1}, 5]}),
    ],
)
def test_diff_states_shapes(a, b, expected):
    assert oplog.diff_states(a, b) == expected


def test_format_time_extremes_do_not_raise():
    for x in (float("nan"), float("inf"), -1e30, 1e30, 0.0, -1.0):
        assert isinstance(oplog.format_time(x), str)


# -- oplog_view -------------------------------------------------------------------------------

DAMAGED = (
    b'garbage\n{"kind":"op","seq":1,"action":"a"}\n{"kind":"op","seq\n\xff\xfe\x00\n[1,2]\n'
    b'{"kind":"op","seq":"x","action":3,"details":5}\n'
    b'{"kind":"result","changes":{"a":5,"b":[1]},"settled":false}\n{"kind":"session","event":null}\n'
    b'{"kind":"job"}\n{"kind":"op"}\n{"kind":"exception","traceback":null}\n{"kind":"result","changes":[1,2]}\n'
    b'{"kind":"op","seq":1,"details":[1],"ts":5}\n{"kind":"op","seq":1,"details":{"a":{"nan":NaN}}}\n'
    b'{"kind":"unknown","x":1}\n{"kind":"message"}\n'
)


def test_read_records_skips_non_json_non_object_and_binary_lines(tmp_path):
    p = tmp_path / "c.log"
    p.write_bytes(DAMAGED)
    recs = oplog_view.read_records(p)
    assert all(isinstance(r, dict) for r in recs) and len(recs) >= 10


def test_damaged_log_renders_without_raising(tmp_path):
    """Wrong-shaped records (details as an int, changes as a list, no seq) crashed the viewer
    with AttributeError/KeyError, exactly when a damaged log is being inspected."""
    p = tmp_path / "c.log"
    p.write_bytes(DAMAGED)
    out = io.StringIO()
    assert oplog_view.main([str(p)], out) == 0
    text = out.getvalue()
    assert "#1  a" in text and "EXCEPTION" in text


def test_one_bad_record_does_not_hide_the_good_ones(tmp_path):
    p = tmp_path / "c.log"
    p.write_text(
        '{"kind":"op","seq":1,"action":"first"}\n{"kind":"op","details":5}\n{"kind":"op","seq":3,"action":"third"}\n',
        encoding="utf-8",
    )
    out = io.StringIO()
    oplog_view.main([str(p)], out)
    assert "first" in out.getvalue() and "third" in out.getvalue()


@pytest.mark.parametrize("content", [b"", b"\n\n\n", b"\xff\xfe\xfd", b"{"])
def test_empty_or_unparseable_log_prints_nothing_and_succeeds(tmp_path, content):
    p = tmp_path / "e.log"
    p.write_bytes(content)
    out = io.StringIO()
    assert oplog_view.main([str(p)], out) == 0
    assert out.getvalue() == ""


def test_missing_file_and_directory_return_1(tmp_path, capsys):
    assert oplog_view.main([str(tmp_path / "missing.log")], io.StringIO()) == 1
    assert oplog_view.main([str(tmp_path)], io.StringIO()) == 1  # used to raise IsADirectoryError
    assert "not found" in capsys.readouterr().err


def test_default_file_is_todays_log_in_the_configured_dir(isolated_config, capsys):
    assert oplog_view.main([], io.StringIO()) == 1  # nothing there yet: reported, not raised


@pytest.mark.parametrize("last", ["0", "-3", "1", "999"])
def test_last_option_with_odd_numbers(tmp_path, last):
    p = tmp_path / "c.log"
    p.write_bytes(DAMAGED)
    assert oplog_view.main([str(p), "--last", last], io.StringIO()) == 0


def test_last_and_session_selection_on_multi_session_input():
    recs = [
        {"kind": "session", "session": "A", "event": "start"},
        {"kind": "op", "seq": 1, "action": "x", "session": "A"},
        {"kind": "session", "session": "B", "event": "start"},
        {"kind": "op", "seq": 1, "action": "y", "session": "B"},
        {"kind": "op", "seq": 2, "action": "z", "session": "B", "parent": 1},
    ]
    assert [r.get("action") for r in oplog_view.select_records(recs, session_only=True) if r["kind"] == "op"] == ["y", "z"]
    assert oplog_view.select_records(recs, last=1)[0].get("action") == "y"
    assert oplog_view.select_records([], session_only=True, last=3) == []


def test_non_numeric_last_is_an_argparse_error(tmp_path):
    with pytest.raises(SystemExit):
        oplog_view.main([str(tmp_path / "x"), "--last", "abc"], io.StringIO())


def test_roundtrip_of_a_real_log_renders_every_kind(log_dir):
    oplog.configure(log_dir)
    oplog.set_state_provider(lambda: {"n": 1})
    oplog.log_op("act", x=1)
    oplog.record_job("j", "done", ms=1.23, target="t")
    oplog.record_message("warning", "w", "text")
    try:
        1 / 0
    except ZeroDivisionError as e:
        oplog.record_exception(type(e), e, e.__traceback__)
    oplog.flush_pending()
    oplog.shutdown()
    out = io.StringIO()
    assert oplog_view.main([str(next(log_dir.glob("*ops*.log")))], out) == 0
    text = out.getvalue()
    for needle in ("session start", "act", "job j done", "message [warning/w]", "EXCEPTION ZeroDivisionError", "session end"):
        assert needle in text
