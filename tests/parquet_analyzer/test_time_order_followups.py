"""Chapter 56 stage 1 (data.ja.md 56.1-56.3, 56.6): range warnings, duplicate timestamps and
abnormal time axes through the real open path."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.core import numeric
from parquet_analyzer.core.numeric import TimeOrderScanner, check_time_order
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog

_SRC = str(Path(__file__).resolve().parents[2] / "src")
N = 2000
WARN = "⚠"


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _examples(n: int) -> int:
    return n * (10 if os.environ.get("HYPOTHESIS_PROFILE") == "thorough" else 1)


def _wait(qapp, cond, timeout_s=20.0):
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert cond(), "timed out"


def _settle(qapp, win):
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.01)
    _wait(qapp, lambda: not win._busy_sources)


def _rep(values):
    return check_time_order(np.array(values, dtype=float))


def _one_shot(values):
    s = TimeOrderScanner()
    s.feed(np.asarray(values, dtype=float), 0)
    return s.report()


# -- 56.2 check_time_order: duplicates ----------------------------------------------------


def test_duplicates_none():
    r = _rep([1, 2, 3, 4])
    assert (r.duplicate_count, r.first_duplicate_index) == (0, None)


def test_duplicates_consecutive_and_ok_unchanged():
    r = _rep([1, 2, 2, 2, 3])
    assert (r.duplicate_count, r.first_duplicate_index) == (2, 2)
    assert r.ok  # duplicates keep ranges correct (56.2)


def test_duplicates_separated_are_not_counted():
    r = _rep([1, 2, 3, 2, 4])  # a decrease, not a duplicate
    assert r.duplicate_count == 0 and r.decreasing_count == 1
    r = _rep([1, 1, 2, 3, 3])
    assert (r.duplicate_count, r.first_duplicate_index) == (2, 1)


def test_duplicates_across_chunk_boundary(monkeypatch):
    monkeypatch.setattr(numeric, "_TIME_ORDER_CHUNK_ROWS", 4)
    v = np.array([0, 1, 2, 3, 3, 4, 5, 6, 7, 7], dtype=float)  # 3|3 straddles the first boundary
    r = check_time_order(v)
    assert (r.duplicate_count, r.first_duplicate_index) == (2, 4)
    assert r == _one_shot(v)


def test_duplicates_nan_between_equal_values_compares_with_last_finite():
    # Same rule as decreasing_count: a non-finite value is skipped, so 2, nan, 2 is one duplicate.
    r = _rep([1, 2, np.nan, 2, 3])
    assert (r.duplicate_count, r.first_duplicate_index, r.nan_count) == (1, 3, 1)
    assert not r.ok
    assert _rep([np.nan, 5, np.nan]).duplicate_count == 0


def test_duplicates_inf_is_not_finite_so_never_a_duplicate():
    r = _rep([1, np.inf, np.inf, -np.inf, -np.inf, 2])
    assert r.duplicate_count == 0 and r.nan_count == 4


@settings(max_examples=_examples(150))
@given(
    values=st.lists(
        st.one_of(st.integers(-5, 5).map(float), st.sampled_from([np.nan, np.inf, -np.inf])), max_size=60
    ),
    chunk=st.integers(1, 9),
)
def test_chunked_equals_one_shot(values, chunk):
    v = np.array(values, dtype=float)
    s = TimeOrderScanner()
    for start in range(0, v.size, chunk):
        s.feed(v[start : start + chunk], start)
    assert s.report() == _one_shot(v)
    fin = v[np.isfinite(v)]
    assert s.report().duplicate_count == int(np.count_nonzero(np.diff(fin) == 0))


# -- files ------------------------------------------------------------------------------


def _write(path, columns):
    pq.write_table(pa.table(columns), path)
    return str(path)


def _file(tmp_path):
    t = np.arange(N, dtype=float)
    dec = t.copy()
    dec[10], dec[11] = dec[11], dec[10]  # one decrease at row index 11
    dup = t.copy()
    dup[100] = dup[99]  # one duplicate at row index 100
    nan_t = t.copy()
    nan_t[50] = np.nan
    return _write(
        tmp_path / "t.parquet",
        {"dec": dec, "good": t, "dup": dup, "nan_t": nan_t, "v": np.sin(t / 30.0), "w": np.cos(t / 30.0)},
    )


def _open(tmp_path, first="good"):
    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    assert win.load_parquet(_file(tmp_path))
    if first != win._time_column:
        win.set_time_column(first)
    return win


# -- 56.2 label -------------------------------------------------------------------------


def test_label_duplicates_only_shown_but_ok(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "dup")
    label = win._time_order_label
    assert win._time_order_report.ok  # duplicates do not change ok
    assert label.isVisible()
    assert label.text().count(WARN) == 1 and "1" in label.text()
    assert "101" in label.toolTip()  # first duplicate: index 100, shown 1-based
    win.set_time_column("good")
    assert not label.isVisible()
    win.close()


def test_label_combined_wording(qapp, isolated_config, tmp_path):
    path = _write(
        tmp_path / "c.parquet",
        {"t": np.array([1.0, 2.0, 2.0, np.nan, 1.5, 3.0]), "v": np.arange(6, dtype=float)},
    )
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    r = win._time_order_report
    assert (r.decreasing_count, r.nan_count, r.duplicate_count) == (1, 1, 1)
    assert win._time_order_label.text().count(WARN) == 3  # not ascending + NaN + duplicates
    win.close()


def test_state_snapshot_and_oplog_carry_duplicate_count(qapp, isolated_config, tmp_path, monkeypatch):
    ops = []
    monkeypatch.setattr(main_window_module, "log_op", lambda action, **kw: ops.append((action, kw)))
    win = _open(tmp_path, "dup")
    assert win.state_snapshot()["file"]["time_order"]["duplicate_count"] == 1
    assert any(a == "time_order_warning" and kw.get("duplicate_count") == 1 for a, kw in ops)
    win.close()


# -- 56.1 time_order_unreliable ---------------------------------------------------------------


def test_unreliable_flag_by_axis(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "good")
    assert not win.time_order_unreliable()
    for name, expected in [("dec", True), ("nan_t", False), ("dup", False), ("good", False)]:
        win.set_time_column(name)
        assert win.time_order_unreliable() is expected, name
    win.close()


def _plot_with_v(qapp, win):
    plot = win.plot_grid.plots[0]
    win._add_variable_to_plot(plot, "v")
    _settle(qapp, win)
    return win.plot_grid.plots[0]


def test_cursor_readout_warning_follows_axis(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "dec")
    plot = _plot_with_v(qapp, win)
    plot.add_cursor("c1", 500.0)
    label = plot._cursors["c1"]["label"]
    assert label.toPlainText().startswith(WARN) and label.toolTip()
    for name in ("nan_t", "dup", "good"):
        win.set_time_column(name)
        _settle(qapp, win)
        plot = win.plot_grid.plots[0]
        assert not plot._time_unreliable, name
        plot.add_cursor("c2", 500.0)
        assert not plot._cursors["c2"]["label"].toPlainText().startswith(WARN), name
    win.close()


def test_cursor_warning_removed_on_existing_cursor_when_axis_changes(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "dec")
    plot = _plot_with_v(qapp, win)
    plot.set_time_unreliable(False)  # as the grid does after switching to a clean axis
    plot.add_cursor("c1", 500.0)
    assert not plot._cursors["c1"]["label"].toPlainText().startswith(WARN)
    plot.set_time_unreliable(True, "tip")
    assert plot._cursors["c1"]["label"].toPlainText().startswith(WARN)  # existing cursors are refreshed
    win.close()


def test_hover_readout_warning_and_cached(qapp, isolated_config, tmp_path, monkeypatch):
    win = _open(tmp_path, "dec")
    plot = _plot_with_v(qapp, win)
    calls = []
    monkeypatch.setattr(MainWindow, "time_order_unreliable", lambda self: calls.append(1) or True)
    monkeypatch.setattr(main_window_module, "check_time_order", lambda *a, **k: calls.append(1))
    plot.set_hover_enabled(True)
    for x in (100.0, 200.0, 300.0):
        plot.set_hover_x(x)
        assert plot._hover_label.toPlainText().startswith(WARN)
    assert plot._hover_label.toolTip()
    assert calls == []  # hover reads the cached flag only
    monkeypatch.undo()
    win.set_time_column("good")
    _settle(qapp, win)
    plot = win.plot_grid.plots[0]
    plot.set_hover_enabled(True)
    plot.set_hover_x(100.0)
    assert not plot._hover_label.toPlainText().startswith(WARN)
    win.close()


def test_hover_no_warning_for_nan_only_and_duplicates_only(qapp, isolated_config, tmp_path):
    for axis in ("nan_t", "dup"):
        win = _open(tmp_path, axis)
        plot = _plot_with_v(qapp, win)
        plot.set_hover_enabled(True)
        plot.set_hover_x(100.0)
        assert not plot._hover_label.toPlainText().startswith(WARN), axis
        win.close()


def test_plot_added_later_inherits_flag(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "dec")
    win.plot_grid.add_plot()
    assert len(win.plot_grid.plots) >= 2
    assert all(p._time_unreliable for p in win.plot_grid.plots if p.x_axis_datetime)
    win.close()


def _stats_dialog(qapp, win):
    win.variable_panel.clearSelection()
    win.variable_panel.findItems("v", Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    for d in win.findChildren(StatsDialog):
        d.close()
        d.deleteLater()
    win._on_stats_requested()
    _wait(qapp, lambda: win.findChildren(StatsDialog))
    _settle(qapp, win)
    return win.findChildren(StatsDialog)[-1]


@pytest.mark.parametrize("axis,expected", [("dec", True), ("nan_t", False), ("dup", False), ("good", False)])
def test_stats_dialog_warning(qapp, isolated_config, tmp_path, axis, expected):
    win = _open(tmp_path, axis)
    dlg = _stats_dialog(qapp, win)
    assert dlg.warning_label.isHidden() is (not expected)
    assert WARN in dlg.warning_label.text()  # the label exists above the table; visible only when needed
    win.close()


def test_stats_dialog_warning_disappears_after_clean_axis(qapp, isolated_config, tmp_path):
    win = _open(tmp_path, "dec")
    assert not _stats_dialog(qapp, win).warning_label.isHidden()
    win.set_time_column("good")
    assert _stats_dialog(qapp, win).warning_label.isHidden()
    win.close()


def _narrow_to_clean_part(qapp, win):
    """The decrease is at row 11 and analysis windows refuse a range containing it: look at 500..1500."""
    t = win._time_values
    win.plot_grid.anchor_plot().setXRange(float(t[500]), float(t[1500]), padding=0)
    _settle(qapp, win)


def _wait_window(qapp, w):
    _wait(qapp, lambda: not w.is_computing())
    for _ in range(5):
        qapp.processEvents()


@pytest.mark.parametrize("axis,expected", [("dec", True), ("dup", False), ("good", False)])
def test_xy_and_histogram_warning(qapp, isolated_config, tmp_path, axis, expected):
    win = _open(tmp_path, axis)
    _narrow_to_clean_part(qapp, win)
    win.variable_panel.clearSelection()
    for n in ("v", "w"):
        win.variable_panel.findItems(n, Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    xy = win.open_xy_plot("2d")
    _wait_window(qapp, xy)
    assert xy._result is not None
    hist = win.open_histogram()
    hist.recompute()
    _wait_window(qapp, hist)
    assert hist._result is not None
    for w in (xy, hist):
        assert (WARN in w.status_text()) is expected, (axis, w.status_text())
    win.close()


# -- 56.3 / 56.6 abnormal axes through load_parquet ------------------------------------------

_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {src!r})
    import numpy as np
    from PySide6.QtWidgets import QApplication, QMessageBox
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    app = QApplication(sys.argv[:1])
    from parquet_analyzer.ui.main_window import MainWindow
    from parquet_analyzer.ui.stats_dialog import StatsDialog
    w = MainWindow(); w.resize(900, 700); w.show()
    assert w.load_parquet({path!r})
    def pump(n=8):
        for _ in range(n):
            app.processEvents(); time.sleep(0.01)
    pump()
    plot = w.plot_grid.plots[0]
    w._add_variable_to_plot(plot, "val"); pump()
    w.plot_grid.go_home(); pump()
    w.grab(); pump()
    w.variable_panel.selectAll()
    w._on_stats_requested()
    end = time.monotonic() + 20
    while not w.findChildren(StatsDialog) and time.monotonic() < end:
        app.processEvents(); time.sleep(0.01)
    assert w.findChildren(StatsDialog)
    fin = np.asarray(w._time_values, dtype=float)
    fin = fin[np.isfinite(fin)]
    if len(fin):
        w.plot_grid.plots[0].add_cursor("c1", float(fin[len(fin) // 2]))
    pump()
    w.grab()
    print("DONE")
    """
)

