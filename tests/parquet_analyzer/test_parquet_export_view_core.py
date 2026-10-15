"""core/parquet_export.py, export from a saved view (export.ja.md 57.7.2-57.7.4, 57.7.6). No Qt."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import parquet_export as pe
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import evaluate_expression
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.core.numeric import to_numeric
from parquet_analyzer.core.view_check import Problem
from parquet_analyzer.io.view import DerivedVariableDef, PlotDef, SeriesDef, SourceDef, View

N = 2400
GROUP = 300


def times_us():
    return np.datetime64("2026-01-01T00:00:00", "us") + np.arange(N, dtype="int64") * np.timedelta64(1_000_000, "us")


def write(path, stats=True, shuffle_group=None, extra=None):
    rng = np.random.default_rng(5)
    t = times_us()
    a = rng.normal(size=N) * 10
    b = rng.normal(size=N)
    table = pa.table(
        {
            "t": pa.array(t, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "Speed_raw": a,
            "b": b,
            "cnt": pa.array(np.arange(N, dtype=np.int32)),
            "unused": rng.normal(size=N),
            **(extra or {}),
        }
    )
    if shuffle_group is not None:  # a group whose rows are not ascending (its min/max stay right)
        lo, hi = shuffle_group * GROUP, (shuffle_group + 1) * GROUP
        idx = np.arange(N)
        idx[lo + 1 : hi - 1] = idx[lo + 1 : hi - 1][::-1]
        table = table.take(pa.array(idx))
    pq.write_table(table, path, row_group_size=GROUP, write_statistics=stats)
    return path


@pytest.fixture
def src(tmp_path):
    return write(tmp_path / "src.parquet")


@pytest.fixture
def registry():
    reg = FunctionRegistry()
    reg.user_functions["twice"] = UserFunction("twice", ["x"], [], "x * 2")
    return reg


def make_view(plots, derived=(), time_column="t", x_range=None, name="v1"):
    return View(
        view_name=name,
        source=SourceDef("/nowhere.parquet", "absolute"),
        plots=[PlotDef(f"p{i}", [SeriesDef(n, "#ff0000") for n in names]) for i, names in enumerate(plots)],
        derived_variables=[DerivedVariableDef(n, e) for n, e in derived],
        x_axis_range=x_range,
        time_column=time_column,
    )


def columns_of(path):
    return list(pq.read_schema(path).names)


def info_for(view, src, registry, aliases=None, **kw):
    return pe.view_export_info(view, columns_of(src), aliases or {}, registry, **kw)


def plan_for(view, info, src, registry, selected, **kw):
    return pe.plan_from_view(
        view, info, source_path=str(src), total_rows=pq.read_metadata(src).num_rows, selected=selected,
        use_saved_range=kw.pop("use_saved_range", False), view_file="/views/v1.json", software="test",
        registry=registry, chunk_rows=kw.pop("chunk_rows", 250), **kw,
    )


def whole(src, expr, registry, **names):
    ds = ParquetDataSource(src)
    arrays = {n: to_numeric(ds.read_columns([c])[c]) for n, c in names.items()}
    return np.asarray(evaluate_expression(expr, arrays, registry), dtype=np.float64)


# -- the list (57.7.3) --------------------------------------------------------------------------


def test_list_is_plot_signals_then_other_derived(src, registry):
    view = make_view(
        [["b", "d1"], ["Speed_raw", "b"]],
        derived=[("d1", "b * 2"), ("other", "Speed_raw + 1"), ("d2", "other + b")],
    )
    info = info_for(view, src, registry)
    assert [(i.name, i.kind, i.checked) for i in info.items] == [
        ("b", "plot", True),
        ("d1", "plot", True),
        ("Speed_raw", "plot", True),
        ("other", "derived", False),
        ("d2", "derived", False),
    ]
    assert info.time_name == "t" and info.time_source == "t" and info.time_problem is None
    assert all(i.usable for i in info.items)
    assert "unused" not in {i.name for i in info.items}


def test_time_axis_is_not_a_list_entry(src, registry):
    view = make_view([["t", "b"]])
    assert [i.name for i in info_for(view, src, registry).items] == ["b"]


def test_old_view_without_time_column_uses_the_first_column(src, registry):
    view = make_view([["b"]], time_column=None)
    info = info_for(view, src, registry)
    assert info.time_name == "t" and info.time_problem is None


def test_time_axis_missing_from_the_file(src, registry):
    view = make_view([["b"]], time_column="gone")
    info = info_for(view, src, registry)
    assert info.time_problem == Problem("time", "gone")
    with pytest.raises(pe.ExportError):
        plan_for(view, info, src, registry, ["b"])


@pytest.mark.parametrize(
    ("derived", "kind", "detail"),
    [
        (("x", "nope + 1"), "missing_column", "nope"),
        (("x", "nofunc(b)"), "unknown_function", "nofunc"),
        (("x", "twice(b, b)"), "call", None),
        (("x", "b +"), "syntax", ""),
        (("cnt", "b + 1"), "column_name", ""),
        (("Speed", "cnt + 1"), "alias_name", ""),
    ],
    ids=["missing_column", "unknown_function", "arity", "syntax", "column_name", "alias_name"],
)
def test_unusable_derived_variables_carry_a_reason(src, registry, derived, kind, detail):
    view = make_view([["b"]], derived=[derived])
    info = info_for(view, src, registry, alias_names={"Speed"})
    item = next(i for i in info.items if i.name == derived[0] and i.kind == "derived")
    assert item.problem is not None and item.problem.kind == kind
    if detail is not None:
        assert item.problem.detail == detail
    assert derived[0] not in info.expressions


def test_dependency_on_an_unusable_variable_cascades(src, registry):
    view = make_view([["b"]], derived=[("bad", "nope + 1"), ("child", "bad * 2"), ("ok", "b * 2")])
    info = info_for(view, src, registry)
    problems = {i.name: i.problem for i in info.items}
    assert problems["child"] == Problem("dependency", "bad")
    assert problems["ok"] is None
    assert set(info.expressions) == {"ok"}


def test_unusable_plotted_signal_is_listed_with_its_reason(src, registry):
    view = make_view([["b", "ghost"]])
    info = info_for(view, src, registry)
    ghost = next(i for i in info.items if i.name == "ghost")
    assert ghost.problem == Problem("missing_column", "ghost")
    with pytest.raises(pe.ExportError):
        plan_for(view, info, src, registry, ["ghost"])


# -- the plan (57.7.5) --------------------------------------------------------------------------


def test_export_computes_only_what_is_selected_and_matches_a_full_computation(src, registry, tmp_path, monkeypatch):
    view = make_view(
        [["b", "d1"]], derived=[("d1", "twice(b) + 1"), ("unselected", "Speed_raw * 3"), ("base", "b + 1"), ("top", "base * 2")]
    )
    info = info_for(view, src, registry)
    calls: list[str] = []
    real = pe.evaluate_expression
    monkeypatch.setattr(pe, "evaluate_expression", lambda e, v, r=None: (calls.append(e), real(e, v, r))[1])
    plan = plan_for(view, info, src, registry, ["b", "d1", "top"])
    out = tmp_path / "out.parquet"
    result = pe.export_parquet(plan, out)
    assert "Speed_raw * 3" not in " ".join(calls)  # an unselected variable is not computed
    assert "base * 2" in " ".join(calls) and "b + 1" in " ".join(calls)  # the chain of a selected one is
    table = pq.read_table(out)
    assert table.column_names == ["t", "b", "d1", "top"]
    assert result.rows == N and result.row_groups == -(-N // 250)
    np.testing.assert_array_equal(table["d1"].to_numpy(), whole(src, "twice(b) + 1", registry, b="b"))
    np.testing.assert_array_equal(table["top"].to_numpy(), whole(src, "(b + 1) * 2", registry, b="b"))
    assert table.schema.field("t").type == pq.read_schema(src).field("t").type
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["view"] == "v1" and meta["view_file"] == "/views/v1.json" and "aliases" not in meta
    assert set(meta["derived"]) == {"d1", "top", "base"}


def test_aliases_resolve_signals_time_and_formulas(src, registry, tmp_path):
    view = make_view(
        [["Speed", "double"]], derived=[("double", "Speed * 2")], time_column="T"
    )
    aliases = {"Speed": ["Speed_raw"], "T": ["t"]}
    info = info_for(view, src, registry, aliases)
    assert info.time_name == "T" and info.time_source == "t"
    assert all(i.usable for i in info.items)
    plan = plan_for(view, info, src, registry, ["Speed", "double"])
    out = tmp_path / "out.parquet"
    pe.export_parquet(plan, out)
    table = pq.read_table(out)
    assert table.column_names == ["T", "Speed", "double"]  # the view's names
    ds = ParquetDataSource(src)
    raw = ds.read_columns(["Speed_raw"])["Speed_raw"]
    np.testing.assert_array_equal(table["Speed"].to_numpy(), raw)  # the source column's content
    np.testing.assert_array_equal(table["double"].to_numpy(), raw * 2)
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["aliases"] == {"T": "t", "Speed": "Speed_raw"}
    assert meta["time_column"] == "T"


def test_alias_switch_off_does_not_resolve(src, registry):
    view = make_view([["Speed"]], derived=[("double", "Speed * 2")], time_column="T")
    info = info_for(view, src, registry, aliases={}, alias_names={"Speed", "T"})  # switch off: nothing attaches
    assert info.time_problem is not None
    assert info.items[0].problem == Problem("missing_column", "Speed")
    assert next(i for i in info.items if i.name == "double").problem is not None


def test_alias_in_the_axis_is_removed_from_the_variables(src, registry):
    # the axis' source and all its aliases are the axis, not variables (persistence 54.5)
    view = make_view([["t", "T2", "b"]], time_column="T")
    info = info_for(view, src, registry, {"T": ["t"], "T2": ["t"]})
    assert [i.name for i in info.items] == ["b"]


def test_output_equal_to_source_is_refused(src, registry):
    view = make_view([["b"]])
    plan = plan_for(view, info_for(view, src, registry), src, registry, ["b"])
    with pytest.raises(pe.ExportError):
        pe.export_parquet(plan, src)
    assert pq.read_metadata(src).num_rows == N  # the source is untouched


def test_selection_of_nothing_known_is_an_error(src, registry):
    view = make_view([["b"]])
    with pytest.raises(pe.ExportError):
        plan_for(view, info_for(view, src, registry), src, registry, ["not_in_the_list"])


def test_saved_range_needs_a_range(src, registry):
    view = make_view([["b"]])
    with pytest.raises(pe.ExportError):
        plan_for(view, info_for(view, src, registry), src, registry, ["b"], use_saved_range=True)


# -- the range (57.7.4) --------------------------------------------------------------------------


def epoch(i):
    """The screen's number for row i of the time axis (to_numeric of its datetime64 value)."""
    return float(to_numeric(times_us()[i : i + 1])[0])


