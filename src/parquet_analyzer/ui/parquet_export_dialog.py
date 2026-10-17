"""The Parquet export's settings dialog and its status-bar progress widget (export.ja.md 57.1, 57.3).

`ParquetExportDialog` only asks (range, signals); `MainWindow.export_parquet_dialog` then asks for
the file and starts the job. `ParquetExportProgress` is the status-bar bar + label + cancel button,
a separate widget from the view-load progress (expressions 55.4) so both can show at once.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.export_queue import STAGE_FULL, STAGE_RANGE, STAGE_WRITE, ExportItem, ItemState
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
    time_candidates: list[str] = field(default_factory=list)  # columns usable as the time axis (57.8.3)
    view_time_name: str = ""  # the view's own time axis, whatever column is chosen now


class ParquetExportDialog(QDialog):
    """Source (current screen / saved view), range and signals to write (57.1, 57.7.1).

    Without `provider` the dialog is the plain stage-1 one for `names`. With it, `provider(key,
    file_override)` returns the `ExportChoices` for a source (`key` None = current screen, else a saved
    view's name; `file_override` = a file picked with "Other file...") and is asked again whenever the
    source or the file changes. "Other file..." picks one or several files (57.8.2); with two or more
    the dialog lists every file with what is usable in it and `source_files()` returns them all."""

    def __init__(
        self,
        names: list[str],
        full_rows: int,
        visible_rows: int,
        time_name: str,
        parent: QWidget | None = None,
        *,
        sources: list[tuple[str, str | None]] | None = None,
        provider: Callable[[str | None, str | None, str | None], ExportChoices] | None = None,
        pick_file: Callable[[], Sequence[str]] | None = None,
        initial_key: str | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("parquet_export_dialog")
        self.setWindowTitle(tr("export_parquet.title"))
        self._provider = provider
        self._pick_file = pick_file
        self._file_overrides: list[str] = []
        self._file_choices: list[ExportChoices] = []  # one per picked file, only with two or more (57.8.2)
        self._populating = False
        self._choices_cache: dict[tuple, ExportChoices] = {}  # (view, file, time column) -> footer-based choices
        self._time_choice: str | None = None  # a column picked in the time-axis combo (None: the view's, 57.8.3)
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
        self.files_list = QListWidget()  # per-file usability when several files are picked (57.8.2)
        self.files_list.setObjectName("export_files_list")
        self.files_list.setMaximumHeight(110)
        self.files_list.setVisible(False)
        layout.addWidget(self.files_list)
        self.time_widget = QWidget()  # the time axis of the output (57.8.3); saved views only
        time_row = QHBoxLayout(self.time_widget)
        time_row.setContentsMargins(0, 0, 0, 0)
        self.time_combo = QComboBox()
        self.time_combo.setObjectName("export_time_combo")
        time_row.addWidget(QLabel(tr("export_parquet.time_axis_label")))
        time_row.addWidget(self.time_combo, 1)
        layout.addWidget(self.time_widget)
        self.time_combo.activated.connect(self._on_time_chosen)
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
        self.list.itemChanged.connect(lambda *_: self._update_file_status())
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

        dtype_row = QHBoxLayout()  # 57.9.6b
        self.dtype_combo = QComboBox()
        self.dtype_combo.setObjectName("export_dtype_combo")
        self.dtype_combo.addItem(tr("export_parquet.dtype_float64"), "float64")
        self.dtype_combo.addItem(tr("export_parquet.dtype_float32"), "float32")
        self.dtype_combo.setToolTip(tr("export_parquet.dtype_tooltip"))
        dtype_row.addWidget(QLabel(tr("export_parquet.dtype_label")))
        dtype_row.addWidget(self.dtype_combo, 1)
        layout.addLayout(dtype_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("export_parquet.export_button"))
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if provider is None:
            self.source_label.setVisible(False)
            self.source_combo.setVisible(False)
            self.file_widget.setVisible(False)
            self.time_widget.setVisible(False)
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

    def source_files(self) -> list[str]:
        """The files picked with "Other file..." (empty: the view's own file)."""
        return list(self._file_overrides) if self._view_mode else []

    def source_file_override(self) -> str | None:
        """The one picked file, or None (no file picked, or several: see `source_files`)."""
        files = self.source_files()
        return files[0] if len(files) == 1 else None

    def selected_time_column(self) -> str | None:
        """The column chosen as the output's time axis, None for the view's own (57.8.3)."""
        return self._time_choice if self._view_mode else None

    def selected_dtype(self) -> str:
        """"float64" or "float32": the type the derived variables are written in (57.9.6b)."""
        return self.dtype_combo.currentData()

    def _on_time_chosen(self, *_args) -> None:
        self._time_choice = self.time_combo.currentData()
        self._reload()

    def _on_source_changed(self, *_args) -> None:
        self._file_overrides = []
        self._time_choice = None
        self._choices_cache.clear()
        self._reload()

    def _on_other_file(self) -> None:
        picked = self._pick_file() if self._pick_file is not None else None
        if isinstance(picked, str):
            picked = [picked]
        files = list(dict.fromkeys(str(p) for p in (picked or []) if p))
        if files:
            self._file_overrides = files
            self._reload()

    def _on_view_file(self) -> None:
        self._file_overrides = []
        self._reload()

    def _reload(self) -> None:
        key = self.selected_source()
        self._file_choices = []
        if key is not None and len(self._file_overrides) >= 2:
            per = [self._cached_choices(key, f) for f in self._file_overrides]
            self._file_choices = per
            self._show(self._merge(per))
            return
        self._show(self._provider(key, self._file_overrides[0] if self._file_overrides else None, self._time_choice))

    def _cached_choices(self, key: str | None, file: str) -> ExportChoices:
        """The provider reads the file's footer; ticking signals or switching back and forth must not read
        every file again, so the answer for (view, file, time column) is kept while the dialog is open."""
        ck = (key, file, self._time_choice)
        if ck not in self._choices_cache:
            self._choices_cache[ck] = self._provider(key, file, self._time_choice)
        return self._choices_cache[ck]

    def _merge(self, per: list[ExportChoices]) -> ExportChoices:
        """One list for several files: a signal is selectable when some file has it; the row count is the
        sum; the error shows only when no file can be exported (57.8.2)."""
        good = [c for c in per if not c.error and c.items]
        base = good or per
        by_name: dict[str, list[ExportItemChoice]] = {}
        for c in base:
            for it in c.items:
                by_name.setdefault(it.name, []).append(it)
        items = []
        for name, versions in by_name.items():
            usable = [v for v in versions if v.enabled]
            first = versions[0]
            items.append(
                ExportItemChoice(name, first.checked, bool(usable), "" if usable else first.reason)
            )
        return ExportChoices(
            items,
            full_rows=sum(c.full_rows for c in good),
            time_name=next((c.time_name for c in base if c.time_name), ""),
            source_file=tr("export_parquet.files_count", n=len(per)),
            error="" if good else (per[0].error if per else ""),
            saved_range_text=next((c.saved_range_text for c in base if c.saved_range_text), ""),
            time_candidates=list(dict.fromkeys(n for c in per for n in c.time_candidates)),
            view_time_name=next((c.view_time_name for c in base if c.view_time_name), ""),
        )

    def _update_file_status(self) -> None:
        """The per-file list of a multi-file export (57.8.2): for the signals checked now, what each file
        lacks. Runs whenever the checks change."""
        multi = len(self._file_choices) >= 2
        self.files_list.setVisible(multi)
        if self._populating or not multi:
            self.files_list.clear()
            return
        selected = self.selected_names()
        self.files_list.clear()
        for path, choices in zip(self._file_overrides, self._file_choices, strict=False):
            name = Path(path).name
            if choices.error:
                text, bad = tr("export_parquet.file_error", name=name, error=choices.error), True
            else:
                usable = {i.name for i in choices.items if i.enabled}
                missing = [n for n in selected if n not in usable]
                if missing and len(missing) == len(selected):
                    text, bad = tr("export_parquet.file_none", name=name), True
                elif missing:
                    text, bad = tr("export_parquet.file_missing", name=name, n=len(missing), names=", ".join(missing)), True
                else:
                    text, bad = tr("export_parquet.file_ok", name=name), False
            item = QListWidgetItem(text)
            item.setToolTip(path)
            if bad:
                item.setForeground(QColor("#b45309"))
            self.files_list.addItem(item)

    def _fill_time_combo(self, choices: ExportChoices) -> None:
        """"The view's time axis (name)" first, then every column usable as one (57.8.3)."""
        if self._time_choice is not None and self._time_choice not in choices.time_candidates:
            self._time_choice = None
        self.time_combo.blockSignals(True)
        self.time_combo.clear()
        self.time_combo.addItem(tr("export_parquet.time_axis_view", name=choices.view_time_name or choices.time_name or "-"), None)
        for name in choices.time_candidates:
            self.time_combo.addItem(name, name)
        self.time_combo.setCurrentIndex(max(0, self.time_combo.findData(self._time_choice)))
        self.time_combo.blockSignals(False)

    def _show(self, choices: ExportChoices) -> None:
        view_mode = self.selected_source() is not None
        self._view_mode = view_mode
        self.file_widget.setVisible(view_mode)
        self.time_widget.setVisible(view_mode)
        self._fill_time_combo(choices)
        self.file_label.setText(choices.source_file)
        self.view_file_button.setEnabled(bool(self._file_overrides))
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
        self._populating = True
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
        self._populating = False
        self.time_note.setText(tr("export_parquet.time_note", name=choices.time_name))
        self._update_file_status()

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
    """Status-bar summary of the export queue (57.3, 57.9.7): bar, "Exporting to Parquet 2/5 - 43%" (one
    file: "Exporting to Parquet NN%"), a "Details..." button that opens the list and a cancel button
    (cancels everything). Hidden when nothing waits or runs."""

    def __init__(
        self,
        on_cancel: Callable[[], None],
        on_details: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ):
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
        self.details_button = QPushButton(tr("export_parquet.details_button"))
        self.details_button.setObjectName("parquet_export_details_button")
        if on_details is not None:
            self.details_button.clicked.connect(lambda *_: on_details())
        self.cancel_button = QPushButton(tr("export_parquet.cancel"))
        self.cancel_button.setObjectName("parquet_export_cancel_button")
        self.cancel_button.clicked.connect(lambda *_: on_cancel())
        layout.addWidget(self.bar)
        layout.addWidget(self.label)
        layout.addWidget(self.details_button)
        layout.addWidget(self.cancel_button)
        self.setVisible(False)

    def begin(self) -> None:
        self.set_progress(0, 1)
        self.setVisible(True)

    def end(self) -> None:
        self.setVisible(False)

    def set_progress(self, done: int, total: int) -> None:
        """One file: `total < 0`: a phase without a percentage: computing variables over all rows (57.3, -1) or
        searching the saved time range (57.7.4, -2)."""
        if total < 0:
            self.set_summary(0, 1, 0, total)
            return
        self.set_summary(0, 1, 100 if total <= 0 else min(100, int(done * 100 / total)))

    def set_summary(self, finished: int, files: int, percent: int, phase: int = 1) -> None:
        """`finished` of `files` files are over; `percent`: rows written over rows to write (0-100);
        `phase` < 0: nothing has a percentage yet (-1: all-rows computation, -2: range search)."""
        self.cancel_button.setText(tr("export_parquet.cancel_all") if files > 1 else tr("export_parquet.cancel"))
        if phase < 0:
            self.bar.setRange(0, 0)  # indeterminate
            self.label.setText(tr("export_parquet.progress_range" if phase == -2 else "export_parquet.progress_full"))
            return
        self.bar.setRange(0, 100)
        percent = max(0, min(100, int(percent)))
        self.bar.setValue(percent)
        if files > 1:
            self.label.setText(tr("export_parquet.progress_files", done=finished, files=files, percent=percent))
        else:
            self.label.setText(tr("export_parquet.progress", percent=percent))


@dataclass
class PanelSummary:
    """What the list's header shows (57.9.7); built by the controller from the queue."""

    percent: int = 0
    indeterminate: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    elapsed: float = 0.0
    remaining: float | None = None
    warnings: list[str] = field(default_factory=list)


def format_seconds(sec: float | None) -> str:
    if sec is None or sec < 0:
        return "-"
    sec = int(sec)
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_STATE_COLORS = {
    ItemState.WAITING: "#6b7280",
    ItemState.RUNNING: "#1d4ed8",
    ItemState.DONE: "#15803d",
    ItemState.FAILED: "#b91c1c",
    ItemState.CANCELLING: "#b45309",
    ItemState.CANCELLED: "#b45309",
}

PANEL_COLUMNS = (
    "export_parquet.col_source", "export_parquet.col_output", "export_parquet.col_state", "export_parquet.col_progress",
    "export_parquet.col_rows", "export_parquet.col_rate", "export_parquet.col_elapsed", "export_parquet.col_remaining",
    "export_parquet.col_action",
)
COL_SOURCE, COL_OUTPUT, COL_STATE, COL_BAR, COL_ROWS, COL_RATE, COL_ELAPSED, COL_REMAINING, COL_ACTION = range(9)


def state_text(item: ExportItem) -> str:
    """The list's wording for an item's state (colour is added by the table, so colour is not the only cue)."""
    if item.state is ItemState.WAITING:
        if item.wait_reason == "disk":
            return tr("export_parquet.state_wait_disk")
        if item.wait_reason == "memory":
            return tr("export_parquet.state_wait_memory")
        return tr("export_parquet.state_waiting")
    if item.state is ItemState.RUNNING:
        if item.stage == STAGE_RANGE:
            return tr("export_parquet.state_range")
        if item.stage == STAGE_FULL:
            return tr("export_parquet.state_full")
        return tr("export_parquet.state_writing")
    return tr(f"export_parquet.state_{item.state.value}")


class ParquetExportPanel(QDialog):
    """The modeless list of the export queue (57.9.7): one row per output file with its state, %, rows,
    rows/s, elapsed and remaining time, and a cancel / open-folder button; a header with the overall bar
    and the counts; buttons to cancel everything and to clear the finished rows. Closing it does not
    stop the export. It only displays: `refresh` is driven by the controller's 250 ms timer."""

    def __init__(
        self,
        on_row_action: Callable[[int], None],
        on_cancel_all: Callable[[], None],
        on_clear: Callable[[], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("parquet_export_panel")
        self.setWindowTitle(tr("export_parquet.panel_title"))
        self.setModal(False)
        self.resize(1100, 420)
        self._on_row_action = on_row_action
        self._ids: list[int] = []
        self._items: dict[int, ExportItem] = {}
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setObjectName("export_panel_bar")
        self.bar.setRange(0, 100)
        self.counts_label = QLabel()
        self.counts_label.setObjectName("export_panel_counts")
        self.time_label = QLabel()
        self.time_label.setObjectName("export_panel_time")
        top.addWidget(self.bar, 1)
        top.addWidget(self.counts_label)
        top.addWidget(self.time_label)
        layout.addLayout(top)
        self.warning_label = QLabel()
        self.warning_label.setObjectName("export_panel_warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b45309;")
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)
        self.table = QTableWidget(0, len(PANEL_COLUMNS))
        self.table.setObjectName("export_panel_table")
        self.table.setHorizontalHeaderLabels([tr(k) for k in PANEL_COLUMNS])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)
        for col in (COL_SOURCE, COL_OUTPUT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in (COL_STATE, COL_ROWS, COL_RATE, COL_ELAPSED, COL_REMAINING):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_BAR, COL_ACTION):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._fit_columns = False
        self.table.itemSelectionChanged.connect(self._update_detail)
        layout.addWidget(self.table, 1)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("export_panel_detail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_label)
        buttons = QHBoxLayout()
        self.cancel_all_button = QPushButton(tr("export_parquet.cancel_all"))
        self.cancel_all_button.setObjectName("export_panel_cancel_all")
        self.cancel_all_button.clicked.connect(lambda *_: on_cancel_all())
        self.clear_button = QPushButton(tr("export_parquet.clear_finished"))
        self.clear_button.setObjectName("export_panel_clear")
        self.clear_button.clicked.connect(lambda *_: on_clear())
        self.close_button = QPushButton(tr("export_parquet.close"))
        self.close_button.setObjectName("export_panel_close")
        self.close_button.clicked.connect(lambda *_: self.close())
        buttons.addWidget(self.cancel_all_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    # -- drawing ---------------------------------------------------------------------------

    def _rebuild(self, items: Sequence[ExportItem]) -> None:
        self.table.setRowCount(0)
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            for col in (COL_SOURCE, COL_OUTPUT, COL_STATE, COL_ROWS, COL_RATE, COL_ELAPSED, COL_REMAINING):
                self.table.setItem(row, col, QTableWidgetItem())
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setObjectName(f"export_panel_row_bar_{item.id}")
            self.table.setCellWidget(row, COL_BAR, bar)
            button = QPushButton()
            button.setObjectName(f"export_panel_row_action_{item.id}")
            button.clicked.connect(lambda *_a, i=item.id: self._on_row_action(i))
            self.table.setCellWidget(row, COL_ACTION, button)
        self._ids = [i.id for i in items]
        self._fit_columns = True  # sized after the first draw: empty cells have no width yet

    def refresh(self, items: Sequence[ExportItem], summary: PanelSummary) -> None:
        if [i.id for i in items] != self._ids:
            self._rebuild(items)
        self._items = {i.id: i for i in items}
        now = time.monotonic()
        c = summary.counts
        self.counts_label.setText(
            tr(
                "export_parquet.panel_counts", done=c.get("done", 0), failed=c.get("failed", 0),
                cancelled=c.get("cancelled", 0), total=c.get("total", 0),
            )
        )
        self.time_label.setText(
            tr("export_parquet.panel_time", elapsed=format_seconds(summary.elapsed), remaining=format_seconds(summary.remaining))
        )
        if summary.indeterminate:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(max(0, min(100, summary.percent)))
        self.warning_label.setText("\n".join(summary.warnings))
        self.warning_label.setVisible(bool(summary.warnings))
        for row, item in enumerate(items):
            self._draw_row(row, item, now)
        if self._fit_columns:  # the fixed columns: wide enough for the bar and for the longest button text
            self._fit_columns = False
            probe = QPushButton()
            width = 0
            for key in ("export_parquet.cancel", "export_parquet.open_folder"):
                probe.setText(tr(key))
                width = max(width, probe.sizeHint().width())
            self.table.setColumnWidth(COL_BAR, 120)
            self.table.setColumnWidth(COL_ACTION, width + 8)
        self.cancel_all_button.setEnabled(any(i.state in (ItemState.WAITING, ItemState.RUNNING) for i in items))
        self.clear_button.setEnabled(any(i.finished for i in items))
        self._update_detail()

    def _draw_row(self, row: int, item: ExportItem, now: float) -> None:
        def cell(col: int) -> QTableWidgetItem:
            return self.table.item(row, col)

        cell(COL_SOURCE).setText(Path(item.source).name)
        cell(COL_SOURCE).setToolTip(item.source)
        cell(COL_OUTPUT).setText(Path(item.output).name)
        cell(COL_OUTPUT).setToolTip(item.output)
        state = cell(COL_STATE)
        state.setText(state_text(item))
        state.setForeground(QColor(_STATE_COLORS[item.state]))
        tip = item.error or item.note
        if item.missing:
            tip = (tip + "\n" if tip else "") + tr("export_parquet.file_missing_tip", names=", ".join(item.missing))
        state.setToolTip(tip)
        bar = self.table.cellWidget(row, COL_BAR)
        if item.state is ItemState.RUNNING and item.stage and item.stage != STAGE_WRITE:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            if item.state is ItemState.DONE:
                bar.setValue(100)
            elif item.total_rows > 0:
                bar.setValue(min(99, int(item.done_rows * 100 / item.total_rows)) if item.active else 0)
            else:
                bar.setValue(0)
        cell(COL_ROWS).setText(f"{item.done_rows:,} / {item.total_rows:,}" if item.total_rows or item.done_rows else "-")
        running = item.state is ItemState.RUNNING
        cell(COL_RATE).setText(f"{item.rate:,.0f}" if running and item.rate > 0 else "-")
        if item.started_at is not None:
            end = item.finished_at if item.finished_at is not None else now
            cell(COL_ELAPSED).setText(format_seconds(end - item.started_at))
        else:
            cell(COL_ELAPSED).setText("-")
        remaining = None
        if running and item.rate > 0 and item.total_rows >= item.done_rows:
            remaining = (item.total_rows - item.done_rows) / item.rate
        cell(COL_REMAINING).setText(format_seconds(remaining))
        button = self.table.cellWidget(row, COL_ACTION)
        if item.state in (ItemState.WAITING, ItemState.RUNNING):
            button.setText(tr("export_parquet.cancel"))
            button.setEnabled(True)
            button.setVisible(True)
        elif item.state is ItemState.DONE:
            button.setText(tr("export_parquet.open_folder"))
            button.setEnabled(True)
            button.setVisible(True)
        else:
            button.setVisible(False)

    def _update_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            self.detail_label.setText("")
            return
        row = rows[0].row()
        if not 0 <= row < len(self._ids):
            return
        item = self._items.get(self._ids[row])
        if item is None:
            return
        parts = [item.output]
        if item.error:
            parts.append(item.error)
        if item.missing:
            parts.append(tr("export_parquet.file_missing_tip", names=", ".join(item.missing)))
        if item.note:
            parts.append(item.note)
        self.detail_label.setText("\n".join(parts))
