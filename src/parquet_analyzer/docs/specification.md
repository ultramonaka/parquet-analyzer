# Parquet Analyzer Specification / 仕様書

- Version / 版数: v0.3 (updated 2026-09-18 to reflect the current implementation; see
  `detailed_specification.md` for the history)
- Audience / 対象読者: implementers / 実装担当者
- Status / ステータス: core technical choices and controls are finalized (see
  "11. Open Items" / `## 11. 未確定事項`); implementation-level detail is in
  `detailed_specification.md`.

This document exists in two parallel copies: English first, then 日本語 below. Both
describe the same current state of the app; when they drift, treat it as a documentation
bug — keep both docs and the code in sync.

本書は英語版・日本語版を1ファイルに並べたもの。両方とも同じ「現在の実装状態」を記述する。
内容が食い違ったらそれ自体がドキュメントのバグである。仕様書とコードは常に同期させる。

---

## English

### 1. Purpose

A desktop tool for loading Parquet files and visualizing/analyzing the time-series
data inside them. Lets the user inspect waveforms, view an automatically downsampled
display, apply inter-variable expressions, and run frequency and delta analysis, all
from a GUI.

### 2. Runtime Environment

| Item | Value |
|---|---|
| OS | Windows, macOS |
| Language | Python |
| Package management | uv |
| GUI framework | PySide6 |
| Plotting library | pyqtgraph |
| Distribution | a Windows `.exe` (built with PyInstaller or similar; see [11.5](#115-packaging-as-an-exe-resolved)) |

### 3. Directory Structure

```
parquet-analyzer/
├── cfg/                        # app settings files
│   └── parquet_analyzer/
├── data/
│   ├── parquet_analyzer/        # saved views (screen layouts)
│   └── raw/                    # source Parquet data to analyze (incl. sample/test data)
├── log/                        # logs / debug output
│   └── parquet_analyzer/
├── src/
│   ├── common/                  # code shared with other projects (currently empty)
│   └── parquet_analyzer/         # this app's own source
│       ├── config.py            # shared path-definitions module (importable from other projects too)
│       ├── __main__.py          # GUI entry point
│       ├── convert_cli.py       # CLI entry point for the MDF/MATLAB -> Parquet converter
│       ├── docs/                 # specification.md (this document), detailed_specification.md
│       ├── ui/                  # screens (main window, navigator, toolbar, etc.)
│       ├── core/                 # data loading, downsampling, expressions, analysis, format conversion
│       └── io/                   # view / settings persistence
├── tests/
│   └── parquet_analyzer/         # pytest suite
└── tools/
    ├── run.sh / run.bat / run.command   # launch scripts (.command is for double-click launch on macOS)
    ├── run_debug.sh / run_debug.bat      # debug-mode launch scripts
    ├── convert_to_parquet.sh              # MDF/MATLAB -> Parquet conversion tool
    └── publish_to_public.sh               # syncs this dev repo to a separate public repo
```

#### 3.1 Sharing Paths with Other Projects

- Paths (the directories under `cfg/`, `data/`, `log/`, etc.) are defined as constants
  in `src/parquet_analyzer/config.py`; the app itself, code under `src/common/`, and
  other projects all import this one module to reference them (finalized in
  [11.2](#112-path-sharing-approach-with-other-projects-resolved)).
- `src/common/` holds code that other projects might also want to reuse (Parquet
  reading helpers, shared utilities, etc.).
- `tools/run.sh` / `tools/run.bat` always add the `src` directory to the `PYTHONPATH`
  environment variable at launch. This makes the `parquet_analyzer` package (including
  its `config` submodule) and `common` importable without qualification.
- Other projects can reuse `config.py` and code under `common/` the same way, by
  adding this repository's `src` to their own `PYTHONPATH` from their own launch
  script.
- `config.py` depends only on the standard library. `REPO_ROOT` resolves by default
  from `config.py`'s own location, but can be overridden with the
  `PARQUET_ANALYZER_ROOT` environment variable (for cases like a frozen executable,
  where `__file__`-based resolution doesn't work). Directory creation is never an
  import-time side effect; call `ensure_dirs()` explicitly instead.

### 4. Launching

- Run `tools/run.sh` (macOS/Linux) or `tools/run.bat` (Windows).
- What the script does:
  1. Add `<repo>/src` to `PYTHONPATH`.
  2. Launch the app with `uv run python -m parquet_analyzer`.
- The distributed `.exe` is a build of this same entry point.

### 5. Screen Layout and Main Features

#### 5.1 Main Screen
- Multi-plot support: several time-series graphs can be tiled (e.g. stacked
  vertically).
- Overlay display: multiple variables can be overlaid within one plot pane.
- Each plot pane supports independent zoom/pan.
- Each plot pane shows a legend (list of variable names), kept in sync as series are
  added/removed.
- Right-clicking a plot pane offers "move up"/"move down" to reorder the vertical
  stack of panes (added 2026-09-17). Choosing "move up" on the topmost pane, or "move
  down" on the bottommost, does nothing (out-of-range moves are ignored). After a
  reorder, the X-axis link shared between time-domain plots
  ([5.3](#53-controls-v2-finalized-from-real-world-feedback)) is always rebuilt around
  **whichever pane is currently on top** (see `detailed_specification.md` §5 for
  detail).

#### 5.2 Navigator
- A single navigator shared by every plot (~~or one navigator per plot~~) — an
  overview plus a rectangle showing the current visible range — is always present on
  screen.
- Dragging/clicking on the navigator moves the visible range.
- Only the time axis can be moved this way; the vertical axis stays fixed. Coarse
  rendering is fine here.
- The variable shown as the overview waveform is **the first variable overlaid on the
  topmost plot pane** (changed 2026-09-17 — it used to unconditionally show whichever
  variable happened to be first in file column order). Falls back to that old
  behavior only when nothing has been overlaid anywhere yet. Updates to follow
  whenever the topmost pane's content changes (a variable is added, panes are
  reordered, etc.).

#### 5.3 Controls (v2, finalized from real-world feedback)
| Action | Effect |
|---|---|
| Mouse wheel scroll | Zoom the X (time) axis only |
| Ctrl + scroll | Zoom the Y (amplitude) axis only |
| Shift + scroll | Pan along the X (time) axis (move only, no zoom) |
| H key / Home button | Reset the view to show all data |
| R key / Redraw button | Redraw the plot for the current view (including a downsampling recompute) |

> **Change from v1**: plain scroll originally zoomed both axes, with Ctrl limiting it
> to the X axis and Shift to the Y axis; "zoom both axes" could be disabled via a
> setting (the old `both_axis_scroll_zoom`). Real usage feedback said this felt bad,
> so it was changed to the current scheme, which separates the three actions
> completely: plain = time zoom, Ctrl = amplitude zoom, Shift = time pan. Since plain
> scroll is now always dedicated to time-zoom, the both-axis-zoom setting itself
> became unnecessary and was removed.
>
> Testing on real hardware (macOS) then turned up a bug where Ctrl/Shift did nothing
> and every scroll just zoomed time. Two causes: Qt reports the physical Ctrl key on
> macOS as `MetaModifier` (Cmd/Ctrl are swapped for cross-platform shortcut
> conventions), and some trackpads report a Shift+scroll gesture as a horizontal wheel
> value. Both are now handled (see
> [detailed_specification.md §4](./detailed_specification.md#4-controls-finalized-113-v2)).

- The time (X) axis tick labels change dynamically with zoom level: date/month units
  when zoomed out, down to hour:minute:second.millisecond when zoomed into a few
  seconds or minutes.

#### 5.4 Automatic Downsampling
- Purpose: rendering millions of raw points directly makes loading and drawing slow,
  so points are decimated down to what's actually needed for display.
- Method: the **LTTB (Largest-Triangle-Three-Buckets)** algorithm. Unlike naive
  decimation (uniform-interval sampling), it preserves a waveform's visually
  significant features (peaks/troughs) as much as possible while reducing the point
  count.
- Behavior:
  - Downsampling only kicks in once the point count within the visible range exceeds
    the display window's budget (screen pixels spanned by the plot area — e.g. a
    1200px-wide plot allows up to ~2400 points, 2 per pixel).
  - Recomputed for the new range every time the view changes via zoom/pan (also
    triggerable explicitly via the Redraw button). This automatic recompute can be
    turned off via a setting (`auto_redraw`, see
    [5.10](#510-settings-menu)) — manual recompute via the Redraw button/shortcut
    always still works even when it's off.
  - The source data stays resident in memory (or cache); downsampling is a temporary,
    display-only decimation that never destroys the underlying data.
  - The recompute runs asynchronously on a separate thread so it never blocks the UI
    thread (see
    [detailed_specification.md §10](./detailed_specification.md#10-asynchronous-downsample-recompute-finalized)) —
    otherwise the UI would freeze on every zoom operation over large data.
  - A status-bar progress indicator (indeterminate) is shown while a recompute is in
    flight (see [5.10](#510-settings-menu)).

#### 5.5 Inter-Variable Expressions
- Arithmetic (+, -, ×, ÷, exponent, modulo, floor division), absolute value (abs) and
  square root (sqrt), plus trigonometric functions (sin/cos/tan), exponential/log
  (exp/log/log10), rounding (sign/floor/ceil/round), range operations
  (min/max/clip, all elementwise), and a moving average (rolling_mean) can all be
  applied to variables (columns). See
  [detailed_specification.md §6](./detailed_specification.md#6-expression-input-114)
  for the full list and detail.
- A result is added to the plottable variable list as a new derived variable.
- Defining a derived variable whose name collides with an existing column is an
  error, preventing accidental overwriting of real data.
- The expression input method/UI is finalized in
  [11.4](#114-expression-input-ui-resolved).
- A derived variable is saved into a view as its **defining formula** (the expression
  string plus its input variable names), not as computed raw data, and recomputed
  from the source columns whenever the view is loaded. Reasoning: this tracks changes
  to the underlying source data via recomputation, and keeps the saved file small.

#### 5.6 Frequency Analysis
- Runs an FFT on the selected variable and displays the spectrum as a separate plot.
- The FFT is scoped to **the selected variable's currently visible time range**
  (the X range shared across time-domain plots), not the whole file (changed
  2026-09-17; see `detailed_specification.md` §13.5.1 for the reasoning). Two
  reasons: (1) it avoids materializing the whole file in memory, and (2) this app's
  intended data (intermittent burst sampling, e.g. a short high-rate capture every
  several hours rather than continuous sampling) contains stretches of non-uniform
  sample spacing, which makes a whole-file FFT's own sampling
  interval estimate (the median of adjacent-sample gaps) meaningless in the first
  place regardless of memory. If the visible range is too wide (over 8,000,000 rows
  by default), a message asks the user to zoom in instead of running the
  computation. Runs asynchronously so the UI thread is never blocked.

#### 5.7 Delta (Δ) Analysis
- Computes the first-order difference between adjacent samples of the selected
  variable and displays it as a time series.

#### 5.8 View Save Feature
- The current screen layout (plot arrangement, displayed variables, expressions, axis
  ranges, etc.) can be saved as a "view."
- Saved under: `data/parquet_analyzer/`.
- Format: **JSON** (chosen for readability/extensibility; a binary format was judged
  unnecessary).
- A saved view includes a relative or absolute path reference to the source Parquet
  file it was loaded from, and re-accesses that same data when loaded.
- However, if a **different file is already open** when a view is loaded, that file
  is not reopened — only the plot layout (displayed variables, expressions, axis
  ranges) is applied to the file that's already open (changed 2026-09-17; see
  `detailed_specification.md` §8 for the reasoning). Intended use case: reusing one
  layout across several files that share the same column names.

#### 5.9 Data Folder Selection
- When opening a Parquet file, the user can choose which folder to browse (a folder
  picker dialog).
- The default starting folder is **the last one browsed**.
- The 5 most recently browsed folders are kept as quick-pick candidates in the
  dialog.
- See [detailed_specification.md §7](./detailed_specification.md#7-data-source-selection-and-folder-history)
  for detail.

#### 5.10 Settings Menu
- A settings dialog, opened from the toolbar, lets the user change all of the
  following in one place:
  - Auto-redraw on/off (`auto_redraw`)
  - Downsampling density (`downsample_pixel_ratio`)
  - Keyboard shortcuts for each action (home, redraw, open, add plot, frequency
    analysis, statistics, save/load view)
  - Which scroll-wheel modifier performs which action (`scroll_bindings`)
  - **UI language** (`language`; `English`/`日本語`, default `English` for a fresh
    install — added 2026-09-19, user-requested. Takes effect after restarting the
    app.)
- Settings are saved to `cfg/parquet_analyzer/settings.json` and restored on next
  launch.
- See [detailed_specification.md §9.1](./detailed_specification.md#91-settings-menu)
  for detail.

#### 5.11 Layout Persistence
- Window size, the variable panel (dock)'s position, toolbar position, and similar
  screen layout are saved to `cfg/parquet_analyzer/settings.json` and restored on next
  launch.
- The variable panel's default position (first launch, or nothing saved yet) is the
  **left side of the window**.
- "Reset layout" on the toolbar discards the saved layout and restores the default
  arrangement.
- See [detailed_specification.md §9.3](./detailed_specification.md#93-layout-persistence)
  for detail.

#### 5.12 Statistics Display (added 2026-09-17)
- Selecting one or more variables in the variable panel and running this computes
  count, missing-value count, min, max, mean, standard deviation, and median over
  **the currently visible time range** (the X range shared across time-domain plots
  — the same range-determination method as FFT's, [5.6](#56-frequency-analysis)) and
  shows them as a table in a separate (non-modal) window.
- Computed over **every raw sample in that range, not the downsampled points actually
  drawn on screen** (sliced from `_raw_variables` separately from the LTTB display
  data of [5.4](#54-automatic-downsampling)). Unlike FFT, no row-count cap is applied
  — mean/standard deviation etc. stay meaningful even with non-uniform sampling
  intervals, and cost only a single numpy pass.
- With multiple variables selected, all of them are computed and shown together in
  one table in one run (in selection order).
- Runs asynchronously per variable so the UI thread is never blocked (see
  `detailed_specification.md` §14 for detail).

#### 5.13 Cursors (added 2026-09-19, user-requested)
- A toolbar toggle ("Cursor") turns cursor placement on/off; it's off by default and
  doesn't affect normal pan/zoom either way.
- While on, clicking anywhere on a time-domain plot places a cursor there: a
  draggable vertical line plus a text overlay giving that X (time) value and every
  series currently plotted **on that same plot** at the nearest sample. If more than
  one plot is open, the same cursor (same X) is placed on **every other time-domain
  plot too**, each showing its own series' values at that X — a frequency-domain
  (FFT) plot is a separate X domain and is never included.
- Clicking directly on an existing cursor removes it (from every plot showing it);
  dragging it moves it (also synced to every plot showing it). Turning the toggle off
  hides existing cursors (not draggable/removable/visible while off) rather than
  deleting them — turning it back on restores them at the same position, handing
  mouse interaction back to normal pan/zoom in between.
- Any number of cursors can be placed at once, independently of each other.
- Opening a new file, or switching the time-axis column, clears every cursor (its X
  position referred to a position on the previous time axis, which is meaningless on
  the new one).
- See `detailed_specification.md` §16 for detail.

### 6. Data Processing

- Parquet reading uses `polars` (lazy evaluation via `scan_parquet`; see
  [11.6](#116-parquet-reading-library-resolved)).
- Lazy loading and reading only the necessary range are preferred wherever possible
  for large files.

### 7. Settings (`cfg/`)

- App settings — downsampling threshold/density, auto-redraw on/off, keyboard
  shortcuts, recently-browsed-folder and recently-opened-file history, etc. — are
  saved as JSON under `cfg/parquet_analyzer`. See
  [detailed_specification.md §9](./detailed_specification.md#9-settings-file-cfgparquet_analyzersettingsjson)
  for the schema.

### 8. Logging (`log/parquet_analyzer`)

- Runtime logs and error stack traces are written under `log/parquet_analyzer`.
- Launching with `--debug` enables debug mode, which dumps user actions (opening
  files, zoom/pan, overlaying variables, saving/loading views, etc.) to a dedicated
  file (`parquet_analyzer_ops_*.log`). See
  [detailed_specification.md §11.1](./detailed_specification.md#111-debug-mode-launch-and-the-operation-log)
  for detail.

### 9. Non-Functional Requirements

- UI operations must be reflected within a few seconds even when handling large
  datasets (millions of rows), thanks to downsampling.
- Support both Windows and macOS.
- Distributed as a `.exe` requiring no installation (Windows).

### 10. Out of Scope

- Direct support for file formats other than Parquet (conversion from CSV etc. is out
  of scope for the *app itself*). MDF and MATLAB `.mat` files are common in this same
  domain, but are handled by a **separate conversion tool**
  (`tools/convert_to_parquet.sh`) rather than the app reading them directly — see
  [detailed_specification.md §15](./detailed_specification.md#15-mdfmatlab--parquet-conversion-tool-added-2026-09-18)
  for why (they aren't structured for Parquet's kind of column/range-selective
  reads, which several of this app's own efficiency designs depend on).
- Displaying real-time streaming data.

### 11. Open Items

Items the draft spec's wording alone couldn't uniquely resolve. A provisional answer
is used in the body text above, but each needed confirmation before implementation.

#### 11.1 Interpretation of "Pygraph" (resolved)
The draft's `Pygraph` is interpreted and finalized as referring to `pyqtgraph` (see
[detailed_specification.md §1](./detailed_specification.md#1-technology-choices-finalized)).

#### 11.2 Path-Sharing Approach with Other Projects (resolved)
Finalized as: centralize path definitions in `src/parquet_analyzer/config.py`, imported
directly by other projects via `PYTHONPATH` (see
[3.1](#31-sharing-paths-with-other-projects)). How `src/common/` (shared code) itself
gets distributed (a git submodule, split into its own repo, plain local reference,
etc.) remains separately undecided.

#### 11.3 Zoom Control Axis Assignment (resolved, v2)
Finalized as: plain scroll = X (time) axis zoom, Ctrl+scroll = Y (amplitude) axis
zoom, Shift+scroll = X-axis pan (no zoom) (see
[5.3](#53-controls-v2-finalized-from-real-world-feedback) /
[detailed_specification.md §4](./detailed_specification.md#4-controls-finalized-113-v2)).

#### 11.4 Expression Input UI (resolved)
Finalized as: a text input field (Python-like expressions) plus double-click-to-insert
for variable names, evaluated under a restricted AST whitelist (see
[detailed_specification.md §6](./detailed_specification.md#6-expression-input-114)).

#### 11.5 Packaging as an .exe (resolved)
Finalized as PyInstaller (`--onedir`) (see
[detailed_specification.md §1](./detailed_specification.md#1-technology-choices-finalized)).

#### 11.6 Parquet Reading Library (resolved)
Finalized as `polars` (lazy evaluation via `scan_parquet`); lightweight operations
like schema inspection also use `pyarrow` internally (see
[detailed_specification.md §1](./detailed_specification.md#1-technology-choices-finalized)).

#### 11.7 Multi-Plot/Overlay UI Operations (resolved)
Finalized as: drag-and-drop from the variable panel to overlay, a right-click menu to
separate/remove/reorder, and a toolbar "+" to add a plot pane (see
[detailed_specification.md §5](./detailed_specification.md#5-multi-plot--overlay-operations-117)).

---

## 日本語

- 版数: v0.3（2026-09-18、現在の実装に合わせて更新。経緯は
  `detailed_specification.md`参照）
- 対象読者: 実装担当者
- ステータス: 主要な技術選定・操作仕様は確定（`## 11. 未確定事項` 参照）。実装レベルの詳細は
  `detailed_specification.md` を参照。

---

### 1. 目的

Parquetファイルを読み込み、含まれる時系列データを可視化・解析するためのデスクトップツール。
波形（時系列）の確認、ダウンサンプリング表示、変数間演算、周波数解析、差分解析などを
GUI上で行えるようにする。

### 2. 動作環境

| 項目 | 内容 |
|---|---|
| OS | Windows, macOS |
| 言語 | Python |
| パッケージ管理 | uv |
| GUIフレームワーク | PySide6 |
| プロットライブラリ | pyqtgraph |
| 配布形態 | exe（PyInstaller等でWindows向けにビルド。詳細は [11.5](#115-exeの方式解決済み) 参照） |

### 3. ディレクトリ構成

```
parquet-analyzer/
├── cfg/                        # アプリ設定ファイル（既定パラメータ等）
│   └── parquet_analyzer/        # アプリ設定ファイルの格納先
├── data/
│   ├── parquet_analyzer/        # 保存されたビュー（画面レイアウト）の格納先
│   └── raw/                    # 解析対象の元Parquetデータ（サンプル/テストデータ含む）
├── log/                        # ログ・デバッグ出力
│   └── parquet_analyzer/        # ログの格納先
├── src/
│   ├── common/                  # 他プロジェクトとも共有する共通コード（現状は空）
│   └── parquet_analyzer/         # 本アプリのソース一式（アプリ固有コードはここに集約）
│       ├── config.py            # パス定義の共有モジュール（他プロジェクトからもimport可能）
│       ├── __main__.py          # GUIエントリポイント
│       ├── convert_cli.py       # MDF/MATLAB→Parquet変換のCLIエントリポイント
│       ├── docs/                 # specification.md（本書）・detailed_specification.md
│       ├── ui/                  # 画面（メインウィンドウ、ナビゲータ、ツールバー等）
│       ├── core/                 # データ読み込み、ダウンサンプリング、演算、解析ロジック、変換ロジック
│       └── io/                   # ビュー保存/読み込み、設定ファイルの読み書き
├── tests/
│   └── parquet_analyzer/         # pytestテスト一式
└── tools/
    ├── run.sh / run.bat / run.command  # 起動スクリプト（.commandはmacOSダブルクリック起動用）
    ├── run_debug.sh / run_debug.bat    # デバッグモード起動スクリプト
    ├── convert_to_parquet.sh            # MDF/MATLAB→Parquet変換ツール
    └── publish_to_public.sh             # 開発リポジトリから公開リポジトリへの同期スクリプト
```

#### 3.1 他プロジェクトとのパス共有

- パス（`cfg/`, `data/`, `log/` 配下の各ディレクトリなど）は `src/parquet_analyzer/config.py` に定数として
  定義し、アプリ本体・`src/common/` 配下のコード・他プロジェクトのいずれもこのモジュールを
  import して参照する（[11.2](#112-他プロジェクトとのパス共有方式解決済み) で確定）。
- `src/common/` には、Parquet読み込みや共通ユーティリティなど他プロジェクトでも使う可能性のある
  コードを置く。
- `tools/run.sh` / `tools/run.bat` が起動時に環境変数 `PYTHONPATH` に `src` ディレクトリを
  常に追加する。これにより `parquet_analyzer`（`config`サブモジュールを含む）・`common` の
  各モジュール/パッケージが修飾なしで import 可能になる。
- 他プロジェクト側も同様に、自身の起動スクリプトで本リポジトリの `src` を `PYTHONPATH` に
  追加することで `config.py` や `common` 配下のコードを再利用できる。
- `config.py` はstdlibのみに依存し、`REPO_ROOT` は既定で `config.py` の配置場所から解決するが、
  環境変数 `PARQUET_ANALYZER_ROOT` で上書き可能（exe化時など `__file__` 基準の解決が
  使えない場合に対応）。ディレクトリ作成は import時の副作用にせず `ensure_dirs()` を
  明示的に呼ぶ設計とする。

### 4. 起動方法

- `tools/run.sh`（macOS/Linux）、`tools/run.bat`（Windows）を実行する。
- スクリプトの処理内容:
  1. `PYTHONPATH` に `<repo>/src` を追加
  2. `uv run python -m parquet_analyzer` でアプリを起動
- 配布用 exe はこのエントリポイントをビルドしたもの。

### 5. 画面構成・主要機能

#### 5.1 メイン画面
- マルチプロット対応: 複数の時系列グラフをタイル状（縦分割など）に並べて表示できる。
- 重ね合わせ表示: 1つのプロット領域に複数の変数を重ねて表示できる。
- 各プロットに対し独立してズーム・パン操作が可能。
- 各プロットに凡例（変数名の一覧）を表示する。系列の追加/削除に連動して更新される。
- プロット右クリックメニューから「上に移動」「下に移動」で縦の並び順を入れ替えられる
  （2026-09-17追加）。最上段・最下段でそれぞれ「上に移動」「下に移動」を選んでも何も
  起きない（範囲外への移動は無視される）。並び替え後、時間軸プロット間のX軸連動
  （[5.3](#53-操作v2実使用フィードバックにより確定)）は常に**現在最上段にあるプロット**を
  基準に再構成される（詳細は`detailed_specification.md` 5章）。

#### 5.2 ナビゲータ
- 全プロット共通、~~またはプロットごとのナビゲータ~~（縮小表示 + 現在の表示範囲を示す
  矩形）を画面内に常設する。
- ナビゲータ上のドラッグ/クリックで表示範囲を移動できる。
- 時間軸の移動はできるが、縦軸は固定して。粗くてもいい。
- 概要波形として表示する変数は**最上段のプロットに重ねられている最初の変数**
  （2026-09-17変更。以前はファイルの列順で決まる先頭の変数を無条件に表示していた）。
  まだ何も重ねられていない場合はその暫定挙動にフォールバックする。最上段のプロットの
  内容（重ね合わせの追加・並び替え等）が変わるたびに追従して更新される。

#### 5.3 操作（v2、実使用フィードバックにより確定）
| 操作 | 動作 |
|---|---|
| マウスホイールスクロール | X軸（時間）のみズーム |
| Ctrl + スクロール | Y軸（Amp）のみズーム |
| Shift + スクロール | X軸（時間）方向にパン（移動、ズームはしない） |
| H キー / Home ボタン | 表示範囲を全データ表示にリセット |
| R キー / Redraw ボタン | 現在の表示範囲でプロットを再描画（ダウンサンプリング再計算含む） |

> **v1からの変更点**: 当初は無印スクロール=両軸ズーム、Ctrl=X軸のみ、Shift=Y軸のみで、
> 「両軸ズーム」は設定で無効化できる形にしていた（旧`both_axis_scroll_zoom`設定）。
> 実際に使ってみると操作感が悪いとのフィードバックがあり、「無印=時間ズーム、Ctrl=Ampズーム、
> Shift=時間移動」という3操作を完全に分離する現行方式に変更した。無印スクロールが常に時間軸
> ズーム専任になったため、両軸ズームの設定自体が不要になり削除した。
>
> さらに実機（macOS）で試したところ、Ctrl/Shiftが効かず常に時間ズームしか起きないという
> 不具合が判明した。原因はQtがmacOS上で物理Ctrlキーを`MetaModifier`として報告する仕様
> （Cmd/Ctrl入れ替え）と、一部トラックパッドがShift+スクロールを水平方向のホイール値として
> 報告する挙動の2点。両方に対応済み（詳細は
> [detailed_specification.md 4章](./detailed_specification.md#4-操作仕様確定113v2)）。

- 時刻軸（X軸）の目盛表示は、ズームレベルに応じて動的に変わる。広い範囲では日付/月単位、
  数秒〜数分まで拡大すると時:分:秒（ミリ秒）単位まで自動的に詳細化される。

#### 5.4 自動ダウンサンプリング
- 目的: 大量データ（数百万点規模）をそのまま描画すると読み込み・描画が遅くなるため、
  表示に必要な点数まで間引く。
- 方式: **LTTB (Largest-Triangle-Three-Buckets)** アルゴリズムを採用する。
  単純な間引き（等間隔サンプリング）と異なり、波形の視覚的な特徴（ピーク・谷）を
  可能な限り保持したまま点数を削減できるため。
- 動作:
  - 表示ウィンドウ（プロット領域の横幅に対応する画面ピクセル数、例: 幅1200pxなら
    最大 2400点程度＝1pxあたり2点）を上限として、範囲内のデータ点数がそれを超える場合に
    ダウンサンプリングを適用する。
  - ズーム/パンで表示範囲が変わるたびに、その範囲に対して再計算する（Rボタンで明示的にも
    再計算可能）。この自動再計算は設定でオフにできる（`auto_redraw`設定、[5.10](#510-設定メニュー)参照）。
    オフの場合もRボタン/ショートカットでの手動再計算は常に有効。
  - 元データはメモリ上（またはキャッシュ）に保持し、ダウンサンプリングは表示用の
    一時的な間引きであり元データを破壊しない。
  - 再計算はUIスレッドをブロックしないよう別スレッドで非同期実行する（詳細は
    [detailed_specification.md 10章](./detailed_specification.md#10-ダウンサンプリング再計算の非同期化確定)）。
    大量データ時にズーム操作のたびにUIがフリーズするのを避けるため。
  - 再計算中はステータスバーに進捗インジケータ（不定進捗）を表示する（[5.10](#510-設定メニュー)参照）。

#### 5.5 変数間演算
- 四則演算（+, -, ×, ÷, べき乗, 剰余, 切り捨て除算）、絶対値（abs）、平方根（sqrt）に加え、
  三角関数（sin/cos/tan）、指数・対数（exp/log/log10）、丸め（sign/floor/ceil/round）、
  範囲操作（min/max/clip、いずれも要素ごと）、移動平均（rolling_mean）を変数（列）に対して
  適用できる。詳細・対応関数一覧は
  [detailed_specification.md 6章](./detailed_specification.md#6-演算式入力114)。
- 演算結果は新しい仮想変数としてプロット可能な変数リストに追加される。
- 演算変数名が既存の列名と衝突する場合はエラーとし、データの意図しない上書きを防ぐ。
- 対応演算式の入力方法・UIは [11.4](#114-演算式の入力ui解決済み) で確定。
- 演算で作成した変数（仮想変数）も、生データではなく**定義式（式の文字列と入力元変数名）**として
  ビューに保存する。ビュー読み込み時に元の列から再計算して復元する。
  理由: 元データが更新された場合も再計算で追従でき、保存ファイルも軽量に保てるため。

#### 5.6 周波数解析
- 選択した変数に対しFFTを実行し、周波数スペクトルを別プロットとして表示する。
- FFTの対象は**選択した変数の現在の可視時間範囲**（時間軸プロット間で共有されているX範囲）。
  ファイル全体を対象にしない（2026-09-17変更、経緯は`detailed_specification.md`13.5.1参照）。
  理由は二つ: (1) メモリ上に全データを展開せずに済む、(2) 本アプリの想定データ（間欠バースト
  サンプリング。例えば連続サンプリングではなく、数時間おきの短時間高レート計測など）は
  サンプル間隔が均一でない区間を含み、
  ファイル全体を対象にしたFFTのサンプリング間隔推定（隣接サンプル間隔の中央値）自体が
  そもそも意味を持たない。可視範囲が広すぎる（既定8,000,000行超）場合はズームインを促す
  メッセージを表示し、計算は実行しない。処理はUIスレッドをブロックしないよう非同期実行する。

#### 5.7 Δ（デルタ）解析
- 選択した変数の隣接サンプル間差分（1階差分）を計算し、時系列として表示する。

#### 5.8 ビュー保存機能
- 現在の画面レイアウト（プロット配置、表示変数、演算式、軸範囲等）を「ビュー」として
  保存できる。
- 保存先: `data/parquet_analyzer/`
- 保存フォーマット: **JSON**（可読性・拡張性を優先。バイナリ形式は不要と判断）。
- 保存されるビューには、読み込んだ元Parquetファイルへの相対/絶対パス参照を含み、
  読み込み時に同じデータへ再アクセスする。
- ただし、ビュー読み込み時に**別のファイルが既に開かれている場合**は、そのファイルを
  開き直さず、現在開いているファイルに対してプロット配置（表示変数・演算式・軸範囲）だけを
  適用する（2026-09-17変更、経緯は`detailed_specification.md` 8章参照）。同じ列名を持つ
  複数ファイルに同一レイアウトを使い回すユースケースを想定している。

#### 5.9 データフォルダ選択
- Parquetファイルを開く際、参照先フォルダを選べるようにする（フォルダ選択ダイアログ）。
- デフォルトの参照先は**最後に参照したフォルダ**。
- 直近参照フォルダを**候補として5件**バッファし、ダイアログ内から素早く選び直せるようにする。
- 詳細は [detailed_specification.md 7章](./detailed_specification.md#7-データソース選択とフォルダ履歴)。

#### 5.10 設定メニュー
- ツールバーから設定ダイアログを開き、以下をまとめて変更できる。
  - 自動再描画のON/OFF（`auto_redraw`）
  - ダウンサンプリング密度（`downsample_pixel_ratio`）
  - 各操作（ホーム、再描画、開く、プロット追加、周波数解析、統計、ビュー保存/読込）のショートカットキー
  - どのスクロールホイール修飾キーがどの操作を行うか（`scroll_bindings`）
  - **UI言語**（`language`; `English`/`日本語`、新規インストール時の既定は`English`——
    2026-09-19追加、ユーザー要望。反映はアプリの次回起動時）
- 設定は `cfg/parquet_analyzer/settings.json` に保存され、次回起動時も引き継がれる。
- 詳細は [detailed_specification.md 9.1章](./detailed_specification.md#91-設定メニュー)。

#### 5.11 レイアウトの永続化
- ウィンドウサイズ、変数パネル（ドック）の配置、ツールバー位置などの画面レイアウトを
  `cfg/parquet_analyzer/settings.json` に保存し、次回起動時に復元する。
- 変数パネルの既定（初回起動時・未保存時）の配置は**画面左側**。
- ツールバーの「レイアウトをリセット」で、保存されたレイアウトを破棄し既定配置に戻せる。
- 詳細は [detailed_specification.md 9.3章](./detailed_specification.md#93-レイアウトの永続化)。

#### 5.12 統計表示（2026-09-17追加）
- `VariablePanel`で1つ以上の変数を選択して実行すると、**現在表示中の時間範囲**（時間軸
  プロット間で共有されているX範囲。[5.6](#56-周波数解析)のFFTと同じ範囲決定方法）について
  件数・欠損値数・最小・最大・平均・標準偏差・中央値を計算し、別ウィンドウ（非モーダル）に
  表として表示する。
- 対象は**実際に描画されているダウンサンプリング後の点ではなく、その範囲の生データ全件**
  （[5.4](#54-自動ダウンサンプリング)のLTTB表示用データとは別に、`_raw_variables`から
  範囲を切り出して計算する）。FFTと異なり行数の上限は設けない（平均・標準偏差等はサンプリング
  間隔が不均一でも意味を持ち、1回のnumpy走査で済むため）。
- 複数選択時は選択した全変数を1回の実行でまとめて表として表示する（選択順を保持）。
- 処理はUIスレッドをブロックしないよう変数ごとに非同期実行する（詳細は
  `detailed_specification.md` 14章参照）。

#### 5.13 カーソル（2026-09-19追加、ユーザー要望）
- ツールバーの「カーソル」トグルでカーソル設置モードのON/OFFを切り替える。既定はOFFで、
  ON/OFFいずれの状態でも通常のパン/ズーム操作自体には影響しない。
- ONの状態で時間軸プロット上の任意の位置をクリックするとカーソルを設置する: ドラッグ可能な
  縦線と、そのX（時刻）値および**同じプロット上に現在表示されている各系列**の最近傍サンプル値を
  示すテキストオーバーレイが表示される。複数のプロットが開いている場合、同じカーソル（同じX）は
  **他の全ての時間軸プロットにも**設置され、それぞれ自分のプロット上の系列の値を表示する
  （周波数軸（FFT）プロットはX軸のドメインが異なるため対象外）。
- 既存のカーソルを直接クリックすると削除される（表示している全プロットから）。ドラッグすると
  移動する（同様に表示している全プロットに同期）。トグルをOFFにすると、既存のカーソルは削除
  されず**非表示**になる（ドラッグ・削除不可、表示もされない）。再度ONにすると同じ位置のまま
  表示が復元される。OFFの間はマウス操作は通常のパン/ズームとして扱われる。
- カーソルは何本でも、互いに独立して同時に設置できる。
- ファイルを新しく開く、または時間軸の列を切り替えると、全カーソルはクリアされる（以前の
  時間軸上のX位置は新しい時間軸では意味を持たないため）。
- 詳細は `detailed_specification.md` 16章参照。

### 6. データ処理

- Parquet読み込みは `polars`（`scan_parquet`によるレイジー評価）を採用する
  （[11.6](#116-parquet読み込みライブラリ解決済み) 参照）。
- 大容量ファイルはできる限り遅延読み込み・必要範囲のみ読み込みを検討する。

### 7. 設定（cfg/）

- ダウンサンプリングの閾値・密度、自動再描画のON/OFF、ショートカットキー、直近参照フォルダ・
  直近開いたファイル履歴などのアプリ設定を `cfg/parquet_analyzer` 配下にJSONで保存する。
  スキーマ詳細は [detailed_specification.md 9章](./detailed_specification.md#9-設定ファイル-cfgparquet_analyzersettingsjson)。

### 8. ログ（log/parquet_analyzer）

- 実行時ログ、エラー時のスタックトレース等を `log/parquet_analyzer` 配下に出力する。
- `--debug` 付きで起動するとデバッグモードになり、ファイルを開く・ズーム/パン・変数の
  重ね合わせ・ビュー保存/読込などのユーザー操作を専用ファイル（`parquet_analyzer_ops_*.log`）に
  ダンプできる。詳細は
  [detailed_specification.md 11.1章](./detailed_specification.md#111-デバッグモード起動と操作ログ)。

### 9. 非機能要件

- 大容量データ（数百万行）を扱う際も、ダウンサンプリングによりUI操作が数秒以内に反映されること。
- Windows/macOS両対応。
- 配布はexeでインストール不要（Windows向け）。

### 10. 対象外（スコープ外）

- Parquet以外のファイル形式への**アプリ本体での**直接対応（CSV等の変換は対象外）。MDF・
  MATLAB（`.mat`）は同じ分野でよく使われるが、アプリが直接読むのではなく**別ツール**
  （`tools/convert_to_parquet.sh`）で変換する方式にした。理由は
  [detailed_specification.md 15章](./detailed_specification.md#15-mdfmatlab--parquet-変換ツール2026-09-18追加)
  参照（これらの形式はParquetのような列・範囲選択的な読み込みに向いた構造ではなく、
  本アプリの効率化設計の一部がParquetの構造に依存しているため）。
- リアルタイムストリーミングデータの表示。

### 11. 未確定事項

ドラフトの記述だけでは実装方針を一意に決められない項目。上記本文には暫定案を記載したが、
実装前に確認・確定が必要。

#### 11.1 「Pygraph」の解釈（解決済み）
ドラフトに記載の `Pygraph` は `pyqtgraph` を指すと解釈して確定した
（[detailed_specification.md 1章](./detailed_specification.md#1-技術選定確定)）。

#### 11.2 他プロジェクトとのパス共有方式（解決済み）
`src/parquet_analyzer/config.py` にパス定義を集約し、他プロジェクトからは `PYTHONPATH` 経由でこれを
直接importして参照する方式に確定（[3.1](#31-他プロジェクトとのパス共有) 参照）。
なお `src/common/`（コード共有）自体をどう配布するか（gitサブモジュール化、別リポジトリへの
切り出し、単純にローカル参照のみ、等）は別途未確定。

#### 11.3 ズーム操作の軸割当（解決済み・v2）
無印スクロール=X軸（時間）ズーム、Ctrl+スクロール=Y軸（Amp）ズーム、Shift+スクロール=X軸パン
（ズームなし）に確定（[5.3](#53-操作v2実使用フィードバックにより確定) /
[detailed_specification.md 4章](./detailed_specification.md#4-操作仕様確定113v2)）。

#### 11.4 演算式の入力UI（解決済み）
テキスト入力欄（Python風の式）＋変数ダブルクリックでの挿入補助、ASTホワイトリストによる
制限付き評価に確定（[detailed_specification.md 6章](./detailed_specification.md#6-演算式入力114)）。

#### 11.5 exeの方式（解決済み）
PyInstaller（`--onedir`）に確定
（[detailed_specification.md 1章](./detailed_specification.md#1-技術選定確定)）。

#### 11.6 Parquet読み込みライブラリ（解決済み）
`polars`（`scan_parquet`によるレイジー評価）に確定。スキーマ確認など軽量操作は内部で
`pyarrow`も利用する（[detailed_specification.md 1章](./detailed_specification.md#1-技術選定確定)）。

#### 11.7 マルチプロット/重ね合わせのUI操作（解決済み）
変数パネルからのドラッグ&ドロップで重ね合わせ、右クリックメニューで分離/削除/並び替え、
ツールバーの＋でプロット段を追加する方式に確定
（[detailed_specification.md 5章](./detailed_specification.md#5-マルチプロット--重ね合わせ操作117)）。