def expected_rows(x_lo, x_hi):
    t = to_numeric(times_us())
    return int(np.searchsorted(t, x_lo, "left")), int(np.searchsorted(t, x_hi, "right"))


@pytest.mark.parametrize(
    ("lo", "hi"),
    [
        (10, 100),  # inside the first group
        (250, 650),  # across groups
        (300, 599),  # exactly the group's first/last rows
        (299, 300),  # the boundary pair
        (0, N - 1),  # everything
        (2000, N - 1),  # the tail
        (5, 5),  # a single row
    ],
)
def test_find_row_range_matches_searchsorted(src, lo, hi):
    x_lo, x_hi = epoch(lo), epoch(hi)
    assert pe.find_row_range(src, "t", x_lo, x_hi) == expected_rows(x_lo, x_hi)


def test_find_row_range_between_rows_and_outside(src):
    x_lo, x_hi = epoch(100) + 0.2, epoch(200) + 0.7  # between samples
    assert pe.find_row_range(src, "t", x_lo, x_hi) == expected_rows(x_lo, x_hi)
    assert pe.find_row_range(src, "t", epoch(0) - 1000, epoch(3) - 0.5) == (0, 3)  # starts before the data
    assert pe.find_row_range(src, "t", epoch(N - 2) + 0.5, epoch(N - 1) + 1000) == (N - 1, N)


