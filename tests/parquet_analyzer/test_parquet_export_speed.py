"""Export speed work, stage A (export.ja.md 57.9.3, 57.9.5, 57.9.6, 57.9.6a): the polars streaming
writer and its pyarrow fallback, automatic chunk size, progress throttling, disk / memory guards and the
checks before the start. No Qt except the last test (the confirmation before the start)."""

from __future__ import annotations

import json
import time

import numpy as np
import polars.io.plugins
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import export_resources as res
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction

N = 60_000
WRITERS = ["polars", "pyarrow"]


@pytest.fixture
def src(tmp_path):
    rng = np.random.default_rng(5)
    a = rng.normal(size=N)
    a[[0, 7, N - 1]] = np.nan
    times = np.datetime64("2026-01-01T00:00:00", "us") + np.arange(N, dtype="int64") * np.timedelta64(1000, "us")
    table = pa.table(
        {
            "t": pa.array(times, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "cnt": pa.array(np.arange(N, dtype=np.int32)),
            "f32": pa.array(rng.normal(size=N).astype(np.float32)),
            "a": a,
            "label": pa.array([f"s{i % 7}" for i in range(N)]),
        }
    )
    path = tmp_path / "src.parquet"
    pq.write_table(table, path, row_group_size=10_000)
    return path


FILE_COLUMNS = {"a": "a", "cnt": "cnt", "f32": "f32", "label": "label"}


def registry():
    reg = FunctionRegistry()
    # a window given by a constant: the margin is not known statically -> computed over all rows
    reg.user_functions["roll"] = UserFunction("roll", ["x"], [("w", 5.0)], "rolling_mean(x, w)")
    return reg


def make_plan(src, signals, derived=None, chunk_rows=7_000, rows=(0, N)):
    cols = [pe.ExportColumn(n) if n in (derived or {}) else pe.ExportColumn(n, FILE_COLUMNS[n]) for n in signals]
    return pe.build_plan(
        source_path=str(src), total_rows=N, row_range=rows, time=pe.ExportColumn("t", "t"), signals=cols,
        derived_expressions=derived or {}, file_columns=FILE_COLUMNS, registry=registry(),
        chunk_rows=chunk_rows,
    )


# -- the writers (57.9.3) ----------------------------------------------------------------------------


def test_polars_writer_is_chosen_when_the_types_survive(src, tmp_path):
    plan = make_plan(src, ["a", "cnt", "f32", "d"], {"d": "a * 2"})
    assert pe.export_parquet(plan, tmp_path / "o.parquet").writer == "polars"


def test_string_column_falls_back_to_pyarrow(src, tmp_path):
    plan = make_plan(src, ["a", "label"])
    res_ = pe.export_parquet(plan, tmp_path / "o.parquet")
    assert res_.writer == "pyarrow"
    assert pq.read_schema(tmp_path / "o.parquet").field("label").type == pa.string()  # not large_string


def test_pyarrow_writer_is_used_when_polars_plugin_api_is_missing(src, tmp_path, monkeypatch):
    monkeypatch.delattr(polars.io.plugins, "register_io_source")
    assert pe.export_parquet(make_plan(src, ["a"]), tmp_path / "o.parquet").writer == "pyarrow"
    assert pq.read_table(tmp_path / "o.parquet").num_rows == N


def test_pyarrow_writer_skips_dictionary_for_floats_only(src, tmp_path):
    plan = make_plan(src, ["a", "cnt", "label"])
    pe.export_parquet(plan, tmp_path / "o.parquet", writer="pyarrow")
    rg = pq.read_metadata(tmp_path / "o.parquet").row_group(0)
    encodings = {rg.column(i).path_in_schema: rg.column(i).encodings for i in range(rg.num_columns)}
    assert "PLAIN_DICTIONARY" not in encodings["a"] and "RLE_DICTIONARY" not in encodings["a"]
    assert {"PLAIN_DICTIONARY", "RLE_DICTIONARY"} & set(encodings["label"])


@pytest.mark.parametrize("writer", WRITERS)
def test_both_writers_give_the_same_file_content(src, tmp_path, writer):
    plan = make_plan(src, ["a", "cnt", "f32", "d"], {"d": "delta(a) + rolling_mean(a, 5)"}, rows=(1_234, 51_000))
    out = tmp_path / f"{writer}.parquet"
    res_ = pe.export_parquet(plan, out, writer=writer)
    assert res_.writer == writer
    t = pq.read_table(out)
    assert t.column_names == ["t", "a", "cnt", "f32", "d"]
    assert t.num_rows == res_.rows == 51_000 - 1_234
    assert t.schema.field("t").type == pa.timestamp("us", tz="Asia/Tokyo")
    assert t.schema.field("cnt").type == pa.int32() and t.schema.field("f32").type == pa.float32()
    assert t.schema.field("d").type == pa.float64()
    source = pq.read_table(src)
    np.testing.assert_array_equal(t.column("a").to_numpy(), source.column("a").to_numpy()[1_234:51_000])
    assert t.column("t").equals(source.column("t").slice(1_234, 51_000 - 1_234))
    info = json.loads(pq.read_schema(out).metadata[pe.METADATA_KEY])
    assert info["range"]["rows"] == [1_234, 51_000] and info["time_column"] == "t"
    meta = pq.read_metadata(out)
    sizes = [meta.row_group(i).num_rows for i in range(meta.num_row_groups)]
    assert sum(sizes) == t.num_rows and max(sizes) <= plan.chunk_rows
    assert meta.num_row_groups == res_.row_groups
    assert meta.row_group(0).column(0).compression == "ZSTD"
    stats = meta.row_group(0).column(0).statistics  # the time axis keeps min / max (find_row_range reads them)
    assert stats is not None and stats.has_min_max


def test_the_writers_write_identical_values(src, tmp_path):
    plan = make_plan(src, ["a", "d"], {"d": "a + 1"})
    pe.export_parquet(plan, tmp_path / "p.parquet", writer="polars")
    pe.export_parquet(plan, tmp_path / "q.parquet", writer="pyarrow")
    p, q = pq.read_table(tmp_path / "p.parquet"), pq.read_table(tmp_path / "q.parquet")
    assert p.schema.equals(q.schema)  # NaN != NaN for Table.equals, so the values are compared as arrays
    for name in p.column_names:
        np.testing.assert_array_equal(p.column(name).to_numpy(), q.column(name).to_numpy())


@pytest.mark.parametrize("writer", WRITERS)
def test_empty_range_is_written(src, tmp_path, writer):
    res_ = pe.export_parquet(make_plan(src, ["a"], rows=(5, 5)), tmp_path / "o.parquet", writer=writer)
    assert res_.rows == 0 and pq.read_table(tmp_path / "o.parquet").num_rows == 0


# -- cancel / failure inside the polars generator --------------------------------------------------------


def test_polars_path_cancel_inside_the_generator(src, tmp_path):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous")
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] > 6  # well inside the generator, after the first chunks were handed over

    with pytest.raises(pe.ExportCancelled):  # not polars' ComputeError
        pe.export_parquet(make_plan(src, ["a"], chunk_rows=3_000), out, cancelled=cancelled, overwrite=True)
    assert out.read_bytes() == b"previous" and not list(tmp_path.glob("*.part"))


