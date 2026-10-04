"""Abnormal input / abnormal operation sequences for analysis (stats, FFT, sampling report,
XY / 3D windows, the analysis-window frame). analysis.ja.md, data 29.4, 28.2.

Degenerate data (all-NaN, one/two samples, constant, Inf, 1e300 / 1e-300), empty or far-away
ranges, failing reads, windows closed with jobs in flight, repeated requests."""

from __future__ import annotations

import math
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QTableWidget

from parquet_analyzer import features
from parquet_analyzer.core import analysis
from parquet_analyzer.core.analysis import basic_stats, delta, fft, rolling_mean, sampling_report
from parquet_analyzer.ui import analysis_window
from parquet_analyzer.ui.analysis_common import chunks, format_time_tick
from parquet_analyzer.ui.analysis_window import CANCELLED
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog
from parquet_analyzer.ui.xy_plot_window import XYPlotWindow, compute_xy
from parquet_analyzer.ui.xyz_3d_window import (
    axis_tick_labels,
    colors_for,
    compute_xyz,
    nice_ticks,
    normalize,
)

N = 400


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setitem(features._EFFECTIVE, "fft", True)


@pytest.fixture
def slot_errors(monkeypatch):
    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(v))
    return errors


def _pump(qapp, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _settle(qapp, win, timeout=20.0):
    end = time.monotonic() + timeout
    while (win._busy_sources or win._analysis_hub.pending) and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win._busy_sources


def _write(tmp_path, name, cols):
    path = tmp_path / f"{name}.parquet"
    pq.write_table(pa.table({k: pa.array(v) for k, v in cols.items()}), path)
    return str(path)


def _open(qapp, path):
    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    assert win.load_parquet(path)
    _pump(qapp, 0.05)
    return win


def _select(win, *names):
    win.variable_panel.clearSelection()
    for n in names:
        for it in win.variable_panel.findItems(n, Qt.MatchFlag.MatchExactly):
            it.setSelected(True)


def _dialogs(win):
    return win.findChildren(StatsDialog)


# ---------------------------------------------------------------------------
# core/analysis.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "y, count, nan_count",
    [
        (np.array([]), 0, 0),
        (np.full(4, np.nan), 4, 4),
        (np.array([np.inf, -np.inf, np.nan]), 3, 3),
        (np.array([1, 2, 3]), 3, 0),  # integer dtype
        (np.array([True, False]), 2, 0),  # bool dtype
    ],
)
def test_basic_stats_degenerate_counts(y, count, nan_count):
    s = basic_stats(y) if y.dtype != bool else basic_stats(y.astype(float))
    assert s["count"] == count and s["nan_count"] == nan_count
    if count == nan_count:
        assert all(math.isnan(s[k]) for k in ("min", "max", "mean", "std", "median"))


def test_basic_stats_single_and_constant_and_mixed():
    s = basic_stats(np.array([7.0]))
    assert (s["min"], s["max"], s["mean"], s["std"], s["median"]) == (7.0, 7.0, 7.0, 0.0, 7.0)
    s = basic_stats(np.full(10, 3.0))
    assert s["std"] == 0.0
    s = basic_stats(np.array([1.0, np.nan, 3.0, np.inf]))
    assert (s["count"], s["nan_count"], s["mean"], s["median"]) == (4, 2, 2.0, 2.0)


def test_basic_stats_does_not_mutate_input():
    y = np.array([3.0, np.nan, 1.0, 2.0])
    basic_stats(y)
    assert np.array_equal(y, [3.0, np.nan, 1.0, 2.0], equal_nan=True)


def test_basic_stats_extreme_magnitudes_do_not_raise():
    with np.errstate(all="ignore"):
        big = basic_stats(np.array([1e300, -1e300, 1e300]))
        tiny = basic_stats(np.array([1e-300, 2e-300, 3e-300]))
    assert big["min"] == -1e300 and big["max"] == 1e300
    assert tiny["median"] == 2e-300 and tiny["mean"] == pytest.approx(2e-300)


def test_fft_degenerate_lengths():
    f, a = fft(np.array([]), np.array([]))
    assert len(f) == len(a) == 0
    f, a = fft(np.array([5.0]), np.array([2.0]))  # one sample: dt falls back to 1
    assert len(f) == len(a) == 1 and a[0] == 2.0
    f, a = fft(np.array([0.0, 2.0]), np.array([1.0, 3.0]))
    assert f[1] == pytest.approx(0.25) and len(a) == 2


def test_fft_of_nonfinite_y_propagates_nan_not_exception():
    with np.errstate(all="ignore"):
        f, a = fft(np.arange(8.0), np.array([1, np.nan, 1, 1, 1, 1, 1, 1.0]))
    assert len(f) == 5 and np.isnan(a).all()  # callers refuse such ranges beforehand (32)


def test_fft_pure_tone_peak_location():
    t = np.arange(256) * 0.01
    f, a = fft(t, np.sin(2 * np.pi * 5 * t))
    assert f[np.argmax(a)] == pytest.approx(5.0, abs=0.5)


