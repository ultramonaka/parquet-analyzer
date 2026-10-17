"""core/parquet_export.py (export.ja.md 57.2, 57.5): chunked computation, types, metadata,
cancel/failure cleanup. No Qt."""

from __future__ import annotations

import datetime
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import evaluate_expression
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.core.numeric import to_numeric

N = 3000
TZ = "Asia/Tokyo"


@pytest.fixture(autouse=True, params=["polars", "pyarrow"])
def writer(request, monkeypatch):
    """Every test here runs with both writers (57.9.9): `export_parquet` defaults to this writer
    ("polars" = the streaming sink when the column types survive it, else pyarrow)."""
    real = pe.export_parquet

    def run(*args, **kwargs):
        kwargs.setdefault("writer", request.param)
        return real(*args, **kwargs)

    monkeypatch.setattr(pe, "export_parquet", run)
    return request.param


@pytest.fixture
def src(tmp_path):
    rng = np.random.default_rng(3)
    a = rng.normal(size=N) * 10
    b = rng.normal(size=N)
    a[[0, 5, 700, N - 1]] = np.nan
    b[[3, 1500]] = np.nan
    times = np.datetime64("2026-01-01T00:00:00", "us") + np.arange(N, dtype="int64") * np.timedelta64(1_000_000, "us")
    table = pa.table(
        {
            "t": pa.array(times, type=pa.timestamp("us", tz=TZ)),
            "cnt": pa.array(np.arange(N, dtype=np.int32)),
            "f32": pa.array(rng.normal(size=N).astype(np.float32)),
            "a": a,
            "b": b,
            "unused": rng.normal(size=N),
        }
    )
    path = tmp_path / "src.parquet"
    pq.write_table(table, path, row_group_size=300)
    return path


@pytest.fixture
def registry():
    reg = FunctionRegistry()
    reg.user_functions["scaled"] = UserFunction("scaled", ["x"], [("k", 2.0)], "x * k + abs(x)")
    reg.user_functions["smooth"] = UserFunction("smooth", ["x"], [], "rolling_mean(delta(x), 4)")
    # a window given by a constant: the margin is not known statically -> computed over all rows
    reg.user_functions["roll"] = UserFunction("roll", ["x"], [("w", 5.0)], "rolling_mean(x, w)")
    reg.maps["curve"] = LookupTable("curve", "curve", [-30.0, 0.0, 30.0], [0.0, 5.0, 1.0])
    return reg


FILE_COLUMNS = {"a": "a", "b": "b", "cnt": "cnt", "f32": "f32", "unused": "unused"}


def make_plan(src, registry, signals, derived=None, rows=(0, N), chunk_rows=211, **kw):
    cols = [pe.ExportColumn(n, FILE_COLUMNS.get(n)) if n not in (derived or {}) else pe.ExportColumn(n) for n in signals]
    return pe.build_plan(
        source_path=str(src),
        total_rows=N,
        row_range=rows,
        time=pe.ExportColumn("t", "t"),
        signals=cols,
        derived_expressions=derived or {},
        file_columns=FILE_COLUMNS,
        registry=registry,
        chunk_rows=chunk_rows,
        **kw,
    )


def whole(src, registry, expr):
    ds = ParquetDataSource(src)
    arrays = {c: to_numeric(v) for c, v in ds.read_columns(["a", "b"]).items()}
    return np.asarray(evaluate_expression(expr, arrays, registry), dtype=np.float64)


EXPRS = [
    "a + b",
    "delta(a)",
    "rolling_mean(a, 5)",
    "rolling_nanmean(a, 7) + delta(b)",
    "trailing_mean(a, 5)",
    "delta(rolling_mean(a, 6))",
    "where(a > b, a, delta(b))",
    "a if b > 0 else delta(a)",
    "0 < a < 5 and not isnan(b)",
    "scaled(a, k=3)",
    "smooth(b)",
    "curve(a)",
    "delta(curve(a)) + scaled(b)",
    "rolling_mean(a, 100000)",
]


