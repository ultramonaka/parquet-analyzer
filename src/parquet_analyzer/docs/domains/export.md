# Export (saving images)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: saving the plots as an image (re-drawn with matplotlib, formats,
annotation and metadata). The analysis windows' image export uses the same mechanism.
(2026-10-15) Also: writing the view's signals to Parquet (ch. 57).

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

#### 5.51 Writing the view's signals to Parquet (added 2026-10-15, user request)
- Computes the current view (the signals shown in the plots) for the open file and writes the
  result to a Parquet file.
- The range written can be chosen: **all rows** or **the visible range**.
- The columns written are chosen from **the signals currently shown in the plots** (both original
  columns and virtual variables; all by default). The time-axis column is always put first.
- **Virtual variables not needed by the chosen columns are not computed** (only what is used).
- The default file name is `<Parquet file name>_<export date-time>.parquet`; the default folder is
  the one last saved to (shared with image export).
- Writing runs in the background and shows a **progress bar with a percentage (%)** and a
  **cancel** button. Other operations remain possible while it runs.
- Information such as the source file name, the view name and the virtual-variable formulas is
  stored as metadata in the written file.
- Computation is heavy, so it is computed and written in chunks and memory does not grow with the
  row count. Ways to make it faster (multi-core) are also considered (together with the
  measurements in ch. 58).
- **A saved view can be exported without opening it** (stage 2, added 2026-10-15, user request).
  For large data, merely opening a view (computing and drawing virtual variables) takes a long
  time. It is chosen from the view manager and from the export screen. The source file defaults to
  the view's file, another file can be chosen, and signals converted by signal aliases
  (persistence ch. 54) are covered. The list is the plots' signals (checked by default) plus the
  view's other virtual variables (unchecked by default). The range is all rows or the range saved
  in the view. Details in 57.7.
