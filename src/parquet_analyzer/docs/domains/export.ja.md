# 画像保存

> 総括仕様: [overview.ja.md](../overview.ja.md)（共通ルール・用語・旧節番号の対応表）。
> 本書は「要求」→「設計」の順。節番号は旧仕様書の番号をそのまま使っている。

**この分野の範囲**: プロットの画像保存（matplotlibでの再描画、形式、記載・メタデータ）。解析ウィンドウの画像保存もこの仕組みを使う。

## 要求

#### 5.14 プロットの画像保存（2026-09-30追加、ユーザー要望。同日に方式を変更）
- ツールバーの「画像を保存」で、**現在表示しているプロット**を画像ファイルに保存する。画面の
  キャプチャではなく、**matplotlibで描き直した**論文・資料向けの見た目の図にする。プロットは
  画面と同じ順に縦に並べる。ナビゲータ・変数リスト・ツールバーは含めない。
- 各プロットの内容は画面と同じ: 同じ系列・同じ色・同じX範囲（全時間軸プロットで共通）と
  Y範囲、カーソルモードがオンならカーソルも描く。描くデータも画面と同じ
  （ダウンサンプリング後の）データ。
- **X軸を揃える**: 全時間軸プロットは1本のX軸を共有し、プロット領域の左右端をぴったり
  揃える。日時の目盛りラベルは一番下のプロットにだけ付ける（画面ではY軸目盛りラベルの幅の
  違いでプロット領域がずれることがあるが、画像ではずれない）。
