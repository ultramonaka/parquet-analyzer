"""Map (lookup table) manager window (detailed_specification.md 27.4).

Non-modal singleton owned by `MainWindow.open_map_manager()`. Import / list / settings /
re-import / delete, with a preview (table, plus a line for curves or heat maps for maps, the
interpolated result drawn over/next to the raw data). Every change that affects the
registry goes through `MainWindow.update_registry` (all-or-nothing recompute of dependents).

For a 2-input map a "3D" toggle shows the table as a rotatable surface via the 25.3
component (`Xyz3DView.set_surface`); it exists only when the `xyz_3d` flag is on and the
OpenGL probe has not failed, and the probe itself runs on the first press.

While the value table has unsaved edits the two plots become "Before" (saved values) and
"After" (live from the edited cells) on one shared colour range; with the 3D toggle on they
are two `Xyz3DView`s whose cameras are linked (27.9).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import features
from ..core.maps import EXTRAPOLATIONS, INTERPOLATIONS, LookupTable, normalize_axis_names
from ..core.oplog import log_op
from ..io import maps as maps_io
from ..io.settings import normalize_splitter_sizes, save_settings
from . import messages
from .frozen_table import FrozenAxesTable
from .i18n import tr

_ERROR_STYLE = "color: #d32f2f;"
_OK_STYLE = "color: #2e7d32;"
_EDITED_BRUSH = QBrush(QColor("#ffcc80"))  # a cell changed since the last save (27.8)
_AXIS_BRUSH = QBrush(QColor("#cfd8dc"))  # axis values of a map (first row / column)
_BLACK = QBrush(QColor("#000000"))
_FILLED_BRUSH = QBrush(QColor("#8a8a8a"))  # a cell that was filled in, not measured (44.4)
_PREVIEW_PAD = 0.10  # the interpolated preview extends 10% beyond the table (27.4)


def default_map_name(path: str | Path) -> str:
    """The file's stem made a Python identifier (27.4): other characters -> `_`, a leading
    digit gets a `_` prefix, empty -> `map`."""
    stem = re.sub(r"\W", "_", Path(path).stem, flags=re.ASCII)
    if not stem or set(stem) == {"_"}:
        stem = "map"
    if stem[0].isdigit():
        stem = "_" + stem
    return stem


def interpolated_preview(table: LookupTable, n: int = 400):
    """The table evaluated on a fine grid extending 10% beyond its range (27.4).
    Curve -> `(xs, ys)`; map -> `(xs, ys, z)` with z shaped `(len(ys), len(xs))`."""

    def span(a: np.ndarray, k: int) -> np.ndarray:
        pad = (a[-1] - a[0]) * _PREVIEW_PAD
        return np.linspace(a[0] - pad, a[-1] + pad, k)

    if table.kind == "curve":
        xs = span(table.x, n)
        return xs, table(xs)
    xs, ys = span(table.x, 120), span(table.y, 120)
    gx, gy = np.meshgrid(xs, ys)
    return xs, ys, table(gx, gy)


def _label(prefix: str, key: str) -> str:
    """Translated label for a registry key; a key without an i18n entry shows as itself, so a
    newly added interpolation/extrapolation works before its strings exist (27.1)."""
    try:
        return tr(f"{prefix}{key}")
    except KeyError:
        return key


def cell_edges(axis: np.ndarray) -> np.ndarray:
    """Cell boundaries for a (possibly non-uniform) grid axis: midpoints between neighbours,
    the first/last cell extended by half of its neighbouring spacing (27.4)."""
    a = np.asarray(axis, dtype=np.float64)
    mid = (a[:-1] + a[1:]) / 2
    return np.concatenate([[a[0] - (a[1] - a[0]) / 2], mid, [a[-1] + (a[-1] - a[-2]) / 2]])


class MapImportDialog(QDialog):
    """File + name + kind + interpolation + extrapolation (27.4)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("map.import_title"))
        self.file_edit = QLineEdit()
        browse = QPushButton(tr("map.browse"))
        browse.clicked.connect(self._browse)
        self.name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        for key in ("auto", "curve", "map"):
            self.kind_combo.addItem(tr(f"map.kind_{key}"), key)
        self.interp_combo = QComboBox()
        self.extrap_combo = QComboBox()
        for key in EXTRAPOLATIONS:
            self.extrap_combo.addItem(_label("map.extrap_", key), key)
        self.kind_combo.currentIndexChanged.connect(self._refill_interpolations)
        self._refill_interpolations()
        row = QHBoxLayout()
        row.addWidget(self.file_edit, 1)
        row.addWidget(browse)
        form = QFormLayout()
        form.addRow(tr("map.file"), row)
        form.addRow(tr("map.name"), self.name_edit)
        form.addRow(tr("map.kind"), self.kind_combo)
        form.addRow(tr("map.interpolation"), self.interp_combo)
        form.addRow(tr("map.extrapolation"), self.extrap_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _refill_interpolations(self) -> None:
        """`spline` only exists for curves, so it is hidden once "map" is chosen (27.4)."""
        kind = self.kind_combo.currentData()
        previous = self.interp_combo.currentData()
        self.interp_combo.clear()
        for key, spec in INTERPOLATIONS.items():
            if kind != "map" or "map" in spec.kinds:
                self.interp_combo.addItem(_label("map.interp_", key), key)
        idx = self.interp_combo.findData(previous)
        self.interp_combo.setCurrentIndex(max(idx, 0))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("map.import_title"), "", tr("map.csv_filter"))
        if path:
            self.set_file(path)

    def set_file(self, path: str) -> None:
        self.file_edit.setText(path)
        self.name_edit.setText(default_map_name(path))

    def values(self) -> dict:
        return {
            "path": self.file_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "kind": self.kind_combo.currentData(),
            "interpolation": self.interp_combo.currentData(),
            "extrapolation": self.extrap_combo.currentData(),
        }