- Details in [ch. 57](#57-writing-the-views-signals-to-parquet-added-2026-10-15).

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

### 57. Writing the view's signals to Parquet (added 2026-10-15)

Requirement: 5.51.

#### 57.1 Entry point and settings screen (`ui/parquet_export_dialog.py`, new)

- Toolbar/menu action `export_parquet` ("Export to Parquet...", shortcut configurable, unbound by
  default), next to image export. The feature flag `parquet_export` (default OFF; turned ON after
  review and a real-machine check; app ch. 21) hides the entry point.
- No file open → `error.open_file_first`. No signal in any plot → `export.nothing_to_export`.
  Pressing it again while an export is running → tell the user "an export is running" and do
  nothing (only one at a time).
- Settings screen (a small modal dialog; tests monkeypatch it / replace `exec`):
  - Range: "All rows" (default) / "Visible range" (the rows of the linked group's range,
    `_visible_row_range`). The row counts are shown next to both.
  - Columns: a checklist of the signals currently shown in the plots (plot order, then order within
    the plot; duplicates merged; hidden series included). All checked by default; "All" and "None"
    buttons. The time-axis column is not listed; a note says it is always included.
  - "Export" opens `QFileDialog.getSaveFileName` (filter `*.parquet`; default:
    `<settings.last_export_folder or the Parquet file's folder>/<stem>_<YYYYmmdd_HHMMSS>.parquet`;
    `.parquet` is added if there is no extension).
- Operation log: `export_parquet` (path, range, column count, row count), `export_parquet_cancel`.

#### 57.2 Computation and writing (`core/parquet_export.py`, new; no Qt dependency)

- `export_parquet(plan, path, progress, cancelled) -> ExportResult`. `plan` is an immutable copy
  made on the UI thread: the data source (path), the row range `[start, end)`, the order of output
  columns (time axis first), how each column is made (original column: the column name / virtual
  variable: its formula plus the virtual variables the formula uses, in dependency order), a copy
  of functions and maps (`_registry_snapshot()`), and the metadata.
- **Only what is used**: only the chosen virtual variables and the virtual variables they
  (transitively) use are computed (using the dependency information in `core/variable.py`). Other
  virtual variables and original columns are not read.
- **Processed in chunks**: for each `chunk_rows` rows (default 1 million), the original columns are
  read for that range only (plus the expression margin), the virtual variables are computed for that
  range, and the result is written as one row group with `pyarrow.parquet.ParquetWriter`. Only
  variables whose formulas have an unknown expression margin (`expression_margin`, data 36.8 C —
  functions with no margin definition, windows not determined by a number, etc.) are **computed over
  all rows first** and then written in chunks (this uses memory; their names are recorded in
  `ExportResult.full_computed` and shown in the completion message). The result must equal the
  value computed over all rows at once.
- Column types: the time axis and original columns keep **their original type** (read from the
  data source without changing the type; time zones are kept). Virtual variables are `float64`.
  When displayed sorted (56.4), `source_row` (`int64`, 1-based) is added after the time axis.
- Compression is zstd. Writing goes to `<path>.part` and is replaced into `<path>` (`os.replace`)
  after everything is done. On cancel or failure `.part` is deleted.
- Progress: `progress(done_rows, total_rows)` is called per chunk. If `cancelled()` is `True`, it
  stops at a chunk boundary, deletes `.part` and raises `ExportCancelled`.
- **Metadata** (a Parquet file key-value entry, key `parquet_analyzer`, JSON): `source` (full
  source path), `source_name`, `view` (if any), `time_column`, `range` (`rows: [start, end]` and
  start/end time strings), `derived` (`{name: formula}` of the written and used virtual variables),
  `functions` / `maps` (only the names used), `created` (ISO 8601 with offset), `software` (app name
  and version), `source_row` (its description, when added).

#### 57.3 Background processing and screen (`MainWindow`)

- `export_parquet` runs as one `WorkerHub` job (exactly one of success / cancel / failure, ch. 37).
  Progress is sent to the UI thread per chunk (a progress signal separate from the `WorkerHub`
  result, via the relay; the UI thread only sets numbers).
- At the bottom right of the status bar, only while exporting: a progress bar (0-100%, definite
  value; while computing over all rows first, a motion-only bar and "Computing all rows..."),
  "Exporting to Parquet NN%", and a cancel button (the same form as the view-load progress (55.4);
  separate widgets so both can be shown at once).
- **Other operations are possible while exporting** (`plan` is a copy, so editing virtual variables
  or changing functions does not affect the export). The export runs on the copy taken at the
  start, so **opening another file or view does not stop it** (2026-10-15, user decision). Only the
  cancel button and closing the window stop it. The progress widgets stay in the status bar across
  file/view switches, and the completion notice and the `last_export_folder` recording also happen
  after the switch. For large files, note that the export and the newly opened file compete for
  memory.
- When finished: success → the status bar shows the path, row count and time (and the names of
  variables computed over all rows, if any). The destination folder is recorded in
  `last_export_folder`. Cancel → "Export canceled". Failure (`OSError` etc.) → a warning in the same
  form as `export.failed`.
- `_set_busy("parquet_export", ...)` is not used (so the "drawing" indicator and the operation log's
  wait for the result are not extended until the export finishes).

#### 57.4 Speed (stage 1 scope)

- Stage 1 does "read → compute → write" in order inside one job (polars reading and much of the
  numpy computation use multiple cores themselves / release the GIL).
- The measurements in ch. 58 measure export time and CPU usage; if it is slow, stage 2 decides how
  to overlap read, compute and write (pipelining chunks).

#### 57.5 Test plan

- `core/parquet_export` (no Qt): for all rows and a partial range, original columns keep their
  original type (integer, float32, time with time zone) / virtual-variable values equal the values
  computed over all rows at once (`delta`, `rolling_mean`, conditionals, user functions, maps,
  windows spanning chunk boundaries, with a small `chunk_rows`) / expressions with unknown margin
  are computed over all rows first, match, and appear in `full_computed` / unselected virtual
  variables are not computed (count the evaluations) / a chain of used virtual variables is
  computed / number of row groups = number of chunks / metadata contents / canceling removes `.part`
  and creates no output file (an existing file of the same name is not deleted) / a write failure
  leaves no `.part` / progress increases monotonically and ends at 100% / nanosecond time axis
  (`timestamp[ns]`, with and without time zone, sub-microsecond values) keeps its type and the
  metadata range strings are produced / canceling during the all-rows computation stops at the next
  block and leaves no `.part` / an output path that is the source file (same file, hard link, path
  containing `..`) is rejected before starting.
- Screen (`MainWindow`, dialogs replaced): entry enabled/disabled (no file, no signals, flag OFF) /
  list contents (plot order, no duplicates, hidden series included, time axis not listed) / the row
  counts match the range choice / runs in the background without freezing the UI / progress is shown
  and disappears when done / cancel / opening another file or view during an export does not stop
  it and it finishes correctly with the original file's data (the progress widgets remain and the
  completion notice appears) / closing the window cancels and leaves no `.part` / editing a virtual
  variable during an export leaves the exported values as the formula at the start / recording of
  `last_export_folder` / operation log.
