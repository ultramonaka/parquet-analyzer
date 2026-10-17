"""Runs the Parquet export queue for the main window (export.ja.md 57.9.7, 57.9.8).

`ExportController` owns the pieces that belong together: the `ExportQueue` (core, no Qt), the export
thread pool (at most `Settings.export_parallel_files` threads) and its `WorkerHub`, the 250 ms
redraw timer, the status-bar summary and the modeless list (`ParquetExportPanel`). One file is one
`WorkerHub` job that reports success, failure or cancellation exactly once; the controller starts the
next waiting file each time one is over. Everything here runs on the UI thread; workers only compute,
write, and send throttled progress numbers through the hub's relay.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from ..core import export_resources as res
from ..core import oplog
from ..core import parquet_export as pe
from ..core.background_worker import WorkerHub
from ..core.export_queue import (
    STAGE_FULL,
    STAGE_RANGE,
    ExportItem,
    ExportQueue,
    ItemState,
    memory_budget,
    memory_over_physical,
)
from ..core.oplog import log_op
from ..io.settings import EXPORT_PARALLEL_FILES_RANGE, save_settings
from . import messages
from .i18n import tr
from .parquet_export_dialog import PanelSummary, ParquetExportPanel, ParquetExportProgress

logger = logging.getLogger(__name__)

REFRESH_MS = 250  # 57.9.7: the status bar and the list are redrawn at most this often
RATE_WINDOW = 5.0  # seconds of history for the rows-per-second moving average


class _NoRows:
    """The worker's result when the saved time range holds no rows (57.7.4)."""


NO_ROWS = _NoRows()


