"""Function reference window (detailed_specification.md 38, requirement 5.31).

A non-modal singleton owned by `MainWindow.open_function_reference()`. The list is built from
the live function registry (`_ALLOWED_FUNCS` for builtins, `FunctionRegistry.user_functions`,
`FunctionRegistry.maps`) -- nothing is listed by hand except the *documentation* of each
builtin (`BUILTIN_DOCS` + the `fnref.desc.<name>` i18n strings). A test fails when a builtin
has no documentation, so adding one to `_ALLOWED_FUNCS` cannot silently leave it undocumented.

"Insert into expression" writes the example at the cursor of the line edit the window was
opened from (`target_provider`; the expression bar or the function manager's expression field).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.expression import _ALLOWED_FUNCS
from ..core.oplog import log_op
from .function_manager import build_call_expression
from .i18n import tr

# builtin name -> (category key, signature, example). The example is language-neutral and uses
# the column names of `feature_test.parquet` (sine, cosine, ...) so it evaluates as written.
BUILTIN_DOCS: dict[str, tuple[str, str, str]] = {
    "abs": ("basic", "abs(a)", "abs(sine)"),
    "sqrt": ("basic", "sqrt(a)", "sqrt(offset_sine)"),
    "delta": ("analysis", "delta(a)", "delta(sine)"),
    "rolling_mean": ("analysis", "rolling_mean(a, n)", "rolling_mean(sine, 20)"),
    "rolling_nanmean": ("analysis", "rolling_nanmean(a, n)", "rolling_nanmean(with_nan, 20)"),
    "sin": ("trig", "sin(a)", "sin(sine)"),
    "cos": ("trig", "cos(a)", "cos(cosine)"),
    "tan": ("trig", "tan(a)", "tan(sine)"),
    "exp": ("explog", "exp(a)", "exp(sine)"),
    "log": ("explog", "log(a)", "log(offset_sine)"),
    "log10": ("explog", "log10(a)", "log10(offset_sine)"),
    "sign": ("round", "sign(a)", "sign(sine)"),
    "floor": ("round", "floor(a)", "floor(offset_sine)"),
    "ceil": ("round", "ceil(a)", "ceil(offset_sine)"),
    "round": ("round", "round(a)", "round(offset_sine)"),
    "min": ("compare", "min(a, b)", "min(sine, cosine)"),
    "max": ("compare", "max(a, b)", "max(sine, 0)"),
    "clip": ("compare", "clip(x, lo, hi)", "clip(sine, -0.5, 0.5)"),
}

# operator -> (description key suffix, example)
OPERATORS: dict[str, tuple[str, str]] = {
    "+": ("add", "sine + cosine"),
    "-": ("sub", "offset_sine - sine"),
    "*": ("mul", "sine * 2"),
    "/": ("div", "sine / 2"),
    "**": ("pow", "sine ** 2"),
    "%": ("mod", "ramp % 60"),
    "//": ("floordiv", "ramp // 60"),
}

_BUILTIN_CATEGORIES = ("basic", "analysis", "trig", "explog", "round", "compare")
_ALL_CATEGORIES = _BUILTIN_CATEGORIES + ("operator", "user", "curve", "map")


@dataclass(frozen=True)
class ReferenceEntry:
    category: str  # one of _ALL_CATEGORIES
    name: str
    signature: str
    description: str
    example: str


def missing_builtin_docs() -> list[str]:
    """Builtins in `_ALLOWED_FUNCS` lacking a BUILTIN_DOCS entry or an en/ja description."""
    from .i18n import _STRINGS  # noqa: PLC0415 (private on purpose: checks both languages)

    bad = []
    for name in _ALLOWED_FUNCS:
        entry = _STRINGS.get(f"fnref.desc.{name}", {})
        if name not in BUILTIN_DOCS or not entry.get("en") or not entry.get("ja"):
            bad.append(name)
    return bad


def build_entries(registry) -> list[ReferenceEntry]:
    """All reference rows for the registry's current content, in display order."""
    entries: list[ReferenceEntry] = []
    for name in _ALLOWED_FUNCS:
        if name not in BUILTIN_DOCS:
            # still listed (the registry is the source), just without curated docs
            entries.append(ReferenceEntry("basic", name, f"{name}(...)", "", f"{name}()"))
            continue
        cat, sig, example = BUILTIN_DOCS[name]
        entries.append(ReferenceEntry(cat, name, sig, tr(f"fnref.desc.{name}"), example))
    for op, (key, example) in OPERATORS.items():
        entries.append(ReferenceEntry("operator", op, f"a {op} b", tr(f"fnref.op.{key}"), example))
    for name, fn in registry.user_functions.items():
        sig = f"{name}({', '.join([*fn.params, *(f'{c}={_fmt(v)}' for c, v in fn.constants)])})"
        entries.append(
            ReferenceEntry("user", name, sig, fn.description or tr("fnref.user_no_description"),
                           build_call_expression(fn, list(fn.params), {}))
        )
    for name, table in registry.maps.items():
        is_curve = table.kind == "curve"
        args = "x" if is_curve else "x, y"
        desc = tr(
            "fnref.map_desc",
            kind=tr("map.kind_curve") if is_curve else tr("map.kind_map"),
            interp=tr(f"map.interp_{table.interpolation}"),
            extrap=tr(f"map.extrap_{table.extrapolation}"),
        )
        entries.append(ReferenceEntry("curve" if is_curve else "map", name, f"{name}({args})", desc, f"{name}({args})"))
    return entries


