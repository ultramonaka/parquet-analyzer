# Parquet Analyzer Detailed Specification / 詳細仕様書

- Version / 版数: v0.6 (updated 2026-09-19: §13.5.1 corrected and extended with a second
  independent Opus/Fable re-investigation)
- Role / 位置づけ: fleshes out `specification.md` (the requirements spec) to
  implementation level. Resolves items 11.1, 11.5, 11.6 of `specification.md` §11 and
  details 11.3/11.4/11.7 to an implementable level. See `specification.md` for the
  requirements-level background/reasoning.

English first, 日本語 below — see the note at the top of `specification.md` for why.

---

## English

### 1. Technology Choices (Finalized)

| Item | Choice | Reasoning |
|---|---|---|
| Plotting library (11.1) | `pyqtgraph` | The draft's `Pygraph` is interpreted as a variant spelling of `pyqtgraph`. Finalized for its PySide6 compatibility and track record rendering large point counts. |
| Parquet reading (11.6) | `polars` | Lazy evaluation via `pl.scan_parquet` reads only the needed columns/row range. A naturally columnar API, more memory-efficient than `pandas`. Lightweight operations like schema inspection also use `pyarrow` internally. |
| Packaging as an .exe (11.5) | `PyInstaller` | Well-proven for PySide6 apps. Distributed as `--onedir` to avoid slower startup. |

### 2. Module Structure

```
src/parquet_analyzer/
├── __main__.py               # GUI entry point
├── convert_cli.py            # CLI entry point for the MDF/MATLAB -> Parquet converter (argparse; touches no Qt)
├── config.py                 # shared path-definitions module (stdlib-only; importable from other projects)
├── ui/
│   ├── main_window.py        # MainWindow: menus, toolbar (H/R buttons), overall layout
│   ├── plot_grid.py          # PlotGridWidget: arranges/adds/removes the stacked multi-plot area
│   ├── plot_widget.py        # TimePlotWidget: wraps pyqtgraph.PlotWidget, zoom/pan handling
│   ├── navigator.py          # NavigatorWidget: the shared, single overview + selection rectangle
│   ├── variable_panel.py     # VariablePanel: variable list, drag-and-drop, right-click menu
│   ├── expression_bar.py     # ExpressionBar: the expression input field
│   ├── stats_dialog.py       # StatsDialog: non-modal window showing statistics results
│   ├── settings_dialog.py    # SettingsDialog: the settings menu dialog
│   └── folder_picker.py      # FolderPickerDialog: data folder selection + recent-folder history
├── core/
│   ├── data_source.py        # ParquetDataSource: wraps a polars LazyFrame, schema/range reads
│   ├── downsample.py         # lttb(x, y, n_out) -> (x_ds, y_ds), called from a QThreadPool worker
│   ├── background_worker.py  # BackgroundWorker(QRunnable): runs an arbitrary callable off the UI
│   │                          # thread and returns its result via a signal (shared by downsample
│   │                          # recompute/FFT/statistics; the caller in plot_widget.py is
│   │                          # responsible for including a generation number in its own result
│   │                          # tuple and checking it, for stale-result discarding)
│   ├── expression.py         # evaluate_expression(expr, variables): restricted evaluation
│   ├── analysis.py           # fft(x, y), delta(y), rolling_mean(y, window), basic_stats(y)
│   ├── variable.py           # Variable / DerivedVariable data structures
│   ├── convert.py            # MDF/MATLAB -> Parquet conversion (see §15); GUI-independent, only
│   │                          # reachable via convert_cli.py, not the GUI app
│   └── oplog.py              # debug-mode operation-log helper, log_op()
└── io/
    ├── view.py               # View data structure, save_view()/load_view()
    └── settings.py           # Settings data structure, load_settings()/save_settings()
```

Each module's responsibility is as commented above. `ui/` depends on PySide6/pyqtgraph;
`core/` and `io/` are GUI-independent (for unit-testability and to leave room for a
future CLI/other UI).

### 3. Startup Sequence

1. `tools/run.sh` / `run.bat` add `src` to `PYTHONPATH` and run
   `uv run python -m parquet_analyzer`.
2. `__main__.py`:
   1. Calls `config.ensure_dirs()`, creating the `parquet_analyzer/` subdirectories
      under `cfg`/`data`/`log`.
   2. Configures `logging` to write under `config.PARQUET_ANALYZER_LOG_DIR`.
   3. Loads `cfg/parquet_analyzer/settings.json` (or defaults if it doesn't exist).
   4. Starts `QApplication` and shows `MainWindow`.

### 4. Controls (Finalized, 11.3, v2)

| Action | Effect (current default — see below, this is configurable) |
|---|---|
| Mouse wheel scroll | Pan along the X (time) axis (move only, no zoom) |
| Ctrl + scroll | Zoom the X (time) axis only |
| Shift + scroll | Zoom the Y (amplitude) axis only |
| H key / Home button | Reset the view to show all data |
| R key / Redraw button | Redraw the plot for the current view (including a downsampling recompute) |

- Implemented in `TimePlotWidget.wheelEvent` (`ui/plot_widget.py`). The three actions
  are completely separated — no modifier ever performs more than one of them — but
  **which modifier performs which action is configurable** (`Settings.scroll_bindings`,
  a `{"none", "ctrl", "shift"}` -> `{"x_zoom", "y_zoom", "pan"}` permutation, 設定
  dialog's "スクロール操作" section; see the v3 history note below for why this
  differs from the v1 "both-axis zoom" toggle that was deliberately removed, and for
  why the table above no longer matches the original v2 scheme's assignment).
- The Shift-pan distance is "15% of the currently visible X span, per wheel notch
  (120 units)" (`_PAN_STEP_FRACTION = 0.15`). The direction is hardcoded so that
  scrolling up moves backward in time; if the opposite feels more natural, just flip
  the sign inside `wheelEvent`.
- **Two cross-platform gotchas** (found via real-hardware feedback, both handled
  inside `wheelEvent`):
  - On macOS, Qt reports the physical Ctrl key as `Qt.KeyboardModifier.MetaModifier`
    (Qt swaps Cmd/Ctrl to match cross-platform shortcut conventions). Checking only
    `ControlModifier` would make the physical Ctrl key incorrectly fall through to
    plain-scroll behavior (time zoom). Fixed by treating both `ControlModifier` and
    `MetaModifier` as "Ctrl".
  - Some trackpads/OSes report a Shift+vertical-scroll gesture as a horizontal wheel
    event (the `angleDelta()` value lands in `.x()` instead of `.y()`). Checking only
    `.y()` would make Shift-pan silently do nothing. Fixed by using whichever of
    `angle_delta.y() or angle_delta.x()` is non-zero.

> **History (v1 -> v2)**: the design originally had plain scroll = zoom both axes,
> Ctrl = X-axis-only zoom, Shift = Y-axis-only zoom, with the both-axis zoom
> switchable to X-only via the (now-removed) `both_axis_scroll_zoom` setting (this in
> turn was itself a fix for an even earlier version where plain scroll and Ctrl both
> zoomed the X axis, a contradiction). Real usage feedback said this felt bad, so it
> was changed to the current fully-separated scheme: plain = time zoom, Ctrl =
> amplitude zoom, Shift = time pan. Since plain scroll is now always dedicated to
> time zoom, the whole both-axis/single-axis toggle setting became unnecessary and
> was deleted (the `Settings.both_axis_scroll_zoom` field, and the corresponding
> `TimePlotWidget`/`PlotGridWidget` constructor arguments, were removed entirely, not
> just defaulted off).

> **v3 (2026-09-19, user-requested): which modifier performs which action is now
> configurable again, but not the same setting as v1's.** The v1 setting picked
> between two different *schemes* (both-axis zoom on plain scroll, vs. the
> fully-separated scheme) and was removed because real usage found the variable
> scheme confusing regardless of which mode was picked. This is a different kind of
> knob: the three actions stay exactly as separated as the fixed v2 scheme (no
> modifier ever performs more than one action, and every action is always reachable
> by exactly one modifier) — only *which* modifier maps to *which* action moves.
> `Settings.scroll_bindings: dict[str, str]` (`io/settings.py`) is a `{"none",
> "ctrl", "shift"}` -> `{"x_zoom", "y_zoom", "pan"}` permutation. Initially defaulted
> to the v2 scheme (`{"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}`), then
> changed the same day, once real usage under the new setting existed: read directly
> from the user's own `cfg/parquet_analyzer/settings.json` (`{"none": "pan", "ctrl":
> "x_zoom", "shift": "y_zoom"}`) and made that the new `DEFAULT_SCROLL_BINDINGS`,
> per an explicit request to make their already-adjusted preference the default for
> a fresh install rather than guessing at one. `normalize_scroll_bindings()` rejects
> anything that isn't exactly a permutation (missing modifier, duplicate/unknown
> action) wholesale, falling back to the default, rather than trying to patch a
> partially-corrupt mapping — a
> half-invalid mapping could otherwise leave an action unreachable by scroll at all,
> which `TimePlotWidget.wheelEvent` has no way to detect or recover from at the point
> it actually needs a binding. `TimePlotWidget.scroll_bindings` (normalized in
> `__init__`, settable live via `set_scroll_bindings()`) is consulted in
> `wheelEvent` in place of the old `if shift: ... elif ctrl: ...` branching — the
> shift-wins-over-ctrl precedence when both are held at once is unchanged. Threaded
> through `PlotGridWidget` (constructor + `set_scroll_bindings()`, applied to every
> plot including ones added later) and `MainWindow` (constructor arg, and
> `apply_settings()`) the same way `downsample_pixel_ratio` already was.
> `SettingsDialog` gets one `QComboBox` per modifier (each offering all three
> actions); picking an action already assigned to another modifier's combo swaps the
> two combos rather than leaving two modifiers bound to the same action. Tests:
> `test_normalize_scroll_bindings_accepts_a_valid_permutation`,
> `test_normalize_scroll_bindings_falls_back_to_default_on_anything_invalid`,
> `test_load_settings_falls_back_when_scroll_bindings_is_corrupt`
> (`tests/parquet_analyzer/test_io.py`);
> `test_custom_scroll_bindings_swap_which_modifier_zooms_which_axis`,
> `test_set_scroll_bindings_updates_behavior_live`
> (`tests/parquet_analyzer/test_plot_widget.py`);
> `test_settings_dialog_scroll_binding_combo_swaps_the_conflicting_modifier`, and an
> extended `test_apply_settings_propagates_to_plots_and_actions`
> (`tests/parquet_analyzer/test_settings_dialog.py`).

#### 4.1 Dynamic Time-Axis Formatting

- The X (time) axis tick labels automatically change granularity with zoom level:
  date/month units when zoomed out, switching automatically down to
  hour:minute:second.millisecond when zoomed into a few seconds or minutes. Example:
  month labels like "Feb"/"Mar" over a 4-year span, sub-second labels like "50.250"
  over a 3-second span.
- Implemented using pyqtgraph's standard `DateAxisItem` as the bottom axis of
  `TimePlotWidget`/`NavigatorWidget`
  (`pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})`). Assumes the X-axis data
  is epoch seconds as a `float` (already converted by `MainWindow._to_numeric`).
- A frequency analysis (FFT) plot's X axis is frequency in Hz, not time, so it
  doesn't use `DateAxisItem` (`TimePlotWidget(..., x_axis_datetime=False)`,
  `plot.setLabel("bottom", "Frequency", units="Hz")`). Toggled via
  `PlotGridWidget.add_plot(x_axis_datetime=...)`. Since the X axis means something
  different (time vs. frequency), an `x_axis_datetime=False` plot is never X-linked
  to a time-domain plot (checked inside `PlotGridWidget.add_plot`).

#### 4.2 Choosing Which Column Is the Time (X) Axis

- Which column is used as the time axis defaults to the **first column** of the
  loaded Parquet file. It used to be a heuristic that preferentially searched for a
  column named `time`/`timestamp`/`datetime`, but there was no way for the user to
  override that choice explicitly, so it was effectively fixed regardless of what it
  picked. Changed to "default to the first column, freely changeable from the
  toolbar" (the heuristic was removed) in response to feedback that "the first
  column being time by default is fine, but it should be freely changeable."
- The toolbar's "Time axis:" combo box (`MainWindow.time_axis_combo`) lists every
  column of the loaded file; changing the selection calls
  `MainWindow.set_time_column(name)`.
- `MainWindow._file_columns` keeps holding every column of the loaded file (already
  converted to numeric); `_raw_variables` is rebuilt each time as `_file_columns`
  minus whichever column is currently the time axis. So switching the time axis
  doesn't discard the old time column — it reappears in the variable panel as an
  ordinary variable (a cached derived-variable value is carried over unchanged, since
  its defining formula stays in `_derived`).
- On switching, `PlotGridWidget.set_time_axis_data` repoints every time-domain
  (`x_axis_datetime=True`) plot's series' X arrays at the new time-axis data, and
  refits the view via `go_home()` (a different column can have a completely
  different scale/range, so preserving the old zoom range wouldn't make sense).
  Frequency-analysis plots (whose X axis is Hz) are unaffected by this.
- The view (`view.py`) schema doesn't save which column was chosen as the time axis
  (it's always the file's first column by default on load). If per-view memory of
  this is ever needed, add a `time_column` field to `View`.

### 5. Multi-Plot / Overlay Operations (11.7)

- Dragging a variable from `VariablePanel` onto a target plot adds it there as a
  series (overlay).
- Plot right-click menu:
  - "Move up" / "Move down" (reorders the vertical stack, added 2026-09-17;
    `PlotGridWidget.move_plot_up`/`move_plot_down`. Does nothing at a boundary —
    "move up" on the topmost pane, "move down" on the bottommost).
  - "Remove from this plot" (`PlotGridWidget.remove_plot` — removes the whole plot
    pane, not a single series; if multiple series were overlaid there, they're all
    removed together. There is no "-" button on the toolbar; this right-click menu
    item is the only way to remove a plot pane).
  - "Split into a new plot" (moves every series except the first one to a new plot
    pane).
- The toolbar's "+" button appends an empty plot pane at the end.
- **Rebuilding the X-axis link after a reorder/removal**: a new time-domain plot is
  always X-linked to "the currently topmost time-domain plot" (a star topology).
  When move-up/move-down changes plot order, `plots[0]` (the link target) can become
  a different widget, so `PlotGridWidget._relink_anchor()` re-establishes every
  time-domain plot's link around the current `plots[0]` every time (previously this
  was only handled on removal, via a `was_anchor` check; extracted into shared logic
  called from both removal and reordering once reordering was added).
- **The navigator's overview waveform tracks `plots[0]`'s first series**
  (`MainWindow._refresh_navigator_overview()`, added 2026-09-17). It used to
  unconditionally show `next(iter(self._raw_variables.values()))` — whichever
  variable happened to be first in file column order — regardless of what the user
  was actually looking at. Falls back to that old behavior only when
  `plots[0].series_names()` is empty (nothing has been overlaid yet). Called from:
  `load_parquet` / `set_time_column` / a variable drag-and-drop (only when the target
  is `plots[0]`) / `load_view` (once, unconditionally, in a `finally` block, whether
  it fully or only partially succeeded) / the `PlotGridWidget.layoutChanged` signal
  (fired on plot removal/reordering).
  - **A real bug caught while implementing this**: the value of `plots[0]`'s first
    series is read via `plot.series_data(name)` (the array the plot itself holds),
    not via `MainWindow.variable_values(name)` (name resolution against
    `_raw_variables`/`_derived` — this method was named `_variable_array` before
    13.5.1 Phase C made it public). Reason: if the user overlays a column that is
    *not* currently the time axis, then switches the time axis *to* that same
    column, its name drops out of `_raw_variables` (it's now the time axis itself).
    The already-plotted series remains valid and keeps rendering, but resolving it
    by name via `variable_values` would raise `ExpressionError: unknown variable`
    (caught via the situation covered by
    `tests/parquet_analyzer/test_load_robustness.py`'s
    `test_set_time_column_refits_an_already_plotted_series_to_the_new_x_data`).

### 6. Expression Input (11.4)

- `ExpressionBar` accepts a Python-like expression typed directly as text (e.g.
  `A + B`, `abs(A) - sqrt(B)`, `rolling_mean(A, 20) ** 2`).
- Double-clicking a variable in `VariablePanel` inserts its name at the cursor
  position (input assistance).
- Evaluation validates the AST via `ast.parse` under a whitelist of allowed
  operators/functions/known variable names (never raw `eval`). The goal isn't
  defending against malicious input — it's turning a typo into a clear error instead
  of a crash or unintended side effect.
- **The input string is normalized with `unicodedata.normalize("NFKC", ...)` before
  parsing** (added 2026-09-17). Real bug this fixed: Japanese IME full-width input
  mode turns `+` into `＋` (U+FF0B), and `ast.parse` rejects that outright — the raw
  Python `invalid character '＋' (U+FF0B)` syntax error was being shown to the user
  as-is, via `ExpressionError`. NFKC normalization maps full-width Latin
  letters/digits/symbols (`＋－＊／（）`, etc.) and the full-width space (U+3000) back
  to their ASCII equivalents, so an expression typed via IME without the user
  noticing the conversion still parses.
  - Operators: `+ - * / ** % //` (exponent, modulo, and floor division are all
    supported; unary `+ -` too).
  - Functions (all elementwise; registered in `_ALLOWED_FUNCS` as
    `{name: (expected arg count, implementation)}`):
    | Category | Functions | Args |
    |---|---|---|
    | Basic | `abs`, `sqrt` | 1 |
    | Analysis | `delta` (first-order difference), `rolling_mean` (moving average, 2nd arg is window width) | 1 / 2 |
    | Trigonometric | `sin`, `cos`, `tan` | 1 |
    | Exponent/log | `exp`, `log` (natural log), `log10` | 1 |
    | Rounding | `sign`, `floor`, `ceil`, `round` | 1 |
    | Comparison/range | `min`, `max` (elementwise comparison, unlike Python's built-in reduction min/max), `clip` (value, lower, upper) | 2 / 2 / 3 |
  - Note that `min`/`max`/`clip` are **elementwise** operations (e.g. `max(a, 0)`
    clamps every sample of `a` to be at least 0 — it does not reduce to a single
    scalar; a derived variable must stay the same length as the time axis to be
    plottable).
  - `rolling_mean(a, window)` is a moving average implemented via convolution
    (`np.convolve`, `mode="same"`). Near the edges it averages over only the actual
    overlap (no NaN padding), so the whole series stays plottable (`core/analysis.py`).
- **Argument count is validated too**. `abs`/`sqrt` are numpy ufuncs whose 2nd
  positional argument is interpreted as `out=` (a write-destination buffer); without
  arity checking, letting an expression like `sqrt(a, b)` through would silently
  overwrite `b`'s real data in place (a real bug that occurred, found and fixed in
  review; the same check applies to every newly-added function too).
