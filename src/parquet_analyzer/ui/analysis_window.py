"""Shared frame for analysis windows (detailed_specification.md 25.1; analysis.ja.md 25.1).

A non-modal window opened from the main window that analyses the *currently visible*
(X-linked group) time range: settings panel on top, a display area in the middle, a status
line at the bottom, and a toolbar with "Recompute", "Follow main range" (default off) and
"Save image". Subclasses (the XY plot now, a histogram later) supply the settings panel,
the display, the computation and the figure builder.

Computation runs as a `WorkerHub` job (main window's `_analysis_hub`, detailed_specification.md 37.1) with a generation counter (stale results are
dropped) and strong references to in-flight workers (overview.ja.md 2: both `finished` and
`failed` must clean up). Closing the window with a job in flight is safe: the worker is
kept alive by a module-level set until it signals, and the callbacks ignore a closed window.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QVBoxLayout, QWidget

from ..core.oplog import log_op
from ..io.settings import save_settings
from . import messages
from .i18n import tr
from .image_export import EXPORT_FORMATS, save_figure

logger = logging.getLogger(__name__)

FOLLOW_DEBOUNCE_MS = 300  # coalesce a burst of main-range changes before recomputing (25.1)

# Returned by a job that noticed it was cancelled (window closed / superseded); the window
# ignores it silently (no failure message, no error log).
CANCELLED = object()


class AnalysisWindow(QMainWindow):
    """Base class; see module docstring. `main` is the owning MainWindow."""

    def __init__(self, main, title: str):
        super().__init__(main)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(title)
        self.resize(900, 700)
        self._main = main
        self._closed = False
        self._generation = 0
        self._workers: set[int] = set()  # tokens (in main._analysis_hub.pending) of this window's in-flight jobs
        self._shown_range: tuple[float, float] | None = None  # (x_lo, x_hi) of the result on screen
        self._range: tuple[int, int, float, float] | None = None  # (row_start, row_end, x_lo, x_hi) last used

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        self._settings_panel = self.build_settings_panel()
        layout.addWidget(self._settings_panel)
        layout.addWidget(self.build_view(), 1)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self.setCentralWidget(central)

        toolbar = self.addToolBar("analysis")
        toolbar.setObjectName("analysis_toolbar")
        self.recompute_action = QAction(tr("analysis.recompute"), self)
        self.recompute_action.triggered.connect(lambda *_: self.recompute())
        toolbar.addAction(self.recompute_action)
        self.follow_action = QAction(tr("analysis.follow"), self)
        self.follow_action.setCheckable(True)
        self.follow_action.setToolTip(tr("analysis.follow_tooltip"))
        self.follow_action.toggled.connect(self._on_follow_toggled)
        toolbar.addAction(self.follow_action)
        self.export_action = QAction(tr("toolbar.export_image"), self)
        self.export_action.triggered.connect(lambda *_: self.export_image_dialog())
        toolbar.addAction(self.export_action)

        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(FOLLOW_DEBOUNCE_MS)
        self._follow_timer.timeout.connect(self.recompute)
        main.plot_grid.rangeChanged.connect(self._on_main_range_changed)

    # -- hooks for subclasses ------------------------------------------------------

    def build_settings_panel(self) -> QWidget:
        raise NotImplementedError

    def build_view(self) -> QWidget:
        raise NotImplementedError

    def make_job(self, row_start: int, row_end: int, cancelled: Callable[[], bool]) -> Callable[[], object]:
        """A zero-arg function run on the worker thread (called on the UI thread). It must
        resolve everything it needs from MainWindow *here* and never touch MainWindow/Qt
        from the worker; it should poll `cancelled()` between chunks and return CANCELLED."""
        raise NotImplementedError

    def on_result(self, result: object) -> None:
        raise NotImplementedError

    def on_failure(self, message: str) -> None:
        self.set_status(tr("analysis.failed", message=message))

    def build_export_figure(self, stamp_lines: list[str], size_px: tuple[int, int]):
        raise NotImplementedError

    def export_stamp_extra(self) -> str | None:
        """An extra stamp line added before the "exported at" line (25.1)."""
        return None

    # -- status --------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def status_text(self) -> str:
        return self._status.text()

    def is_computing(self) -> bool:
        return bool(self._workers)

    # -- range / recompute ---------------------------------------------------------

    def _on_follow_toggled(self, checked: bool) -> None:
        log_op("analysis_follow", enabled=checked, window=type(self).__name__)
        if checked:
            self.recompute()
        else:
            self._follow_timer.stop()

    def _on_main_range_changed(self, _range) -> None:
        if self._closed or not self.follow_action.isChecked():
            return
        self._follow_timer.start()  # restarts the debounce

    def recompute(self, *, keep_range: bool = False) -> None:
        """Re-read the main window's current range (or, `keep_range`, reuse the last one --
        used when only a setting changed) and recompute in the background."""
        if self._closed:
            return
        if self._main._time_values is None:
            return
        if not keep_range or self._range is None:
            self._range = self._main.analysis_range()
        row_start, row_end = self._range[0], self._range[1]
        rng = self._range
        self._generation += 1
        generation = self._generation
        try:
            job = self.make_job(row_start, row_end, lambda: self._closed or self._generation != generation)
        except ValueError as exc:  # incomplete settings: just say so in the status line
            self.set_status(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - shown in the window (28)
            logger.exception("analysis job setup failed")
            self.on_failure(f"{type(exc).__name__}: {exc}")
            return
        self.set_status(tr("analysis.computing"))
        # The hub (owned by the main window, so it outlives this window) holds the callbacks
        # until the result arrives, even if this window is closed meanwhile; the callbacks
        # check `_closed`. The token is shared with them through a one-element box.
        box: list[int] = []
        token = self._main._analysis_hub.submit(
            job,
            lambda res, g=generation, r=rng: self._on_finished(res, box[0], g, r),
            lambda msg, g=generation: self._on_failed(msg, box[0], g),
            job="analysis",
            target=type(self).__name__,
        )
        box.append(token)
        self._workers.add(token)

    def _on_finished(self, result, key: int, generation: int, rng=None) -> None:
        if self._closed:
            return
        self._workers.discard(key)
        if generation != self._generation or result is CANCELLED:
            return  # superseded or cancelled
        if rng is not None and rng[2] == rng[2] and rng[3] == rng[3]:  # not NaN
            self._shown_range = (rng[2], rng[3])
        else:
            self._shown_range = None
        self.on_result(result)

    def _on_failed(self, message: str, key: int, generation: int) -> None:
        if self._closed:
            return
        self._workers.discard(key)
        if generation != self._generation:
            return
        self.on_failure(message)

    # -- image export (19.2 reuse) -------------------------------------------------

    def export_image_dialog(self) -> None:
        main = self._main
        if main._data_source is None:
            return
        source = Path(main._data_source.path)
        folder = main.settings.last_export_folder or str(source.parent)
        default = Path(folder) / f"{source.stem}_{type(self).__name__}_{datetime.now():%Y%m%d_%H%M%S}.png"
        filters = [tr(key) for key, _ in main._EXPORT_FILTERS]
        path, selected = QFileDialog.getSaveFileName(
            self, tr("toolbar.export_image"), str(default), ";;".join(filters)
        )
        if not path:
            return
        if Path(path).suffix.lower() not in EXPORT_FORMATS:
            suffix = dict(zip(filters, (sfx for _, sfx in main._EXPORT_FILTERS))).get(selected, ".png")
            path += suffix
        self.export_image(path)

    def export_image(self, path: str) -> bool:
        main = self._main
        log_op("analysis_export_image", path=path, window=type(self).__name__)
        lines, metadata = main._image_stamp(datetime.now(), range_override=self._shown_range)
        extra = self.export_stamp_extra()
        if extra:
            lines.insert(max(len(lines) - 1, 0), extra)
            metadata["Variables"] = extra
        size_px = (self.width(), self.height())
        try:
            fig = self.build_export_figure(lines, size_px)
            if fig is None:
                messages.information(self, tr("toolbar.export_image"), tr("export.nothing_to_export"))
                return False
            save_figure(fig, path, metadata)
        except (OSError, ValueError) as e:
            logger.exception("failed to export analysis image: %s", path)
            messages.warning(self, tr("toolbar.export_image"), tr("export.failed", error=e))
            return False
        main.settings.last_export_folder = str(Path(path).resolve().parent)
        save_settings(main.settings)
        main._show_notice(tr("export.saved", path=path), 10_000)
        return True

    # -- lifecycle -----------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._closed = True
        self._generation += 1  # any in-flight result is now stale
        self._follow_timer.stop()
        try:
            self._main.plot_grid.rangeChanged.disconnect(self._on_main_range_changed)
        except (RuntimeError, TypeError):
            pass
        self.on_close()
        self._main._analysis_window_closed(self)
        super().closeEvent(event)

    def on_close(self) -> None:
        """Subclass hook: disconnect from main-window signals."""
