# Parquet Analyzer

**[English](#english)** | **[日本語](#japanese)**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.4.0-informational.svg)
![Author](https://img.shields.io/badge/author-ultramonaka-lightgrey.svg)

---

<a name="english"></a>
## English

A viewer and time-series analysis tool for Parquet files. Handles files of around 100 million
rows (reads only the visible range and decimates automatically), multiple plots, inter-variable
expressions, user functions and maps (lookup tables), summary statistics, XY and 3D plots,
cursors, a hover readout, saved views, image export, and an operation log.

For the specification, start at
[`src/parquet_analyzer/docs/overview.md`](src/parquet_analyzer/docs/overview.md) (overview), which
links to the per-domain specs in `src/parquet_analyzer/docs/domains/` (a Japanese edition, `*.ja.md`,
sits next to each file).

### Quick start

1. `tools/run.sh` (Windows: `tools\run.bat`; macOS double-click: `tools/run.command`), then "Open" a Parquet file.
2. Drag a column from the "Variables" panel onto a plot ("+ Add plot" for another pane).
3. Zoom and pan with Ctrl + scroll / scroll, or drag the frame in the navigator at the top.
4. Type an expression such as `speed * 3.6` into the expression bar at the bottom and "Add" it —
   it becomes a derived variable you can plot like a column.
5. Save the layout as a view ("View ▾" > "Save..." on the toolbar) to reopen it on this or another file.

**Reporting a bug**: just reproduce it in a normal run (every run writes an operation log), then
run `tools/show_ops_log.sh --session` and attach its output (or today's `*_ops_*.log` from
Settings > "Open log folder") to the report, with what you expected to happen.

**Large files** (measured on an Apple M4 Pro / 24 GB, 100 million rows, 4.6 GB): the first draw
takes about 1.1 s, zooming and panning 0.1–0.5 s; memory is about 1.4 GB in normal use and peaks at
about 4.7 GB (statistics over the whole file). Details: data domain 36.4 / 36.7.

### Requirements

- Windows or macOS
- [uv](https://docs.astral.sh/uv/) (uv provisions Python and every dependency
  automatically — nothing to install by hand)

### Running

```sh
tools/run.sh          # macOS/Linux
tools\run.bat          # Windows
```

On first launch `uv` automatically installs the dependencies (PySide6, pyqtgraph,
polars, pyarrow, numpy, matplotlib).

The UI is in English by default; switch to Japanese in "⚙ ▾" > "Settings..." (takes effect after
a restart). Button names below are the English ones.

On macOS, double-clicking a `.sh` file just opens it in a text editor by default and
doesn't run it (that's Finder's standard behavior). To launch by double-clicking, use
`tools/run.command` instead (a thin wrapper that just calls `run.sh` — Finder treats
`.command` files as executable). `tools/run_debug.command` does the same for debug mode
(calls `run_debug.sh`).

#### Debug mode

Verbose (DEBUG) logging plus a readable echo of the operation log on the console.
The operation log itself (every operation, the state it led to, background jobs,
messages and exceptions) is written in **every** run, debug or not, to
`log/parquet_analyzer/parquet_analyzer_ops_<date>.log`, so to report a bug just run
normally, reproduce it, and attach today's `*_ops_*.log` (Settings > "Open log folder").
`tools/show_ops_log.sh <file> [--last N] [--session]` prints it as a readable timeline.
Log files older than Settings > "Log retention (days)" (default 7) are deleted at startup.

```sh
tools/run_debug.sh     # macOS/Linux
tools\run_debug.bat     # Windows
```

(Equivalently enabled via the `--debug` flag or the `PARQUET_ANALYZER_DEBUG=1`
environment variable. The window title gets a `[DEBUG]` suffix while this is active.)

### Basic usage

1. Choose "Open" on the toolbar to select a Parquet file. The dialog starts in
   the last folder you opened, with the 5 most recent folders available in a sidebar.
   The currently open file's name is shown in the window title and in the status bar
   at the bottom left (hover it for the full path).
2. The loaded columns are listed in the "Variables" panel (on the left by default).
   Drag one onto a plot to overlay it there (the legend updates automatically).
3. "+ Add plot" on the toolbar adds a new plot pane (see "Working with plots" below).
4. The navigator at the top of the window lets you quickly move the visible time
   range. It shows an overview of **whichever variable is overlaid first on the topmost
   plot** (falling back to another column if nothing has been plotted yet).

#### Working with plots

- **Height**: drag the border between two plots (saved in views as ratios).
- **Reorder**: drag the grip (dots) at a plot's left edge up or down, or use "Move up" /
  "Move down" in the right-click menu (heights are kept).
- **Select / remove a series**: click its name in the legend to select it (the line gets
  thicker), then press **Delete** (macOS "delete" works too) to remove just that series.
  Click again, click empty space, or press Esc to deselect.
- **Show / hide a series**: click the short line sample left of its name in the legend.
  Hidden series are left out of statistics, cursor values and image export.
- **Right-click menu**: "Delete series ▸", "Show/hide series ▸", "Separate series into new plot ▸",
  "Separate into new plot" (moves all but the first series), and "Remove plot".
- **Independent X axis**: the chain icon at a plot's left edge, or "Link X axis" in the
  right-click menu, lets that plot zoom and pan on its own (the last linked plot can't be
  unlinked). An unlinked plot offers "Stats for this range" in its right-click menu.
  The navigator shows the range of the linked plots.
- Time labels are shown only under the bottom linked plot and on unlinked plots. The Y-axis
  widths of stacked plots line up automatically.

#### Changing which column is used as the time (X) axis

By default the file's **first column** is used as the time (X) axis. Change it via the
"Time axis:" combo box on the toolbar. Switching axes returns the previous
time column to the variable panel as an ordinary variable, and every already-plotted
series is re-fit to the new time axis. If the time column isn't ascending or contains
missing values, the visible range, statistics and cursor values may be wrong, so a warning
is shown in the status bar (drawing continues and never freezes). To see how two variables
that go up and down relate to each other, use the XY plot rather than changing the time axis.

#### Zoom / pan controls

| Action | Effect (current default) |
|---|---|
| Mouse wheel scroll | Pan along the time axis (no zoom) |
| Ctrl + scroll | Zoom time (X) axis only |
| Shift + scroll | Zoom amplitude (Y) axis only |
| H key / "Show all" button | Reset the view to the full data range |
| R key / Redraw button | Recompute downsampling for the current view |

Which action each of plain/Ctrl+/Shift+scroll performs is configurable in the
"Settings" dialog's "Scroll controls" section — reassigning
an action already used by another modifier swaps the two, so every action always
stays reachable by exactly one modifier. The table above is just the default.

The time (X) axis tick granularity adapts automatically to the zoom level — date/month
when zoomed out, down to hour:minute:second.millisecond when zoomed into a few
seconds (this doesn't apply to an FFT plot's X axis, which is frequency in Hz).

Large datasets are automatically decimated for display based on pixel count (the LTTB
algorithm, which preserves a waveform's peaks/troughs while reducing point count).
This recompute runs asynchronously on a background thread — a progress indicator
appears in the status bar while it's running, but the UI itself never blocks.
"Allow coarse preview" in the toolbar's "⚙ ▾" menu can be turned off to always draw the real,
per-sample data (slower on very large files).

**Large files** (by default, when a column is over 512 MB; configurable in Settings) are
never read whole: only the visible range is read, on demand. A coarse preview is drawn
first (the status bar says "Showing a coarse preview") and is replaced by the detailed
curve once the overview has been built in the background. The time column isn't kept in
memory either (only when it is ascending; otherwise it is read in full with a warning).
Derived variables on a large file are computed for the visible range only, so their first
draw can take a moment (about 2 s at 100 million rows). As a guide, a 100-million-row,
4.6 GB file uses about 1.4 GB of memory in normal use (briefly about 4.5 GB while computing
statistics over the whole file).

#### Cursors

Turn on "Cursor" on the toolbar, then click a plot to place a vertical cursor showing
the time and each series' value there. Cursors can be dragged, are shared by every
time-domain plot, and are removed by clicking them. Turning "Cursor" off hides them
without deleting them.

#### Hover readout

Hovering over a plot shows a dotted vertical line and, at the top right, the time and the
value of each visible series there. Other plots linked on the X axis show the same line and
values. On large files the values shown while the mouse moves are the decimated ones (marked
"≈"); they are replaced by the real sample values once the mouse stops. Toggle it with
"Values" on the toolbar (saved in the settings). Hidden series aren't shown, and the readout
isn't included in exported images.

#### Inter-variable expressions and analysis

- Typing an expression that references variable names (e.g. `abs(A) - sqrt(B)`,
  `delta(rpm)`, `rolling_mean(rpm, 20)`, `clip(rpm, 0, 3000)`) into the expression bar
  at the bottom of the window and clicking "Add" creates a new derived variable in
  the variable panel (shown in italics; hover it to see its expression).
  - Operators: `+ - * / ** % //`
  - Functions (all elementwise): `abs`, `sqrt`, `delta` (first-order difference),
    `rolling_mean(a, window)` (centered moving average: the window spans samples before and
    after the point — for an even window n, n/2 before and n/2-1 after; a missing value in
    the window makes the result missing), `rolling_nanmean(a, window)` (the same centered
    window, ignoring missing values),
    `trailing_mean(a, window)` (past-only moving average: for each point, the mean of the current
    point and up to window-1 past points; at the start, averages available points; missing values
    propagate but only window-1 points forward), `trailing_nanmean(a, window)` (the same past-only
    window, ignoring missing values),
    `sin`, `cos`, `tan`, `exp`, `log`, `log10`, `sign`, `floor`, `ceil`, `round`,
    `min(a, b)`, `max(a, b)`, `clip(a, lo, hi)` (`min`/`max` are elementwise comparisons
    here, unlike Python's built-ins — they don't reduce to a single scalar).
  - Conditionals: `where(c, a, b)` or `a if c else b` (a where the condition is true, else b),
    comparisons `< <= > >= == !=` (chains like `0 < a < 5` work) and `and` / `or` / `not`,
    which give 1 (true) or 0 (false) and so can be used in arithmetic, e.g. `(a > 0) * a`.
    `isnan(a)` is 1 for missing values. A comparison or condition involving a missing value
    (NaN) is itself missing, except that `and`/`or` use three-valued logic (`true or NaN` is 1,
    `false and NaN` is 0); replace missing values with `where(isnan(a), 0, a)`.
- Defining a derived variable with the same name as an existing column is an error
  (to prevent accidentally shadowing real data).
- Right-click a derived variable in the panel for "Edit expression..." or "Delete".
  Editing happens in the expression bar ("Update" applies, "Cancel" or Esc aborts);
  derived variables built on it and its plotted curves are recomputed. A derived
  variable still used by another one can't be deleted, and an expression that refers
  back to itself (circularly) is rejected.
- Double-clicking a variable in the panel — or dragging it onto the expression bar —
  inserts its name at the cursor position, also while editing.
- "Function list..." in the expression bar opens a list of the built-in functions, user
  functions and maps (syntax, description, example) in a separate window; a chosen
  example can be inserted into the expression bar.
- Frequency analysis (FFT) exists internally, but it is hidden from the "Graphs ▾" menu by a
  development feature flag until its debugging is finished (not reachable from the GUI right
  now). Over a range whose sample spacing is uneven or has gaps, FFT isn't run and the reason
  is shown in the status bar (narrow the range to an evenly spaced part first).
- Clicking "Stats" computes count, missing-value count, min, max, mean,
  standard deviation, and median over every raw sample in the currently visible time
  range (not the downsampled curve actually drawn on screen) and shows the results in
  a separate window, for one row per variable. Targets whichever variable(s) are
  selected in the panel; if nothing is selected there, it falls back to whatever's
  currently overlaid on the plots, so a variable that's already plotted doesn't need
  re-selecting in the panel just to see its stats. An explicit panel selection always
  takes priority over what's plotted.

#### User functions and maps

- **User functions** ("User functions" on the toolbar): define an expression with arguments and
  constants (with default values) once and use it with any file. Example:
  `Power_flow_by_press(flow_lpm, press_kpa)` = `flow_lpm * press_kpa / k` (`k = 60000`); in an
  expression, `power1 = Power_flow_by_press(signal1, signal2)`, and override a constant with
  `…, k=1000)`. The "Apply to signals" tab lets you build the call by dragging variables from
  the panel onto the argument fields. Saved in `cfg/parquet_analyzer/user_functions.json`.
- **Maps** ("Maps" on the toolbar): import a CSV curve (2 columns) or map (first row is the
  X axis, first column the Y axis) and use it in expressions as `name(x)` / `name(x, y)`.
  Choose the interpolation (linear, nearest, previous value, spline) and what happens out of
  range (edge value, 0, linear extrapolation, missing). The imported CSV is copied and kept.
  "Edit..." renames the map and its axes (shown on the preview axes; a map in use can't be
  renamed). Table cells can be edited directly and saved (double-click to edit); while
  editing, the preview splits into "Before" and "After" so the difference is visible. The
  preview switches between 2D and a 3D surface. Sample files for trying it out:
  `uv run python tests/parquet_analyzer/generate_sample_maps.py` writes
  `data/raw/sample_curve.csv` (x³) and `sample_map.csv` (x²+y²; `sample_map(sine, cosine)` is
  about 1).
- Changing a function or map recomputes the derived variables and plotted curves that use
  it. A function or map in use can't be deleted.

#### XY / 3D plot

"Graphs ▾" > "XY / 3D plot" on the toolbar opens a separate window: pick one X and one or
more Y to draw a scatter plot of the visible time range. The time column can be chosen as an
axis too. Color: single, time, or a Z variable; display: scatter or density (automatic when
there are many points). "Recompute" re-reads the main window's current range, and "Follow main
range" keeps it in sync automatically. The point at the cursor's time is highlighted, and
"Save image" saves an annotated image.

Switching the display to "3D" draws a 3-D scatter plot of X, Y and Z (the chosen variables
carry over). Rotate and zoom with the mouse; the buttons above it (Home, XY, YZ, ZX) switch
the viewpoint. Where OpenGL isn't available 3D can't be selected (the tooltip says why).

#### Saving and loading views

"View ▾" > "Save..." on the toolbar saves the current plot layout, overlaid variables, and expressions as a
"view" (JSON, under `data/parquet_analyzer/`). Derived variables are saved as their defining
formula rather than computed values, so they're recomputed from the source columns
whenever the view is loaded — including after the underlying data has changed. If any
file is already open when you load a view — whether it's a different file, or
literally the file the view was saved against — only the saved layout is (re)applied
to it; the file itself is never (re-)opened from disk in that case (only when nothing
is open yet does loading a view open its recorded source file). A variable the saved
layout references that doesn't exist in the currently open file is simply skipped
(with a warning listing what couldn't be restored) — every other variable that does
match is still applied, so a view saved against one file works as expected when
reused against another that only shares *some* of its column names, not all of them.

A view also records the time-axis column, and loading it switches back to that column. The
source file's location is saved as a relative path as well, so a folder holding the data and
its views can be moved elsewhere and still open.

The save dialog lists the saved views: pick one to overwrite it (you are asked to confirm) or type a
new name. After a view has been loaded, its name is filled in first.

"View ▾" > "Manage..." opens the view manager: pick a saved view to see what it contains before
loading it — source file, each plot's channels with their colors, derived variables
and their expressions, the saved time range, and when it was saved. With a file open,
channels that file doesn't have (and would be skipped) are shown in red. Views can be
loaded (button or double-click) or deleted (after a confirmation) from there. A malformed
view file (a required item missing or of the wrong type) is not loaded; the manager says
which item is wrong (e.g. "plots: expected a list (got a string)") and lets you delete it.

#### Saving an image of the plots

"Save image" saves the plots currently shown as PNG, JPEG, TIFF, SVG, or PDF. The
plots are re-drawn (not screen-captured) with aligned time axes and a legend per plot,
and the bottom-right corner records the source file (and view name), the displayed
time range, the export date/time, and the app version.

#### Settings menu

"⚙ ▾" > "Settings..." on the toolbar lets you change the following (saved to
`cfg/parquet_analyzer/settings.json` and restored on next launch):

- Auto-redraw on/off (manual redraw via the R button/shortcut always works even when
  off)
- Downsampling density (displayed points per pixel)
- The size threshold above which a column is read from disk on demand (visible
  range only) instead of being fully loaded once and kept resident — only affects
  files opened after the change
- Which action (X zoom / Y zoom / pan) each of plain/Ctrl+/Shift+scroll performs
- Keyboard shortcuts for each toolbar action (show all, redraw, open, add plot, statistics,
  XY / 3D plot, save view, view manager, save image, coarse-preview toggle, cursor, values,
  functions, maps) and for removing the selected series (Delete by default) — frequency
  analysis is omitted here while it is hidden from the menu, see above
- UI language (English / 日本語; takes effect after a restart)
- Log retention in days (default 7) and "Open log folder"

#### Layout persistence

Window size, the variable panel's dock position, and similar screen layout are saved
automatically on exit and restored on next launch. The variable panel defaults to the
left side of the window. "Reset layout" in the toolbar's "⚙ ▾" menu discards
the saved layout and restores the default arrangement at any time.

### Converting MDF/MATLAB files

This app only reads Parquet. MDF (`.mf4`/`.mdf`/`.dat`) and MATLAB (`.mat`, both v5/v7
and v7.3) measurement files need to be converted to Parquet first:

```sh
tools/convert_to_parquet.sh <input file> [output.parquet] [--time-var COLUMN] [--raster SECONDS]
```

- Run `uv sync --group convert` once to install the conversion-only dependencies
  (asammdf, scipy, h5py) — kept in a separate group since the regular app never needs
  them.
- MDF channels can run at different sample rates; `--raster` picks a shared time step
  in seconds to resample them all onto (omit it to use the source timestamps as-is).
- MATLAB files carry no information about which variable is the time axis, so a
  variable named `time`/`t`/`timestamp`/`timestamps` is auto-detected. Pass
  `--time-var` explicitly if it's named something else.

### Where data is stored

Centralized in `src/parquet_analyzer/config.py`. All of the following are per-machine
runtime state and excluded from version control:

| Purpose | Path |
|---|---|
| App settings | `cfg/parquet_analyzer/settings.json` |
| Saved views | `data/parquet_analyzer/*.json` |
| Log (normal) | `log/parquet_analyzer/parquet_analyzer_<date>.log` |
| Log (operation log, always written, JSON Lines) | `log/parquet_analyzer/parquet_analyzer_ops_<date>.log` |
| User functions | `cfg/parquet_analyzer/user_functions.json` |
| Maps (copies of the imported CSVs) | `data/parquet_analyzer/maps/` |

### For developers

```sh
uv sync --group dev   # install dev-only dependencies (pytest, etc.)
uv run pytest         # run the test suite
```

Exercising the app headlessly (no real display):

```sh
QT_QPA_PLATFORM=offscreen bash tools/run.sh
```

A small synthetic file for trying features out (columns with known relationships, e.g.
`sine**2 + cosine**2` is 1 everywhere; written to `data/raw/feature_test.parquet`):

```sh
uv run python tests/parquet_analyzer/generate_feature_test_parquet.py
```

### License

Licensed under the **[MIT License](LICENSE)**.

For information about third-party software and libraries used by this application, see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

**Author**: [ultramonaka](https://github.com/ultramonaka)

---

<a name="japanese"></a>
## 日本語

Parquetファイルのビューワー・時系列解析ツール。1億行規模の大きなファイルの表示（表示範囲だけを
読み、自動で間引く）、マルチプロット、変数間演算、ユーザー関数・マップ（参照テーブル）、統計表示、
XY・3Dプロット、カーソル、ホバー表示、ビュー保存、画像保存、操作ログなどに対応する。

仕様の詳細は [`src/parquet_analyzer/docs/overview.ja.md`](src/parquet_analyzer/docs/overview.ja.md)
（総括仕様）から、分野ごとの仕様（`src/parquet_analyzer/docs/domains/`）へ辿れる。

### 最短ルート

1. `tools/run.sh`（Windowsは`tools\run.bat`、macOSのダブルクリックは`tools/run.command`）で起動し、「開く」でParquetファイルを選ぶ。
2. 「変数」パネルから列をプロットへドラッグする（プロットを増やすときは「＋ プロット追加」）。
3. Ctrl＋スクロールでズーム、スクロールでパン。画面上部のナビゲータの枠をドラッグしても動かせる。
4. 画面下部の演算式入力欄に`speed * 3.6`のような式を入れて「追加」すると仮想変数になり、
   列と同じようにプロットできる。
5. ツールバーの「ビュー ▾」→「保存...」で今の配置を「ビュー」として保存すると、同じファイルや別のファイルで呼び出せる。

**不具合の報告**: 普通に起動して再現し（操作ログは毎回書かれる）、`tools/show_ops_log.sh --session`の
出力（または設定画面の「ログのフォルダを開く」にある当日の`*_ops_*.log`）を、期待していた動きと一緒に添える。

**大きなファイル**（Apple M4 Pro／24GB、1億行・4.6GBで測定）: 最初の描画は約1.1秒、ズーム・パンは
0.1〜0.5秒。メモリは普段約1.4GB、ファイル全体の統計の計算中に最大約4.7GB。詳細はデータの仕様36.4・36.7。

### 必要環境

- Windows または macOS
- [uv](https://docs.astral.sh/uv/)（Pythonと依存パッケージはuvが自動で用意する。手動インストール不要）

### 起動方法

```sh
tools/run.sh          # macOS/Linux
tools\run.bat          # Windows
```

初回起動時に `uv` が依存パッケージ（PySide6, pyqtgraph, polars, pyarrow, numpy, matplotlib）を
自動でインストールする。

画面の表示言語は既定で英語。ツールバーの「⚙ ▾」→「Settings...」（設定）で日本語に切り替えられる（再起動後に反映）。
以下のボタン名は日本語表示時のもの。

macOSでは `.sh` はダブルクリックしても既定でテキストエディタが開くだけで実行されない
（Finderの標準動作）。ダブルクリックで起動したい場合は `tools/run.command` を使う
（`run.sh` を呼ぶだけの薄いラッパー。Finderは `.command` を実行可能として扱う）。
デバッグモードをダブルクリックで起動したい場合は `tools/run_debug.command`（`run_debug.sh` を呼ぶ）を使う。

#### デバッグモード

詳細（DEBUG）ログと、操作ログを読みやすくしたコンソール出力つきで起動する。
操作ログ本体（操作・その結果の状態・裏の処理・メッセージ・例外）はデバッグモードでなくても
**毎回** `log/parquet_analyzer/parquet_analyzer_ops_<日付>.log` に書かれる。不具合の報告は、
普通に起動して再現し、当日の `*_ops_*.log` を渡す（設定画面の「ログのフォルダを開く」から）。
`tools/show_ops_log.sh <ファイル> [--last N] [--session]` で読みやすい時系列として表示できる。
設定の「ログの保存期間（日）」（既定7日）より古いログは起動時に削除される。

```sh
tools/run_debug.sh     # macOS/Linux
tools\run_debug.bat     # Windows
```

（`--debug` フラグ、または環境変数 `PARQUET_ANALYZER_DEBUG=1` でも同様に有効化できる。
起動中はウィンドウタイトル末尾に `[DEBUG]` が付く。）

### 基本的な使い方

1. ツールバーの「開く」からParquetファイルを選択する。ダイアログは最後に開いたフォルダを
   初期表示し、直近5件のフォルダをサイドバーから選べる。現在開いているファイル名は
   ウィンドウタイトルと画面左下のステータスバーに表示される（ホバーするとフルパスを確認できる）。
2. 「変数」パネル（既定では画面左側）に読み込んだ列が一覧表示される。プロットへドラッグ&ドロップすると
   その変数が重ね合わせ表示される（凡例が自動更新される）。
3. ツールバーの「＋ プロット追加」で新しいプロットを追加できる（下の「プロットの操作」参照）。
4. 画面上部のナビゲータで表示範囲（時間軸）を素早く移動できる。ナビゲータには
   **最上段のプロットに最初に重ねられた変数**の概要波形が表示される（何も重ねられていない
   場合は暫定的に別の列が表示される）。

#### プロットの操作

- **高さ**: プロットとプロットの境界をドラッグして変えられる（ビューに比率で保存される）。
- **並べ替え**: 各プロットの左端のつまみ（点々）を上下にドラッグする。右クリックの「上に移動」
  「下に移動」でもよい（高さは保たれる）。
- **系列の選択・削除**: 凡例の名前をクリックで選択（線が太くなる）→ **Delete**（macOSの
  「delete」キーでも可）で、その系列だけをプロットから削除する。もう一度クリック・空き領域の
  クリック・Escで選択解除。
- **系列の表示/非表示**: 凡例の線の見本（名前の左の短い線）をクリック。非表示の系列は統計・
  カーソルの値・画像保存から除かれる。
- **右クリックメニュー**: 「系列を削除 ▸」「系列の表示/非表示 ▸」「系列を新規プロットに分離 ▸」、
  「新規プロットとして分離」（先頭以外の全系列を移す）、「プロットを削除」（プロットごと削除）。
- **X軸の独立**: プロット左端の鎖アイコン、または右クリック「X軸を連動」で、そのプロットだけ
  単独でズーム・パンできる（最後の1つは外せない）。連動を外したプロットは右クリック「この範囲で
  統計」で、その範囲の統計を取れる。ナビゲータは連動しているプロットの範囲を表示する。
- 時刻ラベルは、連動しているプロットの一番下と、連動を外したプロットにだけ表示される。
  縦に並んだプロットのY軸の幅は自動でそろう。

#### 時間軸（X軸）に使う列の変更

既定では読み込んだファイルの**先頭列**が時間軸（X軸）として使われる。ツールバーの
「時間軸:」コンボボックスから任意の列名に変更できる。切り替えると、それまで時間軸だった列は
通常の変数として変数パネルに戻り、プロット済みの波形は新しい時間軸に合わせて表示範囲が
再フィットされる。時間軸の列が昇順になっていない・欠損を含む場合は、表示範囲・統計・カーソルの
値が正しくない可能性があるので、ステータスバーに警告が表示される（警告を出したうえで表示は続け、
固まらない）。値が増えたり減ったりする2つの変数の関係を見たいときは、時間軸を変えるのではなく
XYプロットを使う。

#### ズーム・パン操作

| 操作 | 動作（現在の既定値） |
|---|---|
| マウスホイールスクロール | 時間軸方向にパン（移動。ズームはしない） |
| Ctrl + スクロール | 時間軸（X軸）のみズーム |
| Shift + スクロール | Amp（Y軸）のみズーム |
| H キー / 「全体表示」ボタン | 表示範囲を全データ表示にリセット |
| R キー / 再描画ボタン | 現在の表示範囲でダウンサンプリングを再計算 |

無印/Ctrl+/Shift+スクロールそれぞれの動作は、「設定」ダイアログの「スクロール操作」欄で
変更できる。既に他の修飾キーに割り当て済みの動作を選ぶと、その2つが自動的に入れ替わる
ため、常にどの動作も何らかの修飾キーで実行可能な状態が保たれる。上表はあくまで既定値。

時刻軸（X軸）の目盛は、ズームレベルに応じて自動的に粒度が変わる。広い範囲を表示しているときは
日付/月単位、数秒〜数分まで拡大すると時:分:秒（ミリ秒）単位まで自動的に詳細化される
（周波数解析プロットのX軸は周波数[Hz]なのでこの対象外）。

大量データはピクセル数に応じて自動的に間引いて（ダウンサンプリング）表示される
（LTTBアルゴリズム、波形のピーク・谷を保ったまま点数を削減）。この再計算は別スレッドで
非同期に実行されるため、実行中はステータスバーに進捗バーが表示されるがUI操作はブロックされない。
ツールバーの「⚙ ▾」の「簡易表示を許可」をオフにすると、常に間引かない実データで描画する（非常に大きな
ファイルでは遅くなる）。

**大きなファイル**（既定では1列が512MBを超えるもの。設定で変更可）は、列を丸ごと読まず、表示範囲の
分だけを都度読む。最初は粗い表示（ステータスバーに「簡易表示中」）を出し、裏で全体の概形を作り終えると
細かい表示に置き換わる。時間軸の列も全件をメモリに持たない（昇順のときだけ。昇順でなければ全件を
読んで警告を出す）。大きなファイルの仮想変数は表示範囲の分だけ計算するので、最初の表示まで少し
（1億行で約2秒）待つことがある。目安として、1億行・約4.6GBのファイルで、ふだんのメモリ使用量は
約1.4GB（全体の統計を取ると一時的に約4.5GB）。

#### カーソル

ツールバーの「カーソル」をオンにしてプロットをクリックすると、縦線のカーソルを置ける。
その位置の時刻と各系列の値が表示される。カーソルはドラッグで動かせ、全時間軸プロットで共有され、
クリックで削除できる。「カーソル」をオフにすると、削除せずに非表示にする。

#### マウスを乗せた位置の値（ホバー表示）

プロットにマウスを乗せると、点線の縦線と、右上にその時刻と表示中の各系列の値が出る。X軸が連動して
いる他のプロットにも同じ時刻の線と値が出る。大きいファイルでは、マウスを動かしている間は間引き後の
値（「≈」付き）を出し、止まると実際のサンプルの値に置き換わる。ツールバーの「値の表示」でON/OFF
（設定に保存）。非表示の系列は出さず、画像保存にも含まれない。

#### 変数間演算・解析

- 画面下部の演算式入力欄に、変数名を使った式（例: `abs(A) - sqrt(B)`, `delta(rpm)`,
  `rolling_mean(rpm, 20)`, `clip(rpm, 0, 3000)`）を入力し「追加」すると、新しい変数
  （仮想変数）として変数パネルに追加される（斜体で表示。ホバーすると式を確認できる）。
  - 演算子: `+ - * / ** % //`
  - 関数（いずれも要素ごとの演算）: `abs`, `sqrt`, `delta`（1階差分）,
    `rolling_mean(a, window)`（中心移動平均。窓はその点の前後にまたがる。偶数nのときは
    前にn/2点・後にn/2-1点。窓の中に欠損があると結果も欠損）,
    `rolling_nanmean(a, window)`（同じ窓で欠損を除いた中心移動平均）,
    `trailing_mean(a, window)`（過去だけの移動平均。各点でその点と過去最大window-1点の平均。先頭ではある分だけで平均。欠損がn-1点先まで伝播）,
    `trailing_nanmean(a, window)`（同じ過去だけの窓で欠損を除いた移動平均）,
    `sin`, `cos`, `tan`, `exp`, `log`, `log10`,
    `sign`, `floor`, `ceil`, `round`, `min(a, b)`, `max(a, b)`, `clip(a, lo, hi)`
    （`min`/`max`はPython組み込みと違い要素ごとの比較で、スカラー1つを返す集約関数ではない）
  - 条件分岐: `where(c, a, b)` または `a if c else b`（条件が真ならa、偽ならb）、
    比較 `< <= > >= == !=`（`0 < a < 5`のような連続した比較も可）、`and` / `or` / `not`。
    結果は1（真）か0（偽）なので `(a > 0) * a` のように計算にも使える。`isnan(a)` は欠損なら1。
    欠損(NaN)を含む比較・条件の結果は欠損になる（`and`/`or`は3値論理: 真 or 欠損=1、偽 and 欠損=0）。欠損の置き換えは `where(isnan(a), 0, a)`。
- 演算変数名が既存の列名と同じ場合はエラーになる（列データの意図しない上書きを防ぐため）。
- 変数パネルで仮想変数を右クリックすると「式を編集...」「削除」を選べる。編集は演算式入力欄で
  行い（「更新」で適用、「キャンセル」またはEscで中止）、それを使う他の仮想変数や表示中の曲線も
  再計算される。他の仮想変数から使われている仮想変数は削除できず、自分自身を（循環して）参照する
  式はエラーになる。
- 変数パネルの変数をダブルクリックする、または演算式入力欄へドラッグすると、カーソル位置に
  変数名が挿入される（入力補助。編集中も使える）。
- 演算式入力欄の「関数一覧...」で、組み込み関数・ユーザー関数・マップの一覧（書き方・説明・使用例）を
  別ウィンドウで開ける。選んだ使用例を式の入力欄に挿入できる。
- 周波数解析（FFT）機能は内部的には存在するが、デバッグが完了するまで開発用の機能フラグで
  「グラフ ▾」メニューから隠している（現時点ではGUIから実行できない）。時刻の間隔がばらばら・途切れのある範囲では
  FFTを実行せず、理由をステータスバーに出す（等間隔の範囲に絞ってから実行する）。
- 「統計」ボタンを押すと、現在表示中の時間範囲の生データ全件（画面に描画されている
  間引き後の点ではない）について件数・欠損値・最小・最大・平均・標準偏差・中央値を
  変数ごとに1行ずつ計算し、別ウィンドウに表として表示する。対象は変数パネルで選択中の
  変数。パネルで何も選択していない場合は、現在プロットに表示中（重ねて表示中）の変数が
  自動的に対象になる——既にプロット済みの変数の統計を見るのに、パネルで改めて選び直す
  必要はない。パネルで明示的に選択している場合は、プロット中の変数より常にそちらが優先される。

#### ユーザー関数・マップ

- **ユーザー関数**（ツールバー「ユーザー関数」）: 引数・定数（デフォルト値付き）を持つ式を定義し、どの
  ファイルでも使える。例: `Power_flow_by_press(flow_lpm, press_kpa)` = `flow_lpm * press_kpa / k`
  （`k = 60000`）。式で`power1 = Power_flow_by_press(signal1, signal2)`、定数の上書きは
  `…, k=1000)`。「信号に適用」タブでは、変数パネルから引数の欄へドラッグして作れる。
  `cfg/parquet_analyzer/user_functions.json`に保存される。
- **マップ**（ツールバー「マップ」）: CSVのカーブ（2列）・マップ（1行目がX軸、1列目がY軸）を
  取り込み、式で`名前(x)`・`名前(x, y)`として使う。補間（線形・最近傍・前の値・スプライン）と
  範囲外の扱い（端の値・0・線形で延長・欠損）を選べる。取り込んだCSVはコピーして保存される。
  「編集...」で名前と軸の名前（プレビューの軸に出る）を変えられる（使用中のマップの名前は変えられない）。
  表のセルの値も直接書き換えて保存できる（ダブルクリックで編集）。編集中はプレビューが「変更前」と
  「変更後」の2つになり、違いを見比べられる。マップのプレビューは2Dと3D曲面を切り替えられる。
  確認用のサンプルは`uv run python tests/parquet_analyzer/generate_sample_maps.py`で
  `data/raw/sample_curve.csv`（x³）・`sample_map.csv`（x²+y²。`sample_map(sine, cosine)`がほぼ1）を作れる。
- 関数・マップを変更すると、それを使う仮想変数と表示中の曲線が再計算される。使用中の関数・マップは
  削除できない。

#### XY / 3Dプロット

ツールバーの「グラフ ▾」→「XY / 3Dプロット」で別ウィンドウを開き、X（1つ）とY（複数可）を選ぶと、
表示中の時間範囲の散布図を描く。時間軸の列も軸に選べる。色は単色・時間・Z変数、表示は散布図・
密度（点が多いと自動）。「再計算」でメイン画面の今の範囲を読み直し、「メインの範囲に追従」で
自動追従。カーソルの時刻の点を強調表示し、「画像を保存」で記載付きの画像を保存できる。

表示を「3D」に切り替えると、X・Y・Zの3次元の散布図になる（選んだ変数は引き継がれる）。マウスで
回転・拡大でき、上のボタン（ホーム・XY・YZ・ZX）で視点を切り替えられる。OpenGLが使えない環境では
3Dは選べない（理由をツールチップに出す）。

#### ビューの保存・読込

ツールバーの「ビュー ▾」→「保存...」で、現在のプロット配置・重ね合わせ変数・演算式を「ビュー」としてJSON保存できる
（`data/parquet_analyzer/`）。演算変数は計算結果ではなく定義式として保存されるため、
元データが更新されても読み込み時に再計算される。ビュー読込時、**何らかのファイルが既に
開かれている場合**（ビュー保存時のファイルとは別のファイルでも、まさにそのファイル自身でも）
は、そのファイルを開き直さず、現在開いているファイルに対してレイアウトだけを適用する
（開いているファイルが無い場合のみ、ビューに記録された元ファイルを開く）。保存済みレイアウトが
参照する変数のうち現在開いているファイルに存在しないものはスキップされる（どの変数を復元
できなかったかを警告として表示）が、一致する変数は問題なく適用される——同じ列名を持つ
複数ファイルに同一レイアウトを使い回す用途で、列名が完全一致していなくても機能する。

ビューには時間軸に使った列も保存され、読み込むとその列に戻る。データとビューの入ったフォルダごと
別の場所へ移しても開けるよう、元ファイルの場所は相対パスでも保存される。

保存の画面では保存済みのビューの一覧から選んで上書きでき（確認が出る）、新しい名前も打てる。
ビューを読み込んだ後は、その名前が最初に入っている。

「ビュー ▾」→「管理...」でビューの管理画面を開く。保存済みビューを選ぶと、読み込む前に中身を確認できる——
元ファイル、プロットごとのチャンネルとその色、仮想変数とその式、保存時の時間範囲、保存日時。
ファイルを開いている場合、そのファイルに無い（読み込むとスキップされる）チャンネルは赤字で
示される。ここからビューを読み込む（ボタンまたはダブルクリック）ことも、削除する（確認あり）
こともできる。形の壊れたビューのファイル（必要な項目が無い・型が違う）は、どの項目が悪いか
（例「plots: リストが必要です（文字列）」）を示して読み込まず、削除だけできる。

#### プロットの画像保存

「画像を保存」で、表示中のプロットをPNG・JPEG・TIFF・SVG・PDFで保存できる。画面キャプチャでは
なく描き直すため、時間軸が揃い、各プロットに凡例が付く。右下には元ファイル（とビュー名）、
表示中の時間範囲、出力日時、アプリのバージョンが記載される。

#### 設定メニュー

ツールバーの「⚙ ▾」→「設定...」から以下を変更できる（`cfg/parquet_analyzer/settings.json` に保存され、
次回起動時も引き継がれる）。

- 自動再描画のON/OFF（OFFでもRボタン/ショートカットでの手動再描画は常に可能）
- ダウンサンプリング密度（1pxあたりの表示点数）
- 列を開いた時点で全体を読み込まず表示範囲だけを都度読み込むようにする容量しきい値
  （変更後に開いたファイルから反映）
- 無印/Ctrl+/Shift+スクロールそれぞれの動作（X軸ズーム/Y軸ズーム/パン）
- ツールバーの各操作（全体表示、再描画、開く、プロット追加、統計、XY / 3Dプロット、ビュー保存、
  ビュー管理、画像を保存、簡易表示の切替、カーソル、値の表示、ユーザー関数、マップ）と、選択した系列の削除
  （既定はDelete）のショートカットキー
  （周波数解析はメニューから隠している間、ここにも表示されない。上記参照）
- 表示言語（English / 日本語。再起動後に反映）
- ログの保存期間（日、既定7日）と「ログのフォルダを開く」

#### レイアウトの永続化

ウィンドウサイズ・変数パネルの配置などの画面レイアウトは終了時に自動保存され、次回起動時に
復元される。変数パネルの既定位置は画面左側。ツールバーの「⚙ ▾」の「レイアウトをリセット」で、保存された
レイアウトを破棄していつでも既定配置に戻せる。

### MDF/MATLABファイルの変換

このアプリはParquetしか読み込めない。MDF（`.mf4`/`.mdf`/`.dat`）やMATLAB（`.mat`、v5/v7・
v7.3どちらも対応）の計測ファイルは、あらかじめ以下でParquetに変換してから開く。

```sh
tools/convert_to_parquet.sh <入力ファイル> [出力先.parquet] [--time-var 列名] [--raster 秒数]
```

- 初回のみ `uv sync --group convert` で変換専用の依存関係（asammdf, scipy, h5py）を
  インストールする（通常のアプリ起動では使わないため別グループに分離している）。
- MDFはチャンネルごとにサンプリングレートが異なりうるため、`--raster`で共通の時間刻み
  （秒）を指定できる（省略時は元データのタイムスタンプをそのまま使う）。
- MATLABファイルには「どの変数が時間軸か」という情報が無いため、`time`/`t`/`timestamp`/
  `timestamps`という名前の変数を自動検出する。異なる名前の場合は`--time-var`で明示する。

### データの保存場所

`src/parquet_analyzer/config.py` が一元管理する。実行環境ごとの状態であり、いずれもgitignore対象。

| 用途 | パス |
|---|---|
| アプリ設定 | `cfg/parquet_analyzer/settings.json` |
| 保存したビュー | `data/parquet_analyzer/*.json` |
| ログ（通常） | `log/parquet_analyzer/parquet_analyzer_<日付>.log` |
| 操作ログ（毎回記録） | `log/parquet_analyzer/parquet_analyzer_ops_<日付>.log` |
| ユーザー関数 | `cfg/parquet_analyzer/user_functions.json` |
| マップ（取り込んだCSVのコピー） | `data/parquet_analyzer/maps/` |

### 開発者向け

```sh
uv sync --group dev   # pytestなど開発用依存関係をインストール
uv run pytest         # テスト実行
```

実ディスプレイなしでの動作確認（ヘッドレス）:

```sh
QT_QPA_PLATFORM=offscreen bash tools/run.sh
```

機能を試すための小さな合成データ（関係が既知の列を持つ。例: `sine**2 + cosine**2` はどこでも1。
`data/raw/feature_test.parquet` に出力）:

```sh
uv run python tests/parquet_analyzer/generate_feature_test_parquet.py
```

### ライセンス

**[MIT License](LICENSE)** の下で公開しています。

このアプリケーションが使用する第三者ソフトウェアとライブラリについては [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) をご覧ください。

**作者**: [ultramonaka](https://github.com/ultramonaka)
