from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem


class VariablePanel(QListWidget):
    """Lists loaded + derived variables (specification.md 5.5, detailed_specification.md 5/6章).

    Drag onto a TimePlotWidget to overlay; double-click to insert into the
    ExpressionBar at the cursor.
    """

    variableDoubleClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.itemDoubleClicked.connect(lambda item: self.variableDoubleClicked.emit(item.text()))

    def set_variables(self, names: list[str]) -> None:
        self.clear()
        for name in names:
            self.addItem(QListWidgetItem(name))

    def add_variable(self, name: str) -> None:
        if not self.findItems(name, Qt.MatchFlag.MatchExactly):
            self.addItem(QListWidgetItem(name))

    def mimeData(self, items):  # noqa: N802 (Qt override)
        mime = super().mimeData(items)
        mime.setText(items[0].text())
        return mime
