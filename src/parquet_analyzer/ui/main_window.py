from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Mapping

import numpy as np
from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.analysis import basic_stats, fft
from ..core.background_worker import BackgroundWorker
from ..core.column import Column, LazyColumn, LazyVariables, MaterializedColumn, WindowedColumn
from ..core.data_source import ParquetDataSource
from ..core.expression import ExpressionError, evaluate_expression
from ..core.numeric import to_numeric
from ..core.oplog import log_op
from ..core.variable import DerivedVariable, Variable
from ..io import view as view_io
from ..io.settings import FFT_ENABLED, Settings, load_settings, push_recent_folder, save_settings
from .expression_bar import ExpressionBar
from .folder_picker import pick_parquet_file
from .i18n import set_language, tr
from .navigator import NavigatorWidget
from .plot_grid import PlotGridWidget
from .settings_dialog import SettingsDialog
from .stats_dialog import StatsDialog
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


class MainWindow(QMainWindow):
    def __init__(self, debug: bool = False):
        super().__init__()
        self._debug = debug
        self._title_suffix = ""
        # Created here (before _build_layout() adds it to the status bar below) so
        # _set_title() -- called immediately next -- can already update it.
        self._file_label = QLabel("")
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
        self._time_values: np.ndarray | None = None
        self._raw_variables: dict[str, Column] = {}  # _file_columns minus the current time column
        self._derived: dict[str, DerivedVariable] = {}
        self._fft_workers: set[BackgroundWorker] = set()  # strong refs; see BackgroundWorker for why
        self._stats_workers: set[BackgroundWorker] = set()  # strong refs; see BackgroundWorker for why
        # A separate pool from TimePlotWidget's (QThreadPool.globalInstance(), used for
        # pan/zoom downsampling): queuing several stats/FFT jobs — each potentially an
        # O(n log n) sort/transform over millions of rows — must not starve the threads
        # interactive pan/zoom redraws depend on for responsiveness.
        self._analysis_thread_pool = QThreadPool(self)
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

        self._build_layout()
        self._build_actions()
        self._wire_signals()

        # Captured *before* restoring any saved layout, so "reset to default" has
        # something to go back to (specification.md 5.11).
        self._default_geometry = self.saveGeometry()
        self._default_state = self.saveState()
        self._restore_layout()

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
        if FFT_ENABLED:  # hidden from the GUI while still under debugging; see io/settings.py
            self._add_action("fft", tr("toolbar.fft"), toolbar, self._on_fft_requested)
        self._add_action("stats", tr("toolbar.stats"), toolbar, self._on_stats_requested)
        toolbar.addSeparator()
        self._add_action("save_view", tr("toolbar.save_view"), toolbar, self.save_view_dialog)
        self._add_action("load_view", tr("toolbar.load_view"), toolbar, self.load_view_dialog)
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
        toolbar.addSeparator()

        settings_action = QAction(tr("toolbar.settings"), self)
        settings_action.triggered.connect(self.open_settings_dialog)
        toolbar.addAction(settings_action)

        reset_layout_action = QAction(tr("toolbar.reset_layout"), self)
        reset_layout_action.triggered.connect(self.reset_layout)
        toolbar.addAction(reset_layout_action)

    def _wire_signals(self) -> None:
        self.variable_panel.variableDoubleClicked.connect(self.expression_bar.insert_variable_name)
        self.plot_grid.rangeChanged.connect(lambda r: self.navigator.set_selected_range(*r))
        self.navigator.rangeSelected.connect(lambda lo, hi: self.plot_grid.set_x_range(lo, hi))
        self.plot_grid.plotAdded.connect(self._wire_plot)
        self.plot_grid.busyChanged.connect(lambda busy: self._set_busy("plot_grid", busy))
        self.plot_grid.layoutChanged.connect(self._refresh_navigator_overview)
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

    # -- data loading -------------------------------------------------------------

    def open_parquet_dialog(self) -> None:
        path = pick_parquet_file(self, self.settings.last_data_folder, self.settings.recent_data_folders)
        log_op("open_parquet_dialog", chosen=path)
        if path:
            self.load_parquet(path)

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
            raw_time = data_source.read_columns([time_column])[time_column]
            file_columns: dict[str, Column] = {time_column: MaterializedColumn(to_numeric(raw_time))}
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
            row_count = data_source.row_count()
            over_threshold = row_count * 8 > self.settings.eager_load_limit_mb * 1024 * 1024
            for name in columns:
                if name == time_column:
                    continue
                if over_threshold:
                    file_columns[name] = WindowedColumn(data_source, name)
                else:
                    file_columns[name] = LazyColumn(data_source, name)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            logger.exception("failed to load parquet file: %s", path)
            QMessageBox.critical(self, tr("error.load_failed_title"), tr("error.load_failed_body", error=e))
            return False

        self._data_source = data_source
        self._file_columns = file_columns
        self._time_column = time_column
        self._time_values = file_columns[time_column].values()  # already materialized above; free
        self._raw_variables = {k: v for k, v in file_columns.items() if k != time_column}
        self._derived = {}
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
        return True

    def _clear_plot_grid(self) -> None:
        while len(self.plot_grid.plots) > 1:
            self.plot_grid.remove_plot(self.plot_grid.plots[-1])
        for series_name in self.plot_grid.plots[0].series_names():
            self.plot_grid.plots[0].remove_series(series_name)
        self.plot_grid.clear_cursors()

    def _refresh_navigator_overview(self) -> None:
        """The navigator shows an example waveform just for overall shape/scale, not
        any specific analysis — prefer the anchor (topmost time-domain) plot's first
        series (what the user is actually looking at) over an arbitrary file column,
        falling back to the file's first raw variable when nothing has been plotted at
        all yet. Uses plot_grid.anchor_plot(), not plots[0]: a frequency-domain (FFT)
        plot's series are in Hz, not aligned with self._time_values at all.
        """
        if self._time_values is None:
            return
        first_plot = self.plot_grid.anchor_plot()
        series_names = first_plot.series_names() if first_plot is not None else []
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
                x, y = self._windowed_navigator_overview(y_source)
                self.navigator.set_overview_data(x, y)
                return
        elif self._raw_variables:
            column = next(iter(self._raw_variables.values()))
            if isinstance(column, WindowedColumn):
                # Coarse row-group min/max envelope from footer statistics (Phase
                # B), not the flat single (min, max) column_bounds() would give —
                # keeps real shape (e.g. this test data's daily on/off bursts)
                # visible without materializing this WindowedColumn's whole self
                # just to preview it (detailed_specification.md 13.5.1 Phase D).
                x, y = self._windowed_navigator_overview(column)
                self.navigator.set_overview_data(x, y)
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
        self.navigator.set_overview_data(self._time_values, y)

    def _windowed_navigator_overview(self, column: WindowedColumn) -> tuple[np.ndarray, np.ndarray]:
        """A coarse per-row-group (min, max) envelope for `column`, built purely
        from Parquet footer statistics (no data I/O) — real shape, not just a
        flat line, at essentially zero cost. x comes from the already-resident
        time column at each row group's starting row (never re-read).
        """
        stats = self._data_source.column_stats(column.name)
        xs: list[float] = []
        ys: list[float] = []
        for (row_offset, _row_count), (mn, mx, _null_count) in zip(self._data_source.row_groups(), stats):
            if mn is None or row_offset >= len(self._time_values):
                continue
            t = self._time_values[row_offset]
            xs.append(t)
            ys.append(mn)
            xs.append(t)
            ys.append(mx)
        return np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64)

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
        self._time_column = name
        # Materializes `name` if it was still a LazyColumn (e.g. never plotted) —
        # unavoidable, the time axis needs real data now for searchsorted/navigator.
        self._time_values = self._file_columns[name].values()
        derived_cache = {n: self._raw_variables[n] for n in self._derived if n in self._raw_variables}
        self._raw_variables = {k: v for k, v in self._file_columns.items() if k != name}
        self._raw_variables.update(derived_cache)

        self.variable_panel.set_variables(list(self._raw_variables.keys()))
        self._refresh_navigator_overview()
        self.plot_grid.set_time_axis_data(self._time_values)

        if self.time_axis_combo.currentText() != name:
            self.time_axis_combo.blockSignals(True)
            self.time_axis_combo.setCurrentText(name)
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
        return evaluate_expression(derived.expression, self._all_arrays())

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
            arrays[name] = evaluate_expression(self._derived[name].expression, arrays)
        return arrays

    def _on_add_derived_variable(self, name: str, expression: str) -> None:
        log_op("add_derived_variable", name=name, expression=expression)
        if self._time_values is None:
            raise ExpressionError(tr("error.open_file_first"))
        if name in self._raw_variables and name not in self._derived:
            raise ExpressionError(tr("error.duplicate_variable_name", name=name))
        # Evaluate eagerly (via _all_arrays()'s lazy mapping, so this only
        # materializes the specific source columns the expression references, not
        # every loaded column) so a typo surfaces immediately instead of at plot time.
        # Note: if a referenced source is a WindowedColumn (Phase D, over the size
        # threshold), LazyVariables.__getitem__ calls its values() -> a full read
        # of that whole column, same cost as the old eager-load-everything path —
        # deriving a variable isn't itself windowed in this pass (deferred; see
        # detailed_specification.md 13.5.1 Phase D's scope note on required_halo).
        value = evaluate_expression(expression, self._all_arrays())
        self._derived[name] = DerivedVariable(name=name, expression=expression)
        self._raw_variables[name] = MaterializedColumn(np.asarray(value, dtype=np.float64))
        self.variable_panel.add_variable(name)

    def _on_variable_dropped(self, plot, name: str) -> None:
        log_op("overlay_variable", plot=plot.plot_id, variable=name)
        if self._time_values is None:
            return
        self._add_variable_to_plot(plot, name)
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
        if isinstance(column, WindowedColumn):
            plot.add_windowed_series(name, self._time_values, column, column.bounds(), color)
        else:
            plot.add_series(name, self._time_values, self.variable_values(name), color)

    def _visible_row_range(self) -> tuple[int, int]:
        """Row range of self._time_values covered by the current shared time-domain
        view range ("currently visible" = the shared X range of the time-domain plots;
        plot_grid.py X-links them all together, and a frequency-domain (FFT) plot's own
        range must be ignored here). Falls back to the full range if no time-domain
        plot has anything plotted yet — an empty plot's viewRange() is pyqtgraph's
        arbitrary default (not fitted to any data), not the real data extent.
        """
        time_plots = [p for p in self.plot_grid.plots if p.x_axis_datetime and not p.is_empty()]
        if not time_plots:
            return 0, len(self._time_values)
        x_lo, x_hi = time_plots[0].getViewBox().viewRange()[0]
        row_start = max(0, int(np.searchsorted(self._time_values, x_lo, side="left")))
        row_end = min(len(self._time_values), int(np.searchsorted(self._time_values, x_hi, side="right")))
        return row_start, row_end

    def _on_fft_requested(self) -> None:
        selected = self.variable_panel.selectedItems()
        if not selected or self._time_values is None:
            QMessageBox.information(self, tr("toolbar.fft"), tr("fft.select_variable"))
            return
        name = selected[0].text()

        # FFT is scoped to the currently visible time range, not the whole file
        # (see _FFT_MAX_ROWS above for why).
        row_start, row_end = self._visible_row_range()

        if row_end - row_start > _FFT_MAX_ROWS:
            QMessageBox.information(
                self,
                tr("toolbar.fft"),
                tr("fft.range_too_wide", rows=row_end - row_start, max_rows=_FFT_MAX_ROWS),
            )
            return

        log_op("fft", variable=name, row_start=row_start, row_end=row_end)
        x = self._time_values[row_start:row_end]
        y = self.variable_window(name, row_start, row_end)
        label = f"{name} (FFT: {x[0]:.3f}–{x[-1]:.3f}s)" if len(x) else f"{name} (FFT)"

        worker = BackgroundWorker(lambda: fft(x, y))
        self._fft_workers.add(worker)  # strong ref until finished is handled; see BackgroundWorker
        worker.signals.finished.connect(
            lambda result, lbl=label, w=worker: self._on_fft_finished(lbl, *result, w)
        )
        self._set_busy("fft", True)
        self._analysis_thread_pool.start(worker)

    def _on_fft_finished(self, label: str, freqs: np.ndarray, amps: np.ndarray, worker: BackgroundWorker) -> None:
        self._fft_workers.discard(worker)
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
        for plot in self.plot_grid.plots:
            if not plot.x_axis_datetime:
                continue
            for name in plot.series_names():
                seen.setdefault(name, None)
        return list(seen)

    def _on_stats_requested(self) -> None:
        selected = self.variable_panel.selectedItems()
        if selected:
            candidate_names = [item.text() for item in selected]
        else:
            # Nothing explicitly selected in the variable panel -- default to
            # whichever variables are currently plotted, so a variable already
            # overlaid on a plot doesn't also need re-selecting in the panel just to
            # see its stats. Explicit panel selection still takes priority when
            # there is one (e.g. to check a variable that isn't plotted at all).
            candidate_names = self._plotted_variable_names()
        if not candidate_names or self._time_values is None:
            QMessageBox.information(self, tr("toolbar.stats"), tr("stats.select_variable"))
            return

        # Unlike FFT, there's no row cap here: mean/min/max/std/median stay meaningful
        # (and cheap — O(n), one pass) regardless of sampling gaps, and the user asked
        # specifically for "all the data" in the visible range, not a downsampled or
        # truncated view of it.
        row_start, row_end = self._visible_row_range()
        x = self._time_values[row_start:row_end]
        range_label = (
            tr("stats.range_with_count", start=x[0], end=x[-1], count=row_end - row_start)
            if len(x)
            else tr("stats.empty_range")
        )
        log_op("stats", variables=candidate_names, row_start=row_start, row_end=row_end)

        # Resolve every candidate up front rather than inside the per-worker loop
        # below: a name coming from _plotted_variable_names() (unlike a panel
        # selection, which is always a currently-known variable) could in principle
        # no longer resolve to one, and skipping it here keeps `pending`'s
        # name/result bookkeeping consistent from the start instead of needing to
        # patch it up mid-flight.
        resolved: dict[str, np.ndarray] = {}
        for name in candidate_names:
            try:
                resolved[name] = self.variable_window(name, row_start, row_end)
            except (ExpressionError, KeyError):
                logger.info("stats: skipping unresolved variable %r", name)
        if not resolved:
            QMessageBox.information(self, tr("toolbar.stats"), tr("stats.cannot_compute"))
            return

        names = list(resolved.keys())
        pending = {"range_label": range_label, "names": names, "results": {}, "remaining": len(names)}
        for name, y in resolved.items():
            worker = BackgroundWorker(lambda y=y: basic_stats(y))
            self._stats_workers.add(worker)  # strong ref until finished is handled; see BackgroundWorker
            worker.signals.finished.connect(
                lambda stats, n=name, w=worker, batch=pending: self._on_stats_finished(n, stats, w, batch)
            )
            self._set_busy("stats", True)
            self._analysis_thread_pool.start(worker)

    def _on_stats_finished(self, name: str, stats: dict, worker: BackgroundWorker, batch: dict) -> None:
        self._stats_workers.discard(worker)
        self._set_busy("stats", bool(self._stats_workers))
        batch["results"][name] = stats
        batch["remaining"] -= 1
        if batch["remaining"] > 0:
            return  # other variables in this same request are still computing
        # Preserve the order the user selected the variables in, not completion order.
        ordered_results = {name: batch["results"][name] for name in batch["names"]}
        dialog = StatsDialog(batch["range_label"], ordered_results, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    # -- views -----------------------------------------------------------------------

    def save_view_dialog(self) -> None:
        if self._data_source is None:
            QMessageBox.information(self, tr("toolbar.save_view"), tr("error.open_file_first"))
            return
        name, ok = QInputDialog.getText(self, tr("toolbar.save_view"), tr("view.name_label"))
        if not ok or not name.strip():
            return

        plots = []
        x_axis_range = None
        for plot in self.plot_grid.plots:
            series = []
            for series_name in plot.series_names():
                _, _, color = plot.series_data(series_name)
                series.append(view_io.SeriesDef(variable=series_name, color=color))
            if not series:
                continue
            x_range, y_range = plot.getViewBox().viewRange()
            plots.append(
                view_io.PlotDef(plot_id=plot.plot_id, series=series, y_axis_range=(y_range[0], y_range[1]))
            )
            if x_axis_range is None and plot.x_axis_datetime:
                # All time-domain plots are X-linked together, so one representative
                # range covers all of them; a frequency-domain (FFT) plot's X range
                # isn't time and wouldn't mean anything here.
                x_axis_range = (x_range[0], x_range[1])

        view = view_io.View(
            view_name=name.strip(),
            source=view_io.SourceDef(parquet_path=str(self._data_source.path), path_type="absolute"),
            plots=plots,
            x_axis_range=x_axis_range,
            derived_variables=[
                view_io.DerivedVariableDef(name=d.name, expression=d.expression)
                for d in self._derived.values()
            ],
            downsample_enabled=self._downsample_enabled,
        )
        try:
            path = view_io.save_view(view)
        except ValueError as e:
            QMessageBox.warning(self, tr("toolbar.save_view"), tr("view.invalid_name", error=e))
            return
        log_op("save_view", name=view.view_name, path=str(path))
        QMessageBox.information(self, tr("toolbar.save_view"), tr("view.saved", path=path))

    def load_view_dialog(self) -> None:
        names = view_io.list_views()
        if not names:
            QMessageBox.information(self, tr("toolbar.load_view"), tr("view.none_saved"))
            return
        name, ok = QInputDialog.getItem(self, tr("toolbar.load_view"), tr("view.select_label"), names, editable=False)
        if not ok:
            return
        self.load_view(name)

    def load_view(self, name: str) -> None:
        log_op("load_view", name=name)
        try:
            view = view_io.load_view(name)
            source_path = view_io.resolve_source_path(view)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            logger.exception("failed to load view: %s", name)
            QMessageBox.critical(self, tr("view.load_error_title"), tr("view.load_failed_body", error=e))
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
            self.variable_panel.set_variables(list(self._raw_variables.keys()))
        elif not self.load_parquet(str(source_path)):
            return  # load_parquet already showed an error dialog

        # Each derived variable / series is applied independently below and a failure
        # (e.g. its source column doesn't exist in whichever file is actually open —
        # expected and common on the "reuse a layout across files that share *some*
        # column names" path above, not just when the recorded source file itself was
        # regenerated) only skips that one item, rather than aborting every subsequent
        # derived variable/series/plot too. `skipped` collects what didn't make it, for
        # one consolidated warning at the end instead of per-item dialogs.
        skipped: list[str] = []
        try:
            # Applied before plotting any series, so their first draw already respects
            # the loaded setting instead of drawing once and immediately redrawing.
            self._apply_downsample_enabled(view.downsample_enabled, update_action=True)

            for derived in view.derived_variables:
                try:
                    self._on_add_derived_variable(derived.name, derived.expression)
                except (ExpressionError, KeyError):
                    skipped.append(derived.name)

            # The grid was already cleared above (either branch) down to a single plot,
            # which is guaranteed time-domain: PlotGridWidget.remove_plot() refuses to
            # remove the last time-domain plot, and move_plot_up/down never let an FFT
            # plot cross in front of one, so plots[0] can't end up being an FFT plot.
            first_plot = self.plot_grid.plots[0]
            for i, plot_def in enumerate(view.plots):
                plot = first_plot if i == 0 else self.plot_grid.add_plot()
                for series in plot_def.series:
                    try:
                        self._add_variable_to_plot(plot, series.variable, series.color)
                    except (ExpressionError, KeyError):
                        skipped.append(series.variable)
                if plot is not first_plot and not plot.series_names():
                    # Every series meant for this plot failed to apply — drop the empty
                    # plot itself rather than leaving a blank pane in the grid.
                    self.plot_grid.remove_plot(plot)
                    continue
                y_lo, y_hi = plot_def.y_axis_range
                if y_lo is not None and y_hi is not None:
                    plot.getViewBox().setYRange(y_lo, y_hi, padding=0)

            if view.x_axis_range is not None:
                # Plots are X-linked together, so restoring it on one restores it for
                # all of them.
                self.plot_grid.set_x_range(*view.x_axis_range)
        except Exception as e:  # noqa: BLE001 (surfaced to the user, not swallowed)
            # Not expected on the per-item-skipping path above (every ExpressionError/
            # KeyError from a derived variable or series is already caught there); kept
            # as a safety net so an unrelated failure still reports instead of leaving
            # load_view looking like it silently did nothing.
            logger.exception("failed to rebuild view layout: %s", name)
            QMessageBox.warning(
                self,
                tr("view.load_error_title"),
                tr("view.partial_restore_failed", name=name, error=e),
            )
        else:
            if skipped:
                logger.info("view %r: skipped variables not present in the open file: %s", name, skipped)
                QMessageBox.warning(
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
            # untouched) is what the navigator should reflect.
            self._refresh_navigator_overview()

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
            scroll_bindings=settings.scroll_bindings,
            shortcuts=settings.shortcuts,
        )
        self.settings = settings
        self.plot_grid.set_auto_redraw(settings.auto_redraw)
        self.plot_grid.set_downsample_pixel_ratio(settings.downsample_pixel_ratio)
        self.plot_grid.set_scroll_bindings(settings.scroll_bindings)
        for key, action in self._actions.items():
            seq = settings.shortcuts.get(key, "")
            action.setShortcut(QKeySequence(seq) if seq else QKeySequence())
        save_settings(self.settings)

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
        self._save_layout()
        save_settings(self.settings)
        super().closeEvent(event)
