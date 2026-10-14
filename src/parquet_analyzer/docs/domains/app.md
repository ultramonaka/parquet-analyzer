# App foundation (layout, startup, logs, errors, version, feature flags)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: directory layout and path sharing, startup, logs and the operation log,
the error-handling policy, embedding the version, development feature flags.

## Requirements

### 3. Directory layout

```
parquet-analyzer/
├── cfg/                        # app settings files (default parameters etc.)
│   └── parquet_analyzer/        # where the app's settings files live
├── data/
│   ├── parquet_analyzer/        # saved views (screen layouts)
│   └── raw/                    # source Parquet data to analyze (including sample/test data)
├── log/                        # logs and debug output
│   └── parquet_analyzer/        # where logs live
├── src/
│   ├── common/                  # code shared with other projects (empty for now)
│   └── parquet_analyzer/         # all of this app's source (app-specific code lives here)
│       ├── config.py            # shared path definitions (importable from other projects)
│       ├── __main__.py          # GUI entry point
│       ├── convert_cli.py       # CLI entry point for MDF/MATLAB→Parquet conversion
│       ├── docs/                 # the specification (overview + per-domain files, Japanese and English)
│       ├── ui/                  # screens (main window, navigator, toolbar, …)
│       ├── core/                 # data reading, downsampling, expressions, analysis logic, conversion logic
│       └── io/                   # saving/loading views, reading/writing the settings file
├── tests/
│   └── parquet_analyzer/         # the pytest suite
└── tools/
    ├── run.sh / run.bat / run.command  # launch scripts (.command is for double-click launch on macOS)
    ├── run_debug.sh / run_debug.bat    # debug-mode launch scripts
    ├── convert_to_parquet.sh            # MDF/MATLAB→Parquet conversion tool
    └── publish_to_public.sh             # syncs the development repo to the public repo
```

#### 3.1 Sharing paths with other projects

- Paths (the directories under `cfg/`, `data/`, `log/`, …) are defined as constants in
  `src/parquet_analyzer/config.py`; the app itself, code under `src/common/` and other projects all
  import this module to refer to them (settled in 11.2).
- `src/common/` holds code that other projects may use too, such as Parquet reading and shared
  utilities.
- `tools/run.sh` / `tools/run.bat` always add the `src` directory to the `PYTHONPATH` environment
  variable on launch. This makes `parquet_analyzer` (including its `config` submodule) and the
  `common` modules/packages importable without qualification.
- Other projects likewise add this repository's `src` to `PYTHONPATH` in their own launch scripts
  to reuse `config.py` and the code under `common`.
