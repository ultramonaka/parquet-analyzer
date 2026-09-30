"""Plot image export (specification.md 5.14, detailed_specification.md 19章)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from parquet_analyzer.ui.image_export import (
    EXPORT_FORMATS,
    PlotSnapshot,
    SeriesSnapshot,
    build_figure,
    export_format,
    export_plots_image,
    save_figure,
)
from parquet_analyzer.ui.main_window import MainWindow

X0 = 1_767_225_600.0  # epoch seconds, ~2026


def snap(y_scale=1.0, names=("s1",), colors=("#1f77b4", "#d62728"), datetime_axis=True, cursors=(), n=200, span=300.0):
    if datetime_axis:
        x = X0 + np.linspace(0, span, n)
        x_range = (X0, X0 + span)
    else:
        x = np.linspace(0, 50, n)
        x_range = (0.0, 50.0)
    series = [SeriesSnapshot(nm, colors[i], x, np.sin(x / 7 + i) * y_scale) for i, nm in enumerate(names)]
    return PlotSnapshot(datetime_axis, x_range, (-1.1 * y_scale, 1.1 * y_scale), series, list(cursors))


def test_export_format_follows_suffix_and_rejects_others():
    assert export_format("a.PNG") == "png"
    assert export_format("a.jpeg") == export_format("a.jpg") == "jpeg"
    assert export_format("a.tif") == "tiff"
    assert export_format("a.svg") == "svg"
    assert export_format("a.pdf") == "pdf"
    for bad in ("a.gif", "a"):
        with pytest.raises(ValueError):
            export_format(bad)
    assert ".svg" in EXPORT_FORMATS


def _drawn(fig):
    fig.canvas.draw()
    return fig


def test_time_subplots_share_x_and_align_edges_despite_tick_label_widths():
    fig = _drawn(build_figure([snap(0.001), snap(1e6), snap(1.0)], ["stamp"], (900, 700)))
    axes = fig.axes
    assert len(axes) == 3
    x0s = [ax.get_position().x0 for ax in axes]
    x1s = [ax.get_position().x1 for ax in axes]
    assert max(x0s) - min(x0s) < 1e-6 and max(x1s) - min(x1s) < 1e-6
    assert axes[1].get_shared_x_axes().joined(axes[0], axes[1])
    assert axes[0].get_xlim() == axes[2].get_xlim()


def test_x_tick_labels_only_on_bottom_time_subplot():
    fig = _drawn(build_figure([snap(), snap(), snap()], ["stamp"], (900, 700)))
    shown = [any(t.get_visible() and t.get_text() for t in ax.get_xticklabels()) for ax in fig.axes]
    assert shown == [False, False, True]


def test_legend_colors_limits_and_cursors():
    s = snap(names=("alpha", "beta"), cursors=(X0 + 100,))
    s.y_range = (-2.0, 3.0)
    fig = _drawn(build_figure([s], ["stamp"], (900, 500)))
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["alpha", "beta"]
    lines = [ln for ln in ax.get_lines() if ln.get_label() in ("alpha", "beta")]
    assert [ln.get_color() for ln in lines] == ["#1f77b4", "#d62728"]
    assert ax.get_ylim() == (-2.0, 3.0)
    vlines = [ln for ln in ax.get_lines() if ln.get_label() not in ("alpha", "beta")]
    assert len(vlines) == 1 and vlines[0].get_color() == "#ffaa00"
    xs = vlines[0].get_xdata()
    assert xs[0] == xs[1]


def test_time_axis_is_local_time():
    import matplotlib.dates as mdates

    fig = _drawn(build_figure([snap()], ["stamp"], (900, 500)))
    lo = mdates.num2date(fig.axes[0].get_xlim()[0]).replace(tzinfo=None)
    assert abs((lo - datetime.fromtimestamp(X0)).total_seconds()) < 1e-3


def test_fft_like_plot_is_not_shared_and_has_hz_label():
    fig = _drawn(build_figure([snap(), snap(datetime_axis=False)], ["stamp"], (900, 600)))
    t_ax, f_ax = fig.axes
    assert not t_ax.get_shared_x_axes().joined(t_ax, f_ax)
    assert f_ax.get_xlabel() == "Hz"
    assert f_ax.get_xlim() == (0.0, 50.0)
    # the time plot is the bottom of its own group -> still labelled
    assert any(t.get_text() for t in t_ax.get_xticklabels())


def test_stamp_is_bottom_right_and_overlaps_no_axes():
    fig = build_figure([snap(0.001), snap(1e6)], ["file.parquet", "Range: x", "Exported: y"], (900, 600))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    stamp = next(t for t in fig.texts if "file.parquet" in t.get_text())
    assert stamp.get_ha() == "right"
    sb = stamp.get_window_extent(renderer)
    fb = fig.bbox
    assert sb.x1 > fb.x0 + 0.9 * fb.width and sb.y0 < fb.y0 + 0.15 * fb.height
    for ax in fig.axes:
        assert not sb.overlaps(ax.get_tightbbox(renderer))


META = {"Title": "f.parquet", "Source": "/x/f.parquet", "Range": "a - b", "Software": "Parquet Analyzer vT"}


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".pdf"])
def test_every_format_writes_a_file(tmp_path, suffix):
    out = tmp_path / f"o{suffix}"
    export_plots_image([snap(), snap()], out, ["stamp"], META, (600, 400))
    assert out.stat().st_size > 0


def test_png_metadata_and_size(qapp, tmp_path):
    out = tmp_path / "o.png"
    export_plots_image([snap()], out, ["stamp"], META, (600, 400))
    image = QImage(str(out))
    assert image.text("Title") == "f.parquet" and image.text("Source") == "/x/f.parquet"
    assert (image.width(), image.height()) == (1200, 800)


def test_svg_and_pdf_metadata(tmp_path):
    svg, pdf = tmp_path / "o.svg", tmp_path / "o.pdf"
    export_plots_image([snap()], svg, ["stamp"], META, (600, 400))
    export_plots_image([snap()], pdf, ["stamp"], META, (600, 400))
    assert "f.parquet" in svg.read_text(encoding="utf-8")
    assert pdf.read_bytes().startswith(b"%PDF")


def test_jpeg_and_tiff_carry_metadata(tmp_path):
    from PIL import Image

    j, t = tmp_path / "o.jpg", tmp_path / "o.tif"
    export_plots_image([snap()], j, ["stamp"], META, (600, 400))
    export_plots_image([snap()], t, ["stamp"], META, (600, 400))
    assert b"Title: f.parquet" in Image.open(j).info["comment"]
    assert "Title: f.parquet" in Image.open(t).tag_v2[270]


def test_unsupported_suffix_raises(tmp_path):
    with pytest.raises(ValueError):
        export_plots_image([snap()], tmp_path / "o.gif", ["s"], META, (600, 400))
    with pytest.raises(ValueError):
        save_figure(build_figure([snap()], [], (600, 400)), tmp_path / "o.bmp", META)


def test_svg_curve_keeps_distinct_x_coordinates_for_epoch_seconds(tmp_path):
    """19.0: Qt's SVG writer collapsed epoch-second X (~1.7e9) onto one value."""
    out = tmp_path / "o.svg"
    export_plots_image([snap(n=500)], out, ["stamp"], META, (900, 500))
    text = out.read_text(encoding="utf-8")
    paths = re.findall(r'<path d="([^"]+)"[^>]*style="[^"]*stroke: #1f77b4', text)
    assert paths
    xs = {round(float(m.group(1)), 1) for m in re.finditer(r"[ML]\s*([-\d.]+)[ ,]", max(paths, key=len).replace("\n", " "))}
    assert len(xs) > 50