def test_polars_path_failure_keeps_its_type(src, tmp_path, monkeypatch):
    real = pe.evaluate_expression
    calls = {"n": 0}

    def failing(expr, variables, reg=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ZeroDivisionError("boom")
        return real(expr, variables, reg)

    monkeypatch.setattr(pe, "evaluate_expression", failing)
    with pytest.raises(ZeroDivisionError):
        pe.export_parquet(make_plan(src, ["d"], {"d": "a + 1"}, chunk_rows=3_000), tmp_path / "o.parquet")
    assert list(tmp_path.glob("o*")) == []


# -- chunk size (57.9.3) and memory estimate (57.9.5) --------------------------------------------------------


def test_auto_chunk_rows_shrinks_with_width_and_stays_in_bounds(src):
    narrow = make_plan(src, ["a"], chunk_rows=None)
    derived = {f"d{i}": f"a + {i}" for i in range(300)}
    wide = make_plan(src, list(derived), derived, chunk_rows=None)
    assert narrow.chunk_rows == pe.MAX_AUTO_CHUNK_ROWS
    assert pe.MIN_AUTO_CHUNK_ROWS <= wide.chunk_rows < narrow.chunk_rows
    # about 8 bytes per output column and 8 per working array: ~256 MB per chunk
    assert wide.chunk_rows * (8 * 301 + 8 * 301) <= pe.CHUNK_TARGET_BYTES * 1.2
    explicit = make_plan(src, ["a"], chunk_rows=123)
    assert explicit.chunk_rows == 123  # an explicit size is kept


def test_auto_chunk_rows_lower_bound(src):
    derived = {f"d{i}": f"a + {i}" for i in range(3000)}
    assert make_plan(src, list(derived), derived, chunk_rows=None).chunk_rows == pe.MIN_AUTO_CHUNK_ROWS


def test_memory_estimate_counts_full_row_variables(src):
    plain = make_plan(src, ["d"], {"d": "a + 1"}, chunk_rows=1_000)
    full = make_plan(src, ["d"], {"d": "roll(a)"}, chunk_rows=1_000)  # unknown margin
    assert pe.split_by_margin(full)[1] == ["d"]
    e_plain, e_full = pe.estimate_export_memory(plain), pe.estimate_export_memory(full)
    assert e_full - e_plain >= N * 8  # the rows x 8 bytes of the all-row arrays
    assert e_plain >= 200 * res.MB
    narrow = pe.estimate_export_memory(make_plan(src, ["a"], chunk_rows=1_000))
    bigger_chunk = pe.estimate_export_memory(make_plan(src, ["a"], chunk_rows=5_000))
    assert bigger_chunk > narrow


def test_memory_estimate_does_not_charge_a_chunk_larger_than_the_export(src):
    small = make_plan(src, ["a"], chunk_rows=1_000_000, rows=(0, 1_000))
    exact = make_plan(src, ["a"], chunk_rows=1_000, rows=(0, 1_000))
    assert pe.estimate_export_memory(small) == pe.estimate_export_memory(exact)
    assert pe.estimate_export_memory(small) < 250 * res.MB  # not ~1M rows worth
    empty = make_plan(src, ["a"], chunk_rows=1_000_000, rows=(5, 5))
    assert pe.estimate_export_memory(empty) >= 200 * res.MB


def test_output_bytes_upper_bound(src):
    plan = make_plan(src, ["a", "cnt", "d"], {"d": "a"}, rows=(0, 1_000))
    assert pe.output_row_bytes(plan) == 8 + 8 + 4 + 8  # t, a, cnt(int32), d(float64)
    assert pe.estimate_output_bytes(plan) == 1_000 * 28


def test_memory_budget_is_forty_percent_of_physical(monkeypatch):
    monkeypatch.setattr(res, "physical_memory_bytes", lambda: 10 * res.GB)
    assert res.memory_budget_bytes() == int(4 * res.GB)
    monkeypatch.setattr(res, "physical_memory_bytes", lambda: None)
    assert res.memory_budget_bytes() == 4 * res.GB


def test_os_probes_return_numbers_or_none(real_export_probes):
    real = real_export_probes
    for fn in (res.physical_memory_bytes, real["available_memory_bytes"]):
        value = fn()
        assert value is None or value > 0
    assert real["disk_free_bytes"]("/definitely/not/there") is not None  # falls back to an existing parent


# -- progress throttling (57.9.7) --------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_progress_is_throttled_but_phase_changes_and_the_end_pass(src, tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(pe, "_clock", clock)
    calls: list[tuple[int, int]] = []
    real_check = pe._Run.check

    def ticking_check(self):  # time passes only through the checks: 1 ms per check, far below 100 ms
        clock.now += 0.001
        real_check(self)

    monkeypatch.setattr(pe._Run, "check", ticking_check)
    plan = make_plan(src, ["f", "a"], {"f": "roll(a)"}, chunk_rows=1_000)  # full phase + 60 chunks
    pe.export_parquet(plan, tmp_path / "o.parquet", progress=lambda d, t: calls.append((d, t)))
    assert calls[0] == (0, pe.PROGRESS_FULL)  # the phase marker always passes
    assert (0, N) in calls  # the start of the write
    assert calls[-1] == (N, N)  # the last chunk always passes
    assert len(calls) <= 5, calls  # 60 chunks, but time barely advanced


def test_progress_is_sent_again_after_the_interval(src, tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(pe, "_clock", clock)
    real_check = pe._Run.check

    def slow_check(self):
        clock.now += 0.06  # two checks per chunk -> 120 ms per chunk > 100 ms
        real_check(self)

    monkeypatch.setattr(pe._Run, "check", slow_check)
    calls: list[tuple[int, int]] = []
    pe.export_parquet(make_plan(src, ["a"], chunk_rows=10_000), tmp_path / "o.parquet",
                      progress=lambda d, t: calls.append((d, t)))
    assert len(calls) >= 6  # start + about one per chunk
    done = [d for d, _ in calls]
    assert done == sorted(done)


# -- disk guard (57.9.6, 57.9.6a) --------------------------------------------------------------------------------


@pytest.mark.parametrize("writer", WRITERS)
def test_low_disk_fails_only_this_file_and_removes_the_part(src, tmp_path, monkeypatch, writer):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous")
    reads = {"n": 0}

    def free(path):
        reads["n"] += 1
        return 500 * res.GB if reads["n"] <= 3 else 50 * res.MB  # below the 100 MB margin after a few chunks

    monkeypatch.setattr(res, "disk_free_bytes", free)
    with pytest.raises(pe.DiskFull):
        pe.export_parquet(make_plan(src, ["a"], chunk_rows=3_000), out, overwrite=True, writer=writer)
    assert out.read_bytes() == b"previous" and not list(tmp_path.glob("*.part"))
    # the next file (disk is fine again) is written normally
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 500 * res.GB)
    done = pe.export_parquet(make_plan(src, ["a"]), tmp_path / "next.parquet", writer=writer)
    assert done.rows == N and pq.read_table(tmp_path / "next.parquet").num_rows == N


def test_a_finished_file_survives_a_later_disk_failure(src, tmp_path, monkeypatch):
    first = tmp_path / "first.parquet"
    pe.export_parquet(make_plan(src, ["a"]), first)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 50 * res.MB)
    with pytest.raises(pe.DiskFull):
        pe.export_parquet(make_plan(src, ["a"]), tmp_path / "second.parquet")
    assert pq.read_table(first).num_rows == N
    assert list(tmp_path.glob("second*")) == []


