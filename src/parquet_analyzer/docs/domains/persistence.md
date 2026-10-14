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

#### 5.42 Overwriting an existing view by choosing it when saving (added 2026-10-13, user request)
- Today, "View ▾ → Save..." requires typing the name every time, and overwriting the same view means typing the same name again.
  Also, typing a name that already exists **overwrites it without confirmation**.
- In the save dialog, the user can **type a new name**, or **choose an existing view from the list**.
  - The initial value is the name of the current view (the view last loaded or saved). If there is no current view, it is empty.
  - When an existing name is chosen (or typed), **an overwrite confirmation is always shown**, even when it is the same name as the current view
    (2026-10-13, user decision).
- The content saved, the file format and the naming rules do not change.
- Details in [ch. 49](#49-overwriting-an-existing-view-by-choosing-it-when-saving-added-2026-10-13-user-request).

#### 5.47 Signal aliases (names that exist only inside this app) (added 2026-10-14, user request)
- Even for the same kind of measurement, the signal name can differ between files (for example `EngSpd` and `NE_rpm`). Each time, a view's
  signal is not found (today only a warning is shown), and the view has to be rebuilt by hand. Also, the user wants to refer to hard-to-read
  column names by another name that exists only inside this app.
- **One dictionary of aliases** for the whole app (2026-10-14, user decision): a list of "alias ← candidate original column names (in priority order)".
  When a file is opened, the first candidate that exists in that file gives the alias. The file itself is not rewritten.
  - Views are saved with the alias, so the same view works as is on files whose signal names differ.
- For a column that has an alias, the variable list and similar places show **only the alias** (the original name can be seen in the tooltip).
  The original name can still be used in expressions.
- The dictionary is edited (added, edited, deleted) in a **dedicated editing window** ("Signal aliases"). It can also be opened from the right-click
  "Add alias..." on a column in the variable list.
- When a view is loaded and a signal is not found, the warning offers "**Assign a substitute signal...**", which lets the user choose a column of the
  current file for each missing name. The chosen assignments are added to the dictionary, and the view is reloaded.
- Each alias can be turned on or off as "enabled", and a global switch "Use signal aliases" turns them all off at once (the definitions are kept;
  2026-10-14, user request, 54.9).
- Details in [ch. 54](#54-signal-aliases-added-2026-10-14-user-request).

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
- **Wording of the grouped warning** (2026-10-09, backlog item 33; found by the tests of old views in A4): previously, a derived variable that
  could not be restored appeared **twice**, once as "derived variable" and once as "the series that drew it", and the heading did not fit when
  the cause was a missing function or map. The fixed form: the heading is "View "…": the following items could not be restored (the rest has been
  restored)". Each item appears once (a derived variable that was skipped does not also list its series). Items whose reason is known get it in
  parentheses: a missing function or map → "function/map "…" is missing"; a series that is in neither the file nor the derived variables → "not in the
  open file"; the time-axis column → one line as before. Regression test:
  `test_load_view_warning_lists_each_skipped_item_once_with_its_reason`
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
  (2026-10-13) The toolbars are now two, `"file_view_toolbar"` and `"analysis_toolbar"`, and `saveState(1)`/`restoreState(state, 1)` attach a
  version number. A saved state that contains the old `main_toolbar` is discarded and the default arrangement is used (app foundation 48.3).
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

### 49. Overwriting an existing view by choosing it when saving (added 2026-10-13, user request)

Requirement: [5.42](#542-overwriting-an-existing-view-by-choosing-it-when-saving-added-2026-10-13-user-request).

#### 49.1 Entering the name (`MainWindow.save_view_dialog`)
- Replace `QInputDialog.getText` with `QInputDialog.getItem(self, tr("toolbar.save_view_title"), tr("view.name_label"), items,
  current, editable=True)`. `items` is `view_io.list_views()` (sorted). If the current view `self._view_name` is in the list, its
  position becomes `current`; if not (unsaved or deleted), an empty item `""` is added at the top of the list and selected
  (so that the combo's first display is empty; an empty item is not accepted as a name).
  - Name entry is collected in a small function `_ask_view_name() -> str | None` (cancel or empty gives `None`; leading and trailing spaces are removed).
- Overwrite confirmation: if the name is in `list_views()` (even if it is the same as `self._view_name`), show the `messages` confirmation
  (`view.overwrite_confirm`, "\"{name}\" already exists. Overwrite it?"). If "No", nothing is saved (the name entry is not shown again; start
  again from "Save...").
- Name checks, saving, the handling of failures, `log_op("save_view", ...)`, and updating `self._view_name` after saving stay as they are.
  `overwrite=True/False` is added to the `log_op` record.
- Dialog title: the current `toolbar.save_view` (it became "Save..." in ch. 48) is not suitable as a title, so the new key
  `toolbar.save_view_title` ("Save view") is used. Other message titles in `save_view_dialog` use the same key.

#### 49.2 Test plan
- The list shows the view names; when the current view exists it is selected first, and when it does not, the empty item is selected (check the arguments of `getItem`).
- New name → saved without confirmation. Another existing name → confirmation appears; "Yes" overwrites, "No" writes nothing (the file's content and
  modification time do not change). Confirmation also appears for the same name as the current view.
- Cancel, empty, or whitespace only → nothing happens.
- Existing tests that replace `QInputDialog.getText` to save views (`test_view_errors.py` and about a dozen other files) are changed to `getItem`
  (and `side_effects_helper.py`). `getText` used for things other than saving views, such as expression input, is not changed.
- English and Japanese translations exist.

### 54. Signal aliases (added 2026-10-14, user request)

Requirement: [5.47](#547-signal-aliases-names-that-exist-only-inside-this-app-added-2026-10-14-user-request). Background: idea.md "view", item
"when a signal is not found, assign a substitute signal" (after the impact was investigated on 2026-10-13, the specification was held, recommending
option (B), "attach an alias to a file column"). Decision (2026-10-14, user): option (B), one dictionary for the whole app, the list shows only
aliases, and assignment can be made both from the dedicated editing window and from the view's warning.

**The implementation is split into two stages** (one stage = one branch): **stage 1** = 54.1 to 54.5 and 54.9 (dictionary, resolution, display in the
list, editing window, marks in the view manager, enable/disable), **stage 2** = 54.6 (assignment from the view's warning).

#### 54.1 Dictionary and storage (`io/signal_aliases.py`, new; pure resolution function in `core/signal_aliases.py`)
- Storage path: `cfg/parquet_analyzer/signal_aliases.json` (how the path is built is in 54.8; a place that is not mixed into the view list `list_views`).
  ```json
  {"version": 1, "aliases": [{"alias": "engine_speed", "sources": ["EngSpd", "NE_rpm"]}]}
  ```
  - `alias`: a non-empty string (leading and trailing spaces removed). `sources`: one or more non-empty strings, no duplicates, **the order is the priority**.
  - Aliases do not duplicate each other (case-sensitive, as column names are).
  - An alias need not be a valid identifier (treated like a column name). If it is not an identifier, the editing window only warns "cannot be used in expressions"
    (conversely, giving an identifier alias to a column whose name is not an identifier makes it usable in expressions, which also avoids the limit in 13.4).
  - An alias does not overlap the names of built-in functions, user functions or maps (`MainWindow.reserved_names()`, since inside an expression they cannot be told apart from functions).
  - **The reverse is also checked** (2026-10-14, specification review): the name checks when a user function or map is newly created or renamed
    (`function_manager`, `map_manager`; today they look at `_file_columns`, `_derived` and `reserved_names()`) include **all aliases in the dictionary**
    (whether or not they are attached in the current file, because the dictionary is one for the whole app and aliases attach when another file is opened).
  - `sources` is matched only against the file's actual column names. **Aliases do not chain** (even if another alias's name is among the candidates,
    it is not used when no column of that name exists in the file).
- Loading: if the file does not exist, the dictionary is empty. If the shape is wrong or the JSON cannot be read, the original file is renamed to
  `signal_aliases.json.bad-<YYYYmmdd_HHMMSS>` and set aside, as for user functions (26.3); the dictionary starts empty and a warning is shown once.
  Per-item errors (empty alias, duplicates, etc.) skip only that item and are collected into the warning. Unknown keys are ignored (as in ch. 8).
- Writing uses a temporary file and replacement (the same method as the settings file). If it fails, the dictionary is left unchanged and an error is shown.

#### 54.2 Resolution (when opening a file, changing the time axis, reading a view)
- Pure function `resolve_aliases(columns: Sequence[str], aliases) -> dict[str, str]` (alias → original column name):
  - For each alias, look through `sources` from the top and make the first one that is in `columns` the original column. If none, the alias is not attached.
  - **If a column with the same name as the alias exists in the file, that alias is not attached** (the file's column wins; the editing window shows "not used in this file
    (a column with the same name exists)").
  - One column may have several aliases (they all point to the same column).
- After `MainWindow` builds the file's columns (`_file_columns`), it adds the aliases as **the same `Column` object** (`_file_columns[alias] = _file_columns[source]`;
  not copied, so memory, windowed reads and caches are shared with the original column). It also keeps `self._alias_sources: dict[str, str]` (alias → original column name).
  - As a result, series, expressions (`LazyVariables`, `DerivedColumn`), statistics, XY, histograms and export treat an alias as an ordinary column name
    (no change is needed on each side).
  - Time axis: aliases are also shown in the time-axis column selection (combo) (the original name is not shown); choosing an alias makes its original column the time axis.
    The view's `time_column` (ch. 33) is also resolved through aliases. An alias of the column chosen as the time axis is not shown in the variable list, just like the original column.
  - **How the time-axis name is held** (2026-10-14, specification review): `_time_column` stays the **original column name** (the data itself; `_time_indexes`,
    `_build_time_index` and `read_columns` read by the original name). One function that returns the display name (e.g. `_display_name(source)`: the first
    alias by name order if any is attached, otherwise the original name) is created, and **the combo display, `_sync_time_axis_combo`, the `time_column` saved in views,
    and the `time_column` in the operation log** use the display name (saving the original name in a view would make the time axis not found in a file with different signal names).
    `set_time_column(name)` first converts `name` back to the original name, and only then makes the existing comparison (`name == self._time_column`); the combo's
    `currentTextChanged` passes the display string (that is, the alias).
  - **Excluding the time axis**: today `_raw_variables` excludes only the time-axis column (`k != name`). After aliases are added, **the original column and all of its aliases**
    are excluded (so that the time-axis column stays invisible to expressions, XY, histograms and statistics as before). The check in 54.5 uses the same rule.
- **Put the building of `_raw_variables` in one place** (2026-10-14, specification review): today `{k: v for k, v in self._file_columns.items() if k != time axis}` is
  written in three places: `load_parquet`, `set_time_column` and `load_view`. Make one function (e.g. `_rebuild_raw_variables()`, which excludes the time-axis original
  column and its aliases and, for `set_time_column`, re-adds the virtual-variable cache) and call it from the three places.
- **Names shown in lists** (one place, e.g. `MainWindow.visible_column_names()`): from the file's columns, remove the original columns that have an alias, and add the aliases
  (the order puts the alias in the position of the original column; if one column has several aliases, by alias name order). Places that use it: the variable list
  (`variable_panel.set_variables`), the time-axis combo, and the variable choices of XY, histograms and the like (places that build them from the keys of `_raw_variables` or
  from the variable list today). **The original column names disappear from the lists only; they can still be used in expressions and views as before** (they stay in `_file_columns`).
  - Places that today use the keys of `_raw_variables` directly as choices (as of 2026-10-14): the variable list of `histogram_window.py` (`_raw_variables.keys()`), the
    X, Y (and Z) choices of `xy_plot_window.py` (two places), and `variable_panel.set_variables` in `MainWindow` (three places; fixed together with `_rebuild_raw_variables`
    above). All are replaced by "names shown in lists" (`_raw_variables` also holds the original names, so without this the original names would appear). Places that fetch
    values (`_raw_variables.get(name)`) stay as they are.
- Variable list items: the display string is the alias itself (some places use the item string as a variable name, so no decoration is added after it).
  The tooltip says "original column: EngSpd". Filtering (search) also matches the original name.
- Virtual variable with the same name as an alias: rejected, as with "a column cannot have the same name as a virtual variable" today (all aliases in the dictionary
  are checked, 54.8; when loading a view, it is skipped with the current warning).
- Operation log: the aliases attached are recorded in the post-operation state record (`file_info` of `_state_snapshot`, the `result` of 30.2) as
  `aliases={"engine_speed": "EngSpd"}` (2026-10-14, specification review. The rule is that `log_op("load_parquet")` is written at the start of the process, and at that point the
  aliases are not yet decided, so they are not added to the `load_parquet` record).

#### 54.3 Variable list and saving views
- If an alias is put into a plot, the series name is the alias (the legend, hover, cursor values, images and the headings of exports also show the alias). It is saved
  in views with the alias (the view format does not change, and `version` is not raised). Expressions written with the original name are saved with the original name.
- Right-click in the variable list: on a file column (not a virtual variable), "Add alias...". On a column that has an alias, "Edit alias...". Both open the editing window of
  54.4 with that column already included as a candidate in a new item (or an existing item) selected.

#### 54.4 Editing window (`ui/signal_alias_manager.py`, new)
- Entry points: next to "User functions, Maps" on the analysis toolbar, "Signal aliases" (shortcut configurable as `signal_aliases`, no default;
  added to the order of ch. 48), and the right-click of 54.3. Non-modal, one at a time (as with the map manager window).
- Left: the list of aliases (the alias and, for the current file, the original column; "—" if none is attached; "a column with the same name exists" if
  there is one). Buttons "Add" and "Delete".
- Right: editing the selected alias: the name (`QLineEdit`), the list of candidate column names (in priority order; `QListWidget` plus "Up", "Down", "Delete";
  candidates are added either by choosing from the current file's columns in a combo (original names that have no alias are also shown) or by typing a name).
- "Save" and "Revert" (as in the editing of map values, 27.8: enabled only while there are unsaved changes; closing or selecting another alias with unsaved changes asks for
  confirmation). On save, the checks of 54.1 apply (if they fail, the reason is shown and nothing is saved).
- **Reflecting a save** (when a file is open): resolve 54.2 again with the new dictionary and compare with before.
  - Newly attached aliases appear in the list at once.
  - **If an alias in use disappears or starts pointing to a different column**, the save is refused and the places that use it are shown (the same idea as refusing deletion of
    a map or user function that is in use). "In use" means: a series name shown in a plot, a name referenced by a virtual variable's expression (`referenced_names`), or a name
    chosen as the time axis.
  - **When a new alias with the same name as an existing virtual variable is attached**, the save is also refused, with the reason "overlaps with virtual variable <name>"
    (2026-10-14, specification review; attaching it as is would make `_file_columns` and `_raw_variables` overwrite each other with the same name; the reverse of
    "a virtual variable cannot be created with the name of an alias" in 54.2).
  - Analysis windows (XY, histograms and the like) that use an alias that disappears or changes are closed, as when the file is reopened (25.1).
  - After reflecting, the variable list and time-axis combo are rebuilt (plots stay as they are; no navigator synchronization is needed).
- Operation log: `signal_alias_save` (the names of aliases added, deleted or changed), `signal_alias_save_refused` (the name in use and where it is used).
  `log_op` is written at the start of the save.
- All wording goes through `tr()` (en and ja).

#### 54.5 "Missing" marks in the view manager (20.2)
- Include aliases in the set of column names used to judge "missing" (the `available` and `all_columns` that `load_view_dialog` passes to `ViewManagerDialog`).
  These are the keys of `_file_columns`, so passing the set after the aliases are added matches the resolution in `load_view`. The promise of 20.2 (the marks match what
  `load_view` actually skips) is kept.
  - `available` currently excludes the time-axis column with `c != self._time_column`. **The aliases of the time-axis column are also excluded** (the same set as the time-axis
    exclusion of 54.2; if not excluded, the marks and `load_view` would disagree for a view that uses a time-axis alias as a series).

#### 54.6 Stage 2: assigning from the view's warning
- When the grouped warning of `load_view` (the current `messages.warning`) contains a **signal that is not found**, add a button "Assign a substitute signal..."
  (add a warning with a button to `ui/messages`, which returns whether it was pressed). "A signal not found" means a variable name of a series that is not in the file, a
  time-axis column name, or a name referenced by the expression of a skipped virtual variable (`referenced_names`) that is none of a column, virtual variable, function or map.
- Pressing it opens the assignment window: one row per missing name, with a choice of the names shown in the current file's list (54.2), plus a combo of original column
  names (default "Do not assign"). On OK, for each assigned name, add to the dictionary: if an alias with the same name exists, append the column to its `sources`;
  otherwise add `{"alias": missing name, "sources": [chosen column]}`. Then save (54.1) and reload the same view (`load_view`).
  - The chosen column may already be an original column with an alias (several aliases on one column, 54.2).
  - If a missing name overlaps a built-in function and so on (cannot be an alias under 54.1), that row cannot be chosen and the reason is shown.
- Operation log: `signal_alias_assign_from_view` (the view's name, and the set of assignments).

#### 54.7 Test plan
- Stage 1:
  - Round trip of reading and writing the dictionary; no file → empty; broken JSON → set aside as `.bad-` and empty, with one warning; per-item errors (empty, duplicates,
    no candidate, overlapping a function name) skip only that item; unknown keys are ignored. A failed write leaves the dictionary unchanged.
  - `resolve_aliases`: priority order of candidates; none present; an alias with a same-named column is not attached; several aliases on one column. (It is also acceptable to add a
    property test with `hypothesis` that "the original column in the result is always in `columns`, and the alias is never in `columns`.")
  - Opening a file shows aliases in the variable list and the time-axis combo, and the original names disappear. Tooltip and filtering. The alias column and the original column are the
    same `Column` object (not copied). An alias put into a plot has the same values as the original column. Expressions can use both the alias and the original name.
    The same holds for large files (windowed reads).
  - A view saved with aliases, read in a file whose candidate column names differ, is fully restored (no warning). Also for an alias of the time-axis column.
  - (Added with the 2026-10-14 specification review) Time axis: choosing an alias in the combo makes `_time_column` the original name, the combo display stays the alias, and the
    view's `time_column` is saved as the alias. Neither the original name nor the alias of the time-axis column appears in the variable list, the XY or histogram choices, or `_raw_variables`.
    The original name does not appear in XY or histogram choices. A function or map with the same name as an alias cannot be created (including aliases not attached in the current file).
    Saving an alias that overlaps a current virtual variable is refused. Attached aliases appear in the operation log's state record (`file_info.aliases`).
    No chaining (if alias A's candidates contain only alias B's name, A is not attached).
  - The "missing" marks in the view manager match what `load_view` skips (with and without aliases).
  - Editing window: add, edit, delete, reorder, save, revert, the unsaved-changes confirmation, validation errors. Saving while a file is open refreshes the list. Deleting or
    reassigning an alias in use is refused and the places using it are shown (series, expressions, time axis). Analysis windows are closed.
  - The right-click "Add alias..." and "Edit alias..." appear (not on virtual variables). The toolbar entry and the row for the shortcut in the settings.
  - Operation side effects (app ch. 45): saving aliases does not change unrelated state. English and Japanese translations.
- Stage 2: the button appears only when a signal is missing. Assigning and reloading restores the view. The chosen column is added to the candidates of an existing alias, or a new
  alias is created. A name that overlaps a function name cannot be chosen. "Do not assign" alone leaves the dictionary unchanged.
- Visual check: take images of the variable list and the editing window without a screen, and confirm.

#### 54.8 Decisions made in implementation and review (2026-10-14, stage 1)

- **Storage path**: not a constant in `config.py`; built by `signal_aliases_path()` in `io/signal_aliases.py` from `config.PARQUET_ANALYZER_CFG_DIR` at the time it is called
  (as with `user_functions_path()`, so that replacing `isolated_config` in tests takes effect).
- **Unreadable file** (exists, but cannot be read with `OSError`): not set aside as `.bad-`. Aliases start empty, saving is disabled, and an error is shown
  (as with `UserFunctionsUnreadable` for user functions).
- **Skipped items** (empty, duplicate, overlapping a function name, etc.) are kept as they are and not removed, and written back unchanged on the next save (as in 26.3).
  So items overlapping a reserved name do not appear in the editing window, and a warning is shown at each startup (fix it by editing the file by hand or by renaming the
  function or map). Per-item warnings are translated with `tr()` and shown **together in one notice** with the startup warnings for functions and maps (the later one does not
  overwrite the earlier one).
- **State record**: `file_info` in 54.2 is the `file` of `_state_snapshot()`. `file.aliases` is written only when some alias is attached.
- **How time-axis names are compared**: the display name of the time axis can change when the dictionary changes (for example, an earlier alias by name order is added to the same
  column). So whether a name is the time axis is judged by **converting it back to the original name** and comparing with `_time_column` (not by comparing display names with
  each other; a bug was found in review where, in the XY window, after saving the time axis became an "unusable variable").
- **When the time-axis column has several aliases**: the time-axis combo shows only the display name of that column (the first by name order). (Showing all of them would make
  choosing another alias revert to the display name, which is confusing.) The other aliases can still be used in expressions, series and the view's `time_column` as before.
- **Virtual variable names**: a name that equals **any alias in the dictionary** cannot be used (whether or not it is attached in the current file; the same reason as for functions and maps:
  the dictionary is one for the whole app, and aliases attach when another file is opened). When a view is loaded, a virtual variable with an alias's name is skipped, reported in the
  grouped warning, and the "missing" mark in the view manager uses the same judgement (20.2).
- **Narrowing the variable list**: the variable list has no search field, so "filtering also matches the original name" in 54.2 has no target. The search of the Y choice of XY
  (`name_picker.py`) matches only the display name (the alias).
- **Editing window**: "Delete" saves **immediately** after confirmation (going through the same refusal checks as a save). "Add" creates a new unsaved row. After saving, when editing starts
  again, the "Saved" message is cleared.
- **Conditions for closing analysis windows** (54.4): in addition to aliases that disappear or are reassigned, also when a name that is no longer shown in the list after the change is used
  (for example, the original name hidden by a new alias) (closing is the safer choice).
- **Deleting a time-axis alias**: if the time-axis column has aliases a and b and the display is a, deleting a is refused as "in use as the time axis" (b could stand in for it, but
  by the rule for "aliases in use" in 54.4; to be reconsidered if needed).
- The message when a time-axis column cannot be created (`time_axis.failed`) uses the display name. The time-axis metadata in the export of histogram bin values stays the original column
  name (to record which column it was built from).

#### 54.9 Enabling and disabling aliases (added 2026-10-14, user request; included in stage 1)

Background: the user wants to stop an alias temporarily while keeping its definition (today the only option is deleting it). Decision (2026-10-14, user): **a per-alias "enabled" check**
and **a global switch "Use signal aliases"** are both added.

- **Per alias enabled**: an `"enabled"` key (true or false, default `true`; `true` if the key is missing) is added to the dictionary item. `version` stays 1
  (older versions ignore this key and treat the alias as enabled, under the rule that unknown keys are ignored). A value that is not true or false is skipped as an invalid item (54.1).
  When writing, `"enabled": false` is written only for `false` (files of enabled items keep exactly the same shape as before).
- **Global switch**: a setting (`Settings.signal_aliases_enabled`, default `True`, in `settings.json`). It is not kept in the dictionary file
  (the dictionary holds definitions; the switch is a usage preference).
- **Resolution** (54.2): if the global switch is OFF, no alias is attached. If ON, aliases with `enabled` `false` are not attached. An alias that is not attached is treated as today's
  "no candidate column exists" (the original column name appears in the list, and a view that uses the alias finds its signal missing and shows the warning).
  `resolve_aliases` receives only the enabled items (the pure function's shape does not change).
- **Name-overlap checks** (54.1, 54.8) cover **all aliases** even when an alias is disabled or the global switch is OFF (because they attach again when turned back on).
- **Refusal when in use** (54.4): if disabling an alias or turning the global switch OFF would remove an alias that is in use (a series shown, a virtual variable's expression, or the
  time axis), it is refused like deletion, and the places using it are shown (the settings and dictionary are not changed). The rule for closing analysis windows is the same as in 54.4.
- **Editing window** (54.4):
  - An "Enabled" check box at the top of the right-hand editing area (above the name). It is part of the edit like the other fields, and is reflected on "Save" (the unsaved mark and confirmation work the same way).
  - Left list: disabled aliases are marked "(disabled)" in a faded colour (the status column says "disabled").
  - A "**Use signal aliases**" check box at the top of the window (the global switch). This one is **applied immediately when pressed** (save the setting and resolve again; if refused, the check
    box is set back). While OFF, the status column of the left list reads "not used (global switch is OFF)" for all items, and editing and saving items still work as before.
- **Entry point**: "Use signal aliases" (a check-box item, `toggle_signal_aliases`) is added to "⚙ ▾" (below "Allow fast display"). It works as the check box in the editing window,
  with the same behaviour and state (a change made from either place is reflected in both).
- Operation log: the change of enabled state is included in `signal_alias_save`'s changes (`enabled_changed`). The global switch is `signal_aliases_toggle` (`enabled`), and a refusal
  has the same form as `signal_alias_save_refused` (`reason="toggle"`). `log_op` is written first.
- State record: `file.aliases` contains only the attached aliases (as now). The setting `signal_aliases_enabled` appears in the settings record.
- Tests: round trip of reading and writing `enabled` (missing key → enabled; only `false` is written; non-boolean values are skipped). A disabled alias is not attached and the
  original column name appears in the list; enabling it again attaches it. With the global switch OFF, all are detached, and ON restores them (saved in settings, and still effective after restart).
  With disabled or OFF, aliases still remain in the name checks for functions, maps and virtual variables. Disabling an alias in use or turning OFF while in use is refused and nothing
  changes (the check box goes back). The "⚙ ▾" check box and the editing-window check box show the same state. "(disabled)" display and the unsaved mark in the editing window.
  Operation log. A view saved with aliases, loaded with the global switch OFF, shows the "not found" warning, and the marks in the view manager match (20.2). English and Japanese translations.

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
- **App foundation** (app ch. 48 and ch. 49 of this document, 2026-10-13): the view save is opened from "View ▾ → Save...". The name is entered with `getItem` (editable), and overwriting an existing view always asks for confirmation. When changing the toolbar wording, do not mix it with the title key of 49.1.
- **Data, expressions and analysis** (ch. 54, 2026-10-14): signal aliases only add the same `Column` under another name to `_file_columns`, so any code that reads columns by name handles aliases as is. Code that builds variable choices uses "names shown in lists" (54.2) and does not list the keys of `_file_columns` directly (the original names would appear). The name-overlap checks for virtual variables, functions and maps include the aliases.
- **App foundation** (54.4): the "Signal aliases" toolbar entry, the shortcut `signal_aliases`, and the path `signal_aliases.json` (`config.py`).
