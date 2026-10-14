"""Tools for the side-effect tests (app 5.36 / design chapter 45).

A button/menu action must change only what it is meant to change. `SideEffectHarness.run`
takes the window's `state_snapshot()` before and after an action (settled in both cases),
diffs them with `core.oplog.diff_states` (the same diff the operation log writes as
`changes`, 30.2) and fails when a changed path matches none of the action's allowed
patterns (`fnmatch`, `*` = any one or more characters). Watchers add: no file reload, no
dialog that the action did not declare, no new `exception` record in the operation log.
"""

from __future__ import annotations

import json
import time
from datetime import date
from fnmatch import fnmatchcase

from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from parquet_analyzer.core import oplog
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.ui import main_window as main_window_module

SETTLE_TIMEOUT = 5.0


class SideEffectFailure(AssertionError):
    """The action changed something it should not have (message lists every finding)."""


def path_allowed(path: str, patterns) -> bool:
    """Whether the dotted state path matches one of the `fnmatch` patterns."""
    return any(fnmatchcase(path, p) for p in patterns)


def unexpected_changes(changes: dict, patterns) -> dict:
    """The part of a `diff_states` result that no pattern covers."""
    return {p: v for p, v in changes.items() if not path_allowed(p, patterns)}


def settle(win, timeout: float = SETTLE_TIMEOUT) -> None:
    """Run the event loop until nothing is in flight: no busy source, no pending redraw
    debounce, and the operation log has no result waiting. Fails after `timeout` seconds."""
    deadline = time.monotonic() + timeout

    def quiet() -> bool:
        # analysis/XY/histogram windows run their own jobs (not in win._busy_sources): their
        # result handlers' side effects must be observed before the row is judged
        if any(w.is_computing() for w in list(getattr(win, "_analysis_windows", ()))):
            return False
        return not win._is_settling() and not oplog.has_pending()

    stable = 0
    while stable < 3:  # quiet three times in a row: a finished job may start the next step
        QApplication.processEvents()
        if quiet():
            stable += 1
            time.sleep(0.005)
        else:
            stable = 0
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"did not settle within {timeout}s: busy={sorted(win._busy_sources)}, "
                    f"pending result={oplog.has_pending()}, "
                    f"analysis computing={[type(w).__name__ for w in win._analysis_windows if w.is_computing()]}"
                )
            time.sleep(0.005)
    oplog.flush()


def exception_records() -> list[dict]:
    """Every `exception` record in today's operation log file."""
    oplog.flush()
    path = oplog.log_path_for(date.today())
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("kind") == "exception":
                out.append(rec)
    return out


class SideEffectHarness:
    """Wraps one `MainWindow`: patches every modal entry point and counts file loads."""

    _MESSAGE_BOXES = ("information", "warning", "critical", "question")

    def __init__(self, win, monkeypatch) -> None:
        self.win = win
        self.dialogs: list[str] = []  # every dialog shown during the current action
        self.answers: dict[str, object] = {}  # dialog name -> answer for the current action
        self.loads: list[str] = []  # load_parquet calls
        self.data_sources: list[str] = []  # ParquetDataSource constructions
        self._install(monkeypatch)

    # -- patches -----------------------------------------------------------------------

    def _fake(self, name: str, default):
        def fake(*args, **kwargs):
            self.dialogs.append(name)
            return self.answers.get(name, default)

        return staticmethod(fake)

    def _install(self, mp) -> None:
        yes = QMessageBox.StandardButton.Yes
        for kind in self._MESSAGE_BOXES:
            mp.setattr(QMessageBox, kind, self._fake(f"QMessageBox.{kind}", yes if kind == "question" else QMessageBox.StandardButton.Ok))
        mp.setattr(QInputDialog, "getText", self._fake("QInputDialog.getText", ("", False)))
        mp.setattr(QInputDialog, "getInt", self._fake("QInputDialog.getInt", (0, False)))
        mp.setattr(QInputDialog, "getDouble", self._fake("QInputDialog.getDouble", (0.0, False)))
        mp.setattr(QInputDialog, "getItem", self._fake("QInputDialog.getItem", ("", False)))
        for kind in ("getSaveFileName", "getOpenFileName", "getExistingDirectory"):
            default = "" if kind == "getExistingDirectory" else ("", "")
            mp.setattr(QFileDialog, kind, self._fake(f"QFileDialog.{kind}", default))
        mp.setattr(QFileDialog, "getOpenFileNames", self._fake("QFileDialog.getOpenFileNames", ([], "")))

        harness = self
        orig_load = main_window_module.MainWindow.load_parquet

        def counting_load(self_, path, *a, **k):
            harness.loads.append(str(path))
            return orig_load(self_, path, *a, **k)

        mp.setattr(main_window_module.MainWindow, "load_parquet", counting_load)
        orig_init = ParquetDataSource.__init__

        def counting_init(self_, path, *a, **k):
            harness.data_sources.append(str(path))
            return orig_init(self_, path, *a, **k)

        mp.setattr(ParquetDataSource, "__init__", counting_init)

    # -- running one action -------------------------------------------------------------

    def run(self, name: str, action, allowed=(), answers: dict | None = None, expected_dialogs=()) -> dict:
        """Run `action()` and check what changed. Returns the diff (for further asserts).

        allowed          fnmatch patterns of state paths the action may change (or a
                         callable returning them, evaluated after the action -- e.g. when
                         a new plot's id is only known then)
        answers          dialog name -> answer returned for this action
        expected_dialogs dialog names the action's table row declares (anything else fails)
        """
        win = self.win
        settle(win)
        before = win.state_snapshot()
        self.dialogs.clear()
        self.loads.clear()
        self.data_sources.clear()
        self.answers = dict(answers or {})
        exceptions_before = len(exception_records())

        action()
        settle(win)

        after = win.state_snapshot()
        if callable(allowed):
            allowed = allowed()
        changes = oplog.diff_states(before, after)
        problems: list[str] = []
        for path, (old, new) in unexpected_changes(changes, allowed).items():
            problems.append(f"changed: {path}: {old!r} -> {new!r}")
        if self.loads:
            problems.append(f"file reloaded (load_parquet called): {self.loads}")
        if self.data_sources:
            problems.append(f"data source re-created: {self.data_sources}")
        for d in self.dialogs:
            if d not in expected_dialogs:
                problems.append(f"unexpected dialog: {d}")
        new_exceptions = exception_records()[exceptions_before:]
        for rec in new_exceptions:
            problems.append(f"exception record in the operation log: {rec.get('type')}: {rec.get('message')}")
        if problems:
            raise SideEffectFailure(f"side effects of action {name!r}:\n  " + "\n  ".join(problems))
        return changes
