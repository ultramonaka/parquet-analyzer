# Persistence (views, settings, layout)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: views (saving, loading, the JSON schema, the manager window), the
settings file and settings menu, persisting the window layout.

## Requirements

#### 5.8 Saving views
- The current screen layout (plot arrangement, displayed variables, expressions, axis ranges, …)
  can be saved as a "view".
- Location: `data/parquet_analyzer/`
- Format: **JSON** (readability and extensibility first; a binary format was judged unnecessary).
- A saved view contains a relative/absolute path reference to the source Parquet file it was
  loaded from, and the same data is accessed again when it is loaded.
- However, if **another file is already open** when a view is loaded, that file is not reopened;
  only the plot arrangement (displayed variables, expressions, axis ranges) is applied to the file
  currently open (changed 2026-09-17; background in `detailed_specification.ja.md` ch. 8). The use
  case is reusing one layout across several files with the same column names.
- Also saved in a view, added 2026-10-02: each plot's height ratio ([plots 5.15](./plots.md)),
  each series' shown/hidden state ([plots 5.17](./plots.md)), whether each plot's X axis is linked,
  and the X range of unlinked plots ([plots 5.18](./plots.md)). Even if a derived variable uses
  user functions or maps ([expressions 5.20](./expressions.md), [5.21](./expressions.md)), the view
  stores only the expression (including the function and map names), not copies of the
  definitions.
