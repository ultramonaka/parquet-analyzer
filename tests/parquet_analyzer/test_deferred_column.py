"""DeferredColumn / LazyColumn.peek / ViewComputeRunner (expressions 55.2, 55.3, 55.7)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QApplication

from parquet_analyzer.core import expression as expression_module
from parquet_analyzer.core.background_worker import WorkerHub
from parquet_analyzer.core.column import DeferredColumn, LazyColumn, LazyVariables
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import (
    ExpressionError,
    UnknownFunctionError,
    evaluate_expression,
)
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.ui.view_compute import (
    ViewComputeRunner,
    estimate_remaining,
    format_remaining,
)

N = 5000


class CountingSource(ParquetDataSource):
    """Records every read_columns call as (columns, row_start, row_end)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.reads: list[tuple] = []

    def read_columns(self, columns, row_start=0, row_end=None):
        self.reads.append((tuple(columns), row_start, row_end))
        return super().read_columns(columns, row_start, row_end)


@pytest.fixture
def src(tmp_path):
    rng = np.random.default_rng(3)
    table = pa.table(
        {
            "a": rng.normal(size=N),
            "b": rng.normal(size=N),
            "s": [f"x{i}" for i in range(N)],
        }
    )
    path = tmp_path / "d.parquet"
    pq.write_table(table, path, row_group_size=500)
    return CountingSource(path)


@pytest.fixture
def cols(src):
    return {"a": LazyColumn(src, "a"), "b": LazyColumn(src, "b"), "s": LazyColumn(src, "s")}


def _full(expr, cols, registry=None):
    return np.asarray(evaluate_expression(expr, LazyVariables(dict(cols)), registry), dtype=np.float64)


def test_creating_a_deferred_column_reads_only_the_first_rows(src, cols):
    col = DeferredColumn("a * 2 + b", cols, N, FunctionRegistry())
    assert not col.is_ready
    assert cols["a"]._array is None and cols["b"]._array is None  # nothing became resident
    assert src.reads, "the trial computation reads the first rows"
    assert all(end is not None and end <= 1000 for _, _, end in src.reads)


def test_values_equal_the_full_evaluation(cols):
    reg = FunctionRegistry()
    reg.user_functions["scaled"] = UserFunction("scaled", ["x"], [("k", 2.0)], "x * k + abs(x)")
    for expr in ["a + b", "scaled(a) - b", "rolling_mean(a, 7) + delta(b)", "where(a > 0, a, b)"]:
        col = DeferredColumn(expr, cols, N, reg)
        np.testing.assert_array_equal(col.values(), _full(expr, cols, reg))
        assert col.is_ready
        assert col.values() is col.values()  # cached
        np.testing.assert_array_equal(col.window(10, 20), col.values()[10:20])


def test_scalar_result_is_broadcast(cols):
    col = DeferredColumn("2 + 1", cols, N, FunctionRegistry())
    np.testing.assert_array_equal(col.values(), np.full(N, 3.0))


def test_a_bad_expression_raises_at_creation(cols):
    reg = FunctionRegistry()
    with pytest.raises(ExpressionError):
        DeferredColumn("a +", cols, N, reg)  # syntax
    with pytest.raises(ExpressionError):
        DeferredColumn("a + nosuch", cols, N, reg)  # unknown name
    with pytest.raises(UnknownFunctionError):
        DeferredColumn("nosuchfn(a)", cols, N, reg)  # unknown function
    with pytest.raises(ExpressionError):
        DeferredColumn("a + s", cols, N, reg)  # a text column is not numeric


def test_trial_computation_is_limited_to_probe_rows(src, cols):
    DeferredColumn("a + b", cols, N, FunctionRegistry(), probe_rows=100)
    assert all(end == 100 for _, _, end in src.reads)


