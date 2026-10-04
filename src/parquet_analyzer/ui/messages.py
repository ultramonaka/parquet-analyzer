"""The one place user-facing messages are shown (detailed_specification.md 30.3).

Each helper records a `message` entry in the operation log and then does exactly what
the direct `QMessageBox` / status-bar call did before, so behaviour (and tests that
monkeypatch `QMessageBox.<name>`) is unchanged. `QMessageBox` is looked up at call time
for that reason.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from ..core import oplog


def _record(level: str, text: object, title: object = None) -> None:
    oplog.record_message(level, "dialog", str(text), str(title) if title else None)


def information(parent, title, text, *args, **kwargs):
    _record("info", text, title)
    return QMessageBox.information(parent, title, text, *args, **kwargs)


def warning(parent, title, text, *args, **kwargs):
    _record("warning", text, title)
    return QMessageBox.warning(parent, title, text, *args, **kwargs)


def critical(parent, title, text, *args, **kwargs):
    _record("error", text, title)
    return QMessageBox.critical(parent, title, text, *args, **kwargs)


def question(parent, title, text, *args, **kwargs):
    _record("info", text, title)
    return QMessageBox.question(parent, title, text, *args, **kwargs)


def status_notice(text: object, level: str = "info") -> None:
    """Record a status-bar notice (call next to wherever it is shown)."""
    oplog.record_message(level, "status", str(text))