@pytest.mark.parametrize(
    ("lo", "hi"),
    [(epoch(0) - 500, epoch(0) - 10), (epoch(N - 1) + 10, epoch(N - 1) + 500), (epoch(100) + 0.2, epoch(100) + 0.8)],
    ids=["before", "after", "between-samples"],
)
def test_find_row_range_with_no_rows(src, lo, hi):
    start, end = pe.find_row_range(src, "t", lo, hi)
    assert end <= start


def test_find_row_range_reads_only_the_boundary_row_groups(src, monkeypatch):
    reads: list[tuple[int, int]] = []
    real = ParquetDataSource.read_columns

    def spy(self, columns, row_start=0, row_end=None):
        reads.append((row_start, row_end))
        return real(self, columns, row_start, row_end)

    monkeypatch.setattr(ParquetDataSource, "read_columns", spy)
    x_lo, x_hi = epoch(350), epoch(1850)
    assert pe.find_row_range(src, "t", x_lo, x_hi) == expected_rows(x_lo, x_hi)
    assert reads == [(300, 600), (1800, 2100)]  # the two boundary groups, nothing in between


def test_find_row_range_without_statistics_reads_in_chunks(tmp_path, monkeypatch):
    path = write(tmp_path / "nostats.parquet", stats=False)
    reads: list[tuple[int, int]] = []
    real = ParquetDataSource.read_columns

    def spy(self, columns, row_start=0, row_end=None):
        reads.append((row_start, row_end))
        return real(self, columns, row_start, row_end)

    monkeypatch.setattr(ParquetDataSource, "read_columns", spy)
    x_lo, x_hi = epoch(350), epoch(1850)
    assert pe.find_row_range(path, "t", x_lo, x_hi, chunk_rows=500) == expected_rows(x_lo, x_hi)
    assert max(hi - lo for lo, hi in reads) <= 500 and sum(hi - lo for lo, hi in reads) == N