- Add `export_parquet` and `export_parquet_cancel` to the list in the operation side-effect tests
  (app ch. 45).

#### 57.6 Decisions made during implementation and differences from the spec (2026-10-15, implementer)

- **`source_row` (56.4) is not implemented** (56.4 is not on `main`). The structure is such that
  adding one column to the column order of `_Run.chunk_table` in `core/parquet_export.py` suffices
  (`ExportPlan` carries the row-number source, and `chunk_table` adds an `int64` column after the
  time axis). The `source_row` description in the metadata is added at the same time.
- **The plan is built by `build_plan` (no Qt)**. The screen only passes copies (formulas,
  `_registry_snapshot()` of functions and maps, the name → original column mapping, the row range);
  collecting dependencies (the chosen virtual variables plus transitive dependencies,
  `dependency_order`) and finding the original columns used by formulas are in `core`. Aliases
  (persistence 54) are resolved via the "name → original column" mapping; the output column name
  stays the screen name (alias), and the content is the original column. The time-axis column name
  is also the screen name.
- **Splitting margins and all-rows computation**: for each virtual variable, in dependency order,
  the margin is found with `expression_margin` (original columns are (0,0), a virtual variable has
  its own margin). A variable whose margin is `None`, the variables that (transitively) depend on
  it, and the variables it uses are **computed over all rows first** (all of them go in
  `full_computed`). For the rest, each chunk reads the original columns for the range widened by "the
  maximum margin over all variables", computes each once in dependency order, and writes only the
  chunk's rows (the same rule as `DerivedColumn.window`; range ends are clipped at the file ends, so
  it equals the all-rows computation). For a chunk variable that uses an all-rows-computed variable,
  the same range is sliced from that variable's all-rows values. Example: a function whose window
  is given by a user-function constant rather than a literal (`rolling_mean(x, w)`) has an unknown
  margin and is computed over all rows.
- **The all-rows computation uses all the file's rows regardless of the range** (even when only the
  visible range is exported; so the result equals the all-rows computation even for expressions
  where values outside the range affect the result). It reads in blocks of `chunk_rows` rows and
  checks for cancel per block and per variable (cancelable even if it takes minutes). During this,
  `progress(0, -1)` (`total < 0`) is emitted once and the status bar shows a motion-only bar and
  "Computing all rows...". The chunk progress (0-100%) starts after the all-rows computation.
- **How the original type is kept**: the time axis and chosen original columns, read per chunk with
  polars, are `cast` to the type in the original file's Arrow schema (`pq.read_schema`) and written
  (polars turns strings into `large_string`, etc.). Columns whose type cannot be changed keep the
  type as read. The first chunk's type is the output schema; later chunks are `cast` to it.
- **Export-only reads**: the numeric columns used for computation are read one column at a time
  with a dedicated polars `scan_parquet`, not `ParquetDataSource` (the app's data source and its
  block cache are not touched; the "data source rebuilt" watcher in app ch. 45 does not trigger).
