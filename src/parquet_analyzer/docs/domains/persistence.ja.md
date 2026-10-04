# 保存（ビュー・設定・レイアウト）

> 総括仕様: [overview.ja.md](../overview.ja.md)（共通ルール・用語・旧節番号の対応表）。
> 本書は「要求」→「設計」の順。節番号は旧仕様書の番号をそのまま使っている。

**この分野の範囲**: ビュー（保存・読み込み・JSONスキーマ・管理画面）、設定ファイルと設定メニュー、ウィンドウレイアウトの永続化。

## 要求

#### 5.8 ビュー保存機能
- 現在の画面レイアウト（プロット配置、表示変数、演算式、軸範囲等）を「ビュー」として
  保存できる。
- 保存先: `data/parquet_analyzer/`
- 保存フォーマット: **JSON**（可読性・拡張性を優先。バイナリ形式は不要と判断）。
- 保存されるビューには、読み込んだ元Parquetファイルへの相対/絶対パス参照を含み、
  読み込み時に同じデータへ再アクセスする。
- ただし、ビュー読み込み時に**別のファイルが既に開かれている場合**は、そのファイルを
  開き直さず、現在開いているファイルに対してプロット配置（表示変数・演算式・軸範囲）だけを
  適用する（2026-09-17変更、経緯は`detailed_specification.ja.md` 8章参照）。同じ列名を持つ
  複数ファイルに同一レイアウトを使い回すユースケースを想定している。