def test_polars_generator_ends_normally_on_cancel_and_failure(src, tmp_path, monkeypatch):
    """57.9.3: an exception raised inside the generator lets polars' writer thread create the `.part`
    up to ~34 ms later, so the generator must end normally and the stored exception is raised after
    the sink returned. No exception may escape the generator; the `.part` stays absent."""
    escaped: list[BaseException] = []
    real_register = polars.io.plugins.register_io_source

    def spying_register(io_source, schema):
        def wrapped(*args, **kwargs):
            try:
                yield from io_source(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001
                escaped.append(e)
                raise

        return real_register(io_source=wrapped, schema=schema)

    monkeypatch.setattr(polars.io.plugins, "register_io_source", spying_register)
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] > 6

    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(make_plan(src, ["a"], chunk_rows=3_000), tmp_path / "o.parquet", cancelled=cancelled)
    assert escaped == []
    time.sleep(0.2)  # long enough for a late writer thread to show up
    assert list(tmp_path.glob("o*")) == []


def test_polars_failure_before_the_first_chunk_starts_no_writer(src, tmp_path, monkeypatch):
    """A failure of the guard before the sink runs (here: no room at all) must not start polars."""
    started: list[int] = []
    real_register = polars.io.plugins.register_io_source
    monkeypatch.setattr(
        polars.io.plugins, "register_io_source", lambda **kw: (started.append(1), real_register(**kw))[1]
    )
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 50 * res.MB)
    with pytest.raises(pe.DiskFull):
        pe.export_parquet(make_plan(src, ["a"]), tmp_path / "o.parquet")
    assert started == [] and list(tmp_path.glob("o*")) == []


