"""The XY window's overlay of registered maps / curves: menu, drawing, contour levels (analysis.ja.md 44.7,
44.10; split off by 44.12).

`OverlayState` (xy_overlay.py, Qt-free) keeps the decisions; `XYOverlayController` owns the "Overlay" menu
button, the drawn items and the legend proxies, and draws them through the small `OverlayHost` interface
that `XYPlotWindow` implements (so it never reaches into the window's other members).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QMenu, QPushButton, QWidget

from ..core.contour_levels import LevelSpec
from ..core.oplog import log_op
from . import xy_map as xm
from . import xy_overlay as xo
from .i18n import tr
from .xyz_3d_window import Xyz3DResult


class OverlayHost(Protocol):
    """What the controller needs from the window (`XYPlotWindow`)."""

    @property
    def mode(self) -> str: ...  # "2d" / "3d"

    @property
    def result(self): ...  # the last XYResult / Xyz3DResult / XYMapResult, None before the first compute

    @property
    def plot(self) -> pg.PlotItem: ...

    @property
    def legend(self) -> pg.LegendItem: ...

    @property
    def view3d(self): ...  # Xyz3DView or None

    @property
    def closed(self) -> bool: ...

    @property
    def status_core(self) -> str: ...  # the status text without the overlay notice

    def selected_y(self) -> list[str]: ...

    def x_name(self) -> str: ...

    def registry_tables(self) -> dict: ...  # the main window's maps / curves, by name

    def add_legend_entry(self, proxy, label: str) -> None: ...

    def redraw_scene_3d(self, r) -> None: ...  # the 3D base scene (its Z range depends on the overlays)

    def map_bounds_3d(self, r) -> dict: ...

    def set_status(self, text: str) -> None: ...

    def dialog_parent(self) -> QWidget: ...


class XYOverlayController:
    """Overlay button / menu, drawing and contour levels for one XY window."""

    def __init__(self, host: OverlayHost) -> None:
        self.host = host
        self.state = xo.OverlayState()
        self.items: dict[str, list] = {}  # name -> the plot items drawn (2D)
        self.proxies: dict[object, str] = {}  # legend swatch item -> overlay name
        self.menu: QMenu | None = None  # None without the `map_from_xy` flag: no overlay at all
        self.button: QPushButton | None = None

    # -- menu ----------------------------------------------------------------------------

    def build_button(self, flow) -> None:
        """"Overlay" menu button: registered maps and curves as checkable entries, then "Contour levels..."."""
        self.button = QPushButton(tr("xy.overlay_button"))
        self.button.setObjectName("xy_overlay_button")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu = QMenu(self.button)
        self.menu.setObjectName("xy_overlay_menu")
        self.menu.setToolTipsVisible(True)
        self.menu.aboutToShow.connect(self.rebuild_menu)
        self.button.setMenu(self.menu)
        flow.addWidget(self.button)
        self.rebuild_menu()

    def rebuild_menu(self) -> None:
        menu = self.menu
        if menu is None:
            return
        menu.clear()
        three = self.host.mode == "3d"
        tables = self.host.registry_tables()
        for name in sorted(tables):
            t = tables[name]
            kind = tr("xy.overlay_kind_curve") if t.kind == "curve" else tr("xy.overlay_kind_map")
            act = QAction(f"{name} ({kind})", menu)
            act.setObjectName(f"xy_overlay_{name}")
            act.setCheckable(True)
            act.setChecked(name in self.state.names)
            if name in self.state.colors:  # swatch of the assigned colour (3D has no legend)
                pix = QPixmap(14, 14)
                pix.fill(QColor(self.state.colors[name]))
                act.setIcon(QIcon(pix))
            if three and t.kind == "curve":  # a curve has no place in the 3D scene (44.7)
                act.setEnabled(False)
                act.setToolTip(tr("xy.overlay_curve_3d"))
            act.toggled.connect(lambda on, n=name: self.set_overlay(n, on))
            menu.addAction(act)
        if tables:
            menu.addSeparator()
        act = QAction(tr("xy.levels_menu"), menu)
        act.setObjectName("xy_overlay_levels_action")
        act.triggered.connect(lambda *_: self.choose_contour_levels())
        menu.addAction(act)

    # -- public operations (the window delegates to these) ---------------------------------

    def names(self) -> list[str]:
        return list(self.state.names)

    def set_overlay(self, name: str, on: bool) -> None:
        """Add / remove a registered table as an overlay (44.7)."""
        host, state = self.host, self.state
        if self.menu is None or on == (name in state.names):
            return
        t = host.registry_tables().get(name)
        if on:
            if t is None or (host.mode == "3d" and t.kind == "curve"):
                return
            log_op("xy_plot_overlay", added=[name], removed=[])
            # colours the Y series of the display use are left alone (the first overlay is orange with one Y)
            n_y = 0 if host.mode == "3d" else len(host.selected_y())
            state.add(name, n_y)
            self.rebuild_menu()
        else:
            log_op("xy_plot_overlay", added=[], removed=[name])
            state.remove(name)
            self.rebuild_menu()
        self.refresh()

    def set_hidden(self, name: str, hidden: bool) -> None:
        """Hide / show one overlay (legend click, 44.7). Levels are planned over the visible maps only,
        so the lines are redrawn."""
        if hidden:
            self.state.hidden.add(name)
        else:
            self.state.hidden.discard(name)
        self.refresh()

    def set_contour_spec(self, spec: LevelSpec) -> None:
        log_op("xy_plot_contour_levels", **spec.describe())
        self.state.contour_spec = spec
        self.refresh()

    def choose_contour_levels(self) -> bool:
        """The "Contour levels..." dialog; the accepted spec applies to every overlaid map (and the image)."""
        from .contour_levels_dialog import ContourLevelsDialog

        tables = {n: t for n, t in self.host.registry_tables().items() if n in self.state.names}
        ranges = {n: r for n, t in tables.items() if t.kind == "map" and (r := xo.value_range(t)) is not None}
        dlg = ContourLevelsDialog(self.state.contour_spec, ranges, self.host.dialog_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self.set_contour_spec(dlg.spec())
        return True

    def on_registry_changed(self) -> None:
        """The main window's maps changed (edit / delete / overwrite, 27.8): redraw or drop overlays."""
        if self.host.closed:
            return
        self.state.prune_cache(self.host.registry_tables())
        self.rebuild_menu()
        self.refresh()

    def entries(self) -> list[xo.OverlayEntry]:
        """The overlays still valid for the current mode, in order; vanished / unusable ones are dropped."""
        host = self.host
        ys = host.selected_y()
        return self.state.entries(host.registry_tables(), host.mode, host.x_name(), ys[0] if ys else None)

    def is_hidden(self, name: str) -> bool:
        return self.state.is_hidden(name, self.host.mode)

    def planned_levels(self, entries) -> dict[str, list[float]]:
        return self.state.plan_levels(entries, self.host.mode)

    def contour_plan(self) -> dict[str, list[float]]:
        """The contour values per visible overlaid map, as drawn and as exported."""
        return self.planned_levels(self.entries())

    # -- 3D Z range ------------------------------------------------------------------------

    def widened_bounds(self, bounds: dict) -> dict:
        """`bounds` with Z widened to hold the visible map overlays inside the displayed X / Y range (44.7)."""
        if self.host.mode != "3d" or not bounds or "z" not in bounds:
            return bounds
        lo, hi = bounds["z"]
        for e in self.entries():
            if e.table.kind != "map":
                continue
            t = e.table
            ix = np.flatnonzero((t.x >= bounds["x"][0]) & (t.x <= bounds["x"][1]))
            iy = np.flatnonzero((t.y >= bounds["y"][0]) & (t.y <= bounds["y"][1]))
            if len(ix) < 2 or len(iy) < 2:
                continue
            v = t.z[np.ix_(iy, ix)]
            v = v[np.isfinite(v)]
            if v.size:
                lo, hi = min(lo, float(v.min())), max(hi, float(v.max()))
        return {**bounds, "z": (lo, hi)}

    def _rebase_3d_if_needed(self) -> None:
        """The overlays changed: redraw the base scene when its Z range must grow / shrink to fit them."""
        r, view = self.host.result, self.host.view3d
        if view is None or not view.scene.get("bounds"):
            return
        if isinstance(r, Xyz3DResult) and r.valid:
            base = r.bounds
        elif isinstance(r, xm.XYMapResult) and r.is_3d and r.grids and not r.no_data and r.n_valid:
            base = self.host.map_bounds_3d(r)
        else:
            return
        want = self.widened_bounds(base)["z"]
        have = view.scene["bounds"]["z"]
        if not np.allclose(want, have, rtol=1e-12, atol=0.0):
            self.host.redraw_scene_3d(r)

    # -- drawing -----------------------------------------------------------------------------

    def refresh(self, rebase: bool = True) -> None:
        """(Re)draw every overlay over the current display. Called after each draw of the base display
        and when overlays, levels or the registry change; cheap (contours are cached per table and level)."""
        host = self.host
        if host.closed or self.menu is None:
            return
        if rebase and host.mode == "3d" and host.result is not None:
            self._rebase_3d_if_needed()
        self.remove_items()
        entries = self.entries()
        plan = self.planned_levels(entries)
        self.state.levels = plan
        if entries and host.result is not None:
            if host.mode == "3d":
                self._draw_3d(entries)
            else:
                self._draw_2d(entries, plan)
        host.set_status(host.status_core)  # re-append the overlay notice

    def remove_items(self) -> None:
        host = self.host
        plot = host.plot
        for items in self.items.values():
            for it in items:
                if it in plot.items:
                    plot.removeItem(it)
        self.items.clear()
        for proxy in self.proxies:
            try:
                host.legend.removeItem(proxy)
            except (ValueError, KeyError, RuntimeError):
                pass
        self.proxies.clear()
        if host.view3d is not None:
            host.view3d.clear_overlays()

    def _draw_2d(self, entries, plan) -> None:
        plot = self.host.plot
        for e in entries:
            t = e.table
            pen = pg.mkPen(e.color, width=2)
            items: list = []
            if t.kind == "curve":
                fin = np.isfinite(t.y)
                items.append(pg.PlotDataItem(t.x, t.y, pen=pen, connect="finite"))
                items.append(pg.ScatterPlotItem(t.x[fin], t.y[fin], size=6, pen=pg.mkPen(e.color),
                                                brush=pg.mkBrush(e.color), pxMode=True))
            else:
                for level in plan.get(e.name, []):
                    iso = xo.MapIsocurveItem(t.x, t.y, t.z, level, pen, segments=self.state.segments(e.name, t, level))
                    items.append(iso)
                    pos = iso.label_position()
                    if pos is not None and all(map(np.isfinite, pos)):
                        lab = pg.TextItem(f"{level:g}", color=e.color, anchor=(0.5, 0.5))
                        lab.setPos(*pos)
                        items.append(lab)
            hidden = self.is_hidden(e.name)
            for it in items:
                it.setZValue(5)
                plot.addItem(it, ignoreBounds=True)  # an overlay never moves the view
                it.setVisible(not hidden)
            proxy = pg.PlotDataItem([], [], pen=pen)
            proxy.setVisible(not hidden)
            self.proxies[proxy] = e.name
            self.host.add_legend_entry(proxy, e.label)
            self.items[e.name] = items

    def _draw_3d(self, entries) -> None:
        view = self.host.view3d
        if view is None:
            return
        for e in entries:
            if e.table.kind != "map":
                continue
            rgba = QColor(e.color).getRgbF()
            view.add_surface_overlay(e.table.x, e.table.y, e.table.z, (rgba[0], rgba[1], rgba[2], 0.4))
