from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr

_COLUMN_KEYS = [
    "stats.col_variable",
    "stats.col_count",
    "stats.col_nan",
    "stats.col_min",
    "stats.col_max",
    "stats.col_mean",
    "stats.col_std",
    "stats.col_median",
]


class StatsDialog(QDialog):
    """Separate, non-modal window showing summary statistics for one or more selected
    variables over the currently visible time range (specification.md 5.x). Computed
    from the full underlying data for that range, not the downsampled curve actually
    drawn on screen (core/analysis.py basic_stats, via core/background_worker.py).
    """

    def __init__(self, range_label: str, results: dict[str, dict], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("toolbar.stats"))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("stats.range_label", range=range_label)))

        columns = [tr(key) for key in _COLUMN_KEYS]
        table = QTableWidget(len(results), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, (name, stats) in enumerate(results.items()):
            values = [
                name,
                str(stats["count"]),
                str(stats["nan_count"]),
                f"{stats['min']:.6g}",
                f"{stats['max']:.6g}",
                f"{stats['mean']:.6g}",
                f"{stats['std']:.6g}",
                f"{stats['median']:.6g}",
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)

        self.resize(680, 140 + 28 * len(results))