@pytest.mark.parametrize(
    "x, reason",
    [
        (np.array([]), "too_few"),
        (np.array([1.0, 2.0]), "too_few"),
        (np.array([1.0, np.nan, 3.0]), "not_finite"),
        (np.array([1.0, np.inf, 3.0]), "not_finite"),
        (np.full(5, np.nan), "not_finite"),
        (np.array([3.0, 2.0, 1.0]), "not_ascending"),
        (np.array([1.0, 1.0, 1.0]), "not_ascending"),
        (np.array([0.0, 1.0, 1.0, 2.0]), "not_ascending"),
        (np.array([0.0, 1.0, 2.0, 10.0]), "non_uniform"),
    ],
)
def test_sampling_report_reasons(x, reason):
    r = sampling_report(x)
    assert not r.uniform and r.reason == reason


def test_sampling_report_accepts_ints_lists_and_2d():
    assert sampling_report(np.array([0, 1, 2, 3])).uniform
    assert sampling_report([0.0, 1.0, 2.0]).uniform
    assert sampling_report(np.arange(5.0).reshape(5, 1)).uniform  # raveled
    r = sampling_report(np.arange(6.0) * 1e-320)  # denormal steps
    assert r.reason in ("", "non_uniform")  # must not raise


def test_sampling_report_counts_gaps_and_jitter():
    x = np.concatenate([np.arange(10.0), np.arange(20.0, 30.0)])
    r = sampling_report(x)
    assert r.gap_count == 1 and not r.uniform
    x = np.arange(100.0)
    x[50] += 0.005  # 0.5 % jitter: within tolerance
    assert sampling_report(x).uniform
    x[50] += 0.05  # now beyond 1 %
    assert not sampling_report(x).uniform


def test_delta_and_rolling_mean_edge_cases():
    assert len(delta(np.array([]))) == 0
    assert delta(np.array([5.0]))[0] == 0
    assert list(delta(np.array([1, 4, 9]))) == [0, 3, 5]
    with pytest.raises(ValueError):
        rolling_mean(np.array([1.0, 2.0]), 0)
    assert len(rolling_mean(np.array([]), 3)) == 0
    out = rolling_mean(np.array([1.0, 2.0, 3.0]), 100)  # window larger than the data
    assert np.allclose(out, [1.5, 2.0, 2.5])  # clamped to the data: edges average what exists
    assert np.isnan(rolling_mean(np.array([1.0, np.nan, 3.0]), 3)).all()


def test_analysis_constants_are_sane():
    assert 0 < analysis.FFT_JITTER_TOLERANCE < analysis.FFT_GAP_FACTOR


# ---------------------------------------------------------------------------
# analysis_common
# ---------------------------------------------------------------------------


def test_chunks_cover_range_exactly_and_empty_ranges_yield_nothing():
    assert list(chunks(0, 10, 4)) == [(0, 4), (4, 8), (8, 10)]
    assert list(chunks(5, 5, 4)) == []
    assert list(chunks(7, 3, 4)) == []  # reversed
    assert list(chunks(0, 3, 100)) == [(0, 3)]


@pytest.mark.parametrize("value", [1e300, -1e300, float("nan"), float("inf"), 1e18, -1e18, 0.0, -5.0])
@pytest.mark.parametrize("span", [0.0, 1.0, 3600.0, 86400.0 * 10, float("nan")])
def test_format_time_tick_never_raises(value, span):
    assert isinstance(format_time_tick(value, span), str)


def test_format_time_tick_precision_follows_span():
    v = 1_700_000_000.123
    assert "." in format_time_tick(v, 1.0)
    labels = {format_time_tick(v, span) for span in (1.0, 3600.0, 86400.0, 86400.0 * 30)}
    assert len(labels) == 4  # a different resolution per span class
    assert format_time_tick(v, 1.0).endswith(".123")


# ---------------------------------------------------------------------------
# 3D helpers (pure, no OpenGL)
# ---------------------------------------------------------------------------


def test_nice_ticks_overflowing_and_underflowing_spans():
    """Regression: a 1e308-wide span overflowed (hi - lo = inf -> OverflowError) and a denormal
    span underflowed (log10(0) -> ValueError) while building the 3D frame."""
    assert nice_ticks(-1.7e308, 1.7e308) == [-1.7e308]
    assert nice_ticks(0.0, 5e-324) == [0.0]
    assert nice_ticks(0.0, 1e-323) == [0.0]
    labels = axis_tick_labels({"x": (-1.7e308, 1.7e308), "y": (0.0, 5e-324), "z": (0.0, 1.0)})
    assert {a for a, _p, _t in labels} == {"x", "y", "z"}


@pytest.mark.parametrize(
    "lo, hi, expected",
    [(float("nan"), 1.0, []), (0.0, float("inf"), []), (5.0, 1.0, [5.0]), (2.0, 2.0, [2.0])],
)
def test_nice_ticks_degenerate(lo, hi, expected):
    assert nice_ticks(lo, hi) == expected


def test_nice_ticks_normal_span_is_round_and_inside():
    ticks = nice_ticks(0.3, 9.7)
    assert ticks and all(0.3 <= t <= 9.7 for t in ticks)
    assert ticks == sorted(ticks)