_BASE = np.datetime64("2026-01-01T00:00:00", "us") + np.arange(N, dtype="int64") * np.timedelta64(1, "s")


def _kinds():
    t = np.arange(N, dtype=float)
    dup = t.copy()
    dup[10:20] = dup[9]
    head = t.copy()
    head[:5] = np.nan
    mid = t.copy()
    mid[900:910] = np.nan
    tail = t.copy()
    tail[-5:] = np.nan
    inf = t.copy()
    inf[3], inf[4], inf[1000] = np.inf, -np.inf, np.inf
    nat = _BASE.copy()
    nat[[0, 500, N - 1]] = np.datetime64("NaT", "us")
    return {
        "duplicates": pa.array(dup),
        "nan_head": pa.array(head),
        "nan_mid": pa.array(mid),
        "nan_tail": pa.array(tail),
        "inf": pa.array(inf),
        "tz_utc": pa.array(_BASE, type=pa.timestamp("us", tz="UTC")),
        "tz_tokyo": pa.array(_BASE, type=pa.timestamp("us", tz="Asia/Tokyo")),
        "tz_nat": pa.array(nat, type=pa.timestamp("us", tz="UTC")),
    }


@pytest.mark.parametrize("kind", ["duplicates", "nan_head", "nan_mid", "nan_tail", "inf", "tz_utc", "tz_tokyo", "tz_nat"])
def test_abnormal_time_axis_opens_without_hang(tmp_path, kind):
    path = tmp_path / f"{kind}.parquet"
    pq.write_table(pa.table({"time": _kinds()[kind], "val": pa.array(np.sin(np.arange(N) / 50.0))}), path)
    script = _SCRIPT.format(src=_SRC, path=str(path))
    env = {**os.environ, "PARQUET_ANALYZER_ROOT": str(tmp_path / "root")}
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{kind}: app hung (29.4)")
    assert "DONE" in proc.stdout, proc.stdout + proc.stderr


