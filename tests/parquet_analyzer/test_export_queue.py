"""core/export_queue (export.ja.md 57.9.4, 57.9.5, 57.9.6a): the state table and the rule for which file
starts next. Pure: no Qt, no files; the disk / memory probes are replaced."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import export_queue as eq
from parquet_analyzer.core import export_resources as res
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.export_queue import ExportItem, ExportQueue, ItemState
from parquet_analyzer.core.functions import FunctionRegistry

S = ItemState


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def item(name="a", memory=0, max_bytes=0, output=None):
    return ExportItem(source=f"/src/{name}.parquet", output=output or f"/out/{name}.parquet", memory=memory, max_bytes=max_bytes)


def queue_of(*names, **kw):
    q = ExportQueue(clock=Clock())
    items = [q.add(item(n, **kw)) for n in names]
    return q, items


def start_all(q, parallel=2, **kw):
    started = []
    while (it := q.next_to_start(parallel, budget=kw.get("budget", 10 * res.GB))) is not None:
        q.mark_running(it)
        started.append(it)
    return started


# -- transitions ----------------------------------------------------------------------------------


def reach(q, it, state):
    """Drive `it` (waiting) to `state` through the public API."""
    if state is S.WAITING:
        return
    if state is S.CANCELLED and it.state is S.WAITING:
        q.cancel(it)
        return
    q.mark_running(it)
    if state is S.RUNNING:
        return
    if state is S.DONE:
        q.finish(it, S.DONE, result=None)
    elif state is S.FAILED:
        q.finish(it, S.FAILED, error="boom")
    elif state is S.CANCELLING:
        q.cancel(it)


def test_the_normal_paths():
    q, (a, b, c) = queue_of("a", "b", "c")
    assert [i.state for i in q.items] == [S.WAITING] * 3
    q.mark_running(a)
    q.finish(a, S.DONE, result=None)
    assert a.state is S.DONE and a.finished_at is not None
    q.mark_running(b)
    q.finish(b, S.FAILED, error="x")
    assert b.state is S.FAILED and b.error == "x"
    assert q.cancel(c) is S.CANCELLED  # a waiting item goes straight to cancelled
    assert c.state is S.CANCELLED and not c.cancel_event.is_set()
    d = q.add(item("d"))
    q.mark_running(d)
    assert q.cancel(d) is S.CANCELLING and d.cancel_event.is_set()
    q.finish(d, S.CANCELLED)
    assert d.state is S.CANCELLED


@pytest.mark.parametrize("state", [S.DONE, S.FAILED, S.CANCELLED])
def test_a_finished_item_never_changes_again(state):
    q, (it,) = queue_of("a")
    reach(q, it, state)
    assert it.state is state
    assert q.cancel(it) is None
    for final in (S.DONE, S.FAILED, S.CANCELLED):
        with pytest.raises(ValueError):
            q.finish(it, final)
    with pytest.raises(ValueError):
        q.mark_running(it)


def test_illegal_moves_raise():
    q, (waiting, running) = queue_of("w", "r")
    q.mark_running(running)
    with pytest.raises(ValueError):
        q.finish(waiting, S.DONE)  # never started
    with pytest.raises(ValueError):
        q.mark_running(running)  # already running
    with pytest.raises(ValueError):
        q.finish(running, S.WAITING)  # not a final state


def test_a_cancelling_item_may_still_turn_out_done():
    """The worker can finish the file before it sees the flag (57.9.8): the file exists, so it is done."""
    q, (it,) = queue_of("a")
    q.mark_running(it)
    q.cancel(it)
    q.finish(it, S.DONE, result=None)
    assert it.state is S.DONE


def test_counts_and_clear_finished():
    q, (a, b, c) = queue_of("a", "b", "c")
    reach(q, a, S.DONE)
    reach(q, b, S.RUNNING)
    assert q.counts() == {"waiting": 1, "running": 1, "done": 1, "failed": 0, "cancelling": 0, "cancelled": 0, "total": 3}
    assert not q.is_idle()
    assert q.clear_finished() == 1 and q.items == [b, c]


def test_progress_numbers_and_stages():
    q, (a, b) = queue_of("a", "b")
    q.mark_running(a)
    q.set_progress(a, 0, pe.PROGRESS_FULL)
    assert a.stage == eq.STAGE_FULL
    q.set_progress(a, 0, pe.PROGRESS_RANGE)
    assert a.stage == eq.STAGE_RANGE
    q.set_progress(a, 40, 100)
    assert (a.stage, a.done_rows, a.total_rows) == (eq.STAGE_WRITE, 40, 100)
    b.total_rows = 100
    assert q.rows_progress() == (40, 200)
    q.cancel(b)  # a cancelled file drops out of the overall figure
    assert q.rows_progress() == (40, 100)


# -- how many run at once ------------------------------------------------------------------------


@pytest.mark.parametrize("parallel", [1, 2, 3, 4])
def test_at_most_parallel_files_start(parallel):
    q, items = queue_of("a", "b", "c", "d", "e")
    started = start_all(q, parallel)
    assert started == items[:parallel]
    assert q.next_to_start(parallel, budget=10 * res.GB) is None
    q.finish(items[0], S.DONE, result=None)  # a slot is free again: the next one in order
    assert q.next_to_start(parallel, budget=10 * res.GB) is items[parallel]


def test_a_cancelling_file_still_holds_its_slot():
    q, (a, b) = queue_of("a", "b")
    q.mark_running(a)
    q.cancel(a)
    assert q.next_to_start(1, budget=res.GB) is None  # its thread is still cleaning up
    q.finish(a, S.CANCELLED)
    assert q.next_to_start(1, budget=res.GB) is b


def test_items_added_while_others_run_go_last():
    q, (a, b) = queue_of("a", "b")
    start_all(q, 2)
    late = q.add(item("late"))
    assert q.items[-1] is late
    q.finish(a, S.DONE, result=None)
    assert q.next_to_start(2, budget=res.GB) is late


def test_failure_does_not_stop_the_rest():
    q, (a, b, c) = queue_of("a", "b", "c")
    q.mark_running(a)
    q.finish(a, S.FAILED, error="ExportError: bad")
    assert q.next_to_start(1, budget=res.GB) is b


# -- memory budget (57.9.5) ----------------------------------------------------------------------


def test_the_memory_budget_holds_a_file_back():
    q, (a, b) = queue_of("a", "b", memory=3 * res.GB)
    assert q.next_to_start(2, budget=4 * res.GB) is a
    q.mark_running(a)
    assert q.next_to_start(2, budget=4 * res.GB) is None  # 3 + 3 > 4
    assert b.wait_reason == "memory_budget"
    q.finish(a, S.DONE, result=None)
    assert q.next_to_start(2, budget=4 * res.GB) is b and b.wait_reason == ""


def test_a_file_over_the_budget_starts_when_nothing_runs():
    q, (big,) = queue_of("big", memory=9 * res.GB)
    assert q.next_to_start(2, budget=2 * res.GB) is big


def test_a_file_over_the_budget_runs_alone():
    q, (a, big, small) = queue_of("a", "big", "small")
    a.memory, big.memory, small.memory = res.GB, 9 * res.GB, res.GB // 2
    q.mark_running(a)
    assert q.next_to_start(3, budget=4 * res.GB) is None  # waits for a
    q.finish(a, S.DONE, result=None)
    q.mark_running(big)
    assert q.next_to_start(3, budget=4 * res.GB) is None  # alone while it runs
    assert small.wait_reason == "memory_budget"  # nothing joins a file that exceeds the budget


def test_no_overtaking_a_waiting_big_file():
    q, (a, big, small) = queue_of("a", "big", "small")
    a.memory, big.memory, small.memory = 3 * res.GB, 3 * res.GB, 1
    q.mark_running(a)
    assert q.next_to_start(3, budget=4 * res.GB) is None  # big waits for memory ...
    assert small.state is S.WAITING  # ... and small does not jump the queue
    assert q.next_to_start(3, budget=4 * res.GB) is None


def test_the_default_budget_is_read_from_the_resources(monkeypatch):
    monkeypatch.setattr(res, "memory_budget_bytes", lambda: 5 * res.GB)
    assert eq.memory_budget() == 5 * res.GB
    q, (a, b) = queue_of("a", "b", memory=3 * res.GB)
    q.mark_running(a)
    assert q.next_to_start(2) is None  # 6 GB > the 5 GB budget from the probe


# -- the same output path (57.9.4 c) ----------------------------------------------------------


def test_the_same_output_waits_for_the_first_including_its_cleanup():
    q = ExportQueue(clock=Clock())
    first = q.add(item("one", output="/out/same.parquet"))
    second = q.add(item("two", output="/out/same.parquet"))
    q.mark_running(first)
    assert q.next_to_start(2, budget=res.GB) is None
    q.cancel(first)  # cancelling: the .part is still being removed
    assert q.next_to_start(2, budget=res.GB) is None
    q.finish(first, S.CANCELLED)
    assert q.next_to_start(2, budget=res.GB) is second


# -- cancelling ----------------------------------------------------------------------------------


def test_cancel_all_cancels_the_waiting_first_and_asks_the_running_to_stop():
    q, (a, b, c, d) = queue_of("a", "b", "c", "d")
    start_all(q, 2)
    changed = q.cancel_all()
    assert [(i.id, s) for i, s in changed] == [(c.id, S.CANCELLED), (d.id, S.CANCELLED), (a.id, S.CANCELLING), (b.id, S.CANCELLING)]
    assert a.cancel_event.is_set() and b.cancel_event.is_set() and not c.cancel_event.is_set()
    assert q.next_to_start(2, budget=res.GB) is None  # nothing waits any more
    assert not q.is_idle()  # until the workers are done
    q.finish(a, S.CANCELLED)
    q.finish(b, S.CANCELLED)
    assert q.is_idle()


def test_cancelling_one_running_file_frees_its_slot_for_the_next_after_cleanup():
    q, (a, b, c) = queue_of("a", "b", "c")
    start_all(q, 2)
    q.cancel(a)
    assert q.next_to_start(2, budget=res.GB) is None
    q.finish(a, S.CANCELLED)
    assert q.next_to_start(2, budget=res.GB) is c


# -- a full disk (57.9.6) -------------------------------------------------------------------------


def test_repeated_full_disk_failures_fail_the_rest_without_starting_them():
    q, items = queue_of("a", "b", "c", "d")
    for it in items[:2]:
        q.mark_running(it)
        q.finish(it, S.FAILED, error="DiskFull: disk full")
    assert q.disk_streak == 2
    assert q.next_to_start(2, budget=res.GB) is None
    assert [i.state for i in items[2:]] == [S.FAILED, S.FAILED]
    assert all(i.error == eq.DISK_ERROR and i.started_at is None for i in items[2:])
    assert q.is_idle()


def test_one_full_disk_failure_does_not_stop_the_rest():
    q, (a, b) = queue_of("a", "b")
    q.mark_running(a)
    q.finish(a, S.FAILED, error="DiskFull: disk full")
    assert q.next_to_start(1, budget=res.GB) is b


def test_a_success_or_another_failure_ends_the_disk_streak():
    q, (a, b, c, d) = queue_of("a", "b", "c", "d")
    q.mark_running(a)
    q.finish(a, S.FAILED, error="DiskFull: x")
    q.mark_running(b)
    q.finish(b, S.DONE, result=None)
    assert q.disk_streak == 0
    q.mark_running(c)
    q.finish(c, S.FAILED, error="DiskFull: x")
    q.mark_running(d)
    q.finish(d, S.FAILED, error="ExportError: other")
    assert q.disk_streak == 0


# -- the disk / memory guards between files (57.9.6a) ----------------------------------------------


def test_a_waiting_file_is_held_back_by_the_disk_and_started_when_it_frees(monkeypatch):
    clock = Clock()
    q = ExportQueue(clock=clock)
    a = q.add(item("a", max_bytes=10 * res.GB))
    b = q.add(item("b", max_bytes=10 * res.GB))
    q.mark_running(a)
    free = {"v": 12 * res.GB}
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: free["v"])
    assert q.next_to_start(2, budget=res.GB * 100) is None  # a still needs 10 GB, b another 10: 12 free
    assert b.wait_reason == "disk"
    free["v"] = 100 * res.GB
    assert q.next_to_start(2, budget=res.GB * 100) is None  # asked a moment ago: not asked again yet
    clock.now += eq.RESOURCE_RECHECK + 0.1
    assert q.next_to_start(2, budget=res.GB * 100) is b and b.wait_reason == ""


def test_low_memory_holds_a_further_file_back(monkeypatch):
    q, (a, b) = queue_of("a", "b")
    q.mark_running(a)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: res.GB)
    assert q.next_to_start(2, budget=res.GB * 100) is None
    assert b.wait_reason == "memory"
    assert q.next_to_start(2, budget=res.GB * 100, force_resource_check=True) is None
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 64 * res.GB)
    assert q.next_to_start(2, budget=res.GB * 100, force_resource_check=True) is b


def test_the_first_file_starts_even_on_a_full_disk(monkeypatch):
    """Nothing is running, so waiting could never end: the file's own guard fails it (and counts toward the streak)."""
    q, (a,) = queue_of("a", max_bytes=10 * res.GB)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 1)
    assert q.next_to_start(2, budget=res.GB) is a