# -- Japanese text (19.2) ---------------------------------------------------------------


def _ja_snap():
    s = snap(names=("温度センサー",))
    return s


@pytest.mark.parametrize("suffix", [".png", ".pdf", ".svg", ".jpg", ".tif"])
def test_japanese_text_has_no_missing_glyphs(tmp_path, suffix):
    import warnings

    from parquet_analyzer.ui import image_export

    if not image_export._cjk_fonts():
        pytest.skip("no CJK font installed")
    meta = {**META, "View": "試験ビュー"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        export_plots_image([_ja_snap()], tmp_path / f"o{suffix}", ["ファイル  |  ビュー: 試験ビュー", "表示範囲: x"], meta, (600, 400))
    missing = [str(w.message) for w in caught if "missing from font" in str(w.message)]
    assert not missing, missing[:2]


def test_font_family_list_only_contains_installed_families(monkeypatch, caplog, tmp_path):
    import matplotlib

    from parquet_analyzer.ui import image_export

    real = image_export._cjk_fonts() or None
    monkeypatch.setattr(image_export, "_cjk_font_cache", None)
    monkeypatch.setattr(image_export, "_CJK_FONT_CANDIDATES", ["No Such Font XYZ", *(real or [])])
    fonts = image_export._cjk_fonts()
    assert "No Such Font XYZ" not in fonts
    with caplog.at_level("WARNING"):
        export_plots_image([snap()], tmp_path / "o.png", ["s"], META, (600, 400))
    assert not [r for r in caplog.records if "not found" in r.getMessage()]
    with image_export._font_context():
        assert "No Such Font XYZ" not in matplotlib.rcParams["font.family"]


def test_export_works_without_any_cjk_font(monkeypatch, caplog, tmp_path):
    from parquet_analyzer.ui import image_export

    monkeypatch.setattr(image_export, "_cjk_font_cache", None)
    monkeypatch.setattr(image_export, "_CJK_FONT_CANDIDATES", ["No Such Font XYZ"])
    with caplog.at_level("WARNING"):
        export_plots_image([snap()], tmp_path / "o.png", ["s"], META, (600, 400))
    assert (tmp_path / "o.png").stat().st_size > 0
    assert any("CJK" in r.getMessage() for r in caplog.records)


# -- MainWindow ------------------------------------------------------------------------


def _wait_until_drawn(win, qapp, timeout_s: float = 10.0) -> None:
    """export_image_dialog refuses while a downsample job is in flight (19.2)."""
    import time

    deadline = time.monotonic() + timeout_s
    while win._busy_sources and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not win._busy_sources


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path):
    w = MainWindow()
    w.resize(800, 600)
    w.load_parquet(str(sample_parquet_path))
    w._add_variable_to_plot(w.plot_grid.plots[0], "a")
    _wait_until_drawn(w, qapp)
    yield w
    w.close()