- **Progress signal**: `WorkerHub` gained `progress_sink(callback)` / `drop_progress(key)`, and
  `_Relay` gained a `progress` signal (weak reference to the hub, key, two ints). It goes through the
  relay, so the worker never touches widgets. Progress that arrives after `drop_progress` is
  discarded.
- **Thread**: a dedicated `QThreadPool` (at most 1 thread; the `WorkerHub` `target` is the output
  path), so analysis jobs are not made to wait. The "exporting" state is held by
  `MainWindow._parquet_export` (the running job: destination of the widgets, cancel and result
  intake) and a list of canceled jobs whose workers are still cleaning up. **The next export cannot
  start until the cleanup finishes** (so they don't delete each other's `.part` for the same output;
  the user is told "an export is running").
- **Cancel**: for both the button and closing the window, the UI side detaches the job immediately
  (hides the widgets, discards the result; `export_parquet_cancel` is `log_op`'d only for the
  button; both record `parquet_export` / `cancel` as the `job`). The worker stops at the next chunk
  boundary (and just before publishing with `os.replace`) and deletes `.part`. No notice is shown
  when closing the window. **Opening a file/view does not cancel** (the first version stopped it;
  changed by user decision).
- **Time strings** (`range.start` / `end` in the metadata) are produced with Arrow's
  `cast(string)` (`as_py()` raises `ValueError` for sub-microsecond `timestamp[ns]`). The form is
  `2026-01-01 09:00:10.000000+0900` (keeps the time-zone offset and nanoseconds; not the earlier
  `isoformat` with a `T` separator). `source` is the `resolve()`d full path.
- **Where cancel is checked**: besides chunk boundaries, also before emitting progress and just
  before publishing (`os.replace`). After a cancel, `closeEvent` waits for the worker's cleanup with
  `QThreadPool.waitForDone(5000)`.
- **Not supported (stage 2)**: an original column used for both the output and a formula is read
  twice per chunk (Arrow and numpy). Stage 2 should make it a single read.
- **`ExportResult.full_computed`** holds all variables computed over all rows first (variables with
  unknown margin, those depending on them, and those they use). The names shown in the completion
  notice are the same.
- **Output path is the file itself** (the source Parquet): refused before starting, on the screen
  side (`MainWindow._is_source_file`): if the destination exists, `os.path.samefile` (handles
  case-insensitive file systems and hard links); otherwise a comparison of resolved paths. The
  message is `export_parquet.same_as_source` (`ui/messages`). `core`'s `ExportError` remains as a
  backstop.
- **File dialog defaults**: the default destination is `last_export_folder` (shared with image
  export), and `.parquet` is added if there is no extension. The settings screen's `exec` and
  `QFileDialog.getSaveFileName` are called inside `MainWindow.export_parquet_dialog` (replaced in
  tests). The actual export is `MainWindow.export_parquet(path, names, row_range)` (no dialog).
- **Shortcut**: the settings screen's shortcut section also gained an `export_parquet` row (shown
  only while the feature flag is ON; unbound by default).
- **While a view is loading** (expressions 55.3) the `export_parquet` entry is disabled (the layout
  is not yet settled).
- **App ch. 45 table**: rows for `export_parquet` and `export_parquet_cancel` were added (the
  initial `ctx` state turns `parquet_export` ON; items allowed to change: none).

Speed measurement (57.4; macOS, Apple Silicon; the flag turned ON via `features._EFFECTIVE`, not
`PARQUET_ANALYZER_FEATURES`; offscreen; the file has row groups of 500k rows with 3 float64 + int32
+ float32 + time-zone-aware time columns; written in chunks of 1 million rows). Formulas: `a*2+b`,
`rolling_mean(a,50)`, `delta(d1)+where(c>0,b,-b)`, and the user function `roll5` (constant window,
unknown margin):

| Condition | Rows | Time | Rows/s | CPU time / elapsed |
|---|---|---|---|---|
| 7 columns (3 original + 4 virtual, one computed over all rows), all rows | 5M | 0.71 s | about 7.06M | 1.05 |
| same | 20M | 2.73 s | about 7.33M | 1.05 |
| only `a` and `d1` (chunked computation only) | 20M | 1.06 s | about 18.85M | 1.06 |
| visible range (same 7 columns, all-rows computation mixed in) | 5M | 0.99 s (20M-row file) | about 5.07M | 2.6 (the all-rows numeric work uses several threads) |

