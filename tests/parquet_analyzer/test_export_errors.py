"""Abnormal input for plot image export (export.ja.md 19)."""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox

from parquet_analyzer.io.settings import load_settings
from parquet_analyzer.ui import main_window as mw
from parquet_analyzer.ui.image_export import (
    PlotSnapshot,
    SeriesSnapshot,
    build_figure,
    export_format,
    export_plots_image,
)
from parquet_analyzer.ui.main_window import MainWindow

X0 = 1_767_225_600.0
NAN = float("nan")
INF = float("inf")


def _series(x=None, y=None, name="s", color="#112233"):
    x = np.linspace(X0, X0 + 10, 50) if x is None else np.asarray(x, float)
    y = np.sin(x) if y is None else np.asarray(y, float)
    return SeriesSnapshot(name, color, x, y)


def _plot(series=None, xr=(X0, X0 + 10), yr=(-1.0, 1.0), dt=True, **kw):
    return PlotSnapshot(dt, xr, yr, [_series()] if series is None else series, **kw)


def _export(tmp_path, snaps, name="a.png", size=(800, 600), stamp=("stamp",)):
    path = tmp_path / name
    export_plots_image(snaps, path, list(stamp), {"Title": "t", "Software": "x"}, size)
    return path


# -- renders (degenerate but finite input) --------------------------------------------------------


@pytest.mark.parametrize(
    "snaps",
    [
        [],  # no plots at all
        [_plot([])],  # a plot without series
        [_plot([_series(y=[NAN] * 50)])],  # all-NaN values
        [_plot([_series(x=[NAN] * 50)])],  # all-NaN time
        [_plot([_series(x=[], y=[])])],  # empty arrays
        [_plot([_series(x=[X0], y=[1.0])])],  # one point
        [_plot(xr=(X0, X0))],  # zero-width ranges
        [_plot(yr=(1.0, 1.0))],
        [_plot(xr=(X0 + 10, X0), yr=(1.0, -1.0))],  # inverted
        [_plot(cursors=[NAN, INF, 1e30])],  # junk cursors
        [_plot(dt=False, xr=(0.0, 50.0))],
        [_plot() for _ in range(25)],  # many panes
        [_plot([_series(y=[1e300, -1e300, 0] * 10, x=np.arange(30.0) + X0)], yr=(-1e300, 1e300))],
    ],
    ids=["none", "no-series", "nan-y", "nan-x", "empty", "one-point", "xr-equal", "yr-equal", "inverted", "cursors", "freq", "many", "huge-y"],
)
@pytest.mark.parametrize("suffix", [".png", ".svg"])
def test_degenerate_but_finite_snapshots_still_produce_a_file(tmp_path, snaps, suffix):
    path = _export(tmp_path, snaps, "a" + suffix)
    assert path.stat().st_size > 0


@pytest.mark.parametrize("size", [(0, 0), (-5, -5), (1, 1), (4000, 3000)])
def test_odd_sizes_are_clamped(tmp_path, size):
    assert _export(tmp_path, [_plot()], size=size).stat().st_size > 0


def test_stamp_with_control_characters_and_japanese(tmp_path):
    assert _export(tmp_path, [_plot()], stamp=("a\x00b", "日本語のスタンプ", "")).stat().st_size > 0


def test_series_name_with_japanese_and_markup(tmp_path):
    assert _export(tmp_path, [_plot([_series(name="温度 $x^{ \\bad")])], "n.svg").stat().st_size > 0


# -- raises ValueError / OSError (what MainWindow.export_image catches) -------------------------


@pytest.mark.parametrize(
    "snap",
    [
        _plot(xr=(NAN, NAN)),
        _plot(xr=(-INF, INF)),
        _plot(yr=(NAN, NAN)),
        _plot(yr=(-INF, INF)),
        _plot(xr=(-1e300, 1e300)),
        _plot(xr=(1e18, 2e18)),
        _plot(dt=False, xr=(NAN, 5.0)),
        _plot([_series(x=np.arange(5.0) + X0, y=np.arange(4.0))]),  # length mismatch
        _plot([_series(color="notacolor")]),
        _plot(height_ratio=NAN),
    ],
    ids=["x-nan", "x-inf", "y-nan", "y-inf", "x-huge", "x-epoch-overflow", "freq-nan", "mismatch", "bad-color", "ratio-nan"],
)
def test_invalid_snapshots_raise_valueerror_not_something_else(tmp_path, snap):
    with pytest.raises(ValueError):
        _export(tmp_path, [snap])