@pytest.mark.parametrize("rows", [(0, N), (137, 2501), (1500, 1501), (2900, N)], ids=str)
@pytest.mark.parametrize("expr", EXPRS)
def test_chunked_equals_whole_array(src, registry, tmp_path, expr, rows):
    plan = make_plan(src, registry, ["d"], {"d": expr}, rows=rows, chunk_rows=211)
    out = tmp_path / "o.parquet"
    res = pe.export_parquet(plan, out)
    got = pq.read_table(out).column("d").to_numpy()
    np.testing.assert_array_equal(got, whole(src, registry, expr)[rows[0] : rows[1]])
    assert res.rows == rows[1] - rows[0]
    assert res.full_computed == ()


def test_unknown_margin_is_computed_over_all_rows_and_matches(src, registry, tmp_path):
    derived = {"r": "roll(a)", "r2": "r + 1", "ok": "delta(b)"}
    plan = make_plan(src, registry, ["r2", "ok"], derived, rows=(400, 2600), chunk_rows=300)
    res = pe.export_parquet(plan, tmp_path / "o.parquet")
    assert set(res.full_computed) == {"r", "r2"}  # r2 depends on the unknown r
    t = pq.read_table(tmp_path / "o.parquet")
    r_full = whole(src, registry, "roll(a)")
    np.testing.assert_array_equal(t.column("r2").to_numpy(), (r_full + 1)[400:2600])
    np.testing.assert_array_equal(t.column("ok").to_numpy(), whole(src, registry, "delta(b)")[400:2600])


def test_chunked_variable_using_a_full_variable_is_exact(src, registry, tmp_path):
    # `d` has a known margin and is used by the unknown-margin `roll(d)` -> `d` is computed in full too
    derived = {"d": "delta(a)", "r": "roll(d)", "z": "d + 1"}
    plan = make_plan(src, registry, ["z", "r"], derived, chunk_rows=250)
    res = pe.export_parquet(plan, tmp_path / "o.parquet")
    assert set(res.full_computed) == {"d", "r"}
    t = pq.read_table(tmp_path / "o.parquet")
    d_full = whole(src, registry, "delta(a)")
    np.testing.assert_array_equal(t.column("z").to_numpy(), d_full + 1)


def test_types_are_kept_and_derived_are_float64(src, registry, tmp_path):
    plan = make_plan(src, registry, ["cnt", "f32", "a", "d"], {"d": "a + 1"})
    pe.export_parquet(plan, tmp_path / "o.parquet")
    t = pq.read_table(tmp_path / "o.parquet")
    assert t.column_names == ["t", "cnt", "f32", "a", "d"]  # the time axis first
    assert t.schema.field("t").type == pa.timestamp("us", tz=TZ)
    assert t.schema.field("cnt").type == pa.int32()
    assert t.schema.field("f32").type == pa.float32()
    assert t.schema.field("a").type == pa.float64() and t.schema.field("d").type == pa.float64()
    original = pq.read_table(src)
    assert t.column("t").equals(original.column("t"))
    assert t.column("cnt").equals(original.column("cnt"))
    assert t.column("f32").equals(original.column("f32"))
    # the tz-aware time axis keeps its wall-clock time zone
    assert t.column("t")[0].as_py() == original.column("t")[0].as_py()


def test_raw_columns_in_a_sub_range(src, registry, tmp_path):
    plan = make_plan(src, registry, ["cnt"], rows=(100, 900), chunk_rows=300)
    pe.export_parquet(plan, tmp_path / "o.parquet")
    t = pq.read_table(tmp_path / "o.parquet")
    assert t.column("cnt").to_pylist() == list(range(100, 900))


def test_alias_names_map_to_the_source_column(src, registry, tmp_path):
    file_columns = {**FILE_COLUMNS, "alias_a": "a"}
    plan = pe.build_plan(
        source_path=str(src), total_rows=N, row_range=(0, N), time=pe.ExportColumn("t", "t"),
        signals=[pe.ExportColumn("alias_a", "a"), pe.ExportColumn("d")],
        derived_expressions={"d": "alias_a * 2"}, file_columns=file_columns, registry=registry, chunk_rows=500,
    )
    pe.export_parquet(plan, tmp_path / "o.parquet")
    t = pq.read_table(tmp_path / "o.parquet")
    assert t.column_names == ["t", "alias_a", "d"]
    np.testing.assert_array_equal(t.column("alias_a").to_numpy(), t.column("d").to_numpy() / 2)