CPU usage is about one core (read → compute → write run in order per chunk, and zstd compression
is also on this thread). At this scale the wait is a few seconds, so stage 2 (overlapping read,
compute and write) is not urgent. At the 100M-row scale (ch. 58) about 15 seconds plus the
all-rows computation is expected; if needed, the first candidate is to move writing to
`ParquetWriter` (compression) to another thread.

#### 57.7 Stage 2: export from a saved view (added 2026-10-15, user request)

Purpose: without opening the view (no virtual-variable computation or drawing), write only the
chosen signals from a saved view's definition. The export processing (`build_plan` and
`export_parquet` in 57.2) does not use screen state; it works from only the path, time axis,
formulas, a copy of functions and maps, and the output columns, so it is enough to build the plan
from the view JSON and the file's schema (reading only the footer). It builds on stage 1
(57.1-57.6). One branch (`feat/57-parquet-export-view`).

##### 57.7.1 Entry points

- **View manager** (persistence ch. 20): a "Export to Parquet..." button for the selected view
  (only while the feature flag `parquet_export` is ON). Works even with no file open.
- **Export screen** (57.1): a "Source: current screen / saved view (combo to choose the view)"
  choice is added at the top. Choosing "saved view" changes the list, range and source-file fields
  to the view's contents. The toolbar entry becomes usable without an open file via "saved view"
  (it used to be `error.open_file_first`; with no views at all it stays as before).
- While a view-load computation is running (expressions 55.3), neither entry can be used, same as
  for the current export (to keep the reasons consistent; exporting from a saved view itself does
  not touch screen state). The one-export-at-a-time rule (57.1) applies too.

##### 57.7.2 Source file

- The default is the file saved in the view (`io/view.resolve_source`, the same resolution as
  `load_view`). "Other file..." lets the user pick another Parquet file (one open in the app or
  not; it is not opened in the app). If it is not found, the field shows it in red and "Export"
  cannot be pressed.
- Column names and types come from `pyarrow.parquet.read_schema` (footer only). The row count also
  comes from the footer metadata.
- **Signal aliases** (persistence ch. 54): if the global alias switch is ON (54.9), `resolve_aliases`
  is solved with the file's columns and the current alias dictionary, and the view's names (series,
  names referenced by formulas, time axis) are mapped to original columns. The output column name
  stays the view's name (alias) and the content is the original column (same as 57.6). The aliases
  used are recorded in the metadata `aliases` (`{alias: original column}`).

##### 57.7.3 Time axis and signal list

- Time axis: the view's `time_column` (resolved through aliases). If absent (an old view), the same
  choice as the default time axis when that file is opened. If the file does not have it, export is
  impossible (the reason is shown).
- List: (1) the plots' signals (plot order, then order within the plot; duplicates merged; hidden
  series included; **checked by default**), (2) the view's other virtual variables (in the view's
  definition order; **unchecked by default**). The time axis is not listed and is always included
  (same as 57.1).
- **Unusable items** are shown grayed out in the list, cannot be checked, and carry a reason: a
  column not in the file (not resolvable by alias either), a missing function/map, a function with
  the wrong argument count, a syntax error in the formula, a virtual variable that depends on an
  unusable virtual variable, a virtual variable with the same name as a column. The judgment uses
  **the same rules and the same function** as `load_view`'s skipping and the view manager's
  "missing" marks (persistence 20.2, 54.5), so the three places never disagree. (If the current
  judgment depends on screen state, extract it into a pure function that takes the set of column
  names, the aliases, and a copy of functions and maps.)
- Formulas are checked, but no trial computation (the first few rows) is done (the file's values
  are not read). Failures at computation time are reported as export failures, as in 57.3.

##### 57.7.4 Range

- "All rows" (default) / "Range saved in the view" (selectable only when the view has
  `x_axis_range`; that time range is exported).
