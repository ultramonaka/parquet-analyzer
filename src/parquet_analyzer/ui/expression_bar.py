from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QWidget

from ..core.expression import ExpressionError
from .i18n import tr


class ExpressionBar(QWidget):
    """Text input for deriving a new variable from an expression
    (specification.md 5.5, detailed_specification.md 6章), and for editing an
    existing derived variable's expression in place (18.3 — deliberately not a
    modal dialog, so variable names can still be dragged/double-click-inserted
    from the variable list while editing).
    """

    def __init__(self, evaluate_callback, parent: QWidget | None = None):
        super().__init__(parent)
        self._evaluate_callback = evaluate_callback
        self._editing: str | None = None

        self.name_edit = QLineEdit(placeholderText=tr("expr.name_placeholder"))
        self.expr_edit = QLineEdit(placeholderText=tr("expr.expr_placeholder"))
        self.add_button = QPushButton(tr("expr.add_button"))
        self.add_button.clicked.connect(self._on_add_clicked)
        self.cancel_button = QPushButton(tr("expr.cancel_button"))
        self.cancel_button.clicked.connect(self.cancel_edit)
        self.cancel_button.hide()
        cancel_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.expr_edit)
        cancel_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        cancel_shortcut.activated.connect(self.cancel_edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.name_edit, 1)
        layout.addWidget(self.expr_edit, 3)
        layout.addWidget(self.add_button)
        layout.addWidget(self.cancel_button)

    @property
    def editing_name(self) -> str | None:
        return self._editing

    def insert_variable_name(self, name: str) -> None:
        self.expr_edit.insert(name)
        self.expr_edit.setFocus()

    def start_edit(self, name: str, expression: str) -> None:
        self._editing = name
        self.name_edit.setText(name)
        self.name_edit.setReadOnly(True)
        self.expr_edit.setText(expression)
        self.add_button.setText(tr("expr.update_button"))
        self.cancel_button.show()
        self.expr_edit.setFocus()

    def cancel_edit(self) -> None:
        if self._editing is None:
            return
        self._reset()

    def _reset(self) -> None:
        self._editing = None
        self.name_edit.setReadOnly(False)
        self.name_edit.clear()
        self.expr_edit.clear()
        self.add_button.setText(tr("expr.add_button"))
        self.cancel_button.hide()

    def _on_add_clicked(self) -> None:
        name = self.name_edit.text().strip()
        expr = self.expr_edit.text().strip()
        if not name or not expr:
            QMessageBox.warning(self, tr("expr.warning_title"), tr("expr.warning_body"))
            return
        try:
            self._evaluate_callback(name, expr)
        except ExpressionError as e:
            QMessageBox.warning(self, tr("expr.error_title"), str(e))
            return  # stays in edit mode (if editing) with the user's text intact
        self._reset()
