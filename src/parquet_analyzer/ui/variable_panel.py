from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu

from .i18n import tr


class VariablePanel(QListWidget):
    """Lists loaded + derived variables (specification.md 5.5, detailed_specification.md 5/6/18章).

    Drag onto a TimePlotWidget to overlay; double-click to insert into the
    ExpressionBar at the cursor. Derived variables are shown in italics with their
    expression as a tooltip, and get an edit/delete context menu (18.3).
    """

    variableDoubleClicked = Signal(str)
    editDerivedRequested = Signal(str)
    deleteDerivedRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.itemDoubleClicked.connect(lambda item: self.variableDoubleClicked.emit(item.text()))
        self._derived: dict[str, str] = {}

    def set_variables(self, names: list[str], derived: Mapping[str, str] | None = None) -> None:
        self.clear()
        self._derived = {}
        for name in names:
            self.addItem(QListWidgetItem(name))
        for name, expression in (derived or {}).items():
            self.add_variable(name, expression)

    def add_variable(self, name: str, expression: str | None = None) -> None:
        matches = self.findItems(name, Qt.MatchFlag.MatchExactly)
        item = matches[0] if matches else QListWidgetItem(name)
        if not matches:
            self.addItem(item)
        if expression is not None:
            self._derived[name] = expression
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
            item.setToolTip(f"{name} = {expression}")

    def remove_variable(self, name: str) -> None:
        self._derived.pop(name, None)
        for item in self.findItems(name, Qt.MatchFlag.MatchExactly):
            self.takeItem(self.row(item))

    def is_derived(self, name: str) -> bool:
        return name in self._derived

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        item = self.itemAt(event.pos())
        if item is None or item.text() not in self._derived:
            return  # file columns are real data, not formulas: nothing to edit/delete
        name = item.text()
        menu = QMenu(self)
        menu.addAction(tr("variable.edit_expression"), lambda: self.editDerivedRequested.emit(name))
        menu.addAction(tr("variable.delete"), lambda: self.deleteDerivedRequested.emit(name))
        menu.exec(event.globalPos())

    def mimeData(self, items):  # noqa: N802 (Qt override)
        mime = super().mimeData(items)
        mime.setText(items[0].text())
        return mime