- 2026-10-02追加分としてビューに保存するもの: 各プロットの高さの比率
  （[5.15](./plots.ja.md#515-プロットの高さ変更並べ替え2026-10-02追加ユーザー要望)）、各系列の表示/非表示
  （[5.17](./plots.ja.md#517-系列ごとの操作-選択削除表示非表示2026-10-02追加ユーザー要望)）、各プロットのX軸連動の
  有無と、連動していないプロットのX範囲（[5.18](./plots.ja.md#518-プロットごとのx軸の独立2026-10-02追加ユーザー要望)）。
  仮想変数がユーザー関数・マップ（[5.20](./expressions.ja.md#520-ユーザー関数2026-10-02追加ユーザー要望)・
  [5.21](./expressions.ja.md#521-マップテーブル2026-10-02追加ユーザー要望)）を使っていても、ビューには式（関数名・マップ名を
  含む）だけを保存し、定義そのものはコピーしない。
- **ビューの管理**（2026-10-01追加、ユーザー要望）: 「ビュー読込」は、名前だけの一覧ではなく
  ビュー管理ウィンドウを開く。保存済みビューを一覧表示し、選択したビューについて読み込む前に
  **中身**を表示する: 元ファイル、各プロットとそこに表示するチャンネル（変数）とその色、
  演算変数とその式、保存時の時間範囲、保存日時。ファイルを開いている場合は、開いている
  ファイルに無い（＝読み込み時にスキップされる）チャンネルに印を付ける。同じウィンドウから
  ビューを**読み込む**ことも**削除**することもできる（削除は確認の後、JSONファイルを消す。
  元に戻せない）。読めないビューファイル（破損・手編集など）も一覧に出し、中身の代わりに
  エラー内容を表示し、削除できる。詳細は
  [detailed_specification.ja.md 20章](#20-ビュー管理ウィンドウ2026-10-01追加ユーザー要望)。

#### 5.10 設定メニュー
- ツールバーから設定ダイアログを開き、以下をまとめて変更できる。
  - 自動再描画のON/OFF（`auto_redraw`）
  - ダウンサンプリング密度（`downsample_pixel_ratio`）
  - 列を常駐させずに必要時に読み込む切り替えサイズ（`eager_load_limit_mb`。変更後に開くファイルから
    適用）
  - ツールバーの各操作（ホーム、再描画、開く、プロット追加、統計、ビュー保存、ビュー管理、画像を保存、
    簡易表示の切替、カーソル。周波数解析は有効な間のみ）のショートカットキー
  - どのスクロールホイール修飾キーがどの操作を行うか（`scroll_bindings`）
  - **UI言語**（`language`; `English`/`日本語`、新規インストール時の既定は`English`——
    2026-09-19追加、ユーザー要望。反映はアプリの次回起動時）
- 設定は `cfg/parquet_analyzer/settings.json` に保存され、次回起動時も引き継がれる。
- 詳細は [detailed_specification.ja.md 9.1章](#91-設定メニュー)。

#### 5.11 レイアウトの永続化
- ウィンドウサイズ、変数パネル（ドック）の配置、ツールバー位置などの画面レイアウトを
  `cfg/parquet_analyzer/settings.json` に保存し、次回起動時に復元する。
- 変数パネルの既定（初回起動時・未保存時）の配置は**画面左側**。
- ツールバーの「レイアウトをリセット」で、保存されたレイアウトを破棄し既定配置に戻せる。
- 詳細は [detailed_specification.ja.md 9.3章](#93-レイアウトの永続化)。

### 7. 設定（cfg/）

- ダウンサンプリングの閾値・密度、自動再描画のON/OFF、ショートカットキー、直近参照フォルダ・
  直近開いたファイル履歴などのアプリ設定を `cfg/parquet_analyzer` 配下にJSONで保存する。
  スキーマ詳細は [detailed_specification.ja.md 9章](#9-設定ファイル-cfgparquet_analyzersettingsjson)。

#### 5.26 ビューに時間軸の列を保存する（2026-10-02追加、外部レビューの事実確認から）
- 今のビューは、時間軸に使った列を保存しない。別の列を時間軸にして保存したビューを開くと、
  時間軸がファイルの先頭の列に戻り、「保存したのに違う」状態になる。**時間軸の列をビューに
  保存し、開くときにその列を時間軸にする**（その列がファイルに無ければ今の時間軸のまま、
  まとめて警告に1行加える）。
- 詳細は[33章](#33-ビューに時間軸の列を保存する2026-10-02追加外部レビューの事実確認から)。

#### 5.27 ビューのファイルの場所を相対パスでも保存する（2026-10-02追加、外部レビューの事実確認から）
- 今のビューは元のParquetファイルを絶対パスで保存するので、フォルダごと別の場所・別のPCへ
  移すと開けない。**絶対パスに加えて、ビューのファイルからの相対パスも保存**し、開くときは
  絶対パス → 相対パスの順に探す（残タスク13.3の`path_type="relative"`の件をこれで解消する）。
- 詳細は[34章](#34-ビューのファイルの場所を相対パスでも保存する2026-10-02追加外部レビューの事実確認から)。

#### 5.28 ビューのファイルの形の検証（2026-10-02追加、外部レビューの事実確認から）
- 手で編集されたビューのファイルで、項目の型が違う（例: `"plots": "hello"`）場合も、
  「どこが違うか」が分かるエラーメッセージを出す（今も落ちはしないが、メッセージが例外の文字列の
  ままで分かりにくい）。優先度は低い。
- 詳細は[35章](#35-ビューのファイルの形の検証2026-10-02追加外部レビューの事実確認から)。

## 設計

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

- `derived_variables` は計算結果ではなく式そのものを保存する（`specification.ja.md` 5.5参照）。
- **2026-10-02追加のキー**（すべて省略可。無ければ従来の動作。`schema_version`は`"1"`のまま
  ——追加のみで、古いアプリで読んでも追加キーが無視されるだけのため）:
  - `plots[].height_ratio`（数値）— 22.2。
  - `plots[].x_linked`（真偽）と`plots[].x_axis_range`（`[lo, hi]`、連動していないプロット
    だけ）— 24.5。`x_axis_range`（トップレベル）は連動グループの範囲。
  - `plots[].series[].visible`（真偽）— 23.5。
  - ユーザー関数・マップを使う仮想変数も式だけを保存する（26.5・27.5）。
  - `io/view.py`の`load_view`は現在、`PlotDef`/`SeriesDef`を決め打ちのキーで作っている
    （`SeriesDef(**s)`は未知のキーで例外になる）。新しいキーは`.get(..., 既定値)`で読み、
    **未知のキーは無視する**ように直す（将来キーを足したときに古いアプリで読めなくならないように）。
- `source.path_type` が `relative` の場合、`view_name.json` からの相対パスとして解決する
  （`data/parquet_analyzer/sensor_overview.json` から見て `../raw/sensor_log_4y.parquet`）。
  ただし `MainWindow.save_view_dialog` は現状常に `path_type="absolute"` で保存するため、
  `relative` 分岐は`io/view.py`のロジックとしては実装済みだが、UIから実際に使われる経路が
  まだ無い（13章）。
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

- `eager_load_limit_mb`: 13.5.1章
  Phase E参照。
- `scroll_bindings`: [4章](./plots.ja.md#4-操作仕様確定113v2)のv3の経緯参照。
- `window_geometry` / `window_state`: [9.3章](#93-レイアウトの永続化)参照。
- `auto_redraw`: `false`にすると、パン/ズームで表示範囲が変わってもダウンサンプリング再計算を
  自動実行しない（Rボタン/ショートカットでの手動再描画のみ）。大量データで頻繁にパン/ズームする際、
  自動再計算が負荷になる場合の逃げ道。新規追加したデータの初回表示はこの設定に関わらず必ず行う
  （[ui/plot_widget.py](../overview.ja.md#2-モジュール構成) `add_series`）。
- `shortcuts`: アクションID→`QKeySequence`文字列（空文字は未割当）。[9.1章](#91-設定メニュー)の
  設定ダイアログから編集する。保存時に、当時存在しなかった新しいアクションIDは既定値で補完される
  （`io/settings.py` の `load_settings`）。
- `last_data_folder` / `recent_data_folders`: [7章](./data.ja.md#7-データソース選択とフォルダ履歴)参照。
  `recent_data_folders` は要素数5を上限としたMRUリスト、`last_data_folder` は
  `recent_data_folders[0]` と一致させる（先頭を読めば済むが、明示フィールドとして持たせて
  読み手にとっての意図を明確にする）。
- `last_export_folder`: 最後にプロット画像を保存したフォルダ（19.3章）。次回保存時の既定
  フォルダに使う。初回保存までは`null`。

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

### 20. ビュー管理ウィンドウ（2026-10-01追加、ユーザー要望）

（2026-10-02）欠けているものの印に、存在しないユーザー関数・マップを使う仮想変数
（とその依存先）を加える（26.5・27.5）。`load_view`の実際のスキップと同じ規則を保つこと。

要件: `specification.ja.md` 5.8章（「ビューの管理」）。背景: ビューは保存・読み込みはできたが
削除はできず（JSONを手で消すしかなかった）、読み込みダイアログ（`QInputDialog.getItem`）は
名前しか表示しないため、読み込む前にそのビューにどのチャンネルが含まれるか知る手段が無かった。

#### 20.1 `io/view.py`

- `delete_view(view_name) -> None`: `view_path(view_name).unlink()`（同じ名前検証が効く）。
  存在しなければ`FileNotFoundError`。
- `view_saved_at(view_name) -> datetime`: JSONファイルの更新日時（ローカル時刻）。ビューの
  スキーマには保存日時の項目が無いが、追加はしない（スキーマ変更なし）。

#### 20.2 `ui/view_manager.py` — `ViewManagerDialog(QDialog)`

- 表示に必要なデータだけを受け取って作る: `available_columns: set[str] | None`（開いている
  ファイルの列から**現在の時間軸列を除いたもの**。ファイル未オープンなら`None`）。時間軸列を
  除くのは、`load_view`が系列・式を時間軸列を含まない`_raw_variables`に対して解決するため、
  時間軸列を表示・参照するビューではその項目がスキップされるから（確認時に修正: 当初の仕様は
  「時間軸列を含む」としていた）。`MainWindow`の状態には
  自分では触れない: 読み込み・削除は戻り値/シグナルで伝える（後述）。
- レイアウト: 左に`view_io.list_views()`の`QListWidget`（返り値のままソート済み）、右に
  選択中ビューの詳細を表示する読み取り専用の`QTreeWidget`、下に**読み込む**・**削除**・
  **閉じる**ボタン。開いた時点で先頭のビューを選択。ビューをダブルクリック＝読み込む。
  未選択時は読み込む/削除を無効化。保存済みビューが無い場合: 一覧は空、詳細に
  `view.none_saved`、読み込む/削除は無効。
- 詳細ツリー（トップレベル行と、インデントされた子行）。`view_io.load_view()`から作る:
  - `元ファイル: <parquet_path>`（ツールチップ: パス種別）。
  - `保存日時: <YYYY-MM-DD HH:MM:SS>`（`view_saved_at`から）。
  - `x_axis_range`があれば`時間範囲: <開始> – <終了>`。カーソルラベルと同じ書式
    （`%Y-%m-%d %H:%M:%S.mmm`、`datetime.fromtimestamp`でローカル時刻）。
  - プロットごとに1行`プロット<n>（<k>チャンネル）`、展開状態。子行は系列ごとに1行:
    変数名と、保存された`color`の小さな色見本アイコン。
  - 演算変数があれば`演算変数（<k>）`、展開状態。子行は`名前 = 式`。
  - `ダウンサンプリング: 有効/無効`。
- 欠けているチャンネルの印（`available_columns`が`None`でないときだけ）。`load_view`の実際の
  連鎖と同じく**推移的に**判定する: ビューの演算変数を`core.variable.dependency_order`順に
  見ていき、式が解析でき、参照する名前（`core.expression.referenced_names`）がすべて
  `available_columns`にあるか、既に解決可能と判定された演算変数であれば*解決可能*、そうで
  なければ「欠けている」。系列は、その変数が`available_columns`にも解決可能な演算変数にも
  無ければ「欠けている」。つまり`d1`が欠けていれば、`d2 = d1 + 1`も、`d1`や`d2`を表示する系列も
  すべて印が付く（確認時に修正: 当初の仕様は直接の参照しか見ておらず、読み込み時に実際に
  スキップされるものを過少に示していた）。欠けている行: 赤字、末尾に
  ` — 開いているファイルに無い`、読み込み時にスキップされる旨のツールチップ。m > 0 のときだけ
  先頭に要約行`開いているファイルに無いチャンネル: <m>件`。`MainWindow.load_view`の実際の
  スキップ動作（8章）をそのまま反映するもので、それより厳しい判定はしない。
- 読めないビュー（`view_io.load_view`が何らかの例外）: 詳細は赤字の1行
  `このビューを読み込めません: <エラー>`のみ。読み込むは無効、削除は有効のまま。
- 削除: `QMessageBox.question`（`view.delete_confirm`、ビュー名を含む）。はいなら
  `view_io.delete_view`、一覧から除き、次のビュー（最後だったなら前のビュー）を選択し、
  `viewDeleted(str)`を発行。`OSError` → 警告`view.delete_failed`、一覧は変更しない。
- 読み込む: `accept()`し、`selected_view_name()`がその名前を返す。`MainWindow`はこれまで通り
  `load_view(name)`を呼ぶ（8章の動作は変更なし）。

#### 20.3 `MainWindow`

- `load_view_dialog()`は`ViewManagerDialog`を開く（モーダル、`exec()`）。
  時間軸列を除いた`self._file_columns`の列名集合、または`None`を渡す。acceptされたら`load_view(name)`。ツールバーの
  表記は`View...` / `ビュー...`（i18n `toolbar.load_view`）。ショートカットIDは`load_view`の
  まま（設定の移行は不要）。従来の「保存済みビューが無い」ときの情報ダイアログでの早期
  リターンは削除する——ダイアログ自身の空状態表示で足りる。
- `viewDeleted(name)`: `name == self._view_name`なら`_view_name`をクリアする（画像保存の
  記載に、もう存在しないビュー名を出さないため。19.3章）。`log_op("delete_view", name=...)`。
- i18n: 上記の文字列はすべて`tr()`を通す（en + ja）。

### 33. ビューに時間軸の列を保存する（2026-10-02追加、外部レビューの事実確認から）

要件: 5.26（外部レビュー#9）。
- `View.time_column: str | None`（JSONの`time_column`、既定`None`）。`save_view_dialog`は
  `MainWindow._time_column`を入れる。キーの追加だけなので`schema_version`は据え置き（8章の方針）。
- `load_view`: レイアウトを組み直す**前に**、`time_column`があり、開いているファイルの列にあり、
  今の時間軸と違えば`set_time_column(name)`を呼ぶ（時間軸を切り替えてから系列・範囲を適用しないと、
  保存したX範囲が別の列の値として解釈されてしまう）。列が無ければ切り替えず、まとめて出す警告に
  「時間軸の列 <名前> がありません」を加える。`None`（古いビュー）なら何もしない。
- ビュー管理画面（20章）の詳細に「時間軸: <列名>」を表示し、開いているファイルに無ければ
  欠けている印を付ける（`load_view`の実際の判定と同じ規則）。
- テスト: 先頭以外の列を時間軸にして保存→別の列を時間軸にした状態で読み込み→保存した列に戻る、
  保存したX範囲がその列の値として正しく復元される、列が無いファイルでは警告して今の時間軸のまま、
  古いビュー、ビュー管理画面の表示と印。

### 34. ビューのファイルの場所を相対パスでも保存する（2026-10-02追加、外部レビューの事実確認から）

要件: 5.27（外部レビュー#10、残タスク13.3）。
- `SourceDef`に`relative_path: str | None`を追加（ビューのJSONのある場所からの相対パス。
  `os.path.relpath`。別のドライブ等で作れなければ`None`）。`parquet_path`（絶対パス）と
  `path_type="absolute"`は従来どおり書く（古いアプリでも読めるように）。
- 開くとき（`resolve_source_path`）: 絶対パスにファイルがあればそれ、無ければ`relative_path`を
  ビューのJSONの場所から解決してあればそれ、どちらも無ければ従来どおりのエラー。どちらを
  使ったかをログ（操作ログの`details`）に残す。
- 既存の`path_type="relative"`の分岐（`parquet_path`自体が相対）はそのまま残す。
- 残タスク13.3の「`path_type`の`relative`分岐に到達しない」項目は、この章で解消済みとして
  `history.ja.md`へ移す。
- テスト: 同じ場所で開ける、フォルダ（データとビューの`data/`）ごと別の場所へ移しても開ける、
  絶対パスが優先される、どちらも無いときのエラー、相対パスが作れない場合（`relative_path=None`）。

### 35. ビューのファイルの形の検証（2026-10-02追加、外部レビューの事実確認から）

要件: 5.28（外部レビュー#8）。優先度: 低。
- `io/view.py`の`load_view`で、トップレベル・`source`・`plots[]`・`series[]`・`derived_variables[]`の
  型を確かめ、違えば`ViewFormatError(ValueError)`を「<項目のパス>: <期待する型>が必要です
  （<実際の値の型>）」の形で投げる（例: `plots: リストが必要です（文字列）`）。未知のキーは
  従来どおり無視する。
- `MainWindow.load_view`とビュー管理画面は、このメッセージをそのまま表示する。
- テスト: 各項目の型違い・必須項目の欠落・`null`、正常なビューはそのまま読める。

#### 35.1 壊れた値・書き込みの失敗（2026-10-03追加、テスト強化で見つかった不具合から）

原則: **1つの項目が壊れていても、ビュー・設定の残りは読み込む。保存の失敗で前のファイルを
壊さない。保存の失敗で画面が落ちない。**

- ビュー:
  - `x_axis_range`/`y_axis_range`が非有限（NaN・inf）なら、その範囲だけ捨てる（以前は
    pyqtgraphが例外を出し、以降のプロット・系列が復元されなかった）。±1e300のような有限の
    極端な値は現状どおり読み込む（表示が潰れるが、手で書き換えない限り起きないので扱いを
    決めずに置く）。
  - `downsample.enabled`が真偽値でなければ`true`。系列の色がQtで解釈できなければ自動の色。
  - 先頭のBOMを許す（`utf-8-sig`）。名前にNUL文字を含むものは拒否する。
  - 保存は一時ファイル＋`os.replace`（失敗しても同名の既存ビューは残る）。保存の`OSError`
    （名前が長すぎる、データフォルダーが書き込めない、ディスクが一杯）は警告を出す。
- 設定（`settings.json`、アプリ基盤12章の「壊れていたら既定値」を項目単位に細かくしたもの）:
  - 型・範囲が違う項目は**その項目だけ**既定値に戻す（範囲は設定画面の入力欄と同じ。
    `downsample_pixel_ratio` 1〜20、`eager_load_limit_mb` 1〜100000）。BOMを許す。
  - `save_settings`は失敗しても例外を出さず、ログに残して`False`を返す（終了時の
    `closeEvent`で例外が出ると閉じられなくなっていた）。**設定画面のOKで保存に失敗したとき
    だけ**警告を出す（2026-10-03決定。ユーザーが明示的に保存した操作なので知らせる。終了時・
    ファイルを開いたときなど裏で保存するときはログだけ）。
- 画像のエクスポート: matplotlibが出す例外は種類を問わず警告にする（落ちない）。
  範囲が非有限のときは現状どおり警告（画面上の範囲は非有限にならないので、実際には起きない）。
- 操作ログ: 引数に孤立サロゲート（デコードできないファイル名）や`__str__`が壊れたオブジェクトが
  あってもその記録を失わない。ログの表示ツールは形の壊れた記録を1件ずつ生のまま表示する。

## 他の分野との関係

- **プロット**（[plots.ja.md](./plots.ja.md)）: ビューの中身の大半はプロット構成。プロット側で状態を足したら、スキーマ・保存・読み込みを合わせる。
- **演算式**（[expressions.ja.md](./expressions.ja.md)）: 仮想変数の式の保存順（依存順）、関数・マップが無い場合のスキップ。
- **データ**（[data.ja.md](./data.ja.md)）: 元ファイルの扱い（既に開いているファイルは開き直さない）。
- **アプリ基盤**（[app.ja.md](./app.ja.md)）: パス（`config.py`）、設定ファイル破損時の扱い（12章）。
- **データ**（33章、2026-10-03）: ビューの時間軸の列で時間軸を切り替える。
- **アプリ基盤**（30章）: ログの保存期間（`log_retention_days`）は設定に保存し、設定画面から変える。
- **演算式**（26・27章）: ビュー管理画面の「欠けている」印は、関数・マップの有無と引数の静的な検査でも判定する。