# -- only what is needed is computed -------------------------------------------------------------


def test_unselected_derived_variables_are_not_computed(src, registry, tmp_path, monkeypatch):
    seen: list[str] = []
    real = pe.evaluate_expression

    def counting(expr, variables, reg=None):
        seen.append(expr)
        return real(expr, variables, reg)

    monkeypatch.setattr(pe, "evaluate_expression", counting)
    reads: list[tuple] = []
    real_read = pe._Run.read_numeric

    def recording(self, files, lo, hi):
        reads.append(tuple(sorted(files)))
        return real_read(self, files, lo, hi)

    monkeypatch.setattr(pe._Run, "read_numeric", recording)
    derived = {"wanted": "a + 1", "other": "b * 2", "dep": "delta(a)", "chain": "dep * 2"}
    plan = make_plan(src, registry, ["wanted"], derived, chunk_rows=1000)
    pe.export_parquet(plan, tmp_path / "o.parquet")
    assert set(seen) == {"a + 1"}
    assert len(seen) == 3  # one evaluation per chunk, nothing else
    assert {c for cols in reads for c in cols} == {"a"}  # b / unused are never read

    seen.clear()
    plan = make_plan(src, registry, ["chain"], derived, chunk_rows=1000)
    assert [n for n, _ in plan.derived] == ["dep", "chain"]  # the chain is in dependency order
    pe.export_parquet(plan, tmp_path / "o2.parquet")
    assert set(seen) == {"delta(a)", "dep * 2"}


def test_cancel_during_the_last_chunk_does_not_publish(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] >= 3  # polled before the compute, after it, and once more before the publish

    plan = make_plan(src, registry, ["a"], chunk_rows=N)  # a single chunk
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(plan, out, cancelled=cancelled)
    assert not out.exists() and not (tmp_path / "o.parquet.part").exists()


def test_only_selected_columns_are_written(src, registry, tmp_path):
    plan = make_plan(src, registry, ["a"])
    pe.export_parquet(plan, tmp_path / "o.parquet")
    assert pq.read_table(tmp_path / "o.parquet").column_names == ["t", "a"]


def test_unknown_variable_is_an_error_at_planning(src, registry):
    with pytest.raises(pe.ExportError):
        make_plan(src, registry, ["d"], {"d": "nope + 1"})


# -- row groups / metadata / progress -------------------------------------------------------------


def test_one_row_group_per_chunk(src, registry, tmp_path):
    plan = make_plan(src, registry, ["a", "d"], {"d": "delta(a)"}, rows=(0, 1000), chunk_rows=300)
    res = pe.export_parquet(plan, tmp_path / "o.parquet")
    meta = pq.read_metadata(tmp_path / "o.parquet")
    assert meta.num_row_groups == 4 == res.row_groups  # 300 + 300 + 300 + 100
    assert [meta.row_group(i).num_rows for i in range(4)] == [300, 300, 300, 100]
    assert meta.row_group(0).column(0).compression == "ZSTD"


def test_metadata_content(src, registry, tmp_path):
    derived = {"d": "scaled(delta(a)) + curve(b)", "e": "a"}
    plan = make_plan(src, registry, ["d"], derived, rows=(10, 20), metadata={"view": "my view", "software": "Test v1"})
    pe.export_parquet(plan, tmp_path / "o.parquet")
    info = json.loads(pq.read_schema(tmp_path / "o.parquet").metadata[pe.METADATA_KEY])
    assert info["source"] == str(src.resolve()) and info["source_name"] == "src.parquet"
    assert info["view"] == "my view" and info["software"] == "Test v1"
    assert info["time_column"] == "t"
    assert info["range"]["rows"] == [10, 20]
    assert info["range"]["start"].startswith("2026-01-01 09:00:10")
    assert info["range"]["end"].startswith("2026-01-01 09:00:19")
    assert info["derived"] == {"d": "scaled(delta(a)) + curve(b)"}  # `e` is not used
    assert list(info["functions"]) == ["scaled"] and info["functions"]["scaled"]["params"] == ["x"]
    assert list(info["maps"]) == ["curve"] and info["maps"]["curve"]["kind"] == "curve"
    assert info["range"]["start"].endswith("+0900")  # the time zone offset is kept
    created = datetime.datetime.fromisoformat(info["created"])
    assert created.tzinfo is not None


