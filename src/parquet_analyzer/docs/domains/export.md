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
- **An existing file is never silently overwritten** (added 2026-10-16, user request; data was actually lost). When the destination already exists, a warning is shown; the default is
  "save under a different name with a timestamp", and overwriting is allowed after confirmation. Writing onto the source Parquet file is refused as before. Details in 57.8.1.
- **One view can be applied to several files A, B and C and written separately as A', B' and C'** (added 2026-10-16, user request). They are never mixed into one file. The time axis can also be chosen on the export screen. Details in 57.8.2 and 57.8.3.
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

#### 57.6 Decisions made during implementation and differences from the spec (2026-10-15)

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

#### 57.8 Stage 3: overwrite protection and exporting several files (added 2026-10-16, user request)

##### 57.8.1 Overwriting an existing file (a bug fix; fix first)

Background: the user picked an existing file as the destination and the original data was lost. Currently there is no confirmation of our own; we rely on the OS save dialog's confirmation (`ui/main_window.py`
`QFileDialog.getSaveFileName`). When `.parquet` is appended afterwards to a name without an extension, nobody checks whether the resulting name already exists (it slips past the OS confirmation.
**Not yet confirmed to be the cause**; one tries to reproduce it and records the result in 57.6). The core also replaces an existing `path` unconditionally.

- **Core** (`core/parquet_export.py`): `export_parquet(..., overwrite=False)`. If `path` already exists and `overwrite` is not set, raise `ExportError` without writing anything
  (no `.part` either). The source-file check (57.2) still refuses regardless of `overwrite`. `overwrite=True` is passed only after the screen's confirmation
  (double defence: even a path that forgot the screen's confirmation cannot destroy a file).
- **Screen**: after the destination is decided (**after** the extension is completed), if the path already exists, show a confirmation dialog (`ui/messages.py`). The OS confirmation is
  suppressed with `DontConfirmOverwrite` so that there is exactly one confirmation. Three buttons:
  - "Save with a timestamp" (**default**): `<name>_<YYYYmmdd_HHMMSS>.parquet` (`_2`, `_3`, ... if that already exists within the same second).
  - "Overwrite": shows the existing file's name, size and modification time in the body, and states that overwriting cannot be undone.
  - "Cancel": does nothing.
  If the source Parquet file itself was chosen, the flow does not reach the third confirmation and is refused as before with `export_parquet.same_as_source`.
- The export-from-a-view path (57.7) and the multi-file path (57.8.2) go through the same function (one confirmation component; not written per path).
- Operation log: `export_parquet` gets `overwrite` (`true`/`false`) and `renamed` (`true` when saved with a timestamp).
- **Test plan**: core — with an existing `path`, `overwrite=False` raises `ExportError`, the existing file's content is unchanged and no `.part` remains / `overwrite=True` replaces it (the
  existing tests are kept: a failure or cancel midway leaves the original) / the source file is refused even with `overwrite=True`. Screen (dialogs replaced) — a confirmation appears for an existing path /
  choose a name without an extension, and if the completed name exists a confirmation appears (**a regression test that fails without this fix**) / "Save with a timestamp" creates a different file and the existing one is untouched /
  only "Overwrite" replaces it / "Cancel" does nothing / `_2` when the timestamped name also exists / the source file is refused without reaching the confirmation / the same holds for export from a view.

##### 57.8.2 Applying one view to several files and exporting them together

The user's intent (2026-10-16): prepare different files A, B and C and **choose one view**. The signals chosen in the view are taken from each of A, B and C
and written as A', B' and C' to **separate files** (never mixed into one file). (This was first misread as "choose several views". The unit of input is the file; the view is the common
definition of "what to write".)

Since 57.7 already handles "one view + one other file", this is widened to "several files". The signal alias mechanism (persistence ch. 54) that absorbs per-file column-name differences
is used as it is.

- Entry: when "Source: saved view" is chosen on the export screen (57.7.1), the source-file field accepts **several files** ("Another file..." becomes multi-select;
  the default is the view's one file). The list shows the chosen files, with "usable / not usable" per file. The same screen can be entered from the view manager's button.
- Signals and range are **common**: the signal list is the view's content (57.7.3) and the same selection is used for all files. The range is "all rows" / "the range saved in the view" (it is expressed in time, so it is
  converted to rows per file; if it lies outside that file's time, the file fails with 57.7.4's "no rows").
- Per file, the view's signals, virtual variables and time axis are **resolved against that file's columns** (including aliases; a signal that cannot be resolved is "not usable" as in 57.7.3. A file with
  unusable signals is **written without those signals, with a notice** (user decision 2026-10-16; not a failure). To prevent output with silently missing columns, the notice appears in two places: the
  pre-run list shows "N signals unusable (names)" per file, and the final message also lists the "files with missing signals" and the missing signal names. Only a file with no usable signal at all fails).
- One **folder** is chosen as the destination (`last_export_folder` is the default). File names are `<that file's name>_<view name>_<YYYYmmdd_HHMMSS>.parquet` (unusable characters become `_`;
  duplicates get `_2`...). Names contain a timestamp so they normally do not collide, but if they do, the name is **always changed** rather than asking 57.8.1's confirmation (batch export never overwrites).