- If a column name isn't a valid Python identifier (contains spaces/symbols, etc.),
  it can't be referenced as-is in an expression (it can't be parsed as `ast.Name`).
  There is currently no column-name aliasing feature
  ([§13.4](#134-known-limitations-of-expressions)).
- **Defining a derived variable whose name collides with an existing column is an
  error** (`MainWindow._on_add_derived_variable`). It used to silently overwrite
  (found and fixed in review). Redefining an existing derived variable's formula
  under the same name is allowed (treated as an intentional redefinition).
- A generated derived variable appears in `VariablePanel` and can be plotted/saved
  just like any other variable (see `specification.md` §5.5).

### 7. Data Source Selection and Folder History

- Opens a folder-picker dialog from the menu/toolbar's "Open" to choose a Parquet
  file.
- The dialog's initial folder is **the last one browsed** (`last_data_folder`).
- The most recently browsed folders are kept as an MRU (Most Recently Used) list of
  **up to 5** (`recent_data_folders`), shown as quick-pick candidates in the dialog.
  - Each newly browsed folder is added to the front, duplicates removed, and entries
    beyond 5 discarded oldest-first.
  - These 5 can be switched to instantly by clicking directly in a dropdown etc.
- Saved to `cfg/parquet_analyzer/settings.json`
  ([§9](#9-settings-file-cfgparquet_analyzersettingsjson)).

### 8. View JSON Schema

Example of the format saved to `data/parquet_analyzer/<view_name>.json`.
(`source.parquet_path` here points at the actual test data
`data/raw/sensor_log_4y.parquet` [a 4-year span, a 10ms-interval 1-minute burst every
16 hours, ~13.15 million rows]):

```json
{
  "schema_version": "1",
  "view_name": "sensor_overview",
  "source": {
    "parquet_path": "../raw/sensor_log_4y.parquet",
    "path_type": "relative"
  },
  "derived_variables": [
    { "name": "rpm_delta", "expression": "delta(rpm)" },
    { "name": "temp_pressure_ratio", "expression": "temperature / pressure" }
  ],
  "plots": [
    {
      "plot_id": "p1",
      "series": [
        { "variable": "rpm", "color": "#1f77b4" },
        { "variable": "rpm_delta", "color": "#ff7f0e" }
      ],
      "y_axis_range": [null, null]
    },
    {
      "plot_id": "p2",
      "series": [
        { "variable": "temperature", "color": "#2ca02c" },
        { "variable": "vibration", "color": "#d62728" }
      ],
      "y_axis_range": [null, null]
    }
  ],
  "x_axis_range": [0, 6000],
  "downsample": { "enabled": true }
}
```

- `derived_variables` saves the formula itself, not the computed result (see
  `specification.md` §5.5).
- When `source.path_type` is `relative`, it's resolved relative to `view_name.json`
  (e.g. `../raw/sensor_log_4y.parquet` as seen from
  `data/parquet_analyzer/sensor_overview.json`). However, `MainWindow.save_view_dialog`
  currently always saves `path_type="absolute"`, so the `relative` branch is
  implemented in `io/view.py`'s logic but has no path from the UI that actually
  exercises it
  ([§13](#13-open-items-and-remaining-tasks-as-of-this-writing)).
- `y_axis_range`/`x_axis_range`: `save_view_dialog` reads the Y/X range the plot was
  actually showing at save time and writes it, and `load_view` applies it on
  restore. These fields used to exist in the schema but were never actually written
  or read (saving a "view" didn't preserve the zoom/pan you were looking at — found
  and fixed in review).
- Since `view_name` is used directly as a filename, a name that doesn't survive a
  round-trip through `Path(view_name).name` (contains a `/` separator, is `..`, is
  empty, etc.) is rejected by `io/view.view_path()` raising `ValueError`. It used to
  pass through unchecked, letting a view name starting with `/` write outside the
  configured save directory entirely (found and fixed in review).
- **If a file other than the one `source.parquet_path` points at is already open,
  `load_view` doesn't reopen that file — it applies only the plot layout (overlaid
  variables, colors, axis ranges, derived-variable formulas) to the file that's
  already open** (changed 2026-09-17, `MainWindow.load_view`). Intended use case:
  reusing one layout across several files that share the same column names. If
  nothing is currently open, or the currently-open file already *is*
  `source.parquet_path` (compared as absolute paths), it loads via `load_parquet` as
  before. Even in the layout-only path, `_derived` (derived variables) is cleared
  first before reapplying the view's own `derived_variables` — otherwise a leftover
  derived variable from before switching files could sit in `_raw_variables` and
  cause the view's own same-named derived variable to be misjudged as "colliding
  with an existing column."
- **Applying a view's derived variables/series to whatever file is currently open
  used to be all-or-nothing: the first missing variable aborted every subsequent
  derived variable, series, and plot too** (found and fixed 2026-09-19,
  `MainWindow.load_view`). E.g. a view with plot 1 = `x`, plot 2 = `y`, and a derived
  variable `z = x + 1`, reused against a file that has `y` but not `x`, used to show
  *nothing at all* — the `ExpressionError` from evaluating `z` (its source `x` is
  missing) propagated out of the whole rebuild before plot 2's `y` series, which
  would have applied fine, was ever reached. This directly undercut the layout-reuse
  feature's own stated purpose (reusing one layout "across several files that share
  the same column names" only works if *partial* name overlap is tolerated — two
  files rarely share every single column). Fixed by wrapping each derived-variable
  and each series application in its own `try`/`except (ExpressionError, KeyError)`
  and collecting what got skipped into one consolidated warning at the end, instead
  of one `try` around the entire rebuild; a plot left with zero series because all of
  its series were skipped is removed rather than left as an empty pane (except
  `plots[0]`, which the grid always keeps at least one of). Verified: a derived
  variable's expression is still correctly recomputed from the *newly opened* file's
  own data when its source column does exist there, not left stale from the file the
  view was originally saved against. Regression tests:
  `test_load_view_applies_matching_variables_even_when_others_are_missing`,
  `test_load_view_recomputes_derived_variables_from_the_newly_opened_file`
  (`tests/parquet_analyzer/test_load_robustness.py`).
- **`load_view` used to call `load_parquet` (a full re-read from disk) whenever the
  currently open file already *was* the view's own recorded source, not just when
  nothing was open yet** (found and fixed 2026-09-19, `MainWindow.load_view`). Pointed
  out directly by the user: there's no reason to reopen a file that's already loaded
  just to apply a view saved against it — the view is about to fully replace the plot
  layout and derived variables regardless, so nothing about the already-open file
  needs to change. The unconditional reopen was also pure waste under 13.5.1's
  lazy/windowed columns specifically: it discarded whatever `LazyColumn`s had already
  materialized and re-read the whole file from scratch for no behavioral difference.
  Fixed by folding the "same file already open" case into the existing
  "different file already open" layout-only path (previously only that path skipped
  `load_parquet`) — `load_parquet` is now called only when `self._data_source is
  None`. Regression test: `test_load_view_does_not_reopen_a_file_already_open`
  (asserts `load_parquet` is never called via `monkeypatch`, and that `_data_source`
  keeps its prior identity rather than being replaced by a fresh
  `ParquetDataSource`).
- **The currently open file's name wasn't reliably visible** (added 2026-09-19, also
  user-requested). `MainWindow._set_title` already put it in the window title, but a
  title bar is easy to miss (maximized windows, some window managers/remote
  desktops), so it's now also mirrored into a status-bar label
  (`MainWindow._file_label`, left-aligned via `addWidget` rather than
  `addPermanentWidget` — the latter is reserved for the busy/precision indicators on
  the right) showing `"ファイル: <name>"`, with the full resolved path as its tooltip.
  Cleared (empty string) before any file is open. Regression test:
  `test_opening_a_file_shows_its_name_in_the_title_and_status_bar`.

### 9. Settings File (`cfg/parquet_analyzer/settings.json`)

```json
{
  "schema_version": "1",
  "downsample_pixel_ratio": 2,
  "auto_redraw": true,
  "eager_load_limit_mb": 512,
  "scroll_bindings": {"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"},
  "shortcuts": {
    "home": "H",
    "redraw": "R",
    "open_parquet": "",
    "add_plot": "",
    "fft": "",
    "stats": "",
    "save_view": "",
    "load_view": "",
    "toggle_downsample": ""
  },
  "window_geometry": null,
  "window_state": null,
  "last_data_folder": null,
  "recent_data_folders": [],
  "recent_files": [],
  "recent_views": [],
  "default_overlay": false
}
```

- `eager_load_limit_mb`: see [§13.5.1](#1351-memory-blowup-on-large-4gb-files-investigated-2026-09-17-phased-fix-implemented-2026-09-19)
  Phase E.
- `scroll_bindings`: see [§4](#4-controls-finalized-113-v2)'s v3 history note.
- `window_geometry` / `window_state`: see [§9.3](#93-layout-persistence).
- `auto_redraw`: when `false`, a downsampling recompute is not automatically
  triggered just because the visible range changed via pan/zoom (only manual redraw
  via the R button/shortcut runs). An escape hatch for when auto-recompute becomes a
  burden from frequent pan/zoom over large data. Newly-added data's initial display
  always happens regardless of this setting (`ui/plot_widget.py` `add_series`, see
  [§2](#2-module-structure)).
- `shortcuts`: action id -> `QKeySequence` string (empty string = unbound). Edited
  from the [§9.1](#91-settings-menu) settings dialog. On save, any action id that
  didn't exist when the file was last written is backfilled with its default
  (`io/settings.py`'s `load_settings`).
- `last_data_folder` / `recent_data_folders`: see
  [§7](#7-data-source-selection-and-folder-history). `recent_data_folders` is an MRU
  list capped at 5 entries; `last_data_folder` is kept equal to
  `recent_data_folders[0]` (redundant with just reading the first element, but kept
  as an explicit field to make the intent clear to a reader).

#### 9.1 Settings Menu

- Opens `SettingsDialog` (`ui/settings_dialog.py`) from the toolbar's "Settings"
  button.
- Editable items: auto-redraw (`auto_redraw`), downsampling density
  (`downsample_pixel_ratio`), the large-column threshold (`eager_load_limit_mb`),
  scroll-wheel modifier assignment (`scroll_bindings`), each action's shortcut
  (`QKeySequenceEdit`), and UI language (`language`, added 2026-09-19,
  user-requested — see below).
- Clicking OK has `MainWindow.apply_settings()` push the new `Settings` into
  `PlotGridWidget` and every `QAction`'s shortcut, then save to
  `cfg/parquet_analyzer/settings.json`. Canceling changes nothing (the dialog edits a
  copy, applied only on confirmation).
- **UI language** (`Settings.language`, `"en"` or `"ja"`, default `"en"` for a fresh
  install): `ui/i18n.py` holds every UI string as a `{key: {"en": ..., "ja": ...}}`
  table plus `tr(key, **kwargs)` (str.format-style placeholders) and
  `set_language()`/`get_language()` module-level state — a small self-contained
  dictionary lookup, not Qt's `QTranslator`/`.ts`/`.qm` toolchain (this app's UI text
  volume didn't justify pulling in `pyside6-lupdate`/`lrelease` as a build
  dependency). `MainWindow.__init__` calls `set_language(self.settings.language)`
  right after loading settings, before building any widget that calls `tr()`.
  Changing the language in the settings dialog only takes effect after restarting
  the app (`settings.language_restart_note`'s tooltip on the combo says so) —
  already-constructed widgets' text isn't retranslated live, deliberately, to avoid
  the complexity of re-running every `_build_*` method or wiring `Qt.LanguageChange`
  events through every widget for a setting that's rarely toggled. The
  presentation-only label dictionaries formerly in `io/settings.py`
  (`SHORTCUT_LABELS`, `SCROLL_ACTION_LABELS`, `SCROLL_MODIFIER_LABELS`) moved into
  `ui/i18n.py` as translated keys (`shortcut.*`, `scroll_action.*`,
  `scroll_modifier.*`) — `io/settings.py` keeps only the ids
  (`DEFAULT_SHORTCUTS`/`SCROLL_ACTIONS`/`SCROLL_MODIFIERS`), consistent with io/ not
  holding UI text. `core/expression.py`'s `ExpressionError` messages are intentionally
  left in English regardless of `language` — they're internal/diagnostic text (syntax
  errors, disallowed functions), not part of the translated UI string table.

#### 9.2 In-Progress Indicator

- `TimePlotWidget` emits `downsampleStarted`/`downsampleFinished` each time it
  starts/finishes a downsampling-recompute worker. `PlotGridWidget` aggregates the
  count across every plot pane, emitting `busyChanged(True)` when the in-flight job
  count goes 0->1, and `busyChanged(False)` on 1->0.
- `MainWindow` has an indeterminate (marquee) `QProgressBar` in the status bar, shown
  or hidden in response to `busyChanged`. Since an individual LTTB computation
  doesn't report percentage progress, this only ever shows "something is running,"
  not a percentage — visual feedback for the non-blocking asynchronous processing of
  [§10](#10-asynchronous-downsample-recompute-finalized).

#### 9.3 Layout Persistence

- `QMainWindow.saveGeometry()` / `saveState()`'s returned `QByteArray` is base64-encoded
  and stored as `window_geometry` / `window_state` (JSON can't hold binary directly).
  `saveState()` covers dock/toolbar arrangement and size; `saveGeometry()` covers the
  window's own position/size.
- For `saveState()`/`restoreState()` to work correctly, every target `QDockWidget`/
  `QToolBar` needs a unique `objectName` set (a Qt requirement). Set explicitly on
  `MainWindow._variable_dock` (`"variable_dock"`) and the main toolbar
  (`"main_toolbar"`).
- The variable panel's default position is the **left side** of the window
  (`Qt.DockWidgetArea.LeftDockWidgetArea`).
- Startup sequence: right after `_build_layout()`/`_build_actions()` build the default
  layout, the **pre-restore state** is captured as `self._default_geometry`/
  `self._default_state`, and only then does `_restore_layout()` restore any saved
  settings (if `window_geometry`/`window_state` is `None`, it does nothing, i.e.
  stays at the default).
- The toolbar's "Reset layout" (`MainWindow.reset_layout()`) reverts to the captured
  `_default_geometry`/`_default_state`, and saves `window_geometry`/`window_state`
  back as `None`.
- Saved on `closeEvent` (`MainWindow._save_layout()`) — every time the window is
  closed, the current layout is written back to `settings.json`.

### 10. Asynchronous Downsample Recompute (Finalized)

- Running LTTB recompute on the main thread on every zoom/pan risks blocking UI
  operations for large data, failing `specification.md` §9's non-functional
  requirement (UI operations reflected within a few seconds). So downsampling
  recompute is finalized to run asynchronously on a `QThreadPool` (a `QRunnable`,
  `BackgroundWorker` — see [§10.2](#102-consolidating-into-backgroundworker-and-separating-thread-pools-2026-09-18)).
- A new job is dispatched every time the visible range changes, but each job carries
  a **per-series generation counter**, and a stale generation's result is discarded
  if it arrives late (preventing a plot from being overwritten by an outdated
  recompute during continuous zooming). The generation counter is kept **per
  series**, as `TimePlotWidget._series[name]["generation"]` — there was a period
  where it was a single counter shared across the whole plot, which caused a real
  bug: with 2+ overlaid series, every redraw pass advanced the shared counter once
  per series, so every series except the last one processed always looked "stale"
  by the time its own result came back and got discarded (overlay display — a core
  feature — was effectively broken; found and fixed in review).
- A worker's completion is delivered to the UI thread via a Qt signal (thread-safe);
  `TimePlotWidget` receives it and updates the drawing.
- `PlotGridWidget.remove_plot` doesn't call `deleteLater()` on a plot pane while its
  `pending_downsample_count() > 0` (a job is still running on a worker thread) — it
  waits for the `downsampleFinished` signal to confirm the job count has reached 0
  before deleting. Deleting immediately via `deleteLater()` risks a crash if a
  worker's queued signal arrives at the (already-deleted) widget (the same kind of
  fix as `DownsampleWorker`'s own lifetime handling; the job-count tracking of
  [§9.2](#92-in-progress-indicator) also depends on this mechanism).
- Reasoning for deciding to go asynchronous from the start, rather than "implement
  naively on the main thread first, thread it later if it feels slow": LTTB's cost
  scales with point count, so running it on the main thread over large data (~13.15
  million rows in the test data) carries a high risk of the UI freezing on every zoom
  operation, and the cost of building it asynchronous from the start is lower than
  the cost of retrofitting asynchrony later.

#### 10.1 LTTB's NaN Handling and Performance

- `core/downsample.py`'s `lttb()` had a bug where a single NaN anywhere in a bucket
  made a naive `np.mean`'s area calculation for that bucket's candidate points NaN
  across the board, degrading toward always picking the first point instead of the
  real peak/trough (a real-world problem, since sensor logs commonly have missing
  values; found and fixed in review).
- The fix used `np.nanmean` + `np.nan_to_num(..., nan=-np.inf)` to avoid NaN, but
  `np.nanmean` is roughly 5x slower than `np.mean`, and calling it **once per
  bucket** (plot width × `downsample_pixel_ratio`, typically 2000+) made converting
  13M rows to 2400 points regress from ~37.5ms to ~104ms (found via real-hardware
  feedback that redrawing had gotten slower).
- Final fix: check `np.isfinite(x).all() and np.isfinite(y).all()` once up front
  (~2ms for 13M elements), and use `np.nanmean` only when NaN/Inf is actually
  present (otherwise stick with the original `np.mean`). Verified that the fast path
  on NaN-free data produces bit-identical output to the original implementation.
  Since NaN is rare in actual usage, most redraws get NaN-safety at no performance
  cost.

#### 10.2 Consolidating into BackgroundWorker and Separating Thread Pools (2026-09-18)

While adding FFT (§13.5.1) and statistics (§14), a code review pointed out that the
three `QRunnable` subclasses `DownsampleWorker`, `FFTWorker`, and `StatsWorker` each
re-implemented almost the identical safety pattern of "`setAutoDelete(False)` + the
caller holds a strong reference until `finished` has been handled" (this pattern
itself is mandatory per §10's reasoning — omitting it risks a crash).

- Consolidated into `core/background_worker.py`'s `BackgroundWorker`. Its
  constructor takes any `Callable[[], object]`; `run()` calls it on the worker
  thread and returns the result as-is via `signals.finished` (`Signal(object)`).
  Downsampling's generation number (used for stale-result detection) is no longer
  `BackgroundWorker`'s own responsibility — the caller (`plot_widget.py`) now
  includes it in the closure's own return tuple and checks it on the receiving end
  (`DownsampleWorker`'s dedicated `generation` constructor argument was dropped in
  favor of this more general approach).
- `FFTWorker`/`StatsWorker` were deleted; the caller (`MainWindow`) now assembles a
  worker directly, e.g. `BackgroundWorker(lambda: fft(x, y))`.
- **Analysis jobs (FFT, statistics) and pan/zoom redraw jobs were split onto
  separate `QThreadPool`s** (`MainWindow._analysis_thread_pool`; `plot_widget.py`
  keeps using `QThreadPool.globalInstance()` as before). Since statistics dispatches
  one worker per selected variable all at once (§14), sharing
  `QThreadPool.globalInstance()` with these risked a multi-variable statistics
  computation over large data temporarily monopolizing the threads pan/zoom's
  downsampling recompute depends on for responsiveness — a code review finding —
  hence the split.

### 11. Logging Specification

- Uses the standard `logging` module.
- Output: `log/parquet_analyzer/parquet_analyzer_{YYYYMMDD}.log` (rotated daily).
- `INFO` under normal operation; stack traces logged at `ERROR` on exceptions.

#### 11.1 Debug-Mode Launch and the Operation Log

- Passing the `--debug` flag at launch (or setting the `PARQUET_ANALYZER_DEBUG=1`
  environment variable) enables debug mode. `tools/run_debug.sh` / `run_debug.bat`
  are dedicated launchers that pass this flag.
- In debug mode:
  - The main log's level becomes `DEBUG`.
  - The window title gets a `[DEBUG]` suffix (so it's obvious at a glance which mode
    is running).
  - An **operation log** — a trace of user actions such as opening files, overlaying
    variables, adding a derived variable, H/R, zoom/pan, adding/removing/separating
    plots, saving/loading views, changing settings — is written to
    `log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log` (also echoed to the
    console). Kept in a separate file from the main log so frequent operations don't
    bury the app's normal log.
- Implemented as `core/oplog.py`'s `log_op(action, **details)`, a thin wrapper —
  outside debug mode, `logger.isEnabledFor(DEBUG)` is `False`, making this
  effectively a no-op (no string-formatting cost is incurred either).

### 12. Error-Handling Policy

- A Parquet load failure, an invalid expression, or a view load failure are all
  reported to the user via a dialog, logged, and the app keeps running (never
  crashes).
- **Processing that must never block the app from launching in the first place is
  defended especially carefully** (both the settings file and the window layout are
  loaded unconditionally inside `MainWindow.__init__`, so an exception here would
  crash before a single window is shown — which looks like "the app won't start" to
  the user).
  - `io/settings.load_settings()`: falls back to defaults (`Settings()`) rather than
    raising, whether the JSON is corrupt or has the wrong types. `save_settings()`
    also writes atomically via a temp file + `os.replace`, so a crash mid-write never
    leaves behind a corrupted file.
  - `MainWindow._restore_layout()`: if `base64.b64decode()` on `window_geometry`/
    `window_state` fails (corrupt data, or incompatibility with an old format), the
    exception is swallowed and it falls back to the default layout. It also resets to
    the default position if the restored window position doesn't overlap any screen
    at all (`QGuiApplication.screens()`) — e.g. launching without the external
    monitor that was connected when it was saved (Qt's own `restoreGeometry()`
    already self-corrects extreme coordinates somewhat, but this is an extra
    safeguard).
  - Both exist to prevent "a new bug report about being unable to launch at all" —
    a lesson actually learned from a post-review can't-launch-on-first-run bug (a
    gap in `_restore_layout`'s error handling).
- `load_parquet` completes every step needed for loading (schema read, time-column
  conversion, non-empty-column check) on local variables before committing to
  `self.*`. This prevents a half-failed state where `self._data_source` etc. point
  at the new file while stale data from the old one keeps being shown. It returns
  success/failure as a `bool`, and `load_view` checks this before proceeding
  (rebuilding derived variables, restoring the plot layout).
- `load_view` further separates the error handling for loading the view itself (JSON
  + resolving the source path) from rebuilding the plot layout using the loaded
  data. If the latter (`ExpressionError`/`KeyError` — e.g. the view references a
  column that no longer exists, because the source Parquet was regenerated) fails,
  it doesn't invalidate the data load that already succeeded (a warning dialog is
  shown, but the data that was opened stays usable as-is).

### 13. Open Items and Remaining Tasks as of This Writing

Of the items found by an Opus/Fable code review (from a correctness/efficiency/
simplification/test-coverage angle), **all 14 correctness bugs have been fixed**.
The following remain unaddressed.

#### 13.1 Bug Under Investigation

- There was a report of being unable to launch via `tools/run.sh` / `run_debug.sh`.
  Two candidate causes were identified and fixed:
  (a) a crash when the settings file is corrupt, (b) the window position being
  restored off-screen (see [§12](#12-error-handling-policy)). The reporter's actual
  root cause on their own machine is unconfirmed, though — still waiting on their
  actual terminal output/error message.

#### 13.2 Efficiency (Unaddressed)

- ~~Frequency analysis (FFT, `MainWindow._on_fft_requested`) ran synchronously on the
  UI thread~~ -> fixed (2026-09-17, §13.5.1 Step 0). Changed to run in the background
  via `core/background_worker.py`'s `BackgroundWorker` (`QRunnable`,
  `setAutoDelete(False)` + caller-held strong reference pattern). Also changed its
  scope to "the selected variable's visible time range" at the same time (see
  [specification.md §5.6](../specification.md#56-frequency-analysis) — not
  targeting the whole file also sidesteps the memory problem).
- ~~`load_parquet` (`MainWindow`) synchronously bulk-loads every column of the file via
  `ParquetDataSource.read_columns()`~~ -> fixed (2026-09-19, §13.5.1 Phases A-D). Only the time
  column is still read eagerly at open; every other column is a `LazyColumn` (materializes on
  first use) or, over `Settings.eager_load_limit_mb`, a `WindowedColumn` (never materializes fully
  — only the currently-visible row range is ever fetched).
- ~~`TimePlotWidget._on_range_changed` queues a downsampling recompute even for a
  Y-axis-only change (including amplitude-direction zoom), with no debounce~~ ->
  fixed (2026-09-17, §13.5.1 Step 0). `TimePlotWidget._on_range_changed` now records
  the X range and skips recompute if it's unchanged from last time (i.e. only Y
  changed). Even when the X range does change, it doesn't redraw immediately —
  restarting a 120ms single-shot `QTimer` (`_redraw_debounce`) coalesces a burst of
  consecutive `sigRangeChanged` events (e.g. a drag-pan) into one. The
  navigator-sync `rangeChanged` signal is left undebounced, firing immediately (so
  the navigator's selection doesn't lag behind the view's movement).

#### 13.3 Unused/Unwired Code (Needs a Decision: Implement or Delete)

- `core/variable.py`'s `Variable` dataclass is never instantiated anywhere (only
  imported).
- `PlotGridWidget.add_series_to_plot` is never called from anywhere.
- ~~`TimePlotWidget.set_downsample_enabled`/`PlotGridWidget.set_downsample_enabled`
  are never called from anywhere in the UI — there's no UI operation to disable
  downsampling itself. `View.downsample_enabled` is also serialized to JSON but never
  written on save nor applied on load (the same "in the schema but never wired up"
  state as `y_axis_range`/`x_axis_range` in [§8](#8-view-json-schema)).~~ -> **decided:
  implement, not delete** (2026-09-19, [§13.5.1 Phase E](#1351-memory-blowup-on-large-4gb-files-investigated-2026-09-17-phased-fix-implemented-2026-09-19)).
  The pyramid (13.5.1 Phase D) didn't make this incoherent after all — `_redraw_series`
  already only uses the pyramid when downsampling is enabled, so disabling it still has
  a well-defined meaning even for a `WindowedColumn` series ("always fetch real
  per-sample data, never simplify"). Now a checkable toolbar action
  (`toggle_downsample`), applied via `MainWindow._apply_downsample_enabled`, and
  correctly round-tripped through `save_view_dialog`/`load_view` alongside
  `y_axis_range`/`x_axis_range`.
- `Settings.recent_files`/`recent_views` fields are never read or written (only
  `recent_data_folders` is actually used).
- `SourceDef.path_type`'s `"relative"` branch is implemented on the `io/view.py`
  side, but `save_view_dialog` always saves `"absolute"`, so it's never actually
  reached.

#### 13.4 Known Limitations of Expressions

- ~~`**` (exponent), `%` (modulo), `//` (floor division) were unsupported~~ -> fixed
  (2026-09-20, [§6](#6-expression-input-114)). `sin`/`cos`/`tan`/`exp`/`log`/`log10`/
  `sign`/`floor`/`ceil`/`round`/`min`/`max`/`clip`/`rolling_mean` were added at the
  same time.
- If a column name isn't a valid Python identifier (contains spaces/symbols, etc.),
  it can't be referenced in an expression.
- `round` can't take a decimal-place count (`round(a)` only, fixed at one argument).
  A workaround when a specific number of decimal places is needed:
  `round(a * 100) / 100`.
- `rolling_mean` only accepts a fixed sample-count window; it can't be specified in
  time terms (e.g. "the past 1 second") — sample count only.

#### 13.5 Other

- **Frequency analysis (FFT) is temporarily hidden from the GUI** (2026-09-19,
  user-requested — "haven't debugged it yet"). `io/settings.py`'s `FFT_ENABLED = False`
  gates both `MainWindow._build_actions`'s toolbar button and `SettingsDialog`'s
  "fft" shortcut row; nothing else was touched — `MainWindow._on_fft_requested`,
  `core/analysis.py`'s `fft()`, and `TimePlotWidget`/`PlotGridWidget`'s
  `x_axis_datetime=False` FFT-plot support are all untouched and still exercised
  directly by `tests/parquet_analyzer/test_fft.py` (which calls
  `_on_fft_requested()` rather than going through the toolbar) and
  `test_windowed_loading.py`. Flip `FFT_ENABLED` back to `True` to restore both UI
  entry points once debugging is done; `DEFAULT_SHORTCUTS`/`SHORTCUT_LABELS` still
  carry an `"fft"` entry so no settings-schema migration is needed either way.
- Detailed design of row-range (row-group)-level lazy loading using `polars`'
  `scan_parquet` (to be considered together with the `load_parquet` synchronous-load
  problem of [§13.2](#132-efficiency-unaddressed)).

##### 13.5.1 Memory Blowup on Large (~4GB) Files (investigated 2026-09-17, phased fix implemented 2026-09-19)

Investigated a report that opening a ~4GB Parquet file
(`data/raw/sensor_log_4y_4gb.parquet`, 91,980,000 rows x 6 columns) causes memory
usage to spike and the app to become unusable.
The cause is exactly the `load_parquet` synchronous bulk-load already flagged in
[§13.2](#132-efficiency-unaddressed): even though `ParquetDataSource` already has a
lazy-loading API (`row_count()`/`read_columns(row_start, row_end)`),
`MainWindow.load_parquet` calls `read_columns(columns)` with no range at all, loading
every column and every row at once (`load_parquet` in
`src/parquet_analyzer/ui/main_window.py`).

The first pass at investigating this wrote that "polars' `collect()` ->
`to_numpy()` -> `_to_numeric`'s multiple copies are what strain CPU/memory," but
after having Opus and Fable independently design and measure this, **that
understanding turned out to be wrong** (both reached the same conclusion).
`pl.Series.to_numpy()` is **zero-copy** for float64 columns (a view over an Arrow
buffer — confirmed via `flags.writeable == False`, `base is not None`), and reading
itself is fast (`collect().to_numpy()` over 92M rows of one column measured at 0.25
seconds). The real cost is **not copy count, but the fact that 6 columns x
91,980,000 rows' worth of arrays stay resident in memory for the rest of the
session** after loading (4.42GB for 6 float64 columns, plus +736MB from the one real
copy `_to_numeric` makes converting the int64/datetime time column, for a peak over
5GB total). Also, `slice()` pushdown genuinely works — reading 2,000,000 rows x 2
columns from the middle of the 4GB file measured at just 14ms. In other words, the
most important empirical finding from this re-investigation is that a "read only the
visible range, from disk, each time" policy is entirely viable on latency grounds.

##### Design Adopted (Opus's proposal as the base, incorporating Fable's points)

The previously-written Stage A/B/C split is replaced, for these reasons: Stage A's
"read only the time column, fully, up front" portion is unnecessary — it can be
replaced by the Parquet footer's row-group statistics (min/max, obtainable from just
reading the footer, measured at 6ms), which eliminates that 736MB read entirely.
Stage B's "the real fix," reading the visible range, was confirmed viable by
measurement, but neither A, B, nor C had a plan for the case where zooming out
(visible range = everything) degrades read cost back to reading every column (a
background-built coarse min/max pyramid). Stage C's "needs a decision" items were
each judged by both models as having no reason to stay undecided, and both reached
concrete conclusions.

New step breakdown:

- **Step 0 (low risk, no data-model change; can be started alone, first)**
  - Debounce implementation for `TimePlotWidget._on_range_changed` (a known gap per
    [§13.2](#132-efficiency-unaddressed); skip recompute for a Y-axis-only change).
    The steps below assume "re-query the disk on every pan/zoom" as a premise, so
    this is a **prerequisite** for Step 1 onward, not something to defer.
  - Move FFT (`_on_fft_requested`) off the UI thread via `QRunnable` (also a known
    gap per [§13.2](#132-efficiency-unaddressed)).
  - Fix a bug in the `data/raw/sensor_log_4y_4gb.parquet` generation script (below).
- **Step 1 (extending `ParquetDataSource` only; unused by the UI, so it absolutely
  cannot break existing behavior)**: `row_groups()`/`column_stats()` (footer
  row-group statistics), `column_bounds()`, `row_range_for_x()` (binary-search the
  stats, snapped to block boundaries), block-wise reads + an LRU cache,
  `core/pyramid.py` (a background-built min/max pyramid at an 8192-row bucket size
  per column — about 270KB per column for the 4GB file).
- **Step 2 (introducing the `Column`/`SeriesSource` abstraction; swaps the wiring
  only, without changing the loading approach)**: add `MaterializedColumn`/
  `LazyColumn`/`DerivedColumn` and `SeriesSource` to `core/column.py`. Change
  `TimePlotWidget._series`'s storage format from `{"x": ndarray, "y": ndarray}` to
  `SeriesSource`; change `_raw_variables`/`_file_columns` to hold `Column`s.
  `add_series(name, x, y, color)` keeps its signature, wrapping the arrays in a
  `MaterializedColumn` so existing callers/tests need no changes (`series_data()`
  splits into `series_color()` and `series_source()`). `load_parquet` still loads
  every column eagerly at this stage, so **the memory problem isn't solved yet** —
  this is groundwork to make the later step safe.
- **Step 3 (this is what actually solves the memory problem: a dual path gated by a
  size threshold)**: add `Settings.eager_load_limit_mb` (default around 512MB); if
  `row_count * (column count - 1) * 8 <= threshold`, **keep using the current
  eager-load-everything path completely unchanged**. Only a file over the threshold
  takes the `LazyColumn`/`DerivedColumn` path (Step 1's block reads + pyramid, a
  `SeriesSampleWorker` sampling on a background thread, stale-result discarding via
  the per-series generation counter — reusing the safety measures already
  established for `DownsampleWorker` as-is). This "small files stay exactly as they
  are today" threshold branch is the single biggest lever for minimizing impact on
  the existing 82 tests (detail below). Derived-variable evaluation
  (`core/expression.py`) needs no change to `evaluate_expression` itself (it was
  already a pure `Mapping[str, ndarray] -> ndarray` function), but a
  "looks-at-neighbors" function like `delta`/`rolling_mean` produces inaccurate
  values at a block boundary, so a new `required_halo(expr)` (how many rows of
  margin on each side the expression needs) is introduced, reading that much extra
  margin around the visible range before evaluating, then trimming after. The
  navigator's overview display updates progressively: footer statistics (instant, no
  I/O, right after opening) -> once pyramid construction finishes, the actually
  plotted variable's min/max envelope.
- **Step 4 (polish)**: make the coarse (min/max envelope) vs. precise (LTTB) display
  switch on zoom-out visible somewhere like the status bar. Cancel in-flight pyramid
  construction workers on file switch. Add an `eager_load_limit_mb` entry to the
  settings dialog. [§13.3](#133-unusedunwired-code-needs-a-decision-implement-or-delete)'s
  `set_downsample_enabled` — since "disabling downsampling" stops being a coherent
  concept once a pyramid exists — can reasonably be judged as "delete rather than
  wire up" if this design is adopted.

##### Points Needing Individual Judgment/Care From Step 3 Onward

- **View save/load (`io/view.py`)**: both models concluded "no change needed." A
  `View` only ever holds variable names/formulas/colors/ranges — it never saved data
  arrays in the first place. The only affected code is `main_window.py`'s
  `save_view_dialog`/`load_view` call sites (just replacing `series_data()` with
  `series_color()`/`series_source()`).
- **FFT (`core/analysis.py`, `_on_fft_requested`)**: Opus's proposal disagrees with
  the very premise of "the whole dataset is needed" — this test data spans 4 years
  but is an intermittent burst every 16 hours, and `fft()`'s internal
  `dt = median(diff(x))` is meaningless across a 16-hour gap. The point that
  **a whole-file FFT produces a meaningless result even before the memory problem**
  is valid. A design of "run FFT against the currently visible range, and prompt via
  a dialog to narrow the range if the row count exceeds a threshold" fits the actual
  data better. However this is a **spec change** to FFT's target range in
  `specification.md` §5.6, needing a separate user decision from the memory fix
  itself (Fable's proposal instead supports "keep targeting the whole dataset per
  spec, but make it asynchronous" — the two proposals disagree here).
- **The monotonicity assumption**: an X-to-row-range conversion using row-group
  statistics, like `row_range_for_x()`, depends on the time-axis column being sorted
  (monotonically increasing). In fact, `_redraw_series`'s `np.searchsorted` **already**
  implicitly depends on this today — choosing a non-monotonic column as the time axis
  via `time_axis_combo` already produces a silent bug (a wrong crop) today. Under
  lazy loading this would worsen from "wrong crop" to "reads the wrong rows," so an
  `is_monotonic()` check should be added, and it should be written down which of (a)
  force a fallback to the eager-load path for a non-monotonic column, or (b) exclude
  it from `time_axis_combo`, is adopted, before implementing.
- **Impact on existing tests is smaller than originally assumed (79+)**: the only
  places that actually assert `_raw_variables`/`_time_values` directly as arrays are
  `tests/parquet_analyzer/test_ui_stress.py` (2 tests) and
  `tests/parquet_analyzer/test_load_robustness.py` (4 tests, including 1 via
  `series_data()`) — 6 tests, ~10 lines, total. `test_plot_widget.py`/
  `test_plot_grid.py`/`test_downsample.py`/`test_expression.py`/`test_analysis.py`
  etc. all pass arrays directly to `TimePlotWidget`/`PlotGridWidget`/core functions
  in their tests, bypassing `MainWindow.load_parquet` entirely, so they're expected
  to need no changes. With the threshold branch above in place, all of the existing
  fixtures (tens of thousands of rows) stay on the current eager-load path, so in
  practice it looks like it'll be enough to redirect just those 6 tests to a new
  accessor (e.g. `variable_values()`).

##### Byproduct: a Type Mismatch in the Test 4GB File's Time Column (found and fixed)

Opus's investigation found that the `data/raw/sensor_log_4y_4gb.parquet` generation
script wrote the `time` column as a plain `int64` (millisecond epoch), which didn't
match the real `sensor_log_4y.parquet`'s type (`timestamp[us]`). Since
`MainWindow._to_numeric` only converts a `datetime64` type to seconds, using it with
this mismatch as-is displayed the X axis as the raw millisecond value (around the
year 54600), which would have led to chasing an unrelated bug mistaken for the
memory problem. Fixed the generation script to use `timestamp[us]` and regenerated
the file (schema/size confirmed via `du -h` at ~4.0GB, row count unchanged).

##### 2026-09-19 Re-investigation (second independent Opus/Fable review)

Re-investigated with fresh reproduction data (`tests/parquet_analyzer/generate_test_parquet.py`,
committed to the repo — unlike the script that produced `sensor_log_4y_4gb.parquet`, which was
never checked in; `data/raw/sensor_log_4y_4gb.parquet` is not present in this checkout).
`data/raw/daily_cycle_4gb.parquet` (86,600,000 rows x 5 columns, 872 row groups, a daily 8h-on/
16h-off pattern) and `data/raw/daily_cycle_500mb.parquet` are now the standard fixtures for this
section; regenerate with the CLI in that script's docstring.

**Correction to the 2026-09-17 zero-copy claim above.** `pl.Series.to_numpy()` is zero-copy only
for a **single-chunk** Series (verified pointer-identical to the underlying Arrow buffer).
`scan_parquet(...).collect()` over a real multi-row-group file yields **one Arrow chunk per row
group** (`df[c].n_chunks()` measured at 872 for the 4GB repro file) — `to_numpy()` on a
multi-chunk Series must rechunk into a fresh contiguous buffer (`writeable=True`, `owndata=False`,
`base` is a newly-rechunked `Series`, not the original), which **is** a real second full copy. The
2026-09-17 analysis generalized from a case that likely collapsed to one chunk; on the actual
reproduction file `ParquetDataSource.read_columns()` (`core/data_source.py:33-39`) costs one full
extra copy of everything it reads, undercounted above by ~1x file size. `.rechunk()` cannot "fix"
this — rechunk *is* the copy.

**Measured ledger** (Windows, `peak_wset`; logical payload for the 4GB file is ~3.2 GiB across 5
columns):

| Step | RSS after | Peak |
|---|---|---|
| `MainWindow()` only | 0.11 GiB | 0.11 GiB |
| `ParquetDataSource(path)` (schema/footer only) | 0.05 GiB | 4 ms |
| `read_columns(all 5 columns)` | 6.76 GiB | 7.26 GiB |
| + `_to_numeric` on all 5 | 7.41 GiB | 8.05 GiB |
| full `load_parquet` (incl. navigator LTTB) | 7.48 GiB | 8.12 GiB |
| overlay 4 variables on plot 1 | 7.48 GiB | 8.77 GiB |
| **`go_home()` with 5 series** | 7.48 GiB | **12.64 GiB** (+3.9 GiB transient, 2.4s on the UI thread) |
| add one derived variable | 8.12 GiB | 12.64 GiB |
| open the *same* file 3x in a row (no plotting) | 8.18 GiB | 13.19 GiB |

So an ordinary session on this 3.7 GiB file already peaks at ~13 GiB here (~3.5x file size) —
same failure mode as the 32 GiB report, just a less unlucky allocator/usage pattern on this
machine. `del df; del data; gc.collect()` after a full load moved RSS by 0.000 GiB — freed
polars/Arrow arena memory is not returned to the OS, confirming the allocator-retention hypothesis
directly rather than as a plausible guess.

**A previously-uncounted, independent quick win**: `TimePlotWidget._finite_bounds`
(`ui/plot_widget.py:120-134`) does boolean-mask indexing (`a[np.isfinite(a)]`) per array, then
`np.concatenate`s all of them — a full copy per array plus one more for the concatenation.
`add_series` (`plot_widget.py:113-114`) pays this twice per overlay; `go_home()`
(`plot_widget.py:229-230`) pays it once per series for x *and* y — with 5 series sharing one 0.645
GiB x array, that's the measured +3.9 GiB / 2.4s UI-thread freeze above. Fixing this to a
streaming `nanmin`/`nanmax` reduction (no mask, no concatenate) is independent of everything else
in this section and worth doing regardless of which phase below gets picked up next.

**Correction to the `_to_numeric` copy-count framing**: reducing the time-column conversion chain
from 3 copies to 1 (`values.astype("datetime64[us]").astype(np.int64) / 1e6` →
`np.true_divide(values.view(np.int64), divisor, dtype=np.float64)`, `divisor` from
`np.datetime_data`) does **not** reduce steady-state resident memory — CPython frees the
intermediate arrays immediately regardless of chain length, so the persisted result is always one
new array either way. It only lowers **peak transient** allocation by ~1 array's worth (~0.645
GiB here). Still a free, zero-risk, bit-identical change (verified `np.array_equal` against the
current 3-copy result; use `true_divide`, not `multiply` by `1e-3` — that is *not* bit-identical,
max diff 2.4e-7) — just don't expect it to move the steady-state number that actually matters.

**Also newly noted**: `_to_numeric` (`main_window.py:303-308`) force-widens any non-float64 column
to float64, silently doubling memory for a file whose source columns are e.g. float32/int32. If
the 32 GiB report's file isn't all-float64, this compounds independently of everything else here —
worth asking the reporter for their file's exact schema before assuming the multiplier is fully
explained by the above.

**Other full-array materializers found, relevant to any design that still resolves them eagerly**:
`core/analysis.py`'s `basic_stats` double-copies (`finite = y[np.isfinite(y)]` then a `median`
sort); `_on_stats_requested` launches one `BackgroundWorker` per selected variable in parallel, so
selecting several variables multiplies the transient cost; `MainWindow._all_arrays()`
(`main_window.py:320-325`) re-evaluates *every* derived variable over the *full* file length on
every call; `navigator.set_overview_data` still runs a full-column `lttb` on the UI thread
(`navigator.py:34`) before the design below replaces it with footer stats; and
`_visible_row_range`/`_redraw_series`'s `np.searchsorted` already silently assumes the time column
is monotonic today (a non-monotonic time-axis choice already produces a wrong crop, independent of
this section).

**Feasibility numbers for "read only what's visible," re-confirmed on the actual repro file**:
footer + full schema open, 5.8ms; **all 872 row groups' statistics** (min/max/null_count, every
column), 7.1ms, no measurable memory; a 1,000,000-row x 1-column slice-pushdown read, 2.3ms; an
8192-row-bucket min/max pyramid for all 4 value columns, streaming, 1.74s total, peak <1 GiB,
**165 KiB stored per column**. A visible-window read is ~1000x cheaper than a full-column read —
reading fresh from disk on every pan/zoom is not merely acceptable, it's faster than what the app
does at open today.

###### Consolidated plan (Phase A-E, supersedes the Step 0-4 numbering above one-for-one except
where noted)

Both models independently converged on keeping the 2026-09-17 design as the backbone. Renamed to
phases so each stands alone and ends in a measurable RSS check against `daily_cycle_4gb.parquet`.

- **Phase A = Step 0, expanded** (allocation hygiene; no data-model change; ~0.5 day; risk very
  low; do this regardless of what's decided for B-E). `_to_numeric` single-copy conversion (above);
  `read_columns` reads and converts **one column at a time**, dropping the polars frame before the
  next (measured 8.05 → 4.89 GiB peak at open, same wall time); allocation-free `_finite_bounds`;
  `basic_stats` avoiding its double copy; drop the pre-conversion `data` dict reference before
  committing in `load_parquet`. Debounce on `_on_range_changed` and off-UI-thread FFT (the original
  Step 0 items) are already done in the current code — verified, no action needed.

  **✅ Implemented and verified 2026-09-19** (`core/data_source.py`, `ui/main_window.py`'s
  `_to_numeric`/`load_parquet`, `ui/plot_widget.py`'s `_finite_bounds`, `core/analysis.py`'s
  `basic_stats`). `_to_numeric` verified bit-identical to the old 3-copy result across s/ms/us/ns
  `datetime64` units (`np.array_equal`); `_finite_bounds` verified identical to the old
  filter+concatenate result across empty/all-NaN/mixed-Inf/multi-array cases. `uv run pytest`: 107
  passed, 1 skipped, unchanged. Re-measured the same ledger against `data/raw/daily_cycle_4gb.parquet`:

  | Step | Before (2026-09-19 opus measurement) | After (Phase A) |
  |---|---|---|
  | after `load_parquet` (open) | RSS 7.48 / peak 8.12 GiB | RSS 5.50 / **peak 5.58 GiB** |
  | after overlaying variables | RSS 7.48 / peak 8.77 GiB (5 series) | RSS 5.74 / **peak 5.74 GiB** (4 series) |
  | `go_home()` | peak **12.64 GiB** (+3.9 GiB transient, 2.4s on the UI thread) | peak **5.84 GiB** (no material spike, 0.92s) |
  | re-open the same file 3x (no plotting) | peak 13.19 GiB | peak 11.54 GiB |

  `go_home()`'s multi-gigabyte transient spike and UI freeze are gone (that was almost entirely
  `_finite_bounds`, as suspected). Open-time peak is down ~31%. The 3x-reopen case improves less
  (11.54 vs 13.19 GiB) since Phase A doesn't address the old file's arrays staying alive during
  `load_parquet`'s atomic-commit window — that's Phase C/D territory, not a Phase A goal.
- **Phase B = Step 1** (extend `ParquetDataSource` only; unused by the UI so it cannot break
  anything; ~1-1.5 days; risk near zero): `row_groups()`, `column_stats()`, `column_bounds()`,
  `row_range_for_x()` (binary search over row-group stats, gated behind `is_monotonic()`),
  block-cached `read_window()`, `core/pyramid.py`. Wire the two free wins that need no new
  abstraction: navigator overview and initial axis extents from footer stats instead of the
  full-column `lttb` at open.

  **✅ `ParquetDataSource`/`core/pyramid.py` implemented and verified 2026-09-19 — the navigator/UI
  wiring is deliberately deferred.** `core/data_source.py` gained `row_groups()`, `column_stats()`,
  `column_bounds()`, `is_monotonic()`, `row_range_for_x()` (bisect over row-group min/max — a linear
  scan would also have been fast enough at hundreds of row groups, but bisect stays cheap even on a
  file with far more of them), and a block-cached `read_window()` (fixed-size blocks, an
  `lru_cache`-backed **block-count** budget rather than the byte-budget the plan above describes —
  a reasonable simplification while nothing calls this yet; revisit for Phase D if real usage wants
  tighter control). `core/pyramid.py` adds `build_pyramid()`/`ColumnPyramid`/`pyramid_bounds()`
  (streams via `pyarrow.iter_batches`, no threading of its own by design — a later phase wires it
  through `BackgroundWorker` the same way downsampling/FFT/stats already do). `_to_numeric` moved
  out of `MainWindow` into `core/numeric.py` (`to_numeric()`) as part of this, since `pyramid.py`
  needs the identical datetime conversion and `core/` must not depend on `ui/`;
  `MainWindow._to_numeric` re-exports it unchanged (`_to_numeric = staticmethod(to_numeric)`), so
  every existing reference to that name in this document and in `core/convert.py`'s comments still
  resolves correctly.

  Deliberately **not** wired into `navigator.py`/`MainWindow` yet, despite the plan above listing it
  as a "free win": replacing the full-column LTTB waveform with a flat footer min/max line would be
  a real usefulness regression (loses the visible shape/pattern, e.g. this test data's daily
  on/off bursts) unless it's built from `column_stats()`'s per-row-group envelope or a pyramid
  instead of a single (min, max) pair — worth doing carefully together with Phase C's `Column`/
  `SeriesSource` work (which touches `navigator.py`'s call site anyway) rather than as a rushed
  standalone change now.

  Verified against `tests/parquet_analyzer/test_data_source.py` (12 tests) and
  `tests/parquet_analyzer/test_pyramid.py` (5 tests): `row_groups()` sums to the real row count and
  is contiguous; `column_bounds()`/pyramid bounds match the real column's min/max; `is_monotonic()`
  correctly distinguishes a sorted time column from random data; `row_range_for_x()` always
  over-covers (never narrower than) the brute-force `np.searchsorted` range and lands exactly on
  row-group boundaries; `read_window()` matches `read_columns()` for the same range, including
  across a cache-block boundary, and reuses cached blocks by object identity on a repeat request.
  `uv run pytest`: 126 passed, 1 skipped. Re-measured the feasibility numbers against
  `data/raw/daily_cycle_4gb.parquet` directly (not just estimated): `row_groups()` (872 groups)
  4.8ms; `column_bounds()` for all 5 columns 19ms; `is_monotonic("time")` <0.1ms; `row_range_for_x`
  for a 1-hour window 23ms; `read_window()` for the resulting 400,000 rows 14ms; `build_pyramid()`
  for the 4 value columns 1.82s (10,572 buckets at 8192 rows each) — all consistent with the
  2026-09-19 re-investigation's estimates above.
- **Phase C — departs from the original Step 2.** Step 2 introduced the `Column`/`SeriesSource`
  abstraction but explicitly kept `load_parquet` eager ("the memory problem isn't solved yet").
  This round's recommendation is to fold the biggest single practical win into this phase instead
  of deferring it to Phase D: at open, read schema + footer stats only; materialize a column's data
  the first time it's actually used (plotted, referenced by an expression, selected for stats). The
  time column stays eagerly materialized for now (needed for `searchsorted`/navigator) until
  Phase D's `row_range_for_x` removes even that. `add_series(name, x, y, color)` keeps its
  signature (wraps arrays in `MaterializedColumn`), so existing tests that call it directly need no
  changes; `series_data()` splits into `series_color()`/`series_source()` as originally planned.
  ~2-3 days, risk moderate (touches `MainWindow` state and the 6 tests noted below).

  **✅ Implemented and verified 2026-09-19 — with one deliberate scope cut vs. the plan above.**
  `core/column.py` adds `Column` (a `Protocol`: `values() -> np.ndarray`), `MaterializedColumn`
  (wraps an already-resident array — what the time column and every derived variable's computed
  result become), `LazyColumn` (path + column name + `ParquetDataSource`; reads and caches the
  *whole* column on first `values()` call — not yet windowed, that's still Phase D), and
  `LazyVariables` (a `Mapping[str, ndarray]` that materializes a `Column` only when actually looked
  up — handed to `evaluate_expression()` so `a + b` only ever pulls in `a`/`b`, never any other
  loaded-but-unrelated column; needed **zero** changes to `core/expression.py`, which was already
  confirmed to only ever do `in`/`[]` on the mapping it's given, never iterate every value).
  `MainWindow._file_columns`/`_raw_variables` now hold `Column`s; `load_parquet` reads only the
  selected time column eagerly, wrapping every other column in a `LazyColumn`. `_variable_array` was
  renamed to the public `variable_values(name)` (the accessor this plan promised) and is now the
  one place that resolves a raw-or-derived variable's array, materializing on demand.

  **Deliberately not done this phase, unlike the plan above**: `TimePlotWidget._series`/
  `add_series`/`series_data()` are untouched — series data is still passed and stored as plain
  `ndarray`s, not wrapped in a `SeriesSource`. Nothing in Phase C's actual goal (defer materializing
  a column until it's used) requires the plot widget to know anything changed — `add_series` always
  receives an already-materialized array regardless of when `MainWindow` decided to materialize it.
  The `SeriesSource`/`series_color()`/`series_source()` split matters for Phase D (the plot widget
  needs to know a series can be *re-fetched* for a different window, not just hold a fixed array),
  so it's deferred there rather than done now without an immediate behavioral need — the same
  "don't do UI-facing work before it's actually load-bearing" judgment call as Phase B's deferred
  navigator wiring below.

  `_refresh_navigator_overview`'s "nothing plotted yet" fallback (`main_window.py`) still eagerly
  materializes one column (the file's first raw variable) at open — Phase B's footer-stats/pyramid
  wiring that would remove this remains deferred (see that phase's note), now explicitly pushed to
  a dedicated follow-up rather than "together with Phase C" as previously worded, since Phase C's
  own diff was already broad enough on its own.

  Verified via `tests/parquet_analyzer/test_column.py` (7 tests: `LazyColumn` doesn't read until
  `values()`, caches after; `LazyVariables` proven — via a call-counting wrapper — to only
  materialize a column an expression actually references) and
  `tests/parquet_analyzer/test_lazy_loading.py` (5 tests, at the `MainWindow` level: opening a
  multi-column file leaves every column but the time column and the navigator's one fallback column
  unmaterialized; plotting/deriving-from/selecting-as-time-axis one variable materializes exactly
  that one, not its siblings). `uv run pytest`: 137 passed, 1 skipped. Re-measured against
  `data/raw/daily_cycle_4gb.parquet`:

  | Step | After Phase A only | After Phase C |
  |---|---|---|
  | after `load_parquet` (open) | peak 5.58 GiB (all 5 columns read) | **peak 2.99 GiB** (time column + 1 navigator-fallback column only) |
  | after overlaying all 4 raw variables | peak 5.74 GiB | peak 5.12 GiB (converges to ~the same total once everything actually *is* used — expected, Phase C changes *when* columns are read, not the eventual steady state for a session that touches all of them) |
  | `go_home()` | peak 5.84 GiB | peak 5.12 GiB (Phase A's fix still holds — no spike) |

  Open-time peak dropped a further ~46% versus Phase A alone. The middle row's converging-not-improving
  result is the correct, expected shape for this specific test session (it deliberately overlays
  every column to prove Phase C still ends up byte-for-byte the same as before once everything is
  touched) — the actual win is for the more common case this phase targets: a user who only ever
  looks at 2 of a file's 20 columns now only ever pays for those 2, not all 20.
- **Phase D = Step 3** ("the real fix": windowed reads keyed to the visible range). Phase B's
  `read_window` + LRU cache; a `SeriesSampleWorker` (fetch + downsample as one `BackgroundWorker`
  job, so a partially-fetched window is never drawn) reusing the existing per-series generation
  counter for staleness discarding; pyramid envelope for zoomed-out rendering; `required_halo(expr)`
  margin reads for `delta`/`rolling_mean`. **Amendment vs. the original Step 3 wording**: the
  `eager_load_limit_mb` size threshold should select a `Column` implementation only — never an
  `if` branch inside `MainWindow`/plot logic — so there is exactly one UI code path regardless of
  file size, not two that can silently diverge. ~4-6 days; this is the real risk.

  **✅ Substantially implemented and verified 2026-09-19 — with real, measured limitations, not
  just deferred scope.** `Settings.eager_load_limit_mb` (default 512 MB) gates a per-column check
  in `load_parquet` (`row_count * 8 > threshold` — **a deliberate simplification of the original
  wording's `row_count * (column_count - 1) * 8`**: since Phase C already materializes only the
  columns actually used, the relevant question is a single column's own size, not the whole file's
  width) — under threshold, unchanged `LazyColumn` (Phase C); over it, the new `WindowedColumn`
  (`core/column.py`): `window(row_start, row_end)` fetches only that range via Phase B's
  `read_window()`; `values()` falls back to `window(0, row_count)` (still needed for "nothing
  plotted yet"/derived-variable-source cases that genuinely want everything); `bounds()` returns
  Phase B's `column_bounds()` (footer statistics, no I/O) for `go_home()`'s true extent without
  materializing anything.

  The amendment above is honored exactly: `MainWindow._add_variable_to_plot()` is the **one** place
  that branches on `isinstance(column, WindowedColumn)` — `TimePlotWidget`, `PlotGridWidget`, and
  every other call site just call `Column.window()`/`.values()` the same way regardless of which it
  turns out to be. `TimePlotWidget` gained `add_windowed_series(name, x, y_source, y_bounds, color)`
  alongside the unchanged `add_series()` (verified byte-identical behavior for every existing caller
  — all 137 pre-Phase-D tests passed with zero changes needed); `_redraw_series` now fetches a
  windowed series' Y off the UI thread as part of the same background job that downsamples it (a
  partially-fetched window is never drawn), reusing the existing per-series generation counter for
  staleness discarding without any change to that mechanism; `go_home()`/the "first series" initial
  range now combine a windowed series' footer-stats bounds with a resident series' real extent via a
  sentinel-based aggregation (`_series_y_bounds`/`_finite_bounds_raw`) that was written carefully to
  avoid the bug an earlier draft had: computing each series' (0.0, 1.0) fallback independently and
  combining *those* would let one all-NaN/empty series silently narrow a real series' true range —
  fixed by using an (inf, -inf) sentinel per series and applying the (0.0, 1.0) fallback only once,
  after combining. `series_data()` returns `y=None` for a windowed series (no resident array exists
  to return) — a new `series_source()`/`transfer_series()` pair (used by `PlotGridWidget`'s
  "separate into a new plot") lets callers that need the underlying fetcher get it without depending
  on `MainWindow`'s variable bookkeeping still knowing that name (mirrors the existing
  `series_data()`-over-`variable_values()` preference for exactly this reason). `MainWindow` gained
  `variable_window(name, row_start, row_end)` — FFT/stats now use it instead of
  `variable_values(name)[a:b]`, so requesting FFT/stats on a windowed column reads only the visible
  range, not the whole column first. The navigator's "nothing plotted yet" fallback and "the anchor
  plot's first series is windowed" case both now build a coarse per-row-group min/max envelope from
  `column_stats()` (`_windowed_navigator_overview()`) instead of requiring a resident array — real
  shape (this test data's daily bursts are visible in it), not a flat line, at footer-only cost.

  Verified via `tests/parquet_analyzer/test_column.py` (5 new `WindowedColumn` tests: `window()`
  matches the real slice and never reads the whole file — spied on `ParquetDataSource.read_columns`
  to confirm; `values()`/`bounds()` fall back correctly), `tests/parquet_analyzer/
  test_windowed_series.py` (9 tests, `TimePlotWidget` level, using a call-counting fake window
  source: initial range from `y_bounds` not data; redraw fetches only the visible range; correct
  displayed values; `series_data()`/`series_source()`/`transfer_series()` contracts; `go_home()`'s
  mixed resident+windowed aggregation doesn't let one series pollute another's range — the bug
  above, caught by this test before it shipped; stale-fetch discarding via the generation counter),
  and `tests/parquet_analyzer/test_windowed_loading.py` (8 tests, `MainWindow` level, forcing the
  windowed path on small fixtures via `eager_load_limit_mb = 0`: threshold selects the right
  `Column` type; dropping/FFT/stats/navigator/go_home all work correctly on a windowed variable).
  `uv run pytest`: 159 passed, 1 skipped.

  **Real measured behavior on `data/raw/daily_cycle_4gb.parquet`** (all 4 raw columns become
  `WindowedColumn` under the default 512 MB threshold — 86.6M rows × 8 bytes ≈ 693 MB > 512 MB):

  | Step | Peak |
  |---|---|
  | after `load_parquet` (open) | 2.15 GiB |
  | after overlaying all 4 variables (view still at full zoom-out from the first series) | 2.23 GiB |
  | once the initial windowed fetches settle | **6.74 GiB** |
  | after 5 pan/zoom operations into smaller ranges | 6.74 GiB peak, but **RSS drops to 3.84 GiB** |
  | `go_home()` (back to full zoom-out) | 6.74 GiB |
  | `variable_window()` for a ~2.6M-row FFT-sized range | 6.74 GiB peak, **RSS drops to 1.12 GiB** |

  **The honest finding, not just the good news**: at full zoom-out (the view right after adding the
  first series, or after `go_home()`), a windowed series' fetch covers close to the *entire* visible
  range — for this file, ~86.6M rows — before LTTB can downsample it, which costs about as much as
  Phase C's old "materialize the whole column once" path *did*, and with 4 such fetches able to run
  concurrently on the thread pool, transiently costs *more* (6.74 GiB peak vs. Phase C's 5.12 GiB
  plateau for the identical "overlay all 4, go_home" scenario). This is precisely the known gap the
  2026-09-19 re-investigation flagged and the plan's "pyramid envelope for zoomed-out rendering"
  line was meant to close — `core/pyramid.py` exists (Phase B) but **is not wired into
  `_redraw_series`'s decision logic in this pass**; `_redraw_series` always does a real windowed
  fetch regardless of how wide the visible range is. What Phase D *does* deliver, and what the
  pan/zoom numbers above demonstrate, is that memory now **adapts to the current view** — it goes
  up when zoomed out, and, critically, **comes back down once you zoom into a smaller range**,
  rather than staying pinned at Phase C's permanent plateau forever regardless of what you do
  afterward. For the common interactive case (look at a couple of columns, zoomed in), this is a
  real improvement; for "open a huge file and immediately view everything zoomed all the way out,"
  it is not yet better than Phase C and can transiently be worse. Wiring the pyramid into
  `_redraw_series` (use bucket min/max instead of a real fetch once the visible row count exceeds
  some multiple of `n_out`) is the natural next increment, not attempted in this pass.

  **Also deliberately out of scope for this pass** (each a real, understood gap, not an oversight):
  the time column stays fully resident even over the threshold (`row_range_for_x()` isn't used by
  this design at all — `_redraw_series`/`_visible_row_range` still `np.searchsorted` a resident `x`,
  exactly as before Phase D, which is *why* the monotonicity policy decision below never actually
  came up here); creating a derived variable whose expression references a `WindowedColumn` source
  still forces a full read of that source (`LazyVariables.__getitem__` calls `values()`, not
  `window()`) — `required_halo(expr)` for windowed derived-variable evaluation was not implemented.

  ###### Pyramid-wiring follow-up (implemented and verified 2026-09-19, same day)

  Closed the gap above: `core/pyramid.py` (built in Phase B) is now actually wired into
  `TimePlotWidget._redraw_series`'s decision. `WindowedColumn` (`core/column.py`) gained
  `pyramid`/`request_pyramid()`/`set_pyramid()`: `request_pyramid()` returns a zero-arg job to run
  on a background thread (or `None` if a build is already done or in flight — no duplicate builds),
  which streams the column once via `build_pyramid()`. `_redraw_series` now checks, only for a
  windowed series with downsampling enabled: if the visible row count exceeds
  `_PYRAMID_PREFERRED_ROW_THRESHOLD` (5,000,000 rows — a fixed count, not a multiplier of the
  pixel-derived `n_out`, chosen because the actual concern is a real fetch's own cost/memory at
  that width, not how zoomed out the view happens to be; ~40MB for a single fetch at that
  threshold, and real fetches measured well under it stay fast — 14ms for 400,000 rows against the
  4GB reproduction file), prefer the pyramid's coarse per-bucket min/max envelope once built
  (`_render_from_pyramid` — synchronous, no disk I/O, so no background worker needed; still bumps
  the generation counter so a slower, still-in-flight real-fetch result from an earlier redraw is
  correctly discarded as stale) over a real windowed fetch. If no pyramid exists yet,
  `_maybe_start_pyramid_build` kicks one off in the background (`_on_pyramid_ready` calls
  `set_pyramid()` then re-triggers `_redraw_series`, guarded by checking the series still points at
  the same `y_source` — a plot removed or repointed while the build was in flight is a no-op, not a
  crash) while this redraw falls through to the existing real fetch, same as before this follow-up.
  Downsampling disabled bypasses the pyramid entirely (the user explicitly asked for full raw
  precision, which per-bucket min/max can't offer).

  Verified via 3 new `core/column.py` tests (`request_pyramid()` returns a working job; returns
  `None` while already building or once already built — no duplicate builds) and 5 new
  `TimePlotWidget`-level tests in `test_windowed_series.py` (below threshold: pyramid never
  requested; above threshold without one yet: falls back to a real fetch *and* requests a build;
  above threshold with one ready: the real fetch is skipped entirely; a background build completing
  triggers a redraw that then uses it; downsampling disabled bypasses the pyramid even above
  threshold) — all using a fake window source with the pyramid protocol so this didn't need a real
  multi-million-row fixture. `uv run pytest`: 167 passed, 1 skipped.

  **Real measurement, isolated to one column** (to separate the mechanism's own effect from
  multi-column concurrency, below) — `data/raw/daily_cycle_4gb.parquet`, one variable plotted:

  | Step | Peak |
  |---|---|
  | after `load_parquet` | 2.15 GiB |
  | first full-zoom-out render (real fetch; pyramid finishes building during this same window) | 3.71 GiB |
  | 5× repeated `go_home()` at full zoom-out | **still 3.71 GiB — no further growth** |
  | 5× (zoom into a tiny range, then `go_home()`) cycles | **still 3.71 GiB** |

  This is the win working exactly as designed: one real fetch + one pyramid build, then every
  further redraw at that zoom level is free, indefinitely, instead of re-fetching (and paying disk
  I/O and transient memory for) close to the whole file on every single `go_home()`/zoom-out.

  **A limitation found plotting all 4 columns together, not present in isolation — investigated
  further, and the initial diagnosis turned out to be wrong.** After overlaying all 4 raw variables
  and letting the view settle, none of the 4 columns' pyramids had finished building even after
  1.5s of waiting, and repeatedly pressing `go_home()` 6 times in a row showed peak climbing
  8.41 → 8.81 → 9.05 GiB before stabilizing only on the 4th–6th press. The first hypothesis — the
  real windowed fetch and the pyramid build competing for the same `QThreadPool.globalInstance()`,
  so 4 columns' worth of large real fetches queue ahead of and starve the pyramid builds — was
  tested directly: a dedicated pool for pyramid-build jobs was implemented (`_PYRAMID_THREAD_POOL`,
  `core/background_worker.py`-style separation from `self._thread_pool`, mirroring
  `MainWindow._analysis_thread_pool`'s existing FFT/stats isolation) and A/B-measured against the
  shared-pool baseline with precise per-column completion timestamps. **The hypothesis did not
  hold up**: a dedicated pool with unrestricted concurrency finished all 4 pyramids in ~3.5s, the
  same as the shared global pool (~3.5–3.6s) — no improvement. A dedicated pool artificially capped
  at 2 concurrent threads was *slower* (~3.7–3.8s), from needlessly serializing work that ran fine
  in parallel on the shared pool. The real explanation: each pyramid build streams close to the
  whole column (~700MB for this file, via `pyarrow.iter_batches`) — that read simply takes a few
  seconds of wall-clock I/O regardless of which pool runs it or how many threads are available; the
  6-press `go_home()` climb was just that same ~3.5s elapsing in real time across the loop, not a
  scheduling artifact. **Reverted the dedicated pool** (kept on `self._thread_pool`, same as real
  fetches) since it added complexity without a measured benefit. This is not a fixable-by-
  rescheduling gap — closing it for real would mean making pyramid construction itself faster
  (smaller/adaptive bucket counts, sampling instead of a full stream, or building only for the
  columns/ranges actually about to be viewed first) rather than reordering where it runs; not
  attempted in this pass. Verified via `uv run pytest`: 167 passed, 1 skipped (unchanged — this
  investigation net-reverted to the same code shape, just with the correct reasoning now recorded
  in its place).
- **Phase E = Step 4** (polish, unchanged): status-bar coarse/precise indicator, cancel in-flight
  pyramid workers on file switch, `eager_load_limit_mb` settings entry, delete
  `set_downsample_enabled` (§13.3) once a pyramid makes "disable downsampling" incoherent.

  **✅ Implemented and verified 2026-09-19 — with one item's plan revised and one left
  deliberately unimplemented, both explained below.** `Settings.eager_load_limit_mb` gained a
  `SettingsDialog` entry (`eager_load_limit_spin`, 1–100,000 MB, tooltip noting it only affects
  files opened *after* the change — the already-open file's `Column` choices were made at open
  time and don't retroactively change). The status-bar coarse/precise indicator:
  `TimePlotWidget` tracks `_coarse_series` (names currently shown via a pyramid envelope, updated
  by `_render_from_pyramid`/`_on_downsampled`/`remove_series`/`transfer_series`) and emits
  `coarseRenderingChanged(self, bool)` only on an empty↔non-empty transition;
  `PlotGridWidget` aggregates across every plot the same way `busyChanged` already aggregates
  background-job state; `MainWindow._precision_label` (a new permanent status-bar widget) shows
  "簡易表示中…" while any plot has any coarse series. **§13.3's decision, revised**: rather than
  deleting `set_downsample_enabled` as that section's pre-Phase-D note speculated, the pyramid
  turned out *not* to make it incoherent — `_redraw_series` already only prefers the pyramid when
  downsampling is enabled, so disabling it still has an exact, well-defined meaning even for a
  `WindowedColumn` series ("always fetch real per-sample data, never a bucket envelope"). Wired up
  instead: a new checkable toolbar action (`toggle_downsample`, `MainWindow._downsample_enabled`,
  applied via `_apply_downsample_enabled`), round-tripped through `save_view_dialog`/`load_view`
  exactly like `y_axis_range`/`x_axis_range` (per-view state, not a persisted `Setting` — opening a
  plain file without a view resets it to the default). **Cancel in-flight pyramid workers on file
  switch: investigated, left unimplemented.** `BackgroundWorker`/`QRunnable` have no built-in
  interrupt, and the existing `_on_pyramid_ready` guard (`entry is None or entry["y_source"] is not
  y_source`) already makes a stale pyramid landing after a file switch a safe no-op, not a crash —
  confirmed via a dedicated test that switches files mid-build. True cancellation (stopping the
  disk read mid-stream) would need `build_pyramid()` to accept and periodically check a
  cooperative-cancellation callback, a real but separable change; the correctness case this bullet
  originally worried about doesn't require it, so it stays a documented "efficiency, not
  correctness" gap rather than something this pass attempted.

  Verified via `tests/parquet_analyzer/test_settings_dialog.py` (1 test extended for
  `eager_load_limit_mb`) and 8 new tests in `test_windowed_loading.py` (status indicator
  shows/clears correctly; the toolbar toggle applies to every plot; `downsample_enabled`
  round-trips through a saved view; opening a plain file resets it to the default; switching files
  mid-pyramid-build doesn't crash). `uv run pytest`: 173 passed, 1 skipped. Manually verified
  against `data/raw/daily_cycle_4gb.parquet`: opening the file, plotting a column at full
  zoom-out, and letting the pyramid build showed the precision label switch on; disabling
  downsampling correctly forced real fetches and cleared it.

**Policy decisions, both models converged, treated as resolved pending final sign-off**:
non-monotonic time column → force the eager/materialized path for that column and surface a
status-bar note, rather than excluding it from `time_axis_combo` (would hide legitimate data); the
size threshold selects a `Column` implementation only, never a branch in UI code (Phase D above).
The 2026-09-17 Opus/Fable disagreement on FFT's target range is moot — `specification.md` §5.6 and
`main_window.py:48,371-383` already implement "visible range, row-count-capped," confirmed
unchanged by this round.

**New decision needed: where derived cache/index data (the pyramid, the block-read cache) lives on
disk.** Raw Parquet files under `data/raw/` must never be modified or written next to (per-user
instruction: no raw-data processing; `data/raw/` may be a shared or read-only measurement-data
location in practice, and even placing sidecar files there blurs "raw = untouched source").
Recommendation: a new `data/parquet_analyzer/cache/` directory (an addition under the existing,
already-gitignored, per-machine `data/parquet_analyzer/` app-data root — no new top-level directory,
no `config.py` change beyond one new subpath), keyed by `(resolved absolute path, file size,
mtime)` rather than a content hash (O(1) to compute; a renamed/regenerated file just costs one
cheap rebuild — 1.74s for a 4-column pyramid per the measurement above). This keeps `data/raw/`
untouched (safe even if it's a read-only mount), is trivially and safely clearable
(`rm -rf data/parquet_analyzer/cache/`) with zero risk to source data, and gives independent caches
to multiple machines/users opening the same shared raw file with no write conflicts. Not yet
implemented; flagging here so Phase B's `core/pyramid.py` design starts from this decision instead
of an ad-hoc choice.

Phases A-E of the phased fix itself are implemented and verified (see each phase's own "✅
Implemented and verified" note above); the on-disk cache directory proposed immediately above is
the one still-open, not-yet-implemented piece of this section, deliberately left for a future
follow-up rather than folded into this already-broad pass. Test-count note: the "79+"/"82"/"107"
figures cited per-phase above reflect the suite size at the time each phase's note was written; the
suite now stands at 173 passed, 1 skipped (Phase E's count, current as of 2026-09-19).

### 14. Statistics Display (added 2026-09-17)

See `specification.md` §5.12. For the selected variable(s), computes count,
missing-value count, min, max, mean, standard deviation, and median from every raw
sample in the currently visible time range, and displays them in a separate window.

- **Range determination shares
  [§13.5.1](#1351-memory-blowup-on-large-4gb-files-investigated-2026-09-17-phased-fix-implemented-2026-09-19)'s
  `MainWindow._visible_row_range()`, implemented for FFT** (the logic that was
  inlined inside `_on_fft_requested` when FFT was implemented was extracted into a
  method at the point this feature was added). Falls back to the whole file when no
  time-domain plot has any series (e.g. right after opening a file), same as FFT.
- **Unlike FFT, no row-count cap is applied.** `core/analysis.py`'s `basic_stats(y)`
  just computes `min`/`max`/`mean`/`std`/`median` NaN/Inf-safely in a single numpy
  pass (only `median` needs an internal sort), with none of FFT's "the result is
  meaningless if the sampling interval is non-uniform" constraint. However, at the
  91,980,000-row scale (`sensor_log_4y_4gb.parquet`), `median`'s sort cost measured
  at 0.6-0.7 seconds, so it still runs asynchronously via `core/background_worker.py`'s
  `BackgroundWorker` (`QRunnable`, `setAutoDelete(False)` + caller-held strong
  reference pattern; shared by downsampling recompute/FFT/statistics).
- With multiple variables selected, one `BackgroundWorker` is started per variable
  (`MainWindow._on_stats_requested`), and only once every one has finished is
  `StatsDialog` built and shown, once (`_on_stats_finished` checks a `remaining`
  counter and only builds the dialog once the last one completes). The results are
  shown in the order the variables were selected, not completion order
  (`variable_panel.selectedItems()`'s order).
- **Target selection: explicit panel selection, falling back to whatever's currently
  plotted** (changed 2026-09-19, user-requested — the original behavior required
  `variable_panel.selectedItems()` to be non-empty even for a variable already
  overlaid on a plot, which felt like a redundant extra step).
  `MainWindow._on_stats_requested`: if `variable_panel.selectedItems()` is empty,
  falls back to `MainWindow._plotted_variable_names()` — every variable currently
  overlaid on any *time-domain* plot (an FFT plot's series are already FFT output,
  not a raw/derived variable — excluded, same exclusion `_visible_row_range` already
  makes), deduplicated across plots, in plot/series order. An explicit panel
  selection always takes priority over what's plotted, so checking a variable that
  isn't plotted at all still works exactly as before. Each candidate name is
  resolved via `variable_window()` up front (not lazily inside the per-worker loop)
  and any that raises `ExpressionError`/`KeyError` is skipped (logged, not shown as
  an error dialog) rather than crashing — realistically only reachable via
  `_plotted_variable_names()`, since a panel selection is always a currently-known
  variable name already, but kept for the same "one bad item shouldn't take down the
  rest" reasoning as the `load_view` fix earlier in this document. If nothing
  resolves at all, shows an information dialog instead of proceeding with zero rows.
  Regression tests: `test_stats_falls_back_to_plotted_variables_when_none_selected_in_panel`,
  `test_stats_prefers_explicit_panel_selection_over_plotted_variables`
  (`tests/parquet_analyzer/test_stats.py`).
- `StatsDialog` (`ui/stats_dialog.py`) is a `QDialog`, but opened non-modally via
  `show()` rather than `exec()` (so results can be compared while still interacting
  with the main window — matching the "in a separate window" request). It has
  `MainWindow` as its parent (`parent=self`) and `WA_DeleteOnClose` set, so it's
  destroyed on close and its lifetime is otherwise tied to `MainWindow`'s.
- The curve rendered by downsampling
  ([§10](#10-asynchronous-downsample-recompute-finalized)) consists of decimated
  display points; the statistics computation is separate from that, slicing the same
  row range from `_raw_variables`/derived variables each time (aggregating the
  rendered points would be statistically meaningless). Confirmed via a test that the
  post-LTTB-decimation point count and the statistics' sample count actually differ
  (`tests/parquet_analyzer/test_stats.py`).

### 15. MDF/MATLAB -> Parquet Conversion Tool (added 2026-09-18)

`tools/convert_to_parquet.sh` (`src/parquet_analyzer/convert_cli.py` is the CLI,
`core/convert.py` is the implementation). Since this app only reads Parquet, a
separate tool converts from measurement formats (MDF/MATLAB).

#### 15.1 Why Not Read Them Directly

Both MDF and MATLAB are "measurement/recording" formats, not structured around
Parquet's kind of columnar, selective reading (column pruning, learning a range from
just the footer via row-group statistics). This is a poor match for the "memory
countermeasures that depend on Parquet's row-group statistics" design considered in
[§13.5.1](#1351-memory-blowup-on-large-4gb-files-investigated-2026-09-17-phased-fix-implemented-2026-09-19) —
building MDF/MATLAB reading directly into the app would make that kind of efficiency
work unusable as-is. Keeping "measurement stays MDF/MATLAB, convert to Parquet before
analysis" as a two-stage process addresses this while keeping the existing design
(polars/pyarrow-based, efficiency work that depends on Parquet's structure) intact.

#### 15.2 Separating Dependencies

`asammdf`, `scipy`, and `h5py` are all conversion-only — the GUI app itself never
uses them. Mixing them into the app's main dependencies would mean installing heavy
dependencies (especially `asammdf`, which also pulls in `pandas`, `lxml`, etc.) even
for a normal GUI launch, so they're kept in a separate `convert` group under
`pyproject.toml`'s `[dependency-groups]` (installed explicitly via
`uv sync --group convert`). `tests/parquet_analyzer/test_convert.py` skips itself
automatically via `pytest.importorskip` in an environment without that group
installed, so it doesn't break a normal `uv run pytest` run (the `dev` group only).

#### 15.3 MDF Conversion

Uses `asammdf.MDF.to_dataframe(raster=...)`. MDF channel groups can run at different
sample rates (e.g. a fast channel and a slow channel coexisting in the same file), so
this relies on `to_dataframe`'s built-in resampling of every channel onto one shared
time axis — without it, "combining multiple series with different row counts into
one Parquet table" would have needed solving from scratch. Passing `raster` (in
seconds) aligns everything to that interval; omitted, it falls back to asammdf's own
default (the union of every channel's timestamps).

The index `to_dataframe` returns is **seconds relative to the start of the
recording**, which as-is doesn't line up with the epoch seconds
`MainWindow._to_numeric`/`pg.DateAxisItem` expect (it would display as a date near
the year 1970 — the same kind of trap as the 4GB test file's timestamp type mismatch
found in §13.5.1). `mdf.header.start_time` (the recording's absolute start time) is
added to the relative seconds to convert to epoch seconds before writing out
(regression-checked by `tests/parquet_analyzer/test_convert.py`'s
`test_mdf_time_column_is_absolute_epoch_seconds`).

#### 15.4 MATLAB Conversion

A MATLAB `.mat` file carries no file-format-level information about which variable
is the time axis (it's just a collection of named variables), so:

- Only 1-D numeric-array variables are considered candidates (matrices, structs,
  strings, etc. are excluded).
- A variable named `time`/`t`/`timestamp`/`timestamps` (case-insensitive) is
  auto-detected as the time axis. If none is found, explicit `--time-var` is
  required, with the error message listing the candidate variable names
  (`ConversionError` — playing the same "turn an unexplained exception into a
  message that makes sense" role as `core/expression.py`'s `ExpressionError`).
- A variable whose row count doesn't match the time axis is silently skipped with a
  warning (never truncated or zero-padded to force it to fit).

v5/v7 format is read via `scipy.io.loadmat`. v7.3 format (used automatically by
MATLAB when saving a variable over 2GB — actually HDF5 under the hood) makes
`scipy.io.loadmat` raise `NotImplementedError` ("Please use HDF reader for matlab
v7.3 files"), which is caught to fall back to an `h5py`-based read. The two only
differ in how they're read; the subsequent "auto-detect the time axis, skip
mismatched row counts" logic (`_select_time_and_columns`) is shared between them.

### 16. Cursors (added 2026-09-19, user-requested)

`ui/plot_widget.py`'s `TimePlotWidget` and `ui/plot_grid.py`'s `PlotGridWidget`
together implement the cursor feature described in specification.md §5.13. Split as:
`TimePlotWidget` owns everything about *drawing* a cursor (the `pg.InfiniteLine` +
`pg.TextItem` pair, nearest-sample lookup) and *detecting* user intent
(click-to-add/remove, drag-to-move, show/hide) on itself alone; `PlotGridWidget`
owns *cross-plot coordination* (broadcasting one plot's cursor add/move/remove to
every other plot of the same X domain, and seeding a newly-added plot with whatever
cursors already exist).

#### 16.1 Placement and Removal

`TimePlotWidget.__init__` connects `self.scene().sigMouseClicked` to
`_on_scene_mouse_clicked`, gated on `self._cursor_mode_enabled` (off by default;
`MainWindow`'s "Cursor" toolbar toggle -> `PlotGridWidget.set_cursor_mode_enabled()`
-> every plot's `set_cursor_mode_enabled()`) and `ev.button() ==
Qt.MouseButton.LeftButton`. A click that lands on one of this plot's own cursor
lines/labels (`ev.currentItem` — pyqtgraph's `GraphicsScene.sendClickEvent` always
sets this to whichever item actually received the click, even though
`sigMouseClicked` itself fires unconditionally for every click) is ignored here —
`pg.InfiniteLine`'s own `sigClicked` (emitted from its `mouseClickEvent`, which Qt
only calls for a press+release *without* significant movement — a real drag instead
triggers `mouseDragEvent`/`sigDragged`, so the two never both fire for the same
gesture) is what handles removal, via `_on_cursor_line_clicked` ->
`cursorRemoveRequested`. Otherwise, the scene click's position is mapped from scene
to view (data) coordinates (`ViewBox.mapSceneToView`) and emitted as
`cursorAddRequested(self, x)`.

`PlotGridWidget._on_cursor_add_requested` assigns a new id (`f"c{n}"`), records
`(x_axis_datetime, x)` in `self._cursor_positions` (the source of truth used to seed
a plot added later — see 16.3), and calls `add_cursor(id, x)` on every plot sharing
the originating plot's domain (`plot.x_axis_datetime == source.x_axis_datetime`;
this is the same domain-matching rule `add_plot`'s X-linking already uses).
`_on_cursor_remove_requested` mirrors this for removal.

#### 16.2 Dragging, Labels, and Hide/Show

`pg.InfiniteLine(movable=True)`'s built-in drag handling does the actual moving;
`TimePlotWidget` only listens to `sigPositionChanged` (`_on_cursor_line_moved`),
which recomputes that cursor's own label and re-emits `cursorMoveRequested(self, id,
x)` so `PlotGridWidget._on_cursor_move_requested` can call `set_cursor_x(id, x)` on
every *other* plot of the same domain (skipping the plot the drag actually happened
on, since its line is already at the new position — re-`setValue`-ing it there too
would just re-fire the same signal). `set_cursor_x` blocks the line's own signals
while repositioning it precisely to prevent that echo.

A cursor's label content (`_update_cursor_label`) is only ever recomputed on an
actual add/move, or when the set of series on that plot changes
(`refresh_cursor_labels()`, called from `_install_series`/`remove_series`/
`transfer_series`) — never from `_on_range_changed` (which fires continuously during
a mouse-drag pan). A `WindowedColumn` series' value lookup
(`_series_value_at`) does a real (if tiny — one row) disk read via
`y_source.window(row, row+1)`, so doing that on every pan-driven range-change tick
would risk exactly the kind of per-frame UI-thread I/O jank this app's rendering
path otherwise goes out of its way to avoid (§10, §13.5.1). `_on_range_changed`
instead only calls `_reposition_cursor_overlays()`, which repositions existing
label items (they're pinned near the top of the current Y view range so panning/
zooming doesn't leave them stranded) without touching their text.

`set_cursor_mode_enabled(enabled)` toggles every existing cursor's `line`/`label`
`setVisible(enabled)` (and `line.setMovable(enabled)`) rather than deleting or
freezing-in-place: turning the toolbar toggle off hides cursors entirely (so they
don't clutter a plot the user is now just panning/zooming normally), and turning it
back on restores them, still at the same `x`, with no separate "show" step needed —
the position was never touched, only visibility.

#### 16.3 Cross-Plot Sync and Cleanup

`PlotGridWidget._cursor_positions` (`cursor_id -> (domain, x)`) is the coordinator's
own record, separate from each `TimePlotWidget._cursors`. `add_plot()` seeds the new
plot with every position whose domain matches, so a plot added *after* cursors
already exist still shows them.

A cursor's X value is only meaningful relative to the time column it was placed
against, so both `TimePlotWidget.set_x_data()` (time-axis column switch) and
`MainWindow._clear_plot_grid()` (opening a new file) drop every cursor — via
`TimePlotWidget.clear_cursors()` and `PlotGridWidget.clear_cursors()` respectively —
rather than leaving them at a numeric position that no longer corresponds to
anything real.

### 17. Application Versioning (added 2026-09-20, user-requested)

`pyproject.toml`'s `version` field is the canonical version number, mirrored by hand
into `parquet_analyzer/__init__.py`'s `__version__` string constant (the two are kept
in sync manually rather than one being derived from the other at runtime — this
project isn't installed as a distribution (`[tool.uv] package = false`), so
`importlib.metadata.version()` has nothing to look up; reading `pyproject.toml`
itself at runtime would work but adds a parse step for a single string that changes
rarely). `__init__.py` staying a one-line constant (rather than empty, as it was
before this) doesn't change config.py's dependency-free-import guarantee — it's still
just a string literal, nothing that drags in PySide6/pyqtgraph/polars.

Two things read it: `__main__.py`'s `--version` flag (`argparse`'s built-in
`action="version"`, printing `parquet_analyzer <version>` and exiting), and
`ui/main_window.py`'s `_APP_TITLE`, which now reads `"Parquet Analyzer v<version>"` —
visible in the window title bar on every launch, including the public/downstream
copies produced by `tools/publish_to_public.sh`, so a bug report against a copied
repo can be tied back to a specific version without asking the reporter to check
`pyproject.toml` by hand.

---

## 日本語

- 版数: v0.6（2026-09-19更新: 13.5.1章をOpus・Fableによる2回目の独立再調査で修正・拡張）
- 位置づけ: `specification.md`（要求仕様）を実装レベルまで具体化したもの。
  `specification.md` 第11章の未確定事項のうち 11.1, 11.5, 11.6 を確定し、11.3・11.4・11.7 を
  実装可能な形まで詳細化する。要求レベルの背景・理由は `specification.md` を参照。

---

### 1. 技術選定（確定）

| 項目 | 選定 | 理由 |
|---|---|---|
| プロットライブラリ（11.1） | `pyqtgraph` | ドラフト記載の `Pygraph` は `pyqtgraph` の表記ゆれと解釈。PySide6との親和性・大量点描画性能の実績から確定。 |
| Parquet読み込み（11.6） | `polars` | `pl.scan_parquet` によるレイジー評価で、必要な列・行範囲のみ読み込める。列指向処理がAPIとして自然で、`pandas`よりメモリ効率が良い。スキーマ確認など軽量な操作には内部で`pyarrow`も利用する。 |
| exe化（11.5） | `PyInstaller` | PySide6アプリでの実績が豊富。`--onedir`形式で配布し、起動時間の悪化を避ける。 |

### 2. モジュール構成

```
src/parquet_analyzer/
├── __main__.py               # GUIエントリポイント
├── convert_cli.py            # MDF/MATLAB→Parquet変換のCLIエントリポイント（argparse、Qt非依存）
├── config.py                 # パス定義の共有モジュール（stdlibのみ依存、他プロジェクトからもimport可能）
├── ui/
│   ├── main_window.py       # MainWindow: メニュー、ツールバー(H/Rボタン)、全体レイアウト
│   ├── plot_grid.py         # PlotGridWidget: マルチプロットの配置・追加/削除管理
│   ├── plot_widget.py       # TimePlotWidget: pyqtgraph.PlotWidgetラッパー、ズーム/パン処理
│   ├── navigator.py         # NavigatorWidget: 縮小全体表示 + 選択範囲矩形（共通・単一）
│   ├── variable_panel.py    # VariablePanel: 変数一覧、D&D、右クリックメニュー
│   ├── expression_bar.py    # ExpressionBar: 演算式入力欄
│   ├── stats_dialog.py      # StatsDialog: 統計結果を表示する非モーダル別ウィンドウ
│   ├── settings_dialog.py   # SettingsDialog: 設定メニューのダイアログ
│   └── folder_picker.py     # FolderPickerDialog: データフォルダ選択 + 履歴表示
├── core/
│   ├── data_source.py       # ParquetDataSource: polars LazyFrameラップ、スキーマ/範囲読み込み
│   ├── downsample.py        # lttb(x, y, n_out) -> (x_ds, y_ds)。QThreadPool上のワーカーから呼ばれる
│   ├── background_worker.py # BackgroundWorker(QRunnable): 任意の関数をUIスレッド外で実行し
│   │                         # 結果をシグナルで返す共通ワーカー（ダウンサンプリング再計算/
│   │                         # FFT/統計が共用。世代番号による陳腐化破棄はダウンサンプリング側の
│   │                         # 呼び出し元（plot_widget.py）が結果タプルに含めて自前で判定する）
│   ├── expression.py        # evaluate_expression(expr, variables): 制限付き評価
│   ├── analysis.py          # fft(x, y), delta(y), rolling_mean(y, window), basic_stats(y)
│   ├── variable.py          # Variable / DerivedVariable データ構造
│   ├── convert.py           # MDF/MATLAB→Parquet変換（15章参照）。GUI非依存で、
│   │                         # convert_cli.py経由でのみ到達し、GUI本体からは使われない
│   └── oplog.py             # デバッグモードの操作ログヘルパー、log_op()
└── io/
    ├── view.py              # View データ構造、save_view()/load_view()
    └── settings.py          # Settings データ構造、load_settings()/save_settings()
```

各モジュールの責務は上記コメントの通り。`ui/` はPySide6/pyqtgraphに依存し、`core/`・`io/` は
GUI非依存（単体テスト容易性・将来のCLI化に備える）。

### 3. 起動シーケンス

1. `tools/run.sh` / `run.bat` が `PYTHONPATH` に `src` を追加し、
   `uv run python -m parquet_analyzer` を実行。
2. `__main__.py`:
   1. `config.ensure_dirs()` を呼び、`cfg/data/log` 配下の `parquet_analyzer/` サブディレクトリを作成。
   2. `logging` を `config.PARQUET_ANALYZER_LOG_DIR` 向けに設定。
   3. `cfg/parquet_analyzer/settings.json`（無ければ既定値）を読み込み。
   4. `QApplication` を起動し `MainWindow` を表示。

### 4. 操作仕様（確定・11.3・v2）

| 操作 | 動作（現在の既定値——後述の通り設定変更可能） |
|---|---|
| マウスホイールスクロール | X軸（時間軸）方向へパン（移動のみ、ズームしない） |
| Ctrl + スクロール | X軸（時間軸）のみズーム |
| Shift + スクロール | Y軸（Amp）のみズーム |
| H キー / Home ボタン | 表示範囲を全データ表示にリセット |
| R キー / Redraw ボタン | 現在の表示範囲でプロットを再描画（ダウンサンプリング再計算含む） |

- 実装は `TimePlotWidget.wheelEvent`（`ui/plot_widget.py`）。3操作は完全に分離されており
  （どの修飾キーも2つ以上の動作を兼ねることはない）、**どの修飾キーがどの動作を担うかは
  設定で変更できる**（`Settings.scroll_bindings`、`{"none", "ctrl", "shift"}`から
  `{"x_zoom", "y_zoom", "pan"}`への全単射、設定ダイアログの「スクロール操作」欄。
  以前あって廃止された「両軸ズーム」設定（v1）とはどう違うか、また上表が当初のv2方式の
  割り当てと一致しない理由は、後述のv3の経緯を参照）。
- Shiftパンの移動量は「現在の表示中Xスパンの15% × ホイール1ノッチ(120)あたり」
  （`_PAN_STEP_FRACTION = 0.15`）。移動方向は「上スクロール＝時間を遡る」方向に決め打ちして
  いる。逆の方が自然に感じる場合は`wheelEvent`内の符号を反転するだけで良い。
- **クロスプラットフォームの罠2点**（実機フィードバックで発覚、`wheelEvent`内で対処済み）:
  - macOSでは、Qtが物理Ctrlキーを`Qt.KeyboardModifier.MetaModifier`として報告する
    （Cmd/Ctrlをクロスプラットフォームなショートカット慣習に合わせるためQtが入れ替えている）。
    `ControlModifier`のみをチェックしていると、物理Ctrlキーでの操作が「無印スクロール」
    （時間ズーム）に誤って倒れてしまう。`ControlModifier | MetaModifier`の両方を「Ctrl」
    として扱うことで対処。
  - 一部のトラックパッド/OSでは、Shift+垂直スクロールのジェスチャーを水平ホイールイベントとして
    報告する（`angleDelta()`の値が`.y()`ではなく`.x()`に入る）。`.y()`だけを見ていると
    Shiftパンが無音無反応になる。`angle_delta.y() or angle_delta.x()`でどちらか非ゼロの方を
    使うことで対処。

> **経緯 (v1→v2)**: 当初は無印スクロール=両軸ズーム、Ctrl=X軸のみズーム、Shift=Y軸のみズーム
> という設計で、無印スクロールの両軸ズームは`both_axis_scroll_zoom`設定でX軸のみズームに
> 切り替え可能にしていた（旧仕様。さらにその前は無印とCtrlがどちらもX軸ズームで矛盾していた
> ものを修正した版）。実際に使ってみた結果「操作感が悪い」というフィードバックを受け、
> 無印=時間ズーム・Ctrl=Ampズーム・Shift=時間移動、という3操作完全分離の方式に変更した。
> 無印スクロールが常に時間ズーム専任になったため、両軸/片軸を切り替える設定自体が不要になり
> 削除した（`Settings.both_axis_scroll_zoom`フィールドごと削除、`TimePlotWidget`/
> `PlotGridWidget`のコンストラクタ引数からも削除）。

> **v3（2026-09-19、ユーザー要望）: どの修飾キーがどの動作を担うかを再び設定可能にしたが、
> v1の設定とは別物。** v1の設定は「無印スクロールで両軸ズームするか、X軸のみズームするか」
> という**方式そのもの**を切り替えるものであり、どちらの方式を選んでも実際の使用感が悪いと
> 判明したために廃止された。今回のものは種類が異なる: 3操作の完全分離という固定v2方式は
> そのまま維持し（どの修飾キーも2つ以上の動作を兼ねず、どの動作も常にちょうど1つの修飾キーで
> 到達可能）、**どの修飾キーがどの動作を担うか**だけを動かせるようにした。
> `Settings.scroll_bindings: dict[str, str]`（`io/settings.py`）は`{"none", "ctrl",
> "shift"}`から`{"x_zoom", "y_zoom", "pan"}`への全単射。当初はv2方式
> （`{"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}`）を既定値としたが、同日中に
> 新設定の下で実際に使ってみた結果を受けて変更した: ユーザー自身の
> `cfg/parquet_analyzer/settings.json`から実際の設定値
> （`{"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"}`）を直接読み取り、それを新しい
> `DEFAULT_SCROLL_BINDINGS`とした——「既に自分で調整済みの設定を、憶測ではなく新規
> インストール時の既定値にしてほしい」という明示的な要望による。
> `normalize_scroll_bindings()`は、完全な全単射でなければ（修飾キーの欠落、動作の重複/
> 未知の値など）部分的な修正を試みず丸ごと既定値にフォールバックする——中途半端に壊れた
> マッピングは、ある動作がスクロールで一切到達不能になる恐れがあり、`TimePlotWidget.
> wheelEvent`がその場でそれを検出・回復する手段を持たないため。`TimePlotWidget.
> scroll_bindings`（`__init__`で正規化、`set_scroll_bindings()`で実行時変更も可能）を
> `wheelEvent`内で参照し、従来の`if shift: ... elif ctrl: ...`という分岐を置き換えた
> （両方の修飾キーが同時に押された場合にshiftをctrlより優先する仕様は変更なし）。
> `PlotGridWidget`（コンストラクタ＋`set_scroll_bindings()`。後から追加されたプロットにも
> 適用される）・`MainWindow`（コンストラクタ引数、`apply_settings()`）へは
> `downsample_pixel_ratio`と同じ経路で伝播させた。`SettingsDialog`には修飾キーごとに
> `QComboBox`を1つずつ配置（各コンボは3動作全てを選択肢として持つ）。既に他の修飾キーに
> 割り当て済みの動作を選ぶと、2つのコンボが自動的に入れ替わるようにし、2つの修飾キーが
> 同じ動作に割り当てられたまま残ることがないようにした。テスト:
> `test_normalize_scroll_bindings_accepts_a_valid_permutation`、
> `test_normalize_scroll_bindings_falls_back_to_default_on_anything_invalid`、
> `test_load_settings_falls_back_when_scroll_bindings_is_corrupt`
> （`tests/parquet_analyzer/test_io.py`）、
> `test_custom_scroll_bindings_swap_which_modifier_zooms_which_axis`、
> `test_set_scroll_bindings_updates_behavior_live`
> （`tests/parquet_analyzer/test_plot_widget.py`）、
> `test_settings_dialog_scroll_binding_combo_swaps_the_conflicting_modifier`、および
> 既存の`test_apply_settings_propagates_to_plots_and_actions`の拡張
> （`tests/parquet_analyzer/test_settings_dialog.py`）。

#### 4.1 時刻軸の動的フォーマット

- X軸（時刻）の目盛表示は、ズームレベルに応じて自動的に粒度を変える。広い範囲を表示している
  ときは日付/月単位、数秒〜数分まで拡大すると時:分:秒（ミリ秒）単位まで自動で切り替わる。
  例: 4年スパンでは「2月」「3月」のような月表示、3秒スパンでは「50.250」のような秒以下表示。
- 実装は pyqtgraph 標準の `DateAxisItem` を `TimePlotWidget`/`NavigatorWidget` の下軸に使う
  （`pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})`）。X軸データがエポック秒
  （`float`）であることを前提とする（`MainWindow._to_numeric`で変換済み）。
- 周波数解析（FFT）プロットのX軸は時刻ではなく周波数[Hz]のため、`DateAxisItem`を使わない
  （`TimePlotWidget(..., x_axis_datetime=False)`、`plot.setLabel("bottom", "Frequency",
  units="Hz")`）。`PlotGridWidget.add_plot(x_axis_datetime=...)`で切り替える。
  X軸の意味が異なる（時刻 vs 周波数）ため、`x_axis_datetime=False`のプロットは
  時刻系のプロットとX軸リンクしない（`PlotGridWidget.add_plot`内で判定）。

#### 4.2 時間軸（X軸）に使う列の選択

- どの列を時間軸として使うかは、既定では**先頭列**（読み込んだParquetファイルの最初の列）。
  以前は列名が`time`/`timestamp`/`datetime`のいずれかに一致する列を優先的に探すヒューリスティック
  だったが、ユーザーから明示的に選べる手段がなく実質固定だったため、
  「基本的には第一列が時間でいい、ただし自由に変更できるように」という要望を受けて
  「既定は先頭列＋ツールバーから自由変更」方式に変更した（ヒューリスティックは削除）。
- ツールバーの「時間軸:」コンボボックス（`MainWindow.time_axis_combo`）に読み込んだファイルの
  全列名が表示され、選択を変えると`MainWindow.set_time_column(name)`が呼ばれる。
- `MainWindow._file_columns`が読み込んだファイルの全列（数値変換済み）を保持し続け、
  `_raw_variables`は`_file_columns`から現在の時間軸列を除いたものとして都度再構築する。
  そのため時間軸を切り替えても元の時間軸列は消えず、変数パネルに通常の変数として再登場する
  （演算変数のキャッシュ済み値は`_derived`に定義式が残っているのでそのまま引き継がれる）。
- 切り替え時、`PlotGridWidget.set_time_axis_data`が時刻系（`x_axis_datetime=True`）の全プロット
  の各系列のX配列を新しい時間軸データに差し替え、`go_home()`で表示範囲を再フィットする
  （列によってスケール・範囲がまったく異なりうるため、旧ズーム範囲を維持する意味がない）。
  周波数解析プロット（X軸がHz）はこの対象外。
- ビュー（`view.py`）のスキーマは時間軸列の選択を保存しない（常にファイルの先頭列が既定になる）。
  将来的にビューごとに記憶したくなった場合は`View`に`time_column`フィールドを追加する。

### 5. マルチプロット / 重ね合わせ操作（11.7）

- `VariablePanel` 上の変数を対象プロットへドラッグ&ドロップすると、そのプロットに系列として
  追加される（重ね合わせ）。
- プロット右クリックメニュー:
  - 「上に移動」「下に移動」（縦の並び順を入れ替える、2026-09-17追加。
    `PlotGridWidget.move_plot_up`/`move_plot_down`。境界（最上段で上に移動、
    最下段で下に移動）では何もしない）
  - 「このプロットから削除」（`PlotGridWidget.remove_plot` — プロット段そのものを削除する。
    複数系列を重ねていた場合もまとめて削除される。「－」ボタンはツールバーに存在せず、
    プロット削除の唯一の経路はこの右クリックメニュー）
  - 「新規プロットとして分離」（選択系列以外を新しいプロットに移す）
- ツールバーの「＋」ボタンで空のプロット段を末尾に追加する。
- **並び替え・削除後のX軸連動の再構成**: 新しい時間軸プロットは常に「現在の最上段の
  時間軸プロット」にX軸リンクされる（星型トポロジー）。上に移動/下に移動でプロットの
  順序が変わると`plots[0]`（＝リンク先の基準）自体が別ウィジェットになるため、
  `PlotGridWidget._relink_anchor()`で毎回、現在の`plots[0]`を基準に全時間軸プロットの
  リンクを再設定する（以前は削除時のみ`was_anchor`判定で個別対応していたが、並び替え機能の
  追加に合わせて削除・並び替えの両方から呼ばれる共通処理として切り出した）。
- **ナビゲータの概要波形は`plots[0]`の最初の系列に連動する**
  （`MainWindow._refresh_navigator_overview()`、2026-09-17追加）。以前は
  `next(iter(self._raw_variables.values()))`でファイルの列順に基づく先頭の変数を
  無条件に表示していたが、実際にユーザーが見ているものと無関係だった。
  `plots[0].series_names()`が空（まだ何も重ねられていない）の場合のみ、その旧来の
  フォールバックを使う。呼び出し箇所: `load_parquet`／`set_time_column`／
  変数のドラッグ&ドロップ時（対象が`plots[0]`の場合のみ）／`load_view`（`finally`節で
  成功・部分失敗を問わず必ず1回）／`PlotGridWidget.layoutChanged`シグナル
  （プロット削除・並び替え時に発火）。
  - **注意点（実装時に発覚した実バグ）**: `plots[0]`の最初の系列の値は
    `MainWindow.variable_values(name)`（`_raw_variables`/`_derived`からの名前解決——
    このメソッドは13.5.1 Phase Cで公開されるまで`_variable_array`という名前だった）
    ではなく、`plot.series_data(name)`（プロット自体が保持する配列）から取得する。
    理由: ユーザーが「現在時間軸として使っていない列」を系列として重ねた後、その列を
    新しい時間軸に切り替えると、その列名は`_raw_variables`から除外される（時間軸自体に
    なるため）。系列としては引き続き有効に描画され続けるが、`variable_values`で名前解決
    しようとすると`ExpressionError: unknown variable`になる
    （`tests/parquet_analyzer/test_load_robustness.py`の
    `test_set_time_column_refits_an_already_plotted_series_to_the_new_x_data`相当の状況で発覚）。

### 6. 演算式入力（11.4）

- `ExpressionBar` にPython風の式をテキストで直接入力する（例: `A + B`, `abs(A) - sqrt(B)`,
  `rolling_mean(A, 20) ** 2`）。
- `VariablePanel` の変数をダブルクリックすると、カーソル位置に変数名が挿入される（入力補助）。
- 評価は `ast.parse` でASTを検証し、許可した演算子・関数・既知の変数名のみを許すホワイトリスト
  方式で行う（生の `eval` は使わない）。目的は悪意ある入力対策ではなく、誤入力による
  クラッシュや意図しない副作用を防ぐこと。
- **入力文字列はパース前に`unicodedata.normalize("NFKC", ...)`で正規化する**（2026-09-17
  追加。実際に発生した不具合: 日本語IMEの全角入力モードで`+`が`＋`(U+FF0B)に変換され、
  `ast.parse`が`invalid character '＋' (U+FF0B)`という生のPython構文エラーをそのまま
  `ExpressionError`経由でユーザーに表示していた）。NFKC正規化は全角英数字・全角記号
  （`＋－＊／（）`等）・全角スペース(U+3000)を対応する半角文字に変換するため、IMEの
  変換に気づかず入力した場合でも解釈できる。
  - 演算子: `+ - * / ** % //`（べき乗・剰余・切り捨て除算も対応。単項の`+ -`も可）。
  - 関数（すべて要素ごとの演算。`_ALLOWED_FUNCS`に`{名前: (期待する引数個数, 実装)}`の形で
    登録）:
    | 分類 | 関数 | 引数 |
    |---|---|---|
    | 基本 | `abs`, `sqrt` | 1 |
    | 解析 | `delta`（1階差分）, `rolling_mean`（移動平均、第2引数はウィンドウ幅） | 1 / 2 |
    | 三角関数 | `sin`, `cos`, `tan` | 1 |
    | 指数・対数 | `exp`, `log`（自然対数）, `log10` | 1 |
    | 丸め | `sign`, `floor`, `ceil`, `round` | 1 |
    | 比較・範囲 | `min`, `max`（要素ごとの比較。Python組み込みの縮約min/maxとは異なる）,
      `clip`（値, 下限, 上限） | 2 / 2 / 3 |
  - `min`/`max`/`clip`は**要素ごと**の演算である点に注意（例: `max(a, 0)`はaの各サンプルを
    0未満なら0にクランプする。単一のスカラー値を返す集約関数ではない — 派生変数は
    時間軸と同じ長さの配列である必要があるため）。
  - `rolling_mean(a, window)`は畳み込み（`np.convolve`, `mode="same"`）で実装した移動平均。
    端では実際に重なった範囲だけで平均する（NaNパディングしない）ので系列全体がそのまま
    プロットできる（`core/analysis.py`）。
- **関数は引数個数も検証する**。`abs`/`sqrt`はnumpyのufuncであり2引数目は`out=`（書き込み先
  バッファ）として解釈されるため、引数個数を検証せず`sqrt(a, b)`のような式を素通りさせると
  `b`の実データを無言で書き換えてしまう（実際に発生した不具合。レビューで発見・修正。
  新しく追加した関数群にも同じ検証を適用済み）。
- 列名がPythonの識別子として不正な場合（空白・記号を含む列名など）、式中でその変数名を
  そのまま参照できない（`ast.Name`として解釈できないため）。現時点で列名のエイリアス機能は無い
  （[13.4章](#134-演算式の既知の制限)）。
- **演算変数名が既存の列名と衝突する場合はエラーにする**（`MainWindow._on_add_derived_variable`）。
  以前は無言で上書きしていた（レビューで発見・修正）。同名の演算変数を式だけ変えて再定義する
  ことは許可している（意図的な再定義とみなす）。
- 生成された仮想変数は通常の変数と同様に `VariablePanel` に表示され、プロット・保存が可能
  （`specification.md` 5.5参照）。

### 7. データソース選択とフォルダ履歴

- メニュー/ツールバーの「開く」からフォルダ選択ダイアログを開き、Parquetファイルを選ぶ。
- ダイアログの初期表示フォルダは**直近に参照したフォルダ**（`last_data_folder`）。
- 直近参照フォルダは**最大5件**のMRU（Most Recently Used）リストとして保持し
  (`recent_data_folders`)、ダイアログ内のクイック候補として表示する。
  - 新しいフォルダを参照するたびに先頭へ追加し、重複は除去、5件を超えた分は古い順に破棄する。
  - この5件はドロップダウン等で直接クリックして即座に切り替えられるようにする。
- 保存先は `cfg/parquet_analyzer/settings.json`（[9章](#9-設定ファイル-cfgparquet_analyzersettingsjson)）。

### 8. View JSONスキーマ

`data/parquet_analyzer/<view_name>.json` に保存する形式の例。
（`source.parquet_path` は実際に用意したテストデータ `data/raw/sensor_log_4y.parquet`
[4年分、16時間おきに10ms間隔×1分間のバーストを記録、約1,315万行] を指している）:

```json
{
  "schema_version": "1",
  "view_name": "sensor_overview",
  "source": {
    "parquet_path": "../raw/sensor_log_4y.parquet",
    "path_type": "relative"
  },
  "derived_variables": [
    { "name": "rpm_delta", "expression": "delta(rpm)" },
    { "name": "temp_pressure_ratio", "expression": "temperature / pressure" }
  ],
  "plots": [
    {
      "plot_id": "p1",
      "series": [
        { "variable": "rpm", "color": "#1f77b4" },
        { "variable": "rpm_delta", "color": "#ff7f0e" }
      ],
      "y_axis_range": [null, null]
    },
    {
      "plot_id": "p2",
      "series": [
        { "variable": "temperature", "color": "#2ca02c" },
        { "variable": "vibration", "color": "#d62728" }
      ],
      "y_axis_range": [null, null]
    }
  ],
  "x_axis_range": [0, 6000],
  "downsample": { "enabled": true }
}
```

- `derived_variables` は計算結果ではなく式そのものを保存する（`specification.md` 5.5参照）。
- `source.path_type` が `relative` の場合、`view_name.json` からの相対パスとして解決する
  （`data/parquet_analyzer/sensor_overview.json` から見て `../raw/sensor_log_4y.parquet`）。
  ただし `MainWindow.save_view_dialog` は現状常に `path_type="absolute"` で保存するため、
  `relative` 分岐は`io/view.py`のロジックとしては実装済みだが、UIから実際に使われる経路が
  まだ無い（[13章](#13-本書時点での未確定事項残タスク)）。
- `y_axis_range`/`x_axis_range` は、保存時点でプロットが実際に表示していたY/X範囲を
  `save_view_dialog`が読み取って書き込み、`load_view`が復元時に適用する。以前はスキーマに
  フィールドはあったが実際には書き込まれず・読み込まれずのまま放置されていた（"ビュー保存"を
  しても表示中のズーム/パンが保存されない不具合。レビューで発見・修正）。
- `view_name` はファイル名にそのまま使われるため、`Path(view_name).name`と一致しない名前
  （`/`区切りを含む、`..`、空文字など）は`io/view.view_path()`が`ValueError`を投げて拒否する。
  以前は素通りしており、先頭が`/`のview名で保存先ディレクトリの外に書き込まれる不具合があった
  （レビューで発見・修正）。
- **`source.parquet_path`が指す元ファイルとは別のファイルが既に開かれている場合、
  `load_view`はそのファイルを開き直さず、現在開いているファイルに対してプロット配置
  （変数の重ね合わせ・色・軸範囲・演算変数の式）だけを適用する**（2026-09-17変更、
  `MainWindow.load_view`）。同じ列名を持つ複数のファイルに同一レイアウトを使い回す
  ユースケースを想定。現在何も開いていない場合、または現在開いているファイルが
  `source.parquet_path`と（絶対パス比較で）一致する場合は、従来通り`load_parquet`で
  読み込む。レイアウトのみを適用する場合も`_derived`（演算変数）は一旦クリアしてから
  ビューの`derived_variables`を再適用する（別ファイルに切り替える前の演算変数が
  `_raw_variables`に残っていると、ビュー側の同名の演算変数が「既存列と衝突」と誤判定される
  ため）。
- **現在開いているファイルにビューの演算変数・系列を適用する処理が、これまでは
  all-or-nothing だった: 最初に見つからない変数が1つあるだけで、以降の演算変数・系列・
  プロットが全て中断されていた**（2026-09-19発見・修正、`MainWindow.load_view`）。
  例: プロット1=`x`、プロット2=`y`、演算変数`z = x + 1`のビューを、`y`はあるが`x`は無い
  ファイルに対して使い回すと、`z`の評価（ソースの`x`が無い）で`ExpressionError`が
  再構築処理全体から外へ伝播し、本来正常に適用できたはずのプロット2の`y`系列にすら
  到達せず、結果として**何も表示されない**という不具合があった。これはレイアウト
  使い回し機能自体が謳う目的（「同じ列名を持つ複数のファイルに同一レイアウトを使い回す」）
  と直接矛盾する——2つのファイルが列を完全一致で共有することは稀で、部分的な一致を
  許容できて初めて意味を持つ機能のため。修正: 再構築処理全体を1つの`try`で囲むのではなく、
  演算変数・系列それぞれを個別に`try`/`except (ExpressionError, KeyError)`で囲み、
  適用できなかったものを集約して最後に1回だけ警告する形にした。系列が1つも適用できず
  空になったプロットは（`plots[0]`を除き——グリッドは常に最低1プロットを保持するため）
  空のまま残さず削除する。検証: 演算変数のソース列が新しく開いたファイルに実在する場合、
  ビューの保存元ファイルの値のまま固定されず、新しいファイル自身のデータから正しく
  再計算されることも確認済み。回帰テスト:
  `test_load_view_applies_matching_variables_even_when_others_are_missing`、
  `test_load_view_recomputes_derived_variables_from_the_newly_opened_file`
  （`tests/parquet_analyzer/test_load_robustness.py`）。
- **`load_view`は、現在開いているファイルがまさにビュー自身が記録している元ファイルで
  あった場合にも、何も開かれていない場合と同様に`load_parquet`（ディスクからの
  全体再読み込み）を呼んでいた**（2026-09-19発見・修正、`MainWindow.load_view`）。
  ユーザー本人からの指摘: 既に開いているファイルに対してそのファイル自身が保存元の
  ビューを適用するだけなら、開き直す理由が無い——どのみちビューがプロット配置・演算変数を
  完全に置き換えるため、既に開いているファイル自体は何も変える必要が無い。この無条件の
  開き直しは13.5.1のlazy/windowed列にとって特に無駄が大きかった: それまでに実体化済みの
  `LazyColumn`を全て破棄し、挙動上の違いを何ら生まないままファイル全体を最初から
  読み直していた。修正: 「別ファイルが既に開かれている」場合のレイアウトのみ適用パス
  （従来は`load_parquet`をスキップするのはこの経路だけだった）に「同じファイルが既に
  開かれている」場合も統合し、`self._data_source is None`のとき（=何も開いていない
  とき）だけ`load_parquet`を呼ぶようにした。回帰テスト:
  `test_load_view_does_not_reopen_a_file_already_open`（`monkeypatch`で
  `load_parquet`が一切呼ばれないことと、`_data_source`が新しい`ParquetDataSource`に
  差し替えられず同一オブジェクトのままであることを検証）。
- **現在開いているファイル名が確実に見える形になっていなかった**（2026-09-19追加、
  こちらもユーザー要望）。`MainWindow._set_title`は既にウィンドウタイトルへ反映していたが、
  タイトルバーは見落としやすい（ウィンドウ最大化時や一部のウィンドウマネージャ/リモート
  デスクトップでは非表示になる場合がある）ため、ステータスバーのラベルにも同じ内容を
  表示するようにした（`MainWindow._file_label`。右側の進行状況/精度インジケータが使う
  `addPermanentWidget`ではなく`addWidget`で左寄せ配置）。`"ファイル: <名前>"`を表示し、
  ツールチップに解決済みのフルパスを持つ。ファイルが開かれていない間は空文字。回帰テスト:
  `test_opening_a_file_shows_its_name_in_the_title_and_status_bar`。

### 9. 設定ファイル (`cfg/parquet_analyzer/settings.json`)

```json
{
  "schema_version": "1",
  "downsample_pixel_ratio": 2,
  "auto_redraw": true,
  "eager_load_limit_mb": 512,
  "scroll_bindings": {"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"},
  "shortcuts": {
    "home": "H",
    "redraw": "R",
    "open_parquet": "",
    "add_plot": "",
    "fft": "",
    "stats": "",
    "save_view": "",
    "load_view": "",
    "toggle_downsample": ""
  },
  "window_geometry": null,
  "window_state": null,
  "last_data_folder": null,
  "recent_data_folders": [],
  "recent_files": [],
  "recent_views": [],
  "default_overlay": false
}
```

- `eager_load_limit_mb`: [13.5.1章](#1351-大容量ファイル4gbでのメモリ急増問題2026-09-17調査2026-09-19段階的対策実装済み)
  Phase E参照。
- `scroll_bindings`: [4章](#4-操作仕様確定113v2)のv3の経緯参照。
- `window_geometry` / `window_state`: [9.3章](#93-レイアウトの永続化)参照。
- `auto_redraw`: `false`にすると、パン/ズームで表示範囲が変わってもダウンサンプリング再計算を
  自動実行しない（Rボタン/ショートカットでの手動再描画のみ）。大量データで頻繁にパン/ズームする際、
  自動再計算が負荷になる場合の逃げ道。新規追加したデータの初回表示はこの設定に関わらず必ず行う
  （[ui/plot_widget.py](#2-モジュール構成) `add_series`）。
- `shortcuts`: アクションID→`QKeySequence`文字列（空文字は未割当）。[9.1章](#91-設定メニュー)の
  設定ダイアログから編集する。保存時に、当時存在しなかった新しいアクションIDは既定値で補完される
  （`io/settings.py` の `load_settings`）。
- `last_data_folder` / `recent_data_folders`: [7章](#7-データソース選択とフォルダ履歴)参照。
  `recent_data_folders` は要素数5を上限としたMRUリスト、`last_data_folder` は
  `recent_data_folders[0]` と一致させる（先頭を読めば済むが、明示フィールドとして持たせて
  読み手にとっての意図を明確にする）。

#### 9.1 設定メニュー

- ツールバーの「設定」ボタンから `SettingsDialog`（`ui/settings_dialog.py`）を開く。
- 編集項目: 自動再描画(`auto_redraw`)、ダウンサンプリング密度(`downsample_pixel_ratio`)、
  大容量列のしきい値(`eager_load_limit_mb`)、スクロールホイールの修飾キー割当
  (`scroll_bindings`)、各アクションのショートカット(`QKeySequenceEdit`)、UI言語
  (`language`、2026-09-19追加、ユーザー要望。詳細は後述)。
- OKで確定すると `MainWindow.apply_settings()` が新しい`Settings`を`PlotGridWidget`・各
  `QAction`のショートカットへ反映し、`cfg/parquet_analyzer/settings.json`に保存する。
  キャンセル時は何も変更しない（ダイアログはコピー上で編集し、確定時にのみ適用する設計）。
- **UI言語**（`Settings.language`、`"en"`または`"ja"`、新規インストール時の既定値は
  `"en"`）: `ui/i18n.py`が全UI文字列を`{key: {"en": ..., "ja": ...}}`形式の辞書として保持し、
  `tr(key, **kwargs)`（str.format形式のプレースホルダ対応）と`set_language()`/
  `get_language()`のモジュールレベル状態を提供する — Qt標準の`QTranslator`/`.ts`/`.qm`
  ツールチェーンではなく、小さな自前の辞書ルックアップとした（このアプリのUI文字列量では
  `pyside6-lupdate`/`lrelease`をビルド依存に加えるほどではないと判断）。
  `MainWindow.__init__`は設定読み込み直後、`tr()`を呼ぶ最初のウィジェット構築より前に
  `set_language(self.settings.language)`を呼ぶ。設定ダイアログで言語を変更しても、
  反映はアプリの次回起動時のみ（コンボのツールチップ`settings.language_restart_note`が
  その旨を示す）——既存ウィジェットの表示テキストをその場で再翻訳する仕組みは意図的に持たない
  （全`_build_*`メソッドの再実行や`Qt.LanguageChange`イベントの配線は、めったに切り替えない
  設定のために見合う複雑さではないため）。旧`io/settings.py`にあった表示用ラベル辞書
  (`SHORTCUT_LABELS`、`SCROLL_ACTION_LABELS`、`SCROLL_MODIFIER_LABELS`)は
  `ui/i18n.py`側の翻訳キー(`shortcut.*`、`scroll_action.*`、`scroll_modifier.*`)に移動し、
  `io/settings.py`にはID(`DEFAULT_SHORTCUTS`/`SCROLL_ACTIONS`/`SCROLL_MODIFIERS`)のみが
  残る — io/がUI文字列を持たない、という既存方針に合わせたもの。`core/expression.py`の
  `ExpressionError`のメッセージ（構文エラーや許可されていない関数名など、内部/診断用の文言）は
  `language`設定に関わらず意図的に英語のまま残す（翻訳対象のUI文字列テーブルには含めない）。

#### 9.2 描画中インジケータ

- `TimePlotWidget`はダウンサンプリング再計算のワーカーを開始/終了するたびに
  `downsampleStarted`/`downsampleFinished`を発する。`PlotGridWidget`が全プロット分の
  発生数を集計し、実行中ジョブが0→1になった時に`busyChanged(True)`、1→0で`busyChanged(False)`
  を発する。
- `MainWindow`はステータスバーに不定進捗（マーキー表示）の`QProgressBar`を持ち、
  `busyChanged`に応じて表示/非表示を切り替える。個々のLTTB計算は進捗率を報告しないため、
  パーセンテージ表示ではなく「実行中かどうか」の表示に留める。UIスレッドをブロックしない
  非同期処理（[10章](#10-ダウンサンプリング再計算の非同期化確定)）に対する視覚的フィードバック。

#### 9.3 レイアウトの永続化

- `QMainWindow.saveGeometry()` / `saveState()` の返す `QByteArray` をbase64文字列化して
  `window_geometry` / `window_state` に保存する（JSONはバイナリを直接持てないため）。
  `saveState()` はドック/ツールバーの配置・サイズを、`saveGeometry()` はウィンドウ自体の
  位置・サイズを保持する。
- `saveState()`/`restoreState()` が正しく機能するには、対象の `QDockWidget`/`QToolBar` に
  一意な `objectName` が設定されている必要がある（Qtの要件）。
  `MainWindow._variable_dock`（`"variable_dock"`）とメインツールバー（`"main_toolbar"`）に
  明示的に設定している。
- 変数パネルの既定配置は画面**左側**（`Qt.DockWidgetArea.LeftDockWidgetArea`）。
- 起動シーケンス: `_build_layout()`/`_build_actions()`で既定レイアウトを構築した直後、
  `self._default_geometry`/`self._default_state` として**保存前の状態**を控えてから
  `_restore_layout()`で保存済み設定を復元する（`window_geometry`/`window_state`が
  `None`なら何もしない＝既定のまま）。
- ツールバーの「レイアウトをリセット」（`MainWindow.reset_layout()`）は、控えておいた
  `_default_geometry`/`_default_state`に戻し、`window_geometry`/`window_state`を
  `None`にクリアして保存する。
- 保存タイミングは `closeEvent`（`MainWindow._save_layout()`）。ウィンドウを閉じるたびに
  現在のレイアウトを`settings.json`へ書き戻す。

### 10. ダウンサンプリング再計算の非同期化（確定）

- ズーム/パンのたびにLTTB再計算をメインスレッドで行うと、大量データ時にUI操作がブロックされ
  `specification.md` 9章の非機能要件（数秒以内にUI操作が反映されること）を満たせないおそれがある。
  そのためダウンサンプリング再計算は `QThreadPool`（`QRunnable` の `BackgroundWorker`、
  10.2章参照）上で非同期に実行する方式に確定した。
- 表示範囲が変わるたびに新しいジョブを投げるが、**系列ごとの世代番号（generation counter）**を
  各ジョブに持たせ、古い世代の結果が後から返ってきても破棄する（連続ズーム操作時に古い再計算
  結果でプロットが上書きされるのを防ぐ）。世代番号は `TimePlotWidget._series[name]["generation"]`
  として**系列単位**で持つ。プロット全体で1つのカウンタを共有する実装だった時期があったが、
  2系列以上を重ね合わせたプロットで再描画のたびに複数系列分カウンタが進むため、最後に処理された
  系列以外は常に「古い」と誤判定されて結果が破棄される不具合があった（重ね合わせ表示という
  コア機能が実質壊れていた。レビューで発見・修正）。
- ワーカーの完了通知はQtシグナル（スレッドセーフ）でUIスレッドへ返し、`TimePlotWidget` が
  受け取って描画を更新する。
- `PlotGridWidget.remove_plot`は、対象プロットに`pending_downsample_count() > 0`（ジョブが
  ワーカースレッドで実行中）の間は`deleteLater()`を呼ばず、`downsampleFinished`シグナルで
  ジョブが0件になったのを確認してから削除する。プロットをすぐに`deleteLater()`すると、
  ワーカーからのキュー付きシグナルが（削除済みの）ウィジェットに届いた時にクラッシュする
  リスクがあるため（`DownsampleWorker`自体のライフタイム対策と同種の対処。[9.2章](#92-描画中インジケータ)の
  ジョブカウントもこの仕組みに依存する）。
- 「まず素朴にメインスレッドで実装し、体感で遅ければスレッド化する」のではなく最初から
  非同期化する判断とした理由: LTTBの計算量はデータ点数に比例するため、大量データ
  （テストデータで約1,315万行）でメインスレッド実行するとズーム操作のたびにUIがフリーズする
  リスクが高く、後から非同期化に作り替えるコストよりも最初から非同期設計にするコストの方が
  低いため。

#### 10.1 LTTBのNaN対応と性能

- `core/downsample.py`の`lttb()`は、バケット内にNaNが1つでもあると素朴な`np.mean`では
  そのバケットの候補点の面積計算が丸ごとNaNになり、実際のピーク/谷が選ばれず先頭点固定に
  近い挙動へ劣化する不具合があった（センサーログでは欠損値が珍しくないため実害があった。
  レビューで発見・修正）。
- 対処として`np.nanmean`＋`np.nan_to_num(..., nan=-np.inf)`でNaNを避けるようにしたが、
  `np.nanmean`は`np.mean`よりおよそ5倍遅く、これを**バケットの数だけ**（プロット幅×
  `downsample_pixel_ratio`個、典型的に2000超）毎回呼ぶと、13M行→2400点の変換が
  約37.5ms→約104msまで悪化した（再描画が遅くなったという実機フィードバックで発覚）。
- 最終的な対処: 呼び出しの先頭で一度だけ`np.isfinite(x).all() and np.isfinite(y).all()`を
  チェックし（13M要素で約2ms）、NaN/Infが実際に含まれる場合のみ`np.nanmean`を使う
  （含まれなければ元の`np.mean`のまま）。NaN無しデータでのfast pathは元の実装とビット単位で
  同一の出力になることを確認済み。実際の利用シーンではNaNは稀なので、ほとんどの再描画は
  性能劣化なしでNaN安全性を得られる。

#### 10.2 BackgroundWorkerへの統合とスレッドプールの分離（2026-09-18）

FFT（13.5.1章）・統計（14章）を追加していく過程で、`DownsampleWorker`・`FFTWorker`・
`StatsWorker`という3つの`QRunnable`サブクラスが、いずれも「`setAutoDelete(False)`＋
呼び出し元が`finished`処理完了まで強参照を保持する」という同一の安全パターンをほぼ丸ごと
複製していることがコードレビューで指摘された（このパターン自体は10章の理由により必須で、
省略するとクラッシュしうる）。

- `core/background_worker.py`の`BackgroundWorker`に統合。コンストラクタに任意の
  `Callable[[], object]`を渡すと、`run()`がそれをワーカースレッドで呼び出し、戻り値を
  そのまま`signals.finished`（`Signal(object)`）で返す。ダウンサンプリングの世代番号
  （stale判定用）は`BackgroundWorker`自体の責務ではなく、呼び出し元（`plot_widget.py`）が
  渡すクロージャの戻り値タプルに含め、受け取り側でチェックする形に変更した
  （`DownsampleWorker`が専用の`generation`コンストラクタ引数を持っていたのをやめ、
  一般化した）。
- `FFTWorker`/`StatsWorker`は削除し、呼び出し元（`MainWindow`）で
  `BackgroundWorker(lambda: fft(x, y))`のように直接組み立てる形にした。
- **分析系ジョブ（FFT・統計）とパン/ズームの再描画ジョブは別の`QThreadPool`に分離**した
  （`MainWindow._analysis_thread_pool`、`plot_widget.py`は従来通り
  `QThreadPool.globalInstance()`）。統計は選択した変数の数だけワーカーを一括投入するため
  （14章）、これらが`QThreadPool.globalInstance()`を共有していると、大量データに対する
  複数変数の統計計算がパン/ズーム時のダウンサンプリング再計算用スレッドを一時的に
  占有し、操作中の応答性を落としうるという指摘（コードレビュー）を受けて分離した。

### 11. ログ仕様

- 標準 `logging` モジュールを使用。
- 出力先: `log/parquet_analyzer/parquet_analyzer_{YYYYMMDD}.log`（日次ローテーション）。
- 平常時は `INFO`、例外発生時は `ERROR` でスタックトレースを記録する。

#### 11.1 デバッグモード起動と操作ログ

- 起動時に `--debug` フラグ（または環境変数 `PARQUET_ANALYZER_DEBUG=1`）を指定するとデバッグ
  モードになる。`tools/run_debug.sh` / `run_debug.bat` はこのフラグ付きで起動する専用ランチャー。
- デバッグモードでは:
  - メインログのレベルが `DEBUG` になる。
  - ウィンドウタイトル末尾に `[DEBUG]` が付く（現在どちらのモードで動いているか一目で分かるように）。
  - **操作ログ**（ファイルを開く、変数の重ね合わせ、演算変数追加、H/R、ズーム/パン、
    プロット追加/削除/分離、ビュー保存/読込、設定変更など、ユーザー操作のトレース）を
    `log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log` に書き出す（コンソールにもエコー）。
    メインログとは別ファイルに分離し、操作の頻度が高くてもアプリの通常ログを埋もれさせない。
- 実装は `core/oplog.py` の `log_op(action, **details)` を呼ぶだけの薄いラッパーで、
  デバッグモードでない場合は `logger.isEnabledFor(DEBUG)` が `False` になるため実質no-op
  （文字列整形コストも発生しない）。

### 12. エラーハンドリング方針

- Parquet読み込み失敗、不正な演算式、ビュー読み込み失敗はいずれもダイアログでユーザーに通知し
  ログに記録した上で、アプリを継続動作させる（クラッシュさせない）。
- **「アプリの起動そのものをブロックしてはいけない」処理は特に手厚く防御する**（設定ファイル・
  ウィンドウレイアウトはどちらも`MainWindow.__init__`の中で無条件に読み込まれるため、
  ここで例外が飛ぶとウィンドウが一枚も表示されないまま落ちる＝「起動しない」に見える）。
  - `io/settings.load_settings()`: JSON破損・型不正のいずれも例外を投げず既定値
    (`Settings()`) にフォールバックする。`save_settings()`も一時ファイル+`os.replace`で
    アトミックに書き込み、書き込み中のクラッシュで壊れたファイルが残らないようにしている。
  - `MainWindow._restore_layout()`: `window_geometry`/`window_state`の
    `base64.b64decode()`が失敗した場合（壊れたデータ、旧形式との非互換）は例外を握り
    つぶして既定レイアウトにフォールバックする。加えて、復元後のウィンドウ位置が
    どの画面（`QGuiApplication.screens()`）とも重ならない場合（例: 保存時に接続していた
    外部モニタが無い状態で起動）も既定位置に戻す（Qt自身の`restoreGeometry()`も極端な
    座標はある程度自動補正するが、その保険として）。
  - どちらも「起動できない状態で新しいバグ報告が来る」ことを防ぐのが目的で、実際に
    レビュー後の初回起動不能バグ（`_restore_layout`のエラー処理漏れ）から得た教訓。
- `load_parquet`は読み込みに必要な全処理（スキーマ読み取り、時刻列変換、非空列チェック）を
  ローカル変数の上で完了させてから`self.*`へ反映する。途中で失敗した場合、`self._data_source`
  等が新ファイルを指したまま古いデータが表示され続けるという中途半端な状態を防ぐため。
  成功/失敗を`bool`で返し、`load_view`はこれを見てから後続処理（演算変数の再構築・
  プロット復元）に進む。
- `load_view`はさらに、ビュー本体の読み込み（JSON・ソースパス解決）と、読み込んだデータを
  使ったプロットレイアウトの再構築を別の例外処理に分けている。後者（`ExpressionError`/
  `KeyError`。ビューが参照する列が、ソースParquetの再生成等で無くなっている場合など）が
  失敗しても、既に成功しているデータ読み込み自体は無効にしない
  （警告ダイアログを出しつつ、開けたデータはそのまま使える状態を保つ）。

### 13. 本書時点での未確定事項・残タスク

Opus・Fableによるコードレビュー（correctness/efficiency/simplification/test-coverage観点）で
見つかった項目のうち、**correctness（不具合）14件は全て修正済み**。
以下は未対応のまま残っている項目。

#### 13.1 調査中の不具合

- `tools/run.sh` / `run_debug.sh` から起動できないという報告があり、原因候補として
  (a) 設定ファイル破損時のクラッシュ、(b) ウィンドウ位置が画面外に復元される、の2つを
  特定・修正済み（[12章](#12-エラーハンドリング方針)）。ただし報告者の実環境での根本原因は
  未確認。実際のターミナル出力・エラーメッセージの提供待ち。

#### 13.2 効率面（未対応）

- ~~周波数解析（FFT、`MainWindow._on_fft_requested`）がUIスレッドで同期実行されている~~
  → 対応済み（2026-09-17、13.5.1章のStep 0）。`core/background_worker.py`の
  `BackgroundWorker`（`QRunnable`、`setAutoDelete(False)`＋呼び出し元での強参照パターン）
  でバックグラウンド実行するよう変更。同時に対象範囲も「選択変数の可視時間範囲」に変更
  （[specification.md 5.6](../specification.md#56-周波数解析)参照。ファイル全体を対象に
  しないことでメモリ問題も回避）。
- ~~`load_parquet`（`MainWindow`）がファイルの全列を`ParquetDataSource.read_columns()`で
  同期的に一括読み込みしている~~ → 対応済み（2026-09-19、13.5.1章 Phase A〜D）。時刻列のみ
  開いた時点で即座に読み込み、それ以外の列は`LazyColumn`（初回使用時に実体化）、
  `Settings.eager_load_limit_mb`を超える列は`WindowedColumn`（全体を実体化せず、常に
  表示中の行範囲だけをその都度取得）になった。
- ~~`TimePlotWidget._on_range_changed`はY軸のみの変化（Amp方向ズーム含む）でもダウンサンプリング
  再計算をキューに積んでおり、デバウンスが無い~~ → 対応済み（2026-09-17、13.5.1章のStep 0）。
  `TimePlotWidget._on_range_changed`がX範囲の変化を記録し、前回と同じX範囲（＝Y軸のみの変化）
  なら再計算をスキップ。X範囲が変わった場合も即座には再描画せず、120msのシングルショット
  `QTimer`（`_redraw_debounce`）を再スタートすることで連続する`sigRangeChanged`（ドラッグパン等）
  を1回にまとめる。ナビゲータ同期用の`rangeChanged`シグナルはデバウンスせず即時発火のまま
  （ナビゲータの選択範囲がビューの動きに遅れて追従するのは望ましくないため）。

#### 13.3 未使用・未配線のコード（要判断: 実装して活かすか、削除するか）

- `core/variable.py`の`Variable`データクラスがどこからもインスタンス化されていない
  （importのみ）。
- `PlotGridWidget.add_series_to_plot`がどこからも呼ばれていない。
- ~~`TimePlotWidget.set_downsample_enabled`/`PlotGridWidget.set_downsample_enabled`が
  UIのどこからも呼ばれておらず、ダウンサンプリング自体を無効化するUI操作が存在しない。
  `View.downsample_enabled`もJSONにシリアライズされるが、保存時に書き込まれず・
  読み込み時に適用されない（[8章](#8-view-jsonスキーマ)の`y_axis_range`/`x_axis_range`と
  同様の「スキーマにはあるが未配線」状態）。~~ -> **判断: 削除ではなく実装する**
  （2026-09-19、[13.5.1章 Phase E](#1351-大容量ファイル4gbでのメモリ急増問題2026-09-17調査2026-09-19段階的対策実装済み)）。
  ピラミッド（13.5.1章 Phase D）があっても結局不整合にはならなかった——`_redraw_series`は
  ダウンサンプリングが有効な場合にのみピラミッドを使う設計だったため、無効化しても
  `WindowedColumn`系列に対してすら明確な意味を持つ（「常に実データを取得し、一切簡略化しない」）。
  チェック可能なツールバーアクション（`toggle_downsample`）として実装し、
  `MainWindow._apply_downsample_enabled`経由で適用、`y_axis_range`/`x_axis_range`と同様に
  `save_view_dialog`/`load_view`で正しく往復するようにした。
- `Settings.recent_files`/`recent_views`フィールドが読み書きされていない
  （`recent_data_folders`のみ実際に使われている）。
- `SourceDef.path_type`の`"relative"`分岐は`io/view.py`側は実装済みだが、
  `save_view_dialog`が常に`"absolute"`で保存するため実際には到達しない。

#### 13.4 演算式の既知の制限

- ~~`**`（べき乗）、`%`（剰余）、`//`（切り捨て除算）が未対応~~ → 対応済み（2026-09-20、
  [6章](#6-演算式入力114)）。同時に`sin`/`cos`/`tan`/`exp`/`log`/`log10`/`sign`/`floor`/
  `ceil`/`round`/`min`/`max`/`clip`/`rolling_mean`も追加。
- 列名がPythonの識別子として不正な場合（空白・記号を含む列名等）、式中でその変数を
  参照できない。
- `round`は小数点以下の桁数を指定できない（`round(a)`のみ、1引数固定）。桁数指定が必要な
  場合は`round(a * 100) / 100`のように書く回避策がある。
- `rolling_mean`はウィンドウ幅を固定値としてしか指定できず、時間ベース（例: 「過去1秒」）の
  指定はできない（サンプル数指定のみ）。

#### 13.5 その他

- **周波数解析（FFT）機能を一時的にGUIから非表示にしている**（2026-09-19、
  ユーザー要望——「まだデバッグをしていないから」）。`io/settings.py`の
  `FFT_ENABLED = False`が`MainWindow._build_actions`のツールバーボタンと
  `SettingsDialog`の「fft」ショートカット欄の両方をゲートしており、それ以外は
  一切変更していない——`MainWindow._on_fft_requested`、`core/analysis.py`の
  `fft()`、`TimePlotWidget`/`PlotGridWidget`の`x_axis_datetime=False`（FFT
  プロット対応）はすべてそのままで、`tests/parquet_analyzer/test_fft.py`
  （ツールバー経由ではなく`_on_fft_requested()`を直接呼ぶ）・
  `test_windowed_loading.py`から引き続き実行・検証されている。デバッグが
  完了したら`FFT_ENABLED`を`True`に戻せば両方のUI導線が復活する。
  `DEFAULT_SHORTCUTS`/`SHORTCUT_LABELS`には引き続き`"fft"`エントリを残して
  あるため、設定スキーマのマイグレーションはどちらの方向でも不要。
- `polars` の `scan_parquet` を用いた行範囲（row group）単位の遅延読み込みの詳細設計
  （[13.2章](#132-効率面未対応)の`load_parquet`同期読み込み問題と合わせて検討）。

##### 13.5.1 大容量ファイル（~4GB）でのメモリ急増問題（2026-09-17調査、2026-09-19段階的対策実装済み）

約4GBのParquetファイル（`data/raw/sensor_log_4y_4gb.parquet`、91,980,000行×6列）を
開くとメモリ使用量が急増し操作不能になるという報告を受けて
調査。原因は[13.2章](#132-効率面未対応)で指摘済みの`load_parquet`同期一括読み込みそのもので、
`ParquetDataSource`自体は`row_count()`/`read_columns(row_start, row_end)`という遅延読み込み
APIを既に持っているにもかかわらず、`MainWindow.load_parquet`が範囲指定なしで
`read_columns(columns)`を呼び、全列・全行を一度に読み込んでいる
（`src/parquet_analyzer/ui/main_window.py`の`load_parquet`）。

最初の調査ではここを「polarsの`collect()`→`to_numpy()`→`_to_numeric`の複数回コピーが
CPU/メモリを圧迫している」と書いたが、Opus・Fableそれぞれに独立で設計させて実測させたところ
**この理解は誤りだった**（両者とも同じ結論に到達）。`pl.Series.to_numpy()`はfloat64列では
**ゼロコピー**（Arrowバッファ上のview。`flags.writeable == False`、`base is not None`で確認済み）
であり、読み込み自体も高速（92M行1列の`collect().to_numpy()`は実測0.25秒）。問題は**コピー回数
ではなく、6列×91,980,000行分の配列が読み込み後もずっとレジデントであること**
（float64 6列で4.42GB、time列がint64/datetimeで`_to_numeric`が実コピーを1回発生させる分
+736MB、合計ピークで5GB超）。また`slice()`のpushdownは実際に効いており、4GBファイルの中間から
2,000,000行×2列を読むのに実測14msしかかからない——つまり「可視範囲だけ都度ディスクから読む」
という方針はレイテンシ上十分に成立する、というのがこの再調査で最も重要な実測結果。

###### 採用する設計（Opus案をベースに、Fable案の指摘を統合）

過去に書いていた段階A/B/Cの区切りは以下の理由で置き換える：段階Aが「時間軸列だけ全読み込み」
としていた部分は、Parquetフッタのrow group統計（min/max、フッタ読み込みだけで取得可能・実測6ms）
で代替できるため不要（736MBの読み込みをゼロにできる）。段階Bが「本質的解決」としていた
可視範囲読み込みは実測で妥当性を確認したが、ズームアウト時（可視範囲＝全体）に読み込みコストが
全列読み込みに退化するケースへの対策（バックグラウンドで作る粗いmin/maxピラミッド）が
段階A/B/Cのどこにも無かった。段階Cの「要判断」だった項目は両モデルとも「保留にする理由がない」
と判断し、それぞれ具体的な結論を出している。

新しいステップ分割：

- **Step 0（低リスク、データモデル変更なし。単独で先に着手可能）**
  - `TimePlotWidget._on_range_changed`のデバウンス実装（[13.2章](#132-効率面未対応)で
    既知の欠落。Y軸のみの変化では再計算しない）。以下のStepでは「パン/ズームごとに
    ディスクへ再クエリ」が前提になるため、これはStep 1以降の**前提条件**であり後回しにできない。
  - FFT（`_on_fft_requested`）を`QRunnable`化してUIスレッドから外す（[13.2章](#132-効率面未対応)
    で既知）。
  - `data/raw/sensor_log_4y_4gb.parquet`生成スクリプトの不具合修正（後述）。
- **Step 1（`ParquetDataSource`の拡張のみ。UIからは未使用なので絶対に既存動作を壊さない）**
  `row_groups()`/`column_stats()`（フッタのrow group統計）、`column_bounds()`、
  `row_range_for_x()`（統計を二分探索してブロック境界にスナップ）、ブロック単位read＋LRUキャッシュ、
  `core/pyramid.py`（列ごとに8192行バケットのmin/maxピラミッドをバックグラウンドで構築、
  4GBファイルで1列あたり約270KB）。
- **Step 2（`Column`/`SeriesSource`抽象の導入。ロード方式は変えず配線だけ差し替える）**
  `core/column.py`に`MaterializedColumn`/`LazyColumn`/`DerivedColumn`と`SeriesSource`を追加。
  `TimePlotWidget._series`の保持形式を`{"x": ndarray, "y": ndarray}`から`SeriesSource`に、
  `_raw_variables`/`_file_columns`を`Column`に変更。`add_series(name, x, y, color)`は
  シグネチャ互換のまま`MaterializedColumn`でラップし、既存呼び出し元・既存テストは無改修で
  通る設計にする（`series_data()`は`series_color()`と`series_source()`に分割）。
  この段階では`load_parquet`は変わらず全列即時読み込みのままなので**メモリ問題はまだ解決しない**
  が、後段を安全にするための下地。
- **Step 3（実際にメモリ問題を解決する。サイズしきい値による二重経路）**
  `Settings.eager_load_limit_mb`（既定512MB程度）を追加し、
  `row_count * (列数-1) * 8 <= 閾値`なら**現行の全列即時読み込みを一切変更せず**そのまま使う。
  閾値を超えるファイルだけ`LazyColumn`/`DerivedColumn`経路（Step 1のブロック読み込み＋ピラミッド、
  `SeriesSampleWorker`による背景スレッドでのサンプリング、per-series世代カウンタでの
  stale結果破棄——`DownsampleWorker`で確立済みの安全策をそのまま再利用）に入る。
  この「小さいファイルは今のまま」というしきい値分岐が、既存82テストへの影響を最小化する
  最大の勘所（後述）。
  派生変数の評価（`core/expression.py`）は`evaluate_expression`自体は変更不要（元々
  `Mapping[str, ndarray] -> ndarray`の純粋関数）だが、`delta`/`rolling_mean`のような
  「近傍を見る」関数はブロック境界で不正確な値を出すため、`required_halo(expr)`
  （式が要求する前後マージン行数）を新設し、可視範囲の前後にそのマージンを加えて読んでから
  評価後にトリムする。
  ナビゲータの概要表示はフッタ統計（開いた直後、I/Oなしで即時）→ピラミッド構築完了後は
  実際にプロットされた変数のmin/max envelope、に順次更新する。
- **Step 4（仕上げ）**
  ズームアウト時の粗い（min/max envelope）表示と精密（LTTB）表示の切り替えをステータスバー等で
  明示。ファイル切り替え時にピラミッド構築ワーカーをキャンセルする。設定ダイアログへの
  `eager_load_limit_mb`項目追加。[13.3章](#133-未使用未配線のコード要判断-実装して活かすか削除するか)の
  `set_downsample_enabled`は「ピラミッドがある以上ダウンサンプリング無効化という概念自体が
  意味をなさない」ため、この設計を採用するなら配線せず削除する方向で判断してよい。

###### Step 3以降で個別に判断・注意が必要な点

- **view保存/読み込み（`io/view.py`)**: 両モデルとも「変更不要」と結論。`View`は変数名・式・
  色・レンジしか持たず、データ配列は元々一度も保存していない。影響があるのは
  `main_window.py`の`save_view_dialog`/`load_view`側の呼び出し（`series_data()`→
  `series_color()`/`series_source()`への置き換えのみ）。
- **FFT（`core/analysis.py`、`_on_fft_requested`）**: 「全データが必要」という前提そのものに
  Opus案から異論あり——このテストデータは4年スパンだが16時間おきの間欠バーストであり、
  `fft()`内部の`dt = median(diff(x))`は16時間ギャップを挟むと意味を持たない。**ファイル全体の
  FFTはメモリ問題以前にそもそも無意味な結果になる**という指摘は妥当。「現在の可視範囲に対して
  FFTを実行し、行数が閾値を超えたらダイアログで範囲を絞るよう促す」という設計の方が
  データの実態に合っている。ただしこれは`specification.md`5.6章のFFTの対象範囲に関する
  **仕様変更**であり、メモリ問題の修正とは別にユーザー判断が必要（Fable案は逆に「仕様通り
  全データを対象にする、ただし非同期化する」を支持しており、ここは2案が割れている）。
- **単調性の前提**: `row_range_for_x()`のようなrow group統計を使ったX→行範囲変換は、時間軸列が
  ソート済み（単調増加）であることに依存する。実は`_redraw_series`の`np.searchsorted`は
  **現在も**この前提に暗黙で依存しており、`time_axis_combo`で非単調な列を時間軸に選ぶと
  今も黒く見えないバグ（誤ったクロップ）になっている。レイジー読み込みではこれが「誤った
  クロップ」から「誤った行を読む」に悪化するため、`is_monotonic()`チェックを追加し、
  非単調列は（a）強制的に即時読み込み経路にフォールバックする、または（b）
  `time_axis_combo`から除外する、のどちらを採るか明文化してから実装する。
- **既存テストへの影響は当初の想定（79件超）より小さい**：実際に`_raw_variables`/
  `_time_values`を配列として直接assertしているのは`tests/parquet_analyzer/test_ui_stress.py`
  （2テスト）と`tests/parquet_analyzer/test_load_robustness.py`（4テスト、`series_data()`経由の
  1件含む）の計6テスト・約10行のみ。`test_plot_widget.py`/`test_plot_grid.py`/
  `test_downsample.py`/`test_expression.py`/`test_analysis.py`等は`MainWindow.load_parquet`を
  経由せず直接配列を渡してテストしているため無改修で通る想定。上記のしきい値分岐を入れれば
  既存フィクスチャ（数万行規模）はすべて現行の即時読み込み経路のままになるので、実際には
  この6テストの参照先を新しいアクセサ（`variable_values()`等）に差し替えるだけで済む見込み。

##### 副産物: テスト用4GBファイルの時刻列の型不整合（発見・修正済み）

Opusの調査により、`data/raw/sensor_log_4y_4gb.parquet`の生成スクリプトが`time`列を素の
`int64`（ミリ秒epoch）で書き出しており、実物の`sensor_log_4y.parquet`（`timestamp[us]`）と
型が違うことが判明した。`MainWindow._to_numeric`は`datetime64`型だけを秒変換するため、この
不整合のまま使うとX軸が生のミリ秒値（西暦54600年相当）で表示され、メモリ問題とは無関係な
別の不具合を誤って追いかけることになっていた。生成スクリプトを`timestamp[us]`に修正し、
ファイルを再生成済み（スキーマ・サイズは`du -h`で約4.0GB、行数不変）。

##### 2026-09-19再調査（Opus・Fableによる2回目の独立レビュー）

新しい再現用データ（`tests/parquet_analyzer/generate_test_parquet.py`、本リポジトリにコミット
済み——`sensor_log_4y_4gb.parquet`を生成したスクリプトは一度もコミットされておらず、この
チェックアウトには`data/raw/sensor_log_4y_4gb.parquet`自体が存在しない点と対照的）で再調査した。
`data/raw/daily_cycle_4gb.parquet`（86,600,000行×5列、872 row group、24時間中8時間稼働の
パターン）と`data/raw/daily_cycle_500mb.parquet`を本章の標準フィクスチャとする（同スクリプトの
docstringのCLIで再生成可能）。

**2026-09-17に書いたゼロコピーの結論を訂正する。** `pl.Series.to_numpy()`がゼロコピーなのは
**単一チャンクのSeries**の場合のみ（Arrowバッファへのポインタが完全一致することを確認済み）。
実際のrow group数を持つファイルに対する`scan_parquet(...).collect()`は**row group毎に1チャンク**
になる（4GB再現ファイルで`df[c].n_chunks()`実測872）。複数チャンクのSeriesに対する`to_numpy()`
は連続バッファへの再構成（rechunk）が必要で、これは実体のあるフルコピー（`writeable=True`、
`owndata=False`、`base`は元のSeriesではなく新しくrechunkされたSeries）。2026-09-17の調査は
おそらく1チャンクに収まるケースから一般化してしまっていた。実際の再現ファイルでは
`ParquetDataSource.read_columns()`（`core/data_source.py:33-39`）は読み込む内容の**フルコピーを
もう1回**発生させており、上記の分析はファイル1本分過小評価していたことになる。
`.rechunk()`での「修正」はできない——rechunkそのものがコピーだから。

**実測結果**（Windows、`peak_wset`。4GBファイルの論理データ量は5列で約3.2GiB）:

| ステップ | RSS | ピーク |
|---|---|---|
| `MainWindow()`のみ | 0.11 GiB | 0.11 GiB |
| `ParquetDataSource(path)`（スキーマ/フッタのみ） | 0.05 GiB | 4 ms |
| `read_columns(全5列)` | 6.76 GiB | 7.26 GiB |
| + 全5列に`_to_numeric` | 7.41 GiB | 8.05 GiB |
| `load_parquet`全体（ナビゲータのLTTB含む） | 7.48 GiB | 8.12 GiB |
| plot 1に4変数を重ね描画 | 7.48 GiB | 8.77 GiB |
| **5系列で`go_home()`** | 7.48 GiB | **12.64 GiB**（一時的に+3.9GiB、UIスレッドで2.4秒） |
| 派生変数を1つ追加 | 8.12 GiB | 12.64 GiB |
| 同じファイルを3回連続で開く（プロットなし） | 8.18 GiB | 13.19 GiB |

つまりこの3.7GiBファイルで普通に使っているだけで、このマシンでもピーク約13GiB（ファイルの約
3.5倍）に達する——32GiB報告と同じ故障モードで、このマシンではアロケータ/使用パターンの巡り
合わせがやや良かっただけ。読み込み後に`del df; del data; gc.collect()`してもRSSは0.000GiBしか
動かず、解放されたpolars/Arrowのアリーナメモリ―がOSに返却されないという仮説を、推測ではなく
直接確認した。

**見落とされていた独立の即効性ある修正点**: `TimePlotWidget._finite_bounds`
（`ui/plot_widget.py:120-134`）は配列ごとにbool mask index（`a[np.isfinite(a)]`）を取り、それを
`np.concatenate`している——配列ごとに1コピー、さらに結合でもう1コピー。`add_series`
（`plot_widget.py:113-114`）は重ね描画のたびにこれを2回、`go_home()`
（`plot_widget.py:229-230`）は系列ごとにx・yそれぞれで支払う——5系列が0.645GiBのx配列を共有する
状況で、上記の+3.9GiB／UIスレッド2.4秒フリーズの正体はこれ。mask・concatenateを使わない
streamingな`nanmin`/`nanmax`に置き換えれば解消する。本章の設計全体とは独立に、単独で直す価値が
ある。

**`_to_numeric`のコピー回数削減についての訂正**: 時刻列変換を3コピーから1コピーに減らしても
（`values.astype("datetime64[us]").astype(np.int64) / 1e6` →
`np.true_divide(values.view(np.int64), divisor, dtype=np.float64)`、`divisor`は
`np.datetime_data`から算出）、**定常状態の常駐メモリは変わらない**——CPythonはチェーンの長さに
関わらず中間配列を即座に解放するため、最終的に生き残る配列はどちらの実装でも1個だけ。減るのは
**一時的なピーク**のみ（このファイルで約0.645GiB分）。無料・ゼロリスク・ビット単位で同一
（`np.array_equal`で現行3コピー版と一致確認済み。`* 1e-3`の`multiply`は**ビット同一にならない**
ため`true_divide`を使うこと、最大誤差2.4e-7）なのでやる価値はあるが、常駐メモリの実測値が
これで大きく動くとは期待しないこと。

**新たに気づいた点**: `_to_numeric`（`main_window.py:303-308`）はfloat64以外の列を無条件で
float64に強制変換しており、元の列がfloat32/int32等のファイルではこれだけでメモリが倍になる。
32GiB報告のファイルが全列float64でないなら、これだけで説明のつかない差の一部が埋まる可能性が
ある——報告者の実ファイルのスキーマを確認する価値がある。

**その他に見つかった全配列実体化箇所**（いずれのデザインでも即時解決するなら関係する）:
`core/analysis.py`の`basic_stats`の二重コピー（`finite = y[np.isfinite(y)]`後に`median`で
ソート）；`_on_stats_requested`は選択した変数ごとに`BackgroundWorker`を並列起動するため複数変数
選択で一時コピーが倍加；`MainWindow._all_arrays()`（`main_window.py:320-325`）は呼ばれるたびに
**全ての**派生変数を**ファイル全長**で再評価；`navigator.set_overview_data`は今もUIスレッドで
全列`lttb`を実行（`navigator.py:34`、後述の設計でfooter統計に置き換える）；
`_visible_row_range`/`_redraw_series`の`np.searchsorted`は今も時刻列が単調増加であることに
暗黙に依存している（非単調な時刻軸選択は本章と無関係に既に誤ったクロップを生む）。

**「可視範囲だけ読む」方針の実現可能性を実ファイルで再確認**: フッタ+スキーマのオープン
5.8ms；**872個全row groupの統計**（min/max/null_count、全列）7.1ms・メモリ増加は測定不能；
100万行×1列のslice pushdown読み込み 2.3ms；4つの値列全部に対する8192行バケットのmin/maxピラミッド
構築（streaming）1.74秒・ピーク1GiB未満・**列あたり保存サイズ165KiB**。可視範囲の読み込みは
全列読み込みの約1000分の1のコストで、パン/ズームのたびにディスクから読み直す方が今の
「開いた瞬間に全部読む」よりむしろ速い。

###### 統合計画（Phase A〜E。上記Step 0〜4をほぼ1対1で置き換え、変更点のみ明記）

両モデルとも独立に「2026-09-17の設計を骨格として維持する」という結論に達した。各フェーズが
単独で完結し、`daily_cycle_4gb.parquet`に対するRSS実測で検証できるようフェーズ名に付け替えた。

- **Phase A = Step 0の拡張版**（アロケーション衛生。データモデル変更なし。約0.5日。リスク極小。
  B〜E をどう決めても関係なく実施すべき）: `_to_numeric`の単一コピー化（前述）；`read_columns`を
  **列ごとに逐次**読み込み・変換し、次の列に進む前にpolars側のフレームを解放（実測ピーク
  8.05→4.89GiB、所要時間は同程度）；アロケーションフリーな`_finite_bounds`；`basic_stats`の
  二重コピー解消；`load_parquet`でコミット前に変換前の`data`辞書への参照を外す。
  `_on_range_changed`のデバウンスとFFTの非UIスレッド化（元のStep 0の項目）は既に実装済みである
  ことを確認済み、対応不要。

  **✅ 2026-09-19実装・検証済み**（`core/data_source.py`、`ui/main_window.py`の
  `_to_numeric`/`load_parquet`、`ui/plot_widget.py`の`_finite_bounds`、`core/analysis.py`の
  `basic_stats`）。`_to_numeric`はs/ms/us/ns全ての`datetime64`単位で旧3コピー版と
  ビット単位で一致することを`np.array_equal`で確認、`_finite_bounds`は空配列/全NaN/Inf混在/
  複数配列の各ケースで旧実装（filter+concatenate）と一致することを確認。`uv run pytest`は
  107 passed, 1 skippedのまま変化なし。`data/raw/daily_cycle_4gb.parquet`で同じ実測項目を
  再測定:

  | ステップ | 修正前（2026-09-19 opus実測） | 修正後（Phase A） |
  |---|---|---|
  | `load_parquet`直後 | RSS 7.48 / peak 8.12 GiB | RSS 5.50 / **peak 5.58 GiB** |
  | 変数を重ね描画 | RSS 7.48 / peak 8.77 GiB（5系列） | RSS 5.74 / **peak 5.74 GiB**（4系列） |
  | `go_home()` | peak **12.64 GiB**（一時的に+3.9GiB、UIスレッド2.4秒） | peak **5.84 GiB**（スパイクほぼ消滅、0.92秒） |
  | 同ファイルを3回開き直す（プロットなし） | peak 13.19 GiB | peak 11.54 GiB |

  `go_home()`の数GiB規模の一時スパイクとUIフリーズは解消した（疑い通りほぼ`_finite_bounds`が
  原因だった）。開いた直後のピークは約31%減。3回開き直すケースの改善幅が小さいのは、
  `load_parquet`のatomic commit中に旧ファイルの配列が生き続ける問題にPhase Aは対応していない
  ため（これはPhase C/Dの領域であり、Phase Aの目標外）。
- **Phase B = Step 1**（`ParquetDataSource`の拡張のみ。UIから未使用なので既存動作を壊しようが
  ない。約1〜1.5日。リスクほぼゼロ）: `row_groups()`、`column_stats()`、`column_bounds()`、
  `row_range_for_x()`（row group統計を二分探索、`is_monotonic()`でガード）、ブロック単位read＋
  LRUキャッシュ、`core/pyramid.py`。新規抽象が不要な2つの無料の改善も配線する: ナビゲータ概要と
  初期軸範囲をフッタ統計から取得（開いた瞬間の全列`lttb`を置き換え）。

  **✅ `ParquetDataSource`/`core/pyramid.py`は2026-09-19に実装・検証済み——ナビゲータ/UIへの
  配線は意図的に見送り。** `core/data_source.py`に`row_groups()`、`column_stats()`、
  `column_bounds()`、`is_monotonic()`、`row_range_for_x()`（row group min/maxをbisectで二分探索
  ——row group数が数百程度なら線形走査でも十分速いが、row group数がもっと多いファイルでも
  安く済むようbisectにした）、ブロック単位でキャッシュする`read_window()`（固定サイズブロック、
  `lru_cache`ベースの**ブロック件数**上限——上記計画が書いていたバイト予算方式ではなく、まだ
  何も呼んでいない段階での妥当な簡略化。Phase Dで実際の使われ方が判明したら見直す）を追加。
  `core/pyramid.py`に`build_pyramid()`/`ColumnPyramid`/`pyramid_bounds()`を追加
  （`pyarrow.iter_batches`でstreaming、意図的に自前のスレッド化はせず——後のフェーズで
  ダウンサンプリング/FFT/統計と同じく`BackgroundWorker`経由で呼ぶ想定）。この作業の一環として
  `_to_numeric`を`MainWindow`から`core/numeric.py`（`to_numeric()`）へ移した——`pyramid.py`が
  全く同じ日時変換ロジックを必要とし、`core/`は`ui/`に依存してはいけないため。
  `MainWindow._to_numeric`はそれをそのまま再エクスポートしている
  （`_to_numeric = staticmethod(to_numeric)`）ので、本書内や`core/convert.py`のコメント中にある
  既存の`MainWindow._to_numeric`という名前への参照は引き続き正しく解決される。

  計画上は「無料の改善」としていた`navigator.py`/`MainWindow`への配線は**意図的に見送った**：
  全列LTTBの波形表示を単なるフッタmin/maxの1本の線に置き換えると、実質的な見やすさの後退になる
  （形・パターンが見えなくなる。例えばこのテストデータの毎日の稼働/停止バースト）。
  `column_stats()`のrow group単位envelopeやピラミッドから組み立てない限り改善にならないため、
  拙速な単独変更ではなく、どのみち`navigator.py`の呼び出し箇所を触るPhase Cの`Column`/
  `SeriesSource`作業と合わせて丁寧に行う方が適切と判断した。

  `tests/parquet_analyzer/test_data_source.py`（12テスト）・`test_pyramid.py`（5テスト）で検証:
  `row_groups()`が実際の行数と一致し連続していること、`column_bounds()`/ピラミッドの境界が
  実列のmin/maxと一致すること、`is_monotonic()`がソート済み時刻列とランダムデータを正しく
  区別すること、`row_range_for_x()`がbrute-force `np.searchsorted`の範囲を常に包含し
  （狭くなることはなく）row group境界にちょうど一致すること、`read_window()`が
  キャッシュブロック境界をまたぐ場合も含め`read_columns()`と一致し、同じリクエストの再実行では
  オブジェクト同一性でキャッシュを再利用すること。`uv run pytest`は126 passed, 1 skipped。
  `data/raw/daily_cycle_4gb.parquet`で実測値も再確認（見積もりだけでなく）:
  `row_groups()`（872グループ）4.8ms；全5列の`column_bounds()`19ms；`is_monotonic("time")`
  0.1ms未満；1時間分の`row_range_for_x`23ms；結果の400,000行に対する`read_window()`14ms；
  4つの値列に対する`build_pyramid()`1.82秒（8192行バケットで10,572バケット）——
  いずれも上記2026-09-19再調査の見積もりと整合している。
- **Phase C — 元のStep 2から方針変更。** Step 2は`Column`/`SeriesSource`抽象を導入しつつも
  `load_parquet`は変わらず即時読み込みのままとしていた（「メモリ問題はまだ解決しない」）。
  今回の推奨は、最大の実利益をPhase Dまで待たずこの段階に組み込むこと: ファイルを開く時点では
  スキーマ+フッタ統計のみ読み込み、列のデータは**実際に使われた時点**（プロットされた／式から
  参照された／統計対象に選ばれた）で初めて実体化する。時刻列は`searchsorted`/ナビゲータの都合で
  当面は開いた時点で実体化したままとする（Phase Dの`row_range_for_x()`でこれも不要になる）。
  `add_series(name, x, y, color)`はシグネチャ互換のまま（`MaterializedColumn`でラップ）なので、
  これを直接呼ぶ既存テストは無改修で通る。`series_data()`は当初案通り`series_color()`/
  `series_source()`に分割する。約2〜3日、リスク中（`MainWindow`の状態管理と、後述の6テストに
  影響）。

  **✅ 2026-09-19に実装・検証済み——上記計画から意図的に1点スコープを外している。**
  `core/column.py`に`Column`（`Protocol`: `values() -> np.ndarray`）、`MaterializedColumn`
  （既に実体化済みの配列をラップ——時刻列と、全ての演算変数の計算結果がこれになる）、
  `LazyColumn`（パス＋列名＋`ParquetDataSource`。初回の`values()`呼び出しで列**全体**を読み込み
  キャッシュする——まだwindowed読み込みではない。それはPhase Dのまま）、`LazyVariables`
  （実際に参照されたときだけ`Column`を実体化する`Mapping[str, ndarray]`——
  `evaluate_expression()`に渡すことで`a + b`は`a`/`b`だけを実体化し、それ以外の無関係な
  読み込み済み列には一切触れない。`core/expression.py`側は`in`/`[]`しか使わないことを事前確認
  済みだったため**変更ゼロ**で済んだ）を追加。`MainWindow._file_columns`/`_raw_variables`は
  `Column`を保持するようになり、`load_parquet`は選択中の時刻列だけを即時読み込みし、それ以外は
  `LazyColumn`でラップする。`_variable_array`は本計画が約束していた公開アクセサ
  `variable_values(name)`にリネームし、生変数・演算変数を問わずこれ1つで名前解決＋
  必要に応じた実体化を行う唯一の窓口にした。

  **上記計画と異なり、今回は意図的に対応しなかった点**: `TimePlotWidget._series`/`add_series`/
  `series_data()`には一切手を入れていない——系列データは引き続き素の`ndarray`として渡され保持
  される（`SeriesSource`でラップしない）。Phase Cの本来の目標（列を使うまで実体化しない）は
  プロットウィジェット側が何も知らなくても達成できる——`add_series`は`MainWindow`がいつ実体化を
  決めたかに関わらず、常に既に実体化済みの配列を受け取るだけだから。`SeriesSource`/
  `series_color()`/`series_source()`の分割は、プロットウィジェット側が「系列を別のwindowに対して
  再取得できる」ことを知る必要があるPhase Dでこそ意味を持つため、今すぐの挙動上の必要性がない
  まま行うのではなくそちらに見送った——Phase Bでナビゲータ配線を見送ったのと同じ判断基準。

  `_refresh_navigator_overview`の「まだ何もプロットされていない」フォールバック
  （`main_window.py`）は、開いた時点で1列（ファイルの先頭の生変数）を今も即座に実体化する——
  これを解消するPhase Bのフッタ統計/ピラミッド配線は引き続き見送ったままで、
  「Phase Cと合わせて」ではなく明示的に別途のフォローアップに先送りした（Phase C自体の差分が
  既に十分大きかったため）。

  `tests/parquet_analyzer/test_column.py`（7テスト: `LazyColumn`が`values()`まで読み込まない
  こと・以降キャッシュされること、`LazyVariables`が——呼び出し回数を数えるラッパー経由で
  ——式が実際に参照した列しか実体化しないことを証明）と
  `tests/parquet_analyzer/test_lazy_loading.py`（5テスト、`MainWindow`レベル: 複数列ファイルを
  開くと時刻列とナビゲータのフォールバック対象列以外は未実体化のままであること、ある変数を
  プロット/演算式で参照/時間軸に選択するとその変数だけが実体化され兄弟列は影響されないこと）で
  検証。`uv run pytest`は137 passed, 1 skipped。`data/raw/daily_cycle_4gb.parquet`で再測定:

  | ステップ | Phase Aのみ適用後 | Phase C適用後 |
  |---|---|---|
  | `load_parquet`直後（開いた直後） | peak 5.58 GiB（全5列を読み込み） | **peak 2.99 GiB**（時刻列＋ナビゲータフォールバックの1列のみ） |
  | 4つの生変数全てを重ね描画後 | peak 5.74 GiB | peak 5.12 GiB（結局全部使うと総量はほぼ同じに収束——想定通り。Phase Cは「いつ読むか」を変えるのであって、全列を触るセッションの最終的な定常状態を変えるものではない） |
  | `go_home()` | peak 5.84 GiB | peak 5.12 GiB（Phase Aの修正は引き続き有効——スパイクなし） |

  開いた直後のピークはPhase A単体からさらに約46%減。中段の「収束するだけで改善しない」結果は
  このテストセッション特有の正しい挙動（あえて全列を重ね描画して「全部使えば結局Phase C前と
  バイト単位で同じ状態に収束する」ことを検証するため）——実際の恩恵はこのフェーズが狙う、より
  一般的なケース（20列中2列しか見ないユーザーは、その2列分しか払わなくなる）の方に出る。
- **Phase D = Step 3**（「実際にメモリ問題を解決する」：可視範囲へのwindowed読み込み）。
  Phase Bの`read_window`＋LRUキャッシュ；`SeriesSampleWorker`（取得とダウンサンプリングを1つの
  `BackgroundWorker`ジョブにまとめ、取得途中のwindowが描画されないようにする）は既存のper-series
  世代カウンタをそのまま再利用してstale結果を破棄；ズームアウト時はピラミッドのenvelopeで描画；
  `delta`/`rolling_mean`用に`required_halo(expr)`でマージン込みの読み込み。**元のStep 3の記述
  からの修正点**: `eager_load_limit_mb`によるサイズ閾値は`Column`実装の選択にのみ使う——
  `MainWindow`/プロットロジック内に`if`分岐を作らない。これにより、ファイルサイズに関わらず
  UIのコードパスは常に1本になり、2本が知らぬ間に乖離するリスクを避けられる。約4〜6日、
  ここが本当のリスク。

  **✅ 2026-09-19に大筋実装・検証済み——ただし実測ベースの明確な制約あり（単なる先送りではない）。**
  `Settings.eager_load_limit_mb`（既定512MB）が`load_parquet`内の列単位判定
  （`row_count * 8 > 閾値`——**元の記述`row_count * (列数-1) * 8`から意図的に簡略化**:
  Phase Cで既に「実際に使う列だけ」しか実体化しないため、判断すべきは列1本の大きさであって
  ファイル全体の幅ではないため）をゲートする。閾値以下は従来通り`LazyColumn`（Phase C）、
  超えたら新設の`WindowedColumn`（`core/column.py`）: `window(row_start, row_end)`はPhase Bの
  `read_window()`でその範囲だけ取得；`values()`は`window(0, row_count)`にフォールバック
  （「まだ何もプロットされていない」／演算変数のソースなど、本当に全体が必要なケース用に
  引き続き必要）；`bounds()`はPhase Bの`column_bounds()`（フッタ統計、I/Oなし）を返し、
  `go_home()`が何も実体化せず真の範囲を得られるようにする。

  上記の修正方針は忠実に守った: `isinstance(column, WindowedColumn)`で分岐するのは
  `MainWindow._add_variable_to_plot()`の**1箇所だけ**——`TimePlotWidget`・`PlotGridWidget`・
  その他全ての呼び出し箇所は、実体がどちらであっても同じように`Column.window()`/`.values()`を
  呼ぶだけ。`TimePlotWidget`に`add_windowed_series(name, x, y_source, y_bounds, color)`を
  既存の`add_series()`はそのまま維持して追加（既存の全呼び出し元でビット単位同一の挙動を確認——
  Phase D以前の137テストが無改修で全てパス）；`_redraw_series`はwindowed系列のYを、
  ダウンサンプリングと同じバックグラウンドジョブの一部としてUIスレッド外で取得するようにした
  （取得途中のwindowが描画されることはない）——既存のper-series世代カウンタをその仕組み自体は
  一切変更せず再利用；`go_home()`／最初の系列追加時の初期範囲は、windowed系列のフッタ統計
  bounds とresident系列の実際の範囲をsentinelベースの集約（`_series_y_bounds`/
  `_finite_bounds_raw`）で組み合わせるようにした——これは、初期案にあったバグ（各系列の
  `(0.0, 1.0)`フォールバックを個別に計算してから結合すると、全NaN/空の系列が他の実データを
  持つ系列の真の範囲を誤って狭めてしまう）を、`(inf, -inf)`のsentinelを系列ごとに使い、
  全て結合した**後**に一度だけ`(0.0, 1.0)`フォールバックを適用する形に直して回避したもの。
  `series_data()`はwindowed系列に対して`y=None`を返す（返せる実体の配列が無いため）——
  新設の`series_source()`/`transfer_series()`のペア（`PlotGridWidget`の「新規プロットとして
  分離」で使用）により、呼び出し側は`MainWindow`側の変数管理がその名前をまだ知っているかに
  依存せず元のfetcherを取得できる（`series_data()`を`variable_values()`より優先する既存の
  設計判断と同じ理由）。`MainWindow`に`variable_window(name, row_start, row_end)`を追加し、
  FFT/統計は`variable_values(name)[a:b]`ではなくこちらを使うようにした——windowed列に対する
  FFT/統計要求は、列全体ではなく可視範囲だけを読むようになった。ナビゲータの
  「まだ何もプロットされていない」フォールバックと「アンカープロットの最初の系列がwindowed」
  ケースは、どちらも`column_stats()`から粗いrow group単位min/maxのenvelopeを構築する
  （`_windowed_navigator_overview()`）ようにした——resident配列を必要とせず、フッタのみの
  コストで、平坦な線ではなく実際の形（このテストデータの毎日のバーストが見える）を示せる。

  `tests/parquet_analyzer/test_column.py`（`WindowedColumn`の新規テスト5件: `window()`が
  実際のスライスと一致し、ファイル全体を読まないことを`ParquetDataSource.read_columns`への
  spyで確認；`values()`/`bounds()`のフォールバックが正しいこと）、
  `tests/parquet_analyzer/test_windowed_series.py`（`TimePlotWidget`レベル9件、呼び出し回数を
  数える偽windowソースを使用: 初期範囲がデータではなく`y_bounds`由来であること；再描画が
  可視範囲のみを取得すること；表示値の正しさ；`series_data()`/`series_source()`/
  `transfer_series()`の契約；`go_home()`の混在集約が系列間で汚染しないこと——上記のバグは
  リリース前にこのテストで検出済み；世代カウンタによるstale取得の破棄）、
  `tests/parquet_analyzer/test_windowed_loading.py`（`MainWindow`レベル8件、
  `eager_load_limit_mb = 0`で小さいフィクスチャでもwindowed経路を強制: 閾値が正しい`Column`型を
  選ぶこと；windowed変数に対するドロップ/FFT/統計/ナビゲータ/go_homeが全て正しく動作すること）
  で検証。`uv run pytest`は159 passed, 1 skipped。

  **`data/raw/daily_cycle_4gb.parquet`での実測**（既定512MB閾値では全4つの生列が
  `WindowedColumn`になる——86.6M行×8byte≈693MB > 512MB）:

  | ステップ | ピーク |
  |---|---|
  | `load_parquet`直後（開いた直後） | 2.15 GiB |
  | 全4変数を重ね描画（最初の系列から全体ズームアウトのまま） | 2.23 GiB |
  | 初回のwindowed取得が落ち着いた後 | **6.74 GiB** |
  | より狭い範囲へ5回パン/ズーム後 | ピークは6.74 GiBのまま、だが**RSSは3.84 GiBまで低下** |
  | `go_home()`（全体ズームアウトに戻す） | 6.74 GiB |
  | 約260万行のFFT相当範囲への`variable_window()` | ピーク6.74 GiBのまま、**RSSは1.12 GiBまで低下** |

  **良い面だけでなく正直な所見**: 全体ズームアウト時（最初の系列を追加した直後、または
  `go_home()`直後）は、windowed系列の取得が可視範囲——このファイルではほぼ全体の約86.6M行——を
  LTTBでダウンサンプリングする前にほぼ丸ごと取得することになり、これはPhase Cの「列全体を
  一度だけ実体化する」旧経路とほぼ同じコストがかかる。さらにこの取得が4本同時にスレッドプール上で
  実行され得るため、一時的にはPhase Cより**多く**かかる（同一の「全4列重ね描画→go_home」
  シナリオでPhase Cは5.12 GiBのプラトーだったのに対し、Phase Dはピーク6.74 GiB）。これは
  2026-09-19の再調査で既に指摘され、計画の「ズームアウト時はピラミッドのenvelopeで描画」という
  一文がまさに埋めるはずだったギャップそのものである——`core/pyramid.py`自体はPhase Bで既に
  存在するが、**今回のパスでは`_redraw_series`の判断ロジックに配線していない**——
  `_redraw_series`は可視範囲がどれだけ広くても常に実際のwindowed取得を行う。Phase Dが
  実際にもたらしたもの、そして上記のパン/ズームの数字が示すものは、メモリ使用量が
  **現在の表示範囲に応じて増減するようになった**こと——ズームアウトすれば増え、そして重要なのは
  **より狭い範囲にズームインすれば減る**ことで、Phase Cのように一度使ったらその後何をしても
  永久にプラトーに張り付いたままにはならない。一般的な対話的利用（数列だけを見る、ズームインした
  状態）ではこれは実際の改善だが、「巨大ファイルを開いて即座に全体をズームアウトして全部見る」
  という使い方では、Phase Cよりまだ良くなっておらず、一時的には悪化しうる。
  `_redraw_series`にピラミッドを配線する（可視行数が`n_out`の何倍かを超えたら実際の取得ではなく
  バケットのmin/maxを使う）のが自然な次の一手だが、今回のパスでは着手していない。

  **今回のパスで意図的に対象外とした点**（見落としではなく、理解した上での既知のギャップ）:
  時刻列は閾値を超えても常にresidentのまま（`row_range_for_x()`は本設計では一切使っていない
  ——`_redraw_series`/`_visible_row_range`は今もresidentな`x`に対して`np.searchsorted`する、
  Phase D以前と全く同じ——これが理由で、後述の単調性に関する方針決定はここでは実際には
  一度も問題にならなかった）；`WindowedColumn`をソースに参照する演算変数を作成すると、
  そのソースの全体読み込みが今も強制される（`LazyVariables.__getitem__`は`window()`ではなく
  `values()`を呼ぶ）——windowedな演算変数評価用の`required_halo(expr)`は未実装。

  ###### ピラミッド配線のフォローアップ（同日2026-09-19に実装・検証済み）

  上記のギャップを埋めた: Phase Bで構築した`core/pyramid.py`を、`TimePlotWidget._redraw_series`の
  判断に実際に配線した。`WindowedColumn`（`core/column.py`）に`pyramid`/`request_pyramid()`/
  `set_pyramid()`を追加: `request_pyramid()`はバックグラウンドスレッドで実行するジョブ（引数なし）
  を返す（既に構築済みか構築中なら`None`——重複構築なし）。このジョブは`build_pyramid()`で
  列を1回streamする。`_redraw_series`は、windowed系列かつダウンサンプリングが有効な場合に限り
  以下をチェックするようになった: 可視行数が`_PYRAMID_PREFERRED_ROW_THRESHOLD`（500万行——
  ピクセル由来の`n_out`の倍数ではなく固定行数にしたのは、問題の本質がその幅での実フェッチ自体の
  コスト/メモリであって、ズームアウトの度合いそのものではないため。この閾値での単発フェッチは
  約40MB相当で、これを十分下回る実フェッチは実測でも高速——4GB再現ファイルで40万行14ms）を
  超える場合、ピラミッドの粗いバケット単位min/maxエンベロープが構築済みならそちらを優先する
  （`_render_from_pyramid`——同期的、ディスクI/Oなしなのでバックグラウンドワーカー不要。
  それでも世代カウンタは増やす——より前の再描画による、まだ完了していない実フェッチの結果が
  後から届いてもstaleとして正しく破棄されるように）。ピラミッドがまだ無ければ
  `_maybe_start_pyramid_build`がバックグラウンドで構築を開始し（`_on_pyramid_ready`が
  `set_pyramid()`を呼んでから`_redraw_series`を再度呼び直す——系列が構築中に別の`y_source`に
  差し替わっていたりプロット自体が消えていた場合はno-opでクラッシュしない）、
  今回の再描画自体はこのフォローアップ以前と同じ実フェッチにフォールバックする。
  ダウンサンプリングが無効な場合はピラミッドを一切使わない（ユーザーが明示的に生の精度を
  求めている以上、バケット単位min/maxでは代替にならないため）。

  `core/column.py`の新規テスト3件（`request_pyramid()`が有効なジョブを返すこと；構築中・
  構築済みのどちらでも`None`を返し重複構築しないこと）と、`test_windowed_series.py`の
  `TimePlotWidget`レベル新規テスト5件（閾値未満ではピラミッドを要求しないこと；閾値超過かつ
  未構築時は実フェッチにフォールバックしつつ構築も要求すること；閾値超過かつ構築済みなら
  実フェッチを完全にスキップすること；バックグラウンド構築完了が再描画を発火しそれ以降ピラミッド
  を使うこと；ダウンサンプリング無効時は閾値超過でもピラミッドを迂回すること）で検証——
  いずれもピラミッドプロトコルを実装した偽のwindowソースを使い、数百万行規模の実フィクスチャは
  不要。`uv run pytest`は167 passed, 1 skipped。

  **1列だけに絞った実測**（複数列同時実行時の競合と、仕組みそのものの効果を切り分けるため）——
  `data/raw/daily_cycle_4gb.parquet`、変数1つだけをプロット:

  | ステップ | ピーク |
  |---|---|
  | `load_parquet`直後 | 2.15 GiB |
  | 初回の全体ズームアウト描画（実フェッチ。この間にピラミッド構築も完了） | 3.71 GiB |
  | 全体ズームアウト状態で`go_home()`を5回連続 | **3.71 GiBのまま、それ以上増えない** |
  | （極小範囲へズーム→`go_home()`）を5回繰り返し | **3.71 GiBのまま** |

  これは狙い通りの効果: 実フェッチ1回＋ピラミッド構築1回の後は、そのズームレベルでの
  それ以降の再描画は無期限に無料になる——`go_home()`/ズームアウトのたびにファイルのほぼ全体を
  再フェッチしてディスクI/Oと一時メモリを払い続ける、という従来の挙動を置き換えられている。

  **単独では見えなかった、全4列同時プロット時の制約——さらに検証したところ、当初の診断は
  誤りだったと判明。** 全4つの生変数を重ね描画して表示が落ち着くのを待ったところ、1.5秒待っても
  4列ともピラミッド未構築（`pyramid built: False`）だった。`go_home()`を6回連続で押すと、
  ピークが8.41→8.81→9.05 GiBとじわじわ増加し、4〜6回目でようやく安定した。最初の仮説——
  実windowedフェッチとピラミッド構築が同じ`QThreadPool.globalInstance()`を奪い合い、4列分の
  巨大な実フェッチがピラミッド構築より先にスレッドを占有してしまっている——を直接検証した:
  ピラミッド構築専用のスレッドプール（`_PYRAMID_THREAD_POOL`、`MainWindow._analysis_thread_pool`
  の既存のFFT/統計分離と同じ発想で`self._thread_pool`から分離）を実装し、共有プールのケースと
  正確な列ごとの完了タイムスタンプでA/B比較した。**仮説は支持されなかった**: 並列度を制限しない
  専用プールでは4列とも約3.5秒で完了——共有のグローバルプール（約3.5〜3.6秒）と同じで改善なし。
  同時実行数を2に制限した専用プールは逆に**遅くなった**（約3.7〜3.8秒）——共有プールなら問題なく
  並列実行できていたものを不必要に直列化してしまったため。本当の説明: ピラミッド構築1回は列
  全体近く（このファイルでは約700MB、`pyarrow.iter_batches`経由）をストリームするので、
  どのプールで実行しようがスレッド数がいくつあろうが、その読み込み自体が数秒のI/O待ち時間を
  要するだけであり、6回連続の`go_home()`でピークが増加していったのは、そのループの間に
  同じ約3.5秒が実時間で経過していただけで、スケジューリングの産物ではなかった。**専用プールは
  元に戻した**（実フェッチと同じ`self._thread_pool`のまま）——測定可能な効果もないまま複雑さだけ
  増えるため。これはスケジューリングの調整では埋められないギャップであり、本当に埋めるには
  ピラミッド構築そのものを速くする（バケット数を小さく/適応的にする、全ストリームの代わりに
  サンプリングする、実際にこれから見られる列/範囲を優先して構築する、等）必要がある——
  今回のパスでは着手していない。`uv run pytest`は167 passed, 1 skipped（変化なし——今回の調査は
  結果的にコード形状を元に戻しただけで、代わりに正しい理由がドキュメントに残った）。
- **Phase E = Step 4**（仕上げ、変更なし）: 粗い（ピラミッド）/精密（LTTB）表示の切り替えを
  ステータスバー等で明示、ファイル切り替え時のピラミッド構築ワーカーのキャンセル、設定ダイアログ
  への`eager_load_limit_mb`項目追加、ピラミッドがある以上意味をなさなくなる
  `set_downsample_enabled`（13.3章）の削除。

  **✅ 2026-09-19に実装・検証済み——1項目は計画を修正し、1項目は意図的に未実装とした（両方とも
  以下で説明）。** `Settings.eager_load_limit_mb`は`SettingsDialog`に項目を追加した
  （`eager_load_limit_spin`、1〜100,000MB、ツールチップで「変更後に開いたファイルからのみ
  反映される（既に開いているファイルの`Column`選択は開いた時点で決まっており遡って変わらない）」
  旨を明記）。ステータスバーの粗い/精密インジケータ: `TimePlotWidget`が`_coarse_series`
  （現在ピラミッドenvelopeで表示中の系列名。`_render_from_pyramid`/`_on_downsampled`/
  `remove_series`/`transfer_series`で更新）を追跡し、空⇔非空の遷移時のみ
  `coarseRenderingChanged(self, bool)`を発火する；`PlotGridWidget`は既存の`busyChanged`と同じ
  やり方で全プロットを集約する；`MainWindow._precision_label`（新規の常設ステータスバーウィジェット）
  が、いずれかのプロットに粗い系列が1つでもある間「簡易表示中…」を表示する。
  **13.3章の判断を修正**: Phase D以前の注記が予想していたのとは異なり、ピラミッドがあっても
  結局`set_downsample_enabled`は不整合にならなかった——`_redraw_series`はダウンサンプリングが
  有効な場合にのみピラミッドを優先する設計だったため、無効化すれば`WindowedColumn`系列に対して
  すら「常に実データを取得し、バケットenvelopeには一切頼らない」という明確な意味を持つ。そこで
  削除ではなく配線した: 新しいチェック可能なツールバーアクション（`toggle_downsample`、
  `MainWindow._downsample_enabled`、`_apply_downsample_enabled`経由で適用）を追加し、
  `y_axis_range`/`x_axis_range`と全く同様に`save_view_dialog`/`load_view`で往復する
  （永続化された`Setting`ではなくper-view状態——ビューを介さずに素のファイルを開くと既定値に
  戻る）。**ファイル切り替え時のピラミッド構築ワーカーのキャンセル: 検討した上で未実装とした。**
  `BackgroundWorker`/`QRunnable`には組み込みの割り込み機構が無く、既存の`_on_pyramid_ready`の
  ガード（`entry is None or entry["y_source"] is not y_source`）が既に、ファイル切り替え後に
  古いピラミッドが届いても安全なno-opになる（クラッシュしない）ことを、ファイル切り替えを
  構築途中で行う専用テストで確認済み。本当のキャンセル（ディスク読み込みをストリーム途中で
  止める）には`build_pyramid()`に協調的キャンセルのコールバックを渡して定期的にチェックさせる
  必要があり、これは実質的だが分離可能な変更である——このブレットが当初懸念していた
  正しさの問題自体はキャンセルが無くても解消しているため、「効率の問題であって正しさの問題では
  ない」既知のギャップとして記録するに留め、今回のパスでは着手していない。

  `tests/parquet_analyzer/test_settings_dialog.py`（`eager_load_limit_mb`用に1テスト拡張）と
  `test_windowed_loading.py`の新規テスト8件（表示インジケータが正しく表示/消灯すること；
  ツールバーのトグルが全プロットに適用されること；`downsample_enabled`が保存済みビューを
  経由して往復すること；素のファイルを開くと既定値に戻ること；ピラミッド構築途中でファイルを
  切り替えてもクラッシュしないこと）で検証。`uv run pytest`は173 passed, 1 skipped。
  `data/raw/daily_cycle_4gb.parquet`で手動確認: ファイルを開いて全体ズームアウトで1列プロットし
  ピラミッド構築を待つと精度ラベルが点灯し、ダウンサンプリングを無効化すると正しく実フェッチが
  強制されラベルが消灯した。

**両モデルが一致し、最終確認待ちで解決済み扱いとする方針決定**: 非単調な時刻列は
`time_axis_combo`から除外するのではなく、その列については強制的に即時読み込み/実体化経路に
フォールバックしステータスバーに注記する（除外は正当なデータを隠すことになるため）；サイズ閾値
は`Column`実装の選択にのみ使い、UIコードに分岐を作らない（Phase D）。2026-09-17時点で割れていた
FFTの対象範囲についてのOpus/Fableの対立は既に解消済み——`specification.md` 5.6章と
`main_window.py:48,371-383`は既に「可視範囲＋行数上限」を実装済みであり、今回の再調査でも
変更不要と確認した。

**新たな決定事項: 派生キャッシュ/インデックスデータ（ピラミッド、ブロック読み込みキャッシュ）
をディスク上のどこに置くか。** `data/raw/`配下の生Parquetファイルは変更禁止・隣にファイルを
作ることも禁止（ユーザー指示: raw データの加工は禁止。`data/raw/`は実運用では共有/読み取り専用の
計測データ置き場である可能性があり、sidecarファイルを置くだけでも「raw＝手を加えない元データ」
という前提を崩しかねない）。推奨: 新しい`data/parquet_analyzer/cache/`ディレクトリ（既存の、
既にgitignore対象・マシンローカルな`data/parquet_analyzer/`アプリデータルートの下に追加するだけ
——新しいトップレベルディレクトリも`config.py`の変更も、新しいサブパス1つ以外には不要）。
キーは内容ハッシュではなく`(解決済み絶対パス, ファイルサイズ, mtime)`とする（O(1)で計算でき、
ファイルがリネーム/再生成された場合も再構築コストは前述の実測（4列のピラミッドで1.74秒）程度で
安い）。これなら`data/raw/`は一切触らず（読み取り専用マウントでも安全）、キャッシュ全体を
安全かつ即座に削除でき（`rm -rf data/parquet_analyzer/cache/`）、同じ共有raw ファイルを複数の
マシン/ユーザーが開いても書き込み競合なく独立にキャッシュされる。未実装。Phase Bの
`core/pyramid.py`設計がその場しのぎの選択ではなくこの決定から始まるよう、ここに明記しておく。

段階的対策のPhase A〜E自体は実装・検証済み（各Phaseの「✅ 実装・検証済み」の記述を参照）。
すぐ上で提案したディスク上のキャッシュディレクトリだけが本章で唯一まだ未実装のまま残っている
項目であり、今回の広範な変更にまとめて含めず意図的に別途のフォローアップに先送りした。
テスト件数についての注記: 上記で各Phaseごとに参照している「79+」「82」「107」という数字は
それぞれの記述を書いた時点のもの。テストスイートは現在173件成功・1件スキップ（Phase Eの
数値、2026-09-19時点で最新）。

### 14. 統計表示（2026-09-17追加）

`specification.md` 5.12参照。選択した変数について、現在表示中の時間範囲の生データ全件から
件数・欠損値数・最小・最大・平均・標準偏差・中央値を計算し、別ウィンドウに表示する。

- **範囲決定は[13.5.1章](#1351-大容量ファイル4gbでのメモリ急増問題2026-09-17調査2026-09-19段階的対策実装済み)で
  FFT用に実装した`MainWindow._visible_row_range()`をそのまま共有する**（FFT実装時は
  `_on_fft_requested`内にインライン展開されていたロジックを、この機能追加のタイミングで
  メソッドとして切り出した）。時間軸プロットが1つも系列を持たない場合（開いた直後など）は
  ファイル全体にフォールバックする点もFFTと同じ。
- **FFTと異なり行数の上限を設けない**。`core/analysis.py`の`basic_stats(y)`は
  `min`/`max`/`mean`/`std`/`median`をNaN/Inf-safeに1回のnumpy走査（`median`のみ内部で
  ソートが必要）で計算するだけで、FFTのように「サンプリング間隔が不均一だと結果の意味が
  崩れる」という制約が無いため。ただし91,980,000行（`sensor_log_4y_4gb.parquet`）規模では
  `median`のソートコストにより実測0.6〜0.7秒かかることを確認済みなので、
  `core/background_worker.py`の`BackgroundWorker`（`QRunnable`、`setAutoDelete(False)`＋
  呼び出し元強参照パターン。ダウンサンプリング再計算・FFT・統計が共用）により非同期実行する。
- 複数変数を選択した場合、変数ごとに`BackgroundWorker`を1つ起動し（`MainWindow._on_stats_requested`）、
  全員の完了を待ってから`StatsDialog`を1回だけ構築して表示する（`_on_stats_finished`が
  `remaining`カウンタを見て最後の1件が完了した時にだけダイアログを作る）。結果の表示順は
  ワーカーの完了順ではなく、選択した順（`variable_panel.selectedItems()`の順）を保持する。
- **対象の決定: パネルでの明示的選択を優先し、無ければ現在プロット中の変数にフォール
  バックする**（2026-09-19変更、ユーザー要望——元の実装は既にプロットに表示中の変数
  であっても`variable_panel.selectedItems()`が空でないことを常に要求しており、二度手間に
  感じられた）。`MainWindow._on_stats_requested`: `variable_panel.selectedItems()`が
  空の場合、`MainWindow._plotted_variable_names()`（いずれかの**時間軸プロット**に
  現在重ねて表示されている変数全て——FFTプロットの系列は既にFFT出力であり生/演算変数
  ではないため除外。`_visible_row_range`が既に行っているのと同じ除外）にフォールバック
  する。プロット間の重複は除去し、プロット・系列の順序を保持する。パネルで明示的に
  選択している場合は常にそちらが優先されるため、プロットしていない変数を確認する
  従来の使い方はそのまま動作する。各候補名は（ワーカーごとのループ内で遅延評価するの
  ではなく）事前に`variable_window()`で解決し、`ExpressionError`/`KeyError`が出た
  ものはエラーダイアログを出さずスキップする（ログのみ）——パネル選択は常に既知の
  変数名のみなので現実的には`_plotted_variable_names()`経由でしか起こり得ないが、
  本書の前段の`load_view`修正と同じ「1件の不具合で残り全体を巻き込まない」という
  考え方に合わせた。1件も解決できなかった場合は0行のまま進めず情報ダイアログを表示する。
  回帰テスト: `test_stats_falls_back_to_plotted_variables_when_none_selected_in_panel`、
  `test_stats_prefers_explicit_panel_selection_over_plotted_variables`
  （`tests/parquet_analyzer/test_stats.py`）。
- `StatsDialog`（`ui/stats_dialog.py`）は`QDialog`だが`exec()`ではなく`show()`で非モーダルに
  開く（メイン画面を操作しながら結果を見比べられるようにするため。「別windowに」という要望通り）。
  `MainWindow`を親（`parent=self`）にして`WA_DeleteOnClose`を設定しているので、閉じれば
  破棄され、開いている間は`MainWindow`のライフタイムに紐づく。
- ダウンサンプリング（[10章](#10-ダウンサンプリング再計算の非同期化確定)）で描画される
  曲線は表示用に間引かれた点であり、統計計算はそれとは別に`_raw_variables`/派生変数から
  同じ行範囲を都度切り出して計算する（描画中の点を集計しても統計的に無意味なため。
  実際にLTTB間引き後の点数と統計対象の件数が異なることをテストで確認済み、
  `tests/parquet_analyzer/test_stats.py`）。

### 15. MDF/MATLAB → Parquet 変換ツール（2026-09-18追加）

`tools/convert_to_parquet.sh`（`src/parquet_analyzer/convert_cli.py`がCLI、
`core/convert.py`が実装）。本アプリはParquetしか読み込まないため、計測フォーマット
（MDF/MATLAB）からの変換を別ツールとして用意した。

#### 15.1 なぜ直接読み込みにしないか

MDF・MATLABはいずれも「計測・記録」用フォーマットで、Parquetのような列指向の
選択的読み込み（列プルーニング、row group統計によるフッタだけでの範囲把握）を
前提とした構造ではない。この点は[13.5.1章](#1351-大容量ファイル4gbでのメモリ急増問題2026-09-17調査2026-09-19段階的対策実装済み)で
検討した「Parquetのrow group統計に依存したメモリ対策」の設計とも相性が悪く、
アプリ本体にMDF/MATLAB読み込みを直接組み込むと、その種の効率化がそのまま使えなくなる。
「計測はMDF/MATLABのまま、分析前にParquetへ変換する」という2段構成にすることで、
既存の設計（polars/pyarrowベース、Parquetの構造に依存した効率化）を維持したまま対応する。

#### 15.2 依存関係の分離

`asammdf`・`scipy`・`h5py`はいずれも変換専用で、GUI本体は一切使わない。アプリの
主依存関係に混ぜると、通常のGUI起動でも重い依存（特に`asammdf`はさらに`pandas`・`lxml`
等を連れてくる）をインストールすることになるため、`pyproject.toml`の
`[dependency-groups]`に`convert`という別グループとして分離した（`uv sync --group convert`
で明示的にインストール）。`tests/parquet_analyzer/test_convert.py`は`pytest.importorskip`で
このグループが無い環境では自動的にスキップされ、`uv run pytest`（`dev`グループのみ）の
通常実行を壊さない。

#### 15.3 MDF変換

`asammdf.MDF.to_dataframe(raster=...)`を使う。MDFはチャンネルグループごとに
サンプリングレートが異なりうる（例: 高速チャンネルと低速チャンネルが同じファイルに
混在）ため、`to_dataframe`が全チャンネルを1つの時間軸に再サンプリングしてくれる
機能に依存している——これが無いと「行数が違う複数の系列を1つのParquetテーブルに
まとめる」問題を自前で解決する必要があった。`raster`（秒）を指定すればその間隔で
揃えられる。省略時はasammdf側の既定（全チャンネルのタイムスタンプの和集合）になる。

`to_dataframe`が返すインデックスは**記録開始からの相対秒**であり、そのままでは
`MainWindow._to_numeric`や`pg.DateAxisItem`が期待するepoch秒と噛み合わない
（西暦1970年付近の日時として表示されてしまう。詳細仕様書13.5.1章で見つけた
4GBテストファイルのタイムスタンプ型不整合と同種の罠）。`mdf.header.start_time`
（記録開始の絶対時刻）を相対秒に足し込んでepoch秒に変換してから書き出す
（`tests/parquet_analyzer/test_convert.py`の
`test_mdf_time_column_is_absolute_epoch_seconds`で回帰確認済み）。

#### 15.4 MATLAB変換

MATLABの`.mat`ファイルには「どの変数が時間軸か」という情報がファイル形式として
存在しない（単なる名前付き変数の集まり）。そのため:

- 1次元の数値配列である変数だけを候補とする（行列・構造体・文字列等は対象外）。
- 変数名が`time`/`t`/`timestamp`/`timestamps`（大文字小文字を区別しない）に一致する
  ものを時間軸として自動検出する。見つからない場合は`--time-var`での明示指定を要求し、
  エラーメッセージに候補の変数名一覧を含める（`ConversionError`、
  `core/expression.py`の`ExpressionError`と同じ「原因不明の例外ではなく分かる
  メッセージにする」という役割）。
- 時間軸と行数が一致しない変数は、警告を出して静かにスキップする（データを
  切り詰めたり0埋めしたりして辻褄を合わせることはしない）。

v5/v7形式は`scipy.io.loadmat`で読む。v7.3形式（2GB超の変数を保存する場合にMATLABが
自動的に使う、実体はHDF5）は`scipy.io.loadmat`が`NotImplementedError`
（"Please use HDF reader for matlab v7.3 files"）を投げるので、これを捕捉して
`h5py`ベースの読み込みにフォールバックする。両者は読み込み方法が異なるだけで、
その後の「時間軸の自動検出・行数不一致のスキップ」ロジック（`_select_time_and_columns`）
は共通化している。

### 16. カーソル（2026-09-19追加、ユーザー要望）

`ui/plot_widget.py`の`TimePlotWidget`と`ui/plot_grid.py`の`PlotGridWidget`が
specification.md 5.13章のカーソル機能を実装する。役割分担は: `TimePlotWidget`が
カーソルの**描画**（`pg.InfiniteLine`＋`pg.TextItem`のペア、最近傍サンプル探索）と
そのプロット単体での**ユーザー操作の検出**（クリックで追加/削除、ドラッグで移動、
表示/非表示切替）を担い、`PlotGridWidget`が**複数プロット間の同期**（あるプロットでの
カーソル追加/移動/削除を同じXドメインの他プロットへ伝播、新規プロット追加時に既存
カーソルを反映）を担う。

#### 16.1 設置と削除

`TimePlotWidget.__init__`は`self.scene().sigMouseClicked`を`_on_scene_mouse_clicked`
に接続し、`self._cursor_mode_enabled`（既定OFF。`MainWindow`のツールバー「カーソル」
トグル→`PlotGridWidget.set_cursor_mode_enabled()`→各プロットの
`set_cursor_mode_enabled()`で切り替え）と`ev.button() == Qt.MouseButton.LeftButton`
の両方で絞り込む。クリックがこのプロット自身の既存カーソル（線またはラベル）に
当たった場合（`ev.currentItem`——pyqtgraphの`GraphicsScene.sendClickEvent`は
`sigMouseClicked`自体は毎クリック無条件で発火する一方、実際にクリックを受け取った
アイテムを常にこれにセットする）はここでは無視する——削除は`pg.InfiniteLine`自身の
`sigClicked`（`mouseClickEvent`から発火。Qtは移動を伴わない press+release のときだけ
これを呼び、実際のドラッグは代わりに`mouseDragEvent`/`sigDragged`を呼ぶため、同じ
ジェスチャで両方発火することはない）が`_on_cursor_line_clicked`→
`cursorRemoveRequested`経由で処理する。それ以外（背景クリック）はシーン座標を
ビュー（データ）座標へ変換し（`ViewBox.mapSceneToView`）、`cursorAddRequested(self,
x)`として発する。

`PlotGridWidget._on_cursor_add_requested`は新規id（`f"c{n}"`）を割り当て、
`self._cursor_positions`に`(x_axis_datetime, x)`を記録し（後から追加されたプロットへ
既存カーソルを反映するための正本データ——16.3参照）、発生元プロットと同じドメイン
（`plot.x_axis_datetime == source.x_axis_datetime`。`add_plot`のXリンクと同じ
ドメイン判定則）の全プロットに`add_cursor(id, x)`を呼ぶ。削除も
`_on_cursor_remove_requested`が同様に処理する。

#### 16.2 ドラッグ・ラベル・表示/非表示

実際の移動処理は`pg.InfiniteLine(movable=True)`が内蔵する機能に任せ、
`TimePlotWidget`は`sigPositionChanged`（`_on_cursor_line_moved`）を購読するだけで、
そのカーソル自身のラベルを再計算しつつ`cursorMoveRequested(self, id, x)`を再発行する。
`PlotGridWidget._on_cursor_move_requested`はこれを受けて、同じドメインの**他の**
プロット（ドラッグが実際に起きたプロット自身は対象外——そこの線は既に新しい位置に
あるので、そこへも`setValue`し直すと同じシグナルを再度発火させてしまう）に対して
`set_cursor_x(id, x)`を呼ぶ。`set_cursor_x`は位置を正確に反映する間、線自身の
シグナルをブロックしてこのエコーを防ぐ。

カーソルのラベル内容（`_update_cursor_label`）は実際の追加/移動があったとき、または
そのプロット上の系列構成が変わったとき（`refresh_cursor_labels()`、
`_install_series`/`remove_series`/`transfer_series`から呼ばれる）にのみ再計算し、
`_on_range_changed`（マウスドラッグによるパン中に連続して発火する）からは呼ばない。
`WindowedColumn`系列の値取得（`_series_value_at`）は`y_source.window(row, row+1)`で
実際に（1行だけとはいえ）ディスク読み込みを行うため、パン操作のたびに毎フレーム
これを呼ぶと、本アプリの描画パスがこれまで注意深く避けてきた「UIスレッド上での
フレーム毎I/O」によるカクつきを再導入しかねない（10章・13.5.1章）。そのため
`_on_range_changed`からは`_reposition_cursor_overlays()`のみを呼び、既存のラベル
アイテムの位置（現在のY表示範囲の上端付近に固定しているため、パン/ズームで置き
去りにならないよう追従させる）だけを更新し、テキストには触れない。

`set_cursor_mode_enabled(enabled)`は既存の全カーソルの`line`/`label`に対して
`setVisible(enabled)`（および`line.setMovable(enabled)`）を呼ぶだけで、削除も
「その場に固定」もしない: ツールバーのトグルをOFFにするとカーソルは完全に非表示になり
（通常のパン/ズームをしている最中の画面を邪魔しない）、再度ONにすると同じ`x`のまま
復元される——位置自体は一度も変更していないので、別途「復元」の処理は不要。

#### 16.3 複数プロット間の同期とクリア

`PlotGridWidget._cursor_positions`（`cursor_id -> (domain, x)`）は各
`TimePlotWidget._cursors`とは別に、調整役自身が持つ記録である。`add_plot()`は
ドメインが一致する全ての位置を新規プロットに反映するので、カーソル設置**後**に
追加されたプロットにも既存カーソルが表示される。

カーソルのX値はそれを設置した時点の時間軸列に対してのみ意味を持つため、
`TimePlotWidget.set_x_data()`（時間軸列の切替）と`MainWindow._clear_plot_grid()`
（新規ファイルを開く）はいずれも、それぞれ`TimePlotWidget.clear_cursors()`と
`PlotGridWidget.clear_cursors()`経由で全カーソルを破棄する——もはや何にも対応しない
数値位置のまま残すのではなく。

### 17. バージョン埋め込み（2026-09-20追加、ユーザー要望）

`pyproject.toml`の`version`フィールドを正とし、`parquet_analyzer/__init__.py`の
`__version__`文字列定数に手動で反映する（実行時に片方からもう片方を導出するのでは
なく、手動同期という単純な形を選んだ——本プロジェクトは配布パッケージとしてインストール
されない（`[tool.uv] package = false`）ため`importlib.metadata.version()`では参照先が
存在せず、`pyproject.toml`自体を実行時にパースする方式も可能ではあるが、滅多に変わらない
文字列1つのためにパース処理を追加するほどではないと判断した）。`__init__.py`が
（これまでの空ファイルから）1行の定数定義を持つようになった点は、config.pyが謳う
「依存関係なしでimportできる」という保証には影響しない——単なる文字列リテラルであり、
PySide6/pyqtgraph/polarsを引き込むものではないため。

この値を参照する箇所は2つ: `__main__.py`の`--version`フラグ（`argparse`標準の
`action="version"`を使い、`parquet_analyzer <version>`を出力して終了する）と、
`ui/main_window.py`の`_APP_TITLE`（`"Parquet Analyzer v<version>"`という表記になり、
起動するたびウィンドウタイトルバーに表示される）。`tools/publish_to_public.sh`で
作られた公開/派生先のリポジトリでも同様に表示されるため、コピー先で不具合報告を受けた際に
`pyproject.toml`を手動で確認してもらわなくても、どのバージョンかをタイトルバーだけで
特定できる。
