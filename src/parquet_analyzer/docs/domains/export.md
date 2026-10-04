# Export (saving images)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: saving the plots as an image (re-drawn with matplotlib, formats,
annotation and metadata). The analysis windows' image export uses the same mechanism.

## Requirements

#### 5.14 Saving the plots as an image (added 2026-09-30, user request; approach changed the same day)
- "Save image" on the toolbar saves **the plots currently shown** to an image file. It is not a
  screen capture but a figure **re-drawn with matplotlib** that looks right for papers and
  documents. Plots are stacked vertically in the same order as on screen. The navigator,
  variable list and toolbar are not included.
- Each plot's content is the same as on screen: the same series, the same colors, the same X
  range (shared by all time-domain plots) and Y range, and the cursors if cursor mode is on. The
  data drawn is also the same (downsampled) data as on screen.
- **Aligned X axes**: all time-domain plots share one X axis, and the left and right edges of the
  plot areas line up exactly. Date-time tick labels go only on the bottom plot (on screen the plot
  areas can be offset by the different widths of the Y tick labels; in the image they are not).
- **A legend in each plot** shows that plot's series names.
- Added 2026-10-02:
  - Hidden series ([plots 5.17](./plots.md)) are not drawn.
  - Each plot's height follows its on-screen height ratio ([plots 5.15](./plots.md)).
  - Plots whose X axis isn't linked ([plots 5.18](./plots.md)) are not part of the shared X axis
    and have their own X axis and time labels. The time-label rule is the same as
    [plots 5.16](./plots.md).
- Formats: **PNG** (default), **JPEG**, **TIFF** (raster; twice the on-screen pixel size),
  **SVG**, **PDF** (vector). The format is decided by the file extension.
- **Annotation**: at the bottom right of the image, below the plots (not overlapping them):
  1. The source Parquet file name (plus the view name, if the current layout was loaded from a
     view or saved as one)
  2. The visible time range (start – end)
  3. The export date/time and the app version
  The same information is also embedded in the file's own metadata where the format supports it.