@pytest.mark.parametrize("name", ["a", "a.txt", "a.PNGX", ".png"[:0] + "noext"])
def test_unsupported_suffix_fails_fast_before_drawing(tmp_path, monkeypatch, name):
    monkeypatch.setattr("parquet_analyzer.ui.image_export.build_figure", lambda *a, **k: pytest.fail("drew anyway"))
    with pytest.raises(ValueError):
        export_plots_image([_plot()], tmp_path / name, [], {}, (800, 600))


def test_export_format_of_a_directory_like_path():
    with pytest.raises(ValueError):
        export_format("")
    assert export_format("dir.v2/file.JPEG") == "jpeg"


def test_missing_parent_directory_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        _export(tmp_path, [_plot()], "nodir/sub/a.png")


def test_target_is_a_directory_is_an_oserror(tmp_path):
    (tmp_path / "d.png").mkdir()
    with pytest.raises(OSError):
        _export(tmp_path, [_plot()], "d.png")


def test_build_figure_does_not_mutate_snapshots():
    snap = _plot(cursors=[X0 + 1])
    before = (list(snap.cursors), snap.x_range, snap.y_range, snap.series[0].x.copy())
    build_figure([snap], ["s"], (400, 300))
    assert (snap.cursors, snap.x_range, snap.y_range) == before[:3]
    assert np.array_equal(snap.series[0].x, before[3])


# -- MainWindow.export_image -----------------------------------------------------------------------


def _wait(win, qapp, timeout=10.0):
    end = time.monotonic() + timeout
    while win._busy_sources and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert not win._busy_sources


@pytest.fixture
def boxes(monkeypatch):
    calls = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: calls.append((_n, str(a[2])))))
    return calls


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, boxes):
    w = MainWindow()
    w.resize(800, 600)
    w.load_parquet(str(sample_parquet_path))
    w._add_variable_to_plot(w.plot_grid.plots[0], "a")
    _wait(w, qapp)
    yield w
    w.close()


@pytest.mark.parametrize("exc", [RuntimeError("boom"), TypeError("set_text()"), MemoryError(), KeyError("k"), OSError(13, "denied")])
def test_any_failure_inside_the_export_is_reported_not_raised(win, boxes, monkeypatch, tmp_path, exc):
    """Only OSError/ValueError were caught; a TypeError from matplotlib escaped the slot."""

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(mw, "export_plots_image", boom)
    assert win.export_image(str(tmp_path / "q.png")) is False
    assert [b[0] for b in boxes] == ["warning"]
    assert not (tmp_path / "q.png").exists()


def test_failed_export_does_not_remember_the_folder(win, boxes, monkeypatch, tmp_path):
    monkeypatch.setattr(mw, "export_plots_image", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    win.export_image(str(tmp_path / "q.png"))
    assert win.settings.last_export_folder is None
    assert load_settings().last_export_folder is None


def test_export_to_missing_folder_warns_and_succeeds_nowhere(win, boxes, tmp_path):
    assert win.export_image(str(tmp_path / "nope" / "x.png")) is False
    assert [b[0] for b in boxes] == ["warning"]


def test_export_to_directory_path_warns(win, boxes, tmp_path):
    (tmp_path / "d.png").mkdir()
    assert win.export_image(str(tmp_path / "d.png")) is False
    assert [b[0] for b in boxes] == ["warning"]


def test_export_succeeds_even_if_remembering_the_folder_fails(win, boxes, tmp_path, monkeypatch):
    """The image is written; a failing settings write must not turn that into an error."""
    import parquet_analyzer.io.settings as S

    monkeypatch.setattr(S.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert win.export_image(str(tmp_path / "ok.png")) is True
    assert (tmp_path / "ok.png").stat().st_size > 0
    assert boxes == []


def test_export_with_unicode_and_spaces_in_the_path(win, boxes, tmp_path):
    d = tmp_path / "出力 先"
    d.mkdir()
    assert win.export_image(str(d / "グラフ 1.png")) is True


def test_export_after_closing_all_series_is_refused_politely(win, boxes, tmp_path):
    for name in list(win.plot_grid.plots[0].series_names()):
        win.plot_grid.plots[0].remove_series(name)
    assert win.export_image(str(tmp_path / "e.png")) is False
    assert [b[0] for b in boxes] == ["information"]
    assert not (tmp_path / "e.png").exists()