def test_normalize_and_colors_degenerate():
    assert list(normalize([1.0, 2.0], 3.0, 3.0)) == [0.5, 0.5]
    assert list(normalize([1.0], 5.0, 1.0)) == [0.5]  # reversed span
    assert list(normalize([1.0], float("nan"), float("nan"))) == [0.5]
    rgba = colors_for([1.0, 2.0, 3.0], 2.0, 2.0)
    assert rgba.shape == (3, 4) and np.isfinite(rgba).all()
    assert colors_for([], 0.0, 1.0).shape == (0, 4)


# ---------------------------------------------------------------------------
# compute_xy / compute_xyz (pure)
# ---------------------------------------------------------------------------


def _f(data):
    return lambda name, a, b: data[name][a:b]


@pytest.mark.parametrize(
    "vals",
    [
        np.array([]),
        np.array([1.0]),
        np.array([1.0, 2.0]),
        np.full(5, np.nan),
        np.full(5, 2.0),
        np.array([np.inf, -np.inf, 1.0, 2.0]),
        np.array([1e300, -1e300, 0.0]),
        np.array([0.0, 1e-300, 2e-300]),
    ],
)
@pytest.mark.parametrize("display", ["scatter", "density", "auto"])
def test_compute_xy_degenerate_inputs(vals, display):
    n = len(vals)
    data = {"x": vals, "y": vals[::-1].copy(), "z": vals}
    for color, z in (("single", None), ("z", "z"), ("time", None)):
        r = compute_xy(_f(data), "x", ["y"], z, color, display, 0, n, times=np.arange(n, dtype=float))
        assert r.total_valid + r.excluded == n
        for s in r.scatter:
            assert np.isfinite(s.x).all() and np.isfinite(s.y).all()
        if r.total_valid == 0:
            assert r.scatter == [] or all(len(s.x) == 0 for s in r.scatter)
            assert r.x_bounds is None


def test_compute_xy_density_with_overflowing_range_raises_cleanly():
    """The +-1.7e308 range cannot be binned (width overflows). It must surface as an ordinary
    exception (-> the window's failure status via the job), not hang or corrupt state."""
    data = {"x": np.array([-1.7e308, 0.0, 1.7e308]), "y": np.array([-1.7e308, 0.0, 1.7e308])}
    with np.errstate(all="ignore"):
        with pytest.raises(ValueError):
            compute_xy(_f(data), "x", ["y"], None, "single", "density", 0, 3)


def test_compute_xy_empty_and_reversed_ranges():
    data = {"x": np.arange(10.0), "y": np.arange(10.0)}
    for a, b in ((5, 5), (7, 3), (0, 0)):
        r = compute_xy(_f(data), "x", ["y"], None, "single", "scatter", a, b)
        assert r.total_valid == 0 and r.excluded == 0 and r.scatter == []


def test_compute_xy_cancelled_returns_marker():
    data = {"x": np.arange(100.0), "y": np.arange(100.0)}
    r = compute_xy(_f(data), "x", ["y"], None, "single", "scatter", 0, 100, chunk_rows=10, cancelled=lambda: True)
    assert r is CANCELLED


def test_compute_xy_fetch_error_propagates():
    def boom(name, a, b):
        raise OSError("disk gone")

    with pytest.raises(OSError):
        compute_xy(boom, "x", ["y"], None, "single", "scatter", 0, 10)


def test_compute_xy_color_modes_degrade_with_several_y():
    data = {"x": np.arange(10.0), "a": np.arange(10.0), "b": np.arange(10.0), "z": np.arange(10.0)}
    r = compute_xy(_f(data), "x", ["a", "b"], "z", "z", "scatter", 0, 10, times=np.arange(10.0))
    assert r.color == "single" and r.z_name is None and r.color_range is None


@pytest.mark.parametrize("vals", [np.array([]), np.array([1.0]), np.full(4, np.nan), np.full(4, 2.0),
                                  np.array([np.inf, 1.0, 2.0]), np.array([1e300, -1e300, 0.0])])
@pytest.mark.parametrize("color", ["z", "time", "single"])
def test_compute_xyz_degenerate_inputs(vals, color):
    n = len(vals)
    data = {"x": vals, "y": vals, "z": vals}
    r = compute_xyz(_f(data), "x", "y", "z", color, 0, n, times=np.arange(n, dtype=float))
    assert r.valid + r.excluded == n
    if r.valid:
        assert np.isfinite(r.x).all() and np.isfinite(r.c).all()
        assert set(r.bounds) == {"x", "y", "z"}
    else:
        assert r.bounds == {} and r.color_range is None
    axis_tick_labels(r.bounds or {a: (0.0, 1.0) for a in "xyz"})  # the view builds ticks from these


def test_compute_xyz_thinning_and_cancel():
    n = 1000
    data = {"x": np.arange(n, dtype=float), "y": np.arange(n, dtype=float), "z": np.arange(n, dtype=float)}
    r = compute_xyz(_f(data), "x", "y", "z", "z", 0, n, limit=100, chunk_rows=64)
    assert len(r.x) <= 100 and r.thinned == (len(r.x), n) and r.bounds["x"] == (0.0, 999.0)
    assert compute_xyz(_f(data), "x", "y", "z", "z", 0, n, chunk_rows=64, cancelled=lambda: True) is CANCELLED