@pytest.mark.parametrize("tz", [None, TZ], ids=["naive", "tz"])
def test_nanosecond_time_axis(tmp_path, registry, tz):
    n = 1000
    ns = 1_700_000_000_000_000_000 + np.arange(n, dtype="int64") * 1_000_000_123  # sub-microsecond digits
    typ = pa.timestamp("ns", tz=tz)
    a = np.linspace(0, 1, n)
    path = tmp_path / "ns.parquet"
    pq.write_table(pa.table({"t": pa.array(ns, type=pa.int64()).cast(typ), "a": a}), path, row_group_size=100)
    plan = pe.build_plan(
        source_path=str(path), total_rows=n, row_range=(0, n), time=pe.ExportColumn("t", "t"),
        signals=[pe.ExportColumn("a", "a"), pe.ExportColumn("d")], derived_expressions={"d": "delta(a)"},
        file_columns={"a": "a"}, registry=registry, chunk_rows=300,
    )
    out = tmp_path / "o.parquet"
    pe.export_parquet(plan, out)
    t = pq.read_table(out)
    assert t.schema.field("t").type == typ
    assert t.column("t").equals(pq.read_table(path).column("t"))
    info = json.loads(pq.read_schema(out).metadata[pe.METADATA_KEY])
    assert info["range"]["start"].startswith("2023-11-14") or info["range"]["start"].startswith("2023-11-15")
    assert ".000122877" in info["range"]["end"]  # the nanoseconds are in the text
    if tz:
        assert info["range"]["start"].endswith("+0900")