def test_find_row_range_with_an_unordered_boundary_group_falls_back(tmp_path):
    path = write(tmp_path / "shuffled.parquet", shuffle_group=1)  # rows 300..599 are not ascending
    # the unordered group is where the range starts and ends
    t = to_numeric(pq.read_table(path)["t"].to_numpy())
    x_lo, x_hi = float(t[350]), float(t[1000])
    start, end = pe.find_row_range(path, "t", x_lo, x_hi, chunk_rows=700)
    # by position: the first row at or above x_lo and the row after the last one at or below x_hi
    assert start == int(np.flatnonzero(t >= x_lo)[0]) == 301
    assert end == int(np.flatnonzero(t <= x_hi)[-1]) + 1 == 1001


def test_find_row_range_can_be_cancelled(tmp_path):
    path = write(tmp_path / "nostats.parquet", stats=False)
    with pytest.raises(pe.ExportCancelled):
        pe.find_row_range(path, "t", epoch(1), epoch(5), chunk_rows=100, cancelled=lambda: True)


def test_find_row_range_of_a_numeric_axis(tmp_path):
    path = tmp_path / "num.parquet"
    pq.write_table(pa.table({"x": np.arange(1000, dtype=np.float64) * 0.5, "y": np.arange(1000.0)}), path, row_group_size=100)
    assert pe.find_row_range(path, "x", 100.0, 200.0) == (200, 401)


