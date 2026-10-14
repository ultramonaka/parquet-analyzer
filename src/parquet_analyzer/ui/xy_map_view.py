"""Map / curve display of the XY window (analysis.ja.md 44.3-44.11; split off by 44.12).

`XYMapViewMixin` holds the map's settings widgets, constraints, job, drawing (2D heat / curves, 3D surface),
hover, status and the "Export as map..." action. It is a mixin, not a component: these parts share the
settings panel, the compute call and the 2D/3D switch with `XYPlotWindow`, so they were moved as they were.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton, QSpinBox, QToolBar, QWidget

from .. import features
from ..core import map_grid as mg
from ..core.oplog import log_op
from . import xy_exclude as xex  # (`xe` is the cell-edge array in the drawing methods)
from . import xy_map as xm
from .i18n import tr
from .plot_widget import _COLOR_CYCLE


def xm_agg_keys() -> tuple[str, ...]:
    return mg.AGGREGATIONS


class XYMapViewMixin:
    """The map / curve part of `XYPlotWindow` (mixed in first: `XYPlotWindow(XYMapViewMixin, AnalysisWindow)`).

    Relies on these members of the window (defined in `xy_plot_window.py` / `AnalysisWindow`):
      state     _main, _mode ("2d"/"3d"), _result, _closed, _plot, _legend, _series_items, _legend_proxies,
                _colorbar, _cursor_item, _hover_label, _hidden_y, _view3d, _status_core, _grid_invalid
      widgets   _x_combo, _z_combo, _display_combo, _color_combo, _y_picker (settings panel)
      methods   _group, selected_y, _color_mode, _is_time, _resolve_columns, _on_setting_changed, recompute,
                set_status, _range_text, _prune_hidden, _set_colorbar, _add_legend_entry,
                _legend_series_visible, _on_cursors_changed, _draw_scene_3d (3D base scene)
      overlay   _overlays (XYOverlayController: build_button, refresh, widened_bounds; 44.7, 44.12)
    The window in turn calls the mixin's `_build_map_controls`, `_map_selected`, `_map_kind`,
    `_apply_map_constraints`, `_make_job_map`, `_show_map*`, `_on_mouse_moved`, `_add_map_actions`.
    """

    def _build_map_controls(self, flow) -> None:
        """The map display's settings (44.3): grids, aggregate, min. points, fill, overlay, Auto. They
        exist only with the `map_from_xy` flag; they are shown while a map is the chosen display."""
        self._map_enabled = features.is_enabled("map_from_xy")
        self._map_groups: list[QWidget] = []
        self._grid_vars: tuple = ()
        self._x_grid, self._y_grid = xm.GridAxisEdit(), xm.GridAxisEdit()
        self._agg_combo, self._fill_combo = QComboBox(), QComboBox()
        self._kind3d_combo = QComboBox()
        self._min_count_spin = QSpinBox()
        self._points_check = QCheckBox(tr("xy.map_overlay_points"))
        self._auto_button = QPushButton(tr("xy.map_auto"))
        self._kind3d_group = QWidget()
        self._keep_sparse_check = QCheckBox(tr("xy.map_keep_sparse"))
        if not self._map_enabled:
            return
        self._display_combo.addItem(tr("xy.display_map"), "map")
        for kind in ("points", "map"):
            self._kind3d_combo.addItem(tr(f"xy.kind3d_{kind}"), kind)
        self._kind3d_group, _l = self._group(flow, tr("xy.kind3d"), self._kind3d_combo)
        self._kind3d_group.setVisible(False)
        self._x_grid_group, _l = self._group(flow, tr("xy.map_x_grid"), self._x_grid)
        self._y_grid_group, _l = self._group(flow, tr("xy.map_y_grid"), self._y_grid)
        for key in xm_agg_keys():
            self._agg_combo.addItem(tr(f"xy.agg_{key}"), key)
        self._agg_group, _l = self._group(flow, tr("xy.map_agg"), self._agg_combo)
        self._min_count_spin.setRange(1, 1_000_000)
        self._min_count_spin.setValue(1)
        self._min_count_spin.setToolTip(tr("xy.map_min_count_tooltip"))
        self._min_group, _l = self._group(flow, tr("xy.map_min_count"), self._min_count_spin)
        for key in mg.FILLS:
            self._fill_combo.addItem(tr(f"xy.fill_{key}"), key)
        self._fill_group, _l = self._group(flow, tr("xy.map_fill"), self._fill_combo)
        self._keep_sparse_check.setToolTip(tr("xy.map_keep_sparse_tooltip"))
        # 44.13 (8): the exclusion UI and the keep-sparse checkbox are hidden while the flag is off. The
        # checkbox then stays an unchecked widget that is never shown; `_exclude` keeps no buttons.
        exclude_on = features.is_enabled("map_exclude_points")
        if exclude_on:
            flow.addWidget(self._keep_sparse_check)
        self._points_check.setChecked(True)
        flow.addWidget(self._points_check)
        exclude_group = self._exclude.build_controls(flow) if exclude_on else None
        self._auto_button.setToolTip(tr("xy.map_auto_tooltip"))
        self._auto_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        flow.addWidget(self._auto_button)
        self._map_groups = [self._x_grid_group, self._y_grid_group, self._agg_group, self._min_group,
                            self._fill_group, self._points_check, self._auto_button]
        if exclude_group is not None:
            self._map_groups += [self._keep_sparse_check, exclude_group]
        for g in self._map_groups:
            g.setVisible(False)
        self._x_grid.edited.connect(self._on_setting_changed)
        self._y_grid.edited.connect(self._on_setting_changed)
        self._agg_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._fill_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._min_count_spin.valueChanged.connect(self._on_setting_changed)
        self._keep_sparse_check.toggled.connect(self._on_setting_changed)
        self._kind3d_combo.currentIndexChanged.connect(self._on_setting_changed)
        self._points_check.toggled.connect(self._on_points_toggled)
        self._auto_button.clicked.connect(self._on_map_auto)
        self._overlays.build_button(flow)

    def _map_selected(self) -> bool:
        """Whether the map is the chosen display (needs the `map_from_xy` flag)."""
        if not self._map_enabled:
            return False
        if self._mode == "3d":
            return self._kind3d_combo.currentData() == "map"
        return self._display_combo.currentData() == "map"

    def _map_kind(self) -> str:
        """3D and 2D with one Y coloured by Z give a map z(x, y); 2D otherwise gives a curve per Y."""
        if self._mode == "3d":
            return "map"
        single = len(self.selected_y()) == 1 and self._color_mode() == "z" and bool(self._z_combo.currentText())
        return "map" if single else "curve"

    def _map_blocked_reason(self) -> str:
        """"" or why no map can be made: a map on the time axis could not be used in expressions."""
        names = [self._x_combo.currentText(), *self.selected_y()]
        if self._map_kind() == "map":
            names.append(self._z_combo.currentText())
        return tr("xy.map_blocked_time") if any(self._is_time(n) for n in names) else ""

    def _apply_map_constraints(self) -> None:
        """Choosing the time-axis column as X/Y/Z switches the map off and disables its entries with
        the reason as tooltip (44.3)."""
        if not self._map_enabled:
            return
        reason = self._map_blocked_reason()
        for combo in (self._display_combo, self._kind3d_combo):
            item = combo.model().item(combo.findData("map"))
            item.setEnabled(not reason)
            item.setToolTip(reason)
        self._map_notice = ""
        if reason and self._map_selected():
            self._map_notice = reason
            for combo, fallback in ((self._display_combo, "auto"), (self._kind3d_combo, "points")):
                if combo.currentData() == "map":
                    combo.blockSignals(True)
                    combo.setCurrentIndex(combo.findData(fallback))
                    combo.blockSignals(False)

    def _update_map_controls(self) -> None:
        """Show the map settings while a map is chosen (the Y grid only for a map, not a curve)."""
        if not self._map_enabled:
            return
        three = self._mode == "3d"
        self._kind3d_group.setVisible(three)
        active = self._map_selected()
        for g in self._map_groups:
            g.setVisible(active)
        self._y_grid_group.setVisible(active and self._map_kind() == "map")
        count = self._agg_combo.currentData() == "count"
        self._fill_group.setEnabled(not count)
        self._keep_sparse_check.setEnabled(not count and self._fill_combo.currentData() != "none")  # 44.13
        self._exclude.update_buttons()
        self._update_median_item()
        self._update_export_action()

    def _update_median_item(self) -> None:
        """The median needs every point of a cell in memory: not offered beyond the limit (44.2)."""
        item = self._agg_combo.model().item(self._agg_combo.findData("median"))
        too_many = self._median_total is not None and self._median_total > mg.MAP_MEDIAN_MAX_POINTS
        item.setEnabled(not too_many)
        item.setToolTip(
            tr("xy.map_median_refused", limit=mg.MAP_MEDIAN_MAX_POINTS, n=self._median_total) if too_many else ""
        )

    def _forget_grids_if_variables_changed(self) -> None:
        """A different X/Y/Z makes the old grid ranges meaningless: take the new round ones."""
        if not self._map_enabled:
            return
        ys = self.selected_y()
        now = (self._x_combo.currentText(), ys[0] if ys else "", self._z_combo.currentText(), self._mode)
        if self._grid_vars and now[:3] != self._grid_vars[:3] and self._grid_vars[3] == now[3]:
            self._x_grid.forget()
            self._y_grid.forget()
            self._median_total = None
        self._grid_vars = now

    def _on_map_auto(self) -> None:
        log_op("xy_plot_map_auto")
        self._x_grid.reset_auto()
        self._y_grid.reset_auto()
        self.recompute(keep_range=True)

    def _on_points_toggled(self, _checked: bool) -> None:
        log_op("xy_plot_map_overlay", enabled=self._points_check.isChecked())
        if not self._points_check.isChecked():
            self._exclude.points_hidden()
        r = self._result
        if isinstance(r, xm.XYMapResult) and not r.no_data:
            if r.is_3d:
                self._show_map_3d(r)
            else:
                self._draw_map_result(r)

    def _make_job_map(self, row_start: int, row_end: int, cancelled):
        from . import (
            xy_plot_window as xpw,  # late: tests patch the module's constants; avoids an import cycle
        )

        x_name, ys = self._x_combo.currentText(), self.selected_y()
        if not x_name or not ys:
            raise ValueError(tr("xy.need_xy"))
        kind = self._map_kind()
        z_name = self._z_combo.currentText() or None
        if kind == "map" and not z_name:
            raise ValueError(tr("xyz.need_xyz"))
        reason = self._map_blocked_reason()
        if reason:
            raise ValueError(reason)
        three = self._mode == "3d"
        if three:
            ys = ys[:1]
        # an invalid grid is reported in the status line and nothing is computed (44.1)
        try:
            x_req = self._x_grid.request()
            y_req = self._y_grid.request() if kind == "map" else None
        except ValueError:
            self._grid_invalid = True  # the shown result no longer matches the settings: no export (44.1)
            self._update_export_action()
            raise
        self._grid_invalid = False
        self._update_export_action()
        agg, fill = self._agg_combo.currentData(), self._fill_combo.currentData()
        min_count = self._min_count_spin.value()
        keep_sparse = self._keep_sparse_check.isChecked() and self._keep_sparse_check.isEnabled()
        excluded = self._exclude.rows  # replaced, never mutated: safe to hand to the worker (44.13)
        main = self._main
        columns = self._resolve_columns([x_name, *ys, *([z_name] if kind == "map" else [])])
        time_name = main._time_column

        def fetch(name: str, a: int, b: int) -> np.ndarray:
            return columns[name].window(a, b)

        log_op(
            "xy_plot_compute", mode="map", kind=kind, view=("3d" if three else "2d"), x=x_name, y=ys,
            z=z_name if kind == "map" else None, row_start=row_start, row_end=row_end, agg=agg,
            min_count=min_count, fill=fill, keep_sparse_empty=keep_sparse, excluded_rows=int(len(excluded)),
            x_grid=self._x_grid.describe(),
            y_grid=self._y_grid.describe() if kind == "map" else None,
        )
        return lambda: xm.compute_xy_map(
            fetch, x_name, ys, z_name, kind, x_req, y_req, agg, min_count, fill, row_start, row_end,
            chunk_rows=xpw.XY_CHUNK_ROWS, scatter_limit=xpw.XY_SCATTER_LIMIT, cancelled=cancelled,
            time_name=time_name, is_3d=three, excluded_rows=excluded, keep_sparse_empty=keep_sparse,
        )

    def _show_map(self, r: xm.XYMapResult) -> None:
        self._hover_label.setText("")
        self._median_total = r.n_valid
        self._update_median_item()
        if r.median_refused:  # too many points for the median: back to the mean and say why
            self._agg_combo.blockSignals(True)
            self._agg_combo.setCurrentIndex(self._agg_combo.findData("mean"))
            self._agg_combo.blockSignals(False)
            self._map_notice = tr("xy.map_median_refused", limit=mg.MAP_MEDIAN_MAX_POINTS, n=r.n_valid)
            self.recompute(keep_range=True)
            return
        self._prune_hidden(r.names if r.is_curve else [], False)
        self._x_grid.set_auto(r.x_auto)
        self._y_grid.set_auto(r.y_auto)
        self._update_status_map(r)
        self._update_export_action()
        if r.is_3d:
            self._show_map_3d(r)
        else:
            self._draw_map_result(r)

    def _update_status_map(self, r: xm.XYMapResult) -> None:
        parts = [tr("xy.status_range", range=self._range_text(r), rows=r.row_end - r.row_start)]
        if r.n_user_excluded:
            shown = max((len(p) for p in r.excluded_point_rows), default=0)
            parts.append(tr("xy.map_status_user_excluded_thinned", n=r.n_user_excluded, shown=shown)
                         if r.excluded_thinned else tr("xy.map_status_user_excluded", n=r.n_user_excluded))
        if r.n_valid == 0:
            parts.append(tr("xy.status_no_points"))
        else:
            parts.append(tr("xy.map_status_points", valid=r.n_valid, excluded=r.n_excluded, outside=r.n_outside))
            if r.grids:
                measured, filled, empty = r.cell_counts()
                parts.append(tr("xy.map_status_cells", measured=measured, filled=filled, empty=empty))
            if r.no_data:
                parts.append(tr("xy.map_no_data"))
            if r.thinned:
                parts.append(tr("xy.status_thinned", shown=r.thinned[0], total=r.thinned[1]))
        if self._map_notice:
            parts.append(self._map_notice)
        one_shot = self._exclude.take_notice()  # "excluded points were restored": shown once (44.13 (7))
        if one_shot:
            parts.append(one_shot)
        self.set_status("  |  ".join(parts))

    def _clear_plot_items(self) -> None:
        plot = self._plot
        for item in list(plot.items):
            if item is not self._cursor_item:
                plot.removeItem(item)

    def _draw_map_result(self, r: xm.XYMapResult) -> None:
        """2D: heat map (or curves) of the aggregated grid; empty cells are not drawn."""
        plot = self._plot
        self._clear_plot_items()
        self._legend.clear()
        self._series_items.clear()
        self._legend_proxies.clear()
        self._colorbar.setVisible(False)
        self._cursor_item.setData([], [])
        for axis in ("bottom", "left"):
            plot.getAxis(axis).set_time_mode(False)
        plot.setLabel("bottom", r.x_name)
        if not r.grids or r.n_valid == 0 or r.no_data:
            plot.setLabel("left", ", ".join(r.y_names))
            self._draw_excluded_only(r)
        elif r.is_curve:
            self._draw_map_curves(r)
        else:
            self._draw_map_heat(r)
        self._overlays.refresh()
        self._on_cursors_changed(self._main.plot_grid.cursor_times())  # the cursor rows' (x, y) as in a scatter

    def _keep_view_now(self) -> bool:
        """The shown result comes from a click / rectangle recompute: keep the view range (44.13 (7))."""
        return self._keep_view_gen == self._generation

    def _draw_excluded_only(self, r: xm.XYMapResult) -> None:
        """No valid point (everything excluded, or no data): still draw the grey crosses so they can be
        clicked back (44.13 (7))."""
        if not (self._points_check.isChecked() and r.excluded_points):
            return
        xs, ys = [], []
        for pts in r.excluded_points:
            if len(pts[0]):
                self._plot.addItem(xex.excluded_item(pts[0], pts[1]))
                xs.append(pts[0])
                ys.append(pts[1])
        if xs and not self._keep_view_now():
            x, y = np.concatenate(xs), np.concatenate(ys)
            padx, pady = (np.ptp(x) or 1.0) * 0.1, (np.ptp(y) or 1.0) * 0.1
            self._plot.setRange(xRange=(float(x.min() - padx), float(x.max() + padx)),
                                yRange=(float(y.min() - pady), float(y.max() + pady)), padding=0)

    def _draw_map_heat(self, r: xm.XYMapResult) -> None:
        from . import (
            xy_plot_window as xpw,  # late: tests patch the module's constants; avoids an import cycle
        )

        plot = self._plot
        g = r.grids[0]
        plot.setLabel("left", r.y_names[0])
        xe, ye = mg.cell_edges(g.x_axis), mg.cell_edges(g.y_axis)
        levels = r.color_range() or (0.0, 1.0)  # 44.10: the same minimum-width rule as the image export
        img = pg.ImageItem(xm.map_rgba(g.value, levels, xpw._colormap()))
        img.setRect(QRectF(xe[0], ye[0], xe[-1] - xe[0], ye[-1] - ye[0]))
        img.setZValue(0)
        plot.addItem(img)
        self._map_items = {"image": img}
        if g.filled_mask.any():  # hatch = filled in, not measured
            hatch = xm.HatchItem(xe, ye, g.filled_mask)
            hatch.setZValue(1)
            plot.addItem(hatch)
            self._map_items["hatch"] = hatch
        if self._points_check.isChecked() and r.points and len(r.points[0][0]):
            pts = xex.PassiveScatter(r.points[0][0], r.points[0][1], size=2, pen=None, pxMode=True,
                                     brush=pg.mkBrush(90, 90, 90, 150))
            pts.setZValue(2)
            plot.addItem(pts)
            self._map_items["points"] = pts
        if self._points_check.isChecked() and r.excluded_points and len(r.excluded_points[0][0]):
            crosses = xex.excluded_item(r.excluded_points[0][0], r.excluded_points[0][1])  # 44.13
            plot.addItem(crosses)
            self._map_items["excluded"] = crosses
        self._set_colorbar(levels, time_ticks=False)
        if not self._keep_view_now():
            plot.setRange(xRange=(float(xe[0]), float(xe[-1])), yRange=(float(ye[0]), float(ye[-1])), padding=0)

    def _draw_map_curves(self, r: xm.XYMapResult) -> None:
        from . import (
            xy_plot_window as xpw,  # late: tests patch the module's constants; avoids an import cycle
        )

        plot = self._plot
        plot.setLabel("left", ", ".join(r.names))
        for i, (name, g) in enumerate(zip(r.names, r.grids)):
            base = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
            visible = self._legend_series_visible(name)
            line = plot.plot(g.x_axis, g.value, pen=pg.mkPen(base, width=2), connect="finite")  # empty = no line
            items = [line]
            m, f = g.measured_mask, g.filled_mask
            if m.any():
                items.append(xex.PassiveScatter(g.x_axis[m], g.value[m], size=8, pen=pg.mkPen(base),
                                                brush=pg.mkBrush(base), pxMode=True))
            if f.any():  # filled-in points are open circles
                items.append(xex.PassiveScatter(g.x_axis[f], g.value[f], size=8, pen=pg.mkPen(base, width=2),
                                                brush=pg.mkBrush("w"), pxMode=True))
            if self._points_check.isChecked() and r.points and len(r.points[i][0]):
                c = QColor(base)
                c.setAlpha(110)
                items.append(xex.PassiveScatter(r.points[i][0], r.points[i][1], size=2, pen=None, pxMode=True,
                                                brush=pg.mkBrush(c)))
            if self._points_check.isChecked() and i < len(r.excluded_points) and len(r.excluded_points[i][0]):
                items.append(xex.excluded_item(*r.excluded_points[i]))  # 44.13
            for it in items[1:]:
                plot.addItem(it)
            for it in items:
                it.setVisible(visible)
            self._series_items[name] = items
            proxy = pg.ScatterPlotItem(size=xpw.LEGEND_MARKER_SIZE, pen=None, brush=pg.mkBrush(base))
            proxy.setVisible(visible)
            self._legend_proxies[proxy] = name
            self._add_legend_entry(proxy, name)
        if self._keep_view_now():
            return
        xe = mg.cell_edges(r.grids[0].x_axis)
        plot.setRange(xRange=(float(xe[0]), float(xe[-1])), padding=0)
        yr = xm.curve_y_range(r, self._points_check.isChecked() and bool(r.points))  # 44.9
        if yr is not None:
            plot.setRange(yRange=yr, padding=0)

    def _show_map_3d(self, r: xm.XYMapResult) -> None:
        self._draw_scene_3d(r)
        self._overlays.refresh(rebase=False)

    def _show_map_3d_scene(self, r: xm.XYMapResult, view) -> None:
        g = r.grids[0]

        def extra(iy: int, ix: int) -> str:
            return tr("xy.map_hover_extra", count=int(g.count[iy, ix]), state=xm.cell_state(g, (iy, ix)))

        rng = r.color_range()  # 44.11: the minimum-width rule reaches the colouring and the Z axis alike
        wide = self._overlays.widened_bounds(self._map_bounds_3d(r)).get("z")
        z_range = wide if (rng is not None and wide is not None) else rng
        view.set_surface(g.x_axis, g.y_axis, g.value, labels=(r.x_name, r.y_names[0], r.z_name or "Z"), extra=extra,
                         z_range=z_range)
        rng = z_range or rng
        view.set_colorbar(rng if rng and rng[1] > rng[0] else None)
        if self._points_check.isChecked() and r.points and len(r.points[0]) == 3:
            px, py, pz = r.points[0]
            view.add_points(px, py, pz)

    def _map_bounds_3d(self, r: xm.XYMapResult) -> dict:
        g = r.grids[0]
        rng = r.color_range() or (0.0, 1.0)  # 44.11
        return {"x": (float(g.x_axis.min()), float(g.x_axis.max())), "y": (float(g.y_axis.min()), float(g.y_axis.max())),
                "z": rng}

    def _on_mouse_moved(self, pos) -> None:
        """Hover readout of the 2D map (44.3): the cell under the mouse, read from the computed grid
        (never from disk)."""
        r = self._result
        if self._closed or not isinstance(r, xm.XYMapResult) or r.is_3d:
            return
        if not self._plot.vb.sceneBoundingRect().contains(pos):
            self._hover_label.setText("")
            return
        pt = self._plot.vb.mapSceneToView(pos)
        self._hover_label.setText(xm.map_hover_text(r, pt.x(), pt.y(), self._hidden_y))

    def _add_map_actions(self) -> None:
        """"Export as map..." in the toolbar (44.4); only with the flag, enabled while a map or curve
        with data is shown."""
        self.export_map_action = QAction(tr("xy.export_map"), self)
        self.export_map_action.setEnabled(False)
        self.export_map_action.triggered.connect(lambda *_: self.export_map_dialog())
        if self._map_enabled:
            self.findChild(QToolBar, "analysis_toolbar").addAction(self.export_map_action)

    def _update_export_action(self) -> None:
        if not hasattr(self, "export_map_action"):
            return
        r = self._result
        ok = isinstance(r, xm.XYMapResult) and not r.no_data and bool(r.grids) and not self._grid_invalid
        self.export_map_action.setEnabled(bool(ok))

    def export_map_dialog(self) -> bool:
        """Open the export dialog for the map / curve on screen (44.4)."""
        from .map_export_dialog import run_export_dialog

        r = self._result
        if not (isinstance(r, xm.XYMapResult) and not r.no_data) or self._grid_invalid:
            return False
        return run_export_dialog(self, r)
