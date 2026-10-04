"""Prototype 36.6 C: derived variables over windowed columns computed per window
(`core.column.DerivedColumn`, behind the `big_c_windowed_derived` feature flag)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import features
from parquet_analyzer.core.analysis import basic_stats, chunked_stats
from parquet_analyzer.core.column import (
    DerivedColumn,
    LazyVariables,
    MaterializedColumn,
    WindowedColumn,
    make_derived_column,
)
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.core.expression import ExpressionError, evaluate_expression, expression_margin
from parquet_analyzer.core.functions import FunctionRegistry, UserFunction
from parquet_analyzer.core.maps import LookupTable
from parquet_analyzer.core.pyramid import build_pyramid_from_windows
from parquet_analyzer.ui.main_window import MainWindow

N = 3000


@pytest.fixture
def cols(tmp_path):
    rng = np.random.default_rng(1)
    a = rng.normal(size=N) * 10
    b = rng.normal(size=N)
    a[[0, 5, 700, N - 1]] = np.nan  # NaNs at the edges and inside
    b[[3, 1500]] = np.nan
    path = tmp_path / "w.parquet"
    pq.write_table(pa.table({"a": a, "b": b}), path, row_group_size=300)
    ds = ParquetDataSource(path, block_rows=256)
    return {"a": WindowedColumn(ds, "a"), "b": WindowedColumn(ds, "b")}


@pytest.fixture
def registry():
    reg = FunctionRegistry()
    reg.user_functions["scaled"] = UserFunction("scaled", ["x"], [("k", 2.0)], "x * k + abs(x)")
    reg.user_functions["smooth"] = UserFunction("smooth", ["x"], [], "rolling_mean(delta(x), 4)")
    reg.maps["curve"] = LookupTable("curve", "curve", [-30.0, 0.0, 30.0], [0.0, 5.0, 1.0])
    return reg


def _full(expr, columns, registry):
    return np.asarray(evaluate_expression(expr, LazyVariables(columns), registry), dtype=np.float64)


EXPRS = [
    "a + b",
    "a * 2 - b / 3",
    "abs(a) + sqrt(abs(b))",
    "delta(a)",
    "delta(a) + delta(b)",
    "rolling_mean(a, 5)",
    "rolling_nanmean(a, 7)",
    "rolling_mean(b, 1)",
    "rolling_nanmean(delta(a), 4)",
    "delta(rolling_mean(a, 6))",
    "max(a, 0) + clip(b, -1, 1)",
    "scaled(a)",
    "scaled(a, k=3)",
    "smooth(b)",
    "curve(a)",
    "delta(curve(a)) + scaled(b)",
    "-a",
    "rolling_mean(a, 100000)",  # window wider than the file
]


@pytest.mark.parametrize("expr", EXPRS)
def test_window_equals_full_computation(cols, registry, expr):
    full = _full(expr, cols, registry)
    d = make_derived_column(expr, cols, N, registry)
    assert isinstance(d, DerivedColumn), expr
    np.testing.assert_array_equal(d.values(), full)
    rng = np.random.default_rng(7)
    windows = [(0, 1), (0, N), (N - 1, N), (0, 10), (N - 10, N), (299, 301), (1, 2), (5, 6)]
    windows += [tuple(sorted(rng.integers(0, N + 1, size=2))) for _ in range(40)]
    for lo, hi in windows:
        got = d.window(lo, hi)
        np.testing.assert_array_equal(got, full[lo:hi], err_msg=f"{expr} [{lo}:{hi}]")


def test_nested_derived_columns(cols, registry):
    d1 = make_derived_column("delta(a)", cols, N, registry)
    both = {**cols, "d1": d1}
    d2 = make_derived_column("rolling_mean(d1, 5) + b", both, N, registry)
    assert isinstance(d2, DerivedColumn)
    assert d2.margin == (5, 4)  # 1 before (delta) + 4 (rolling) each way
    full_d1 = _full("delta(a)", cols, registry)
    full = _full("rolling_mean(d1, 5) + b", {**cols, "d1": MaterializedColumn(full_d1)}, registry)
    for lo, hi in [(0, 4), (0, N), (100, 160), (N - 3, N), (7, 8)]:
        np.testing.assert_array_equal(d2.window(lo, hi), full[lo:hi])


def test_margins(registry):
    m = lambda e: expression_margin(e, registry, {"a": (0, 0), "b": (0, 0)})  # noqa: E731
    assert m("a + b") == (0, 0)
    assert m("delta(a)") == (1, 0)
    assert m("rolling_mean(a, 10)") == (9, 9)
    assert m("delta(rolling_nanmean(a, 3))") == (3, 2)
    assert m("scaled(delta(a))") == (1, 0)
    assert m("smooth(a)") == (4, 3)  # delta(1,0) inside rolling_mean(4) -> (3,3): total (4,3)
    assert m("curve(a)") == (0, 0)


def test_unknown_margin_falls_back(cols, registry):
    # non-constant rolling window, unknown names/functions, invalid window -> no DerivedColumn
    assert make_derived_column("rolling_mean(a, b)", cols, N, registry) is None
    assert make_derived_column("rolling_mean(a, 0)", cols, N, registry) is None
    assert make_derived_column("nope(a)", cols, N, registry) is None
    assert make_derived_column("a + zzz", cols, N, registry) is None
    assert make_derived_column("a +", cols, N, registry) is None
    assert make_derived_column("scaled(a, k=b)", cols, N, registry) is None
    # no windowed input -> resident computation as before
    assert make_derived_column("x + 1", {"x": MaterializedColumn(np.arange(N, dtype=float))}, N, registry) is None
    assert make_derived_column("1 + 2", cols, N, registry) is None


def test_builtin_without_margin_entry_falls_back(cols, registry, monkeypatch):
    from parquet_analyzer.core import expression

    monkeypatch.delitem(expression._FUNC_MARGINS, "sqrt")
    assert make_derived_column("sqrt(a)", cols, N, registry) is None
    assert isinstance(make_derived_column("abs(a)", cols, N, registry), DerivedColumn)


def test_probe_raises_for_bad_expression(cols, registry):
    with pytest.raises(ExpressionError):
        make_derived_column("sqrt(a, b)", cols, N, registry)  # arity: raised before commit


def test_window_reads_only_what_it_needs(cols, registry):
    reads = []
    inner = cols["a"].window
    cols["a"].__class__  # WindowedColumn has __slots__: wrap through a proxy column instead

    class Spy:
        def __init__(self, c):
            self.c = c

        def window(self, lo, hi):
            reads.append((lo, hi))
            return self.c.window(lo, hi)

        def values(self):
            return self.c.values()

    d = DerivedColumn("delta(a) + rolling_mean(a, 5)", {"a": Spy(cols["a"])}, N, registry, (4, 4))
    d.window(1000, 1010)
    assert reads == [(996, 1014)]  # once, margin included, not the whole column
    reads.clear()
    d.window(0, 5)
    assert reads == [(0, 9)]


def test_bounds_and_pyramid_from_chunks(cols, registry):
    d = make_derived_column("a + b", cols, N, registry)
    full = _full("a + b", cols, registry)
    fin = full[np.isfinite(full)]
    assert d.bounds() == (float(fin.min()), float(fin.max()))
    job = d.request_pyramid()
    assert job is not None and d.request_pyramid() is None  # one build at a time
    pyr = job()
    d.set_pyramid(pyr)
    assert d.pyramid is pyr and d.request_pyramid() is None
    ref = build_pyramid_from_windows(lambda lo, hi: full[lo:hi], N, 8192, 8192)
    np.testing.assert_array_equal(pyr.row_starts, ref.row_starts)
    np.testing.assert_array_equal(pyr.mins, ref.mins)
    # a hand-checked bucket
    small = build_pyramid_from_windows(lambda lo, hi: full[lo:hi], N, 100, 300)
    seg = full[100:200]
    seg = seg[np.isfinite(seg)]
    assert small.mins[1] == seg.min() and small.maxs[1] == seg.max() and len(small) == 30


def test_all_nan_bounds_none(tmp_path, registry):
    path = tmp_path / "n.parquet"
    pq.write_table(pa.table({"a": np.full(100, np.nan)}), path)
    c = {"a": WindowedColumn(ParquetDataSource(path), "a")}
    assert make_derived_column("a + 1", c, 100, registry).bounds() is None


@pytest.mark.parametrize("chunk", [97, 1000, 100000])
def test_chunked_stats_equal_basic_stats(cols, registry, chunk):
    d = make_derived_column("a + b", cols, N, registry)
    full = d.values()
    for lo, hi in [(0, N), (10, 2900), (0, 1), (100, 101)]:
        want = basic_stats(full[lo:hi])
        got = chunked_stats(d.window, lo, hi, chunk_rows=chunk, bins=64)
        for k in ("count", "nan_count", "min", "max", "median"):
            assert got[k] == want[k] or (np.isnan(got[k]) and np.isnan(want[k])), (k, lo, hi)
        np.testing.assert_allclose([got["mean"], got["std"]], [want["mean"], want["std"]], rtol=1e-9, atol=1e-12)


def test_chunked_stats_edge_cases():
    nan = np.full(10, np.nan)
    assert chunked_stats(lambda a, b: nan[a:b], 0, 10)["nan_count"] == 10
    const = np.full(10, 3.0)
    assert chunked_stats(lambda a, b: const[a:b], 0, 10)["median"] == 3.0
    even = np.array([1.0, 2.0, 3.0, 10.0])
    assert chunked_stats(lambda a, b: even[a:b], 0, 4, chunk_rows=3, bins=4)["median"] == 2.5
    assert chunked_stats(lambda a, b: even[a:b], 0, 0)["count"] == 0


# -- MainWindow integration ---------------------------------------------------------------


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "big_c_windowed_derived", True)


def _win(qapp, isolated_config, tmp_path, limit=0):
    rng = np.random.default_rng(3)
    n = 4000
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    a, b = rng.normal(size=n), rng.normal(size=n) * 5
    a[100] = np.nan
    path = tmp_path / "m.parquet"
    pq.write_table(pa.table({"time": pa.array(times), "a": a, "b": b}), path, row_group_size=500)
    win = MainWindow()
    win.settings.eager_load_limit_mb = limit
    win.load_parquet(str(path))
    return win, a, b


def _settle(qapp, win):
    import time

    for _ in range(400):
        qapp.processEvents()
        if not win._busy_sources:
            return
        time.sleep(0.005)


def test_flag_off_is_resident(qapp, isolated_config, tmp_path, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "big_c_windowed_derived", False)
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        win._on_add_derived_variable("d", "a + b")
        assert isinstance(win._raw_variables["d"], MaterializedColumn)
    finally:
        win.close()


def test_flag_on_windowed_derived_end_to_end(qapp, isolated_config, tmp_path, flag_on):
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        win._on_add_derived_variable("d", "delta(a) + b")
        win._on_add_derived_variable("e", "d * 2")
        assert isinstance(win._raw_variables["d"], DerivedColumn)
        assert isinstance(win._raw_variables["e"], DerivedColumn)
        want_d = np.concatenate([[0.0], np.diff(a)]) + b
        np.testing.assert_array_equal(win.variable_values("d"), want_d)
        np.testing.assert_array_equal(win.variable_window("e", 10, 30), (want_d * 2)[10:30])

        plot = win.plot_grid.plots[0]
        win._add_variable_to_plot(plot, "e")
        _settle(qapp, win)
        assert plot.series_source("e") is not None  # windowed, not resident
        x, y = plot.curve_data("e") if hasattr(plot, "curve_data") else (None, None)
        # cursor/hover value through the windowed path
        row = 1234
        entry = plot._series["e"]
        assert plot._series_value_at(entry, float(win._time_values[row])) == pytest.approx((want_d * 2)[row])

        # edit: recompute all-or-nothing, dependents follow, plot updates
        win.edit_derived_variable("d", "b - a")
        _settle(qapp, win)
        np.testing.assert_array_equal(win.variable_values("e"), ((b - a) * 2))
        assert isinstance(win._raw_variables["e"], DerivedColumn)
        before = win._raw_variables["e"]
        with pytest.raises((ExpressionError, ValueError)):
            win.edit_derived_variable("d", "rolling_mean(a, 0) + nope(b)")
        assert win._raw_variables["e"] is before  # untouched
        assert win._derived["d"].expression == "b - a"

        # editing to a resident-only expression turns the plotted series resident again
        win.edit_derived_variable("e", "1 + 2")
        _settle(qapp, win)
        assert isinstance(win._raw_variables["e"], MaterializedColumn)
        assert plot.series_source("e") is None
    finally:
        win.close()


def test_flag_on_fallback_for_nonconstant_window(qapp, isolated_config, tmp_path, flag_on):
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        win._on_add_derived_variable("d", "rolling_mean(a, 3 + 2)")  # not a plain number: no margin
        assert isinstance(win._raw_variables["d"], MaterializedColumn)
    finally:
        win.close()


def test_flag_on_stats_per_window(qapp, isolated_config, tmp_path, flag_on, monkeypatch):
    win, a, b = _win(qapp, isolated_config, tmp_path)
    shown = []
    monkeypatch.setattr(win, "_show_stats_dialog", lambda batch: shown.append(batch))
    try:
        win._on_add_derived_variable("d", "a + b")
        plot = win.plot_grid.plots[0]
        win._add_variable_to_plot(plot, "d")
        _settle(qapp, win)
        win._on_stats_requested()
        for _ in range(200):
            qapp.processEvents()
            if shown:
                break
        assert shown
        r0, r1 = win._visible_row_range()
        want = basic_stats((a + b)[r0:r1])
        got = shown[0]["results"]["d"]
        assert got["count"] == want["count"] and got["nan_count"] == want["nan_count"]
        assert got["median"] == want["median"] and got["max"] == want["max"]
        assert got["mean"] == pytest.approx(want["mean"])
    finally:
        win.close()


def test_flag_on_registry_update_recomputes_derived(qapp, isolated_config, tmp_path, flag_on):
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        reg = win.function_registry
        reg.user_functions["f"] = UserFunction("f", ["x"], [("k", 1.0)], "x * k")
        win._on_add_derived_variable("d", "f(a) + b")
        d = win._raw_variables["d"]
        assert isinstance(d, DerivedColumn)
        cand = dict(reg.user_functions)
        cand["f"] = UserFunction("f", ["x"], [("k", 3.0)], "x * k")
        failures = win.update_registry(user_functions=cand, touched={"f"})
        assert failures == []
        new = win._raw_variables["d"]
        assert isinstance(new, DerivedColumn) and new is not d
        assert new.registry is not win.function_registry  # an immutable snapshot (36.8), same contents
        assert new.registry.user_functions.keys() == win.function_registry.user_functions.keys()
        np.testing.assert_array_equal(win.variable_values("d"), a * 3 + b)
        # a failing candidate changes nothing
        bad = dict(reg.user_functions)
        bad["f"] = UserFunction("f", ["x"], [], "rolling_mean(x, 0)")
        assert win.update_registry(user_functions=bad, touched={"f"})
        assert win._raw_variables["d"] is new
    finally:
        win.close()


def test_flag_on_navigator_overview_for_derived(qapp, isolated_config, tmp_path, flag_on):
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        win._on_add_derived_variable("d", "a + b")
        plot = win.plot_grid.plots[0]
        win._add_variable_to_plot(plot, "d")
        win._refresh_navigator_overview()
        _settle(qapp, win)  # short file: computed directly, no crash
        win._set_derived_navigator_overview(win._raw_variables["d"])
        ox, oy = win.navigator._curve.getData()
        assert len(ox) == len(oy) > 100 and np.isfinite(ox).all()
        # the overview is the derived series itself (a + b), NaN rows aside
        fin = np.isfinite(oy)
        assert oy[fin].max() == pytest.approx(np.nanmax(a + b)) and oy[fin].min() == pytest.approx(np.nanmin(a + b))
        assert not win._busy_sources  # computed directly: no background job left running
    finally:
        win.close()


def test_flag_on_wide_view_waits_for_pyramid_instead_of_full_fetch(qapp, isolated_config, tmp_path, flag_on, monkeypatch):
    from parquet_analyzer.ui import main_window as mw_module
    from parquet_analyzer.ui import plot_widget as pw_module

    monkeypatch.setattr(pw_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 100)
    monkeypatch.setattr(mw_module, "_DERIVED_NAV_DIRECT_ROWS", 100)
    win, a, b = _win(qapp, isolated_config, tmp_path)
    try:
        win._on_add_derived_variable("d", "a + b")
        d = win._raw_variables["d"]
        plot = win.plot_grid.plots[0]
        win._add_variable_to_plot(plot, "d")
        _settle(qapp, win)
        assert d.pyramid is not None  # built in chunks by the background job
        assert "d" in plot._coarse_series  # drawn from the envelope, not from a full fetch + LTTB
        assert len(plot._series["d"]["curve"].getData()[0]) > 0
        # navigator got the pyramid-based overview without error
        win._refresh_navigator_overview()
        _settle(qapp, win)
    finally:
        win.close()
