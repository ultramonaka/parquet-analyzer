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

#### 5.22 Reporting background-job failures, checking the time-axis order (added 2026-10-02, from fact-checking an external review)
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
- Details in [ch. 28](#28-reporting-background-job-failures-added-2026-10-02-from-fact-checking-an-external-review)
  and [ch. 29](#29-checking-the-time-axis-order-added-2026-10-02-from-fact-checking-an-external-review).

#### 5.29 The next stage of large-data support (added 2026-10-02, from fact-checking an external review)
- Handle only the visible range of the time-axis column and of derived variables' results too, keeping
  memory use down even for files of around 100 million rows. In addition, manage the windowed-read cache
  by memory size, and make background work that is no longer needed stoppable midway.
- 2026-10-04: the time-axis index, windowed computation of derived variables and the thinned first draw
  were adopted and implemented (cache memory management and cancellation were not adopted). The design is
  in [36.8](#368-the-adopted-design-2026-10-04-from-the-367-decision-and-the-reviewers-review).

#### 5.46 Aggregating statistics over a whole column window by window (added 2026-10-13, stage 1 of backlog item 29; external review 2, candidate C)
- When "Statistics" is pressed on a large file (a windowed column), the rows of the displayed range are not read all
  at once; they are read in chunks and aggregated as they go. The 4–6 GB needed for statistics over the whole of a
  100-million-row column (36.7) is brought down to about the amount resident after drawing.
- The reading is not done on the UI thread but inside background work (today the UI thread reads all rows, so the
  screen freezes meanwhile).
- The results (count, missing, min, max, mean, standard deviation, median) are the same as now (the median stays exact).
- Separating `values()` (the read-everything API) and making it explicit by name is specified later as stage 2
  (user's decision, 2026-10-13).
- Details in [ch. 53](#53-aggregating-statistics-over-a-whole-column-window-by-window-added-2026-10-13-stage-1-of-backlog-item-29).

#### 5.30 Reliably releasing background-job parts and protecting UI objects (added 2026-10-03, from the 13.6 investigation)
- When background work (downsampling, envelopes, statistics, FFT, analysis windows) finishes, the parts
  made for it and the data passed to it are reliably released (previously they weren't, and memory grew
  the more the app was used).
- Python's cleanup of data no longer used (garbage collection) runs only on the UI thread, preventing
  crashes from UI objects being destroyed on a background thread.
- Details in [ch. 37](#37-releasing-background-job-parts-and-garbage-collection-added-2026-10-03-from-the-136-investigation).

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

### 28. Reporting background-job failures (added 2026-10-02, from fact-checking an external review)

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

#### 28.4 Discarding old results of the previous file (added 2026-10-08, from the A2 results; backlog item 30)

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

### 29. Checking the time-axis order (added 2026-10-02, from fact-checking an external review)

Requirement: 5.22. Background: `MainWindow._visible_row_range`, `TimePlotWidget._redraw_series` and the
cursors' nearest-sample lookup use `np.searchsorted`, assuming an ascending time axis. Any column can be
chosen as the time axis, yet that assumption was checked nowhere. `ParquetDataSource.is_monotonic()` was
implemented but unused, and it looks only at row-group statistics, so it can't see disorder within a row
group (external review #1 in `idea.md`, fact-checked).

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

#### 29.3 Test plan (including the error-case tests of external review #12)

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

### 36. The next stage of large-data support (added 2026-10-02, from fact-checking an external review)

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
  linked group's visible range (via `_on_grid_layout_changed`; the sixth path of LEARNINGS 2026-10-02).
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

Requirement: 5.30. Background (the investigation in `history.md` 13.6):
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

### 40. Navigator overview of windowed columns (added 2026-10-03, from `backlog.md` 13.7)

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

### 41. Measuring at the scale of 100 million rows (recording a baseline) (added 2026-10-03, the remainder of external review #12)

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

### 53. Aggregating statistics over a whole column window by window (added 2026-10-13, stage 1 of backlog item 29)

Requirement: 5.46. Sources: idea.md "External review 2" #4, candidate C, and "Remainder of large-data support" (2).

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
5. Not changed in stage 1: the name and API of `values()` (stage 2), the inside of `chunked_stats` (the "median bin becomes large
   for a column where one value dominates" item of idea.md "Remainder of large-data support" (4); the ±1e308 overflow in backlog is a
   separate issue and stays as is), and how histogram, XY and FFT read data.

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
- **History** (history.md): the details of the design and measurements of lazy/windowed reads and
  the pyramid are in old 13.5.1 (the large-file memory problem). Always consult it when changing this domain's
  design.
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
