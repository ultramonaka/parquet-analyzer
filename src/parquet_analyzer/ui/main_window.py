from __future__ import annotations

import base64
from datetime import datetime
import logging
from collections import ChainMap
from pathlib import Path
from typing import Callable, Mapping

import numpy as np
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, features
from ..core.analysis import basic_stats, chunked_stats, fft, sampling_report
from ..core.background_worker import WorkerHub
from ..core.column import (
    Column,
    DerivedColumn,
    LazyColumn,
    LazyVariables,
    MaterializedColumn,
    WindowedColumn,
    make_derived_column,
)
from ..core.data_source import ParquetDataSource
from ..core.expression import (
    ExpressionError,
    UnknownFunctionError,
    evaluate_expression,
    referenced_functions,
    referenced_names,
)
from ..core.functions import FunctionRegistry
from ..core.numeric import NonNumericColumnError, check_time_order, to_numeric
from ..core.pyramid import thin_column
from ..core.time_index import ANCHOR_ROWS, TimeIndex
from ..core import oplog
from ..core.oplog import log_op
from ..core.variable import DerivedVariable, dependency_order, dependents_of
from ..io import maps as maps_io
from ..io import user_functions as uf_io
from ..io import view as view_io
from ..io.settings import Settings, load_settings, push_recent_folder, save_settings
from .expression_bar import ExpressionBar
from .folder_picker import pick_parquet_file
from .image_export import EXPORT_FORMATS, export_plots_image
from . import messages
from .i18n import set_language, tr
from .navigator import NavigatorWidget
from .plot_grid import PlotGridWidget
from .settings_dialog import SettingsDialog
from .stats_dialog import StatsDialog
from .view_manager import ViewManagerDialog, view_error_text
from .variable_panel import VariablePanel

_APP_TITLE = f"Parquet Analyzer v{__version__}"
logger = logging.getLogger(__name__)

# FFT over the whole file is both memory-heavy (materializes the full column) and, for
# data with large sampling gaps (this app's intended data is intermittent-burst
# sampling, e.g. a 4-year span sampled in short high-rate bursts), not physically
# meaningful in the first place: fft()'s dt = median(diff(x)) is computed across those
# gaps too. FFT is scoped to the currently visible X range instead
# (detailed_specification.md 13.5.1), with this cap so an accidental zoomed-way-out
# request doesn't reintroduce the same memory problem.
_FFT_MAX_ROWS = 8_000_000
# Navigator overview of a windowed column: fewer row groups than this -> refine with a thinned read (data 40).
_NAV_FEW_ROW_GROUPS = 200
_DERIVED_NAV_DIRECT_ROWS = 2_000_000  # 36.6 C: a windowed derived series up to this long is computed directly for the navigator


