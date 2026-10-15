"""Histogram window: the time-of-day view (analysis.ja.md 42.13, 42.14)."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QFileDialog, QMessageBox

from parquet_analyzer import features
from parquet_analyzer.ui import histogram_window
from parquet_analyzer.ui.main_window import MainWindow

STEP_S = 10
N_DAYS = 3
ROWS = N_DAYS * 86400 // STEP_S


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


def _write(tmp_path, name, t, **cols):
    path = tmp_path / name
    pq.write_table(pa.table({"time": t, **cols}), path)
    return str(path)


@pytest.fixture
def multiday_path(tmp_path):
    """Mon 2026-03-02 .. Wed, one row per 10 s; `power` = the hour of day (0..23), `const` = 2."""
    t = np.datetime64("2026-03-02T00:00:00", "s") + np.arange(ROWS, dtype="int64") * np.timedelta64(STEP_S, "s")
    hour = (np.arange(ROWS) * STEP_S // 3600 % 24).astype(float)
    return _write(tmp_path, "multiday.parquet", pa.array(t), power=hour, const=np.full(ROWS, 2.0))


@pytest.fixture
def numeric_path(tmp_path):
    t = np.arange(ROWS, dtype=float) * STEP_S
    return _write(tmp_path, "numeric.parquet", pa.array(t), power=(t // 3600 % 24))


def _mw(qapp, isolated_config, path):
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    win._add_variable_to_plot(win.plot_grid.plots[0], "power")
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


def _tod(qapp, w):
    _set(w._view_combo, "timeofday")
    _wait(qapp, w)


# -- panel -------------------------------------------------------------------------


def test_view_combo_has_time_of_day_and_defaults(qapp, mw):
    w = _open(qapp, mw)
    assert w._view_combo.findData("timeofday") >= 0
    assert w._slot_combo.currentData() == 3600.0
    assert [w._slot_combo.itemData(i) for i in range(w._slot_combo.count())] == [600.0, 900.0, 1800.0, 3600.0, 7200.0, 10800.0]
    assert w._tod_agg_combo.currentData() == "mean"
    assert w._tod_per_day.isChecked()


def test_fields_enabled_for_time_of_day(qapp, mw):
    w = _open(qapp, mw)
    assert not w._slot_group.isEnabled() and not w._tod_agg_group.isEnabled()  # distribution
    _tod(qapp, w)
    for grp in (w._mode_group, w._weight_group, w._bucket_group, w._agg_group, w._two_group, w._bins_group, w._yaxis_group):
        assert not grp.isEnabled()
        assert not grp.isHidden()  # disabled, not hidden (42.11)
    assert w._origin_group.isEnabled() and w._slot_group.isEnabled() and w._tod_agg_group.isEnabled()
    assert not w._tod_per_day_group.isEnabled() and not w._unit_group.isEnabled()  # mean: no per-day, no unit
    _set(w._tod_agg_combo, "integral")
    _wait(qapp, w)
    assert w._tod_per_day_group.isEnabled() and w._unit_group.isEnabled()
    assert w._tf_check.isEnabled()  # the time filter applies to every view
    _set(w._view_combo, "dist")
    _wait(qapp, w)
    assert w._mode_group.isEnabled() and not w._slot_group.isEnabled()


# -- compute and display -----------------------------------------------------------


def test_time_of_day_result_and_drawing(qapp, mw, ops):
    w = _open(qapp, mw)
    _tod(qapp, w)
    r = w._result
    assert r.settings.view == "timeofday"
    np.testing.assert_allclose(r.variables[0].slot_values, np.arange(24))
    assert w._stack.currentWidget() is w._tod_widget
    lines = [i for i in w._tod_plot.listDataItems()]
    assert len(lines) == 1  # no gaps in the line: one run
    x, y = lines[0].getData()
    assert np.isfinite(y).all() and np.isfinite(x).all()
    assert any(a == "histogram_settings" and k.get("item") == "view" and k.get("value") == "timeofday" for a, k in ops)
    assert any(a == "histogram_compute" and k.get("view") == "timeofday" for a, k in ops)


def test_origin_changes_axis_and_labels(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    w._origin_edit.setTime(QTime(6, 0))
    _wait(qapp, w)
    r = w._result
    assert r.variables[0].slot_values[0] == pytest.approx(6.0)
    assert w._clock_axis.origin_s == 6 * 3600
    labels = w._clock_axis.tickStrings([0.0, 3600.0, 18 * 3600.0], 1.0, 3600.0)
    assert labels == ["06:00", "07:00", "00:00"]


def test_time_filter_breaks_the_line_and_counts(qapp, mw):
    w = _open(qapp, mw)
    w._tf_check.setChecked(True)
    _wait(qapp, w)
    w._tf_start.setTime(QTime(9, 0))
    w._tf_end.setTime(QTime(17, 0))
    _wait(qapp, w)
    _tod(qapp, w)
    r = w._result
    vals = r.variables[0].slot_values
    assert np.isfinite(vals[9:17]).all() and np.isnan(vals[:9]).all() and np.isnan(vals[17:]).all()
    assert len(w._tod_plot.listDataItems()) == 1  # a single run 9-17, nothing else drawn


def test_integral_per_day_label_and_values(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    _set(w._tod_agg_combo, "integral")
    _wait(qapp, w)
    r = w._result
    # power = hour h for one hour -> integral [x h] per day = h
    assert r.variables[0].slot_values[7] == pytest.approx(7.0, rel=0.01)
    assert "power" in w.tod_y_label(r) and "[" in w.tod_y_label(r)
    w._tod_per_day.setChecked(False)
    _wait(qapp, w)
    assert w._result.variables[0].slot_values[7] == pytest.approx(21.0, rel=0.01)


def test_hover_text(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    text = w.tod_hover_text(13.5 * 3600)
    assert "13:00" in text and "14:00" in text and "13" in text
    assert w.tod_hover_text(-5.0) == histogram_window.tr("hist.hover_hint")
    assert w.tod_hover_text(86400.0 + 1) == histogram_window.tr("hist.hover_hint")


def test_hover_text_reports_partial_days(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    var = w._result.variables[0]
    k = 13
    var.slot_partial_days[k] = 2  # as if two days covered 13:00-14:00 only partly
    text = w.tod_hover_text(k * 3600 + 1800)
    assert histogram_window.tr("hist.hover_tod_partial", n=2) in text
    var.slot_partial_days[k] = 0
    assert histogram_window.tr("hist.hover_tod_partial", n=0) not in w.tod_hover_text(k * 3600 + 1800)


def test_time_filter_no_weekdays_does_not_compute(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    w._tf_check.setChecked(True)
    for b in w._tf_day_buttons:
        b.blockSignals(True)
        b.setChecked(False)
        b.blockSignals(False)
    w._tf_day_buttons[0].toggled.emit(False)
    _wait(qapp, w)
    assert w._result is None


def test_numeric_axis_weekdays_disabled_but_time_of_day_works(qapp, mw_numeric):
    w = _open(qapp, mw_numeric)
    _tod(qapp, w)
    np.testing.assert_allclose(w._result.variables[0].slot_values, np.arange(24))
    w._tf_check.setChecked(True)
    _wait(qapp, w)
    assert not w._tf_days_group.isEnabled()


def test_switching_view_back_redraws_distribution(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    _set(w._view_combo, "dist")
    _wait(qapp, w)
    assert w._result.settings.view == "dist"
    assert w._stack.currentWidget() is w._plot_widget


# -- export ------------------------------------------------------------------------


def test_save_slot_values(qapp, mw, tmp_path, monkeypatch):
    w = _open(qapp, mw)
    assert not w.export_windows_action.isEnabled()  # distribution plain: nothing to save
    _tod(qapp, w)
    assert w.export_windows_action.isEnabled()
    assert w.export_windows_action.text() == histogram_window.tr("hist.export_slots")
    out = tmp_path / "slots.parquet"
    filters = [histogram_window.tr("hist.export_windows_filter_parquet")]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), filters[0])))
    w.export_windows_dialog()
    table = pq.read_table(out)
    assert table.num_rows == 24
    assert table.column("slot_start").to_pylist()[:2] == ["00:00", "01:00"]
    assert table.column("power_days").to_pylist()[0] == 3
    meta = json.loads(table.schema.metadata[b"parquet_analyzer"])
    assert meta["view"] == "timeofday" and meta["time_of_day"]["slot_s"] == 3600.0
    _set(w._view_combo, "dist")
    _wait(qapp, w)
    assert w.export_windows_action.text() == histogram_window.tr("hist.export_windows")


def test_save_slot_values_csv(qapp, mw, tmp_path, monkeypatch):
    w = _open(qapp, mw)
    _tod(qapp, w)
    out = tmp_path / "slots.csv"
    filters = [histogram_window.tr("hist.export_windows_filter_parquet"), histogram_window.tr("hist.export_windows_filter_csv")]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), filters[1])))
    w.export_windows_dialog()
    assert out.read_text(encoding="utf-8").splitlines()[0] == "slot_start,power,power_valid_s,power_days"


def test_image_export_has_view_and_filter_lines(qapp, mw, tmp_path):
    w = _open(qapp, mw)
    w._tf_check.setChecked(True)
    _wait(qapp, w)
    _tod(qapp, w)
    lines = w.export_stamp_extra()
    view_line = histogram_window.tr("hist.stamp_view", text=w.tod_summary(w._result.settings))
    assert view_line in lines
    assert any(ln.startswith(("Filter:", "絞り込み:")) for ln in lines), lines
    out = tmp_path / "tod.png"
    assert w.export_image(str(out))
    assert out.exists() and out.stat().st_size > 1000
    fig = w.build_export_figure(["x"], (800, 500))
    assert fig is not None and len(fig.axes[0].patches) >= 1  # stairs are drawn


# -- follow-ups (review of the first version) ----------------------------------------


def test_compute_log_has_slot_settings_only_for_time_of_day(qapp, mw, ops):
    w = _open(qapp, mw)
    assert not any("slot_s" in k for a, k in ops if a == "histogram_compute")
    _tod(qapp, w)
    _set(w._tod_agg_combo, "integral")
    _wait(qapp, w)
    last = [k for a, k in ops if a == "histogram_compute"][-1]
    assert last["slot_s"] == 3600.0 and last["tod_agg"] == "integral" and last["tod_per_day"] is True


def test_nonfinite_slot_values_report_err_nonfinite(qapp, isolated_config, tmp_path):
    t = np.datetime64("2026-03-02T00:00:00", "s") + np.arange(ROWS, dtype="int64") * np.timedelta64(STEP_S, "s")
    path = _write(tmp_path, "big.parquet", pa.array(t), power=np.full(ROWS, 1e308))
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)  # the 1e308 column is not plotted (the main plot cannot range it)
    try:
        w = _open(qapp, win)
        _tod(qapp, w)
        assert w.status_text() == histogram_window.tr("hist.err_nonfinite")
        assert len(w._tod_plot.listDataItems()) == 0
    finally:
        win.close()


def test_hover_through_real_mouse_move(qapp, mw):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    w = _open(qapp, mw)
    _tod(qapp, w)
    w.show()
    w.resize(1000, 600)
    _drain(qapp, 0.2)
    scene_pt = w._tod_vb.mapViewToScene(QPointF(13.5 * 3600, 5.0))
    view_pt = w._tod_widget.mapFromScene(scene_pt)
    for _ in range(3):  # the real mouse route; offscreen it may not reach the scene, so also emit its signal
        QTest.mouseMove(w._tod_widget.viewport(), QPoint(view_pt.x() - 2, view_pt.y()))
        QTest.mouseMove(w._tod_widget.viewport(), QPoint(view_pt.x(), view_pt.y()))
        _drain(qapp, 0.05)
    if "13:00" not in w._hover_label.text():
        w._tod_plot.scene().sigMouseMoved.emit(scene_pt)
    text = w._hover_label.text()
    assert "13:00" in text and "14:00" in text, text


def test_trend_to_time_of_day_restores_previous_mode(qapp, mw):
    w = _open(qapp, mw)
    assert w._mode_combo.currentData() == "plain"
    _set(w._view_combo, "trend")
    _wait(qapp, w)
    assert w._mode_combo.currentData() == "bucket"
    assert w.export_windows_action.text() == histogram_window.tr("hist.export_windows")
    _tod(qapp, w)
    assert w._mode_combo.currentData() == "plain"  # restored when leaving the trend
    assert w.export_windows_action.text() == histogram_window.tr("hist.export_slots")
    _set(w._view_combo, "trend")
    _wait(qapp, w)
    assert w.export_windows_action.text() == histogram_window.tr("hist.export_windows")  # label goes back
    _set(w._view_combo, "dist")
    _wait(qapp, w)
    assert w._mode_combo.currentData() == "plain"


def test_clock_axis_has_a_label_at_the_left_edge(qapp, mw):
    w = _open(qapp, mw)
    _tod(qapp, w)
    major = w._clock_axis.tickValues(0.0, 86400.0, 800)[0][1]
    assert major[0] == 0.0 and major[-1] == 86400.0


def test_time_order_warning_shows_in_time_of_day(qapp, isolated_config, tmp_path):
    from PySide6.QtCore import Qt

    t = np.arange(2000, dtype=float)
    dec = t.copy()
    dec[10], dec[11] = dec[11], dec[10]
    path = _write(tmp_path, "t.parquet", pa.array(dec), v=np.sin(t / 30.0))
    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    assert win.load_parquet(path)
    win._add_variable_to_plot(win.plot_grid.plots[0], "v")
    _drain(qapp, 0.2)
    tt = win._time_values
    win.plot_grid.anchor_plot().setXRange(float(tt[500]), float(tt[1500]), padding=0)
    _drain(qapp, 0.3)
    win.variable_panel.clearSelection()
    win.variable_panel.findItems("v", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    w = win.open_histogram()
    _set(w._view_combo, "timeofday")
    w.recompute()
    _wait(qapp, w)
    assert w._result is not None and w._result.settings.view == "timeofday"
    assert "⚠" in w.status_text(), w.status_text()
    win.close()
