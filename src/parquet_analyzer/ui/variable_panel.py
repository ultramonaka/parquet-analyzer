from __future__ import annotations

from collections.abc import Mapping

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
    aliasRequested = Signal(str)  # "Add alias..." / "Edit alias..." on a file column (persistence 54.3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.itemDoubleClicked.connect(lambda item: self.variableDoubleClicked.emit(item.text()))
        self._derived: dict[str, str] = {}
        self._aliases: dict[str, str] = {}  # alias -> source column of the open file

    def set_variables(
        self,
        names: list[str],
        derived: Mapping[str, str] | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        """`names` are the display names (aliased columns appear under their alias only, 54.2);
        `aliases` (alias -> source) gives those items a tooltip naming the source column."""
        self.clear()
        self._derived = {}
        self._aliases = dict(aliases or {})
        for name in names:
            item = QListWidgetItem(name)  # text is the alias itself: it is used as the variable name
            if name in self._aliases:
                item.setToolTip(tr("variable.alias_source", source=self._aliases[name]))
            self.addItem(item)
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
        if item is None:
            return
        name = item.text()
        menu = QMenu(self)
        if name not in self._derived:  # a file column: real data, no formula to edit (alias only, 54.3)
            label = tr("variable.alias_edit") if name in self._aliases else tr("variable.alias_add")
            menu.addAction(label, lambda: self.aliasRequested.emit(name))
            menu.exec(event.globalPos())
            return
        menu.addAction(tr("variable.edit_expression"), lambda: self.editDerivedRequested.emit(name))
        menu.addAction(tr("variable.delete"), lambda: self.deleteDerivedRequested.emit(name))
        menu.exec(event.globalPos())

    def mimeData(self, items):  # noqa: N802 (Qt override)
        mime = super().mimeData(items)
        mime.setText(items[0].text())
        return mime
