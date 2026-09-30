"""View management window (detailed_specification.md 20.2).

Lists saved views, shows what the selected one contains, and lets the user load or
delete it. Display-only: it never touches `MainWindow` state, loading is reported via
`accept()` + `selected_view_name()` and deletion via `viewDeleted`.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core.expression import ExpressionError, referenced_names
from ..core.variable import dependency_order
from ..io import view as view_io
from .i18n import tr

_MISSING_COLOR = QColor("#d32f2f")


def format_timestamp(x: float) -> str:
    """Epoch seconds as `YYYY-MM-DD HH:MM:SS.mmm` (same as `TimePlotWidget.format_x`)."""
    try:
        return datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (OverflowError, OSError, ValueError):
        return f"{x:.6g}"


def _swatch(color: str) -> QIcon:
    pm = QPixmap(12, 12)
    qc = QColor(color)
    pm.fill(qc if qc.isValid() else QColor("#888888"))
    return QIcon(pm)


class ViewManagerDialog(QDialog):
    viewDeleted = Signal(str)

    def __init__(self, available_columns: set[str] | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("view.manager_title"))
        self._available = available_columns

        self.view_list = QListWidget()
        self.details = QTreeWidget()
        self.details.setHeaderHidden(True)
        self.details.setIndentation(16)
        self.load_button = QPushButton(tr("view.load_button"))
        self.delete_button = QPushButton(tr("view.delete_button"))
        self.close_button = QPushButton(tr("view.close_button"))

        body = QHBoxLayout()
        body.addWidget(self.view_list, 1)
        body.addWidget(self.details, 3)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(buttons)
        self.resize(760, 420)

        self.view_list.addItems(view_io.list_views())
        self.view_list.currentItemChanged.connect(lambda *_: self._refresh_details())
        self.view_list.itemDoubleClicked.connect(lambda *_: self.load_selected())
        self.load_button.clicked.connect(self.load_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.close_button.clicked.connect(self.reject)
        if self.view_list.count():
            self.view_list.setCurrentRow(0)
        self._refresh_details()

    # -- selection -----------------------------------------------------------------
    def selected_view_name(self) -> str | None:
        item = self.view_list.currentItem()
        return item.text() if item is not None else None

    def load_selected(self) -> None:
        if self.load_button.isEnabled():
            self.accept()

    def delete_selected(self) -> None:
        name = self.selected_view_name()
        if name is None:
            return
        answer = QMessageBox.question(
            self, tr("view.manager_title"), tr("view.delete_confirm", name=name)
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            view_io.delete_view(name)
        except OSError as e:
            QMessageBox.warning(self, tr("view.manager_title"), tr("view.delete_failed", error=e))
            return
        row = self.view_list.currentRow()
        self.view_list.takeItem(row)  # selection moves to a neighbour (next, else previous)
        self.viewDeleted.emit(name)
        self._refresh_details()

    # -- details -------------------------------------------------------------------
    def _refresh_details(self) -> None:
        self.details.clear()
        name = self.selected_view_name()
        self.delete_button.setEnabled(name is not None)
        self.load_button.setEnabled(False)
        if name is None:
            self.details.addTopLevelItem(QTreeWidgetItem([tr("view.none_saved")]))
            return
        try:
            view = view_io.load_view(name)
            saved_at = view_io.view_saved_at(name)
            missing_series, missing_derived = self._missing(view)
        except Exception as e:  # noqa: BLE001 (shown in the details, not swallowed)
            item = QTreeWidgetItem([tr("view.detail_unreadable", error=e)])
            item.setForeground(0, QBrush(_MISSING_COLOR))
            self.details.addTopLevelItem(item)
            return
        self.load_button.setEnabled(True)

        n_missing = len(missing_series) + len(missing_derived)
        if n_missing:
            self._top(tr("view.detail_missing_summary", m=n_missing), red=True)
        src = self._top(tr("view.detail_source", path=view.source.parquet_path))
        src.setToolTip(0, tr("view.detail_source_tooltip", type=view.source.path_type))
        self._top(tr("view.detail_saved", time=saved_at.strftime("%Y-%m-%d %H:%M:%S")))
        if view.x_axis_range is not None:
            start, end = view.x_axis_range
            self._top(tr("view.detail_time_range", start=format_timestamp(start), end=format_timestamp(end)))
        for n, plot in enumerate(view.plots, start=1):
            parent = self._top(tr("view.detail_plot", n=n, k=len(plot.series)))
            for s in plot.series:
                child = QTreeWidgetItem(parent, [s.variable])
                child.setIcon(0, _swatch(s.color))
                if s.variable in missing_series:
                    self._mark_missing(child)
            parent.setExpanded(True)
        if view.derived_variables:
            parent = self._top(tr("view.detail_derived", k=len(view.derived_variables)))
            for d in view.derived_variables:
                child = QTreeWidgetItem(parent, [f"{d.name} = {d.expression}"])
                if d.name in missing_derived:
                    self._mark_missing(child)
            parent.setExpanded(True)
        state = tr("view.state_on") if view.downsample_enabled else tr("view.state_off")
        self._top(tr("view.detail_downsample", state=state))

    def _top(self, text: str, red: bool = False) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        if red:
            item.setForeground(0, QBrush(_MISSING_COLOR))
        self.details.addTopLevelItem(item)
        return item

    @staticmethod
    def _mark_missing(item: QTreeWidgetItem) -> None:
        item.setText(0, item.text(0) + tr("view.detail_missing_suffix"))
        item.setForeground(0, QBrush(_MISSING_COLOR))
        item.setToolTip(0, tr("view.detail_missing_tooltip"))

    def _missing(self, view: view_io.View) -> tuple[set[str], set[str]]:
        """Series variables / derived names that `MainWindow.load_view` would skip,
        cascading the way a load does: a derived variable depending on a missing one
        is missing too (detailed_specification.md 20.2). Empty when no file is open."""
        if self._available is None:
            return set(), set()
        exprs = {d.name: d.expression for d in view.derived_variables}
        resolvable: set[str] = set()
        missing_derived: set[str] = set()
        for name in dependency_order(exprs):
            try:
                refs = referenced_names(exprs[name])
            except ExpressionError:
                missing_derived.add(name)
                continue
            if all(r in self._available or r in resolvable for r in refs):
                resolvable.add(name)
            else:
                missing_derived.add(name)
        known = self._available | resolvable
        missing_series = {s.variable for p in view.plots for s in p.series if s.variable not in known}
        return missing_series, missing_derived