def test_cancel_before_the_sink_starts_no_writer(src, tmp_path, monkeypatch):
    started: list[int] = []
    real_register = polars.io.plugins.register_io_source
    monkeypatch.setattr(
        polars.io.plugins, "register_io_source", lambda **kw: (started.append(1), real_register(**kw))[1]
    )
    calls = {"n": 0}

    def cancelled():  # the first checks pass; the one right before the sink says cancel
        calls["n"] += 1
        return calls["n"] >= 3

    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(make_plan(src, ["a"]), tmp_path / "o.parquet", cancelled=cancelled)
    assert started == [] and list(tmp_path.glob("o*")) == []


# -- memory guard (57.9.6a) ----------------------------------------------------------------------------------------


@pytest.fixture
def every_chunk(monkeypatch):
    monkeypatch.setattr(pe, "MEMORY_PROBE_INTERVAL", 0.0)


@pytest.mark.parametrize("writer", WRITERS)
def test_memory_pressure_halves_the_chunk(src, tmp_path, monkeypatch, every_chunk, writer):
    monkeypatch.setattr(res, "available_memory_bytes", lambda: int(1.5 * res.GB))  # below 2 GB, above 1 GB
    plan = make_plan(src, ["a"], chunk_rows=40_000)
    out = tmp_path / "o.parquet"
    res_ = pe.export_parquet(plan, out, writer=writer)
    assert res_.chunk_halvings == 1  # once per drop below the line, not once per probe
    meta = pq.read_metadata(out)
    sizes = [meta.row_group(i).num_rows for i in range(meta.num_row_groups)]
    assert sum(sizes) == N and sizes[0] == 40_000 and max(sizes[1:]) <= 20_000  # later chunks are smaller
    np.testing.assert_array_equal(pq.read_table(out).column("a").to_numpy(), pq.read_table(src).column("a").to_numpy())


