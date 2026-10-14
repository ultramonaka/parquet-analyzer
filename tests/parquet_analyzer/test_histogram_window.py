"""Histogram window (analysis.ja.md 5.33 / 42.1-42.6): menu, settings, display, frame behaviours."""

from __future__ import annotations

import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyqtgraph as pg
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from parquet_analyzer import features
from parquet_analyzer.ui import histogram_window
from parquet_analyzer.ui.analysis_window import CANCELLED
from parquet_analyzer.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)


@pytest.fixture(autouse=True)
def _utc(monkeypatch):
    """The data's naive timestamps are read as UTC; buckets are local-day based, so pin UTC."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def ops(monkeypatch):
    """Record `log_op` calls made by the window."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(histogram_window, "log_op", lambda action, **kw: calls.append((action, kw)))
    return calls


def _drain(qapp, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _wait(qapp, win, timeout=20.0):
    end = time.time() + timeout
    while win.is_computing() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        qapp.processEvents()
    assert not win.is_computing()


N_DAYS = 3
STEP_S = 10
ROWS = N_DAYS * 86400 // STEP_S


@pytest.fixture
def multiday_path(tmp_path):
    """3 days at 10 s: `power` = 5 + 3 sin (daily), `const` = 2, `nanny` has NaN rows; local naive times."""
    t = np.datetime64("2026-03-02T00:00:00", "s") + np.arange(ROWS, dtype="int64") * np.timedelta64(STEP_S, "s")
    phase = np.arange(ROWS) * STEP_S / 86400.0 * 2 * np.pi
    power = 5.0 + 3.0 * np.sin(phase)
    nanny = power.copy()
    nanny[::50] = np.nan
    table = pa.table({"time": pa.array(t), "power": power, "const": np.full(ROWS, 2.0), "nanny": nanny})
    path = tmp_path / "multiday.parquet"
    pq.write_table(table, path)
    return str(path)


@pytest.fixture
def mw(qapp, isolated_config, multiday_path):
    win = MainWindow()
    win.show()
    assert win.load_parquet(multiday_path)
    win._add_variable_to_plot(win.plot_grid.plots[0], "power")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
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


def _set(widget, data=None, text=None, index=None):
    if index is not None:
        widget.setCurrentIndex(index)
    elif data is not None:
        widget.setCurrentIndex(widget.findData(data))


# -- entry point ------------------------------------------------------------------------


def test_menu_entry_gated_by_flag(qapp, isolated_config, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "histogram", False)
    off = MainWindow()
    assert "histogram" not in off._actions
    monkeypatch.setitem(features._EFFECTIVE, "histogram", True)
    on = MainWindow()
    assert "histogram" in on._actions
    assert on._actions["histogram"].text() in ("Histogram", "ヒストグラム")
    menu_actions = on.graphs_button.menu().actions()
    assert on._actions["histogram"] in menu_actions


def test_histogram_flag_defaults_on():
    # ON since 2026-10-06, after review and the user's real-machine check (42.1)
    assert features.DEFAULT_FEATURES["histogram"] is True


def test_shortcut_registered_and_row_hidden_when_flag_off(qapp, isolated_config, monkeypatch):
    from parquet_analyzer.io.settings import DEFAULT_SHORTCUTS, Settings
    from parquet_analyzer.ui.settings_dialog import SettingsDialog

    assert DEFAULT_SHORTCUTS["histogram"] == ""
    assert "histogram" in SettingsDialog(Settings())._shortcut_edits
    monkeypatch.setitem(features._EFFECTIVE, "histogram", False)
    assert "histogram" not in SettingsDialog(Settings())._shortcut_edits


def test_open_without_file_shows_message(qapp, isolated_config):
    win = MainWindow()
    assert win.open_histogram() is None
    assert win._analysis_windows == []


def test_opens_with_panel_selection_and_computes(qapp, mw, ops):
    mw.variable_panel.selectAll()
    mw.variable_panel.clearSelection()
    item = mw.variable_panel.findItems("power", pg.QtCore.Qt.MatchFlag.MatchExactly)[0]
    item.setSelected(True)
    w = mw.open_histogram()
    assert w.selected_names() == ["power"]
    _wait(qapp, w)
    assert w._result is not None and w._result.variables[0].n == ROWS
    assert ("histogram_open" in [a for a, _ in ops])
    opened = [kw for a, kw in ops if a == "histogram_open"][0]
    assert opened["variables"] == ["power"]
    assert w in mw._analysis_windows


def test_no_selection_opens_empty_with_guidance(qapp, mw):
    mw.variable_panel.clearSelection()
    w = mw.open_histogram()
    assert w.selected_names() == []
    w.recompute()
    assert not w.is_computing()
    assert "Select" in w.status_text() or "選択" in w.status_text()
    assert "time" not in [w._var_list.item(i).text() for i in range(w._var_list.count())]


def test_several_windows_may_be_open(qapp, mw):
    a, b = mw.open_histogram(), mw.open_histogram()
    assert a is not b and len(mw._analysis_windows) == 2


# -- settings panel ---------------------------------------------------------------------


def test_defaults_match_the_spec(qapp, mw):
    w = mw.open_histogram()
    s = w.current_settings()
    assert (s.mode, s.weight, s.bucket_s, s.origin_s, s.agg, s.integral_unit_s) == (
        "plain", "rows", 86400.0, 0, "sum", 3600.0)
    assert s.exclude_incomplete is False and s.gap_s is None and s.bins is None
    assert w._yaxis_combo.currentData() == "count"


def test_controls_enable_disable_with_settings(qapp, mw):
    w = mw.open_histogram()
    assert w._weight_group.isEnabled()
    for grp in (w._bucket_group, w._origin_group, w._agg_group, w._exclude_group, w._unit_group):
        assert not grp.isEnabled()
    assert not w._gap_group.isEnabled()  # rows weighting needs no gap
    _set(w._weight_combo, "time")
    assert w._gap_group.isEnabled() and not w._gap_spin.isEnabled()
    _set(w._gap_combo, "value")
    assert w._gap_spin.isEnabled()
    _set(w._mode_combo, "bucket")
    assert not w._weight_group.isEnabled()
    for grp in (w._bucket_group, w._origin_group, w._agg_group, w._exclude_group):
        assert grp.isEnabled()
    assert not w._unit_group.isEnabled() and not w._custom_value.isEnabled()
    _set(w._agg_combo, "integral")
    assert w._unit_group.isEnabled() and w._unit_combo.currentData() == 3600.0
    _set(w._bucket_combo, None, index=w._bucket_combo.count() - 1)
    assert w._custom_value.isEnabled() and w._custom_unit.isEnabled()
    assert not w._bins_spin.isEnabled()
    _set(w._bins_combo, "value")
    assert w._bins_spin.isEnabled()


def test_sum_tooltip_explains_instantaneous_values(qapp, mw):
    w = mw.open_histogram()
    assert w._agg_combo.toolTip()


def test_illegal_bucket_length_shows_reason_and_does_not_compute(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _wait(qapp, w)
    _set(w._bucket_combo, None, index=w._bucket_combo.count() - 1)
    _wait(qapp, w)
    w._custom_unit.setCurrentIndex(0)  # minutes
    w._custom_value.setValue(7.0)  # 7 min does not divide 24 h
    _drain(qapp)
    assert w._bucket_error.text()
    assert not w.is_computing()
    assert w.status_text() == w._bucket_error.text()
    w._custom_unit.setCurrentIndex(2)  # days (7 days: legal, computes)
    _wait(qapp, w)
    w._custom_value.setValue(1.5)
    assert w._bucket_error.text() and not w.is_computing()
    w._custom_value.setValue(2.0)
    _wait(qapp, w)
    assert not w._bucket_error.text()
    assert w._result.settings.bucket_s == 2 * 86400


def test_origin_and_aggregation_flow_through_to_the_result(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _set(w._agg_combo, "integral")
    from PySide6.QtCore import QTime

    w._origin_edit.setTime(QTime(6, 0))
    _wait(qapp, w)
    s = w._result.settings
    assert (s.mode, s.agg, s.origin_s, s.integral_unit_s) == ("bucket", "integral", 6 * 3600, 3600.0)
    # the first bucket starts the previous day 06:00 -> 4 buckets over 3 days
    assert len(w._result.variables[0].values) == 4


def test_settings_changes_are_logged_and_recompute_with_the_same_range(qapp, mw, ops):
    w = _open(qapp, mw)
    ops.clear()
    rng = w._range
    _set(w._weight_combo, "time")
    _wait(qapp, w)
    assert w._range == rng
    changed = [kw for a, kw in ops if a == "histogram_settings"]
    assert changed and changed[-1] == {"item": "weight", "value": "time"}
    assert w._result.settings.weight == "time"
    _set(w._bins_combo, "value")
    w._bins_spin.setValue(12)
    _wait(qapp, w)
    assert w._result.bins == 12
    assert ("bins", 12) in [(kw["item"], kw["value"]) for a, kw in ops if a == "histogram_settings"]


def test_yaxis_percent_redraws_without_recompute(qapp, mw):
    w = _open(qapp, mw)
    gen = w._generation
    _set(w._yaxis_combo, "percent")
    assert w._generation == gen and not w.is_computing()
    items = [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert items and abs(float(items[0].yData.sum()) - 100.0) < 1e-6
    assert "%" in w._plot.getAxis("left").labelText or "割合" in w._plot.getAxis("left").labelText


# -- display ----------------------------------------------------------------------------


def test_plain_display_items_lines_and_legend(qapp, mw):
    w = _open(qapp, mw, names=("power", "nanny"))
    items = [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert len(items) == 2
    for it in items:
        assert it.opts["stepMode"] == "center" and it.opts["fillLevel"] == 0
        assert len(it.xData) == len(it.yData) + 1
        assert np.isfinite(it.xData).all() and np.isfinite(it.yData).all()
    lines = w.mean_median_lines()
    assert len(lines) == 4  # mean + median per variable
    styles = {ln.pen.style() for ln in lines}
    assert pg.QtCore.Qt.PenStyle.DashLine in styles and pg.QtCore.Qt.PenStyle.DotLine in styles
    legend_names = [label.text for _, label in w._legend.items]
    assert any(n.startswith("power") and "n=" in n and "σ=" in n for n in legend_names)
    assert w._plot.getAxis("bottom").labelText == "power, nanny"


def test_excluded_nan_count_is_in_the_status(qapp, mw):
    w = _open(qapp, mw, names=("nanny",))
    v = w._result.variables[0]
    assert v.excluded == len(range(0, ROWS, 50))
    assert str(v.excluded) in w.status_text()


def test_sine_distribution_is_u_shaped_on_screen(qapp, mw):
    w = _open(qapp, mw)
    c = w._result.variables[0].counts
    mid = len(c) // 2
    assert c[:3].sum() > c[mid - 1:mid + 2].sum() and c[-3:].sum() > c[mid - 1:mid + 2].sum()


def test_bucket_display_labels_and_counts(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _set(w._agg_combo, "integral")
    _wait(qapp, w)
    r = w._result
    assert r.y_unit == "buckets" and r.variables[0].n == 3
    xl = w._plot.getAxis("bottom").labelText
    assert "power" in xl and "×" in xl
    # integral of 5 + 3 sin over a day (kW*h): ~120
    assert all(abs(x - 120.0) < 0.5 for x in r.variables[0].values[:2])


def test_hover_label_reports_bin_below_the_plot(qapp, mw):
    w = _open(qapp, mw)
    r = w._result
    mid = float((r.edges[0] + r.edges[-1]) / 2)
    text = w.hover_text(mid)
    assert "power" in text and "..".replace(" ", "") in text.replace(" ", "")
    assert w.hover_text(float(r.edges[0]) - 100) == w.hover_text(float("nan"))  # outside: the hint
    # the label is not overlaid on the plot
    assert w._hover_label.parent() is not w._plot_widget
    w._hover_label.setText("x")
    assert w._plot_widget.findChild(type(w._hover_label)) is None


def test_hover_through_mouse_move_signal(qapp, mw):
    w = _open(qapp, mw)
    w.show()
    qapp.processEvents()
    vb = w._plot.getViewBox()
    x = float((w._result.edges[0] + w._result.edges[-1]) / 2)
    pos = vb.mapViewToScene(pg.QtCore.QPointF(x, 1.0))
    w._plot.scene().sigMouseMoved.emit(pos)
    assert "power" in w._hover_label.text()


def test_time_not_ascending_shows_reason_not_a_failure(qapp, mw, monkeypatch):
    w = mw.open_histogram()
    tv = np.asarray(mw._time_values).copy()
    tv[100] = tv[0]
    monkeypatch.setattr(mw, "_time_values", tv)
    _set(w._weight_combo, "time")
    w._var_list.item(0).setSelected(True)
    w.recompute()
    _wait(qapp, w)
    assert "ascending" in w.status_text() or "昇順" in w.status_text()
    assert "failed" not in w.status_text().lower() and w._result is None


def test_too_many_buckets_shows_reason(qapp, mw):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _set(w._bucket_combo, 60.0)
    w._range = (0, ROWS, 0.0, 0.0)
    # fake a huge time span through the settings: 1-minute buckets over 3 days are fine ...
    _wait(qapp, w)
    assert w._result is not None and w._result.variables[0].n > 0
    # ... but a 4,000-day span is not: patch the edge builder's limit
    import parquet_analyzer.core.histogram as H

    old = H.HIST_MAX_BUCKETS
    H.HIST_MAX_BUCKETS = 100
    try:
        w.recompute(keep_range=True)
        _wait(qapp, w)
    finally:
        H.HIST_MAX_BUCKETS = old
    assert w._result is None and ("many" in w.status_text() or "多すぎ" in w.status_text())


# -- analysis-window frame behaviours (25.4) --------------------------------------------


def test_follow_off_ignores_main_range_and_recompute_reads_it(qapp, mw):
    w = _open(qapp, mw)
    full = w._result.row_end - w._result.row_start
    t = mw._time_values
    mw.plot_grid.set_x_range(float(t[0]), float(t[0] + (t[-1] - t[0]) / 4))
    _drain(qapp, 0.6)
    assert not w.is_computing() and w._result.row_end - w._result.row_start == full
    w.recompute()
    _wait(qapp, w)
    assert w._result.row_end - w._result.row_start < full * 0.5


def test_follow_on_recomputes_after_debounce(qapp, mw):
    w = _open(qapp, mw)
    full = w._result.row_end - w._result.row_start
    w.follow_action.setChecked(True)
    _wait(qapp, w)
    t = mw._time_values
    mw.plot_grid.set_x_range(float(t[0]), float(t[0] + (t[-1] - t[0]) / 2))
    _drain(qapp, 0.8)
    _wait(qapp, w)
    assert w._result.row_end - w._result.row_start < full * 0.6


def test_stale_result_is_dropped(qapp, mw):
    w = _open(qapp, mw)
    current = w._result
    w._generation = 7
    fake = histogram_window._Refused("time_not_ascending")
    w._on_finished(fake, -1, 6)
    assert w._result is current
    w._on_finished(fake, -1, 7)
    assert w._result is None


def test_worker_failure_is_shown_and_released(qapp, mw, monkeypatch):
    w = _open(qapp, mw)

    def boom(a, b, c):
        def run():
            raise RuntimeError("kaboom")

        return run

    monkeypatch.setattr(w, "make_job", boom)
    w.recompute()
    _wait(qapp, w)
    assert "kaboom" in w.status_text()
    assert not mw._analysis_hub.pending


def test_closing_with_job_in_flight_does_not_crash(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    real = w.make_job
    monkeypatch.setattr(w, "make_job", lambda a, b, c: (lambda j=real(a, b, c): (time.sleep(0.3), j())[1]))
    w.recompute()
    assert w.is_computing()
    w.close()
    assert w not in mw._analysis_windows
    _drain(qapp, 0.8)
    assert not mw._analysis_hub.pending


def test_closing_window_cancels_running_job(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    seen = []
    real = w.make_job

    def slow(a, b, c):
        def run():
            end = time.time() + 3
            while time.time() < end:
                if c():
                    seen.append(True)
                    return CANCELLED
                time.sleep(0.01)
            return real(a, b, c)()

        return run

    monkeypatch.setattr(w, "make_job", slow)
    w.recompute()
    time.sleep(0.05)
    t0 = time.time()
    w.close()
    _drain(qapp, 0.3)
    assert seen and time.time() - t0 < 1.0 and not mw._analysis_hub.pending


def test_job_cancel_inside_compute_returns_cancelled(qapp, mw):
    w = _open(qapp, mw)
    job = w.make_job(0, ROWS, lambda: True)
    assert job() is CANCELLED


def test_reopening_file_or_changing_time_column_closes_windows(qapp, mw, multiday_path):
    w1 = _open(qapp, mw)
    w2 = mw.open_histogram()
    assert len(mw._analysis_windows) == 2
    mw.set_time_column("power")
    qapp.processEvents()
    assert mw._analysis_windows == [] and w1._closed and w2._closed
    mw.set_time_column("time")
    w3 = mw.open_histogram()
    assert mw.load_parquet(multiday_path)
    assert mw._analysis_windows == [] and w3._closed


def test_main_window_close_cancels_analysis_jobs(qapp, mw):
    w = _open(qapp, mw)
    mw.close()
    assert w._closed and mw._analysis_windows == []


def test_job_survives_time_column_switch_and_reopen(qapp, mw, multiday_path, monkeypatch):
    w = _open(qapp, mw)
    real = w.make_job
    monkeypatch.setattr(w, "make_job", lambda a, b, c: (lambda j=real(a, b, c): (time.sleep(0.2), j())[1]))
    w.recompute()
    mw.load_parquet(multiday_path)  # closes the window; the in-flight job keeps its own Columns
    _drain(qapp, 0.8)
    assert not mw._analysis_hub.pending


def test_candidates_refresh_with_derived_variables(qapp, mw):
    w = _open(qapp, mw)
    mw._on_add_derived_variable("dd", "power*2")
    _drain(qapp, 0.1)
    assert "dd" in [w._var_list.item(i).text() for i in range(w._var_list.count())]
    assert w.selected_names() == ["power"]
    mw.delete_derived_variable("dd")
    _drain(qapp, 0.1)
    assert "dd" not in [w._var_list.item(i).text() for i in range(w._var_list.count())]


def test_derived_variable_can_be_histogrammed(qapp, mw):
    mw._on_add_derived_variable("err", "power - 5")
    _drain(qapp, 0.1)
    w = _open(qapp, mw, names=("err",))
    v = w._result.variables[0]
    assert v.n == ROWS and abs(v.mean) < 0.05 and abs(v.std - 3 / np.sqrt(2)) < 0.05


def test_constant_variable_does_not_break_display(qapp, mw):
    w = _open(qapp, mw, names=("const",))
    assert w._result.variables[0].n == ROWS
    items = [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert items and np.isfinite(items[0].xData).all()


# -- image export -----------------------------------------------------------------------


@pytest.mark.parametrize("ext", ["png", "svg"])
def test_image_export_writes_file(qapp, mw, tmp_path, ext):
    w = _open(qapp, mw, names=("power", "nanny"))
    out = tmp_path / f"h.{ext}"
    assert w.export_image(str(out))
    assert out.exists() and out.stat().st_size > 1000


def test_export_stamp_has_variable_aggregation_and_bin_lines(qapp, mw, monkeypatch, tmp_path):
    captured = {}
    orig = histogram_window.build_histogram_figure

    def spy(win, r, lines, size_px):
        captured["lines"] = list(lines)
        return orig(win, r, lines, size_px)

    monkeypatch.setattr(histogram_window, "build_histogram_figure", spy)
    w = _open(qapp, mw, names=("power", "nanny"))
    w.export_image(str(tmp_path / "a.png"))
    text = "\n".join(captured["lines"])
    assert "power, nanny" in text
    assert any(("Aggregation" in ln or "集計" in ln) and ("No buckets" in ln or "区切りなし" in ln) for ln in captured["lines"])
    assert any(ln.startswith(("Bins", "ビン")) and ("auto" in ln or "自動" in ln) for ln in captured["lines"])
    _set(w._mode_combo, "bucket")
    _set(w._agg_combo, "integral")
    from PySide6.QtCore import QTime

    w._origin_edit.setTime(QTime(6, 0))
    w._exclude_check.setChecked(True)
    _set(w._bins_combo, "value")
    w._bins_spin.setValue(10)
    _wait(qapp, w)
    w.export_image(str(tmp_path / "b.png"))
    agg = [ln for ln in captured["lines"] if ln.startswith(("Aggregation", "集計"))][0]
    assert "6:00" in agg and ("Integral" in agg or "積算" in agg) and ("×" in agg)
    assert any(ln.startswith(("Bins", "ビン")) and "10" in ln and "auto" not in ln for ln in captured["lines"])
    # the stamp still ends with the "exported" line
    assert captured["lines"][-1].count("|") >= 1


def test_export_without_result_says_nothing_to_export(qapp, mw, tmp_path):
    w = mw.open_histogram()
    assert not w.export_image(str(tmp_path / "none.png"))
    assert not (tmp_path / "none.png").exists()


def test_time_weighted_legend_marks_median_as_approximate_and_gaps_not_doubled(qapp, mw):
    w = _open(qapp, mw, names=("power", "nanny"))
    assert "median=" in w.legend_text(w._result, 0) and "≈" not in w.legend_text(w._result, 0)
    _set(w._weight_combo, "time")
    _wait(qapp, w)
    assert "median≈" in w.legend_text(w._result, 0)
    assert w._result.gap_count == 0
    _set(w._gap_combo, "value")
    w._gap_spin.setValue(5.0)  # every 10 s interval is a gap
    _wait(qapp, w)
    n = w._result.gap_count
    assert n == ROWS - 1 and (f"Gaps: {n} " in w.status_text() or f"すき間: {n}件" in w.status_text())


def test_nonfinite_edges_are_not_drawn_and_not_exported(qapp, mw, monkeypatch, tmp_path):
    w = _open(qapp, mw)
    r = w._result
    bad = histogram_window.HistogramResult(r.settings, r.row_start, r.row_end, np.array([0.0, np.inf, np.inf]),
                                           variables=r.variables[:1], y_unit="rows")
    bad.variables[0].counts = np.array([1.0, 2.0])
    w._on_finished(bad, -1, w._generation)
    assert not [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert "finite" in w.status_text() or "有限" in w.status_text()
    assert not w.export_image(str(tmp_path / "x.png")) and not (tmp_path / "x.png").exists()


def test_deselecting_everything_clears_the_graph_and_blocks_export(qapp, mw, tmp_path):
    w = _open(qapp, mw)
    assert [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    w._var_list.clearSelection()  # signal -> recompute -> nothing to compute
    _drain(qapp, 0.1)
    assert w._result is None
    assert not [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert not w.export_image(str(tmp_path / "old.png")) and not (tmp_path / "old.png").exists()


def test_illegal_bucket_and_refusal_clear_the_old_graph(qapp, mw, tmp_path):
    w = _open(qapp, mw)
    _set(w._mode_combo, "bucket")
    _wait(qapp, w)
    assert w._result is not None
    _set(w._bucket_combo, None, index=w._bucket_combo.count() - 1)
    _wait(qapp, w)
    w._custom_unit.setCurrentIndex(0)
    w._custom_value.setValue(7.0)  # 7 min does not divide 24 h
    _drain(qapp, 0.1)
    assert w._result is None and not [i for i in w._plot.items if isinstance(i, pg.PlotDataItem)]
    assert not w.export_image(str(tmp_path / "old2.png"))


def test_deleting_a_selected_derived_variable_recomputes_with_the_rest(qapp, mw):
    mw._on_add_derived_variable("dd", "power*2")
    _drain(qapp, 0.1)
    w = _open(qapp, mw, names=("power", "dd"))
    assert [v.name for v in w._result.variables] == ["power", "dd"]
    mw.delete_derived_variable("dd")
    _drain(qapp, 0.1)
    _wait(qapp, w)
    assert w.selected_names() == ["power"]
    assert [v.name for v in w._result.variables] == ["power"]
    # deleting the only selected variable: nothing is left, the graph goes
    mw._on_add_derived_variable("d2", "power*3")
    _drain(qapp, 0.1)
    w2 = _open(qapp, mw, names=("d2",))
    mw.delete_derived_variable("d2")
    _drain(qapp, 0.1)
    assert w2.selected_names() == [] and w2._result is None


def test_mean_and_median_lines_are_two_pixels_wide(qapp, mw):
    w = _open(qapp, mw)
    assert w.mean_median_lines() and all(ln.pen.width() == 2 for ln in w.mean_median_lines())