- `config.py` depends only on the stdlib. `REPO_ROOT` is resolved from the location of `config.py`
  by default but can be overridden with the `PARQUET_ANALYZER_ROOT` environment variable (for cases
  such as the exe where resolving from `__file__` doesn't work). Creating directories is not a side
  effect of import; `ensure_dirs()` is called explicitly.

### 4. How to start

- Run `tools/run.sh` (macOS/Linux) or `tools/run.bat` (Windows).
- What the scripts do:
  1. Add `<repo>/src` to `PYTHONPATH`
  2. Start the app with `uv run python -m parquet_analyzer`
- The distributed exe is a build of this entry point.

### 8. Logs (log/parquet_analyzer)

- Runtime logs, stack traces on errors etc. are written under `log/parquet_analyzer`.
- Starting with `--debug` enables debug mode, which can dump user operations such as opening a
  file, zoom/pan, overlaying variables and saving/loading views to a separate file
  (`parquet_analyzer_ops_*.log`). Details in
  [detailed_specification 11.1](#111-debug-mode-and-the-operation-log).

#### 5.23 A richer operation log: recording operations and what followed (added 2026-10-02, user request)
- For bug reports and investigations, **record "what was operated" paired with "what the app did
  as a result"**. Example: "remove series → result: series b disappeared, the navigator range
  changed [60, 120] → [12, 642]".
- **Always record** (not only in debug mode), so that a bug that happened during a normal run can
  be checked in the log afterwards. Debug mode adds more detail and console output.
- What is recorded:
  - User operations (everything the current operation log covers).
  - The **result** of an operation: the state when the screen has settled (background work
    finished) and **what changed since before the operation**.
  - Start, completion and failure of background jobs (so it is clear which operation triggered them).
  - Messages shown to the user (warning/error dialogs, status-bar notices).
  - Unexpected exceptions (currently some show nothing on screen and may not be logged either).
- Logs are saved in one file per day, and **files past the retention period (default 7 days) are
  deleted automatically**. The retention period can be changed in the settings (2026-10-02, user
  choice).
- Provide a tool that shows the log in a human-readable form, and an "Open log folder" menu item.
- Details in [ch. 30](#30-a-richer-operation-log-recording-operations-and-results-added-2026-10-02-user-request).

#### 5.36 Side effects of operations: unrelated state must not change (added 2026-10-06, user request)
- When a button or menu is pressed, an automated test checks that **state unrelated to that operation does not change**.
  Examples: saving a map moved the plot range (dev notes item 14); removing a series shifted the navigator range
  (the same kind of bug occurred 5 times, dev notes 2026-10-02).
- For each operation, keep a table of the state that **may** change. If the difference in state before and after the
  operation (the same diff as in the operation log, ch. 30) falls outside the table, the test fails. Re-reading a file,
  an unexpected dialog, and an exception also fail the test.
- When adding a new button or menu, add one row to this table (a rule for all domains).
- Details in [ch. 45](#45-side-effect-tests-unrelated-features-must-not-change-added-2026-10-06-user-request).

#### 5.38 Stronger tests: random checks, failure paths, windowed reads, view backward compatibility (added 2026-10-08)
- Candidate A of external review 2 (dev notes). The app's behavior does not change; tests are added to check more
  mechanically that things have not broken. Split into 4 parts, one part = one branch, done in order (2026-10-08, user decision):
  1. **A1 Property tests on random data** (`hypothesis`): the expression evaluator, `TimeIndex.searchsorted`, `chunked_stats`,
     LTTB, `rolling_mean`/`rolling_nanmean` and `row_range_for_x` keep their stated properties on arbitrary inputs.
  2. **A2 Failure paths of background work**: a table of combinations of success, exception, cancellation, widget deletion,
     file switch and plot removal; add tests for the cells not yet covered.
  3. **A3 Windowed-read columns**: `window(lo, hi)` equals the full-array slice `[lo:hi]`. Guard that drawing does not cause
     a full read of a windowed column.
  4. **A4 View backward compatibility**: keep view files saved by past versions as test data and confirm they can still be read.
- Bugs found by these tests are not fixed on the spot; they are reported and handled as separate work (same as ch. 45).
- Details in [ch. 46](#46-stronger-tests-random-checks-failure-paths-windowed-reads-view-backward-compatibility-added-2026-10-08).

#### 5.41 Toolbar cleanup: split into two, wording, group rare operations (added 2026-10-13, user request)
- The main toolbar has about 16 operations in one row, which are (1) hard to read in order and grouping, (2) worded
  unclearly, and (3) too many to find easily (the user's observation, 2026-10-13). No menu bar is added; instead **the toolbar
  is split into two** (user decision):
  - **File & view**: Open | Time axis | View ▾, Save image | Show all, Redraw | Add plot | Cursor, Show values
  - **Analysis**: Statistics & graphs ▾ | User functions, Maps | ⚙ ▾
- Wording: "Home" → "Show all". "Save view" and "View..." → "View ▾" (Save... / Manage...). The toolbar's "Functions" →
  "User functions" (to distinguish it from "Function list..." at the right end of the expression input; in English
  "Functions..." → "Function list...").
- Rarely used items, "Allow fast display" (kept as a checkbox), "Settings..." and "Reset layout", are grouped into "⚙ ▾".
- By default the two toolbars sit in the same row. Positions are still saved and restored as before; "Reset layout" returns
  them to the default.
- The features themselves and the shortcut settings (key names) do not change.
- Details in [ch. 48](#48-toolbar-cleanup-added-2026-10-13-user-request).

#### 5.44 CI setup: run the macOS job by hand, fix the tests that fail on Ubuntu (added 2026-10-13, user decision)
- The day-to-day machine is a Mac, so tests should also be runnable on macOS in CI (GitHub Actions). However, the repository
  is private, macOS minutes count for many times more than Ubuntu (about 1,500 minutes on Ubuntu alone over the 23 days
  from 9/20 to 10/13), so macOS cannot run on every push. **The macOS job is only run by hand (`workflow_dispatch`)**
  (user decision, option A).
- The Ubuntu CI keeps running on every push as before. A manual trigger is also added to the Ubuntu workflow so CI can be tried on a branch.
- From 2026-10-07 the CI on `main` has been red (everything passes on the Mac locally). Fix these first:
  `test_worker_paths.py::test_stats_paths[S-3]` (since 10/07) and three "A" button tests in `test_plot_widget.py`
  (since the 10/13 merge).
- Details in [ch. 51](#51-ci-setup-added-2026-10-13-user-decision).

#### 5.45 Introducing lint (ruff) (added 2026-10-13, user decision; dev notes item 36)
- Find unused imports, undefined names, import order and outdated syntax mechanically, and prevent new ones from being
  added in CI. Rules: `E`, `F`, `I`, `UP` (user decision). Line length (E501) is not checked. Formatting (`ruff format`)
  is not introduced (user decision). No type checking (mypy/pyright): the type information for PySide6 and pyqtgraph is
  weak, and the benefit is small for the effort.
- The first findings are fixed in one batch. Only changes that do not alter behavior are fixed directly; findings that could
  change behavior are decided one by one.
- Details in [ch. 52](#52-introducing-lint-ruff-added-2026-10-13-user-decision).

## Design

### 3. Startup sequence

1. `tools/run.sh` / `run.bat` adds `src` to `PYTHONPATH` and runs
   `uv run python -m parquet_analyzer`.
2. `__main__.py`:
   1. Calls `config.ensure_dirs()` to create the `parquet_analyzer/` subdirectories under
      `cfg/data/log`.
   2. Configures `logging` for `config.PARQUET_ANALYZER_LOG_DIR`.
   3. Reads `cfg/parquet_analyzer/settings.json` (defaults if absent).
   4. Starts the `QApplication` and shows `MainWindow`.

### 11. Logging

- Uses the standard `logging` module.
- Output: `log/parquet_analyzer/parquet_analyzer_{YYYYMMDD}.log` (rotated daily).
- `INFO` normally; on an exception, `ERROR` with the stack trace.

#### 11.1 Debug mode and the operation log

- Starting with the `--debug` flag (or the environment variable `PARQUET_ANALYZER_DEBUG=1`)
  enables debug mode. `tools/run_debug.sh` / `run_debug.bat` are dedicated launchers that start
  with this flag.
- In debug mode:
  - The main log level becomes `DEBUG`.
  - The window title gets a `[DEBUG]` suffix (so which mode is running is visible at a glance).
  - The **operation log** (a trace of user operations such as opening a file, overlaying
    variables, adding derived variables, H/R, zoom/pan, adding/removing/separating plots,
    saving/loading views, changing settings) is written to
    `log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log` (and echoed to the console). It is
    kept in a separate file from the main log so frequent operations don't bury the app's normal
    log.
- The implementation is a thin wrapper: just call `log_op(action, **details)` in `core/oplog.py`.
  Outside debug mode `logger.isEnabledFor(DEBUG)` is `False`, so it is effectively a no-op (not even
  the string formatting cost is paid).

### 12. Error-handling policy

- A failure to read Parquet, an invalid expression or a failure to load a view is reported to the
  user in a dialog and logged, and the app keeps running (never crashes).
- **Work that "must never block the app from starting" is defended especially carefully** (the
  settings file and the window layout are both read unconditionally in `MainWindow.__init__`, so
  an exception there would crash before any window is shown — which looks like "it doesn't
  start").
  - `io/settings.load_settings()`: neither corrupt JSON nor wrong types raise; it falls back to
    the defaults (`Settings()`). `save_settings()` writes atomically with a temporary file +
    `os.replace` so a crash while writing never leaves a broken file behind.
  - `MainWindow._restore_layout()`: if `base64.b64decode()` of `window_geometry`/`window_state`
    fails (corrupt data, incompatibility with an old format) the exception is swallowed and the
    default layout is used. In addition, if the restored window position overlaps no screen
    (`QGuiApplication.screens()`) — e.g. starting without the external monitor that was connected
    when it was saved — the window goes back to the default position (Qt's own
    `restoreGeometry()` corrects extreme coordinates to some degree; this is insurance).
  - Both aim to prevent "a new bug report arrives while the app can't start", a lesson from a real
    first-start failure after a review (missing error handling in `_restore_layout`).
- `load_parquet` completes everything needed to load (reading the schema, converting the time
  column, checking for non-empty columns) on local variables before applying it to `self.*`. This
  prevents a half state where, after a failure midway, `self._data_source` etc. point at the new
  file while the old data stays on screen. It returns success/failure as a `bool`, and `load_view`
  checks it before proceeding (rebuilding derived variables, restoring plots).
- `load_view` further splits loading the view itself (JSON, resolving the source path) and
  rebuilding the plot layout with the loaded data into separate exception handling. If the latter
  fails (`ExpressionError`/`KeyError`; e.g. a column the view refers to is gone because the source
  Parquet was regenerated), the data load that already succeeded is not invalidated (a warning
  dialog is shown, and the opened data stays usable).

### 17. Embedding the version (added 2026-09-20, user request)

The `version` field of `pyproject.toml` is authoritative and is copied by hand into the
`__version__` string constant in `parquet_analyzer/__init__.py` (rather than deriving one from the
other at runtime, the simple manual sync was chosen — this project is not installed as a
distribution package (`[tool.uv] package = false`), so `importlib.metadata.version()` has nothing
to look up, and while parsing `pyproject.toml` at runtime is possible, adding a parser for one
rarely changing string wasn't judged worth it). That `__init__.py` now has a one-line constant
(instead of being empty) doesn't affect config.py's promise of "importable without dependencies" —
it is just a string literal and pulls in none of PySide6/pyqtgraph/polars.

Two places use this value: the `--version` flag in `__main__.py` (argparse's standard
`action="version"`, prints `parquet_analyzer <version>` and exits) and `_APP_TITLE` in
`ui/main_window.py` (`"Parquet Analyzer v<version>"`, shown in the window title bar on every
start). Repositories made by `tools/publish_to_public.sh` show it the same way, so a bug report
from a copy can be pinned to a version from the title bar alone, without asking the reporter to
check `pyproject.toml`.

**Every place that holds the version number** — update them all together when bumping
(`uv.lock` records it too, so run `uv lock`):
- `version` in `pyproject.toml` (authoritative)
- `__version__` in `src/parquet_analyzer/__init__.py`
- The version badge in `README.md` (`version-X.Y.Z-informational`) — added to this list because it
  was missed when bumping to 0.2.0 (found 2026-10-01).

### 21. Development feature flags (added 2026-10-02, user request)

Requirement: `specification.ja.md` ch. 9. Background: to hide FFT from the GUI, a one-line
`FFT_ENABLED = False` had been put in `io/settings.py` (13.5), but it was FFT-only and couldn't be
used for other features, and the user-settings module was the wrong kind of place for it. This
makes a general mechanism so new features can be "committed off until finished and reviewed".

#### 21.1 `src/parquet_analyzer/features.py` (new)

- Like `config.py`, depends **only on the standard library** (used from both `io/` and `ui/`).
- `DEFAULT_FEATURES: dict[str, bool]` — feature name → enabled/disabled by default. Values:
  | Name | Default | For |
  |---|---|---|
  | `fft` | `False` | FFT (debugging unfinished; formerly `FFT_ENABLED`) |
  | `independent_x_axis` | `True` (on 2026-10-02 after review + real-machine check) | ch. 24 independent X axis |
  | `xy_plot` | `True` (on 2026-10-03 after review + real-machine check) | ch. 25 stage 1 |
  | `xyz_3d` | `True` (on 2026-10-03 after review + real-machine check) | ch. 25 stage 2 |
  | `user_functions` | `True` (on 2026-10-03 after review + real-machine check) | ch. 26 |
  | `maps` | `True` (on 2026-10-03 after review + real-machine check) | ch. 27 |
  | `big_a_thin_first_draw`, `big_c_windowed_derived`, `big_d_time_index` | `True` (adopted 2026-10-04, data 36.8; the flags themselves are removed if everyday use shows no problems) | data ch. 36 A, C, D |
  Each is set to `True` once its chapter is implemented and reviewed (the main session makes that
  change).
- Can be overridden temporarily with the environment variable `PARQUET_ANALYZER_FEATURES`:
  comma-separated `name=value` (e.g. `fft=on,xy_plot=on`). Values are
  `on/off/1/0/true/false/yes/no` (case-insensitive). An unknown name or invalid value is ignored for
  that item only, with a `logger.warning`.
- **When frozen (PyInstaller exe, `getattr(sys, "frozen", False)`) the environment variable is not
  read** (a distribution is fixed to the defaults; user's answer 2026-10-02).
- The effective values are computed once at module load and kept in `_EFFECTIVE: dict[str, bool]`
  (the UI is built at startup; changing them while running isn't expected).
- `is_enabled(name: str) -> bool` — an unknown name is a `KeyError` (a caller's typo must not
  silently mean "disabled").
- `effective_features() -> dict[str, bool]` — returns a copy. The list is logged with
  `logger.info` at startup (`MainWindow.__init__`).
- Tests switch them with `monkeypatch.setitem(features._EFFECTIVE, "xy_plot", True)` (before
  building `MainWindow`).

#### 21.2 Migrating existing code

- Remove `FFT_ENABLED` from `io/settings.py`; `ui/main_window.py` (toolbar) and
  `ui/settings_dialog.py` (shortcut rows) use `features.is_enabled("fft")`. The text in 13.5 (FFT
  being hidden) is updated to refer to this chapter.
- A flag stops **only the UI entry points** (toolbar buttons, menu items, shortcuts, settings
  rows). Computation, internal APIs and tests exist and run regardless of the flag (the same idea
  as for FFT). Settings schemas such as `DEFAULT_SHORTCUTS` keep entries for disabled features too
  (so turning a feature on or off needs no settings migration).
- Not shown in the user-facing settings.

#### 21.3 Test plan

- Defaults are as in the table above.
- Environment variable: `fft=on` enables, `fft=off` disables, upper case and `1/0/yes/no` work,
  unknown names and invalid values are ignored with a warning, empty or unset means defaults.
- When frozen (`monkeypatch.setattr(sys, "frozen", True, raising=False)`) the environment variable
  is ignored.
- `is_enabled("typo")` is a `KeyError`.
- With `fft` disabled the toolbar has no FFT button and the settings have no FFT shortcut row
  (replacing the existing tests); with it enabled they do.
- (The environment variable is read at load time, so tests call the `features` module's parsing
  functions `_parse_overrides(text)` / `_compute_effective(env, frozen)` directly.)

### 30. A richer operation log: recording operations and results (added 2026-10-02, user request)

Requirement: 5.23. Background: the operation log of 11.1 was recorded only in debug mode, and only
"what was operated", not the result (how the on-screen state changed). The bug reported on the
real machine — "after removing a series the navigator range widens by itself" — would have been
clear from just reading the log had the state change after the operation been recorded.

#### 30.1 Log format and storage

- The operation log is **always** written to
  `log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log`, one record per line as **JSON Lines**
  (machine-readable and easy to `grep`).
- Common fields: `ts` (ISO 8601 to the millisecond, **with the time-zone offset**), `seq` (running
  number since startup; restarts at 1 on each start, so it is unique only together with
  `session`), `kind`, `op` (the number of the operation the event belongs to; an operation itself
  is its own `seq`), `session` (an ID per start).
- `kind` values:
  - `op`: a user operation. `action` and `details` (the current `log_op` arguments).
  - `result`: the result of an operation. `state` (a state summary, 30.2) and `changes` (the
    difference from the previous state).
  - `job`: background work. `job` (kind: downsample/pyramid/stats/fft/future analyses), `event`
    (start/finish/fail), `target` (plot, series, variable), `ms` (time taken), and `error` on
    failure.
  - `message`: a message shown to the user. `level` (info/warning/error), `where`
    (dialog/status), `text`.
  - `exception`: an unexpected exception. `type`, `message`, `traceback`.
  - `session`: start and end (app version, feature flags, Python/Qt/OS versions, whether debug
    mode).
- **Retention**: `Settings.log_retention_days` (**default 7 days**; an integer 1–365; out-of-range
  or invalid values fall back to the default). At startup, **both the operation logs and the
  normal logs** (`parquet_analyzer_{date}.log`, `parquet_analyzer_ops_{date}.log`) whose file-name
  date is older than the retention period are deleted. Today's file is never deleted regardless
  of the period. Files with a different name pattern are left alone. (2026-10-02, user choice:
  changed from the original 14 days and operation logs only.)
- **Settings** (9.1) gains a "Log retention (days)" field (`QSpinBox`, 1–365). A change takes
  effect from the deletion at the next start (nothing is deleted when the settings are applied).
  i18n key `settings.log_retention`.
- Keep each record small (lists such as series names are cut at 50 entries, written as
  `"...(+N)"`).
- A write failure (disk full etc.) never stops the app (one warning in the normal log, then
  silence).
- The normal log (`parquet_analyzer_{YYYYMMDD}.log`) is unchanged. In debug mode the operation log
  is also printed to the console in a readable one-line form (as now).

#### 30.2 Recording the result of an operation

- `core/oplog.py` can register a **state provider** (`set_state_provider(fn)`). `MainWindow`
  registers `state_snapshot() -> dict`. `core/` can record the state without knowing about the
  screen.
- What the state summary (`state`) contains:
  - The file (path, row count, time-axis column, result of the time-axis order check).
  - Per plot: ID, time domain or not, X-axis linking, X range, Y range, height ratio, series
    (name, shown/hidden, selected or not, number of points drawn, coarse or not).
  - The navigator selection, the anchor, cursor positions, work in progress (`_busy_sources`),
    warnings (time-axis order) and notices shown, feature flags.
  - Times are written as numbers (epoch seconds) plus a human-readable form (`"09:01:00.000"`).
    The human-readable form is **local time, as on screen** (`datetime.fromtimestamp`; fixed in
    review: the first version used UTC and was 9 hours off from the on-screen times). The
    `session` record keeps the offset from UTC.
- When it is recorded: a call to `log_op` puts that operation **waiting for its result**. After
  returning to the event loop, once `_busy_sources` is empty and things have been quiet for a
  moment (normally 150 ms, 300 ms for consecutive operations), one `result` is written. If it
  hasn't settled 3 s after the last related event, it is written with `"settled": false`.
- **Child operations** (decided during implementation, tidied in review): an operation recorded
  after another started waiting for its result **but before the event loop has run even once** is
  written as a child of that operation with `parent` (the parent operation's `seq`) and has no
  result of its own. Operations that follow on inside a button's handler (e.g. "Open" → loading the
  file, a context-menu request → the actual removal) and range changes made by the program as part
  of an operation fall into this. The event loop always runs before the user's next operation, so
  a real operation is never swallowed as a child.
  - Consecutive operations such as zoom, pan and dragging the navigator are combined into one
    record 300 ms after the last one (the state isn't recorded for every intermediate step).
  - If the next operation arrives while waiting for a result, the previous operation's result is
    written with the state at that moment before moving on. If drawing is still in progress then,
    `"settled": false` and `"interrupted_by": <seq of the next operation>` are added (fixed in
    review: it used to write "settled" even while drawing, so the previous operation's changes
    were recorded as the next operation's).
  - The result of combined consecutive operations has the last operation's `seq` as `op`, plus
    `merged` (count) and `first_op`. Outside debug mode only the first and last `op` lines of
    combined operations are written (to keep the volume down).
- `changes`: the difference from the previous `result`'s state, recorded as item paths with before
  and after values (e.g. `{"navigator.region": [[60.0, 120.0], [12.6, 642.6]],
  "plots.p1.series": [["a","b"],["a"]]}`). A float difference under a relative 1e-9 counts as no
  change.

#### 30.3 Background jobs, messages, exceptions

- A `BackgroundWorker` failure is caught inside and emits `failed` as in ch. 28, and a single
  `job` `fail` is recorded (not recorded as an `exception`).
- `BackgroundWorker` (ch. 28) gets a mechanism to record `job` on start, completion and failure
  (the caller passes the kind and target; the triggering operation is the one waiting for a result
  at start time). Downsampling is frequent, so normally only failures are recorded; completion is
  recorded only in debug mode.
- When showing a notice with `QMessageBox` (warning, error, information) or in the status bar (the
  notice label of ch. 29, the time-axis warning), a `message` is recorded (the showing functions
  are gathered in one place, which records).
- Unexpected exceptions: `sys.excepthook` and `threading.excepthook` are set, so exceptions not
  caught inside Qt slots are recorded as `exception` too (and as `ERROR` in the normal log). The
  app keeps running.

#### 30.4 Reading tool, menu

- `tools/show_ops_log.sh <file>` (calls `core/oplog_view.py`): shows the operation log as a
  readable timeline. Under each operation it lists, indented, the `changes` of its result and the
  `job`/`message`/`exception` records belonging to it. `--last N` (only the last N operations),
  `--session` (only the last start).
- The settings menu (or "Help") gets "Open log folder" (`QDesktopServices.openUrl`).
- The bug-report procedure (README, dev rules) becomes: "start normally and reproduce → hand over
  today's `*_ops_*.log` from the log folder" (reproducing in debug mode is no longer needed).

#### 30.5 Performance

- The state summary is light work proportional to plots × series (it never touches data arrays).
  Aim for under 5 ms per call, measured in a test.
- Log writes are batched (no `flush` per line; but `exception` and the `session` end are flushed
  immediately).

#### 30.6 Test plan

- Format: each `kind` parses as JSON and has the common fields. `seq` keeps increasing and `op`
  points at the operation's number.
- Result recording: with a real `MainWindow`, after each of opening a file, adding a series,
  zooming, removing a series and loading a view, one `result` is written with the expected
  difference in `changes` (e.g. removing a series produces a `plots.<id>.series` difference; a bug
  like the navigator range change seen on the ch. 23 branch would show up as a `navigator.region`
  difference — confirmed with an operation that deliberately changes the range).
- Consecutive zooms combine into one. If the next operation arrives while waiting, the previous
  operation's `result` is written first.
- Background jobs: a failure is recorded as a `job` `fail` with the triggering operation's number.
- Messages: warning dialogs and status-bar notices are recorded as `message`.
- Exceptions: an uncaught exception in a slot is recorded as `exception` and the app keeps running.
- Retention: normal and operation logs older than the retention period are deleted; today's file,
  files within the period and files with a different name pattern remain. The default is 7 days.
  A value changed in the settings is saved and used at the next start. Out-of-range values fall
  back to the default. A write failure doesn't stop the app, and recording happens outside debug
  mode too.
- Reading tool: a sample log gives the expected timeline.
- Performance: the state summary takes under 5 ms per call (4 plots, 3 series each).

### 45. Side-effect tests: unrelated features must not change (added 2026-10-06, user request)

Requirements: 5.36. This chapter only adds tests; the app's behavior does not change (bugs found are fixed as separate work).

#### 45.1 Mechanism

- A new test file `tests/parquet_analyzer/test_side_effects.py`, and small helpers used there (in the same file, or in
  `tests/parquet_analyzer/side_effects_helper.py`).
- **Starting state** (the same for every `MainWindow`; uses `sample_parquet_path` and `isolated_config`):
  - Two plots (both time-domain, X-linked). The first has two raw variables and one derived variable (using a map, e.g.
    in the form `sample_map(a, b)`; the map is created in `tmp` and registered). The second has one raw variable.
  - **The range is not at home but partway** (zoomed to around 20–60% of the whole). This is so that any movement is visible.
  - Cursor ON, one cursor placed inside the range. Value display (hover) at the default setting.
- **How one operation is checked**:
  1. Take the state (`MainWindow.state_snapshot()`).
  2. Trigger the operation **from the real entry point**: toolbar via `QAction.trigger()`, menu items via the `QAction` of that
     menu, buttons, legend and navigator via `QTest` mouse operations (Qt's real routing, per the dev rules test rules).
     All dialogs are monkeypatched to return fixed answers (table below).
  3. Wait until settled: run `processEvents` until `_busy_sources` is empty and no operation-log result is pending
     (limit 5 seconds; exceeding it fails).
  4. Take the state again and compute the difference with `core.oplog.diff_states(before, after)`.
  5. Check that each path in the difference matches one of the operation's "may change" items (path patterns in `fnmatch`
     form, e.g. `plots.*.series_info.*.points`). If an item matches none, fail, showing the operation name, path and
     before/after values side by side.
  6. Extra checks (common to all operations):
     - **Re-reading the file**: `MainWindow.load_parquet` and recreation of `DataSource` are not called (except for
       "Open", "Load view" and "Change time axis"). Counted with monkeypatch.
     - **Unexpected dialogs**: if a `QMessageBox`, `QInputDialog` or `QFileDialog` appears that is not in the operation's
       table, fail (all are monkeypatched and their calls recorded).
     - **Exceptions**: no new `exception` record appears in the operation log (the `log` mechanism, ch. 30).
- Floating-point handling of the difference is the same as in the operation log (relative change below 1e-9 counts as no change, 30.2).
- **Test time**: aim for under 30 seconds in total (small sample, new window per operation). If it is slow, speed up building
  the starting state. Do not reuse windows, since earlier operations would remain.

#### 45.2 Table of operations and "may change" items

`*` stands for any one segment such as a plot ID or series name. Items not listed here (`file.*`, `x_range` of other plots,
`navigator.region`, `anchor`, `cursors.*`, `features`, etc.) must not change.

| Operation (entry point) | Dialog answer | May change |
|---|---|---|
| Home | — | `plots.*.x_range`, `plots.*.y_range`, `navigator.region`, `plots.*.series_info.*.points`, `coarse` |
| Redraw | — | `plots.*.series_info.*.points`, `coarse`, `plots.*.y_range` |
| Add plot | — | `plots.<new ID>` (the whole item set of the new plot), `plots.*.height_ratio` |
| Statistics | Only opens the window | none |
| Graphs ▾ → XY, 3D, histogram (those whose feature flag is on) | Only opens the window | none |
| Open function manager and close | Close without changes | none |
| Function manager: add a function not used by a plot and save | Name and expression given fixed values | none |
| Open map manager and close | Close without changes | none |
| Map manager: change one map value used by a plot's derived variable and save | — | that derived variable's `series_info.*.points`, `coarse`, and that plot's `y_range` |
| Map manager: reload the same map from file | — | same as above |
| Save view | Name given a fixed value; the "saved" notice (`QMessageBox.information`) may appear | none |
| Save image | Destination given as a path in `tmp` | none |
| Toggle coarse display | — | `downsample_enabled`, `plots.*.series_info.*.points`, `coarse` |
| Toggle cursors | — | `cursors.*` |
| Toggle value display | — | `hover_values` |
| Open settings and OK (no change) | — | none |
| Delete the selected series (Del key) | — | the plot's `series`, `series_info.<that series>`, `y_range` of the plot that holds the selected series, `plots.*.series_info.*.selected` |
| Hide a series from the legend | — | that series' `visible`, `points` |
| Add a derived variable from the expression field (not added to a plot) | — | none |
| Add the derived variable in the expression field to a plot | — | that plot's `series`, `series_info.<that series>`, `y_range` |
| Drag the navigator range | — | linked plots' `x_range`, `y_range`, `points`, `coarse`, `navigator.region` |
| "Recompute" in an analysis window (statistics, XY, histogram) | — | none (the main window state does not change) |

- "Open", "Load view" and "Change time axis" change everything, so they are not in this table
  (they are checked by the tests of their own chapters).
- Entry points of features whose feature flag is OFF (such as `fft`) are skipped by leaving them out of the table
  (not by `pytest.skip`). They enter the table when the flag is turned ON.
- **When the table and the actual behavior disagree**: the implementer does not loosen the test to match the table, nor
  change app code to match it. The implementer reports the operation, item and before/after values that disagree; the
  main session decides whether it is a bug or a table error. Items judged to be bugs are marked
  `pytest.mark.xfail(strict=True, reason="dev notes item <number>")`, so that the fix is noticed automatically later
  (example: saving or reloading a map returned the navigator range to the whole range = dev notes item 14; found during
  implementation, fixed by item 14's change (plots 24.1), so the xfail was removed).

#### 45.3 Test plan

- Each row of the table is one test (`pytest.mark.parametrize`, IDs are the operation names).
- Confirm the checker itself catches problems:
  - A fake operation that moves the range (changes `navigator.region`), run with "May change: none", fails, and the
    message shows `navigator.region` with before/after values.
  - A fake operation that calls `load_parquet`, and a fake operation that shows a `QMessageBox.warning` not in the table,
    each fail.
  - Pattern matching: `plots.*.x_range` matches `plots.p1.x_range` and does not match `plots.p1.series`.
- Total test time stays at about 30 seconds (if not, report it).

### 46. Stronger tests: random checks, failure paths, windowed reads, view backward compatibility (added 2026-10-08)

Requirements: 5.38. This chapter only adds tests; the app code (`src/`) does not change. If a test fails, do not loosen the test
or fix the app. Report the input (the minimal example hypothesis shrank to), the expectation and the actual result. The main
session decides whether it is a bug or a test error; if it is a bug, mark it `pytest.mark.xfail(strict=True, reason="dev notes item <number>")`
and fix it in separate work.

#### 46.1 Common: hypothesis settings

- Add `hypothesis` to the dev dependencies (`[dependency-groups] dev` in `pyproject.toml`; update `uv.lock` too). Do not add it to the app's dependencies.
- The settings are registered as profiles in `tests/parquet_analyzer/conftest.py`:
  - `default` (the default): `derandomize=True` (the same inputs every time; results do not vary in CI or parallel runs, and xdist IDs are stable),
    `max_examples` is the limit set per test (see the sections below; guideline 50–200), `deadline=None` (CI on Ubuntu is slow,
    so one example's time must not fail it), `database=None` (do not create `.hypothesis/`), `print_blob=True`.
  - `thorough`: `derandomize=False`, `max_examples` ×10. For extra manual runs:
    `HYPOTHESIS_PROFILE=thorough uv run pytest tests/parquet_analyzer/test_property_*.py`.
  - The profile is chosen with the environment variable `HYPOTHESIS_PROFILE` (`default` if unset).
- Add `.hypothesis/` to `.gitignore` (it is created by `thorough`).
- When something hangs: do not use the timeout plugin. Rely on the existing `faulthandler_timeout = 120`.
- **Run time**: the whole set of A1 tests should take **within 15 seconds** even without parallelism (`-n 0`) as a guideline. If it
  exceeds that, lower `max_examples` or the data size and write the measured time in the report.
- Files are `tests/parquet_analyzer/test_property_<target>.py` (one file per target).
- Building data: large arrays (tens of thousands of rows) are not built element by element by hypothesis (slow, and shrinking
  does not work well). Hypothesis draws **a few values that fix the shape** (length, ratio of duplicates, kind of NaN placement,
  random seed, etc.), and the array is assembled with numpy random numbers (`np.random.default_rng(seed)`). Small arrays
  (up to a few hundred) may use hypothesis `arrays`.

#### 46.2 A1: property tests on random data

| Target | Property to check | Input construction and notes |
|---|---|---|
| Expression evaluator (`evaluate_expression` in `core/expression.py`) | (1) For **arbitrary strings** (hypothesis `text()`, and strings made by putting expression parts—variable names, numbers, operators, parentheses, function names, `if`/`else`/`and`/`or`/`not`, comparisons, `,`—together at random; two ways), the only exception that escapes is `ExpressionError` (and its subclasses). When a value is returned, it is `np.ndarray` or a scalar. (2) **Valid expressions** (built from parts following the grammar: four arithmetic operations, `**`, unary `-`, `abs`/`sqrt`/`sin`/`exp`/`log`/`min`/`max`/`clip`/`where`/`isnan`, comparisons, conditional expressions): the result matches the result of computing the same expression directly with numpy (`np.testing.assert_allclose`, `equal_nan=True`, `rtol=1e-12`; the numpy computation is built alongside the expression) | 2–3 variables, `float64` of length 1–50 (including NaN, ±Inf, 0 and negatives). Exponents of `**` are limited to small integers or `0.5` (for overflow with large powers, matching `inf`/`nan` results is enough). Expression depth limited (about 5). User functions and maps are out of scope (registry is `None`). `max_examples`: (1) 300, (2) 200 |
| `TimeIndex.searchsorted` (`core/time_index.py`) | `TimeIndex.searchsorted(v, side)` == `np.searchsorted(full array, v, side)` (both `side` values `left` and `right`; `v` both as a scalar and as an array) | The time axis is **ascending and finite** (`TimeIndex.build` returns `None` for non-ascending or non-finite columns, so those are out of scope; confirm the `None` result in one example). Length 0.5–4 times `ANCHOR_ROWS` (8192), with **long runs of duplicates** (the same value for thousands of rows, including cases crossing an anchor boundary). `v` takes array values themselves, values between them, values below the minimum, above the maximum, anchor values, `±inf`, and `nan`. Parquet is written to `tmp_path` and opened with `ParquetDataSource` (also when passing a small `pin_rows`). Since files are written, `max_examples` is about 30, with many `v` tried in each example |
| `chunked_stats` (`core/analysis.py`) | `chunked_stats(window, lo, hi, chunk_rows, bins)` matches `basic_stats(y[lo:hi])`: `count`, `nan_count`, `min`, `max`, `median` **exactly equal**; `mean` and `std` with `rtol=1e-9` (an `atol` for values near zero). `window` is `lambda a, b: y[a:b]` | Length 0–50,000; `chunk_rows` from 1 to beyond the length (small values give many chunks); `bins` from 2 to `1<<16`. Values: uniform, mostly identical (all the same, nearly the same), near ±1e308, subnormal (near 5e-324), with NaN/±Inf mixed in, all non-finite. `lo`/`hi` are arbitrary positions within range (including `lo == hi`). `max_examples` 200 |
| LTTB (`lttb` in `core/downsample.py`) | Number of output points ≤ `max(n_out, 3)` (if the input is no larger, the input is returned as is); the first and last points are the input's first and last; each output point (x, y) equals some input point (same index pair); the output x keeps the input order (if input x is ascending, output x is ascending) | Length 0–20,000; `n_out` from 0 to length + 10. x ascending (with duplicates); y with NaN/±Inf mixed in in some cases. Non-finite input values must not raise exceptions. `max_examples` 200 |
| `rolling_mean`/`rolling_nanmean` (`core/analysis.py`) | Matches a naive implementation (for each point, slice the window in Python and average; the window range is in 31.1: output i covers `y[i-⌊n/2⌋]` to `y[i+⌈n/2⌉-1]`, truncated at the ends) (`rtol=1e-9`, `atol=1e-12`, `equal_nan=True`). `rolling_mean`: if any value in the window is NaN, the result is NaN; `rolling_nanmean`: only points whose window is entirely NaN are NaN | Length 0–300; window width 1 to length + 5 (windows longer than the data are truncated to the length); NaN mixed in, integer arrays. Keep value magnitudes around ±1e6 (convolution round-off would otherwise differ from the naive implementation; precision at large values is treated as a separate matter). `max_examples` 200 |
| `row_range_for_x` (`core/data_source.py`) | The returned row range `[s, e)` **contains all rows** whose time-axis value lies in `[x_lo, x_hi]` (it may be larger, since it works per row group). If no row overlaps, `(0, 0)`. `x_lo > x_hi` and NaN bounds give `(0, 0)` | Ascending (with duplicates) time axis written to Parquet with randomly varied row-group sizes (`row_group_size`). `x_lo`/`x_hi` are values, values between them, out of range, or NaN. Since files are written, `max_examples` 30, with many ranges tried in each example |

- Existing tests (`test_time_index.py`, `test_stats.py`, `test_downsample.py`, `test_expression*.py`) are not deleted or changed.
- If a failure occurs on a shrunk minimal example, report it as is (see the beginning of ch. 46).

#### 46.3 A2 to A4 (described in detail before starting)

- **A2 Failure paths of background work**: described in detail in 46.5 (2026-10-08).
- **A3 Windowed-read columns**: described in detail in 46.6 (2026-10-08).
- **A4 View backward compatibility**: described in detail in 46.7 (2026-10-08).

#### 46.4 Test plan

- Each row of the table in 46.2 is at least one test. `@given` from hypothesis; settings from the profile in 46.1.
- Self-check of the mechanism: run the `default` profile twice in a row and get the same results (`derandomize`). With `-n auto`,
  collection IDs match (xdist does not stop).
- The whole test suite (`uv run pytest -q -n auto`) passes, and the added time fits within a few seconds. Report the run time of
  the A1 tests alone (`-n 0`) in the report.

#### 46.5 A2: exhaustive failure paths of background work (added 2026-10-08, dev notes item 25)

Purpose: confirm, with a table, that `WorkerHub` (data ch. 37)'s rule "always exactly one result or failure, and cleanup
once it is received" (overview ch. 2, data ch. 28) holds **for every owner and every event**. Only tests are added; `src/`
does not change (if something fails, report it and mark it xfail, as in 46).

**Owners (columns)** (`main` on 2026-10-08; the location of `submit`)

| Symbol | Owner / job | Location | Receive (success / failure) |
|---|---|---|---|
| D | Series thinning (first draw `_on_thin_drawn` and normal `_on_downsampled`) | `_hub` in `plot_widget.py` | same as left / `_on_series_worker_failed` |
| P | Pyramid build | `_hub` in `plot_widget.py` | `_on_pyramid_ready` / `_on_pyramid_failed` |
| N | Navigator overview (raw columns and derived variables) | `_hub` in `navigator_sync.py` | `_on_navigator_overview_ready`, `_on_derived_overview_built` / `…_failed` |
| S | Statistics | `_stats_hub` in `main_window.py` | `_on_stats_finished` (failure is `{"error": …}`) |
| F | FFT (feature flag `fft` is OFF but the processing still works; the tests call it regardless of the flag) | `_fft_hub` in `main_window.py` | `_on_fft_finished` / `_on_fft_failed` |
| A | Analysis windows (XY, histogram, map points for XY; also the rectangle job that removes map points once 22 is merged) | `analysis_window.py` (`main._analysis_hub`) | `_on_finished` / `_on_failed` |

**Events (rows)**: what happens while a job is running (kept in place with the "holding" tool described below).

| Symbol | Event |
|---|---|
| 1 | Success (finishes as is) |
| 2 | Exception (the processing function raises) |
| 3 | Becomes stale by a new job (the same owner receives a new request: zoom, recompute, range change; the generation counter discards the old result) |
| 4 | The receiver disappears (series removed, plot removed, analysis window closed) |
| 5 | Another file is opened |
| 6 | The time axis is switched |

**What to check in each cell (where applicable)**:
- No crash, no freeze (wait in real time until settled, with a limit; fail if exceeded. CI on Ubuntu is slow, so insert `sleep`).
- Received **exactly once or zero times** (zero is fine for stale or vanished receivers), and an old result is **not used** in the screen or state
  (e.g. in 3, the old thinned points are not drawn; in 5, the statistics of the previous file do not appear).
- Cleanup: the "computing" / "drawing" markers (`_busy_sources` etc.) are cleared, and `hub.pending` is empty (no strong reference remains).
- On failure (2), the user-facing notice and log appear exactly once as expected (not repeated for the same failure).
- No new `exception` record appears in the operation log (an expected failure in 2 is recorded only as a job failure).

**Procedure**:
1. First read the existing tests (`test_worker_release.py`, `test_worker_failure_and_time_order.py`, `test_worker_failure_followups.py`,
   `test_ui_stress.py`, `test_big_*.py`, `test_navigator_*.py`, `test_xy*.py`, `test_histogram*.py`, and others) and list which
   tests fill each cell in the table above (**put the table in the docstring at the top of the test file**: for each cell, "existing: file::test name",
   "new: test name", or "out of scope: reason").
2. Add tests for the empty cells to `tests/parquet_analyzer/test_worker_paths.py` (owner × event via `parametrize`, IDs like `D-3`).
3. **Holding tool**: wrap the processing functions (`lttb`, `build_pyramid`, `thin_column`, `chunked_stats`/`basic_stats`, `fft`, the
   analysis computation functions) with monkeypatch, and stop them with `threading.Event` (also signal that they started, with an `Event`).
   While held, cause the event; after releasing, wait until settled. For 2, raise an exception from the wrapped function. Never end a test
   with a pool thread still held (release it in `finally`, including on failure).
4. Cells that do not apply (e.g. F-4, or P-6 when the pyramid is not rebuilt) are written as "out of scope" with the reason.

**Test plan**: every cell of the table is "existing", "new" or "out of scope". New tests all pass, or if a bug is found, are xfail (reported).
The added total test time stays within 10 seconds (keep holds short and data small).

**Decision for S-5 and F-5 (2026-10-13, user decision, dev notes item 31)**: if another file is opened while statistics or FFT jobs are
running, **the results of the previous file are discarded** (no statistics dialog, no spectrum plot is added).
- `MainWindow` has `_file_generation: int` (starts at 0). `load_parquet` increments it by 1 **when it successfully switches to the new file**
  (not when loading fails; the previous file stays).
- `_on_stats_requested` and `_on_fft_requested` pass the `_file_generation` at the time of the request to the receiver (`lambda`).
  If the passed generation differs from the current one, `_on_stats_finished`, `_on_fft_finished` and `_on_fft_failed` only clear the
  "computing" marker and discard the result (no dialog, plot or failure notice). Statistics discard all variables of one request as one
  unit. The discard is recorded with `logger.info` (the `job` record in the operation log is still written by `WorkerHub` as before).
  No user-facing notice is shown (the user opened another file themselves, so the absence of results is natural).
- Time-axis switches (S-6, F-6) continue to show the results of the time of the request (same file, so the values are valid).
- Tests: the `xfail(strict)` marks on `test_stats_result_of_previous_file_is_not_shown` and `test_fft_result_of_previous_file_is_not_added`
  in `test_worker_paths.py` are removed so they pass. Also added: a failure (exception) result from the previous file does not show a warning
  (for F, and for the statistics `{"error"}`); when a file that fails to load is opened, the generation does not change and results still
  appear; the "computing" marker and `hub.pending` are empty. The existing tests for S-6 and F-6 still pass.

#### 46.6 A3: windowed-read column agreement and watching for full reads (added 2026-10-08, dev notes item 26)

As in the beginning of 46, only tests are added and `src/` does not change (report failures; xfail(strict) for bugs).

**(1) Agreement tests** (`tests/parquet_analyzer/test_property_windowed.py`, hypothesis, profile from 46.1)

The oracle is built **without going through the class under test**: the column read whole with pyarrow/polars (converted to numbers with
`to_numeric`), and for expressions, the result of `evaluate_expression` on the same full array.

| Target | What to confirm | Input construction |
|---|---|---|
| `ParquetDataSource.read_window`, `WindowedColumn.window` | A **sequence** of arbitrary `(lo, hi)` (including repeated identical ranges, adjacent ranges, ranges crossing block boundaries, `lo == hi`, `hi > row count`, `lo < 0`), read in order, each exactly equal to the full `[lo:hi]` (NaN positions included). `read_window` reading several columns at once too | `ParquetDataSource(path, block_rows=small, cache_blocks=small)` (e.g. 1000 rows, 2–4 blocks; both cache hits and evictions occur). Row count 0.5–20 times the block size; row-group sizes arbitrary. Columns `float64`, integer, with NaN. Since files are written, `max_examples` 30, with many ranges in each example |
| `DerivedColumn.window` (data 36.6 C) | `window(lo, hi)` of a column built by `make_derived_column` equals `[lo:hi]` of the full computation (`assert_allclose`, `rtol=1e-12`, `equal_nan=True`; for functions with a margin, the edge rows too) | Expressions: element-wise (`a*2+b`), `delta(a)`, `rolling_mean(a, n)`, `rolling_nanmean(a, n)`, their nestings (`delta(rolling_mean(a, 5))`), and ones taking another `DerivedColumn` as input. Inputs are `WindowedColumn` (same small blocks as above). `n` from 1 to beyond the row count. An expression for which `make_derived_column` returns `None` (margin unknown) is out of scope; confirm this in one example. `max_examples` 50 |

#### 46.7 A4: view backward compatibility (added 2026-10-08, dev notes item 27)

Purpose: confirm that view JSON saved by past versions can be read by the current code, using **files actually saved by that version's code**
(persistence 8, 20, 33, 35: missing items are warned about and skipped; unknown keys are ignored; shape errors raise `ViewFormatError`).

**Versions** (git commits; the only tag is `v0.3.0`):

| Version | Commit | Note |
|---|---|---|
| 0.1.0 | `f5573e9^` (just before raising to 0.2.0, 2026-09-30) | last form of 0.1 |
| 0.2.0 | `f5573e9` (2026-09-30) | |
| 0.3.0 | `v0.3.0` (`860303a`, 2026-10-04) | saves the time-axis column and the relative path of the original file (ch. 33, 34) |

**How to make them** (placed in `tests/parquet_analyzer/data/views/make_old_views.py`; the steps are also written in the docstring at the top of the test file):

1. Temporarily `git worktree add <tmp> <commit>` (run `git worktree remove` afterwards; the repository's working tree is not touched).
2. Put that version's `src/` at the head of `sys.path`, run `MainWindow` with `QT_QPA_PLATFORM=offscreen`, open a fixed Parquet file
   (written by the script; columns: `time` (ascending seconds), `sine`, `cosine`, `ramp`, `flag` (0/1), 1,000 rows), and save a **view with as many
   of that version's operations as possible**: multiple plots, multiple series in one plot, derived variables (including nested), series shown/hidden,
   X independence (if the version has it), the time-axis column (if any), user functions or maps (if any), plot heights, and display ranges.
   The script lists, per version, which operations it included. Operations for features the version does not have are skipped (judged with `hasattr` etc.).
   Saving calls that version's `MainWindow` save-view processing (dialogs monkeypatched). File name is `<version>/<name>.json`.
3. The dependencies do not have to match that version's `uv.lock` (it is enough that it runs with the current environment's `uv run python`).
   If a version does not run, skip it and report it (do not write the JSON by hand).
4. Commit the generated JSON. The script is not run during the test run (it needs git operations).

**Tests** (`tests/parquet_analyzer/test_view_backward_compat.py`):

- For each JSON (`parametrize`, IDs `<version>/<name>`): write the same Parquet as the script to `tmp_path`, open it with the current `MainWindow`,
  and `load_view`. Confirm: no exception, no `ViewFormatError`; the number of plots, the series names of each plot, the names and expressions of derived
  variables, the time-axis column (if any) and the shown/hidden state are as saved. Warnings (count them by monkeypatching `ui/messages`) must be **only the
  expected ones** (the expectations per version are written as a table in the test; e.g. items for features removed in the current version). The expectations
  are decided by first reading with the current code and having the main session judge whether the result matches the spec (persistence chapter); the implementer reports the actual results.
- The "missing" marks in the view manager (`view_manager.py`) match exactly what `load_view` actually skipped (an invariant).
- After loading, save again with the current version and read again: the same state results (round trip).
- In future, when the view format changes, run the same script on `main` before the change and add the JSON (noted at the top of the script).

**Test plan**: all 3 versions × several files pass; any that do not are reported (whether to mark them xfail(strict) is decided by the main session).

### 48. Toolbar cleanup (added 2026-10-13, user request)

Requirements: [5.41](#541-toolbar-cleanup-split-into-two-wording-group-rare-operations-added-2026-10-13-user-request).
Rearrange `_build_actions()` in `ui/main_window.py`. Each operation's processing (`open_parquet_dialog`, `go_home`, etc.), the keys in `_actions`,
the keys in `settings.shortcuts`, and the `log_op` names are **not changed** (the table in ch. 45, the operation log and the shortcut settings in the settings
dialog can then be used as they are).

#### 48.1 Two toolbars

| objectName | Display name (`toggleViewAction` and the right-click menu) | Contents (`｜` is a separator) |
|---|---|---|
| `file_view_toolbar` | File & view (watch Qt's handling of `&`) | Open ｜ Time axis: [combo] ｜ View ▾, Save image ｜ Show all, Redraw ｜ ＋ Add plot ｜ Cursor, Show values |
| `analysis_toolbar` | Analysis | Statistics & graphs ▾ ｜ User functions, Maps ｜ ⚙ ▾ |

- Both are added with `addToolBar` to `Qt.TopToolBarArea`, in this order (they sit in the same row; if the width is insufficient, leave it to Qt's normal behavior).
- The old `main_toolbar` is removed. Handling of items that disappear with feature flags (`user_functions`, `maps`, and the graph items) is the same as now.
  When both "User functions" and "Maps" in `analysis_toolbar` are absent, the separator before them is not shown either (two separators must not run together).
- "View ▾" and "⚙ ▾" are built the same way as "Graphs ▾" (analysis 25.5): `QToolButton` + `QMenu`, `InstantPopup`, with `objectName`
  (`view_button`/`view_menu`, `settings_button`/`settings_menu`). Operations inside the menus are also added with `self.addAction(action)`, so the
  shortcuts work even when the menu is closed (same as `_build_graphs_menu`).
  - "View ▾": `save_view` as "Save..."; `load_view` as "Manage...".
  - "⚙ ▾": `toggle_downsample` as "Allow fast display" (with a check mark; the tooltip stays as now; menu item tooltips are shown with
    `menu.setToolTipsVisible(True)`), a separator, "Settings...", "Reset layout". The button text is "⚙", tooltip "Settings and display options".
- "Cursor" and "Show values" stay checkable and are placed at the right end of `file_view_toolbar`.

#### 48.2 Wording (`ui/i18n.py`)

| Key | Now | New |
|---|---|---|
| `toolbar.home` | Home | Show all |
| `toolbar.save_view` | Save view | Save... (inside "View ▾") |
| `toolbar.load_view` | View... | Manage... (same place) |
| `toolbar.functions` | Functions | User functions |
| `toolbar.settings` | Settings | unchanged (also used as the settings dialog title) |
| new `toolbar.settings_item` | — | Settings... (inside "⚙ ▾") |
| `fnref.button` | Functions... | Function list... |
| new `toolbar.view_menu` | — | View |
| new `toolbar.view_menu_tooltip` | — | Save or manage views |
| new `toolbar.settings_menu_tooltip` | — | Settings and display options |
| new `toolbar.file_view_title` | — | File && view |
| new `toolbar.analysis_title` | — | Analysis |

- The shortcut section of the settings dialog (persistence 9.1) uses separate keys `shortcut.*` (`shortcut_label`) and its wording is understandable without context,
  so it is mostly unchanged. To align with the toolbar names, only these three change (the Japanese labels follow the toolbar wording;
  the English ones are unchanged except for `shortcut.home` and `shortcut.user_functions`, which take the toolbar names):
  `shortcut.home` Show all, `shortcut.toggle_downsample` (its English label is unchanged), and `shortcut.user_functions` User functions.
- Tooltip for "Show all" (add if missing): "Return all plots to the full range of the data".

#### 48.3 Saving the layout (persistence 9.3)

- A `window_state` saved by the old version contains `main_toolbar`. Attach a **version number** to `saveState`/`restoreState`
  (`saveState(1)`, `restoreState(state, 1)`); discard old states and use the default arrangement (the window position and size, `window_geometry`, are still restored).
  When the version does not match and `restoreState` returns `False`, no warning is shown.
- "Reset layout" works as before (the default state is now the two toolbars).

#### 48.4 Test plan

- The two toolbars exist with the `objectName`s as above, and the items of `file_view_toolbar` and `analysis_toolbar` are in this order (as in the table;
  separator positions too). `main_toolbar` does not exist.
- The contents of "View ▾" and "⚙ ▾", and triggering each menu item calls the same processing as now (`save_view`, `load_view`,
  the check toggle of `toggle_downsample`, settings, reset layout).
- The shortcuts set on menu items work without opening the menu (for `toggle_downsample` and `save_view`).
- With feature flags `user_functions` and `maps` both OFF, "User functions", "Maps" and their separator are absent, and no separators run together.
- Starting with a `window_state` that includes the old `main_toolbar` (saved without a version number) does not raise an exception; the two toolbars are
  at their default positions, and `window_geometry` is restored. Saving and restoring in the new form returns the two toolbars to their positions.
- Both English and Japanese have translations for the new keys (add them if the existing i18n test does not cover them).
- Existing tests that check `main_toolbar` or the old wording (`test_xyz_3d.py`, `test_features.py`, etc.) are updated to the new form. The
  table in 45 (`test_side_effects.py`) uses operation keys that do not change, so its rows are unchanged (confirm that it still passes).
- Confirm: open `MainWindow` headlessly, draw to an image, and check the arrangement and wording visually (widths 1400 px and 1000 px).

### 51. CI setup (added 2026-10-13, user decision)

Requirements: 5.44. Windows is still postponed along with the exe build (dev notes "exe build").

#### 51.1 The macOS job (manual runs only)

- A new workflow `.github/workflows/test-macos.yml` (name `Test (macOS)`). The only trigger is `workflow_dispatch`.
  `runs-on: macos-latest`; the steps are the same as Ubuntu (checkout → `astral-sh/setup-uv` → `uv sync --group dev` →
  `uv run pytest -n auto`). The apt steps for the Qt shared libraries are not needed.
- How to run: in GitHub "Actions" → "Test (macOS)" → "Run workflow", choose the branch; or
  `gh workflow run test-macos.yml --ref <branch>` (the branch must already be pushed). Results: `gh run watch`.
- Add `workflow_dispatch:` to the `on:` of `.github/workflows/test.yml` (Ubuntu), so a branch can also be tried with
  `gh workflow run test.yml --ref <branch>`. Behavior on push and on PR stays as now.
- Guideline: use it before merging a change that affects plots or worker timing, or to isolate a failure that only happens on Ubuntu.
  Add one line on how to run it to the Commands section of dev rules.

#### 51.2 Fixing tests that fail on Ubuntu

- Failing tests (CI log, `main` on 2026-10-13):
  - `test_worker_paths.py::test_stats_paths[S-3]`: two statistics jobs fail with `gate: held job was never released` (after 30 seconds), and
    a failure record remains in `assert_clean_log`. Failing on every run since the run of 2026-10-07 23:33.
  - `test_plot_widget.py`'s `test_a_button_fits_only_visible_x_range_and_leaves_x_alone`,
    `test_a_button_on_windowed_series_uses_drawn_curve_not_window_reads`, and
    `test_a_button_changes_only_the_pressed_plot_in_a_linked_grid`: after pressing "A", Y stays at the range from before the press
    (the "A" operation does not arrive). `test_a_button_with_cursor_fits_y_to_data_and_stays_put`, of the same shape, passes.
- Policy: first confirm the cause (if it does not reproduce on the local Mac, push the branch and run the Ubuntu workflow by hand;
  add temporary diagnostic output if needed). **If it is a bug in the app, fix the app; if it is in how the test is written (differences
  between real machine and CI, or waiting), fix the test.** Do not just loosen tests to make them pass. Waiting uses real-time `sleep` with a limit (dev rules rule).
- Done when: the Ubuntu CI is green on the fixed branch, and all tests pass locally. Add the cause and the fix to this section.
  Also run the macOS job once by hand and record the result here.
- **Cause and fix (2026-10-13)**: neither was an app bug; both were in how the tests were written.
  - `test_stats_paths[S-3]`: statistics jobs run on `MainWindow._analysis_thread_pool` (a dedicated pool per window), but
    `_wide_pools` in `test_worker_paths.py` only widened `QThreadPool.globalInstance()`. The default thread count of a pool is the number of
    cores, and on CI's Ubuntu (2 cores) the third job could not enter the gate; the first and second waited 30 seconds and failed
    (it does not reproduce on the multi-core local Mac; with two pools it reproduced locally). It had failed on every run since the test was
    added at 2026-10-07 23:31 (UTC). Fix: `open_window` also sets the window's analysis pool to 6 threads.
  - The three "A" button tests: the helper `_click_a_button` in the tests called `processEvents()` after `btn.show()`. pyqtgraph's
    `updateButtons()` hides the buttons whenever the visibility changes (unless hovering), so on the slower Ubuntu, the range change and redraw events
    that were pending ran in between and hid the button, and the click did not arrive (confirmed by a diagnostic on CI: in the two failing cases,
    `isVisible()` was false just before the press). The app side is correct (it works on a real display). Fix: flush the events being waited on with
    `processEvents()` first, then `show()`, then click immediately without processing events (assert `isVisible()` just before the press).
    Confirmed that removing the `fit_y` connection makes these 7 tests fail.

### 52. Introducing lint (ruff) (added 2026-10-13, user decision)

Requirements: 5.45. Source: dev notes "research targets" (2026-10-13).

#### 52.1 Configuration

- Add `ruff` to `[dependency-groups] dev` in `pyproject.toml` (lower bound is the version at introduction; `ruff>=0.17` as of 2026-10-13).
- `[tool.ruff]`: `target-version = "py311"` (matching `requires-python`), `line-length = 100` (E501 is not checked, but it is used for wrapping by `I`),
  `src = ["src", "tests"]` (so that `I` treats our own package as first-party). Targets are `src/`, `tests/`, `tools/` (exclude `data/`, `log/`, `cfg/`, etc. with `extend-exclude` if needed).
- `[tool.ruff.lint]`: `select = ["E", "F", "I", "UP"]`, `ignore = ["E501"]`.
- The test-fixture style of importing a fixture from another test module and receiving it as an argument (`from .test_x import win` →
  `def test_y(win)`) is reported as F811 (redefinition of an unused name) (about 70 cases). This is not a reason to change how the tests are
  written, so F811 is turned off only for `tests/**` through `[tool.ruff.lint.per-file-ignores]` (and F401 for those imports too, under `tests/**`, if it appears).
  Moving shared fixtures into `conftest.py` is not done this time (it would need a review of behavior and parallel-run effects; separate work).

#### 52.2 The first batch of fixes

- Counts on `main` on 2026-10-13 (ruff 0.17.0, E501 excluded): I001 76, F811 71 (turned off in 52.1), UP037 33, UP035 23,
  F401 18, UP017 8, E741 6, F841 5, E731 3, UP031 1.
- Items fixed automatically (`ruff check --fix`, do not use unsafe fixes) are applied as is: I001, UP037, UP035, F401, UP017.
  However, do not remove an F401 that is a re-export in an `__init__.py` (an `from .x import y` to expose a name outward), or an import
  made for a side effect (e.g. OpenGL or Qt initialization, the registration in `features`). For such cases, add to `__all__`
  or use `# noqa: F401` with the reason written.
- Fixed by hand:
  - E741 (confusing names such as `l`, in tests): rename.
  - E731 (`f = lambda ...`, in tests): change to `def`.
  - F841 (unused variable): remove in tests. The `consts` in `src/parquet_analyzer/core/functions.py` must be **checked for intent before removing it**
    (if it is an unused leftover it is a bug; stop, do not remove it, and report).
  - UP031 (`%` formatting): change to f-string. However, lazy `logging` formatting (`log.debug("%s", x)`) should be out of scope; if it appears, report it.
- Findings that could change behavior (beyond the above, or those needing unsafe fixes) are not fixed; they are listed in the report and the main session decides each one.

- **Implementation result (2026-10-13, `feat/52-ruff`)**: counted with the settings in `pyproject.toml` (the `src` setting changes how packages are
  classified), I001 is 57 (76 above is the value counted without settings). 139 items fixed automatically (I001 57, UP037 33, UP035 23, F401 18, UP017 8),
  14 fixed by hand (E741 6, E731 3, F841 4, UP031 1). The F401s removed with `src` are 3 (`Iterator` in `map_grid.py`, `QSizePolicy` in `flow_layout.py`,
  `Qt` in `view_manager.py`); there were no re-export or side-effect imports. The `consts` in `core/functions.py` is only created in `_static_check_calls`
  and not used (constants of the called functions are checked via `target.constants`), so it was removed. The number of tests is the same before and after
  (3196 passed, 12 skipped, 1 xfail).

#### 52.3 CI and documentation

- Add one step, `uv run ruff check`, to `.github/workflows/test.yml` (Ubuntu) after installing dependencies and before the tests (it fails faster than the tests).
  Do not add it to the macOS workflow (51.1), since the result would be identical (wasted run time).
- Add `uv run ruff check [--fix]` as one line to the Commands section of dev rules. Check whether dev rules needs a lint note; if it departs from
  the template, raise its version and changelog.

#### 52.4 Test plan

- Since app behavior does not change, no new tests are added.
- Confirm: `uv run ruff check` reports 0 findings; the full test run (`uv run pytest -q -n auto`) passes with the same count (confirming no test
  functions were removed); starting `tools/run.sh` works (create and close `MainWindow` headlessly).
- After pushing, run the Ubuntu workflow by hand on the branch (`gh workflow run test.yml --ref <branch>`) and confirm the lint step and the
  tests are green.

## Relations to other domains

- **All domains**: feature flags (ch. 21), logs, the error-handling policy and i18n are used by
  every domain.
- **Persistence** ([persistence.md](./persistence.md)): handling of the settings file, paths.
- **All domains** (ch. 30, 2026-10-03): the operation log records every domain's `log_op`,
  messages (`ui/messages`) and background `job`s. When adding a new window or operation, call
  `log_op` at the start of the operation and route dialogs through `ui/messages`.
- **All domains** (ch. 45, 2026-10-06): when adding a new button, menu or operation to the main window, add one row
  (the items that may change) to the table in 45.2 and put it in `test_side_effects.py`. When adding an item to the state
  summary (30.2), review for each row of the table whether that item may change.
- **Data, expressions and analysis** (ch. 46, 2026-10-08): there are tests that check, on random inputs, the windowed-read
  columns (data ch. 36), the expression evaluator (expressions 6.1 and ch. 43), `rolling_mean` (expressions 31.1), and the
  properties of `chunked_stats` and LTTB. When changing these guarantees (for example, that results equal those computed on the
  whole array, or that failures are only `ExpressionError`), also update `test_property_*.py`.
- **Persistence** (ch. 48, 2026-10-13): the toolbar is split into `file_view_toolbar` and `analysis_toolbar`, and
  `saveState`/`restoreState` use version number 1 (persistence 9.3). When adding an item to a toolbar, decide which of the
  two groups in the table in 48.1 it belongs to, and put rare operations into "⚙ ▾".
- **All domains** (ch. 37): Python's automatic GC is turned off and pyqtgraph's
  `GarbageCollector` collects from the UI thread.
- **Persistence** (persistence 54.4, 2026-10-14): in the analysis toolbar, next to "User functions, Maps", there is
  "Signal aliases" (shortcut `signal_aliases`). The storage location `cfg/parquet_analyzer/signal_aliases.json` is defined in `config.py`.