- The default file name is `<Parquet file name>_<export date-time>.png`. The default folder is the
  one last saved to (the first time, the Parquet file's folder).
- Available only when a file is open and at least one plot has data. While plots are being
  redrawn, the user is told to try again after drawing finishes, so a half-drawn state is not
  saved.
- Details in [detailed_specification ch. 19](#19-saving-the-plots-as-an-image-added-2026-09-30-user-request).

## Design

### 19. Saving the plots as an image (added 2026-09-30, user request)

Requirement: `specification.ja.md` 5.14. (2026-10-02) Hidden series are not drawn (23.1), heights
follow the on-screen ratio, and unlinked plots are not in `sharex` but have their own X axis and
tick labels (24.6).

#### 19.0 Background: screen capture → matplotlib (changed the same day)

The first implementation copied the plot area as drawn on screen (`QWidget.render` into a
`QImage`/`QPdfWriter`). After the user's check it was withdrawn for these reasons: (1) the X axes
of stacked plots don't line up — each pyqtgraph plot sizes its Y axis to its own tick-label width,
so the plot areas start at different X positions; (2) pyqtgraph's on-screen legend (line and text
overlap, no frame) is hard to read in a saved figure; (3) SVG can't be produced correctly — Qt's
`QSvgGenerator` writes coordinates with only 6 significant digits, so on an epoch-second time
axis (about 1.7e9) every curve collapses into one vertical line. pyqtgraph's own `SVGExporter`
raises on PySide6 6.11's path syntax (pyqtgraph 0.14.0). Re-drawing with matplotlib solves all
three (a shared X axis aligns the axes, proper legends, and the SVG backend writes device
coordinates).

#### 19.1 Snapshot of the on-screen content (`ui/plot_widget.py`)

- `TimePlotWidget.export_snapshot() -> PlotSnapshot` (a dataclass in `ui/image_export.py`; pure
  data with no Qt objects): `x_axis_datetime: bool`, `x_range: (lo, hi)`, `y_range: (lo, hi)`
  (the current `viewRange()`), `series: list[SeriesSnapshot(name, color, x, y)]` (in display
  order; `x`/`y` are copies of each series' `curve.getData()` — the same downsampled /
  pyramid-envelope data as on screen, or empty arrays if nothing has been drawn yet),
  `cursors: list[float]` (the X of each cursor line, only while the cursor lines are shown, i.e.
  cursor mode is on).
- `MainWindow` collects `export_snapshot()` of every plot in `plot_grid.plots` order and drops
  plots with no series. If none remain it shows `export.nothing_to_export` and doesn't save.
  (2026-10-02) Hidden series are not in `export_snapshot()` (23.1), so a plot whose series are all
  hidden is also left out of the image as a "plot with no series".

#### 19.2 Drawing (`ui/image_export.py`)

- Split into `build_figure(snapshots, stamp_lines, size_px) -> matplotlib.figure.Figure` and
  `save_figure(fig, path, metadata)` so tests can inspect the figure without writing a file.
  `export_plots_image(snapshots, path, stamp_lines, metadata, size_px)` calls both.
  `matplotlib.figure.Figure` and the Agg canvas are used directly, never `pyplot` (no global
  state, and the GUI backend doesn't interfere with the running Qt app). `matplotlib` is imported
  lazily inside these functions so it doesn't affect startup time.
- Figure: size is `size_px` (the size of the `plot_grid` widget) / 100 inches, white background.
  One subplot per snapshot, stacked vertically with equal heights. Time-domain subplots share the
  X axis (`sharex`). Non-time subplots (FFT, X in Hz) have their own X axis labeled `Hz`. The
  layout engine is `constrained`, which aligns the left edges of all subplot areas regardless of
  the Y tick-label widths. X tick labels are shown only on the bottom subplot of the shared group.
- Each subplot: every series is drawn as a line in the same color as on screen (line width 0.8),
  the X/Y ranges are the snapshot's ranges, a light grid (alpha 0.3), a legend of the series names
  (`loc="upper right"`, semi-transparent frame, font size 8), and cursors as dashed vertical lines
  in the same cursor color as on screen (`#ffaa00`).
- Time X axis: the values are epoch seconds and are shown in **local time** as on screen
  (`DateAxisItem` and the cursor labels use `datetime.fromtimestamp`). They are converted to naive
  local `datetime64` with the local UTC offset at the start of the X range and formatted with
  `matplotlib.dates.ConciseDateFormatter` (the granularity changes with zoom, like the on-screen
  axis). If daylight saving time changes within the range, the later part is off by that
  difference — accepted, and stated here.
- Annotation: `stamp_lines` joined with newlines, drawn right-aligned at the bottom right of the
  figure (font size 7, gray). The constrained layout's `rect` reserves enough height at the bottom
  so it never overlaps any axis or tick label.
- `save_figure`: the format is decided by the path's extension (`EXPORT_FORMATS`: `.png`,
  `.jpg`/`.jpeg`, `.tif`/`.tiff`, `.svg`, `.pdf`; anything else is a `ValueError`). Raster formats
  use `dpi=200` (the figure is made at 100 dpi, so twice the on-screen pixel count). JPEG quality
  is 95. Metadata: PNG — every key in `tEXt` chunks; PDF — `Title`, `Creator`, `Subject` (range),
  `Keywords` (source path); SVG — `Title`, `Description` (every key as `key: value` lines),
  `Creator`; TIFF — the ImageDescription tag (every key as `key: value` lines); JPEG — the same
  text as a JPEG comment. If the file doesn't exist after saving, `OSError`.
- **Japanese characters** (found during the check): matplotlib's default font (DejaVu Sans) has no
  CJK glyphs, so Japanese series names, file/view names and the annotation labels in the `ja`
  setting came out as empty boxes ("tofu"). The figure is built inside
  `matplotlib.rc_context({"font.family": ["DejaVu Sans", <cjk>]})` and uses matplotlib's per-glyph
  font fallback (Latin characters stay in DejaVu Sans; only missing glyphs come from `<cjk>`).
  `<cjk>` is the first of `_CJK_FONT_CANDIDATES` that is actually installed (matched against the
  names in `matplotlib.font_manager.fontManager.ttflist`; checked once and cached): `Hiragino
  Sans`, `Hiragino Kaku Gothic ProN` (macOS), `Yu Gothic`, `Meiryo`, `MS Gothic` (Windows), `Noto
  Sans CJK JP`, `Noto Sans JP`, `IPAexGothic`, `IPAGothic`, `TakaoGothic` (Linux), `Arial Unicode
  MS`. Only installed fonts go in the list (a font name that doesn't exist makes matplotlib warn
  "font family not found" on every draw). If none is found, saving continues with DejaVu Sans only
  (one `logger.warning`). Text is laid out at draw time, so the rc_context covers not only
  building the figure but also `savefig`. Verified on macOS: Hiragino Sans draws Japanese without
  missing glyphs in PNG, PDF and SVG. Fonts on Windows/Linux are not verified here.
- Dependency: `matplotlib` is added to the main dependencies (image export is a core feature). The
  PyInstaller distribution grows.

#### 19.3 `MainWindow.export_image_dialog()` / `export_image(path)`

- The toolbar action `export_image` (shortcut configurable, unbound by default; see ch. 9).
- No file open → `error.open_file_first`. `plot_grid` busy drawing (a downsampling job running,
  `"plot_grid" in _busy_sources`) → show `export.busy` and don't save.
- `QFileDialog.getSaveFileName` with a filter per format. Default:
  `<settings.last_export_folder or the Parquet file's folder>/<parquet stem>_<YYYYmmdd_HHMMSS>.png`.
  If the chosen name has no matching extension, the selected filter's extension is added. On a
  successful save the folder is recorded in `settings.last_export_folder` and saved.
- Annotation lines (`_image_stamp()`):
  1. `<file name>` or `<file name>  |  View: <view name>`. `MainWindow._view_name` is set by
     `load_view` and `save_view_dialog` and cleared by `load_parquet` (once a file is opened
     directly, the layout no longer corresponds to any saved view).
  2. `Range: <start> – <end>` — the X range shared by the time-domain plots, in the same format as
     the cursor labels (`%Y-%m-%d %H:%M:%S.mmm`, `TimePlotWidget.format_x`). Omitted if no
     time-domain plot has data.
  3. `Exported: <YYYY-MM-DD HH:MM:SS>  |  Parquet Analyzer v<version>`.
  The labels go through `tr()` (the language setting applies).
- Embedded metadata keys: `Title` (file name), `Source` (full path of the Parquet file), `View`
  (if any), `Range`, `CreationTime` (ISO 8601), `Software`.
- Write failure (`OSError`/`ValueError`) → `export.failed` as a warning. On success the path is
  shown in the status bar (not a modal dialog: it is a frequent action and the dialog would only
  ever be closed).
- Operation log: `export_image` (path, format).

## Relations to other domains

- **Plots** ([plots.md](./plots.md)): what `export_snapshot()` contains (hidden series excluded,
  height ratios, X-axis linking).
- **Analysis** ([analysis.md](./analysis.md)): the analysis windows share the save routine.
- **Persistence** ([persistence.md](./persistence.md)): `last_export_folder`, the view name (shown
  in the annotation).
- **Analysis** (ch. 25, 2026-10-03): the analysis windows' image export uses
  `_image_stamp(range_override=...)` to annotate the range the window computed.