def test_cancel_during_the_full_phase_is_prompt_and_leaves_no_part(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    reads = {"n": 0}
    calls: list[tuple[int, int]] = []
    plan = make_plan(src, registry, ["r"], {"r": "roll(a)"}, chunk_rows=100)  # unknown margin: full phase, 30 blocks
    state = {"stop": False}

    def cancelled():
        return state["stop"]

    def progress(done, total):
        calls.append((done, total))

    orig = pe._Run.read_numeric

    def read_and_stop(self, files, lo, hi):
        reads["n"] += 1
        if reads["n"] == 3:
            state["stop"] = True
        return orig(self, files, lo, hi)

    pe._Run.read_numeric = read_and_stop
    try:
        with pytest.raises(pe.ExportCancelled):
            pe.export_parquet(plan, out, progress=progress, cancelled=cancelled)
    finally:
        pe._Run.read_numeric = orig
    assert reads["n"] == 3  # stopped at the next block, not after reading all 30
    assert calls == [(0, -1)]  # the full phase is announced with total < 0
    assert list(tmp_path.glob("o*")) == []


def test_full_phase_blocks_equal_whole_read(src, registry, tmp_path):
    plan = make_plan(src, registry, ["r"], {"r": "roll(a)"}, chunk_rows=77)
    pe.export_parquet(plan, tmp_path / "o.parquet")
    np.testing.assert_array_equal(
        pq.read_table(tmp_path / "o.parquet").column("r").to_numpy(), whole(src, registry, "roll(a)")
    )


def test_progress_is_monotonic_and_ends_at_100_percent(src, registry, tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "PROGRESS_INTERVAL", 0.0)  # no throttling here (see test_parquet_export_speed)
    calls: list[tuple[int, int]] = []
    plan = make_plan(src, registry, ["a"], rows=(100, 1000), chunk_rows=250)
    pe.export_parquet(plan, tmp_path / "o.parquet", progress=lambda d, t: calls.append((d, t)))
    done = [d for d, _ in calls]
    assert done == sorted(done) and done[0] == 0 and calls[-1] == (900, 900)
    assert {t for _, t in calls} == {900}
    assert len(calls) == 1 + 4  # start + one per chunk


def test_empty_range_writes_an_empty_file(src, registry, tmp_path):
    plan = make_plan(src, registry, ["a", "d"], {"d": "a + 1"}, rows=(500, 500))
    res = pe.export_parquet(plan, tmp_path / "o.parquet")
    t = pq.read_table(tmp_path / "o.parquet")
    assert res.rows == 0 and t.num_rows == 0 and t.column_names == ["t", "a", "d"]


# -- cancel / failure ------------------------------------------------------------------------------


def test_cancel_removes_part_and_keeps_an_existing_output(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous content")
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 3

    plan = make_plan(src, registry, ["a", "d"], {"d": "delta(a)"}, chunk_rows=100)
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(plan, out, cancelled=cancelled, overwrite=True)
    assert out.read_bytes() == b"previous content"
    assert not (tmp_path / "o.parquet.part").exists()


def test_cancel_creates_no_output_file(src, registry, tmp_path):
    out = tmp_path / "fresh.parquet"
    plan = make_plan(src, registry, ["a"], chunk_rows=100)
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(plan, out, cancelled=lambda: True)
    assert list(tmp_path.glob("fresh*")) == []


def test_failure_in_the_middle_leaves_no_part_file(src, registry, tmp_path, monkeypatch):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous content")
    real = pe.evaluate_expression
    calls = {"n": 0}

    def failing(expr, variables, reg=None):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("boom")
        return real(expr, variables, reg)

    monkeypatch.setattr(pe, "evaluate_expression", failing)
    plan = make_plan(src, registry, ["d"], {"d": "a + 1"}, chunk_rows=100)
    with pytest.raises(RuntimeError):
        pe.export_parquet(plan, out, overwrite=True)
    assert not (tmp_path / "o.parquet.part").exists()
    assert out.read_bytes() == b"previous content"


def test_unwritable_target_fails_without_part(src, registry, tmp_path):
    plan = make_plan(src, registry, ["a"])
    with pytest.raises(OSError):
        pe.export_parquet(plan, tmp_path / "no_such_dir" / "o.parquet")
    assert list(tmp_path.rglob("*.part")) == []


def test_target_equal_to_source_is_refused(src, registry):
    plan = make_plan(src, registry, ["a"])
    before = src.read_bytes()
    with pytest.raises(pe.ExportError):
        pe.export_parquet(plan, src)
    assert src.read_bytes() == before


# -- overwrite protection (export 57.8.1) ------------------------------------------------------------


def test_existing_target_is_refused_without_overwrite(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous content")
    plan = make_plan(src, registry, ["a"])
    with pytest.raises(pe.ExportError):
        pe.export_parquet(plan, out)
    assert out.read_bytes() == b"previous content"
    assert list(tmp_path.glob("*.part")) == []


def test_existing_target_is_replaced_with_overwrite(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous content")
    res = pe.export_parquet(make_plan(src, registry, ["a"]), out, overwrite=True)
    assert res.rows == N and pq.read_table(out).num_rows == N
    assert list(tmp_path.glob("*.part")) == []


def test_source_is_refused_even_with_overwrite(src, registry):
    before = src.read_bytes()
    with pytest.raises(pe.ExportError):
        pe.export_parquet(make_plan(src, registry, ["a"]), src, overwrite=True)
    assert src.read_bytes() == before


def test_overwrite_cancelled_keeps_the_original(src, registry, tmp_path):
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous content")
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(make_plan(src, registry, ["a"]), out, cancelled=lambda: True, overwrite=True)
    assert out.read_bytes() == b"previous content"
    assert list(tmp_path.glob("*.part")) == []


def test_the_parametrized_writer_is_the_one_used(src, registry, tmp_path, writer):
    res = pe.export_parquet(make_plan(src, registry, ["a", "cnt"]), tmp_path / "o.parquet")
    assert res.writer == writer