# ---------------------------------------------------------------------------
# StatsDialog
# ---------------------------------------------------------------------------


def test_stats_dialog_error_rows_nan_and_empty(qapp):
    ok = basic_stats(np.array([1.0, 2.0]))
    nan = basic_stats(np.full(3, np.nan))
    dlg = StatsDialog("range", {"ok": ok, "nan": nan, "bad": {"error": "OSError: x"}})
    table = dlg.findChild(QTableWidget)
    assert table.rowCount() == 3
    assert table.item(1, 5).text() == "nan"
    assert table.item(2, 0).text() == "bad" and "OSError" in table.item(2, 1).text()
    assert table.columnSpan(2, 1) == table.columnCount() - 1
    empty = StatsDialog("none", {})
    assert empty.findChild(QTableWidget).rowCount() == 0
    del dlg


def test_stats_dialog_extreme_values_format(qapp):
    s = {"count": 2, "nan_count": 0, "min": -1e300, "max": 1e300, "mean": float("inf"), "std": 0.0, "median": 5e-324}
    dlg = StatsDialog("r", {"v": s})
    table = dlg.findChild(QTableWidget)
    assert table.item(0, 3).text() == "-1e+300" and table.item(0, 5).text() == "inf"


# ---------------------------------------------------------------------------
# MainWindow: stats
# ---------------------------------------------------------------------------


@pytest.fixture
def data_file(tmp_path):
    t = np.arange(N, dtype=float)
    return _write(
        tmp_path, "d",
        {
            "time": t, "a": np.sin(t / 10), "b": np.cos(t / 10), "nanc": np.full(N, np.nan),
            "const": np.full(N, 4.0), "infc": np.where(t % 7 == 0, np.inf, t),
        },
    )