class MainWindow(QMainWindow):
    def __init__(self, debug: bool = False):
        super().__init__()
        self._debug = debug
        # detailed_specification.md 21.1: record which dev feature flags are in effect.
        logger.info("feature flags: %s", features.effective_features())
        self._title_suffix = ""
        # Created here (before _build_layout() adds it to the status bar below) so
        # _set_title() -- called immediately next -- can already update it.
        self._file_label = QLabel("")
        self._time_order_label = QLabel("")  # time-axis order warning (29.2); hidden when ok
        self._time_order_label.setStyleSheet("color: #b45309;")
        self._time_order_label.setVisible(False)
        # Transient notices (render failure, image saved) live in a normal label, not
        # statusBar().showMessage(): showMessage hides every addWidget widget (file
        # name, time-order warning) while it is up.
        self._notice_label = QLabel("")
        self._notice_label.setVisible(False)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_notice)
        self._set_title()
        self.resize(1200, 800)

        self.settings: Settings = load_settings()
        set_language(self.settings.language)

        self._data_source: ParquetDataSource | None = None
        # Every column of the loaded file. Not necessarily resident: only the
        # currently-selected time column is read eagerly at open — the rest are
        # LazyColumns that materialize (and cache) on first actual use (plotted,
        # referenced by an expression, or selected for stats) — see
        # detailed_specification.md 13.5.1 Phase C and core/column.py.
        self._file_columns: dict[str, Column] = {}
        self._time_column: str | None = None
        self._time_values: "np.ndarray | TimeIndex | None" = None
        self._time_indexes: dict[str, TimeIndex] = {}  # 36.6 D: per-file cache of built indexes
        self._nav_pyramid_column: "DerivedColumn | None" = None  # 36.6 C: waited on for the navigator overview
        self._raw_variables: dict[str, Column] = {}  # _file_columns minus the current time column
        self._derived: dict[str, DerivedVariable] = {}
        # Name of the view the current layout was loaded from / saved as, for the
        # image-export stamp (detailed_specification.md 19.3); None after a plain open.
        self._view_name: str | None = None
        self._analysis_windows: list = []  # open AnalysisWindows (25.1); closed on file reopen / time-axis switch
        # A separate pool from TimePlotWidget's (QThreadPool.globalInstance(), used for
        # pan/zoom downsampling): queuing several stats/FFT jobs — each potentially an
        # O(n log n) sort/transform over millions of rows — must not starve the threads
        # interactive pan/zoom redraws depend on for responsiveness.
        self._analysis_thread_pool = QThreadPool(self)
        # One hub per kind of analysis job, all on that pool (detailed_specification.md 37.1);
        # each hub's `pending` dict is the in-flight bookkeeping (`_stats_workers`/`_fft_workers`).
        self._stats_hub = WorkerHub(self._analysis_thread_pool)
        self._fft_hub = WorkerHub(self._analysis_thread_pool)
        self._analysis_hub = WorkerHub(self._analysis_thread_pool)  # XY etc. (analysis_window.py)
        self._navigator_hub = WorkerHub(self._analysis_thread_pool)  # windowed-column overview (40)
        self._navigator_overview_gen = 0
        self._busy_sources: set[str] = set()  # e.g. {"plot_grid", "fft", "stats"}
        # Live, per-session (not persisted to Settings — saved per-View instead, like
        # x/y axis ranges) toggle for TimePlotWidget/PlotGridWidget.set_downsample_enabled.
        # Off means "always show real per-sample data" — still coherent with the
        # pyramid (13.5.1 Phase D): _redraw_series already only uses the pyramid when
        # downsampling is enabled, so this also forces real (precise, possibly slow on
        # a huge windowed column) fetches at every zoom level.
        self._downsample_enabled = True

        self.plot_grid = PlotGridWidget(
            auto_redraw=self.settings.auto_redraw,
            downsample_pixel_ratio=self.settings.downsample_pixel_ratio,
            scroll_bindings=self.settings.scroll_bindings,
        )
        self.navigator = NavigatorWidget()
        self.variable_panel = VariablePanel()
        self.expression_bar = ExpressionBar(self._on_add_derived_variable)

        self._actions: dict[str, QAction] = {}  # shortcut-configurable actions, by settings key

        # User functions + maps (detailed_specification.md 26.1/27.3). Always active
        # regardless of the feature flags: the flags only gate the toolbar entries, so a
        # view using a function still evaluates.
        self._function_registry = FunctionRegistry()
        self.skipped_function_entries: list[dict] = []  # raw entries that failed to load; re-saved verbatim
        self._user_functions_saveable = True  # False when the file existed but was unreadable (26.3)
        self.startup_warnings: list[str] = []
        self._function_manager = None
        self._map_manager = None
        self._function_reference = None

        self._build_layout()
        self._load_functions_and_maps()
        self._build_actions()
        self._wire_signals()

        # Captured *before* restoring any saved layout, so "reset to default" has
        # something to go back to (specification.md 5.11).
        self._default_geometry = self.saveGeometry()
        self._default_state = self.saveState()
        self._restore_layout()
        # Operation log (detailed_specification.md 30.2): results are recorded from this.
        oplog.set_state_provider(self.state_snapshot, self._is_settling)

    def _set_title(self, suffix: str = "", full_path: str | None = None) -> None:
        self._title_suffix = suffix
        title = f"{_APP_TITLE} — {suffix}" if suffix else _APP_TITLE
        if self._debug:
            title += " [DEBUG]"
        self.setWindowTitle(title)
        # Mirrored into the status bar, not just the window title: the title bar is
        # easy to miss (maximized windows, some window managers/remote desktops hide
        # it), so this is the more reliably visible "which file is open" indicator.
        self._file_label.setText(tr("app.file_label", name=suffix) if suffix else "")
        self._file_label.setToolTip(full_path or "")

    # -- layout / actions -------------------------------------------------------

    def _build_layout(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.navigator)
        layout.addWidget(self.plot_grid, 1)
        layout.addWidget(self.expression_bar)
        self.setCentralWidget(central)

        self._variable_dock = QDockWidget(tr("app.variable_dock"), self)
        self._variable_dock.setObjectName("variable_dock")  # needed for saveState/restoreState
        self._variable_dock.setWidget(self.variable_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._variable_dock)

        self.statusBar().addWidget(self._file_label)  # left-aligned, unlike the permanent widgets below
        self.statusBar().addWidget(self._time_order_label)  # next to the file name (29.2), not permanent
        self.statusBar().addWidget(self._notice_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate — LTTB jobs don't report incremental progress
        self._progress_bar.setFormat(tr("app.rendering"))
        self._progress_bar.setMaximumWidth(160)
        self._progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress_bar)

        # Shown while >=1 windowed series (13.5.1 Phase D) is displaying its pyramid's
        # coarse min/max envelope rather than real per-sample data — lets the user
        # know a wide zoom-out is trading precision for not re-fetching close to the
        # whole file (Phase E).
        self._precision_label = QLabel("")
        self.statusBar().addPermanentWidget(self._precision_label)

    @staticmethod
    def _shortcut_sequences(key: str, seq: str) -> list[QKeySequence]:
        """Key sequences for a configurable action. `remove_series` bound to Del also gets
        Backspace: the macOS "delete" key sends Backspace (detailed_specification.md 23.3)."""
        if not seq:
            return []
        seqs = [QKeySequence(seq)]
        if key == "remove_series" and QKeySequence(seq) == QKeySequence(Qt.Key.Key_Delete):
            seqs.append(QKeySequence(Qt.Key.Key_Backspace))
        return seqs

    def _add_action(self, key: str, label: str, toolbar, callback) -> QAction:
        """Toolbar action whose shortcut comes from settings.shortcuts[key]."""
        action = QAction(label, self)
        seq = self.settings.shortcuts.get(key, "")
        if seq:
            action.setShortcut(QKeySequence(seq))
        action.triggered.connect(callback)
        toolbar.addAction(action)
        self._actions[key] = action
        return action

    def _build_actions(self) -> None:
        toolbar = self.addToolBar("main")
        toolbar.setObjectName("main_toolbar")  # needed for saveState/restoreState

        self._add_action("open_parquet", tr("toolbar.open"), toolbar, self.open_parquet_dialog)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(tr("toolbar.time_axis_label")))
        self.time_axis_combo = QComboBox()
        self.time_axis_combo.setMinimumWidth(120)
        self.time_axis_combo.setToolTip(tr("toolbar.time_axis_tooltip"))
        self.time_axis_combo.currentTextChanged.connect(self.set_time_column)
        toolbar.addWidget(self.time_axis_combo)
        toolbar.addSeparator()
        self._add_action("home", tr("toolbar.home"), toolbar, self.plot_grid.go_home)
        self._add_action("redraw", tr("toolbar.redraw"), toolbar, self.plot_grid.redraw)
        toolbar.addSeparator()
        self._add_action("add_plot", tr("toolbar.add_plot"), toolbar, lambda: self.plot_grid.add_plot())
        toolbar.addSeparator()
        # lambda: QAction.triggered(bool) must not leak `checked` into _on_stats_requested's `plot` arg
        self._add_action("stats", tr("toolbar.stats"), toolbar, lambda *_: self._on_stats_requested())
        self._build_graphs_menu(toolbar)
        toolbar.addSeparator()
        if features.is_enabled("user_functions"):  # developer feature flag, detailed_specification.md 21
            self._add_action("user_functions", tr("toolbar.functions"), toolbar, lambda *_: self.open_function_manager())
        if features.is_enabled("maps"):
            self._add_action("maps", tr("toolbar.maps"), toolbar, lambda *_: self.open_map_manager())
        self._add_action("save_view", tr("toolbar.save_view"), toolbar, self.save_view_dialog)
        self._add_action("load_view", tr("toolbar.load_view"), toolbar, self.load_view_dialog)
        self._add_action("export_image", tr("toolbar.export_image"), toolbar, self.export_image_dialog)
        toolbar.addSeparator()
        downsample_action = self._add_action(
            "toggle_downsample", tr("toolbar.toggle_downsample"), toolbar, self._on_toggle_downsample_enabled
        )
        downsample_action.setCheckable(True)
        downsample_action.setChecked(self._downsample_enabled)
        downsample_action.setToolTip(tr("toolbar.toggle_downsample_tooltip"))
        toolbar.addSeparator()
        cursor_action = self._add_action("cursor", tr("toolbar.cursor"), toolbar, self._on_toggle_cursor_mode)
        cursor_action.setCheckable(True)
        cursor_action.setToolTip(tr("toolbar.cursor_tooltip"))
        hover_action = self._add_action("hover_values", tr("toolbar.hover_values"), toolbar, self._on_toggle_hover_values)
        hover_action.setCheckable(True)
        hover_action.setChecked(self.settings.hover_values_enabled)
        hover_action.setToolTip(tr("toolbar.hover_values_tooltip"))
        self.plot_grid.set_hover_enabled(self.settings.hover_values_enabled)
        toolbar.addSeparator()

        # Window-level only (no toolbar button): removes the selected series (23.3). QLineEdit
        # claims Delete/Backspace via ShortcutOverride, so typing in the expression bar is safe.
        remove_series_action = QAction(tr("shortcut.remove_series"), self)
        remove_series_action.setShortcuts(
            self._shortcut_sequences("remove_series", self.settings.shortcuts.get("remove_series", ""))
        )
        remove_series_action.triggered.connect(lambda: self.plot_grid.remove_selected_series())
        self.addAction(remove_series_action)
        self._actions["remove_series"] = remove_series_action

        settings_action = QAction(tr("toolbar.settings"), self)
        settings_action.triggered.connect(self.open_settings_dialog)
        toolbar.addAction(settings_action)

        reset_layout_action = QAction(tr("toolbar.reset_layout"), self)
        reset_layout_action.triggered.connect(self.reset_layout)
        toolbar.addAction(reset_layout_action)

    def _wire_signals(self) -> None:
        self.variable_panel.variableDoubleClicked.connect(self.expression_bar.insert_variable_name)
        self.expression_bar.reference_requested.connect(lambda: self.open_function_reference())
        self.variable_panel.editDerivedRequested.connect(self._on_edit_derived_requested)
        self.variable_panel.deleteDerivedRequested.connect(self._on_delete_derived_requested)
        self.plot_grid.rangeChanged.connect(lambda r: self.navigator.set_selected_range(*r))
        self.navigator.rangeSelected.connect(lambda lo, hi: self.plot_grid.set_x_range(lo, hi))
        self.plot_grid.plotAdded.connect(self._wire_plot)
        self.plot_grid.renderFailed.connect(self._on_render_failed)
        self.plot_grid.busyChanged.connect(lambda busy: self._set_busy("plot_grid", busy))
        self.plot_grid.layoutChanged.connect(self._on_grid_layout_changed)
        self.plot_grid.statsForRangeRequested.connect(lambda plot: self._on_stats_requested(plot=plot))
        self.plot_grid.coarseRenderingChanged.connect(self._set_coarse_indicator)
        for plot in self.plot_grid.plots:
            self._wire_plot(plot)

    def _wire_plot(self, plot) -> None:
        plot.variableDropped.connect(self._on_variable_dropped)

    def _set_coarse_indicator(self, is_coarse: bool) -> None:
        self._precision_label.setText(tr("app.coarse_indicator") if is_coarse else "")

    def _on_toggle_downsample_enabled(self, checked: bool) -> None:
        log_op("toggle_downsample_enabled", enabled=checked)
        self._apply_downsample_enabled(checked, update_action=False)  # the action just set its own state

    def _on_toggle_cursor_mode(self, checked: bool) -> None:
        log_op("toggle_cursor_mode", enabled=checked)
        self.plot_grid.set_cursor_mode_enabled(checked)

    def _on_toggle_hover_values(self, checked: bool) -> None:
        log_op("hover_values", enabled=checked)
        self.plot_grid.set_hover_enabled(checked)
        self.settings.hover_values_enabled = bool(checked)
        save_settings(self.settings)

    def _apply_downsample_enabled(self, enabled: bool, *, update_action: bool) -> None:
        self._downsample_enabled = enabled
        self.plot_grid.set_downsample_enabled(enabled)
        if update_action:
            action = self._actions.get("toggle_downsample")
            if action is not None:
                action.blockSignals(True)
                action.setChecked(enabled)
                action.blockSignals(False)

    def _set_busy(self, source: str, busy: bool) -> None:
        # Several independent background-work sources (downsampling, FFT, stats) share
        # one indicator, so none of them can clear it while another is still running.
        self._busy_sources.discard(source) if not busy else self._busy_sources.add(source)
        self._progress_bar.setVisible(bool(self._busy_sources))

    def _is_settling(self) -> bool:
        """True while drawing is in flight or about to start (a plot's redraw debounce is
        pending) -- the operation log waits for this to clear before writing a result."""
        return bool(self._busy_sources) or any(p._redraw_debounce.isActive() for p in self.plot_grid.plots)

    def state_snapshot(self) -> dict:
        """Compact, JSON-friendly description of what is on screen, for the operation log's
        `result` records (detailed_specification.md 30.2). Never touches data arrays
        (only curve lengths), so it is cheap enough to call after every operation."""
        ds = self._data_source
        file_info: dict = {
            "path": str(ds.path) if ds is not None else None,
            "rows": len(self._time_values) if self._time_values is not None else 0,
            "time_column": self._time_column,
        }
        report = getattr(self, "_time_order_report", None)  # chapter 29, when present
        if report is not None:
            file_info["time_order"] = {
                "ok": getattr(report, "ok", None),
                "nan_count": getattr(report, "nan_count", None),
                "decreasing_count": getattr(report, "decreasing_count", None),
                "first_decreasing_index": getattr(report, "first_decreasing_index", None),
            }
        anchor = self.plot_grid.anchor_plot()
        selected = getattr(self.plot_grid, "_selected", None)  # chapter 23, when present
        ratios = self.plot_grid.plot_height_ratios()
        plots: dict = {}
        for i, plot in enumerate(self.plot_grid.plots):
            (x0, x1), (y0, y1) = plot.getViewBox().viewRange()
            series: dict = {}
            for name in plot.series_names():
                entry = plot._series[name]
                xs = entry["curve"].xData
                series[name] = {
                    "visible": plot.is_series_visible(name) if hasattr(plot, "is_series_visible") else True,
                    "selected": bool(selected is not None and selected[0] is plot and selected[1] == name),
                    "points": 0 if xs is None else len(xs),
                    "coarse": name in plot._coarse_series,
                }
            info: dict = {
                "time_domain": plot.x_axis_datetime,
                "x_range": [x0, x1],
                "y_range": [y0, y1],
                "height_ratio": round(ratios[i], 3) if i < len(ratios) else None,
                "series": plot.series_names(),
                "series_info": series,
            }
            if plot.x_axis_datetime:
                info["x_linked"] = getattr(plot, "x_linked", True)
                info["x_range_text"] = [oplog.format_time(x0), oplog.format_time(x1)]
            plots[plot.plot_id] = info
        lo, hi = self.navigator.selected_range()
        cursor_action = self._actions.get("cursor")
        return {
            "file": file_info,
            "plots": plots,
            "anchor": anchor.plot_id if anchor is not None else None,
            "navigator": {
                "region": [lo, hi],
                "region_text": [oplog.format_time(lo), oplog.format_time(hi)],
            },
            "cursors": {
                "enabled": bool(cursor_action.isChecked()) if cursor_action is not None else False,
                "positions": dict(getattr(self.plot_grid, "_cursor_positions", {})),
            },
            "downsample_enabled": self._downsample_enabled,
            "hover_values": self.plot_grid.plots[0].hover_enabled() if self.plot_grid.plots else None,
            "busy": sorted(self._busy_sources),
            "features": features.effective_features(),
        }

    def _on_render_failed(self, plot, name: str, message: str) -> None:
        """detailed_specification.md 28.2: a plot's background render job raised;
        temporary status-bar message only (no dialog), previous curve stays."""
        self._show_notice(tr("render.failed", name=name, message=message), 8_000)

    def _show_notice(self, text: str, timeout_ms: int) -> None:
        messages.status_notice(text)
        self._notice_label.setText(text)
        self._notice_label.setVisible(True)
        self._notice_timer.start(timeout_ms)  # restarts if a notice is already showing

    def _clear_notice(self) -> None:
        self._notice_timer.stop()
        self._notice_label.setText("")
        self._notice_label.setVisible(False)

    def _update_time_order_warning(self) -> None:
        """Check the current time axis (detailed_specification.md 29.2) and show or
        hide the status-bar warning. Never modal."""
        tv = self._time_values
        if tv is None:
            report = None
        elif isinstance(tv, TimeIndex):
            report = tv.report  # an index only exists for an ascending, finite axis (36.6 D)
        else:
            report = check_time_order(tv)
        self._time_order_report = report
        if report is None or report.ok:
            self._time_order_label.setText("")
            self._time_order_label.setToolTip("")
            self._time_order_label.setVisible(False)
            return
        parts = []
        if report.decreasing_count:
            parts.append(tr("time_order.not_sorted", count=report.decreasing_count))
        if report.nan_count:
            parts.append(tr("time_order.has_nan", count=report.nan_count))
        text = " ".join(parts)
        if report.first_decreasing_index is not None:
            tip = tr("time_order.tooltip", row=report.first_decreasing_index + 1)
        else:
            tip = tr("time_order.tooltip_nan")
        self._time_order_label.setText(text)
        self._time_order_label.setToolTip(tip)
        self._time_order_label.setVisible(True)
        messages.status_notice(text, "warning")
        logger.warning("time axis %r: %s", self._time_column, text)
        log_op(
            "time_order_warning",
            time_column=self._time_column,
            nan_count=report.nan_count,
            decreasing_count=report.decreasing_count,
            first_decreasing_index=report.first_decreasing_index,
        )

    # -- data loading -------------------------------------------------------------

    @oplog.compound
    def open_parquet_dialog(self) -> None:
        path = pick_parquet_file(self, self.settings.last_data_folder, self.settings.recent_data_folders)
        log_op("open_parquet_dialog", chosen=path)
        if path:
            self.load_parquet(path)

    @oplog.compound
    def load_parquet(self, path: str) -> bool:
        """Returns True on success, False if the file couldn't be loaded (an error
        dialog is already shown in that case; callers that chain further work off a
        load, like load_view, must check this before proceeding).
        """
        log_op("load_parquet", path=path)
        # Do all the fallible work on locals first, and only commit to self.* once
        # everything has actually succeeded — otherwise a failure partway through left
        # self._data_source pointing at the new file while self._raw_variables/plots
        # still showed the *previous* file's data, an inconsistent mix of old and new.
        try:
            data_source = ParquetDataSource(path)
            columns = data_source.columns
            if len(columns) < 2:
                raise ValueError(tr("error.no_data_columns"))
            # First column is the time axis by default; the user can change this
            # afterward via the toolbar's time-axis selector (set_time_column).
            time_column = columns[0]
            # Only the time column is read now — it's needed immediately (the
            # navigator, _visible_row_range's np.searchsorted). The time column
            # itself always stays fully resident, even over the threshold below —
            # making it lazy/windowed too needs row_range_for_x() to replace
            # _visible_row_range's np.searchsorted, deliberately left for a future
            # increment (detailed_specification.md 13.5.1 Phase D's scope note).
            row_count = data_source.row_count()
            over_threshold = row_count * 8 > self.settings.eager_load_limit_mb * 1024 * 1024
            time_indexes: dict[str, TimeIndex] = {}
            time_values = self._build_time_index(data_source, time_column, over_threshold)
            if time_values is not None:  # 36.6 D: the axis stays on disk; only the index is resident
                time_indexes[time_column] = time_values
                file_columns: dict[str, Column] = {time_column: WindowedColumn(data_source, time_column)}
            else:
                raw_time = data_source.read_columns([time_column])[time_column]
                time_values = to_numeric(raw_time)
                file_columns = {time_column: MaterializedColumn(time_values)}
            # Every other column: under the threshold, a LazyColumn that reads from
            # disk (and caches) the first time it's actually plotted/used in an
            # expression/selected for stats (Phase C) — a column nothing ever
            # touches costs nothing beyond a small wrapper. Over the threshold, a
            # WindowedColumn instead: even once "used", only the currently-visible
            # row range is ever fetched, re-fetched again on every pan/zoom, so a
            # single huge column can't blow up resident memory the way a LazyColumn
            # materializing its whole self would (Phase D — this is the size check,
            # never a branch in the plotting/expression/stats code itself, which
            # always just calls Column.window()/values() the same way regardless of
            # which of the two this turns out to be).
            for name in columns:
                if name == time_column:
                    continue
                if over_threshold:
                    file_columns[name] = WindowedColumn(data_source, name)
                else:
                    file_columns[name] = LazyColumn(data_source, name)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            logger.exception("failed to load parquet file: %s", path)
            messages.critical(self, tr("error.load_failed_title"), tr("error.load_failed_body", error=e))
            return False

        self._close_analysis_windows()  # their variables belong to the previous file (25.1)
        self._data_source = data_source
        self._file_columns = file_columns
        self._time_column = time_column
        self._time_values = time_values  # already materialized (or an index) above; free
        self._time_indexes = time_indexes
        self._raw_variables = {k: v for k, v in file_columns.items() if k != time_column}
        self._derived = {}
        self._view_name = None
        self.expression_bar.cancel_edit()
        # downsample_enabled is per-view state (like x/y axis range), not a persisted
        # Setting — a plain file open (not via load_view, which applies the view's own
        # saved value right after this returns) resets to the default.
        self._apply_downsample_enabled(True, update_action=True)

        self._clear_plot_grid()

        self.time_axis_combo.blockSignals(True)
        self.time_axis_combo.clear()
        self.time_axis_combo.addItems(columns)
        self.time_axis_combo.setCurrentText(time_column)
        self.time_axis_combo.blockSignals(False)

        self.variable_panel.set_variables(list(self._raw_variables.keys()))
        self._refresh_navigator_overview()

        push_recent_folder(self.settings, str(Path(path).resolve().parent))
        save_settings(self.settings)

        self._set_title(Path(path).name, full_path=str(Path(path).resolve()))
        self._clear_notice()  # an old file's failure must not linger
        self._update_time_order_warning()
        return True

    def _is_over_threshold(self) -> bool:
        """Same size test load_parquet uses to choose WindowedColumn over LazyColumn."""
        return self._data_source.row_count() * 8 > self.settings.eager_load_limit_mb * 1024 * 1024

    def _build_time_index(self, data_source, column: str, over_threshold: bool) -> "TimeIndex | None":
        """36.6 D: a TimeIndex for `column` if the flag is on, the column is over the eager-load
        threshold and ascending/finite (29.1); None -> the caller keeps the fully resident path
        (any failure also falls back, correctness first)."""
        if not (over_threshold and features.is_enabled("big_d_time_index")):
            return None
        try:
            index = TimeIndex.build(data_source, column, pin_rows=[off for off, _n in data_source.row_groups()])
        except Exception:  # noqa: BLE001 - fall back to the resident axis
            logger.exception("time index build failed for %r; using the resident axis", column)
            return None
        log_op("time_index", column=column, built=index is not None)
        return index

    def _time_finite_bounds(self) -> tuple[float, float] | None:
        """(min, max) of the finite time values, None if there are none."""
        tv = self._time_values
        if tv is None or not len(tv):
            return None
        if isinstance(tv, TimeIndex):
            return tv.finite_min_max()
        finite = tv[np.isfinite(tv)]
        return (float(finite.min()), float(finite.max())) if len(finite) else None

    def _clear_plot_grid(self) -> None:
        while len(self.plot_grid.plots) > 1:
            self.plot_grid.remove_plot(self.plot_grid.plots[-1])
        for series_name in self.plot_grid.plots[0].series_names():
            self.plot_grid.plots[0].remove_series(series_name)
        self.plot_grid.clear_cursors()
        self.plot_grid.clear_selection()

    def _on_grid_layout_changed(self) -> None:
        """Plots/series/links changed: refresh the overview, then put the navigator's
        region back on the linked group's current range -- set_overview_data() resets the
        region to the full extent, which would otherwise undo the re-sync after a link
        toggle, reorder or removal (detailed_specification.md 24.1)."""
        self._refresh_navigator_overview()
        group = self._group_x_range()
        if group is not None:
            self.navigator.set_selected_range(group[1], group[2])

    def _group_x_range(self):
        """(plot, x_lo, x_hi) of the X-linked group's range, read from its first non-empty
        plot (an empty plot's viewRange is arbitrary); None if every linked plot is empty.
        The one place that decides "the group's range" for _visible_row_range, view saving,
        the export stamp and the navigator (24.2/24.5/24.6) so they cannot drift."""
        for p in self.plot_grid.linked_plots():
            if not p.is_empty():
                lo, hi = p.getViewBox().viewRange()[0]
                return p, lo, hi
        return None

    def _refresh_navigator_overview(self) -> None:
        """The navigator shows an example waveform just for overall shape/scale, not
        any specific analysis — prefer the anchor (topmost time-domain) plot's first
        series (what the user is actually looking at) over an arbitrary file column,
        falling back to the file's first raw variable when nothing has been plotted at
        all yet. Uses plot_grid.anchor_plot(), not plots[0]: a frequency-domain (FFT)
        plot's series are in Hz, not aligned with self._time_values at all.
        """
        self._detach_nav_pyramid_listener()
        if self._time_values is None:
            return
        first_plot = self.plot_grid.anchor_plot()
        series_names = first_plot.visible_series_names() if first_plot is not None else []
        if series_names:
            # Read back the plotted series' own stored data rather than re-resolving
            # the name through variable_values: a plotted series stays valid even if
            # its name is no longer a "known variable" (e.g. the user just switched
            # the time axis to a column that's also currently plotted as a series —
            # it drops out of _raw_variables, but the already-plotted series is fine).
            name = series_names[0]
            _x, y, _color = first_plot.series_data(name)
            if y is None:  # windowed series (Phase D): no resident y to show as-is
                source = first_plot.series_source(name)
                if source is None:
                    return  # shouldn't happen (y is None only for a windowed series)
                y_source, _y_bounds = source
                if isinstance(y_source, DerivedColumn):
                    self._set_derived_navigator_overview(y_source)
                    return
                self._set_windowed_navigator_overview(y_source)
                return
        elif self._raw_variables:
            column = self._first_plottable_column()
            if column is None:  # every raw column is non-numeric: nothing to preview
                self.navigator.set_overview_data(np.array([], dtype=np.float64), np.array([], dtype=np.float64))
                return
            if isinstance(column, DerivedColumn):
                self._set_derived_navigator_overview(column)
                return
            if isinstance(column, WindowedColumn):
                # Coarse row-group min/max envelope from footer statistics (Phase
                # B), not the flat single (min, max) column_bounds() would give —
                # keeps real shape (e.g. this test data's daily on/off bursts)
                # visible without materializing this WindowedColumn's whole self
                # just to preview it (detailed_specification.md 13.5.1 Phase D).
                self._set_windowed_navigator_overview(column)
                return
            # Materializes one column (the file's first raw variable) even though
            # nothing has been plotted yet — an eager read Phase C doesn't remove
            # for a LazyColumn under the size threshold. Wiring this to Phase B's
            # footer-stats/pyramid unconditionally (not just for the
            # over-threshold case above) was deliberately deferred rather than
            # folded into this already-broad change — see detailed_specification.md
            # 13.5.1's Phase B note.
            y = column.values()
        else:
            return
        tv = self._time_values
        if isinstance(tv, TimeIndex):
            # Lazy axis: per-anchor-interval min/max envelope of the resident y at the held anchor times.
            y = np.asarray(y, dtype=np.float64)
            starts = np.arange(0, min(len(y), len(tv)), ANCHOR_ROWS)
            if len(starts):
                mins, maxs = np.fmin.reduceat(y, starts), np.fmax.reduceat(y, starts)
                t = tv.take(starts)
                x = np.repeat(t, 2)
                yy = np.empty(len(t) * 2)
                yy[0::2], yy[1::2] = mins, maxs
                lo, hi = tv.finite_min_max()  # end points so the bounds cover the whole axis
                x = np.concatenate([[lo], x, [hi]])
                yy = np.concatenate([[yy[0]], yy, [yy[-1]]])
                self.navigator.set_overview_data(x, yy)
            return
        self.navigator.set_overview_data(tv, y)

    def _first_plottable_column(self):
        """The first raw variable whose data can be a numeric series, for the navigator's
        "nothing plotted yet" preview. A file whose second column is a string/list column
        (common: a label next to the signals) must still open (data 29.5),
        so an unusable column is skipped rather than raised; None if there is none."""
        for column in self._raw_variables.values():
            if isinstance(column, (WindowedColumn, DerivedColumn)):
                return column  # footer statistics only; an unusable dtype just has no envelope
            try:
                column.values()
            except NonNumericColumnError:
                continue
            return column
        return None

    def _set_derived_navigator_overview(self, column: DerivedColumn) -> None:
        """Navigator overview of a windowed derived series (36.6 C). A computed column has no
        footer statistics: show its pyramid envelope once one exists (the plot builds it when
        zoomed far out; otherwise a background build is requested here), until then a flat line
        spanning the time axis; a short file is just computed."""
        self._navigator_overview_gen += 1
        self._set_busy("navigator", False)
        tv = self._time_values
        lazy_t = isinstance(tv, TimeIndex)  # 36.6 D: lazy axis, no resident array (D x C)
        if lazy_t:
            t_lo, t_hi = tv.finite_min_max()
        else:
            finite_t = tv[np.isfinite(tv)]
            if not len(finite_t):
                self.navigator.set_overview_data(np.array([], dtype=np.float64), np.array([], dtype=np.float64))
                return
            t_lo, t_hi = float(finite_t.min()), float(finite_t.max())
        pyramid = column.pyramid
        if pyramid is None and len(tv) <= _DERIVED_NAV_DIRECT_ROWS:
            self.navigator.set_overview_data(tv if not lazy_t else tv[0:len(tv)], column.values())
            return
        if pyramid is None:
            self.navigator.set_overview_data(
                np.array([t_lo, t_hi], dtype=np.float64), np.array([0.0, 0.0], dtype=np.float64)
            )
            column.add_pyramid_listener("navigator", self._on_derived_pyramid_for_navigator)  # one-shot
            self._nav_pyramid_column = column
            job = column.request_pyramid()
            if job is not None:
                gen = self._navigator_overview_gen
                self._set_busy("navigator", True)
                self._navigator_hub.submit(
                    job,
                    lambda p, c=column, g=gen: self._on_derived_overview_built(c, g, p),
                    lambda msg, c=column, g=gen: self._on_derived_overview_failed(c, g, msg),
                    job="navigator_overview",
                    target="derived",
                )
            return
        xs: list[float] = []
        ys: list[float] = []
        keep = (pyramid.row_starts < len(tv)) & np.isfinite(pyramid.mins) & np.isfinite(pyramid.maxs)
        t_starts = tv.take(pyramid.row_starts[keep]) if lazy_t else tv[pyramid.row_starts[keep]]  # anchors: O(1)
        for t_start, mn, mx in zip(t_starts, pyramid.mins[keep], pyramid.maxs[keep]):
            xs += [float(t_start), float(t_start)]
            ys += [mn, mx]
        y_first = ys[0] if ys else 0.0
        y_last = ys[-1] if ys else 0.0
        self.navigator.set_overview_data(
            np.array([t_lo, *xs, t_hi], dtype=np.float64), np.array([y_first, *ys, y_last], dtype=np.float64)
        )
        group = self._group_x_range()
        if group is not None:
            self.navigator.set_selected_range(group[1], group[2])

    def _on_derived_pyramid_for_navigator(self, ok: bool) -> None:
        # The pyramid of a derived series landed (or its build failed). One-shot listener: it is
        # gone now. Refresh through the full re-sync, which also puts the region back on the
        # linked group's range (set_overview_data resets it to the full extent, 24.1).
        self._nav_pyramid_column = None
        if ok and self._time_values is not None:
            self._on_grid_layout_changed()

    def _detach_nav_pyramid_listener(self) -> None:
        """The overview is being re-decided: the column that was waiting for its pyramid on the
        navigator's behalf only gets to keep waiting if _set_derived_navigator_overview asks again."""
        column, self._nav_pyramid_column = self._nav_pyramid_column, None
        if column is not None:
            column.remove_pyramid_listener("navigator")

    def _on_derived_overview_built(self, column: DerivedColumn, gen: int, pyramid) -> None:
        self._set_busy("navigator", False)
        column.set_pyramid(pyramid)  # fires on_pyramid_ready -> refresh (a no-op if superseded)

    def _on_derived_overview_failed(self, column: DerivedColumn, gen: int, message: str) -> None:
        self._set_busy("navigator", False)
        column.pyramid_failed()
        logger.warning("derived navigator overview failed: %s", message)

    def _set_windowed_navigator_overview(self, column: WindowedColumn) -> None:
        """Show `column`'s overview in the navigator at once (no data I/O), then -- for a
        file with few row groups and no pyramid yet -- refine it with an evenly thinned
        read on a background job (detailed_specification.md 40)."""
        self._navigator_overview_gen += 1  # drops a still-running refinement for an older request
        self._set_busy("navigator", False)
        x, y = self._windowed_navigator_overview(column)
        self.navigator.set_overview_data(x, y)
        if column.pyramid is not None or self._data_source is None:
            return
        row_groups = self._data_source.row_groups()
        if not (0 < len(row_groups) < _NAV_FEW_ROW_GROUPS):
            return  # many row groups: the footer-stats envelope is already the overview
        gen = self._navigator_overview_gen
        time_values = self._time_values
        path = self._data_source.path
        name = column.name

        def job() -> tuple[np.ndarray, np.ndarray]:
            return thin_column(path, name)  # captures no resident array (37.3)

        self._set_busy("navigator", True)
        self._navigator_hub.submit(
            job,
            lambda result, g=gen, tv=time_values: self._on_navigator_overview_ready(g, tv, result),
            lambda msg, g=gen: self._on_navigator_overview_failed(g, msg),
            job="navigator_overview",
            target=name,
        )

    def _on_navigator_overview_ready(self, gen: int, time_values, result) -> None:
        if gen != self._navigator_overview_gen or time_values is not self._time_values:
            return  # superseded; the newer request owns the busy flag
        self._set_busy("navigator", False)
        idx, vals = result
        keep = idx < len(time_values)
        if isinstance(time_values, TimeIndex):
            x, y = time_values.approx_take(idx[keep]), vals[keep]  # coarse overview: no per-row disk reads
            bounds = time_values.finite_min_max()
        else:
            x, y = time_values[idx[keep]], vals[keep]
            fin = time_values[np.isfinite(time_values)]
            bounds = (fin.min(), fin.max()) if len(fin) else None
        if bounds is not None and len(y):  # end points: bounds = the time axis' finite min/max
            x = np.concatenate([[bounds[0]], x, [bounds[1]]])
            y = np.concatenate([[y[0]], y, [y[-1]]])
        self.navigator.set_overview_data(x, y)
        # set_overview_data() resets the region to the full extent: put it back on the
        # linked group's range (the 24.1 re-sync rule).
        group = self._group_x_range()
        if group is not None:
            self.navigator.set_selected_range(group[1], group[2])

    def _on_navigator_overview_failed(self, gen: int, message: str) -> None:
        if gen != self._navigator_overview_gen:
            return
        self._set_busy("navigator", False)
        logger.warning("navigator overview read failed: %s", message)  # the coarse overview stays (28.2)

    def _windowed_navigator_overview(self, column: WindowedColumn) -> tuple[np.ndarray, np.ndarray]:
        """A coarse overview for `column` without data I/O (detailed_specification.md 40):
        the built pyramid's per-bucket (min, max) if there is one, otherwise the footer
        per-row-group (min, max) envelope. x comes from the resident time column; the
        overview always spans the time axis' finite min/max (end points added), so the
        navigator's bounds / initial selection never collapse to a point.
        """
        tv = self._time_values
        bounds = self._time_finite_bounds()
        if bounds is None:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        t_lo, t_hi = bounds
        xs: list[float] = []
        ys: list[float] = []
        pyramid = column.pyramid
        lazy_t = isinstance(tv, TimeIndex)  # 36.6 D: lazy axis, no resident array
        if pyramid is not None:
            keep = pyramid.row_starts < len(tv)
            # a bucket start is a multiple of DEFAULT_BUCKET_ROWS == ANCHOR_ROWS: an O(1) anchor lookup
            t_starts = tv.take(pyramid.row_starts[keep]) if lazy_t else tv[pyramid.row_starts[keep]]
            for t_start, mn, mx in zip(t_starts, pyramid.mins[keep], pyramid.maxs[keep]):
                xs += [float(t_start), float(t_start)]
                ys += [mn, mx]
        else:
            stats = self._data_source.column_stats(column.name)
            for (row_offset, _row_count), (mn, mx, _null_count) in zip(self._data_source.row_groups(), stats):
                if mn is None or row_offset >= len(tv):
                    continue
                t_off = float(tv[row_offset])  # a TimeIndex pins every row-group start (O(1))
                xs += [t_off, t_off]
                ys += [mn, mx]
        bounds = column.bounds()
        mid = (bounds[0] + bounds[1]) / 2 if bounds else 0.0
        y_first = ys[0] if ys else mid
        y_last = ys[-1] if ys else mid
        x = np.array([t_lo, *xs, t_hi], dtype=np.float64)
        y = np.array([y_first, *ys, y_last], dtype=np.float64)
        return x, y

    @oplog.compound
    def set_time_column(self, name: str) -> None:
        """Switch which loaded column is used as the time (X) axis.

        The previously-selected time column becomes an ordinary variable again (it's
        real data in the file, not something to discard), and every existing
        time-domain plot is re-fit to the new X data — its scale/extent may be
        completely different from the old time column's.
        """
        if not name or self._time_values is None or name == self._time_column or name not in self._file_columns:
            return
        log_op("set_time_column", name=name)
        # Materializes `name` if it was still a LazyColumn (e.g. never plotted) —
        # unavoidable, the time axis needs real data now for searchsorted/navigator.
        # Done before anything is changed: a column that cannot be a time axis (string,
        # list, a file that vanished) must leave the old axis fully in place
        # (data 29.5).
        try:
            index = self._time_indexes.get(name)
            if index is None and not isinstance(self._file_columns[name], MaterializedColumn):
                index = self._build_time_index(self._data_source, name, self._is_over_threshold())
            new_time_values = index if index is not None else self._file_columns[name].values()
        except Exception as e:  # noqa: BLE001 (reported to the user, state untouched)
            logger.exception("cannot use %r as the time axis", name)
            self._show_notice(tr("time_axis.failed", name=name, message=e), 8_000)
            self._sync_time_axis_combo()
            return
        if index is not None:
            self._time_indexes[name] = index
        self._close_analysis_windows()  # row ranges/times of open analysis windows are now stale (25.1)
        self._time_column = name
        self._time_values = new_time_values
        derived_cache = {n: self._raw_variables[n] for n in self._derived if n in self._raw_variables}
        self._raw_variables = {k: v for k, v in self._file_columns.items() if k != name}
        self._raw_variables.update(derived_cache)

        self.variable_panel.set_variables(list(self._raw_variables.keys()), self._derived_expressions())
        self._refresh_navigator_overview()
        self.plot_grid.set_time_axis_data(self._time_values)
        self._update_time_order_warning()

        self._sync_time_axis_combo()

    def _sync_time_axis_combo(self) -> None:
        """Make the toolbar's time-axis combo show the column actually in use."""
        if self._time_column is not None and self.time_axis_combo.currentText() != self._time_column:
            self.time_axis_combo.blockSignals(True)
            self.time_axis_combo.setCurrentText(self._time_column)
            self.time_axis_combo.blockSignals(False)

    # Implementation lives in core/numeric.py (core/pyramid.py needs the identical
    # conversion and core/ must not depend on ui/) — re-exported under this name
    # since it's referenced that way throughout detailed_specification.md.
    _to_numeric = staticmethod(to_numeric)

    # -- variables / expressions ---------------------------------------------------

    def variable_values(self, name: str) -> np.ndarray:
        """The array for any raw or derived variable, materializing a LazyColumn
        (Phase C) on first use if it wasn't already — or, for a WindowedColumn
        (Phase D), reading the *whole* column (its values() falls back to
        window(0, row_count)). Prefer variable_window() when only a specific row
        range is actually needed (FFT/stats): this is still the right call for
        "the whole file" cases (nothing plotted yet, or a derived variable's
        source — see _on_add_derived_variable's note on why that one still forces
        a full read of a WindowedColumn source).
        """
        if name in self._raw_variables:
            return self._raw_variables[name].values()
        if name not in self._derived:
            raise ExpressionError(f"unknown variable: {name}")
        derived = self._derived[name]
        return evaluate_expression(derived.expression, self._all_arrays(), self._function_registry)

    def variable_window(self, name: str, row_start: int, row_end: int) -> np.ndarray:
        """Like variable_values(name)[row_start:row_end], but for a raw variable
        backed by a WindowedColumn (Phase D), fetches only that row range from
        disk instead of materializing the whole column first — what FFT/stats
        should use instead of variable_values()[a:b]. A derived variable is
        already a MaterializedColumn once created (see _on_add_derived_variable),
        so slicing it is plain numpy slicing regardless, not a disk read.
        """
        if name in self._raw_variables:
            return self._raw_variables[name].window(row_start, row_end)
        return self.variable_values(name)[row_start:row_end]

    def _all_arrays(self) -> Mapping[str, np.ndarray]:
        # LazyVariables materializes a Column only when evaluate_expression actually
        # looks it up by name — an expression like `a + b` never touches any other
        # loaded-but-unrelated column (Phase C's point; expression.py needs no
        # change for this, it only ever does `in`/`[]` on what it's handed).
        lazy = LazyVariables(self._raw_variables)
        missing = [n for n in self._derived if n not in self._raw_variables]
        if not missing:
            return lazy
        # Defensive fallback: every _on_add_derived_variable call caches its result
        # into _raw_variables immediately, so in practice `missing` is always empty
        # — this path exists only so a _derived entry without a cached value still
        # evaluates correctly rather than silently returning nothing for it.
        arrays: dict[str, np.ndarray] = {k: v.values() for k, v in self._raw_variables.items()}
        for name in missing:
            arrays[name] = evaluate_expression(self._derived[name].expression, arrays, self._function_registry)
        return arrays

    def _registry_snapshot(self) -> FunctionRegistry:
        """A private copy of the live registry for a DerivedColumn, whose worker-thread
        `window()` calls must never see `update_registry` mutating the live dicts mid-job."""
        return FunctionRegistry(self._function_registry.user_functions, self._function_registry.maps)

    def _derived_expressions(self) -> dict[str, str]:
        return {n: d.expression for n, d in self._derived.items()}

    def _build_derived_column(self, expression: str, columns: Mapping[str, Column], registry) -> Column:
        """The one place a derived variable's Column is made (add, edit, registry update).
        With the `big_c_windowed_derived` flag (36.6 C) an expression over a WindowedColumn
        whose margins are all known becomes a DerivedColumn (computed per window); everything
        else is evaluated in full into a MaterializedColumn, as always. Raises on a bad
        expression before the caller commits anything."""
        if features.is_enabled("big_c_windowed_derived") and self._time_values is not None:
            column = make_derived_column(expression, columns, len(self._time_values), registry)
            if column is not None:
                return column
        value = evaluate_expression(expression, LazyVariables(columns), registry)
        return MaterializedColumn(self._series_value(value))

    def _on_add_derived_variable(self, name: str, expression: str) -> None:
        if name in self._derived:
            # Redefinition via ExpressionBar: same path as the context-menu edit, so
            # dependents and plotted curves don't keep showing stale values (18.2).
            self.edit_derived_variable(name, expression)
            return
        log_op("add_derived_variable", name=name, expression=expression)
        if self._time_values is None:
            raise ExpressionError(tr("error.open_file_first"))
        if name in self._raw_variables:
            raise ExpressionError(tr("error.duplicate_variable_name", name=name))
        # Evaluate eagerly (via _all_arrays()'s lazy mapping, so this only
        # materializes the specific source columns the expression references, not
        # every loaded column) so a typo surfaces immediately instead of at plot time.
        # Note: if a referenced source is a WindowedColumn (Phase D, over the size
        # threshold), LazyVariables.__getitem__ calls its values() -> a full read
        # of that whole column, same cost as the old eager-load-everything path —
        # deriving a variable isn't itself windowed in this pass (deferred; see
        # detailed_specification.md 13.5.1 Phase D's scope note on required_halo).
        column = self._build_derived_column(expression, self._raw_variables, self._registry_snapshot())  # before committing anything
        self._derived[name] = DerivedVariable(name=name, expression=expression)
        self._raw_variables[name] = column
        self.variable_panel.add_variable(name, expression)

    def edit_derived_variable(self, name: str, expression: str) -> None:
        """Redefine a derived variable's formula, recomputing it and every derived
        variable depending on it, and updating their plotted curves in place
        (detailed_specification.md 18.2). All-or-nothing: any evaluation error
        leaves the previous state untouched.
        """
        log_op("edit_derived_variable", name=name, expression=expression)
        if name not in self._derived:
            raise ExpressionError(tr("error.not_derived_variable", name=name))
        exprs = self._derived_expressions()
        dependents = dependents_of(name, exprs)
        if referenced_names(expression) & ({name} | set(dependents)):
            raise ExpressionError(tr("error.circular_definition", name=name))

        exprs[name] = expression
        overlay: dict[str, Column] = {}
        columns = ChainMap(overlay, self._raw_variables)  # new columns shadow the cached (old) ones
        for n in [name, *dependents]:
            overlay[n] = self._build_derived_column(exprs[n], columns, self._registry_snapshot())

        self._derived[name] = DerivedVariable(name=name, expression=expression)
        for n, column in overlay.items():
            self._raw_variables[n] = column
        self.variable_panel.add_variable(name, expression)
        anchor = self.plot_grid.anchor_plot()
        anchor_affected = False
        for plot in self._time_plots():
            for n in plot.series_names():
                if n in overlay:
                    self._replace_plot_series(plot, n, overlay[n])
                    anchor_affected |= plot is anchor
        if anchor_affected:
            self._refresh_navigator_overview()

    # -- user functions / maps (detailed_specification.md 26, 27) ---------------------

    @property
    def function_registry(self) -> FunctionRegistry:
        return self._function_registry

    def _load_functions_and_maps(self) -> None:
        """Startup: load maps then user functions into the registry (27.3, 26.3). Problems are
        collected and shown once as a non-blocking status-bar notice."""
        warnings: list[str] = []
        tables, map_warnings = maps_io.load_all_maps()
        warnings += [tr("startup.map_warning", detail=w) for w in map_warnings]
        self._function_registry.maps.update(tables)
        try:
            functions, fn_warnings, self.skipped_function_entries = uf_io.load_user_functions_ex(tables)
            self._function_registry.user_functions.update({f.name: f for f in functions})
            warnings += [tr("startup.function_warning", detail=w) for w in fn_warnings]
        except uf_io.UserFunctionsUnreadable as e:
            self._user_functions_saveable = False
            warnings.append(tr("startup.functions_unreadable", error=e))
        self.startup_warnings = warnings
        if warnings:
            self._show_notice(" / ".join(warnings), 20_000)

    def reserved_names(self) -> set[str]:
        """Names a new function / map must not take: every registry name plus the names of
        functions that failed to load and are still in the file (they come back once fixed)."""
        names = self._function_registry.all_names()
        names.update(n for n in map(uf_io.skipped_entry_name, self.skipped_function_entries) if n)
        return names

    def save_functions(self, functions: Mapping[str, object]) -> None:
        """Write the function file: the valid ones, then the skipped raw entries unchanged."""
        uf_io.save_user_functions(list(functions.values()), self.skipped_function_entries)

    @staticmethod
    def _replace_plot_series(plot, name: str, column: Column) -> None:
        """Point an existing derived series at its recomputed Column (resident or windowed)."""
        if isinstance(column, DerivedColumn):
            plot.replace_series_source(name, column, column.bounds())
        else:
            plot.replace_series_y(name, column.values())

    def _series_value(self, value) -> np.ndarray:
        """The one place a derived result becomes a series array (add, edit, registry update):
        float64; a 0-d scalar is broadcast to a constant series over the time axis; any other
        shape mismatch is an ExpressionError (so it fails before anything is committed)."""
        arr = np.asarray(value, dtype=np.float64)
        n = len(self._time_values) if self._time_values is not None else 0
        if arr.ndim == 0:
            return np.full(n, float(arr))
        if arr.shape != (n,):
            raise ExpressionError(tr("error.bad_series_shape", shape=arr.shape, n=n))
        return arr

    @property
    def user_functions_saveable(self) -> bool:
        return self._user_functions_saveable

    def open_function_manager(self) -> None:
        """Non-modal singleton (26.4): a second call raises the existing window."""
        from .function_manager import FunctionManagerWindow

        if self._function_manager is None:
            self._function_manager = FunctionManagerWindow(self)
            self._function_manager.destroyed.connect(lambda *_: setattr(self, "_function_manager", None))
        self._function_manager.show()
        self._function_manager.raise_()
        self._function_manager.activateWindow()

    def open_function_reference(self, target_provider=None) -> None:
        """Show the function reference (38). `target_provider` returns the line edit "Insert into
        expression" writes to (None/closed -> disabled); default: the expression bar's field."""
        log_op("open_function_reference")
        from .function_reference import FunctionReferenceDialog

        if target_provider is None:
            target_provider = lambda: self.expression_bar.expr_edit  # noqa: E731
        if self._function_reference is None:
            self._function_reference = FunctionReferenceDialog(self, target_provider)
            self._function_reference.destroyed.connect(lambda *_: setattr(self, "_function_reference", None))
        else:
            self._function_reference.set_target_provider(target_provider)
        self._function_reference.show()
        self._function_reference.raise_()
        self._function_reference.activateWindow()

    def _refresh_function_reference(self) -> None:
        if self._function_reference is not None:
            self._function_reference.refresh()

    def open_map_manager(self) -> None:
        """Non-modal singleton (27.4)."""
        from .map_manager import MapManagerWindow

        if self._map_manager is None:
            self._map_manager = MapManagerWindow(self)
            self._map_manager.destroyed.connect(lambda *_: setattr(self, "_map_manager", None))
        self._map_manager.show()
        self._map_manager.raise_()
        self._map_manager.activateWindow()

    def callable_users(self, name: str) -> list[str]:
        """What directly uses the user function / map `name`: derived variables (by name) and
        other user functions (shown as `Fn <name>`), for the in-use refusals (26.4, 27.4)."""
        users = []
        for n, d in self._derived.items():
            try:
                if name in referenced_functions(d.expression):
                    users.append(n)
            except ExpressionError:
                continue
        for n, f in self._function_registry.user_functions.items():
            if n == name:
                continue
            try:
                if name in referenced_functions(f.expression):
                    users.append(tr("function.user_function_ref", name=n))
            except ExpressionError:
                continue
        return users

    def update_registry(
        self,
        *,
        user_functions: Mapping[str, object] | None = None,
        maps: Mapping[str, object] | None = None,
        touched: set[str] = frozenset(),
        persist: Callable[[], None] | None = None,
    ) -> list[tuple[str, str]]:
        """Replace the registry's user functions and/or maps (26.4, 27.4) and recompute every
        derived variable that uses a `touched` name -- directly, via other user functions,
        or via other derived variables -- all-or-nothing (18.2's rule).

        Returns the failures `[(derived name, reason)]`; non-empty means nothing was changed.
        `persist` runs after the recompute succeeded and before anything is committed; if it
        raises, the exception propagates and nothing is changed.
        """
        candidate = FunctionRegistry(
            self._function_registry.user_functions if user_functions is None else user_functions,
            self._function_registry.maps if maps is None else maps,
        )
        # Names whose meaning changes: the touched ones plus user functions calling them
        # (using the candidate's bodies, so a just-edited function's new calls count).
        names = set(touched)
        changed = True
        while changed:
            changed = False
            for n, f in candidate.user_functions.items():
                if n in names:
                    continue
                try:
                    called = referenced_functions(f.expression)
                except ExpressionError:
                    continue
                if called & names:
                    names.add(n)
                    changed = True
        exprs = self._derived_expressions()
        roots = []
        for n, e in exprs.items():
            try:
                if referenced_functions(e) & names:
                    roots.append(n)
            except ExpressionError:
                continue
        affected = set(roots)
        for r in roots:
            affected.update(dependents_of(r, exprs))
        order = [n for n in dependency_order(exprs) if n in affected]

        overlay: dict[str, Column] = {}
        failures: list[tuple[str, str]] = []
        failed: set[str] = set()
        columns = ChainMap(overlay, self._raw_variables)
        for n in order:
            if referenced_names(exprs[n]) & failed:
                failed.add(n)  # only the root cause is reported
                continue
            try:
                overlay[n] = self._build_derived_column(exprs[n], columns, candidate)
            except UnknownFunctionError as e:
                failed.add(n)
                failures.append((n, tr("error.unknown_function", name=e.name)))
            except Exception as e:  # noqa: BLE001 (reported to the user)
                failed.add(n)
                failures.append((n, str(e) or type(e).__name__))
        if failures:
            return failures

        if persist is not None:
            persist()
        self._function_registry.user_functions.clear()
        self._function_registry.user_functions.update(candidate.user_functions)
        self._function_registry.maps.clear()
        self._function_registry.maps.update(candidate.maps)
        self._refresh_function_reference()
        for n, column in overlay.items():
            self._raw_variables[n] = column  # DerivedColumns hold `candidate` (an immutable snapshot, 36.6 C)
        anchor = self.plot_grid.anchor_plot()
        anchor_affected = False
        for plot in self._time_plots():
            for n in plot.series_names():
                if n in overlay:
                    self._replace_plot_series(plot, n, overlay[n])
                    anchor_affected |= plot is anchor
        if anchor_affected:
            self._refresh_navigator_overview()
        return []

    def delete_derived_variable(self, name: str) -> None:
        """Remove a derived variable from the list and every time-domain plot.
        Refused if another derived variable still references it — no cascading
        delete (detailed_specification.md 18.2).
        """
        log_op("delete_derived_variable", name=name)
        if name not in self._derived:
            raise ExpressionError(tr("error.not_derived_variable", name=name))
        users = [
            n for n, d in self._derived.items() if n != name and name in referenced_names(d.expression)
        ]
        if users:
            raise ExpressionError(tr("error.derived_in_use", name=name, users=", ".join(users)))
        anchor = self.plot_grid.anchor_plot()
        anchor_affected = False
        for plot in self._time_plots():
            if name in plot.series_names():
                plot.remove_series(name)
                anchor_affected |= plot is anchor
        del self._derived[name]
        self._raw_variables.pop(name, None)
        self.variable_panel.remove_variable(name)
        if self.expression_bar.editing_name == name:
            self.expression_bar.cancel_edit()
        if anchor_affected:
            self._refresh_navigator_overview()

    def _time_plots(self) -> list:
        # FFT plots hold static "<name> (FFT...)" results, never live variable references.
        return [p for p in self.plot_grid.plots if p.x_axis_datetime]

    def _on_edit_derived_requested(self, name: str) -> None:
        # In the expression bar, not a modal dialog, so the variable list stays
        # usable for drag/double-click insertion while editing (18.3).
        if name in self._derived:
            self.expression_bar.start_edit(name, self._derived[name].expression)

    def _on_delete_derived_requested(self, name: str) -> None:
        if name not in self._derived:
            return
        answer = messages.question(
            self, tr("variable.delete_title"), tr("variable.delete_confirm", name=name)
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.delete_derived_variable(name)
        except ExpressionError as e:
            messages.warning(self, tr("variable.delete_title"), str(e))

    def _on_variable_dropped(self, plot, name: str) -> None:
        log_op("overlay_variable", plot=plot.plot_id, variable=name)
        if self._time_values is None:
            return
        if name not in self._raw_variables and name not in self._derived:
            # Any text can be dropped on a plot (e.g. dragged in from another application), and
            # a stale name is possible too (plots 24.8): ignore it instead of raising from the drop slot.
            logger.info("drop ignored: %r is not a known variable", name)
            return
        try:
            self._add_variable_to_plot(plot, name)
        except NonNumericColumnError as e:  # a string/list column: nothing to draw (data 29.5)
            logger.warning("cannot plot %r: %s", name, e)
            self._show_notice(tr("variable.not_numeric", name=name), 8_000)
            return
        if self.plot_grid.plots and plot is self.plot_grid.plots[0]:
            self._refresh_navigator_overview()

    def _add_variable_to_plot(self, plot, name: str, color: str | None = None) -> None:
        """Plot `name` on `plot`, choosing add_series() (a resident array) or
        add_windowed_series() (Phase D) based on whether the resolved Column
        happens to be a WindowedColumn — the only place that distinction is ever
        branched on; TimePlotWidget/the rest of MainWindow never need to know
        which kind a given series is.
        """
        column = self._raw_variables.get(name)
        if isinstance(column, (WindowedColumn, DerivedColumn)):
            plot.add_windowed_series(name, self._time_values, column, column.bounds(), color)
        else:
            plot.add_series(name, self._time_values, self.variable_values(name), color)

    def _visible_row_range(self, plot=None) -> tuple[int, int]:
        """Row range of self._time_values covered by the visible X range
        (detailed_specification.md 24.2). No argument: the shared X range of the X-linked
        time-domain group (an independent plot's and a frequency-domain (FFT) plot's own
        range are ignored here); falls back to the full range if no linked plot has
        anything plotted yet — an empty plot's viewRange() is pyqtgraph's arbitrary
        default (not fitted to any data), not the real data extent. With `plot`: that
        plot's own range.
        """
        if plot is None:
            group = self._group_x_range()
            if group is None:
                return 0, len(self._time_values)
            x_lo, x_hi = group[1], group[2]
        if plot is not None:
            x_lo, x_hi = plot.getViewBox().viewRange()[0]
        row_start = max(0, int(np.searchsorted(self._time_values, x_lo, side="left")))
        row_end = min(len(self._time_values), int(np.searchsorted(self._time_values, x_hi, side="right")))
        return row_start, row_end

    def _build_graphs_menu(self, toolbar) -> None:
        """The "Graphs" drop-down (25.5): XY / 3D, and FFT while its feature flag is on. Replaces
        the standalone XY / 3D / FFT toolbar buttons; an entry whose flag is off is absent, and
        with no entry at all there is no button. The entries' shortcuts stay configurable
        (`xy_plot`, `fft`)."""
        menu = QMenu(self)
        menu.setObjectName("graphs_menu")
        if features.is_enabled("xy_plot"):  # developer feature flag, detailed_specification.md 21
            label = tr("graphs.xy_3d") if features.is_enabled("xyz_3d") else tr("graphs.xy")
            action = self._add_action("xy_plot", label, menu, lambda *_: self.open_xy_plot())
            self.addAction(action)  # keep the shortcut live although the menu is closed
        if features.is_enabled("fft"):
            action = self._add_action("fft", tr("graphs.fft"), menu, lambda *_: self._on_fft_requested())
            self.addAction(action)
        if not menu.actions():
            return
        button = QToolButton()
        button.setObjectName("graphs_button")
        button.setText(tr("toolbar.graphs"))
        button.setToolTip(tr("toolbar.graphs_tooltip"))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        toolbar.addWidget(button)
        self.graphs_button = button

    # -- analysis windows (25.1) ---------------------------------------------------

    def analysis_range(self) -> tuple[int, int, float, float]:
        """(row_start, row_end, x_lo, x_hi) of the linked group's visible range (24.2)."""
        row_start, row_end = self._visible_row_range()
        tv = self._time_values
        if row_end > row_start:
            return row_start, row_end, float(tv[row_start]), float(tv[row_end - 1])
        return row_start, row_end, float("nan"), float("nan")

    def open_xy_plot(self, mode: str = "2d"):
        """Open a new XY / 3D plot window (25.2, 25.5); several may be open at once."""
        if self._data_source is None:
            messages.information(self, tr("toolbar.xy_plot"), tr("error.open_file_first"))
            return None
        from .xy_plot_window import XYPlotWindow

        selected = [item.text() for item in self.variable_panel.selectedItems()]
        x_name, y_names = (selected[0], selected[1:]) if len(selected) >= 2 else ("", [])
        window = XYPlotWindow(self, x_name, y_names, mode)
        self._analysis_windows.append(window)
        window.show()
        if (x_name and y_names) or window.mode == "3d":
            window.recompute()
        return window

    def ensure_gl3d(self) -> bool:
        """Probe OpenGL on the first 3D use outside the XY window (the map manager's surface
        toggle, 25.3). On failure: log it and tell the user once; later calls just return False."""
        from . import xyz_3d_window

        status = xyz_3d_window.gl3d_status()
        if status is None:
            status = xyz_3d_window.check_gl3d()
            if not status[0]:
                logger.warning("3D view disabled: %s", status[1])
                log_op("xyz_3d_unavailable", reason=status[1])
                messages.warning(self, tr("xyz.unavailable_title"), tr("xyz.unavailable", reason=status[1]))
        return status[0]

    def _analysis_window_closed(self, window) -> None:
        if window in self._analysis_windows:
            self._analysis_windows.remove(window)

    def _close_analysis_windows(self) -> None:
        for window in list(self._analysis_windows):
            window.close()
        self._analysis_windows.clear()

    def _on_fft_requested(self) -> None:
        selected = self.variable_panel.selectedItems()
        if not selected or self._time_values is None:
            messages.information(self, tr("toolbar.fft"), tr("fft.select_variable"))
            return
        name = selected[0].text()

        # FFT is scoped to the currently visible time range, not the whole file
        # (see _FFT_MAX_ROWS above for why).
        row_start, row_end = self._visible_row_range()

        if row_end - row_start > _FFT_MAX_ROWS:
            messages.information(
                self,
                tr("toolbar.fft"),
                tr("fft.range_too_wide", rows=row_end - row_start, max_rows=_FFT_MAX_ROWS),
            )
            return

        log_op("fft", variable=name, row_start=row_start, row_end=row_end)
        x = self._time_values[row_start:row_end]
        try:
            y = self.variable_window(name, row_start, row_end)
        except Exception as exc:  # noqa: BLE001 - e.g. a failed disk read (28.2)
            logger.exception("fft: reading %r failed", name)
            messages.warning(self, tr("toolbar.fft"), tr("fft.failed", message=f"{type(exc).__name__}: {exc}"))
            return
        if not self._fft_sampling_ok(x):
            return
        label = f"{name} (FFT: {x[0]:.3f}–{x[-1]:.3f}s)" if len(x) else f"{name} (FFT)"

        self._set_busy("fft", True)
        self._fft_hub.submit(
            lambda: fft(x, y),
            lambda result, lbl=label: self._on_fft_finished(lbl, *result),
            self._on_fft_failed,
            job="fft",
            target=name,
        )

    def _fft_sampling_ok(self, x: np.ndarray) -> bool:
        """detailed_specification.md 32: refuse to start FFT unless the range is evenly
        sampled. Non-modal: the reason goes to the status bar (and the op log)."""
        report = sampling_report(x)
        if report.uniform:
            return True
        if report.reason == "too_few":
            text = tr("fft.too_few_samples")
        elif report.reason == "not_finite":
            text = tr("fft.time_not_finite")
        elif report.reason == "not_ascending":
            text = tr("fft.time_not_ascending")
        elif report.gap_count:  # a deviation like "300000%" says less than the gap count
            text = tr("fft.has_gaps", dt=f"{report.dt_median:.6g}", gaps=report.gap_count)
        else:
            text = tr(
                "fft.not_uniform",
                dt=f"{report.dt_median:.6g}",
                dev=f"{report.max_rel_deviation * 100:.1f}",
                gaps=report.gap_count,
            )
        log_op("fft_refused", reason=report.reason, gap_count=report.gap_count)
        messages.status_notice(text, "warning")
        self.statusBar().showMessage(text, 20000)
        return False

    @property
    def _fft_workers(self) -> dict:
        return self._fft_hub.pending

    @property
    def _stats_workers(self) -> dict:
        return self._stats_hub.pending

    def _on_fft_failed(self, message: str) -> None:
        """detailed_specification.md 28.2: clear the busy flag and
        tell the user (modal is fine: FFT is an explicit one-shot action)."""
        self._set_busy("fft", bool(self._fft_workers))
        messages.warning(self, tr("toolbar.fft"), tr("fft.failed", message=message))

    def _on_fft_finished(self, label: str, freqs: np.ndarray, amps: np.ndarray) -> None:
        self._set_busy("fft", bool(self._fft_workers))
        plot = self.plot_grid.add_plot(x_axis_datetime=False)  # X is frequency, not time
        plot.setLabel("bottom", "Frequency", units="Hz")
        plot.add_series(label, freqs, amps)

    def _plotted_variable_names(self) -> list[str]:
        """Names of every variable currently overlaid on a time-domain plot, across
        every plot, in plot/series order with duplicates (the same variable overlaid
        on more than one plot) removed. A frequency-domain (FFT) plot's series are
        already FFT output, not a raw/derived variable, so those plots are skipped —
        same exclusion as _visible_row_range.
        """
        seen: dict[str, None] = {}  # insertion-ordered "set"
        # Only the linked group's plots: the toolbar Stats computes over the group's range, so
        # series that exist only on an independent plot are reached via that plot's "Stats for
        # this range" instead (24.2).
        for plot in self.plot_grid.linked_plots():
            for name in plot.visible_series_names():  # hidden series are not stats targets (23.1)
                seen.setdefault(name, None)
        return list(seen)

    def _on_stats_requested(self, plot=None) -> None:
        """Stats over the visible range: the X-linked group's, or `plot`'s own range
        (context menu "Stats for this range" on an independent plot, 24.2)."""
        selected = self.variable_panel.selectedItems()
        if selected:
            candidate_names = [item.text() for item in selected]
        elif plot is not None:
            candidate_names = plot.visible_series_names()  # this plot's series, not the whole grid's
        else:
            # Nothing explicitly selected in the variable panel -- default to
            # whichever variables are currently plotted, so a variable already
            # overlaid on a plot doesn't also need re-selecting in the panel just to
            # see its stats. Explicit panel selection still takes priority when
            # there is one (e.g. to check a variable that isn't plotted at all).
            candidate_names = self._plotted_variable_names()
        if not candidate_names or self._time_values is None:
            messages.information(self, tr("toolbar.stats"), tr("stats.select_variable"))
            return

        # Unlike FFT, there's no row cap here: mean/min/max/std/median stay meaningful
        # (and cheap — O(n), one pass) regardless of sampling gaps, and the user asked
        # specifically for "all the data" in the visible range, not a downsampled or
        # truncated view of it.
        row_start, row_end = self._visible_row_range(plot)
        tv = self._time_values  # indexing only the two end rows keeps a lazy axis from being read in full
        range_label = (
            tr("stats.range_with_count", start=tv[row_start], end=tv[row_end - 1], count=row_end - row_start)
            if row_end > row_start
            else tr("stats.empty_range")
        )
        extra = {"plot": plot.plot_id} if plot is not None else {}
        log_op("stats", variables=candidate_names, row_start=row_start, row_end=row_end, **extra)

        # Resolve every candidate up front rather than inside the per-worker loop
        # below: a name coming from _plotted_variable_names() (unlike a panel
        # selection, which is always a currently-known variable) could in principle
        # no longer resolve to one, and skipping it here keeps `pending`'s
        # name/result bookkeeping consistent from the start instead of needing to
        # patch it up mid-flight.
        resolved: dict[str, np.ndarray | DerivedColumn] = {}
        errors: dict[str, str] = {}
        for name in candidate_names:
            try:
                column = self._raw_variables.get(name)
                if isinstance(column, DerivedColumn):
                    resolved[name] = column  # aggregated per window in the worker (36.6 C)
                    continue
                resolved[name] = self.variable_window(name, row_start, row_end)
            except (ExpressionError, KeyError):
                logger.info("stats: skipping unresolved variable %r", name)
            except Exception as exc:  # noqa: BLE001 - e.g. a failed disk read (28.2)
                logger.exception("stats: reading %r failed", name)
                errors[name] = f"{type(exc).__name__}: {exc}"
        if not resolved and not errors:
            messages.information(self, tr("toolbar.stats"), tr("stats.cannot_compute"))
            return

        names = [n for n in candidate_names if n in resolved or n in errors]
        pending = {
            "range_label": range_label,
            "names": names,
            "results": {n: {"error": m} for n, m in errors.items()},
            "remaining": len(resolved),
        }
        if not resolved:
            self._show_stats_dialog(pending)
            return
        for name, y in resolved.items():
            self._set_busy("stats", True)
            self._stats_hub.submit(
                (lambda col=y: chunked_stats(col.window, row_start, row_end))
                if isinstance(y, DerivedColumn)
                else (lambda y=y: basic_stats(y)),
                lambda stats, n=name, batch=pending: self._on_stats_finished(n, stats, batch),
                lambda msg, n=name, batch=pending: self._on_stats_finished(n, {"error": msg}, batch),
                job="stats",
                target=name,
            )

    def _on_stats_finished(self, name: str, stats: dict, batch: dict) -> None:
        # `stats` is {"error": msg} for a variable whose worker failed (28.2); the
        # remaining variables still show normally.
        self._set_busy("stats", bool(self._stats_workers))
        batch["results"][name] = stats
        batch["remaining"] -= 1
        if batch["remaining"] > 0:
            return  # other variables in this same request are still computing
        self._show_stats_dialog(batch)

    def _show_stats_dialog(self, batch: dict) -> None:
        # Preserve the order the user selected the variables in, not completion order.
        ordered_results = {name: batch["results"][name] for name in batch["names"]}
        dialog = StatsDialog(batch["range_label"], ordered_results, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Esc clears the series selection (23.2). Done here rather than as a QShortcut so it
        # cannot become ambiguous with the expression bar's own Esc shortcut.
        if event.key() == Qt.Key.Key_Escape and self.plot_grid.selected_series() is not None:
            self.plot_grid.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- views -----------------------------------------------------------------------

    def save_view_dialog(self) -> None:
        if self._data_source is None:
            messages.information(self, tr("toolbar.save_view"), tr("error.open_file_first"))
            return
        name, ok = QInputDialog.getText(self, tr("toolbar.save_view"), tr("view.name_label"))
        if not ok or not name.strip():
            return

        plots = []
        x_axis_range = None
        ratios = dict(zip(self.plot_grid.plots, self.plot_grid.plot_height_ratios()))
        for plot in self.plot_grid.plots:
            series = []
            for series_name in plot.series_names():
                _, _, color = plot.series_data(series_name)
                series.append(
                    view_io.SeriesDef(
                        variable=series_name, color=color, visible=plot.is_series_visible(series_name)
                    )
                )
            if not series:
                continue
            x_range, y_range = plot.getViewBox().viewRange()
            plots.append(
                view_io.PlotDef(
                    plot_id=plot.plot_id,
                    series=series,
                    y_axis_range=(y_range[0], y_range[1]),
                    height_ratio=ratios[plot],
                    x_linked=self.plot_grid._is_x_linked(plot) or not plot.x_axis_datetime,
                    # Only an independent time plot carries its own X range (24.5).
                    x_axis_range=(x_range[0], x_range[1]) if plot.x_axis_datetime and not plot.x_linked else None,
                )
            )

        group = self._group_x_range()  # shared helper (24.5)
        if group is not None:
            x_axis_range = (group[1], group[2])
        view = view_io.View(
            view_name=name.strip(),
            source=view_io.SourceDef(
                parquet_path=str(self._data_source.path),
                path_type="absolute",
                relative_path=view_io.relative_source_path(name.strip(), str(self._data_source.path)),
            ),
            time_column=self._time_column,
            plots=plots,
            x_axis_range=x_axis_range,
            # Dependency order, not insertion order: an edit can make an earlier
            # variable reference a later one (detailed_specification.md 18.4).
            derived_variables=[
                view_io.DerivedVariableDef(name=n, expression=self._derived[n].expression)
                for n in dependency_order(self._derived_expressions())
            ],
            downsample_enabled=self._downsample_enabled,
        )
        try:
            path = view_io.save_view(view)
        except ValueError as e:
            messages.warning(self, tr("toolbar.save_view"), tr("view.invalid_name", error=e))
            return
        except OSError as e:  # name too long for the file system, unwritable data folder, disk full (persistence 35.1)
            logger.exception("failed to save view: %s", view.view_name)
            messages.warning(self, tr("toolbar.save_view"), tr("view.save_failed", error=e))
            return
        log_op("save_view", name=view.view_name, path=str(path))
        self._view_name = view.view_name
        messages.information(self, tr("toolbar.save_view"), tr("view.saved", path=path))

    def load_view_dialog(self) -> None:
        """Open the view management window (detailed_specification.md 20.3)."""
        # Minus the time column: load_view resolves against _raw_variables, which
        # never holds it, so a view referencing it gets that item skipped (20.2).
        available = (
            {c for c in self._file_columns if c != self._time_column} if self._data_source is not None else None
        )
        all_columns = set(self._file_columns) if self._data_source is not None else None
        dialog = ViewManagerDialog(
            available,
            self,
            all_columns=all_columns,
            available_functions=self._function_registry.all_names(),
            registry=self._function_registry,
        )
        dialog.viewDeleted.connect(self._on_view_deleted)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.selected_view_name()
        if name:
            self.load_view(name)

    def _on_view_deleted(self, name: str) -> None:
        log_op("delete_view", name=name)
        if name == self._view_name:
            self._view_name = None

    @oplog.compound
    def load_view(self, name: str) -> None:
        log_op("load_view", name=name)
        try:
            view = view_io.load_view(name)
            source_path, source_how = view_io.resolve_source(view)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            logger.exception("failed to load view: %s", name)
            messages.critical(self, tr("view.load_error_title"), tr("view.load_failed_body", error=view_error_text(e)))
            return

        # If *any* file is already open — a different one, or literally the view's own
        # recorded source — apply only the saved waveform layout to it rather than
        # re-reading a parquet from disk that's already loaded. Re-opening the same
        # file via load_parquet would be pure waste (a full re-read, undoing whatever
        # LazyColumns/WindowedColumns already materialized from it — 13.5.1) for no
        # behavioral benefit, since the view is about to fully replace the plot layout
        # and derived variables anyway. This also covers re-using a layout across
        # several parquet files that share the same column names. Only reload from
        # disk via load_parquet when nothing is open yet.
        if self._data_source is not None:
            log_op(
                "load_view_layout_only",
                name=name,
                view_source=str(source_path),
                current_file=str(self._data_source.path),
                same_file=Path(self._data_source.path).resolve() == source_path.resolve(),
            )
            self._clear_plot_grid()
            self._raw_variables = {k: v for k, v in self._file_columns.items() if k != self._time_column}
            self._derived = {}
            self.expression_bar.cancel_edit()
            self.variable_panel.set_variables(list(self._raw_variables.keys()))
        elif not self.load_parquet(str(source_path)):
            return  # load_parquet already showed an error dialog

        log_op("load_view_source", name=name, path=str(source_path), resolved_by=source_how)
        self._view_name = view.view_name
        # Each derived variable / series is applied independently below and a failure
        # (e.g. its source column doesn't exist in whichever file is actually open —
        # expected and common on the "reuse a layout across files that share *some*
        # column names" path above, not just when the recorded source file itself was
        # regenerated) only skips that one item, rather than aborting every subsequent
        # derived variable/series/plot too. `skipped` collects what didn't make it, for
        # one consolidated warning at the end instead of per-item dialogs.
        skipped: list[str] = []
        try:
            # Time axis first (persistence 33): saved X ranges are in that column's units, and
            # set_time_column go_home()s the plots, so it must precede the layout rebuild.
            if view.time_column is not None:
                if view.time_column in self._file_columns:
                    if view.time_column != self._time_column:
                        self.set_time_column(view.time_column)
                else:
                    skipped.append(tr("view.time_column_missing", name=view.time_column))
            # Applied before plotting any series, so their first draw already respects
            # the loaded setting instead of drawing once and immediately redrawing.
            self._apply_downsample_enabled(view.downsample_enabled, update_action=True)

            saved = {d.name: d for d in view.derived_variables}
            for derived in (saved[n] for n in dependency_order({n: d.expression for n, d in saved.items()})):
                try:
                    self._on_add_derived_variable(derived.name, derived.expression)
                except UnknownFunctionError as e:
                    skipped.append(f"{derived.name} ({tr('error.unknown_function', name=e.name)})")
                except (ExpressionError, KeyError):
                    skipped.append(derived.name)

            # The grid was already cleared above (either branch) down to a single plot,
            # which is guaranteed time-domain: PlotGridWidget.remove_plot() refuses to
            # remove the last time-domain plot, and move_plot_up/down never let an FFT
            # plot cross in front of one, so plots[0] can't end up being an FFT plot.
            first_plot = self.plot_grid.plots[0]
            kept_ratios: list[float | None] = []  # height_ratio of each plot that survived (22.2)
            kept_plots: list[tuple] = []  # (plot, PlotDef) of each plot that survived (24.5)
            for i, plot_def in enumerate(view.plots):
                plot = first_plot if i == 0 else self.plot_grid.add_plot()
                for series in plot_def.series:
                    try:
                        # A hand-edited colour Qt can't parse would raise and abort the rest of
                        # the view (persistence 35.1): use the automatic palette colour instead.
                        color = series.color if QColor(series.color).isValid() else None
                        self._add_variable_to_plot(plot, series.variable, color)
                        if not series.visible:
                            plot.set_series_visible(series.variable, False)
                    except (ExpressionError, KeyError, NonNumericColumnError):
                        skipped.append(series.variable)
                if plot is not first_plot and not plot.series_names():
                    # Every series meant for this plot failed to apply — drop the empty
                    # plot itself rather than leaving a blank pane in the grid.
                    self.plot_grid.remove_plot(plot)
                    continue
                kept_ratios.append(plot_def.height_ratio)
                kept_plots.append((plot, plot_def))
                y_lo, y_hi = plot_def.y_axis_range
                if y_lo is not None and y_hi is not None:
                    plot.getViewBox().setYRange(y_lo, y_hi, padding=0)

            # Heights last, once the final plot count is known; any None (a view saved
            # before 22.2) leaves the equal split in place. Normalised over the survivors.
            if kept_ratios and len(kept_ratios) == len(self.plot_grid.plots) and None not in kept_ratios:
                self.plot_grid.set_plot_height_ratios(kept_ratios)

            # X-link state (24.5): applied regardless of the independent_x_axis flag. A
            # request to unlink the last linked plot (hand-edited file) is refused by the grid
            # and so stays linked.
            for plot, plot_def in kept_plots:
                if not plot_def.x_linked:
                    self.plot_grid.set_plot_x_linked(plot, False)

            if view.x_axis_range is not None:
                # The linked group shares one range (set_x_range moves the anchor, the rest
                # follow); independent plots keep their own, restored just below.
                self.plot_grid.set_x_range(*view.x_axis_range)
            for plot, plot_def in kept_plots:
                if not plot.x_linked and plot.x_axis_datetime and plot_def.x_axis_range is not None:
                    plot.setXRange(*plot_def.x_axis_range, padding=0)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            # Not expected on the per-item-skipping path above (every ExpressionError/
            # KeyError from a derived variable or series is already caught there); kept
            # as a safety net so an unrelated failure still reports instead of leaving
            # load_view looking like it silently did nothing.
            logger.exception("failed to rebuild view layout: %s", name)
            messages.warning(
                self,
                tr("view.load_error_title"),
                tr("view.partial_restore_failed", name=name, error=e),
            )
        else:
            if skipped:
                logger.info("view %r: skipped variables not present in the open file: %s", name, skipped)
                messages.warning(
                    self,
                    tr("toolbar.load_view"),
                    tr(
                        "view.skipped_variables",
                        name=name,
                        list="\n".join(f"- {v}" for v in skipped),
                    ),
                )
        finally:
            # Whatever ended up on plot 1 (fully applied, partially applied, or
            # untouched) is what the navigator should reflect -- and its region must stay
            # on the restored group range: the overview refresh resets the region to the
            # full extent (same issue _on_grid_layout_changed handles; found via the op log,
            # detailed_specification.md 24.1/30).
            self._on_grid_layout_changed()

    # -- image export ------------------------------------------------------------------

    _EXPORT_FILTERS = (  # (i18n key, default suffix) in QFileDialog filter order
        ("export.filter_png", ".png"),
        ("export.filter_jpeg", ".jpg"),
        ("export.filter_tiff", ".tif"),
        ("export.filter_svg", ".svg"),
        ("export.filter_pdf", ".pdf"),
    )

    def export_image_dialog(self) -> None:
        """Save the plot area as an image (specification.md 5.14, detailed_specification.md 19.3)."""
        if self._data_source is None:
            messages.information(self, tr("toolbar.export_image"), tr("error.open_file_first"))
            return
        if "plot_grid" in self._busy_sources:
            messages.information(self, tr("toolbar.export_image"), tr("export.busy"))
            return
        source = Path(self._data_source.path)
        folder = self.settings.last_export_folder or str(source.parent)
        default = Path(folder) / f"{source.stem}_{datetime.now():%Y%m%d_%H%M%S}.png"
        filters = [tr(key) for key, _ in self._EXPORT_FILTERS]
        path, selected = QFileDialog.getSaveFileName(
            self, tr("toolbar.export_image"), str(default), ";;".join(filters)
        )
        if not path:
            return
        if Path(path).suffix.lower() not in EXPORT_FORMATS:
            suffix = dict(zip(filters, (sfx for _, sfx in self._EXPORT_FILTERS))).get(selected, ".png")
            path += suffix
        self.export_image(path)

    def export_image(self, path: str) -> bool:
        log_op("export_image", path=path, format=Path(path).suffix.lower())
        now = datetime.now()
        lines, metadata = self._image_stamp(now)
        ratios = self.plot_grid.plot_height_ratios()
        snapshots = []
        for plot, ratio in zip(self.plot_grid.plots, ratios):
            snap = plot.export_snapshot()
            snap.height_ratio = ratio  # on-screen height share (24.6)
            if snap.series:
                snapshots.append(snap)
        if not snapshots:
            messages.information(self, tr("toolbar.export_image"), tr("export.nothing_to_export"))
            return False
        size_px = (self.plot_grid.width(), self.plot_grid.height())
        try:
            export_plots_image(snapshots, path, lines, metadata, size_px)
        except Exception as e:  # noqa: BLE001 (matplotlib may raise anything, e.g. TypeError; persistence 35.1: notify, don't crash)
            logger.exception("failed to export image: %s", path)
            messages.warning(self, tr("toolbar.export_image"), tr("export.failed", error=e))
            return False
        self.settings.last_export_folder = str(Path(path).resolve().parent)
        save_settings(self.settings)
        self._show_notice(tr("export.saved", path=path), 10_000)
        return True

    def _image_stamp(
        self, now: datetime, range_override: tuple[float, float] | None = None
    ) -> tuple[list[str], dict[str, str]]:
        """(footer lines, embedded metadata) for export_image — detailed_specification.md 19.3.
        `range_override` (x_lo, x_hi): an analysis window stamps the range it computed, not
        the main window's current one (25.1)."""
        source = Path(self._data_source.path)
        software = f"Parquet Analyzer v{__version__}"
        metadata = {"Title": source.name, "Source": str(source)}
        first = source.name
        if self._view_name:
            first += "  |  " + tr("export.stamp_view", name=self._view_name)
            metadata["View"] = self._view_name
        lines = [first]
        group = self._group_x_range()  # the linked group's range, never an independent plot's (24.6)
        if range_override is not None:
            gplot = group[0] if group is not None else self.plot_grid.anchor_plot()
            group = (gplot, *range_override) if gplot is not None else None
        if group is not None:
            gplot, x_lo, x_hi = group
            start, end = gplot.format_x(x_lo), gplot.format_x(x_hi)
            lines.append(tr("export.stamp_range", start=start, end=end))
            metadata["Range"] = f"{start} - {end}"
        lines.append(tr("export.stamp_exported", when=f"{now:%Y-%m-%d %H:%M:%S}") + "  |  " + software)
        metadata["CreationTime"] = now.isoformat(timespec="seconds")
        metadata["Software"] = software
        return lines, metadata

    # -- settings ----------------------------------------------------------------------

    def open_settings_dialog(self) -> None:
        log_op("open_settings_dialog")
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_settings(dialog.result_settings())

    def apply_settings(self, settings: Settings) -> None:
        log_op(
            "apply_settings",
            auto_redraw=settings.auto_redraw,
            downsample_pixel_ratio=settings.downsample_pixel_ratio,
            log_retention_days=settings.log_retention_days,
            language=settings.language,
            eager_load_limit_mb=settings.eager_load_limit_mb,
            scroll_bindings=settings.scroll_bindings,
            shortcuts=settings.shortcuts,
        )
        self.settings = settings
        self.plot_grid.set_auto_redraw(settings.auto_redraw)
        self.plot_grid.set_downsample_pixel_ratio(settings.downsample_pixel_ratio)
        self.plot_grid.set_scroll_bindings(settings.scroll_bindings)
        hover_action = self._actions.get("hover_values")
        if hover_action is not None:
            hover_action.setChecked(settings.hover_values_enabled)
        self.plot_grid.set_hover_enabled(settings.hover_values_enabled)
        for key, action in self._actions.items():
            seq = settings.shortcuts.get(key, "")
            action.setShortcuts(self._shortcut_sequences(key, seq))
        # The settings dialog's OK is the user's explicit save: tell them if it did not reach
        # the disk (persistence 35.1). Every other save_settings caller stays log-only.
        if not save_settings(self.settings):
            messages.warning(self, tr("toolbar.settings"), tr("settings.save_failed"))

    # -- window layout (dock/toolbar positions, size) -----------------------------------

    def _restore_layout(self) -> None:
        # Must not raise: this runs unconditionally during __init__, so a corrupted
        # window_geometry/window_state (bad base64, or bytes from an incompatible old
        # format) would otherwise crash the app before any window ever appears —
        # exactly the kind of "won't start at all" failure load_settings() is already
        # guarded against for the same reason.
        try:
            if self.settings.window_geometry:
                self.restoreGeometry(base64.b64decode(self.settings.window_geometry))
                if not self._is_visible_on_some_screen():
                    # e.g. saved while on an external monitor that's no longer
                    # connected: the window would restore to a position with no
                    # screen there, making it invisible/unreachable even though the
                    # process is running fine — indistinguishable from "won't start".
                    logger.warning("restored window geometry is off-screen; using the default instead")
                    self.restoreGeometry(self._default_geometry)
                    self.settings.window_geometry = None
            if self.settings.window_state:
                self.restoreState(base64.b64decode(self.settings.window_state))
        except Exception:
            logger.exception("failed to restore saved window layout; using the default")
            self.settings.window_geometry = None
            self.settings.window_state = None

    def _is_visible_on_some_screen(self) -> bool:
        from PySide6.QtGui import QGuiApplication

        frame = self.frameGeometry()
        return any(screen.availableGeometry().intersects(frame) for screen in QGuiApplication.screens())

    def _save_layout(self) -> None:
        self.settings.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.window_state = base64.b64encode(bytes(self.saveState())).decode("ascii")

    def reset_layout(self) -> None:
        """Discard any saved window/dock layout and restore the built-in default
        (variable panel docked on the left) — specification.md 5.11.
        """
        log_op("reset_layout")
        self.restoreGeometry(self._default_geometry)
        self.restoreState(self._default_state)
        self.settings.window_geometry = None
        self.settings.window_state = None
        save_settings(self.settings)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._close_analysis_windows()  # cancels their in-flight jobs (children are not closeEvent'ed)
        oplog.flush_pending()  # the last operation's result must not be lost on exit
        self._save_layout()
        save_settings(self.settings)
        super().closeEvent(event)