- Finding the row range `[start, end)` from the time range is done in the background (the first
  stage of `export_parquet`): narrow down the boundary row groups using the time-axis column's
  row-group statistics (min/max), read only those row groups and `searchsorted` (the whole file is
  not read at once). The time axis is assumed ascending (data ch. 29). If there are no statistics,
  or the inside of a boundary row group is not ascending, find it by reading the time axis in chunks
  (memory is the chunk size). If the range has no rows, fail with "No rows in the range".
  `x_axis_range` is a screen number (for time, seconds since 1970, `to_numeric`), so compare it with
  the same conversion.

##### 57.7.5 Computation, writing and screen

- Only how the plan (`ExportPlan`) is built differs; the export processing, progress, cancel,
  `.part`, completion notice and `last_export_folder` are the same as stage 1. Functions and maps
  are a copy of the app's current ones (`_registry_snapshot()`).
- A pure function that builds the plan is added to `core/parquet_export.py` (e.g.
  `plan_from_view(view, path, schema_columns, aliases, registry, names, range)`; no Qt). The screen
  side only reads the view JSON and passes the alias dictionary and the copy of functions and maps.
- It never touches the file, display or virtual variables currently open in the app (it does not use
  the data source, `_raw_variables` or `_derived`). Choosing the same file that is open is fine (the
  reads are export-only, 57.6). If the output path is the source file, it is refused (same judgment
  as 57.6).
- Default file name: `<source Parquet name>_<date-time>.parquet` (same as stage 1).
- Metadata: in addition to stage 1's content, `view` is the view's name, plus `view_file` (the path
  of the view JSON) and `aliases` (only when used).
- Operation log: `from_view` (the view name) and `source` (the source file) are added to
  `export_parquet`. The view manager's button uses the same `log_op`.

##### 57.7.6 Test plan

- `core` (no Qt): build a plan from the view JSON and the schema (dependencies of the plots' signals
  and virtual variables; unselected virtual variables are not computed) / signals, time axis and
  formula references resolvable by alias (output column name is the view's name, content is the
  original column, metadata `aliases`) / not resolved when the alias switch is OFF / the judgment of
  unusable items matches `load_view`'s skipping and the view manager's marks (same function) / saved
  range → row range (spanning row-group boundaries, exactly on a boundary, no rows in the range, a
  file without statistics), and the export of that range equals the same range of the all-rows
  computation / an old view (no `time_column`).
- Screen (dialogs replaced): the view manager's button (hidden with the flag OFF; usable with no
  file open) / switching "Source" on the export screen changes the list, range and source file /
  choosing another file changes which items are usable / a file that is not found / the file and
  display open in the app do not change during an export (and the data source is not rebuilt) /
  **export works without opening the view** (`load_view` is not called, no virtual variables are
  created) / operation log.
- App ch. 45 table: a row for `export_parquet` from a saved view (items allowed to change: none).

## Relations to other domains

- **Plots** ([plots.md](./plots.md)): what `export_snapshot()` contains (hidden series excluded,
  height ratios, X-axis linking).
- **Analysis** ([analysis.md](./analysis.md)): the analysis windows share the save routine.
- **Persistence** ([persistence.md](./persistence.md)): `last_export_folder`, the view name (shown
  in the annotation).
- **Analysis** (ch. 25, 2026-10-03): the analysis windows' image export uses
  `_image_stamp(range_override=...)` to annotate the range the window computed.
- **Expressions / Data** (ch. 57, 2026-10-15): the Parquet export computes only the chosen virtual variables and their dependencies, in chunks (per chunk for formulas with a known `expression_margin`, over all rows for the others). Functions and maps use a copy.
- **Data** (56.4, ch. 58): `source_row` is added when displayed sorted. Export speed is judged by the measurements in ch. 58.
- **Persistence** (57.7, 2026-10-15): export from a saved view uses the same judgments as the view JSON (`resolve_source`), signal aliases (ch. 54) and the view manager's "missing" marks (20.2, 54.5). The view manager gets an export button.
