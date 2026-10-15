"""The Parquet export's settings dialog and its status-bar progress widget (export.ja.md 57.1, 57.3).

`ParquetExportDialog` only asks (range, signals); `MainWindow.export_parquet_dialog` then asks for
the file and starts the job. `ParquetExportProgress` is the status-bar bar + label + cancel button,
a separate widget from the view-load progress (expressions 55.4) so both can show at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import messages
from .i18n import tr


@dataclass
class ExportItemChoice:
    """One list row: `reason` (when `enabled` is False) says why it cannot be exported (57.7.3)."""

    name: str
    checked: bool = True
    enabled: bool = True
    reason: str = ""


@dataclass
class ExportChoices:
    """What the dialog shows for one source (the current screen or a saved view, 57.7.1).
    `error` (red, disables "Export") is why nothing can be exported from this source/file;
    `saved_range_text` (a saved view only) enables the "view's saved range" choice."""

    items: list[ExportItemChoice]
    full_rows: int = 0
    visible_rows: int = 0
    time_name: str = ""
    source_file: str = ""
    error: str = ""
    saved_range_text: str = ""


class ParquetExportDialog(QDialog):
    """Source (current screen / saved view), range and signals to write (57.1, 57.7.1).

    Without `provider` the dialog is the plain stage-1 one for `names`. With it, `provider(key,
    file_override)` returns the `ExportChoices` for a source (`key` None = current screen, else a saved
    view's name; `file_override` = a file picked with "Other file...") and is asked again whenever the
    source or the file changes."""

    def __init__(
        self,
        names: list[str],
        full_rows: int,
        visible_rows: int,
        time_name: str,
        parent: QWidget | None = None,
        *,
        sources: list[tuple[str, str | None]] | None = None,
        provider: Callable[[str | None, str | None], ExportChoices] | None = None,
        pick_file: Callable[[], str | None] | None = None,
        initial_key: str | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("parquet_export_dialog")
        self.setWindowTitle(tr("export_parquet.title"))
        self._provider = provider
        self._pick_file = pick_file
        self._file_override: str | None = None
        self._view_mode = False
        layout = QVBoxLayout(self)

        self.source_combo = QComboBox()
        self.source_combo.setObjectName("export_source_combo")
        source_row = QHBoxLayout()
        self.source_label = QLabel(tr("export_parquet.source_label"))
        source_row.addWidget(self.source_label)
        source_row.addWidget(self.source_combo, 1)
        layout.addLayout(source_row)
        self.file_widget = QWidget()
        file_row = QHBoxLayout(self.file_widget)
        file_row.setContentsMargins(0, 0, 0, 0)
        self.file_label = QLabel()
        self.file_label.setObjectName("export_source_file")
        self.file_label.setWordWrap(True)
        self.other_file_button = QPushButton(tr("export_parquet.source_other_button"))
        self.other_file_button.setObjectName("export_source_other_button")
        self.view_file_button = QPushButton(tr("export_parquet.source_view_file_button"))
        self.view_file_button.setObjectName("export_source_view_file_button")
        file_row.addWidget(QLabel(tr("export_parquet.source_file_label")))
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.other_file_button)
        file_row.addWidget(self.view_file_button)
        layout.addWidget(self.file_widget)
        self.error_label = QLabel()
        self.error_label.setObjectName("export_source_error")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #d32f2f;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        range_box = QGroupBox(tr("export_parquet.range_group"))
        range_layout = QVBoxLayout(range_box)
        self.all_radio = QRadioButton()
        self.all_radio.setObjectName("export_range_all")
        self.visible_radio = QRadioButton()
        self.visible_radio.setObjectName("export_range_visible")
        self.all_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.all_radio)
        group.addButton(self.visible_radio)
        range_layout.addWidget(self.all_radio)
        range_layout.addWidget(self.visible_radio)
        layout.addWidget(range_box)

        columns_box = QGroupBox(tr("export_parquet.columns_group"))
        columns_layout = QVBoxLayout(columns_box)
        self.list = QListWidget()
        self.list.setObjectName("export_signal_list")
        columns_layout.addWidget(self.list)
        buttons_row = QHBoxLayout()
        self.select_all_button = QPushButton(tr("export_parquet.select_all"))
        self.select_none_button = QPushButton(tr("export_parquet.select_none"))
        self.select_all_button.clicked.connect(lambda *_: self._set_all(Qt.CheckState.Checked))
        self.select_none_button.clicked.connect(lambda *_: self._set_all(Qt.CheckState.Unchecked))
        buttons_row.addWidget(self.select_all_button)
        buttons_row.addWidget(self.select_none_button)
        buttons_row.addStretch(1)
        columns_layout.addLayout(buttons_row)
        self.time_note = QLabel()
        columns_layout.addWidget(self.time_note)
        layout.addWidget(columns_box)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("export_parquet.export_button"))
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if provider is None:
            self.source_label.setVisible(False)
            self.source_combo.setVisible(False)
            self.file_widget.setVisible(False)
            self._show(ExportChoices([ExportItemChoice(n) for n in names], full_rows, visible_rows, time_name))
        else:
            for label, key in sources or []:
                self.source_combo.addItem(label, key)
            if initial_key is not None:
                self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(initial_key)))
            self.source_combo.currentIndexChanged.connect(self._on_source_changed)
            self.other_file_button.clicked.connect(lambda *_: self._on_other_file())
            self.view_file_button.clicked.connect(lambda *_: self._on_view_file())
            self._reload()

    # -- source -----------------------------------------------------------------------------

    def selected_source(self) -> str | None:
        """None = the current screen, else the chosen saved view's name."""
        return self.source_combo.currentData() if self._provider is not None else None

    def source_file_override(self) -> str | None:
        return self._file_override if self._view_mode else None

    def _on_source_changed(self, *_args) -> None:
        self._file_override = None
        self._reload()

    def _on_other_file(self) -> None:
        path = self._pick_file() if self._pick_file is not None else None
        if path:
            self._file_override = path
            self._reload()

    def _on_view_file(self) -> None:
        self._file_override = None
        self._reload()

    def _reload(self) -> None:
        self._show(self._provider(self.selected_source(), self._file_override))

    def _show(self, choices: ExportChoices) -> None:
        view_mode = self.selected_source() is not None
        self._view_mode = view_mode
        self.file_widget.setVisible(view_mode)
        self.file_label.setText(choices.source_file)
        self.view_file_button.setEnabled(self._file_override is not None)
        self.error_label.setText(choices.error)
        self.error_label.setVisible(bool(choices.error))
        self.file_label.setStyleSheet("color: #d32f2f;" if choices.error else "")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not choices.error)
        self.all_radio.setText(tr("export_parquet.range_all", rows=f"{choices.full_rows:,}"))
        if view_mode:
            self.visible_radio.setText(tr("export_parquet.range_saved", range=choices.saved_range_text))
            self.visible_radio.setEnabled(bool(choices.saved_range_text))
        else:
            self.visible_radio.setText(tr("export_parquet.range_visible", rows=f"{choices.visible_rows:,}"))
            self.visible_radio.setEnabled(choices.visible_rows > 0)
        if not self.visible_radio.isEnabled():
            self.all_radio.setChecked(True)
        self.list.clear()
        for choice in choices.items:
            text = choice.name if choice.enabled else f"{choice.name}  ({choice.reason})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, choice.name)
            if choice.enabled:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if choice.checked else Qt.CheckState.Unchecked)
            else:  # greyed out and not checkable, with the reason (57.7.3)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setToolTip(choice.reason)
            self.list.addItem(item)
        self.time_note.setText(tr("export_parquet.time_note", name=choices.time_name))

    # -- result -----------------------------------------------------------------------------

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable:
                self.list.item(i).setCheckState(state)

    def _on_accept(self) -> None:
        if not self.selected_names():
            messages.information(self, tr("export_parquet.title"), tr("export_parquet.nothing_selected"))
            return
        self.accept()

    def selected_names(self) -> list[str]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def use_visible_range(self) -> bool:
        """The second range choice: the visible range (current screen) / the view's saved range."""
        return self.visible_radio.isChecked()


