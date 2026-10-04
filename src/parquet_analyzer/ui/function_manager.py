"""User-function manager window (detailed_specification.md 26.4).

Non-modal (so signals can be dragged in from the variable panel) and a singleton owned by
`MainWindow.open_function_manager()`. Left: the function list. Right: a "Definition" tab
(edit/save/delete) and an "Apply to signals" tab that builds `Fn(a, b[, k=v])` and hands it to
`MainWindow._on_add_derived_variable`. All registry changes go through
`MainWindow.update_registry`, which recomputes dependents all-or-nothing.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.expression import ExpressionError
from ..core.functions import FunctionRegistry, UserFunction, validate_user_function
from ..core.oplog import log_op
from . import messages
from .i18n import tr

_ERROR_STYLE = "color: #d32f2f;"
_OK_STYLE = "color: #2e7d32;"


class DropLineEdit(QLineEdit):
    """A line edit that takes a variable name dragged from the variable panel (plain text
    mime) and *replaces* its content with it (QLineEdit's own text drop would insert)."""

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        text = event.mimeData().text().strip()
        if text:
            self.setText(text)
            event.acceptProposedAction()
        else:
            event.ignore()


def format_constant(value: float) -> str:
    """Shortest faithful text for a constant: 60000 not 60000.0."""
    v = float(value)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def build_call_expression(fn: UserFunction, args: list[str], consts: dict[str, float]) -> str:
    """`Fn(a, b[, k=v ...])`; only constants differing from the default are written (26.4)."""
    parts = list(args)
    defaults = dict(fn.constants)
    for name, _ in fn.constants:
        if name in consts and not math.isclose(consts[name], float(defaults[name]), rel_tol=0, abs_tol=0):
            parts.append(f"{name}={format_constant(consts[name])}")
    return f"{fn.name}({', '.join(parts)})"


class FunctionManagerWindow(QDialog):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.setModal(False)
        self.setWindowTitle(tr("function.manager_title"))
        self.resize(820, 520)
        self._current: str | None = None  # name of the stored function being edited; None = new

        self.function_list = QListWidget()
        self.tabs = QTabWidget()
        self._build_definition_tab()
        self._build_apply_tab()
        self.tabs.addTab(self._definition_tab, tr("function.tab_definition"))
        self.tabs.addTab(self._apply_tab, tr("function.tab_apply"))

        body = QHBoxLayout(self)
        body.addWidget(self.function_list, 1)
        body.addWidget(self.tabs, 3)
        self.function_list.currentItemChanged.connect(self._on_selection_changed)
        self._refresh_list()

    # -- helpers ---------------------------------------------------------------------

    @property
    def _functions(self) -> dict[str, UserFunction]:
        return self._mw.function_registry.user_functions

    def _refresh_list(self, select: str | None = None) -> None:
        self.function_list.blockSignals(True)
        self.function_list.clear()
        for name in self._functions:
            self.function_list.addItem(name)
        self.function_list.blockSignals(False)
        if select is not None and select in self._functions:
            items = self.function_list.findItems(select, Qt.MatchFlag.MatchExactly)
            self.function_list.setCurrentItem(items[0])
        elif self.function_list.count():
            self.function_list.setCurrentRow(0)
        else:
            self._on_selection_changed()

    def _on_selection_changed(self, *_) -> None:
        item = self.function_list.currentItem()
        name = item.text() if item is not None else None
        self._load_definition(self._functions.get(name) if name else None)
        self._rebuild_apply_form()

    def selected_function(self) -> UserFunction | None:
        item = self.function_list.currentItem()
        return self._functions.get(item.text()) if item is not None else None

    # -- definition tab ----------------------------------------------------------------

    def _build_definition_tab(self) -> None:
        self._definition_tab = QWidget()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("function.name_placeholder"))
        self.params_edit = QLineEdit()
        self.params_edit.setPlaceholderText(tr("function.params_placeholder"))
        self.constants_table = QTableWidget(0, 2)
        self.constants_table.setHorizontalHeaderLabels([tr("function.const_name"), tr("function.const_default")])
        self.constants_table.horizontalHeader().setStretchLastSection(True)
        self.constants_table.setMaximumHeight(130)
        add_row = QPushButton(tr("function.add_constant"))
        del_row = QPushButton(tr("function.remove_constant"))
        add_row.clicked.connect(lambda: self.constants_table.insertRow(self.constants_table.rowCount()))
        del_row.clicked.connect(self._remove_constant_row)
        rows = QHBoxLayout()
        rows.addWidget(add_row)
        rows.addWidget(del_row)
        rows.addStretch(1)
        self.constants_hint = QLabel(tr("function.constants_hint"))
        self.constants_hint.setStyleSheet("color: gray;")
        self.expression_edit = QLineEdit()
        self.expression_edit.setPlaceholderText(tr("function.expression_placeholder"))
        self.expression_edit.setToolTip(tr("function.expression_tooltip"))
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText(tr("function.description_placeholder"))
        self.example_button = QPushButton(tr("function.insert_example"))
        self.example_button.clicked.connect(self.insert_example)
        self.new_button = QPushButton(tr("function.new"))
        self.save_button = QPushButton(tr("function.save"))
        self.delete_button = QPushButton(tr("function.delete"))
        self.new_button.clicked.connect(self.new_function)
        self.save_button.clicked.connect(self.save_function)
        self.delete_button.clicked.connect(self.delete_function)
        buttons = QHBoxLayout()
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.example_button)
        self.definition_message = QLabel("")
        self.definition_message.setWordWrap(True)

        form = QFormLayout()
        form.addRow(tr("function.name"), self.name_edit)
        form.addRow(tr("function.params"), self.params_edit)
        consts = QVBoxLayout()
        consts.addWidget(self.constants_table)
        consts.addLayout(rows)
        consts.addWidget(self.constants_hint)
        form.addRow(tr("function.constants"), consts)
        self.reference_button = QPushButton(tr("fnref.button"))
        self.reference_button.setToolTip(tr("fnref.button_tooltip"))
        self.reference_button.clicked.connect(self.open_reference)
        expr_row = QHBoxLayout()
        expr_row.addWidget(self.expression_edit, 1)
        expr_row.addWidget(self.reference_button)
        form.addRow(tr("function.expression"), expr_row)
        form.addRow(tr("function.description"), self.description_edit)
        layout = QVBoxLayout(self._definition_tab)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.definition_message)
        layout.addStretch(1)

    def open_reference(self) -> None:
        """Open the function reference inserting into this window's expression field (38)."""
        self._mw.open_function_reference(lambda: self.expression_edit if self.isVisible() else None)

    def _remove_constant_row(self) -> None:
        row = self.constants_table.currentRow()
        if row < 0:
            row = self.constants_table.rowCount() - 1
        if row >= 0:
            self.constants_table.removeRow(row)

    def _set_message(self, label: QLabel, text: str, ok: bool = False) -> None:
        label.setStyleSheet(_OK_STYLE if ok else _ERROR_STYLE)
        label.setText(text)

    def _load_definition(self, fn: UserFunction | None) -> None:
        self._current = fn.name if fn else None
        self.name_edit.setText(fn.name if fn else "")
        self.params_edit.setText(", ".join(fn.params) if fn else "")
        self.expression_edit.setText(fn.expression if fn else "")
        self.description_edit.setText(fn.description if fn else "")
        self.constants_table.setRowCount(0)
        for name, default in fn.constants if fn else []:
            r = self.constants_table.rowCount()
            self.constants_table.insertRow(r)
            self.constants_table.setItem(r, 0, QTableWidgetItem(name))
            self.constants_table.setItem(r, 1, QTableWidgetItem(format_constant(default)))
        self.definition_message.setText("")
        self.delete_button.setEnabled(fn is not None)

    def _definition_has_content(self) -> bool:
        if any(e.text().strip() for e in (self.name_edit, self.params_edit, self.expression_edit,
                                          self.description_edit)):
            return True
        for r in range(self.constants_table.rowCount()):
            for c in range(2):
                item = self.constants_table.item(r, c)
                if item is not None and item.text().strip():
                    return True
        return False

    def insert_example(self) -> bool:
        """Fill the definition fields with the example set (26.4, 2026-10-03). Never saves.

        Non-empty fields are replaced only after a confirmation. Like "New", it detaches
        from the stored function being edited, so a later Save creates a new function
        rather than silently renaming the selected one.
        """
        log_op("user_function_insert_example")
        if self._definition_has_content():
            answer = messages.question(self, tr("function.manager_title"), tr("function.example_confirm"))
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.new_function()
        self.name_edit.setText("Power_flow_by_press")
        self.params_edit.setText("flow_lpm, press_kpa")
        self.constants_table.insertRow(0)
        self.constants_table.setItem(0, 0, QTableWidgetItem("k"))
        self.constants_table.setItem(0, 1, QTableWidgetItem("60000"))
        self.expression_edit.setText("flow_lpm * press_kpa / k")
        self.description_edit.setText(tr("function.description_placeholder"))
        return True

    def new_function(self) -> None:
        self.function_list.blockSignals(True)
        self.function_list.clearSelection()
        self.function_list.setCurrentRow(-1)
        self.function_list.blockSignals(False)
        self._load_definition(None)
        self._rebuild_apply_form()

    def _read_form(self) -> UserFunction:
        """The form as a UserFunction (ExpressionError for unparsable input)."""
        params = [p.strip() for p in self.params_edit.text().replace("、", ",").split(",") if p.strip()]
        constants: list[tuple[str, float]] = []
        for r in range(self.constants_table.rowCount()):
            n_item, v_item = self.constants_table.item(r, 0), self.constants_table.item(r, 1)
            name = (n_item.text() if n_item else "").strip()
            text = (v_item.text() if v_item else "").strip()
            if not name and not text:
                continue  # an untouched blank row
            try:
                value = float(text)
            except ValueError:
                raise ExpressionError(tr("function.bad_default", name=name or "?", text=text)) from None
            constants.append((name, value))
        return UserFunction(
            name=self.name_edit.text().strip(),
            params=params,
            constants=constants,
            expression=self.expression_edit.text().strip(),
            description=self.description_edit.text().strip(),
        )

    def save_function(self) -> bool:
        """Validate and store the form (26.4). Errors show inline; returns success."""
        msg = self.definition_message
        old = self._current
        log_op("user_function_save", name=self.name_edit.text().strip(), replaces=old,
               expression=self.expression_edit.text().strip())
        try:
            fn = self._read_form()
            if not self._mw.user_functions_saveable:
                raise ExpressionError(tr("function.save_disabled"))
            registry = self._mw.function_registry
            if fn.name != old and fn.name in self._mw.reserved_names() - registry.all_names():
                raise ExpressionError(tr("function.reserved_name", name=fn.name))
            validate_user_function(fn, registry, replaces=old)
            if old is not None and fn.name != old:
                users = self._mw.callable_users(old)
                if users:
                    raise ExpressionError(tr("function.rename_in_use", name=old, users=", ".join(users)))
            # Rebuild the dict keeping order; renaming keeps the slot.
            new_functions: dict[str, UserFunction] = {}
            if old is None or old not in self._functions:
                new_functions.update(self._functions)
                new_functions[fn.name] = fn
            else:
                for n, f in self._functions.items():
                    if n == old:
                        new_functions[fn.name] = fn
                    else:
                        new_functions[n] = f
            # Every other function must still be valid against the new set (e.g. a caller
            # whose call no longer matches the changed signature).
            candidate = FunctionRegistry(new_functions, registry.maps)
            for n, f in new_functions.items():
                if n != fn.name:
                    try:
                        validate_user_function(f, candidate, replaces=n)
                    except ExpressionError as e:
                        raise ExpressionError(tr("function.breaks_function", name=n, reason=e)) from None
        except ExpressionError as e:
            log_op("user_function_save_refused", reason=str(e))
            self._set_message(msg, str(e))
            return False
        try:
            failures = self._mw.update_registry(
                user_functions=new_functions,
                touched={fn.name} | ({old} if old else set()),
                persist=lambda: self._mw.save_functions(new_functions),
            )
        except OSError as e:
            log_op("user_function_save_failed", reason=str(e))
            self._set_message(msg, tr("function.save_failed", error=e))
            return False
        if failures:
            log_op("user_function_save_rolled_back", failures=[n for n, _ in failures])
            self._set_message(msg, tr("function.rolled_back") + "\n" + "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list(select=fn.name)
        self._set_message(self.definition_message, tr("function.saved", name=fn.name), ok=True)
        return True

    def delete_function(self) -> bool:
        name = self._current
        if name is None or name not in self._functions:
            return False
        log_op("user_function_delete", name=name)
        users = self._mw.callable_users(name)
        if users:
            log_op("user_function_delete_refused", name=name, users=users)
            self._set_message(self.definition_message, tr("function.delete_in_use", name=name, users=", ".join(users)))
            return False
        if not self._mw.user_functions_saveable:
            self._set_message(self.definition_message, tr("function.save_disabled"))
            return False
        answer = messages.question(self, tr("function.manager_title"), tr("function.delete_confirm", name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return False
        remaining = {n: f for n, f in self._functions.items() if n != name}
        try:
            failures = self._mw.update_registry(
                user_functions=remaining,
                touched={name},
                persist=lambda: self._mw.save_functions(remaining),
            )
        except OSError as e:
            self._set_message(self.definition_message, tr("function.save_failed", error=e))
            return False
        if failures:
            self._set_message(self.definition_message, "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list()
        return True

    # -- apply tab -----------------------------------------------------------------------

    def _build_apply_tab(self) -> None:
        self._apply_tab = QWidget()
        self.apply_title = QLabel("")
        self.apply_form = QFormLayout()
        self.result_name_edit: QLineEdit | None = None  # created per selected function
        self.create_button = QPushButton(tr("function.create"))
        self.create_button.clicked.connect(self.apply_to_signals)
        self.apply_message = QLabel("")
        self.apply_message.setWordWrap(True)
        layout = QVBoxLayout(self._apply_tab)
        layout.addWidget(self.apply_title)
        layout.addLayout(self.apply_form)
        layout.addWidget(self.create_button)
        layout.addWidget(self.apply_message)
        layout.addStretch(1)
        self._param_fields: list[DropLineEdit] = []
        self._const_fields: dict[str, QLineEdit] = {}

    def _rebuild_apply_form(self) -> None:
        while self.apply_form.rowCount():
            self.apply_form.removeRow(0)
        self._param_fields = []
        self._const_fields = {}
        self.apply_message.setText("")
        fn = self.selected_function()
        self.create_button.setEnabled(fn is not None)
        if fn is None:
            self.apply_title.setText(tr("function.apply_select"))
            return
        self.apply_title.setText(f"{fn.name}  —  {fn.description}" if fn.description else fn.name)
        for p in fn.params:
            field = DropLineEdit()
            field.setPlaceholderText(tr("function.drop_here"))
            self._param_fields.append(field)
            self.apply_form.addRow(p, field)
        for cname, default in fn.constants:
            field = QLineEdit(format_constant(default))
            self._const_fields[cname] = field
            self.apply_form.addRow(cname, field)
        self.result_name_edit = QLineEdit()
        self.apply_form.addRow(tr("function.result_name"), self.result_name_edit)

    def apply_to_signals(self) -> bool:
        """Build `Fn(args[, k=v])` and create the derived variable through the normal path."""
        fn = self.selected_function()
        if fn is None:
            return False
        args = [f.text().strip() for f in self._param_fields]
        result = self.result_name_edit.text().strip()
        log_op("user_function_apply", name=fn.name, args=args, result=result)
        try:
            if any(not a for a in args):
                raise ExpressionError(tr("function.args_missing"))
            consts: dict[str, float] = {}
            for cname, field in self._const_fields.items():
                try:
                    consts[cname] = float(field.text())
                except ValueError:
                    raise ExpressionError(tr("function.bad_default", name=cname, text=field.text())) from None
            if not result:
                raise ExpressionError(tr("function.result_missing"))
            if result in self._mw._file_columns or result in self._mw._derived:
                raise ExpressionError(tr("error.duplicate_variable_name", name=result))
            expression = build_call_expression(fn, args, consts)
            self._mw._on_add_derived_variable(result, expression)
        except ExpressionError as e:
            log_op("user_function_apply_refused", reason=str(e))
            self._set_message(self.apply_message, str(e))
            return False
        self._set_message(self.apply_message, tr("function.created", name=result, expression=expression), ok=True)
        return True