def test_concurrent_values_compute_once(cols, monkeypatch):
    col = DeferredColumn("a + b", cols, N, FunctionRegistry())
    calls = []
    real = expression_module.evaluate_expression

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.15)
        return real(*a, **k)

    monkeypatch.setattr(expression_module, "evaluate_expression", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(col.values())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(calls) == 1
    assert len(results) == 4 and all(r is results[0] for r in results)


def test_chain_of_deferred_columns(src, cols):
    a1 = DeferredColumn("a * 2", cols, N, FunctionRegistry())
    chain = dict(cols, d1=a1)
    d2 = DeferredColumn("d1 + 1", chain, N, FunctionRegistry())
    chain["d2"] = d2
    d3 = DeferredColumn("d2 * b", chain, N, FunctionRegistry())
    assert not (a1.is_ready or d2.is_ready or d3.is_ready)
    assert cols["a"]._array is None  # creating the chain still reads no whole column
    a = cols["a"].values()
    b = cols["b"].values()
    np.testing.assert_allclose(d3.values(), (a * 2 + 1) * b)
    assert a1.is_ready and d2.is_ready  # computing d3 computed what it needs


def test_chain_probe_sees_the_first_rows_of_an_uncomputed_input(cols):
    d1 = DeferredColumn("a * 2", cols, N, FunctionRegistry())
    np.testing.assert_allclose(d1.peek(0, 50), cols["a"].peek(0, 50) * 2)
    assert not d1.is_ready


def test_lazy_column_peek_does_not_read_the_whole_column(src):
    col = LazyColumn(src, "a")
    part = col.peek(10, 30)
    assert len(part) == 20 and col._array is None
    assert src.reads == [(("a",), 10, 30)]
    full = col.values()
    np.testing.assert_array_equal(part, full[10:30])
    n_reads = len(src.reads)
    np.testing.assert_array_equal(col.peek(10, 30), full[10:30])  # served from the cache
    assert len(src.reads) == n_reads
    assert len(col.peek(5, 5)) == 0


def test_lazy_column_values_reads_once_under_concurrency(src):
    col = LazyColumn(src, "a")
    threads = [threading.Thread(target=col.values) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert [r for r in src.reads if r[2] is None] == [(("a",), 0, None)]


# -- progress estimate (55.4) -------------------------------------------------------------


def test_estimate_remaining():
    assert estimate_remaining(5.0, 0, 10) is None  # nothing finished yet
    assert estimate_remaining(10.0, 2, 10) == pytest.approx(40.0)
    assert estimate_remaining(10.0, 10, 10) == 0.0
    assert estimate_remaining(3.0, 1, 1) == 0.0


def test_format_remaining(qapp):
    assert "5" in format_remaining(4.2)
    assert "1" in format_remaining(61)
    assert format_remaining(0.0)  # never empty


# -- the scheduler (55.3) ------------------------------------------------------------------


class FakeColumn:
    """Stands in for a DeferredColumn: records when compute() starts and ends."""

    def __init__(self, name, log, lock, sleep=0.05, fail=False):
        self.name, self.log, self.lock, self.sleep, self.fail = name, log, lock, sleep, fail

    def compute(self):
        with self.lock:
            self.log.append(("start", self.name))
        time.sleep(self.sleep)
        with self.lock:
            self.log.append(("end", self.name))
        if self.fail:
            raise RuntimeError("boom " + self.name)


def _drive(runner, timeout=20.0):
    deadline = time.monotonic() + timeout
    while runner.active:
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("runner did not finish")
        time.sleep(0.002)
    QApplication.processEvents()


def _make_runner(qapp, names, deps, parallel, failing=(), sleep=0.05):
    log: list = []
    lock = threading.Lock()
    columns = {n: FakeColumn(n, log, lock, sleep, fail=n in failing) for n in names}
    results = []
    progress = []
    runner = ViewComputeRunner(
        WorkerHub(),
        columns,
        deps,
        parallel,
        on_progress=lambda d, t: progress.append((d, t)),
        on_finished=results.append,
    )
    return runner, log, results, progress


def _max_concurrency(log):
    cur = best = 0
    for kind, _ in log:
        cur += 1 if kind == "start" else -1
        best = max(best, cur)
    return best


def test_runner_respects_dependency_order_and_cap(qapp):
    names = ["a", "b", "c", "d", "e", "f"]
    deps = {"c": {"a", "b"}, "d": {"c"}, "f": {"e"}}
    runner, log, results, progress = _make_runner(qapp, names, deps, parallel=2)
    runner.start()
    _drive(runner)
    assert len(results) == 1 and not results[0].cancelled and not results[0].failed
    assert sorted(results[0].computed) == names
    assert _max_concurrency(log) == 2
    pos = {e: i for i, e in enumerate(log)}
    for n, needs in deps.items():
        for d in needs:
            assert pos[("end", d)] < pos[("start", n)]
    assert progress[-1] == (6, 6)


def test_runner_parallel_one_is_sequential(qapp):
    runner, log, results, _ = _make_runner(qapp, ["a", "b", "c"], {}, parallel=1)
    runner.start()
    _drive(runner)
    assert _max_concurrency(log) == 1 and len(results[0].computed) == 3


def test_runner_failure_skips_dependents_only(qapp):
    runner, log, results, progress = _make_runner(
        qapp, ["a", "b", "c", "d"], {"b": {"a"}, "c": {"b"}}, parallel=2, failing={"a"}
    )
    runner.start()
    _drive(runner)
    res = results[0]
    assert sorted(res.failed) == ["a", "b", "c"]
    assert "boom a" in res.failed["a"]
    assert res.computed == ["d"]
    started = {n for k, n in log if k == "start"}
    assert started == {"a", "d"}  # b and c were never run
    assert progress[-1] == (4, 4)


def test_runner_cancel_finishes_at_once_and_ignores_late_results(qapp):
    runner, log, results, _ = _make_runner(qapp, ["a", "b", "c"], {"b": {"a"}, "c": {"b"}}, parallel=2, sleep=0.2)
    runner.start()
    runner.cancel()
    assert len(results) == 1 and results[0].cancelled and not runner.active
    time.sleep(0.4)
    for _ in range(20):
        QApplication.processEvents()
    assert len(results) == 1  # late results change nothing
    assert {n for k, n in log if k == "start"} == {"a"}  # b, c were never started


def test_runner_abort_reports_nothing(qapp):
    runner, log, results, _ = _make_runner(qapp, ["a", "b"], {}, parallel=2, sleep=0.1)
    runner.start()
    runner.abort()
    time.sleep(0.3)
    for _ in range(20):
        QApplication.processEvents()
    assert results == [] and not runner.active


def test_runner_with_materialized_inputs_is_not_confused(qapp):
    # a dependency that is not among the columns (a raw/ready variable) is simply not waited for
    runner, log, results, _ = _make_runner(qapp, ["x"], {"x": {"not_a_target"}}, parallel=1)
    runner.start()
    _drive(runner)
    assert results[0].computed == ["x"]