- **Managing views** (added 2026-10-01, user request): "Load view" opens a view manager window
  instead of a names-only list. It lists the saved views and, for the selected view, shows its
  **contents** before loading: source file, each plot with its channels (variables) and their
  colors, derived variables and their expressions, the saved time range, and when it was saved.
  When a file is open, channels the open file doesn't have (= skipped on load) are marked. Views
  can be **loaded** or **deleted** from the same window (deleting, after a confirmation, removes
  the JSON file and can't be undone). Unreadable view files (corrupt, hand-edited, …) are listed
  too, showing the error instead of the contents, and can be deleted. Details in
  [detailed_specification ch. 20](#20-view-manager-window-added-2026-10-01-user-request).

#### 5.10 Settings menu
- A settings dialog opened from the toolbar changes all of the following together:
  - Auto-redraw on/off (`auto_redraw`)
  - Downsampling density (`downsample_pixel_ratio`)
  - The size at which a column is read on demand instead of kept resident
    (`eager_load_limit_mb`; applies to files opened after the change)
  - Keyboard shortcuts for each toolbar action (home, redraw, open, add plot, statistics, save
    view, view manager, save image, coarse-preview toggle, cursor; frequency analysis only while
    enabled)
  - Which scroll-wheel modifier does which operation (`scroll_bindings`)
  - **UI language** (`language`; `English`/`日本語`, `English` by default on a new install —
    added 2026-09-19, user request; takes effect at the app's next start)
- Settings are saved in `cfg/parquet_analyzer/settings.json` and carried over to the next start.
- Details in [detailed_specification 9.1](#91-settings-menu).

#### 5.11 Layout persistence
- The screen layout — window size, the variable panel's (dock's) position, the toolbar position,
  etc. — is saved in `cfg/parquet_analyzer/settings.json` and restored at the next start.
- The variable panel's default position (first start / nothing saved) is the **left side of the
  window**.
- "Reset layout" on the toolbar discards the saved layout and restores the default arrangement.
- Details in [detailed_specification 9.3](#93-layout-persistence).

### 7. Settings (cfg/)

- App settings such as downsampling thresholds and density, auto-redraw on/off, shortcut keys,
  recently used folders and recently opened files are saved as JSON under `cfg/parquet_analyzer`.
  Schema details in
  [detailed_specification ch. 9](#9-settings-file-cfgparquet_analyzersettingsjson).

#### 5.26 Saving the time-axis column in a view (added 2026-10-02, from fact-checking an external review)
- Views currently don't save which column was used as the time axis. Opening a view saved with
  another column as the time axis puts the time axis back on the file's first column — "saved,
  but different". **Save the time-axis column in the view, and use that column as the time axis
  when opening it** (if the file doesn't have that column, keep the current time axis and add a
  line to the consolidated warning).
- Details in [ch. 33](#33-saving-the-time-axis-column-in-a-view-added-2026-10-02-from-fact-checking-an-external-review).

#### 5.27 Saving the view's file location as a relative path too (added 2026-10-02, from fact-checking an external review)
- Views currently save the source Parquet file as an absolute path, so moving the folder elsewhere
  or to another PC makes them unopenable. **Save a path relative to the view file in addition to
  the absolute path**, and when opening, try the absolute path → the relative path (this resolves
  the `path_type="relative"` item in remaining task 13.3).
- Details in [ch. 34](#34-saving-the-views-file-location-as-a-relative-path-too-added-2026-10-02-from-fact-checking-an-external-review).

#### 5.28 Validating the shape of view files (added 2026-10-02, from fact-checking an external review)
- For a hand-edited view file whose items have the wrong type (e.g. `"plots": "hello"`), show an
  error message that says "where it is wrong" (it doesn't crash today, but the message is the raw
  exception text and hard to understand). Low priority.
- Details in [ch. 35](#35-validating-the-shape-of-view-files-added-2026-10-02-from-fact-checking-an-external-review).

## Design

### 8. View JSON schema

An example of the format saved in `data/parquet_analyzer/<view_name>.json`
(`source.parquet_path` points at the real test data `data/raw/sensor_log_4y.parquet` [4 years,
recording a 1-minute burst at 10 ms intervals every 16 hours, about 13.15 million rows]):

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

- `derived_variables` stores the expressions themselves, not computed results (see
  `specification.ja.md` 5.5).
- **Keys added 2026-10-02** (all optional; without them the old behavior applies. `schema_version`
  stays `"1"` — they are additions only, and an older app just ignores them):
  - `plots[].height_ratio` (number) — 22.2.
  - `plots[].x_linked` (bool) and `plots[].x_axis_range` (`[lo, hi]`, only for unlinked plots) —
    24.5. The top-level `x_axis_range` is the linked group's range.
  - `plots[].series[].visible` (bool) — 23.5.
  - Derived variables using user functions or maps also store only the expression (26.5, 27.5).
  - `load_view` in `io/view.py` currently builds `PlotDef`/`SeriesDef` with hard-coded keys
    (`SeriesDef(**s)` raises on an unknown key). Change it to read new keys with
    `.get(..., default)` and **ignore unknown keys** (so adding keys later doesn't make views
    unreadable for older apps).
- If `source.path_type` is `relative`, it is resolved relative to `view_name.json`
  (`../raw/sensor_log_4y.parquet` as seen from `data/parquet_analyzer/sensor_overview.json`).
  However, `MainWindow.save_view_dialog` currently always saves with `path_type="absolute"`, so
  the `relative` branch exists as logic in `io/view.py` but no path from the UI uses it yet
  (ch. 13).
- `y_axis_range`/`x_axis_range`: `save_view_dialog` reads the Y/X range each plot was actually
  showing when saving and writes it, and `load_view` applies it when restoring. Previously the
  fields existed in the schema but were never written or read (a bug where "Save view" didn't save
  the current zoom/pan; found and fixed in review).
- `view_name` is used as the file name as is, so `io/view.view_path()` rejects with `ValueError`
  any name that doesn't equal `Path(view_name).name` (containing `/` separators, `..`, empty,
  etc.). Previously these went through, and a view name starting with `/` wrote outside the save
  directory (found and fixed in review).
- **If a file other than the one `source.parquet_path` points to is already open, `load_view`
  doesn't reopen anything and applies only the plot arrangement (overlaid variables, colors, axis
  ranges, derived-variable expressions) to the file currently open** (changed 2026-09-17,
  `MainWindow.load_view`). The use case is reusing one layout across several files with the same
  column names. If nothing is open, or the open file matches `source.parquet_path` (compared as
  absolute paths), it is loaded with `load_parquet` as before. Even when only the layout is
  applied, `_derived` (derived variables) is cleared first and the view's `derived_variables` are
  re-applied (if derived variables from before switching files remained in `_raw_variables`, the
  view's derived variables of the same name would be wrongly judged as "conflicting with an
  existing column").
- **Applying a view's derived variables and series to the open file used to be all-or-nothing:
  one missing variable aborted every later derived variable, series and plot** (found and fixed
  2026-09-19, `MainWindow.load_view`). Example: reusing a view with plot 1 = `x`, plot 2 = `y`
  and derived `z = x + 1` on a file that has `y` but not `x` — evaluating `z` (source `x` missing)
  raised an `ExpressionError` out of the whole rebuild, never reaching plot 2's `y` series that
  could have been applied, and as a result **nothing was shown**. This directly contradicts the
  stated purpose of reusing layouts ("reuse one layout across several files with the same column
  names") — two files rarely share columns exactly, and the feature only makes sense if partial
  matches are tolerated. Fix: instead of wrapping the whole rebuild in one `try`, each derived
  variable and each series is wrapped in its own `try`/`except (ExpressionError, KeyError)`, and
  what couldn't be applied is collected into one warning at the end. A plot left with no series
  (except `plots[0]` — the grid always keeps at least one plot) is removed rather than left empty.
  Verified: when the derived variable's source columns exist in the newly opened file, it is
  correctly recomputed from the new file's own data rather than stuck with the view's original
  file's values. Regression tests:
  `test_load_view_applies_matching_variables_even_when_others_are_missing`,
  `test_load_view_recomputes_derived_variables_from_the_newly_opened_file`
  (`tests/parquet_analyzer/test_load_robustness.py`).
- **`load_view` also called `load_parquet` (a full re-read from disk) when the open file was
  exactly the source file the view records, just as when nothing was open** (found and fixed
  2026-09-19, `MainWindow.load_view`). Pointed out by the user: applying a view to the very file it
  was saved from gives no reason to reopen it — the view replaces the plot arrangement and derived
  variables entirely anyway, and nothing about the open file needs to change. This unconditional
  reopen was especially wasteful for 13.5.1's lazy/windowed columns: every already materialized
  `LazyColumn` was discarded and the whole file was read again from the start, for no difference
  in behavior. Fix: the "same file already open" case was merged into the layout-only path for
  "another file already open" (previously the only path that skipped `load_parquet`), and
  `load_parquet` is called only when `self._data_source is None` (= nothing open). Regression test:
  `test_load_view_does_not_reopen_a_file_already_open` (uses `monkeypatch` to check that
  `load_parquet` is never called and that `_data_source` stays the same object rather than being
  replaced by a new `ParquetDataSource`).
- **The name of the open file wasn't reliably visible** (added 2026-09-19, also a user request).
  `MainWindow._set_title` already put it in the window title, but the title bar is easy to miss
  (it can be hidden when the window is maximized or under some window managers/remote desktops),
  so the same text is also shown in a status-bar label (`MainWindow._file_label`, placed on the
  left with `addWidget` rather than `addPermanentWidget`, which the progress/precision indicators
  on the right use). It shows `"File: <name>"` with the resolved full path as its tooltip, and is
  empty while no file is open. Regression test:
  `test_opening_a_file_shows_its_name_in_the_title_and_status_bar`.

### 9. Settings file (`cfg/parquet_analyzer/settings.json`)

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
    "toggle_downsample": "",
    "cursor": "",
    "export_image": ""
  },
  "window_geometry": null,
  "window_state": null,
  "last_data_folder": null,
  "recent_data_folders": [],
  "last_export_folder": null,
  "default_overlay": false
}
```

- `eager_load_limit_mb`: see Phase E in 13.5.1.
- `scroll_bindings`: see the v3 background in [plots ch. 4](./plots.md).
- `window_geometry` / `window_state`: see [9.3](#93-layout-persistence).
- `auto_redraw`: when `false`, the downsampling recompute is not run automatically when pan/zoom
  changes the visible range (only manual redraw via the R button/shortcut). An escape hatch when
  automatic recompute is a burden while panning/zooming large data often. The first display of
  newly added data always happens regardless of this setting (`add_series` in
  [ui/plot_widget.py](../overview.md)).
- `shortcuts`: action ID → `QKeySequence` string (empty means unbound). Edited from the settings
  dialog in [9.1](#91-settings-menu). On load, action IDs that didn't exist when the file was
  saved are filled with defaults (`load_settings` in `io/settings.py`).
- `last_data_folder` / `recent_data_folders`: see [data ch. 7](./data.md). `recent_data_folders`
  is an MRU list of at most 5 entries, and `last_data_folder` equals `recent_data_folders[0]`
  (reading the first entry would do, but it is kept as an explicit field to make the intent clear
  to readers).
- `last_export_folder`: the folder the plot image was last saved to (19.3), used as the default
  folder next time. `null` until the first save.

#### 9.1 Settings menu

- The toolbar's "Settings" button opens `SettingsDialog` (`ui/settings_dialog.py`).
- Editable items: auto-redraw (`auto_redraw`), downsampling density (`downsample_pixel_ratio`),
  the large-column threshold (`eager_load_limit_mb`), the scroll-wheel modifier assignment
  (`scroll_bindings`), each action's shortcut (`QKeySequenceEdit`), UI language (`language`, added
  2026-09-19, user request; details below).
- Confirming with OK makes `MainWindow.apply_settings()` apply the new `Settings` to
  `PlotGridWidget` and each `QAction`'s shortcut and save `cfg/parquet_analyzer/settings.json`.
  Cancel changes nothing (the dialog edits a copy and applies it only on confirmation).
- **UI language** (`Settings.language`, `"en"` or `"ja"`, `"en"` by default on a new install):
  `ui/i18n.py` keeps every UI string in a dictionary of the form `{key: {"en": ..., "ja": ...}}`
  and provides `tr(key, **kwargs)` (with str.format placeholders) and module-level state via
  `set_language()`/`get_language()` — a small custom dictionary lookup rather than Qt's standard
  `QTranslator`/`.ts`/`.qm` toolchain (for this app's amount of UI text, adding
  `pyside6-lupdate`/`lrelease` as build dependencies wasn't judged worth it). `MainWindow.__init__`
  calls `set_language(self.settings.language)` right after loading the settings, before building
  the first widget that calls `tr()`. Changing the language in the settings dialog takes effect
  only at the app's next start (the combo's tooltip `settings.language_restart_note` says so) —
  there is deliberately no mechanism to re-translate existing widgets in place (re-running every
  `_build_*` method or wiring `Qt.LanguageChange` events isn't worth the complexity for a setting
  that is rarely changed). The display-label dictionaries that used to be in `io/settings.py`
  (`SHORTCUT_LABELS`, `SCROLL_ACTION_LABELS`, `SCROLL_MODIFIER_LABELS`) moved to translation keys in
  `ui/i18n.py` (`shortcut.*`, `scroll_action.*`, `scroll_modifier.*`), and only the IDs
  (`DEFAULT_SHORTCUTS`/`SCROLL_ACTIONS`/`SCROLL_MODIFIERS`) remain in `io/settings.py` — in line
  with the existing policy that io/ holds no UI strings. The messages of `ExpressionError` in
  `core/expression.py` (internal/diagnostic text such as syntax errors and disallowed function
  names) are deliberately left in English regardless of `language` (not part of the translated UI
  string table).

#### 9.3 Layout persistence

- The `QByteArray`s returned by `QMainWindow.saveGeometry()` / `saveState()` are base64-encoded
  and saved in `window_geometry` / `window_state` (JSON can't hold binary directly). `saveState()`
  holds the dock/toolbar arrangement and sizes, `saveGeometry()` the window's own position and size.
- For `saveState()`/`restoreState()` to work, the target `QDockWidget`/`QToolBar` must have a
  unique `objectName` (a Qt requirement). It is set explicitly on `MainWindow._variable_dock`
  (`"variable_dock"`) and the main toolbar (`"main_toolbar"`).
- The variable panel's default position is the **left side** of the window
  (`Qt.DockWidgetArea.LeftDockWidgetArea`).
- Startup sequence: right after `_build_layout()`/`_build_actions()` build the default layout, the
  **state before restoring** is kept as `self._default_geometry`/`self._default_state`, and then
  `_restore_layout()` restores the saved settings (if `window_geometry`/`window_state` is `None`,
  nothing happens = stays default).
- "Reset layout" on the toolbar (`MainWindow.reset_layout()`) goes back to the kept
  `_default_geometry`/`_default_state` and saves `window_geometry`/`window_state` cleared to
  `None`.
- Saved in `closeEvent` (`MainWindow._save_layout()`): every time the window closes, the current
  layout is written back to `settings.json`.

### 20. View manager window (added 2026-10-01, user request)

(2026-10-02) Derived variables that use a non-existent user function or map (and what depends on
them) are added to the missing marks (26.5, 27.5). Keep the same rules as what `load_view`
actually skips.

Requirement: `specification.ja.md` 5.8 ("Managing views"). Background: views could be saved and
loaded but not deleted (the JSON had to be removed by hand), and the load dialog
(`QInputDialog.getItem`) showed only names, so there was no way to know which channels a view
contained before loading it.

#### 20.1 `io/view.py`

- `delete_view(view_name) -> None`: `view_path(view_name).unlink()` (the same name validation
  applies). `FileNotFoundError` if it doesn't exist.
- `view_saved_at(view_name) -> datetime`: the JSON file's modification time (local time). The view
  schema has no saved-at field, and none is added (no schema change).

#### 20.2 `ui/view_manager.py` — `ViewManagerDialog(QDialog)`

- Built from only the data it needs to display: `available_columns: set[str] | None` (the open
  file's columns **minus the current time-axis column**; `None` if no file is open). The time-axis
  column is excluded because `load_view` resolves series and expressions against `_raw_variables`,
  which doesn't contain it, so a view showing or referring to the time-axis column has that item
  skipped (fixed during the check: the original spec said "including the time-axis column"). It
  never touches `MainWindow`'s state itself: loading and deleting are communicated through the
  return value / signals (below).
- Layout: on the left a `QListWidget` of `view_io.list_views()` (already sorted as returned), on
  the right a read-only `QTreeWidget` with the selected view's details, and at the bottom **Load**,
  **Delete** and **Close** buttons. The first view is selected when opened. Double-clicking a view
  = load. Load/Delete are disabled when nothing is selected. With no saved views: the list is
  empty, the details show `view.none_saved`, and Load/Delete are disabled.
- Details tree (top-level rows and indented child rows), built from `view_io.load_view()`:
  - `Source: <parquet_path>` (tooltip: path type).
  - `Saved: <YYYY-MM-DD HH:MM:SS>` (from `view_saved_at`).
  - `Time range: <start> – <end>` if there is `x_axis_range`, in the same format as the cursor
    labels (`%Y-%m-%d %H:%M:%S.mmm`, local time via `datetime.fromtimestamp`).
  - One row per plot, `Plot <n> (<k> channels)`, expanded. Child rows, one per series: the
    variable name with a small color swatch icon of the saved `color`.
  - If there are derived variables, `Derived variables (<k>)`, expanded. Child rows
    `name = expression`.
  - `Downsampling: enabled/disabled`.
- Missing-channel marks (only when `available_columns` isn't `None`), judged **transitively**,
  the same chain as `load_view` really follows: the view's derived variables are visited in
  `core.variable.dependency_order` order; one is *resolvable* if its expression parses and every
  name it references (`core.expression.referenced_names`) is in `available_columns` or is a derived
  variable already judged resolvable, otherwise it is "missing". A series is "missing" if its
  variable is neither in `available_columns` nor a resolvable derived variable. So if `d1` is
  missing, `d2 = d1 + 1` and any series showing `d1` or `d2` are all marked (fixed during the
  check: the original spec looked only at direct references and under-reported what is actually
  skipped on load). Missing rows: red text, suffixed ` — not in the open file`, with a tooltip
  saying it will be skipped on load. Only when m > 0, a summary row at the top:
  `Channels not in the open file: <m>`. This mirrors `MainWindow.load_view`'s actual skipping
  (ch. 8) exactly and is never stricter.
- An unreadable view (`view_io.load_view` raises anything): the details are a single red line,
  `Cannot read this view: <error>`. Load is disabled; Delete stays enabled.
- Delete: `QMessageBox.question` (`view.delete_confirm`, including the view name). On yes,
  `view_io.delete_view`, remove it from the list, select the next view (or the previous one if it
  was last), and emit `viewDeleted(str)`. `OSError` → warning `view.delete_failed`, list unchanged.
- Load: `accept()`, and `selected_view_name()` returns that name. `MainWindow` calls
  `load_view(name)` as before (the behavior of ch. 8 is unchanged).

#### 20.3 `MainWindow`

- `load_view_dialog()` opens `ViewManagerDialog` (modal, `exec()`), passing the set of column
  names of `self._file_columns` minus the time-axis column, or `None`. If accepted, `load_view(name)`.
  The toolbar label is `View...` / `ビュー...` (i18n `toolbar.load_view`). The shortcut ID stays
  `load_view` (no settings migration needed). The old early return with an information dialog
  when "there are no saved views" is removed — the dialog's own empty state is enough.
- `viewDeleted(name)`: if `name == self._view_name`, clear `_view_name` (so an image export's
  annotation doesn't show a view name that no longer exists; 19.3). `log_op("delete_view", name=...)`.
- i18n: all of the strings above go through `tr()` (en + ja).

### 33. Saving the time-axis column in a view (added 2026-10-02, from fact-checking an external review)

Requirement: 5.26 (external review #9).
- `View.time_column: str | None` (JSON `time_column`, default `None`). `save_view_dialog` stores
  `MainWindow._time_column`. It is only an added key, so `schema_version` stays (the policy of
  ch. 8).
- `load_view`: **before** rebuilding the layout, if there is a `time_column`, it is among the open
  file's columns and differs from the current time axis, call `set_time_column(name)` (unless the
  time axis is switched before applying series and ranges, the saved X range would be interpreted
  as values of a different column). If the column is missing, don't switch, and add "time-axis
  column <name> not found" to the consolidated warning. If `None` (an older view), do nothing.
- The view manager (ch. 20) shows "Time axis: <column>" in the details and marks it missing if the
  open file doesn't have it (the same rule as `load_view`'s actual check).
- Tests: save with a non-first column as the time axis → load with another column as the time axis
  → it goes back to the saved column; the saved X range is restored correctly as values of that
  column; a file without the column warns and keeps the current time axis; an older view; the view
  manager's display and marks.

### 34. Saving the view's file location as a relative path too (added 2026-10-02, from fact-checking an external review)

Requirement: 5.27 (external review #10, remaining task 13.3).
- Add `relative_path: str | None` to `SourceDef` (relative to the view JSON's location,
  `os.path.relpath`; `None` if it can't be made, e.g. on another drive). `parquet_path` (absolute)
  and `path_type="absolute"` are written as before (so older apps can still read it).
- When opening (`resolve_source_path`): the absolute path if a file is there; otherwise
  `relative_path` resolved from the view JSON's location if a file is there; otherwise the same
  error as before. Which one was used is logged (in the operation log's `details`).
- The existing `path_type="relative"` branch (where `parquet_path` itself is relative) stays.
- The remaining-task 13.3 item "the `relative` branch of `path_type` is never reached" is resolved
  by this chapter and moved to `history.md`.
- Tests: opens from the same place; opens after moving the folder (the `data/` with data and
  views) elsewhere; the absolute path wins; the error when neither exists; when the relative path
  can't be made (`relative_path=None`).

### 35. Validating the shape of view files (added 2026-10-02, from fact-checking an external review)

Requirement: 5.28 (external review #8). Priority: low.
- `load_view` in `io/view.py` checks the types of the top level, `source`, `plots[]`, `series[]`
  and `derived_variables[]`, and on a mismatch raises `ViewFormatError(ValueError)` in the form
  "<item path>: expected <type> (got <actual type>)" (e.g. `plots: expected a list (got a
  string)`). Unknown keys are ignored as before.
- `MainWindow.load_view` and the view manager show this message as is.
- Tests: a wrong type for each item, missing required items, `null`; a valid view still loads.

#### 35.1 Corrupt values, failed writes (added 2026-10-03, from bugs found while strengthening the tests)

Principle: **if one item is corrupt, still load the rest of the view or settings. A failed save
never damages the previous file. A failed save never crashes the screen.**

- Views:
  - If `x_axis_range`/`y_axis_range` is non-finite (NaN, inf), drop just that range (previously
    pyqtgraph raised and the later plots and series weren't restored). Finite extreme values such
    as ±1e300 are loaded as now (the display collapses, but it doesn't happen unless edited by
    hand, so how to handle it is left undecided).
  - If `downsample.enabled` isn't a bool, `true`. If a series color can't be parsed by Qt, an
    automatic color.
  - A leading BOM is allowed (`utf-8-sig`). Names containing a NUL character are rejected.
  - Saving uses a temporary file + `os.replace` (on failure the existing view of the same name
    survives). An `OSError` while saving (name too long, data folder not writable, disk full) shows
    a warning.
- Settings (`settings.json`; app ch. 12's "defaults if corrupt" made per item):
  - An item with the wrong type or range falls back to its default **for that item only** (the
    ranges are those of the settings dialog's fields: `downsample_pixel_ratio` 1–20,
    `eager_load_limit_mb` 1–100000). A BOM is allowed.
  - `save_settings` doesn't raise on failure; it logs and returns `False` (an exception in
    `closeEvent` on exit used to make the window impossible to close). **Only when saving fails
    from the settings dialog's OK** is a warning shown (decided 2026-10-03: the user explicitly
    saved, so they are told; saves in the background — on exit, when opening a file — only log).
- Image export: any exception from matplotlib becomes a warning (no crash). A non-finite range
  warns as now (the on-screen range never becomes non-finite, so it doesn't happen in practice).
- Operation log: a record isn't lost even if an argument contains a lone surrogate (an undecodable
  file name) or an object with a broken `__str__`. The log viewer shows malformed records raw, one
  at a time.

## Relations to other domains

- **Plots** ([plots.md](./plots.md)): most of a view's content is the plot configuration. When the
  plots gain state, keep the schema, saving and loading in step.
- **Expressions** ([expressions.md](./expressions.md)): the save order of derived-variable
  expressions (dependency order), skipping when functions or maps are missing.
- **Data** ([data.md](./data.md)): handling of the source file (a file already open is not
  reopened).
- **App foundation** ([app.md](./app.md)): paths (`config.py`), handling a corrupt settings file
  (ch. 12).
- **Data** (ch. 33, 2026-10-03): the view's time-axis column switches the time axis.
- **App foundation** (ch. 30): the log retention (`log_retention_days`) is saved in the settings and
  changed from the settings dialog.
- **Expressions** (ch. 26, 27): the view manager's "missing" marks also consider whether functions
  and maps exist and a static check of their arguments.
