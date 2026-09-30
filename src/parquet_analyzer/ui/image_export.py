"""Plot image export via matplotlib (detailed_specification.md 19.2).

The plots are re-drawn from `PlotSnapshot`s (pure data captured from the on-screen
`TimePlotWidget`s) rather than screen-captured, so stacked axes line up, legends are
proper, and SVG works. matplotlib is imported lazily inside the functions so app
startup doesn't pay for it, and `pyplot` is never imported (no global state, no GUI
backend interfering with the running Qt app).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import logging
import numpy as np

logger = logging.getLogger(__name__)

# suffix -> format id (detailed_specification.md 19.2)
EXPORT_FORMATS: dict[str, str] = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".svg": "svg",
    ".pdf": "pdf",
}

# Preferred CJK fonts, first installed wins (detailed_specification.md 19.2, "Japanese text").
_CJK_FONT_CANDIDATES = [
    "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Yu Gothic", "Meiryo", "MS Gothic",
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic", "TakaoGothic",
    "Arial Unicode MS",
]
_cjk_font_cache: list[str] | None = None

_CURSOR_COLOR = "#ffaa00"  # same as plot_widget._CURSOR_COLOR
_FIG_DPI = 100  # figure is sized at 100 dpi ...
_RASTER_DPI = 200  # ... and rasterized at 2x
_JPEG_QUALITY = 95


@dataclass
class SeriesSnapshot:
    name: str
    color: str
    x: np.ndarray
    y: np.ndarray


@dataclass
class PlotSnapshot:
    x_axis_datetime: bool
    x_range: tuple[float, float]
    y_range: tuple[float, float]
    series: list[SeriesSnapshot] = field(default_factory=list)
    cursors: list[float] = field(default_factory=list)


def export_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in EXPORT_FORMATS:
        raise ValueError(f"unsupported image format: {suffix or '(none)'}")
    return EXPORT_FORMATS[suffix]


def _to_local_datetime64(x: np.ndarray, offset_s: float) -> np.ndarray:
    """Epoch seconds -> naive local-time datetime64[us] (fixed UTC offset)."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full(x.shape, np.datetime64("NaT", "us"), dtype="datetime64[us]")
    ok = np.isfinite(x)
    out[ok] = np.round((x[ok] + offset_s) * 1e6).astype(np.int64).astype("datetime64[us]")
    return out


def _local_offset_s(x0: float) -> float:
    """Local UTC offset (seconds) at epoch second `x0`."""
    try:
        return (datetime.fromtimestamp(x0) - datetime.fromtimestamp(x0, timezone.utc).replace(tzinfo=None)).total_seconds()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _cjk_fonts() -> list[str]:
    """Installed CJK family to fall back to for missing glyphs ([] if none); detected once."""
    global _cjk_font_cache
    if _cjk_font_cache is None:
        from matplotlib import font_manager

        installed = {f.name for f in font_manager.fontManager.ttflist}
        found = [name for name in _CJK_FONT_CANDIDATES if name in installed]
        _cjk_font_cache = found[:1]
        if not found:
            logger.warning("no CJK font found for image export; Japanese text may not render")
    return _cjk_font_cache


def _font_context():
    """rc_context with DejaVu Sans plus a per-glyph CJK fallback (19.2). Must cover both
    figure construction and savefig."""
    import matplotlib

    return matplotlib.rc_context({"font.family": ["DejaVu Sans", *_cjk_fonts()]})


def build_figure(snapshots: Sequence[PlotSnapshot], stamp_lines: Sequence[str], size_px: tuple[int, int]):
    """Build the matplotlib Figure for `snapshots` (stacked vertically)."""
    with _font_context():
        return _build_figure(snapshots, stamp_lines, size_px)


