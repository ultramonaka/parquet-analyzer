# Data (reading, time axis, drawing performance, conversion tool)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: reading Parquet files (lazy and windowed reads), choosing the time-axis
column, downsampling and running it asynchronously, the drawing indicator, choosing the data folder,
the MDF/MATLAB→Parquet conversion tool.

## Requirements

#### 5.4 Automatic downsampling
- Purpose: drawing large data (millions of points) as is makes reading and drawing slow, so it is
  thinned to the number of points needed for display.
- Method: the **LTTB (Largest-Triangle-Three-Buckets)** algorithm. Unlike simple thinning (evenly
  spaced sampling), it reduces the number of points while keeping the visual features of the waveform
  (peaks, troughs) as far as possible.
- Behavior:
  - With the display window (the number of screen pixels across the plot area, e.g. at most about 2,400
    points for a 1,200 px width = 2 points per pixel) as the limit, downsampling is applied when the
    number of data points in range exceeds it.
  - Every time zoom/pan changes the visible range it is recomputed for that range (it can also be
    recomputed explicitly with the R button). This automatic recompute can be turned off in the
    settings (the `auto_redraw` setting, see [persistence 5.10](./persistence.md)). Even when off,
    manual recompute via the R button/shortcut always works.
  - The original data is kept in memory (or a cache); downsampling is a temporary thinning for display
    and never destroys the original data.
  - The recompute runs asynchronously on a separate thread so it doesn't block the UI thread (details in
    [detailed_specification ch. 10](#10-asynchronous-downsampling-recompute-settled)), to avoid the UI
    freezing on every zoom with large data.
  - While recomputing, the status bar shows a progress indicator (indeterminate) (see
    [persistence 5.10](./persistence.md)).

#### 5.9 Choosing the data folder
- When opening a Parquet file, the folder to look in can be chosen (a folder dialog).
- The default is **the folder last looked in**.
- **Five** recently used folders are kept as candidates so they can be picked again quickly inside the
  dialog.
- Details in [detailed_specification ch. 7](#7-choosing-the-data-source-and-folder-history).

### 6. Data processing

- Parquet is read with `polars` (lazy evaluation with `scan_parquet`) (see 11.6).
- For large files, lazy reads and reading only the needed range are considered wherever possible.

#### 5.22 Reporting background-job failures, checking the time-axis order (added 2026-10-02)
- **Even if an exception occurs in work running in the background (downsampling, building the envelope
  for the coarse display, statistics, FFT, future analysis windows), the "drawing" indicator must not stay
  on forever, and that column's processing must not stay stuck afterwards.** The user is told about the
  failure (in the status bar or in the result display) and the details are logged. The previous display
  stays as it is.
- **If the column chosen as the time axis isn't ascending (runs of equal values are fine) or contains
  missing values (NaN), warn.** Visible ranges, statistics and cursor values are computed assuming an
  ascending time axis, so using a non-ascending column as the time axis can give wrong results. The
  warning is given when opening a file and when switching the time-axis column, and stays shown in the
  status bar (not modal). Sorting, or computing correctly with a non-ascending time axis, isn't supported
  in this version (to be considered in the future large-data design). (Added 2026-10-03, found on the real
  machine) However, **using a non-ascending or missing-value column as the time axis must never freeze or
  crash the app** (after the warning, display continues as far as it can).
- Details in [ch. 28](#28-reporting-background-job-failures-added-2026-10-02)
  and [ch. 29](#29-checking-the-time-axis-order-added-2026-10-02).

#### 5.29 The next stage of large-data support (added 2026-10-02)
- Handle only the visible range of the time-axis column and of derived variables' results too, keeping
  memory use down even for files of around 100 million rows. In addition, manage the windowed-read cache
  by memory size, and make background work that is no longer needed stoppable midway.
- 2026-10-04: the time-axis index, windowed computation of derived variables and the thinned first draw
  were adopted and implemented (cache memory management and cancellation were not adopted). The design is
  in [36.8](#368-the-adopted-design-2026-10-04-from-the-367-decision-and-the-reviewers-review).

#### 5.46 Aggregating statistics over a whole column window by window
- When "Statistics" is pressed on a large file (a windowed column), the rows of the displayed range are not read all
  at once; they are read in chunks and aggregated as they go. The 4–6 GB needed for statistics over the whole of a
  100-million-row column (36.7) is brought down to about the amount resident after drawing.
- The reading is not done on the UI thread but inside background work (today the UI thread reads all rows, so the
  screen freezes meanwhile).
- The results (count, missing, min, max, mean, standard deviation, median) are the same as now (the median stays exact).
- Separating `values()` (the read-everything API) and making it explicit by name is specified later as stage 2
  (user's decision, 2026-10-13).
- Details in [ch. 53].

#### 5.30 Reliably releasing background-job parts and protecting UI objects (added 2026-10-03, from the 13.6 investigation)
- When background work (downsampling, envelopes, statistics, FFT, analysis windows) finishes, the parts
  made for it and the data passed to it are reliably released (previously they weren't, and memory grew
  the more the app was used).
- Python's cleanup of data no longer used (garbage collection) runs only on the UI thread, preventing
  crashes from UI objects being destroyed on a background thread.
- Details in [ch. 37](#37-releasing-background-job-parts-and-garbage-collection-added-2026-10-03-from-the-136-investigation).

#### 5.50 Time-axis order, continued: statistics warnings, duplicate times, sorted display, time zones (added 2026-10-15, user request)
- The status-bar warning (5.22) stays as it is (user's decision).
- When the time axis is not ascending, **screens that show range-based values also show a warning**: the statistics result, analysis
  windows that use a range (XY, histogram, FFT), cursor readouts and the hover display. The values are still shown as before.
- **Duplicate times** (places where the same time repeats) are counted and the count is shown in the status-bar warning (calculations are
  still correct, so this is shown as a "notice", separate from not-ascending / missing).
- A feature to **display sorted by the time axis** is added. It can be switched on and off (default off). Even after sorting, the
  correspondence with the **original row number** is kept, and the original row number can be seen in the cursor, the hover and
  exported files.
- A time axis of **time-zone-aware times** is displayed in **UTC**.
- Tests are added for time axes with duplicate times, NaN, infinity and time-zone-aware times.
- Details in [ch. 56](#56-time-axis-order-continued-added-2026-10-15).

#### 5.52 Withstanding data of 500 million rows x 50 columns (added 2026-10-15, user request; stage 1 is measurement)
- Parquet files of about 500 million rows and 50 columns are used for analysis (none on hand yet). Reduced from the original 1 billion rows x
  100 columns (2026-10-15, user's decision; stage 1 measurements were already done at 1 billion rows, so they serve as the upper-bound result).
  Processing that uses all rows (views, export, histograms, etc.) is heavy.
- Target: the main operations (open, open a view, whole-column statistics, histogram) **within 20 seconds**. The memory limit can be changed
  per environment in the settings (e.g. up to 95% of physical memory). Calculations that use all rows **prioritize accuracy** (no
  approximation).
- Places expected to get faster (virtual-variable computation, statistics, etc.) are run in parallel on **multiple cores**.
- Whether to design the time axis so that it is not held entirely in memory either will be decided as work proceeds.
- Stage 1 is **measurement only**: write a script that creates test files (about 20 columns), generate files, and measure where it gets stuck.
  The policy for stage 2 is decided from the results (user's decision, 2026-10-15).
- Details in [ch. 58](#58-withstanding-data-of-500-million-rows-x-50-columns-added-2026-10-15).

#### 5.53 Correctness and boundary conditions on large data (added 2026-10-15, from external review 3)
- At the scale of 500 million rows, confirm by measurement that results (statistics, histograms, virtual variables, displayed range,
  pyramid, export) are correct. The current measurements look only at time and memory.
- Premises that rely on pyarrow's current behavior, such as the pyramid bucket positions, are guaranteed by our own code and fixed by tests.
- Verify with random data the cases where NaN, Inf, duplicate times or float32 lie at bucket, anchor and row-group boundaries.
- Background work that never stops, memory limits and fallback to full computation are handled in stage 2 of ch. 58 (not in ch. 59).
- Details in [ch. 59](#59-correctness-and-boundary-conditions-on-large-data-added-2026-10-15).

## Design

#### 4.2 Choosing the column used as the time (X) axis

- By default the time axis is the **first column** (the first column of the loaded Parquet file). It used
  to be a heuristic that preferred a column named `time`/`timestamp`/`datetime`, but there was no way for
  the user to choose explicitly, so it was effectively fixed. After the request "the first column being
  time is basically fine, but let me change it freely", it changed to "first column by default + freely
  changeable from the toolbar" (the heuristic was removed).
- The toolbar's "Time axis:" combo box (`MainWindow.time_axis_combo`) lists every column of the loaded
  file, and changing the selection calls `MainWindow.set_time_column(name)`.
- `MainWindow._file_columns` keeps every column of the loaded file (converted to numbers), and
  `_raw_variables` is rebuilt each time as `_file_columns` minus the current time-axis column. So switching
  the time axis doesn't lose the old time-axis column; it reappears in the variable panel as an ordinary
  variable (derived variables' cached values carry over, since their definitions remain in `_derived`).
- On switching, `PlotGridWidget.set_time_axis_data` replaces the X arrays of every series of every
  time-type plot (`x_axis_datetime=True`) with the new time-axis data and refits the visible range with
  `go_home()` (columns can have completely different scales and ranges, so keeping the old zoom range is
  meaningless). Frequency-analysis plots (X axis in Hz) are excluded.
- The view (`view.py`) schema doesn't save the time-axis column choice (the file's first column is always
  the default). If remembering it per view is wanted in future, add a `time_column` field to `View`.

### 7. Choosing the data source and folder history

- "Open" in the menu/toolbar opens a folder dialog to choose a Parquet file.
- The dialog initially shows **the folder last looked in** (`last_data_folder`).
- Recently used folders are kept as an MRU (Most Recently Used) list of **at most 5**
  (`recent_data_folders`) and shown as quick candidates in the dialog.
  - Each newly visited folder is added at the front, duplicates are removed, and entries beyond 5 are
    discarded oldest first.
  - These 5 can be switched to at once by clicking them directly in a dropdown or similar.
- Saved in `cfg/parquet_analyzer/settings.json` ([persistence ch. 9](./persistence.md)).

#### 9.2 Drawing indicator

- `TimePlotWidget` emits `downsampleStarted`/`downsampleFinished` every time it starts/finishes a
  downsampling recompute worker. `PlotGridWidget` totals them over all plots and emits `busyChanged(True)`
  when the running job count goes 0→1 and `busyChanged(False)` on 1→0.
- `MainWindow` has an indeterminate (marquee) `QProgressBar` in the status bar and shows/hides it per
  `busyChanged`. Individual LTTB computations don't report progress, so it shows "running or not" rather
  than a percentage. Visual feedback for the asynchronous work that doesn't block the UI thread
  ([ch. 10](#10-asynchronous-downsampling-recompute-settled)).

### 10. Asynchronous downsampling recompute (settled)

- Doing the LTTB recompute on the main thread on every zoom/pan blocks UI operations with large data and
  may break the non-functional requirement of `specification.ja.md` ch. 9 (UI operations reflected within a
  few seconds). So it was settled that the downsampling recompute runs asynchronously on a `QThreadPool`
  (`BackgroundWorker`, a `QRunnable`, see 10.2).
- A new job is issued every time the visible range changes, but each job carries **a per-series
  generation counter**, and a stale generation's result arriving later is discarded (preventing old
  recompute results from overwriting the plot during consecutive zooms). The generation is kept **per
  series** as `TimePlotWidget._series[name]["generation"]`. There was a time when one counter was shared by
  the whole plot; with two or more overlaid series, every redraw advanced the counter once per series, so
  every series but the last processed was always judged "stale" and its result discarded (the core overlay
  feature was effectively broken; found and fixed in review).
- The worker's completion is returned to the UI thread via a Qt signal (thread-safe), and `TimePlotWidget`
  receives it and updates the drawing.
- `PlotGridWidget.remove_plot` doesn't call `deleteLater()` while the plot has
  `pending_downsample_count() > 0` (jobs running on worker threads); it deletes after confirming via the
  `downsampleFinished` signal that the job count reached 0. Calling `deleteLater()` on the plot right away
  risks a crash when a queued signal from a worker reaches the (deleted) widget (the same kind of measure as
  for `DownsampleWorker`'s own lifetime; the job count of [9.2](#92-drawing-indicator) depends on this
  mechanism too).
- Why it was made asynchronous from the start rather than "implement it simply on the main thread first and
  move to threads if it feels slow": LTTB's cost is proportional to the number of points, so on the main
  thread large data (about 13.15 million rows in the test data) very likely freezes the UI on every zoom,
  and designing it asynchronous from the start costs less than reworking it later.

#### 10.1 LTTB NaN handling and performance

- `lttb()` in `core/downsample.py` had a bug: with even one NaN in a bucket, a naive `np.mean` made the area
  computation of that bucket's candidates NaN as a whole, so the real peaks/troughs weren't chosen and it
  degraded to nearly always picking the first point (missing values are common in sensor logs, so this did
  real harm; found and fixed in review).
- The fix avoided NaN with `np.nanmean` + `np.nan_to_num(..., nan=-np.inf)`, but `np.nanmean` is about 5×
  slower than `np.mean`, and calling it **once per bucket** (plot width × `downsample_pixel_ratio`,
  typically over 2,000) each time worsened 13M rows → 2,400 points from about 37.5 ms to about 104 ms (found
  from real-machine feedback that redrawing had become slow).
- Final fix: check `np.isfinite(x).all() and np.isfinite(y).all()` once at the start of the call (about
  2 ms for 13M elements) and use `np.nanmean` only when NaN/Inf is actually present (otherwise the original
  `np.mean`). The fast path on NaN-free data was confirmed to give bit-identical output to the original
  implementation. NaN is rare in actual use, so most redraws get NaN safety without any slowdown.

#### 10.2 Merging into BackgroundWorker and separating thread pools (2026-09-18)

While adding FFT (13.5.1) and statistics (ch. 14), a code review pointed out that three `QRunnable`
subclasses, `DownsampleWorker`, `FFTWorker` and `StatsWorker`, each duplicated almost entirely the same
safety pattern "`setAutoDelete(False)` + the caller holds a strong reference until `finished` has been
handled" (the pattern itself is required for the reasons in ch. 10; omitting it can crash).

- Merged into `BackgroundWorker` in `core/background_worker.py`. Given any `Callable[[], object]` in its
  constructor, `run()` calls it on the worker thread and returns the result as is via `signals.finished`
  (`Signal(object)`). The downsampling generation (for stale checks) is no longer the responsibility of
  `BackgroundWorker` itself; it is included in the tuple returned by the closure the caller
  (`plot_widget.py`) passes and checked on the receiving side (`DownsampleWorker`'s dedicated `generation`
  constructor argument was dropped and generalized).
- `FFTWorker`/`StatsWorker` were deleted, and the caller (`MainWindow`) builds them directly as
  `BackgroundWorker(lambda: fft(x, y))`.
- **Analysis jobs (FFT, statistics) and pan/zoom redraw jobs were separated into different
  `QThreadPool`s** (`MainWindow._analysis_thread_pool`; `plot_widget.py` uses
  `QThreadPool.globalInstance()` as before). Statistics submit one worker per selected variable at once
  (ch. 14), so if they shared `QThreadPool.globalInstance()`, computing statistics of several variables on
  large data could temporarily occupy the threads for the pan/zoom downsampling recompute and hurt
  responsiveness while operating — separated after that code-review remark.

### 15. MDF/MATLAB → Parquet conversion tool (added 2026-09-18)

`tools/convert_to_parquet.sh` (CLI in `src/parquet_analyzer/convert_cli.py`, implementation in
`core/convert.py`). The app reads only Parquet, so conversion from measurement formats (MDF/MATLAB) is
provided as a separate tool.

#### 15.1 Why not read them directly

MDF and MATLAB are both "measurement/recording" formats, not structured for the column-oriented selective
reads Parquet assumes (column pruning, knowing ranges from row-group statistics in the footer alone). They
also fit poorly with the "memory measures relying on Parquet row-group statistics" designed in
13.5.1; building MDF/MATLAB reading into the app itself would make that kind of efficiency
unusable as is. A two-step setup — "measure in MDF/MATLAB, convert to Parquet before analysis" — supports
them while keeping the existing design (polars/pyarrow-based, efficiency relying on Parquet's structure).

#### 15.2 Separating the dependencies

`asammdf`, `scipy` and `h5py` are all conversion-only; the GUI itself never uses them. Mixing them into the
app's main dependencies would install heavy dependencies even for a normal GUI start (`asammdf` in
particular brings `pandas`, `lxml` and more), so they are separated into a group named `convert` under
`[dependency-groups]` in `pyproject.toml` (installed explicitly with `uv sync --group convert`).
`tests/parquet_analyzer/test_convert.py` skips automatically with `pytest.importorskip` where this group is
absent, so the normal `uv run pytest` (only the `dev` group) isn't broken.

#### 15.3 MDF conversion

Uses `asammdf.MDF.to_dataframe(raster=...)`. Sampling rates can differ between MDF channel groups (e.g.
fast and slow channels mixed in one file), so it relies on `to_dataframe` resampling every channel onto one
time axis — without it, the problem of "putting several series with different row counts into one Parquet
table" would have to be solved by hand. With `raster` (seconds) they are aligned to that interval; when
omitted, asammdf's default applies (the union of every channel's timestamps).

The index `to_dataframe` returns is **relative seconds from the start of recording**, which doesn't match
the epoch seconds `MainWindow._to_numeric` and `pg.DateAxisItem` expect (it would be shown as dates around
1970 — the same kind of trap as the 4 GB test file's timestamp type mismatch found in 13.5.1).
`mdf.header.start_time` (the absolute start time of the recording) is added to the relative seconds to
convert to epoch seconds before writing (regression-checked by
`test_mdf_time_column_is_absolute_epoch_seconds` in `tests/parquet_analyzer/test_convert.py`).

#### 15.4 MATLAB conversion

A MATLAB `.mat` file has no notion, as a file format, of "which variable is the time axis" (it is just a set
of named variables). So:

- Only variables that are 1-D numeric arrays are candidates (matrices, structs, strings, etc. are excluded).
- A variable named `time`/`t`/`timestamp`/`timestamps` (case-insensitive) is auto-detected as the time axis.
  If none is found, `--time-var` is required, and the error message lists the candidate variable names
  (`ConversionError`; the same role as `ExpressionError` in `core/expression.py`: "an understandable message
  rather than a mysterious exception").
- Variables whose row count doesn't match the time axis are skipped quietly with a warning (data is never
  truncated or zero-filled to make it fit).

v5/v7 formats are read with `scipy.io.loadmat`. For the v7.3 format (which MATLAB uses automatically when
saving variables over 2 GB; it is HDF5 underneath), `scipy.io.loadmat` raises `NotImplementedError`
("Please use HDF reader for matlab v7.3 files"), which is caught to fall back to reading with `h5py`. They
differ only in how they read; the following "time-axis auto-detection and skipping mismatched row counts"
logic (`_select_time_and_columns`) is shared.

#### 15.5 Handling abnormal input (added 2026-10-03, from bugs found while strengthening the tests)

Principle: a conversion failure ends with **an understandable message, not a traceback** (`ConversionError`
etc.), and the CLI exits with code 1. **The input file and an existing output file are never damaged.**

- Unreadable files (corrupt, empty, wrong-format `.mat`/`.mf4`), a non-existent input, `--raster` of 0,
  negative, NaN or inf → `ConversionError` (or `FileNotFoundError` when the input doesn't exist). An output
  that is a directory or not writable → the CLI catches `OSError` and exits with code 1.
- The output being the input file itself → `ConversionError` (previously it silently overwrote the input with
  Parquet).
- Writes go to `<output>.tmp` and then `os.replace`. On failure the temporary file is deleted (no half-written
  file left, and no damage to an existing good file).
- Complex variables/channels and string columns → skipped with a warning (previously the imaginary part was
  silently dropped).
- Handling decided on 2026-10-03:
  - **A conversion resulting in 0 rows** (only zero-length variables, etc.) → `ConversionError` (don't make a
    file that shows nothing when opened).
  - **A time axis that isn't ascending or contains NaN** → converted (the app warns when loading, ch. 29), but
    the conversion also prints a warning to standard error (which variable, the first position).
  - **Reversed MDF timestamps** (asammdf silently turns it into one row) → detected before converting,
    `ConversionError` (don't silently produce different data).
  - A MAT v7.3 group (struct) gets the warning text "not numeric" (not "zero length").
  - The extension `.dat` is treated as MDF (as now).

### 28. Reporting background-job failures (added 2026-10-02)

Requirement: 5.22. Background: `BackgroundWorker.run()` in `core/background_worker.py` was only
`signals.finished.emit(self._func())`, so if `func` raised, `finished` was never emitted. The caller kept its
strong reference to the worker, the drawing indicator (the count of `downsampleStarted`/`Finished`,
`MainWindow._busy_sources`) was never cleared, and `WindowedColumn`'s "envelope being built" mark
(`_pyramid_building`) stayed `True`, so that column's envelope could never be built again (external review 1 #2, fact-checked).

#### 28.1 `BackgroundWorker`

- Add `failed = Signal(str)` to `_Signals`. `run()` wraps `func` in `try`: on success `finished(result)`, on
  an exception it logs the traceback with `logger.exception(...)` and then emits
  `failed("<exception type>: <message>")`. **In every case exactly one of `finished` or `failed` is emitted,
  exactly once.** Of the `BaseException` subclasses, `KeyboardInterrupt`/`SystemExit` aren't caught.
- The strong-reference rule (`setAutoDelete(False)` + the caller holds it) doesn't change. Callers connect
  their cleanup to both `finished` and `failed`.

#### 28.2 Handling per caller

- **Downsampling** (`TimePlotWidget._start_series_worker`): on failure too, remove it from `_active_workers`
  and emit `downsampleFinished` (keeping the drawing count balanced). That series' curve keeps its previous
  display. Emit a `renderFailed(self, name, message)` signal, which `MainWindow` shows briefly in the status
  bar (`render.failed`, for a few seconds). Repeated failures of the same series just overwrite the status
  text; no dialog.
- **Building the envelope** (`_maybe_start_pyramid_build`/`WindowedColumn`): on failure,
  `WindowedColumn.pyramid_failed()` (new) puts `_pyramid_building` back to `False` and counts the failures.
  Up to the second failure, the next redraw tries to build it again; after 3 failures the envelope for that
  column is given up for the session and it is always drawn by fetching real data (to avoid repeating work
  that keeps failing). Logged.
- **Statistics** (`_on_stats_requested`): if a variable's worker fails, that variable's row shows
  "Computation failed: <message>" and the other variables' results are shown normally. Removed from
  `_busy_sources` once all have finished (succeeded or failed).
- **FFT** (hidden by a feature flag): on failure, removed from `_busy_sources` and a warning dialog
  (`fft.failed`) is shown (a modal is fine, since the user explicitly runs it once).
- **Future analysis windows** (25.1) follow the same rule (showing the failure in the result display).
- (Added in review) **Synchronous reads on the UI thread are covered too**: statistics and FFT read the
  target range with `variable_window(...)` on the UI thread before handing it to the worker (from 2026-10-13,
  statistics of windowed columns and of windowed derived variables read in chunks on the background thread; ch. 53.
  What follows covers the other columns and FFT). For windowed columns this is where the disk is actually read,
  so `Exception` is caught per variable; statistics show the
  failure in that variable's row and FFT warns (previously only `ExpressionError`/`KeyError` were caught, and
  read errors were never reported to the user).
- (Added in review) The downsampling failure notice (`renderFailed`) is emitted only when the series still
  exists and the job's generation is the latest (no notice for an old failure a newer job has already drawn
  over, or for a removed series). Cleanup always happens.
- (Added in review) When the envelope is given up (the third failure), that is logged once.
- **How transient notices are shown** (changed in review): transient notices such as a drawing failure or an
  image export finishing are shown not with `QStatusBar.showMessage` but in a dedicated status-bar label
  (cleared automatically after a while). While a `showMessage` is showing, all of the status bar's normal
  widgets (file name, time-axis warning) are hidden, so a read that kept failing kept the warning out of
  sight. Reopening a file clears any remaining notice.
- Add "failures are always reported too, and cleaned up after" to the shared rules in the overview's
  section 2.

#### 28.3 Test plan

- `BackgroundWorker`: success emits only `finished`, an exception only `failed`, once each (run it for real on
  a `QThreadPool` and process events to check). The traceback is logged.
- Downsampling: with the series' data fetch made to raise (replacing a windowed column's fetch function), zoom
  → the drawing indicator is cleared (`MainWindow._busy_sources` back to empty, `pending_downsample_count()`
  is 0), `renderFailed` is emitted, the previous curve remains. Then restore the fetch and zoom → it redraws.
- Envelope: building raises → `_pyramid_building` goes back to `False`, retried up to twice, and from the
  third time on it doesn't try to build and draws real data.
- Statistics: only some variables fail → failure shown in those rows, the others normal, `_busy_sources` back
  to empty.
- FFT: failure → a warning (checked with a monkeypatch), `_busy_sources` back to empty.
- Confirm each test fails if 28.1's exception handling is removed (the drawing indicator remains, etc.).

#### 28.4 Discarding old results of the previous file

In A2 ([app 46.5](./app.md)), two paths were found through which results of jobs of the previous file got through when
a file was reopened. Both are marked `xfail(strict=True)` in `test_worker_paths.py`; remove the marks once fixed.

**(1) Generation numbers of series downsampling (`TimePlotWidget`)**

- Cause: the generation number is per series (`entry["generation"]`), and a new series starts from 0 (`_install_series`).
  When a late result of the previous file's series `a` (generation N) arrives, and the next file's series of the same
  name `a` is also at generation N, that result is drawn as the new series' result (`_on_downsampled`,
  `_on_thin_drawn` and `_on_series_worker_failed` look only at the name and the generation).
- Fix: take the generation number from **one ever-increasing number per plot** (`itertools.count` or similar). Each series
  keeps the number it last issued in `entry["generation"]`, and a received result is compared with it (the comparison
  stays as it is now). When a series is rebuilt (`_install_series`), it takes a new number, so it never matches a job of
  the previous series.
  - This differs from the earlier "one generation number per plot" (the bug where overlaid series treated each other as
    stale; see the comment in `_start_series_worker`): only the source of the numbers is shared; the comparison is still
    per series.
  - All places that raise the generation (`_start_series_worker`, `_start_thin_draw`, `_render_from_pyramid`,
    `_install_series`) take it from the same source.
- The "drawing" mark (the `downsampleStarted`/`downsampleFinished` pair) still always returns once each, even for stale results.

**(2) Generation of the navigator overview (`NavigatorSync`, plots 24.9)**

- Cause: `NavigatorSync._gen` is raised only by the windowed-column and derived-column paths. It is not raised on the
  paths where the next file's column is held in memory (the final `self._apply(tv, y)` of `_refresh_impl`, the
  `TimeIndex` path, the paths where nothing is drawn or there is no numeric column). The previous file's thinned read
  (`_on_navigator_overview_ready`) is discarded because `time_values` has changed, but at that point it does not clear
  the `navigator` "drawing" mark, so the progress indicator stays visible.
- Fix: raise `_gen` and clear the `navigator` mark at the **start** of `_refresh_impl` (when the overview is re-determined,
  any earlier request's result becomes stale on every path). Also raise and clear when `_time_values()` returns `None` and
  the function exits early. Raising again inside the windowed and derived paths stays as it is now.
  - With this, the combination "`gen` is the same but `time_values` differs" no longer occurs, but keep the `time_values`
    comparison just in case. If it is kept, the comment in that branch should say that the mark handling is also correct
    there (the new request holds the mark).

**Test plan**

- The `xfail` marks of `test_downsample_stale_job_of_previous_file_with_same_series_name` and
  `test_navigator_stale_refinement_when_next_file_is_eager` are removed and the tests pass.
- Added: on the same plot, remove series `a` and immediately re-add it under the same name; the result of the previous `a`'s
  job (whose number may coincidentally match) is not drawn (without changing files; checked with `TimePlotWidget` alone).
- Added: while a thinned read of the navigator is in progress, switch to a file whose column is held in memory, and switch the
  time axis, in each case; the `navigator` mark clears (`_busy_sources` goes back to empty).
- All other combinations in `test_worker_paths.py` (D, N, P) pass. Each test is confirmed to fail when the fix is removed.

### 29. Checking the time-axis order (added 2026-10-02)

Requirement: 5.22. Background: `MainWindow._visible_row_range`, `TimePlotWidget._redraw_series` and the
cursors' nearest-sample lookup use `np.searchsorted`, assuming an ascending time axis. Any column can be
chosen as the time axis, yet that assumption was checked nowhere. `ParquetDataSource.is_monotonic()` was
implemented but unused, and it looks only at row-group statistics, so it can't see disorder within a row
group.

#### 29.1 The check (add `check_time_order(values) -> TimeOrderReport` to `core/numeric.py`)

- The time-axis values are already all in memory (`MainWindow._time_values`, old 4.2), so the check runs on
  **all the actual values**, not `ParquetDataSource.is_monotonic()` (row-group statistics only) (one
  `np.diff`; expected under a second even for 100 million rows).
- (Added in review) The check runs in pieces of about 2 million rows, carrying "the previous finite value",
  the counts and the first row number across piece boundaries (the result equals doing it in one go). A
  single `np.diff` temporarily uses 1–1.8 GB at 100 million rows, going against the large-file measures (old
  13.5.1).
- `TimeOrderReport`: `nan_count` (number of non-finite values), `decreasing_count` (the number of places
  where, looking only at the finite values in order, a value is smaller than the previous one),
  `first_decreasing_index` (the original row number, or `None`), `ok` (`True` if both are 0). Runs of equal
  values (duplicate times) are not a problem.
- When it runs: after `load_parquet` decides the time axis, and after `set_time_column`.

#### 29.2 Display

- `MainWindow` puts a warning label (`_time_order_label`, next to the file-name label rather than via
  `addPermanentWidget`, in a warning color) in the status bar and shows it unless `ok`:
  - Not ascending: `time_order.not_sorted` (e.g. "⚠ The time axis is not ascending (N places). Visible range,
    statistics and cursor values may be wrong").
  - Missing values: `time_order.has_nan` ("⚠ The time axis has N missing values"). Both if both.
  - The tooltip shows the first disordered row number (displayed **1-based**) and suggests switching the time
    axis to another column.
  - Wording: the missing-value count includes ±Inf, so it says "missing/infinite". The English counts are
    worded to read correctly for both singular and plural.
- Hidden if `ok`. Reapplied every time a file is closed, reopened or the time axis is switched.
- The same content is logged with `logger.warning`, and in the operation log
  (`log_op("time_order_warning", ...)`).
- No modal dialog (not adding the chore of closing one every time a file is opened).

#### 29.4 Not freezing on a time axis with non-finite values (added 2026-10-03, found on the real machine)

- What happened: in `feature_test.parquet`, switching the time axis to `with_nan` (5,000 NaNs including the
  first rows, values -1 to 1 and not ascending) made the app unresponsive (CPU stuck at 100%; unrelated to
  `xyz_3d`, it happened on `main` too).
- Cause (reproduced and isolated headlessly): the navigator's `set_overview_data`
  - passed the time-axis values as is to `lttb` → `PlotCurveItem.setData` (with the default
    `connect="all"`), and Qt never returned while painting a path with NaN X coordinates;
  - used `x[0]`/`x[-1]` (the first being NaN) as the region's bounds and selection, making the
    `LinearRegionItem`'s range NaN.
  Hiding the navigator avoided the freeze. An empty plot's X range was also an odd ±880 million.
- Rule: **never hand pyqtgraph a non-finite X coordinate, range or bound.**
  - Navigator: the overview is built only from points where both the time axis and the value are finite.
    The region's bounds and the initial selection are the minimum and maximum of the time axis's finite
    values (not `x[0]`/`x[-1]`, so the whole is covered even for a non-ascending column). If the time axis
    has no finite value at all, the overview is empty and no range is set.
  - Plots: the full view (`go_home`), the range when the first series is added, and the range after switching
    the time axis are computed from the time axis's finite values only. Without finite values the range isn't
    changed. Drawn curves don't include points where the time axis is non-finite (or skip them reliably with
    `connect="finite"`).
  - Ranges, statistics and cursor values being inaccurate under the ascending assumption is as warned in
    29.2 and isn't fixed in this section (only "never freezes or crashes" is guaranteed).
- Tests (going through actual drawing): open with `load_parquet` files whose time axis is each of a
  non-ascending column with NaN/±Inf at the start and in the middle, and an all-NaN column; switch the time
  axis, add series, show all, and **make it actually paint with `MainWindow.grab()`**, confirming it returns
  within a few seconds. Since the bug is a freeze, the test runs in a separate process with a time limit,
  and it is confirmed to fail by timeout on the implementation before the fix.

#### 29.3 Test plan (including error-case tests)

- `check_time_order`: strictly increasing, with duplicates (ok), going back midway, decreasing throughout,
  containing NaN (at the start, middle, end), containing Inf, empty, one value, all NaN.
- `MainWindow`: opening small Parquet files with the time axes above shows/hides the warning label with the
  right text. Switching the time axis to a good column clears it.
- Reading abnormal columns (external review #12): `uint32`/`int64`/`float32`/`float64` columns, an all-NaN
  column, a column with one value, a constant column — each opened as a non-time column — can be plotted and
  have statistics (no exception). Files with many tiny row groups (e.g. 1,000 rows × 100 row groups) open too,
  and the visible-range statistics match the full computation.
- Checked through the actual processing path (open the file with `load_parquet`, not only calling internal
  functions).

#### 29.5 Abnormal input such as non-numeric columns and out-of-range reads (added 2026-10-03, from bugs found while strengthening the tests)

Principle: with abnormal columns or files, **no exception reaches the screen**, a failed operation **doesn't
change the state** (all or nothing), and pyqtgraph is never given a non-finite range (29.4).

| Input | Handling |
|---|---|
| A column that can't be converted to numbers (strings, dicts, binary, lists, structs, time-only) | `to_numeric` raises `NonNumericColumnError` (a `ValueError` subclass) naming the column. The same for a result that isn't 1-D. Inside an expression it becomes an `ExpressionError` |
| A non-numeric column in the variable panel | **Stays in the list** (hiding it by type would look like "a column disappeared"). Dropping it on a plot shows a status-bar notice (`variable.not_numeric`) and draws nothing. If it is a series in a view, it is skipped when loading |
| Switching to a column unusable as the time axis / the file disappearing after opening | Read the values first; on failure leave the state unchanged, put the combo back and notify (`time_axis.failed`) |
| The first non-time column isn't numeric (the navigator overview) | Use the first usable column. If there is none, clear the overview. A failure of a windowed overview goes down 28.2's path (log only) |
| A `read_window`/`read_columns` range that is negative, reversed or beyond the file | Clamped to `[0, row count]` (reversed means empty). No freezing on huge ranges |
| Types whose row-group statistics can't be read (strings, binary, float16, time-only), ±Inf statistics | That row group's range is "unknown" (None). `bounds()` is computed only from finite values |
| Missing date-time / duration values (NaT) | NaN (caught by `check_time_order`'s non-finite warning). The `UserWarning` from converting time-zone-aware statistics is suppressed since the values are right (UTC) |
| `lttb` with `n_out` under 3 (a zero-width screen, etc.) | **Treated as 3** (first, one point, last). Returning all points would draw everything on an invisible screen |
| `row_range_for_x` with a reversed or NaN range | No overlap (`(0, 0)`). Unused by production code (a part of the ch. 36 trials) |
| datetime64 in ps/fs/as units | Not handled (Parquet doesn't produce them) |
| A column that doesn't exist given to `thin_column`/`build_pyramid` | Stays an exception (reported via the background-job failure path, ch. 28) |

Test plan: `test_data_errors.py` (reading, statistics, the type table, NaT), `test_pyramid_errors.py`
(envelopes, thinning, LTTB edge cases), `test_open_errors_ui.py` (`load_parquet`, switching the time axis,
drops, views through the actual path). A bug fix comes with a regression test that fails before the fix.

### 36. The next stage of large-data support (added 2026-10-02)

Requirement: 5.29 (external review #3, #6, #7, #11). 36.1–36.7 are the record of the study and
measurements. **The adopted design is 36.8** (decided 2026-10-04: A, C and D adopted, B not pursued, E not
adopted).

#### 36.1 Current constraints (fact-checked)
- The time-axis column is held whole (about 800 MB at 100 million rows), because the visible-range,
  statistics and cursor computations assume the whole with `np.searchsorted` (the same as ch. 29's
  ascending assumption).
- Derived variables are computed in full and held when created (`A + B` adds another array the size of
  the inputs).
- The windowed-read cache is managed by "number of blocks (64 × 100k rows)", not by memory size.
- Background work only drops stale results by generation number; the computation itself doesn't stop.

#### 36.2 Direction (proposal)
1. **A time-axis index**: using row-group statistics (`ParquetDataSource.column_stats`) and the time axis
   being ascending (ch. 29's check), build an index (`TimeIndex`) that finds "X range → row range" without
   the whole time axis. A non-ascending time axis stays with the current hold-everything approach (or warns
   and doesn't use large-data mode). Below a threshold (e.g. 10 million rows), the current approach.
2. **Windowed computation of derived variables**: keep a derived variable as a `DerivedColumn` (expression +
   dependent columns) and compute only that range on `window(row range)`. Functions needing neighboring
   values, like `delta` and `rolling_mean`, read the window widened before and after (the needed margin is
   defined per function). Statistics are computed per window and aggregated.
3. **Managing the cache by memory size**: manage the cache with `max_cache_bytes` (a setting, default e.g.
   512 MB).
4. **Cancellation**: pass a cancellation token (`CancelToken`) to background work, checked at every read
   block and every LTTB step to stop midway. A new request cancels the old work.
5. **Testing**: record memory and time measurements on files of about 100 million rows (generated with
   `generate_test_parquet.py`) at each stage (the same way as old 13.5.1).

#### 36.3 Open questions (before starting)
- The threshold for switching to large-data mode, and how to handle a non-ascending time axis.
- How the windowed computation of derived variables handles user functions and maps (ch. 26, 27) (if all
  are elementwise, they can be computed per window as is).
- The order of the stages (recommended: 4 cancellation → 3 cache → 1 time-axis index → 2 windowed derived
  variables).

#### 36.4 Measurements (baseline) (2026-10-03, ch. 41's measurement)

**Baseline of the current implementation (`main` e68a02f)**, measured with ch. 41's
`tests/parquet_analyzer/bench_large_file.py`. The implementation was not changed at all.

**Machine**: Apple M4 Pro (arm64) / 24 GB memory / macOS 26.6.2 / Python 3.12.12 / offscreen (no display,
but the window is `show()`n). Garbage collection as in production (`_start_gc_guard`).

**Files measured** (`generate_test_parquet.py`; columns are time (ms) + 4 × float64, about 46 bytes per row):

| File | Rows | Size | Row groups | `--target-size` of the generation command |
|---|---:|---:|---:|---|
| `data/raw/bench_10m.parquet` | 10,040,000 | 463,880,835 B (0.46 GB) | 101 | `462MB` |
| `data/raw/bench_100m.parquet` | 100,020,000 | 4,621,209,883 B (4.62 GB) | 1007 | `4620MB` |

Row groups are about 100k rows. With the default `eager_load_limit_mb` (512), 10 million rows (80 MB per
column) become `LazyColumn` and 100 million rows (800 MB per column) `WindowedColumn`. With `0`, both are
`WindowedColumn`. So "100M, default" and "100M, 0" take the same path, and differences are measurement noise
(and how memory was left over from earlier stages).

**How to read it**: each cell is "time (s) / RSS at the end of the stage (MB) / the process's maximum RSS up
to that point (`ru_maxrss`, MB)". The time is wall-clock time until all background work (`WorkerHub`)
finished (until `_is_settling()` is false). See ch. 41 and the script's description for the stage definitions
(`zoom`/`pan` set the visible range to 1/10 and 1/1000 of the whole and wait for the background drawing to
finish; `pan` moves half a screen three times; `derived` creates `temperature + pressure`; `time_switch`
switches the time axis to `pressure`). One measurement each (no repetitions).

| Stage | 10M, default (512) | 10M, 0 | 100M, default (512) | 100M, 0 |
|---|---:|---:|---:|---:|
| `load` | 0.24 / 580 / 649 | 0.17 / 391 / 460 | 0.67 / 1093 / 1708 | 0.42 / 1544 / 2300 |
| `plot` | 0.19 / 581 / 649 | 0.17 / 629 / 629 | 1.93 / 4128 / 4128 | 1.35 / 4748 / 5054 |
| `home1` | 0.03 / 581 / 649 | 0.02 / 629 / 629 | 0.09 / 4128 / 4128 | 0.10 / 4630 / 5054 |
| `home2` | 0.03 / 581 / 649 | 0.02 / 629 / 629 | 0.09 / 4128 / 4128 | 0.10 / 4596 / 5054 |
| `home3` | 0.03 / 581 / 649 | 0.02 / 629 / 629 | 0.09 / 4128 / 4128 | 0.10 / 4537 / 5054 |
| `zoom10` | 0.14 / 581 / 649 | 0.14 / 629 / 629 | 0.14 / 4129 / 4129 | 0.16 / 4509 / 5054 |
| `pan10` | 0.44 / 583 / 649 | 0.44 / 634 / 634 | 0.45 / 4118 / 4129 | 0.44 / 3660 / 5054 |
| `zoom1000` | 0.13 / 583 / 649 | 0.14 / 634 / 634 | 0.16 / 4124 / 4129 | 0.16 / 3451 / 5054 |
| `pan1000` | 0.38 / 583 / 649 | 0.40 / 634 / 634 | 0.45 / 4084 / 4129 | 0.45 / 2532 / 5054 |
| `stats_zoom` | 0.02 / 583 / 649 | 0.01 / 634 / 634 | 0.02 / 4092 / 4129 | 0.02 / 2538 / 5054 |
| `home_back` | 0.19 / 583 / 649 | 0.15 / 634 / 634 | 0.22 / 4092 / 4129 | 0.27 / 2634 / 5054 |
| `stats_full` | 0.06 / 662 / 662 | 0.11 / 849 / 849 | 0.97 / 4116 / 4330 | 1.17 / 3666 / 5054 |
| `derived` | 0.02 / 753 / 776 | 0.08 / 859 / 861 | 1.21 / 3845 / 4330 | 1.63 / 3807 / 5054 |
| `derived_plot` | 0.05 / 754 / 776 | 0.05 / 859 / 861 | 0.35 / 4497 / 4496 | 0.31 / 4168 / 5054 |
| `time_switch` | 0.31 / 758 / 776 | 0.24 / 890 / 890 | 1.06 / 4177 / 4496 | 1.14 / 3987 / 5054 |
| `time_switch_back` | 0.33 / 758 / 776 | 0.20 / 892 / 892 | 0.62 / 4179 / 4496 | 0.65 / 3761 / 5054 |

**Timeouts**: none (a limit of 180 s per stage, 600 s for `load`; every stage finished within a few seconds).
The timeout recording path itself was confirmed in a trial with shortened limits (the child process is killed
and it moves to the next stage).

**Reading**:
- **Time is fine.** Even at 100 million rows the worst cases are 1–2 s (`plot` 1.4–1.9 s, `derived` 1.2–1.6 s,
  `time_switch` about 1.1 s, `stats_full` about 1–1.2 s), and no stage took tens of seconds. Operations that
  narrow the visible range (`zoom`/`pan`/`stats_zoom`) are constant regardless of row count (0.02–0.45 s);
  windowed reads and the pyramid are working. At 10 million rows every stage is under 0.5 s.
- **Memory is the problem** (the main point). At 100 million rows, just drawing one column for the first
  time puts RSS at the end of `plot` at about 4.1–4.7 GB with a peak of 4.1–5.1 GB (about 1.1–1.5 GB after
  `load`). **About 3 GB more** for an 800 MB column of data. Assessment: the pyramid build streams with
  `iter_batches` and isn't the cause; the first draw before the pyramid exists falls into the "actually read
  the whole range" path (the "No pyramid yet" branch in `plot_widget.py`), and on top of reading the whole
  800 MB column, several temporary arrays before LTTB (the finite mask, full slices/copies of x and y) pile up
  (to be confirmed; this chapter only measures). At 10 million rows (default) `plot` adds nothing, while
  `load` adds about 410 MB (overhead of reading the time axis and allocating arrays; large relative to an
  80 MB column).
- **The time axis is held whole** (36.1 item 1). `load` alone adds about 0.9–1.4 GB at 100 million rows.
  Switching the time axis (`time_switch`) reads the whole target column (about 1.1 s, `pressure`'s 800 MB).
- **Derived variables** (36.1 item 2). `derived` takes 1.2–1.6 s at 100 million rows, reads all of `a` and
  `b` and holds another array for the result (800 MB) (`MaterializedColumn`). RSS after `derived_plot` reaches
  about 4.2–4.5 GB.
- **`stats_full`** (statistics over the full range) takes about 1–1.2 s at 100 million rows, adding a few
  hundred MB to 1 GB of RSS (fetching the whole `window`).
- With `eager_load_limit_mb=0` at 100 million rows, RSS drops midway from 5.0 GB to 2.5 GB (cache eviction
  and the allocator returning memory). With the default, RSS doesn't drop and stays at about 4.1 GB.
- On a 24 GB machine everything stays around 5 GB and never becomes unusable. But on machines with less
  memory (8–16 GB), or when plotting several columns or adding derived variables, it grows in proportion to
  the number of columns — the grounds for prioritizing ch. 36's items 4 and 3 (cancellation, managing the
  cache by memory size) and reducing the temporary arrays at `plot` time.

**Re-measuring**: the numbers in this section can be reproduced with the command above (the script's
description). After implementing each stage of ch. 36, measure again with the same script and add to this
section.

#### 36.5 Proposed plan (2026-10-03, from 36.4's measurements; awaiting the user's confirmation)

36.4 showed "time is fine, memory is the issue", so the order of 36.2 is revised **to put the biggest
memory reductions first**. Each stage can be merged independently, and ch. 41's measurement is run at each
stage, adding the results to 36.4's table.

| Stage | Content | Expected reduction (100M rows) | Size |
|---|---|---|---|
| A | **Don't read the whole column on the first draw.** When there's no pyramid yet and the visible range is large, instead of a windowed read of the whole + LTTB, draw coarsely with ch. 40's `thin_column` (thinned reads per row group) and replace it once the pyramid is ready (the "coarse" mark uses the current `_set_series_coarse`). No LTTB temporary arrays either | about 3 GB → a few hundred MB per plotted column | medium |
| B | **Manage the windowed-read cache by memory size** (36.2 item 3). Cap the total blocks of all columns with `max_cache_bytes` (default 512 MB, a setting) | sets an upper bound when many columns are shown | small |
| C | **Windowed computation of derived variables** (36.2 item 2). Don't compute `a + b` in full and hold it; compute only the visible range. `delta` and `rolling_mean` read with margins before and after. User functions and maps are elementwise, so treated the same | about 800 MB per derived variable | large |
| D | **Time-axis index** (36.2 item 1). Don't hold the whole time axis; find "X range → row range" from row-group statistics. Used only for an ascending time axis; otherwise the current approach (held whole) | about 0.8–1.4 GB | large (touches ranges, statistics, cursors, hover, navigator, views) |
| E | **Cancellation** (36.2 item 4). Time is fine, so last | (time improvement) | medium |

- **What counts as large**: use the current `eager_load_limit_mb` (per column, default 512 MB) as is (no new
  threshold). D is additionally used only above N rows (e.g. 10 million).
- **A non-ascending time axis**: D isn't used; the current approach (held whole + ch. 29's warning).
- Recommended: **A → B → C → D → E**. A alone is expected to bring showing one column at 100 million rows from
  about 4.5 GB to around 2 GB (the time axis held in memory remains). A and B are small to medium and can start
  right away.

#### 36.6 Trials and comparison (2026-10-03, user's decision "try all of A–E and put numbers on the promising ones")

Build **trials of all of** 36.5's A–E and **compare them with the same measurement** before deciding what to
adopt.

- **Switch with flags**: build each stage behind a feature flag (`features.py`, all off by default):
  `big_a_thin_first_draw`, `big_b_cache_bytes`, `big_c_windowed_derived`, `big_d_time_index`, `big_e_cancel`.
  Measure by turning them on one at a time and in combinations with `PARQUET_ANALYZER_FEATURES`. With the flags
  off, the behavior must be exactly the current one (the full suite passes). The full suite must pass with the
  flag on too (`PARQUET_ANALYZER_FEATURES="big_x=on" uv run pytest`). Any test that doesn't pass is fixed or
  reported with a reason.
- Each stage is built in parallel on its own branch (`exp/36a-…` to `exp/36e-…`), and only the ones adopted
  after the comparison are merged (once adopted, the flag is turned on by default, and later the flag itself
  is removed).
- **Added measurements** (`bench_large_file.py`, branch `feat/41b-bench-scenarios`): in addition to the
  current stages, "plot 3 columns" (`plot3`), "create 2 derived variables and plot both" (`derived2_plot`),
  "20 consecutive zooms/pans while shown" (`burst20`; the time until the last drawing finishes, and the number
  of jobs discarded on the way), and the total from start to the first draw (`open_to_first_draw`).
  `--features` sets the child process's `PARQUET_ANALYZER_FEATURES`, recorded in the output. A comparison
  table (reading several JSONs and laying them side by side, `--compare a.json b.json …`) can be produced.
- **Numbers compared** (100M and 10M rows, `eager_load_limit_mb` default): each stage's time, resident memory
  (RSS) at the end of the stage, maximum memory. The baseline (all off), A–E on one at a time, and combinations
  of the promising ones. Configurations are measured one at a time in sequence (running them at once skews the
  times), twice each, with a third run if the two differ a lot.
- Results are kept as a table after 36.4 as "36.7 Comparison results", with what is adopted and why.

How each stage is built (in addition to 36.5's table):
- **A**: in `_redraw_series`'s "no pyramid yet" path, if the visible row count exceeds
  `_PYRAMID_PREFERRED_ROW_THRESHOLD`, draw about `n_out` points with a thinned read equivalent to
  `core/pyramid.thin_column` (made to accept a row range) instead of a windowed read of the whole + LTTB, and
  mark it coarse. Once the pyramid is ready, the current path replaces it.
- **B**: make `ParquetDataSource`'s block cache an LRU shared by all columns with a "byte limit"
  (`Settings.max_cache_bytes`, default 512 MB, also in the settings dialog).
- **C**: keep a derived variable as a `DerivedColumn` (expression + dependent columns; `window(lo, hi)`
  computes only that range). `delta` reads a margin of 1 row before, `rolling_mean(a, n)` and `rolling_nanmean`
  a margin of `n-1` rows before (a table defining the margin per function). User functions and maps are
  elementwise. Statistics are aggregated per window. An expression using a function with no margin definition
  goes back to the current approach (computed in full).
- **D**: an ascending time axis (ok in ch. 29's check) over `eager_load_limit_mb` isn't held whole but becomes
  a `TimeIndex` that finds "X range → row range" with row-group statistics and windowed reads. The visible
  range, statistics, cursors, hover, navigator and the views' time range use it. If not ascending, the current
  approach. Since its impact is wide, places it can't cover go back to the current approach and are reported.
- **E**: `WorkerHub.submit` can take a cancellation token (`CancelToken`), and the downsampling, windowed read,
  pyramid and statistics work checks it at every block and LTTB step to stop midway. A new request for the same
  series cancels the old work.

#### 36.7 Comparison results (2026-10-04; user's decision "go with A+C+D, and add E and measure" → A, C, D adopted, B not pursued, E after measuring)

Measurement: `bench_100m.parquet` (100 million rows, 4.62 GB, 1007 row groups), Apple M4 Pro / 24 GB, the
median of 2 runs per configuration, one configuration at a time. Single stages (A–E) were measured on each
`exp/36x-…` branch, and combinations on `exp/36-combined`, which merges A–D (E not included). `comb_off` (all
flags off on the combined branch) gave the same values as the baseline, confirming that with the flags off the
behavior is unchanged (the combined branch's full suite passed too). `eager_load_limit_mb=0` gave nearly the
same values (at 100 million rows every column is windowed even with the default). 10 million rows not measured.

Times in seconds, memory in MB (RSS). "Max" is the maximum memory over all stages.

| Configuration | Open → first draw | First draw (`plot`) | Resident after drawing | 3 columns (`plot3`) time / max | Create + draw derived | 2 derived drawn time / max | Time axis back | Full stats max | Max over all stages |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline (all off) | 1.33 | 1.03 | 5,056 | 3.91 / 9,562 | 1.29 | 3.33 / 5,020 | 0.71 | 5,771 | 9,562 |
| A | 0.96 | **0.59** | 3,401 | **1.14** / 4,540 | 1.10 | 2.21 / 4,886 | 0.70 | 5,270 | 5,310 |
| B | 1.22 | 0.92 | 5,059 | 3.65 / 9,362 | 1.06 | 3.26 / 5,068 | 0.70 | 6,240 | 9,362 |
| C | 1.31 | 0.99 | 5,073 | 3.47 / 10,256 | 1.64 | 3.41 / 3,146 | 0.43 | 6,049 | 10,256 |
| D | 1.97 | 1.74 | 5,817 | 8.09 / 10,914 | 1.98 | 5.26 / 9,863 | 1.40 | 6,338 | 10,914 |
| E | 1.07 | 0.71 | 3,800 | 1.93 / 8,807 | 1.08 | 2.15 / 4,898 | 0.69 | 6,074 | 8,807 |
| A+B | 0.97 | 0.60 | 3,411 | 1.14 / 5,428 | 1.07 | 2.29 / 5,073 | 0.68 | 5,478 | 5,479 |
| A+C | 0.94 | 0.60 | 3,404 | 1.16 / 4,546 | 1.68 | 2.67 / 3,142 | 0.41 | 5,328 | 5,490 |
| A+D | 1.39 | 1.14 | **1,381** | 2.88 / 3,414 | 2.00 | 3.43 / 9,051 | 1.36 | 4,243 | 9,051 |
| **A+C+D** | 1.37 | 1.13 | **1,388** | 2.90 / **3,378** | 1.66 | 2.56 / **749** | **0.20** | 4,388 | **4,724** |
| A+B+C+D | 1.40 | 1.16 | 1,861 | 2.92 / 3,846 | 1.73 | 2.64 / 1,601 | 0.20 | 4,481 | 5,157 |

Findings:
- **A is the most effective.** The first draw goes 1.03 → 0.59 s, memory after drawing 5.1 → 3.4 GB, three
  columns 3.9 → 1.1 s with the max 9.6 → 4.5 GB. No stage gets worse on its own.
- **D alone is counterproductive** (first draw 1.74 s, max 10.9 GB): it no longer holds the time axis, but the
  path that reads the whole on the first draw remains. **Combined with A**, memory right after opening drops
  2.4 GB → 0.3 GB and resident memory after drawing to 1.4 GB.
- **C removes derived variables from memory** (two derived variables' max 5.0 → 3.1 GB; 0.75 GB with A+C+D). In
  exchange, a derived variable's first draw slows from 0.2 to 1.7 s (the visible range is computed on the spot).
  Creating one becomes instant. With A+D alone two derived variables jump to a max of 9.1 GB, so **D must be used
  together with C**.
- **B has no effect in this measurement** (memory is even about +0.5 GB). B aims at "an upper bound when many
  columns are shown", which doesn't show in this measurement (at most 3 columns).
- **E's effect is cancellation**: stopping discarded work midway made the first draw (0.71 s) and three columns
  (1.9 s) faster. Not included in the combined branch.
- Statistics over the whole (`stats_full`) stay at 4–6 GB in every configuration, since all of the column is
  read (an open issue).

**Recommendation: adopt A+C+D, then add E and measure. Don't pursue B (remove it with its flag).**
- Reason: the max over all stages drops 9.6 → 4.7 GB and normal resident memory 5.1 → 1.4 GB, the biggest
  reduction. Time gets a little slower for the first draw (1.03 → 1.13 s) and a derived variable's first draw
  (0.2 → 1.7 s), but is the same or faster elsewhere (three columns 3.9 → 2.9 s, time axis back 0.7 → 0.2 s).
- Before merging: D touches visible ranges, statistics, cursors, hover, the navigator and views widely, so it
  goes through the `reviewer`'s review and a real-machine check. Also the full suite with the flags on, and
  checks with 10 million rows and a non-ascending time axis.
- Add E to `exp/36-combined` and measure A+C+D+E; adopt it together if nothing gets worse.
- The slowness of a derived variable's first draw (C) can be reduced later, e.g. by keeping the visible range's
  computed result.

**Measuring A+C+D+E (2026-10-04, `feat/36-large-data`)**: on a branch combining A, C, D and E after `main` (B
not included; the full suite passed 2328 tests with the flags off and with all four on), A+C+D and A+C+D+E were
measured under the same conditions.

| Configuration | Open → first draw | First draw | Resident after drawing | 3 columns time / max | Derived first draw | 2 derived time / max | Full stats max | Time-axis switch max | Max over all stages |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A+C+D (trial branch, table above) | 1.37 | 1.13 | 1,388 | 2.90 / 3,378 | 1.66 | 2.56 / 749 | 4,388 | 4,724 | 4,724 |
| A+C+D (combined branch) | 1.42 | 1.14 | 1,379 | 2.96 / 3,294 | 1.88 | 2.77 / 721 | 4,290 | 4,722 | 4,722 |
| A+C+D+E | 1.43 | 1.16 | 1,378 | 2.84 / 3,367 | 1.66 | 2.59 / 719 | **4,941** | **5,222** | **5,222** |

Findings:
- A+C+D on the combined branch gives the same values as the trial branch (differences within measurement
  noise). Combining made nothing worse.
- **Adding E doesn't make it faster.** The first draw and three columns that E sped up on its own show no
  difference, because with A+C+D the discarded work itself is gone (`burst20` = consecutive operations is the
  same 0.45 s too).
- **Adding E costs about +0.6 GB in full statistics** (4.3 → 4.9 GB), which persists into the following
  time-axis switch, making the max over all stages 4.7 → 5.2 GB. The same tendency with
  `eager_load_limit_mb=0`. E alone also had full statistics +0.3 GB above the baseline, so it comes from how
  E's cancellable statistics (the path reading in fine pieces) uses memory.
- E's real benefit (stopping unneeded work at once so the next operation doesn't wait) doesn't show in numbers
  in this measurement.

**Recommendation: don't adopt E this time** (it doesn't meet 36.7's "adopt it together if nothing gets
worse"). Adopt only A+C+D, and decide on E again after fixing its statistics memory increase and adding a
measurement where cancellation pays off (e.g. changing the visible range in the middle of heavy work).

**Decision (2026-10-04, user's decision "go ahead with those steps")**: E is not adopted (reverted from
`feat/36-large-data`; `big_e_cancel` and `CancelToken` removed). The A, C and D flags were turned on by default
(the flags themselves to be removed later). A test of the C×D combination (`test_big_c_d_combo.py`) was added.
Before merging: the `reviewer`'s review, checks with 10 million rows and a non-ascending time axis, and a
real-machine check.

#### 36.8 The adopted design (2026-10-04, from the 36.7 decision and the reviewer's review)

A, C and D are adopted (the flags `big_a_thin_first_draw`, `big_c_windowed_derived`, `big_d_time_index` are on
by default; after the real-machine check and the merge, the flags themselves are removed). B was not pursued
(removed with its flag), and E was not adopted (36.7).

**Which columns it applies to** (no new threshold; 36.5's "D only from 10 million rows" is not adopted):
- Real data columns: as before, `eager_load_limit_mb` (windowed when row count × 8 bytes exceeds it).
- **D (time-axis index)**: a `TimeIndex` only when the time-axis column exceeds `eager_load_limit_mb` and is
  ascending without non-finite values (ok in ch. 29's check). With the default (512 MB) that is about 67 million
  rows and up; a 10-million-row file stays with the current approach (held whole). Non-ascending or containing
  NaN/Inf: the current approach + ch. 29's warning.
  - Unlike 36.6, it doesn't use row-group statistics. When opening, it reads through the time axis once (about
    0.55 s at 100 million rows, in the background) and keeps the value every 8192 rows (`ANCHOR_ROWS`) plus the
    starts of the row groups. Reason: row-group statistics may be missing or coarse depending on how the file
    was written, while with a value every 8192 rows "X range → row range" can be found by reading a single block
    (about 12k points, about 0.1 MB at 100 million rows).
  - `ANCHOR_ROWS` must equal the pyramid's bucket size (`DEFAULT_BUCKET_ROWS`) (so the time at the start of each
    pyramid bucket can be looked up without reading). The same constant is used.
- **C (windowed derived variables)**: a `DerivedColumn` when every function in the expression has a margin
  definition (`_FUNC_MARGINS`). Without a definition, or when the margin isn't a fixed number (`n` of
  `rolling_mean(a, n)` being an expression, etc.), the current approach (computed in full and held). D is used
  together with C (36.7: A+D alone reaches a max of 9 GB with derived variables).
- **A (thinned first draw)**: when there is no pyramid and the visible row count exceeds
  `_PYRAMID_PREFERRED_ROW_THRESHOLD` (5 million rows).

**Rules for reading on the UI thread** (the concrete form of the overview §2 "never block the UI"; written down
because the review found violations):
- Allowed: lookups that need no reading (the index's values every 8192 rows, the pyramid), and, per single user
  action (settling a pan/zoom, dragging a cursor, showing a range), a binary search into cached blocks or reading
  a few rows.
- Not allowed: reads proportional to the number of points drawn or rows (e.g. reading the time one row at a time
  for each thinned point), and reads per mouse move (while moving, the hover shows the drawn curve's value "≈";
  the exact single row after stopping for 100 ms; plots ch. 39; the same when the time axis is a `TimeIndex`).
- The times (X) of A's thinned draw are found in the background work, for the same thinned positions as the
  values (Y).

**Other rules**:
- A wide derived variable (over `_DERIVED_NAV_DIRECT_ROWS` = 2 million rows) shows no curve until its pyramid is
  ready (about 1.7 s at 100 million rows). This is accepted. When the pyramid is ready, **every plot and the
  navigator waiting for it** redraw (no matter who started building it). If it can't be built, `renderFailed`
  reports it; it never stays blank silently.
- When the navigator's overview changes later (on the pyramid's completion), the selection is put back on the
  linked group's visible range.
- Background computation of derived variables uses a snapshot (never modified) of the functions and maps taken
  when the column was built. Function and map changes replace it with a new snapshot through the current
  recompute path (expressions ch. 18/26's "all or nothing" doesn't change).
- Statistics over the whole (`chunked_stats`) don't fail even with extreme values whose span overflows.
- (2026-10-13, ch. 53) Statistics also read windowed columns in chunks with `chunked_stats`. The reads use
  `WindowedColumn.read_uncached` (which does not go through the block cache; only `window` still goes through the cache).

**Test plan** (in addition to what 36.6 and 36.7 added, from the review's findings):
- The navigator overview after a windowed column's pyramid is built (time axis `TimeIndex` / resident, flags on/off).
- With a `TimeIndex` time axis, a thinned draw's time-axis reads on the UI thread don't depend on the number of
  points.
- Hover over a resident series with a `TimeIndex` time axis: no reads while moving.
- The selection stays at the visible range even when the navigator's overview changes later.
- A plot is redrawn too when the pyramid of a derived variable the navigator started building completes, and
  `renderFailed` on failure.
- A function change overlapping background computation doesn't cause `UnknownFunctionError`.
- In the C×D test, no operation turns the whole time axis into an array (`TimeIndex.__array__`).

**Before merging**: fix the review's findings, the full suite with the flags on and off, checks with 10 million
rows (with `eager_load_limit_mb=0` to exercise D too) and a non-ascending time axis, and a real-machine check.

### 37. Releasing background-job parts and garbage collection (added 2026-10-03, from the 13.6 investigation)

Requirement: 5.30.
- `BackgroundWorker` (`setAutoDelete(False)` + a strong reference held by the caller) was **never released**
  for two reasons: (1) the lambdas receiving results captured the worker (PySide keeps lambdas on the C++ side,
  where Python's GC can't follow them), and (2) `QThreadPool` keeps Python references to work it doesn't
  auto-delete (a memory leak keeping the signal QObject and the arrays passed to the work).
- Once they are released, **Python's cyclic GC can run on a pool thread and destroy UI objects (QObjects) on that
  thread, crashing** (3 out of 8 full test runs). The leak had been hiding this. It may also be the cause of
  13.6's "occasionally stalls" (unconfirmed).

#### 37.1 `WorkerHub` (`core/background_worker.py`, replacing `BackgroundWorker`)
- One per owner (`TimePlotWidget`, `MainWindow`'s statistics/FFT/analysis), **a plain Python object** (not a
  QObject), held by the owner as an attribute. Analysis windows don't have their own; they share `MainWindow`'s
  analysis hub (windows disappear when closed, so the hub mustn't disappear mid-job; intentional).
- **The signal sender is a single relay per process** (a module-global QObject belonging to the UI thread, never
  deleted). Its signal `done(hub_ref, token, result, error)` (`hub_ref` is a weak reference to the hub) is
  received over a queued connection; on the UI thread the weak reference is resolved and the hub's `_deliver` is
  called. If the hub is already gone the result is dropped.
  - Reason (reproduced in review, 2026-10-03): if the sender QObject is destroyed on the UI thread while a pool
    thread is emitting its signal, it crashes with use-after-free (not catchable as `RuntimeError`; making the
    hub a child of its owner and deleting the owner mid-job crashed 3 out of 3 times). Consolidating senders into
    one that never disappears avoids it (0 times under the same load).
- `submit(func, on_finished, on_failed, *, job, target) -> token`: issues a ticket and records `pending[token] =
  (on_finished, on_failed)` (this is the strong reference until the result is handled). The pool is given **a
  plain callable, not a `QRunnable` subclass** (`_Task.__call__`), with `pool.start(task)`.
  - Reason: PySide's `QThreadPool.start(QRunnable)` registers the `QRunnable` as a Python child of the pool after
    releasing the GIL and starting execution. For a short job, execution and deletion finish before registration,
    and the child reference is left behind and never removed (a memory leak keeping the arrays the job captured;
    about 430 out of 3,000 remained). Passing a callable leaves none (0 out of 5,000).
- `_Task.__call__()`: first takes the work out with `func, self._func = self._func, None` and runs it, and
  **`del func` before emitting the signal** (so the last reference to what the work captured remains on the UI
  thread's receiving side rather than on the pool thread). Emits the relay's `done` exactly once on both success
  and failure. Exceptions are caught and logged as in ch. 28 (UI objects are never touched outside the UI
  thread). The `job` records of ch. 30's operation log (start, completion, failure, kind, target, operation
  number) are made here.
- `_deliver` (UI thread): takes the entry out of `pending` first, then calls exactly one of `on_finished` or
  `on_failed` once. If it can't be taken out (already cancelled, etc.), nothing happens.
- **Progress path (2026-10-15, addition from export 57.3)**: the relay gained another signal, `progress(hub_ref, key, done, total)`.
  `hub.progress_sink(callback) -> (key, emit)` registers `callback(done, total)` and returns an `emit(done, total)` that can be called from a pool
  thread. `emit` fires the relay's `progress`, and `callback` is called on the UI thread (the worker never touches UI parts). Progress that
  arrives after `hub.drop_progress(key)` is discarded. Progress arrives before the same thread's `done` (in emission order). The meaning of
  `total < 0` belongs to the caller (for export: "computing all rows").
- Existing rules stay: dropping stale results by generation number, balancing `downsampleStarted`/`Finished`,
  not `deleteLater`-ing a plot with jobs in flight (counted with `len(pending)`), ch. 28's failure reporting, and
  25.1's cancellation of analysis windows (results of cancelled work are ignored by the receiver).

#### 37.2 Limiting garbage collection to the UI thread
- In `__main__.py`, after creating the `QApplication`, start pyqtgraph's
  `pyqtgraph.util.garbage_collector.GarbageCollector` (turns automatic GC off and collects on a UI-thread timer
  by checking each generation's threshold; the part pyqtgraph provides for this purpose) and keep a reference to
  it until the app exits. Interval 1 s.
- In tests, `tests/parquet_analyzer/conftest.py` turns automatic GC off and runs `gc.collect()` on the UI thread
  per test (the same idea).
- Confirm memory doesn't keep growing during long use (that collection actually runs).

#### 37.3 Test plan
- Release: for downsampling, envelope, statistics, FFT and analysis-window work, weak references to the work and
  to the arrays passed to it disappear after `gc.collect()` following the handling of the result (confirm they
  don't disappear on the implementation before the fix).
- Exactly-once notification: the receiver is called once each on success and on failure. Results of cancelled
  tickets are ignored.
- The existing tests of the existing rules (generation numbers, balancing, postponed `deleteLater`, ch. 28's
  failures, 25.1's cancellation, ch. 30's `job` records) all pass.
- GC: on the app's startup path `GarbageCollector` is running and automatic GC is off (`gc.isenabled()` is
  false). Collection runs from the timer.
- Repeating the full suite many times (including parallel runs under load) causes no crashes or stalls.
- (Added in review) Stress tests (run in a separate process): destroying a hub's owner mid-job about 2,000 times
  doesn't crash. Submitting thousands of empty jobs leaves no `_Task` in the pool after the results are handled
  (counted with weak references, not `gc.get_referrers`).
- GC timer: run the event loop for about 1.2 s with the default thresholds, and collection runs from the timer.

### 40. Navigator overview of windowed columns

Background: with `eager_load_limit_mb=0`, plotting a column of a file with a single row group
(`feature_test.parquet`) made `MainWindow._windowed_navigator_overview` build the overview from row-group
statistics (each row group's min and max), giving two points and a zero-width navigator selection (reproduced on
`main`).

- Range: the navigator's bounds and the initial selection are **the minimum and maximum of the time axis's finite
  values** (the same as 29.4), not decided from row-group statistics.
- Overview: when there are few row groups (guideline: under 200), it is built not from row-group statistics but
  from an evenly thinned read of the whole column (about 2,000 points; using `ParquetDataSource`'s per-row-group
  reads without holding the whole; if the pyramid (`core/pyramid.py`) has been built, that is used). With many row
  groups, the current approach (from the statistics) stays. If either is heavy enough to need background work, it
  runs in `WorkerHub` (ch. 37).
- Tests: open files with one, a few and many row groups with `eager_load_limit_mb=0` and plot; the navigator
  overview has well over 2 points, and the selection is the minimum and maximum of the time axis's finite values.
  Confirm it fails before the fix.

### 41. Measuring at the scale of 100 million rows (recording a baseline) (added 2026-10-03)

Purpose: before designing ch. 36 (the next stage of large-data support), measure the time and memory of the
current implementation on files of about 100 million rows and record them as a baseline. **This chapter doesn't
improve the implementation** (it only measures).

- Measurement script: `tests/parquet_analyzer/bench_large_file.py` (not a pytest test; run with
  `uv run python …`). Runs `MainWindow` headless (offscreen) in a separate process, records each stage's elapsed
  time and the maximum resident memory (`resource.getrusage`'s `ru_maxrss`, plus `psutil` RSS before and after the
  stage if available), and outputs JSON and a table (Markdown).
- Stages measured: open the file (`load_parquet`) / plot one column (until the first draw finishes, waiting for
  background work) / show all (`go_home`, 3 times) / zoom in and pan (visible range at 1/10 and 1/1000) /
  statistics over the visible range / creating the derived variable `a + b` / switching the time axis (to another
  column and back). Each stage has a time limit; when exceeded, "timeout" is recorded and it moves on (so it never
  stays stuck without finishing).
- Files: generated with `generate_test_parquet.py` (a `--target-size` giving about 100 million rows; the generated
  row count, file size and row-group count are recorded). Placed in `data/raw/`, not in the repository. For
  comparison, the same measurement on a 10-million-row file. Settings in two variants: default (including the
  default `eager_load_limit_mb`) and `eager_load_limit_mb=0`.
- Results: recorded as a table, with the measuring machine's information (CPU, memory, OS), after this document's
  36.1 as "36.4 Measurements (baseline)". For stages that are clearly problematic (tens of seconds or more, several
  GB of memory or more), add an assessment of the cause (fixing it is ch. 36's job).

### 53. Aggregating statistics over a whole column window by window

Requirement: 5.46.

#### 53.1 The current design (fact-checked, `main` as of 2026-10-13)

- The statistics request (the statistics operation in `MainWindow`, where `resolved` is built in `main_window.py`) does, per variable:
  - `DerivedColumn` (a windowed-computed derived variable, 36.6 C) → in the background, `chunked_stats(col.window, row_start, row_end)`, reading in chunks.
  - Anything else → **on the UI thread** `variable_window(name, row_start, row_end)`, i.e. `WindowedColumn.window` reads all rows
    of the displayed range in one go, and that array is passed to the background `basic_stats`.
- So when a windowed raw column (`WindowedColumn`) is displayed in full and statistics are pressed, a 100-million-row column
  holds 800 MB of read data plus a copy converted to numbers at the same time, which gives the "whole-column statistics maximum"
  of 4.3 GB in 36.7. While it reads, the screen also freezes (contrary to the overview's "do not do heavy work on the UI thread").
- `chunked_stats` computes count, missing, min and max exactly; mean and standard deviation by combining per-chunk moments; and
  the median exactly with a second pass (counting bins) and a third pass (collecting only the values in the median bin)
  (at most 3 reads). It was checked against `basic_stats` by the property tests (app 46.2).
- `WindowedColumn.window` goes through `read_window` (an LRU cache of 100k-row blocks, 64 blocks). Sweeping the displayed range
  three times in chunks, at 100 million rows, reads 1,000 blocks per pass and pushes out all the blocks the plots were using from the cache.

#### 53.2 Changes

1. **`WindowedColumn` is also aggregated with `chunked_stats`.** For a statistics request, if the column is a `WindowedColumn`,
   it is not read on the UI thread; as with `DerivedColumn`, the column itself is passed to the background, and there
   `chunked_stats(<read function>, row_start, row_end)` is called. `MaterializedColumn` and `LazyColumn` (up to
   `eager_load_limit_mb`, i.e. sizes that may be held in memory) stay as now (slicing with `variable_window` → `basic_stats`).
   The check is `isinstance`, placed at the statistics request, not inside `variable_window`.
2. **Reads that bypass the cache.** The read function passed to `chunked_stats` bypasses the block cache (`read_window`):
   add `read_uncached(row_start, row_end)` (provisional name) to `WindowedColumn`, which returns the range via
   `ParquetDataSource.read_columns([name], row_start, row_end)` → `_numeric_column` (the same numeric conversion as `window`).
   - Reason: a three-pass sweep over the whole range should not push out the plots' cache. Reads are in chunks larger than the
     block size (100k rows) (`chunked_stats`'s default of 4 million rows), so there are fewer calls.
   - Rows outside the range must not be read (before `row_start` or at/after `row_end`). The range clamping of `read_columns` (data 29.5) still applies.
   - If the measurements (53.4) show it is slower or uses more memory than the cached path, the reason is written down and the
     cached path may be restored (report to the main session).
3. **Failures.** A read failure used to be caught on the UI thread and put into `errors` (28.2). For windowed columns it now
   arrives as a failure of background work (`{"error": msg}` in `_on_stats_finished`). The error text for that variable in the
   dialog keeps the same form as now, and the other variables are shown normally. Every path still satisfies "report success or
   failure exactly once" (overview §2).
4. **Switching files.** Discarding results when another file is opened during a computation (app 46.5 S-5) stays as it is. The
   background work keeps reading the old `ParquetDataSource`, so reading a closed file must not crash (same as the current
   `DerivedColumn` path: a read that raises is reported as a failure and dropped by the generation check).
5. Not changed in stage 1: the name and API of `values()` (stage 2), the inside of `chunked_stats`, and how histogram, XY and FFT read data.

#### 53.3 Test plan

- Match: with `eager_load_limit_mb = 0`, a small file in which every column is a `WindowedColumn` (several hundred thousand rows,
  spanning several 100k-row blocks, with one column containing NaN/±Inf). Compute the statistics for the whole and for part of the
  range (with `row_start`/`row_end` straddling block boundaries) and compare with `basic_stats` over the same range
  (count, missing, min, max and median match exactly; mean and standard deviation to `rtol=1e-9`). Also check the case of several
  chunks, with the chunk size made small (e.g. `chunk_rows` of `chunked_stats` monkeypatched to tens of thousands of rows).
- Not read on the UI thread: from pressing statistics until the result appears, the `WindowedColumn` reads (`window`,
  `read_uncached`, `read_window`, `read_columns`) are not called from the UI thread; verified by recording the calling thread.
  However, the one-row reads at both ends of the time axis for the range display ("t0 to t1 (N rows)", `TimeIndex`) are excluded
  (as before; they are cheap; added in review, 2026-10-13).
- Full-read watch (app 46.6 (2), `test_no_full_read.py`): add the operation "8. Statistics (part of the displayed range)". Also check
  that rows outside the displayed range are not read. Remove "statistics over the whole displayed column" from the list of
  excluded operations in the watch, and add it as an operation that checks **no single read exceeds half of the total** (with small chunks).
  Fix the wording of "not targeted" in 46.6 accordingly.
- Cache not pushed out: the contents (keys) of `ParquetDataSource`'s block cache do not change before and after the statistics.
- Failure: make `read_uncached` raise; then only that variable shows the error, the other variables still show values, the busy display
  (`_set_busy`) clears, and the report happens exactly once (the row of the statistics table in app 46.5 is also used for windowed columns).
- File switch: open another file while statistics on a windowed column are being computed → the result is discarded (same form as
  46.5 S-5, on a windowed column).
- The existing statistics tests (the statistics rows in `test_worker_paths.py`, the statistics dialog) pass. Tests whose preconditions no
  longer hold because the path changed are repaired while keeping the property they check (not weakened).
- All tests pass.

#### 53.4 Measurements (before merging)

- With `tests/parquet_analyzer/bench_large_file.py`, measure `stats_full` and `stats_zoom` on `bench_100m.parquet` before the change
  (`main`) and after it, under the same conditions (median of 2 runs, one configuration at a time, in turn). Record the time (seconds) and
  peak memory (MB) in this section, in the form of the 36.7 table.
- Target: the peak memory of `stats_full` stays within the resident memory after drawing (about 1.4 GB) plus a few hundred MB. Time is
  within 1.5× the time before (reads may go from 1 pass to up to 3, so it may be slower). If it falls outside this, write down the reason and
  a proposal (e.g. change the chunk size) and report to the main session.
- `stats_zoom` (the displayed range is 1/1000) is the same or faster.
- When several variables are selected (added in review, 2026-10-13): each variable becomes one background job, running at the same time in
  the analysis pool. Per variable, a chunk (4 million rows) of temporary arrays is about 150 MB. With 4 variables at once on 100 million rows,
  +515 MB (measured by the `reviewer`). With more than 12 it could be +1.5–2 GB, but that is still well below before (where 800 MB per variable
  was read in one go on the UI thread). If it becomes a problem, make `chunk_rows` smaller.

**Measurement results (2026-10-13)**: `bench_100m.parquet` (100 million rows, 4.62 GB), Apple M4 Pro / 24 GB, median of 2 runs each,
`bench_large_file.py --stages stats_zoom,stats_full` (`eager_load_limit_mb` both at the default and at 0; at 100 million rows even the
default makes every column windowed). Before = `main` (8b28cb6); after = `feat/53-chunked-stats`. Time in seconds, memory in MB (peak RSS up to
that stage). The RSS at the start of the stage was about 1,350 MB (the resident memory after drawing).

| Stage | Setting | Before: time | Before: peak | After: time | After: peak |
|---|---|---:|---:|---:|---:|
| `stats_full` | default | 0.75 | 4,332 | 1.02 | 1,592 |
| `stats_full` | 0 | 2.70 | 3,019 | 1.02 | 1,598 |
| `stats_zoom` | default | 0.03 | 1,357 | 0.02 | 1,355 |
| `stats_zoom` | 0 | 0.03 | 1,388 | 0.02 | 1,358 |

- The peak memory of `stats_full` stayed within the resident memory after drawing plus about 250 MB (before, it was +1.7 to 3 GB).
  This matches the target (resident plus a few hundred MB).
- Time was 1.36× the earlier default (0.75 s), within the 1.5× target. It is faster than the earlier setting 0 (2.7 s). Before, the times
  differed greatly between the default and 0; the cause was not investigated (probably a difference in the file-cache state).
- `stats_zoom` (displayed range 1/1000) is the same or faster.
- It was not necessary to return to the cached path (`read_uncached` stays).

#### 53.5 Relations to other domains

- **Analysis** (analysis, statistics chapter and the statistics dialog): the displayed values and form are unchanged.
- **App foundation** (app 46.5, the statistics failure path; 46.6 (2), the watch): remove "statistics over the whole" from the list of
  excluded operations in 46.6.
- **36.8 in this document**: the windowed-column read paths gain `read_uncached` (the only way through the cache stays `window`).

### 56. Time-axis order, continued (added 2026-10-15)

Requirement: 5.50. A continuation of ch. 29. Split
into two stages: stage 1 = 56.1-56.3 and 56.6 (warnings, duplicate times and tests), stage 2 = 56.4 and 56.5 (sorted display and UTC; split
because the impact is wide).

#### 56.1 Warnings on screens that use a range (stage 1)

- `MainWindow` gets one place that answers "might range calculations be wrong with the current time axis?"
  (`time_order_unreliable() -> bool`: `True` when `_time_order_report` exists and `decreasing_count > 0`. Missing values alone give `False`,
  because missing values do not break range calculations. `False` while displayed sorted (56.4)), and each screen looks only at this.
- Where and how it is shown (values are still shown as before; not modal):
  - Statistics result (statistics dialog): one warning-colored line above the table, "⚠ The time axis is not ascending, so statistics for
    the range may be incorrect".
  - Analysis windows that use a range (XY, histogram, FFT): one line with the same meaning next to the range display (a short wording is fine).
  - Cursor readout and hover display: a "⚠" before the value, and the reason in the tooltip (if any). Keep per-mouse-move work light
    (overview §2): do not compute `time_order_unreliable()` each time (just read the value held when the time axis was decided).
- Re-apply when the time axis is switched or the file is reopened (open analysis windows and statistics dialogs are re-applied at their next
  recalculation).
- Implementation (stage 1): for the cursor and hover, `MainWindow._apply_time_order_to_readouts` passes `PlotGrid.set_time_unreliable(flag, tip)`
  to each `TimePlotWidget` every time the time axis is decided (also inherited by plots added later; FFT plots are excluded). The statistics
  dialog has a warning label via `StatsDialog(time_unreliable=)`. XY and histogram add a short wording (`time_order.range_warning_short`) at the
  start of the status display.
- Deviation (FFT): FFT is disabled by a feature flag, and its result is added to a plot rather than to a window with a "range display". When
  the range is not ascending, the existing `fft.time_not_ascending` refuses the run itself (ch. 32), so no warning line was added to FFT.
- How duplicates are counted (56.2): like `decreasing_count`, non-finite values are skipped, and a value equal to the previous **finite value**
  is a duplicate (`2, NaN, 2` is one). +-Inf is not finite and is not counted as a duplicate. `TimeIndex.build` also keeps the scan result
  as-is in `report` (to show the duplicate count).
- Impact on existing tests: a time axis with all rows equal (`constant`/`bool`) or `_times()` with consecutive duplicates is `ok` but has many
  duplicates, so the label now appears (the tests' expectations were corrected to match 56.2).

#### 56.2 Duplicate times (stage 1)

- `TimeOrderReport` gains `duplicate_count` (the number of places where, with only finite values lined up, the value equals the previous one)
  and `first_duplicate_index` (the original row number, `None` if none). Keep the chunked scan design (29.1), giving the same result
  across chunk boundaries as in one pass. The meaning of `ok` is unchanged (duplicates do not break range calculations; the condition for
  using `TimeIndex` is also unchanged).
- The status-bar warning label (29.2) is shown when it is not `ok` **or** `duplicate_count > 0`. The duplicate wording is
  `time_order.has_duplicates` ("⚠ The time axis has N identical times"). The tooltip shows the row number of the first duplicate (1-based).
- `duplicate_count` is added to the operation log's `time_order_warning` and to `state_snapshot`.

#### 56.3 Confirming the current handling of time-zone-aware times (stage 1)

- Stage 1 does not change the display (switching to UTC is 56.5 in stage 2). The current behavior (epoch seconds and whether a warning
  appears when a time-zone-aware column is the time axis) is fixed by tests so that it is clear what stage 2 changes.

#### 56.4 Display sorted by the time axis (stage 2)

- Switch: "Display sorted by time axis" in a "View"-type menu (or the toolbar), a checkable action, default off, shortcut configurable. Enabled
  only when the time axis is not ascending (cannot be pressed if ascending; the tooltip gives the reason). The state is saved in the view
  (`time_sorted: bool`; adding a key, so `schema_version` is not raised. Old views are off).
- Mechanism: build once a stable sort order from the time-axis values (`order = np.argsort(time, kind="stable")`, non-finite values at the end),
  wrap **all columns so they are seen in this order** (`SortedColumn(base, order)`: `values()` is `base.values()[order]`, `window(a, b)` is
  `base.values()[order[a:b]]`), and replace `_raw_variables`. The time-axis values are also the sorted ones (ch. 29's check becomes ok plus
  missing only on the sorted values). Virtual variables are recomputed from the sorted inputs (`delta` and moving averages are computed in time
  order; this is the purpose).
- **Original row number**: `order[i]` is the original row number of displayed row `i`. The cursor readout and the exact hover value (after
  stopping) show "original row N" (1-based). Parquet export (ch. 57) adds a column `source_row` (the original row number, **1-based**,
  `int64`) while displayed sorted.
- Restriction (stage 2): not usable on files that have windowed columns (over `eager_load_limit_mb`) (cannot be pressed; the tooltip gives the
  reason), because sorting turns reads into random reads and breaks the premise of windowed reads. How to handle large files is decided in
  stage 2 of ch. 58.
- Memory: the order array (8 bytes x rows) and sorted copies of the columns used. At switch time an estimate of the required amount is given,
  and once the memory-limit setting of ch. 58 exists, it refuses based on that.
- Turning it off returns to the original columns and recomputes virtual variables. Plots and ranges return to the whole view (because the
  row correspondence changes).

#### 56.5 Display time-zone-aware times in UTC (stage 2)

- Currently all time displays use `datetime.fromtimestamp` (the PC's local time), in about 10 places: axis, cursor, hover, view manager,
  histogram, image annotation, operation log, etc. Only when the time-axis column is time-zone-aware, these are displayed in **UTC** (times
  without a time zone and numeric time axes stay local as before).
- Mechanism: gather the display time conversion into one place in `core/` (e.g. `to_display_datetime(x, utc: bool)` in `core/timefmt.py`, plus a
  small setting that holds UTC or local), and have each place call it. Whether the current time axis is shown in UTC is decided by
  `MainWindow` when it decides the time axis. When UTC, "UTC" is shown in the axis label and annotation.
- When starting stage 2, add the list of places (`grep fromtimestamp`) to the spec before proceeding.

#### 56.6 Test plan

- `check_time_order`: duplicate count and first row number (no duplicates, consecutive duplicates, separated duplicates, duplicates across
  chunk boundaries, equal values separated by NaN, +-Inf duplicates). The chunked result equals the one-pass result (with `hypothesis`,
  written like the property tests of app ch. 46).
- Warning label: shown for duplicates only while staying `ok` (the `TimeIndex` condition also unchanged). Wording for not ascending +
  duplicates + missing.
- Range warnings: with a non-ascending time axis, a warning appears in the statistics dialog, XY, histogram, cursor readout and hover. Not for
  missing-only, duplicates-only or normal. Disappears when the time axis is switched to a normal column. The check is not redone on every
  hover movement.
- Abnormal time axes (open real files with `load_parquet`): duplicate times, NaN (start, middle, end), +-Inf, time-zone-aware times (UTC and
  `Asia/Tokyo`), a time-zone-aware column containing NaT. Opening, plotting, whole view, statistics and cursor raise no exception and do not
  hang (written as in 29.4).
- Stage 2: sort toggle (values, original row number, virtual-variable `delta` in time order, restored when turned off, saving and loading in a
  view, cannot be pressed for windowed files). UTC display (09:00 in `Asia/Tokyo` is 00:00 UTC on the axis, cursor and hover; a column without
  a time zone stays local as before).

### 58. Withstanding data of 500 million rows x 50 columns (added 2026-10-15)

Requirement: 5.52. A continuation of ch. 36, 41 and 53.

#### 58.1 Premises and targets

- Scale: 500 million rows x 50 columns (reduced from the original 1 billion rows x 100 columns, 2026-10-15, user's decision). With float64, one column
  is 4 GB and the whole is about 200 GB (does not fit in physical memory). The time axis alone is 4 GB.
- Current baseline (36.4, 36.7): resident memory of about 4-5 GB at 100 million rows. Many places currently hold the whole time axis
  (`_time_values`) (36.8 D's `TimeIndex` is only for ascending axes).
- Target: open, open a view, whole-column statistics and histogram within 20 seconds (how far each operation reaches is estimated from the stage 1
  measurements). Accuracy first (no thinning or sampling in calculations over all rows). The memory limit is a setting (what % of physical
  memory).
- Multi-core: places where it should help, such as virtual-variable computation and statistics. The process-based and thread-based approaches
  are chosen after measuring.
- Development machine (used for measurements): 12 cores, 24 GB memory, about 178 GB free disk (2026-10-15). 1 billion rows x 20 columns in
  float64 would be about 168 GB (uncompressed), which does not fit in the free space, so test files **default to float32** and use values that
  shrink under compression (zstd).

#### 58.2 Stage 1-1: script that creates test files

- `tests/parquet_analyzer/generate_wide_parquet.py` (run with `uv run python ...`; not a pytest test).
- Arguments: `--output`, `--rows` (e.g. `1e9` is also accepted), `--columns` (default 20, excluding the time axis), `--dtype` (`float32`
  default / `float64`), `--row-group-rows` (default 1 million), `--compression` (`zstd` default / `snappy` / `none`), `--interval-ms`
  (default 1), `--gaps` (number of time jumps to insert, default 2), `--seed`, `--force`.
- Columns: `time` (`datetime[ns]`, no time zone, evenly spaced plus jumps), and signals that repeat several kinds in turn (sine, slow slope,
  noise, steps, integer counter). Known relations are built in (e.g. `sig_00**2 + sig_01**2 = 1`, `sig_02 - sig_00 = 10`), so that they can be
  checked by eye as with `feature_test.parquet`. The docstring lists the columns and relations.
- **Memory is constant regardless of the row count**: build one row group at a time and write with `pyarrow.parquet.ParquetWriter`. Shows
  progress (rows, %, elapsed, estimated remaining).
- **Disk check**: estimate the total from the size of the first row group written, and stop if the free space is less than "estimate + 10 GB"
  (`--force` to continue).
- Generated files go under `data/raw/` (not in the repository). Sizes to create: 100 million, 300 million and 1 billion rows (all 20
  columns, float32). For comparison, also create 100 million rows in float64 (if disk allows).

#### 58.3 Stage 1-2: measurement

- Use `tests/parquet_analyzer/bench_large_file.py` (ch. 41). Stages to add: open a view (10 virtual variables, 3 of which are plotted; compare
  before and after ch. 55), whole-column histogram, whole-column statistics (the ch. 53 path), computing a virtual variable over all rows
  (about `a*b+c`), Parquet export (if ch. 57 is in). For each stage, record elapsed time and peak resident memory plus **CPU usage** (the
  process's CPU time / elapsed time = a rough number of cores used), to see the room for multi-core. If the time limit is exceeded, record
  "timeout" and move on (same as ch. 41).
- Measure in the order 100 million -> 300 million -> 1 billion rows, and tabulate how each stage's time and memory grow with the row count. If
  even opening fails at 1 billion rows (out of memory / timeout), record the cause at that point (which array, how many GB) and stop.
- The results are recorded as a table under "58.4 Measurement results" in this chapter and reported to the user.

#### 58.4 Measurement results (2026-10-15, stage 1)

Machine: Apple M4 Pro (12 cores), 24 GB memory, macOS 26.6, Python 3.12. Settings are defaults (default `eager_load_limit_mb`, feature flags
default). The files are the defaults of `generate_wide_parquet.py` (20 float32 columns, zstd, 1 million rows per row group, 1 ms interval, 2
time jumps). The measuring command is `bench_large_file.py` (ch. 41). Each file was measured once (no repetition).

**File generation** (written with constant memory, one row group at a time):

| Rows | Size | Generation time | Speed |
|---:|---:|---:|---:|
| 100M | 1.29 GB | 22 s | 4.5M rows/s |
| 300M | 3.88 GB | 70 s | 4.3M rows/s |
| 1B | 12.95 GB | 255 s | 3.9M rows/s |

About 13 bytes per row (for 21 columns). Values are rounded to power-of-two steps, so they shrink well with dictionary encoding + zstd (raw
data for 20 float32 columns is about 84 bytes/row). **Real data of 500 million rows x 50 columns (about 200 GB in float64) is much larger (and harder
to compress), and disk reads become the bottleneck**. Treat this result as the "lower bound when the amount read is small". Also, the 13 GB file
fits in 24 GB of physical memory, so after the first stage the measurements are with the file in the OS page cache.

**Time per stage (seconds) and peak resident memory (MB, the process maximum up to that stage)**. `hist_full`, `derived_full`, `view_load`,
`plot3`, `derived2_plot`, `open_to_first_draw` and `burst20` were measured in dedicated child processes (RSS is not affected by other stages).
The others are consecutive stages in the same child process (resident memory remains after `time_switch`, so do not read the absolute RSS of
later stages).

| Stage | 100M: s / MB | 300M: s / MB | 1B: s / MB | 1B CPU (cores) |
|---|---:|---:|---:|---:|
| load (open) | 0.84 / 300 | 1.97 / 358 | 6.44 / 364 | 1.4 |
| plot (first draw of 1 column) | 0.90 / 459 | 2.40 / 650 | 7.68 / 1213 | 2.4 |
| zoom1000 (zoom to 1/1000) | 0.18 / 467 | 0.19 / 660 | 0.21 / 1228 | 0.5 |
| pan1000 | 0.53 / 471 | 0.58 / 667 | 0.57 / 1245 | 0.4 |
| stats_full (whole-column statistics, one raw column) | 0.98 / 704 | 2.83 / 880 | 9.80 / 1488 | 1.2 |
| derived_plot (show `a+b`) | 1.46 / 704 | 4.35 / 880 | 15.4 / 1488 | 1.7 |
| time_switch (another column as time axis) | 0.82 / 3025 | 3.25 / 5803 | 19.9 / 9611 | 1.0 |
| hist_full (whole-column histogram, 1 column) | 1.68 / 572 | 5.08 / 686 | 18.6 / 1282 | 1.5 |
| derived_full (`a*b+c` over all rows) | 3.51 / 571 | 10.7 / 580 | 40.1 / 689 | 1.6 |
| plot3 (show 3 columns) | 1.31 / 753 | 3.64 / 1037 | 12.3 / 2641 | 6.1 |
| derived2_plot (show 2 virtual variables) | 2.12 / 635 | 6.50 / 630 | 26.8 / 649 | 2.1 |
| open_to_first_draw (open -> first draw) | 1.47 / 479 | 4.16 / 655 | 19.7 / 822 | 2.0 |
| view_load (open a view with 10 virtual variables, 3 shown) | 3.94 / 724 | 12.1 / 738 | 44.2 / 3263 | 2.5 |
| burst20 (20 quick zooms/pans) | 0.45 / 490 | 0.44 / 635 | 0.50 / 910 | 1.0 |

`parquet_export` is recorded as "skipped" because `MainWindow` does not yet have an export entry point (ch. 57 is not implemented) (add a call
to that stage of `bench_large_file.py` once the entry point exists). No stage timed out or ran out of memory, even at 1 billion rows (opening and
all stages ran to the end).

**What can be read from this**:

- **Time grows almost in proportion to the row count** (about 8-11x from 100M to 1B). Stages that scan all rows exceed the 20-second target
  (58.1): `view_load` 44 s, `derived_full` 40 s, `derived2_plot` 27 s, `derived_plot` 15 s (within 20 s but with no margin), `hist_full` 19 s,
  `open_to_first_draw` 20 s is exactly on the border. `stats_full` (9.8 s) is within the target. Zoom, pan and whole view are constant
  regardless of the row count and comfortable even at 1 billion rows (0.2-0.6 s).
- **CPU is barely used**: of 12 cores, the stages that scan all rows (`stats_full`, `hist_full`, `derived_full`, `view_load`) use only 1.2-2.5
  cores (only `plot3` uses 6 cores, because it runs a separate background job per column). Processing in parallel chunks (per row group)
  could shorten times by a factor close to the number of cores.
- **Two memory peaks**. (1) `time_switch`: making a column that is neither ascending nor a time (`sig_01`) the time axis loads all rows as
  float64, **about 9.3 GB** at 1 billion rows (about 9.6 bytes per row). It passes on 24 GB, but breaks at scales beyond physical memory or with
  repeated switches. The default time axis (an ascending time) `load` stays at 364 MB, thanks to `TimeIndex` (36.8 D). (2) The part
  proportional to the number of displayed columns: about 0.9-1.2 bytes/row per column (the increment of `plot`, about 0.9 GB at 1 billion rows).
  `plot3` rises to 2.6 GB and `view_load` to 3.3 GB (because 3 virtual variables are first drawn at the same time). Showing many of the 50
  columns at 500 million rows x 50 columns reaches over ten GB.
- The first draw of virtual variables (`derived_plot`, `derived2_plot`, `view_load`) is, after all, the same work as computing that variable once
  over all rows (about the same seconds per variable as `derived_full`).

**Implications for stage 2 (58.5)**:

1. Split the processing that scans all rows (statistics, histogram, virtual-variable computation, thinning for the first draw) across multiple
   threads/processes per row group. First `derived_full`, `hist_full` and `view_load` (if close to 10 cores can be used, around 10 seconds
   even at 1 billion rows).
2. Switching the time axis (a non-ascending / non-time column) should not load all rows as float64 (extend `TimeIndex`, or windowed reads).
   The memory-limit setting (58.5) refuses this kind of operation before it starts.
3. When opening a view, either parallelize the computation for the first draw of plotted virtual variables, or defer it with ch. 55's laziness
   and open the screen first.
4. Next measurement: real 50 columns, float64, hard-to-compress values, and a size that does not fit in the page cache (a file over 24 GB).
   This result is the lower bound when the amount read is small.

#### 58.5 Stage 2 policy (2026-10-15, user's decision)

The target scale is 500 million rows x 50 columns (5.52). Estimates from halving stage 1's values by row count: open a view about 22 s, virtual
variable over all rows about 20 s (on the border of the 20 s target), histogram about 9 s, first draw about 10 s, statistics about 5 s (within
target). However, the stage 1 files compress well and were in the page cache, so values with real data, where disk reads are the bottleneck,
are unknown. So proceed in this order.

1. **Stage 2-0: re-measure with files close to real data** (58.5.1). Confirm here how effective the parallel approach is (whether CPU or reads are
   the bottleneck).
2. **Stage 2-1: run calculations over all rows in parallel chunks**. The approach **starts with threads** (per row group; numpy and polars release
   the GIL during computation; built on `WorkerHub`). If that does not work, consider processes. Targets: whole-column statistics, histogram,
   virtual-variable computation over all rows, export (stage 2 of 57.4). Making the map faster in stage 2 of ch. 46 (expressions 55.8) is
   decided together here.
3. **Stage 2-2: when opening a view, compute and draw only the displayed range first, and continue the all-rows computation in the
   background** (implication 3 of 58.4). Make the time from opening a view to a picture appearing independent of the row count. Operations
   that need all rows (statistics, etc.) wait for the all-rows computation to finish, or compute in chunks.
4. **Stage 2-3: memory-limit setting** (`Settings.memory_limit_percent`, e.g. 95), and a mechanism that estimates and refuses, before starting,
   operations likely to exceed the limit (switching the time axis to a non-ascending column, showing many columns, etc.).
- The details (design and test plan) of 2-1 to 2-3 will be written after seeing the results of 2-0. Candidates not yet decided: not holding the
  whole time axis in memory (extend `TimeIndex` to non-ascending / non-finite cases, or make the time axis windowed too), and how to handle 56.4's
  sorted display on large files.

#### 58.5.1 Stage 2-0: measurement with files close to real data

- File: **500 million rows x 35 columns x float32, hard-to-compress values** (`--dtype float32 --values full`. About 74 bytes/row for a trial of
  2 million rows x 25 columns -> about 51 GB for 35 columns. 2026-10-15, user's decision: real data is float32 and about 50 GB. Changed from the
  initial plan of 25 columns float64, about 100 GB, and the next plan of 12 columns float64, about 47 GB). Parquet reads only the columns used,
  so the read and compute time per column is determined by row count and type and hardly depends on the number of columns (the number of
  columns matters for memory when showing many columns, which is extrapolated from the per-column value to 50 columns). The app turns every
  column read into float64 (`to_numeric`), so float32 columns double in memory. The time and memory of this conversion are included in the
  measurement.
- **Actual usage** (user, 2026-10-15): use **about 10** original columns and make **about 20** virtual variables from them by calculation. Make the
  measurement's `view_load` stage match this: a view that makes 20 virtual variables (including chains) from 10 original columns (make
  `VIEW_DERIVED` etc. in `bench_large_file.py` configurable, keeping the default stage 1 shape). **Memory estimate**: a virtual variable of 500
  million rows holds 4 GB (float64) if it keeps all rows, 80 GB for 20, far above the 24 GB of physical memory. Confirm with this stage's memory
  where expressions that do not become windowed virtual variables (36.8 C, `DerivedColumn`), and all-rows computations (ch. 55's
  `DeferredColumn` keeps all computed rows), occur. This is expected to be the main issue in the design of stage 2 (2-1 to 2-3).
- **Page cache**: a measurement stage reads the time axis and the first 4 columns (`cols[:4]` in `bench_large_file.py`), about 19 GB in
  total. Regardless of the whole file size this can fit in 24 GB of physical memory, so enlarging the file does not make a disk-read
  measurement. So take both **measurements with the page cache emptied before each stage** (`sudo purge` on macOS; the user runs it, or the
  measuring script calls it before a stage with an option) and measurements without emptying.
- Add `--values {quantised,full}` (default `quantised` = as before) to `generate_wide_parquet.py`. `full` does not round each signal's values,
  and adds small noise (standard deviation about 1e-6; the random seed is `(seed, start)` as now) to all signals so that mantissas vary and
  zstd barely compresses them. Known relations are kept with a widened tolerance (written in the docstring). The disk check (58.2) is used as is.
- Measure with `bench_large_file.py` in the same stages and form as stage 1 (the table in 58.4), on a 500 million row x 35 column float32 file.
  For comparison, also measure a compressible 500 million row x 20 column float32 file (`--values quantised`) (to see the difference from the
  page-cache-resident case). Stop other heavy processing (such as a full test run) before measuring.
- Record: the same table as 58.4 (seconds, MB, number of CPU cores) plus a guide to disk read speed (bytes read in that stage / time. If
  `io_counters` of `psutil` cannot be obtained on macOS, estimate from the sizes of the columns read). The results are recorded as a table
  below this section and reported to the user.
- Tests: a small run of `--values full` (10,000 rows x 5 columns): type, row count, known relations (widened tolerance), same content for the
  same seed, and a larger file than `quantised`.
- Decided during implementation (2026-10-15, preparation for stage 2-0):
  - The noise of `--values full` is taken from a stream (`(seed, start, 1)`) separate from the existing random stream (`(seed, start)`). The
    `quantised` output is byte-for-byte the same as before (default value). The tolerance is `FULL_TOLERANCE` (1e-4).
  - Read speed is taken from `io_counters` of `psutil` (difference in bytes read per stage). macOS has no `io_counters`, so the read column is
    left blank ("-") there and no estimate is built into the script. On the measuring machine (Mac), estimate at report time as (size of the
    columns each stage read (Parquet metadata)) / time (supplemented at merge-time check). On Linux the `io_counters` value is shown (bytes
    read from the page cache are not counted).
  - Trial measurement (2 million rows x 25 columns float64): `full` is about 185 bytes/row (compression about 10%), `quantised` about 15
    bytes/row. Extrapolated to 500 million rows, `full` is about 93 GB and generation about 5 minutes (assuming generation speed is
    proportional to the row count).
- **Make the shape of `view_load` configurable** (2026-10-15, continuation of stage 2-0. `feat/58-bench-view-shape`. Medium -> `implementer`):
  - Add to `bench_large_file.py` `--view-raw N` (number of original columns the view uses, default 4), `--view-derived M` (number of virtual
    variables, default 10) and `--view-plotted K` (how many of those are plotted, default 3). With the defaults, the view (list of formulas,
    names and colors) is **the same** as now (to compare with the stage 1 results). For measuring actual usage, use `--view-raw 10
    --view-derived 20 --view-plotted 5`.
  - Original columns: the first N numeric columns of the file (excluding the time axis; widens the current selection in `bench_columns`).
    Stop before starting if there are not enough. If N exceeds 4 for the `BENCH_COLUMNS` file (`generate_test_parquet.py`, 4 columns), stop.
  - Formulas (built in a fixed order, no randomness): for the part beyond the default shape, (1) the first half are element-wise formulas of
    original columns (cycled so that every original column is used at least once), (2) the second half are **chains** that use earlier virtual
    variables (e.g. `d03 + d07`, `d10 * c4`). Include one each of `rolling_mean(<original column>, 100)`, `delta(<virtual variable>)` and
    `where(<original column> > 0, <virtual variable>, <original column>)` (to also measure formulas with a windowed-read margin and
    conditional formulas). All are formulas with a known margin (forms that can become `DerivedColumn`; if they don't, that is the
    measurement result). The ones plotted are, in the default shape, the first K as before, and otherwise a half-and-half mix of "formulas of
    original columns only" and "chain formulas" (including deep chains; colors cycle through a fixed list).
  - Additional records (the `view_load` stage): in addition to the point where drawing settles (the current end), also take the time and RSS
    at the point where **the background all-rows computation (ch. 55's `DeferredColumn`) has completely finished** (the `view_load_full`
    column; the upper limit of the wait is `--stage-timeout`). At both points, count the kind of column of each virtual variable
    (`DerivedColumn` / `DeferredColumn` (whether computed) / `MaterializedColumn`), output per name in JSON and in the table in the form
    "Derived 18 / Deferred 2 / Materialized 0". Which columns hold all rows is material for the design of 2-1 to 2-3.
  - `--purge`: empty the page cache before each stage (just before the measured stage, not counting preparation stages that are not
    recorded). macOS uses `sudo -n purge`; Linux uses `sync` and then `sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches'`. Try it once in the
    parent process before starting, and if it fails because a password is needed, show "Run `sudo -v` first, or allow purge without a
    password" and stop (if it fails in a middle stage, mark that stage `error` and continue). The time to empty is not included in the
    measurement. Leave whether `purge` was used in the JSON and the table header.
  - Tests (`tests/parquet_analyzer/test_bench_view_shape.py`, new): make the formula builder a pure function; with the default shape it equals the
    current list / with N=10, M=20 all original columns are used, there are chains, no duplicate names, it includes `rolling_mean`, `delta` and
    `where`, the number plotted is K, all formulas are syntactically valid (`referenced_names` passes), and every reference is "an original
    column or an earlier virtual variable" / assembly of the purge command (per OS) and how it stops on failure (`subprocess.run` is replaced;
    the real `sudo` is not called) / on a small file (100,000 rows x 12 columns, `generate_wide_parquet.py`), `--stages view_load --view-raw 10
    --view-derived 20` runs to the end and the sum of the column-kind counts is 20 (run in a child process; keep the row count small so it
    does not take too long).
  - The measurement itself (generating the file of about 51 GB and each stage) is done after this implementation is merged, when there is no
    heavy work such as the user's real-machine check.
  - Decided during implementation (2026-10-15, `feat/58-bench-view-shape`):
    - Default shape = 4 original columns and 10 or fewer virtual variables (the current list or its beginning). Anything else is a generated list
      (e.g. N=4, M=12 is also generated). If the argument ranges (N, M >= 1, 0 <= K <= M) are violated, stop before starting (exit code 2).
    - If purge fails in a middle stage, that stage is marked `error` ("purge failed: ...") **without measuring** and the run moves on (so that
      values measured while still warm are not kept).
    - The `view_load` row (time, RSS, CPU) is for the point where drawing settles (to compare with stage 1). The `view_load_full` row is up to
      the end of the all-rows computation (`TIMEOUT` if the limit is reached). The read speed of the `view_load` row is blank.
    - **Finding**: the background computation for view loading (`view_compute` in 55.3) computes **only the virtual variables plotted and the
      virtual variables they use**. Virtual variables not plotted stay `DeferredColumn` and are not computed (they are computed when used).
      The wait for drawing to settle also waits for `view_compute`, so the settled point and the end of the all-rows computation are nearly the
      same. In the 100,000 rows x 12 columns trial (`--view-raw 10 --view-derived 20 --view-plotted 5`) the result was "Derived 0 / Deferred 20
      (17 ready) / Materialized 0" (a small file keeps the original columns resident, so they do not become `DerivedColumn`). At 500 million
      rows the issue becomes "all rows of the virtual variables plotted and those they use (4 GB each)".

#### 58.6 Test plan (stage 1)

- Small runs of `generate_wide_parquet.py` (10,000 rows x 5 columns, 1,000-row row groups) in pytest: row count, column names, types, number of
  row groups, known relations (with tolerance), same content for the same `--seed`, number of time jumps, and the disk-estimate function (stops
  when free space is insufficient; the free-space value is replaced).
- The measurement script does not turn the measurement itself into a test (same as ch. 41). Only confirm that new stages run to the end on a small
  file (100,000 rows).

### 59. Correctness and boundary conditions on large data (added 2026-10-15)

Requirement: 5.53. Background: the confirmation results of external review 3, and the response to the review's "verify boundary
conditions, and measure performance and numerical correctness at 500M rows" (user's decision, 2026-10-15). Order: 59.2 and 59.3 (one branch,
`fix/59-pyramid-alignment`, medium -> `implementer`) can start right away. 59.1 (`feat/59-bench-verify`, medium -> `implementer`) touches the
measurement script, so create it from `main` after `feat/58-bench-view-shape` of 58.5.1 is merged, and **merge it before the stage 2-0
measurement of ch. 58** (so correctness is also checked in that measurement).

#### 59.1 Add correctness checks to the measurement (`bench_large_file.py --verify`)

- After each stage (after recording time and RSS; the check time is not included in the stage time), compare that stage's result with a value
  calculated by a **path separate from the app**. The separate path = read directly with `pyarrow.parquet` (`iter_batches`, `read_row_group`)
  and calculate with numpy. The app's `ParquetDataSource`, `WindowedColumn`, `TimeIndex`, `chunked_stats` and pyramid are not used. Only
  expression evaluation may use the app's `evaluate_expression` (what is to be checked is the correctness of chunks, windows, chains and
  background computation, not numpy's operations themselves).
- `--verify` is on by default, `--no-verify` turns it off. The result is stored in JSON per stage as `verify` (`ok` / `failed: <detail>` /
  `skipped: <reason>`) together with the check seconds, and a "correctness" column is added to the table. Measurement continues even on failure.
  Checks may read all rows (statistics, histogram), so they may take time. Checking reads warm the page cache, but with `--purge` it is emptied
  before the next stage (58.5.1).
- What is checked (per stage; `skipped` if the target stage did not run):
  - `stats_full`, `derived_full`: count, NaN count, min and max **match**; mean and standard deviation within relative error 1e-9. The separate
    path reads per row group, and the mean uses two scans (the first for the mean, the second for the sum of squared deviations; sums add the
    per-chunk partial sums with `math.fsum`). The `derived_full` formula (`a*b+c`) is computed per row group with numpy.
  - `hist_full`: the count in each bin **matches** the sum of per-chunk `np.histogram` over the same bin edges. The total count = the number of
    finite values.
  - Pyramid (after `home1`, if a pyramid was built): the bucket start rows are all multiples of 8192 and line up as `0, 8192, 16384, ...` without
    gaps / the overall min and max match the column's min and max (the values from the check above) / for 20 buckets chosen with a fixed-seed
    random number, the min and max of the rows read directly **match**.
  - Time -> row (after `zoom10`, `zoom1000`, `stats_zoom`): for the times at both ends of the displayed range, read the rows before and after
    the row `r` the app found, and check that `t[r-1] < x <= t[r]` (left side) / `t[r-1] <= x < t[r]` (right side) hold (matching the
    convention of `row_range_for_x`). Also the same check for 20 times chosen with a fixed-seed random number, and for times at the first, last
    and row-group boundaries.
  - Window values (after `zoom1000`, `derived_plot`): for the displayed row range, the windowed values of original columns (float32 converted
    to float64 as `to_numeric` does) and virtual variables match values read directly and calculated (original columns **match**; virtual
    variables within relative error 1e-12; NaN positions match).
  - `view_load` (after 58.5.1's `view_load_full`): for each of the 20 virtual variables, take three windows (10,000 rows each) at the start,
    middle and end, read the original columns directly with enough margin before and after the window (a fixed value larger than the sum of the
    margins of the measurement view's formulas, e.g. 1000 rows), compute in chain order with `evaluate_expression`, and compare (within relative
    error 1e-9; looser for `rolling_mean` because the order of rounding may change at the start of a chunk; NaN positions match). Both
    `DeferredColumn` (holds all rows) and `DerivedColumn` (computed by window) are covered.
  - `parquet_export` (once the stage actually performs an export): the exported file's row count, column names and types, and the values at 20
    rows match the original columns and the computed values. Until then `skipped`.
- Implementation: the comparison functions (statistics, histogram, pyramid, time -> row, window) are pure functions that take numbers and
  arrays rather than app objects (easy to test and clearly a separate path).
- Tests (`tests/parquet_analyzer/test_bench_verify.py`, new): match and mismatch per pure function (shifting one value gives `failed`; a
  difference in NaN positions is also detected) / on a small file (100,000 rows, 7,000-row row groups, with float32 columns and time jumps), a
  measurement with `--verify` runs to the end and every stage is `ok` or `skipped` / deliberately breaking one place in the app's result
  (monkeypatching a statistics maximum, one pyramid bucket, one value of a virtual variable window) makes that stage `failed` (when run in a
  child process, the breaking hook is passed to the child via an environment variable; it does not affect the production path).

#### 59.2 Align pyramid buckets ourselves (`core/pyramid.py`)

- Current state (confirmation of external review 3 #1): `build_pyramid` treats each batch returned by `iter_batches(batch_size=bucket_rows)` as one
  bucket. pyarrow 25 returns `bucket_rows` rows at a time across row groups, so bucket start rows are multiples of 8192 and match the
  `TimeIndex` anchors (data 36.6 D, `ANCHOR_ROWS = DEFAULT_BUCKET_ROWS`). But this is pyarrow's behavior; in versions or settings that cut
  batches at row-group boundaries, the start rows shift and `TimeIndex.take` reads non-anchor rows one at a time (drawing becomes slow; values
  are correct). The tests also do not confirm this (`test_pyramid.py` uses 5000-row row groups and 1000-row buckets, and 5000 is a multiple of
  1000, so there is no shift).
- Change: `build_pyramid` repacks rows into buckets of `bucket_rows` regardless of the sizes of the batches returned (joining the remainder of
  the previous batch to the head of the next. The calculation "read in bulk, reshape, then min/max" shared with `build_pyramid_from_windows` may
  be made a common function). The size of the batch read is a multiple of `bucket_rows` (e.g. 64 buckets) to reduce the number of calls. The
  result has bucket start rows `arange(n_buckets) * bucket_rows`, and only the last bucket may be shorter. Handling of NaN and Inf (a bucket with
  no finite value is NaN) is the same as now.
- Guard: `WindowedColumn.set_pyramid` (and the same handling in `DerivedColumn`) does not accept a pyramid whose start rows are not multiples of
  `ANCHOR_ROWS` or that has gaps, and treats it as a build failure (counted as in 28.2; the reason is logged). It does not occur as long as
  the pyramid is built correctly.
- Tests (add to `test_pyramid.py`): row groups of 5000, 7000, 8192 and 1 million rows, and a row count that is not a multiple of 8192: all start
  rows are `k * 8192`, and min and max match the values found per bucket from all rows / a **regression test**: even if `iter_batches` is
  replaced to return batches of uneven sizes (e.g. 5000, 3192, 7000, ...), the start rows line up (this must fail with the current code) /
  guard: passing a shifted pyramid to `set_pyramid` is counted as a failure, and drawing returns to actual reads.

- Decided during implementation (2026-10-15, `fix/59-pyramid-alignment`): the batch read is 64 buckets. The per-bucket min and max is
  `_bucket_minmax`, shared with `build_pyramid_from_windows`. A pyramid that was not accepted follows 28.2's failure counting, so it is rebuilt
  at most 3 times (`MAX_PYRAMID_FAILURES`), after which drawing uses actual reads. A windowed column does not send a failure notice (a
  virtual-variable column still reports `ok=False` as before). On 20 million rows (float32) the result matches the old version and the time went
  from 0.07 s to 0.04 s. The regression test was confirmed to fail before the fix.

#### 59.3 Add property tests for boundary conditions (hypothesis, app ch. 46 settings)

- Add to the existing property tests (`test_property_time_index.py`, `test_property_windowed.py`, `test_property_row_range.py`,
  `test_property_analysis.py`). Before adding, check what the current tests generate so as not to overlap existing checks (do not add what
  overlaps).
- What is generated: row count (1 to 50,000), row-group size (any value from 1 to 20,000, many not multiples of 8192), value type (float64,
  float32, int32), and NaN, +Inf, -Inf, runs of duplicate times and all-NaN buckets placed before and after bucket boundaries (multiples of
  8192, row-group boundaries, `chunked_stats` chunk boundaries).
- Properties checked:
  - Pyramid (after 59.2): start rows line up, the min and max per bucket match the values found from all rows, and `pyramid_bounds` is the min
    and max of the finite values.
  - `TimeIndex`: `take`, `searchsorted` and `value_at` match the results on the full array (including NaN and duplicates at boundaries. Also
    confirm that a non-ascending or non-finite time axis does not build a `TimeIndex` and falls on the warning side, per the current agreement
    (ch. 29, 36.6 D)).
  - Windowed columns: for float32 and int32 columns, the values of any window match `to_numeric` of the full column.
  - Window-computed virtual variables (`delta`, `rolling_mean`, `trailing_mean`, `where`): in windows with NaN or Inf at the boundaries, they
    match the full computation (including NaN positions).
  - `chunked_stats`: even if the chunk size changes (1 row, all NaN at a boundary), count, NaN count, min and max match, and mean and
    standard deviation are within relative error 1e-9.
- The number of examples is the default of 46.1 (do not make CI too slow; if a single test exceeds a few seconds, lower `max_examples`). Fix
  failing examples with `@example`.

#### 59.4 Test plan (summary)

- Decided during implementation of 59.1 (2026-10-16): the check is run by a child process after the stage's RESULT line and returned in a separate
  `VERIFY` line (the check time is not in `wall_s` or RSS). If `--verify-timeout` (default 3600 s) is exceeded, the stage becomes "skipped", the
  child is stopped, and the remaining stages continue in a new child. The reference side is `ParquetRef` in `bench_verify.py` (pyarrow/numpy
  only; it does not use `ParquetDataSource`, `WindowedColumn`, `TimeIndex`, `chunked_stats` or pyramid construction). Tolerances: mean and
  standard deviation of statistics 1e-9, virtual-variable windows 1e-12 (chained `view_load` 1e-9), raw columns exact. The relative tolerance is
  `rel*max(|a|,|b|) + rel*the maximum absolute value of the reference inside the window` (so that values cancelling to near 0 are not judged
  against themselves). The pyramid check runs only when there is a pyramid (the app builds one when displayed rows exceed 5 million), otherwise
  "skipped". The `parquet_export` stage is "skipped" until the export entry point exists. The fault hook for tests, `BENCH_VERIFY_FAULT`
  (`stats_max` / `pyramid_bucket` / `derived_window`), and `BENCH_VERIFY_PYRAMID_THRESHOLD` are not used in production. Unconfirmed: the check
  time at 500M rows and the effect of `--purge`.
- As in each section of 59.1, 59.2 and 59.3. The full test suite passes, and `uv run ruff check` passes.
- The regression test of 59.2 fails before the fix (confirm with the replaced batches).
- Confirmation at merge for 59.1: run a measurement with `--verify` on a small file (100,000 rows) and on something like
  `daily_cycle_500mb.parquet`; everything is `ok` or `skipped`.

## Relations to other domains

- **Plots** ([plots.md](./plots.md)): drawing series uses this document's downsampling, windowed reads and
  pyramid (`TimePlotWidget._redraw_series`). Switching the time-axis column (4.2) calls `set_x_data`/`go_home`
  on every time-domain plot.
- **Analysis** ([analysis.md](./analysis.md)): FFT and XY read raw data of a range with
  `MainWindow.variable_window` (even windowed columns are never held whole). Statistics on windowed columns or windowed derived
  variables are aggregated in chunks in the background with `chunked_stats` (ch. 53); other statistics use `variable_window`.
- **Expressions** ([expressions.md](./expressions.md)): derived variables are evaluated via `LazyVariables`,
  materializing only the columns the expression refers to.
- **Persistence** ([persistence.md](./persistence.md)): settings such as `eager_load_limit_mb`, recently used
  folders.
- **All domains (background work)** (2026-10-03): background work follows ch. 28 (failure reporting) and ch. 37
  (`WorkerHub`, GC only on the UI thread). Plots, analysis, statistics and FFT work all use this mechanism.
- **Plots, analysis** (ch. 29): a time axis that isn't ascending or has missing values triggers a warning.
  Visible ranges, statistics, cursors and XY assume ascending order.
- **Persistence** (ch. 33): the time-axis column saved in a view switches the time axis on load
  (`set_time_column`).
- **App foundation** (ch. 30): the start, completion and failure of background work are recorded as `job` in the
  operation log.
- **Persistence** (persistence ch. 54, 2026-10-14): a signal alias adds the same `Column` under the alias to `_file_columns` after the file
  is opened (no copy). The time-axis selection and a view's `time_column` are also resolved through the alias. The choices for variables are
  built from the "names shown in the list" (54.2).
- **Plots** (59.2, 2026-10-15): the start rows of pyramid buckets always match `TimeIndex` anchors (every 8192 rows); a misaligned pyramid is not accepted. This is the premise that lets drawing look up bucket start times (`x[bucket_starts]`) without reading disk.
- **Analysis, plots** (ch. 56, 2026-10-15): when the time axis is not ascending, statistics, XY, histogram, FFT, cursor and hover show a warning (they look at `MainWindow.time_order_unreliable()`). In stage 2: sorted display (`SortedColumn`, original row number) and UTC display of time-zone-aware times (time display gathered in one place in `core/`).
- **Image export / Parquet export** (56.4, ch. 57): when exporting while displayed sorted, a column `source_row` with the original row number is added.
- **All domains** (ch. 58, 2026-10-15): measurement for large data (target 500 million rows x 50 columns; stage 1 was done with 1 billion rows x 20 columns). Stage 2 decides the memory-limit setting, the handling of the time axis and calculations run in parallel chunks.
