"""Export stage B2, core side (export.ja.md 57.8.3, 57.9.6a (c), 57.9.6b). No Qt."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import export_resources as res
from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.functions import FunctionRegistry
from parquet_analyzer.io.settings import Settings
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View

N = 5000
WRITERS = ["polars", "pyarrow"]


@pytest.fixture
def src(tmp_path):
    t = np.arange(N, dtype="int64")
    a = np.arange(N, dtype="float64")
    a[3] = np.nan
    a[4] = np.inf
    big = np.full(N, 1e30)
    big[0] = 3e38  # finite, but 3e38 * 2 does not fit float32
    table = pa.table({
        "t": pa.array(t), "u": pa.array(t * 10.0), "a": a, "big": big, "cnt": pa.array(t.astype(np.int32)),
        "f32": pa.array(a.astype(np.float32)), "label": pa.array([f"s{i % 3}" for i in range(N)]),
    })
    path = tmp_path / "s.parquet"
    pq.write_table(table, path, row_group_size=1000)
    return path


COLS = {"a": "a", "big": "big", "cnt": "cnt", "f32": "f32"}


def make_plan(src, signals, derived=None, dtype="float64", chunk_rows=1200):
    cols = [pe.ExportColumn(n) if n in (derived or {}) else pe.ExportColumn(n, COLS[n]) for n in signals]
    return pe.build_plan(
        source_path=str(src), total_rows=N, row_range=(0, N), time=pe.ExportColumn("t", "t"), signals=cols,
        derived_expressions=derived or {}, file_columns=COLS, registry=FunctionRegistry(), chunk_rows=chunk_rows,
        derived_dtype=dtype,
    )


# -- 57.9.6b: float32 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("writer", WRITERS)
def test_float32_derived_variables_and_raw_columns_keep_their_types(src, tmp_path, writer):
    plan = make_plan(src, ["a", "cnt", "f32", "d"], {"d": "a * 0.1"}, dtype="float32")
    out = tmp_path / "o.parquet"
    result = pe.export_parquet(plan, out, writer=writer)
    schema = pq.read_schema(out)
    assert [str(schema.field(n).type) for n in ("t", "a", "cnt", "f32", "d")] == [
        "int64", "double", "int32", "float", "float",
    ]
    table = pq.read_table(out)
    expected = (np.arange(N, dtype="float64") * 0.1)
    expected[3] = np.nan
    expected[4] = np.inf
    np.testing.assert_array_equal(table["d"].to_numpy(), expected.astype(np.float32))  # NaN / Inf kept
    assert result.float32_overflow == ()
    meta = json.loads(schema.metadata[pe.METADATA_KEY])
    assert meta["derived_dtype"] == "float32"


@pytest.mark.parametrize("writer", WRITERS)
def test_default_is_float64_and_recorded(src, tmp_path, writer):
    out = tmp_path / "o.parquet"
    pe.export_parquet(make_plan(src, ["d"], {"d": "a * 0.1"}), out, writer=writer)
    assert str(pq.read_schema(out).field("d").type) == "double"
    assert json.loads(pq.read_schema(out).metadata[pe.METADATA_KEY])["derived_dtype"] == "float64"


@pytest.mark.parametrize("writer", WRITERS)
def test_values_too_large_for_float32_become_inf_and_are_reported(src, tmp_path, writer):
    plan = make_plan(src, ["d", "ok"], {"d": "big * 2", "ok": "a + 1"}, dtype="float32")
    out = tmp_path / "o.parquet"
    result = pe.export_parquet(plan, out, writer=writer)
    d = pq.read_table(out)["d"].to_numpy()
    assert np.isinf(d[0]) and np.isfinite(d[1]) and result.float32_overflow == ("d",)
    # the same data in float64 is exact and reports nothing
    r64 = pe.export_parquet(make_plan(src, ["d"], {"d": "big * 2"}), tmp_path / "o64.parquet", writer=writer)
    assert r64.float32_overflow == () and pq.read_table(tmp_path / "o64.parquet")["d"][0].as_py() == 6e38


def test_a_nan_or_inf_input_is_not_an_overflow(src, tmp_path):
    result = pe.export_parquet(make_plan(src, ["d"], {"d": "a * 1"}, dtype="float32"), tmp_path / "o.parquet")
    assert result.float32_overflow == ()


def test_float32_halves_the_size_estimates(src):
    p64 = make_plan(src, ["d1", "d2"], {"d1": "a + 1", "d2": "a + 2"})
    p32 = make_plan(src, ["d1", "d2"], {"d1": "a + 1", "d2": "a + 2"}, dtype="float32")
    assert pe.output_row_bytes(p64) - pe.output_row_bytes(p32) == 8  # two columns, 4 bytes each
    assert pe.estimate_output_bytes(p32) < pe.estimate_output_bytes(p64)
    assert pe.preflight_item(p32, "x").max_bytes == pe.estimate_output_bytes(p32)


def test_unknown_dtype_is_refused(src):
    with pytest.raises(pe.ExportError):
        make_plan(src, ["a"], dtype="float16")


# -- 57.8.3: the time axis ----------------------------------------------------------------------------


def test_time_candidates_are_numeric_and_temporal_columns():
    schema = pa.schema([
        ("t", pa.int64()), ("s", pa.string()), ("b", pa.bool_()), ("ts", pa.timestamp("us", tz="UTC")),
        ("d", pa.date32()), ("f", pa.float32()), ("l", pa.list_(pa.int32())),
    ])
    assert pe.time_candidates(schema) == ["t", "ts", "d", "f"]


def view_for(src, time_column="t", x_range=None, derived=()):
    return View(
        view_name="v", source=SourceDef(str(src), "absolute"),
        plots=[PlotDef("p", [SeriesDef("a", "#f00")])],
        derived_variables=[DerivedVariableDef(n, e) for n, e in derived], time_column=time_column, x_axis_range=x_range,
    )


def info_for(src, view, time_column=None):
    schema = pq.read_schema(src)
    return pe.view_export_info(view, list(schema.names), {}, FunctionRegistry(), time_column=time_column)


def test_a_chosen_column_becomes_the_first_output_column(src, tmp_path):
    view = view_for(src)
    info = info_for(src, view, time_column="u")
    assert info.time_name == "u" and info.time_source == "u" and info.time_problem is None
    plan = pe.plan_from_view(
        view, info, source_path=str(src), total_rows=N, selected=["a"], use_saved_range=False, view_file="v.json",
        software="t", registry=FunctionRegistry(),
    )
    out = tmp_path / "o.parquet"
    pe.export_parquet(plan, out)
    assert pq.read_schema(out).names == ["u", "a"]
    assert json.loads(pq.read_schema(out).metadata[pe.METADATA_KEY])["time_column"] == "u"


def test_the_default_is_the_views_own_time_axis(src):
    assert info_for(src, view_for(src)).time_name == "t"
    assert info_for(src, view_for(src), time_column=None).time_name == "t"


def test_a_chosen_column_the_file_lacks_is_a_time_problem(src):
    info = info_for(src, view_for(src), time_column="nope")
    assert info.time_problem is not None and info.time_problem.detail == "nope"
    with pytest.raises(pe.ExportError):
        pe.plan_from_view(
            view_for(src), info, source_path=str(src), total_rows=N, selected=["a"], use_saved_range=False,
            view_file="", software="t", registry=FunctionRegistry(),
        )


def test_the_saved_range_stays_in_the_views_units_when_another_column_is_the_output_axis(src, tmp_path):
    """The range's numbers belong to the view's own axis (t): it selects rows 20..50 whichever column is
    written first. (Searching the chosen column u = 10 t would silently give rows 2..5.)"""
    view = view_for(src, x_range=(20.0, 50.0))
    for time_column, first in ((None, list(range(20, 51))), ("u", [10.0 * i for i in range(20, 51)])):
        info = info_for(src, view, time_column=time_column)
        plan = pe.plan_from_view(
            view, info, source_path=str(src), total_rows=N, selected=["a"], use_saved_range=True, view_file="",
            software="t", registry=FunctionRegistry(),
        )
        out = tmp_path / f"{time_column}.parquet"
        pe.export_parquet(plan, out)
        table = pq.read_table(out)
        assert table.column_names[0] == (time_column or "t") and table.column(0).to_pylist() == first
        assert table["a"].to_pylist()[:2] == [20.0, 21.0]  # the same rows


def test_idx_axis_with_a_range_in_the_views_time_units(tmp_path):
    n = 1000
    time = np.arange(n, dtype="float64")
    path = tmp_path / "w.parquet"
    pq.write_table(pa.table({"time": time, "idx": pa.array((time * 1000).astype("int64")), "a": time * 2}), path)
    view = view_for(path, time_column="time", x_range=(100.0, 200.0))
    info = info_for(path, view, time_column="idx")
    plan = pe.plan_from_view(
        view, info, source_path=str(path), total_rows=n, selected=["a"], use_saved_range=True, view_file="",
        software="t", registry=FunctionRegistry(),
    )
    out = tmp_path / "o.parquet"
    pe.export_parquet(plan, out)
    table = pq.read_table(out)
    assert table.num_rows == 101 and table.column_names == ["idx", "a"]
    assert table["idx"][0].as_py() == 100_000 and table["idx"][-1].as_py() == 200_000


def test_a_saved_range_needs_the_views_own_axis_in_the_file(tmp_path):
    path = tmp_path / "w.parquet"
    pq.write_table(pa.table({"idx": pa.array(np.arange(100)), "a": np.arange(100.0)}), path)
    view = view_for(path, time_column="time", x_range=(1.0, 5.0))  # the file has no "time"
    info = info_for(path, view, time_column="idx")
    assert info.range_source is None and info.range_name == "time"
    with pytest.raises(pe.RangeAxisMissing) as e:
        pe.plan_from_view(
            view, info, source_path=str(path), total_rows=100, selected=["a"], use_saved_range=True, view_file="",
            software="t", registry=FunctionRegistry(),
        )
    assert e.value.name == "time"
    # without the saved range the same choice works
    plan = pe.plan_from_view(
        view, info, source_path=str(path), total_rows=100, selected=["a"], use_saved_range=False, view_file="",
        software="t", registry=FunctionRegistry(),
    )
    assert plan.time_range is None and plan.time_range_column is None


# -- 57.9.6a: one margin, and the least advanced file stops first -------------------------------------


@pytest.fixture
def margin():
    yield res.set_disk_margin_mb
    res.set_disk_margin_mb(None)


def test_the_margin_setting_is_what_all_three_checks_use(src, tmp_path, margin, monkeypatch):
    margin(500)
    assert res.disk_margin() == 500 * res.MB
    # 1) before the start
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 10 * res.GB + 500 * res.MB - 1)
    short = res.preflight([res.PreflightItem(str(tmp_path / "o.parquet"), 10 * res.GB, 0)])
    assert short.disk_short
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 10 * res.GB + 500 * res.MB)
    assert not res.preflight([res.PreflightItem(str(tmp_path / "o.parquet"), 10 * res.GB, 0)]).disk_short
    # 2) before a further file starts
    assert res.can_start_new_file(10 * res.GB, tmp_path) is None
    assert res.can_start_new_file(10 * res.GB + 1, tmp_path) == "disk"
    # 3) while running
    plan = make_plan(src, ["a"])
    run = pe._Run(plan, lambda: False)
    part = tmp_path / "o.parquet.part"
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 500 * res.MB)
    run.gate(part, 0)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 500 * res.MB - 1)
    with pytest.raises(pe.DiskFull):
        run.gate(part, 0)


def test_the_margin_default_and_invalid_values(margin):
    assert res.disk_margin() == 100 * res.MB == res.DISK_MARGIN
    for bad in (0, -5, None, "7", True, 1.5):
        margin(bad)
        assert res.disk_margin() == res.DISK_MARGIN


@pytest.mark.parametrize(
    "value,expect", [(1, 1), (5000, 5000), (0, 100), (-1, 100), (100_001, 100), ("9", 100), (True, 100), (None, 100)]
)
def test_settings_margin_validation(isolated_config, value, expect):
    import json as js

    from parquet_analyzer.io import settings as S

    assert Settings().export_disk_margin_mb == 100
    S.settings_path().parent.mkdir(parents=True, exist_ok=True)
    S.settings_path().write_text(js.dumps({"export_disk_margin_mb": value}), encoding="utf-8")
    assert S.load_settings().export_disk_margin_mb == expect


def test_arbiter_picks_the_least_advanced_and_the_newest_on_a_tie():
    a = res.DiskArbiter()
    assert a.is_least_advanced("x")  # unknown / alone
    a.register("one", "vol")
    a.register("two", "vol")
    a.update("one", 0.8)
    a.update("two", 0.2)
    assert a.is_least_advanced("two") and not a.is_least_advanced("one")
    a.update("two", 0.8)  # a tie: the newest stops
    assert a.is_least_advanced("two") and not a.is_least_advanced("one")
    a.unregister("two")
    assert a.is_least_advanced("one")


def test_arbiter_only_compares_files_on_the_same_volume():
    a = res.DiskArbiter()
    a.register("c1", "C")
    a.register("d1", "D")
    a.update("c1", 0.9)
    a.update("d1", 0.1)
    assert a.is_least_advanced("c1") and a.is_least_advanced("d1")  # alone on their own volumes
    assert a.request_stop_least("c1") is None  # nobody on C to stop
    a.register("c2", "C")
    a.update("c2", 0.3)
    assert a.request_stop_least("c1") == "c2" and a.stop_requested("c2")
    assert not a.stop_requested("d1") and not a.stop_requested("c1")
    a.unregister("c2")
    assert not a.stop_requested("c2")  # the mark goes with the file


def runs(src, fractions=(0.8, 0.2)):
    plan = make_plan(src, ["a"])
    arbiter = res.DiskArbiter()
    advanced = pe._Run(plan, lambda: False, arbiter, "advanced")
    shallow = pe._Run(plan, lambda: False, arbiter, "shallow")
    arbiter.register("advanced", "v")
    arbiter.register("shallow", "v")
    arbiter.update("advanced", fractions[0])
    arbiter.update("shallow", fractions[1])
    return arbiter, advanced, shallow


def gate_at(run, part, fraction):
    run.gate(part, int(N * fraction))


def test_when_the_disk_is_short_the_least_advanced_file_fails_at_once(src, tmp_path, monkeypatch):
    arbiter, advanced, shallow = runs(src)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 0)
    with pytest.raises(pe.DiskFull):
        gate_at(shallow, tmp_path / "s.part", 0.2)  # 20 % done, the other one is at 80 %


def test_a_more_advanced_file_marks_the_least_advanced_one_and_waits(src, tmp_path, monkeypatch):
    arbiter, advanced, shallow = runs(src)
    free = {"v": 0}
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: free["v"])
    slept = []

    def fake_sleep(_s):  # the marked file stops meanwhile: its space comes back
        slept.append(1)
        assert arbiter.stop_requested("shallow")
        arbiter.unregister("shallow")
        free["v"] = 10 * res.GB

    monkeypatch.setattr(pe, "_sleep", fake_sleep)
    gate_at(advanced, tmp_path / "a.part", 0.8)  # no DiskFull: it waited and went on
    assert slept == [1]


def test_the_marked_file_stops_at_its_next_gate_even_if_it_sees_room(src, tmp_path, monkeypatch):
    arbiter, advanced, shallow = runs(src)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 100 * res.GB)
    arbiter.request_stop_least("advanced")
    with pytest.raises(pe.DiskFull):
        gate_at(shallow, tmp_path / "s.part", 0.2)


def test_the_wait_for_the_other_file_is_limited(src, tmp_path, monkeypatch):
    arbiter, advanced, shallow = runs(src)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 0)
    monkeypatch.setattr(res, "DISK_YIELD_WAIT", 0.0)
    monkeypatch.setattr(pe, "_sleep", lambda s: None)
    with pytest.raises(pe.DiskFull):
        gate_at(advanced, tmp_path / "a.part", 0.8)


def test_a_cancel_while_waiting_for_the_other_file_is_honoured(src, tmp_path, monkeypatch):
    arbiter, advanced, shallow = runs(src)
    advanced.cancelled = lambda: True
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 0)
    monkeypatch.setattr(pe, "_sleep", lambda s: None)
    with pytest.raises(pe.ExportCancelled):
        gate_at(advanced, tmp_path / "a.part", 0.8)


def test_export_parquet_registers_and_unregisters_with_the_arbiter(src, tmp_path):
    arbiter = res.DiskArbiter()
    seen = []
    orig = arbiter.register

    def spy(key, volume=None):
        seen.append(key)
        orig(key, volume)

    arbiter.register = spy
    pe.export_parquet(make_plan(src, ["a"]), tmp_path / "o.parquet", arbiter=arbiter, arbiter_key="k")
    assert seen == ["k"] and arbiter._fraction == {}  # unregistered at the end
    with pytest.raises(pe.ExportError):  # a failed run also unregisters
        pe.export_parquet(make_plan(src, ["a"]), tmp_path / "o.parquet", arbiter=arbiter, arbiter_key="k2")
    assert arbiter._fraction == {}


def test_two_exports_at_once_the_less_advanced_one_is_stopped_for_the_other(src, tmp_path, monkeypatch):
    """Both really run in threads. The deep file's folder runs out of room at 75 %; the shallow file's folder
    (same volume) has plenty, so only the arbiter's stop request can end it. The deep file then finishes."""
    import threading

    monkeypatch.setattr(pe, "PROGRESS_INTERVAL", 0.0)  # every chunk reports (the hooks below run in the sink)
    dir_a, dir_b = tmp_path / "shallow", tmp_path / "deep"
    dir_a.mkdir()
    dir_b.mkdir()
    free = {str(dir_b): 100 * res.GB}
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: free.get(str(p), 100 * res.GB))
    monkeypatch.setattr(res, "DISK_YIELD_WAIT", 20.0)
    arbiter = res.DiskArbiter()
    shallow_hold, shallow_at_2 = threading.Event(), threading.Event()
    deep_short = threading.Event()
    outcome: dict[str, object] = {}

    def shallow_progress(done, total):
        if done >= 1000 and not shallow_at_2.is_set():
            shallow_at_2.set()
            assert shallow_hold.wait(30)

    def deep_progress(done, total):
        if done >= 4500 and not deep_short.is_set():
            assert shallow_at_2.wait(30)  # the shallow file has reported 1/12 or 2/12 by now
            free[str(dir_b)] = 0
            deep_short.set()
            shallow_hold.set()  # the shallow file goes on to its next gate

    def run(name, folder, sink):
        try:
            outcome[name] = pe.export_parquet(
                make_plan(src, ["a"], chunk_rows=500), folder / "o.parquet", sink, arbiter=arbiter, arbiter_key=name
            )
        except BaseException as e:  # noqa: BLE001 (collected for the asserts)
            outcome[name] = e

    ts = threading.Thread(target=run, args=("shallow", dir_a, shallow_progress))
    ts.start()
    assert shallow_at_2.wait(30)
    td = threading.Thread(target=run, args=("deep", dir_b, deep_progress))
    td.start()
    ts.join(30)
    assert isinstance(outcome["shallow"], pe.DiskFull)  # told to stop by the deep file, though its folder had room
    assert not list(dir_a.glob("*"))  # no .part, no output
    free[str(dir_b)] = 100 * res.GB  # its room came back
    td.join(30)
    assert not td.is_alive() and isinstance(outcome["deep"], pe.ExportResult)
    assert pq.read_table(dir_b / "o.parquet").num_rows == N
    assert arbiter._fraction == {}
