"""XY window small fixes (analysis.ja.md 44.5): legend click hides/shows a series, and the Y
selector is a button opening a searchable checkbox list."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).parent))
from generate_feature_test_parquet import build_table  # noqa: E402

from parquet_analyzer.ui.main_window import MainWindow  # noqa: E402
from parquet_analyzer.ui.name_picker import NamePicker  # noqa: E402
from parquet_analyzer.ui.xy_plot_window import build_xy_figure  # noqa: E402


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
def mw(qapp, isolated_config, tmp_path):
    path = tmp_path / "feature_test.parquet"
    pq.write_table(build_table(duration_s=60.0, rate_hz=100.0, gap_s=5.0), path)
    win = MainWindow()
    win.show()
    assert win.load_parquet(str(path))
    win._add_variable_to_plot(win.plot_grid.plots[0], "sine")
    qapp.processEvents()
    win.plot_grid.go_home()
    _drain(qapp)
    return win


def _open_two_y(qapp, mw):
    w = mw.open_xy_plot()
    w.show()
    w._x_combo.setCurrentText("sine")
    w._y_picker.set_selected(["cosine", "noise"])
    w._display_combo.setCurrentIndex(1)  # scatter
    w.recompute()
    _wait(qapp, w)
    _drain(qapp)
    return w


def _click_legend_entry(qapp, w, index):
    """A real left click on the legend swatch, routed through the GraphicsView's viewport."""
    sample, _label = w._legend.items[index]
    centre = sample.mapToScene(sample.boundingRect().center())
    pos = w._glw.mapFromScene(centre)
    QTest.mouseClick(w._glw.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    _drain(qapp)


# -- legend click -----------------------------------------------------------------


def test_legend_click_hides_and_shows_the_series(qapp, mw):
    w = _open_two_y(qapp, mw)
    assert len(w._legend.items) == 2
    items = dict(w._series_items)
    assert all(it.isVisible() for it in items.values())
    _click_legend_entry(qapp, w, 0)
    name = w._legend.items[0][1].text
    assert w.hidden_series() == {name}
    assert not items[name].isVisible()  # the REAL points are gone, not only the legend swatch
    other = next(n for n in items if n != name)
    assert items[other].isVisible()
    _click_legend_entry(qapp, w, 0)
    assert w.hidden_series() == set() and items[name].isVisible()


def test_hidden_series_stays_hidden_after_recompute(qapp, mw):
    w = _open_two_y(qapp, mw)
    _click_legend_entry(qapp, w, 0)
    hidden = w.hidden_series()
    assert len(hidden) == 1
    w.recompute()
    _wait(qapp, w)
    _drain(qapp)
    assert w.hidden_series() == hidden
    name = next(iter(hidden))
    assert not w._series_items[name].isVisible()
    (proxy,) = [p for p, n in w._legend_proxies.items() if n == name]
    assert not proxy.isVisible()  # the legend swatch keeps showing it as off


def test_hidden_series_forgotten_when_deselected(qapp, mw):
    w = _open_two_y(qapp, mw)
    _click_legend_entry(qapp, w, 0)
    name = next(iter(w.hidden_series()))
    w._y_picker.set_selected([n for n in ("cosine", "noise") if n != name])
    _wait(qapp, w)
    assert w.hidden_series() == set()
    w._y_picker.set_selected(["cosine", "noise"])  # selected again: visible again
    _wait(qapp, w)
    assert all(it.isVisible() for it in w._series_items.values())


def test_hidden_series_not_in_image_export(qapp, mw):
    w = _open_two_y(qapp, mw)
    fig = w.build_export_figure(["stamp"], (600, 400))
    assert len(fig.axes[0].collections) == 2
    _click_legend_entry(qapp, w, 0)
    fig = w.build_export_figure(["stamp"], (600, 400))
    assert len(fig.axes[0].collections) == 1
    name = next(iter(w.hidden_series()))
    assert name not in fig.axes[0].get_ylabel()
    assert len(build_xy_figure(w._result, ["s"], (600, 400)).axes[0].collections) == 2  # default: all


def test_hidden_series_not_highlighted_by_cursors(qapp, mw):
    w = _open_two_y(qapp, mw)
    r = w._result
    t = float(mw._time_values[r.row_start + 10])
    w._on_cursors_changed([t])
    assert len(w.cursor_highlight_points()) == 2
    _click_legend_entry(qapp, w, 0)
    w._on_cursors_changed([t])
    assert len(w.cursor_highlight_points()) == 1


def test_hidden_series_is_ignored_by_the_density(qapp, mw):
    w = _open_two_y(qapp, mw)
    w._display_combo.setCurrentIndex(2)  # density
    _wait(qapp, w)
    both = w._result.counts.sum()
    w.set_series_hidden("noise", True)
    w.recompute(keep_range=True)
    _wait(qapp, w)
    # 44.5 (2026-10-07): the density has no legend, so a hidden Y is drawn anyway
    assert w._result.mode == "density" and w._result.counts.sum() == both


def test_single_colour_mapped_series_never_stays_hidden(qapp, mw):
    w = _open_two_y(qapp, mw)
    w.set_series_hidden("noise", True)
    w._y_picker.set_selected(["cosine"])
    w._color_combo.setCurrentIndex(1)  # time: colour-mapped, no legend entry to bring it back
    _wait(qapp, w)
    assert w.hidden_series() == set()


# -- Y picker ---------------------------------------------------------------------


def test_picker_button_text_lists_names_and_count(qapp):
    p = NamePicker()
    p.set_candidates(["sine", "cosine", "ramp", "noise"])
    p.set_selected(["sine"])
    assert p.text() == "sine"
    p.set_selected(["sine", "cosine"])
    assert p.text() == "sine, cosine (2)"
    p.set_selected(["sine", "cosine", "ramp"])
    assert p.text().endswith("(3)") and "sine, cosine" in p.text()
    p.set_selected([])
    assert p.selected() == []


def test_picker_search_and_checkbox_clicks(qapp):
    p = NamePicker()
    p.set_candidates(["sine", "cosine", "ramp", "offset_sine"])
    p.show()
    p.show_popup()
    qapp.processEvents()
    seen = []
    p.selectionChanged.connect(lambda: seen.append(p.selected()))
    popup = p._popup
    popup.search.setText("SIN")  # case-insensitive substring
    visible = [popup.list.item(i).text() for i in range(popup.list.count()) if not popup.list.item(i).isHidden()]
    assert visible == ["sine", "cosine", "offset_sine"]
    lst = popup.list
    for i in range(lst.count()):  # click the first two visible rows through the viewport
        it = lst.item(i)
        if it.text() in ("sine", "offset_sine"):
            QTest.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=lst.visualItemRect(it).center())
    assert p.selected() == ["sine", "offset_sine"]
    assert p.text() == "sine, offset_sine (2)"
    assert seen[-1] == ["sine", "offset_sine"]
    popup.search.clear()
    assert all(not lst.item(i).isHidden() for i in range(lst.count()))
    # clicking a checked row unchecks it
    it = next(lst.item(i) for i in range(lst.count()) if lst.item(i).text() == "sine")
    QTest.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=lst.visualItemRect(it).center())
    assert p.selected() == ["offset_sine"]
    p._popup.hide()
    p.close()


