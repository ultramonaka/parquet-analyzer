"""Analysis-window frame and XY plot, stage 1 (detailed_specification.md 25.1/25.2/25.4)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer import features  # noqa: E402
from parquet_analyzer.ui import analysis_window, xy_plot_window  # noqa: E402
from parquet_analyzer.ui.analysis_window import CANCELLED  # noqa: E402
from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402
from parquet_analyzer.ui.xy_plot_window import XYPlotWindow, compute_xy  # noqa: E402


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))


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


@pytest.fixture
def feature_path(tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    return str(path)


@pytest.fixture
def mw(qapp, isolated_config, feature_path):
    win = MainWindow()
    win.show()
    assert win.load_parquet(feature_path)
    win.plot_grid.plots[0]
    win._add_variable_to_plot(win.plot_grid.plots[0], "sine")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    return win


def _open(qapp, mw, x="sine", ys=("cosine",), color=None, z=None, display=None) -> XYPlotWindow:
    w = mw.open_xy_plot()
    _configure(w, x, ys, color, z, display)
    w.recompute()
    _wait(qapp, w)
    return w


def _configure(w, x, ys, color=None, z=None, display=None):
    w.blockSignals(True)
    for widget in (w._x_combo, w._y_picker, w._color_combo, w._z_combo, w._display_combo):
        widget.blockSignals(True)
    w._x_combo.setCurrentText(x)
    w._y_picker.set_selected(ys)
    if color:
        w._color_combo.setCurrentIndex(["single", "time", "z"].index(color))
    if z:
        w._z_combo.setCurrentText(z)
    if display:
        w._display_combo.setCurrentIndex(["auto", "scatter", "density"].index(display))
    for widget in (w._x_combo, w._y_picker, w._color_combo, w._z_combo, w._display_combo):
        widget.blockSignals(False)
    w.blockSignals(False)
    w._apply_color_constraints()


# -- pure computation --------------------------------------------------------------


def _fetch_from(data):
    return lambda name, a, b: data[name][a:b]


def test_nan_inf_rows_excluded_per_y_and_counted():
    n = 100
    x = np.arange(n, dtype=float)
    y1 = x.copy()
    y1[[3, 4]] = np.nan
    y2 = x.copy()
    y2[7] = np.inf
    x_bad = x.copy()
    x_bad[10] = np.nan  # excludes row 10 from every Y
    data = {"x": x_bad, "y1": y1, "y2": y2}
    r = compute_xy(_fetch_from(data), "x", ["y1", "y2"], None, "single", "scatter", 0, n)
    assert r.valid == {"y1": n - 3, "y2": n - 2}
    assert r.excluded == 3 + 2
    assert [len(s.x) for s in r.scatter] == [n - 3, n - 2]
    assert all(np.isfinite(s.x).all() and np.isfinite(s.y).all() for s in r.scatter)


def test_auto_mode_switches_at_the_limit(monkeypatch):
    monkeypatch.setattr(xy_plot_window, "XY_SCATTER_LIMIT", 100)
    for n, expected in ((100, "scatter"), (101, "density")):
        data = {"x": np.linspace(0, 1, n), "y": np.linspace(1, 2, n)}
        r = compute_xy(_fetch_from(data), "x", ["y"], None, "single", "auto", 0, n)
        assert r.mode == expected
    # the limit is on the total over all Y
    data = {"x": np.linspace(0, 1, 60), "a": np.ones(60), "b": np.ones(60)}
    r = compute_xy(_fetch_from(data), "x", ["a", "b"], None, "single", "auto", 0, 60)
    assert r.mode == "density"


def test_forced_scatter_beyond_limit_is_thinned_evenly(monkeypatch):
    monkeypatch.setattr(xy_plot_window, "XY_SCATTER_LIMIT", 1000)
    n = 10_000
    data = {"x": np.arange(n, dtype=float), "a": np.arange(n, dtype=float), "b": np.arange(n, dtype=float)}
    r = compute_xy(_fetch_from(data), "x", ["a", "b"], None, "single", "scatter", 0, n)
    shown = sum(len(s.x) for s in r.scatter)
    assert r.mode == "scatter"
    assert shown <= 1000
    assert r.thinned == (shown, 2 * n)
    xs = r.scatter[0].x
    assert len(np.unique(np.diff(xs))) == 1  # even stride


def test_not_thinned_within_limit():
    data = {"x": np.arange(50.0), "y": np.arange(50.0)}
    r = compute_xy(_fetch_from(data), "x", ["y"], None, "single", "scatter", 0, 50)
    assert r.thinned is None and len(r.scatter[0].x) == 50


def test_density_total_equals_valid_points():
    rng = np.random.default_rng(1)
    n = 5000
    y = rng.normal(size=n)
    y[::50] = np.nan
    data = {"x": rng.normal(size=n), "y": y}
    r = compute_xy(_fetch_from(data), "x", ["y"], None, "single", "density", 0, n)
    assert r.mode == "density"
    assert r.counts.shape == (256, 256)
    assert r.counts.sum() == r.total_valid == n - len(range(0, n, 50))


def test_chunked_density_equals_unchunked(monkeypatch):
    rng = np.random.default_rng(2)
    n = 7777
    data = {"x": rng.normal(size=n), "y": rng.normal(size=n), "z": rng.normal(size=n)}
    data["y"][::13] = np.nan
    args = (_fetch_from(data), "x", ["y"], "z", "z", "density", 0, n)
    whole = compute_xy(*args)
    monkeypatch.setattr(xy_plot_window, "XY_CHUNK_ROWS", 1000)
    chunked = compute_xy(*args)
    assert np.array_equal(whole.counts, chunked.counts)
    assert np.allclose(whole.zmean, chunked.zmean, equal_nan=True)
    assert whole.valid == chunked.valid and whole.excluded == chunked.excluded


def test_chunked_scatter_thinning_matches_unchunked(monkeypatch):
    monkeypatch.setattr(xy_plot_window, "XY_SCATTER_LIMIT", 500)
    n = 3000
    data = {"x": np.arange(n, dtype=float), "y": np.arange(n, dtype=float)}
    whole = compute_xy(_fetch_from(data), "x", ["y"], None, "single", "scatter", 0, n)
    chunk = compute_xy(_fetch_from(data), "x", ["y"], None, "single", "scatter", 0, n, chunk_rows=700)
    assert np.array_equal(whole.scatter[0].x, chunk.scatter[0].x)


def test_density_z_bin_mean_with_known_values():
    # four points: two in the same bin (z=2,4 -> mean 3), one alone (z=10), one at the far corner
    x = np.array([0.0, 0.0, 0.5, 1.0])
    y = np.array([0.0, 0.0, 0.5, 1.0])
    z = np.array([2.0, 4.0, 10.0, 7.0])
    r = compute_xy(_fetch_from({"x": x, "y": y, "z": z}), "x", ["y"], "z", "z", "density", 0, 4)
    assert r.counts[0, 0] == 2 and r.zmean[0, 0] == pytest.approx(3.0)
    assert r.counts[-1, -1] == 1 and r.zmean[-1, -1] == pytest.approx(7.0)
    assert np.isnan(r.zmean[10, 10])
    assert np.nansum(r.counts) == 4


def test_empty_range_does_not_raise():
    data = {"x": np.arange(10.0), "y": np.arange(10.0)}
    for display in ("auto", "scatter", "density"):
        r = compute_xy(_fetch_from(data), "x", ["y"], None, "single", display, 5, 5)
        assert r.total_valid == 0 and r.excluded == 0
    r = compute_xy(_fetch_from({"x": np.full(5, np.nan), "y": np.ones(5)}), "x", ["y"], None, "single", "auto", 0, 5)
    assert r.total_valid == 0 and r.excluded == 5


def test_color_modes_need_a_single_y():
    data = {"x": np.arange(10.0), "a": np.arange(10.0), "b": np.arange(10.0), "z": np.arange(10.0)}
    r = compute_xy(_fetch_from(data), "x", ["a", "b"], "z", "z", "scatter", 0, 10)
    assert r.color == "single" and r.scatter[0].c is None
    r = compute_xy(_fetch_from(data), "x", ["a"], "z", "time", "scatter", 0, 10, times=np.arange(10.0) * 2)
    assert r.color == "time" and r.scatter[0].c.tolist() == (np.arange(10.0) * 2).tolist()


def test_feature_data_sine_cosine_lies_on_unit_circle(feature_path):
    table = pq.read_table(feature_path).to_pydict()
    data = {k: np.asarray(v, dtype=float) for k, v in table.items() if k != "time"}
    n = len(data["sine"])
    r = compute_xy(_fetch_from(data), "sine", ["cosine"], None, "single", "scatter", 0, n)
    s = r.scatter[0]
    assert len(s.x) == n
    assert np.allclose(s.x**2 + s.y**2, 1.0)


# -- window lifecycle ---------------------------------------------------------------


def test_window_computes_unit_circle_through_main_window(qapp, mw):
    w = _open(qapp, mw)
    assert w in mw._analysis_windows
    r = w._result
    assert r.mode == "scatter" and r.total_valid == len(mw._time_values)
    s = r.scatter[0]
    assert np.allclose(s.x**2 + s.y**2, 1.0)
    assert "Points" in w.status_text() or "点数" in w.status_text()


def test_initial_selection_from_variable_panel(qapp, mw):
    from PySide6.QtCore import Qt

    for name in ("offset_sine", "sine", "cosine"):
        mw.variable_panel.findItems(name, Qt.MatchFlag.MatchExactly)[0].setSelected(True)
    w = mw.open_xy_plot()
    _wait(qapp, w)
    assert w._x_combo.currentText() in {"offset_sine", "sine", "cosine"}
    assert len(w.selected_y()) == 2 and w._x_combo.currentText() not in w.selected_y()


def test_follow_off_ignores_main_range_and_recompute_reads_it(qapp, mw):
    w = _open(qapp, mw)
    full = w._result.row_end - w._result.row_start
    t = mw._time_values
    mw.plot_grid.set_x_range(float(t[0]), float(t[0] + (t[-1] - t[0]) / 4))
    _drain(qapp, 0.6)
    assert not w.is_computing()
    assert w._result.row_end - w._result.row_start == full  # untouched without follow
    w.recompute()
    _wait(qapp, w)
    assert w._result.row_end - w._result.row_start < full * 0.5


def test_follow_on_recomputes_after_debounce(qapp, mw):
    w = _open(qapp, mw)
    full = w._result.row_end - w._result.row_start
    w.follow_action.setChecked(True)
    _wait(qapp, w)
    calls = []
    orig = w.recompute
    w._follow_timer.timeout.disconnect()
    w._follow_timer.timeout.connect(lambda: (calls.append(1), orig()))
    t = mw._time_values
    for frac in (0.9, 0.8, 0.5):  # a burst of range changes -> a single recompute
        mw.plot_grid.set_x_range(float(t[0]), float(t[0] + (t[-1] - t[0]) * frac))
        qapp.processEvents()
    _drain(qapp, 0.8)
    _wait(qapp, w)
    assert len(calls) == 1
    assert w._result.row_end - w._result.row_start < full * 0.6


def test_stale_result_is_dropped(qapp, mw):
    w = _open(qapp, mw)
    current = w._result
    w._generation = 7
    fake = xy_plot_window.XYResult("scatter", "sine", ["cosine"], None, "single", 0, 0)
    w._on_finished(fake, -1, 6)
    assert w._result is current
    w._on_finished(fake, -1, 7)
    assert w._result is fake


def test_latest_request_wins_when_two_overlap(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    seen = []
    monkeypatch.setattr(w, "on_result", lambda res: seen.append(res.row_end))
    real = w.make_job

    def slow_first(a, b, c):
        job = real(a, b, c)
        delay = 0.3 if not seen and not getattr(slow_first, "done", False) else 0.0
        slow_first.done = True

        def run():
            time.sleep(delay)
            return job()

        return run

    monkeypatch.setattr(w, "make_job", slow_first)
    w.recompute()
    w._range = (0, 100, 0.0, 0.0)  # second request covers a different range
    w.recompute(keep_range=True)
    _wait(qapp, w)
    _drain(qapp, 0.5)
    assert seen == [100]


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


def test_reopening_file_or_changing_time_column_closes_windows(qapp, mw, feature_path):
    w1 = _open(qapp, mw)
    w2 = mw.open_xy_plot()
    assert len(mw._analysis_windows) == 2
    mw.set_time_column("ramp")
    qapp.processEvents()
    assert mw._analysis_windows == [] and w1._closed and w2._closed
    mw.set_time_column("time")
    w3 = mw.open_xy_plot()
    assert mw.load_parquet(feature_path)
    assert mw._analysis_windows == [] and w3._closed


def test_open_without_file_shows_message(qapp, isolated_config):
    win = MainWindow()
    assert win.open_xy_plot() is None
    assert win._analysis_windows == []


def test_color_by_time_or_z_is_disabled_for_multiple_y(qapp, mw):
    w = _open(qapp, mw, ys=("cosine", "ramp"), color="single")
    model = w._color_combo.model()
    assert not model.item(1).isEnabled() and not model.item(2).isEnabled()
    _configure(w, "sine", ("cosine",))
    assert model.item(1).isEnabled() and model.item(2).isEnabled()
    _configure(w, "sine", ("cosine",), color="z")
    assert w._z_combo.isEnabled()
    _configure(w, "sine", ("cosine", "ramp"))
    assert w._color_mode() == "single" and not w._z_combo.isEnabled()


def test_setting_change_recomputes_with_same_range(qapp, mw):
    w = _open(qapp, mw)
    rng = w._range
    t = mw._time_values
    mw.plot_grid.set_x_range(float(t[0]), float(t[0] + (t[-1] - t[0]) / 4))
    _drain(qapp, 0.3)
    w._display_combo.setCurrentIndex(2)  # density -> triggers recompute
    _wait(qapp, w)
    assert w._range == rng and w._result.mode == "density"


def test_density_and_scatter_render_items(qapp, mw):
    import pyqtgraph as pg

    w = _open(qapp, mw, display="density")
    assert any(isinstance(i, pg.ImageItem) for i in w._plot.items)
    assert w._colorbar.isVisible()
    _configure(w, "sine", ("cosine",), display="scatter")
    w.recompute()
    _wait(qapp, w)
    assert not any(isinstance(i, pg.ImageItem) for i in w._plot.items)
    scatters = [i for i in w._plot.items if isinstance(i, pg.ScatterPlotItem) and i is not w._cursor_item]
    assert len(scatters) == 1 and len(scatters[0].data) == w._result.total_valid


# -- cursors ------------------------------------------------------------------------


def test_cursor_highlight_follows_add_move_remove(qapp, mw):
    w = _open(qapp, mw)
    grid = mw.plot_grid
    plot = grid.plots[0]
    assert w.cursor_highlight_points() == []
    grid.set_cursor_mode_enabled(True)
    t = mw._time_values
    t1 = float(t[len(t) // 4])
    grid._on_cursor_add_requested(plot, t1)
    pts = w.cursor_highlight_points()
    row = int(np.argmin(np.abs(t - t1)))
    x_exp = float(mw.variable_window("sine", row, row + 1)[0])
    y_exp = float(mw.variable_window("cosine", row, row + 1)[0])
    assert pts == [pytest.approx((x_exp, y_exp))]
    cid = next(iter(grid._cursor_positions))
    t2 = float(t[3 * len(t) // 4])
    grid._on_cursor_move_requested(plot, cid, t2)
    row2 = int(np.argmin(np.abs(t - t2)))
    assert w.cursor_highlight_points() == [
        pytest.approx((float(mw.variable_window("sine", row2, row2 + 1)[0]), float(mw.variable_window("cosine", row2, row2 + 1)[0])))
    ]
    grid.set_cursor_mode_enabled(False)  # hidden cursors -> no highlight
    assert w.cursor_highlight_points() == []
    grid.set_cursor_mode_enabled(True)
    assert len(w.cursor_highlight_points()) == 1
    grid._on_cursor_remove_requested(plot, cid)
    assert w.cursor_highlight_points() == []


def test_cursor_outside_computed_range_is_ignored(qapp, mw):
    w = _open(qapp, mw)
    w._range = (0, 1000, 0.0, 0.0)
    w.recompute(keep_range=True)
    _wait(qapp, w)
    grid = mw.plot_grid
    grid.set_cursor_mode_enabled(True)
    grid._on_cursor_add_requested(grid.plots[0], float(mw._time_values[-1]))
    assert w.cursor_highlight_points() == []


def test_cursors_changed_signal_payload(qapp, mw):
    got = []
    mw.plot_grid.cursorsChanged.connect(got.append)
    mw.plot_grid.set_cursor_mode_enabled(True)
    mw.plot_grid._on_cursor_add_requested(mw.plot_grid.plots[0], 123.0)
    assert got[-1] == [123.0]
    mw.plot_grid.clear_cursors()
    assert got[-1] == []


# -- image export -------------------------------------------------------------------


@pytest.mark.parametrize("display,ext", [("scatter", ".png"), ("density", ".png"), ("scatter", ".svg"), ("density", ".pdf")])
def test_image_export_writes_file(qapp, mw, tmp_path, display, ext):
    w = _open(qapp, mw, color="z" if display == "density" else "single", z="ramp", display=display)
    path = tmp_path / f"xy_{display}{ext}"
    assert w.export_image(str(path))
    assert path.exists() and path.stat().st_size > 0
    assert mw.settings.last_export_folder == str(tmp_path.resolve())


def test_export_stamp_has_variable_line(qapp, mw, monkeypatch, tmp_path):
    captured = {}
    orig = xy_plot_window.build_xy_figure

    def spy(r, lines, size_px, **kw):
        captured["lines"] = list(lines)
        return orig(r, lines, size_px, **kw)

    monkeypatch.setattr(xy_plot_window, "build_xy_figure", spy)
    w = _open(qapp, mw, ys=("cosine", "noise"), display="scatter")
    w.export_image(str(tmp_path / "a.png"))
    assert "X: sine  Y: cosine, noise" in captured["lines"]
    assert captured["lines"][-1].startswith("Exported") or "|" in captured["lines"][-1]
    w2 = _open(qapp, mw, ys=("cosine",), color="z", z="ramp", display="scatter")
    w2.export_image(str(tmp_path / "b.png"))
    assert "X: sine  Y: cosine  Z: ramp" in captured["lines"]


# -- feature flag --------------------------------------------------------------------


def test_toolbar_entry_gated_by_flag(qapp, isolated_config, monkeypatch):
    monkeypatch.setitem(features._EFFECTIVE, "xy_plot", False)
    assert "xy_plot" not in MainWindow()._actions
    monkeypatch.setitem(features._EFFECTIVE, "xy_plot", True)
    win = MainWindow()
    assert "xy_plot" in win._actions
    assert win._actions["xy_plot"].text() in ("XY plot", "XYプロット", "XY / 3D plot", "XY / 3Dプロット")


# -- review fixes ------------------------------------------------------------------------


def test_export_stamp_shows_computed_range_not_main_range(qapp, mw, monkeypatch, tmp_path):
    w = _open(qapp, mw, display="scatter")
    t = mw._time_values
    plot = mw.plot_grid.anchor_plot()
    computed = f"{plot.format_x(float(t[0]))} - {plot.format_x(float(t[-1]))}"
    mw.plot_grid.set_x_range(float(t[0]), float(t[100]))  # pan the main window afterwards
    _drain(qapp, 0.3)
    captured = {}
    orig = xy_plot_window.build_xy_figure
    monkeypatch.setattr(xy_plot_window, "build_xy_figure", lambda r, lines, sz, **kw: (captured.setdefault("l", list(lines)), orig(r, lines, sz, **kw))[1])
    seen = {}
    real_save = analysis_window.save_figure
    monkeypatch.setattr(analysis_window, "save_figure", lambda fig, path, meta: (seen.update(meta), real_save(fig, path, meta))[1])
    assert w.export_image(str(tmp_path / "r.png"))
    assert any(computed.split(" - ")[0] in ln and computed.split(" - ")[1] in ln for ln in captured["l"])
    assert seen["Range"] == computed
    # main window's own export still uses its own (panned) range
    assert mw._image_stamp(__import__("datetime").datetime.now())[1]["Range"] != computed


def test_format_time_tick_precision_follows_span():
    f = xy_plot_window.format_time_tick
    v = 1_767_225_600.0
    assert "." in f(v, 1.0) and ":" in f(v, 1.0)
    assert f(v, 600).count(":") == 2 and "." not in f(v, 600)
    assert "-" in f(v, 86400) and ":" in f(v, 86400)
    assert f(v, 400 * 86400).count("-") == 2 and ":" not in f(v, 400 * 86400)
    assert "e+" not in f(v, 600)


def test_colorbar_ticks_are_time_labels_for_time_colour(qapp, mw):
    w = _open(qapp, mw, color="time", display="scatter")
    axis = w._colorbar.axis
    lo, hi = w._result.color_range
    labels = axis.tickStrings([lo, (lo + hi) / 2, hi], 1.0, 1.0)
    assert all(":" in lab and "e+" not in lab for lab in labels)
    _configure(w, "sine", ("cosine",), color="z", z="ramp", display="scatter")
    w.recompute()
    _wait(qapp, w)
    assert ":" not in axis.tickStrings([300.0], 1.0, 1.0)[0]  # plain numbers again for Z


def test_export_colorbar_formatter_is_time_for_time_colour(qapp, mw, monkeypatch, tmp_path):
    w = _open(qapp, mw, color="time", display="scatter")
    fig = xy_plot_window.build_xy_figure(w._result, ["x"], (600, 400))
    cb_ax = fig.axes[-1]
    fig.canvas.draw()
    labels = [t.get_text() for t in cb_ax.get_yticklabels() if t.get_text()]
    assert labels and all(":" in lab for lab in labels)
    assert cb_ax.yaxis.get_offset_text().get_text() == ""


def test_job_survives_time_column_switch_and_reopen(qapp, mw, feature_path, monkeypatch, caplog):
    w = _open(qapp, mw)
    real = w.make_job
    # slow job; resolves its columns up front (3a), so a switch mid-flight cannot affect it
    monkeypatch.setattr(w, "make_job", lambda a, b, c: (lambda j=real(a, b, c): (time.sleep(0.3), j())[1]))
    before = w._result
    w.recompute()
    mw.set_time_column("ramp")  # closes the window
    _drain(qapp, 0.8)
    assert w._result is before  # no stale result applied
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    mw.set_time_column("time")
    w2 = _open(qapp, mw)
    real2 = w2.make_job
    monkeypatch.setattr(w2, "make_job", lambda a, b, c: (lambda j=real2(a, b, c): (time.sleep(0.3), j())[1]))
    w2.recompute()
    assert mw.load_parquet(feature_path)
    _drain(qapp, 0.8)
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_job_does_not_touch_main_window_state(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    monkeypatch.setattr(mw, "variable_window", lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker used MainWindow")))
    w.recompute()
    _wait(qapp, w)
    assert w._result is not None and "failed" not in w.status_text().lower()


def test_cancel_stops_multichunk_job_early():
    calls = []
    n = 1000
    data = {"x": np.arange(n, dtype=float), "y": np.arange(n, dtype=float)}

    def fetch(name, a, b):
        calls.append((name, a))
        return data[name][a:b]

    cancel_at = {"n": 0}

    def cancelled():
        cancel_at["n"] += 1
        return cancel_at["n"] > 2

    r = compute_xy(fetch, "x", ["y"], None, "single", "density", 0, n, chunk_rows=10, cancelled=cancelled)
    assert r is CANCELLED
    assert len(calls) < 10  # 100 chunks were available
    assert compute_xy(fetch, "x", ["y"], None, "single", "scatter", 0, n, chunk_rows=10, cancelled=lambda: False) is not CANCELLED


def test_superseded_job_is_cancelled_and_silent(qapp, mw, monkeypatch, caplog):
    w = _open(qapp, mw)
    seen_cancel = []
    real = w.make_job

    def slow(a, b, c):
        job = real(a, b, c)

        def run():
            for _ in range(100):  # a long job polling the real cancel callback
                if c():
                    seen_cancel.append(True)
                    return CANCELLED
                time.sleep(0.01)
            return job()

        return run

    monkeypatch.setattr(w, "make_job", slow)
    w.recompute()
    monkeypatch.setattr(w, "make_job", real)
    t0 = time.time()
    w.recompute()  # supersedes the first
    _wait(qapp, w)
    assert seen_cancel and time.time() - t0 < 0.8
    assert w._result is not None and "failed" not in w.status_text().lower()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_closing_window_cancels_running_job(qapp, mw, monkeypatch):
    w = _open(qapp, mw)
    seen_cancel = []
    real = w.make_job

    def slow(a, b, c):
        def run():
            end = time.time() + 3
            while time.time() < end:
                if c():
                    seen_cancel.append(True)
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
    assert seen_cancel and time.time() - t0 < 1.0 and not mw._analysis_hub.pending


def test_main_window_close_cancels_analysis_jobs(qapp, mw):
    w = _open(qapp, mw)
    mw.close()
    assert w._closed and mw._analysis_windows == []


def test_candidate_lists_refresh_with_derived_variables(qapp, mw):
    w = _open(qapp, mw)
    mw._on_add_derived_variable("dd", "sine*2")
    _drain(qapp, 0.1)
    assert "dd" in [w._x_combo.itemText(i) for i in range(w._x_combo.count())]
    assert w._x_combo.currentText() == "sine" and w.selected_y() == ["cosine"]
    _configure(w, "sine", ("dd",))
    mw.delete_derived_variable("dd")
    _drain(qapp, 0.1)
    assert "dd" not in [w._z_combo.itemText(i) for i in range(w._z_combo.count())]
    assert w.selected_y() == []
    w.recompute()
    assert "Select" in w.status_text() or "選択" in w.status_text()
    assert not w.is_computing()


def test_unknown_variable_is_a_status_message_not_an_error(qapp, mw, caplog):
    w = _open(qapp, mw)
    w._x_combo.addItem("ghost")
    w._x_combo.setCurrentText("ghost")
    _wait(qapp, w)
    assert "ghost" in w.status_text()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_excluded_is_per_y_in_status(qapp, mw):
    w = _open(qapp, mw, ys=("cosine", "with_nan"), display="scatter")
    r = w._result
    assert r.excluded_per_y["cosine"] == 0 and r.excluded_per_y["with_nan"] > 0
    assert f"with_nan: {r.excluded_per_y['with_nan']}" in w.status_text()
    w1 = _open(qapp, mw, ys=("with_nan",), display="scatter")
    assert f"{w1._result.excluded})" in w1.status_text() and "with_nan:" not in w1.status_text()


def test_legend_markers_are_readable(qapp, mw):
    w = _open(qapp, mw, ys=("cosine", "noise"), display="scatter")
    items = [pair[0].item for pair in w._legend.items]
    assert len(items) == 2
    assert all(it.opts["size"] >= 6 for it in items)