- Execution: **re-decided in 57.9** (2026-10-16). Up to 2 files at once (1 to 4 in settings, narrowed by the memory estimate), and each file is also written multi-core.
  Progress is shown as a status-bar summary and the "export list" (state, %, rows/s, remaining per file). Cancel is per file or all (finished files remain; `.part` files are removed).
  (The first version's "one at a time in order" was changed because measurement showed the write pinned to one core is the bottleneck.)
- If one fails (file unreadable, signal unresolved, write failure), **the rest continue**. At the end one message shows the number succeeded, the failed files with reasons, and the output folder.
- Metadata (57.7.5) is per file (`source` is that file, `view` is common). Operation log: `export_parquet_batch` for the whole (view name, file list, folder),
  and `export_parquet` per file.
- **Test plan**: the core is unchanged (`plan_from_view` is called per file). Screen — choosing 3 files produces 3 outputs, each with correct source, signals and row count / a file with different column names
  is resolved by alias / a file whose signal cannot be resolved is written without that signal, and the missing signal names appear in the pre-run list and the final message / a file with no usable signal fails and the others are written / they run in order (never 2 at once) / cancel prevents the rest from starting and finished ones remain /
  with the same name `_2` is used and batch never overwrites an existing file / export without opening the view (`load_view` is not called) / with one file the flow is as before.

##### 57.8.3 Choosing the time axis

The answer to the feedback, "make the Export time axis selectable". **Current specification**: the leading time-axis column of the output cannot be chosen. From "the current screen" it is the time axis in use at that moment; from a view it is
the view's `time_column` (resolved by alias); for an old view without that, the file's first column (`time_name` in `parquet_export.py`). The name is not fixed to `TIME`, but
the export screen has no place to choose it, and changing it requires changing the time axis on the main screen.

In 57.8.2 the time-axis column name can differ per file (e.g. `TIME` in A, `t` in B), so it is made selectable here:

- Add a **time axis** combo to the export screen. The choices are "the view's time axis (default; resolved by alias)" and the columns of the chosen files usable as time (monotonic numeric / datetime columns).
- With several files, choose either "the view's time axis" (resolved per file by alias) or a column name common to all files. A file without the chosen column name fails (treated as in 57.8.2).
- The chosen column becomes the leading time axis of the output. **The range computation (57.7.4) uses the view's time-axis column, not the chosen column** (2026-10-17, fixed in review): the numbers of "the range saved in the view" are in the view's time-axis unit, so applying them as they are to another column (e.g. `idx = time × 1000`) silently picks wrong rows. `find_row_range` is run on the view's time-axis column to get rows, and those rows are written with the chosen column. If the view's time axis cannot be resolved in that file and the saved range is used, that file fails (`RangeAxisMissing`, "The saved range is in the view's time axis (X) unit, but this column is not in this file"). The order check (data ch. 29) is not in the export path to begin with and is not added.
- Test plan: screen — the combo's choices / the chosen column appears first / resolution and failure when column names differ across files / the default is the same output as before.

#### 57.9 Speed, parallelism and progress display (2026-10-16)

Background: facing 57.8.2 (several files), the user asked: "Should files run at the same time, or should one file be written multi-core, or in sequence? How do we make export fast?"
The user's mandatory conditions (2026-10-16): (1) **the screen never freezes** during export. (2) Progress is clearly visible (what is running, k/N, % per file and overall,
per-file state: waiting, running, done, failed, cancelled). 57.4's "decided in stage 2" and 57.8.2's "one at a time in order" are re-decided here.

##### 57.9.1 Measurements (2026-10-16, Apple M4 Pro 12 cores, 24 GB, polars 1.44.2, pyarrow 25.0.1)

Input: four files of 4 million rows x 20 float32 columns (hard-to-compress values, about 84 bytes/row, 338 MB per file, row groups of 1 million rows). The columns written match the actual use in 58.5.1:
**time axis + 10 original columns + 20 virtual variables** (element-wise expressions, `rolling_mean`, `delta`, `where`, chains; all expressions that can be computed in chunks). Files were in the page cache
(disk reads were not measured). Rows/s is total throughput, not per file. The heartbeat is the maximum interval at which a separate Python thread wakes every 10 ms
(a measure of the time spent waiting for the GIL; stands in for the UI thread).

