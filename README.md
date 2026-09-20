# Parquet Analyzer

**[English](#english)** | **[日本語](#japanese)**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.1.0-informational.svg)
![Author](https://img.shields.io/badge/author-ultramonaka-lightgrey.svg)

---

<a name="english"></a>
## English

A viewer and time-series analysis tool for Parquet files. Supports automatic
downsampling for large datasets, multiple plots, inter-variable expressions,
frequency (FFT) and delta analysis, summary statistics, and saved views.

For requirements-level detail see
[`src/parquet_analyzer/docs/specification.md`](src/parquet_analyzer/docs/specification.md),
and for implementation-level detail see
[`src/parquet_analyzer/docs/detailed_specification.md`](src/parquet_analyzer/docs/detailed_specification.md).

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
polars, pyarrow, numpy).

On macOS, double-clicking a `.sh` file just opens it in a text editor by default and
doesn't run it (that's Finder's standard behavior). To launch by double-clicking, use
`tools/run.command` instead (a thin wrapper that just calls `run.sh` — Finder treats
`.command` files as executable).

#### Debug mode

Launches while dumping an operation log (opening files, zoom/pan, overlaying
variables, saving/loading views, etc.) to
`log/parquet_analyzer/parquet_analyzer_ops_<date>.log`.

```sh
tools/run_debug.sh     # macOS/Linux
tools\run_debug.bat     # Windows
```

(Equivalently enabled via the `--debug` flag or the `PARQUET_ANALYZER_DEBUG=1`
environment variable. The window title gets a `[DEBUG]` suffix while this is active.)

### Basic usage

1. Choose "開く" (Open) on the toolbar to select a Parquet file. The dialog starts in
   the last folder you opened, with the 5 most recent folders available in a sidebar.
   The currently open file's name is shown in the window title and in the status bar
   at the bottom left (hover it for the full path).
2. The loaded columns are listed in the "変数" (Variables) panel on the right.
   Drag one onto a plot to overlay it there (the legend updates automatically).
3. "＋ プロット追加" (Add plot) on the toolbar adds a new plot pane. Right-clicking a
   plot offers "上に移動"/"下に移動" (move up/down, to reorder the stacked panes —
   a frequency-domain (FFT) plot can't be reordered past a time-domain one or vice
   versa), "このプロットから削除" (remove this plot pane entirely), and
   "新規プロットとして分離" (split all but the first overlaid series off into a new
   plot pane).
4. The navigator at the top of the window lets you quickly move the visible time
   range. It shows an overview of whichever variable is overlaid first on the topmost
   plot pane (falling back to an arbitrary column if nothing has been plotted yet).

#### Changing which column is used as the time (X) axis

By default the file's **first column** is used as the time (X) axis. Change it via the
"時間軸:" (Time axis) combo box on the toolbar. Switching axes returns the previous
time column to the variable panel as an ordinary variable, and every already-plotted
series is re-fit to the new time axis.

#### Zoom / pan controls

| Action | Effect (current default) |
|---|---|
| Mouse wheel scroll | Pan along the time axis (no zoom) |
| Ctrl + scroll | Zoom time (X) axis only |
| Shift + scroll | Zoom amplitude (Y) axis only |
| H key / Home button | Reset the view to the full data range |
| R key / Redraw button | Recompute downsampling for the current view |

Which action each of plain/Ctrl+/Shift+scroll performs is configurable in the
"設定" (Settings) dialog's "スクロール操作" (Scroll operations) section — reassigning
an action already used by another modifier swaps the two, so every action always
stays reachable by exactly one modifier. The table above is just the default.

The time (X) axis tick granularity adapts automatically to the zoom level — date/month
when zoomed out, down to hour:minute:second.millisecond when zoomed into a few
seconds (this doesn't apply to an FFT plot's X axis, which is frequency in Hz).

Large datasets are automatically decimated for display based on pixel count (the LTTB
algorithm, which preserves a waveform's peaks/troughs while reducing point count).
This recompute runs asynchronously on a background thread — a progress indicator
appears in the status bar while it's running, but the UI itself never blocks.

#### Inter-variable expressions and analysis

- Typing an expression that references variable names (e.g. `abs(A) - sqrt(B)`,
  `delta(rpm)`, `rolling_mean(rpm, 20)`, `clip(rpm, 0, 3000)`) into the expression bar
  at the bottom of the window and clicking "追加" (Add) creates a new derived
  variable in the variable panel.
  - Operators: `+ - * / ** % //`
  - Functions (all elementwise): `abs`, `sqrt`, `delta` (first-order difference),
    `rolling_mean(a, window)` (moving average), `sin`, `cos`, `tan`, `exp`, `log`,
    `log10`, `sign`, `floor`, `ceil`, `round`, `min(a, b)`, `max(a, b)`,
    `clip(a, lo, hi)` (`min`/`max` are elementwise comparisons here, unlike Python's
    built-ins — they don't reduce to a single scalar).
- Defining a derived variable with the same name as an existing column is an error
  (to prevent accidentally shadowing real data). Redefining an existing derived
  variable's formula under the same name is allowed.
- Double-clicking a variable in the panel inserts its name at the cursor position in
  the expression bar.
- Frequency analysis (FFT) exists internally (adds an FFT spectrum of the *currently
  visible time range* as a new plot) but its toolbar button and settings-dialog
  shortcut row are currently hidden pending further debugging
  (`io/settings.py`'s `FFT_ENABLED`) — not reachable from the GUI right now.
- Clicking "統計" (Statistics) computes count, missing-value count, min, max, mean,
  standard deviation, and median over every raw sample in the currently visible time
  range (not the downsampled curve actually drawn on screen) and shows the results in
  a separate window, for one row per variable. Targets whichever variable(s) are
  selected in the panel; if nothing is selected there, it falls back to whatever's
  currently overlaid on the plots (across all time-domain plots), so a variable
  that's already plotted doesn't need re-selecting in the panel just to see its
  stats. An explicit panel selection always takes priority over what's plotted.

#### Saving and loading views

The current plot layout, overlaid variables, and expressions can be saved as a "view"
(JSON, under `data/parquet_analyzer/`). Derived variables are saved as their defining
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

#### Settings menu

"設定" (Settings) on the toolbar lets you change the following (saved to
`cfg/parquet_analyzer/settings.json` and restored on next launch):

- Auto-redraw on/off (manual redraw via the R button/shortcut always works even when
  off)
- Downsampling density (displayed points per pixel)
- The size threshold above which a column is read from disk on demand (visible
  range only) instead of being fully loaded once and kept resident — only affects
  files opened after the change
- Which action (X zoom / Y zoom / pan) each of plain/Ctrl+/Shift+scroll performs
- Keyboard shortcuts for each action (home, redraw, open, add plot, statistics,
  save/load view — frequency analysis is omitted here while its toolbar button is
  hidden, see above)

#### Layout persistence

Window size, the variable panel's dock position, and similar screen layout are saved
automatically on exit and restored on next launch. The variable panel defaults to the
left side of the window. "レイアウトをリセット" (Reset layout) on the toolbar discards
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
| Log (debug-mode operation log) | `log/parquet_analyzer/parquet_analyzer_ops_<date>.log` |

### For developers

```sh
uv sync --group dev   # install dev-only dependencies (pytest, etc.)
uv run pytest         # run the test suite
```

Exercising the app headlessly (no real display):

```sh
QT_QPA_PLATFORM=offscreen bash tools/run.sh
```

### License

Licensed under the **[MIT License](LICENSE)**.

**Author**: [ultramonaka](https://github.com/ultramonaka)

---

<a name="japanese"></a>
## 日本語

Parquetファイルのビューワー・時系列解析ツール。大量データの自動ダウンサンプリング表示、
マルチプロット、変数間演算、周波数解析・Δ解析、統計表示、ビュー保存などに対応する。

仕様の詳細は [`src/parquet_analyzer/docs/specification.md`](src/parquet_analyzer/docs/specification.md)
（要求仕様）と
[`src/parquet_analyzer/docs/detailed_specification.md`](src/parquet_analyzer/docs/detailed_specification.md)
（実装仕様）を参照。

### 必要環境

- Windows または macOS
- [uv](https://docs.astral.sh/uv/)（Pythonと依存パッケージはuvが自動で用意する。手動インストール不要）

### 起動方法

```sh
tools/run.sh          # macOS/Linux
tools\run.bat          # Windows
```

初回起動時に `uv` が依存パッケージ（PySide6, pyqtgraph, polars, pyarrow, numpy）を
自動でインストールする。

macOSでは `.sh` はダブルクリックしても既定でテキストエディタが開くだけで実行されない
（Finderの標準動作）。ダブルクリックで起動したい場合は `tools/run.command` を使う
（`run.sh` を呼ぶだけの薄いラッパー。Finderは `.command` を実行可能として扱う）。

#### デバッグモード

操作ログ（ファイルを開く、ズーム/パン、変数の重ね合わせ、ビュー保存/読込など）を
`log/parquet_analyzer/parquet_analyzer_ops_<日付>.log` にダンプしながら起動する。

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
2. 右側の「変数」パネルに読み込んだ列が一覧表示される。プロットへドラッグ&ドロップすると
   その変数が重ね合わせ表示される（凡例が自動更新される）。
3. ツールバーの「＋ プロット追加」で新しいプロットを追加できる。プロットを右クリックすると
   「上に移動」「下に移動」（プロット段の並び替え。周波数解析(FFT)プロットは時間軸プロット
   より前には移動できず、その逆もできない）、「このプロットから削除」（プロット段そのものを
   削除する）、「新規プロットとして分離」（最初の系列以外を新しいプロットへ移す）を選べる。
4. 画面上部のナビゲータで表示範囲（時間軸）を素早く移動できる。ナビゲータには
   **最上段のプロットに最初に重ねられた変数**の概要波形が表示される（何も重ねられていない
   場合は暫定的に別の列が表示される）。

#### 時間軸（X軸）に使う列の変更

既定では読み込んだファイルの**先頭列**が時間軸（X軸）として使われる。ツールバーの
「時間軸:」コンボボックスから任意の列名に変更できる。切り替えると、それまで時間軸だった列は
通常の変数として変数パネルに戻り、プロット済みの波形は新しい時間軸に合わせて表示範囲が
再フィットされる。

#### ズーム・パン操作

| 操作 | 動作（現在の既定値） |
|---|---|
| マウスホイールスクロール | 時間軸方向にパン（移動。ズームはしない） |
| Ctrl + スクロール | 時間軸（X軸）のみズーム |
| Shift + スクロール | Amp（Y軸）のみズーム |
| H キー / ホームボタン | 表示範囲を全データ表示にリセット |
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

#### 変数間演算・解析

- 画面下部の演算式入力欄に、変数名を使った式（例: `abs(A) - sqrt(B)`, `delta(rpm)`,
  `rolling_mean(rpm, 20)`, `clip(rpm, 0, 3000)`）を入力し「追加」すると、新しい変数
  （仮想変数）として変数パネルに追加される。
  - 演算子: `+ - * / ** % //`
  - 関数（いずれも要素ごとの演算）: `abs`, `sqrt`, `delta`（1階差分）,
    `rolling_mean(a, window)`（移動平均）, `sin`, `cos`, `tan`, `exp`, `log`, `log10`,
    `sign`, `floor`, `ceil`, `round`, `min(a, b)`, `max(a, b)`, `clip(a, lo, hi)`
    （`min`/`max`はPython組み込みと違い要素ごとの比較で、スカラー1つを返す集約関数ではない）
- 演算変数名が既存の列名と同じ場合はエラーになる（列データの意図しない上書きを防ぐため）。
  同じ名前の演算変数を式だけ変えて再定義することは可能。
- 変数パネルの変数をダブルクリックすると、カーソル位置に変数名が挿入される（入力補助）。
- 周波数解析（FFT）機能は内部的には存在する（現在表示中の時間範囲についてFFTスペクトルを
  新しいプロットとして追加する）が、デバッグが完了するまでツールバーのボタンと設定ダイアログの
  ショートカット欄を一時的に非表示にしている（`io/settings.py`の`FFT_ENABLED`）。
  現時点ではGUIから実行できない。
- 「統計」ボタンを押すと、現在表示中の時間範囲の生データ全件（画面に描画されている
  間引き後の点ではない）について件数・欠損値・最小・最大・平均・標準偏差・中央値を
  変数ごとに1行ずつ計算し、別ウィンドウに表として表示する。対象は変数パネルで選択中の
  変数。パネルで何も選択していない場合は、現在プロットに表示中（重ねて表示中）の変数が
  自動的に対象になる——既にプロット済みの変数の統計を見るのに、パネルで改めて選び直す
  必要はない。パネルで明示的に選択している場合は、プロット中の変数より常にそちらが優先される。

#### ビューの保存・読込

現在のプロット配置・重ね合わせ変数・演算式を「ビュー」としてJSON保存できる
（`data/parquet_analyzer/`）。演算変数は計算結果ではなく定義式として保存されるため、
元データが更新されても読み込み時に再計算される。ビュー読込時、**何らかのファイルが既に
開かれている場合**（ビュー保存時のファイルとは別のファイルでも、まさにそのファイル自身でも）
は、そのファイルを開き直さず、現在開いているファイルに対してレイアウトだけを適用する
（開いているファイルが無い場合のみ、ビューに記録された元ファイルを開く）。保存済みレイアウトが
参照する変数のうち現在開いているファイルに存在しないものはスキップされる（どの変数を復元
できなかったかを警告として表示）が、一致する変数は問題なく適用される——同じ列名を持つ
複数ファイルに同一レイアウトを使い回す用途で、列名が完全一致していなくても機能する。

#### 設定メニュー

ツールバーの「設定」から以下を変更できる（`cfg/parquet_analyzer/settings.json` に保存され、
次回起動時も引き継がれる）。

- 自動再描画のON/OFF（OFFでもRボタン/ショートカットでの手動再描画は常に可能）
- ダウンサンプリング密度（1pxあたりの表示点数）
- 列を開いた時点で全体を読み込まず表示範囲だけを都度読み込むようにする容量しきい値
  （変更後に開いたファイルから反映）
- 無印/Ctrl+/Shift+スクロールそれぞれの動作（X軸ズーム/Y軸ズーム/パン）
- 各操作（ホーム、再描画、開く、プロット追加、統計、ビュー保存/読込）のショートカットキー
  （周波数解析はツールバーボタンが非表示の間、ここにも表示されない。上記参照）

#### レイアウトの永続化

ウィンドウサイズ・変数パネルの配置などの画面レイアウトは終了時に自動保存され、次回起動時に
復元される。変数パネルの既定位置は画面左側。ツールバーの「レイアウトをリセット」で、保存された
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
| ログ（デバッグモードの操作ログ） | `log/parquet_analyzer/parquet_analyzer_ops_<日付>.log` |

### 開発者向け

```sh
uv sync --group dev   # pytestなど開発用依存関係をインストール
uv run pytest         # テスト実行
```

実ディスプレイなしでの動作確認（ヘッドレス）:

```sh
QT_QPA_PLATFORM=offscreen bash tools/run.sh
```

### ライセンス

**[MIT License](LICENSE)** の下で公開しています。

**作者**: [ultramonaka](https://github.com/ultramonaka)