- **各プロットに凡例**を入れ、そのプロットの系列名を示す。
- 2026-10-02追加:
  - 非表示の系列（[5.17](./plots.ja.md#517-系列ごとの操作-選択削除表示非表示2026-10-02追加ユーザー要望)）は描かない。
  - 各プロットの高さは画面上の高さの比率に合わせる（[5.15](./plots.ja.md#515-プロットの高さ変更並べ替え2026-10-02追加ユーザー要望)）。
  - X軸を連動させていないプロット（[5.18](./plots.ja.md#518-プロットごとのx軸の独立2026-10-02追加ユーザー要望)）は
    共有X軸に入れず、自分のX軸と時刻ラベルを持つ。時刻ラベルの規則は
    [5.16](./plots.ja.md#516-時刻ラベルは必要な所だけに表示2026-10-02追加ユーザー要望)と同じ。
- 形式: **PNG**（既定）、**JPEG**、**TIFF**（ラスタ。画面上の大きさの2倍のピクセル数）、
  **SVG**、**PDF**（ベクタ）。形式はファイルの拡張子で決まる。
- **メタ情報の記載**: 画像の右下、プロットの下（プロットには重ねない）に次を記載する:
  1. 元のParquetファイル名（現在のレイアウトがビューから読み込んだもの、またはビューとして
     保存したものであればビュー名も）
  2. 表示中の時間範囲（開始 – 終了）
  3. 出力日時とアプリのバージョン
  同じ情報を、形式が対応していればファイル自体のメタデータにも埋め込む。
- 既定のファイル名は `<Parquetファイル名>_<出力日時>.png`。保存先の既定は前回保存したフォルダ
  （初回はParquetファイルのあるフォルダ）。
- ファイルが開かれていて、データのあるプロットが1つ以上あるときだけ実行できる。プロットの
  再描画中の場合は、描画途中の状態を保存しないよう、描画完了後に再実行するよう案内する。
- 詳細は [detailed_specification.ja.md 19章](#19-プロットの画像保存2026-09-30追加ユーザー要望)。

## 設計

### 19. プロットの画像保存（2026-09-30追加、ユーザー要望）

要件: `specification.ja.md` 5.14章。（2026-10-02）非表示の系列は描かない（23.1）、高さは画面の
比率、連動していないプロットは`sharex`に入れず自分のX軸と目盛ラベルを持つ（24.6）。

#### 19.0 経緯: 画面キャプチャ → matplotlib（同日変更）

最初の実装は画面に描かれたプロット領域をそのまま写していた（`QWidget.render`で`QImage`/
`QPdfWriter`へ）。ユーザー確認の結果、次の理由で取り下げた: (1) 縦に並んだプロットのX軸が
揃わない——pyqtgraphの各プロットはY軸の幅を自分の目盛りラベル幅に合わせるため、プロット
領域の開始X位置がプロットごとにずれる; (2) pyqtgraphの画面用凡例（線と文字が重なり枠も
無い）は保存した図では読みにくい; (3) SVGを正しく作れない——Qtの`QSvgGenerator`は座標を
有効数字6桁でしか書き出さず、エポック秒（約1.7e9）の時間軸では全曲線が1本の縦線に潰れる。
pyqtgraph自身の`SVGExporter`はPySide6 6.11のパス構文で例外になる（pyqtgraph 0.14.0）。
matplotlibで描き直せば3点とも解決する（X軸共有で軸が揃う、まともな凡例、SVGバックエンドは
デバイス座標で書き出す）。

#### 19.1 画面内容のスナップショット（`ui/plot_widget.py`）

- `TimePlotWidget.export_snapshot() -> PlotSnapshot`（`ui/image_export.py`のdataclass。
  Qtオブジェクトを含まない純粋なデータ）: `x_axis_datetime: bool`、`x_range: (lo, hi)`、
  `y_range: (lo, hi)`（現在の`viewRange()`）、`series: list[SeriesSnapshot(name, color, x, y)]`
  （表示順。`x`/`y`は各系列の`curve.getData()`をコピーしたもの——画面と同じくダウンサンプリング
  後/ピラミッド包絡のデータ。まだ何も描かれていなければ空配列）、`cursors: list[float]`
  （各カーソル線のX。カーソル線が表示中、つまりカーソルモードがオンのときだけ）。
- `MainWindow`は`plot_grid.plots`の順に全プロットの`export_snapshot()`を集め、系列の無い
  プロットは除く。1つも残らなければ`export.nothing_to_export`を案内し、保存しない。
  （2026-10-02）非表示の系列は`export_snapshot()`に入らない（23.1）ので、系列が全部非表示の
  プロットも「系列の無いプロット」として画像から除かれる。

#### 19.2 描画（`ui/image_export.py`）

- `build_figure(snapshots, stamp_lines, size_px) -> matplotlib.figure.Figure`と
  `save_figure(fig, path, metadata)`に分け、テストがファイルを書かずに図を検査できるようにする。
  `export_plots_image(snapshots, path, stamp_lines, metadata, size_px)`が両方を呼ぶ。
  `matplotlib.figure.Figure`とAggキャンバスを直接使い、`pyplot`は使わない（グローバル状態を
  持たず、動作中のQtアプリとGUIバックエンドが干渉しない）。起動時間に影響しないよう、
  `matplotlib`はこれらの関数内で遅延importする。
- 図: 大きさは`size_px`（`plot_grid`ウィジェットの大きさ）/ 100 インチ、背景は白。
  スナップショット1つにつきサブプロット1つを縦に並べ、高さは均等。時間軸サブプロットは
  X軸を共有（`sharex`）。時間軸でない（FFT、XはHz）サブプロットは独自のX軸で`Hz`ラベルを
  付ける。レイアウトエンジンは`constrained`で、Y目盛りラベル幅に関係なく全サブプロット領域の
  左端を揃える。X軸の目盛りラベルは共有グループの一番下のサブプロットにだけ表示する。
- 各サブプロット: 全系列を画面と同じ色の線で描く（線幅0.8）、X/Y範囲はスナップショットの
  範囲、薄いグリッド（alpha 0.3）、系列名の凡例（`loc="upper right"`、半透明の枠、
  文字サイズ8）、カーソルは画面と同じカーソル色（`#ffaa00`）の破線の縦線。
- 時間X軸: 値はエポック秒で、画面と同じく**ローカル時刻**で表示する（`DateAxisItem`/
  カーソルラベルは`datetime.fromtimestamp`を使う）。X範囲開始時点のローカルUTCオフセットで
  naiveなローカル`datetime64`に変換し、`matplotlib.dates.ConciseDateFormatter`で書式化する
  （画面の軸と同様、ズームに応じて粒度が変わる）。範囲内で夏時間が切り替わる場合は後半が
  その差だけずれる——許容範囲としてここに明記する。
- 記載: `stamp_lines`を改行で連結し、図の右下に右揃えで描く（文字サイズ7、グレー）。
  constrainedレイアウトの`rect`で下端に十分な高さを確保し、どの軸・目盛りラベルとも
  重ならないようにする。
- `save_figure`: 形式はパスの拡張子で決まる（`EXPORT_FORMATS`: `.png`, `.jpg`/`.jpeg`,
  `.tif`/`.tiff`, `.svg`, `.pdf`。それ以外は`ValueError`）。ラスタは`dpi=200`（図を100dpiで
  作っているので画面上のピクセル数の2倍）。JPEGは品質95。メタデータ: PNG——全キーを`tEXt`
  チャンクに; PDF——`Title`、`Creator`、`Subject`（範囲）、`Keywords`（元パス）; SVG——
  `Title`、`Description`（全キーを`key: value`の行で）、`Creator`; TIFF——ImageDescription
  タグ（全キーを`key: value`の行で）; JPEG——同じテキストをJPEGコメントに。保存後にファイルが
  存在しなければ`OSError`。
- **日本語の文字**（確認時に発見）: matplotlibの既定フォント（DejaVu Sans）にはCJKの字形が
  無く、日本語の系列名・ファイル名/ビュー名・`ja`設定時の記載ラベルが空の四角（豆腐）になって
  いた。図は`matplotlib.rc_context({"font.family": ["DejaVu Sans", <cjk>]})`の中で作り、
  matplotlibの字形単位のフォントフォールバックを使う（英数字はDejaVu Sansのまま、足りない
  字形だけ`<cjk>`から取る）。`<cjk>`は`_CJK_FONT_CANDIDATES`のうち実際にインストールされて
  いる最初のもの（`matplotlib.font_manager.fontManager.ttflist`の名前と照合。1回だけ調べて
  キャッシュ）: `Hiragino Sans`、`Hiragino Kaku Gothic ProN`（macOS）、`Yu Gothic`、`Meiryo`、
  `MS Gothic`（Windows）、`Noto Sans CJK JP`、`Noto Sans JP`、`IPAexGothic`、`IPAGothic`、
  `TakaoGothic`（Linux）、`Arial Unicode MS`。リストにはインストール済みのものだけを入れる
  （存在しないフォント名があると、matplotlibが描画のたびに「font family not found」警告を
  出すため）。どれも無ければDejaVu Sansだけで保存は続行する（`logger.warning`を1回）。
  文字は描画時にレイアウトされるため、rc_contextは図の構築だけでなく`savefig`まで囲む。
  macOSで確認済み: Hiragino SansはPNG・PDF・SVGのいずれでも字形欠けなく日本語を描ける。
  Windows/Linuxのフォントはここでは未検証。
- 依存関係: `matplotlib`を本体の依存に追加する（画像保存は中核機能のため）。PyInstallerの
  配布サイズは増える。

#### 19.3 `MainWindow.export_image_dialog()` / `export_image(path)`

- ツールバーのアクション`export_image`（ショートカット設定可、既定は未割り当て。9章参照）。
- ファイル未オープン → `error.open_file_first`。`plot_grid`が描画中（ダウンサンプリング
  ジョブ実行中、`"plot_grid" in _busy_sources`）→ `export.busy`を案内し、保存しない。
- `QFileDialog.getSaveFileName`で形式ごとのフィルタを出す。既定:
  `<settings.last_export_folder または Parquetファイルのフォルダ>/<parquetのstem>_<YYYYmmdd_HHMMSS>.png`。
  選んだ名前に対応する拡張子が無ければ、選択中フィルタの拡張子を付ける。保存に成功したら
  そのフォルダを`settings.last_export_folder`に記録・保存する。
- 記載行（`_image_stamp()`）:
  1. `<ファイル名>` または `<ファイル名>  |  ビュー: <ビュー名>`。`MainWindow._view_name`は
     `load_view`と`save_view_dialog`で設定し、`load_parquet`でクリアする（ファイルを直接
     開いた時点で、レイアウトはどの保存済みビューとも対応しなくなるため）。
  2. `表示範囲: <開始> – <終了>` — 時間軸プロットで共有しているX範囲。カーソルラベルと同じ
     書式（`%Y-%m-%d %H:%M:%S.mmm`、`TimePlotWidget.format_x`）。データのある時間軸プロットが
     無ければ省略。
  3. `出力日時: <YYYY-MM-DD HH:MM:SS>  |  Parquet Analyzer v<version>`。
  ラベルは`tr()`を通す（言語設定が反映される）。
- 埋め込みメタデータのキー: `Title`（ファイル名）、`Source`（Parquetのフルパス）、`View`
  （あれば）、`Range`、`CreationTime`（ISO 8601）、`Software`。
- 書き込み失敗（`OSError`/`ValueError`）→ `export.failed`を警告表示。成功時はステータスバーに
  パスを表示する（モーダルダイアログにはしない: 頻繁に使う操作で、毎回閉じるだけのダイアログ
  になるため）。
- 操作ログ: `export_image`（パス、形式）。

## 他の分野との関係

- **プロット**（[plots.ja.md](./plots.ja.md)）: `export_snapshot()`の内容（非表示の系列を除く、高さの比率、X軸連動）。
- **解析**（[analysis.ja.md](./analysis.ja.md)）: 解析ウィンドウが保存処理を共通で使う。
- **保存**（[persistence.ja.md](./persistence.ja.md)）: `last_export_folder`、ビュー名（記載に出す）。
- **解析**（25章、2026-10-03）: 解析ウィンドウの画像保存は`_image_stamp(range_override=...)`で、ウィンドウが計算した範囲を記載する。
