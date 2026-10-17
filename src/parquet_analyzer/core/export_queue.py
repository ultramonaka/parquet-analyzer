"""The Parquet export's work queue (export.ja.md 57.9.4, 57.9.5, 57.9.6a). No Qt.

One `ExportItem` per output file, in the order they were added. The queue only keeps the table of
states and decides *which item may start next*; starting a worker, receiving its result and drawing
progress belong to `MainWindow`, so every rule here is testable without a window.

States: waiting -> running -> done / failed / cancelling -> cancelled (a waiting item that is
cancelled goes straight to cancelled; "cancelling" is a running item whose worker was asked to stop
and has not finished removing its `.part` yet). The disk / memory guards of `export_resources` are
asked before a further file starts.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from . import export_resources as res
from . import parquet_export as pe

# 57.9.6: this many files in a row stopped by a full disk -> the files still waiting fail without starting
DISK_FAIL_STREAK = 2
DISK_ERROR = "DiskFull: not enough free disk space"
# re-asking the disk / memory probes for a waiting file at most this often (seconds)
RESOURCE_RECHECK = 1.0

estimate_export_memory = pe.estimate_export_memory  # 57.9.5 (the pure function lives next to the plan)


class ItemState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


_ALLOWED: dict[ItemState, set[ItemState]] = {
    ItemState.WAITING: {ItemState.RUNNING, ItemState.CANCELLED, ItemState.FAILED},
    ItemState.RUNNING: {ItemState.DONE, ItemState.FAILED, ItemState.CANCELLING, ItemState.CANCELLED},
    # a worker that was asked to stop may still have finished the file first (then it is done)
    ItemState.CANCELLING: {ItemState.CANCELLED, ItemState.DONE},
    ItemState.DONE: set(),
    ItemState.FAILED: set(),
    ItemState.CANCELLED: set(),
}

STAGE_RANGE = "range"  # searching the saved time range (57.7.4)
STAGE_FULL = "full"  # computing variables over all rows (57.6)
STAGE_WRITE = "write"

_ids = itertools.count(1)


@dataclass(eq=False)
class ExportItem:
    """One output file. `plan` is None for an item that failed before it could be planned (`error`
    says why). `missing`: the selected signals this file does not have (written without them,
    reported at the end, 57.8.2)."""

    source: str
    output: str
    plan: pe.ExportPlan | None = None
    overwrite: bool = False
    view: str | None = None
    missing: tuple[str, ...] = ()
    memory: int = 0  # estimate, bytes (57.9.5)
    max_bytes: int = 0  # upper bound of the output size (57.9.6a)
    id: int = field(default_factory=lambda: next(_ids))
    state: ItemState = ItemState.WAITING
    stage: str = ""
    done_rows: int = 0
    total_rows: int = 0
    error: str = ""
    result: pe.ExportResult | None = None
    wait_reason: str = ""  # "", "memory_budget", "disk", "memory": why a waiting item has not started
    note: str = ""  # e.g. "uses a lot of memory" (shown in the list)
    added_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    rate: float = 0.0  # rows per second, a moving average kept by the owner of the clock
    samples: deque = field(default_factory=lambda: deque(maxlen=64))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    worker_done: threading.Event = field(default_factory=threading.Event)
    token: int = 0
    progress_key: int = 0

    def __post_init__(self) -> None:
        if self.plan is not None and not self.total_rows:
            self.total_rows = self.plan.rows

    @property
    def active(self) -> bool:
        return self.state in (ItemState.WAITING, ItemState.RUNNING, ItemState.CANCELLING)

    @property
    def finished(self) -> bool:
        return not self.active


def norm_path(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(path))


def memory_budget() -> int:
    """How much the exports running together may be estimated to use (57.9.5)."""
    return res.memory_budget_bytes()


def memory_over_physical(item: ExportItem) -> bool:
    phys = res.physical_memory_bytes()
    return phys is not None and item.memory > phys


class ExportQueue:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.items: list[ExportItem] = []
        self._clock = clock
        self.disk_streak = 0
        self._resource_checked = -1e9

    # -- the table ---------------------------------------------------------------------------

    def add(self, item: ExportItem) -> ExportItem:
        """Append `item` at the end (also while others run, 57.9.2 no. 4)."""
        item.added_at = self._clock()
        self.items.append(item)
        return item

    def get(self, item_id: int) -> ExportItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def in_state(self, *states: ItemState) -> list[ExportItem]:
        return [i for i in self.items if i.state in states]

    def active(self) -> list[ExportItem]:
        return [i for i in self.items if i.active]

    def is_idle(self) -> bool:
        return not self.active()

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in ItemState}
        for i in self.items:
            out[i.state.value] += 1
        out["total"] = len(self.items)
        return out

    def new_batch(self) -> None:
        """A new group of exports starts: forget the finished rows' effect on the rules. The full-disk streak
        (57.9.6) belongs to the last group; a user who made room must not see the next export fail at once."""
        self.disk_streak = 0

    def clear_finished(self) -> int:
        n = len(self.items)
        self.items = [i for i in self.items if i.active]
        return n - len(self.items)

    # -- transitions -------------------------------------------------------------------------

    def _move(self, item: ExportItem, state: ItemState) -> None:
        if state not in _ALLOWED[item.state]:
            raise ValueError(f"export item {item.id}: {item.state.value} -> {state.value} is not allowed")
        item.state = state
        if state in (ItemState.DONE, ItemState.FAILED, ItemState.CANCELLED):
            item.finished_at = self._clock()
            item.wait_reason = ""

    def mark_running(self, item: ExportItem) -> None:
        self._move(item, ItemState.RUNNING)
        item.wait_reason = ""
        item.started_at = self._clock()

    def set_progress(self, item: ExportItem, done: int, total: int) -> None:
        """Remember a worker report (`total < 0`: a phase without a percentage, 57.3)."""
        if total == pe.PROGRESS_FULL:
            item.stage = STAGE_FULL
        elif total == pe.PROGRESS_RANGE:
            item.stage = STAGE_RANGE
        else:
            item.stage = STAGE_WRITE
            item.done_rows = max(0, int(done))
            item.total_rows = max(0, int(total))

    def finish(
        self, item: ExportItem, state: ItemState, *, error: str = "", result: pe.ExportResult | None = None
    ) -> None:
        """A worker's outcome: `DONE` (with `result`), `FAILED` (with `error`) or `CANCELLED`. A full
        disk counts toward `DISK_FAIL_STREAK`; any other outcome that is not a failure ends the streak."""
        if state not in (ItemState.DONE, ItemState.FAILED, ItemState.CANCELLED):
            raise ValueError(f"not a final state: {state}")
        self._move(item, state)
        item.error = error
        item.result = result
        item.stage = ""
        if state is ItemState.DONE:
            item.done_rows = item.total_rows = result.rows if result is not None else item.total_rows
            self.disk_streak = 0
        elif state is ItemState.FAILED:
            self.disk_streak = self.disk_streak + 1 if error.startswith("DiskFull") else 0

    def fail(self, item: ExportItem, error: str) -> None:
        """Fail an item that never ran (waiting): it could not be planned, or the disk is full."""
        self._move(item, ItemState.FAILED)
        item.error = error

    def cancel(self, item: ExportItem) -> ItemState | None:
        """Ask for `item` to stop: a waiting one becomes cancelled at once; a running one becomes
        cancelling and its `cancel_event` is set (the worker finishes at the next chunk boundary,
        `finish(..., CANCELLED)` follows). Returns the new state, None for a finished item."""
        if item.state is ItemState.WAITING:
            self._move(item, ItemState.CANCELLED)
            return ItemState.CANCELLED
        if item.state is ItemState.RUNNING:
            item.cancel_event.set()
            self._move(item, ItemState.CANCELLING)
            return ItemState.CANCELLING
        return None

    def cancel_all(self) -> list[tuple[ExportItem, ItemState]]:
        """Waiting items first (they never start), then the running ones (57.9.8)."""
        out: list[tuple[ExportItem, ItemState]] = []
        for item in self.in_state(ItemState.WAITING) + self.in_state(ItemState.RUNNING):
            new = self.cancel(item)
            if new is not None:
                out.append((item, new))
        return out

    # -- which item starts next --------------------------------------------------------------

    def next_to_start(
        self,
        parallel: int,
        budget: int | None = None,
        *,
        resources: bool = True,
        force_resource_check: bool = False,
    ) -> ExportItem | None:
        """The first waiting item if it may start now, else None (57.9.4). Never overtakes: only the
        head of the waiting items is considered, so the order stays predictable.

        (a) fewer than `parallel` files run (a cancelling one still holds its thread);
        (c) no running / cancelling item writes the same output path (the cancelled one is still
            removing its `.part`);
        (b) the running estimates plus this one stay within `budget` (an idle queue starts the head
            even when it alone exceeds the budget: it runs alone, 57.9.5);
        (d) with something running: `export_resources.can_start_new_file` (disk space for what is still
            to be written, free memory); with nothing running the head starts anyway and its own
            guard fails it if need be (otherwise a full disk would wait forever).
        After `DISK_FAIL_STREAK` full-disk failures in a row every waiting item fails (57.9.6)."""
        waiting = self.in_state(ItemState.WAITING)
        if not waiting:
            return None
        if self.disk_streak >= DISK_FAIL_STREAK:
            for item in waiting:
                self.fail(item, DISK_ERROR)
            return None
        head = waiting[0]
        running = self.in_state(ItemState.RUNNING)
        busy = running + self.in_state(ItemState.CANCELLING)
        if len(busy) >= max(1, parallel):
            head.wait_reason = ""
            return None
        out = norm_path(head.output)
        if any(norm_path(i.output) == out for i in busy):
            head.wait_reason = ""
            return None
        if busy:
            limit = res.memory_budget_bytes() if budget is None else budget
            if sum(i.memory for i in busy) + head.memory > limit:
                head.wait_reason = "memory_budget"
                return None
            if resources:
                now = self._clock()
                if head.wait_reason in ("disk", "memory") and not force_resource_check and (
                    now - self._resource_checked < RESOURCE_RECHECK
                ):
                    return None
                self._resource_checked = now
                remaining = head.max_bytes + sum(
                    int(i.max_bytes * (1 - min(1.0, i.done_rows / i.total_rows))) if i.total_rows else i.max_bytes
                    for i in running
                )
                why = res.can_start_new_file(remaining, Path(head.output).parent)
                if why is not None:
                    head.wait_reason = why
                    return None
        head.wait_reason = ""
        return head

    # -- progress numbers --------------------------------------------------------------------

    def rows_progress(self) -> tuple[int, int]:
        """(rows written, rows to write) over the files that count: done ones in full, running and
        waiting ones as reported. Failed and cancelled files drop out (they will not be written)."""
        done = total = 0
        for i in self.items:
            if i.state is ItemState.DONE:
                done += i.done_rows
                total += i.total_rows
            elif i.state in (ItemState.RUNNING, ItemState.WAITING):
                done += min(i.done_rows, i.total_rows)
                total += i.total_rows
        return done, total
