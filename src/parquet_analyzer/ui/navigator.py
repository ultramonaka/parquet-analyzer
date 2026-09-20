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
        self._x_full = x
        x_ds, y_ds = lttb(x, y, _OVERVIEW_POINTS)
        self._curve.setData(x_ds, y_ds)
        if len(x):
            self._region.setBounds([x[0], x[-1]])
            self.set_selected_range(x[0], x[-1])

    def set_selected_range(self, x_min: float, x_max: float) -> None:
        self._region.blockSignals(True)
        self._region.setRegion((x_min, x_max))
        self._region.blockSignals(False)

    def _on_region_changed(self) -> None:
        x_min, x_max = self._region.getRegion()
        self.rangeSelected.emit(x_min, x_max)
