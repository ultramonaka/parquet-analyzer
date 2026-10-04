"""parquet_analyzer.__main__: argument parsing, logging setup, startup sequence
(detailed_specification.md 30.1 op log, 37.2 GC guard, 25.3 shared GL contexts).

The event loop is never started: QApplication/MainWindow are replaced by fakes, and
every global side effect (logging handlers, excepthooks, oplog) is monkeypatched."""

from __future__ import annotations

import logging

import pytest

from parquet_analyzer import __main__ as entry
from parquet_analyzer import __version__
from parquet_analyzer.core import oplog


@pytest.fixture
def no_global_side_effects(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.setdefault("basicConfig", kw))
    monkeypatch.setattr(oplog, "configure", lambda *a, **kw: calls.setdefault("configure", (a, kw)))
    monkeypatch.setattr(oplog, "install_excepthooks", lambda: calls.setdefault("hooks", True))
    monkeypatch.setattr(oplog, "shutdown", lambda: calls.setdefault("shutdown", True))
    return calls


# ---- _parse_args ---------------------------------------------------------------------------


def test_parse_args_defaults(monkeypatch):
    monkeypatch.delenv("PARQUET_ANALYZER_DEBUG", raising=False)
    # the env default is read when the parser is built, i.e. per call
    assert entry._parse_args([]).debug is False


def test_parse_args_debug_flag(monkeypatch):
    monkeypatch.delenv("PARQUET_ANALYZER_DEBUG", raising=False)
    assert entry._parse_args(["--debug"]).debug is True


@pytest.mark.parametrize("value,expected", [("1", True), ("yes", True), ("0", False), ("", False)])
def test_parse_args_debug_from_environment(monkeypatch, value, expected):
    monkeypatch.setenv("PARQUET_ANALYZER_DEBUG", value)
    assert entry._parse_args([]).debug is expected


def test_parse_args_version_prints_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        entry._parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parse_args_unknown_option_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        entry._parse_args(["--bogus"])
    assert exc.value.code == 2


# ---- _configure_logging --------------------------------------------------------------------


def test_configure_logging_normal_mode(isolated_config, no_global_side_effects, monkeypatch):
    monkeypatch.setattr(entry, "load_settings", lambda: type("S", (), {"log_retention_days": 3})())
    entry._configure_logging(debug=False)

    kw = no_global_side_effects["basicConfig"]
    assert kw["level"] == logging.INFO
    file_handler = next(h for h in kw["handlers"] if isinstance(h, logging.FileHandler))
    try:
        assert file_handler.baseFilename.startswith(str(isolated_config.PARQUET_ANALYZER_LOG_DIR))
        assert isolated_config.PARQUET_ANALYZER_LOG_DIR.is_dir()  # ensure_dirs ran
    finally:
        for h in kw["handlers"]:
            h.close()
    (args, ckw) = no_global_side_effects["configure"]
    assert ckw["retention_days"] == 3 and ckw["debug"] is False and ckw["app_version"] == __version__
    assert no_global_side_effects["hooks"] is True


def test_configure_logging_survives_broken_settings(isolated_config, no_global_side_effects, monkeypatch):
    def boom():
        raise RuntimeError("corrupt settings.json")

    monkeypatch.setattr(entry, "load_settings", boom)
    entry._configure_logging(debug=False)
    for h in no_global_side_effects["basicConfig"]["handlers"]:
        h.close()
    (_args, ckw) = no_global_side_effects["configure"]
    assert ckw["retention_days"] == oplog.DEFAULT_RETENTION_DAYS


def test_configure_logging_debug_mode_adds_console_echo(isolated_config, no_global_side_effects, monkeypatch):
    monkeypatch.setattr(entry, "load_settings", lambda: type("S", (), {"log_retention_days": 7})())
    ops_logger = logging.getLogger(entry._OPS_LOGGER_NAME)
    saved = (list(ops_logger.handlers), ops_logger.level, ops_logger.propagate)
    try:
        entry._configure_logging(debug=True)
        kw = no_global_side_effects["basicConfig"]
        assert kw["level"] == logging.DEBUG
        for h in kw["handlers"]:
            h.close()
        assert ops_logger.level == logging.DEBUG
        assert ops_logger.propagate is False
        assert len(ops_logger.handlers) == len(saved[0]) + 1
        assert no_global_side_effects["configure"][1]["debug"] is True
    finally:
        ops_logger.handlers[:] = saved[0]
        ops_logger.setLevel(saved[1])
        ops_logger.propagate = saved[2]


# ---- GC guard / GL contexts ------------------------------------------------------------------


def test_start_gc_guard_keeps_a_reference(qapp, monkeypatch):
    created = []

    class FakeGuard:
        def __init__(self, interval):
            created.append(interval)

    monkeypatch.setattr(entry, "GarbageCollector", FakeGuard)
    monkeypatch.setattr(entry, "_gc_guard", None)
    guard = entry._start_gc_guard()
    assert created == [1.0]
    assert entry._gc_guard is guard  # held for the app's lifetime


def test_enable_shared_gl_contexts_sets_attribute(qapp):
    from PySide6.QtCore import QCoreApplication, Qt

    entry._enable_shared_gl_contexts()
    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


# ---- main ----------------------------------------------------------------------------------


@pytest.fixture
def fake_app(monkeypatch, no_global_side_effects):
    events: list = []

    class FakeApp:
        def __init__(self, argv):
            events.append("app")

        def exec(self):
            events.append("exec")
            return 7

    class FakeWindow:
        def __init__(self, debug=False):
            events.append(("window", debug))

        def show(self):
            events.append("show")

    monkeypatch.setattr(entry, "QApplication", FakeApp)
    monkeypatch.setattr(entry, "MainWindow", FakeWindow)
    monkeypatch.setattr(entry, "_configure_logging", lambda debug: events.append(("log", debug)))
    monkeypatch.setattr(entry, "_enable_shared_gl_contexts", lambda: events.append("gl"))
    monkeypatch.setattr(entry, "_start_gc_guard", lambda: events.append("gc"))
    return events


def test_main_startup_order_and_exit_code(fake_app, no_global_side_effects, monkeypatch):
    monkeypatch.delenv("PARQUET_ANALYZER_DEBUG", raising=False)
    code = entry.main(["--debug"])
    assert code == 7  # app.exec()'s code is propagated
    # GL attribute before QApplication (25.3); GC guard after it (37.2); window shown before exec
    assert fake_app == [("log", True), "gl", "app", "gc", ("window", True), "show", "exec"]
    assert no_global_side_effects["shutdown"] is True  # oplog flushed on the way out


def test_main_uses_sys_argv_when_argv_is_none(fake_app, monkeypatch):
    monkeypatch.delenv("PARQUET_ANALYZER_DEBUG", raising=False)
    monkeypatch.setattr("sys.argv", ["parquet_analyzer", "--debug"])
    assert entry.main() == 7
    assert ("log", True) in fake_app


def test_main_bad_option_exits_before_any_startup(fake_app):
    with pytest.raises(SystemExit) as exc:
        entry.main(["--nope"])
    assert exc.value.code == 2
    assert fake_app == []  # no logging, no QApplication