def test_export_of_the_saved_range_equals_the_same_rows_of_a_full_computation(src, registry, tmp_path):
    x_lo, x_hi = epoch(450), epoch(1700)
    view = make_view([["b", "d"]], derived=[("d", "twice(b) + delta(b)")], x_range=(x_lo, x_hi))
    info = info_for(view, src, registry)
    plan = plan_for(view, info, src, registry, ["b", "d"], use_saved_range=True)
    assert plan.time_range == (x_lo, x_hi)
    out = tmp_path / "out.parquet"
    progress: list[tuple[int, int]] = []
    result = pe.export_parquet(plan, out, lambda done, total: progress.append((done, total)))
    start, end = expected_rows(x_lo, x_hi)
    assert result.rows == end - start == 1251
    assert progress[0] == (0, pe.PROGRESS_RANGE) and progress[-1] == (end - start, end - start)  # the search phase has no percentage
    table = pq.read_table(out)
    full = whole(src, "twice(b) + delta(b)", registry, b="b")
    np.testing.assert_array_equal(table["d"].to_numpy(), full[start:end])  # delta uses the row before the range
    meta = json.loads(table.schema.metadata[pe.METADATA_KEY])
    assert meta["range"]["rows"] == [start, end]


def test_saved_range_without_rows_fails_and_leaves_nothing(src, registry, tmp_path):
    view = make_view([["b"]], x_range=(epoch(N - 1) + 100, epoch(N - 1) + 200))
    plan = plan_for(view, info_for(view, src, registry), src, registry, ["b"], use_saved_range=True)
    out = tmp_path / "out.parquet"
    with pytest.raises(pe.NoRowsInRange):
        pe.export_parquet(plan, out)
    assert not out.exists() and not (tmp_path / "out.parquet.part").exists()


def test_a_cancel_during_the_row_search_leaves_nothing(tmp_path, registry):
    path = write(tmp_path / "nostats.parquet", stats=False)
    view = make_view([["b"]], x_range=(epoch(1), epoch(5)))
    plan = plan_for(view, info_for(view, path, registry), path, registry, ["b"], use_saved_range=True, chunk_rows=100)
    out = tmp_path / "out.parquet"
    with pytest.raises(pe.ExportCancelled):
        pe.export_parquet(plan, out, cancelled=lambda: True)
    assert not out.exists() and not (tmp_path / "out.parquet.part").exists()


# -- non-finite rows, nanoseconds (found in review) -----------------------------------------------------


def numeric_file(tmp_path, values, stats=True, group=5, name="n.parquet"):
    path = tmp_path / name
    pq.write_table(pa.table({"x": values, "y": np.arange(len(values), dtype=np.float64)}), path,
                   row_group_size=group, write_statistics=stats)
    return path


def by_position(values, x_lo, x_hi):
    v = np.asarray(values, dtype=np.float64)
    ok = np.isfinite(v)
    first = np.flatnonzero(ok & (v >= x_lo))
    last = np.flatnonzero(ok & (v <= x_hi))
    return (int(first[0]), int(last[-1]) + 1) if first.size and last.size and last[-1] >= first[0] else (0, 0)


@pytest.mark.parametrize("stats", [True, False], ids=["stats", "no-stats"])
def test_nan_before_the_range_does_not_shift_it(tmp_path, stats):
    x = np.arange(20.0)
    x[12] = np.nan
    path = numeric_file(tmp_path, x, stats)
    assert pe.find_row_range(path, "x", 13.0, 16.0, chunk_rows=7) == (13, 17)
    assert by_position(x, 13.0, 16.0) == (13, 17)


@pytest.mark.parametrize("stats", [True, False], ids=["stats", "no-stats"])
def test_nan_inside_the_range_stays_in_it(tmp_path, stats):
    x = np.arange(20.0)
    x[8] = np.nan
    path = numeric_file(tmp_path, x, stats)
    assert pe.find_row_range(path, "x", 6.0, 11.0, chunk_rows=6) == (6, 12)


