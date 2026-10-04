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

    def set_overview_data(self, x: np.ndarray, y: np.ndarray) -> None:
        """Overview curve + region bounds. Only points whose x and y are both finite are
        used, and the bounds/initial selection are the finite min/max of x (not x[0]/x[-1],
        which may be NaN or not the extremes); no finite x -> empty overview, range left
        alone (detailed_specification.md 29.4)."""
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
        if len(x_ok):
            lo, hi = float(x_ok.min()), float(x_ok.max())
            self._region.setBounds([lo, hi])
            self.set_selected_range(lo, hi)

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