**Breakdown of the current implementation** (one file, 57.6's `export_parquet` as is):

| Stage | Time | Share |
|---|---:|---:|
| Read original columns (polars→Arrow, type `cast`) | 0.15 s | 6% |
| Read for computation (per column, to float64) | 0.15 s | 6% |
| Evaluate expressions (numpy) | 0.15 s | 6% |
| **`ParquetWriter.write_table` (encode, zstd, write)** | **2.06 s** | **82%** |
| Total (CPU 1.0 core) | 2.53 s | 1.56M rows/s |

**Write settings** (one chunk of 1 million rows x 31 columns, pyarrow `ParquetWriter`): dictionary encoding (ON by default) is ineffective for floating point and only costs time.

| Setting | ms/chunk | Size |
|---|---:|---:|
| zstd, dictionary (current) | 403 | 202 MB |
| zstd level 3 | 432 | 201 MB |
| zstd, no dictionary | 244 | 191 MB |
| zstd, no dictionary, BYTE_STREAM_SPLIT | 177 | 148 MB |
| snappy / lz4, no dictionary | 122-123 | 206-207 MB |
| no compression, no dictionary | 104 | 208 MB |
| polars `write_parquet` (zstd) | 91 (CPU 4.3 cores) | 187 MB |

Even for rounded values (3 digits, like an ADC), the size difference without a dictionary is small: 0.9 → 1.3 MB per 1 million rows x 10 columns. pyarrow's writer processes one row group on one core
and has no setting to write columns in parallel. polars encodes columns in parallel.

**Speed within one file** (one file, same columns as above):

| Method | Time | Rows/s | CPU (cores) | Peak resident |
|---|---:|---:|---:|---:|
| Current (pyarrow, zstd, dictionary) | 2.56 s | 1.56M | 1.0 | 1051 MB |
| pyarrow, no dictionary | 1.88 s | 2.13M | 1.0 | — |
| pyarrow, no dictionary, BYTE_STREAM_SPLIT | 1.31 s | 3.06M | 1.05 | 771 MB |
| Above + overlap computation and writing (one queue, 500k-row chunks) | 0.99 s | 4.06M | 1.5 | 790 MB |
| **polars `register_io_source` (generator returning chunks) + `sink_parquet` (zstd)** | **0.60 s** | **6.63M** | **4.3** | **758 MB** |
| Same, original columns only (no virtual variables) | 0.31 s | 12.99M | 3.5 | 476 MB |
| Reference: polars `scan_parquet().select().sink_parquet()` (original columns only, nothing in between) | 0.19 s | 21.5M | 6.0 | 881 MB |

**Several files** (4 files, 16 million rows in total):

| Method | Total time | Rows/s | CPU (cores) | Peak resident | Max heartbeat |
|---|---:|---:|---:|---:|---:|
| Current implementation in sequence | 10.23 s | 1.56M | 1.0 | 1217 MB | 13 ms |
| Current implementation, 2 threads | 5.34 s | 3.00M | 2.0 | 1879 MB | 15 ms |
| Current implementation, 4 threads | 2.97 s | 5.39M | 3.9 | 3047 MB | 22 ms |
| pyarrow, no dictionary, 4 threads | 2.16 s | 7.41M | 4.0 | 2983 MB | 13 ms |
| pyarrow, no dictionary, 4 **processes** | 2.29 s | 7.00M | 4.0 | (children) | 14 ms |
| polars writer, in sequence | 2.51 s | 6.37M | 4.1 | 852 MB | 22 ms |
| **polars writer, 2 threads** | **1.69 s** | **9.45M** | **6.3** | **1380 MB** | **21 ms** |
| polars writer, 4 threads | 1.30 s | 12.29M | 8.5 | 1758 MB | 21 ms |

What can be read from this:

- **The bottleneck is the write (encoding and compression), pinned to one core**. Reading and computation together are under 20%. One at a time in order (57.8.2's first version) uses only 1 of 12 cores.
- Merely switching to polars' writer makes one file **about 4x faster** (using 4 cores). Running 2 files at once gives a further ~1.5x (about **6x** the current in total). 4 at once is 8x but
  doubles the memory, and with real data it is expected to hit a ceiling from disk reads and writes.
- **Threads and processes are equally fast** (7.41M vs 7.00M rows/s), because polars, pyarrow and numpy release the GIL in the heavy parts. There is no reason to use processes.
- In every method the maximum heartbeat is 22 ms (waiting for the GIL is at most about 12 ms). As long as heavy work is not placed on the UI thread, the screen does not freeze.
- Memory is **determined by the chunk size, not by the row count**: about 500-600 MB per file (1 million rows x 31 columns). It is proportional to the number running at once. Only variables
  computed over all rows first (57.6) are proportional to the row count (4 GB per variable at 500 million rows).

**Limits**: (1) Values are from a page-cached state and exclude disk reads and writes. For an output of 500 million rows x (10 original + 20 virtual), extrapolating from these values gives about 64 GB per file
(float64 virtual variables are hard to compress) and about 76 seconds of CPU alone (about 320 seconds now). With an internal SSD (several GB/s of writing) the write time would be 20-30 seconds and the CPU is still
expected to be the bottleneck. On external disks the benefit of running at once is small. (2) On CI Ubuntu (around 4 cores) the numbers are smaller and the benefit of 2 at once is small. Tests do not check
speed (57.9.9 below). (3) Single measurements (no repetition).

##### 57.9.2 Decisions (summary)

1. **Within one file**: replace the write with polars' streaming write (generator returning chunks → `sink_parquet`). Column encoding and compression become multi-core.
   Read → compute stays per chunk inside the generator as before (the rules and results of 57.2 and 57.6 are unchanged). Only when columns whose types polars cannot preserve are included, fall back to the
   current pyarrow write (dictionary only for non-floating-point columns) (57.9.3).
2. **Between files**: **at most 2 at once** (setting `export_parallel_files`, 1 to 4, default 2). In addition, narrowed so that the total memory estimate does not exceed the budget (57.9.5).
   A file that alone exceeds the budget runs alone (it is not refused).
3. **Done with threads** (no processes). One file = one `WorkerHub` job (exactly one of success, failure or cancel, ch. 37). The maximum thread count of the dedicated export
   `QThreadPool` is `export_parallel_files`.
4. **One export queue** (replacing 57.1's "only one at a time"): if another export is started during an export, it is not refused but appended to the end of the queue.
5. **Progress**: the worker only sends numbers, at most 10 times/s per job. The screen draws from the latest values on a 4 times/s timer. The status bar shows a summary and details are in the
   "export list" (a modeless window) (57.9.7).
6. **Free disk space** is checked with an estimate before starting and also per chunk while running (57.9.6).

Not adopted: splitting into processes (same speed, and adds the hand-over of plan, function and map copies, the operation log and GC rules) / overlapping computation and writing with pyarrow kept (about 1.3x, short of polars' ~4x) / using
`scan_parquet().sink_parquet()` directly when only original columns are written (about 1.7x faster, but per-chunk progress, cancel, disk checks and the row range after `find_row_range` would have to be built
separately. One path is preferred. If it is called slow, handle it in another section) / BYTE_STREAM_SPLIT (also makes the size smaller, but it is unknown whether receiving tools can read it. Not used in the polars path).

##### 57.9.3 Writing one file (`core/parquet_export.py`)

- `_Run.chunk_table(lo, hi)` stays as it is (column order, type `cast`, float64 for virtual variables, slicing values computed over all rows). The write loop of `export_parquet` is replaced with a
  **generator** `_chunks()` (per chunk: `check()` → `chunk_table` → `pl.from_arrow`, then yield; emits progress), and a `LazyFrame` from
  `polars.io.plugins.register_io_source(io_source=..., schema=the polars schema of the first chunk)` is written with
  `sink_parquet(part, compression="zstd", row_group_size=chunk_rows, statistics=True, arrow_schema=Arrow schema with metadata)`
  (found in the 2026-10-16 implementation: `metadata=` goes only into the file's key-values and cannot be read via `pq.read_schema(...).metadata`; with `arrow_schema=` both can read it. It can be used because only columns whose types round-trip take this path).
  The first chunk (which determines the schema and the metadata's range text, 57.6) is still built before the generator, and the generator yields it first.
- **Cancel / failure** (revised 2026-10-16, review): the generator **does not raise**. When `check()` / `chunk_table` etc. raise an exception
  (`ExportCancelled`, disk/memory failure, expression error), the generator stores that exception in the closure and **simply `return`s**
  (ends normally). Then `sink_parquet` closes the file itself and returns, after which the **stored exception is re-raised** (`ExportCancelled` for cancel, otherwise
  the original exception) and `.part` is removed **exactly once**. Before calling `sink_parquet`, `check()` and `gate()` are each run once, so polars' write is not started for a failure before the first chunk.
  Reason: measurement showed that if the generator raises, polars' writer thread **creates `.part` about 34 ms after the error is returned** (under high load).
  A fixed wait cannot prevent this, and later exporting to the same path could produce a broken (zero-filled) file. If the generator ends normally, the writer thread finishes before returning, so
  no guessing about timing is needed. The file is closed in a "too few rows" state but is removed immediately and never published.
- **The generator runs on polars' thread** (a `Dummy-n` thread as seen from Python). It must not touch UI parts or `QObject`s there (as now).
  Automatic GC is off (37.2), so GC does not run on that thread. The chunk arrays create no reference cycles and are freed by reference counting.
- **Types**: polars preserves the original types (integer widths, float32, timezone-aware times, nanoseconds, date), but strings become `large_string` (confirmed by measurement; specifying it with `arrow_schema`
  fails). So at planning time, check whether each output column's Arrow type **round-trips to the same type** via `pl.from_arrow(empty table).to_arrow()`; if even one does not, that
  file is written with **pyarrow's writer** (the current `ParquetWriter`; `use_dictionary` only for non-floating-point columns, zstd). Which one was used is recorded in
  `ExportResult.writer` (`"polars"` / `"pyarrow"`) and in the operation log. Values, types, metadata and row groups (one row group has at most `chunk_rows` rows) must be the same in
  both (57.5's tests are run on both).
- **Row groups**: 57.5's "number of row groups = number of chunks" is relaxed to "each row group has at most `chunk_rows` rows and they total the row count" (polars may cut row groups
  differently from chunk boundaries; they matched in measurement). The time-axis column's statistics (min, max) are written (for when `find_row_range` reads that output).
- **Chunk size**: `chunk_rows` is the row count at which one chunk's Arrow table fits in about 256 MB, from "the size of one output row (sum of type widths + the float64 intermediates of computation)"
  (clamped to 65,000 to 1,000,000 rows). With 31 columns it stays 1 million rows; with 300 columns about 120,000 rows. Memory stays constant even with many columns.
- **Progress**: the generator emits `progress(rows returned, total)` just before returning `chunk_table`. polars buffers a little before writing, so rows returned run ahead of rows
  written. On the screen side, it is held at 99% until the job's completion notice arrives (57.9.7).
- polars' `register_io_source` is an API documented as "unstable". The polars version is pinned in `uv.lock`, and the behaviour above (how cancel is wrapped, `.part`, types) is watched
  by tests. If it is unavailable (`ImportError` etc.), fall back to the pyarrow write.

##### 57.9.4 Ordering between files (`core/export_queue.py`, new; no Qt dependency)

- `ExportQueue`: the export queue and its state. An item = one output file (`ExportItem`: number, source file, output path, plan (or a function to build the plan),
  memory estimate, state, rows, failure reason). States: `waiting` → `running` (stages: finding the range / computing over all rows / writing) →
  `done` / `failed` / `cancelling` (cancel requested but the worker is cleaning up) → `cancelled`. An item cancelled while waiting goes straight to `cancelled`.
- `next_to_start(running)`: from the head of the waiting items, returns the first one satisfying (a) the number running is below `export_parallel_files`, (b) the running estimates' total + this estimate is within the budget
  (it starts even over budget if nothing is running), and (c) its output path equals no running or cancelling item's (57.6's "don't delete each other's `.part`"). It never
  overtakes: if the head waits on (b), the ones behind also wait (so the order is predictable).
- Addendum (B1 implementation, 2026-10-17): items being cancelled in (a) also count toward the concurrency (they still use a thread). The free-space check in (d) is performed **only when one or more are running** (waiting when nothing is running would wait forever, so the head is started and left to fail through the file's own protection). Re-checks while waiting for free space happen at most once per second. The consecutive disk-shortage count (2 in a row fails the remaining waiting items) is **reset to 0 when a new batch starts** (so that a write after space was freed does not fail immediately because of the previous batch's streak).
- The actual starting and receiving of results is done by `MainWindow` (below). `ExportQueue` holds only the state table and can be tested purely.

##### 57.9.5 Memory budget

- Estimate (`estimate_export_memory(plan)`, a pure function): `min(chunk_rows, max(rows, 1)) x (bytes per output row x 3 + 8 x (number of original columns used by the expressions + virtual variables computed per chunk))`
  (a chunk is never larger than the rows being written, so a 1,000-row export is not estimated at 1 million rows)
  + `total rows x 8 x (number of variables computed over all rows first and their inputs)` + 200 MB. The factor 3 stands for "Arrow table, polars copy, write buffer" (against the measured about 500-600 MB per file,
  about 1.1 GB for 31 columns, i.e. conservative).
- Budget: 40% of physical memory (obtained with the standard library: `os.sysconf` `SC_PAGE_SIZE` x `SC_PHYS_PAGES` on macOS and Linux, `ctypes` `GlobalMemoryStatusEx` on Windows.
  `psutil` is not an app dependency so it is not added. 4 GB if it cannot be obtained). When a memory limit setting exists in 58.5 stage 2-3, follow it.
- A file that alone exceeds the budget (e.g. 500 million rows with many variables computed over all rows first) runs alone, and its list row shows "Uses a lot of memory (about N GB)".
  If the estimate exceeds physical memory, a warning appears in the list before starting (the user decides whether to stop; the cancel button).

##### 57.9.6 Free disk space

- Before starting (when adding to the queue): compare the free space of the output folder (`shutil.disk_usage`) with "rows x uncompressed bytes per output row" summed over all waiting and running items, and if
  insufficient show a warning above the list (it still starts, since how much compression shrinks is unknown).
- While running: the generator checks free space per chunk, and **when free space drops below (bytes still expected to be written) + a 100 MB margin**, that file fails with `DiskFull` (`ExportError("disk full")`) (`.part` is removed).
  Bytes still expected = remaining rows x (bytes written to `.part` so far / rows so far) (a measured value that accounts for compression; while nothing has been written it is 0 and only the margin is considered).
  Reason: with a fixed 1 GB, not even a 1 MB export could be written to a nearly full drive such as a USB stick with 800 MB free. What we want to protect against is "using it all up and dragging the OS and
  other apps down", so stopping at the needed amount + a small margin is enough. The pre-start check margin (57.9.6a) was also fixed to the same 100 MB.
  This prevents exhausting the disk and dragging other apps and the OS down. The rest of the queue continues (it can be written if space frees up), but if it fails consecutively for the same reason, the rest are not started and are failed
  (reason: "Not enough free disk space").
- **The decision to start a new file** (`next_to_start`'s (d)) estimates the remainder of running files as "uncompressed upper bound (`max_bytes`) x remaining fraction" (no compression assumed, the safe side. Intentional design. The stop while running (formula above) uses the measured compression ratio).
- Reference: 500 million rows x (10 original + 20 virtual) is about 64 GB per file (57.9.1). Three files come to about 190 GB, exceeding the development machine's free space (about 178 GB, 58.1).

##### 57.9.6a Not failing midway (memory and disk monitoring, 2026-10-16, user request)

57.9.5 and 57.9.6's "warn and start" is strengthened to **stop before starting + protect by slowing down and waiting while running**. "Failing while writing" becomes the last resort.

- **Decided before starting (when adding to the queue)** (pure functions in `core/export_queue.py`; free disk and memory can be replaced via arguments):
  - Disk: whether the **upper-bound estimate** of all waiting and running outputs (rows x uncompressed bytes per output row; savings from compression not assumed) is at most the output folder's free space
    minus the margin (fixed 100 MB; the former "1 GB or 5% of free space" was dropped because it would make even small exports ask for confirmation on a nearly full drive). **If it is exceeded, show a confirmation screen** (default "Stop"; "Start anyway", "Reduce files".
    Compression may make it fit, so the user decides whether to continue). When outputs span several folders or drives, sum per folder (volume).
  - Memory: the same confirmation if one file's estimate exceeds **the currently free memory** (not physical memory, but what the OS regards as "available").
- **While running**:
  - Disk: check free space per chunk. (a) If free space is about to fall below "bytes still expected + margin", **do not start new files** (they stay waiting with "Waiting for disk space" shown).
    (b) If free space still falls below "bytes still expected + 100 MB margin" (the 57.9.6 formula), **stop only that file and fail it** (`.part` removed). Other running files continue (they free space as `.part` is removed).
    (c) It stops starting from the running file with the least progress, so finished files always remain.
  - Memory: check free memory per chunk. When free memory drops below 2 GB, **do not start new files** and halve the chunk row count (floor 10,000 rows).
    Halving happens **once per drop below 2 GB** (even if the state persists, it is not halved at every 0.5-second check; this avoids 1M rows becoming 10,000 in 3.5 seconds).
    When it returns to 2 GB or more, it is doubled at each check until `plan.chunk_rows` (halved once again if it drops again). `chunk_halvings` is the number of halvings. Below 1 GB the
    running file is **paused** (waits at a chunk boundary; the screen stays responsive) until free memory returns, and if it does not return within 30 seconds only the newest file fails.
    This is a second safety in addition to the budget (40% of physical memory) at the default concurrency of 2.
  - The list row's state gets "Waiting for disk space" and "Waiting for memory", with the reason displayed.
- Free space is obtained with the standard library only (disk: `shutil.disk_usage`; memory: "available" obtained as in 57.9.5. If it cannot be obtained, monitoring is skipped and only the pre-start decision is made).
- Tests: with free disk and memory replaced, check (1) the pre-start confirmation (exceeds / fits / per volume), (2) new files are not started when free space shrinks, (3) when it drops below "remaining + 100 MB"
  only that file fails, no `.part`, finished files remain, (4) the chunk halves under memory pressure, and pausing then resuming when it returns.

##### 57.9.6b Choosing the virtual-variable type (float32, 2026-10-16, user request)

- Add "Virtual variable type" to the export screen: `float64` (default; as before) / `float32`. The time axis and original columns are **kept in their original types** regardless of the choice (57.1).
- float32 makes the output about half the size (500 million rows x 20 virtual: 64 GB → about 44 GB; the original columns' share is unchanged) but precision is about 7 digits. The chosen type is
  recorded in the metadata (57.7.5). The disk estimate (57.9.6a) is also computed with this type.
- NaN and Inf of virtual variables are kept as they are. A large value that does not fit float32 becomes Inf. **The check is done per chunk during the export** (the values to write are unknown without computing them); the names of variables that
  have finite values turned into Inf are left in `ExportResult.float32_overflow`, and **the names are shown in the final message** (the values are written as Inf).

##### 57.9.7 Progress display (`ui/parquet_export_dialog.py`)

- **Worker → screen**: the progress path stays the current `progress_sink` (37.1, relay object). The worker-side `emit` is **throttled**: nothing is sent if less than 100 ms has passed since the last one.
  However, stage changes (the `total < 0` marker, 57.6) and the last chunk are always sent. The UI-thread receiver only puts each item's latest `(done, total, time)` into
  a dict (it touches no widgets).
- **Screen update**: a 250 ms timer (`QTimer`, on the UI thread) that runs only during export redraws the status bar and the list from the latest values. Rows/s is a moving average over the last
  about 5 seconds; the remaining estimate is (remaining rows / rows/s). The stages of computing over all rows and finding the range have no fraction, so they show a moving bar with "Computing all rows..." / "Finding the range...".
  The row's % is held at 99% until the job's completion notice arrives (57.9.3). Note for stage B: progress reaches 100% when the last chunk is handed to polars, but polars is still writing at that time
  (rows returned run ahead of rows written). The screen holds 99% until the completion notice, and actual completion is decided by `worker_done` / the job's completion. Heavy work (other than building the plan and reading footers) is not done on the UI thread.
- **Status bar** (extends 57.3's `ParquetExportProgress` component): an overall bar (weighted by rows: sum of finished rows / sum of total rows; files whose row range is not yet known
  are counted by the file's row count), "Exporting to Parquet 2/5 · 43%" (with one file, as before "Exporting to Parquet NN%"), "Details...", and
  "Cancel all". Hidden when the queue becomes empty (as now).
- **Export list** (`ParquetExportPanel`, new, a modeless `QDialog` with an `objectName`, one per `MainWindow`):
  - Opens automatically when an export of two or more files starts. Not for one file (can be opened from the status bar's "Details..." or the menu). Closing it does not stop the exports.
  - Top: the overall bar, "Done 2 / Failed 0 / Cancelled 0 / Total 5", elapsed and remaining estimates, disk and memory warnings (57.9.5, 57.9.6).
  - Table (one row = one output file, in start order): source file name / output file name / state (waiting, finding range, computing all rows, writing, cancelling, done, failed,
    cancelled; shown by both colour and text) / % bar / rows (done/total) / rows/s / elapsed / remaining / actions ("Cancel" for waiting and running, "Open folder" for done).
    The failure reason is in the row's tooltip, and in full in the field below the selected row.
  - Bottom: "Cancel all", "Clear finished", "Close".
- Opening a file or a view leaves the list and the status-bar display as they are (the rule of 57.3 stays). When everything finishes, the status bar shows a one-line summary (number succeeded, number failed, output
  folder). If there is at least one failure, a single warning from `ui/messages.py` appears once (57.8.2's "one message").

##### 57.9.8 Cancel and cleanup (`MainWindow`)

- **Cancel one**: if waiting, just make it `cancelled`. If running, set that job's `cancel_event` and make it `cancelling` (the worker stops at the next chunk boundary,
  at most one chunk = around 1 second, and removes `.part`; as in 57.6). On the job's completion (`worker_done`) it becomes `cancelled` and the next one starts. The screen discards the result (the current
  `hub.cancel(token)`).
- **Cancel all**: mark all waiting as `cancelled`, then treat all running as above.
- **Closing the window**: cancel all, then `waitForDone(5000)` on the dedicated export pool (as now).
- For each finished job (success, failure, cancel), call `ExportQueue.next_to_start` to start the next (the completion notice arrives on the UI thread exactly once, ch. 37).
  The job's strong reference (`pending`) is held until the notice is handled (ch. 37's rule is unchanged). What the in-flight job captured (plan, chunk arrays) is returned to the
  UI thread by `_Task`'s `del func`.
- Addendum (B1 implementation): the spec's `hub.cancel(token)` is used **only when closing the window**. Normal cancel receives the result notification and uses it for the "cancelling → cancelled" transition and for starting the next file. A file the worker had already finished writing during cancelling becomes "done" (the file actually exists). So right after pressing, the status bar shows "Cancel requested", and "Cancelled" only after everything has finished.
- Operation log: `export_parquet_cancel` with `target` (the output path, or `"all"`). The job's `job` record is per file (the current `parquet_export`).
  `export_parquet_batch` (57.8.2) gets `parallel` (the number run at once).

##### 57.9.9 Test plan

- `core/parquet_export`: all of 57.5's tests pass with **both the polars and the pyarrow writer** (values, types, metadata, row groups at most `chunk_rows` rows and totalling the row count) /
  if some column's type does not round-trip in polars, such as a string column, pyarrow is chosen (`ExportResult.writer`) / in the polars path, a cancel in the middle of the generator becomes
  `ExportCancelled` and no `.part` remains, and other exceptions come out with their original type / when `register_io_source` is unavailable it falls back to pyarrow (replaced) / time-axis
  statistics are written / the automatic size of `chunk_rows` (smaller with many columns, upper and lower limits) / progress throttling (clock replaced: nothing sent under 100 ms;
  stage changes and the last are always sent) / free disk (`shutil.disk_usage` replaced: fails below "remaining + 100 MB" and no `.part` remains; a small export passes even with 800 MB free; 0 rows can be written even with little free space) /
  the generator ends normally without raising (checked with a spy), and polars' write is not started for a failure before the first chunk / halving the chunk under memory pressure happens once per drop and returns to the original size when it recovers.
  `core` tests take `writer` as a parameter and run on **both polars and pyarrow** (all of `test_parquet_export_core.py`, via an autouse fixture). Disk and memory readings are replaced by an autouse fixture in `tests/.../conftest.py` (the real machine's free space is not read).
- `core/export_queue`: state transitions (all combinations) / up to `export_parallel_files` at once / waiting by budget, starting even over budget when nothing is running,
  no overtaking / the same output path does not start until the previous one finishes (including cancel cleanup) / cancel while waiting, cancel while running, cancel all /
  continuing after a failure, and failing the rest when disk shortage continues / an item added during export goes last. The value of `estimate_export_memory` (with and without all-rows computation).
- Screen (`MainWindow`, dialogs replaced):
  - **The screen does not freeze**: with a 10 ms `QTimer` on the UI thread, measure the maximum interval while exporting 3 files (300,000 rows each, with a small `chunk_rows`, enough to take over 1 second in total), and require
    **under 250 ms** (about 20 ms measured; leaving margin for CI slowness). Progress updates arrive at least twice midway. Regression check: calling the same export directly on the
    UI thread fails this condition (confirming the amount is sufficient). Waiting is in real time.
  - The number running at once never exceeds `export_parallel_files` (counted inside the worker). With the setting at 1 they run in order.
  - The list rows' state, %, k/N, the status-bar summary; the list does not open automatically for one file and opens for two or more; closing the list does not stop them.
  - Screen updates are throttled (about 4 redraws per second even if hundreds of progress notices arrive).
  - Cancel one (only that file has no `.part`, the others complete), cancel all, closing the window (no `.part` remains).
  - Opening another file or view during export still lets everything finish correctly (57.3) / starting another during export puts it in the queue (it is not refused with "Export in progress").
  - Operation log and app ch. 45's table (`export_parquet_cancel`'s `target`).
- Speed is not checked in tests. Run `bench_large_file.py`'s `parquet_export` stage (58.3) in its form with the export entry point available, and record time, CPU and memory on ch. 58's large files
  (at the user's measurement).

##### 57.9.10 How to proceed

- Stage A (the core's speed: 57.9.3, 57.9.5, 57.9.6, 57.9.6a, progress throttling) first on one branch (`feat/57-export-speed`). Writing one file becomes faster as it is.
  Implemented (2026-10-16, awaiting review). Decided in stage A: the pure functions for disk/memory decisions live in `core/export_resources.py` (`export_queue.py` is built in stage B;
  `preflight` / `can_start_new_file` are called from stage B's queue). The pre-start confirmation is `MainWindow._start_parquet_export` (default "Stop").
  Protection while running is within one file (per chunk: free disk below "remaining + 100 MB" → fail with `DiskFull`; free memory below 2 GB → halve the chunk (floor 10,000 rows); below 1 GB → pause; `MemoryLow` after 30 s).
  "Stop from the file with the least progress" and "do not start new files" are arbitration across several files, so they belong to stage B. `chunk_rows` is automatic via `build_plan(chunk_rows=None)` (default).
  review fixes (2026-10-16): the generator ends normally without raising (57.9.3), memory halving is once per drop plus returning to the original (57.9.6a), the disk stop is "remaining + 100 MB" (57.9.6).
  `result.writer`, `chunk_halvings` and `memory_wait` are recorded in the operation log message (`where=export_parquet`) at the same time as the success notice.
  `_darwin_available` does not add "Pages purgeable" (it double-counts as part of active/inactive; confirmed with `vm_stat`).
  Measurement (4M rows x (10+20) columns, M4 Pro): pyarrow 1.96 s → polars 0.50 s (about 3.9x).
- Stage B (57.9.4, 57.9.7, 57.9.8) goes with 57.8.2 on `feat/57-export-batch` (the entry for several files and the list are work on the same screen).
- Both touch thread handling, so they are large tasks (implementation → review).

##### 57.9.10a What stage B1 implemented and differences from the spec (2026-10-17)

Scope: `core/export_queue.py`, 57.8.2 (several files), 57.9.7/57.9.8 (list, summary, cancel). 57.8.3 (time-axis choice), 57.9.6b (float32), the `export_parallel_files` field in the settings screen and making `DISK_MARGIN` a setting are B2.

- **Structure**: the queue driver was split out into `ui/export_controller.py` (`ExportController`) (to keep `main_window.py` from growing further). It holds the queue, the dedicated pool (max = concurrency), the `WorkerHub`, the 250 ms timer, the status bar and the list. `MainWindow._parquet_export` is a compatibility property (the first running item, or None), and `_parquet_export_busy()` is "at least one item is waiting, running or cancelling".
- **`Settings.export_parallel_files`** (1 to 4, default 2, out-of-range falls back to the default) is added and read. The settings-screen input is B2.
- **`next_to_start`**: examines only the head waiting item (it does not overtake on (a), (c), (d) either, not just (b) memory). Items being cancelled also use a thread, so they count toward the concurrency. (d) The free disk/memory check (`can_start_new_file`) is made **only when one or more are running**. Waiting when nothing is running would wait forever, so the head is started and made to fail through the file's own protection (`DiskFull`) (an addendum not in the spec). Re-checks while waiting for free space are at most once per second (so `vm_stat` is not called every time). Consecutive disk shortages are 2 (`DISK_FAIL_STREAK`), after which the rest of the waiting items fail with `DiskFull`. The streak resets on success or a different failure.
- **Cancel**: the spec's `hub.cancel(token)` is not used, and the result notification is received (the screen-side notification is needed for the cancelling → cancelled transition and for starting the next file). If the worker had finished writing during cancelling, that file becomes "done" (the file actually exists). Only when closing the window is `hub.cancel` used to discard notifications.
- **Final message**: once, when the queue is empty (and nothing is cancelling). For one file the wording is as before (saved / warning / cancelled). For two or more, a one-line status plus one warning if there are failures or missing signals. When a new export is added to an empty queue, the previous batch's finished rows are cleared.
- **57.8.2**: "Another file..." is multi-select (`pick_parquet_files`). When two or more are chosen, per-file "usable / not usable" is shown under the list (following the checked signals), then folder choice → `export_parquet_batch`. Characters unusable in a view name become `_` via `MainWindow._safe_name`. The "all rows" row-count display is the total over all chosen files. Signals usable in "any of the files" can be selected in the signal list. Files whose plan cannot be built (unreadable, no time axis, zero usable signals) appear as `failed` items in the list and message.
- **List window**: `ParquetExportPanel` (`objectName` `parquet_export_panel`). Opens automatically with two or more items. A "Parquet export list..." entry was added to the View menu (when the flag is ON). "Open folder" is `ExportController.open_folder` (replaced in tests).
- **Progress**: the worker-side throttling (100 ms) is as in stage A. The screen side only stores values, and the 250 ms timer draws. Both overall and row stay at 99% until the completion notice.
- **Log**: `export_parquet_batch` (`parallel`), `export_parquet` per file (`batch`), `export_parquet_cancel` (`target` = output path / `all`).
- **Implemented in B2**: the concurrency in the settings screen, time-axis choice, float32, making `DISK_MARGIN` a setting, and 57.9.6a (c) (57.9.10b).
- Existing tests changed: the two tests "starting another during export is refused" became "goes into the queue", the replacement of `pick_parquet_file` became `pick_parquet_files`, and the View-menu item check gained the list entry.

##### 57.9.10b What stage B2 implemented and differences from the spec (2026-10-17, including review fixes)

- **Time-axis choice (57.8.3)**: when exporting from a saved view, the export screen has a "Time axis" combo (`export_time_combo`). The first entry is "The view's time axis (name)" (default; resolved by alias), followed by the chosen files' columns of numeric, datetime, date, time, duration or decimal type (`parquet_export.time_candidates`). **Candidates are narrowed by type only; monotonicity is not checked** (the data is not read; an order check is not in the export path to begin with). With several files the candidates are the **union**, and a file without the chosen column fails with "The time-axis column (X) is not in this file" (also shown in the pre-run file list). The combo is not shown for export from the current screen. If changing files or the source leaves the chosen column out of the candidates, it returns to the view's time axis. The chosen column is resolved including aliases via `view_export_info(time_column=)` and becomes the output's leading column and the metadata's `time_column`. The saved-range search uses the view's time-axis column (57.8.3, `ExportPlan.time_range_column`, `ViewExport.range_source`).
- **Virtual-variable type (57.9.6b)**: `export_dtype_combo` (float64 default / float32) on the export screen. `ExportPlan.derived_dtype`, the metadata `derived_dtype` (always written), and the output row size and disk estimate also use this type (float32 is 4 bytes). The float32 range check **reports after the export** (57.9.6b): a dedicated warning for one file, a section of the final warning for several files.
- **Settings**: `export_parallel_spin` in the settings screen (`export_parallel_files`, 1 to 4; shown only when the feature flag `parquet_export` is ON; changes take effect from the next file started). `export_disk_margin_mb` is **a settings-file key only** (no UI, default 100, outside 1 to 100000 falls back to the default). `export_resources.disk_margin()` is the single read point, and the three places (pre-check, deciding to start a new file, stop while running) read the same value (`MainWindow` calls `set_disk_margin_mb` at startup and in `apply_settings`). `DISK_MARGIN` remains as the default value.
- **57.9.6a (c) Stop from the file with the least progress**: `export_resources.DiskArbiter` (a table of running files' progress and output volumes; one is held by `ExportController` and passed through `export_parquet(arbiter=, arbiter_key=)`). When the disk falls below "remaining + margin", the file fails with `DiskFull` if it is **the one with the least progress among the running files on the same volume** (the newer one if tied) or if there is no other. Otherwise `request_stop_least` marks the least-progressed file for stopping, and it itself waits up to `DISK_YIELD_WAIT` (10 s, waiting at chunk boundaries; cancel still works) for free space to return. The marked file **raises `DiskFull` at its next `gate` (even if its own free space is sufficient)**, removes `.part` and returns the space. The waiting side continues if the space returns, and fails if it becomes the least progressed, gets marked, or times out. It does not arbitrate with files on other volumes. Calls without an arbiter behave as before.
  - Limitation: each file updates its progress in its own `gate`, so the decision can be one chunk stale.
- **Messages**: "1 GB" was removed from `export_parquet.disk_full` and the pre-check body (since the margin changes with the setting).
- **Dialog**: with several files, the reading of each file's choices (footer) is cached for the duration of the dialog per (view, file, time-axis column) (not re-read when toggling a check).
- **Export list**: column widths are fitted after the first row is drawn (source and output stretch; state, rows, rows/s, elapsed and remaining fit their contents; progress and actions are fixed).
- Tests: `test_parquet_export_b2_core.py` (float32, time axis, range units, the margin shared in three places, arbiter, and an integration test that actually runs two `export_parquet` calls side by side), the second half of `test_parquet_export_batch_ui.py` (combo, multi-file failures, float32 warning, settings input, resetting the streak, column widths, failure on range units, cancel wording, footer cache), and `test_export_queue.py` (`new_batch`).

##### 57.9.10 Export speed by expression kind (measured 2026-10-16)

`tests/parquet_analyzer/bench_export_expr.py` (writes one derived variable + the time axis. The maps are a 1-input 1-output curve `cv(x)` and a 2-input 1-output map `mp(x, y)`. 3D is the maps' surface display and is not involved in this computation).
M4 Pro, polars streaming write, single measurements (files in the page cache; the first read is slower than this). The output row count and the first, middle and last values were confirmed to match direct computation from the source data.

| Condition | bench_100m (100M rows, 4.6 GB) s | wide_300m_f32 (300M rows, 3.9 GB) s |
|---|---|---|
| Raw column (baseline) | 0.76 | 2.90 |
| Arithmetic `x*0.5+y` | 0.82 | 3.13 |
| if-expression / `where` (simple) | 0.75 / 0.76 | 3.58 / 3.61 |
| if-expression nested 3 levels / `where` with and/or | 1.54 / 1.32 | 5.50 / 5.54 |
| Curve 33 points / 2049 points | 2.03 / 4.91 | 5.33 / 6.39 |
| 2D map 9x9 / 65x65 / 513x513 (linear) | 4.45 / 7.04 / 10.12 | 11.32 / 12.07 / 13.23 |
| 2D map 65x65 (nearest) | 5.89 | 8.53 |
| Map + if / sum of two maps | 7.41 / 9.85 | 13.03 / 20.57 |

- Simple if, `where` and arithmetic are nearly the same as the baseline (1.0 to 1.25x). Maps are 4 to 13x. The larger the grid, the slower (binary search cost).
- During maps the cores used are 1.4 to 1.8 (3 to 6 for simple expressions): computation is bottlenecked on single-threaded NumPy. Running the map computation in threads per chunk is expected to speed it up (not started; do it if needed).
- Memory is about 400 to 600 MB in all conditions (`ru_maxrss` is the process's overall peak).

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
- **Data** (ch. 37 and 58, 57.9, 2026-10-16): several export jobs run at once on a dedicated export pool (at most `export_parallel_files` threads). The `WorkerHub`
  API is unchanged (one file = one job; throttling is done by the caller of `progress_sink`'s `emit`). polars' generator runs Python on polars' threads, but automatic GC is off
  (37.2), so the rule is unchanged. polars' thread pool is one per process and shares CPU with the app's loading (drawing jobs) during an export. The memory budget (57.9.5) follows
  58.5's stage 2-3 (memory limit setting) once it exists. Values for large files are taken with `bench_large_file.py`'s `parquet_export` stage (58.3).
- **Persistence** (57.9): the setting `Settings.export_parallel_files` (1 to 4, default 2; added to the settings screen). The export list window's position and size are not recorded.
- **App** (57.9): the export list (`ParquetExportPanel`) has an `objectName`. All strings go through `tr()` (en + ja). The operation log `export_parquet_cancel` gets `target` and
  `export_parquet_batch` gets `parallel`. Update app ch. 45's table.
- **Persistence** (57.8, 2026-10-16): multi-file export uses 57.7's alias resolution and the view manager's button. The existing-file confirmation uses the `ui/messages.py` component, and `last_export_folder` records the folder for batch export.
