# Analysis (statistics, FFT, XY/XYZ)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: statistics display, frequency analysis (FFT, currently hidden by a
feature flag), XY/XYZ plots, histogram (5.33 and ch. 42) and the "analysis window" framework they use.

## Requirements

#### 5.6 Frequency analysis
- Run an FFT on a selected variable and show the frequency spectrum as a separate plot.
- The FFT covers **the selected variable's current visible time range** (the X range shared by the
  time-domain plots), not the whole file (changed 2026-09-17; background in
  `detailed_specification.ja.md` 13.5.1). Two reasons: (1) the whole data never has to be expanded
  in memory; (2) the data this app expects (intermittent burst sampling — e.g. short high-rate
  measurements every few hours rather than continuous sampling) contains stretches with uneven
  sample spacing, so estimating the sampling interval of a whole-file FFT (the median of adjacent
  intervals) isn't meaningful in the first place. If the visible range is too large (over
  8,000,000 rows by default), a message asks the user to zoom in and nothing is computed. The work
  runs asynchronously so it doesn't block the UI thread.

#### 5.12 Statistics display (added 2026-09-17)
- Run with one or more variables selected in `VariablePanel`: compute count, missing-value count,
  min, max, mean, standard deviation and median over **the time range currently shown** (the X
  range shared by the time-domain plots; the same way of deciding the range as the FFT in
  [5.6](#56-frequency-analysis)) and show them as a table in a separate (non-modal) window.
  - If some plots' X axes are not linked ([plots 5.18](./plots.md)), "the time range currently
    shown" is **the linked group's range**. To compute over an unlinked plot's range, use "Stats
    for this range" in that plot's context menu (added 2026-10-02).
  - Hidden series ([plots 5.17](./plots.md)) are excluded from the default target used when
    nothing is selected in the variable panel (the plotted variables) (added 2026-10-02).
- The target is **every raw sample in the range, not the downsampled points actually drawn** (the
  range is cut from `_raw_variables`, separately from the LTTB display data of
  [data 5.4](./data.md)). Unlike FFT there is no row limit (mean, standard deviation etc. are
  meaningful even with uneven sample spacing, and it takes one numpy pass).
- With several variables selected, all of them are shown together in one table in one run
  (keeping the selection order).
- The work runs asynchronously per variable so it doesn't block the UI thread (details in
  `detailed_specification.ja.md` ch. 14).

#### 5.19 XY / XYZ plots (added 2026-10-02, user request)
- **Stage 1 — XY plot (Z shown as color)**: a separate (non-modal) window opened from "XY plot"
  on the toolbar. Several can be open at once.
  - A scatter plot of one or more variables (Y, sharing X) against a variable A (X). The target
    is **the time range currently shown** (the linked group's range, like statistics). Rows where
    any variable used is NaN/Inf are excluded, and the count is shown.
  - Point color: **single color** (default) / **by time** / **by the value of a Z variable** (with
    a color bar).
  - Display: **scatter** / **density** (a heat map of a 2D histogram) / **auto** (default; density
    when the total number of points exceeds 200,000, otherwise scatter). When scatter is forced
    above the limit, evenly thinned points are shown and that is indicated.
  - By default it doesn't follow the main range. A "Recompute" button re-reads the current range.
    Turning on the "Follow main range" toggle (default off) recomputes automatically.
  - While cursor mode is on, the point at each cursor's time is highlighted.
  - Image export uses the same annotation and metadata as the main window
    ([export 5.14](./export.md)) plus a line with the X/Y/Z variable names.
  - Not saved in views (out of scope for now).
- **Stage 2 — a rotatable 3D display** (implemented after stage 1): a 3D scatter plot of X/Y/Z
  that can be rotated and zoomed with the mouse, colored by Z or time. Uses OpenGL. Where OpenGL
  isn't available (some remote desktops and virtual environments) the 3D button is disabled with a
  reason and stage 1 remains usable. Image export re-draws the current viewpoint with matplotlib.
  This 3D display is reused for the map surface preview ([expressions 5.21](./expressions.md)).
- To stay general rather than tuned to one use, the defaults are neutral (single color, automatic
  density).
- Development feature flags `xy_plot`, `xyz_3d` ([overview 9](../overview.md)).
- The "analysis window" framework built here for separate windows (range handling, recompute,
  image export) is reused by the postponed histogram feature.
- Details in [detailed_specification ch. 25](#25-xy--xyz-plots-added-2026-10-02-user-request).

#### 5.25 Checking the sample spacing before FFT (added 2026-10-02)
- FFT assumes evenly spaced samples, but the spacing of the target range isn't checked. **Before
  turning FFT back on with its feature flag**, check the sample spacing of the target range before
  computing, and if it varies a lot, say "FFT needs evenly spaced samples" and don't compute (ask
  the user to narrow the range).
- Details in [ch. 32](#32-checking-the-sample-spacing-before-fft-added-2026-10-02).

#### 5.33 Histogram (added 2026-10-04, user request)
- A separate window (analysis window, 25.1) opened from "Histogram" in the "Graphs ▾" menu (25.5). The target is the
  **displayed time range** (the range of the same linked group as statistics and XY). By default it does not follow the
  main range; it is re-read with "Recompute" (following is a toggle, off by default). Several can be open at once. Not saved
  in views (same as XY).
- Several target variables can be **selected**, drawn overlaid with semi-transparent fill over the same bins. Raw and
  derived variables both work (e.g. look at the distribution of an error with a variable from the expression
  "measured − calculated").
- **Aggregation**:
  - **No grouping** (default): the distribution of each row's value in the range. The frequency can be switched between
    **rows** (default) and **time** (the total time each row's value lasted until the next row; "how many hours at which kW
    level").
  - **With grouping**: time is divided into groups of a fixed length, each group is summarized into one value, and the
    distribution of those values is drawn (e.g. the distribution of daily usage, or of 10-minute averages). The frequency is
    the number of groups (e.g. the number of days).
    - Group length: 1 min, 10 min, 1 hour, 1 day, or a free entry (minutes, hours, days).
    - Group origin: a time of day can be given (default 0:00; e.g. a day starting at 6 a.m.). Groups are counted in local
      time, the same as the screen's time axis.
    - Summary: **total**, **integral** (value × time; for instantaneous power in kW it gives kWh per day; the time unit is
      chosen from seconds, minutes and hours, default hours), **mean**, **max** and **min**. For usage per interval (kWh) data
      use the total; for instantaneous values (kW) use the integral; both give the total usage per day (2026-10-04, the user
      decided to include both). The on-screen explanation states that the total depends on the sample period.
    - **Groups with gaps** (data gaps, and the first and last groups that start or end inside the range): used as they are by
      default, and the number of groups with gaps is shown. Checking "Exclude groups with gaps" removes them (2026-10-04,
      user's decision).
- Bins: **auto** (default, Freedman–Diaconis) or **specify the number of bins**. Vertical axis: **frequency** or
  **percentage (%)**.
- For each variable, a vertical line at the mean (dashed) and at the median (dotted); the legend shows count, mean and
  standard deviation. The count of excluded missing values (NaN/Inf) is shown in the status display.
- Computed without holding all data in memory even for large files (the range is split, read in pieces and added up).
- Image export adds to the same notes and metadata as the main screen rows for the variables, the aggregation and the bin
  settings.
- Development feature flag `histogram` (ON by default since 2026-10-06). Candidates for later versions (distribution by time
  of day, load duration curve, narrowing by time of day, 2-variable 2D/3D, comparison of periods, CSV of the bin table, etc.)
  are in dev notes. The two-stage aggregation and the export of values per group are in 5.37.
- Details in [ch. 42](#42-histogram-added-2026-10-04-user-request).

#### 5.37 Two-stage aggregation of the histogram and export of values per group (added 2026-10-06, user request)
- **Two-stage aggregation**: with grouping, a short window (e.g. 3 min) first produces a first-stage summary (e.g. mean),
  which is then summarized again over a long group (e.g. 1 day) into a second-stage summary (e.g. max) before the
  distribution is drawn. E.g. the per-day distribution of "the largest 3-minute mean within the day", or the maximum of
  30-minute means (demand). For max and min, the time at which that value occurred is also kept.
- **Export of values per group**: the grouped result is saved as a table with one row per group, as **Parquet** (or CSV).
  The columns are the group's start and end times, and per variable the value and whether the group has gaps (for max and
  min, also the time of the extreme). It is used, for example, to subtract "consumption − regeneration = net" per day in
  another application. (2026-10-06, user's decision: net is not made in this app; consumption and regeneration are each
  output separately as derived variables with conditional branches: `where(P > 0, P, 0)` and `where(P < 0, -P, 0)`.)
- A chart of the values per group over time (a bar chart with dates on the horizontal axis) is not included this time
  (dev notes).
- Details in [42.8](#428-two-stage-aggregation-and-export-of-values-per-group-added-2026-10-06-user-request).

#### 5.35 Making a map from measurements (map display and export in XY) (added 2026-10-06, user request, high priority)
- From the measured points shown in the XY / 3D window (25.5), **values are aggregated per grid cell to make a map (or a
  curve)**, which can be checked on screen and then **registered in the map manager** and/or **saved as CSV** (both, or
  either one, can be chosen). A registered map can be used in expressions as `name(x, y)` (ch. 27).
  - 2D, X, one Y and color = Z → map `z(x, y)`. 2D, X and Y (no Z) → curve `y(x)`. 3D (X, Y, Z) → map.
  - The target is the time range the window computes (25.1; the cursor and follow work as before).
- **Add "map" to the XY display**: in addition to scatter and density, the aggregated value of each grid cell is shown as
  color (2D, heat map) or as a surface (3D). Measured points can be overlaid. Export writes out the map currently shown as is.
  - Grid: for X and for Y each, choose an **interval** (e.g. steps of 500 rpm) or a **number of divisions**. The initial range
    is the data's minimum to maximum, rounded to round values.
  - Aggregation: **mean** (default), **median**, **max**, **min**, **count**. Cells with fewer than N points (default 1) are
    "no data".
  - Cells with no data: choose **fill by interpolation** (default; smooth from the surrounding measurements), **fill with the
    nearest value**, or **leave empty**. Filled cells can be told apart from measured cells on screen. A map with cells left
    empty can also be registered; when used in an expression, the places that fall on empty cells are missing values (NaN)
    (27.10).
- **Overlay registered maps and curves** (color-coded, with a legend): they can be compared with the measured points or with
  other maps (e.g. two maps that were made, or the original design map against the measurements).
- Even for large files, computed without holding all data in memory (split and added up while reading, as with the XY density).
- Development feature flag `map_from_xy`. Candidates for later versions (saving the XY window settings into a view, etc.) are
  in dev notes.
- Details in [ch. 44](#44-making-a-map-from-measurements-map-display-and-export-in-xy-added-2026-10-06-user-request).

#### 5.39 Excluding outlying points of a map by clicking them, and leaving missing cells empty (added 2026-10-08, user request)
- When making a map from measurements (5.35), a few points far away from the others (outliers) change the shape of the surface
  (2026-10-07, the user's real-machine check). **In the 2D display of XY, measured points can be clicked or boxed to exclude
  them from the map computation.**
- The unit of exclusion is **a measured point (a row)**. Selection is possible only in the **2D display of XY (map and
  curve)**; it is not offered in 3D (the excluded result is also reflected in the 3D map).
- Excluded points stay shown as a **gray ×**. Restore by clicking the × again, or with "Restore all".
- The excluded result is only **reflected in the maps made and exported**, and is not saved in views or the like (closing the
  window discards it).
- In addition, a choice is added to leave cells with fewer than the minimum point count N (44.2) **empty**, instead of filling
  them with the fill method.
- Details in [44.13](#4413-excluding-outlying-points-and-leaving-missing-cells-empty-added-2026-10-08-dev-notes-item-22).
- 2026-10-13: because the operation was hard to understand, the UI entry is hidden behind the feature flag
  `map_exclude_points` (default OFF) (44.13 (8), user's decision).
- The decisions above are from 2026-10-07 (agreed by the user; decisions recorded under "map" in dev notes).

#### 5.48 Display kinds of the histogram (duration curve, trend, by time of day), narrowing by time window, and two-variable frequency (added 2026-10-14, user request)
- The purpose is the analysis of electric power usage (same as 5.33). From the "next version" items of dev notes
  "Histogram (candidates for later versions)", the user chose five (2026-10-14): distribution by time of day, load duration
  curve, narrowing by time of day, trend of values per group, and frequency of a two-variable combination.
- **Narrowing by time of day**: in the histogram window, the target can be narrowed to a **range of times of day** (e.g.
  9:00–17:00; ranges across midnight, e.g. 22:00–6:00, are allowed) and to **days of the week** (all, weekdays, weekends, or any
  chosen day) (2026-10-14, user's decision). Time outside the narrowing is treated like a gap, i.e. values are not regarded as
  continuing across it. It applies to all display kinds and aggregations (no grouping, with grouping, two-stage). Public
  holidays are not handled.
- **Display kind** can be chosen (switched inside the histogram window):
  - **Distribution** (the current display; default).
  - **Duration curve** (load duration curve): the values sorted from largest to smallest, with the horizontal axis as "the time
    the value was at or above this level" (with grouping, the number of groups, i.e. days, etc.). It reads as "how many hours
    at or above ○ kW".
  - **Trend** (only with grouping): the value per group (e.g. daily usage) drawn as a bar chart with the group times on the
    horizontal axis. Groups with gaps are drawn so they can be told apart.
  - **By time of day**: the horizontal axis is the time of day (0–24 h; with a group length of 1 h, 30 min, etc.), and either
    the **mean** of the values at that time (time-weighted) or the **integral** (usage) is drawn; one of the two can be chosen
    (2026-10-14, user's decision). It reads as "around what time we use more".
- **Frequency of a two-variable combination** (rpm × torque): add "**time**" (the total time spent in each cell) to the
  aggregation of the XY map display (5.35, ch. 44). As with the current "count", it is shown as a 2D heat map or 3D surface,
  and can be saved as CSV or registered as a map.
- Implemented in three stages (stage 1 = narrowing by time window, duration curve, trend; stage 2 = by time of day; stage 3 =
  two-variable frequency). Comparison across periods, two-variable frequency after aggregation per group, and narrowing of the
  XY by time window are not included this time (dev notes).
- Details in [42.10–42.13](#4210-narrowing-by-time-window-added-2026-10-14-stage-1) and
  [44.14](#4414-frequency-of-two-variables-time-as-a-map-aggregate-added-2026-10-14-stage-3).

## Design

### 14. Statistics display (added 2026-09-17)

See `specification.ja.md` 5.12. For the selected variables, count, missing-value count, min, max,
mean, standard deviation and median are computed from every raw sample in the time range currently
shown and shown in a separate window. (2026-10-02) "The time range currently shown" is the linked
group's range; from an unlinked plot, "Stats for this range" (24.2). Hidden series are excluded
from the default target (23.1).

- **The range is decided by `MainWindow._visible_row_range()`, implemented for FFT in
  13.5.1, shared as is** (when FFT was implemented the logic was inlined in
  `_on_fft_requested`; it was extracted into a method when this feature was added). Like FFT, if no
  time-domain plot has any series (e.g. just after opening), it falls back to the whole file.
- **Unlike FFT there is no row limit.** `basic_stats(y)` in `core/analysis.py` just computes
  `min`/`max`/`mean`/`std`/`median` NaN/Inf-safely in one numpy pass (only `median` needs a sort
  internally), and there is no constraint like FFT's "the result loses its meaning with uneven
  sample spacing". However, at the scale of 91,980,000 rows (`sensor_log_4y_4gb.parquet`) the
  `median` sort was measured at 0.6–0.7 s, so it runs asynchronously with `BackgroundWorker` in
  `core/background_worker.py` (`QRunnable`, `setAutoDelete(False)` + the caller-holds-a-strong-
  reference pattern; shared by the downsampling recompute, FFT and statistics).
- With several variables selected, one `BackgroundWorker` is started per variable
  (`MainWindow._on_stats_requested`), and `StatsDialog` is built and shown once after all have
  finished (`_on_stats_finished` checks a `remaining` counter and builds the dialog only when the
  last one finishes). Results are shown in the selection order (`variable_panel.selectedItems()`),
  not in the order the workers finished.
- **Choosing the target: an explicit selection in the panel wins; otherwise fall back to the
  variables currently plotted** (changed 2026-09-19, user request — the original implementation
  always required `variable_panel.selectedItems()` to be non-empty even for variables already
  shown on a plot, which felt like doing it twice). In `MainWindow._on_stats_requested`, if
  `variable_panel.selectedItems()` is empty, it falls back to `MainWindow._plotted_variable_names()`
  (every variable currently overlaid on any **time-domain plot** — FFT plots' series are already
  FFT output, not raw/derived variables, so they are excluded, the same exclusion
  `_visible_row_range` already makes). Duplicates across plots are removed and the plot/series
  order is kept. An explicit selection in the panel always wins, so the old usage of checking a
  variable that isn't plotted still works. Each candidate name is resolved beforehand with
  `variable_window()` (rather than lazily inside the per-worker loop), and one raising
  `ExpressionError`/`KeyError` is skipped without an error dialog (logged only) — a panel selection
  only ever contains known variable names, so realistically this can only happen via
  `_plotted_variable_names()`, but it follows the same idea as the `load_view` fix earlier in this
  document: "one bad item doesn't take the rest down". If none can be resolved, it doesn't proceed
  with 0 rows and shows an information dialog. Regression tests:
  `test_stats_falls_back_to_plotted_variables_when_none_selected_in_panel`,
  `test_stats_prefers_explicit_panel_selection_over_plotted_variables`
  (`tests/parquet_analyzer/test_stats.py`).
- `StatsDialog` (`ui/stats_dialog.py`) is a `QDialog` opened non-modally with `show()` rather than
  `exec()` (so results can be compared while working with the main window — "in a separate
  window", as requested). It has `MainWindow` as its parent (`parent=self`) and `WA_DeleteOnClose`,
  so it is destroyed when closed and tied to `MainWindow`'s lifetime while open.
- The curves drawn by downsampling ([data ch. 10](./data.md)) are points thinned for display;
  statistics cut the same row range from `_raw_variables` / derived variables separately each time
  (aggregating the drawn points would be statistically meaningless; a test confirms that the
  number of points after LTTB thinning differs from the number of samples in the statistics,
  `tests/parquet_analyzer/test_stats.py`).

### 25. XY / XYZ plots (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.19. Stage 1 (XY) is implemented and reviewed first; stage 2
(3D) is implemented afterwards as separate work. Feature flags `xy_plot`, `xyz_3d` (ch. 21).

#### 25.1 Analysis window framework (`ui/analysis_window.py`, new)

The shared part, also used by the postponed histogram.

- `AnalysisWindow(QMainWindow)` (non-modal, parented to `MainWindow`, destroyed on close with
  `WA_DeleteOnClose`). `MainWindow` keeps the open analysis windows in `self._analysis_windows`
  (a list) and removes them when they close.
- Shared UI: a settings panel at the top (the subclass builds its content), a display area in the
  middle (pyqtgraph), a status line at the bottom (target range, count, excluded count), and in the
  toolbar "Recompute", "Follow main range" (check, default off) and "Save image".
- Getting the range: a function that obtains `(row_start, row_end, x_lo, x_hi)` from `MainWindow`
  is passed in (`_visible_row_range()` = the linked group, 24.2). With follow on, it connects to
  `PlotGridWidget.rangeChanged` and recomputes after a 300 ms debounce.
- Computation runs in `BackgroundWorker` (10.2), and a generation counter drops stale results (the
  same rule as `TimePlotWidget`'s downsampling; a strong reference to the worker is kept). While
  computing, the status line says "Computing…".
- Data is fetched with `MainWindow.variable_window(name, row_start, row_end)` (13.5.1). (Changed
  in review) The worker never touches `MainWindow`'s state: when starting a computation (on the UI
  thread) each variable name is resolved to a `Column` object and passed in, and the worker only
  calls `col.window(row range)`. (The first version looked names up at read time in the worker, so
  switching the time-axis column or reopening the file mid-run read the new state with the old row
  range.)
- (Added in review) Computations can be cancelled: when the window is closed or a newer
  computation replaces it, it checks at every chunk boundary of the chunked read and stops midway
  (previously quitting the app waited for a running computation to finish). A cancelled result is
  dropped and not treated as a failure.
- Analysis windows don't join `_busy_sources` (the main window's drawing indicator); they show
  "Computing…" in their own window.
- Changing a setting (X/Y/color/display) recomputes over the previous range. The range is re-read
  only on "Recompute" and when following. If X or Y isn't selected, the status line shows a hint
  rather than an error. When the file is closed or reopened (`load_parquet`) or the time-axis
  column changes, `MainWindow` closes every open analysis window (the meaning of the variables
  changes).
- (Changed in review) The "range" in the image annotation is **the range the analysis window
  computed**, not the main window's current range (`_image_stamp(range_override=...)`; in the
  first version, moving the main window made the annotation disagree with the image content).
- Image export: the subclass implements `build_export_figure(stamp_lines, size_px)`, and saving
  uses `image_export.save_figure` (19.2) in common. The annotation is the content of
  `MainWindow._image_stamp()` plus one line from the subclass. CJK font support (`_font_context`)
  is shared too. The output folder shares `settings.last_export_folder`.

#### 25.2 XY plot (stage 1, `ui/xy_plot_window.py`, new)

- "XY plot" on the toolbar (shortcut configurable, `xy_plot`, none by default). No file open →
  `error.open_file_first`. When opened, if two or more variables are selected in the variable
  panel the first becomes X and the rest Y; otherwise empty.
- Settings panel: X (combo), Y (multi-select list), color (single / time / Z variable) + Z (combo,
  enabled only when color = Z), display (auto / scatter / density). The candidate variables are
  `MainWindow`'s `_raw_variables` + derived variables (excluding the time-axis column). Changing a
  setting recomputes.
- Computation (in the worker):
  1. Get X, each Y and (Z) within the range. A row where any variable used is non-finite is
     excluded (judged per Y; with several Ys the valid rows may differ per Y). Count the excluded.
  2. Display mode: in auto, density if the total valid points over all Ys > `XY_SCATTER_LIMIT`
     (200,000), otherwise scatter. If scatter is forced above the limit, each Y is thinned with an
     even stride so the total stays within the limit, and "Thinned (n / N points)" is shown in the
     status line.
  3. Density: one 2D histogram of all Ys together (`np.histogram2d`, 256×256 bins, range from the
     valid points' min/max). To keep memory down on large ranges, the row range is fetched and
     accumulated in pieces of `XY_CHUNK_ROWS` (5,000,000 rows) (the bin range is found first from
     each column's valid min/max with the same split — two passes). With color = Z, each bin shows
     the **mean** of Z (Z's sum and the count are accumulated in the same bins).
- Display (pyqtgraph `PlotWidget`):
  - (Added in review) With color = time, the color bar ticks are shown **in the same local-time
    format as the on-screen time axis**, both on screen and in image export (in the first version
    no ticks appeared on screen and the image showed epoch seconds as "+1.767e9"). The excluded
    missing-value count is shown per Y. Legend markers are made large enough to read.
  - Scatter: `ScatterPlotItem` (`pxMode=True`, size 3, no pen). Each Y in the existing
    `_COLOR_CYCLE` color, with a legend. With color = time/Z each point is colored with
    `pg.colormap.get("viridis")` and a `ColorBarItem` is shown (only for a single Y; with several
    Ys, color = time/Z can't be chosen — disabled in the settings panel).
  - Density: `ImageItem` (counts colored via `log1p`, viridis) + `ColorBarItem`. The axes are the
    X/Y values.
  - Axis labels show the variable names.
- Cursor linking: while cursor mode is on and there are cursors, the point of the row nearest each
  cursor's time is highlighted (`ScatterPlotItem`, a larger circle with a white edge). Updated on
  `PlotGridWidget`'s cursor changes (add, move, remove) (a new signal `cursorsChanged` is added to
  `PlotGridWidget`, carrying the time-domain part of `_cursor_positions`). Cursors out of range are
  ignored. In density display the points are overlaid too.
- Image export (`build_export_figure`): matplotlib `scatter` (`s=2`, `rasterized=True` — so
  SVG/PDF don't get hundreds of thousands of vector points) or `pcolormesh` + `colorbar`. Legend,
  axis labels, and one line `X: <name>  Y: <name, ...>  [Z: <name>]` added to the annotation.
- Operation log: `xy_plot_open`, `xy_plot_compute` (variables, row range, mode).
- Not saved in views.

#### 25.3 3D display (stage 2, `ui/xyz_3d_window.py`, new)

- Dependency: `pyqtgraph.opengl` (needs `PyOpenGL`) is **added to the main dependencies**
  (`PyOpenGL>=3.1.7`, so it is bundled into the exe). `OpenGL_accelerate` is not added.
  `pyqtgraph.opengl` is not imported on the core startup path, only when the 3D display is first
  used (the app starts even where PyOpenGL is missing).
- **Bundling check results** (2026-10-02, macOS arm64, PySide6 6.11.2, pyqtgraph 0.14.0, PyOpenGL
  3.1.10, CPython 3.12):
  - Normal run: a `GLViewWidget` can show a 500k-point `GLScatterPlotItem`, a 100×100
    `GLSurfacePlotItem`, `GLGridItem` and `GLTextItem`; the drawing was checked in an image.
  - PyInstaller `--onedir`: **no hidden imports, extra data or special options needed**
    (pyinstaller-hooks-contrib's `hook-OpenGL.py`/`hook-pyqtgraph.py` are enough; the only warning
    is that the optional `OpenGL_accelerate` is missing). The frozen app was checked to start and
    draw the same in an image. The size grows by about 1 MB (originally about 121 MB because the
    whole PySide6 is bundled).
  - This was verified with a minimal app. Check again when freezing the app itself (the app's
    PyInstaller configuration and scripts are not in the repository yet).
  - **Windows is unverified**: on remote desktops, virtual environments and old drivers only
    OpenGL 1.1 may be available, and creating the context or compiling shaders can fail.
    `QT_OPENGL=angle` doesn't match PyOpenGL's desktop GL calls, so leave the default (desktop GL).
    Before distributing, check the frozen app on a real Windows machine and over remote desktop.
- Availability: `probe_gl3d() -> (ok, reason)`, judged the first time the 3D button is pressed
  after startup:
  1. `import OpenGL` and `import pyqtgraph.opengl` (catching `Exception`; PyOpenGL can raise
     things other than ImportError when loading platform libraries).
  2. Create a `GLViewWidget`, `show()` → `processEvents()` (the context is made after showing),
     confirm that `w.context()` is valid (`isValid()`) and `grabFramebuffer()` isn't empty, then
     close it.
  - **Pitfall (confirmed)**: when OpenGL isn't available, `GLViewWidget()` **doesn't raise**. Qt
    prints a warning to standard error (`QOpenGLWidget: Failed to create context`, etc.) and just
    draws nothing, so wrapping the construction in `try` doesn't detect it. Check 2. above is
    needed.
  - On failure, log the reason, show a message and disable the button (reason in the tooltip;
    disabled for the rest of the session).
  - In headless tests (`QT_QPA_PLATFORM=offscreen`) the 3D display can't draw (the context can't
    be made, as above). Tests cover data preparation, thinning, the path where `probe_gl3d()`
    returns `False`, and disabling; drawing is checked on a real display.
- **Pitfalls with several 3D displays (checked on the real machine in review, 2026-10-03)**:
  - pyqtgraph caches shader programs per class (`GLScatterPlotItem`, etc.). A `GLViewWidget` in
    another window has a separate context, so in the second and later 3D displays `glUseProgram`
    fails with an invalid-value error and nothing but text (`GLTextItem`) is drawn.
    → Set `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)` **before** creating the
    `QApplication` (a small function in `__main__`, checked in a test).
  - On a white background, black lines and points drawn with additive blending
    (`GLLinePlotItem`'s default `glOptions='additive'`) disappear. Draw lines and points with
    `translucent` (or `opaque`).
  - pyqtgraph's `fov` is the horizontal field of view, so in a wide display area (the map manager)
    the top and bottom of the [0,1]³ cube get cut off. The camera distance is decided from the
    display area's aspect ratio (in `resizeEvent`, `distance = base × max(1, width/height)`).
  - When opened with fewer than three variables chosen, it still computes with the combos' initial
    values (the first three variables) (no empty window).
  - `probe_gl3d` always closes the probe widget even if an exception occurs after `show()`
    (`try/finally`).
- Built as an analysis window (25.1). Settings: X/Y/Z (combos, one each), color (Z / time).
  `GLScatterPlotItem`, each axis's range normalized to [0,1] for display (readable even for
  variables on different scales), axis ticks showing the real values with `GLTextItem`, and a
  `GLGridItem` as the floor. Above `XYZ_POINT_LIMIT` (500,000) points, thin with an even stride
  and show it in the status line.
- (Added 2026-10-03, user request) **Back to home**: after rotating and zooming, the initial
  viewpoint (`reset_camera`, including the distance fitted to the aspect ratio) can be restored —
  by a "Home" button laid over the top left of the 3D widget (`Xyz3DView`), by double-clicking on
  the display, or by the `Home` key. It is a widget feature, so it works the same in the map
  manager's 3D surface. Operation log `xyz_3d_home`.
- (Added 2026-10-03, user request. These are also `Xyz3DView` features, so they work in both the 3D
  display and the map's 3D surface.)
  - **Viewpoint presets**: a row of buttons at the top left, "Home", "XY", "YZ", "ZX". XY = from
    directly above (X to the right, Y up), YZ = from the positive X side (Y to the right, Z up),
    ZX = facing from the negative Y side (X to the right, Z up). Straight-side and straight-down
    views are set with `elevation`/`azimuth` (e.g. XY is `elevation=90`), at the home distance
    fitted to the aspect ratio. After implementing, check the orientation (left/right, up/down of
    the axes) in an image. Operation log `xyz_3d_view` (`preset`).
  - **The value of the point under the mouse**: find the point nearest the mouse on screen (the
    displayed points for a scatter, the grid points for a surface); if it is within 12 px,
    highlight it (a slightly larger point in a striking color) and show its real values
    "X: … Y: … Z: …" (plus the time if colored by time) at the bottom left. If nothing is within
    12 px, clear it.
    - How it searches: transform the displayed points' normalized coordinates (`scene`) to screen
      coordinates all at once with the `GLViewWidget`'s projection × view matrix (numpy, once per
      mouse move) and pick the nearest. The points are already thinned to within
      `XYZ_POINT_LIMIT`, so it isn't heavy. No search while dragging to rotate (resumes on
      release).
    - A surface grid point's values are the map table's values themselves (no interpolation).
    - Not written to the operation log (it would be every mouse move).
  - **Axes**: the three axis lines (from the origin), ticks and axis names are shown so they are
    readable from every preset viewpoint. On a map surface the axis names are the map's axis names
    (27.7; "X", "Y", "Z" if none). (Added in implementation) In a preset viewpoint, the ticks of the
    axis pointing into the screen (Z for XY, etc.) pile up in one place and can't be read, so they
    are hidden (the axis name stays; values are available by hovering). Shown again after rotating
    by dragging or going home.
- Image export: matplotlib `mplot3d` `scatter` (`rasterized=True`) drawn from the same viewpoint
  using `GLViewWidget.opts`' `elevation`/`azimuth`.
- So this 3D widget (`Xyz3DView(QWidget)`) can also be used for ch. 27's map preview (a surface
  with `GLSurfacePlotItem`), its data-setting API is split between scatter and surface
  (`set_scatter(x, y, z, colors)`, `set_surface(x_axis, y_axis, z_grid)`).

#### 25.5 Merging XY and 3D, a menu of graph kinds (added 2026-10-03, from the user's real-machine check)

Background (the user's real-machine check): time wasn't among the candidates for the 3D display's
axes, the viewpoint buttons' (Home, XY, YZ, ZX) text didn't appear on the real machine (text of
widgets laid over the GL view isn't drawn), and the axis selection was stacked vertically and took
space. In addition, having separate entry points for the XY plot and the 3D display felt odd.
Decision (2026-10-03, the recommended option): **merge 3D into the XY plot window**, and gather the
entry points into **a menu of graph kinds**.

- **Entry point**: put "Graphs ▾" on the toolbar (`QToolButton` + menu, with an `objectName`),
  listing "XY / 3D" (`xy_plot`) and "FFT" (only while the `fft` flag is on). The future histogram
  (11) is added here too. The standalone "XY plot" and "3D" toolbar buttons go away. The
  per-item shortcut settings (`xy_plot`, etc.) keep working. The old `xyz_3d` shortcut is removed
  from the settings (ignored when read). The time graphs (the main plots) stay in the main window
  as now.
- **The merged window** (extending `XYPlotWindow`; `Xyz3DWindow` goes away):
  - Settings in **one horizontal row**: X (combo), Y (multi-select in 2D, one in 3D), Z (combo;
    for color = Z in 2D, the third axis in 3D), color (single/time/Z), a "2D / 3D" display switch,
    and the 2D display (auto/scatter/density). Wraps when the width runs out (`FlowLayout` or
    similar).
  - **The time-axis column is among the axis candidates** (both 2D and 3D). When time is chosen as
    an axis, that axis's ticks, hover values and image export use the same local-time format as the
    on-screen time axis.
  - Switching 2D⇔3D carries over the X, Y, Z selections (the first Y when switching to 3D with
    several). 3D can be chosen only when the `xyz_3d` flag is on and OpenGL is available
    (`probe_gl3d`); otherwise the switch is disabled with the reason in its tooltip.
  - In 3D, the viewpoint buttons (Home, XY, YZ, ZX) are **not laid over the GL view** but placed in
    a normal row of buttons above it. The value of the point under the mouse also goes to a normal
    label below the view (laid-over widgets don't draw their text on a real display). The map
    manager's 3D surface uses the same widget (`Xyz3DView`), so it takes the same form.
  - Computation, thinning, image export, cursor linking and following are as in 25.2 for 2D and
    25.3 for 3D.
- **Map axis names**: pass 27.7's axis names to the axes of the map manager's 3D surface
  (`set_surface(axis_names=...)`).
- Operation log: `xy_plot_open` gets `mode` (`2d`/`3d`); switching is `xy_plot_mode`. `xyz_3d_open`
  goes away.
- Tests: the menu items vs. the flags, time can be chosen as an axis (2D and 3D), switching 2D⇔3D
  carries over the selections, the 3D switch is disabled without OpenGL, the viewpoint buttons and
  the value label are not children of the GL view, settings in one row, reading a settings file
  with the old `xyz_3d` shortcut doesn't break, map 3D surfaces show axis names. On the real machine
  (this Mac, with a display), check the 3D buttons' text and the time-axis ticks in images.

#### 25.4 Test plan

- Analysis window: with follow off, changing the main range doesn't recompute; "Recompute" uses the
  new range; with follow on it recomputes (after the debounce). A stale result arriving later is
  dropped. Reopening the file closes the window. Closing the window with a worker in flight doesn't
  crash.
- XY computation (testing the compute function directly, not through the window): rows containing
  NaN are excluded and the counts match; the automatic mode switch (either side of the limit); the
  number of thinned points when scatter is forced; the density's total count = the number of valid
  points; chunking (a small `XY_CHUNK_ROWS` patched in) gives the same histogram as computing in one
  go; the bin means for color = Z are correct (checked with known values); an empty range doesn't
  raise.
- With the feature test data (`feature_test.parquet`), `X=sine, Y=cosine` → the points lie on a
  circle of radius 1 (`x²+y²≈1` at every point).
- Cursor linking: placing a cursor makes the highlight appear at (x, y) for that time, moving it
  follows, removing it removes it.
- Image export: a file is produced for both scatter and density, and the annotation has the
  variable-name line. Check one by eye.
- Flags: with `xy_plot` disabled, no button on the toolbar.
- Stage 2: disabling when OpenGL isn't available (reproduce an `import` failure with a
  monkeypatch), thinning, image export from a changed viewpoint (`elev`/`azim` are passed). OpenGL
  is likely unavailable headless, so the display itself needs checking on a real machine (state in
  the report if it couldn't be checked).

### 32. Checking the sample spacing before FFT (added 2026-10-02)

Requirement: 5.25. Background: `analysis.fft` only decides the sampling interval with
`dt = median(diff(x))` and doesn't look at the variation (external review 1 #4). FFT is
currently hidden by a feature flag (ch. 21).

- Add `sampling_report(x) -> SamplingReport` to `core/analysis.py` (`dt_median`, `dt_min`,
  `dt_max`, `max_rel_deviation = max(|dt - dt_median|) / dt_median`, `gap_count` (the number of
  `dt > 1.5 * dt_median`), `uniform` (`max_rel_deviation <= FFT_JITTER_TOLERANCE` (default 0.01)
  and `gap_count == 0`)).
- `_on_fft_requested`: compute `sampling_report` before computing (before handing to the worker);
  if not `uniform`, show a warning (`fft.not_uniform`, with `dt_median`, the largest deviation and
  the number of gaps) and don't compute. Also don't compute when the time-axis order check
  (ch. 29) finds it not ascending.
- The tolerance is a constant in `analysis.py` (not shown in the settings).
- Tests: evenly spaced (0.5% jitter is fine), 5% jitter, a gap in the middle, a time axis not
  ascending, two points or fewer.
- **Turn FFT back on with its feature flag only after this chapter is implemented and FFT has been
  debugged (the remaining task in 13.5).**

#### 32.1 Handling abnormal input (added 2026-10-03, from strengthening the tests)

| Input | Handling |
|---|---|
| `fft(x, y)` with a sampling interval that is 0, negative or non-finite (duplicate or reversed times) | `ValueError` ("sampling interval is not positive"). From the screen, `sampling_report` refuses first so it never gets there, but as a component it never divides by zero |
| Statistics with values near the `float` maximum (mean and standard deviation become `inf`) | Left as is (doesn't happen with real data; fixing it would need rescaled computation, which isn't worth it) |
| XY density with a range of ±1.7e308 (the bin width overflows) | Stays an exception; reported through the background-job failure path (data ch. 28) |
| Failure, cancellation or a stale generation of background work for statistics, FFT, XY/3D | Reports success or failure exactly once (25.1, data ch. 37). Pinned by tests |

Test plan: `test_analysis_errors.py` (degenerate input to `core/analysis.py`, the analysis window
framework, the failure paths of statistics, FFT, XY and 3D).

### 42. Histogram (added 2026-10-04, user request)

Requirement: 5.33. For the use cases (e.g. distribution of electric power usage), see dev notes. Uses the analysis window
framework (25.1), the "Graphs ▾" menu (25.5) and `WorkerHub` (data ch. 37) as they are.

#### 42.1 Structure

- The computation is in `core/histogram.py` (new; pure functions without Qt, to make them easy to test); the screen is
  `HistogramWindow(AnalysisWindow)` in `ui/histogram_window.py` (new).
- Entry point: "Histogram" in the "Graphs ▾" menu (only when the feature flag `histogram` is on; default OFF until after
  review and the user's real-machine check; turned ON 2026-10-06). Shortcut setting `histogram` (no default). If no file is open,
  `error.open_file_first`. On opening, the variables selected in the variable panel (none if nothing is selected) are the target.
- Operation log: `histogram_open` (on opening), `histogram_settings` (when a setting changes; the item changed and its value).
  The computation uses the common `job="analysis"` of the analysis windows. i18n in English and Japanese. Widgets have an `objectName`.

#### 42.2 Settings panel (one row, `FlowLayout` that wraps when the width is not enough, as in 25.5)

| Item | Choices | Default |
|---|---|---|
| Variables | multiple selection (candidates are `_raw_variables` plus derived variables, excluding the time-axis column) | the selection on opening |
| Aggregation | no grouping / with grouping | no grouping |
| Frequency (only with no grouping) | rows / time | rows |
| Group length (only with grouping) | 1 min, 10 min, 1 hour, 1 day, or free entry (number + unit min/h/d) | 1 day |
| Origin (only with grouping) | time of day (`HH:MM`) | 00:00 |
| Summary (only with grouping) | total, integral, mean, max, min | total |
| Time unit of the integral (only for integral) | seconds, minutes, hours | hours |
| Exclude groups with gaps (only with grouping) | checkbox | OFF |
| Interval regarded as a gap | auto / specified seconds | auto |
| Bins | auto / number of bins (2–1000) | auto |
| Vertical axis | frequency / percentage (%) | frequency |

- Constraints on the group length: up to one day, it must **divide a day evenly** (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30 min,
  1, 2, 3, 4, 6, 8, 12 hours, etc.); over one day, it must be a **whole number of days**. Otherwise the reason is shown next to
  the input field and nothing is computed.
- When a setting changes, the computation is redone over the previous range (as in 25.1). If no variable is selected, the status
  display says so.
- When "Total" is chosen, the tooltip next to the summary says: "The total of instantaneous values (kW etc.) changes with the sample
  interval. For usage, use the integral."

#### 42.3 Computation (`core/histogram.py`, inside the worker)

Common:
- The inputs are the `Column` of each variable and the `Column` of the time axis, resolved at the start of the computation on the UI
  thread (as in 25.1, the worker does not touch `MainWindow`'s state). The rows of the range are read in chunks of
  `HIST_CHUNK_ROWS` (1,000,000 rows; changed from 4,000,000 on 2026-10-07, see 42.9) with `window` and added up. Grouping and time
  weights need the time of the next row, so each chunk is read one row further. Cancellation is checked between the chunks of the
  split reads (25.1).
- The time axis must be ascending within the range (data ch. 29). Rows whose time is not ascending or non-finite have their values
  excluded and are counted (because the time cannot be used). If the time axis of the range is not ascending (`check_time_order`),
  nothing is computed and the status display gives the reason. However, **with no grouping and frequency = rows, the time axis is not
  read** (the distribution of values does not depend on time, so this display is possible even when the time axis is broken; for this
  reason the count of excluded rows can differ from frequency = time).
- Conditions that cannot be computed (not ascending, too many groups, invalid group length, no variables) are not treated as errors:
  the job returns "why it was not computed" once as its result (the failure path is only for real exceptions). In these cases, and when
  no variable is selected, **the previous display and result are cleared** (so an old graph does not remain and can be saved as an
  image; found in review). If a selected derived variable is deleted and the selection changes, the computation is redone.
- **Time of each row** (weight) `w_i = x_{i+1} - x_i` (the row's value is regarded as continuing until the next row; the last row of the
  range has `w = 0`). If `w_i > gap`, then `w_i = 0` and it is counted as a gap. **Automatic gap value** = 5 times the median of the
  adjacent intervals (the median is approximated as the median of the per-chunk medians). This is so that a value is not regarded as
  continuing across the space between measurements in intermittently measured data. **The number of gaps is counted once from the time
  axis** (it is a property of the time axis, so it is not counted per variable; this avoids the count changing per variable where values
  are NaN; found in review).
- The time-axis chunks are read once and reused for all variables (not re-read per variable or per pass).
- Rows with non-finite values are excluded (and counted). Their time is not used either.

No grouping:
- Two passes: (1) the finite min and max and the count of each variable (the bin range; a common range for all variables so that they can
  be compared overlaid), (2) add into a fine histogram (`HIST_FINE_BINS` = 65,536 bins, for both frequency and time).
- Automatic bins: Freedman–Diaconis (width `2·IQR·n^(-1/3)`). The quartiles are taken from the cumulative counts of the fine histogram of
  pass (2) (an approximation within one fine bin width is sufficient; if the IQR is 1.5 fine bins or less, it cannot be resolved and is
  taken as 0). When there are several variables, **the FD width is computed for each variable from its own range and count, and the
  largest of them** (the coarsest width) is used for the common range. (Taking the smallest would split the common range into hundreds of
  bars when just one variable has a narrow width, so the other variables look like lines; found in review.) The number of bins is kept
  within 2–1000 (if the IQR is 0, `sqrt(n)` bins, capped at 1000).
- The display bins are **made by merging fine bins** (the display bin edges are aligned to the fine bin edges, so no third pass is needed).
  The same applies when the number of bins is specified (the edges are rounded to the fine bin width; so the widths are slightly uneven, by
  at most one fine bin). The edges are computed as `lo·(1−f) + hi·f` (`lo + (hi−lo)·f` overflows near ±1e308). If a non-finite edge or count
  appears, nothing is drawn and the status display gives the reason (data 29.4).
- Statistics: count, mean, standard deviation and median are computed with the same approach as `chunked_stats` (data 36.8; the median is
  exact). When collecting the values of the fine bin that contains the median, if that bin's min and max are equal, that value is the median
  (nothing is collected). If the number collected would exceed `HIST_MEDIAN_COLLECT_MAX` (1,000,000), a further pass rebuilds a fine histogram
  within that bin's range to narrow it down (because for a column such as electric power that is almost always 0, one bin can hold almost
  the whole column and be loaded into memory; found in review. The same problem in `chunked_stats` is in the dev notes).
  With frequency = time, the mean is the **time-weighted mean**, and the median is an approximation from the fine histogram weighted by time
  (marked "≈" in the legend).

With grouping:
- The group edges are made first: from the start of the local day that contains the range's first time (the origin time of that day), up to
  the end of the range. Lengths of one day or less are laid out at equal intervals **from the origin, for each local day** (on a day with
  daylight saving time of 23 or 25 hours, the groups inside the day keep the same length and the day is cut at its end. The number of groups
  is `ceil(day length / length)`, so the last group is at most the length; if it is shorter, it has gaps. Using `round` would make the last
  group of a 25-hour day longer and give a false "no gap" outlier; found in review. It is as specified that after the time changes, groups
  start at odd local hours). Whole numbers of days are counted in local calendar days. The conversion to local time is the same as the
  screen's time axis (`datetime.fromtimestamp`, the same approach as `image_export._local_offset_s`). Upper limit on the number of groups
  `HIST_MAX_BUCKETS` (1,000,000; above it, nothing is computed and "too many groups" is reported).
- For each chunk, the group number of each row is found with `np.searchsorted(edges, x)` (since the time axis is ascending, so are the
  numbers), and per group the following are added: count, sum of values, integral (`∫y dt`), effective time (`∫dt`), max and min
  (`np.maximum.reduceat` etc.).
- **The time of a row is split at group edges** (of the interval `[x_i, x_i + w_i)` where the row's value continues, the length that falls in
  each group is put into that group; this is correct even when a group is shorter than the measurement interval). Implementation by cumulative
  sums: build the cumulative integral and cumulative effective time at the row times (inside an interval the value is constant, so this is
  linear), and take differences with `np.interp` at the edge times (the cumulative sums are carried across chunks). For max and min, besides the
  rows inside the group, the **value continuing at the start of the group** (when the previous row's interval is not a gap and overlaps the
  group) is also included. Sum and count use only the rows inside the group (since they are row counts and sums of values).
  (The first version put each row's time entirely into the group of its row. With 10-minute data cut into 1-minute groups, the integral came
  out 10 times too large and no gaps were reported; found in review.)
- Summary values: total = sum of values; integral = `Σ y_i·w_i / seconds per unit` (3600 for hours); mean = **time-weighted mean**
  (`∫y dt / ∫dt`); max; min. Groups with no value (not counted): for integral and mean, groups with zero effective time; for max and min,
  groups with neither rows nor a continuing value; for total, groups with no rows at all.
- **Gaps**: a group is "with gaps" when its effective time / group length < `HIST_COMPLETE_RATIO` (0.9) (gaps, and the first and last groups
  cut partway through the range). The count is shown in the status display; if "Exclude" is ON, these groups are excluded.
- The summary values (on the order of the number of groups, i.e. days) are kept in memory and made into a histogram with numpy (automatic bins
  apply FD as is; with several variables, the largest width is used, as with no grouping; the number of bins is also capped at the number of
  values). The statistics (count, mean, standard deviation, median) are computed from these values too.
- The "groups in range" in the status display is **the number of groups that overlap the range `[first time, last time]`** (the edges are made
  per day, so this is not the number of edges itself; when the range ends exactly at 0:00 of a day, an empty day is not counted; found in review).

Result (`HistogramResult`): per variable (bin edges, counts, number of excluded values, mean, standard deviation, median, number of groups
with gaps); common (number of gaps, number of groups in range, a summary of the settings for the image notes and the status display).

#### 42.4 Display

- pyqtgraph `PlotWidget`. Each variable is a step plot (`PlotDataItem(stepMode="center", fillLevel=0)`), with a colour from `_COLOR_CYCLE` and
  a semi-transparent fill. Mean = dashed and median = dotted `InfiniteLine` (same colour, width 2). Legend: "name n=… mean=… σ=…".
- X axis label: for no grouping, the variable name; with grouping, a summary such as "variable name (1 day, integral [×h])". Y axis label:
  frequency (rows / time [in a readable unit of seconds, minutes or hours] / number of groups) or percentage (%).
- Status display: the range (local time), the count per variable, the number of excluded missing values, the number of gaps, the number of groups
  with gaps (with "exclude" ON: "n excluded").
- Hover: the range and frequency of the bin under the mouse are shown in the label below the display.

#### 42.5 Image export

- `build_export_figure`: matplotlib `stairs` (`fill=True`, semi-transparent), plus `axvline` for the mean and median, and a legend. The notes
  (`export_stamp_extra`) get the rows "Variables: …", "Aggregation: no grouping, frequency = rows" or "Aggregation: 1 day (origin 6:00), integral
  [×h], groups with gaps excluded", and "Bins: auto (n bins) / n bins". The range is the one computed by the analysis window (25.1).

#### 42.6 Test plan

- `core/histogram.py` (directly, not through the window):
  - No grouping: the sum of frequencies equals the number of finite values; the number of excluded NaN/Inf values; chunk splitting (with a small
    `HIST_CHUNK_ROWS`) gives the same result as one pass; the display bins align with the fine bin edges; the number of bins specified; all values
    equal (IQR = 0, min = max); an empty range.
  - Frequency = time: with equal intervals, proportional to rows × interval; gaps (automatic or specified) do not count as time; the last row is 0.
  - With grouping: for known data of the `feature_test.parquet` kind, the total, integral, mean, max and min for 1 day, 1 hour and 10 minutes match
    hand calculations. Units of the integral (seconds/hours). An origin at 6:00 shifts the edges. Constraints on the group length (a length that does
    not divide a day, and a length that is not a whole number of days, are errors). Detection and exclusion of gaps (a 30-second gap; the first group
    starting partway through the range). Daylight-saving days (a test with `TZ` set to a region with daylight saving time: 23 and 25-hour days; a 25-hour
    day cut into 2- or 3-hour groups gives a last group of 1 hour with gaps). The upper limit on the number of groups. The number of groups in range
    (a range partway through a day; a range ending exactly at 0:00). Groups shorter than the measurement interval (10-minute data cut into 1-minute
    groups: integral, mean and max correct, no gaps). Rows with non-finite times.
  - Time axis not ascending, or rows with non-finite times: the handling.
  - Statistics: with no grouping the median equals `np.median`; the time-weighted mean. A column of almost identical values, and a column with more
    values in the median bin than the limit (with a small limit), still give an exact median. ±1e308 values keep the edges finite.
  - Several variables: even when overlaid with different widths, the automatic number of bins stays between a few and a few dozen. The number of gaps
    does not depend on NaN values.
- `HistogramWindow`: the relation between the menu item and the flag; enabling and disabling of input fields by settings; follow OFF/ON, recompute,
  discarding stale results, and closing when the file is reopened (as in 25.4); closing does not crash a computation in progress; operation-log records;
  image export produces a file whose notes include the aggregation row. Removing all variables clears the display, and image export is not possible.
  Deleting a selected derived variable triggers a recompute.
- Visual: check the screen and the image (overlay, vertical lines, legend). With the `sine` of `feature_test.parquet`, the no-grouping distribution takes
  the shape of a sine wave (U-shaped with high ends).
- Large files: with `bench_100m.parquet`, measure no grouping and the 1-day total by hand and confirm that memory does not grow (not included in pytest).

#### 42.7 Relations to other domains

- **Data**: windowed reads (`Column.window`), the ordering of the time axis (ch. 29), `WorkerHub` (ch. 37), and the same approach as `chunked_stats`
  (36.8).
- **Plots**: the "Graphs ▾" menu (25.5), notification of the displayed range (`rangeChanged`).
- **Image export**: the save process of `image_export`, the notes, CJK fonts, and the handling of local time (`_local_offset_s`).
- **Expressions**: derived variables can be targets (derived variables computed per window are read with `window` as they are).
- **App foundation**: the feature flag `histogram` (ch. 21), shortcut settings, the operation log (ch. 30), i18n.
- **Saving and export** (42.8): the Parquet of values per group is written with `pyarrow` (the same library as data reading).

#### 42.8 Two-stage aggregation and export of values per group (added 2026-10-06, user request)

Requirement: 5.37. This extends the "with grouping" of 42.2–42.6. The same `HistogramWindow` and `core/histogram.py` are used; the
feature flag stays `histogram` (no new flag; merged after the real-machine check, as in the first version).

**Settings panel** (added to the table of 42.2; shown only with grouping):

| Item | Choices | Default |
|---|---|---|
| Two-stage aggregation | checkbox | OFF |
| Length of the first-stage window (only with two stages) | 1, 3, 5, 10, 15, 30 min, 1 hour, or free entry (number + unit min/h) | 3 min |
| First-stage summary (only with two stages) | mean, total, integral, max, min | mean |
| Second-stage summary (with two stages, the existing "Summary" is used with this meaning) | max, min, mean, total | max |

- With two stages, the "group length" and "origin" are those of the second-stage group (default 1 day). The first-stage windows are laid out
  **from the same origin**.
- Constraints: the first-stage window must divide a day evenly and must **divide the length of the second-stage group** (so that each window
  falls into exactly one group). The second stage is longer than the first. Violations give the reason next to the input field and nothing is
  computed (as in 42.2). The second-stage summary has no integral (there is no point in multiplying the first-stage values by time).
- If the number of first-stage windows exceeds `HIST_MAX_BUCKETS` (1,000,000), nothing is computed (for 3-minute windows, about 5.7 years).

**Computation** (the grouped computation of 42.3 is used as it is for the first stage):
- First stage: with the grouped computation of 42.3, the value of each first-stage window (total, integral, mean, max, min) and its gaps are
  obtained. **A first-stage window with gaps is not used in the second stage** (a 3-minute mean cut partway is not a 3-minute mean); the count is
  shown in the status display. For max and min, the time at which the maximum or minimum occurred within the window is also obtained (the row's
  time; if the value is continuing at the start of the group, that group's start).
- Second stage: for each group, the (complete) first-stage values that fall in it are summarized. Max and min: the "time" is, if the first stage is
  also max or min, **the instant within that window at which the value occurred** (the first stage's time); if the first stage is mean, total or
  integral, the window's start time. Where several windows have the same value, the earlier time is used. (Decision by the user on 2026-10-07: for
  "the daily maximum of 3-minute maxima", using the window start would be off by up to 3 minutes.) Mean (the windows have the same length, so a
  simple mean) and total. A group with no usable first-stage window has no value.
- Second-stage gaps: a group is "with gaps" when (number of complete first-stage windows) / (number of windows falling in the group) <
  `HIST_COMPLETE_RATIO` (0.9). "Exclude groups with gaps" applies to these gaps (as in 42.3).
- With one stage (two stages OFF), the max and min also carry a "time" (used for export). In the computation of 42.3, the time of the row at which
  the max or min occurred within the group is obtained with the same accumulation as the max and min (where the same value occurs more than once,
  the first time).
- The distribution and statistics (bins, mean, median, etc.) are made from the second-stage values (with two stages) by the same method as 42.3.
- `HistogramResult` holds the per-group values: common `bucket_start` and `bucket_end` (epoch seconds); per variable `values`, `complete` (bool),
  and `at` (only for max and min; epoch seconds, NaN where there is no value). With two stages these are the second-stage groups. The memory is the
  number of groups (at most 1,000,000) times the number of variables, which is sufficient.

**Display**:
- The X axis label gets the two-stage summary (e.g. "Power (1 day, max of 3-min mean)"); the status display adds "n complete first-stage windows
  excluded" (for the gaps in the first stage).
- The notes of the image export (42.5) get the rows for the first-stage window and summary.

**Export of values per group**:
- A "Save group values…" button (enabled only with grouping and when there is a result). The save destination uses `QFileDialog`; the types are
  **Parquet (default)** and CSV. The default file name is `<first variable>_<group>_<summary>.parquet` (e.g. `power_kw_1d_3min-mean-max.parquet`).
  The default folder is the last folder used (not saved in settings; until the window is closed).
- The table: one row per group (every group overlapping the range. **Groups with gaps are written too, whatever the "exclude" setting, and are marked
  by the `complete` column**, so that the items removed from the display can also be judged in another application).
  - Columns: `window_start` and `window_end` (if the time axis is date-time, `timestamp[ms]` in the **same local time as the screen**, without time zone;
    for a numeric time axis, `float64` seconds); per variable `<variable name>` (the value, float64, null where there is no value),
    `<variable name>_complete` (bool), and for max and min `<variable name>_at` (a time of the same type as the value, null where there is no value).
  - When column names collide (e.g. a variable named `window_start`), `_2` and so on are appended to avoid the clash.
  - Parquet key-value metadata (JSON under the key `parquet_analyzer`): the path of the original file, the time-axis column, the range, the variables,
    the group length, origin and summary (with two stages, the first-stage window and summary), the gap settings, and the application version (ch. 17).
    For CSV, the same content is not written in a leading line starting with `#`; there are **only the columns** (so that other applications can read it
    easily). The settings go in a `.json` file with the same name, placed beside it.
  - CSV numbers use `repr(float)` (as in 44.4); times are `YYYY-MM-DD HH:MM:SS.fff`.
- Writing is done on the UI thread (expected to take under a second for up to 1,000,000 rows × several columns). Failures are warnings via `ui/messages`.
  Overwriting is left to the confirmation of `QFileDialog` (the `.json` placed beside a CSV is not covered by this confirmation and is overwritten silently;
  it is the settings file of the same name, so this is accepted. Review of 2026-10-07).
- The format **follows the chosen type (filter)**. If the file name's extension differs from the type (e.g. the default `…parquet` name kept while CSV is
  chosen), the extension is replaced with the type's. If there is no extension, or another one (e.g. `.txt`), the type's extension is appended (review of
  2026-10-07: with Qt's dialog on Windows the extension was not rewritten, so choosing CSV still wrote Parquet).
- Operation log: `histogram_export_windows` (path, format, number of rows, columns).

**Decided during implementation** (2026-10-06, by the implementer; adopted after review on 2026-10-07):
- The denominator for second-stage gaps is "group length / first-stage window length" (the nominal number of windows). Using the number of windows
  actually falling in the group would make the short last group of a daylight-saving day (e.g. the last 1 hour of a 25-hour day) "without gaps", which would
  disagree with the one-stage case (42.3). At the ends of the range and at gaps, the nominal and actual counts are the same (first-stage windows always tile
  the day). The difference arises only on daylight-saving days, and the nominal denominator makes these judged the same as in one stage (42.3).
- The first-stage window is limited to one day or less (it must divide a day). A length of 2 days, etc., is the error "does not divide a day". Exceeding the
  number of first-stage windows gives `too_many_windows` (a different message from the group limit `too_many_buckets`).
- The per-group arrays of `HistogramResult`: common `bucket_start` and `bucket_end`; per variable `VariableHistogram.bucket_values`, `bucket_complete`, and
  `bucket_at` (the existing `values` means "the values made into a distribution", a different thing, so the names differ). Only groups overlapping the range
  (as with `bucket_count`). `None` for results without grouping. No value: `bucket_values` is NaN and `bucket_complete` is False.
- In the settings panel, the two-stage items are **disabled**, not hidden, when there is no grouping (as with the existing items, so the panel does not jump).
  The "Summary" combo is replaced with max, min, mean, total (default max) when two stages are on; when two stages are turned OFF, it returns to the selection of
  the one-stage summary. The button is "Save group values..." on the analysis window's toolbar.
- Whether the time axis is date-time or numeric is decided by the Parquet column type (`Date`/`Datetime`) (`ParquetDataSource.is_datetime`).
  For the local times of the export, the UTC offset is subtracted once per hour and only the rows at hours where the offset changes are converted individually
  (the same as the screen's `datetime.fromtimestamp`; on the autumn daylight-saving end day, the wall-clock time repeats, so two groups can have the same
  `window_start` notation).
- The default folder is the last folder used in the window; if none yet, the folder of the original Parquet file (settings are not read or written).
  CSV booleans are `True`/`False`; null values and null times are empty cells. The metadata's `columns` records the mapping of actual column names
  (`window_start`, `window_end`, `variables` {variable name: value column}), so that the names after collision avoidance are known.

**Test plan** (added to 42.6):
- `core/histogram.py` (synthetic data; not relying on `data/raw`):
  - Known values: two days of 1-second data, in which on each day from 12:00:00 to 12:03:00 the value is constant at `100+10·day`, and small noise otherwise
    → the 3-minute mean and the daily max are exactly `100+10·day`, with time 12:00:00. With a 4-minute first stage, the max is about
    `(3·(100+10·day) + 1·noise)/4` (checks the handling of boundaries).
  - The division of a row's time across first-stage window boundaries (the same cumulative method as 42.3) is correct in two stages too (10-second data
    cut into 1-minute first-stage windows).
  - A first-stage window with gaps (a gap or the edge of the range) does not enter the second stage. The gap judgement of the second stage and "exclude".
  - Second-stage summaries (max, min, mean, total); a group with no usable window has no value.
  - Violations of constraints (not dividing evenly, a second stage shorter than the first, too many windows) return the reason for not computing.
  - On daylight-saving days (23/25 hours), the first-stage windows still line up within the day and fall correctly into the second-stage groups (same method as the
    daylight-saving test of 42.6).
  - The "time" of max (min) with one stage. With two stages where both first and second are max (min), the time is the instant of the peak within the window
    (not the window start). If the first stage is mean, the window start.
- Export:
  - Read the Parquet back with `pyarrow`; column names, types (`timestamp[ms]` for a date-time axis, `float64` for a numeric axis), values, nulls, `complete`,
    `_at`, and the metadata JSON all match. The local times match the screen's display.
  - CSV and its `.json`. Avoidance of column name collisions. Groups with gaps are written (even with "exclude" ON).
  - With the actual `HistogramWindow`: two-stage settings → recompute → "Save group values" (`QFileDialog` monkeypatched) → the file is created, and the
    operation log keeps `histogram_export_windows`. Without grouping the button is disabled.
  - When the dialog returns `("x.parquet", CSV type)`, a CSV (`x.csv`) is written; and the reverse (`x.csv` with Parquet type) likewise.
- Visual check of the power sample (`generate_power_sample_parquet.py`) is done in the main session's final check (the daily marker values must appear).

#### 42.9 Fixes found in review (2026-10-07, agreed by the user)

Found in the review after merging (2026-10-07).

- **Groups on a numeric (non-date-time) time axis**: `bucket_edges` always found the day's origin in local time (`datetime.fromtimestamp`), so on a numeric
  axis the groups were shifted by the UTC difference (in Japan, the day ran from 9:00 to 9:00 on the axis values, i.e. starting at `-32400`, and the first and last
  groups had gaps; at UTC+5:30, hourly groups also started at :30). The `window_start` of the export also became values like `-32400`. On Windows, `.timestamp()` of a
  naive date-time before the epoch can raise `OSError`.
  → When the time axis is not date-time (`is_datetime` is false), the **offset is 0** (the axis value 0 is used as the day's origin, with no local-time conversion).
  There is no daylight-saving handling either (a day is always 86400). The `window_start`/`window_end` of the export are the axis values as they are.
- **Bins when there is only one value (`lo == hi`)**: previously this gave two edges `[lo−0.5, lo, lo+0.5]`, so the bar appeared to the right of the value (1.0 to
  1.5) and the mean and median lines sat on the bar's left edge. → **One** bin centred on the value, `[v−h, v+h]` with `h = max(0.5, |v|·1e-9)` (so that the width is not 0
  even for large values). Also one bin when the number of bins is specified. The `np.histogram` path for groups uses the same rule (merged into one function).
- **`HIST_CHUNK_ROWS` set to 1,000,000** (42.3): with grouping and two-stage summaries, about 15 temporary arrays (float64) are created per chunk, so with 4M rows the
  peak working memory was about 690 MB (measured at 12M rows). At 1M rows it is about 175 MB, at the same speed (0.43 s → 0.44 s). Without grouping and with
  frequency = time, it went from 322 MB to 81 MB.
- Tests: (1) with `TZ=Asia/Tokyo`, a numeric time axis `t = 0..3 days` and 1-day groups → the groups are `0, 86400, …` with three complete groups, and the export's
  `window_start` starts at 0 (this fails before the fix; the existing tests only use `TZ=UTC`, so the problem went unnoticed). (2) A variable that is all 1.0 → one bin
  containing 1.0 with the centre at 1.0, also with groups; with a value of 1e20 the width is finite and positive. (3) The existing tests for a range crossing
  `HIST_CHUNK_ROWS` pass (including the existing tests that monkeypatch the constant to be small).

#### 42.10 Narrowing by time window (added 2026-10-14, stage 1)

Requirement: 5.48. Adds a "target time" condition to the computation of 42.2–42.9. The feature flag stays `histogram` (no new flag; merged after the real-machine check,
as in 42.8).

**Settings panel** (added to the table of 42.2; shown regardless of aggregation and display kind):

| Item | Choices | Default |
|---|---|---|
| Narrow by time window | checkbox | OFF |
| Time-of-day range (only when narrowing) | start `HH:MM` to end `HH:MM` | 0:00 to 0:00 |
| Days of the week (only when narrowing) | preset combo (all, weekdays, weekends, custom) + seven toggle buttons, Mon to Sun | all |

- Time-of-day range: if start < end, it is `[start, end)`. If start > end, it crosses midnight: `[start, 24:00) ∪ [0:00, end)`.
  **Start = end means the whole day** (for narrowing by weekday only). Times are local times as on the screen's time axis (the same conversion as the groups of 42.3).
- **For a range that crosses midnight, the weekday is decided by the day on which the range starts** (Friday 22:00 to Saturday 6:00 is a "Friday" range; this counts
  one night shift as one unit).
- If the weekday toggles are changed by hand, the preset combo changes to "custom". If no weekday is selected, nothing is computed (the reason is shown, as in 42.2).
- **Numeric time axis** (not date-time, 42.9): the time-of-day range can use the axis value 0 as the start of the day, as in 42.9. **Weekdays are disabled** (the toggles
  are disabled, with the reason in the tooltip; weekdays have no meaning on a numeric axis).
- Daylight saving: the time-of-day range is decided by wall-clock time (on a spring 23-hour day, times that do not exist are excluded from the range; on an autumn
  25-hour day, times that occur twice are included both times).

**Computation** (`core/histogram.py`):
- **Target time** `A` = the set of times that satisfy the narrowing condition. Within the range `[first time, last time]`, target intervals are made for each local day
  (at most two per day), and the whole is an ascending array of the interval end times (about 2 × the number of days; about 3,000 for 100 million rows over 4 years).
  The limit on the number of intervals is the same as `HIST_MAX_BUCKETS` (above it, nothing is computed).
- **Cumulative target time** `F(t)` (the target time contained up to `t`; a polyline with slope 1 inside the target and 0 outside) is made at the interval ends, and evaluated at
  arbitrary times with `np.interp`.
- **Each row's time** (`w_i` of 42.3) is replaced by `F(x_i + w_i) − F(x_i)` (the gap judgement is still made with the **original** `w_i`, and a gap gives 0). Time during
  which a value continues outside the target is not counted.
- **Whether a row is a target**: whether the row's time `x_i` falls in a target interval (`searchsorted` on the interval ends used to build `F`).
  - No grouping, frequency = rows: only the target rows are counted (this also reads the time axis; an exception to "the time axis is not read" of 42.3. With narrowing OFF, it is not read, as before).
  - Frequency = time, integral, mean: added with the weights above (rows outside the target get weight 0; a row that starts outside and continues into the target
    contributes only its part inside).
  - Group max, min, total and count (row counts and value sums): target rows only. The value continuing at the start of a group (42.3) is included only when the start of
    the group is inside the target.
- **Gaps in a group** (`HIST_COMPLETE_RATIO` of 42.3): the denominator is not the group length but **the target time inside that group**
  (`F(group end) − F(group start)`). (For a daily group with 9:00–17:00, if 90% of the 8 hours have valid time, there is no gap.) **A group with zero target time** (for
  example, Saturday and Sunday when only weekdays are selected) is not counted as a group: it is treated as absent (neither a no-value nor a gap case; it is not counted in
  the "groups in range" of the status display and no bar is drawn for it in the trend).
- Two-stage (42.8): the first-stage windows use the same gap judgement (denominator is the target time inside the window). A window with zero target time does not enter the second
  stage (and is not counted as a gap). The denominator for second-stage gaps (the nominal number of windows of 42.8) becomes "the number of windows with non-zero target time".
- The automatic gap value (5 times the median of adjacent intervals) and the number of gaps are still computed from the whole time axis (independent of narrowing).
- The result gets `target_seconds` added (the total target time within the range).

**Display**:
- The status display adds "Narrowed: 9:00–17:00, weekdays (target n hours)". The same line is added to the notes of the image export (42.5).
- Export of values per group (42.8): groups with zero target time are not written. The narrowing settings are added to the metadata.
- Operation log: `histogram_settings` with `time_filter` (`enabled`, `start`, `end`, `weekdays`).

#### 42.11 Display kinds and the duration curve (added 2026-10-14, stage 1)

- A **Display** combo at the top of the settings panel: distribution (default; the current display), duration curve, trend, and by time of day (by time of
  day is added in stage 2; stage 1 does not create that item). Items that have no meaning for the chosen display kind are **disabled** (not hidden, as in 42.8):

| Item | Distribution | Duration curve | Trend | By time of day |
|---|---|---|---|---|
| Aggregation (no grouping / with grouping), two stages | yes | yes | fixed to with grouping | disabled |
| Frequency (rows / time) | yes | yes (unit of the horizontal axis) | disabled | disabled |
| Bins, vertical axis (frequency / percentage) | yes | only the vertical axis (horizontal axis in %) | disabled | disabled |
| Narrow by time window | yes | yes | yes | yes |

- Choosing trend sets the aggregation to "with grouping" (its combo is disabled). Returning to another kind restores the previous aggregation.
- Changing the display kind triggers a recompute (the computation itself differs; as in 42.2). Operation log `histogram_settings` with `view`.

**Computation of the duration curve** (inside the worker):
- No grouping: the fine histogram of pass (2) of 42.3 (`HIST_FINE_BINS`, for both frequency and time) is **accumulated from the largest down**. The points are
  "the lower edge value v of a fine bin, and the time (or row count) at which the value was at least v". The accuracy is within one fine bin width (the same as the
  distribution with no grouping), and no third pass is read. With frequency = rows, the horizontal axis is the row count.
- With grouping (including two stages): the per-group values (`bucket_values` of 42.8, after removing missing ones) are **sorted in descending order**, and the horizontal
  axis is the rank (1, 2, …, the number of groups). Exact.
- If narrowing by time window (42.10) is ON, it is computed with its weights and target rows (the horizontal axis is then target time).
- For the percentage on the vertical axis: the horizontal axis becomes a percentage of the whole (valid time, row count, or number of groups).
- Result: per variable, `duration_x` (horizontal values, ascending) and `duration_v` (values, descending). As there are many points (65,536 fine bins), before display
  the intervals where the value does not change are merged (an empty bin has the same horizontal value as the previous point, so it is dropped).

**Display of the duration curve**:
- Horizontal axis "time (at or above this value)" (seconds, minutes or hours, in a unit readable from the largest time, with the same rule as the time unit of 42.4),
  "rows", or "number of groups (e.g. days; written with the group length)", and "%" for percentage. The vertical axis is the variable name (with grouping, the
  summary of 42.4's aggregation).
- Per variable, a step line in the `_COLOR_CYCLE` colour (`stepMode`). Legend "name max=… mean=…". No mean or median vertical lines are drawn.
- Hover: "v or more: 123.4 h (56.7%)".
- Image export: matplotlib `step`. The notes are as in 42.5 (aggregation and narrowing rows) plus "Display: duration curve".

#### 42.12 Trend (bar chart of the values per group, added 2026-10-14, stage 1)

- The grouped result (`bucket_start`, `bucket_end`, `bucket_values`, `bucket_complete` of 42.8) is drawn as it is. No new computation (groups with zero target time, when
  narrowing by time window, are not in the result, as in 42.10).
- Horizontal axis: the group times. The same tick marks as the main screen's time axis (local-time format; numbers if the time axis is numeric) are used.
- Bars: the width is the group's `[start, end)`, the height is the value. With several variables, each group's width is divided equally among the variables and they are placed side by side.
  - **Groups with gaps** are drawn in a pale colour (reduced opacity) with hatching. Not drawn if "Exclude groups with gaps" is ON.
  - Groups with no value are not drawn (a gap in the bars).
  - If the number of groups exceeds `HIST_TREND_BAR_MAX` (2,000), the trend is drawn not as bars but as a step line joining the group values (since bars become thinner than
    one pixel; the status display says "drawn as a line because there are many groups").
- Vertical axis: the same aggregation summary as the X-axis label of the grouped display in 42.4 (e.g. "Power (1 day, integral [×h])").
- A horizontal dashed line at the mean for each variable (the legend shows the mean).
- Hover: "Group: 10/14 00:00 – 10/15 00:00, value: … (with gaps)".
- Range: on first display, the range that includes all groups. Mouse zoom and pan are horizontal only (the vertical axis fits the displayed values).
- Image export: matplotlib `bar` (groups with gaps use `alpha` and `hatch`); the time axis uses the same format as the image export's time ticks (`image_export`). The notes get "Display: trend".
- "Save group values…" (42.8) can be used with the trend in the same way.

#### 42.13 By time of day (added 2026-10-14, stage 2)

- Display kind "By time of day". Horizontal axis = time of day within the day; vertical axis = the value of that time slot.
- Settings (enabled only for by time of day):

| Item | Choices | Default |
|---|---|---|
| Length of the time slot | 10 min, 15 min, 30 min, 1 hour, 2 hours, 3 hours | 1 hour |
| Value | mean (time-weighted), integral | mean |
| Time unit of the integral (only for integral) | seconds, minutes, hours (shared with 42.2) | hours |
| Per day (only for integral) | checkbox | ON |

- The "origin" (42.2) is used as the start of the horizontal axis (with origin 6:00, the axis runs from 6:00 to 6:00 the next day). Aggregation (no grouping / with grouping),
  two stages, bins, frequency and the vertical-axis setting are disabled.

**Computation** (`core/histogram.py`, inside the worker):
- The grouped computation of 42.3 is used with the group length = the slot length (laid out from the origin, per local day; the cumulative method that splits each row's time at
  group edges is used as is). For each group, `∫y dt` and `∫dt` (valid time) are obtained. The limit on the number of groups is the same.
- **Folding**: for each group, the slot number is determined from its start local time (wall-clock hour:minute), and `∫y dt` and `∫dt` are added up over the same slot numbers.
  Daylight saving: on an autumn 25-hour day, a time that occurs twice is added to the same slot both times. On a spring 23-hour day, a slot whose time does not exist adds nothing that day.
  For a numeric time axis, the axis value 0 is the start of the day, as in 42.9.
- Values: mean = `Σ∫y dt / Σ∫dt` (time-weighted). Integral = `Σ∫y dt / seconds per unit`. If "Per day" is ON, it is divided by the number of days in which that slot had valid time
  (the number of days is counted per slot, so that days without data are not counted as zero in the average).
- Narrowing by time window (42.10) also applies (slots outside the target have no value). The gap judgement (42.3) is not used (because the weighting is by the total valid time per slot).
- Result: per slot, the start time (seconds from the origin), and per variable the value, valid time and number of days.

**Display**:
- Horizontal axis: time of day (`HH:MM` ticks; 24 hours from the origin). Per variable, a step per slot (`stepMode="center"`), colour-coded and overlaid.
- Vertical axis: the variable name for the mean; "variable name (integral [×h], per day)" for the integral. A slot with no value breaks the line.
- Hover: "13:00–14:00 mean: … (n days)". The status display gives the range of the number of days (minimum to maximum).
- Image export: matplotlib `stairs`. The notes get "Display: by time of day (1 hour, mean)".
- "Save group values…" (42.8) becomes "**Save slot values…**" for by time of day: one row per slot (`slot_start` as an `HH:MM` string, per variable the value, valid time in seconds
  and number of days), written as Parquet/CSV with the same format rules and metadata as 42.8.

#### 42.14 Test plan (42.10–42.13)

- Narrowing by time window (`core/histogram.py`, synthetic data; `TZ` fixed):
  - 1-minute intervals over 7 days (starting Monday), with value = the hour of the time (0–23). 9:00–17:00 → with no grouping, the row-count distribution has only values 9–16,
    and frequency = time totals 7 days × 8 hours. Weekdays only → 5 days. A range across midnight, 22:00–6:00, Friday only → 8 hours from Friday 22:00 to Saturday 6:00.
    Start = end with weekdays → 5 days × 24 hours.
  - A row that starts outside the target and continues into it (9:30 onwards, with hourly data) contributes only the part inside. The gap judgement uses the original intervals.
  - With grouping, 1 day, integral: the gap judgement when narrowing (the target time is the denominator). Weekend groups are absent from the result (weekdays only). Same with two stages.
  - Numeric time axis: the time-of-day range works, weekdays are disabled. Time-of-day ranges on a daylight-saving day (`TZ=Europe/Berlin` etc.).
  - With narrowing OFF, the result is the same as before (all existing tests pass).
- Duration curve: known values (value 1 for 3 hours and value 2 for 1 hour) give "≥2 = 1 hour, ≥1 = 4 hours". With grouping, the daily values sorted in descending order.
  Percentages. Combination with narrowing. The error of the fine-bin approximation with no grouping is within one bin width.
- Trend: the bar positions, widths and values match `bucket_*`; the drawing difference for gaps and that it disappears with "exclude"; groups with no value are not drawn;
  with two variables the width is split equally; above 2,000 groups it becomes a line.
- By time of day: with synthetic data whose value = the hour, the 1-hour slot means run 0–23. 30-minute data with 10-minute slots (slots shorter than the measurement interval).
  Integral and "per day" (divided by the number of days; days without data not counted). Origin 6:00 starts the axis at 6:00. Folding on a 25-hour autumn day.
  Export of slot values.
- `HistogramWindow`: the display kind enables/disables items; trend fixes the aggregation to "with grouping" and restores it on return; changing the kind triggers a recompute;
  the operation log has `view` and `time_filter`; toggling a weekday sets the preset to "custom"; zero weekdays selected does not compute; weekdays are disabled on a numeric
  time axis. Image export of each display produces a file whose notes include the display and narrowing rows.
- Visual (final check): with `power_sample.parquet` (the 12:00 marker, the gaps on days 40–41, the 3-hour gap on day 100), check the duration curve, the trend
  (daily usage bars, pale for the gap days), and by time of day (the 12:00 slot high) in images.

#### 42.15 Decisions made during implementation and review (2026-10-14, stage 1)

- **Range over which target time is built**: not `[first time, last time]`, but **the whole range of the groups** (for two stages, the first-stage windows too), so that the
  denominator of gaps at the ends of the range (target time inside the group) is the full group. (The first version built target time only up to the last day of data, so a
  week with only 3 days of data was judged "no gap"; found in review.) For two stages, the nominal number of windows is also counted over the whole second-stage group.
  `target_seconds` remains `F(last time) − F(first time)`.
- **Continuing value for max and min**: besides the group start, the value that continues at the **start of a target interval inside the group** is included (the time is that
  interval's start; with equal values, the earlier time). This matches the time weighting of mean, integral and no-grouping. (With a range of 9–17 h, a value at 8:30 continuing
  into 9:00–9:30 was included in the mean but not in the max; found in review.)
- **Count of excluded missing values**: in all aggregations, only "rows with an invalid time, rows inside the target, and rows continuing into the target (positive time weight)"
  are counted (NaN outside the target is not counted).
- **Time-of-day range**: when start = end is not 0:00 (e.g. 6:00 to 6:00), it is **24 hours from the start** (the weekday is the day on which it starts). 0:00 to 0:00 is that calendar day.
  The time input has no 24:00, so 9:00 to 0:00 reads as "9:00 to 24:00" (the reading of a range crossing midnight, when start > end).
- **Very large numeric time axes** (counters in microseconds, etc., values that cannot be converted to dates): when grouping or narrowing cannot convert to dates, nothing is
  computed and the reason (`time_too_large`) is shown (no exception).
- **Duration curve**: the maximum in the legend is the exact value. The first point of the no-grouping curve is the lower edge of the top fine bin (an approximation within one fine bin width).
- **Groups at the edges of the range**: a group that has target time but no data inside the range is **no value** (not treated as absent; as before, without narrowing).
- **Operation log**: `histogram_settings` records each changed item, so `item="view"` and `item="time_filter"` are added. `histogram_compute` keeps both `view` and `time_filter`.
- **Trend display**: bars are 95% of the group width (so they do not touch the neighbouring bar). Bars with gaps have darker fill and hatching so they stay visible on a dark background.
  The separator in English narrowing labels is ", " and "/" (Japanese uses a middle dot). The trend chart is a separate `PlotWidget` from the distribution and duration curve
  (switched with a `QStackedWidget`).
- Tests (added to 42.14): "narrowing to all day and all weekdays = no narrowing" is compared on all result items (values, gaps, group count, times, counts, excluded, gaps in the
  time axis) (origin 6:00, 7 days, two stages over 7 days, range ends in the middle of a group); a random check (`hypothesis`) gives the same agreement, and with narrowing ON the
  result does not depend on the chunk size.

### 44. Making a map from measurements (map display and export in XY) (added 2026-10-06, user request)

Requirement: 5.35. Background: the relation between measurements seen in the XY and 3D windows (e.g. rpm × torque → efficiency) should be
usable as a map in expressions. The density display (25.2) already shows the per-bin mean as colour when colour = Z, but at a fixed 256 × 256 and
it cannot be exported. Decisions (2026-10-06, user): the grid is by interval or by number of divisions; output is registration in the map manager plus
CSV saving (either one alone is also allowed); the empty cells can be chosen (default fill by interpolation); the first version also includes a heat
map / surface display and overlaying of several maps.

**The implementation is split into two stages** (one stage = one branch): **stage 1** = 44.1–44.6 (computation, map display, export, and maps with empty
cells, 27.10); **stage 2** = 44.7 (overlay of registered maps and curves). The feature flag `map_from_xy` (ch. 21; ON by default since 2026-10-07) hides the
entry points of the "map" display mode, the export and the overlay.

#### 44.1 Grid (`core/map_grid.py`, new; no dependency on Qt)

- **Grid points = the axis values of the map.** Each measured point goes into the cell of the **nearest grid point** in X and in Y (the cell edges are the midpoints
  between adjacent grid points; the end cells extend outward by the same width, and points further out are **not put in but counted**, shown in the status display
  as "outside the range"). This way "the value of a cell = the value at that grid point", which matches reading the exported map by bilinear interpolation.
- Specification (per axis): `GridAxisSpec(mode: "step"|"count", lo, hi, step | count)`.
  - Interval: grid points are `lo, lo+step, …` up to the largest not exceeding `hi` (computed as `lo + k*step`, not by repeated addition; `hi` is included if its
    difference from the last point is within `step*1e-9`, so that floating-point error does not drop `hi`). The grid points obtained are **rounded to the decimal
    digits of the step + 3** (so that values such as `-0.3999999999999999` do not remain in the CSV or the registered map; review of 2026-10-07).
  - Number of divisions: `count` (2 or more) equally spaced points, `np.linspace(lo, hi, count)`.
  - Initial range: the min and max of the valid points from the first pass are rounded outward to a multiple of a "round" interval (1, 2 or 5 × 10^k, giving about 20
    divisions) (`nice_axis(lo, hi, target=20)`).
  - Validation (`MapGridError`): `lo < hi`, finite, the interval positive, and each axis has **2 to 500 grid points** (`MAP_GRID_MAX_POINTS`, the limit for the table in
    the map manager and for the interpolation cost). Violations show the reason in the settings panel and nothing is computed. `MapGridError` carries a reason
    **code and arguments** (e.g. `lo_ge_hi`, `too_many(limit=500)`), and the UI turns them into text with `tr()` (`xy.grid_err_*`, en and ja); no display text
    lives in core (review of 2026-10-07). When the grid is invalid and nothing is computed, "Export as map" is disabled even if a previous result remains
    (so that a map differing from the settings is never exported).
- A curve (no Z) is a one-dimensional grid on the X axis only, with the same rules.

#### 44.2 Computation (`core/map_grid.py`, inside the worker)

- `grid_aggregate(chunks, x_axis, y_axis | None, agg, min_count) -> GridResult`. `chunks` is an iterator returning arrays `(x, y, z)` (for a curve `(x, v)`) in
  order, read in chunks of `XY_CHUNK_ROWS` as in the XY density (25.2) (the first pass for the initial range uses the same chunking). Rows with any non-finite value
  are excluded and counted.
- For each cell, count, sum, max and min are accumulated (`np.bincount`, `np.maximum.at` / `np.minimum.at`; cell index = `iy*nx + ix`). Mean = sum / count (a simple
  float64 accumulation is taken to be sufficient).
- **Median**: all values in each cell must be collected, so it can be chosen only when the number of valid points is at most `MAP_MEDIAN_MAX_POINTS` (20,000,000)
  (above it, the option is disabled in the aggregation choices with the reason; the point count is known from the first pass). What is collected is pairs of cell
  index (int32) and value (float64); at the end they are sorted by index and `np.median` is taken per group.
- `GridResult`: `x_axis, y_axis, value (ny, nx) | (nx,), count, measured_mask (count ≥ min_count), filled_mask, n_valid, n_excluded_nonfinite, n_outside`. Cells with
  a count below `min_count` get NaN as value (when the aggregation is "count", the count itself is the value, and it is not set to NaN even below `min_count`:
  the count is always valid for all cells).
- **Handling of empty cells** (`fill: "smooth" | "nearest" | "none"`, default `smooth`; not used for the count aggregation):
  - `smooth` (fill by interpolation): the values of measured cells are fixed, and empty cells are filled by solving the **Laplace equation** (each cell becomes the mean
    of its neighbours, the smoothest connection). It is solved in the index space of the grid (the axis spacing is not used, since the grid is equally spaced in index
    terms). Solved by a conjugate gradient method without forming a matrix (5-point differences, numpy vector operations), stopped at a relative residual of 1e-8 or after
    `4*(nx+ny)` iterations. It also spreads outward into the ends where nothing is measured (outward gradient 0 at the ends). scipy is not used (it is not a main
    dependency; same reason as 27.1).
  - `nearest` (fill with the nearest value): for each empty cell, the value of the measured cell with the smallest index distance (Euclidean distance normalised by the
    grid spacing of each axis). For equal distances, the smaller index wins (so that the result is deterministic). When there are many empty cells (empty × measured
    > 50 million), the computation is done in blocks.
  - `none`: left as NaN.
  - If there is no measured cell at all, nothing is filled and the result is shown as "no data", and display and export are disabled.
  - `filled_mask` = the cells that were filled.
- Cancellation (25.1): checked between the chunks of the split reads and every 100 iterations of the interpolation.

#### 44.3 Map display in XY (extension of `ui/xy_plot_window.py`)

- "**Map**" is added to the display choices (2D: "auto / scatter / density / map"; 3D gets a "points / map" switch). Choosing map shows, in the settings panel (one
  row, `FlowLayout`, 25.5): the X grid and the Y grid (each a combo "interval / number of divisions" plus range `lo` to `hi` and value), the aggregation (mean / median /
  max / min / count), the minimum count N (default 1), the handling of empty cells (interpolation / nearest / leave empty), and "Overlay measured points" (checkbox,
  default ON). The initial range is filled in at the first computation from `nice_axis` of 44.1; once the user has touched it, it is no longer changed automatically
  (the "Auto" button returns it to the initial range).
- What becomes the map: in 2D with one Y and colour = Z → map `z(x, y)`. In 2D with no Z (colour = single colour / time) → a curve (if there are several Ys, a curve is
  made for each Y and drawn overlaid; for export, one is chosen at a time). In 3D → `z(x, y)`. **If the time-axis column is selected for X, Y or Z, no map can be chosen**
  (a map with time as an axis cannot be used in expressions; the choice is disabled and the reason is shown). If the time-axis column is selected while in map display,
  the display returns to "auto"; restoring the column does not change the map back automatically.
- Drawing:
  - 2D map: an `ImageItem`, drawing each cell as a rectangle centred on its grid point (the axes show real values). Colour is viridis plus a `ColorBarItem` (the range of
    values = min and max of the finite values displayed). **Empty cells are not drawn (the background shows through).** **Filled cells** get white diagonal hatching over the
    same colour (one layer above the `ImageItem`, a path filled with a hatch pattern `QBrush`). Measured points are overlaid as small grey points with no Z colour (beyond
    `XY_SCATTER_LIMIT`, the same thinning as 25.2).
  - Curve: over the scatter plot, a line joining the grid points (measured cells as ●, filled cells as ○, empty cells not joined).
  - 3D map: `Xyz3DView.set_surface` (25.3). Surfaces touching empty cells are not drawn: **the squares touching empty grid points are removed from the surface** (because
    at alpha 0 in GL the colour of the neighbouring surface blurs; adopted in review of 2026-10-07). Grid points with values that no longer belong to any surface after this
    removal are drawn as coloured points (`GLScatterPlotItem`), so that a ring-shaped measurement does not make the surface almost disappear. Measured points are overlaid
    with `set_scatter`.
  - Cursor (5.35, "the cursor and follow work as before"): on the 2D map and curve too, the (x, y) of the cursor row is overlaid with the same mark as in the scatter plot.
  - Hover (25.2/25.3): over a cell, shows "X: … Y: … value: … count: n (filled value / empty)".
- Status display: valid points, excluded (non-finite), points outside the range, and the number of cells (measured / filled / empty).
- Image export (25.1): 2D uses `pcolormesh` (empty cells masked, filled cells `hatch="//"`) plus a colour bar; 3D uses `plot_surface`. The notes get one line:
  "Map: grid X …–…(interval/count), Y …, aggregation, minimum count, handling of empty cells".
- Operation log: `xy_plot_compute` gets `mode="map"` and the grid, aggregation and handling of empty cells.

#### 44.4 Export ("Export as map...")

- "Export as map..." on the XY window's toolbar (enabled only in map display when there is a result). Dialog:
  - Name (default `<Z>_map`; for a curve `<Y>_curve`). It is checked each time the input changes, with the same validation as 27.3 (whether it can be used as a name in
    expressions, and whether it clashes with built-in names, user functions or maps), and the reason is shown. **When a map of the same name already exists**, it becomes an
    "overwrite", confirmed when the dialog is accepted (virtual variables that use that map are recomputed as in 27.8, and restored if that fails).
  - Output: checkboxes "Register in the map manager" and "Save as CSV" (both ON by default; with both OFF, export is not possible). The CSV destination is chosen in a file
    dialog (default folder `settings.last_export_folder`, file name `<name>.csv`). **If the destination CSV already exists and has not gone through the file dialog (which
    already asked about overwriting), overwriting is confirmed when the dialog is accepted** (for both "CSV only" and "both"; review of 2026-10-07: writing the same name twice
    silently replaced it, and could even change another map's `source_path`). The overwrite confirmation for a map also states that unsaved value edits in the map manager
    will be lost.
  - Interpolation and out-of-range handling (27.1; default `linear` and `clamp`; for a curve, `spline` can also be chosen).
- The CSV format follows 27.2 (for a map: the top-left cell is the string `<Y name>\<X name>`, row 1 = X-axis values, rows 2 onward = Y-axis value followed by Z; for a
  curve: a header row `<X name>,<Y name>` followed by the rows). The export is written with the `csv` module (so that commas in variable names are quoted). The header and
  top-left name are omitted only when the variable names can be read as numbers (which would break the detection of the header), and values are `repr(float)` (the shortest
  representation that round-trips exactly). **Empty cells are empty CSV cells** (27.10). The axis names (27.7) contain the variable names of X, Y and Z.
- Registration: when saved as CSV, that CSV becomes the `source_path`, and the same processing as the import of 27.3 is used (copy plus JSON). Registration alone writes
  directly to `maps/`, with `source_path` empty (re-import is disabled). The JSON gets the origin information added
  (`"origin": {"kind": "xy_map", "file": path of the original Parquet, "time_range": [start, end], "x", "y", "z", "grid": {…}, "agg", "min_count", "fill", "created_at"}`;
  reference information for display. Under the rule that unknown keys are ignored, older versions can still read it). **The positions of filled cells** are kept as
  `"filled": [[iy, ix], …]` and shown in the map manager's table in italic and a pale colour (to tell them apart from measurements). A cell edited in the value editing
  (27.8) becomes a measurement (it is removed from `filled`).
- After exporting, if the map manager is open its list is updated, and the status display shows e.g. "Registered map <name>".
- Operation log: `map_from_xy_export` (name, registered / CSV, whether it is an overwrite, grid size, number of filled cells).

#### 44.5 Small fixes to the XY window (from the user's real-machine check, 2026-10-06)

- **Hiding series from the legend does not work**: in 2D and 3D, clicking a legend item to hide or show a series (Y) has no effect. The cause is to be found first
  (whether there is no mechanism that receives legend clicks, or whether it is received but undone by redrawing), then fixed. After the fix, hidden series are excluded from
  drawing, the colour bar range, the hover targets and image export. A hidden state is kept after recompute (it disappears if the Y is deselected). Switching works not only
  with the legend mark but also **with a click on the name text**. **In density display (which has no legend), hidden states are ignored and all Ys are drawn** (the hidden
  state is kept, and takes effect when returning to scatter; review of 2026-10-07: switching to density automatically had left no way to undo it).
- **The Y list shows only two rows and is hard to search**: the Y choice becomes a list opened by a button, with a search field (multiple selection with checkboxes,
  `NamePicker`). The chosen Ys are shown on the button text, e.g. "sine, cosine (2)". X and Z keep the current ordinary combo (during implementation, the premise that X and Z
  also had searchable lists was found to be wrong; adopted in review of 2026-10-07; searching X and Z can be considered separately if needed).
- These two are independent of the map feature, so they are fixed in the **first commit of stage 1** (effective regardless of the feature flag).

#### 44.6 Test plan (stage 1)

- **Grid** (`core/map_grid.py`, parametrised): with an interval, `hi` exactly included or not; floating-point error (step `0.1` with `hi=1.0` is included); number of divisions;
  errors for fewer than 2 points, more than 500 points, `lo >= hi` and non-finite values; `nice_axis` rounding (negative ranges, a very small width, a single equal value).
- **Assignment**: a value exactly at the midpoint between grid points goes to the larger cell (the same rule as nearest interpolation in 27.1); points up to half a width beyond
  the end cells are included, and points further out are counted as outside the range.
- **Aggregation**: with known points, mean, median, max, min and count match. Below the minimum count gives NaN. Split reads (small `XY_CHUNK_ROWS`) and one pass give the same
  result. Exclusion of non-finite rows and their count. Above the median limit, median cannot be chosen.
- **Filling**: with `smooth`, measured cells do not change; the interior hole of a linear surface (`z = a*ix + b*iy`) is recovered nearly exactly (tolerance 1e-6); it spreads to the
  outside too; the tie rule for `nearest`; `none` stays NaN; the zero-measured-cell case. A 500 × 500 sparse grid with `smooth` takes a few seconds (a guide; there is no CI for
  it, so measure locally and report).
- **Round trip**: re-reading the exported CSV with 27.2 gives the same axes and values (empty cells are NaN). Calling the registered map in an expression gives the aggregated value
  itself at the grid points. With `feature_test.parquet`, make a map of `X=sine, Y=cosine, Z=offset_sine`; `name(sine, cosine) - offset_sine` is small (an error of about the cell width).
- **Screen**: the "map" choice and the feature flag; disabled when the time axis is selected; drawing of 2D, 3D and curve (offscreen, look at the image for 2D; 3D on a real
  machine); hatching for filled cells; that the range is not changed automatically once touched, and "Auto" restores it; name validation in the export dialog and the overwrite
  confirmation for the same name (dialogs monkeypatched); register only / CSV only / both; overwrite recomputes virtual variables and restores on failure; the JSON `origin`
  and `filled`; filled cells can be told apart in the map manager.
- **44.5**: clicking a legend item hides a series, it stays hidden after recompute, and it is absent from image export (using real Qt clicks, as dev rules's testing rules require).
  Search and multiple selection in the Y list. Clicking the name text. A hidden Y is drawn in density display and stays hidden when back in scatter.
- **Additions from the review (2026-10-07)**: grid errors appear in English and Japanese (`tr`); export is disabled when the grid is invalid; no floating-point residue remains in
  grid points (`-1` to `1` with interval 0.1 gives `-0.4`); the CSV headers (`<Y>\<X>`, `<X>,<Y>` for a curve, names containing commas, and omitted for numeric names) read back
  with 27.2 restore the axis names; writing to an existing CSV asks for confirmation (answering No writes nothing), and does not ask when a file dialog has been used; in 3D,
  an empty ring shape passes grid points with no surface as points (drawing checked on a real machine); the cursor mark appears on the 2D map.
- Large files: measure the map display (mean) on `bench_100m.parquet` by hand, and confirm that memory does not keep growing (not included in pytest).

#### 44.7 Overlaying registered maps and curves (stage 2)

- The XY window (both 2D and 3D) gets an "Overlay ▾" menu (a menu with check items), from which registered maps and curves (ch. 27) are chosen to be overlaid. Several can be
  chosen. Each overlay is given, in order, a colour from `_COLOR_CYCLE` that is **not used by the Y series currently displayed** (decision by the user, 2026-10-07; assigning
  from the first colour gave the same blue as the first Y, and the contour lines were buried in the measured points). The colour is fixed when overlaid and does not change
  until removed (if the number of Ys is changed later, a colour may coincide with a Y colour; removing and re-overlaying fixes it). 3D has no Y series, so colours start from the first.
  The legend shows the names (they can be hidden by clicking the legend, 44.5). Each menu item carries a swatch of its assigned colour (3D has no legend, so the mapping of
  colour to name is shown in the menu).
  - 2D: curve → line (with marks on the grid points). Map → **contour lines** (one colour, the assigned one, with value labels; the number of lines defaults to automatic, about 10,
    and can also be specified in "Contour values" below). They can be laid over the current map display (the window's own aggregated heat map) and over measured points. Contours are
    drawn with `pyqtgraph.IsocurveItem` (image export uses matplotlib `contour`).
  - **Contour values** (2026-10-07, user's decision: automatic plus a specification): at the end of the "Overlay ▾" menu is "Contour values...", which opens a small dialog to choose
    one of three options. It is per window (the same values are used for all overlaid maps).
    - Automatic (default): about 10 lines at round steps (1, 2, 5 × 10ⁿ) across the range of the overlaid maps' values.
    - Interval: specify a step (> 0, finite). The values are the multiples of the step that fall within the range of the map values.
    - List of values: comma-separated numbers (e.g. `0.8, 0.9, 0.95`). Duplicates are merged into one and the list is sorted ascending.
    - Invalid input (not a number, step 0 or less, non-finite) shows a translated error inside the dialog and the dialog does not close. A specification with too many lines (over 200
      for one map) is refused in the same way. If no value falls within the range of the map, nothing is drawn and the status display says "No contour value is within the map range".
    - Not saved in views (as with the XY window settings, 25.2); kept until the window is closed. The same values are used for image export.
    - **A line exactly at the minimum or maximum value of the map is not drawn in any mode** (2026-10-07, fix after review; matplotlib's `contour` does not draw the edge values, so the
      screen and image are aligned). If every listed value falls on an edge, the result is "out of range".
    - The automatic step is decided from the sum of the value ranges of all overlaid maps. If maps with very different value ranges are overlaid together, the narrower map gets fewer lines
      (assumed for comparing quantities of the same kind; specify the interval or list if needed).
  - 3D: map → a semi-transparent surface (`GLSurfacePlotItem`, `translucent`) in the assigned single colour. Curve → not a line placed on the floor (Z = minimum), but a line with **X as the
    horizontal axis and the value as the vertical axis**, drawn in 2D only (it cannot be chosen in 3D; the tooltip of the menu explains why). Since 3D has no legend, a series hidden in the 2D
    legend is not carried over to 3D (hiding is done by unchecking the menu item).
    Where the map's values exceed the displayed range of the measured Z, they are **drawn, and the displayed Z range is widened to include the map** (2026-10-07, user's decision; for comparing a
    design map with measurements; not drawing would leave holes at the peaks).
  - Axis correspondence: the axes of an overlaid map are taken to be the current X and Y (and Z) variables (no matching by name; if the map's axis name (27.7) differs from the variable name,
    the legend shows "axis name: rpm×torque" so the difference can be noticed). The display range follows the current display, and parts outside the map's range are not drawn (the
    out-of-range handling of 27.1 is not used).
  - Empty cells (27.10) are drawn neither as contours nor as surfaces.
  - **A difference display to compare two overlaid maps** is not included in this version (a candidate for a later version).
- When a registered map changes (edited, deleted, or overwritten), the overlay is redrawn or removed.
- Image export includes the overlays, and the notes get one line "Overlay: name, …". The legend and labels of the overlays are also made within the CJK font handling of export 19.2
  (found in review: made outside it, Japanese legend text came out as "□").
- Contour computation runs on the UI thread, so the result is remembered for each map and value setting and recomputed only when the registered map changes (found in review: on a 500 × 500 map
  the screen stalled for about 0.25 s at each redraw).
- Operation log: `xy_plot_overlay` (names added and removed), `xy_plot_contour_levels` (automatic, interval or list, and the values).
- Feature flag: stage 2 also sits under `map_from_xy`. It was kept default OFF for a while after stage 2, but once stages 1 and 2 and the real-machine check of 44.9 were complete, it became **default ON**
  (2026-10-07, user's decision).
- Tests: the menu lists registered maps and curves; add, remove, and hide via the legend; removing a map removes its overlay; 2D contour values (a known `x²+y²` map, equivalent to `sample_map.csv`, made
  in the test, gives circles as contours); the number of surfaces in 3D; image export. Contour values: the automatic step gives about 10 lines at round values; lines are drawn as specified by interval or list;
  invalid input and too-many specifications do not close the dialog; when only out-of-range values are given, nothing is drawn and the status display appears; image export (matplotlib `contour`) uses the same values.

#### 44.8 Relations to other domains

- **Expressions**: an exported map goes through the import, storage and registry path of ch. 27. A map with empty cells is 27.10. Recomputing virtual variables on overwrite uses the same process as 27.8.
- **Data**: windowed reads, `WorkerHub` (ch. 37), cancellation (25.1).
- **Image export**: `pcolormesh`, `hatch`, `plot_surface`, `contour` (the common processing of `image_export`).
- **App foundation**: feature flag `map_from_xy`, the operation log, i18n (en/ja), `settings.last_export_folder`.

#### 44.9 Vertical range of the curve display (added 2026-10-07; a stage 1 defect found in the review of 44.7)

Symptom: when a curve (no Z) is drawn in the 2D map display, the screen's vertical axis is decided only by the curve's values (`_draw_map_curves`). If the values are almost constant (e.g. with
X = sine and Y = cosine, the per-cell mean is almost 0, with an error of about 1e-16), `_pad` widens the range only when its width is exactly 0, so the vertical axis is stretched to the width of the
floating-point error (±1e-15), and the points of "Overlay measured points" all fall outside the screen. Image export (`build_xy_map_figure`) does not specify a range and leaves it to matplotlib's automatic
scaling, so the points are included (±1); the range differs between screen and image.

- **Put the rule for the range into one function** (no Qt dependency; `ui/xy_map.py` or `core/map_grid.py`), used both by the screen and by image export.
  - X: as before, the first to last cell edges of the grid (`cell_edges`).
  - Y: from all the curves' finite values (including series hidden in the legend, so that hiding does not move the display range), plus, if "Overlay measured points" is ON, the finite values of each
    series' measured points (`r.points`); the minimum to maximum of these.
  - Margin: 3% of the width on each side (as the current `_pad`). **Minimum width**: if the width is too small relative to the magnitude of the values (width ≤ 1e-9 × max(|min|, |max|)) or 0, the width is
    taken to be max(|min|, |max|). If the magnitude of the values is also 0 (all 0), the width is taken to be 1. This way a constant curve appears as a horizontal line in the middle.
  - If there is no finite value at all (all cells empty), the Y range is not set (as before). Non-finite values are not passed to pyqtgraph's range (overview; data 29.4).
- Image export: the range from the function above is set with `set_xlim` and `set_ylim` (to match the screen).
- The ranges of scatter, density and the 2D map (heat map) are not changed.
- Limitation: with "Overlay measured points" OFF, and when the curve's values themselves vary only at the level of the error (1e-16), the minimum-width rule does not apply and the error width is shown (the magnitude of
  the values is the same as the error, so they cannot be told apart). With points ON, it displays correctly.
- Tests: (1) a curve of X = sine and Y = cosine with points ON → the Y range on screen includes the range of the points (about ±1); (2) a curve of constant value (e.g. all 5.0) → the range is finite around 5; all 0 → width 1;
  (3) the range does not change with a hidden series; (4) `ax.get_ylim()` of image export matches the screen range; (5) with all cells empty, no exception and no range is set; (6) unit tests of the range function (values and points
  containing NaN/Inf). (1) and (4) must fail before the fix.

#### 44.10 Fixes found in review (2026-10-07, agreed by the user)

Found in the review after merging (2026-10-07).

- **The exported CSV is written only after the registry change has succeeded** (`map_export_dialog`): previously the CSV was written first and then the registry was updated. If the overwrite failed, or `update_registry`
  rolled it back, the CSV (which could be another map's `source_path`) remained rewritten, and a later "reload" accepted values that had not been accepted. → Write to a temporary file, and when the registry change
  succeeds, replace the file with `os.replace`. If it fails, delete the temporary file and leave the original CSV as it is.
- **The minimum-width rule of 44.9 also applies to the heat map's colour range**: when the cell values differ only at the level of error (e.g. X constant and Z = sine), the colour scale becomes ±7e-15 and spurious shading appears
  (`XYMapResult.value_range`). → The colour range uses the same minimum-width rule as the range function of 44.9 (if the width ≤ 1e-9 × the largest absolute value or 0, the width is taken to be the largest absolute value, and 1 if all are 0).
  **The same function is used on the screen and in image export** (colour boundaries and levels are computed in one place).
- **An error when the grid points are not ascending** (`points()` of `map_grid`): if the specification is too fine for the range (e.g. 400 points in `[1e15, 1e15+1]`), floating-point arithmetic gives equal values in sequence, which
  produces zero-width cells and breaks the premise that the axis is ascending. → After creating the points, check `np.diff(p) > 0`; if not satisfied, raise `MapGridError("too_fine")` (translated text, en and ja, e.g. "The grid is too fine for this range").
  Handled the same as the existing "grid invalid" display in the settings panel.
- **A caution when exporting with "Leave empty"**: interpolation needs all four surrounding points, so using a map with many empty cells in an expression makes most rows NaN (99.6% for ring-shaped data). This follows the spec (27.10), but it is
  easy to miss. → On the export screen, when the handling of empty cells is "leave empty" and there are empty cells, show the proportion of empty cells and the note "In an expression, cells around empty cells become NaN. Filling with 'nearest' avoids this." (saving is not blocked).
- **Point count in the status display**: for a curve, the point count was counted once per Y series, so duplicated (60,000 rows gave 120,000). → Count by rows.
- **Overlay on a curve when switching to 3D**: previously it disappeared silently and did not come back when returning to 2D. → The overlay selection is kept; it is not drawn in 3D (overlays of maps drawable in 3D stay as before). Redrawn when returning to 2D.
- Tests: (1) with overwrite where `update_registry` fails (monkeypatched) → the CSV content is as it was and no temporary file remains; (2) X constant, Z = sine → the colour range on screen and in image export are finite and match;
  (3) `[1e15, 1e15+1]` with 400 points → `MapGridError("too_fine")`, an error appears in the settings panel and export is disabled; (4) "leave empty" with empty cells → the note and proportion appear, and do not appear without empty cells;
  (5) the point count on a curve with two Y series equals the row count; (6) curve overlay → 3D → 2D restores the overlay. (1)–(3), (5) and (6) must fail before the fix.

#### 44.11 Remainder of 44.10 (added 2026-10-08, the rest of dev notes item 19)

Two items remained after the merge of the fixes of 44.10.

- **The minimum-width rule also for 3D surface colour range and Z range**: 44.10 used `color_range()` only for the heat map (2D); the 3D surface still used `value_range()` (screen: `XYPlotWindow._show_map_3d_scene` and `_map_bounds_3d`; image export: the 3D case of
  `build_xy_map_figure`). For a map whose cell values differ only at the level of error (e.g. X constant and Z = sine), the colour scale and the Z axis were stretched to about ±1e-15, producing bumps and shading on a flat surface that do not exist. → In 3D too, use `color_range()`
  (the same `min_width_range` as 44.10) for the surface colour, the colour bar and the displayed Z range (the range before widening by overlays, 44.7). Image export uses the same range for `vmin`/`vmax` and `set_zlim`, so that it matches the screen. The rule for widening Z by overlays (44.7) is unchanged (if the widened result is wider, that is used).
- **If replacing the CSV fails, the map registration is also rolled back** (`map_export_dialog.perform_export`): previously the registration (`update_registry`) succeeded first and then the temporary file was replaced with `os.replace`, so if only the replacement failed, the map was registered (its `source_path` pointed at the unchanged CSV), and the user saw only "CSV save failed". → The replacement is moved into `persist` of `update_registry`, as the **last step** of `write_generated_map` (after the registration CSV and JSON have been written and read back successfully). If the replacement fails, the existing rollback of `write_generated_map` (restoring the old files in `maps/`) runs and the exception propagates, and `update_registry` changes nothing (the existing promise "if persist raises, nothing is changed"). The dialog deletes the temporary file and shows "CSV save failed" (the log is `map_from_xy_export_failed`). The original CSV is unchanged because `os.replace` is atomic.
  - Implementation: add `publish: Callable[[], None] | None = None` (called last) to `write_generated_map`. A "CSV only" export that does not register goes through the same path as before (it does not go through `persist`, so it replaces the file on the spot).
- Tests: (1) a map with X constant and Z = sine shown in 3D → the surface colour range, colour bar and Z range on screen are finite and match the image export's `get_zlim()` and colour bar range (the result of `min_width_range`); (2) the existing tests where the overlay widens Z pass;
  (3) export with "both", with `os.replace` made to fail (monkeypatched) → the map is not registered (with overwrite, the original map remains and the files in `maps/` are as they were), the original CSV content is unchanged, and the temporary file
  is not left behind, and a warning appears; (4) "Save as CSV only" with `os.replace` failing → the warning as before, and no temporary file. (1) and (3) must fail before the fix.
- Size: small to medium (Sonnet; the main session checks the review).

#### 44.12 Tidying `xy_plot_window.py` (added 2026-10-08, dev notes item 21)

Purpose: split `ui/xy_plot_window.py` (1,771 lines) into pieces that are easier to change for the later versions of 44 (such as 22, "exclude outlying points by clicking"). **The screen appearance, operations, operation log and image export results do not change** (refactoring only). As a guide, make `xy_plot_window.py` about 1,000 lines or fewer.

Current structure (lines as of `main` on 2026-10-08): calculation of scatter and density (`compute_xy`, up to about line 263); `XYPlotWindow`'s settings panel, 2D/3D switch, calls of the computation, and 3D display; **map operations and drawing** (`_build_map_controls` at lines 467–518, `_map_selected` to `_show_map_3d_scene` at 833–1115, `_add_map_actions` to `export_map_dialog` at 1405–1429); **overlays** (lines 1117–1392; the state is spread over eleven attributes on the window: `_overlay_names`, `_overlay_colors`, `_hidden_overlay`, `_contour_spec`, `_segment_cache`, `_overlay_notice`, `_overlay_levels`, `_overlay_items`, `_overlay_proxies`, `_overlay_menu`/`_overlay_button`); 2D display, legend, cursor and image export.

**(1) Rename: the "Overlay measured points" checkbox**

- `XYPlotWindow._overlay_check` → `_points_check` (its name clashed with the overlay feature, which was confusing). `_on_overlay_toggled` → `_on_points_toggled`. If there is an objectName, change it to match. The translation key (`xy.map_overlay_points`) and the on-screen text do not change. The test references are also updated.

**(2) Overlays: the state is `OverlayState`, the Qt parts are `XYOverlayController`**

- `OverlayState` (added to `ui/xy_overlay.py`; a `dataclass` that **does not depend on Qt**, so it can be unit-tested without creating widgets):
  - Fields: `names: list[str]` (in the order overlaid), `colors: dict[str, str]`, `hidden: set[str]` (names hidden in the 2D legend), `contour_spec: LevelSpec`, `segment_cache` ((name, value) → (table, segments)), `notice: str`, `levels: dict`.
  - Methods (the bodies of the current window methods are moved as they are): `add(name, n_y)` (colour assignment: a colour not used by the Y series being displayed, 44.7), `remove(name)`, `is_hidden(name, mode)` (3D does not use the legend state), `entries(tables, mode, x_name, y_name)` (the current `_overlay_entries`; drops tables that have disappeared; in 3D it skips curves while keeping the selection, 44.10), `plan_levels(entries, mode)` (the current `_planned_levels`, which sets `notice`), `segments(name, table, level)` (contour segments with cache), `prune_cache(tables)` (when the registry changes).
- `XYOverlayController` (`ui/xy_overlay_controller.py`, new): the "Overlay ▾" button and menu (rebuilding, colour swatches, disabling curves in 3D), `set_overlay`, `set_contour_spec`, `choose_contour_levels`, `on_registry_changed`; drawing (`_draw_overlays_2d`, `_draw_overlays_3d`, `_remove_overlay_items`, the legend proxy items); `refresh(rebase)`; and the processing that widens the 3D Z range (`_widened_bounds`, `_rebase_3d_if_needed`). The state is held in one `OverlayState`.
  - Its interaction with the window goes through a `typing.Protocol` (`OverlayHost`) that lists only what is used, not the whole window: `mode`, `selected_y()`, `x_name()`, `result`, `plot`, `view3d`, `registry_tables()`, `add_legend_entry(proxy, label)`, `redraw_scene_3d(r)`, `map_bounds_3d(r)`, `set_status(text)`, `closed`, etc.; only those actually used during implementation.
- `XYPlotWindow` keeps the current public methods as thin delegations (`overlay_names`, `set_overlay`, `set_contour_spec`, `contour_spec`, `choose_contour_levels`, `contour_plan`, `on_registry_changed`, `set_overlay_hidden`). The calls from `MainWindow`, image export and the tests do not change. Internal attributes that the tests touch directly (`_overlay_names`, `_overlay_colors`, `_hidden_overlay`, `_segment_cache`, `_overlay_menu`, `_overlay_notice`, etc., in `test_xy_overlay.py` and `test_xy_map.py`) are changed to their new locations (e.g. `window._overlays.state.names`). What they check is not weakened.
- The status display (`set_status` appends the overlay note) and the click-to-hide handling on the legend (`_on_legend_clicked`) stay in the window, reading `OverlayState.hidden` and `notice`.

**(3) Map operations and drawing: `ui/xy_map_view.py` (new)**

- The map methods listed above (the map parts of the settings panel, constraints, `_make_job_map`, `_show_map`, `_draw_map_*`, `_show_map_3d*`, `_update_status_map`, the export operations) are moved **as they are** into `XYMapViewMixin` (`XYPlotWindow(XYMapViewMixin, AnalysisWindow)`). The map part shares a lot of state with the settings panel, the computation, the 2D/3D switch and `self` (`_result`, `_plot`, `_view3d`, `_mode`, etc.), so it is not split into components here; it is only moved (making components would take much effort and make things more dangerous without making the work of 22 easier).
- The class docstring of the mixin lists the attributes and methods of `XYPlotWindow` that the mixin uses (so that the dependencies are visible).
- The pure calculation and range functions (`xy_map.py`) stay as they are.

**(4) How to split the commits** (dev rules rule 7)

1. The rename (1) only.
2. The move to the mixin (3) only. No changes to content.
3. `OverlayState` and `XYOverlayController` (2). Because it includes moving methods and consolidating state, it is treated as a content change; the updates of test attribute references go in the same commit.
4. Add `xy_overlay_controller` and `xy_map_view` to the module structure diagram in overview 5.1 and to the directory listing (this may be in the same commit as 3).

**Test plan**

- The existing `test_xy_plot*.py`, `test_xy_map.py`, `test_xy_overlay.py` and `test_xyz_3d*.py` pass (except for the reference updates). The full test suite passes at each commit.
- Unit tests of `OverlayState` (new, no Qt): colour assignment (with one Y, the first overlay gets the second colour; removing and re-overlaying reuses a free colour); `entries` drops a table that has disappeared; in 3D it skips curves but keeps the selection; `is_hidden` is false in 3D; the notes of `plan_levels` (out of range, invalid specification); `segments` cache (no recomputation for the same table, recomputation when the table changes); `prune_cache`.
- Structure check: `xy_plot_window.py` no longer contains the overlay state attributes such as `_overlay_names` (a simple test that reads the source).
- Exercise it: open the XY window without a screen, go through scatter, map (2D/3D), curve, overlays (contour and curve), hide in the legend, the contour values and the export dialog; render the 2D screen and image export before and after the tidy-up and compare the images (they must be the same).

Size: medium (Sonnet and reviewer). Start after 20 is merged (it does not touch the same files as 20, but the limit of two parallel tasks applies).

**Result of the implementation (2026-10-08, confirmed in review)**

- `xy_plot_window.py` went from 1,771 lines to 1,186 lines, which did not reach the guide of about 1,000. What remains is `compute_xy` (about 150 lines), `build_xy_figure`, the settings panel, and the 2D/3D switch and display. The work of 22 mainly goes into `xy_map_view.py` and `xy_map.py`, so it is not split further (moving `compute_xy` and others to `xy_compute.py` would bring it to about 950 lines, but the tests replace `XY_*` constants and `_colormap` in `xy_plot_window`, so the effort does not pay off).
- Besides the ranges above, `_map_bounds_3d`, `_on_mouse_moved` (hover on the 2D map) and `xm_agg_keys` were also moved to the mixin.
- The mixin imports `xy_plot_window` late, inside `_make_job_map`, `_draw_map_heat` and `_draw_map_curves`, to read the constants (so that the test replacements take effect and a circular import is avoided).
- For `OverlayHost`, the window gained read-only properties `result`, `plot`, `legend`, `view3d`, `closed` and `status_core`, plus `x_name()`, `registry_tables()`, `add_legend_entry()`, `redraw_scene_3d()`, `map_bounds_3d()` and `dialog_parent()`. The controller is created before the window's `super().__init__` (because the "Overlay ▾" button is created inside the settings panel).
- "Does not depend on Qt" means "can be unit-tested without creating widgets" (`xy_overlay.py` itself imports pyqtgraph and `tr()`).

#### 44.13 Excluding outlying points and leaving missing cells empty (added 2026-10-08, dev notes item 22)

Requirement: 5.39. Mainly touches `ui/xy_map_view.py` (`XYMapViewMixin`, 44.12), `ui/xy_map.py` (`compute_xy_map`) and `core/map_grid.py`. The overlay display (`XYOverlayController`), scatter and density displays are not changed.

**(1) How excluded rows are held**

- The window holds `_excluded_rows: np.ndarray` (row numbers of the file, `int64`, ascending, no duplicates), starting empty.
- **When to clear**: when the choice of the X, Y or Z variables changes, and when the file or the time axis changes (also on any path that does not recreate the window). Once cleared, the status display shows "Excluded points restored (the variables changed)" once (`tr`, en and ja). It is **not** cleared by changing the row range (computed over the displayed range), the grid, the aggregation, the minimum count, the handling of empty cells, or the 2D/3D switch (rows outside the range simply do not affect the computation).
- Not saved in views, settings or map JSON (only the count is left in the `origin` of the exported map, see (5)).

**(2) Computation (`compute_xy_map`)**

- Add the argument `excluded_rows: np.ndarray | None = None`. Each chunk `[a, b)` returned by `chunk_iter` has the rows of `excluded_rows` that fall in `[a, b)` removed (using `np.searchsorted`) before it is returned. Both the **automatic range determination (`scan_bounds`) and the aggregation (`grid_aggregate`)** go through this `chunk_iter`, so the excluded points affect neither the initial range, nor the counts, nor the values.
- Excluded rows are counted as neither valid nor excluded (non-finite). Add `XYMapResult.n_user_excluded` (the number of excluded rows within the row range).
- **Row numbers of drawn points**: add `XYMapResult.point_rows: list[np.ndarray]` (same order and length as `points`, `int64`). Thinning (`stride`) is as it is now. This is used to find the row of a clicked point.
- **Coordinates of excluded points**: add `XYMapResult.excluded_points: list[tuple[np.ndarray, ...]]` (same shape as `points`) and `excluded_point_rows`. The values of the excluded rows within the row range are read (non-finite rows are dropped, since they cannot be drawn). The number drawn is limited to `XY_EXCLUDED_DRAW_LIMIT` (20,000); above that, they are thinned at equal intervals, and the status display says "excluded points n (of which shown m)".
- Curves (several Ys): excluding a row removes it from **all the Y curves** (since exclusion is per row).

**(3) Selection operation (2D map and curve)**

- In the row of map controls in the settings panel, add **"Exclude points"** (a toggle button, default OFF) and **"Restore all"** (enabled only when there are excluded rows). In 3D, "Exclude points" is disabled, with the tooltip "You can select points in 2D"; "Restore all" also works in 3D.
- When "Exclude points" is switched ON and "Overlay measured points" is OFF, the latter is switched ON (points that are not visible cannot be selected).
- The mouse on the 2D plot while "Exclude points" is ON:
  - **Click**: find the nearest point on screen within 6 px, among both the drawn measured points and the × marks of excluded points. A measured point → that row is excluded; a × → it is restored. If nothing is within 6 px, nothing happens.
  - **Left drag**: instead of panning, draws a rectangle; on release, **all** rows whose values fall inside the rectangle's **value range** are excluded. (Since the drawn points are thinned, the selection is made from all the rows of the range `[row_start, row_end)`, not from the drawn points; read in chunks of `XY_CHUNK_ROWS` in the worker. For a map, a row counts if (X, Y) is inside; for a curve, if (X, each Y) is inside. Ys hidden in the legend are not included.) The selection follows the same cancellation and generation rules as the computation job (data ch. 37); when it finishes, the map is recomputed.
  - Wheel zoom and right-drag are as they are now.
  - When "Exclude points" is OFF, behaviour is as now (dragging pans, and clicking excludes nothing).
- **The map is recomputed each time the excluded rows change** (through the current computation job path; old results are discarded by the generation number). If clicks are made in succession, the older computation is discarded.
- Operation log: `xy_map_exclude` at the start of the operation, with `kind` (`click_exclude`/`click_restore`/`rect`/`reset`/`cleared`), the number of rows changed, and the total number excluded. (The first argument of `log_op` is `action`, so the detailed key is `kind`.)

**(4) Display**

- On the 2D map and curve: excluded points are drawn as **grey ×** (`symbol="x"`, size 7, `(120, 120, 120)`) over the measured points (not drawn if "Overlay measured points" is OFF). Not drawn in 3D.
- Status display: "excluded points n" is added to the current items (not shown when 0).
- Image export (25.1): in 2D, excluded points are drawn as × (when points are overlaid). The "excluded points n" is added to the map line of the notes (not written when 0).

**(5) Leaving missing cells empty**

- Next to the minimum count N, add **"Leave sparse cells empty"** (checkbox, default OFF). It is disabled when the handling of empty cells is "leave empty", or the aggregation is "count" (it has no meaning then).
- When ON, cells whose count satisfies `1 ≤ count < min_count` (cells with points but too few) are **left empty instead of being filled** by the handling of empty cells. Cells with count 0 are filled as now.
- `core/map_grid.py`: `grid_aggregate(…, keep_sparse_empty: bool = False)`. The filling computation (`fill_empty`) is as it is; after filling, sparse cells are set back to NaN and removed from `filled_mask` (inside the filling computation, sparse cells are treated as unknown values, just like empty ones). Add `GridResult.sparse_mask` (`1 ≤ count < min_count`).
- Hover: a sparse cell shows "empty (too few points: count n)". Export: an empty cell (27.10).
- Operation log: `keep_sparse_empty` in `xy_plot_compute`; the export `origin` (44.4) gets `"keep_sparse_empty"` and `"excluded_rows"` (the number of excluded rows) added (under the rule that unknown keys are ignored, older versions can still read it).

**(6) Test plan**

- Core (`test_map_grid.py` and similar): with `keep_sparse_empty`, cells with counts 1 to N−1 are NaN, not in `filled_mask`, and in `sparse_mask`; cells with count 0 are filled; when OFF, the same as now (for all `fill` values). The same for curves (one-dimensional).
- `compute_xy_map`: excluded rows have no effect on the automatic range, the counts or the values (when the excluded rows are only outliers, the range does not include them); `n_user_excluded` and `n_valid` counts; `point_rows` correspond to `points` (indexing the original arrays by row number gives the same values); `excluded_points`; on a curve, a row is removed from all Ys; if `excluded_rows` contains only rows outside the range, the result is the same as without exclusion; rows at chunk boundaries (with small `chunk_rows`).
- UI (`test_xy_map.py`, using `QTest` with real clicks and drags on the viewport): with "Exclude points" ON, clicking a point excludes that row, the map is recomputed, and a × is drawn. Clicking the × restores it. Dragging a rectangle excludes all rows whose values fall in it (including rows not drawn because of thinning; confirm by making `XY_SCATTER_LIMIT` small). With it OFF, dragging pans and nothing is excluded. "Restore all". In 3D, "Exclude points" is disabled, and the result of exclusion appears on the surface. Changing X, Y or Z clears the excluded rows; changing the grid or the range keeps them. Turning "Exclude points" ON turns "Overlay measured points" ON.
- Export: excluded points and sparse cells are reflected in the exported CSV and the registered map, and the count appears in `origin`.
- Image export: × for excluded points and "excluded points n" in the notes (check by drawing the image).
- Visual check: add a few outliers to `feature_test.parquet` or to generated data, and check the 2D map before and after excluding them, and the 3D surface, in images.

**(7) Changes decided in review (2026-10-08, reviewer)**

- **Display range**: on recomputation caused by excluding or restoring (clicks, rectangle, clicking ×), **the current display range is kept** (so that further exclusion is possible, and a × just excluded remains visible on screen to be restored). The generation of that recomputation carries a "keep range" mark; if a recomputation caused by a settings change arrives later, it follows the grid as now. "Restore all", changes of settings, and "Auto" still fit the grid. It is not fitted to the range containing the grid and all × marks (one distant outlier would leave the display zoomed out, weakening the point of excluding). Image export keeps the grid range (× marks outside the grid are not drawn; the count of excluded points is kept in the notes as "excluded points n").
- **Rectangle when the variables change**: when the X, Y or Z selection changes, the rectangle job in progress is made stale (the generation is raised) even if no rows were excluded. The job records the set of variables it was issued for, and when its result arrives and the current set differs, it is discarded. While the displayed result's variables differ from the combo selections (waiting for recomputation), clicks and rectangles are not accepted.
- **The "excluded points restored" notice** is shown only once (cleared at the next status display update; not left in the map's notes).
- **Union of rectangle-selected rows** with the currently excluded rows (`union`) is done **inside the worker** (merged with a copy of the excluded rows taken when the job is issued; old results are discarded by the generation number). If a rectangle covers most of a 100-million-row range, it can be several million to tens of millions of rows (8 bytes per row), and arranging them on the UI thread would freeze the screen. While selecting, the status display shows "Selecting…". If the rectangle contains no points, a notice is shown and no recomputation is done.
- **When there are zero valid points** (for example, all are excluded), or when the median is refused, the × marks of excluded points are still drawn (they are read before the early return, so the early path can still restore them).
- **Operation log**: since the first argument of `log_op` is `action`, the detail key is `kind`. When the rectangle job finishes, the number of rows actually added is recorded with `kind="rect_done"`. Switching "Exclude points" is recorded with `xy_map_exclude_mode` (`enabled`).
- **Export note**: when "Leave sparse cells empty" is ON and there are empty cells, the text is not "filling with the nearest value avoids this" but "Turning off 'Leave sparse cells empty' fills them" (en and ja).
- Clicking an old × after it has been restored (the row is no longer excluded) does nothing (no log, no recomputation).
- The hover text follows the form of the current hover: "count: n (empty: too few points)".

**(8) Hidden behind a feature flag (added 2026-10-13, user's decision)**

- Because the operation was hard to understand in the real-machine check, 44.13 (exclude points, restore all, leave sparse cells empty) is placed behind the feature flag **`map_exclude_points` (default OFF)** (ch. 21 of app). When it is decided to use them, only the default needs to be set to ON to restore them.
- When OFF: the "Exclude points", "Restore all" and "Leave sparse cells empty" controls are not created (they do not appear in the map settings row). Excluded rows are always empty, the mouse behaves as before (left drag pans), and `keep_sparse_empty` is always `False` (no ×, no "excluded points n", no empty sparse cells). The computation (`excluded_rows` and `keep_sparse_empty` of `compute_xy_map`, and `grid_aggregate`) and its tests remain independent of the flag.
- Test plan: the UI tests of 44.13 run with the flag ON (`monkeypatch.setitem(features._EFFECTIVE, "map_exclude_points", True)`). Add, with it OFF: the three controls are absent when a map is selected; left drag pans; the map computation receives `keep_sparse_empty=False` and zero excluded rows.
- Updating the list of derived variables does not clear excluded rows. Adding or removing Ys in a curve is a change of the Y selection, so it clears excluded rows (2026-10-08, user's agreement).
- For the exported map's `origin`, only the **count** of excluded rows is kept, not the row numbers (2026-10-08, user's decision).
- Tests to add: while a rectangle is being dragged, changing a variable excludes nothing; changing the row range keeps the excluded rows; the notice disappears after a click; the display range is kept after a click, and "Restore all" returns it to the grid; when all points are excluded, the × marks are drawn.

#### 44.14 Frequency of two variables (time as a map aggregate, added 2026-10-14, stage 3)

Requirement: 5.48. Adds "**time**" to the aggregations of the XY map display (44.1–44.4). The frequency of a two-variable combination (e.g. rpm × torque: how many hours were spent at each combination) is used on the same screen as the current "count" (2D heat map and 3D surface) and in the same exports (CSV and registration as a map). The feature flag remains `map_from_xy`.

- Aggregation choices: mean, median, max, min, count, and **time**. "Time" can be chosen for a map (X and Y, no Z) and also for a curve (X only); it does not use the value variable Z (as with "count").
- **Time weights**: as in 42.3, each row's time `w_i = x_{i+1} − x_i` (the row's value is regarded as continuing until the next row; the last row of the range has 0; and a value above 5 times the median adjacent interval, the automatic gap value, counts as 0). `w_i` is summed per cell (`weights` of `np.bincount`).
  - The time-axis column is read together with the others through `chunk_iter`. Each chunk is read one row further, to get the next row's time (as in 42.3). The automatic gap value uses the same function as in 42.3 (the common function in `core/histogram.py` is used; the same calculation is not written again on the map side).
  - If the time axis is not ascending within the range (`check_time_order`), "time" is not computed (the reason is shown, as in 42.3).
  - A map cannot be chosen if the time-axis column is selected for X or Y (44.3), so this case does not arise here.
- Time unit: when the aggregation is "time", a unit combo (seconds, minutes, hours; default hours) appears. Cell value = total time / unit.
- Minimum count N and handling of empty cells: not used, as with "count" (time is valid for all cells; cells with no points are 0).
- Display: colour and surface height = time. The colour bar, legend and hover show the unit (e.g. "time: 1.25 h"). The status display shows the total valid time and the number of gaps.
- Export (44.4): the values are in the selected unit of time. The `agg` in `origin` is `"time"`, and `time_unit` is added. The default name is `<X>_<Y>_time` (for a curve, `<X>_time`).
- Operation log: `agg="time"` and `time_unit` in `xy_plot_compute`.
- Tests: synthetic data where (x, y) is known to stay in each cell for a known time → the cell values match in seconds, minutes and hours; gap time is not included; the last row is 0; chunk boundaries (small `XY_CHUNK_ROWS`) give the same result as one pass; the same for a curve; no computation if the time axis is not ascending; CSV values and `origin`. Screen: with aggregation "time" the unit combo appears; 2D and 3D display; the unit appears in the notes of image export.


## Relations to other domains

- **Plots** ([plots.md](./plots.md)): the target range is the linked group's "visible range"; from
  an unlinked plot, "Stats for this range". Notification of cursor positions (`cursorsChanged`).
- **Data** ([data.md](./data.md)): raw data of a range is read with `variable_window`. Computation
  runs in `WorkerHub` (ch. 37). Statistics over the whole of a large file are aggregated window by
  window (`chunked_stats`, 36.8).
- **Export** ([export.md](./export.md)): the analysis windows' image export shares `image_export`'s
  save routine, annotation and CJK font support.
- **Expressions** ([expressions.md](./expressions.md)): the 3D widget is also used for the map
  preview.
- **App foundation** ([app.md](./app.md)): feature flags `fft`, `xy_plot`, `xyz_3d`, `histogram`
  (ch. 42), `map_from_xy` (ch. 44).
- **Data** (ch. 28, 37, 2026-10-03): analysis-window computations run in `WorkerHub`; failures are
  shown inside the window, and cancellation is supported.
- **App foundation** (ch. 30): analysis-window computations are recorded in the operation log as
  `job="analysis"`.
- **Expressions** (ch. 44, 2026-10-06): maps and curves made from the XY map display are registered through the
  route of ch. 27. Maps with empty cells (27.10) can be made.
- **Plots, export** (42.12, 42.13, 2026-10-14): the horizontal axis of the trend uses the same tick marks as the
  main window's time axis (local time), and image export uses the same format as `image_export`'s time ticks. The
  local-time conversion for time-of-day and time-window narrowing uses the same function as the groups of 42.3.
- **Data** (44.14): the map aggregate "time" also reads the time-axis column in chunks, and its automatic gap value
  uses the same function as 42.3 (`core/histogram.py`).