def test_tz_aware_time_axis_pinned_behaviour(qapp, isolated_config, tmp_path):
    """56.3: today a tz-aware column becomes the epoch seconds of the instant (UTC or not), a clean one
    raises no warning, and NaT counts as non-finite. Stage 2 (56.5) changes only how it is displayed."""
    cols = _kinds()
    expected = _BASE.astype("datetime64[us]").astype("int64") / 1e6
    for kind in ("tz_utc", "tz_tokyo"):
        path = _write(tmp_path / f"{kind}.parquet", {"time": cols[kind], "val": np.arange(N, dtype=float)})
        win = MainWindow()
        win.show()
        assert win.load_parquet(path)
        np.testing.assert_allclose(np.asarray(win._time_values, dtype=float), expected)
        assert win._time_order_report.ok and not win._time_order_label.isVisible()
        assert not win.time_order_unreliable()
        win.close()
    path = _write(tmp_path / "nat.parquet", {"time": cols["tz_nat"], "val": np.arange(N, dtype=float)})
    win = MainWindow()
    win.show()
    assert win.load_parquet(path)
    r = win._time_order_report
    assert r.nan_count == 3 and r.decreasing_count == 0 and not r.ok
    assert win._time_order_label.isVisible() and not win.time_order_unreliable()
    win.close()
