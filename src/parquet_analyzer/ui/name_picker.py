"""A button that opens a searchable, checkable list of names (analysis.ja.md 44.5).

Used for the XY window's Y selector: the old two-row list showed hardly any candidates and was
hard to search. The button's text lists the chosen names ("sine, cosine (2)"); clicking it opens
a popup with a search field and one checkbox per candidate. `single=True` (3D) makes it a
radio-like pick: choosing one clears the others.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .i18n import tr

_MAX_NAMES_SHOWN = 2  # names spelled out on the button before "(n)" takes over the detail


class _Popup(QFrame):
    def __init__(self, picker: NamePicker) -> None:
        super().__init__(picker, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("picker.search"))
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumWidth(220)
        self.list.setMinimumHeight(240)
        lay.addWidget(self.list)


class NamePicker(QPushButton):
    """Multi- (or single-) choice name picker; see module docstring."""

    selectionChanged = Signal()  # user changed the choice (also from `set_selected`, unless blocked)

    def __init__(self, label: str = "", parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._single = False
        self._names: list[str] = []
        self._checked: set[str] = set()
        self._popup = _Popup(self)
        self._popup.search.textChanged.connect(self._apply_filter)
        self._popup.list.itemClicked.connect(self._on_item_clicked)
        self.clicked.connect(self.show_popup)
        self.setMinimumWidth(140)
        self.setMaximumWidth(260)
        self._update_text()

    # -- state ------------------------------------------------------------------

    def set_candidates(self, names: list[str]) -> None:
        """Replace the candidate list, keeping the chosen names that still exist."""
        self._names = list(names)
        self._checked &= set(self._names)
        self._rebuild()

    def selected(self) -> list[str]:
        """Chosen names in candidate order."""
        return [n for n in self._names if n in self._checked]

    def set_selected(self, names) -> None:
        wanted = [n for n in self._names if n in set(names)]
        if self._single:
            wanted = wanted[:1]
        if set(wanted) == self._checked:
            return
        self._checked = set(wanted)
        self._sync_items()
        self._update_text()
        self.selectionChanged.emit()

    def set_single(self, single: bool) -> None:
        self._single = single

    def set_label(self, label: str) -> None:
        self._label = label
        self._update_text()

    def count(self) -> int:
        return len(self._names)

    def candidates(self) -> list[str]:
        return list(self._names)

    # -- popup ------------------------------------------------------------------

    def show_popup(self) -> None:
        self._popup.search.clear()
        self._popup.move(self.mapToGlobal(QPoint(0, self.height())))
        self._popup.show()
        self._popup.search.setFocus()

    def _rebuild(self) -> None:
        lst = self._popup.list
        lst.clear()
        for name in self._names:
            item = QListWidgetItem(name)
            # not user-checkable: the click handler toggles, so a click on the text and a click on
            # the box behave the same (otherwise the box would toggle twice)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if name in self._checked else Qt.CheckState.Unchecked)
            lst.addItem(item)
        self._apply_filter(self._popup.search.text())
        self._update_text()

    def _sync_items(self) -> None:
        lst = self._popup.list
        for i in range(lst.count()):
            it = lst.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.text() in self._checked else Qt.CheckState.Unchecked)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        lst = self._popup.list
        for i in range(lst.count()):
            it = lst.item(i)
            it.setHidden(bool(needle) and needle not in it.text().lower())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        name = item.text()
        if self._single:
            new = {name}
        else:
            new = set(self._checked)
            new.symmetric_difference_update({name})
        self.set_selected(new)
        if self._single:
            self._popup.hide()

    def _update_text(self) -> None:
        names = self.selected()
        if not names:
            body = tr("picker.none")
        elif len(names) <= _MAX_NAMES_SHOWN:
            body = ", ".join(names) if len(names) == 1 else f"{', '.join(names)} ({len(names)})"
        else:
            body = f"{', '.join(names[:_MAX_NAMES_SHOWN])}, ... ({len(names)})"
        self.setText(f"{self._label}  {body}" if self._label else body)
        self.setToolTip(", ".join(names))
