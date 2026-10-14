"""The "Contour levels..." dialog of the XY window's overlay menu (analysis.ja.md 44.7).

Auto / interval / list of values. A bad input (not a number, a step <= 0, more than 200 lines for one
overlaid map) shows a translated error inside the dialog, which stays open.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
)

from ..core import contour_levels as cl
from ..core.contour_levels import ContourLevelError, LevelSpec
from .i18n import tr
from .xy_overlay import levels_error_text


class ContourLevelsDialog(QDialog):
    """`ranges` = name -> (min, max) of the currently overlaid maps, used to refuse specs giving too
    many lines. `spec()` is the accepted value."""

    def __init__(self, spec: LevelSpec, ranges: dict[str, tuple[float, float]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("contour_levels_dialog")
        self.setWindowTitle(tr("xy.levels_title"))
        self._ranges = dict(ranges)
        self._spec = spec
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        lay.addLayout(grid)
        self._buttons = {}
        group = QButtonGroup(self)
        for row, mode in enumerate(cl.MODES):
            b = QRadioButton(tr(f"xy.levels_{mode}"))
            b.setObjectName(f"contour_levels_{mode}")
            group.addButton(b)
            grid.addWidget(b, row, 0)
            self._buttons[mode] = b
        self.step_edit = QLineEdit("" if spec.step is None else f"{spec.step:g}")
        self.step_edit.setObjectName("contour_levels_step_edit")
        self.list_edit = QLineEdit(", ".join(f"{v:g}" for v in spec.values))
        self.list_edit.setObjectName("contour_levels_list_edit")
        self.list_edit.setPlaceholderText("0.8, 0.9, 0.95")
        grid.addWidget(QLabel(tr("xy.levels_auto_hint")), 0, 1)
        grid.addWidget(self.step_edit, 1, 1)
        grid.addWidget(self.list_edit, 2, 1)
        self._buttons[spec.mode if spec.mode in self._buttons else "auto"].setChecked(True)
        self.error_label = QLabel("")
        self.error_label.setObjectName("contour_levels_error")
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.setWordWrap(True)
        lay.addWidget(self.error_label)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._on_ok)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.button_box = box

    def mode(self) -> str:
        return next(m for m, b in self._buttons.items() if b.isChecked())

    def set_mode(self, mode: str) -> None:
        self._buttons[mode].setChecked(True)

    def spec(self) -> LevelSpec:
        return self._spec

    def _read(self) -> LevelSpec:
        """The spec of the fields; raises `ContourLevelError`."""
        mode = self.mode()
        if mode == "step":
            spec = LevelSpec("step", step=cl.parse_step(self.step_edit.text()))
        elif mode == "list":
            spec = LevelSpec("list", values=cl.parse_values(self.list_edit.text()))
        else:
            spec = LevelSpec("auto")
        cl.plan_levels(spec, self._ranges)  # refuses too many lines for one map
        return spec

    def _on_ok(self) -> None:
        try:
            spec = self._read()
        except ContourLevelError as exc:
            self.error_label.setText(levels_error_text(exc))  # stays open
            return
        self.error_label.setText("")
        self._spec = spec
        self.accept()
