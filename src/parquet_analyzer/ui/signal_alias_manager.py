"""Signal alias editor (persistence 54.4).

Non-modal and a singleton owned by `MainWindow.open_signal_alias_manager()` (like the map
manager). Left: the dictionary's aliases with the column each one currently resolves to in the
open file. Right: the selected alias's name and its ordered candidate columns. "Save" / "Revert"
are enabled only while the form differs from the stored entry; leaving with unsaved changes asks.
Validation, the in-use checks and applying to the open file live in
`MainWindow.save_signal_aliases`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.oplog import log_op
from ..core.signal_aliases import AliasProblem, validate_entry
from . import messages
from .i18n import tr

_ERROR_STYLE = "color: #d32f2f;"
_OK_STYLE = "color: #2e7d32;"
_NOTE_STYLE = "color: #b26a00;"


class SignalAliasManagerWindow(QDialog):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.setModal(False)
        self.setWindowTitle(tr("alias.manager_title"))
        self.resize(820, 480)

        # stored name of the entry being edited; None = a new, unsaved one (`_is_new`)
        self._shown: str | None = None
        self._is_new = False
        self._baseline: tuple[str, list[str]] = ("", [])
        self._baseline_enabled = True
        self._loading = False
        self._message_ok = False
        self._row_data: str | None = None  # data of the currently selected left-list row

        self.alias_list = QListWidget()
        self.add_button = QPushButton(tr("alias.add"))
        self.delete_button = QPushButton(tr("alias.delete"))
        self.global_checkbox = QCheckBox(tr("alias.use_aliases"))  # applied at once (54.9)
        self.global_checkbox.setObjectName("signal_aliases_global_checkbox")
        self.global_checkbox.setChecked(self._mw.signal_aliases_enabled)
        self.enabled_checkbox = QCheckBox(tr("alias.enabled"))  # part of the form (54.9)
        self.enabled_checkbox.setObjectName("signal_alias_enabled_checkbox")
        self.enabled_checkbox.setChecked(True)
        self.name_edit = QLineEdit()
        self.sources_list = QListWidget()
        self.up_button = QPushButton(tr("alias.up"))
        self.down_button = QPushButton(tr("alias.down"))
        self.remove_source_button = QPushButton(tr("alias.remove_source"))
        self.source_combo = QComboBox()
        self.source_combo.setEditable(True)
        self.source_combo.lineEdit().setPlaceholderText(tr("alias.source_placeholder"))
        self.add_source_button = QPushButton(tr("alias.add_source"))
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(_NOTE_STYLE)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.save_button = QPushButton(tr("alias.save"))
        self.revert_button = QPushButton(tr("alias.revert"))

        left = QVBoxLayout()
        left.addWidget(self.alias_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.delete_button)
        left.addLayout(row)

        right = QVBoxLayout()
        right.addWidget(self.enabled_checkbox)
        right.addWidget(QLabel(tr("alias.name")))
        right.addWidget(self.name_edit)
        right.addWidget(self.note_label)
        right.addWidget(QLabel(tr("alias.sources")))
        right.addWidget(self.sources_list, 1)
        row = QHBoxLayout()
        for b in (self.up_button, self.down_button, self.remove_source_button):
            row.addWidget(b)
        right.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.source_combo, 1)
        row.addWidget(self.add_source_button)
        right.addLayout(row)
        right.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.save_button)
        row.addWidget(self.revert_button)
        right.addLayout(row)

        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addLayout(right, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(self.global_checkbox)
        layout.addLayout(body, 1)

        self.alias_list.currentRowChanged.connect(self._on_row_changed)
        self.add_button.clicked.connect(lambda *_: self.start_new())
        self.delete_button.clicked.connect(lambda *_: self.delete_selected())
        self.name_edit.textChanged.connect(lambda *_: self._update_state())
        self.enabled_checkbox.toggled.connect(lambda *_: self._update_state())
        self.global_checkbox.clicked.connect(self._on_global_clicked)
        self.up_button.clicked.connect(lambda *_: self._move_source(-1))
        self.down_button.clicked.connect(lambda *_: self._move_source(1))
        self.remove_source_button.clicked.connect(lambda *_: self._remove_source())
        self.add_source_button.clicked.connect(lambda *_: self._add_source())
        self.source_combo.lineEdit().returnPressed.connect(self._add_source)
        self.save_button.clicked.connect(lambda *_: self.save())
        self.revert_button.clicked.connect(lambda *_: self.revert())

        self._fill_combo()
        self._rebuild_list()
        if self.alias_list.count():
            self.alias_list.setCurrentRow(0)
        else:
            self._load_form(None)

    # -- list ------------------------------------------------------------------------------

    def _status_text(self, alias: str) -> str:
        kind, source = self._mw.signal_alias_status(alias)
        if kind == "source":
            return source or ""
        key = {"same_name": "alias.status_same_name", "off": "alias.status_off", "disabled": "alias.status_disabled"}
        return tr(key.get(kind, "alias.status_none"))

    def _rebuild_list(self, select: str | None | bool = True) -> None:
        """Rebuild the left list. `select`: True = keep the current row's entry, a name = select
        it, None = the new-entry row; the selection change itself never asks anything."""
        want = self._row_data if select is True else select
        self._loading = True
        try:
            self.alias_list.clear()
            for alias in self._mw.signal_aliases:
                off = alias in self._mw.signal_aliases_disabled
                shown = f"{alias} {tr('alias.disabled_mark')}" if off else alias
                item = QListWidgetItem(tr("alias.list_item", alias=shown, source=self._status_text(alias)))
                item.setData(Qt.ItemDataRole.UserRole, alias)
                if off:
                    item.setForeground(QBrush(QColor("#9a9a9a")))  # faded
                self.alias_list.addItem(item)
            if self._is_new:
                item = QListWidgetItem(tr("alias.new_entry"))
                item.setData(Qt.ItemDataRole.UserRole, None)
                self.alias_list.addItem(item)
            for i in range(self.alias_list.count()):
                if self.alias_list.item(i).data(Qt.ItemDataRole.UserRole) == want:
                    self.alias_list.setCurrentRow(i)
                    break
            else:
                self.alias_list.setCurrentRow(-1)
            self._row_data = want if self.alias_list.currentRow() >= 0 else None
        finally:
            self._loading = False

    def _fill_combo(self) -> None:
        text = self.source_combo.currentText()
        self.source_combo.clear()
        self.source_combo.addItems(self._mw.signal_alias_columns())
        self.source_combo.setEditText(text)

    def sync_global_checkbox(self) -> None:
        """Show the setting (changed from the gear menu, or reverted after a refusal)."""
        self.global_checkbox.blockSignals(True)
        self.global_checkbox.setChecked(self._mw.signal_aliases_enabled)
        self.global_checkbox.blockSignals(False)

    def _on_global_clicked(self, checked: bool) -> None:
        reason = self._mw.set_signal_aliases_enabled(checked)
        self._mw._sync_alias_toggle_ui()  # also puts the checkbox back after a refusal
        self.refresh_status()
        if reason:
            self._say(reason)

    def refresh_status(self) -> None:
        """The open file changed (or its aliases were re-resolved): redo the status texts and the
        candidate combo, keeping the selection and the form as they are."""
        self._fill_combo()
        self._rebuild_list()
        self._update_state()

    def _on_row_changed(self, row: int) -> None:
        if self._loading:
            return
        item = self.alias_list.item(row) if row >= 0 else None
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if item is None or data == self._row_data:
            return
        if not self._confirm_leave():
            self._rebuild_list()  # back to the entry being edited
            return
        self._is_new = False
        self._rebuild_list(data)
        self._load_form(data)

    # -- form ------------------------------------------------------------------------------

    def _form(self) -> tuple[str, list[str]]:
        sources = [self.sources_list.item(i).text() for i in range(self.sources_list.count())]
        return self.name_edit.text().strip(), sources

    def _load_form(self, alias: str | None, new_source: str | None = None) -> None:
        """Show a stored entry (`alias`) or a blank new one (optionally with a first candidate)."""
        self._loading = True
        try:
            self._shown = alias
            if alias is None:
                name, sources = "", ([new_source] if new_source else [])
                enabled = True
            else:
                name, sources = alias, list(self._mw.signal_aliases.get(alias, []))
                enabled = alias not in self._mw.signal_aliases_disabled
            self.name_edit.setText(name)
            self.enabled_checkbox.setChecked(enabled)
            self._baseline_enabled = enabled
            self.sources_list.clear()
            self.sources_list.addItems(sources)
            self._baseline = (name, list(sources))
        finally:
            self._loading = False
        self._fill_combo()
        self._say("")
        self._update_state()

    def _dirty(self) -> bool:
        return self._form() != self._baseline or self.enabled_checkbox.isChecked() != self._baseline_enabled

    def _update_state(self) -> None:
        dirty = self._dirty()
        if dirty and self._message_ok:
            self._say("")  # "Saved." no longer describes the form
        self.save_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        has_entry = self._shown is not None or self._is_new
        self.delete_button.setEnabled(self._shown is not None or self._is_new)
        for w in (self.enabled_checkbox, self.name_edit, self.sources_list, self.source_combo, self.add_source_button,
                  self.up_button, self.down_button, self.remove_source_button):
            w.setEnabled(has_entry)
        name = self.name_edit.text().strip()
        notes = []
        if name and not name.isidentifier():
            notes.append(tr("alias.not_identifier"))
        if name and name in self._mw.signal_alias_columns():
            notes.append(tr("alias.same_name_note"))
        self.note_label.setText("\n".join(notes))

    def _say(self, text: str, ok: bool = False) -> None:
        self._message_ok = ok
        self.message.setStyleSheet(_OK_STYLE if ok else _ERROR_STYLE)
        self.message.setText(text)

    def _add_source(self) -> None:
        text = self.source_combo.currentText().strip()
        if not text:
            return
        self.sources_list.addItem(text)
        self.source_combo.setEditText("")
        self._update_state()

    def _remove_source(self) -> None:
        row = self.sources_list.currentRow()
        if row >= 0:
            self.sources_list.takeItem(row)
            self._update_state()

    def _move_source(self, delta: int) -> None:
        row = self.sources_list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.sources_list.count():
            return
        item = self.sources_list.takeItem(row)
        self.sources_list.insertItem(target, item)
        self.sources_list.setCurrentRow(target)
        self._update_state()

    # -- actions ---------------------------------------------------------------------------

    def _confirm_leave(self) -> bool:
        """Ask what to do with unsaved edits; True = may continue (saved or discarded)."""
        if not self._dirty():
            return True
        name = self._form()[0] or self._shown or tr("alias.new_entry")
        answer = messages.question(
            self, tr("alias.manager_title"), tr("alias.unsaved_confirm", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return self.save()
        if answer == QMessageBox.StandardButton.No:
            self._baseline = self._form()  # discarded: nothing is dirty any more
            self._baseline_enabled = self.enabled_checkbox.isChecked()
            return True
        return False

    def start_new(self, source: str | None = None) -> None:
        """A blank new entry, optionally with `source` as its first candidate (54.3)."""
        if not self._confirm_leave():
            return
        self._is_new = True
        self._rebuild_list(None)
        self._load_form(None, source)

    def select_alias(self, alias: str) -> None:
        """Show the stored alias `alias` (asks first if the current form has unsaved edits)."""
        if alias not in self._mw.signal_aliases or not self._confirm_leave():
            return
        self._is_new = False
        self._rebuild_list(alias)
        self._load_form(alias)

    def save(self) -> bool:
        """Validate and store the form (54.4). Errors show inline; returns success."""
        name, sources = self._form()
        stored = self._mw.signal_aliases
        others = [a for a in stored if a != self._shown]
        try:
            name, sources = validate_entry(name, sources, reserved=self._mw.callable_names(), others=others)
        except AliasProblem as e:
            self._say(tr(e.key, **e.params))
            return False
        if self._shown is not None and self._shown in stored:
            new = {(name if a == self._shown else a): (sources if a == self._shown else s) for a, s in stored.items()}
        else:
            new = {**stored, name: sources}
        disabled = set(self._mw.signal_aliases_disabled) - {self._shown or name, name}
        if not self.enabled_checkbox.isChecked():
            disabled.add(name)
        reason = self._mw.save_signal_aliases(new, disabled)
        if reason:
            self._say(reason)
            return False
        self._is_new = False
        self._rebuild_list(name)
        self._load_form(name)
        self._say(tr("alias.saved"), ok=True)
        return True

    def revert(self) -> None:
        log_op("signal_alias_revert", name=self._shown)
        self._load_form(self._shown, self._baseline[1][0] if self._is_new and self._baseline[1] else None)

    def delete_selected(self) -> bool:
        """Delete the selected stored alias (confirmed; refused while it is in use), or drop the
        unsaved new entry."""
        if self._shown is None:
            if self._is_new:
                self._is_new = False
                self._rebuild_list(None)
                self._after_delete()
            return True
        name = self._shown
        answer = messages.question(self, tr("alias.manager_title"), tr("alias.delete_confirm", name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return False
        new = {a: s for a, s in self._mw.signal_aliases.items() if a != name}
        reason = self._mw.save_signal_aliases(new)
        if reason:
            self._say(reason)
            return False
        self._is_new = False
        self._rebuild_list(None)
        self._after_delete()
        return True

    def _after_delete(self) -> None:
        if self.alias_list.count():
            data = self.alias_list.item(0).data(Qt.ItemDataRole.UserRole)
            self._rebuild_list(data)
            self._load_form(data)
        else:
            self._load_form(None)
            self._row_data = None

    def done(self, result: int) -> None:
        if not self._confirm_leave():
            return  # stay open (cancelled, or the save failed)
        super().done(result)
