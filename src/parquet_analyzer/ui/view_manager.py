"""View management window (detailed_specification.md 20.2).

Lists saved views, shows what the selected one contains, and lets the user load or
delete it. Display-only: it never touches `MainWindow` state, loading is reported via
`accept()` + `selected_view_name()` and deletion via `viewDeleted`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from PySide6.QtCore import Signal
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

from .. import features
from ..core.view_check import Problem, classify_derived, effective_available
from ..io import view as view_io
from . import messages
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


def view_error_text(e: BaseException) -> str:
    """User-facing text for a view load failure (persistence 35): a `ViewFormatError` becomes
    "<item path>: <expected type> required (<actual type>)" translated; anything else (bad
    JSON, unreadable file, ...) keeps its own message."""
    if not isinstance(e, view_io.ViewFormatError):
        return str(e)
    path = tr("view.type_top") if e.path == "(top level)" else e.path
    if e.expected == "required":
        return tr("view.format_required", path=path)
    return tr(
        "view.format_wrong_type", path=path, expected=tr(f"view.type_{e.expected}"), actual=_actual_text(e.actual)
    )


def _actual_text(kind: str) -> str:
    key = f"view.type_{kind}"
    return tr(key) if kind in ("object", "list", "string", "number", "bool", "null") else kind


class ViewManagerDialog(QDialog):
    viewDeleted = Signal(str)
    # `exec()` result when "Export to Parquet..." was pressed (export 57.7.1): not Accepted (= load),
    # not Rejected; `selected_view_name()` is the view to export from.
    EXPORT_RESULT = 2

    def __init__(
        self,
        available_columns: set[str] | None,
        parent=None,
        all_columns: set[str] | None = None,
        available_functions: set[str] | None = None,
        registry=None,
        aliases: Mapping[str, str] | None = None,
        alias_names: set[str] | None = None,
    ) -> None:
        """`available_columns`: open file's columns minus the current time axis (None = no
        file open). `all_columns`: every column of the open file, so a view's saved time axis
        (persistence 33) can be checked and the available set recomputed for that axis.
        `available_functions`/`registry`: user functions and maps that exist now, so derived
        variables using a missing one (or a mismatched signature) are marked like load_view
        skips them (expressions 26.5/27.5). `aliases` (alias -> source column of the open file,
        persistence 54.5): `all_columns` then contains the aliases, and a view's time axis removes
        its source and all the aliases of it from the available set, as `load_view` does."""
        super().__init__(parent)
        self._aliases = dict(aliases or {})
        # every alias of the dictionary: a saved derived variable with such a name is skipped by load_view
        self._alias_names = set(alias_names or ())
        self._registry = registry  # also enables the arity / keyword check of calls (26.5)
        # Names of the callable user functions/maps; None = don't check (26.5, 27.5).
        self._functions = available_functions
        self._missing_function: dict[str, str] = {}  # derived name -> message for a call-related cause
        self.setWindowTitle(tr("view.manager_title"))
        self._available = available_columns
        self._all_columns = all_columns

        self.view_list = QListWidget()
        self.details = QTreeWidget()
        self.details.setHeaderHidden(True)
        self.details.setIndentation(16)
        self.load_button = QPushButton(tr("view.load_button"))
        self.delete_button = QPushButton(tr("view.delete_button"))
        self.export_button = QPushButton(tr("view.export_button"))
        self.export_button.setObjectName("view_export_button")
        self.export_button.setVisible(features.is_enabled("parquet_export"))  # developer feature flag (export 57.1)
        self.close_button = QPushButton(tr("view.close_button"))

        body = QHBoxLayout()
        body.addWidget(self.view_list, 1)
        body.addWidget(self.details, 3)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.export_button)
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
        self.export_button.clicked.connect(self.export_selected)
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

    def export_selected(self) -> None:
        """Hand the selected view to the Parquet export (57.7.1); works without an open file."""
        if self.export_button.isEnabled():
            self.done(self.EXPORT_RESULT)

    def delete_selected(self) -> None:
        name = self.selected_view_name()
        if name is None:
            return
        answer = messages.question(
            self, tr("view.manager_title"), tr("view.delete_confirm", name=name)
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            view_io.delete_view(name)
        except OSError as e:
            messages.warning(self, tr("view.manager_title"), tr("view.delete_failed", error=e))
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
        self.export_button.setEnabled(False)
        if name is None:
            self.details.addTopLevelItem(QTreeWidgetItem([tr("view.none_saved")]))
            return
        try:
            view = view_io.load_view(name)
            saved_at = view_io.view_saved_at(name)
            missing_series, missing_derived = self._missing(view)
        except Exception as e:  # noqa: BLE001 (shown in the details, not swallowed)
            item = QTreeWidgetItem([tr("view.detail_unreadable", error=view_error_text(e))])
            item.setForeground(0, QBrush(_MISSING_COLOR))
            self.details.addTopLevelItem(item)
            return
        self.load_button.setEnabled(True)
        self.export_button.setEnabled(True)

        n_missing = len(missing_series) + len(missing_derived) + (1 if self._time_column_missing(view) else 0)
        if n_missing:
            self._top(tr("view.detail_missing_summary", m=n_missing), red=True)
        src = self._top(tr("view.detail_source", path=view.source.parquet_path))
        src.setToolTip(0, tr("view.detail_source_tooltip", type=view.source.path_type))
        self._top(tr("view.detail_saved", time=saved_at.strftime("%Y-%m-%d %H:%M:%S")))
        if view.time_column is not None:
            tc = self._top(tr("view.detail_time_column", name=view.time_column))
            if self._time_column_missing(view):
                self._mark_missing(tc)
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
                    self._mark_missing(child, self._missing_function.get(d.name))
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
    def _mark_missing(item: QTreeWidgetItem, function: str | None = None) -> None:
        if function is not None:  # the cause is a function/map absent from the registry (26.5)
            item.setText(0, item.text(0) + " \u2014 " + function)
        else:
            item.setText(0, item.text(0) + tr("view.detail_missing_suffix"))
        item.setForeground(0, QBrush(_MISSING_COLOR))
        item.setToolTip(0, tr("view.detail_missing_tooltip"))

    def _time_column_missing(self, view: view_io.View) -> bool:
        """Same rule as `MainWindow.load_view`: a saved time column the open file lacks."""
        return (
            view.time_column is not None
            and self._all_columns is not None
            and view.time_column not in self._all_columns
        )

    def _effective_available(self, view: view_io.View) -> set[str]:
        """Variables `load_view` resolves against: after switching to the view's time axis
        that column is the axis (not a variable) and the current one is an ordinary variable."""
        if (
            view.time_column is not None
            and self._all_columns is not None
            and view.time_column in self._all_columns
        ):
            return effective_available(self._all_columns, self._aliases, view.time_column)
        return self._available

    def _missing(self, view: view_io.View) -> tuple[set[str], set[str]]:
        """Series variables / derived names that `MainWindow.load_view` would skip,
        cascading the way a load does: a derived variable depending on a missing one
        is missing too (detailed_specification.md 20.2). Empty when no file is open.
        The rule itself is `core.view_check.classify_derived`, shared with load_view and
        the Parquet export from a saved view (export 57.7.3)."""
        if self._available is None:
            return set(), set()
        available = self._effective_available(view)
        exprs = {d.name: d.expression for d in view.derived_variables}
        problems = classify_derived(
            exprs, available, alias_names=self._alias_names, registry=self._registry, functions=self._functions
        )
        self._missing_function = {}
        for name, problem in problems.items():
            if problem is not None and problem.kind in ("unknown_function", "call"):
                self._missing_function[name] = problem_text(problem)
        missing_derived = {n for n, p in problems.items() if p is not None}
        known = available | {n for n, p in problems.items() if p is None}
        missing_series = {s.variable for p in view.plots for s in p.series if s.variable not in known}
        return missing_series, missing_derived


def problem_text(problem: Problem) -> str:
    """User-facing reason for an unusable view item (shared by the view manager and the export
    dialog, 57.7.3)."""
    if problem.kind == "unknown_function":
        return tr("error.unknown_function", name=problem.detail)
    if problem.kind == "call":
        return problem.detail
    return tr(f"view.problem_{problem.kind}", name=problem.detail)
