# Analysis (statistics, FFT, XY/XYZ)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: statistics display, frequency analysis (FFT, currently hidden by a
feature flag), XY/XYZ plots and the "analysis window" framework they use. A histogram will come
here later (`idea.md`).

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

#### 5.25 Checking the sample spacing before FFT (added 2026-10-02, from fact-checking an external review)
- FFT assumes evenly spaced samples, but the spacing of the target range isn't checked. **Before
  turning FFT back on with its feature flag**, check the sample spacing of the target range before
  computing, and if it varies a lot, say "FFT needs evenly spaced samples" and don't compute (ask
  the user to narrow the range).
- Details in [ch. 32](#32-checking-the-sample-spacing-before-fft-added-2026-10-02-from-fact-checking-an-external-review).

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

### 32. Checking the sample spacing before FFT (added 2026-10-02, from fact-checking an external review)

Requirement: 5.25. Background: `analysis.fft` only decides the sampling interval with
`dt = median(diff(x))` and doesn't look at the variation (external review #4 in `idea.md`). FFT is
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
- **App foundation** ([app.md](./app.md)): feature flags `fft`, `xy_plot`, `xyz_3d`.
- **Data** (ch. 28, 37, 2026-10-03): analysis-window computations run in `WorkerHub`; failures are
  shown inside the window, and cancellation is supported.
- **App foundation** (ch. 30): analysis-window computations are recorded in the operation log as
  `job="analysis"`.