class MapEditDialog(QDialog):
    """Name + axis names (X/Y/Z; a curve has X/Y only) of an existing map (27.7)."""

    def __init__(self, table: LookupTable, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("map.edit_title"))
        self.name_edit = QLineEdit(table.name)
        self.axis_edits = [QLineEdit(n) for n in table.axis_names]
        form = QFormLayout()
        form.addRow(tr("map.name"), self.name_edit)
        form.addRow(QLabel(tr("map.axis_names")))
        for key, edit in zip(("x", "y", "z"), self.axis_edits):
            if key == "z" and table.kind == "curve":
                edit.setVisible(False)
                continue
            form.addRow(tr(f"map.axis_{key}"), edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {"name": self.name_edit.text().strip(), "axis_names": tuple(e.text().strip() for e in self.axis_edits)}


class MapManagerWindow(QDialog):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.setModal(False)
        self.setWindowTitle(tr("map.manager_title"))
        self.resize(980, 620)

        self.map_list = QListWidget()
        self.import_button = QPushButton(tr("map.import_button"))
        self.reimport_button = QPushButton(tr("map.reimport_button"))
        self.edit_button = QPushButton(tr("map.edit_button"))
        self.delete_button = QPushButton(tr("map.delete_button"))
        self.edit_button.clicked.connect(self.edit_selected)
        self.import_button.clicked.connect(self._on_import_clicked)
        self.reimport_button.clicked.connect(self.reimport_selected)
        self.delete_button.clicked.connect(self.delete_selected)

        self.interp_combo = QComboBox()
        self.extrap_combo = QComboBox()
        self.apply_button = QPushButton(tr("map.apply_settings"))
        self.apply_button.clicked.connect(self.apply_settings)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.preview_table = FrozenAxesTable()  # first row / column stay visible for maps (27.11)
        self.preview_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.save_values_button = QPushButton(tr("map.values_save"))
        self.revert_button = QPushButton(tr("map.values_revert"))
        self.save_values_button.clicked.connect(self.save_values)
        self.revert_button.clicked.connect(self.revert_values)
        self.save_values_button.setEnabled(False)
        self.revert_button.setEnabled(False)
        # edit state of the shown table (27.8): original cell texts + cells that differ now
        self._shown_name: str | None = None
        self._orig_text: dict[tuple[int, int], str] = {}
        self._preview_kind: str | None = None  # "curve" | "map": what the table currently shows
        self._filled_cells: set[tuple[int, ...]] = set()  # value cells filled in rather than measured (44.4)
        self._changed: set[tuple[int, int]] = set()
        self._loading = False
        self.preview_table.itemChanged.connect(self._on_item_changed)
        self.plot_raw = pg.PlotWidget()
        self.plot_result = pg.PlotWidget()  # only used for maps: the interpolated heat map
        self._connect_plot_clicks()
        self.view3d = None  # Xyz3DView while the 3D toggle is on (27.4 / 25.3); "Before" while editing
        self.view3d_after = None  # the "After" Xyz3DView while editing in 3D (27.9)
        self._plots_mode: str | None = None  # "curve" | "heat" | "3d" | "3d_pair": what the plot area holds
        self.compare_error: str = ""  # why "After" is not drawn (invalid edited values), 27.9
        self.toggle_3d = QPushButton(tr("xyz.map_3d"))
        self.toggle_3d.setCheckable(True)
        self.toggle_3d.setToolTip(tr("xyz.map_3d_tooltip"))
        self.toggle_3d.setVisible(False)
        self.toggle_3d.toggled.connect(self._on_3d_toggled)
        self._plots_layout = QHBoxLayout()

        form = QFormLayout()
        form.addRow(tr("map.interpolation"), self.interp_combo)
        form.addRow(tr("map.extrapolation"), self.extrap_combo)
        settings_row = QHBoxLayout()
        settings_row.addLayout(form, 1)
        settings_row.addWidget(self.apply_button)
        settings_row.addWidget(self.toggle_3d)
        plots = self._plots_layout
        plots.addWidget(self.plot_raw, 1)
        plots.addWidget(self.plot_result, 1)
        right = QVBoxLayout()
        right.addLayout(settings_row)
        right.addWidget(self.info_label)
        right.addWidget(self.message)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self.save_values_button)
        edit_row.addWidget(self.revert_button)
        edit_row.addStretch(1)
        table_box = QWidget()
        tb = QVBoxLayout(table_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.addLayout(edit_row)
        tb.addWidget(self.preview_table, 1)
        plots_box = QWidget()
        plots.setContentsMargins(0, 0, 0, 0)
        plots_box.setLayout(plots)
        # draggable boundary between the table and the graphs; the ratio is a setting (27.8)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("map_manager_splitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(table_box)
        self.splitter.addWidget(plots_box)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        saved = normalize_splitter_sizes(getattr(self._mw.settings, "map_manager_splitter", None))
        self.splitter.setSizes(saved or [240, 360])
        self._splitter_timer = QTimer(self)
        self._splitter_timer.setSingleShot(True)
        self._splitter_timer.setInterval(400)
        self._splitter_timer.timeout.connect(self._save_splitter)
        self.splitter.splitterMoved.connect(lambda *_: self._splitter_timer.start())
        right.addWidget(self.splitter, 1)
        left = QVBoxLayout()
        left.addWidget(self.map_list, 1)
        left.addWidget(self.import_button)
        left.addWidget(self.reimport_button)
        left.addWidget(self.edit_button)
        left.addWidget(self.delete_button)
        body = QHBoxLayout(self)
        body.addLayout(left, 1)
        body.addLayout(right, 4)

        self.map_list.currentItemChanged.connect(self._on_selection_changed)
        self.map_list.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        self._refresh_list()

    # -- helpers ---------------------------------------------------------------------

    @property
    def _tables(self) -> dict[str, LookupTable]:
        return self._mw.function_registry.maps

    def _say(self, text: str, ok: bool = False) -> None:
        self.message.setStyleSheet(_OK_STYLE if ok else _ERROR_STYLE)
        self.message.setText(text)

    def selected_name(self) -> str | None:
        item = self.map_list.currentItem()
        return item.text().split("  ")[0] if item is not None else None

    def _refresh_list(self, select: str | None = None) -> None:
        self.map_list.blockSignals(True)
        self.map_list.clear()
        for name, t in sorted(self._tables.items()):
            self.map_list.addItem(f"{name}  ({tr('map.kind_' + t.kind)})")
        self.map_list.blockSignals(False)
        names = sorted(self._tables)
        if select in names:
            self.map_list.setCurrentRow(names.index(select))
        elif names:
            self.map_list.setCurrentRow(0)
        else:
            self._on_selection_changed()

    def _fill_combos(self, table: LookupTable | None) -> None:
        self.interp_combo.clear()
        self.extrap_combo.clear()
        if table is None:
            return
        for key, spec in INTERPOLATIONS.items():
            if table.kind in spec.kinds:  # `spline` is not offered for maps
                self.interp_combo.addItem(_label("map.interp_", key), key)
        for key in EXTRAPOLATIONS:
            self.extrap_combo.addItem(_label("map.extrap_", key), key)
        self.interp_combo.setCurrentIndex(self.interp_combo.findData(table.interpolation))
        self.extrap_combo.setCurrentIndex(self.extrap_combo.findData(table.extrapolation))

    def _on_selection_changed(self, *_) -> None:
        name = self.selected_name()
        if self._changed and name != self._shown_name and not self._confirm_unsaved():
            # stay on the map being edited
            self.map_list.blockSignals(True)
            names = sorted(self._tables)
            if self._shown_name in names:
                self.map_list.setCurrentRow(names.index(self._shown_name))
            self.map_list.blockSignals(False)
            return
        if name is not None and name != self._shown_name and self.selected_name() != name:
            # the confirmation saved the old map, which re-selected it: go to the wanted one
            self.map_list.blockSignals(True)
            self.map_list.setCurrentRow(sorted(self._tables).index(name))
            self.map_list.blockSignals(False)
        table = self._tables.get(name) if name else None
        self._fill_combos(table)
        for w in (self.apply_button, self.reimport_button, self.edit_button, self.delete_button):
            w.setEnabled(table is not None)
        self.message.setText("")
        self._show_preview(table)
        self._shown_name = name if table is not None else None

    # -- preview -----------------------------------------------------------------------

    def _show_preview(self, table: LookupTable | None) -> None:
        # Fresh plot widgets each time: a heat map's colour bar lives in the plot's layout and
        # would otherwise linger (and fight over its grid cell) when switching maps/curves.
        self._reset_plots(table is not None and table.kind == "map")
        self._loading = True
        self._orig_text.clear()
        self._changed.clear()
        self._filled_cells = set()
        self._preview_kind = table.kind if table is not None else None
        self._update_edit_buttons()
        self._update_3d_toggle(table)
        self.preview_table.clear()
        if table is None:
            self._loading = False
            self.preview_table.set_frozen(False)
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            self.info_label.setText(tr("map.none_selected"))
            return
        try:
            info = next((i for i in maps_io.list_maps() if i.name == table.name), None)
        except Exception:  # noqa: BLE001
            info = None
        self._filled_cells = {tuple(c) for c in (info.filled if info else [])}
        text = tr("map.info", source=info.source_path if info else "?", imported=info.imported_at if info else "?")
        if self._filled_cells:
            text += "  " + tr("map.info_filled", n=len(self._filled_cells))
        self.info_label.setText(text)
        if table.kind == "curve":
            self._preview_curve(table)
        else:
            self._preview_map(table)
        self._loading = False
        self.compare_error = ""
        self._draw_plots(table, None)

    def _reset_plots(self, two_plots: bool) -> None:
        """Replace every plot widget by fresh ones (and drop the 3D views)."""
        for old in (self.plot_raw, self.plot_result, self.view3d, self.view3d_after):
            if old is None:
                continue
            self._plots_layout.removeWidget(old)
            # hide, never setParent(None): an unparented, shown widget becomes a top-level
            # window until deleteLater runs, and on macOS that flashes and activates another
            # window (the main window) on every map selection
            old.hide()
            old.deleteLater()
        self.view3d = self.view3d_after = None
        self._plots_mode = None
        self.plot_raw, self.plot_result = pg.PlotWidget(), pg.PlotWidget()
        self._plots_layout.addWidget(self.plot_raw, 1)
        self._plots_layout.addWidget(self.plot_result, 1)
        self.plot_result.setVisible(two_plots)
        self._connect_plot_clicks()

    def _connect_plot_clicks(self) -> None:
        """A click on a 2D plot jumps to the table cell (27.11); the plots are recreated by
        `_reset_plots`, so this runs for every new pair."""
        for plot, which in ((self.plot_raw, "raw"), (self.plot_result, "result")):
            plot.scene().sigMouseClicked.connect(lambda ev, p=plot, w=which: self._on_plot_clicked(ev, p, w))

    def _jump_table(self) -> LookupTable | None:
        """The table whose grid the table cells follow: the saved one, or the edited one while
        editing when its values are valid (27.11)."""
        saved = self._tables.get(self._shown_name or "")
        if saved is None or not self._changed:
            return saved
        try:
            return self._edited_table(saved)
        except (maps_io.MapError, ValueError):
            return saved

    def _on_plot_clicked(self, ev, plot: pg.PlotWidget, which: str) -> None:
        """Left click (not a drag) on a 2D plot -> make the table cell under it current (27.11)."""
        if ev.button() != Qt.MouseButton.LeftButton or plot not in (self.plot_raw, self.plot_result) or not plot.isVisible():
            return
        saved = self._tables.get(self._shown_name or "")
        if saved is None or self._plots_mode not in ("curve", "heat"):
            return
        if which == "result" and self._changed and self.compare_error:
            return  # "After" is not drawn (invalid values)
        vb = plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        pt = vb.mapSceneToView(ev.scenePos())
        compare = bool(self._changed)
        # the grid is the table's: saved axes, except "After" (and the curve) while editing
        grid = self._jump_table() if (compare and which == "result") or saved.kind == "curve" else saved
        if saved.kind == "curve":
            cell = self._cell_index(grid.x, pt.x())
            if cell is None:
                return
            idx, row_col, name = (cell,), (cell, 1), "curve"
        else:
            ix, iy = self._cell_index(grid.x, pt.x()), self._cell_index(grid.y, pt.y())
            if ix is None or iy is None:
                return
            idx, row_col = (iy, ix), (iy + 1, ix + 1)
            name = ("before" if which == "raw" else "after") if compare else ("table" if which == "raw" else "result")
        log_op("map_preview_cell_jump", name=self._shown_name, cell=list(idx), plot=name)
        self.preview_table.setCurrentCell(*row_col)
        self.preview_table.scrollTo(self.preview_table.model().index(*row_col))
        self.preview_table.setFocus()

    def _on_3d_clicked(self, iy: int, ix: int, plot: str) -> None:
        """A click on a 3D surface grid point -> make its table cell current (27.12). The surface
        is drawn from the table's own axes ("After": the edited ones), so `(iy, ix)` is the cell."""
        saved = self._tables.get(self._shown_name or "")
        if saved is None or saved.kind != "map" or self._plots_mode not in ("3d", "3d_pair"):
            return
        if not (0 <= iy < len(saved.y) and 0 <= ix < len(saved.x)):
            return
        if plot == "3d" and self._plots_mode == "3d_pair":
            plot = "3d_before"  # the first view is "Before" while editing (the view is reused, 27.9)
        log_op("map_preview_cell_jump", name=self._shown_name, cell=[iy, ix], plot=plot)
        row_col = (iy + 1, ix + 1)
        self.preview_table.setCurrentCell(*row_col)
        self.preview_table.scrollTo(self.preview_table.model().index(*row_col))
        self.preview_table.setFocus()

    @staticmethod
    def _cell_index(axis: np.ndarray, v: float) -> int | None:
        """Index of the cell (27.4 `cell_edges`) of `axis` containing `v`; None outside the grid."""
        if len(axis) < 2 or not np.isfinite(v):
            return None
        e = cell_edges(axis)
        if v < e[0] or v > e[-1]:
            return None
        return int(min(max(np.searchsorted(e, v, side="right") - 1, 0), len(axis) - 1))

    @property
    def compare_active(self) -> bool:
        """True while the plots show Before / After (the table has unsaved edits, 27.9)."""
        return bool(self._changed) and self._shown_name is not None

    def _refresh_plots(self) -> None:
        """Redraw the plot area for the current edit state (27.9). The table itself is untouched."""
        table = self._tables.get(self._shown_name or "")
        if table is None:
            return
        edited, self.compare_error = None, ""
        if self._changed:
            try:
                edited = self._edited_table(table)
            except (maps_io.MapError, ValueError) as e:
                self.compare_error = str(e)
        self._draw_plots(table, edited)
        if self._changed:
            self._say(tr("map.after_invalid", reason=self.compare_error) if self.compare_error else "")

    def _draw_plots(self, saved: LookupTable | None, edited: LookupTable | None) -> None:
        """Draw the plots: normal pair, or Before / After when `self._changed` (`edited` None
        then means the edited values are invalid -> no "After", the reason is shown)."""
        if saved is None:
            return
        compare = bool(self._changed)
        want3d = saved.kind == "map" and self.toggle_3d.isChecked()
        mode = ("3d_pair" if compare else "3d") if want3d else ("curve" if saved.kind == "curve" else "heat")
        was3d = bool(self._plots_mode and self._plots_mode.startswith("3d"))
        if not (mode.startswith("3d") and was3d):
            # fresh widgets: a heat map's colour bar would otherwise linger (see _show_preview).
            # 3D views are kept and updated in place (no new GL widget per keystroke).
            self._reset_plots(saved.kind == "map")
        elif mode == "3d" and self.view3d_after is not None:
            self._plots_layout.removeWidget(self.view3d_after)
            self.view3d_after.hide()
            self.view3d_after.deleteLater()
            self.view3d_after = None
        self._plots_mode = mode
        if mode.startswith("3d"):
            self._draw_3d(saved, edited, compare)
        elif mode == "curve":
            self._draw_curve(saved, edited, compare)
        else:
            self._draw_heat(saved, edited, compare)

    @staticmethod
    def _shared_range(*grids) -> tuple[float, float]:
        """One (lo, hi) over the finite values of every grid (27.9)."""
        parts = [g[np.isfinite(g)] for g in grids if g is not None]
        parts = [p for p in parts if p.size]
        if not parts:
            return 0.0, 1.0
        lo = min(float(p.min()) for p in parts)
        hi = max(float(p.max()) for p in parts)
        return (lo, hi if hi > lo else lo + 1.0)

    def _draw_curve(self, saved: LookupTable, edited: LookupTable | None, compare: bool) -> None:
        p = self.plot_raw
        p.setLabel("bottom", saved.axis_names[0])
        p.setLabel("left", saved.axis_names[1])
        p.addLegend()
        if not compare:
            xs, ys = interpolated_preview(saved)
            p.plot(xs, ys, pen=pg.mkPen("#e65100", width=2), name=tr("map.preview_result"), connect="finite")
            p.plot(saved.x, saved.y, pen=pg.mkPen("#1565c0", width=1), symbol="o", symbolSize=7,
                   symbolBrush="#1565c0", name=tr("map.preview_table"), connect="finite")  # no line across empty cells (27.10)
            return
        # before: thin grey; after: thick colour (27.9)
        grey = "#9e9e9e"
        xs, ys = interpolated_preview(saved)
        p.plot(xs, ys, pen=pg.mkPen(grey, width=1), name=tr("map.before"), connect="finite")
        p.plot(saved.x, saved.y, pen=None, symbol="o", symbolSize=5, symbolBrush=grey, symbolPen=grey)
        if edited is None:
            p.setTitle(tr("map.after_invalid_title"))
            return
        xs, ys = interpolated_preview(edited)
        p.plot(xs, ys, pen=pg.mkPen("#e65100", width=3), name=tr("map.after"), connect="finite")
        p.plot(edited.x, edited.y, pen=None, symbol="o", symbolSize=8, symbolBrush="#1565c0", symbolPen="#1565c0")

    def _draw_heat(self, saved: LookupTable, edited: LookupTable | None, compare: bool) -> None:
        xn, yn, zn = saved.axis_names
        if not compare:
            levels = self._shared_range(saved.z)
            # raw grid: one cell per grid point, cell edges at midpoints so a non-uniform axis
            # puts every node inside its own cell
            self._heatmap(self.plot_raw, saved.x, saved.y, saved.z, levels,
                          f"{tr('map.preview_table')} ({zn})", (xn, yn))
            xs, ys, z = interpolated_preview(saved)
            self._heatmap(self.plot_result, xs, ys, z, levels, f"{tr('map.preview_result')} ({zn})", (xn, yn))
            return
        # Before / After on the raw grids (what is being edited), one colour range for both (27.9)
        levels = self._shared_range(saved.z, None if edited is None else edited.z)
        self._heatmap(self.plot_raw, saved.x, saved.y, saved.z, levels, f"{tr('map.before')} ({zn})", (xn, yn))
        if edited is None:
            self.plot_result.setTitle(tr("map.after_invalid_title"))
            return
        self._heatmap(self.plot_result, edited.x, edited.y, edited.z, levels, f"{tr('map.after')} ({zn})", (xn, yn))

    def _draw_3d(self, saved: LookupTable, edited: LookupTable | None, compare: bool) -> None:
        """One surface normally; while editing two (Before / After) with linked cameras and one
        Z scale from the union of both value ranges (27.9)."""
        from .xyz_3d_window import Xyz3DView

        self.plot_raw.setVisible(False)
        self.plot_result.setVisible(False)
        if self.view3d is None:
            self.view3d = Xyz3DView()
            self.view3d.gridPointClicked.connect(lambda iy, ix: self._on_3d_clicked(iy, ix, "3d"))
            self._plots_layout.addWidget(self.view3d, 1)
        zr = self._shared_range(saved.z, None if edited is None else edited.z) if compare else None
        self.view3d.set_surface(saved.x, saved.y, saved.z, axis_names=saved.axis_names, z_range=zr)
        if not compare:
            return
        if self.view3d_after is None:
            self.view3d_after = Xyz3DView()
            self.view3d_after.gridPointClicked.connect(lambda iy, ix: self._on_3d_clicked(iy, ix, "3d_after"))
            self._plots_layout.addWidget(self.view3d_after, 1)
            self.view3d_after.link_camera(self.view3d)
        if edited is None:
            self.view3d_after.clear()
        else:
            self.view3d_after.set_surface(edited.x, edited.y, edited.z, axis_names=edited.axis_names, z_range=zr)

    def _put(self, row: int, col: int, value: float, axis: bool = False) -> None:
        text = "" if np.isnan(value) else f"{value:g}"  # an empty cell shows blank (27.10)
        item = QTableWidgetItem(text)
        if axis:
            item.setBackground(_AXIS_BRUSH)
            item.setForeground(_BLACK)
            font = QFont(item.font())
            font.setBold(True)
            item.setFont(font)
        self.preview_table.setItem(row, col, item)
        self._orig_text[(row, col)] = text
        self._style_filled(item)

    def _value_cell(self, row: int, col: int) -> tuple[int, ...] | None:
        """(iy, ix) of a map cell / (i,) of a curve point for table cell (row, col); None for axes."""
        if self._preview_kind is None:
            return None
        if self._preview_kind == "curve":
            return (row,) if col == 1 else None
        return (row - 1, col - 1) if row >= 1 and col >= 1 else None

    def _style_filled(self, item: QTableWidgetItem) -> None:
        """Italic + grey for a cell that was filled in rather than measured (44.4)."""
        cell = self._value_cell(item.row(), item.column())
        if cell is None or cell not in self._filled_cells:
            return
        font = QFont(item.font())
        font.setItalic(True)
        item.setFont(font)
        item.setForeground(_FILLED_BRUSH)
        item.setToolTip(tr("map.cell_filled_tooltip"))

    # -- value editing (27.8) -------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        key = (item.row(), item.column())
        orig = self._orig_text.get(key)
        if orig is None:
            return
        text = item.text().strip()
        try:
            same = text == orig or float(text) == float(orig)
        except ValueError:
            same = False
        axis = self._is_axis_cell(*key)
        self._loading = True  # setBackground fires itemChanged again
        try:
            if same:
                self._changed.discard(key)
                item.setBackground(_AXIS_BRUSH if axis else QBrush())
                if not axis:
                    item.setForeground(QBrush())
                    self._style_filled(item)
            else:
                self._changed.add(key)
                item.setBackground(_EDITED_BRUSH)
                item.setForeground(_BLACK)
        finally:
            self._loading = False
        self._update_edit_buttons()
        self._refresh_plots()

    def _is_axis_cell(self, row: int, col: int) -> bool:
        table = self._tables.get(self._shown_name or "")
        return table is not None and table.kind == "map" and (row == 0 or col == 0)

    def _update_edit_buttons(self) -> None:
        dirty = bool(self._changed)
        self.save_values_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)

    def has_unsaved_edits(self) -> bool:
        return bool(self._changed)

    def _edited_table(self, table: LookupTable) -> LookupTable:
        """The registry table with the changed cells applied; raises `MapError` naming the row and
        column (1-based, as the shown table) of the first bad value (27.8)."""
        x, y = table.x.copy(), table.y.copy()
        z = table.z.copy() if table.z is not None else None
        for (r, c) in sorted(self._changed):
            item = self.preview_table.item(r, c)
            text = item.text() if item is not None else ""
            is_axis = c == 0 if table.kind == "curve" else (r == 0 or c == 0)
            # a value cell may be emptied (27.10); the axes must stay finite
            v = (maps_io.parse_cell if is_axis else maps_io.parse_value_cell)(text, r + 1, c + 1)
            if table.kind == "curve":
                (x if c == 0 else y)[r] = v
            elif r == 0:
                x[c - 1] = v
            elif c == 0:
                y[r - 1] = v
            else:
                z[r - 1, c - 1] = v
        if table.kind == "curve":
            maps_io.check_axis(list(x), [(i + 1, 1) for i in range(len(x))], table.axis_names[0])
        else:
            maps_io.check_axis(list(x), [(1, j + 2) for j in range(len(x))], table.axis_names[0])
            maps_io.check_axis(list(y), [(i + 2, 1) for i in range(len(y))], table.axis_names[1])
        maps_io.check_has_value(y if table.kind == "curve" else z)
        return dataclasses.replace(table, x=x, y=y, z=z)

    def save_values(self) -> bool:
        """Validate the edited cells, rewrite the stored CSV copy and recompute every derived
        variable that uses the map, all-or-nothing (27.8 / 26.4). On any failure nothing is
        changed and the edited cells stay on screen."""
        name = self._shown_name
        table = self._tables.get(name) if name else None
        if table is None or not self._changed:
            return False
        log_op("map_values_edit", name=name, cells=len(self._changed))
        try:
            candidate = self._edited_table(table)
            failures = self._mw.update_registry(
                maps={**self._tables, name: candidate},
                touched={name},
                persist=lambda: maps_io.save_map_values(
                    name, candidate, {c for c in map(lambda k: self._value_cell(*k), self._changed) if c is not None}
                ),
            )
        except (maps_io.MapError, OSError, ValueError) as e:
            log_op("map_values_edit_failed", name=name, reason=str(e))
            self._say(str(e))
            return False
        if failures:
            log_op("map_values_edit_rolled_back", name=name, failures=[n for n, _ in failures])
            self._say(tr("map.rolled_back") + "\n" + "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._changed.clear()
        self._refresh_list(select=name)
        self._show_preview(self._tables.get(name))  # also when the selection did not change
        self._shown_name = name
        self._say(tr("map.values_saved", name=name), ok=True)
        return True

    def revert_values(self) -> None:
        """Drop the unsaved edits and re-read the table from the registry (27.8)."""
        name = self._shown_name
        if name is None or not self._changed:
            return
        log_op("map_values_revert", name=name, cells=len(self._changed))
        self._show_preview(self._tables.get(name))
        self._say("")

    def _confirm_unsaved(self) -> bool:
        """Ask what to do with unsaved edits; True = may continue (saved or discarded)."""
        answer = messages.question(
            self, tr("map.manager_title"), tr("map.unsaved_confirm", name=self._shown_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return self.save_values()
        if answer == QMessageBox.StandardButton.No:
            self._changed.clear()
            self._update_edit_buttons()
            return True
        return False

    def _save_splitter(self) -> None:
        sizes = normalize_splitter_sizes(self.splitter.sizes())
        if sizes is None or sizes == self._mw.settings.map_manager_splitter:
            return
        self._mw.settings.map_manager_splitter = sizes
        try:
            save_settings(self._mw.settings)
        except OSError:  # pragma: no cover - cosmetic setting
            pass

    def done(self, result: int) -> None:
        if self._changed and not self._confirm_unsaved():
            return  # stay open (cancelled, or the save failed)
        self._splitter_timer.stop()
        self._save_splitter()
        super().done(result)

    # -- 3D surface (27.4 / 25.3) --------------------------------------------------------

    def _update_3d_toggle(self, table: LookupTable | None) -> None:
        """The toggle only exists for a map, with the `xyz_3d` flag on and OpenGL not known to
        be unusable (a failed probe hides it for the rest of the session)."""
        from . import xyz_3d_window

        status = xyz_3d_window.gl3d_status()
        available = (
            features.is_enabled("xyz_3d")
            and table is not None and table.kind == "map"
            and (status is None or status[0])
        )
        self.toggle_3d.setVisible(available)
        if not available and self.toggle_3d.isChecked():
            self.toggle_3d.blockSignals(True)
            self.toggle_3d.setChecked(False)
            self.toggle_3d.blockSignals(False)

    def _on_3d_toggled(self, checked: bool) -> None:
        log_op("map_preview_3d", enabled=checked)
        if checked and not self._mw.ensure_gl3d():  # first press probes OpenGL (25.3)
            self.toggle_3d.blockSignals(True)
            self.toggle_3d.setChecked(False)
            self.toggle_3d.blockSignals(False)
            self.toggle_3d.setVisible(False)
            return
        # redraw the plots only: the table and any unsaved edits stay (27.9: works anytime)
        self._refresh_plots()

    def _preview_curve(self, t: LookupTable) -> None:
        self.preview_table.setRowCount(len(t.x))
        self.preview_table.setColumnCount(2)
        self.preview_table.set_frozen(False)  # 2 columns with Qt headers: nothing to freeze (27.11)
        self.preview_table.horizontalHeader().setVisible(True)
        self.preview_table.verticalHeader().setVisible(True)
        self.preview_table.setHorizontalHeaderLabels(list(t.axis_names[:2]))
        for i, (x, y) in enumerate(zip(t.x, t.y)):
            self._put(i, 0, x)
            self._put(i, 1, y)

    def _preview_map(self, t: LookupTable) -> None:
        xn, yn, zn = t.axis_names
        # Laid out like the CSV (27.2/27.8): the X axis values are the first row, the Y axis
        # values the first column, so every number -- axes included -- is an editable cell.
        self.preview_table.setRowCount(len(t.y) + 1)
        self.preview_table.setColumnCount(len(t.x) + 1)
        # The first row/column already are the axes, so the Qt headers would only repeat the
        # axis names on every column/row: hide them and name the axes in the corner cell.
        self.preview_table.horizontalHeader().setVisible(False)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.set_frozen(True)  # axis row / column stay visible while scrolling (27.11)
        corner = QTableWidgetItem(f"{yn} \\ {xn}")
        corner.setFlags(Qt.ItemFlag.ItemIsEnabled)  # readable text, not editable/selectable
        corner.setForeground(_BLACK)  # on the light axis colour, also in dark mode
        corner.setBackground(_AXIS_BRUSH)
        self.preview_table.setItem(0, 0, corner)
        for c in range(len(t.x)):
            self._put(0, c + 1, t.x[c], axis=True)
        for r in range(len(t.y)):
            self._put(r + 1, 0, t.y[r], axis=True)
            for c in range(len(t.x)):
                self._put(r + 1, c + 1, t.z[r, c])

    @staticmethod
    def _heatmap(plot: pg.PlotWidget, xs: np.ndarray, ys: np.ndarray, z: np.ndarray, levels, title: str,
                 axis_labels: tuple[str, str] = ("X", "Y")) -> None:
        """`z` is shaped (len(ys), len(xs)); the cells are drawn at their real coordinates."""
        xe, ye = cell_edges(xs), cell_edges(ys)
        cmap = pg.colormap.get("viridis")
        item = pg.PColorMeshItem(colorMap=cmap, levels=levels)
        item.setData(*np.meshgrid(xe, ye), z)  # NaN (empty) cells are skipped by PColorMeshItem (27.10)
        plot.addItem(item)
        plot.setTitle(title)
        plot.setLabel("bottom", axis_labels[0])
        plot.setLabel("left", axis_labels[1])
        try:
            bar = pg.ColorBarItem(values=levels, colorMap=cmap, interactive=False)
            bar.setImageItem(item, insert_in=plot.getPlotItem())
        except Exception:  # noqa: BLE001 (a colour bar is cosmetic)
            pass
        plot.getPlotItem().getViewBox().setRange(
            QRectF(xe[0], ye[0], xe[-1] - xe[0], ye[-1] - ye[0]), padding=0
        )

    # -- actions -------------------------------------------------------------------------

    def _on_import_clicked(self) -> None:
        dialog = MapImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        self.do_import(v["path"], v["name"], v["kind"], v["interpolation"], v["extrapolation"])

    def do_import(self, path: str, name: str, kind: str = "auto", interpolation: str = "linear",
                  extrapolation: str = "clamp") -> bool:
        """Import a CSV (27.2/27.3) and register it. Errors are shown inline."""
        log_op("map_import", name=name, path=str(path), kind=kind, interpolation=interpolation,
               extrapolation=extrapolation)
        try:
            existing = self._mw.reserved_names()  # includes functions that failed to load
            table = maps_io.import_map(path, name, kind, interpolation, extrapolation, existing_names=existing)
        except (maps_io.MapError, OSError) as e:
            log_op("map_import_refused", reason=str(e))
            self._say(str(e))
            return False
        failures = self._mw.update_registry(maps={**self._tables, name: table}, touched={name})
        if failures:  # cannot happen for a brand-new name; kept so state never half-applies
            self._say("\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list(select=name)
        warnings = table.warnings()
        self._say(tr("map.imported", name=name) + ("\n" + "\n".join(warnings) if warnings else ""), ok=True)
        return True

    def apply_settings(self) -> bool:
        """Change the selected map's interpolation / extrapolation (27.4)."""
        name = self.selected_name()
        table = self._tables.get(name) if name else None
        if table is None:
            return False
        interp, extrap = self.interp_combo.currentData(), self.extrap_combo.currentData()
        log_op("map_settings", name=name, interpolation=interp, extrapolation=extrap)
        try:
            candidate = dataclasses.replace(table, interpolation=interp, extrapolation=extrap)
            failures = self._mw.update_registry(
                maps={**self._tables, name: candidate},
                touched={name},
                persist=lambda: maps_io.save_map_settings(name, interp, extrap),
            )
        except (ValueError, OSError) as e:
            log_op("map_settings_failed", reason=str(e))
            self._fill_combos(table)  # back to what the registry actually holds
            self._say(str(e))
            return False
        if failures:
            log_op("map_settings_rolled_back", failures=[n for n, _ in failures])
            self._fill_combos(table)
            self._say(tr("map.rolled_back") + "\n" + "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list(select=name)
        self._say(tr("map.settings_saved", name=name), ok=True)
        return True

    def reimport_selected(self) -> bool:
        name = self.selected_name()
        table = self._tables.get(name) if name else None
        if table is None:
            return False
        log_op("map_reimport", name=name)
        try:
            info = next(i for i in maps_io.list_maps() if i.name == name)
            if self._changed or info.edited_at:
                # re-import overwrites hand edits (27.8)
                answer = messages.question(
                    self, tr("map.manager_title"), tr("map.reimport_edited_confirm", name=name)
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
                self._changed.clear()
            src = Path(info.source_path)
            if not info.source_path or not src.is_file():
                raise maps_io.MapError(tr("map.source_missing", path=info.source_path))
            candidate = maps_io.parse_table_csv(src, name, table.kind, table.interpolation, table.extrapolation)
            failures = self._mw.update_registry(
                maps={**self._tables, name: candidate},
                touched={name},
                persist=lambda: maps_io.reimport_map(name),
            )
        except (maps_io.MapError, OSError, StopIteration, ValueError) as e:
            log_op("map_reimport_failed", reason=str(e))
            self._say(str(e) or tr("map.reimport_failed"))
            return False
        if failures:
            log_op("map_reimport_rolled_back", failures=[n for n, _ in failures])
            self._say(tr("map.rolled_back") + "\n" + "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list(select=name)
        warnings = candidate.warnings()
        self._say(tr("map.reimported", name=name) + ("\n" + "\n".join(warnings) if warnings else ""), ok=True)
        return True

    def edit_selected(self) -> bool:
        """Open the edit dialog for the selected map and apply it (27.7)."""
        name = self.selected_name()
        table = self._tables.get(name) if name else None
        if table is None:
            return False
        if self._changed and not self._confirm_unsaved():
            return False
        dialog = MapEditDialog(table, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        v = dialog.values()
        return self.apply_edit(name, v["name"], v["axis_names"])

    def apply_edit(self, old: str, new: str, axis_names=None) -> bool:
        """Rename `old` -> `new` and/or change its axis names (27.7). A rename is refused when a
        derived variable or user function uses the map (as in ch.26); the file rename is
        all-or-nothing (`maps_io.rename_map`). Errors are shown inline; returns success."""
        table = self._tables.get(old)
        if table is None:
            return False
        new = new.strip()
        names = normalize_axis_names(table.axis_names if axis_names is None else axis_names)
        renamed, axes_changed = new != old, names != table.axis_names
        if renamed:
            log_op("map_rename", old=old, new=new)
        if axes_changed:
            log_op("map_axis_names", name=new if renamed else old, x=names[0], y=names[1], z=names[2])
        if not renamed and not axes_changed:
            self._say("")
            return True
        try:
            if renamed:
                maps_io.validate_map_name(new, self._mw.reserved_names())
                users = self._mw.callable_users(old)
                if users:
                    log_op("map_rename_refused", old=old, users=users)
                    self._say(tr("map.rename_in_use", name=old, users=", ".join(users)))
                    return False
            candidate = dataclasses.replace(table, name=new, axis_names=names)

            def persist() -> None:
                if renamed:
                    maps_io.rename_map(old, new, self._mw.reserved_names())
                if axes_changed:
                    try:
                        maps_io.save_map_axis_names(new, *names)
                    except BaseException:
                        if renamed:  # keep the pair of steps all-or-nothing
                            maps_io.rename_map(new, old)
                        raise

            # keep the list order; the renamed table takes the old one's slot
            maps = {(new if n == old else n): (candidate if n == old else t) for n, t in self._tables.items()}
            failures = self._mw.update_registry(maps=maps, touched={old, new}, persist=persist)
        except (maps_io.MapError, OSError) as e:
            log_op("map_edit_failed", old=old, new=new, reason=str(e))
            self._say(str(e))
            return False
        if failures:
            log_op("map_edit_rolled_back", failures=[n for n, _ in failures])
            self._say(tr("map.rolled_back") + "\n" + "\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list(select=new)
        self._say(tr("map.renamed", old=old, new=new) if renamed else tr("map.edited", name=new), ok=True)
        return True

    def delete_selected(self) -> bool:
        name = self.selected_name()
        if name is None or name not in self._tables:
            return False
        log_op("map_delete", name=name)
        users = self._mw.callable_users(name)
        if users:
            log_op("map_delete_refused", name=name, users=users)
            self._say(tr("map.delete_in_use", name=name, users=", ".join(users)))
            return False
        answer = messages.question(self, tr("map.manager_title"), tr("map.delete_confirm", name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._changed.clear()  # the map is going away; its unsaved edits with it
        remaining = {n: t for n, t in self._tables.items() if n != name}
        try:
            failures = self._mw.update_registry(
                maps=remaining, touched={name}, persist=lambda: maps_io.delete_map(name)
            )
        except (maps_io.MapError, OSError) as e:
            self._say(str(e))
            return False
        if failures:
            self._say("\n".join(f"- {n}: {r}" for n, r in failures))
            return False
        self._refresh_list()
        return True