def test_chunk_halves_once_per_drop_and_grows_back(src, tmp_path, monkeypatch, every_chunk):
    plan = make_plan(src, ["a"], chunk_rows=40_000)
    run = pe._Run(plan, lambda: False)
    level = {"v": int(1.5 * res.GB)}
    monkeypatch.setattr(res, "available_memory_bytes", lambda: level["v"])
    part = tmp_path / "o.parquet.part"
    for _ in range(4):  # four probes while memory stays low: one halving
        run.gate(part)
    assert run.chunk_rows == 20_000 and run.halvings == 1
    level["v"] = 64 * res.GB  # memory is back: grow toward the planned size, never beyond
    run.gate(part)
    assert run.chunk_rows == 40_000
    run.gate(part)
    assert run.chunk_rows == 40_000
    level["v"] = int(1.5 * res.GB)  # a second drop halves again
    run.gate(part)
    run.gate(part)
    assert run.chunk_rows == 20_000 and run.halvings == 2


def test_chunk_is_never_halved_below_the_floor(src, tmp_path, monkeypatch, every_chunk):
    monkeypatch.setattr(res, "available_memory_bytes", lambda: int(1.5 * res.GB))
    res_ = pe.export_parquet(make_plan(src, ["a"], chunk_rows=3_000), tmp_path / "o.parquet")
    assert res_.chunk_halvings == 0  # 3000 is already below MIN_CHUNK_ROWS: nothing to shrink


