"""Assign substitute signals to the names a view could not find (persistence 54.6).

Modal. One row per missing name: a combo of the names the open file lists plus its original column
names (default "do not assign"), and a note saying what OK will do for that name. A name that
cannot be an alias (a function / map name) gets a disabled row with the reason. The dialog only
collects choices; `MainWindow.assign_aliases_from_view` stores them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)

from .i18n import tr

_NOTE_STYLE = "color: #b26a00;"
_ERROR_STYLE = "color: #d32f2f;"


@dataclass(frozen=True)
class AssignRow:
    """One missing name: `note` is what OK would do; `selectable=False` disables the row."""

    name: str
    note: str
    selectable: bool = True


class SignalAliasAssignDialog(QDialog):
    def __init__(self, parent, view_name: str, rows: Sequence[AssignRow], choices: Sequence[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("alias.assign_title"))
        self.combos: dict[str, QComboBox] = {}
        intro = QLabel(tr("alias.assign_intro", view=view_name))
        intro.setWordWrap(True)
        grid = QGridLayout()
        for r, row in enumerate(rows):
            combo = QComboBox()
            combo.addItem(tr("alias.assign_none"), None)
            for c in choices:
                combo.addItem(c, c)
            combo.setEnabled(row.selectable)
            combo.setObjectName(f"assign_combo_{row.name}")
            self.combos[row.name] = combo
            note = QLabel(row.note)
            note.setWordWrap(True)
            note.setStyleSheet(_NOTE_STYLE if row.selectable else _ERROR_STYLE)
            grid.addWidget(QLabel(row.name), r * 2, 0)
            grid.addWidget(combo, r * 2, 1)
            grid.addWidget(note, r * 2 + 1, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(grid)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)

    def assignments(self) -> dict[str, str]:
        """`{missing name: chosen listed name}` for the rows that are not "do not assign"."""
        return {
            name: combo.currentData()
            for name, combo in self.combos.items()
            if combo.isEnabled() and combo.currentData() is not None
        }
