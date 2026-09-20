from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QWidget

from ..core.expression import ExpressionError
from .i18n import tr


class ExpressionBar(QWidget):
    """Text input for deriving a new variable from an expression
    (specification.md 5.5, detailed_specification.md 6章).
    """

    def __init__(self, evaluate_callback, parent: QWidget | None = None):
        super().__init__(parent)
        self._evaluate_callback = evaluate_callback

        self.name_edit = QLineEdit(placeholderText=tr("expr.name_placeholder"))
        self.expr_edit = QLineEdit(placeholderText=tr("expr.expr_placeholder"))
        add_button = QPushButton(tr("expr.add_button"))
        add_button.clicked.connect(self._on_add_clicked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.name_edit, 1)
        layout.addWidget(self.expr_edit, 3)
        layout.addWidget(add_button)

    def insert_variable_name(self, name: str) -> None:
        self.expr_edit.insert(name)
        self.expr_edit.setFocus()

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
            return
        self.name_edit.clear()
        self.expr_edit.clear()