def _fmt(value: float) -> str:
    from .function_manager import format_constant  # noqa: PLC0415

    return format_constant(value)


class FunctionReferenceDialog(QDialog):
    def __init__(self, main_window, target_provider: Callable[[], QLineEdit | None] | None = None) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self._target_provider = target_provider
        self._entries: list[ReferenceEntry] = []
        self.setObjectName("function_reference_dialog")
        self.setModal(False)
        self.setWindowTitle(tr("fnref.title"))
        self.resize(1080, 560)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("fnref.search_placeholder"))
        self.search_edit.setClearButtonEnabled(True)
        self.category_combo = QComboBox()
        self.category_combo.addItem(tr("fnref.cat_all"), None)
        for cat in _ALL_CATEGORIES:
            self.category_combo.addItem(tr(f"fnref.cat_{cat}"), cat)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("fnref.col_name"), tr("fnref.col_category"), tr("fnref.col_signature"),
             tr("fnref.col_description"), tr("fnref.col_example")]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 340)
        self.insert_button = QPushButton(tr("fnref.insert"))
        self.notes_label = QLabel(tr("fnref.notes"))
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet("color: gray;")

        top = QHBoxLayout()
        top.addWidget(self.search_edit, 1)
        top.addWidget(QLabel(tr("fnref.category")))
        top.addWidget(self.category_combo)
        bottom = QHBoxLayout()
        bottom.addWidget(self.notes_label, 1)
        bottom.addWidget(self.insert_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.category_combo.currentIndexChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._update_insert_enabled)
        self.table.cellDoubleClicked.connect(lambda *_: self.insert_selected())
        self.insert_button.clicked.connect(self.insert_selected)
        self.refresh()

    # -- content -----------------------------------------------------------------------

    def set_target_provider(self, provider: Callable[[], QLineEdit | None] | None) -> None:
        self._target_provider = provider
        self._update_insert_enabled()

    def refresh(self) -> None:
        """Rebuild from the registry (called when user functions / maps change, 38)."""
        selected = self.selected_entry()
        self._entries = build_entries(self._mw.function_registry)
        self._apply_filter()
        if selected is not None:
            for r in range(self.table.rowCount()):
                if self._row_entry(r) == selected:
                    self.table.selectRow(r)
                    break

    def _matches(self, e: ReferenceEntry) -> bool:
        cat = self.category_combo.currentData()
        if cat is not None and e.category != cat:
            return False
        q = self.search_edit.text().strip().casefold()
        return not q or q in e.name.casefold() or q in e.description.casefold()

    def _apply_filter(self, *_) -> None:
        shown = [e for e in self._entries if self._matches(e)]
        self.table.setRowCount(len(shown))
        for r, e in enumerate(shown):
            cells = (e.name, tr(f"fnref.cat_{e.category}"), e.signature, e.description, e.example)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, e)
                self.table.setItem(r, c, item)
        self.table.resizeRowsToContents()
        self._update_insert_enabled()

    def _row_entry(self, row: int) -> ReferenceEntry | None:
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def visible_names(self) -> list[str]:
        return [self._row_entry(r).name for r in range(self.table.rowCount())]

    def selected_entry(self) -> ReferenceEntry | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self._row_entry(rows[0].row()) if rows else None

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._update_insert_enabled()  # the source window may have been closed meanwhile

    # -- insert --------------------------------------------------------------------------

    def _target(self) -> QLineEdit | None:
        if self._target_provider is None:
            return None
        try:
            t = self._target_provider()
            if t is not None:
                t.isEnabled()  # raises RuntimeError when the C++ widget is gone
            return t
        except RuntimeError:
            return None

    def _update_insert_enabled(self) -> None:
        has_target = self._target() is not None
        self.insert_button.setEnabled(has_target and self.selected_entry() is not None)
        self.insert_button.setToolTip("" if has_target else tr("fnref.insert_disabled_tooltip"))

    def insert_selected(self) -> bool:
        """Insert the selected entry's example at the cursor of the source field (38)."""
        entry = self.selected_entry()
        target = self._target()
        if entry is None or target is None:
            self._update_insert_enabled()
            return False
        log_op("function_reference_insert", name=entry.name)
        target.insert(entry.example)
        target.setFocus()
        return True
