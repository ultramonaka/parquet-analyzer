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


def warning_with_button(parent, title, text, button_text) -> bool:
    """A warning with one extra action button next to OK; True when that button was pressed
    (persistence 54.6). Like `warning`, it records the message first."""
    _record("warning", text, title)
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, parent)
    extra = box.addButton(button_text, QMessageBox.ButtonRole.ActionRole)
    box.exec()
    return box.clickedButton() is extra


def choose_export_overwrite(parent, title, text, timestamp_text, overwrite_text, cancel_text) -> str | None:
    """The three-way "file exists" question of an export (export 57.8.1): "timestamp" (the default
    button), "overwrite" or None (cancel / closed). Records the message like the other helpers."""
    _record("warning", text, title)
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, parent)
    stamp = box.addButton(timestamp_text, QMessageBox.ButtonRole.AcceptRole)
    over = box.addButton(overwrite_text, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(stamp)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is stamp:
        return "timestamp"
    if clicked is over:
        return "overwrite"
    return None


def confirm_export_start(parent, title, text, start_text, cancel_text) -> bool:
    """The "may not fit" question before an export starts (export 57.9.6a): True only for the explicit
    "start anyway" button; the default button, Escape and closing the box all mean "do not start"."""
    _record("warning", text, title)
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, parent)
    start = box.addButton(start_text, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is start


def critical(parent, title, text, *args, **kwargs):
    _record("error", text, title)
    return QMessageBox.critical(parent, title, text, *args, **kwargs)


def question(parent, title, text, *args, **kwargs):
    _record("info", text, title)
    return QMessageBox.question(parent, title, text, *args, **kwargs)


def status_notice(text: object, level: str = "info") -> None:
    """Record a status-bar notice (call next to wherever it is shown)."""
    oplog.record_message(level, "status", str(text))