def _build_figure(snapshots, stamp_lines, size_px):
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    width, height = max(int(size_px[0]), 200), max(int(size_px[1]), 150)
    fig = Figure(figsize=(width / _FIG_DPI, height / _FIG_DPI), dpi=_FIG_DPI, facecolor="white", layout="constrained")
    FigureCanvasAgg(fig)

    # Reserve room at the bottom for the stamp so it never overlaps axes/tick labels.
    n_stamp = max(len(stamp_lines), 1)
    stamp_h = (n_stamp * 7 * 1.25 + 4) / 72 / (height / _FIG_DPI)
    fig.get_layout_engine().set(rect=(0, stamp_h, 1, 1 - stamp_h))

    n = len(snapshots)
    axes = fig.subplots(n, 1, squeeze=False)[:, 0] if n else []
    time_axes = []
    first_time_ax = None
    for ax, snap in zip(axes, snapshots):
        if snap.x_axis_datetime:
            if first_time_ax is None:
                first_time_ax = ax
            else:
                ax.sharex(first_time_ax)
            time_axes.append(ax)

    for ax, snap in zip(axes, snapshots):
        offset = _local_offset_s(snap.x_range[0]) if snap.x_axis_datetime else 0.0
        for s in snap.series:
            x = _to_local_datetime64(s.x, offset) if snap.x_axis_datetime else np.asarray(s.x, dtype=float)
            ax.plot(x, np.asarray(s.y, dtype=float), color=s.color, linewidth=0.8, label=s.name)
        for cx in snap.cursors:
            xv = _to_local_datetime64(np.array([cx]), offset)[0] if snap.x_axis_datetime else cx
            ax.axvline(xv, color=_CURSOR_COLOR, linestyle="--", linewidth=1)
        if snap.x_axis_datetime:
            lo, hi = _to_local_datetime64(np.array(snap.x_range, dtype=float), offset)
            ax.set_xlim(lo, hi)
        else:
            ax.set_xlim(*snap.x_range)
            ax.set_xlabel("Hz")
        ax.set_ylim(*snap.y_range)
        ax.grid(True, alpha=0.3)
        if snap.series:
            ax.legend(loc="upper right", fontsize=8, framealpha=0.7)

    if time_axes:
        locator = mdates.AutoDateLocator()
        time_axes[0].xaxis.set_major_locator(locator)
        time_axes[0].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        for ax in time_axes[:-1]:
            ax.tick_params(axis="x", labelbottom=False)
            ax.xaxis.get_offset_text().set_visible(False)

    if stamp_lines:
        fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
    return fig


def save_figure(fig, path: str | Path, metadata: Mapping[str, str]) -> None:
    """Write `fig` to `path` (format from its suffix). ValueError for an unsupported
    suffix, OSError if the file doesn't exist afterwards."""
    fmt = export_format(path)
    path = str(path)
    with _font_context():
        _save_figure(fig, path, fmt, metadata)
    if not Path(path).exists():
        raise OSError(f"failed to write {path}")


def _save_figure(fig, path: str, fmt: str, metadata: Mapping[str, str]) -> None:
    text = "\n".join(f"{k}: {v}" for k, v in metadata.items())
    software = metadata.get("Software", "")
    if fmt == "png":
        fig.savefig(path, format="png", dpi=_RASTER_DPI, metadata=dict(metadata))
    elif fmt == "jpeg":
        _save_pillow(fig, path, "JPEG", comment=text)
    elif fmt == "tiff":
        _save_pillow(fig, path, "TIFF", description=text)
    elif fmt == "svg":
        fig.savefig(path, format="svg", metadata={"Title": metadata.get("Title", ""), "Description": text, "Creator": software})
    else:  # pdf
        fig.savefig(
            path,
            format="pdf",
            metadata={
                "Title": metadata.get("Title", ""),
                "Creator": software,
                "Subject": metadata.get("Range", ""),
                "Keywords": metadata.get("Source", ""),
            },
        )


def _save_pillow(fig, path: str, pil_format: str, comment: str = "", description: str = "") -> None:
    import io

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_RASTER_DPI)
    buf.seek(0)
    from PIL import Image

    img = Image.open(buf).convert("RGB")
    if pil_format == "JPEG":
        img.save(path, "JPEG", quality=_JPEG_QUALITY, comment=comment.encode("utf-8"))
    else:
        from PIL import TiffImagePlugin

        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[270] = description  # ImageDescription
        img.save(path, "TIFF", tiffinfo=ifd)


def export_plots_image(
    snapshots: Sequence[PlotSnapshot],
    path: str | Path,
    stamp_lines: Sequence[str],
    metadata: Mapping[str, str],
    size_px: tuple[int, int],
) -> None:
    export_format(path)  # fail fast on a bad suffix
    fig = build_figure(snapshots, stamp_lines, size_px)
    save_figure(fig, path, metadata)
