# アプリ基盤（構成・起動・ログ・エラー・バージョン・機能フラグ）

> 総括仕様: [overview.ja.md](../overview.ja.md)（共通ルール・用語・旧節番号の対応表）。
> 本書は「要求」→「設計」の順。節番号は旧仕様書の番号をそのまま使っている。

**この分野の範囲**: ディレクトリ構成とパスの共有、起動、ログ・操作ログ、エラー処理の方針、バージョンの埋め込み、開発用の機能フラグ。

## 要求

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
│       ├── docs/                 # 仕様書（総括＋分野ごと、日本語・英語）
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
  import して参照する（11.2 で確定）。
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

### 8. ログ（log/parquet_analyzer）

- 実行時ログ、エラー時のスタックトレース等を `log/parquet_analyzer` 配下に出力する。
- `--debug` 付きで起動するとデバッグモードになり、ファイルを開く・ズーム/パン・変数の
  重ね合わせ・ビュー保存/読込などのユーザー操作を専用ファイル（`parquet_analyzer_ops_*.log`）に
  ダンプできる。詳細は
  [detailed_specification.ja.md 11.1章](#111-デバッグモード起動と操作ログ)。

#### 5.23 操作ログの充実: 操作と、その後の動作の記録（2026-10-02追加、ユーザー要望）
- 不具合の報告・調査のために、**「何を操作したか」と「その結果アプリがどうなったか」を対に
  して記録する**。例: 「系列を削除 → 結果: 系列 b が消えた、ナビゲータの範囲が
  [60, 120] → [12, 642] に変わった」。
- **常に記録する**（デバッグモードでなくても）。普段の起動で起きた不具合でも、後から
  ログで確かめられるようにするため。デバッグモードでは、より細かい内容とコンソールへの表示を加える。
- 記録するもの:
  - ユーザーの操作（今の操作ログの対象すべて）。
  - 操作の**結果**: 画面が落ち着いた（裏の処理が終わった）時点の状態と、**操作の前からの変化**。
  - 裏の処理の開始・完了・失敗（どの操作がきっかけか分かるように）。
  - 利用者に出したメッセージ（警告・エラーのダイアログ、ステータスバーの通知）。
  - 予期しない例外（今は画面に何も出ず、ログにも残らないことがある）。
- ログは日付ごとのファイルに保存し、**保存期間（既定7日）を過ぎたものは自動で削除する**。
  保存期間は設定画面で変更できる（2026-10-02、ユーザー指定）。
- ログを人が読みやすい形で表示する道具と、メニューから「ログのフォルダを開く」を用意する。
- 詳細は[30章](#30-操作ログの充実-操作と結果の記録2026-10-02追加ユーザー要望)。

#### 5.36 操作の副作用のテスト: 関係のない機能が動かないこと（2026-10-06追加、ユーザー要望）
- ボタン・メニューを押したときに、**その操作と関係のない状態が変わらない**ことを自動テストで確かめる。
  例: マップを保存したらプロットの範囲が動いた（開発メモの項目14）、系列を消したらナビゲータの範囲が
  ずれた（同じ種類の不具合が5回、開発メモ 2026-10-02）。
- 操作ごとに「変わってよい状態」を表にして持ち、操作の前後の状態の差（操作ログ30章と同じ差分）が
  表の外に出たら失敗にする。ファイルの読み直し・予期しないダイアログ・例外も失敗にする。
- 新しいボタン・メニューを足すときは、この表に1行足す（全分野の決まり）。
- 詳細は[45章](#45-操作の副作用のテスト関係のない機能が動かないこと2026-10-06追加ユーザー要望)。

#### 5.38 テストの強化: ランダム検証・失敗経路・窓読み込み・ビューの後方互換（2026-10-08追加）
- 外部レビュー2（開発メモ）の候補A。アプリの動作は変えず、テストだけを足して「壊れていない」ことを
  機械的に確かめる範囲を広げる。4つに分け、1つ＝1ブランチで順に進める（2026-10-08、ユーザー判断）:
  1. **A1 ランダムデータでの性質テスト**（`hypothesis`）: 式の評価器・`TimeIndex.searchsorted`・`chunked_stats`・
     LTTB・`rolling_mean`/`rolling_nanmean`・`row_range_for_x`が、でたらめな入力でも決まった性質を守る。
  2. **A2 裏の処理の失敗経路の網羅**: 成功・例外・取り消し・ウィジェット削除・ファイル切替・プロット削除の組み合わせを
     表にし、今のテストで埋まっていない所を足す。
  3. **A3 窓読み込みの列**: `window(lo, hi)`が全体の`[lo:hi]`と一致する。描画の経路で窓読み込みの列の全件読みが
     起きないことを見張る。
  4. **A4 ビューの後方互換**: 過去の版で保存したビューファイルをテストに置き、いつでも読めることを確かめる。
- テストで見つかった不具合はその場で直さず、報告して別の作業にする（45章と同じ）。
- 詳細は[46章](#46-テストの強化ランダム検証失敗経路窓読み込みビューの後方互換2026-10-08追加)。

#### 5.41 ツールバーの整理: 2本に分ける・文言・まれな操作をまとめる（2026-10-13追加、ユーザー要望）
- メインのツールバーは16個ほどの操作が1本に並び、(1) 並び順・まとまりが分かりにくい、(2) 文言が分かりにくい、
  (3) 数が多くて探しにくい（ユーザーの指摘、2026-10-13）。メニューバーは作らず、**ツールバーを2本に分ける**
  （ユーザー判断）:
  - **ファイル・表示**: 開く ｜ 時間軸 ｜ ビュー ▾・画像を保存 ｜ 全体表示・再描画 ｜ ＋プロット追加 ｜ カーソル・値の表示
  - **解析**: 統計・グラフ ▾ ｜ ユーザー関数・マップ ｜ ⚙ ▾
- 文言: 「ホーム」→「全体表示」。「ビュー保存」「ビュー...」→「ビュー ▾」（保存... / 管理...）。ツールバーの「関数」→
  「ユーザー関数」（式の入力欄の右端の「関数一覧...」と区別する。英語も「Functions...」→「Function list...」）。
- まれにしか使わない「簡易表示を許可」（チェック付きのまま）・「設定...」・「レイアウトをリセット」は「⚙ ▾」にまとめる。
- 既定では2本を同じ行に並べる。位置はこれまでどおり保存・復元され、「レイアウトをリセット」で既定に戻る。
- 機能そのもの・ショートカットの設定（キーの名前）は変えない。
- 詳細は[48章](#48-ツールバーの整理2026-10-13追加ユーザー要望)。

#### 5.44 CIの整備: macOSのジョブを手動で動かす・Ubuntuで落ちるテストを直す（2026-10-13追加、ユーザー判断）
- 普段使いはMacなので、CI（GitHub Actions）でもmacOSでテストを動かせるようにする。ただしリポジトリは非公開で、
  macOSの実行時間はUbuntuより何倍も多く数えられ（9/20〜10/13の23日でUbuntuだけで約1,500分）、毎回のpushでは
  動かせない。**macOSのジョブは手動実行（`workflow_dispatch`）だけ**にする（ユーザー判断、案A）。
- UbuntuのCIは今どおりpushごとに動かす。ブランチでCIを試せるよう、Ubuntuのワークフローにも手動実行を足す。
- 2026-10-07から`main`のCIが赤い（手元のMacでは全部通る）。先に直す: `test_worker_paths.py::test_stats_paths[S-3]`
  （10/07から）と、「A」ボタンのテスト3件（`test_plot_widget.py`、10/13のマージから）。
- 詳細は[51章](#51-ciの整備2026-10-13追加ユーザー判断)。

#### 5.45 lint（ruff）の導入（2026-10-13追加、ユーザー判断。開発メモの項目36）
- 未使用のimport・未定義の名前・importの並び順・古い書き方を機械で見つけ、CIで新しく混ざるのを防ぐ。
- ルールは`E`・`F`・`I`・`UP`（ユーザー判断）。行の長さ（E501）は見ない。整形（`ruff format`）は入れない（ユーザー判断）。
  型チェック（mypy/pyright）も入れない（PySide6・pyqtgraphの型情報が弱く、手間の割に効果が小さい）。
- 最初の指摘は一括で直す。直すのは挙動の変わらないものだけで、挙動が変わりうる指摘は1件ずつ判断する。
- 詳細は[52章](#52-lintruffの導入2026-10-13追加ユーザー判断)。

## 設計

### 3. 起動シーケンス

1. `tools/run.sh` / `run.bat` が `PYTHONPATH` に `src` を追加し、
   `uv run python -m parquet_analyzer` を実行。
2. `__main__.py`:
   1. `config.ensure_dirs()` を呼び、`cfg/data/log` 配下の `parquet_analyzer/` サブディレクトリを作成。
   2. `logging` を `config.PARQUET_ANALYZER_LOG_DIR` 向けに設定。
   3. `cfg/parquet_analyzer/settings.json`（無ければ既定値）を読み込み。
   4. `QApplication` を起動し `MainWindow` を表示。

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

**バージョン番号を書いている全箇所**——上げる際はすべて同時に更新する（`uv.lock`にも
記録されるため`uv lock`を実行する）:
- `pyproject.toml`の`version`（正）
- `src/parquet_analyzer/__init__.py`の`__version__`
- `README.md`のバージョンバッジ（`version-X.Y.Z-informational`）——0.2.0に上げた際に更新漏れが
  あったため、この一覧に追加した（2026-10-01に発見）。

### 21. 開発用の機能フラグ（2026-10-02追加、ユーザー要望）

要件: `specification.ja.md` 9章。背景: FFTをGUIから隠すために`io/settings.py`に
`FFT_ENABLED = False`の1行を置いていた（13.5章）が、FFT専用で他の機能に使えず、置き場所も
ユーザー設定のモジュールで性質が違う。新機能を「完成・レビューまではOFFでコミット」できる
汎用の仕組みにする。

#### 21.1 `src/parquet_analyzer/features.py`（新規）

- `config.py`と同じく**標準ライブラリのみ**に依存する（`io/`・`ui/`のどちらからも使うため）。
- `DEFAULT_FEATURES: dict[str, bool]` — 機能名 → 既定の有効/無効。初期値:
  | 名前 | 既定 | 対象 |
  |---|---|---|
  | `fft` | `False` | FFT（デバッグ未完。旧`FFT_ENABLED`） |
  | `independent_x_axis` | `True`（2026-10-02 レビュー・実機確認後にON） | 24章 X軸の独立 |
  | `xy_plot` | `True`（2026-10-03 レビュー・実機確認後にON） | 25章 段階1 |
  | `xyz_3d` | `True`（2026-10-03 レビュー・実機確認後にON） | 25章 段階2 |
  | `user_functions` | `True`（2026-10-03 レビュー・実機確認後にON） | 26章 |
  | `maps` | `True`（2026-10-03 レビュー・実機確認後にON） | 27章 |
  | `map_exclude_points` | `False`（2026-10-13、実機確認で分かりにくかったため隠す。ユーザー判断） | analysis 44.13 |
  | `big_a_thin_first_draw`・`big_c_windowed_derived`・`big_d_time_index` | `True`（2026-10-04 採用、data 36.8。普段使いで問題がなければフラグ自体を消す） | data 36章 A・C・D |
  各章の実装・レビューが済んだ時点で`True`にする（その変更自体はメインセッションが行う）。
- 環境変数`PARQUET_ANALYZER_FEATURES`で一時的に上書きできる: `名前=値`をカンマ区切り
  （例: `fft=on,xy_plot=on`）。値は`on/off/1/0/true/false/yes/no`（大文字小文字無視）。
  未知の名前・不正な値はその項目だけ無視して`logger.warning`。
- **frozen（PyInstallerのexe、`getattr(sys, "frozen", False)`）では環境変数を読まない**
  （配布物では既定値で固定。ユーザー回答 2026-10-02）。
- 実効値はモジュール読み込み時に1回だけ計算し`_EFFECTIVE: dict[str, bool]`に持つ
  （UIは起動時に組み立てるので、実行中の変更は想定しない）。
- `is_enabled(name: str) -> bool` — 未知の名前は`KeyError`（呼び出し側のタイプミスを
  黙って「無効」にしない）。
- `effective_features() -> dict[str, bool]` — コピーを返す。起動時に`logger.info`で一覧を
  出す（`MainWindow.__init__`）。
- テストからは`monkeypatch.setitem(features._EFFECTIVE, "xy_plot", True)`で切り替える
  （`MainWindow`を作る前に）。

#### 21.2 既存コードの移行

- `io/settings.py`の`FFT_ENABLED`を削除し、`ui/main_window.py`（ツールバー）・
  `ui/settings_dialog.py`（ショートカット欄）は`features.is_enabled("fft")`を使う。
  13.5章の記述（FFTを隠している件）はこの章を参照するよう更新する。
- フラグが止めるのは**UIの入口だけ**（ツールバーボタン・メニュー項目・ショートカット・
  設定欄）。計算処理・内部API・テストはフラグに関係なく存在し、実行される（FFTと同じ
  考え方）。`DEFAULT_SHORTCUTS`等の設定スキーマにも無効な機能のエントリを残す
  （有効/無効を切り替えても設定の移行が要らないように）。
- ユーザー向けの設定画面には出さない。

#### 21.3 テスト計画

- 既定値が上の表どおり。
- 環境変数: `fft=on` で有効、`fft=off` で無効、大文字・`1/0/yes/no`、未知の名前と不正な値は
  無視されて警告、空文字・未設定は既定値。
- frozen（`monkeypatch.setattr(sys, "frozen", True, raising=False)`）では環境変数を無視。
- `is_enabled("typo")` が`KeyError`。
- `fft`無効時にツールバーにFFTボタンが無く、設定画面にFFTのショートカット行が無い
  （既存テストの置き換え）。有効時はある。
- （環境変数を読むのは読み込み時なので、テストでは`features`モジュールの解析関数
  `_parse_overrides(text)`/`_compute_effective(env, frozen)`を直接呼んで確かめる形にする。）

### 30. 操作ログの充実: 操作と結果の記録（2026-10-02追加、ユーザー要望）

要件: 5.23。背景: 11.1の操作ログはデバッグモードでしか記録されず、内容も「何を操作したか」
だけで、結果（画面の状態がどう変わったか）が残らない。実機で報告された「系列を削除した後に
ナビゲータの範囲が勝手に広がる」不具合は、操作の後の状態の変化が記録されていれば、ログを読む
だけで分かった。

#### 30.1 ログの形式と保存

- 操作ログは**常に**`log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log`に書く。
  1行に1件の**JSON Lines**（機械で読めて、`grep`もしやすい）。
- 共通の項目: `ts`（ISO 8601、ミリ秒まで、**タイムゾーンのオフセット付き**）、`seq`（起動からの
  通し番号。起動ごとに1から振り直すので、一意になるのは`session`との組）、`kind`、`op`（その出来事が
  属する操作の番号。操作そのものは自分の`seq`）、`session`（起動ごとのID）。
- `kind`の種類:
  - `op`: ユーザーの操作。`action`と`details`（今の`log_op`の引数）。
  - `result`: 操作の結果。`state`（状態のまとめ、30.2）と`changes`（直前の状態からの差分）。
  - `job`: 裏の処理。`job`（種類: downsample/pyramid/stats/fft/今後の解析）、`event`
    （start/finish/fail）、`target`（プロット・系列・変数）、`ms`（かかった時間）、失敗時は`error`。
  - `message`: 利用者に出したメッセージ。`level`（info/warning/error）、`where`（dialog/status）、`text`。
  - `exception`: 予期しない例外。`type`、`message`、`traceback`。
  - `session`: 起動・終了（アプリの版、機能フラグ、Python/Qt/OSの版、デバッグモードか）。
- **保存期間**: `Settings.log_retention_days`（**既定7日**。1〜365の整数。範囲外・不正な値は既定に
  戻す）。起動時に、ファイル名の日付が保存期間より古い**操作ログと通常のログの両方**
  （`parquet_analyzer_{日付}.log`・`parquet_analyzer_ops_{日付}.log`）を削除する。今日のファイルは
  保存期間に関係なく消さない。名前の形が違うファイルには触れない。（2026-10-02、ユーザー指定:
  当初案の14日・操作ログのみから変更。）
- **設定画面**（9.1）に「ログの保存期間（日）」の欄（`QSpinBox`、1〜365）を追加する。変更は
  次回の起動時の削除から効く（設定を適用した時点では削除しない）。i18nは`settings.log_retention`。
- 1件あたりの大きさを抑える（系列名の一覧などは最大50件で打ち切り、`"...(+N)"`と書く）。
- 書き込みの失敗（ディスク満杯等）はアプリを止めない（1回だけ通常のログに警告を残し、以後は黙る）。
- 通常のログ（`parquet_analyzer_{YYYYMMDD}.log`）は今までどおり。デバッグモードでは操作ログの
  内容を読みやすい1行の形でコンソールにも出す（今と同じ）。

#### 30.2 操作の結果の記録

- `core/oplog.py`に**状態の提供元**（`set_state_provider(fn)`）を登録できるようにする。
  `MainWindow`が`state_snapshot() -> dict`を登録する。`core/`は画面を知らないまま、状態を記録できる。
- 状態のまとめ（`state`）の内容:
  - ファイル（パス・行数・時間軸の列・時間軸の並び順の検査結果）。
  - プロットごと: ID・時間領域か・X軸連動・X範囲・Y範囲・高さの比率・系列
    （名前・表示/非表示・選択中か・描画中の点の数・粗い表示か）。
  - ナビゲータの選択範囲、アンカー、カーソルの位置、描画中の処理（`_busy_sources`）、
    表示中の警告（時間軸の並び順）・通知、機能フラグ。
  - 時刻は数値（エポック秒）に加えて人が読める形（`"09:01:00.000"`）も入れる。人が読める形は
    **画面と同じローカル時刻**（`datetime.fromtimestamp`。レビューで修正: 最初の版はUTCで、
    画面の時刻と9時間ずれていた）。`session`レコードにUTCとの差を残す。
- 記録のタイミング: `log_op`が呼ばれたら、その操作の**結果待ち**にする。イベントループに戻った後、
  `_busy_sources`が空になり少し静かになった時点（通常150ms、続けて起きる操作は300ms）に
  `result`を1件書く。最後の関連する出来事から3秒経っても落ち着かなければ`"settled": false`で書く。
- **子の操作**（実装時に決定・レビューで整理）: 結果待ちになってから**まだイベントループに
  一度も戻っていないうちに**記録された操作は、その操作の子として`parent`（親の操作の`seq`）を
  付けて書き、自分の結果は持たない。ボタンの処理の中で続けて起きる操作（例: 「開く」→
  ファイル読み込み、右クリックメニューの要求→実際の削除）や、操作に伴ってプログラムから起きる
  範囲の変化がこれに当たる。利用者の次の操作の前には必ずイベントループが回るので、本当の
  操作が子として飲み込まれることはない。
  - ズーム・パン・ナビゲータのドラッグのように続けて起きる操作は、最後の操作から300ms経ってから
    まとめて1件にする（全部の途中経過で状態を記録しない）。
  - 結果待ちの間に次の操作が来たら、前の操作の結果をその時点の状態で書いてから次へ進む。
    その時点でまだ描画中なら`"settled": false`と`"interrupted_by": <次の操作のseq>`を付ける
    （レビューで修正: 描画中でも「落ち着いた」と書いていたため、前の操作の変化が次の操作の
    変化として記録されていた）。
  - まとめた連続操作の結果は、最後の操作の`seq`を`op`に持ち、`merged`（件数）と`first_op`を付ける。
    デバッグモードでないときは、まとめた連続操作の`op`の行は最初と最後だけ書く（量を抑える）。
- `changes`: 直前の`result`の状態との差分を、項目のパスと前後の値で記録する
  （例: `{"navigator.region": [[60.0, 120.0], [12.6, 642.6]], "plots.p1.series": [["a","b"],["a"]]}`）。
  浮動小数の差は相対1e-9未満なら変化なしとみなす。

#### 30.3 裏の処理・メッセージ・例外

- `BackgroundWorker`の失敗は28章のとおり中で捕まえて`failed`を出し、`job`の`fail`を1回だけ
  記録する（`exception`としては記録しない）。
- `BackgroundWorker`（28章）に、開始・完了・失敗のときに`job`を記録する仕組みを入れる
  （呼び出し元が種類と対象を渡す。どの操作がきっかけかは、開始時点で結果待ちの操作の番号）。
  ダウンサンプリングは頻繁なので、平常時は失敗だけを記録し、完了はデバッグモードのときだけ記録する。
- `QMessageBox`での通知（警告・エラー・情報）と、ステータスバーの通知（29章の通知ラベル、時間軸の
  警告）を表示するとき、`message`を記録する（表示する関数を1か所にまとめて、そこで記録する）。
- 予期しない例外: `sys.excepthook`と`threading.excepthook`を設定し、Qtのスロット内で捕まえ
  られなかった例外も`exception`として記録する（通常のログにも`ERROR`で残す）。アプリは止めない。

#### 30.4 読む道具・メニュー

- `tools/show_ops_log.sh <ファイル>`（`core/oplog_view.py`を呼ぶ）: 操作ログを人が読みやすい
  時系列で表示する。操作の下に、その結果の`changes`と、その操作に属する`job`/`message`/`exception`を
  字下げして並べる。`--last N`（最後のN操作だけ）、`--session`（最後の起動だけ）。
- 設定メニュー（または「ヘルプ」）に「ログのフォルダを開く」（`QDesktopServices.openUrl`）を追加する。
- 不具合の報告手順（README・開発ルール）: 「普通に起動して再現 → ログのフォルダから当日の
  `*_ops_*.log`を渡す」に変える（デバッグモードでの再現は不要になる）。

#### 30.5 性能

- 状態のまとめは、プロット数×系列数に比例する軽い処理にする（データの配列には触れない）。
  1回あたり5ms未満を目安に、テストで測る。
- ログの書き込みは、まとめて書く（行ごとに`flush`しない。ただし`exception`と`session`の終了は
  すぐ`flush`する）。

#### 30.6 テスト計画

- 形式: 各`kind`がJSONとして読め、共通の項目がそろう。`seq`が増え続け、`op`が操作の番号を指す。
- 結果の記録: 実際の`MainWindow`で、ファイルを開く・系列を追加・ズーム・系列を削除・ビューを読み込む、
  の各操作の後に`result`が1件ずつ書かれ、`changes`に期待どおりの差分が入る（例: 系列を削除すると
  `plots.<id>.series`の差分が出る。23章のブランチで起きたナビゲータの範囲の変化のような
  不具合なら`navigator.region`の差分として現れることを、わざと範囲を変える操作で確かめる）。
- 続けて起きるズームは1件にまとまる。結果待ちの間に次の操作が来ても、前の操作の`result`が先に書かれる。
- 裏の処理: 失敗が`job`の`fail`として、きっかけの操作番号付きで記録される。
- メッセージ: 警告ダイアログ・ステータスバーの通知が`message`として記録される。
- 例外: スロット内の捕まえられない例外が`exception`として記録され、アプリは動き続ける。
- 保存: 保存期間より古い通常のログ・操作ログが削除され、今日のファイル・期間内のファイル・名前の
  形が違うファイルは残る。既定が7日。設定画面で変えた値が保存され、次回の起動で使われる。
  範囲外の値は既定に戻る。書き込み失敗でアプリが止まらない、デバッグモードでなくても記録される。
- 読む道具: サンプルのログから期待どおりの時系列の表示になる。
- 性能: 状態のまとめが1回5ms未満（プロット4・系列各3）。

### 45. 操作の副作用のテスト: 関係のない機能が動かないこと（2026-10-06追加、ユーザー要望）

要求は5.36。テストだけを足す章で、アプリの動作は変えない（見つかった不具合は別の作業として直す）。

#### 45.1 しくみ

- 新しいテストファイル`tests/parquet_analyzer/test_side_effects.py`と、そこで使う小さな道具
  （同じファイルの中、または`tests/parquet_analyzer/side_effects_helper.py`）。
- **始めの状態**（毎回の`MainWindow`で同じものを作る。`sample_parquet_path`、`isolated_config`を使う）:
  - プロット2枚（どちらも時間領域・X軸連動）。1枚目に生の変数2本と仮想変数1本（マップを使う式、
    例: `sample_map(a, b)`の形。マップは`tmp`に作って登録する）、2枚目に生の変数1本。
  - **範囲はホームではなく途中**（全体の20〜60%あたりにズーム）。ここが動いたかどうかを見るため。
  - カーソルON、カーソル1本を範囲の中に置く。値の表示（hover）は設定の既定のまま。
- **1つの操作の確かめ方**:
  1. 状態を取る（`MainWindow.state_snapshot()`）。
  2. 操作を**本物の入口から**起こす: ツールバーは`QAction.trigger()`、メニューの項目は
     そのメニューの`QAction`、ボタン・凡例・ナビゲータは`QTest`のマウス操作（Qtの本当の経路、
     開発ルールのテストの決まり）。ダイアログは全部monkeypatchで決まった答えを返す（下の表）。
  3. 落ち着くまで待つ: `_busy_sources`が空で、操作ログの結果待ちが無くなるまで
     `processEvents`を回す（上限5秒。超えたら失敗）。
  4. もう一度状態を取り、`core.oplog.diff_states(before, after)`で差分を取る。
  5. 差分の各項目のパスが、その操作の「変わってよい項目」（パスのパターン、`fnmatch`形式。
     例: `plots.*.series_info.*.points`）のどれかに当たるかを確かめる。当たらない項目があれば、
     操作名・パス・前後の値を並べて失敗にする。
  6. 追加の見張り（全操作共通）:
     - **ファイルの読み直し**: `MainWindow.load_parquet`と`DataSource`の作り直しが呼ばれない
       （「開く」「ビューを読み込む」「時間軸の変更」だけは除く）。monkeypatchで数える。
     - **予期しないダイアログ**: その操作の表に無い`QMessageBox`・`QInputDialog`・`QFileDialog`が
       出たら失敗（全部monkeypatchして呼ばれた記録を取る）。
     - **例外**: 操作ログの`exception`レコードが増えない（`log`の仕組み、30章）。
- 差分の浮動小数の扱いは操作ログと同じ（相対1e-9未満は変化なし、30.2）。
- **テストの時間**: 全体で30秒以内を目安にする（小さなサンプル、操作ごとに新しいウィンドウ）。
  遅ければ、始めの状態を作る部分を速くする（ウィンドウを使い回すと前の操作が残るので使い回さない）。

#### 45.2 操作と「変わってよい項目」の表

`*`はプロットID・系列名などの任意の1段。ここに無い項目（`file.*`、ほかのプロットの`x_range`、
`navigator.region`、`anchor`、`cursors.*`、`features`など）は変わってはいけない。

| 操作（入口） | ダイアログの答え | 変わってよい項目 |
|---|---|---|
| ホーム | — | `plots.*.x_range`、`plots.*.y_range`、`navigator.region`、`plots.*.series_info.*.points`・`coarse` |
| 再描画 | — | `plots.*.series_info.*.points`・`coarse`、`plots.*.y_range` |
| ＋プロットの追加 | — | `plots.<新しいID>`（新しいプロットの項目一式）、`plots.*.height_ratio` |
| 統計 | ウィンドウを開くだけ | なし |
| グラフ ▾ → XY・3D・ヒストグラム（有効な機能フラグのもの） | ウィンドウを開くだけ | なし |
| 関数管理を開いて閉じる | 何も変えずに閉じる | なし |
| 関数管理: プロットに使われていない関数を追加して保存 | 名前・式を決まった値で | なし |
| マップ管理を開いて閉じる | 何も変えずに閉じる | なし |
| マップ管理: プロットの仮想変数が使うマップの値を1つ変えて保存 | — | その仮想変数の`series_info.*.points`・`coarse`、そのプロットの`y_range` |
| マップ管理: 同じマップをファイルから再読み込み | — | 同上 |
| ビューの保存 | 名前を決まった値で、「保存しました」の知らせ（`QMessageBox.information`）は出てよい | なし |
| 画像保存 | 保存先を`tmp`のパスで | なし |
| 粗い表示の切り替え | — | `downsample_enabled`、`plots.*.series_info.*.points`・`coarse` |
| カーソルの切り替え | — | `cursors.*` |
| 値の表示の切り替え | — | `hover_values` |
| 設定を開いてOK（何も変えない） | — | なし |
| 選んだ系列の削除（Delキー） | — | 選んだ系列のプロットの`series`・`series_info.<その系列>`・`y_range`、`plots.*.series_info.*.selected` |
| 凡例で系列を非表示 | — | その系列の`visible`・`points` |
| 式の欄から仮想変数を追加（プロットには足さない） | — | なし |
| 式の欄の仮想変数をプロットに追加 | — | そのプロットの`series`・`series_info.<その系列>`・`y_range` |
| ナビゲータの範囲をドラッグ | — | 連動するプロットの`x_range`・`y_range`・`points`・`coarse`、`navigator.region` |
| 解析ウィンドウ（統計・XY・ヒストグラム）の「再計算」 | — | なし（メイン画面の状態は変わらない） |

- 「開く」「ビューを読み込む」「時間軸の変更」は全体が変わる操作なので、この表には入れない
  （それぞれの章のテストで確かめる）。
- 機能フラグがOFFの機能（`fft`など）の入口は飛ばす（`pytest.skip`ではなく表から外す）。
  フラグをONにした時点で表に入れる。
- **表と実際が合わないとき**: 実装の担当は、表に合わせてテストをゆるめたり、アプリのコードを直したり
  しない。合わない操作・項目・前後の値を報告し、不具合か表の誤りかをmainセッションが判断する。
  不具合と判断したものは`pytest.mark.xfail(strict=True, reason="開発メモの項目<番号>")`にして、
  直したときに自動で気づけるようにする（例: マップの保存・再読み込みでナビゲータの範囲が全体に戻る件 = 開発メモの項目14。
  実装時に見つかり、14の修正（plots 24.1）で直ったのでxfailは外した）。

#### 45.3 テスト計画

- 表の各行が1つのテスト（`pytest.mark.parametrize`、IDは操作名）。
- 見張りが効くことの確認（このしくみ自体のテスト）:
  - わざと範囲を動かす偽の操作（`navigator.region`を変える）を「変わってよい項目: なし」で流すと
    失敗し、メッセージに`navigator.region`と前後の値が出る。
  - わざと`load_parquet`を呼ぶ偽の操作、表に無い`QMessageBox.warning`を出す偽の操作が、それぞれ失敗になる。
  - パターンの当たり方: `plots.*.x_range`が`plots.p1.x_range`に当たり、`plots.p1.series`には当たらない。
- 全体のテストの時間が30秒程度に収まる（収まらなければ報告）。

### 46. テストの強化: ランダム検証・失敗経路・窓読み込み・ビューの後方互換（2026-10-08追加）

要求は5.38。テストだけを足す章で、アプリのコード（`src/`）は変えない。テストが落ちたら、テストをゆるめたり
アプリを直したりせず、入力（hypothesisが縮めた最小の例）・期待・実際を報告する。mainセッションが不具合か
テストの誤りかを判断し、不具合なら`pytest.mark.xfail(strict=True, reason="開発メモの項目<番号>")`にして別の作業で直す。

#### 46.1 共通: hypothesisの設定

- devの依存に`hypothesis`を足す（`pyproject.toml`の`[dependency-groups] dev`、`uv.lock`も更新）。アプリの依存には足さない。
- 設定は`tests/parquet_analyzer/conftest.py`でプロファイルとして登録する:
  - `default`（既定）: `derandomize=True`（毎回同じ入力。CI・並列実行で結果が揺れない、xdistのIDも安定）、
    `max_examples`は各テストで決めた上限（下の各節。目安50〜200）、`deadline=None`（CIのUbuntuが遅いため、
    1例の時間で落とさない）、`database=None`（`.hypothesis/`を作らない）、`print_blob=True`。
  - `thorough`: `derandomize=False`、`max_examples`を10倍。手で多めに試すとき
    `HYPOTHESIS_PROFILE=thorough uv run pytest tests/parquet_analyzer/test_property_*.py`。
  - 環境変数`HYPOTHESIS_PROFILE`で選ぶ（無ければ`default`）。
- `.hypothesis/`を`.gitignore`に足す（`thorough`で作られるため）。
- 固まったとき: timeoutプラグインは使わない。既存の`faulthandler_timeout = 120`に任せる。
- **実行時間**: A1で足すテスト全体で、並列なし（`-n 0`）でも**15秒以内**を目安にする。超えたら`max_examples`や
  データの大きさを下げ、報告に実測を書く。
- ファイルは`tests/parquet_analyzer/test_property_<対象>.py`（対象ごとに1ファイル）。
- データの作り方: 大きな配列（数万行）はhypothesisに要素ごとに作らせない（遅く、縮小も効かない）。hypothesisには
  **形を決める少数の値**（長さ・重複の割合・NaNの位置の種類・乱数の種など）を引かせ、配列はnumpyの乱数
  （`np.random.default_rng(seed)`）で組み立てる。小さな配列（〜数百）はhypothesisの`arrays`でよい。

#### 46.2 A1: ランダムデータでの性質テスト

| 対象 | 確かめる性質 | 入力の作り方・注意 |
|---|---|---|
| 式の評価器（`core/expression.py`の`evaluate_expression`） | (1) **でたらめな文字列**（hypothesisの`text()`と、式の部品—変数名・数・演算子・括弧・関数名・`if`/`else`/`and`/`or`/`not`・比較・`,`—をでたらめに並べた文字列の2通り）を入れても、外に出る例外は`ExpressionError`（とその派生）だけ。値が返るときは`np.ndarray`またはスカラー。(2) **正しい式**（部品から文法どおりに組み立てた式。四則・`**`・単項`-`・`abs`/`sqrt`/`sin`/`exp`/`log`/`min`/`max`/`clip`/`where`/`isnan`・比較・条件式）の結果が、同じ式をnumpyで直接計算した結果と一致する（`np.testing.assert_allclose`、`equal_nan=True`、`rtol=1e-12`。式を組み立てるときにnumpyの計算も一緒に組み立てる） | 変数は2〜3個、長さ1〜50の`float64`（NaN・±Inf・0・負を含む）。`**`の指数は小さな整数か`0.5`に限る（大きな冪のあふれは結果が`inf`/`nan`で一致すればよい）。式の深さに上限（5程度）。ユーザー関数・マップは対象外（registryは`None`）。`max_examples`: (1) 300、(2) 200 |
| `TimeIndex.searchsorted`（`core/time_index.py`） | `TimeIndex.searchsorted(v, side)` == `np.searchsorted(全配列, v, side)`（`side`は`left`/`right`の両方、`v`はスカラーと配列の両方） | 時間軸は**昇順・有限**（`TimeIndex.build`は昇順でない・非有限の列では`None`を返すので対象外。`None`になることも1例で確かめる）。長さは`ANCHOR_ROWS`（8192）の0.5〜4倍、**重複の連続**（同じ値が数千行続き、アンカーの境界をまたぐ場合を含む）。`v`は配列の値そのもの・値の間・最小未満・最大超・アンカーの値・`±inf`・`nan`。Parquetは`tmp_path`に書いて`ParquetDataSource`で開く（`pin_rows`を少し渡す場合も）。ファイルを書くので`max_examples`は30程度、1例の中で多数の`v`を試す |
| `chunked_stats`（`core/analysis.py`） | `chunked_stats(window, lo, hi, chunk_rows, bins)`が`basic_stats(y[lo:hi])`と一致: `count`・`nan_count`・`min`・`max`・`median`は**完全一致**、`mean`・`std`は`rtol=1e-9`（ほぼ0の値には`atol`を付ける）。`window`は`lambda a, b: y[a:b]` | 長さ0〜5万、`chunk_rows`は1〜長さ超（小さい値で多数のチャンクにする）、`bins`は2〜`1<<16`。値: 一様・同じ値だらけ（全部同じ、ほとんど同じ）・±1e308付近・非正規化数（5e-324付近）・NaN/±Infを混ぜる・全部非有限。`lo`/`hi`は範囲内のでたらめな位置（`lo == hi`を含む）。`max_examples` 200 |
| LTTB（`core/downsample.py`の`lttb`） | 出力の点数 ≤ `max(n_out, 3)`（入力がそれ以下なら入力をそのまま返す）、先頭と末尾が入力の先頭と末尾、出力の各点(x, y)が入力のどれかの点と同じ（同じ添字の組）、出力のxが入力の順序を保つ（入力xが昇順なら出力も昇順） | 長さ0〜2万、`n_out`は0〜長さ+10。xは昇順（重複あり）、yにNaN/±Infを混ぜる場合も。入力に非有限値があっても例外にならない。`max_examples` 200 |
| `rolling_mean`/`rolling_nanmean`（`core/analysis.py`） | 素直な実装（各点で窓の範囲をPythonで切り出して平均。窓の範囲は31.1: 出力iは`y[i-⌊n/2⌋]`〜`y[i+⌈n/2⌉-1]`を端で切り詰めた範囲）と一致（`rtol=1e-9`、`atol=1e-12`、`equal_nan=True`）。`rolling_mean`は窓に1つでもNaNがあれば結果はNaN、`rolling_nanmean`は窓が全部NaNの点だけNaN | 長さ0〜300、窓幅1〜長さ+5（長さを超える窓は長さに切り詰め）、NaNを混ぜる・整数の配列。値の大きさは±1e6程度に抑える（畳み込みの桁落ちで素直な実装とずれるため。大きな値での精度は別の話として扱わない）。`max_examples` 200 |
| `row_range_for_x`（`core/data_source.py`） | 返した行の範囲`[s, e)`が、時間軸の値が`[x_lo, x_hi]`に入る行を**全部含む**（行グループ単位なので多めでよい）。重なる行が無ければ`(0, 0)`。`x_lo > x_hi`・NaNの境界は`(0, 0)` | 昇順（重複あり）の時間軸を、行グループの大きさをでたらめに変えてParquetに書く（`row_group_size`）。`x_lo`/`x_hi`は値・値の間・範囲外・NaN。ファイルを書くので`max_examples` 30、1例の中で多数の範囲を試す |

- 既存のテスト（`test_time_index.py`・`test_stats.py`・`test_downsample.py`・`test_expression*.py`）は消さない・変えない。
- 縮小した最小の例で落ちたら、そのまま報告する（46章の冒頭）。

#### 46.3 A2〜A4（着手の前に詳しく書く）

- **A2 裏の処理の失敗経路**: 46.5に詳しく書いた（2026-10-08）。
- **A3 窓読み込みの列**: 46.6に詳しく書いた（2026-10-08）。
- **A4 ビューの後方互換**: 46.7に詳しく書いた（2026-10-08）。

#### 46.4 テスト計画

- 46.2の表の各行が1つ以上のテスト。hypothesisの`@given`、設定は46.1のプロファイル。
- しくみ自体の確認: `default`プロファイルで2回続けて流して同じ結果（`derandomize`）。`-n auto`でも収集のIDが
  そろう（xdistが止まらない）。
- 全体のテスト（`uv run pytest -q -n auto`）が通り、増えた時間が数秒に収まる。A1のテストだけの実行時間
  （`-n 0`）を報告に書く。

#### 46.5 A2: 裏の処理の失敗経路の網羅（2026-10-08、開発メモの項目25）

目的: `WorkerHub`（data 37章）の「結果か失敗のどちらかを必ず1回、受け取ったら後片付け」（overview 2章、data 28章）が、
**どの持ち主でも・どの出来事でも**守られていることを表で確かめる。テストだけを足し、`src/`は変えない（46章の冒頭のとおり、
落ちたら報告してxfail）。

**持ち主（列）**（2026-10-08の`main`。`submit`の場所）

| 記号 | 持ち主・ジョブ | 場所 | 受け取り（成功 / 失敗） |
|---|---|---|---|
| D | 系列の間引き（最初の描画`_on_thin_drawn`と通常`_on_downsampled`） | `plot_widget.py`の`_hub` | 同左 / `_on_series_worker_failed` |
| P | ピラミッドの構築 | `plot_widget.py`の`_hub` | `_on_pyramid_ready` / `_on_pyramid_failed` |
| N | ナビゲータの概形（生の列・派生変数） | `navigator_sync.py`の`_hub` | `_on_navigator_overview_ready`・`_on_derived_overview_built` / `…_failed` |
| S | 統計 | `main_window.py`の`_stats_hub` | `_on_stats_finished`（失敗は`{"error": …}`） |
| F | FFT（機能フラグ`fft`はOFFだが処理は動く。テストはフラグに関係なく呼ぶ） | `main_window.py`の`_fft_hub` | `_on_fft_finished` / `_on_fft_failed` |
| A | 解析ウィンドウ（XY・ヒストグラム・XYのマップ。22がマージされたらマップの点を外す四角のジョブも） | `analysis_window.py`（`main._analysis_hub`） | `_on_finished` / `_on_failed` |

**出来事（行）**: ジョブが走っている途中（下の「止める道具」で止めておく）に起きること。

| 記号 | 出来事 |
|---|---|
| 1 | 成功（そのまま終わる） |
| 2 | 例外（処理の関数が例外を出す） |
| 3 | 新しいジョブで古くなる（同じ持ち主に次の要求: ズーム・再計算・範囲の変更。世代番号で古い結果を捨てる） |
| 4 | 受け取り先が消える（系列の削除・プロットの削除・解析ウィンドウを閉じる） |
| 5 | 別のファイルを開く |
| 6 | 時間軸を切り替える |

**各マスで確かめること**（当てはまるものだけ）:
- 落ちない・固まらない（落ち着くまで実時間で待つ。上限を決めて超えたら失敗。CIのUbuntuは遅いので`sleep`を挟む）。
- 受け取りは**ちょうど1回か0回**（古い・消えた受け取り先は0回でよい）で、古い結果が画面・状態に**使われない**
  （例: 3で古い間引きの点が描かれない、5で前のファイルの統計が出ない）。
- 後片付け: 「描画中」「計算中」の印（`_busy_sources`等）が消える、`hub.pending`が空になる（強参照が残らない）。
- 失敗（2）では、利用者向けの知らせ・ログが期待どおり1回（同じ失敗で何度も出ない）。
- 操作ログに`exception`の記録が増えない（2の想定した失敗は`job`の失敗として記録されるだけ）。

**進め方**:
1. まず既存のテスト（`test_worker_release.py`・`test_worker_failure_and_time_order.py`・`test_worker_failure_followups.py`・
   `test_ui_stress.py`・`test_big_*.py`・`test_navigator_*.py`・`test_xy*.py`・`test_histogram*.py`ほか）を読み、上の表の
   各マスを埋めているテストを書き出す（**表をテストのファイルの先頭のdocstringに置く**: マスごとに「既存: ファイル::テスト名」か
   「新規: テスト名」か「対象外: 理由」）。
2. 空いたマスのテストを`tests/parquet_analyzer/test_worker_paths.py`に足す（持ち主×出来事を`parametrize`、IDは`D-3`のような記号）。
3. **止める道具**: 処理の関数（`lttb`・`build_pyramid`・`thin_column`・`chunked_stats`/`basic_stats`・`fft`・解析の計算関数）を
   monkeypatchで包み、`threading.Event`で止める（走り出したことも`Event`で知らせる）。止めている間に出来事を起こし、
   放してから落ち着くまで待つ。2は包んだ関数で例外を出す。プールのスレッドを止めたままテストを終えない（失敗時も`finally`で放す）。
4. 当てはまらないマス（例: Fの4、Pの6でピラミッドを作り直さない場合）は「対象外」と理由を書く。

**テスト計画**: 表の全マスが「既存」「新規」「対象外」のどれかになっている。新規のテストは全て通るか、不具合ならxfail（報告）。
全体のテストの時間の増えは10秒以内（止める時間を短く、データは小さく）。

**S-5・F-5の決定（2026-10-13、ユーザー判断、開発メモの項目31）**: 統計・FFTのジョブの途中で**別のファイルを開いたら、
前のファイルの結果は捨てる**（統計のダイアログを出さない・スペクトルのプロットを足さない）。
- `MainWindow`に`_file_generation: int`（0で始まる）を持ち、`load_parquet`が**成功して新しいファイルに切り替えた時点**で1増やす
  （読み込みに失敗したときは増やさない。前のファイルのまま）。
- `_on_stats_requested`・`_on_fft_requested`は要求の時点の`_file_generation`を受け取り口（`lambda`）に渡す。
  `_on_stats_finished`・`_on_fft_finished`・`_on_fft_failed`は、渡された世代が今と違えば**「計算中」の印の後片付けだけ**して
  捨てる（ダイアログ・プロット・失敗の知らせを出さない）。統計は1回の要求の全変数を1まとまりで捨てる。
  捨てたことは`logger.info`に残す（操作ログの`job`の記録は`WorkerHub`が今どおり残す）。利用者向けの知らせは出さない
  （自分で別のファイルを開いたので、結果が出ないのは自然なため）。
- 時間軸の切り替え（S-6・F-6）は今どおり「要求した時点の結果」として出す（同じファイルの値なので）。
- テスト: `test_worker_paths.py`の`test_stats_result_of_previous_file_is_not_shown`・`test_fft_result_of_previous_file_is_not_added`の
  `xfail(strict)`を外して通す。足すもの: 失敗（例外）の結果が前のファイルのものなら警告を出さない（F、統計の`{"error"}`も）、
  読み込みに失敗したファイルを開いた場合は世代が変わらず結果が出る、「計算中」の印・`hub.pending`が空になる。
  S-6・F-6の既存テストは今どおり通る。

#### 46.6 A3: 窓読み込みの列の一致・全件読みの見張り（2026-10-08、開発メモの項目26）

46章の冒頭のとおりテストだけを足し、`src/`は変えない（落ちたら報告、不具合ならxfail(strict)）。

**(1) 一致のテスト**（`tests/parquet_analyzer/test_property_windowed.py`、hypothesis、46.1のプロファイル）

答え（オラクル）は**調べる対象のクラスを通さずに**作る: pyarrow/polarsで列を丸ごと読んだ配列（`to_numeric`で
数値にしたもの）と、式は同じ全体の配列での`evaluate_expression`の結果。

| 対象 | 確かめること | 入力の作り方 |
|---|---|---|
| `ParquetDataSource.read_window`・`WindowedColumn.window` | でたらめな`(lo, hi)`の**並び**（同じ範囲の繰り返し・隣り合う範囲・ブロック境界をまたぐ範囲・`lo == hi`・`hi > 行数`・`lo < 0`を含む）を順に読んで、毎回`全体[lo:hi]`と完全一致（NaNの位置も）。複数列を一度に読む`read_window`も | `ParquetDataSource(path, block_rows=小, cache_blocks=小)`（例: 1000行・2〜4ブロック。キャッシュに入っている/追い出された両方が起きる）。行数はブロックの0.5〜20倍、行グループの大きさもでたらめ。列は`float64`・整数・NaN入り。ファイルを書くので`max_examples` 30、1例で多数の範囲 |
| `DerivedColumn.window`（data 36.6 C） | `make_derived_column`で作った列の`window(lo, hi)`が全体の計算の`[lo:hi]`と一致（`assert_allclose`、`rtol=1e-12`、`equal_nan=True`。マージンを持つ関数は端の行も） | 式は、要素ごと（`a*2+b`）・`delta(a)`・`rolling_mean(a, n)`・`rolling_nanmean(a, n)`・それらの入れ子（`delta(rolling_mean(a, 5))`）・別の`DerivedColumn`を入力にしたもの。入力は`WindowedColumn`（上と同じ小さなブロック）。`n`は1〜行数超。`make_derived_column`が`None`を返す式（マージンが分からない）は対象外として1例で確かめる。`max_examples` 50 |
| `TimeIndex.window`・`value_at` | `window(lo, hi)`が全体`[lo:hi]`、`value_at(r)`が全体`[r]`と一致（範囲の外・`lo == hi`も） | 46.2の`TimeIndex.searchsorted`と同じ時間軸の作り方（昇順・重複の連続・アンカーの境界）。`max_examples` 30 |

- 28（`trailing_*`）が先に`main`に入っていれば、`DerivedColumn`の式に`trailing_mean(a, n)`も入れる（入っていなければ入れない。
  28の側で足す）。

**(2) 見張りのテスト**（`tests/parquet_analyzer/test_no_full_read.py`）

窓読み込みになる列で、普段の操作の間に**全件読み**が起きないことを確かめる。

- 準備: `isolated_config`、`win.settings.eager_load_limit_mb = 0`（既存の`test_windowed_loading.py`と同じ。全部の列が
  `WindowedColumn`、時間軸は`TimeIndex`になることを先に確かめる）。データは数十万行（ブロック100,000行を複数またぐ）の
  小さな列のParquetを`tmp_path`に書く。
- 数え方: monkeypatchで`WindowedColumn.values`・`DerivedColumn.values`・`MaterializedColumn`への昇格（あれば）と、
  `ParquetDataSource.read_window`のうち**行数の半分を超える範囲の読み込み**を数える（呼んだ場所のスタックを1行ずつ記録して、
  失敗したときにどこから呼ばれたかを報告に出す）。裏のスレッドから呼ばれても数えられるよう、数える側はロック付き。
- 見張る操作（それぞれ、裏の処理が落ち着くまで実時間で待ってから数を見る。parametrizeで1操作1テスト、IDは操作名）:
  1. ファイルを開いて2列を2つのプロットに描く（最初の描画・ナビゲータの概形・ピラミッドの構築を含む）
  2. ズーム（プロットのX範囲を全体の1%に）・パン
  3. ナビゲータの枠のドラッグ（`windowHandle()`にドラッグのイベントを送る、または既存のテストと同じ枠の移動の経路）
  4. ホバー（ビューポートにマウス移動 → 100 msの止まりの後の1行の読み込み。plots 39章）
  5. カーソルを置いて動かす
  6. 時間軸の切り替え（別の昇順の列へ、そして戻す）
  7. 窓読み込みになる派生変数（`delta(a)`・`rolling_mean(a, 50)`）を足して描く（`DerivedColumn`になることを確かめる。
     範囲の`bounds()`のための**チャンクに分けた**走査は全件読みに数えない＝`values()`と半分超の`read_window`だけを数える）
- 対象にしない（今の時点で全件を読む所。ここに書いて理由を残す）: 
  `DerivedColumn`にならない式（マージンが分からない・ユーザー関数の一部）の評価、ヒストグラム・XY・FFT、画像・データの書き出し。
  これらを減らすのは開発メモの項目29（C）。全体の統計は29の段階1（data 53章）で窓ごとの集計にしたので対象に入れた（操作8）。
- 見張りが全件読みを見つけたら、どの操作・どこから呼ばれたかを報告し、mainセッションが判断する（46章の冒頭）。

**実装時の仕様との差（2026-10-08）**:
- 見張りのテストは、`plot_widget._PYRAMID_PREFERRED_ROW_THRESHOLD`（500万行）を5万行に**monkeypatch**して動かす。
  アプリは「見えている範囲が500万行以下」なら描画のために範囲を1回で直接読む（data 36.6）ため、30万行のファイルで
  全体が見えている状態は、本物の大きなファイルとは違い正当に全件を読む。しきい値を下げて大きなファイルの領域を再現した。
- 操作7の「チャンクに分けた走査は数えない」は、`read_window`の呼び出しスタックに`build_pyramid_from_windows`・
  `DerivedColumn.scan`・`bounds`があるものを除外して実現した（30万行ではチャンク1個が全体になるため、
  範囲の大きさでは区別できない）。
- 1つの操作（`values()`と`read_window`）が二重に数えられても、0かどうかだけを見るので問題ない。

**テスト計画**: 上の各表の行・各操作が1つ以上のテスト。全体のテストの時間の増えは10秒以内（`-n auto`）。見張りのテストが
「数えられている」ことを確かめるため、1つは**わざと`values()`を呼ぶ操作**（`WindowedColumn.values()`。2026-10-13までは全体の統計だった。data 53章）で数が1以上になることも確かめる
（数え方が壊れて常に0になるのを防ぐ）。

#### 46.7 A4: ビューの後方互換（2026-10-08、開発メモの項目27）

目的: 過去の版で保存したビューJSONを今のコードで読めることを、**その版のコードで実際に保存したファイル**で確かめる
（persistence 8・20・33・35章: 足りない項目は警告して飛ばす、知らないキーは無視、形の誤りは`ViewFormatError`）。

**版**（gitのコミット。タグは`v0.3.0`しか無い）:

| 版 | コミット | 備考 |
|---|---|---|
| 0.1.0 | `f5573e9^`（0.2.0に上げる直前、2026-09-30） | 0.1の最後の形 |
| 0.2.0 | `f5573e9`（2026-09-30） | |
| 0.3.0 | `v0.3.0`（`860303a`、2026-10-04） | 時間軸の列・元のファイルの相対パスを保存（33・34章） |

**作り方**（`tests/parquet_analyzer/data/views/make_old_views.py`に置き、テストのファイルの先頭のdocstringにも手順を書く）:

1. 一時的な`git worktree add <tmp> <コミット>`（作業後に`git worktree remove`。リポジトリの作業ツリーは触らない）。
2. その版の`src/`を`sys.path`の先頭に入れ、`QT_QPA_PLATFORM=offscreen`で`MainWindow`を動かして、決まったParquet
   （スクリプトが書く。列: `time`（昇順の秒）・`sine`・`cosine`・`ramp`・`flag`（0/1）、1,000行）を開き、**その版でできる操作を
   なるべく多く入れたビュー**を保存する: 複数のプロット・1つのプロットに複数の系列・派生変数（入れ子を含む）・系列の
   表示/非表示・Xの独立（その版にあれば）・時間軸の列（あれば）・ユーザー関数やマップ（あれば）・プロットの高さ・表示範囲。
   どの操作を入れたかをスクリプトの中に版ごとに書く。その版で無い機能の操作は飛ばす（`hasattr`等で判断）。
   保存は、その版の`MainWindow`のビュー保存の処理を呼ぶ（ダイアログはmonkeypatch）。ファイル名は`<版>/<名前>.json`。
3. 依存はその版の`uv.lock`のものでなくてよい（今の環境の`uv run python`で動けばよい）。動かない版があれば、その版は
   飛ばして報告する（手でJSONを書かない）。
4. 生成物（JSON）をコミットする。スクリプトはテストの実行時には走らせない（gitの操作が要るため）。

**テスト**（`tests/parquet_analyzer/test_view_backward_compat.py`）:

- 各JSON（`parametrize`、IDは`<版>/<名前>`）について: スクリプトと同じParquetを`tmp_path`に書いて今の`MainWindow`で開き、
  `load_view`する。確かめること: 例外が出ない、`ViewFormatError`にならない、プロットの数・各プロットの系列名・派生変数の
  名前と式・（あれば）時間軸の列・表示/非表示が保存したとおり。警告（`ui/messages`をmonkeypatchして数える）は**期待した
  ものだけ**（版ごとの期待をテストに表で書く。例: 今の版で消えた機能の項目）。期待は、まず今のコードで読んでみて、
  その結果が仕様（persistence章）どおりかをmainセッションが判断して決める（実装者は実際の結果を報告する）。
- ビューの管理画面（`view_manager.py`）の「足りない」印が、`load_view`が実際に飛ばしたものと一致する（不変条件）。
- 読み込んだ後に今の版で保存し直して、もう一度読むと同じ状態になる（往復）。
- 今後、ビューの形を変えたら、変える前の`main`で同じスクリプトを走らせてJSONを足す（スクリプトの先頭に書く）。

**テスト計画**: 3版×数ファイルが全部通るか、通らないものは報告（xfail(strict)にするかはmainセッションが判断）。

### 48. ツールバーの整理（2026-10-13追加、ユーザー要望）

要求は[5.41](#541-ツールバーの整理-2本に分ける文言まれな操作をまとめる2026-10-13追加ユーザー要望)。
`ui/main_window.py`の`_build_actions()`を組み替える。各操作の処理（`open_parquet_dialog`・`go_home`等）・`_actions`の
キー・`settings.shortcuts`のキー・`log_op`の名前は**変えない**（45章の表・操作ログ・設定画面のショートカット欄がそのまま使える）。

#### 48.1 2本のツールバー

| objectName | 表示名（`toggleViewAction`・右クリックのメニュー） | 中身（`｜`は区切り線） |
|---|---|---|
| `file_view_toolbar` | ファイル・表示 / File & view（Qtの`&`の扱いに注意） | 開く ｜ 時間軸: [コンボ] ｜ ビュー ▾・画像を保存 ｜ 全体表示・再描画 ｜ ＋ プロット追加 ｜ カーソル・値の表示 |
| `analysis_toolbar` | 解析 / Analysis | 統計・グラフ ▾ ｜ ユーザー関数・マップ ｜ ⚙ ▾ |

- 両方とも`Qt.TopToolBarArea`に、この順で`addToolBar`する（同じ行に並ぶ。幅が足りなければQtの通常の動きに任せる）。
- 旧`main_toolbar`は無くす。機能フラグで消える項目（`user_functions`・`maps`・グラフの各項目）の扱いは今と同じ。
  `analysis_toolbar`の「ユーザー関数」「マップ」が両方とも無いときは、その前の区切り線も出さない（区切り線が2本続かない）。
- 「ビュー ▾」「⚙ ▾」は「グラフ ▾」（analysis 25.5）と同じ作り: `QToolButton`＋`QMenu`、`InstantPopup`、`objectName`あり
  （`view_button`/`view_menu`、`settings_button`/`settings_menu`）。メニューの中の操作は`self.addAction(action)`もして、
  メニューが閉じていてもショートカットが効くようにする（`_build_graphs_menu`と同じ）。
  - 「ビュー ▾」: `save_view`「保存...」、`load_view`「管理...」。
  - 「⚙ ▾」: `toggle_downsample`「簡易表示を許可」（チェック付き、ツールチップは今のもの。メニュー項目のツールチップは
    `menu.setToolTipsVisible(True)`で出す）、区切り、「設定...」、「レイアウトをリセット」。ボタンの文字は「⚙」、
    ツールチップ「設定・表示のオプション」/「Settings and display options」。
- 「カーソル」「値の表示」はチェック付きのまま`file_view_toolbar`の右端に置く。

#### 48.2 文言（`ui/i18n.py`）

| キー | 今 | 新 |
|---|---|---|
| `toolbar.home` | Home / ホーム | Show all / 全体表示 |
| `toolbar.save_view` | Save view / ビュー保存 | Save... / 保存...（「ビュー ▾」の中） |
| `toolbar.load_view` | View... / ビュー... | Manage... / 管理...（同上） |
| `toolbar.functions` | Functions / 関数 | User functions / ユーザー関数 |
| `toolbar.settings` | Settings / 設定 | 変えない（設定画面の題名にも使っている） |
| 新`toolbar.settings_item` | — | Settings... / 設定...（「⚙ ▾」の中） |
| `fnref.button` | Functions... / 関数一覧... | Function list... / 関数一覧... |
| 新`toolbar.view_menu` | — | View / ビュー |
| 新`toolbar.view_menu_tooltip` | — | Save or manage views / ビューの保存・管理 |
| 新`toolbar.settings_menu_tooltip` | — | Settings and display options / 設定・表示のオプション |
| 新`toolbar.file_view_title` | — | File && view / ファイル・表示 |
| 新`toolbar.analysis_title` | — | Analysis / 解析 |

- 設定画面のショートカット欄（persistence 9.1）は別のキー`shortcut.*`（`shortcut_label`）を使い、前後なしで分かる
  文言なので基本はそのまま。ツールバーと名前をそろえるため次の3つだけ変える: `shortcut.home` Show all / 全体表示、
  `shortcut.toggle_downsample` の日本語 簡易表示の許可 切替（英語はそのまま）、`shortcut.user_functions` User functions /
  ユーザー関数の管理。
- 「全体表示」のツールチップ（無ければ足す）: 「すべてのプロットをデータ全体の範囲に戻します」。

#### 48.3 レイアウトの保存（persistence 9.3）

- 旧版で保存した`window_state`には`main_toolbar`が入っている。`saveState`/`restoreState`に**版の番号**を付けて
  （`saveState(1)`・`restoreState(state, 1)`）、旧い状態は捨てて既定の配置にする（ウィンドウの位置・大きさ
  `window_geometry`はそのまま復元する）。版が合わず`restoreState`が`False`を返したときは何も警告しない。
- 「レイアウトをリセット」は今どおり（既定の状態は2本のツールバーで控える）。

#### 48.4 テスト計画

- 2本のツールバーが`objectName`どおりにあり、`file_view_toolbar`・`analysis_toolbar`の項目がこの順（表と同じ。
  区切り線の位置も）。`main_toolbar`は無い。
- 「ビュー ▾」「⚙ ▾」の中身と、各メニュー項目を`trigger()`すると今と同じ処理が呼ばれる（`save_view`・`load_view`・
  `toggle_downsample`のチェックの切替・設定・レイアウトのリセット）。
- メニューの中の操作に設定したショートカットが、メニューを開かずに効く（`toggle_downsample`・`save_view`で）。
- 機能フラグ`user_functions`・`maps`を両方OFFにすると「ユーザー関数」「マップ」とその区切りが無く、区切り線が続かない。
- 旧`main_toolbar`を含む`window_state`（版の番号なしで保存したもの）を入れて起動しても例外にならず、2本のツールバーが
  既定の位置にあり、`window_geometry`は復元される。新しい形で保存→復元すると2本の位置が戻る。
- 英語・日本語の両方で、新しいキーに訳がある（既存のi18nのテストで足りなければ足す）。
- 既存のテストで`main_toolbar`や旧の文言を見ているもの（`test_xyz_3d.py`・`test_features.py`等）は新しい形に直す。
  45章の表（`test_side_effects.py`）は操作のキーが変わらないので行は変えない（通ることを確かめる）。
- 確認: 画面なしで`MainWindow`を開いて画像に描き、2本の並び・文言を目で見る（幅1400pxと1000px）。

### 51. CIの整備（2026-10-13追加、ユーザー判断）

要求は5.44。Windowsはexe化と一緒に後回しのまま（開発メモ「exe化」）。

#### 51.1 macOSのジョブ（手動実行だけ）

- 新しいワークフロー`.github/workflows/test-macos.yml`（名前`Test (macOS)`）。トリガーは`workflow_dispatch`だけ。
  `runs-on: macos-latest`、手順はUbuntuと同じ（checkout → `astral-sh/setup-uv` → `uv sync --group dev` →
  `uv run pytest -n auto`）。Qtの共有ライブラリのapt手順は要らない。
- 動かし方: GitHubの「Actions」→「Test (macOS)」→「Run workflow」でブランチを選ぶ、または
  `gh workflow run test-macos.yml --ref <ブランチ>`（ブランチはpush済みであること）。結果は`gh run watch`。
- `.github/workflows/test.yml`（Ubuntu）の`on:`に`workflow_dispatch:`を足す（ブランチでも
  `gh workflow run test.yml --ref <ブランチ>`で試せる）。pushごと・PRの動きは今どおり。
- 目安: マージ前にプロット・ワーカーのタイミングに関わる変更をしたとき、またはUbuntuだけで落ちたときの切り分けに使う。
  開発ルールのCommandsに動かし方を1行足す。

#### 51.2 Ubuntuで落ちるテストを直す

- 落ちているテスト（CIのログ、2026-10-13の`main`）:
  - `test_worker_paths.py::test_stats_paths[S-3]`: 統計のジョブ2つが`gate: held job was never released`（30秒）で失敗し、
    `assert_clean_log`に失敗の記録が残る。2026-10-07 23:33の実行から毎回。
  - `test_plot_widget.py`の`test_a_button_fits_only_visible_x_range_and_leaves_x_alone`・
    `test_a_button_on_windowed_series_uses_drawn_curve_not_window_reads`・
    `test_a_button_changes_only_the_pressed_plot_in_a_linked_grid`: 「A」を押した後もYが押す前の範囲のまま
    （「A」の操作が届いていない）。同じ形の`test_a_button_with_cursor_fits_y_to_data_and_stays_put`は通る。
- 方針: まず原因を確かめる（手元のMacで再現しなければ、ブランチをpushしてUbuntuのワークフローを手動実行し、
  必要なら診断の出力を一時的に足す）。**アプリの不具合ならアプリを直し、テストの書き方（実機とCIの違い、待ち方）なら
  テストを直す**。テストを緩めて通すだけにはしない（待ちは実時間の`sleep`・上限付き、開発ルールの規則どおり）。
- 終わりの条件: 直したブランチでUbuntuのCIが緑、手元の全テストが通る。原因と直し方をこの節に書き足す。
  macOSのジョブも1回手動で動かして結果をここに残す。
- **原因と直し方（2026-10-13）**: どちらもアプリの不具合ではなくテストの書き方だった。
  - `test_stats_paths[S-3]`: 統計のジョブは`MainWindow._analysis_thread_pool`（窓ごとの専用プール）で動くが、
    `test_worker_paths.py`の`_wide_pools`は`QThreadPool.globalInstance()`しか広げていなかった。プールの既定のスレッド数は
    コア数で、CIのUbuntu（2コア）では3つ目のジョブが門（gate）に入れず、1・2つ目が30秒待たされて失敗した
    （手元のMacは多コアで再現しない。プールを2本にすると手元でも再現した）。テストが入った2026-10-07 23:31（UTC）以来、毎回落ちていた。
    直し: `open_window`で窓の分析用プールも6本にする。
  - 「A」ボタンの3つ: テストの補助`_click_a_button`が`btn.show()`のあとに`processEvents()`を挟んでいた。pyqtgraphの
    `updateButtons()`は表示状態が変わるたびに（ホバー中でなければ）ボタンを隠すので、遅いUbuntuでは待っていた範囲変更・
    再描画のイベントがその間に走ってボタンが隠れ、クリックが届かなかった（CIに診断を出して、失敗した2件で押す直前に
    `isVisible()`が偽なのを確認）。アプリ側は正しい（実機で動く）。直し: 先に`processEvents()`で待っていたイベントを流し、
    そのあとで`show()`して、イベントを処理せずにすぐクリックする（押す直前に`isVisible()`をassert）。
    `fit_y`の接続を外すとこの7件が落ちることも確かめた。

### 52. lint（ruff）の導入（2026-10-13追加、ユーザー判断）

要求は5.45。出典は開発メモ「調査対象」（2026-10-13）。

#### 52.1 設定

- `pyproject.toml`の`[dependency-groups] dev`に`ruff`を足す（下限は導入時の版、2026-10-13時点で`ruff>=0.17`）。
- `[tool.ruff]`: `target-version = "py311"`（`requires-python`に合わせる）、`line-length = 100`（E501は見ないが、
  `I`の並べ替えの折り返しに使う）、`src = ["src", "tests"]`（`I`で自分のパッケージを1st partyと判定させる）。
  対象は`src/`・`tests/`・`tools/`（`extend-exclude`で`data/`・`log/`・`cfg/`等を外す必要があれば外す）。
- `[tool.ruff.lint]`: `select = ["E", "F", "I", "UP"]`、`ignore = ["E501"]`。
- テストのフィクスチャを別のテストモジュールからimportして引数で受ける書き方（`from .test_x import win` →
  `def test_y(win)`）はF811（未使用の再定義）になる（約70件）。テストの書き方を変える理由にはならないので、
  `[tool.ruff.lint.per-file-ignores]`で`tests/**`だけF811を外す（そのimportにF401が出るならそれも`tests/**`で外す）。
  共通のフィクスチャを`conftest.py`へ移す整理は今回はしない（挙動・並列実行への影響を見る必要があり、別作業）。

#### 52.2 最初の一括修正

- 2026-10-13の`main`での件数（ruff 0.17.0、E501を除く）: I001 76、F811 71（52.1で外す）、UP037 33、UP035 23、
  F401 18、UP017 8、E741 6、F841 5、E731 3、UP031 1。
- 自動修正（`ruff check --fix`、unsafeは使わない）で直るもの（I001・UP037・UP035・F401・UP017）はそのまま当てる。
  ただし`F401`で`__init__.py`の再公開（`from .x import y`を外へ見せるためのimport）や、副作用のためのimport
  （例: OpenGL・Qtの初期化、`features`の登録）を消さないこと。該当するものは消さずに`__all__`へ足すか`# noqa: F401`と理由を書く。
- 手で直すもの:
  - E741（`l`等の紛らわしい名前、テスト）: 名前を変える。
  - E731（`f = lambda ...`、テスト）: `def`にする。
  - F841（未使用の変数）: テストでは消す。`src/parquet_analyzer/core/functions.py`の`consts`は**消す前に意図を確かめる**
    （使い忘れなら不具合なので、消さずに報告して止める）。
  - UP031（`%`書式）: f-stringに直す。ただし`logging`の遅延書式（`log.debug("%s", x)`）は対象外のはずなので、もし出たら報告。
- 挙動が変わりうる指摘（上以外のもの、unsafe fixが要るもの）は直さずに報告に挙げ、main sessionが1件ずつ判断する。
- コミットは2つに分ける: (1) 設定・CI・ドキュメント、(2) 一括修正（`ruff check --fix`で機械的に変わったもの＋手直し）。
  (2)のメッセージに件数を書く。

- **実装の結果（2026-10-13、`feat/52-ruff`）**: `pyproject.toml`の設定（`src`の指定でパッケージの判定が変わる）で数えると
  I001は57件（上の76は設定なしで数えた値）。自動修正139件（I001 57・UP037 33・UP035 23・F401 18・UP017 8）、手直し14件
  （E741 6・E731 3・F841 4・UP031 1）。srcで消したF401は3件（`map_grid.py`の`Iterator`、`flow_layout.py`の`QSizePolicy`、
  `view_manager.py`の`Qt`）で、再公開・副作用のためのimportは無かった。`core/functions.py`の`consts`は
  `_static_check_calls`で作るだけで使っていない（呼び出し先の定数は`target.constants`で検査している）ので消した。
  全テストの件数は前後で同じ（3196件成功・12件skip・1件xfail）。

#### 52.3 CIとドキュメント

- `.github/workflows/test.yml`（Ubuntu）に、依存のインストールの後・テストの前に`uv run ruff check`の1ステップを足す
  （テストより速く落ちる）。macOSのワークフロー（51.1）には足さない（同じ結果になるので実行時間の無駄）。
- 開発ルールのCommandsに`uv run ruff check [--fix]`を1行足す。開発ルールにlintの記載が要るか確かめ、
  テンプレートから外れるなら版・変更履歴を上げる。

#### 52.4 テスト計画

- アプリの挙動は変えないので、新しいテストは足さない。
- 確認: `uv run ruff check`が0件、全テスト（`uv run pytest -q -n auto`）が通る（件数が変わらないこと。
  テストの関数を消していないことの確認）、`tools/run.sh`の起動（画面なしで`MainWindow`を作って閉じる）が通る。
- push後、ブランチでUbuntuのワークフローを手動実行し（`gh workflow run test.yml --ref <ブランチ>`）、lintのステップと
  テストが緑であることを確かめる。

## 他の分野との関係

- **全分野**: 機能フラグ（21章）・ログ・エラー処理の方針・i18nは全分野が使う。
- **保存**（[persistence.ja.md](./persistence.ja.md)）: 設定ファイルの扱い、パス。
- **全分野**（30章、2026-10-03）: 操作ログは全分野の`log_op`・メッセージ（`ui/messages`）・裏の処理の`job`を記録する。
  新しい画面や操作を足すときは、`log_op`を操作の始めに呼び、ダイアログは`ui/messages`を通す。
- **全分野**（45章、2026-10-06）: メイン画面に新しいボタン・メニュー・操作を足すときは、45.2の表に
  1行（変わってよい項目）を足し、`test_side_effects.py`に入れる。状態のまとめ（30.2）に項目を足したときは、
  表の各行でその項目が変わってよいかを見直す。
- **データ・式・解析**（46章、2026-10-08）: `TimeIndex`・`row_range_for_x`・窓読み込みの列（data 36章）、式の評価器
  （expressions 6.1・43章）、`rolling_mean`（expressions 31.1）、`chunked_stats`・LTTBの性質をランダムな入力で
  確かめるテストがある。これらの約束（結果が全体の配列で計算したものと同じ、失敗は`ExpressionError`だけ等）を
  変えるときは`test_property_*.py`も直す。
- **保存**（48章、2026-10-13）: ツールバーを`file_view_toolbar`・`analysis_toolbar`の2本にし、`saveState`/`restoreState`に
  版の番号1を付けた（persistence 9.3）。ツールバーに項目を足すときは48.1の表のどちらのまとまりかを決め、まれな操作は「⚙ ▾」へ。
- **全分野**（37章）: Pythonの自動のGCを止め、`pyqtgraph`の`GarbageCollector`で画面のスレッドから回収する。
- **保存**（persistence 54.4、2026-10-14）: 解析のツールバーの「ユーザー関数・マップ」の隣に「信号の別名」（ショートカット`signal_aliases`）。保存先`cfg/parquet_analyzer/signal_aliases.json`は`config.py`で定義する。