class ParquetExportProgress(QWidget):
    """Status-bar widget shown while an export runs: bar (0-100 %), "Exporting to Parquet NN%" and
    the cancel button (57.3). Hidden otherwise."""

    def __init__(self, on_cancel: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("parquet_export_progress")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setObjectName("parquet_export_bar")
        self.bar.setRange(0, 100)
        self.bar.setMaximumWidth(140)
        self.bar.setTextVisible(False)
        self.label = QLabel()
        self.label.setObjectName("parquet_export_label")
        self.cancel_button = QPushButton(tr("export_parquet.cancel"))
        self.cancel_button.setObjectName("parquet_export_cancel_button")
        self.cancel_button.clicked.connect(lambda *_: on_cancel())
        layout.addWidget(self.bar)
        layout.addWidget(self.label)
        layout.addWidget(self.cancel_button)
        self.setVisible(False)

    def begin(self) -> None:
        self.set_progress(0, 1)
        self.setVisible(True)

    def end(self) -> None:
        self.setVisible(False)

    def set_progress(self, done: int, total: int) -> None:
        """`total < 0`: a phase without a percentage: computing variables over all rows (57.3, -1) or
        searching the saved time range (57.7.4, -2)."""
        if total < 0:
            self.bar.setRange(0, 0)  # indeterminate
            self.label.setText(tr("export_parquet.progress_range" if total == -2 else "export_parquet.progress_full"))
            return
        self.bar.setRange(0, 100)
        percent = 100 if total <= 0 else min(100, int(done * 100 / total))
        self.bar.setValue(percent)
        self.label.setText(tr("export_parquet.progress", percent=percent))