def _gb_text(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


class ExportController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.queue = ExportQueue()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(self.parallel())
        self.hub = WorkerHub(self.pool)
        self.status = ParquetExportProgress(self.cancel_all_clicked, self.show_panel)
        self.panel: ParquetExportPanel | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._tick)
        self.arbiter = res.DiskArbiter()  # which running file stops when the disk runs short (57.9.6a (c))
        self.refreshes = 0  # how many times the display was redrawn (tests: it is throttled)
        self.closing = False
        self.warnings: list[str] = []  # preflight warnings of the current batch (57.9.5, 57.9.6)
        self._new_warnings: list[str] = []  # from the last `confirm_resources`, joined in by `submit`
        self._unreported: list[ExportItem] = []  # finished items the end-of-batch notice has not covered
        self._batch_start = time.monotonic()
        self.open_folder: Callable[[str], None] = self._open_folder

    # -- settings and views of the queue -----------------------------------------------------

    def parallel(self) -> int:
        """How many files run at once (57.9.2): the setting, clamped to 1-4."""
        lo, hi = EXPORT_PARALLEL_FILES_RANGE
        value = getattr(self.win.settings, "export_parallel_files", 2)
        return max(lo, min(hi, value if isinstance(value, int) else 2))

    @property
    def running_item(self) -> ExportItem | None:
        """The first file being written (None when none runs)."""
        running = self.queue.in_state(ItemState.RUNNING)
        return running[0] if running else None

    def busy(self) -> bool:
        """Anything waiting, running, or still removing its `.part` after a cancel."""
        return not self.queue.is_idle()

    def new_item(
        self,
        source: str,
        output: str,
        plan: pe.ExportPlan | None,
        *,
        overwrite: bool = False,
        view: str | None = None,
        missing: tuple[str, ...] = (),
        error: str = "",
    ) -> ExportItem:
        item = ExportItem(source=str(source), output=str(output), plan=plan, overwrite=overwrite, view=view, missing=missing)
        item.error = error
        if plan is not None:
            item.memory = pe.estimate_export_memory(plan)
            item.max_bytes = pe.estimate_output_bytes(plan)
        return item

    # -- adding work -------------------------------------------------------------------------

    def confirm_resources(self, items: list[ExportItem]) -> bool:
        """The check before files are added (57.9.6a): the new files together with everything still
        waiting or running must fit the free disk space (upper bound) and each must fit the free memory;
        if not, ask (default: do not export). Files over the memory budget are only noted."""
        planned = [i for i in items if i.plan is not None]
        self._new_warnings = []
        report = res.preflight(
            [res.PreflightItem(i.output, i.max_bytes, i.memory) for i in self.queue.active() if i.plan is not None]
            + [res.PreflightItem(i.output, i.max_bytes, i.memory) for i in planned]
        )
        budget = memory_budget()
        for i in planned:
            if i.memory > budget:
                i.note = tr("export_parquet.memory_heavy", size=_gb_text(i.memory))
        for i in planned:
            if memory_over_physical(i):
                self._new_warnings.append(tr("export_parquet.memory_over_physical", name=Path(i.output).name, size=_gb_text(i.memory)))
        if report.ok:
            return True
        lines: list[str] = []
        for folder, need, free in report.disk_short:
            lines.append(tr("export_parquet.preflight_disk", need=_gb_text(need), free=_gb_text(free), folder=folder))
        for _p, need, free in report.memory_short:
            lines.append(tr("export_parquet.preflight_memory", need=_gb_text(need), free=_gb_text(free)))
        log_op(
            "export_parquet_preflight", path=planned[0].output if planned else "", disk_short=len(report.disk_short),
            memory_short=len(report.memory_short),
        )
        if not messages.confirm_export_start(
            self.win, tr("toolbar.export_parquet"), "\n\n".join(lines),
            tr("export_parquet.preflight_start"), tr("export_parquet.preflight_stop"),
        ):
            return False
        self._new_warnings.extend(lines)
        return True

    def submit(self, items: list[ExportItem]) -> None:
        """Append `items` to the queue (also while others run, 57.9.2 no. 4) and start what may start.
        Items without a plan failed before they could be planned (`item.error`); they only show up in
        the list and in the final notice."""
        if self.closing:
            return
        if self.queue.is_idle():  # a new batch: the finished rows of the last one are history now
            self.queue.clear_finished()
            self.queue.new_batch()
            self._unreported.clear()
            self.warnings = []
            self._batch_start = time.monotonic()
        self.warnings.extend(self._new_warnings)
        self._new_warnings = []
        for item in items:
            self.queue.add(item)
            if item.plan is None:
                self.queue.fail(item, item.error)
                self._unreported.append(item)
        self._pump()
        if len(self.queue.items) >= 2:  # a batch opens the list by itself; one file does not (57.9.7)
            self.show_panel()
        if self.queue.active():
            self.timer.start()
        self.refresh()
        self._report_if_idle()

    # -- starting ----------------------------------------------------------------------------

    def _pump(self) -> None:
        if self.closing:
            return
        parallel = self.parallel()
        self.pool.setMaxThreadCount(parallel)
        waiting = self.queue.in_state(ItemState.WAITING)
        while True:
            item = self.queue.next_to_start(parallel)
            if item is None:
                break
            self._launch(item)
        # items the queue failed itself (a full disk kept failing the files before them) are news too
        self._unreported.extend(i for i in waiting if i.state is ItemState.FAILED)

    def _launch(self, item: ExportItem) -> None:
        self.queue.mark_running(item)
        plan = item.plan
        assert plan is not None
        item.progress_key, emit = self.hub.progress_sink(lambda done, total: self._on_progress(item, done, total))

        def run() -> pe.ExportResult | None | _NoRows:
            try:
                return pe.export_parquet(
                    plan, item.output, emit, item.cancel_event.is_set, item.overwrite,
                    arbiter=self.arbiter, arbiter_key=item.id,
                )
            except pe.ExportCancelled:
                return None
            except pe.NoRowsInRange:  # an expected outcome of a saved range (57.7.4), not an error
                return NO_ROWS
            finally:
                item.worker_done.set()

        item.token = self.hub.submit(
            run,
            lambda result: self._on_finished(item, result),
            lambda error: self._on_failed(item, error),
            job="parquet_export",
            target=item.output,
        )

    # -- worker -> UI thread -----------------------------------------------------------------

    def _on_progress(self, item: ExportItem, done: int, total: int) -> None:
        """Only stores the numbers (57.9.7); the timer draws them."""
        if item.state is ItemState.RUNNING:
            self.queue.set_progress(item, done, total)

    def _end_progress(self, item: ExportItem) -> None:
        self.hub.drop_progress(item.progress_key)

    def _on_finished(self, item: ExportItem, result: pe.ExportResult | None | _NoRows) -> None:
        self._end_progress(item)
        if item.state not in (ItemState.RUNNING, ItemState.CANCELLING):
            return
        if result is NO_ROWS:
            self.queue.finish(item, ItemState.FAILED, error=tr("export_parquet.no_rows"))
        elif result is None:
            self.queue.finish(item, ItemState.CANCELLED)
        else:
            self.queue.finish(item, ItemState.DONE, result=result)
            oplog.record_message(  # 57.9.3: which writer was used and what the memory guard did
                "info", "export_parquet",
                f"writer={result.writer} rows={result.rows} chunk_halvings={result.chunk_halvings} "
                f"memory_wait={result.memory_wait:.1f}s",
            )
            self.win.settings.last_export_folder = str(Path(result.path).resolve().parent)
            save_settings(self.win.settings)
        self._after_item(item)

    def _on_failed(self, item: ExportItem, error: str) -> None:
        self._end_progress(item)
        if item.state not in (ItemState.RUNNING, ItemState.CANCELLING):
            return
        if item.state is ItemState.CANCELLING:  # the user stopped it: an error while stopping is not news
            self.queue.finish(item, ItemState.CANCELLED)
        else:
            self.queue.finish(item, ItemState.FAILED, error=error)
        self._after_item(item)

    def _after_item(self, item: ExportItem) -> None:
        self._unreported.append(item)
        self._pump()
        self.refresh()
        if self.queue.is_idle():
            self.timer.stop()
        self._report_if_idle()

    # -- cancelling --------------------------------------------------------------------------

    def cancel_all_clicked(self) -> None:
        """The cancel button of the status bar / the list (57.9.8)."""
        log_op("export_parquet_cancel", target="all")
        self.cancel_all(tr("export_parquet.cancel_requested"))

    def cancel_all(self, notice: str | None) -> None:
        """Waiting files never start; running ones stop at their next chunk boundary and remove their
        `.part`. `notice`: what the status bar says (None: nothing, e.g. the window is closing)."""
        changed = self.queue.cancel_all()
        for item, new in changed:
            self._note_cancel(item, new)
        if notice and changed:
            self.win._show_notice(notice, 8_000)
        self._pump()
        self.refresh()
        self._report_if_idle()

    def cancel_item_clicked(self, item: ExportItem) -> None:
        """One file's cancel button in the list (57.9.8)."""
        if not item.active or item.state is ItemState.CANCELLING:
            return
        log_op("export_parquet_cancel", target=item.output)
        new = self.queue.cancel(item)
        if new is None:
            return
        self._note_cancel(item, new)
        self.win._show_notice(
            tr("export_parquet.cancelled" if new is ItemState.CANCELLED else "export_parquet.cancel_requested"), 8_000
        )
        self._pump()  # a freed slot goes to the next waiting file
        self.refresh()
        self._report_if_idle()

    def _note_cancel(self, item: ExportItem, new: ItemState) -> None:
        if new is ItemState.CANCELLED:
            self._unreported.append(item)
        elif oplog.is_active():
            oplog.record_job("parquet_export", "cancel", target=item.output)

    def shutdown(self) -> None:
        """The window is closing (57.9.8): cancel everything, forget what is still in flight so late
        results cannot reach a dead window, wait for the workers to remove their `.part` files."""
        self.closing = True
        self.timer.stop()
        for item, new in self.queue.cancel_all():
            if new is ItemState.CANCELLING and oplog.is_active():
                oplog.record_job("parquet_export", "cancel", target=item.output)
        for item in self.queue.items:
            if item.token:
                self.hub.cancel(item.token)
            if item.progress_key:
                self.hub.drop_progress(item.progress_key)
        self.pool.waitForDone(5000)  # a worker stops at its next chunk boundary (57.3)
        if self.panel is not None:
            self.panel.close()

    # -- display -----------------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        for item in self.queue.in_state(ItemState.RUNNING):
            item.samples.append((now, item.done_rows))
            while len(item.samples) > 2 and now - item.samples[0][0] > RATE_WINDOW:
                item.samples.popleft()
            t0, d0 = item.samples[0]
            item.rate = (item.done_rows - d0) / (now - t0) if now - t0 > 0.05 else item.rate
        if self.queue.in_state(ItemState.WAITING):
            self._pump()  # a waiting file re-asks the disk / memory guards
        self.refresh()
        if self.queue.is_idle():
            self.timer.stop()

    def summary(self) -> PanelSummary:
        counts = self.queue.counts()
        done, total = self.queue.rows_progress()
        live = counts["waiting"] + counts["running"] > 0
        percent = int(done * 100 / total) if total else 0
        if live:
            percent = min(99, percent)  # 100 % is the completion notice, not the last chunk handed over (57.9.3)
        running = self.queue.in_state(ItemState.RUNNING)
        indeterminate = bool(running) and all(i.stage in (STAGE_FULL, STAGE_RANGE) for i in running) and not done
        rate = sum(i.rate for i in running)
        remaining = (total - done) / rate if rate > 0 and total >= done else None
        warnings = list(self.warnings)
        for item in self.queue.in_state(ItemState.WAITING):
            if item.wait_reason == "disk":
                warnings.append(tr("export_parquet.wait_disk"))
                break
        for item in self.queue.in_state(ItemState.WAITING):
            if item.wait_reason == "memory":
                warnings.append(tr("export_parquet.wait_memory"))
                break
        return PanelSummary(percent, indeterminate, counts, time.monotonic() - self._batch_start, remaining, warnings)

    def refresh(self) -> None:
        """Redraw the status bar and the list from the queue (the timer and every state change)."""
        self.refreshes += 1
        counts = self.queue.counts()
        summary = self.summary()
        finished = counts["done"] + counts["failed"] + counts["cancelled"]
        phase = 1
        if summary.indeterminate:
            running = self.queue.in_state(ItemState.RUNNING)
            phase = -2 if any(i.stage == STAGE_RANGE for i in running) else -1
        self.status.set_summary(finished, counts["total"], summary.percent, phase)
        self.status.setVisible(counts["waiting"] + counts["running"] > 0)
        if self.panel is not None:
            self.panel.refresh(self.queue.items, summary)

    def show_panel(self) -> None:
        if self.panel is None:
            self.panel = ParquetExportPanel(
                self._on_row_action, self.cancel_all_clicked, self.clear_finished, parent=self.win
            )
        self.panel.refresh(self.queue.items, self.summary())
        self.panel.show()
        self.panel.raise_()

    def clear_finished(self) -> None:
        self.queue.clear_finished()
        self.refresh()

    def _on_row_action(self, item_id: int) -> None:
        item = self.queue.get(item_id)
        if item is None:
            return
        if item.active:
            self.cancel_item_clicked(item)
        elif item.state is ItemState.DONE:
            self.open_folder(str(Path(item.output).parent))

    @staticmethod
    def _open_folder(folder: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # -- the notice at the end ---------------------------------------------------------------

    @staticmethod
    def error_text(item: ExportItem) -> str:
        error = item.error
        if error.startswith("DiskFull"):  # 57.9.6a: stopped on purpose, the partial file is gone
            return tr("export_parquet.disk_full")
        if error.startswith("MemoryLow"):
            return tr("export_parquet.memory_low")
        return error

    def _report_if_idle(self) -> None:
        """Once nothing waits or runs any more: one status-bar line, and one warning when a file failed or
        lost signals (57.9.7, 57.8.2). A single file keeps the old wording."""
        if self.closing or not self.queue.is_idle() or not self._unreported:
            return
        items, self._unreported = self._unreported, []
        title = tr("toolbar.export_parquet")
        done = [i for i in items if i.state is ItemState.DONE]
        failed = [i for i in items if i.state is ItemState.FAILED]
        cancelled = [i for i in items if i.state is ItemState.CANCELLED]
        lost = [i for i in done if i.missing]
        over = [i for i in done if i.result is not None and i.result.float32_overflow]
        if len(items) == 1:
            item = items[0]
            if done:
                self.win._show_notice(self._saved_text(item), 20_000)
                if over:  # 57.9.6b: values that do not fit float32 became inf
                    messages.warning(
                        self.win, title, tr("export_parquet.float32_overflow", names=", ".join(item.result.float32_overflow))
                    )
            elif failed:
                messages.warning(self.win, title, tr("export_parquet.failed", error=self.error_text(item)))
            else:
                self.win._show_notice(tr("export_parquet.cancelled"), 8_000)
            return
        if not done and not failed:  # everything cancelled
            self.win._show_notice(tr("export_parquet.cancelled"), 8_000)
            return
        folder = str(Path((done or items)[0].output).resolve().parent)
        self.win._show_notice(
            tr("export_parquet.batch_summary", ok=len(done), failed=len(failed), cancelled=len(cancelled), folder=folder),
            20_000,
        )
        if failed or lost or over:
            lines = [tr("export_parquet.batch_summary", ok=len(done), failed=len(failed), cancelled=len(cancelled), folder=folder)]
            if failed:
                lines.append("")
                lines.append(tr("export_parquet.batch_failed_head"))
                lines.extend(f"- {Path(i.source).name}: {self.error_text(i)}" for i in failed)
            if lost:
                lines.append("")
                lines.append(tr("export_parquet.batch_missing_head"))
                lines.extend(f"- {Path(i.source).name}: {', '.join(i.missing)}" for i in lost)
            if over:
                lines.append("")
                lines.append(tr("export_parquet.batch_overflow_head"))
                lines.extend(f"- {Path(i.source).name}: {', '.join(i.result.float32_overflow)}" for i in over)
            messages.warning(self.win, title, "\n".join(lines))

    def _saved_text(self, item: ExportItem) -> str:
        result = item.result
        assert result is not None
        seconds = f"{result.elapsed:.1f}"
        if result.full_computed:
            text = tr(
                "export_parquet.saved_full", path=result.path, rows=f"{result.rows:,}", seconds=seconds,
                names=", ".join(result.full_computed),
            )
        else:
            text = tr("export_parquet.saved", path=result.path, rows=f"{result.rows:,}", seconds=seconds)
        if item.missing:
            text += " " + tr("export_parquet.file_missing_tip", names=", ".join(item.missing))
        return text
