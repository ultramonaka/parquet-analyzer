"""Histogram window: display kinds, time filter, duration curve and trend (analysis.ja.md 5.48 / 42.10-42.12, 42.14)."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyqtgraph as pg
import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QFileDialog, QMessageBox

from parquet_analyzer import features
from parquet_analyzer.core.histogram import HIST_TREND_BAR_MAX
from parquet_analyzer.ui import histogram_window
from parquet_analyzer.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)


@pytest.fixture(autouse=True)
def _utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def ops(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(histogram_window, "log_op", lambda action, **kw: calls.append((action, kw)))
    return calls


def _drain(qapp, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _wait(qapp, win, timeout=30.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


STEP_S = 10
N_DAYS = 3
ROWS = N_DAYS * 86400 // STEP_S


def _write(tmp_path, name, t, **cols):
    table = pa.table({"time": t, **cols})
    path = tmp_path / name
    pq.write_table(table, path)
    return str(path)


@pytest.fixture
def multiday_path(tmp_path):
    """Mon 2026-03-02 .. Wed, one row per 10 s; `power` = the hour of day (0..23), `const` = 2."""
    t = np.datetime64("2026-03-02T00:00:00", "s") + np.arange(ROWS, dtype="int64") * np.timedelta64(STEP_S, "s")
    hour = (np.arange(ROWS) * STEP_S // 3600 % 24).astype(float)
    return _write(tmp_path, "multiday.parquet", pa.array(t), power=hour, const=np.full(ROWS, 2.0))


@pytest.fixture
def gap_path(tmp_path):
    """Like `multiday_path`, but day 2 lacks 12:00-18:00 (6 h): that day is incomplete (18/24 = 0.75)."""
    t = np.datetime64("2026-03-02T00:00:00", "s") + np.arange(ROWS, dtype="int64") * np.timedelta64(STEP_S, "s")
    sec = np.arange(ROWS) * STEP_S
    keep = ~((sec >= 86400 + 12 * 3600) & (sec < 86400 + 18 * 3600))
    return _write(tmp_path, "gap.parquet", pa.array(t[keep]), power=np.full(int(keep.sum()), 5.0))


@pytest.fixture
def numeric_path(tmp_path):
    t = np.arange(ROWS, dtype=float) * STEP_S
    return _write(tmp_path, "numeric.parquet", pa.array(t), power=(t // 3600 % 24))


def _mw(qapp, isolated_config, path, var="power"):
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    win._add_variable_to_plot(win.plot_grid.plots[0], var)
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    return win


@pytest.fixture
def mw(qapp, isolated_config, multiday_path):
    win = _mw(qapp, isolated_config, multiday_path)
    yield win
    win.close()


@pytest.fixture
def mw_gap(qapp, isolated_config, gap_path):
    win = _mw(qapp, isolated_config, gap_path)
    yield win
    win.close()


@pytest.fixture
def mw_numeric(qapp, isolated_config, numeric_path):
    win = _mw(qapp, isolated_config, numeric_path)
    yield win
    win.close()


def _open(qapp, mw, names=("power",)):
    w = mw.open_histogram()
    w._var_list.blockSignals(True)
    for i in range(w._var_list.count()):
        w._var_list.item(i).setSelected(w._var_list.item(i).text() in names)
    w._var_list.blockSignals(False)
    w.recompute()
    _wait(qapp, w)
    return w


def _set(widget, data):
    widget.setCurrentIndex(widget.findData(data))


def _view(qapp, w, view):
    _set(w._view_combo, view)
    _wait(qapp, w)


def _filter(qapp, w, start="09:00", end="17:00", preset=None, wait=True):
    h, m = (int(x) for x in start.split(":"))
    h2, m2 = (int(x) for x in end.split(":"))
    w._tf_check.blockSignals(True)
    w._tf_check.setChecked(True)
    w._tf_check.blockSignals(False)
    w._tf_start.blockSignals(True)
    w._tf_start.setTime(QTime(h, m))
    w._tf_start.blockSignals(False)
    w._tf_end.setTime(QTime(h2, m2))  # signals on: recomputes
    if preset:
        _set(w._tf_preset, preset)
    if wait:
        _wait(qapp, w)


# -- the display combo (42.11) ----------------------------------------------------------


def test_view_combo_is_first_in_the_panel(qapp, mw):
    w = _open(qapp, mw)
    assert w._view_combo.objectName() == "histogram_view"
    first = w._flow.itemAt(0).widget()
    assert w._view_combo in first.findChildren(type(w._view_combo))
    assert [w._view_combo.itemData(i) for i in range(w._view_combo.count())] == ["dist", "duration", "trend"]
    assert w.view() == "dist"  # the default
    assert w._result.settings.view == "dist"


def test_fields_enabled_per_view(qapp, mw):
    w = _open(qapp, mw)
    # distribution: everything that applies to the aggregation, bins and the vertical axis
    assert w._mode_group.isEnabled() and w._weight_group.isEnabled()
    assert w._bins_group.isEnabled() and w._yaxis_group.isEnabled()
    _view(qapp, w, "duration")
    assert w._mode_group.isEnabled() and w._weight_group.isEnabled() and w._yaxis_group.isEnabled()
    assert not w._bins_group.isEnabled()  # a duration curve has no bins
    _view(qapp, w, "trend")
    assert not w._mode_group.isEnabled() and not w._weight_group.isEnabled()
    assert not w._bins_group.isEnabled() and not w._yaxis_group.isEnabled()
    # disabled, not hidden (42.11)
    assert not w._mode_group.isHidden() and not w._bins_group.isHidden()
    # the time filter applies to every view
    w._tf_check.setChecked(True)
    _wait(qapp, w)
    assert w._tf_check.isEnabled() and w._tf_range_group.isEnabled() and w._tf_days_group.isEnabled()


def test_trend_forces_buckets_and_leaving_restores_the_aggregation(qapp, mw, ops):
    w = _open(qapp, mw)
    assert w._mode_combo.currentData() == "plain"
    _view(qapp, w, "trend")
    assert w._mode_combo.currentData() == "bucket"
    assert w._result.settings.mode == "bucket" and w._result.settings.view == "trend"
    _view(qapp, w, "duration")
    assert w._mode_combo.currentData() == "plain"  # back to the previous aggregation
    assert w._result.settings.mode == "plain"
    # a bucketed aggregation chosen before the trend stays bucketed afterwards
    _set(w._mode_combo, "bucket")
    _wait(qapp, w)
    _view(qapp, w, "trend")
    _view(qapp, w, "dist")
    assert w._mode_combo.currentData() == "bucket"


def test_view_change_recomputes_and_is_logged(qapp, mw, ops):
    w = _open(qapp, mw)
    ops.clear()
    gen = w._generation
    _set(w._view_combo, "duration")
    assert w._generation > gen  # recompute started at once
    _wait(qapp, w)
    assert ("histogram_settings", {"item": "view", "value": "duration"}) in ops
    compute = [kw for a, kw in ops if a == "histogram_compute"]
    assert compute and compute[-1]["view"] == "duration" and compute[-1]["time_filter"]["enabled"] is False
    assert w._result.variables[0].duration_x is not None


# -- time filter (42.10) ---------------------------------------------------------------


def test_filter_off_by_default_and_changes_nothing(qapp, mw):
    w = _open(qapp, mw)
    assert not w._tf_check.isChecked()
    assert w._result.variables[0].n == ROWS
    assert w._result.target_seconds is None
    assert not w._tf_range_group.isEnabled()  # fields enabled only with the checkbox


def test_filter_counts_only_the_window_and_status_says_so(qapp, mw, ops):
    w = _open(qapp, mw)
    ops.clear()
    _filter(qapp, w, "09:00", "17:00")
    var = w._result.variables[0]
    assert var.n == N_DAYS * 8 * 3600 // STEP_S
    assert var.counts.sum() == var.n
    assert w._result.edges[0] >= 9 - 1e-9 and w._result.edges[-1] <= 16 + 1e-9  # the hours 9..16 only
    assert "9:00" in w.status_text() and "17:00" in w.status_text()
    assert "24" in w.status_text()  # target 3 days x 8 h = 24 h (minus nothing: ends exactly at 17:00)
    log = [kw for a, kw in ops if a == "histogram_settings" and kw["item"] == "time_filter"]
    assert log and log[-1]["value"]["enabled"] is True
    assert log[-1]["value"]["start"] == "09:00" and log[-1]["value"]["end"] == "17:00"


def test_weekday_preset_sets_toggles_and_manual_toggle_makes_it_custom(qapp, mw):
    w = _open(qapp, mw)
    _filter(qapp, w, "09:00", "17:00", preset="weekdays")
    assert [b.isChecked() for b in w._tf_day_buttons] == [True] * 5 + [False] * 2
    _set(w._tf_preset, "weekend")
    _wait(qapp, w)
    assert [b.isChecked() for b in w._tf_day_buttons] == [False] * 5 + [True] * 2
    w._tf_day_buttons[0].click()  # a manual toggle
    _wait(qapp, w)
    assert w._tf_preset.currentData() == "custom"
    # "custom" keeps the toggles as they are
    assert [b.isChecked() for b in w._tf_day_buttons] == [True] + [False] * 4 + [True] * 2
    _set(w._tf_preset, "all")
    _wait(qapp, w)
    assert all(b.isChecked() for b in w._tf_day_buttons)


def test_weekday_filter_selects_days(qapp, mw):
    w = _open(qapp, mw)
    _filter(qapp, w, "00:00", "00:00", preset="custom")  # whole day, weekdays as they are (all)
    for b in w._tf_day_buttons[1:]:  # keep Monday only
        b.setChecked(False)
    _wait(qapp, w)
    assert w._result.variables[0].n == 86400 // STEP_S
    assert w._tf_preset.currentData() == "custom"


def test_no_weekday_does_not_compute_and_says_why(qapp, mw, ops):
    w = _open(qapp, mw)
    assert w._result is not None
    _filter(qapp, w, "09:00", "17:00")
    for b in w._tf_day_buttons:
        b.setChecked(False)
    _drain(qapp)
    assert not w.is_computing()
    assert w._result is None  # the previous result is gone (42.2)
    assert w.status_text() and "weekday" in w.status_text().lower() or "曜日" in w.status_text()
    assert not any(a == "histogram_compute" and not kw["time_filter"]["weekdays"] for a, kw in ops)


def test_numeric_time_axis_disables_weekdays_but_keeps_the_range(qapp, mw_numeric):
    w = _open(qapp, mw_numeric)
    assert not w._time_is_datetime()
    _filter(qapp, w, "09:00", "17:00")
    assert not w._tf_days_group.isEnabled()  # weekdays make no sense on a numeric axis
    assert w._tf_days_group.toolTip()  # ... and the tooltip says why
    assert w._tf_range_group.isEnabled()
    assert w._result.variables[0].n == N_DAYS * 8 * 3600 // STEP_S


def test_filter_applies_to_bucketed_results_and_absent_buckets(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _set(w._agg_combo, "integral")
    _wait(qapp, w)
    assert w._result.bucket_count == N_DAYS
    # Tuesday only: Monday and Wednesday have no target time -> absent
    _filter(qapp, w, "09:00", "17:00", preset="custom")
    for d, b in enumerate(w._tf_day_buttons):
        b.blockSignals(True)
        b.setChecked(d == 1)
        b.blockSignals(False)
    w._tf_day_buttons[1].toggled.emit(True)
    _wait(qapp, w)
    r = w._result
    assert r.bucket_count == 1 and len(r.bucket_start) == 1
    assert np.isclose(r.variables[0].bucket_values[0], np.mean(np.arange(9, 17)) * 8, rtol=1e-3)


# -- duration curve (42.11) ------------------------------------------------------------


def test_duration_curve_is_drawn_without_mean_and_median_lines(qapp, mw):
    w = _open(qapp, mw)
    _set(w._weight_combo, "time")
    _view(qapp, w, "duration")
    var = w._result.variables[0]
    assert var.duration_x is not None and var.duration_x[-1] == pytest.approx((ROWS - 1) * STEP_S)
    assert w._stack.currentWidget() is w._plot_widget
    items = w._plot.listDataItems()
    assert len(items) == 1 and items[0].opts["stepMode"]  # a step line
    assert w.mean_median_lines() == []
    assert not [i for i in w._plot.items if isinstance(i, pg.InfiniteLine)]
    legend = w.duration_legend_text(w._result, 0)
    assert "max=23" in legend and "mean=" in legend
    assert "h]" in w._plot.getAxis("bottom").labelText or "[" in w._plot.getAxis("bottom").labelText


def test_duration_curve_percent_axis(qapp, mw):
    w = _open(qapp, mw)
    _view(qapp, w, "duration")
    _set(w._yaxis_combo, "percent")
    qapp.processEvents()
    data = w._duration_data(w._result)
    assert data[0]["x"][-1] == pytest.approx(100.0)
    assert "%" in w.duration_x_label(w._result)


def test_duration_hover_text(qapp, mw):
    w = _open(qapp, mw)
    _set(w._weight_combo, "time")
    _view(qapp, w, "duration")
    d = w._duration_data(w._result)[0]
    text = w.hover_text(float(d["x"][-1]) * 0.5)
    assert "%" in text and ":" in text
    assert w.hover_text(float("nan")) != text  # outside / invalid: the hint


def test_duration_curve_bucketed_x_is_the_bucket_rank(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _set(w._agg_combo, "max")
    _view(qapp, w, "duration")
    var = w._result.variables[0]
    assert list(var.duration_x) == [1.0, 2.0, 3.0][: len(var.duration_x)]
    assert np.all(np.diff(var.duration_v) <= 0)


# -- trend (42.12) ---------------------------------------------------------------------


def test_trend_bars_match_the_bucket_arrays(qapp, mw):
    w = _open(qapp, mw)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    r = w._result
    assert w._stack.currentWidget() is w._trend_widget
    bars = [i for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem)]
    assert len(bars) == 1
    o = bars[0].opts
    assert np.allclose(o["x0"], r.bucket_start, rtol=0, atol=1e-3)
    assert np.allclose(o["x1"], r.bucket_start + 0.95 * (r.bucket_end - r.bucket_start), rtol=0, atol=1e-3)  # 5% gap
    assert np.allclose(o["y1"], r.variables[0].bucket_values)
    # the time axis of the trend is a date axis (like the main plots)
    assert isinstance(w._trend_plot.getAxis("bottom"), pg.DateAxisItem)
    # zoom / pan along X only
    assert list(w._trend_vb.state["mouseEnabled"]) == [True, False]
    # a mean line per variable
    assert [i.objectName() for i in w._trend_plot.items if isinstance(i, pg.PlotDataItem)] == ["hist_trend_mean_0"]


def test_trend_two_variables_split_the_bucket_width(qapp, mw):
    w = _open(qapp, mw, names=("power", "const"))
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    r = w._result
    bars = sorted((i for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem)), key=lambda b: b.opts["x0"][0])
    assert len(bars) == 2
    width = (r.bucket_end - r.bucket_start) / 2
    kw = dict(rtol=0, atol=1e-3)
    assert np.allclose(bars[0].opts["x0"], r.bucket_start, **kw)
    assert np.allclose(bars[0].opts["x1"], r.bucket_start + 0.95 * width, **kw)
    assert np.allclose(bars[1].opts["x0"], r.bucket_start + width, **kw)
    assert np.allclose(bars[1].opts["x1"], r.bucket_start + width + 0.95 * width, **kw)


def test_trend_incomplete_bucket_is_drawn_apart_and_hidden_when_excluded(qapp, mw_gap):
    w = _open(qapp, mw_gap)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    r = w._result
    assert list(r.variables[0].bucket_complete) == [True, False, True]
    names = sorted(i.objectName() for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem))
    assert any(n.startswith("hist_trend_incomplete") for n in names)
    full = next(i for i in w._trend_plot.items if i.objectName() == "hist_trend_bars_0")
    assert len(full.opts["x0"]) == 2  # the two complete days
    hatch = next(i for i in w._trend_plot.items if i.objectName() == "hist_trend_incomplete_0")
    assert len(hatch.opts["x0"]) == 1 and hatch.opts["brush"].style() == pg.QtCore.Qt.BrushStyle.BDiagPattern
    w._exclude_check.setChecked(True)  # "exclude incomplete" removes the bar
    _wait(qapp, w)
    assert not any(i.objectName().startswith("hist_trend_incomplete")
                   for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem))
    assert sum(len(i.opts["x0"]) for i in w._trend_plot.items if i.objectName() == "hist_trend_bars_0") == 2


def test_trend_buckets_without_a_value_are_not_drawn(qapp, mw):
    w = _open(qapp, mw)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    r = w._result
    r.variables[0].bucket_values[1] = np.nan  # a bucket without a value
    r.variables[0].bucket_complete[1] = False
    w._draw(r)
    bars = [i for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem)]
    assert sum(len(b.opts["x0"]) for b in bars) == 2


def test_trend_many_buckets_become_a_line(qapp, mw):
    w = _open(qapp, mw)
    _view(qapp, w, "trend")
    _set(w._bucket_combo, 60.0)  # 1 minute: 4320 buckets > HIST_TREND_BAR_MAX
    _wait(qapp, w)
    assert w._result.bucket_count > HIST_TREND_BAR_MAX
    assert not [i for i in w._trend_plot.items if isinstance(i, pg.BarGraphItem)]
    assert any(isinstance(i, pg.PlotDataItem) for i in w._trend_plot.items)
    assert "line" in w.status_text() or "線" in w.status_text()


def test_trend_hover_text(qapp, mw_gap):
    w = _open(qapp, mw_gap)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    r = w._result
    inside_incomplete = float(r.bucket_start[1] + 3600)
    text = w.trend_hover_text(inside_incomplete)
    assert "03/03 00:00" in text and "03/04 00:00" in text
    assert "incomplete" in text or "欠け" in text
    assert "03/02" in w.trend_hover_text(float(r.bucket_start[0] + 10))
    assert w.trend_hover_text(float(r.bucket_start[0] - 99999)) == histogram_window.tr("hist.hover_hint")


def test_trend_y_follows_the_visible_values(qapp, mw):
    w = _open(qapp, mw, names=("power",))
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    # every day has max 23: the Y range covers 0..23
    y_lo, y_hi = w._trend_vb.viewRange()[1]
    assert y_lo <= 0 <= 23 <= y_hi
    # zoom X to the first day only: still finite and covering the visible bar
    r = w._result
    w._trend_vb.setXRange(float(r.bucket_start[0]), float(r.bucket_end[0]), padding=0)
    qapp.processEvents()
    y_lo, y_hi = w._trend_vb.viewRange()[1]
    assert np.isfinite([y_lo, y_hi]).all() and y_hi >= 23


def test_save_bucket_values_works_in_trend_and_skips_absent_buckets(qapp, mw, tmp_path, monkeypatch):
    w = _open(qapp, mw)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    assert w.export_windows_action.isEnabled()
    _filter(qapp, w, "09:00", "17:00", preset="custom")
    for d, b in enumerate(w._tf_day_buttons):
        b.blockSignals(True)
        b.setChecked(d == 1)  # Tuesday only
        b.blockSignals(False)
    w._tf_day_buttons[1].toggled.emit(True)
    _wait(qapp, w)
    out = tmp_path / "trend.parquet"
    filters = [histogram_window.tr("hist.export_windows_filter_parquet")]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), filters[0])))
    w.export_windows_dialog()
    table = pq.read_table(out)
    assert table.num_rows == 1  # the days without target time are not written
    import json

    meta = json.loads(table.schema.metadata[b"parquet_analyzer"])
    assert meta["view"] == "trend" and meta["time_filter"]["enabled"] is True
    assert meta["time_filter"]["weekdays"] == [1]


# -- image export (42.5 / 42.11 / 42.12) ------------------------------------------------


@pytest.mark.parametrize("view", ["dist", "duration", "trend"])
def test_image_export_has_view_and_filter_lines(qapp, mw, tmp_path, view):
    w = _open(qapp, mw)
    _filter(qapp, w, "09:00", "17:00", preset="weekdays")
    _view(qapp, w, view)
    lines = w.export_stamp_extra()
    text = "\n".join(lines)
    if view != "dist":
        assert histogram_window.tr("hist.stamp_view", text=histogram_window.tr("hist.view_" + view)) in lines
    assert any(ln.startswith(("Filter:", "絞り込み:")) for ln in lines), text
    out = tmp_path / f"{view}.png"
    assert w.export_image(str(out))
    assert out.exists() and out.stat().st_size > 1000


def test_image_export_trend_with_incomplete_and_two_variables(qapp, mw_gap, tmp_path):
    w = _open(qapp, mw_gap)
    _set(w._agg_combo, "max")
    _view(qapp, w, "trend")
    out = tmp_path / "trend_gap.png"
    assert w.export_image(str(out)) and out.stat().st_size > 1000
    fig = w.build_export_figure(["x"], (800, 500))
    ax = fig.axes[0]
    assert len(ax.patches) == 3  # one bar per bucket
    hatched = [p for p in ax.patches if p.get_hatch()]
    assert len(hatched) == 1 and hatched[0].get_alpha() is not None and hatched[0].get_alpha() < 0.9


def test_image_export_duration_uses_step(qapp, mw, tmp_path):
    w = _open(qapp, mw)
    _view(qapp, w, "duration")
    fig = w.build_export_figure(["x"], (800, 500))
    ax = fig.axes[0]
    assert len(ax.lines) == 1 and ax.lines[0].get_drawstyle() == "steps-pre"