def test_picker_opens_by_button_click_and_single_mode(qapp):
    p = NamePicker()
    p.set_candidates(["a", "b", "c"])
    p.show()
    QTest.mouseClick(p, Qt.MouseButton.LeftButton)
    assert p._popup.isVisible()
    p._popup.hide()
    p.set_single(True)
    p.set_selected(["a", "b"])
    assert p.selected() == ["a"]
    p.show_popup()
    lst = p._popup.list
    QTest.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=lst.visualItemRect(lst.item(2)).center())
    assert p.selected() == ["c"] and not p._popup.isVisible()  # radio-like, closes on pick
    p.close()


def test_xy_window_uses_picker_for_y(qapp, mw):
    w = mw.open_xy_plot()
    assert isinstance(w._y_picker, NamePicker)
    w._y_picker.set_selected(["cosine", "noise"])
    assert w.selected_y() == ["cosine", "noise"] and "(2)" in w._y_picker.text()
    names = np.array(w._y_picker.candidates())
    assert "sine" in names


# -- review follow-up (44.5) --------------------------------------------------------------


def _click_legend_label(qapp, w, index):
    """A real left click on the legend's name text (not the swatch)."""
    _sample, label = w._legend.items[index]
    centre = label.mapToScene(label.boundingRect().center())
    QTest.mouseClick(w._glw.viewport(), Qt.MouseButton.LeftButton, pos=w._glw.mapFromScene(centre))
    _drain(qapp)


def test_legend_click_on_the_name_text_toggles_too(qapp, mw):
    w = _open_two_y(qapp, mw)
    name = w._legend.items[0][1].text
    items = dict(w._series_items)
    _click_legend_label(qapp, w, 0)
    assert w.hidden_series() == {name} and not items[name].isVisible()
    assert not w._legend.items[0][0].item.isVisible()  # the swatch follows
    _click_legend_label(qapp, w, 0)
    assert w.hidden_series() == set() and items[name].isVisible()


def test_density_draws_hidden_ys_but_keeps_them_hidden_for_scatter(qapp, mw):
    w = _open_two_y(qapp, mw)
    _click_legend_entry(qapp, w, 0)
    hidden = w.hidden_series()
    assert len(hidden) == 1
    n = len(mw._time_values)
    w._display_combo.setCurrentIndex(w._display_combo.findData("density"))
    _wait(qapp, w)
    assert w._result.mode == "density"
    # both selected Ys are counted although one is hidden: 2 Ys x all finite rows
    assert w._result.counts.sum() == w._result.total_valid > n  # more than one Y's worth of points
    w._display_combo.setCurrentIndex(w._display_combo.findData("scatter"))
    _wait(qapp, w)
    assert w.hidden_series() == hidden  # still hidden once back in the scatter
    name = next(iter(hidden))
    assert not w._series_items[name].isVisible()