def test_stamp_has_file_range_export_time_and_version(win, sample_parquet_path):
    from datetime import datetime

    from parquet_analyzer import __version__

    lines, meta = win._image_stamp(datetime(2026, 9, 30, 12, 34, 56))
    assert lines[0] == sample_parquet_path.name
    assert re.match(r"^Range: \d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} – \d{4}-", lines[1])
    assert lines[2] == f"Exported: 2026-09-30 12:34:56  |  Parquet Analyzer v{__version__}"
    assert meta["Source"] == str(sample_parquet_path) and meta["CreationTime"] == "2026-09-30T12:34:56"


def test_stamp_includes_view_name_until_a_plain_file_open(win, monkeypatch, sample_parquet_path):
    from datetime import datetime

    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("myview", True)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    win.save_view_dialog()
    assert win._image_stamp(datetime.now())[0][0].endswith("|  View: myview")

    win.load_parquet(str(sample_parquet_path))
    assert "View" not in win._image_stamp(datetime.now())[1]

    win.load_view("myview")
    assert win._image_stamp(datetime.now())[1]["View"] == "myview"


def test_export_image_writes_file_and_remembers_folder(win, tmp_path):
    out = tmp_path / "exports" / "shot.png"
    out.parent.mkdir()
    assert win.export_image(str(out))
    assert QImage(str(out)).text("Title") == Path(win._data_source.path).name
    assert win.settings.last_export_folder == str(out.parent.resolve())


def test_export_two_stacked_plots_end_to_end(win, qapp, tmp_path):
    win.plot_grid.add_plot()
    win._add_variable_to_plot(win.plot_grid.plots[1], "a")
    _wait_until_drawn(win, qapp)
    out = tmp_path / "two.png"
    assert win.export_image(str(out))
    image = QImage(str(out))
    assert image.width() > 0 and image.height() > 0
    assert image.text("Source") == str(win._data_source.path)


def test_export_with_nothing_plotted_is_refused(win, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    for p in win.plot_grid.plots:
        for name in list(p._series):
            p.remove_series(name)
    out = tmp_path / "none.png"
    assert not win.export_image(str(out))
    assert shown and not out.exists()


def test_dialog_appends_selected_filter_extension_and_uses_default_name(win, tmp_path, monkeypatch):
    seen = {}

    def fake_dialog(parent, title, default, filters):
        seen["default"], seen["filters"] = default, filters.split(";;")
        return str(tmp_path / "noext"), seen["filters"][1]  # JPEG filter selected

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    win.export_image_dialog()
    stem = Path(win._data_source.path).stem
    assert re.search(rf"{stem}_\d{{8}}_\d{{6}}\.png$", seen["default"])
    assert (tmp_path / "noext.jpg").exists()


@pytest.mark.parametrize("state", ["no_file", "busy"])
def test_dialog_refuses_without_file_or_while_drawing(qapp, isolated_config, sample_parquet_path, monkeypatch, state):
    shown, dialogs = [], []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: dialogs.append(1) or ("", "")))
    w = MainWindow()
    if state == "busy":
        w.load_parquet(str(sample_parquet_path))
        w._set_busy("plot_grid", True)
    w.export_image_dialog()
    assert shown and not dialogs
    w.close()