def test_a_nan_in_a_boundary_group_still_uses_the_statistics(tmp_path, monkeypatch):
    x = np.arange(40.0)
    x[12] = np.nan  # in the group where the range starts (rows 10..14)
    x[27] = np.nan  # in the group where it ends (rows 25..29)
    path = numeric_file(tmp_path, x)
    reads: list[tuple[int, int]] = []
    real = ParquetDataSource.read_columns
    monkeypatch.setattr(
        ParquetDataSource, "read_columns",
        lambda self, c, lo=0, hi=None: (reads.append((lo, hi)), real(self, c, lo, hi))[1],
    )
    assert pe.find_row_range(path, "x", 13.0, 26.0) == by_position(x, 13.0, 26.0) == (13, 27)
    assert reads == [(10, 15), (25, 30)]


def test_null_timestamp_before_the_range(tmp_path):
    t = np.datetime64("2026-01-01", "us") + np.arange(20) * np.timedelta64(1_000_000, "us")
    arr = pa.array(t, type=pa.timestamp("us"), mask=np.isin(np.arange(20), [7]))
    for stats in (True, False):
        path = tmp_path / f"ts{stats}.parquet"
        pq.write_table(pa.table({"t": arr}), path, row_group_size=5, write_statistics=stats)
        x_lo = float(to_numeric(t[8:9])[0])
        x_hi = float(to_numeric(t[12:13])[0])
        assert pe.find_row_range(path, "t", x_lo, x_hi, chunk_rows=6) == (8, 13)


def test_ns_timestamps_that_are_not_whole_microseconds_use_the_statistics(tmp_path, monkeypatch):
    t = np.datetime64("2026-01-01", "ns") + np.arange(400) * np.timedelta64(1_234_567, "ns")
    path = tmp_path / "ns.parquet"
    pq.write_table(pa.table({"t": pa.array(t, type=pa.timestamp("ns", tz="Asia/Tokyo"))}), path, row_group_size=50)
    reads: list[tuple[int, int]] = []
    real = ParquetDataSource.read_columns
    monkeypatch.setattr(
        ParquetDataSource, "read_columns",
        lambda self, c, lo=0, hi=None: (reads.append((lo, hi)), real(self, c, lo, hi))[1],
    )
    num = to_numeric(t)
    x_lo, x_hi = float(num[120]), float(num[310])
    assert pe.find_row_range(path, "t", x_lo, x_hi) == by_position(num, x_lo, x_hi)
    assert reads == [(100, 150), (300, 350)]  # the two boundary row groups only


def test_nan_axis_without_statistics_export_matches(tmp_path, registry):
    x = np.arange(40.0)
    x[3] = np.nan
    path = numeric_file(tmp_path, x, stats=False)
    view = make_view([["y"]], time_column="x", x_range=(10.0, 20.0))
    plan = plan_for(view, info_for(view, path, registry), path, registry, ["y"], use_saved_range=True)
    out = tmp_path / "o.parquet"
    assert pe.export_parquet(plan, out).rows == 11
    assert pq.read_table(out)["y"].to_numpy().tolist() == list(range(10, 21))


# -- a derived variable named like the exported time column (57.7.3) -------------------------------------


def test_derived_named_like_the_time_axis_is_listed_greyed(src, registry):
    view = make_view([["t", "b"]], derived=[("t", "b * 2"), ("child", "t + 1")])
    info = info_for(view, src, registry)
    by = {i.name: i for i in info.items}
    assert by["t"].problem == Problem("time_name") and by["t"].kind == "plot"
    assert by["child"].problem == Problem("dependency", "t")
    assert by["b"].usable
    with pytest.raises(pe.ExportError):
        plan_for(view, info, src, registry, ["t"])


def test_format_time_value_matches_the_metadata_text(src):
    ds_type = pq.read_schema(src).field("t").type  # naive microseconds here
    x = epoch(10)
    text = pe.format_time_value(x, ds_type)
    out = pq.read_table(src, columns=["t"]).slice(10, 1)["t"][0]
    assert text == pe._scalar_text(out)
    tz = pa.timestamp("us", tz="Asia/Tokyo")
    assert pe.format_time_value(0.0, tz) == "1970-01-01 09:00:00.000000+0900"
    assert pe.format_time_value(2.5, pa.float64()) == "2.5"