def test_the_resource_check_can_be_skipped(monkeypatch):
    q, (a, b) = queue_of("a", "b")
    q.mark_running(a)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 1)
    assert q.next_to_start(2, budget=res.GB, resources=False) is b


# -- the memory estimate (57.9.5) -----------------------------------------------------------------

N = 20_000


@pytest.fixture
def src(tmp_path):
    t = np.arange(N, dtype="float64")
    path = tmp_path / "s.parquet"
    pq.write_table(pa.table({"t": t, "a": t * 2}), path)
    return path


def plan_for(src, derived=None):
    derived = derived or {}
    return pe.build_plan(
        source_path=str(src), total_rows=N, row_range=(0, N), time=pe.ExportColumn("t", "t"),
        signals=[pe.ExportColumn("a", "a"), *(pe.ExportColumn(n) for n in derived)], derived_expressions=derived,
        file_columns={"a": "a", "t": "t"}, registry=FunctionRegistry(), chunk_rows=1_000,
    )


def test_estimate_export_memory_is_the_core_estimate(src):
    plain = plan_for(src, {"d": "a + 1"})
    assert eq.estimate_export_memory(plain) == pe.estimate_export_memory(plain) >= 200 * res.MB
    assert eq.estimate_export_memory(plan_for(src)) < eq.estimate_export_memory(plain)  # one more working array


def test_a_new_item_from_a_plan_gets_its_row_count(src):
    it = ExportItem(source=str(src), output="/o.parquet", plan=plan_for(src))
    assert it.total_rows == N


def test_a_new_batch_forgets_the_full_disk_streak():
    q, items = queue_of("a", "b")
    for it in items:
        q.mark_running(it)
        q.finish(it, S.FAILED, error="DiskFull: x")
    assert q.disk_streak == 2
    q.new_batch()
    assert q.disk_streak == 0
    c = q.add(item("c"))
    assert q.next_to_start(1, budget=res.GB) is c  # not failed at once by the last group's streak
