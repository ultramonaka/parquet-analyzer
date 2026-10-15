"""Export the XY window's map / curve as a lookup table (analysis.ja.md 44.4).

`MapExportDialog` asks for the name, the outputs (register in the map manager and/or save a CSV) and
the interpolation settings; `perform_export` does the work and is separate so it can be tested without
the dialog. Registering goes through `MainWindow.update_registry`, so overwriting a map that derived
variables use recomputes them and rolls everything back on failure (27.8).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.maps import EXTRAPOLATIONS, INTERPOLATIONS, LookupTable
from ..core.oplog import log_op
from ..io import maps as maps_io
from ..io.settings import save_settings
from . import messages
from .i18n import tr
from .xy_map import XYMapResult

logger = logging.getLogger(__name__)


@dataclass
class ExportValues:
    name: str
    register: bool = True
    save_csv: bool = True
    csv_path: str = ""
    interpolation: str = "linear"
    extrapolation: str = "clamp"
    index: int = 0  # which curve (a result with several Y has one grid per Y)
    csv_confirmed: bool = False  # the path came from the file dialog (it already asked about overwriting, 44.4)


def default_name(r: XYMapResult, index: int = 0) -> str:
    """`<Z>_map` for a map, `<Y>_curve` for a curve, made a valid identifier. The aggregate "time"
    (44.14) gives `<X>_<Y>_time` / `<X>_time`, which is the series name `compute_xy_map` made."""
    if r.is_time:
        base = r.names[index]
    else:
        base = (r.z_name or "z") + "_map" if not r.is_curve else r.names[index] + "_curve"
    base = re.sub(r"\W", "_", base, flags=re.ASCII)
    return "_" + base if base[:1].isdigit() else base


def name_problem(main, name: str) -> tuple[str, bool]:
    """(error text or "", overwrite): the 27.3 name check; an existing map of that name is an
    overwrite, not an error (44.4)."""
    maps = main.function_registry.maps
    try:
        maps_io.validate_map_name(name, main.reserved_names() - set(maps))
    except maps_io.MapError as e:
        return str(e), False
    return "", name in maps


def table_from_result(r: XYMapResult, index: int, name: str, interpolation: str, extrapolation: str):
    """(LookupTable, filled cells) of grid `index`; empty cells stay NaN (27.10). `filled` = `[iy, ix]`
    per filled map cell, `[i]` per filled curve point (JSON `filled`, 44.4)."""
    g = r.grids[index]
    if r.is_curve:
        names = (r.x_name, r.names[index], "")
        table = LookupTable(name, "curve", g.x_axis, g.value, None, interpolation, extrapolation, axis_names=names)
        filled = [[int(i)] for i in np.flatnonzero(g.filled_mask)]
    else:
        names = (r.x_name, r.y_names[0], r.z_name or ("time" if r.is_time else ""))
        table = LookupTable(name, "map", g.x_axis, g.y_axis, g.value, interpolation, extrapolation, axis_names=names)
        filled = [[int(iy), int(ix)] for iy, ix in zip(*np.nonzero(g.filled_mask))]
    return table, filled


def empty_note_text(r: XYMapResult, index: int = 0) -> str:
    """The "empty cells become NaN in expressions" note (44.10): only for fill "none" and a grid that
    has empty cells; "" otherwise. The empty share is of all cells, whole percent (at least 1)."""
    if r.fill != "none" and not r.keep_sparse_empty:
        return ""
    value = r.grids[index].value
    empty = int(np.isnan(value).sum())
    if not empty:
        return ""
    key = "xy.export_empty_note_sparse" if r.fill != "none" else "xy.export_empty_note"
    return tr(key, percent=max(1, round(100.0 * empty / value.size)))


def _spec_dict(spec) -> dict | None:
    if spec is None:
        return None
    return {"mode": spec.mode, "lo": spec.lo, "hi": spec.hi, "step": spec.step, "count": spec.count}


def origin_info(main, r: XYMapResult, index: int) -> dict:
    """The JSON `origin` (44.4): reference information about where the numbers came from."""
    tv = main._time_values
    time_range = None
    if tv is not None and len(tv) and r.row_end > r.row_start:
        time_range = [float(tv[r.row_start]), float(tv[min(r.row_end, len(tv)) - 1])]
    extra = {"time_unit": r.time_unit} if r.is_time else {}  # 44.14: the values are times in this unit
    return {
        "kind": "xy_map",
        "file": str(main._data_source.path) if main._data_source is not None else "",
        "time_range": time_range,
        "x": r.x_name,
        "y": r.y_names[0] if not r.is_curve else r.names[index],
        "z": r.z_name,
        "grid": {"x": _spec_dict(r.x_spec), "y": _spec_dict(r.y_spec)},
        "agg": r.agg,
        **extra,
        "min_count": r.min_count,
        "fill": r.fill,
        "keep_sparse_empty": r.keep_sparse_empty,
        "excluded_rows": r.n_user_excluded,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


class MapExportDialog(QDialog):
    """Name (checked live), outputs, CSV path and interpolation of the exported map / curve."""

    def __init__(self, window, r: XYMapResult) -> None:
        super().__init__(window)
        self._window = window
        self._main = window._main
        self._r = r
        self._path_edited = False
        self._path_confirmed = False  # set by the file dialog, cleared when the path is typed or reset
        self.setWindowTitle(tr("xy.export_title"))
        self.curve_combo = QComboBox()
        for n in r.names:
            self.curve_combo.addItem(n)
        self.name_edit = QLineEdit(default_name(r, 0))
        self.name_message = QLabel("")
        self.name_message.setWordWrap(True)
        self.register_check = QCheckBox(tr("xy.export_register"))
        self.register_check.setChecked(True)
        self.csv_check = QCheckBox(tr("xy.export_csv"))
        self.csv_check.setChecked(True)
        self.path_edit = QLineEdit()
        self.browse_button = QPushButton(tr("map.browse"))
        self.interp_combo = QComboBox()
        self.extrap_combo = QComboBox()
        for key in EXTRAPOLATIONS:
            self.extrap_combo.addItem(_label("map.extrap_", key), key)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)

        form = QFormLayout()
        if r.is_curve and len(r.names) > 1:
            form.addRow(tr("xy.export_curve"), self.curve_combo)
        form.addRow(tr("map.name"), self.name_edit)
        form.addRow(self.name_message)
        self.empty_note = QLabel("")  # fill == "none" with empty cells (44.10); never blocks saving
        self.empty_note.setObjectName("map_export_empty_note")
        self.empty_note.setWordWrap(True)
        self.empty_note.setStyleSheet("color: #e65100;")
        form.addRow(self.empty_note)
        form.addRow(self.register_check)
        form.addRow(self.csv_check)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)
        form.addRow(tr("xy.export_csv_path"), row)
        form.addRow(tr("map.interpolation"), self.interp_combo)
        form.addRow(tr("map.extrapolation"), self.extrap_combo)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self._refill_interpolations()
        self._reset_path()
        self.name_edit.textChanged.connect(self._on_name_changed)
        self.curve_combo.currentIndexChanged.connect(self._on_curve_changed)
        self.register_check.toggled.connect(self._validate)
        self.csv_check.toggled.connect(self._validate)
        self.path_edit.textEdited.connect(self._on_path_edited)
        self.browse_button.clicked.connect(self._browse)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._validate()

    # -- helpers -------------------------------------------------------------------

    @property
    def index(self) -> int:
        return max(self.curve_combo.currentIndex(), 0)

    def _has_empty(self) -> bool:
        return bool(np.isnan(self._r.grids[self.index].value).any())

    def _refill_interpolations(self) -> None:
        """Maps: linear/nearest/previous; curves also spline, except when empty cells make it impossible (27.10)."""
        kind = "curve" if self._r.is_curve else "map"
        prev = self.interp_combo.currentData()
        self.interp_combo.clear()
        for key, spec in INTERPOLATIONS.items():
            if kind in spec.kinds:
                self.interp_combo.addItem(_label("map.interp_", key), key)
        if self._r.is_curve:
            item = self.interp_combo.model().item(self.interp_combo.findData("spline"))
            if self._has_empty():
                item.setEnabled(False)
                item.setToolTip(tr("xy.export_spline_empty"))
        idx = self.interp_combo.findData(prev if prev else "linear")
        self.interp_combo.setCurrentIndex(max(idx, 0))

    def _default_csv_path(self) -> str:
        main = self._main
        folder = main.settings.last_export_folder or (str(Path(main._data_source.path).parent) if main._data_source else "")
        return str(Path(folder) / f"{self.name_edit.text().strip() or 'map'}.csv")

    def _reset_path(self) -> None:
        self._path_confirmed = False
        self.path_edit.setText(self._default_csv_path())

    def _on_name_changed(self, *_a) -> None:
        if not self._path_edited:
            self._reset_path()
        self._validate()

    def _on_curve_changed(self, *_a) -> None:
        self.name_edit.setText(default_name(self._r, self.index))
        self._refill_interpolations()
        self._validate()  # the empty-cell note depends on the chosen curve

    def _on_path_edited(self, *_a) -> None:
        self._path_edited = True
        self._path_confirmed = False

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("xy.export_csv_path"), self.path_edit.text(), tr("map.csv_filter"))
        if path:
            self._path_edited = True
            self.path_edit.setText(path)
            self._path_confirmed = True

    def _validate(self, *_a) -> None:
        """Live name check (27.3): the reason is shown as you type; an existing map is an overwrite."""
        name = self.name_edit.text().strip()
        problem, overwrite = name_problem(self._main, name)
        if problem:
            self.name_message.setStyleSheet("color: #d32f2f;")
            self.name_message.setText(tr("xy.export_name_invalid", reason=problem))
        elif overwrite and self.register_check.isChecked():
            self.name_message.setStyleSheet("color: #e65100;")
            self.name_message.setText(tr("xy.export_name_overwrite", name=name))
        else:
            self.name_message.setText("")
        self.empty_note.setText(empty_note_text(self._r, self.index))
        self.empty_note.setVisible(bool(self.empty_note.text()))
        self.path_edit.setEnabled(self.csv_check.isChecked())
        self.browse_button.setEnabled(self.csv_check.isChecked())
        outputs = self.register_check.isChecked() or self.csv_check.isChecked()
        self.ok_button.setEnabled(not problem and outputs)

    def values(self) -> ExportValues:
        return ExportValues(
            name=self.name_edit.text().strip(),
            register=self.register_check.isChecked(),
            save_csv=self.csv_check.isChecked(),
            csv_path=self.path_edit.text().strip(),
            interpolation=self.interp_combo.currentData(),
            extrapolation=self.extrap_combo.currentData(),
            index=self.index,
            csv_confirmed=self._path_confirmed,
        )


def _label(prefix: str, key: str) -> str:
    try:
        return tr(f"{prefix}{key}")
    except KeyError:
        return key


def run_export_dialog(window, r: XYMapResult) -> bool:
    """Show the dialog for the XY window `window` and export on OK (44.4)."""
    log_op("map_from_xy_export_open", kind=r.kind)
    dialog = MapExportDialog(window, r)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    return perform_export(window, r, dialog.values())


def _discard(tmp: Path | None) -> None:
    """Remove the temporary CSV (the original file, if any, is untouched)."""
    if tmp is not None:
        try:
            tmp.unlink()
        except OSError:
            pass


def perform_export(window, r: XYMapResult, v: ExportValues) -> bool:
    """Write the CSV and/or register the map (44.4). Returns success; problems are shown to the user."""
    main = window._main
    title = tr("xy.export_title")
    problem, _ow = name_problem(main, v.name)
    if problem:
        messages.warning(window, title, tr("xy.export_name_invalid", reason=problem))
        return False
    if not (v.register or v.save_csv):
        return False
    maps = main.function_registry.maps
    overwrite = v.register and v.name in maps
    g = r.grids[v.index]
    log_op(
        "map_from_xy_export", name=v.name, register=v.register, csv=v.save_csv, overwrite=overwrite,
        kind=r.kind, grid=list(g.value.shape), filled=g.n_filled, empty=int(np.isnan(g.value).sum()),
    )
    try:
        table, filled = table_from_result(r, v.index, v.name, v.interpolation, v.extrapolation)
        maps_io.check_has_value(table.values)
    except (ValueError, maps_io.MapError) as e:
        messages.warning(window, title, str(e))
        return False
    if overwrite:
        users = main.callable_users(v.name)
        text = tr("xy.export_overwrite_confirm", name=v.name)
        if users:
            text += "\n" + tr("xy.export_overwrite_users", users=", ".join(users))
        if messages.question(window, title, text) != QMessageBox.StandardButton.Yes:
            log_op("map_from_xy_export_cancelled", name=v.name)
            return False
    source_path = ""
    path: Path | None = None
    tmp_path: Path | None = None  # the CSV is written here first, moved over `path` after the registry change (44.10)
    if v.save_csv:
        path = Path(v.csv_path)
        if path.exists() and not v.csv_confirmed:  # typed / default path: the file dialog did not ask (44.4)
            if messages.question(window, title, tr("xy.export_csv_overwrite_confirm", path=str(path))) != QMessageBox.StandardButton.Yes:
                log_op("map_from_xy_export_cancelled", name=v.name, reason="csv_exists")
                return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            os.close(fd)
            tmp_path = Path(tmp_name)
            maps_io.write_table_csv(table, tmp_path, axis_header=True)
        except OSError as e:
            logger.exception("could not write %s", path)
            _discard(tmp_path)
            messages.warning(window, title, tr("xy.export_csv_failed", path=str(path), error=e))
            return False
        source_path = str(path.resolve())
    publish_failed: list[OSError] = []

    def publish() -> None:
        # the last step of the registry persist: a failure here rolls the map files back (44.11)
        try:
            os.replace(tmp_path, path)
        except OSError as e:
            publish_failed.append(e)
            raise

    if v.register:
        origin = origin_info(main, r, v.index)
        try:
            failures = main.update_registry(
                maps={**maps, v.name: table},
                touched={v.name},
                persist=lambda: maps_io.write_generated_map(
                    table, origin, filled, source_path, overwrite=overwrite,
                    existing_names=main.reserved_names() - set(maps),
                    publish=publish if tmp_path is not None else None,
                ),
            )
        except (maps_io.MapError, OSError, ValueError) as e:
            log_op("map_from_xy_export_failed", name=v.name, reason=str(e))
            _discard(tmp_path)
            if publish_failed:
                logger.error("could not write %s: %s", path, publish_failed[0])
                messages.warning(window, title, tr("xy.export_csv_failed", path=str(path), error=publish_failed[0]))
            else:
                messages.warning(window, title, str(e))
            return False
        if failures:
            log_op("map_from_xy_export_rolled_back", name=v.name, failures=[n for n, _ in failures])
            _discard(tmp_path)
            messages.warning(
                window, title, tr("map.rolled_back") + "\n" + "\n".join(f"- {n}: {why}" for n, why in failures)
            )
            return False
    if tmp_path is not None and path is not None:
        if not v.register:  # CSV only: no registry step to join, replace in place
            try:
                publish()
            except OSError as e:
                logger.exception("could not write %s", path)
                _discard(tmp_path)
                messages.warning(window, title, tr("xy.export_csv_failed", path=str(path), error=e))
                return False
        main.settings.last_export_folder = str(path.resolve().parent)
        try:
            save_settings(main.settings)
        except OSError:  # pragma: no cover - cosmetic
            pass
    if v.register:
        manager = getattr(main, "_map_manager", None)
        if manager is not None:
            if getattr(manager, "_shown_name", None) == v.name:
                manager._changed.clear()  # the map was replaced: its unsaved edits are moot
            manager._refresh_list(select=v.name)
            manager._show_preview(main.function_registry.maps.get(v.name))  # also when the row did not change
            manager._shown_name = v.name
    done = tr("xy.export_done", name=v.name) if v.register else tr("xy.export_done_csv", path=v.csv_path)
    window.set_status(done)
    main._show_notice(done, 10_000)
    return True
