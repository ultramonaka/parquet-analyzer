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
- The bug-report procedure (README, CLAUDE.md) becomes: "start normally and reproduce → hand over
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

## Relations to other domains

- **All domains**: feature flags (ch. 21), logs, the error-handling policy and i18n are used by
  every domain.
- **Persistence** ([persistence.md](./persistence.md)): handling of the settings file, paths.
- **All domains** (ch. 30, 2026-10-03): the operation log records every domain's `log_op`,
  messages (`ui/messages`) and background `job`s. When adding a new window or operation, call
  `log_op` at the start of the operation and route dialogs through `ui/messages`.
- **All domains** (ch. 37): Python's automatic GC is turned off and pyqtgraph's
  `GarbageCollector` collects from the UI thread.