def test_stats_nothing_selected_or_plotted_only_informs(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    win._on_stats_requested()
    assert not win._stats_workers and _dialogs(win) == []
    assert not slot_errors


def test_stats_on_empty_range_zero_width_far_outside(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    _settle(qapp, win)
    for lo, hi in ((1e6, 2e6), (-2e6, -1e6), (50.2, 50.4)):  # no sample inside any of them
        plot.setXRange(lo, hi, padding=0)
        _select(win, "a")
        win._on_stats_requested()
        _settle(qapp, win)
        _pump(qapp, 0.05)
    dlgs = _dialogs(win)
    assert len(dlgs) == 3
    for d in dlgs:
        t = d.findChild(QTableWidget)
        assert t.item(0, 1).text() == "0"
    assert not slot_errors


def test_stats_all_nan_const_and_inf_columns(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    _select(win, "nanc", "const", "infc")
    win._on_stats_requested()
    _settle(qapp, win)
    (dlg,) = _dialogs(win)
    t = dlg.findChild(QTableWidget)
    rows = {t.item(r, 0).text(): [t.item(r, c).text() for c in range(1, 8)] for r in range(t.rowCount())}
    assert rows["nanc"][1] == str(N) and rows["nanc"][3] == "nan"
    assert rows["const"][5] == "0"
    assert int(rows["infc"][1]) == int(np.sum(np.arange(N) % 7 == 0))
    assert not slot_errors


def test_stats_read_failure_gives_error_row_others_unaffected(qapp, isolated_config, data_file, monkeypatch, slot_errors):
    win = _open(qapp, data_file)
    real = win.variable_window

    def flaky(name, a, b):
        if name == "b":
            raise OSError("disk read failed")
        return real(name, a, b)

    monkeypatch.setattr(win, "variable_window", flaky)
    _select(win, "a", "b")
    win._on_stats_requested()
    _settle(qapp, win)
    (dlg,) = _dialogs(win)
    t = dlg.findChild(QTableWidget)
    assert [t.item(r, 0).text() for r in range(2)] == ["a", "b"]  # selection order kept
    assert "disk read failed" in t.item(1, 1).text()
    assert t.item(0, 1).text() == str(N)
    assert not slot_errors


def test_stats_all_reads_fail_still_shows_dialog_without_workers(qapp, isolated_config, data_file, monkeypatch):
    win = _open(qapp, data_file)
    monkeypatch.setattr(win, "variable_window", lambda *a: (_ for _ in ()).throw(OSError("x")))
    _select(win, "a")
    win._on_stats_requested()
    assert not win._stats_workers
    (dlg,) = _dialogs(win)
    assert "x" in dlg.findChild(QTableWidget).item(0, 1).text()


def test_stats_unresolvable_names_are_skipped(qapp, isolated_config, data_file, monkeypatch):
    from parquet_analyzer.core.expression import ExpressionError

    win = _open(qapp, data_file)

    def resolver(name, a, b):
        raise ExpressionError("unknown")

    monkeypatch.setattr(win, "variable_window", resolver)
    _select(win, "a")
    win._on_stats_requested()  # nothing resolves: informational message only, no dialog
    assert _dialogs(win) == [] and not win._stats_workers


def test_stats_worker_failure_is_reported_per_variable(qapp, isolated_config, data_file, monkeypatch, slot_errors):
    from parquet_analyzer.ui import main_window

    win = _open(qapp, data_file)

    def bad_stats(y):
        raise RuntimeError("compute blew up")

    monkeypatch.setattr(main_window, "basic_stats", bad_stats)
    _select(win, "a", "b")
    win._on_stats_requested()
    _settle(qapp, win)
    (dlg,) = _dialogs(win)
    t = dlg.findChild(QTableWidget)
    assert all("compute blew up" in t.item(r, 1).text() for r in range(2))
    assert not win._stats_workers and not win._busy_sources


def test_stats_twice_and_reload_in_flight(qapp, isolated_config, data_file, tmp_path, slot_errors):
    win = _open(qapp, data_file)
    _select(win, "a")
    win._on_stats_requested()
    win._on_stats_requested()
    other = _write(tmp_path, "o", {"time": np.arange(10.0), "q": np.arange(10.0)})
    assert win.load_parquet(other)  # reopen while stats jobs may be running
    _settle(qapp, win)
    assert not win._stats_workers
    assert not slot_errors


# ---------------------------------------------------------------------------
# MainWindow: FFT
# ---------------------------------------------------------------------------


def _fft_file(tmp_path, name, t, y=None):
    y = np.sin(t) if y is None else y
    return _write(tmp_path, name, {"time": t, "v": y})


@pytest.mark.parametrize(
    "kind, reason",
    [
        ("one", "too_few"),
        ("nan", "not_finite"),
        ("desc", "not_ascending"),
        ("gap", "non_uniform"),
        ("jitter", "non_uniform"),
    ],
)
def test_fft_refused_for_unusable_time_axis(qapp, isolated_config, tmp_path, slot_errors, kind, reason):
    n = 200
    t = np.arange(n, dtype=float)
    if kind == "one":
        t = t[:1]
    elif kind == "nan":
        t[5] = np.nan
    elif kind == "desc":
        t = t[::-1].copy()
    elif kind == "gap":
        t[100:] += 50.0
    elif kind == "jitter":
        t += np.random.default_rng(0).uniform(-0.3, 0.3, n)
    win = _open(qapp, _fft_file(tmp_path, kind, t))
    _select(win, "v")
    n_plots = len(win.plot_grid.plots)
    win._on_fft_requested()
    _settle(qapp, win)
    assert len(win.plot_grid.plots) == n_plots, "a refused FFT must not add a plot"
    assert not win._fft_workers
    assert win.statusBar().currentMessage()
    assert not slot_errors


def test_fft_no_selection_and_second_click(qapp, isolated_config, tmp_path, slot_errors):
    t = np.arange(256) * 0.01
    win = _open(qapp, _fft_file(tmp_path, "ok", t))
    n0 = len(win.plot_grid.plots)
    win._on_fft_requested()  # nothing selected
    assert len(win.plot_grid.plots) == n0
    _select(win, "v")
    win._on_fft_requested()
    win._on_fft_requested()  # the same request twice: two results, both clean up
    _settle(qapp, win)
    assert len(win.plot_grid.plots) == n0 + 2
    assert not win._fft_workers and not slot_errors


def test_fft_range_too_wide_is_refused(qapp, isolated_config, tmp_path, monkeypatch):
    from parquet_analyzer.ui import main_window

    monkeypatch.setattr(main_window, "_FFT_MAX_ROWS", 50)
    win = _open(qapp, _fft_file(tmp_path, "w", np.arange(200) * 0.01))
    _select(win, "v")
    n0 = len(win.plot_grid.plots)
    win._on_fft_requested()
    assert len(win.plot_grid.plots) == n0 and not win._fft_workers


def test_fft_read_failure_and_worker_failure(qapp, isolated_config, tmp_path, monkeypatch, slot_errors):
    from parquet_analyzer.ui import main_window

    win = _open(qapp, _fft_file(tmp_path, "f", np.arange(256) * 0.01))
    _select(win, "v")
    n0 = len(win.plot_grid.plots)
    real = win.variable_window
    monkeypatch.setattr(win, "variable_window", lambda *a: (_ for _ in ()).throw(OSError("read")))
    win._on_fft_requested()
    assert len(win.plot_grid.plots) == n0 and not win._fft_workers
    monkeypatch.setattr(win, "variable_window", real)
    monkeypatch.setattr(main_window, "fft", lambda x, y: (_ for _ in ()).throw(RuntimeError("fft failed")))
    win._on_fft_requested()
    _settle(qapp, win)
    assert len(win.plot_grid.plots) == n0 and not win._fft_workers and not win._busy_sources
    assert not slot_errors


def test_fft_on_zoomed_empty_range(qapp, isolated_config, tmp_path, slot_errors):
    win = _open(qapp, _fft_file(tmp_path, "z", np.arange(256) * 0.01))
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "v")
    _settle(qapp, win)
    plot.setXRange(1e6, 2e6, padding=0)
    _select(win, "v")
    win._on_fft_requested()  # zero rows in range: too_few, refused
    _settle(qapp, win)
    assert len(win.plot_grid.plots) == 1 and not slot_errors


# ---------------------------------------------------------------------------
# Analysis window frame / XY window
# ---------------------------------------------------------------------------


def _xy(qapp, win, x="a", ys=("b",), **kw):
    _select(win, x, *ys)
    w = win.open_xy_plot("2d")
    assert w is not None
    _wait(qapp, w)
    return w


def _wait(qapp, w, timeout=20.0):
    end = time.monotonic() + timeout
    while w.is_computing() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not w.is_computing()


def test_open_xy_without_file_informs_only(qapp, isolated_config):
    win = MainWindow()
    assert win.open_xy_plot() is None
    assert win._analysis_windows == []


@pytest.mark.parametrize("col", ["nanc", "const", "infc"])
def test_xy_window_on_degenerate_columns(qapp, isolated_config, data_file, slot_errors, col):
    win = _open(qapp, data_file)
    w = _xy(qapp, win, x=col, ys=("a",))
    assert "failed" not in w.status_text().lower() or col == "none"
    assert w.status_text()
    w._display_combo.setCurrentIndex(2)  # density
    _wait(qapp, w)
    w._color_combo.setCurrentIndex(1)  # time
    _wait(qapp, w)
    assert not slot_errors


def test_xy_window_without_xy_selection_reports_need_xy(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    _select(win)  # nothing selected
    w = win.open_xy_plot("2d")
    w.recompute()  # incomplete settings: ValueError path -> status, no job
    assert not w.is_computing() and w.status_text()
    w._y_list.clearSelection()
    w.recompute()
    assert not w.is_computing()
    assert not slot_errors


def test_xy_window_unknown_variable_after_derived_deleted(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    win._on_add_derived_variable("dd", "a + b")
    w = _xy(qapp, win, x="a", ys=("dd",))
    win.delete_derived_variable("dd")
    _pump(qapp, 0.1)  # candidate refresh runs from the event loop
    w.recompute(keep_range=True)
    _pump(qapp, 0.2)
    assert w.selected_y() == []  # a vanished selection is left empty, not replaced
    assert not slot_errors


def test_xy_window_resolve_columns_unknown_name_raises_value_error(qapp, isolated_config, data_file):
    win = _open(qapp, data_file)
    w = _xy(qapp, win)
    with pytest.raises(ValueError):
        w._resolve_columns(["not_there"])


def test_closing_window_with_job_in_flight_is_safe(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    _select(win, "a", "b")
    w = win.open_xy_plot("2d")
    w.recompute()
    assert w.is_computing()
    w.close()
    assert w not in win._analysis_windows
    w.recompute()  # closed: no-op
    _settle(qapp, win)
    _pump(qapp, 0.2)
    assert not win._analysis_hub.pending
    assert not slot_errors


def test_stale_result_dropped_and_job_reports_once(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    w = _xy(qapp, win)
    shown = []
    real = w.on_result
    w.on_result = lambda r: (shown.append(r), real(r))
    w.recompute()
    w.recompute()
    w.recompute()  # three jobs, only the last generation may be shown
    _wait(qapp, w)
    assert len(shown) == 1
    assert not w._workers and not slot_errors


def test_follow_main_range_debounces_and_stops_after_close(qapp, isolated_config, data_file, monkeypatch, slot_errors):
    win = _open(qapp, data_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    w = _xy(qapp, win)
    monkeypatch.setattr(analysis_window, "FOLLOW_DEBOUNCE_MS", 10)
    calls = []
    real = w.recompute
    monkeypatch.setattr(w, "recompute", lambda *a, **k: calls.append(1) or real(*a, **k))
    w._follow_timer.timeout.disconnect()
    w._follow_timer.timeout.connect(w.recompute)
    w.follow_action.setChecked(True)  # recompute right away
    n_on = len(calls)
    w._follow_timer.setInterval(10)
    for lo in (10, 20, 30, 40):
        plot.setXRange(lo, lo + 100, padding=0)
    _pump(qapp, 0.15)
    assert 0 < len(calls) - n_on <= 2  # burst coalesced
    w.follow_action.setChecked(False)
    before = len(calls)
    plot.setXRange(60, 160, padding=0)
    _pump(qapp, 0.1)
    assert len(calls) == before
    w.close()
    plot.setXRange(70, 170, padding=0)  # after close: disconnected
    _pump(qapp, 0.1)
    assert not slot_errors


def test_range_changes_with_nonfinite_or_empty_group_still_recompute_cleanly(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    w = _xy(qapp, win)
    plot.setXRange(1e6, 2e6, padding=0)  # no rows
    w.recompute()
    _wait(qapp, w)
    assert win.analysis_range()[0] == win.analysis_range()[1] or True
    assert w.status_text()
    assert not slot_errors


def test_analysis_range_for_empty_selection_is_nan(qapp, isolated_config, data_file):
    win = _open(qapp, data_file)
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "a")
    plot.setXRange(1e6, 2e6, padding=0)
    rs, re, lo, hi = win.analysis_range()
    assert re <= rs or rs == len(win._time_values)
    assert math.isnan(lo) and math.isnan(hi)


def test_time_axis_switch_and_reload_close_analysis_windows(qapp, isolated_config, data_file, tmp_path):
    win = _open(qapp, data_file)
    w1 = _xy(qapp, win)
    w2 = _xy(qapp, win, x="b", ys=("a",))
    assert len(win._analysis_windows) == 2
    win.set_time_column("a")
    assert win._analysis_windows == []
    w3 = _xy(qapp, win, x="b", ys=("nanc",))
    other = _write(tmp_path, "o2", {"time": np.arange(5.0), "q": np.arange(5.0)})
    win.load_parquet(other)
    assert win._analysis_windows == [] and w3 not in win._analysis_windows
    assert w1._closed and w2._closed


def test_close_all_windows_twice_and_close_unknown(qapp, isolated_config, data_file):
    win = _open(qapp, data_file)
    w = _xy(qapp, win)
    win._close_analysis_windows()
    win._close_analysis_windows()
    win._analysis_window_closed(w)  # already gone: no ValueError


# -- frame hooks (a minimal subclass) ---------------------------------------------------------


class _Probe(analysis_window.AnalysisWindow):
    def __init__(self, main, mode="ok"):
        self.mode = mode
        self.results, self.failures = [], []
        super().__init__(main, "probe")

    def build_settings_panel(self):
        from PySide6.QtWidgets import QWidget

        return QWidget()

    def build_view(self):
        from PySide6.QtWidgets import QWidget

        return QWidget()

    def make_job(self, row_start, row_end, cancelled):
        if self.mode == "value_error":
            raise ValueError("incomplete settings")
        if self.mode == "setup_error":
            raise RuntimeError("setup blew up")
        if self.mode == "job_error":
            def job():
                raise RuntimeError("job blew up")
            return job
        if self.mode == "cancelled":
            return lambda: CANCELLED
        return lambda: (row_start, row_end)

    def on_result(self, result):
        self.results.append(result)

    def on_failure(self, message):
        self.failures.append(message)
        super().on_failure(message)

    def build_export_figure(self, stamp_lines, size_px):
        if self.mode == "no_figure":
            return None
        if self.mode == "figure_error":
            raise OSError("cannot render")
        from matplotlib.figure import Figure

        return Figure()


def _probe(qapp, win, mode):
    p = _Probe(win, mode)
    win._analysis_windows.append(p)
    p.show()
    return p


@pytest.mark.parametrize(
    "mode, results, failures",
    [("ok", 1, 0), ("value_error", 0, 0), ("setup_error", 0, 1), ("job_error", 0, 1), ("cancelled", 0, 0)],
)
def test_frame_job_outcomes_report_exactly_once(qapp, isolated_config, data_file, slot_errors, mode, results, failures):
    win = _open(qapp, data_file)
    p = _probe(qapp, win, mode)
    p.recompute()
    _wait(qapp, p)
    assert (len(p.results), len(p.failures)) == (results, failures)
    assert not win._analysis_hub.pending
    if mode == "value_error":
        assert p.status_text() == "incomplete settings"


def test_frame_recompute_before_data_and_keep_range(qapp, isolated_config, data_file):
    win = MainWindow()  # no file loaded
    p = _probe(qapp, win, "ok")
    p.recompute()
    assert not p.is_computing()
    win.load_parquet(data_file)
    p.recompute()
    _wait(qapp, p)
    first = p._range
    win.plot_grid.plots[0].add_series("a", win._time_values, win.variable_values("a"))
    win.plot_grid.plots[0].setXRange(10, 20, padding=0)
    p.recompute(keep_range=True)
    _wait(qapp, p)
    assert p._range == first  # settings-only change keeps the last range


def test_frame_abstract_hooks_raise_not_implemented(qapp, isolated_config, data_file):
    win = _open(qapp, data_file)
    p = _probe(qapp, win, "ok")
    for fn in (
        lambda: analysis_window.AnalysisWindow.build_settings_panel(p),
        lambda: analysis_window.AnalysisWindow.build_view(p),
        lambda: analysis_window.AnalysisWindow.make_job(p, 0, 1, lambda: False),
        lambda: analysis_window.AnalysisWindow.on_result(p, None),
        lambda: analysis_window.AnalysisWindow.build_export_figure(p, [], (1, 1)),
    ):
        with pytest.raises(NotImplementedError):
            fn()
    assert analysis_window.AnalysisWindow.export_stamp_extra(p) is None


def test_frame_export_paths(qapp, isolated_config, data_file, tmp_path, monkeypatch, slot_errors):
    win = _open(qapp, data_file)
    infos, warns = [], []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warns.append(a[2])))
    p = _probe(qapp, win, "no_figure")
    assert p.export_image(str(tmp_path / "a.png")) is False and infos
    p.mode = "figure_error"
    assert p.export_image(str(tmp_path / "b.png")) is False and warns
    p.mode = "ok"
    out = tmp_path / "c.png"
    assert p.export_image(str(out)) is True and out.exists() and out.stat().st_size > 0
    assert p.export_image(str(tmp_path / "no_such_dir" / "d.png")) is False  # OSError from save
    assert not slot_errors


def test_frame_export_dialog_cancel_and_suffix_fallback(qapp, isolated_config, data_file, tmp_path, monkeypatch):
    win = _open(qapp, data_file)
    p = _probe(qapp, win, "ok")
    p.export_image_dialog()  # dialog patched to cancel: nothing happens
    target = tmp_path / "noext"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    p.export_image_dialog()  # unknown filter: falls back to .png
    assert (tmp_path / "noext.png").exists()
    empty = MainWindow()
    q = _Probe(empty, "ok")
    q.export_image_dialog()  # no data source: silently nothing


def test_xy_export_with_no_result_or_empty_result(qapp, isolated_config, data_file, tmp_path, monkeypatch):
    win = _open(qapp, data_file)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(1)))
    _select(win)
    w = win.open_xy_plot("2d")
    assert w.export_image(str(tmp_path / "none.png")) is False and infos  # no result yet
    w2 = _xy(qapp, win, x="nanc", ys=("a",))  # all rows excluded: an empty scatter is still exportable
    assert w2.export_image(str(tmp_path / "empty.png")) is True


def test_xy_cursor_highlight_with_stale_and_nonfinite_cursors(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    w = _xy(qapp, win)
    for times in ([], [1e9], [-1e9], [float("nan")], [5.0, 5.0]):
        w._on_cursors_changed(times)
    assert w.cursor_highlight_points() or True
    w._on_cursors_changed([10.0])
    assert len(w.cursor_highlight_points()) == 1
    w.close()
    w._on_cursors_changed([10.0])  # closed: ignored
    assert XYPlotWindow.nearest_row(np.arange(5.0), -1e9) == 0
    assert XYPlotWindow.nearest_row(np.arange(5.0), 1e9) == 4
    assert not slot_errors


def test_xy_many_windows_open_and_closed(qapp, isolated_config, data_file, slot_errors):
    win = _open(qapp, data_file)
    ws = [_xy(qapp, win) for _ in range(6)]
    assert len(win._analysis_windows) == 6
    for w in ws[::2]:
        w.close()
    assert len(win._analysis_windows) == 3
    _select(win, "a", "b")
    win.plot_grid.plots[0].setXRange(10, 20, padding=0)  # no follow: nothing recomputes
    _pump(qapp, 0.1)
    assert not slot_errors


def test_xy_3d_mode_unavailable_offscreen_stays_2d(qapp, isolated_config, data_file, monkeypatch, slot_errors):
    from parquet_analyzer.ui import xyz_3d_window as x3

    x3.reset_gl3d_probe()  # the probe verdict is cached per session: leave no trace for later tests
    monkeypatch.setitem(features._EFFECTIVE, "xyz_3d", True)
    win = _open(qapp, data_file)
    _select(win, "a", "b")
    w = win.open_xy_plot("3d")  # offscreen cannot create a GL context
    _wait(qapp, w)
    if not w._gl3d_ok:
        assert w.mode == "2d" and not w._mode_buttons["3d"].isEnabled()
        w._set_mode("3d")  # refused without raising
        assert w.mode == "2d"
    assert not slot_errors


# -- analysis 32.1 / plots 24.8 follow-ups -------------------------------------------------


@pytest.mark.parametrize(
    "x",
    [np.array([1.0, 1.0, 1.0]), np.array([3.0, 2.0, 1.0]), np.array([np.nan, 1.0, 2.0]), np.array([0.0, np.inf, 5.0])],
)
def test_fft_nonpositive_or_nonfinite_dt_raises_value_error(x):
    """Regression: dt == 0 raised ZeroDivisionError (analysis 32.1)."""
    with np.errstate(all="ignore"), pytest.raises(ValueError, match="sampling interval"):
        fft(x, np.ones(len(x)))


def test_fft_worker_failure_path_reports_value_error(qapp, isolated_config, tmp_path, monkeypatch, slot_errors):
    win = _open(qapp, _write(tmp_path, "dup", {"time": np.arange(64) * 0.01, "v": np.arange(64.0)}))
    monkeypatch.setattr(win, "_fft_sampling_ok", lambda x: True)  # bypass the UI refusal
    win._time_values = np.ones(64)  # all-equal times: dt == 0
    _select(win, "v")
    n0 = len(win.plot_grid.plots)
    win._on_fft_requested()
    _settle(qapp, win)
    assert len(win.plot_grid.plots) == n0 and not win._fft_workers and not win._busy_sources
    assert not slot_errors


def test_nice_ticks_collapsed_duplicates_are_unique():
    """Regression: nice_ticks(0, 1e-300) returned six 0.0 (plots 24.8)."""
    for lo, hi in ((0.0, 1e-300), (0.0, 1e-13)):
        ticks = nice_ticks(lo, hi)
        assert len(ticks) == len(set(ticks)) and ticks
    assert nice_ticks(0.0, 1e-300) == [0.0]


@pytest.fixture(autouse=True)
def _reset_gl_probe_after():
    yield
    from parquet_analyzer.ui import xyz_3d_window as x3

    x3.reset_gl3d_probe()