def test_free_memory_enough_changes_nothing(src, tmp_path, every_chunk):
    res_ = pe.export_parquet(make_plan(src, ["a"], chunk_rows=40_000), tmp_path / "o.parquet")
    assert res_.chunk_halvings == 0 and res_.memory_wait == 0


@pytest.mark.parametrize("writer", WRITERS)
def test_very_low_memory_pauses_and_resumes(src, tmp_path, monkeypatch, every_chunk, writer):
    levels = iter([64 * res.GB, res.GB // 2, res.GB // 2, res.GB // 2, 64 * res.GB])
    sleeps: list[float] = []

    def avail():
        return next(levels, 64 * res.GB)

    monkeypatch.setattr(res, "available_memory_bytes", avail)
    monkeypatch.setattr(pe, "_sleep", sleeps.append)
    out = tmp_path / "o.parquet"
    res_ = pe.export_parquet(make_plan(src, ["a"], chunk_rows=10_000), out, writer=writer)
    assert len(sleeps) == 3  # waited while below 1 GB, then went on
    assert pq.read_table(out).num_rows == N and res_.rows == N


def test_memory_that_does_not_come_back_fails_the_file(src, tmp_path, monkeypatch, every_chunk):
    clock = FakeClock()
    monkeypatch.setattr(pe, "_clock", clock)
    monkeypatch.setattr(pe, "_sleep", lambda s: setattr(clock, "now", clock.now + 10))  # 10 s per wait
    levels = iter([64 * res.GB])
    monkeypatch.setattr(res, "available_memory_bytes", lambda: next(levels, res.GB // 4))
    with pytest.raises(pe.MemoryLow):
        pe.export_parquet(make_plan(src, ["a"], chunk_rows=10_000), tmp_path / "o.parquet")
    assert list(tmp_path.glob("o*")) == []


def test_cancel_while_waiting_for_memory(src, tmp_path, monkeypatch, every_chunk):
    levels = iter([64 * res.GB])
    monkeypatch.setattr(res, "available_memory_bytes", lambda: next(levels, res.GB // 4))
    state = {"waits": 0}

    def sleep(s):
        state["waits"] += 1

    monkeypatch.setattr(pe, "_sleep", sleep)
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(make_plan(src, ["a"], chunk_rows=10_000), tmp_path / "o.parquet",
                          cancelled=lambda: state["waits"] >= 2)
    assert list(tmp_path.glob("o*")) == []


def test_unreadable_memory_skips_the_guard(src, tmp_path, monkeypatch, every_chunk):
    monkeypatch.setattr(res, "available_memory_bytes", lambda: None)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: None)
    assert pe.export_parquet(make_plan(src, ["a"], chunk_rows=10_000), tmp_path / "o.parquet").rows == N


# -- disk guard: stop only when the rest does not fit (57.9.6a) ---------------------------------------


def test_a_nearly_full_drive_still_takes_a_small_export(src, tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 800 * res.MB)  # less than the old fixed 1 GB stop
    plan = make_plan(src, ["a"], rows=(0, 1_000))
    assert pe.export_parquet(plan, tmp_path / "o.parquet").rows == 1_000


def test_disk_stop_is_bytes_still_to_write_plus_the_margin(src, tmp_path):
    plan = make_plan(src, ["a"], chunk_rows=10_000)
    run = pe._Run(plan, lambda: False)
    part = tmp_path / "o.parquet.part"
    part.write_bytes(b"x" * 1_000)  # 1,000 bytes for 10,000 rows -> 0.1 byte per row
    needed = int((N - 10_000) * 1_000 / 10_000)  # the other 50,000 rows
    assert run._disk_still_needed(part, 10_000) == needed
    import unittest.mock as mock

    with mock.patch.object(res, "disk_free_bytes", lambda p: needed + res.DISK_MARGIN + 1):
        run.gate(part, 10_000)
    with mock.patch.object(res, "disk_free_bytes", lambda p: needed + res.DISK_MARGIN - 1), pytest.raises(pe.DiskFull):
        run.gate(part, 10_000)


def test_zero_rows_are_written_even_with_little_room_on_the_pyarrow_path(src, tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 50 * res.MB)  # below the margin: any gate would fail
    out = tmp_path / "o.parquet"
    res_ = pe.export_parquet(make_plan(src, ["a"], rows=(5, 5)), out)
    assert res_.rows == 0 and pq.read_table(out).num_rows == 0


# -- the checks before the start (57.9.6a) ----------------------------------------------------------------------------


def item(path, max_bytes, memory=0):
    return res.PreflightItem(str(path), max_bytes, memory)


def test_preflight_disk_fits_and_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 100 * res.GB)
    assert res.preflight([item(tmp_path / "a.parquet", 99 * res.GB)]).ok
    report = res.preflight([item(tmp_path / "a.parquet", 100 * res.GB - 50 * res.MB)])  # inside the margin
    assert not report.ok and report.disk_short[0][1:] == (100 * res.GB - 50 * res.MB, 100 * res.GB)


def test_preflight_margin_is_100_mb_not_a_fixed_gigabyte(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 800 * res.MB)  # a nearly full USB drive
    assert res.preflight([item(tmp_path / "a", 1 * res.MB)]).ok  # a small export is fine
    assert res.preflight([item(tmp_path / "a", 700 * res.MB)]).ok
    assert not res.preflight([item(tmp_path / "a", 750 * res.MB)]).ok


def test_preflight_sums_files_on_one_volume_and_separates_volumes(tmp_path, monkeypatch):
    free = {"v1": 100 * res.GB, "v2": 100 * res.GB}
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: free[p.name if hasattr(p, "name") else "v1"])
    one, two = tmp_path / "v1", tmp_path / "v2"
    one.mkdir()
    two.mkdir()
    monkeypatch.setattr(res, "_volume_key", lambda folder: folder.name)
    # 60 + 60 on the same volume does not fit; 60 on each of two volumes does
    assert not res.preflight([item(one / "a", 60 * res.GB), item(one / "b", 60 * res.GB)]).ok
    assert res.preflight([item(one / "a", 60 * res.GB), item(two / "b", 60 * res.GB)]).ok
    report = res.preflight([item(one / "a", 60 * res.GB), item(one / "b", 60 * res.GB), item(two / "c", 1)])
    assert [Path_name(f) for f, *_ in report.disk_short] == ["v1"]


def Path_name(folder):  # noqa: N802
    return folder.replace("\\", "/").rsplit("/", 1)[-1]


def test_preflight_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 8 * res.GB)
    monkeypatch.setattr(res, "physical_memory_bytes", lambda: 16 * res.GB)
    assert res.preflight([item(tmp_path / "a", 1, 7 * res.GB)]).ok
    report = res.preflight([item(tmp_path / "a", 1, 9 * res.GB)])
    assert not report.ok and report.memory_short and not report.memory_over_physical
    assert res.preflight([item(tmp_path / "a", 1, 20 * res.GB)]).memory_over_physical


def test_preflight_skips_what_it_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: None)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: None)
    monkeypatch.setattr(res, "physical_memory_bytes", lambda: None)
    assert res.preflight([item(tmp_path / "a", 10**15, 10**15)]).ok


def test_can_start_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 50 * res.GB)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 8 * res.GB)
    assert res.can_start_new_file(10 * res.GB, tmp_path) is None
    assert res.can_start_new_file(49 * res.GB, tmp_path) is None  # 50 > 49 + 0.1
    assert res.can_start_new_file(50 * res.GB - 50 * res.MB, tmp_path) == "disk"
    monkeypatch.setattr(res, "available_memory_bytes", lambda: int(1.9 * res.GB))
    assert res.can_start_new_file(1, tmp_path) == "memory"


def test_preflight_item_uses_the_plan(src, tmp_path):
    plan = make_plan(src, ["a"], rows=(0, 1_000))
    it = pe.preflight_item(plan, tmp_path / "o.parquet")
    assert it.max_bytes == 1_000 * 16 and it.memory_bytes == pe.estimate_export_memory(plan)
