"""Histogram window (analysis.ja.md 5.33 / 42; an analysis window, 25.1).

Distribution of one or more variables over the visible time range, drawn as overlaid
semi-transparent step histograms: plain (per-row values, counted by rows or by time) or bucketed
(the time axis is cut into fixed lengths, each bucket reduced to one value, and the distribution
of those values is drawn). The computation is `core/histogram.py`; this module owns the settings
panel, the display (with the mean / median lines and a hover label below the plot) and the
image export.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import histogram_export as hexport
from ..core.histogram import (
    HIST_MAX_BINS,
    HIST_MIN_BINS,
    HIST_TREND_BAR_MAX,
    INTEGRAL_UNITS,
    STAGE2_AGGREGATIONS,
    Cancelled,
    HistogramError,
    HistogramResult,
    HistogramSettings,
    TimeFilter,
    active_filter,
    compute_histogram,
    validate_bucket,
    validate_two_stage,
)
from ..core.oplog import log_op
from . import messages
from .analysis_window import CANCELLED, AnalysisWindow
from .flow_layout import FlowLayout
from .i18n import tr
from .image_export import _FIG_DPI, _font_context, _local_offset_s, _to_local_datetime64
from .plot_widget import _COLOR_CYCLE

logger = logging.getLogger(__name__)

_MODES = ("plain", "bucket")
_WEIGHTS = ("rows", "time")
_BUCKET_PRESETS = (("1min", 60.0), ("10min", 600.0), ("1h", 3600.0), ("1d", 86400.0))
_CUSTOM_UNITS = (("min", 60.0), ("h", 3600.0), ("d", 86400.0))
_AGGS = ("sum", "integral", "mean", "max", "min")
# Two-stage (42.8): first-stage window presets (seconds, i18n key) and the units of the free input.
_STAGE1_PRESETS = (
    (60.0, "hist.bucket_1min"), (180.0, "hist.stage1_3min"), (300.0, "hist.stage1_5min"),
    (600.0, "hist.bucket_10min"), (900.0, "hist.stage1_15min"), (1800.0, "hist.stage1_30min"),
    (3600.0, "hist.bucket_1h"),
)
_STAGE1_DEFAULT = 180.0
_STAGE1_CUSTOM_UNITS = (("min", 60.0), ("h", 3600.0))
_UNIT_KEYS = ("s", "min", "h")
_FILL_ALPHA = 90
_VIEWS = ("dist", "duration", "trend")
_WEEKDAY_PRESETS = {"all": (0, 1, 2, 3, 4, 5, 6), "weekdays": (0, 1, 2, 3, 4), "weekend": (5, 6)}
_ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)
_BAR_FILL = 0.95  # share of a bar's slot that is filled; the rest is the gap between neighbours (42.12)


@dataclass
class _Refused:
    """A job outcome that is not a failure: the range/setting cannot be computed (42.3)."""

    code: str
    info: dict = field(default_factory=dict)


def _time_unit_for(max_seconds: float) -> tuple[str, float]:
    """(unit key, divisor) that makes a time-weighted count readable: seconds / minutes / hours."""
    if max_seconds >= 2 * 3600:
        return "h", 3600.0
    if max_seconds >= 2 * 60:
        return "min", 60.0
    return "s", 1.0


def _length_text(seconds: float) -> str:
    """"1 day" / "6 h" / "10 min" for a bucket length (localized unit)."""
    for key, div in (("d", 86400.0), ("h", 3600.0), ("min", 60.0)):
        if seconds >= div and abs(seconds / div - round(seconds / div)) < 1e-9:
            return f"{round(seconds / div)}{tr('hist.unit_' + key)}"
    return f"{seconds:g}{tr('hist.unit_s')}"


def _fmt(v: float) -> str:
    return "-" if not math.isfinite(v) else f"{v:.6g}"


class HistogramWindow(AnalysisWindow):
    """The histogram window (42.1-42.5)."""

    def __init__(self, main, names: list[str] | None = None):
        self._init_names = list(names or [])
        self._result: HistogramResult | None = None
        self._refresh_pending = False
        self._lines_visible: list[pg.InfiniteLine] = []
        super().__init__(main, tr("hist.title"))
        self._export_dir: str | None = None  # last folder used by "save bucket values" (this window only, 42.8)
        self._build_export_action()
        self._apply_enabled()
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._trend_plot.scene().sigMouseMoved.connect(self._on_trend_mouse_moved)
        model = main.variable_panel.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            sig.connect(self._schedule_refresh)
        log_op("histogram_open", variables=self.selected_names(), window_count=len(main._analysis_windows) + 1)

    # -- candidates ----------------------------------------------------------------

    def _candidates(self) -> list[str]:
        """Raw + derived variables, the time-axis column excluded (42.2)."""
        return self._main.visible_column_names()  # aliases instead of their sources (persistence 54.2)

    def used_variable_names(self) -> set[str]:
        """Names this window currently uses (for MainWindow's alias re-resolution, 54.4)."""
        return set(self.selected_names())

    def selected_names(self) -> list[str]:
        return [i.text() for i in self._var_list.selectedItems()]

    # -- settings panel ------------------------------------------------------------

    def _group(self, flow, label: str, *widgets: QWidget) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        if label:
            lab = QLabel(label)
            align = Qt.AlignmentFlag.AlignTop if isinstance(widgets[0], QListWidget) else Qt.AlignmentFlag.AlignVCenter
            lay.addWidget(lab, 0, align)
        for w in widgets:
            lay.addWidget(w)
        flow.addWidget(box)
        return box

    @staticmethod
    def _combo(items: list[tuple[str, object]]) -> QComboBox:
        combo = QComboBox()
        for text, data in items:
            combo.addItem(text, data)
        return combo

    def build_settings_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("histogram_settings")
        flow = FlowLayout(panel, 0, 14, 4)
        self._flow = flow

        # The display kind comes first (42.11): distribution / duration curve / trend.
        self._view_combo = self._combo([
            (tr("hist.view_dist"), "dist"), (tr("hist.view_duration"), "duration"), (tr("hist.view_trend"), "trend"),
        ])
        self._view_combo.setObjectName("histogram_view")
        self._view_combo.setItemData(2, tr("hist.view_trend_tooltip"), Qt.ItemDataRole.ToolTipRole)
        self._group(flow, tr("hist.view"), self._view_combo)
        self._mode_before_trend: str | None = None  # the aggregation to return to when leaving "trend"

        names = self._candidates()
        self._var_list = QListWidget()
        self._var_list.setObjectName("histogram_variables")
        self._var_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._var_list.setMaximumHeight(72)
        self._var_list.setMinimumWidth(130)
        self._var_list.setMaximumWidth(240)
        self._var_list.addItems(names)
        for i in range(self._var_list.count()):
            if self._var_list.item(i).text() in self._init_names:
                self._var_list.item(i).setSelected(True)
        self._group(flow, tr("hist.variables"), self._var_list)

        self._mode_combo = self._combo([(tr("hist.mode_plain"), "plain"), (tr("hist.mode_bucket"), "bucket")])
        self._mode_combo.setObjectName("histogram_mode")
        self._mode_group = self._group(flow, tr("hist.mode"), self._mode_combo)

        self._weight_combo = self._combo([(tr("hist.weight_rows"), "rows"), (tr("hist.weight_time"), "time")])
        self._weight_combo.setObjectName("histogram_weight")
        self._weight_group = self._group(flow, tr("hist.weight"), self._weight_combo)

        self._bucket_combo = self._combo(
            [(tr(f"hist.bucket_{key}"), sec) for key, sec in _BUCKET_PRESETS] + [(tr("hist.bucket_custom"), None)]
        )
        self._bucket_combo.setObjectName("histogram_bucket")
        self._bucket_combo.setCurrentIndex(len(_BUCKET_PRESETS) - 1)  # 1 day
        self._custom_value = QDoubleSpinBox()
        self._custom_value.setObjectName("histogram_bucket_value")
        self._custom_value.setDecimals(3)
        self._custom_value.setRange(0.001, 100000.0)
        self._custom_value.setValue(30.0)
        self._custom_unit = self._combo([(tr(f"hist.unit_{k}"), sec) for k, sec in _CUSTOM_UNITS])
        self._custom_unit.setObjectName("histogram_bucket_unit")
        self._bucket_error = QLabel("")
        self._bucket_error.setObjectName("histogram_bucket_error")
        self._bucket_error.setStyleSheet("color: #c0392b;")
        self._bucket_group = self._group(
            flow, tr("hist.bucket"), self._bucket_combo, self._custom_value, self._custom_unit, self._bucket_error
        )

        self._origin_edit = QTimeEdit(QTime(0, 0))
        self._origin_edit.setObjectName("histogram_origin")
        self._origin_edit.setDisplayFormat("HH:mm")
        self._origin_group = self._group(flow, tr("hist.origin"), self._origin_edit)

        # Two-stage aggregation (42.8): the checkbox, the stage-1 window (presets + free input in
        # minutes / hours) with its reason label, and the stage-1 reduction. The existing "reduce
        # by" combo below then means the stage-2 reduction.
        self._two_check = QCheckBox(tr("hist.two_stage"))
        self._two_check.setObjectName("histogram_two_stage")
        self._two_check.setToolTip(tr("hist.two_stage_tooltip"))
        self._two_group = self._group(flow, "", self._two_check)

        self._stage1_combo = self._combo(
            [(tr(key), sec) for sec, key in _STAGE1_PRESETS] + [(tr("hist.bucket_custom"), None)]
        )
        self._stage1_combo.setObjectName("histogram_stage1")
        self._stage1_combo.setCurrentIndex(self._stage1_combo.findData(_STAGE1_DEFAULT))
        self._stage1_value = QDoubleSpinBox()
        self._stage1_value.setObjectName("histogram_stage1_value")
        self._stage1_value.setDecimals(3)
        self._stage1_value.setRange(0.001, 100000.0)
        self._stage1_value.setValue(3.0)
        self._stage1_unit = self._combo([(tr(f"hist.unit_{k}"), sec) for k, sec in _STAGE1_CUSTOM_UNITS])
        self._stage1_unit.setObjectName("histogram_stage1_unit")
        self._stage1_error = QLabel("")
        self._stage1_error.setObjectName("histogram_stage1_error")
        self._stage1_error.setStyleSheet("color: #c0392b;")
        self._stage1_group = self._group(
            flow, tr("hist.stage1"), self._stage1_combo, self._stage1_value, self._stage1_unit, self._stage1_error
        )

        self._stage1_agg_combo = self._combo([(tr(f"hist.agg_{a}"), a) for a in _AGGS])
        self._stage1_agg_combo.setObjectName("histogram_stage1_agg")
        self._stage1_agg_combo.setCurrentIndex(self._stage1_agg_combo.findData("mean"))
        self._stage1_agg_group = self._group(flow, tr("hist.stage1_agg"), self._stage1_agg_combo)

        self._agg_combo = self._combo([(tr(f"hist.agg_{a}"), a) for a in _AGGS])
        self._agg_combo.setObjectName("histogram_agg")
        self._agg_combo.setToolTip(tr("hist.agg_sum_tooltip"))
        self._agg_group = self._group(flow, tr("hist.agg"), self._agg_combo)
        self._agg_label = self._agg_group.findChild(QLabel)
        self._single_agg = "sum"  # remembered while the two-stage items (no integral) are shown

        self._unit_combo = self._combo([(tr(f"hist.unit_{k}"), INTEGRAL_UNITS[k]) for k in _UNIT_KEYS])
        self._unit_combo.setObjectName("histogram_integral_unit")
        self._unit_combo.setCurrentIndex(_UNIT_KEYS.index("h"))
        self._unit_group = self._group(flow, tr("hist.integral_unit"), self._unit_combo)

        self._exclude_check = QCheckBox(tr("hist.exclude_incomplete"))
        self._exclude_check.setObjectName("histogram_exclude_incomplete")
        self._exclude_group = self._group(flow, "", self._exclude_check)

        self._gap_combo = self._combo([(tr("hist.auto"), "auto"), (tr("hist.specify"), "value")])
        self._gap_combo.setObjectName("histogram_gap_mode")
        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setObjectName("histogram_gap_value")
        self._gap_spin.setDecimals(3)
        self._gap_spin.setRange(0.001, 1e9)
        self._gap_spin.setValue(60.0)
        self._gap_spin.setSuffix(" " + tr("hist.unit_s"))
        self._gap_group = self._group(flow, tr("hist.gap"), self._gap_combo, self._gap_spin)
        self._gap_group.setToolTip(tr("hist.gap_tooltip"))

        self._bins_combo = self._combo([(tr("hist.auto"), "auto"), (tr("hist.specify"), "value")])
        self._bins_combo.setObjectName("histogram_bins_mode")
        self._bins_spin = QSpinBox()
        self._bins_spin.setObjectName("histogram_bins_value")
        self._bins_spin.setRange(HIST_MIN_BINS, HIST_MAX_BINS)
        self._bins_spin.setValue(50)
        self._bins_group = self._group(flow, tr("hist.bins"), self._bins_combo, self._bins_spin)

        self._yaxis_combo = self._combo([(tr("hist.y_count"), "count"), (tr("hist.y_percent"), "percent")])
        self._yaxis_combo.setObjectName("histogram_yaxis")
        self._yaxis_group = self._group(flow, tr("hist.yaxis"), self._yaxis_combo)

        # Time-of-day / weekday filter (42.10): the checkbox, the range, the weekday preset + toggles.
        self._tf_check = QCheckBox(tr("hist.time_filter"))
        self._tf_check.setObjectName("histogram_time_filter")
        self._tf_check.setToolTip(tr("hist.time_filter_tooltip"))
        self._group(flow, "", self._tf_check)
        self._tf_start = QTimeEdit(QTime(0, 0))
        self._tf_start.setObjectName("histogram_tf_start")
        self._tf_start.setDisplayFormat("HH:mm")
        self._tf_end = QTimeEdit(QTime(0, 0))
        self._tf_end.setObjectName("histogram_tf_end")
        self._tf_end.setDisplayFormat("HH:mm")
        self._tf_range_group = self._group(flow, tr("hist.tf_range"), self._tf_start, QLabel(tr("hist.tf_to")), self._tf_end)
        self._tf_range_group.setToolTip(tr("hist.tf_range_tooltip"))
        self._tf_preset = self._combo([
            (tr("hist.tf_preset_all"), "all"), (tr("hist.tf_preset_weekdays"), "weekdays"),
            (tr("hist.tf_preset_weekend"), "weekend"), (tr("hist.tf_preset_custom"), "custom"),
        ])
        self._tf_preset.setObjectName("histogram_tf_preset")
        self._tf_day_buttons: list[QToolButton] = []
        for d in _ALL_DAYS:
            btn = QToolButton()
            btn.setObjectName(f"histogram_tf_day_{d}")
            btn.setText(tr(f"hist.wd_{d}"))
            btn.setCheckable(True)
            btn.setChecked(True)
            self._tf_day_buttons.append(btn)
        self._tf_days_group = self._group(flow, tr("hist.tf_weekdays"), self._tf_preset, *self._tf_day_buttons)

        # Every control logs `histogram_settings` (item + value) and recomputes with the last
        # range (25.1); the Y-axis kind only redraws the shown result.
        def hook(widget, signal, item, getter):
            signal.connect(lambda *_a, w=widget, i=item, g=getter: self._on_setting_changed(i, g()))

        hook(self._var_list, self._var_list.itemSelectionChanged, "variables", self.selected_names)
        hook(self._mode_combo, self._mode_combo.currentIndexChanged, "mode", self._mode_combo.currentData)
        hook(self._weight_combo, self._weight_combo.currentIndexChanged, "weight", self._weight_combo.currentData)
        hook(self._bucket_combo, self._bucket_combo.currentIndexChanged, "bucket", self.bucket_seconds)
        hook(self._custom_value, self._custom_value.valueChanged, "bucket", self.bucket_seconds)
        hook(self._custom_unit, self._custom_unit.currentIndexChanged, "bucket", self.bucket_seconds)
        hook(self._origin_edit, self._origin_edit.timeChanged, "origin", lambda: self._origin_edit.time().toString("HH:mm"))
        hook(self._agg_combo, self._agg_combo.currentIndexChanged, "agg", self._agg_combo.currentData)
        self._two_check.toggled.connect(self._on_two_stage_toggled)
        hook(self._stage1_combo, self._stage1_combo.currentIndexChanged, "stage1", self.stage1_seconds)
        hook(self._stage1_value, self._stage1_value.valueChanged, "stage1", self.stage1_seconds)
        hook(self._stage1_unit, self._stage1_unit.currentIndexChanged, "stage1", self.stage1_seconds)
        hook(self._stage1_agg_combo, self._stage1_agg_combo.currentIndexChanged, "stage1_agg",
             self._stage1_agg_combo.currentData)
        hook(self._unit_combo, self._unit_combo.currentIndexChanged, "integral_unit", self._unit_combo.currentData)
        hook(self._exclude_check, self._exclude_check.toggled, "exclude_incomplete", self._exclude_check.isChecked)
        hook(self._gap_combo, self._gap_combo.currentIndexChanged, "gap", self._gap_value)
        hook(self._gap_spin, self._gap_spin.valueChanged, "gap", self._gap_value)
        hook(self._bins_combo, self._bins_combo.currentIndexChanged, "bins", self._bins_value)
        hook(self._bins_spin, self._bins_spin.valueChanged, "bins", self._bins_value)
        hook(self._yaxis_combo, self._yaxis_combo.currentIndexChanged, "yaxis", self._yaxis_combo.currentData)
        self._view_combo.currentIndexChanged.connect(self._on_view_changed)
        hook(self._tf_check, self._tf_check.toggled, "time_filter", self._tf_dict)
        hook(self._tf_start, self._tf_start.timeChanged, "time_filter", self._tf_dict)
        hook(self._tf_end, self._tf_end.timeChanged, "time_filter", self._tf_dict)
        self._tf_preset.currentIndexChanged.connect(self._on_tf_preset_changed)
        for btn in self._tf_day_buttons:
            btn.toggled.connect(self._on_tf_day_toggled)
        return panel

    # -- settings values -----------------------------------------------------------

    def bucket_seconds(self) -> float:
        preset = self._bucket_combo.currentData()
        if preset is not None:
            return float(preset)
        return float(self._custom_value.value() * self._custom_unit.currentData())

    def stage1_seconds(self) -> float:
        preset = self._stage1_combo.currentData()
        if preset is not None:
            return float(preset)
        return float(self._stage1_value.value() * self._stage1_unit.currentData())

    def two_stage(self) -> bool:
        return self._mode_combo.currentData() == "bucket" and self._two_check.isChecked()

    def _gap_value(self) -> float | None:
        return float(self._gap_spin.value()) if self._gap_combo.currentData() == "value" else None

    def _bins_value(self) -> int | None:
        return int(self._bins_spin.value()) if self._bins_combo.currentData() == "value" else None

    def view(self) -> str:
        return self._view_combo.currentData()

    def current_time_filter(self) -> TimeFilter:
        """The filter as set in the panel (42.10). A numeric time axis has no weekdays: all of them."""
        s, e = self._tf_start.time(), self._tf_end.time()
        days = tuple(d for d, b in enumerate(self._tf_day_buttons) if b.isChecked())
        return TimeFilter(
            self._tf_check.isChecked(), s.hour() * 3600 + s.minute() * 60, e.hour() * 3600 + e.minute() * 60,
            days if self._time_is_datetime() else _ALL_DAYS,
        )

    def _tf_dict(self) -> dict:
        tf = self.current_time_filter()
        return {"enabled": tf.enabled, "start": self._tf_start.time().toString("HH:mm"),
                "end": self._tf_end.time().toString("HH:mm"), "weekdays": list(tf.weekdays)}

    def current_settings(self) -> HistogramSettings:
        t = self._origin_edit.time()
        return HistogramSettings(
            mode=self._mode_combo.currentData(),
            weight=self._weight_combo.currentData(),
            bucket_s=self.bucket_seconds(),
            origin_s=t.hour() * 3600 + t.minute() * 60,
            agg=self._agg_combo.currentData(),
            integral_unit_s=float(self._unit_combo.currentData()),
            exclude_incomplete=self._exclude_check.isChecked(),
            gap_s=self._gap_value(),
            bins=self._bins_value(),
            two_stage=self.two_stage(),
            stage1_s=self.stage1_seconds(),
            stage1_agg=self._stage1_agg_combo.currentData(),
            view=self.view(),
            time_filter=self.current_time_filter(),
        )

    def _apply_enabled(self) -> None:
        """Which controls apply to the chosen aggregation (42.2): a control that does not apply is
        disabled (still visible, so the panel does not jump)."""
        bucket = self._mode_combo.currentData() == "bucket"
        agg = self._agg_combo.currentData()
        two = self.two_stage()
        view = self.view()
        trend = view == "trend"
        self._mode_group.setEnabled(not trend)  # the trend needs buckets: the aggregation is fixed (42.11)
        self._weight_group.setEnabled(not bucket and not trend)
        self._bins_group.setEnabled(view == "dist")  # the duration curve has no bins
        self._yaxis_group.setEnabled(not trend)
        tf_on = self._tf_check.isChecked()
        self._tf_range_group.setEnabled(tf_on)
        dated = self._time_is_datetime()
        self._tf_days_group.setEnabled(tf_on and dated)
        self._tf_days_group.setToolTip("" if dated else tr("hist.tf_numeric_axis"))
        for grp in (self._bucket_group, self._origin_group, self._agg_group, self._exclude_group, self._two_group):
            grp.setEnabled(bucket)
        for grp in (self._stage1_group, self._stage1_agg_group):
            grp.setEnabled(two)
        integral = agg == "integral" if not two else self._stage1_agg_combo.currentData() == "integral"
        self._unit_group.setEnabled(bucket and integral)
        custom = bucket and self._bucket_combo.currentData() is None
        self._custom_value.setEnabled(custom)
        self._custom_unit.setEnabled(custom)
        s1_custom = two and self._stage1_combo.currentData() is None
        self._stage1_value.setEnabled(s1_custom)
        self._stage1_unit.setEnabled(s1_custom)
        self._agg_label.setText(tr("hist.agg_stage2" if two else "hist.agg"))
        time_used = bucket or self._weight_combo.currentData() == "time"
        self._gap_group.setEnabled(time_used)
        self._gap_spin.setEnabled(time_used and self._gap_combo.currentData() == "value")
        self._bins_spin.setEnabled(self._bins_combo.currentData() == "value")
        reason = self.setting_error()
        # the reason sits next to the input it is about (42.2 / 42.8)
        stage_reason = reason if reason and reason.startswith("stage") else None
        bucket_reason = reason if reason and not stage_reason else None
        self._bucket_error.setText(tr(f"hist.err_{bucket_reason}") if bucket_reason else "")
        self._stage1_error.setText(tr(f"hist.err_{stage_reason}") if stage_reason else "")

    def setting_error(self) -> str | None:
        """The reason id when the bucket / window lengths cannot be computed (no data needed), else None."""
        if self._mode_combo.currentData() != "bucket":
            return None
        if self.two_stage():
            return validate_two_stage(self.stage1_seconds(), self.bucket_seconds())
        return validate_bucket(self.bucket_seconds())

    def _fill_agg_combo(self, two: bool) -> None:
        """The "reduce by" items: all five for a single stage, max / min / mean / sum (no integral,
        default max) as the stage-2 reduction (42.8). Signals stay blocked; the caller recomputes."""
        keys = STAGE2_AGGREGATIONS if two else _AGGS
        want = "max" if two else self._single_agg
        self._agg_combo.blockSignals(True)
        self._agg_combo.clear()
        for a in keys:
            self._agg_combo.addItem(tr(f"hist.agg_{a}"), a)
        self._agg_combo.setCurrentIndex(max(0, self._agg_combo.findData(want)))
        self._agg_combo.blockSignals(False)

    def _on_two_stage_toggled(self, checked: bool) -> None:
        if self._closed:
            return
        self._fill_agg_combo(self._mode_combo.currentData() == "bucket" and checked)
        self._on_setting_changed("two_stage", checked)

    def _on_view_changed(self, *_a) -> None:
        """The display kind changed (42.11). "Trend" forces the aggregation to "By time buckets" (the
        combo is disabled) and remembers the previous one, which returns when leaving the trend; the
        result is recomputed in every case (the computations differ)."""
        if self._closed:
            return
        if self.view() == "trend":
            if self._mode_before_trend is None:
                self._mode_before_trend = self._mode_combo.currentData()
            self._set_mode_silently("bucket")
        elif self._mode_before_trend is not None:
            self._set_mode_silently(self._mode_before_trend)
            self._mode_before_trend = None
        self._on_setting_changed("view", self.view())

    def _set_mode_silently(self, value: str) -> None:
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(max(0, self._mode_combo.findData(value)))
        self._mode_combo.blockSignals(False)
        self._fill_agg_combo(self.two_stage())

    def _on_tf_preset_changed(self, *_a) -> None:
        """Choosing a weekday preset sets the seven toggles ("custom" keeps them as they are)."""
        if self._closed:
            return
        days = _WEEKDAY_PRESETS.get(self._tf_preset.currentData())
        if days is not None:
            for d, btn in enumerate(self._tf_day_buttons):
                btn.blockSignals(True)
                btn.setChecked(d in days)
                btn.blockSignals(False)
        self._on_setting_changed("time_filter", self._tf_dict())

    def _on_tf_day_toggled(self, *_a) -> None:
        """A manual weekday toggle makes the preset "custom" (42.10)."""
        if self._closed:
            return
        self._tf_preset.blockSignals(True)
        self._tf_preset.setCurrentIndex(self._tf_preset.findData("custom"))
        self._tf_preset.blockSignals(False)
        self._on_setting_changed("time_filter", self._tf_dict())

    def _on_setting_changed(self, item: str, value) -> None:
        if self._closed:
            return
        log_op("histogram_settings", item=item, value=value)
        if item == "mode":  # the stage-2 items follow the effective two-stage state
            self._fill_agg_combo(self.two_stage())
        elif item == "agg" and not self.two_stage():
            self._single_agg = value
        self._apply_enabled()
        if item == "yaxis":  # display only: redraw the shown result
            if self._result is not None and self._result.settings.view != "trend":
                self._draw(self._result)
            return
        self.recompute(keep_range=True)

    # -- candidate refresh (derived variables added / removed) ---------------------

    def _schedule_refresh(self, *_a) -> None:
        if not self._refresh_pending and not self._closed:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh_candidates)

    def _refresh_candidates(self) -> None:
        """Keep the selections that still exist; a vanished variable drops out of the list (the
        next recompute shows the remaining ones, or the hint when none is left)."""
        self._refresh_pending = False
        if self._closed:
            return
        names = self._candidates()
        before = self.selected_names()
        keep = set(before)
        self._var_list.blockSignals(True)
        self._var_list.clear()
        self._var_list.addItems(names)
        for i in range(self._var_list.count()):
            if self._var_list.item(i).text() in keep:
                self._var_list.item(i).setSelected(True)
        self._var_list.blockSignals(False)
        if self.selected_names() != before:  # a selected derived variable was deleted: redo with the rest
            log_op("histogram_settings", item="variables", value=self.selected_names())
            self.recompute(keep_range=True)

    # -- view ----------------------------------------------------------------------

    def build_view(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setObjectName("histogram_plot")
        self._plot = self._plot_widget.getPlotItem()
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setMenuEnabled(False)
        self._legend = self._plot.addLegend()
        # The trend (42.12) has its own plot: a time axis like the main plots' (local time, or plain
        # numbers for a numeric time axis), zoom / pan along X only.
        axis_items = {"bottom": pg.DateAxisItem()} if self._time_is_datetime() else None
        self._trend_widget = pg.PlotWidget(axisItems=axis_items)
        self._trend_widget.setObjectName("histogram_trend_plot")
        self._trend_plot = self._trend_widget.getPlotItem()
        self._trend_plot.showGrid(x=True, y=True, alpha=0.3)
        self._trend_plot.setMenuEnabled(False)
        self._trend_legend = self._trend_plot.addLegend()
        self._trend_vb = self._trend_plot.getViewBox()
        self._trend_vb.setMouseEnabled(x=True, y=False)
        self._trend_vb.disableAutoRange()
        self._trend_vb.sigXRangeChanged.connect(self._trend_fit_y)
        self._trend_series: list[tuple[np.ndarray, np.ndarray]] = []  # drawn (x0, value) arrays per variable
        self._stack = QStackedWidget()
        self._stack.addWidget(self._plot_widget)
        self._stack.addWidget(self._trend_widget)
        lay.addWidget(self._stack, 1)
        # The hover readout is a normal label below the plot (nothing is laid over the plot).
        self._hover_label = QLabel(tr("hist.hover_hint"))
        self._hover_label.setObjectName("histogram_hover")
        lay.addWidget(self._hover_label)
        return box

    # -- compute -------------------------------------------------------------------

    def _cannot_compute(self, message: str):
        """Nothing can be computed with these settings: drop the previous graph and result, so an old
        figure neither stays on screen nor can be exported (42.2); the base shows `message`."""
        self._result = None
        self._clear_plot()
        self._sync_export_action()
        raise ValueError(message)

    def make_job(self, row_start: int, row_end: int, cancelled):
        names = self.selected_names()
        if not names:
            self._cannot_compute(tr("hist.pick_variable"))
        settings = self.current_settings()
        if settings.mode == "bucket":
            reason = self.setting_error()
            if reason:
                self._cannot_compute(tr(f"hist.err_{reason}"))
        tf = active_filter(settings)
        if tf is not None and self._time_is_datetime() and not tf.weekdays:
            self._cannot_compute(tr("hist.err_no_weekdays"))  # nothing to count: say why, compute nothing (42.10)
        main = self._main
        columns = {}
        for name in names:  # resolved here on the UI thread (25.1): the worker never looks names up
            col = main._raw_variables.get(name)
            if col is None:
                self._cannot_compute(tr("xy.unknown_variable", name=name))
            columns[name] = col
        times = main._time_values
        time_name = main._time_column
        time_is_dt = self._time_is_datetime()
        log_op(
            "histogram_compute", variables=names, mode=settings.mode, weight=settings.weight,
            row_start=row_start, row_end=row_end, two_stage=settings.two_stage,
            view=settings.view, time_filter=self._tf_dict(),
        )

        def job():
            try:
                return compute_histogram(
                    lambda nm, a, b: columns[nm].window(a, b), lambda a, b: np.asarray(times[a:b]),
                    names, settings, row_start, row_end, cancelled=cancelled, time_name=time_name,
                    time_is_datetime=time_is_dt,
                )
            except Cancelled:
                return CANCELLED
            except HistogramError as exc:  # not a failure: say why in the status line
                return _Refused(exc.code, exc.info)

        return job

    def on_result(self, result) -> None:
        if isinstance(result, _Refused):
            self._result = None
            self._clear_plot()
            self._sync_export_action()
            self.set_status(tr(f"hist.err_{result.code}", **result.info))
            return
        self._result = result
        self._sync_export_action()
        if not self._drawable(result):  # never hand pyqtgraph a non-finite coordinate (data 29.4)
            self._clear_plot()
            self.set_status(tr("hist.err_nonfinite"))
            return
        self._draw(result)
        self._update_status(result)

    def on_failure(self, message: str) -> None:
        self._result = None
        self._clear_plot()
        self._sync_export_action()
        super().on_failure(message)

    # -- status --------------------------------------------------------------------

    def _range_text(self, r: HistogramResult) -> str:
        main = self._main
        times = main._time_values
        if r.row_end > r.row_start and times is not None and len(times):
            lo, hi = float(times[r.row_start]), float(times[min(r.row_end, len(times)) - 1])
            anchor = main.plot_grid.anchor_plot()
            fmt = anchor.format_x if anchor is not None else (lambda v: f"{v:.6g}")
            return f"{fmt(lo)} - {fmt(hi)}"
        return tr("stats.empty_range")

    def _update_status(self, r: HistogramResult) -> None:
        parts = [tr("xy.status_range", range=self._range_text(r), rows=r.row_end - r.row_start)]
        bucket = r.settings.mode == "bucket"
        unit = tr("hist.count_buckets" if bucket else "hist.count_rows")
        for v in r.variables:
            parts.append(tr("hist.status_var", name=v.name, n=v.n, unit=unit, excluded=v.excluded))
        if bucket:
            parts.append(tr("hist.status_buckets", n=r.bucket_count))
            for v in r.variables:
                if v.incomplete:
                    key = "hist.status_incomplete_excluded" if r.settings.exclude_incomplete else "hist.status_incomplete"
                    parts.append(tr(key, name=v.name, n=v.incomplete))
            if r.settings.two_stage:
                for v in r.variables:
                    if v.stage1_incomplete:
                        parts.append(tr("hist.status_stage1_excluded", name=v.name, n=v.stage1_incomplete))
        if bucket or r.settings.weight == "time":
            if r.gap_count:  # one number: gaps belong to the time axis, not to a variable
                parts.append(tr("hist.status_gaps", n=r.gap_count, gap=_fmt(r.gap_s if r.gap_s is not None else math.nan)))
        tf_text = self.time_filter_text(r)
        if tf_text:
            parts.append(tf_text)
        if r.settings.view == "trend" and self._trend_as_line(r):
            parts.append(tr("hist.status_trend_line", n=len(r.bucket_start)))
        if all(v.n == 0 for v in r.variables):
            parts.append(tr("hist.status_no_data"))
        self.set_status("  |  ".join(parts))

    def time_filter_text(self, r: HistogramResult) -> str:
        """"Filter: 9:00-17:00, weekdays (target 120 h)" for the status line and the image stamp (42.10);
        empty when the filter is off."""
        tf = active_filter(r.settings)
        if tf is None:
            return ""
        rng = (f"{tf.start_s // 3600}:{tf.start_s % 3600 // 60:02d}{tr('hist.tf_sep')}"
               f"{tf.end_s // 3600}:{tf.end_s % 3600 // 60:02d}")
        days = ""
        chosen = tuple(sorted(tf.weekdays))
        if self._time_is_datetime() and chosen != _ALL_DAYS:
            if chosen == _WEEKDAY_PRESETS["weekdays"]:
                days = tr("hist.tf_days_sep") + tr("hist.tf_preset_weekdays")
            elif chosen == _WEEKDAY_PRESETS["weekend"]:
                days = tr("hist.tf_days_sep") + tr("hist.tf_preset_weekend")
            else:
                days = tr("hist.tf_days_sep") + tr("hist.tf_days_join").join(tr(f"hist.wd_{d}") for d in chosen)
        hours = f"{(r.target_seconds or 0.0) / 3600.0:.4g}"
        return tr("hist.status_time_filter", range=rng, days=days, hours=hours)

    # -- display -------------------------------------------------------------------

    def _clear_plot(self) -> None:
        self._plot.clear()
        self._legend.clear()
        self._trend_plot.clear()
        self._trend_legend.clear()
        self._trend_series = []
        self._lines_visible = []
        self._hover_label.setText(tr("hist.hover_hint"))

    def _display_counts(self, r: HistogramResult) -> tuple[list[np.ndarray], str]:
        """Per-variable bar heights and the Y label: counts / time unit / percent (42.4)."""
        percent = self._yaxis_combo.currentData() == "percent"
        out = []
        if percent:
            for v in r.variables:
                tot = float(v.counts.sum())
                out.append(v.counts * (100.0 / tot) if tot > 0 else v.counts.copy())
            return out, tr("hist.y_label_percent")
        if r.y_unit == "seconds":
            top = max((float(v.counts.max()) for v in r.variables if len(v.counts)), default=0.0)
            key, div = _time_unit_for(top)
            return [v.counts / div for v in r.variables], tr("hist.y_label_time", unit=tr("hist.unit_" + key))
        label = tr("hist.y_label_buckets" if r.y_unit == "buckets" else "hist.y_label_rows")
        return [v.counts.copy() for v in r.variables], label

    def x_label(self, r: HistogramResult) -> str:
        names = ", ".join(v.name for v in r.variables)
        return names if r.settings.mode != "bucket" else f"{names} ({self.aggregation_summary(r.settings)})"

    @staticmethod
    def aggregation_summary(s: HistogramSettings, length: bool = True) -> str:
        """"1 day, integral [x h]" style summary of a bucketed aggregation; two stages (42.8):
        "1 day, 3 min Mean -> Max"."""
        def agg_text(agg: str) -> str:
            text = tr("hist.agg_" + agg)
            if agg == "integral":
                key = next((k for k, sec in INTEGRAL_UNITS.items() if sec == s.integral_unit_s), "h")
                text += f" [×{tr('hist.unit_' + key)}]"
            return text

        if s.two_stage:
            return tr("hist.summary_two_stage", bucket=_length_text(s.bucket_s) if length else "",
                      window=_length_text(s.stage1_s), agg1=agg_text(s.stage1_agg), agg2=agg_text(s.agg)
                      ).lstrip(", ・")
        return (f"{_length_text(s.bucket_s)}, " if length else "") + agg_text(s.agg)

    def legend_text(self, r: HistogramResult, i: int) -> str:
        v = r.variables[i]
        eq = "≈" if v.median_approx else "="  # the time-weighted median is approximate (42.3)
        return tr("hist.legend", name=v.name, n=v.n, mean=_fmt(v.mean), std=_fmt(v.std)) + (
            f" {tr('hist.median_short')}{eq}{_fmt(v.median)}" if math.isfinite(v.median) else ""
        )

    def _drawable(self, r: HistogramResult) -> bool:
        """False when an edge or a bar height is not finite (e.g. values near the float limit)."""
        if r.settings.view == "duration":
            return all(np.isfinite(d["x"]).all() and np.isfinite(d["v"]).all() for d in self._duration_data(r))
        if r.settings.view == "trend":
            return r.bucket_start is not None and bool(np.isfinite(r.bucket_start).all() and np.isfinite(r.bucket_end).all())
        if not np.isfinite(r.edges).all():
            return False
        heights, _label = self._display_counts(r)
        return all(np.isfinite(h).all() for h in heights)

    def _draw(self, r: HistogramResult) -> None:
        view = r.settings.view
        if view == "duration":
            self._draw_duration(r)
        elif view == "trend":
            self._draw_trend(r)
        else:
            self._draw_dist(r)

    def _draw_dist(self, r: HistogramResult) -> None:
        self._stack.setCurrentWidget(self._plot_widget)
        self._clear_plot()
        if not self._drawable(r):
            self.set_status(tr("hist.err_nonfinite"))
            return
        heights, ylabel = self._display_counts(r)
        edges = np.asarray(r.edges, dtype=np.float64)
        self._plot.setLabel("bottom", self.x_label(r))
        self._plot.setLabel("left", ylabel)
        self._lines_visible = []
        for i, v in enumerate(r.variables):
            color = QColor(_COLOR_CYCLE[i % len(_COLOR_CYCLE)])
            fill = QColor(color)
            fill.setAlpha(_FILL_ALPHA)
            item = pg.PlotDataItem(
                edges, heights[i], stepMode="center", fillLevel=0, pen=pg.mkPen(color, width=1.5),
                brush=pg.mkBrush(fill), name=self.legend_text(r, i),
            )
            self._plot.addItem(item)
            for value, style, tag in ((v.mean, Qt.PenStyle.DashLine, "mean"), (v.median, Qt.PenStyle.DotLine, "median")):
                if math.isfinite(value):  # never hand pyqtgraph a non-finite coordinate (data 29.4)
                    line = pg.InfiniteLine(pos=value, angle=90, pen=pg.mkPen(color, width=2, style=style))
                    line.setObjectName(f"hist_{tag}_{i}")
                    self._plot.addItem(line)
                    self._lines_visible.append(line)
        top = max((float(h.max()) for h in heights if len(h)), default=0.0)
        top = top if top > 0 and math.isfinite(top) else 1.0
        span = (edges[-1] - edges[0]) or 1.0
        self._plot.setRange(xRange=(edges[0] - 0.02 * span, edges[-1] + 0.02 * span), yRange=(0, top * 1.08), padding=0)
        self._hover_label.setText(tr("hist.hover_hint"))

    def mean_median_lines(self) -> list[pg.InfiniteLine]:
        return list(self._lines_visible)

    # -- hover ---------------------------------------------------------------------

    def hover_text(self, x: float) -> str:
        """Bin range and count under value `x` (42.4); the hint when outside the bins. The duration
        curve reads "v or more: amount (percent)" (42.11)."""
        r = self._result
        if r is not None and r.settings.view == "duration":
            return self._duration_hover(r, x)
        if r is None or r.settings.view == "trend":
            return tr("hist.hover_hint")
        if not math.isfinite(x) or x < r.edges[0] or x >= r.edges[-1]:
            return tr("hist.hover_hint")
        k = min(int(np.searchsorted(r.edges, x, side="right")) - 1, r.bins - 1)
        heights, _label = self._display_counts(r)
        percent = self._yaxis_combo.currentData() == "percent"
        parts = [tr("hist.hover_bin", lo=_fmt(float(r.edges[k])), hi=_fmt(float(r.edges[k + 1])))]
        for v, h in zip(r.variables, heights):
            parts.append(f"{v.name}: {h[k]:.4g}{'%' if percent else ''}")
        return "  |  ".join(parts)

    def _on_mouse_moved(self, pos) -> None:
        if self._closed or self._result is None or self._result.settings.view == "trend":
            return
        vb = self._plot.getViewBox()
        if not self._plot.sceneBoundingRect().contains(pos):
            self._hover_label.setText(tr("hist.hover_hint"))
            return
        self._hover_label.setText(self.hover_text(float(vb.mapSceneToView(pos).x())))

    # -- duration curve (42.11) ----------------------------------------------------

    def _duration_data(self, r: HistogramResult) -> list[dict]:
        """Per variable with a curve: its display x (the amount at or above the value, in the unit of the
        axis or in percent), the raw amount, the values and the index of the variable."""
        percent = self._yaxis_combo.currentData() == "percent"
        curves = [(i, v) for i, v in enumerate(r.variables) if v.duration_x is not None and len(v.duration_x)]
        div = 1.0
        if r.y_unit == "seconds" and not percent:
            div = _time_unit_for(max((float(v.duration_x[-1]) for _i, v in curves), default=0.0))[1]
        out = []
        for i, v in curves:
            raw = np.asarray(v.duration_x, dtype=np.float64)
            total = float(raw[-1])
            x = raw * (100.0 / total) if percent and total > 0 else raw / div
            out.append({"i": i, "x": x, "raw": raw, "v": np.asarray(v.duration_v, dtype=np.float64), "total": total})
        return out

    def duration_x_label(self, r: HistogramResult) -> str:
        percent = self._yaxis_combo.currentData() == "percent"
        kind = {"seconds": "time", "rows": "rows", "buckets": "buckets"}[r.y_unit]
        if percent:
            return tr(f"hist.dur_x_percent_{kind}")
        if kind == "time":
            top = max((float(v.duration_x[-1]) for v in r.variables if v.duration_x is not None and len(v.duration_x)),
                      default=0.0)
            return tr("hist.dur_x_time", unit=tr("hist.unit_" + _time_unit_for(top)[0]))
        if kind == "buckets":
            return tr("hist.dur_x_buckets", length=_length_text(r.settings.bucket_s))
        return tr("hist.dur_x_rows")

    def duration_legend_text(self, r: HistogramResult, i: int) -> str:
        v = r.variables[i]
        return tr("hist.legend_duration", name=v.name, max=_fmt(v.vmax), mean=_fmt(v.mean))

    def _draw_duration(self, r: HistogramResult) -> None:
        self._stack.setCurrentWidget(self._plot_widget)
        self._clear_plot()
        if not self._drawable(r):
            self.set_status(tr("hist.err_nonfinite"))
            return
        self._plot.setLabel("bottom", self.duration_x_label(r))
        self._plot.setLabel("left", self.x_label(r))
        data = self._duration_data(r)
        xmax, vlo, vhi = 0.0, math.inf, -math.inf
        for d in data:
            color = QColor(_COLOR_CYCLE[d["i"] % len(_COLOR_CYCLE)])
            edges = np.concatenate(([0.0], d["x"]))  # step i covers (x[i-1], x[i]] at the value v[i]
            self._plot.addItem(pg.PlotDataItem(
                edges, d["v"], stepMode="center", pen=pg.mkPen(color, width=2), name=self.duration_legend_text(r, d["i"])))
            xmax, vlo, vhi = max(xmax, float(d["x"][-1])), min(vlo, float(d["v"].min())), max(vhi, float(d["v"].max()))
        if data and math.isfinite(vlo) and math.isfinite(vhi):
            pad = 0.05 * ((vhi - vlo) or abs(vhi) or 1.0)
            self._plot.setRange(xRange=(0.0, (xmax or 1.0) * 1.02), yRange=(vlo - pad, vhi + pad), padding=0)
        self._hover_label.setText(tr("hist.hover_duration_hint"))

    def _duration_hover(self, r: HistogramResult, x: float) -> str:
        """"v or more: 123.4 h (56.7%)" for the curve point at display position `x` (42.11)."""
        data = self._duration_data(r)
        if not data or not math.isfinite(x) or x < 0 or x > max(float(d["x"][-1]) for d in data) * 1.0001:
            return tr("hist.hover_duration_hint")
        if r.y_unit == "seconds":
            unit = tr("hist.unit_" + _time_unit_for(max(float(d["raw"][-1]) for d in data))[0])
        else:
            unit = tr("hist.count_rows" if r.y_unit == "rows" else "hist.count_buckets")
        div = 1.0
        if r.y_unit == "seconds":
            div = _time_unit_for(max(float(d["raw"][-1]) for d in data))[1]
        parts = []
        for d in data:
            j = min(int(np.searchsorted(d["x"], x, side="left")), len(d["x"]) - 1)
            raw = float(d["raw"][j])
            pct = raw / d["total"] * 100.0 if d["total"] > 0 else 0.0
            amount = f"{raw / div:.4g} {unit}"
            text = tr("hist.hover_duration", value=_fmt(float(d["v"][j])), amount=amount, percent=f"{pct:.1f}")
            parts.append(text if len(data) == 1 else f"{r.variables[d['i']].name}: {text}")
        return "  |  ".join(parts)

    # -- trend (42.12) -------------------------------------------------------------

    @staticmethod
    def _trend_as_line(r: HistogramResult) -> bool:
        """More buckets than HIST_TREND_BAR_MAX: a step line instead of bars (42.12)."""
        return r.bucket_start is not None and len(r.bucket_start) > HIST_TREND_BAR_MAX

    @staticmethod
    def trend_masks(r: HistogramResult, v) -> tuple[np.ndarray, np.ndarray]:
        """(drawn, complete) bucket masks of a variable: buckets with a value, without the incomplete
        ones when "exclude incomplete" is on (42.12)."""
        vals = np.asarray(v.bucket_values, dtype=np.float64)
        comp = np.asarray(v.bucket_complete, dtype=bool)
        drawn = np.isfinite(vals)
        if r.settings.exclude_incomplete:
            drawn &= comp
        return drawn, comp

    def _draw_trend(self, r: HistogramResult) -> None:
        self._stack.setCurrentWidget(self._trend_widget)
        self._clear_plot()
        if not self._drawable(r):
            self.set_status(tr("hist.err_nonfinite"))
            return
        starts = np.asarray(r.bucket_start, dtype=np.float64)
        ends = np.asarray(r.bucket_end, dtype=np.float64)
        self._trend_plot.setLabel("bottom", r.time_name or "")
        self._trend_plot.setLabel("left", self.x_label(r))
        m = max(1, len(r.variables))
        as_line = self._trend_as_line(r)
        for i, v in enumerate(r.variables):
            color = QColor(_COLOR_CYCLE[i % len(_COLOR_CYCLE)])
            vals = np.asarray(v.bucket_values, dtype=np.float64)
            drawn, comp = self.trend_masks(r, v)
            width = (ends - starts) / m
            x0 = starts + i * width
            x1 = x0 + width * _BAR_FILL  # a small gap to the next bar
            self._trend_series.append((x0[drawn], vals[drawn]))
            if as_line:
                self._add_trend_line(starts, ends, vals, drawn, color)
            else:
                full = drawn & comp
                part = drawn & ~comp
                if full.any():
                    item = pg.BarGraphItem(x0=x0[full], x1=x1[full], y0=np.zeros(int(full.sum())), y1=vals[full],
                                           brush=pg.mkBrush(color), pen=pg.mkPen(color, width=1))
                    item.setObjectName(f"hist_trend_bars_{i}")
                    self._trend_plot.addItem(item)
                if part.any():  # incomplete: faded and hatched (42.12)
                    faded = QColor(color)
                    faded.setAlpha(230)
                    base = QColor(color)
                    base.setAlpha(110)
                    self._trend_plot.addItem(pg.BarGraphItem(
                        x0=x0[part], x1=x1[part], y0=np.zeros(int(part.sum())), y1=vals[part],
                        brush=pg.mkBrush(base), pen=pg.mkPen(None)))
                    item = pg.BarGraphItem(x0=x0[part], x1=x1[part], y0=np.zeros(int(part.sum())), y1=vals[part],
                                           brush=QBrush(faded, Qt.BrushStyle.BDiagPattern),
                                           pen=pg.mkPen(faded, width=1, style=Qt.PenStyle.DashLine))
                    item.setObjectName(f"hist_trend_incomplete_{i}")
                    self._trend_plot.addItem(item)
            if math.isfinite(v.mean) and len(starts):
                mean_line = pg.PlotDataItem(
                    [float(starts[0]), float(ends[-1])], [v.mean, v.mean],
                    pen=pg.mkPen(color, width=2, style=Qt.PenStyle.DashLine),
                    name=tr("hist.legend_trend", name=v.name, mean=_fmt(v.mean)))
                mean_line.setObjectName(f"hist_trend_mean_{i}")
                self._trend_plot.addItem(mean_line)
        if len(starts):
            span = float(ends[-1] - starts[0]) or 1.0
            self._trend_vb.setXRange(float(starts[0]) - 0.02 * span, float(ends[-1]) + 0.02 * span, padding=0)
            self._trend_fit_y()
        self._hover_label.setText(tr("hist.hover_hint"))

    def _add_trend_line(self, starts, ends, vals, drawn, color: QColor) -> None:
        """The step line of the drawn buckets (42.12, many buckets): two points per bucket, joined to the
        next drawn bucket only when it directly follows (no NaN is handed to pyqtgraph, data 29.4)."""
        idx = np.flatnonzero(drawn)
        if not len(idx):
            return
        xs = np.empty(2 * len(idx))
        ys = np.empty(2 * len(idx))
        xs[0::2], xs[1::2] = starts[idx], ends[idx]
        ys[0::2] = ys[1::2] = vals[idx]
        connect = np.ones(2 * len(idx), dtype=np.int32)
        connect[1::2][:-1] = (np.diff(idx) == 1).astype(np.int32)  # the end point links to the next start
        connect[-1] = 0
        self._trend_plot.addItem(pg.PlotDataItem(xs, ys, connect=connect, pen=pg.mkPen(color, width=1.5)))

    def _trend_fit_y(self, *_a) -> None:
        """Y follows the values visible in the current X range (42.12); 0 stays in view."""
        if not self._trend_series or self._closed:
            return
        x_lo, x_hi = self._trend_vb.viewRange()[0]
        lo, hi = 0.0, 0.0
        found = False
        for x0, vals in self._trend_series:
            if not len(vals):
                continue
            sel = (x0 >= x_lo) & (x0 <= x_hi)
            if sel.any():
                found = True
                lo, hi = min(lo, float(vals[sel].min())), max(hi, float(vals[sel].max()))
        if not found or not (math.isfinite(lo) and math.isfinite(hi)):
            return
        pad = 0.06 * ((hi - lo) or 1.0)
        self._trend_vb.setYRange(lo - (pad if lo < 0 else 0.0), hi + pad, padding=0)

    def trend_hover_text(self, x: float) -> str:
        r = self._result
        if r is None or r.settings.view != "trend" or r.bucket_start is None or not math.isfinite(x):
            return tr("hist.hover_hint")
        k = int(np.searchsorted(r.bucket_start, x, side="right")) - 1
        if k < 0 or k >= len(r.bucket_start) or x >= r.bucket_end[k]:
            return tr("hist.hover_hint")
        parts = []
        for v in r.variables:
            val = float(v.bucket_values[k])
            if not math.isfinite(val):
                text = tr("hist.hover_trend_none")
            else:
                text = _fmt(val) + ("" if v.bucket_complete[k] else " " + tr("hist.hover_trend_incomplete"))
            parts.append(text if len(r.variables) == 1 else f"{v.name}: {text}")
        return tr("hist.hover_trend", start=self._fmt_bucket_time(float(r.bucket_start[k])),
                  end=self._fmt_bucket_time(float(r.bucket_end[k])), values=", ".join(parts))

    def _fmt_bucket_time(self, x: float) -> str:
        if self._time_is_datetime():
            try:
                return datetime.fromtimestamp(x).strftime("%m/%d %H:%M")
            except (OverflowError, OSError, ValueError):
                pass
        return f"{x:.6g}"

    def _on_trend_mouse_moved(self, pos) -> None:
        if self._closed or self._result is None or self._result.settings.view != "trend":
            return
        if not self._trend_plot.sceneBoundingRect().contains(pos):
            self._hover_label.setText(tr("hist.hover_hint"))
            return
        self._hover_label.setText(self.trend_hover_text(float(self._trend_vb.mapSceneToView(pos).x())))

    # -- lifecycle -----------------------------------------------------------------

    def on_close(self) -> None:
        model = self._main.variable_panel.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            try:
                sig.disconnect(self._schedule_refresh)
            except (RuntimeError, TypeError):
                pass

    # -- export --------------------------------------------------------------------

    def export_stamp_extra(self) -> list[str] | None:
        r = self._result
        if r is None:
            return None
        s = r.settings
        lines = [tr("hist.stamp_variables", names=", ".join(v.name for v in r.variables))]
        if s.mode == "bucket":
            start = f"{s.origin_s // 3600}:{s.origin_s % 3600 // 60:02d}"
            text = f"{_length_text(s.bucket_s)} ({tr('hist.stamp_origin', time=start)}), " + self.aggregation_summary(s, length=False)
            if s.exclude_incomplete:
                text += ", " + tr("hist.stamp_excl_incomplete")
            lines.append(tr("hist.stamp_aggregation", text=text))
            if s.two_stage:
                agg1 = tr("hist.agg_" + s.stage1_agg)
                lines.append(tr("hist.stamp_stage1", window=_length_text(s.stage1_s), agg=agg1))
        else:
            kind = tr("hist.weight_rows") if s.weight == "rows" else tr("hist.weight_time")
            lines.append(tr("hist.stamp_aggregation", text=f"{tr('hist.mode_plain')}, {kind}"))
        if s.view == "dist":
            bins = tr("hist.stamp_bins_auto", n=r.bins) if r.bins_auto else tr("hist.stamp_bins_fixed", n=r.bins)
            lines.append(tr("hist.stamp_bins", text=bins))
        else:
            lines.append(tr("hist.stamp_view", text=tr("hist.view_" + s.view)))
        tf_text = self.time_filter_text(r)
        if tf_text:
            lines.append(tf_text)
        return lines

    # -- per-bucket values (42.8) ---------------------------------------------------

    def _build_export_action(self) -> None:
        toolbar = self.findChild(QToolBar, "analysis_toolbar")
        self.export_windows_action = QAction(tr("hist.export_windows"), self)
        self.export_windows_action.setObjectName("histogram_export_windows")
        self.export_windows_action.setToolTip(tr("hist.export_windows_tooltip"))
        self.export_windows_action.triggered.connect(lambda *_: self.export_windows_dialog())
        if toolbar is not None:
            toolbar.addAction(self.export_windows_action)
        self._sync_export_action()

    def _sync_export_action(self) -> None:
        """Enabled only with a bucketed result that has a per-bucket table (42.8)."""
        r = self._result
        ok = r is not None and r.settings.mode == "bucket" and r.bucket_start is not None and len(r.bucket_start) > 0
        self.export_windows_action.setEnabled(bool(ok))

    def _time_is_datetime(self) -> bool:
        ds = self._main._data_source
        name = self._main._time_column
        try:
            return bool(ds.is_datetime(name))
        except Exception:  # noqa: BLE001 - an unknown source type: treat the axis as plain seconds
            return False

    def export_windows_dialog(self) -> None:
        r = self._result
        if r is None or not self.export_windows_action.isEnabled():
            return
        source = Path(self._main._data_source.path)
        folder = self._export_dir or str(source.parent)
        default = Path(folder) / f"{hexport.default_file_stem(r)}.parquet"
        filters = [tr("hist.export_windows_filter_parquet"), tr("hist.export_windows_filter_csv")]
        path, selected = QFileDialog.getSaveFileName(
            self, tr("hist.export_windows_title"), str(default), ";;".join(filters)
        )
        if not path:
            return
        # The format always follows the chosen filter (42.8). If the suffix does not match,
        # replace it; if there is no matching suffix, append it.
        is_csv_filter = selected == filters[1]
        target_suffix = ".csv" if is_csv_filter else ".parquet"
        current_suffix = Path(path).suffix.lower()
        if current_suffix in (".parquet", ".csv"):
            if current_suffix != target_suffix:
                path = str(Path(path).with_suffix(target_suffix))
        else:  # no suffix, or another one ("x.txt" -> "x.txt.csv")
            path += target_suffix
        self.export_windows(path)

    def export_windows(self, path: str) -> bool:
        """Write the per-bucket table of the shown result (every bucket overlapping the range,
        incomplete ones too) as Parquet (key-value metadata) or CSV (+ sidecar .json), on the UI
        thread; failures go through ui/messages (42.8)."""
        r = self._result
        if r is None or r.bucket_start is None or not len(r.bucket_start):
            messages.information(self, tr("hist.export_windows_title"), tr("export.nothing_to_export"))
            return False
        fmt = "csv" if Path(path).suffix.lower() == ".csv" else "parquet"
        is_dt = self._time_is_datetime()
        log_op("histogram_export_windows", path=path, format=fmt, rows=int(len(r.bucket_start)))
        try:
            cols, mapping = hexport.build_columns(r, is_dt)
            times = self._main._time_values
            rng = None
            if times is not None and r.row_end > r.row_start and len(times):
                rng = (float(times[r.row_start]), float(times[min(r.row_end, len(times)) - 1]))
            meta = hexport.build_metadata(
                r, source_path=str(self._main._data_source.path), time_column=self._main._time_column,
                time_is_datetime=is_dt, range_s=rng, app_version=__version__, columns=mapping,
            )
            rows = (hexport.write_csv if fmt == "csv" else hexport.write_parquet)(path, cols, meta)
        except Exception as e:  # noqa: BLE001 - OSError, Arrow errors...: shown, nothing else changes
            logger.exception("failed to export bucket values: %s", path)
            messages.warning(self, tr("hist.export_windows_title"), tr("hist.export_windows_failed", error=e))
            return False
        self._export_dir = str(Path(path).resolve().parent)
        self._main._show_notice(tr("hist.export_windows_saved", path=path, rows=rows), 10_000)
        return True

    def build_export_figure(self, stamp_lines: list[str], size_px: tuple[int, int]):
        r = self._result
        if r is None or not self._drawable(r):
            return None
        return build_histogram_figure(self, r, stamp_lines, size_px)


def build_histogram_figure(win: HistogramWindow, r: HistogramResult, stamp_lines, size_px):
    """matplotlib Figure of a HistogramResult (42.5): translucent filled steps, dashed mean /
    dotted median lines, legend."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    view = r.settings.view
    heights, ylabel = win._display_counts(r) if view == "dist" else ([], "")
    with _font_context():
        width, height = max(int(size_px[0]), 200), max(int(size_px[1]), 150)
        fig = Figure(figsize=(width / _FIG_DPI, height / _FIG_DPI), dpi=_FIG_DPI, facecolor="white",
                     layout="constrained")
        FigureCanvasAgg(fig)
        n_stamp = max(len(stamp_lines), 1)
        stamp_h = (n_stamp * 7 * 1.25 + 4) / 72 / (height / _FIG_DPI)
        fig.get_layout_engine().set(rect=(0, stamp_h, 1, 1 - stamp_h))
        ax = fig.subplots()
        if view != "dist":
            (_plot_duration_axes if view == "duration" else _plot_trend_axes)(win, r, ax)
            ax.grid(True, alpha=0.3)
            if stamp_lines:
                fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
            return fig
        edges = np.asarray(r.edges, dtype=np.float64)
        for i, v in enumerate(r.variables):
            color = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
            ax.stairs(heights[i], edges, fill=True, alpha=0.35, color=color, label=win.legend_text(r, i))
            ax.stairs(heights[i], edges, color=color, linewidth=1.2)
            if math.isfinite(v.mean):
                ax.axvline(v.mean, color=color, linestyle="--", linewidth=1.2)
            if math.isfinite(v.median):
                ax.axvline(v.median, color=color, linestyle=":", linewidth=1.5)
        ax.set_xlabel(win.x_label(r))
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.7)
        if stamp_lines:
            fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
    return fig


def _plot_duration_axes(win: HistogramWindow, r: HistogramResult, ax) -> None:
    """Duration curve (42.11): matplotlib `step` per variable, legend with max / mean."""
    for d in win._duration_data(r):
        color = _COLOR_CYCLE[d["i"] % len(_COLOR_CYCLE)]
        x = np.concatenate(([0.0], d["x"]))
        y = np.concatenate(([d["v"][0]], d["v"]))
        ax.step(x, y, where="pre", color=color, linewidth=1.6, label=win.duration_legend_text(r, d["i"]))
    ax.set_xlim(left=0)
    ax.set_xlabel(win.duration_x_label(r))
    ax.set_ylabel(win.x_label(r))
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.7)


def _plot_trend_axes(win: HistogramWindow, r: HistogramResult, ax) -> None:
    """Trend (42.12): `bar` per variable (an incomplete bucket: alpha + hatch), or a step line for many
    buckets; the time axis uses the ticks of the image export (local time, concise dates)."""
    import matplotlib.dates as mdates

    starts = np.asarray(r.bucket_start, dtype=np.float64)
    ends = np.asarray(r.bucket_end, dtype=np.float64)
    dated = win._time_is_datetime()
    if dated and len(starts):
        offset = _local_offset_s(float(starts[0]))
        sd = mdates.date2num(_to_local_datetime64(starts, offset))
        ed = mdates.date2num(_to_local_datetime64(ends, offset))
    else:
        sd, ed = starts, ends
    m = max(1, len(r.variables))
    as_line = win._trend_as_line(r)
    for i, v in enumerate(r.variables):
        color = _COLOR_CYCLE[i % len(_COLOR_CYCLE)]
        vals = np.asarray(v.bucket_values, dtype=np.float64)
        drawn, comp = win.trend_masks(r, v)
        width = (ed - sd) / m
        x0 = sd + i * width
        if as_line:
            idx = np.flatnonzero(drawn)
            for k in np.split(idx, np.flatnonzero(np.diff(idx) != 1) + 1) if len(idx) else []:
                ax.step(np.concatenate((sd[k], [ed[k[-1]]])), np.concatenate((vals[k], [vals[k[-1]]])),
                        where="post", color=color, linewidth=1.2)
        else:
            full, part = drawn & comp, drawn & ~comp
            ax.bar(x0[full], vals[full], width=width[full], align="edge", color=color, alpha=0.9)
            if part.any():
                ax.bar(x0[part], vals[part], width=width[part], align="edge", color=color, alpha=0.35,
                       hatch="//", edgecolor=color, linewidth=0.5)
        if math.isfinite(v.mean):
            ax.axhline(v.mean, color=color, linestyle="--", linewidth=1.2,
                       label=tr("hist.legend_trend", name=v.name, mean=_fmt(v.mean)))
    if dated:
        locator = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.xaxis.get_offset_text().set_visible(False)
    ax.set_ylabel(win.x_label(r))
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.7)
