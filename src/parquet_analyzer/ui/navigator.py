from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal

from ..core.downsample import lttb

_OVERVIEW_POINTS = 2000  # coarse is fine (specification.md 5.2)


class NavigatorWidget(pg.PlotWidget):
    """Single shared overview + draggable window over the full time range
    (specification.md 5.2). Only the X (time) axis is interactive; Y is fixed.
    """

    # Emitted only by the user's drag/click on the window -- never by set_overview_data /
    # set_selected_range (programmatic changes are silent; plots 24.9).
    rangeSelected = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent, axisItems={"bottom": pg.DateAxisItem()})
        self.setMaximumHeight(100)
        self.setMenuEnabled(False)
        self.hideAxis("left")
        self.getViewBox().setMouseEnabled(x=False, y=False)

        self._curve = self.plot([], [], pen=pg.mkPen(color="#888888", width=1))
        self._region = pg.LinearRegionItem()
        self._region.sigRegionChanged.connect(self._on_region_changed)
        self.addItem(self._region)
        self._x_full: np.ndarray | None = None
        self._has_overview = False  # a finite overview has been shown (keep_region has a region to keep)

    def set_overview_data(self, x: np.ndarray, y: np.ndarray, *, keep_region: bool = True) -> None:
        """Overview curve + region bounds. Only points whose x and y are both finite are
        used, and the bounds are the finite min/max of x (not x[0]/x[-1], which may be NaN
        or not the extremes); no finite x -> empty overview, range left alone
        (detailed_specification.md 29.4).

        The selection window: keep_region=True (default) keeps the current one, clamped into
        the new bounds (full extent on the first overview, or when it no longer overlaps the
        new bounds at all); keep_region=False = full extent ("home", plots 24.9). Bounds and
        region are changed with the region's signals blocked: rangeSelected is emitted only
        by the user's drag/click, never by this call (LinearRegionItem.setBounds moves the
        lines and emits sigRegionChanged when the region exceeds the new bounds)."""
        x = np.asarray(x)
        y = np.asarray(y)
        self._x_full = x
        n = min(len(x), len(y))
        x_c, y_c = x[:n], y[:n]
        finite = np.isfinite(x_c) & np.isfinite(y_c)
        if not finite.all():
            x_c, y_c = x_c[finite], y_c[finite]
        x_ds, y_ds = lttb(x_c, y_c, _OVERVIEW_POINTS)
        self._curve.setData(x_ds, y_ds)
        x_ok = x[np.isfinite(x)]
        if not len(x_ok):
            return
        lo, hi = float(x_ok.min()), float(x_ok.max())
        target = (lo, hi)
        if keep_region and self._has_overview:
            cur_lo, cur_hi = self._region.getRegion()
            c_lo, c_hi = max(float(cur_lo), lo), min(float(cur_hi), hi)
            if c_lo <= c_hi and np.isfinite(c_lo) and np.isfinite(c_hi):
                target = (c_lo, c_hi)
        self._region.blockSignals(True)
        try:
            self._region.setBounds([lo, hi])
            self._region.setRegion(target)
        finally:
            self._region.blockSignals(False)
        self._has_overview = True

    def set_selected_range(self, x_min: float, x_max: float) -> None:
        if not (np.isfinite(x_min) and np.isfinite(x_max)):
            return  # never hand pyqtgraph a non-finite region bound (data 29.4)
        self._region.blockSignals(True)
        self._region.setRegion((x_min, x_max))
        self._region.blockSignals(False)

    def selected_range(self) -> tuple[float, float]:
        """The selection window's current (x_min, x_max) (read by the operation log, 30.2)."""
        lo, hi = self._region.getRegion()
        return float(lo), float(hi)

    def _on_region_changed(self) -> None:
        x_min, x_max = self._region.getRegion()
        self.rangeSelected.emit(x_min, x_max)
