from __future__ import annotations

from ..io.settings import DEFAULT_LANGUAGE, LANGUAGES

# Each language's own name for itself, shown in the settings dialog's language
# picker regardless of the currently active UI language (the usual convention --
# a Japanese speaker looking for "日本語" shouldn't have to already read English).
LANGUAGE_LABELS: dict[str, str] = {"en": "English", "ja": "日本語"}

_current_language = DEFAULT_LANGUAGE

# key -> {language -> text}. Text may contain "{name}"-style str.format placeholders,
# filled in by tr()'s kwargs.
_STRINGS: dict[str, dict[str, str]] = {
    "app.file_label": {"en": "File: {name}", "ja": "ファイル: {name}"},
    "app.variable_dock": {"en": "Variables", "ja": "変数"},
    "app.rendering": {"en": "Rendering…", "ja": "描画中…"},
    "app.coarse_indicator": {
        "en": "Showing a coarse preview (zoom in to see exact data)",
        "ja": "簡易表示中（ズームインで実データ表示に戻ります）",
    },
    "toolbar.open": {"en": "Open", "ja": "開く"},
    "toolbar.time_axis_label": {"en": "Time axis: ", "ja": "時間軸: "},
    "toolbar.time_axis_tooltip": {
        "en": "Select the column to use as the X axis (defaults to the first column)",
        "ja": "X軸として使う列を選択（既定は先頭列）",
    },
    "toolbar.home": {"en": "Home", "ja": "ホーム"},
    "toolbar.redraw": {"en": "Redraw", "ja": "再描画"},
    "toolbar.add_plot": {"en": "+ Add plot", "ja": "＋ プロット追加"},
    "toolbar.fft": {"en": "FFT", "ja": "周波数解析"},
    "toolbar.xy_plot": {"en": "XY plot", "ja": "XYプロット"},
    "shortcut.xy_plot": {"en": "XY / 3D plot", "ja": "XY / 3Dプロット"},
    "toolbar.graphs": {"en": "Graphs", "ja": "グラフ"},
    "toolbar.graphs_tooltip": {"en": "Open another kind of graph", "ja": "ほかの種類のグラフを開きます"},
    "graphs.xy": {"en": "XY plot", "ja": "XYプロット"},
    "graphs.xy_3d": {"en": "XY / 3D plot", "ja": "XY / 3Dプロット"},
    "graphs.fft": {"en": "FFT", "ja": "周波数解析"},
    "xyz.need_xyz": {"en": "Select X, Y and Z.", "ja": "X・Y・Zを選択してください。"},
    "xyz.status_points": {
        "en": "Points: {points} (excluded {excluded} rows with NaN/Inf)",
        "ja": "点数: {points}（NaN/Infの{excluded}行を除外）",
    },
    "xyz.unavailable_title": {"en": "3D view unavailable", "ja": "3D表示を使えません"},
    "xyz.unavailable": {
        "en": "The 3D view cannot be used in this environment (OpenGL): {reason}\nThe XY plot is still available.",
        "ja": "この環境では3D表示を使えません（OpenGL）: {reason}\nXYプロットは引き続き使えます。",
    },
    "xyz.map_3d": {"en": "3D", "ja": "3D"},
    "xyz.map_3d_tooltip": {
        "en": "Show the table as a rotatable 3D surface",
        "ja": "テーブルを回転できる3D曲面で表示します",
    },
    "xyz.view_home": {"en": "Home", "ja": "ホーム"},
    "xyz.view_home_tooltip": {
        "en": "Back to the initial view (also: double-click, Home key)",
        "ja": "最初の視点に戻ります（ダブルクリック、Homeキーでも戻れます）",
    },
    "xyz.view_xy": {"en": "XY", "ja": "XY"},
    "xyz.view_xy_tooltip": {"en": "View from above (X right, Y up)", "ja": "真上から見ます（Xが右、Yが上）"},
    "xyz.view_yz": {"en": "YZ", "ja": "YZ"},
    "xyz.view_yz_tooltip": {
        "en": "View from the +X side (Y right, Z up)",
        "ja": "X軸の正の側から見ます（Yが右、Zが上）",
    },
    "xyz.view_zx": {"en": "ZX", "ja": "ZX"},
    "xyz.view_zx_tooltip": {
        "en": "View from the front, -Y side (X right, Z up)",
        "ja": "Yの負の側から正面に見ます（Xが右、Zが上）",
    },
    "xyz.hover_time": {"en": "Time: {value}", "ja": "時刻: {value}"},
    "xy.y_single": {"en": "Y", "ja": "Y"},
    "xy.z_axis": {"en": "Z (3D axis)", "ja": "Z（3Dの軸）"},
    "xy.mode": {"en": "View", "ja": "表示形式"},
    "xy.mode_2d": {"en": "2D", "ja": "2D"},
    "xy.mode_3d": {"en": "3D", "ja": "3D"},
    "xy.mode_3d_unavailable": {
        "en": "3D is unavailable in this environment (OpenGL): {reason}",
        "ja": "この環境では3Dを使えません（OpenGL）: {reason}",
    },
    "analysis.recompute": {"en": "Recompute", "ja": "再計算"},
    "analysis.follow": {"en": "Follow main range", "ja": "メインの範囲に追従"},
    "analysis.follow_tooltip": {
        "en": "Recompute automatically when the main plots' visible range changes",
        "ja": "メインのプロットの表示範囲が変わったら自動で再計算します",
    },
    "analysis.computing": {"en": "Computing…", "ja": "計算中…"},
    "analysis.failed": {"en": "Calculation failed: {message}", "ja": "計算失敗: {message}"},
    "xy.title": {"en": "XY plot", "ja": "XYプロット"},
    "xy.x": {"en": "X", "ja": "X"},
    "xy.y": {"en": "Y (multiple)", "ja": "Y（複数選択可）"},
    "xy.z": {"en": "Z", "ja": "Z"},
    "xy.color": {"en": "Color", "ja": "色"},
    "xy.color_single": {"en": "Single color", "ja": "単色"},
    "xy.color_time": {"en": "Time", "ja": "時間"},
    "xy.color_z": {"en": "Z variable", "ja": "Z変数"},
    "xy.display": {"en": "Display", "ja": "表示"},
    "xy.display_auto": {"en": "Auto", "ja": "自動"},
    "xy.display_scatter": {"en": "Scatter", "ja": "散布図"},
    "xy.display_density": {"en": "Density", "ja": "密度"},
    "xy.need_xy": {"en": "Select X and at least one Y.", "ja": "Xと1つ以上のYを選択してください。"},
    "xy.status_range": {"en": "Range: {range} ({rows} rows)", "ja": "範囲: {range}（{rows}行）"},
    "xy.status_points": {
        "en": "Points: {points} (excluded NaN/Inf: {excluded})",
        "ja": "点数: {points}（NaN/Inf除外: {excluded}）",
    },
    "xy.unknown_variable": {
        "en": "Variable \"{name}\" is no longer available. Reselect it.",
        "ja": "変数「{name}」は利用できなくなりました。選び直してください。",
    },
    "xy.status_no_points": {"en": "No valid points in range", "ja": "範囲内に有効な点がありません"},
    "xy.status_density": {"en": "Density view", "ja": "密度表示"},
    "xy.status_thinned": {"en": "Thinned display ({shown} / {total} points)", "ja": "間引き表示（{shown} / {total}点）"},
    "toolbar.stats": {"en": "Stats", "ja": "統計"},
    "toolbar.save_view": {"en": "Save view", "ja": "ビュー保存"},
    "toolbar.export_image": {"en": "Save image", "ja": "画像を保存"},
    "toolbar.load_view": {"en": "View...", "ja": "ビュー..."},
    "toolbar.toggle_downsample": {"en": "Allow coarse preview", "ja": "簡易表示を許可"},
    "toolbar.cursor": {"en": "Cursor", "ja": "カーソル"},
    "toolbar.hover_values": {"en": "Values", "ja": "値の表示"},
    "toolbar.hover_values_tooltip": {
        "en": "Show the time and each series' value at the mouse position (also on X-linked plots)",
        "ja": "マウスを乗せた位置の時刻と各系列の値を表示します（X軸連動のプロットにも表示）",
    },
    "toolbar.cursor_tooltip": {
        "en": "Click on a plot to place a cursor showing its time and values (click a "
        "cursor to remove it, drag to move it)",
        "ja": "プロット上をクリックするとその時刻と値を示すカーソルを設置します"
        "（カーソルをクリックすると削除、ドラッグで移動）",
    },
    "toolbar.toggle_downsample_tooltip": {
        "en": "When off, always show exact data (can be slower when zoomed out on a huge column)",
        "ja": "オフにすると常に実データのみを表示します（大容量列をズームアウトして見るときに遅くなる場合があります）",
    },
    "toolbar.settings": {"en": "Settings", "ja": "設定"},
    "toolbar.reset_layout": {"en": "Reset layout", "ja": "レイアウトをリセット"},
    "error.no_data_columns": {
        "en": "No data columns found besides the time column.",
        "ja": "時刻列以外のデータ列が見つかりません。",
    },
    "error.load_failed_title": {"en": "Load error", "ja": "読み込みエラー"},
    "error.load_failed_body": {
        "en": "Failed to load the Parquet file:\n{error}",
        "ja": "Parquetファイルの読み込みに失敗しました:\n{error}",
    },
    "error.open_file_first": {
        "en": "Please open a Parquet file first.",
        "ja": "先にParquetファイルを開いてください。",
    },
    "error.duplicate_variable_name": {
        "en": '"{name}" is already used by an existing column. Please choose a different variable name.',
        "ja": "「{name}」は既存の列名と同じです。別の変数名を使ってください。",
    },
    "render.pyramid_failed": {
        "en": "the overview envelope could not be built",
        "ja": "概観（ピラミッド）の作成に失敗しました",
    },
    "render.failed": {
        "en": "Rendering failed for {name}: {message}",
        "ja": "{name}の描画に失敗しました: {message}",
    },
    "variable.not_numeric": {
        "en": '"{name}" is not a numeric column and cannot be plotted.',
        "ja": "「{name}」は数値の列ではないためプロットできません。",
    },
    "time_axis.failed": {
        "en": 'Cannot use "{name}" as the time axis: {message}',
        "ja": "「{name}」は時間軸に使えません: {message}",
    },
    "fft.failed": {"en": "FFT failed: {message}", "ja": "FFTに失敗しました: {message}"},
    "fft.has_gaps": {
        "en": "\u26a0 FFT needs evenly spaced samples. This range has {gaps} gap(s) in the time axis "
        "(median step {dt} s). Zoom in to a range without gaps and try again.",
        "ja": "\u26a0 FFTには等間隔のサンプリングが必要です。この範囲には時間軸の途切れが{gaps}箇所あります"
        "（間隔の中央値 {dt} 秒）。途切れの無い範囲に絞ってから実行してください。",
    },
    "fft.not_uniform": {
        "en": "\u26a0 FFT needs evenly spaced samples. In this range: median step {dt} s, "
        "max deviation {dev}%, {gaps} gap(s). Zoom in to an evenly sampled range and try again.",
        "ja": "\u26a0 FFTには等間隔のサンプリングが必要です。この範囲: 間隔の中央値 {dt} 秒、"
        "最大のずれ {dev}%、途切れ {gaps}箇所。等間隔の範囲に絞ってから実行してください。",
    },
    "fft.too_few_samples": {
        "en": "\u26a0 Too few samples in the range for FFT (need at least 3). Zoom out and try again.",
        "ja": "\u26a0 範囲内のサンプルが少なすぎてFFTできません（3点以上必要）。範囲を広げてください。",
    },
    "fft.time_not_finite": {
        "en": "\u26a0 The time axis has NaN/Inf values in this range, so FFT cannot run. Narrow the range.",
        "ja": "\u26a0 この範囲の時間軸に欠損・無限大があるためFFTできません。範囲を絞ってください。",
    },
    "fft.time_not_ascending": {
        "en": "\u26a0 The time axis is not ascending in this range, so FFT cannot run.",
        "ja": "\u26a0 この範囲の時間軸が昇順でないためFFTできません。",
    },
    "stats.failed": {"en": "Calculation failed: {message}", "ja": "計算失敗: {message}"},
    "time_order.not_sorted": {
        "en": "\u26a0 Time axis is not ascending ({count} place(s)). Visible range, statistics and cursor "
        "values may be wrong.",
        "ja": "\u26a0 時間軸が昇順ではありません（{count}箇所）。表示範囲・統計・カーソルの値が正しくない可能性があります。",
    },
    "time_order.has_nan": {
        "en": "\u26a0 Time axis has {count} non-finite value(s) (NaN/Inf).",
        "ja": "\u26a0 時間軸に欠損・無限大が{count}件あります。",
    },
    "time_order.tooltip": {
        "en": "First problem at row {row}. Try selecting a different column as the time axis.",
        "ja": "最初に乱れる行: {row}。時間軸を別の列に切り替えてみてください。",
    },
    "time_order.tooltip_nan": {
        "en": "Non-finite values (NaN/Inf) are present. Try selecting a different column as the time axis.",
        "ja": "欠損・無限大があります。時間軸を別の列に切り替えてみてください。",
    },
    "fft.select_variable": {"en": "Please select a variable.", "ja": "変数を選択してください。"},
    "fft.range_too_wide": {
        "en": "The visible range is too wide ({rows:,} rows). Zoom in to {max_rows:,} rows or "
        "fewer and try again.",
        "ja": "表示範囲が広すぎます（{rows:,}行）。ズームインして{max_rows:,}行以下に絞ってから実行してください。",
    },
    "stats.select_variable": {
        "en": "Please select a variable, or display one on a plot.",
        "ja": "変数を選択するか、プロットに変数を表示してください。",
    },
    "stats.empty_range": {"en": "(empty range)", "ja": "(空の範囲)"},
    "stats.cannot_compute": {
        "en": "Could not compute the selected variables.",
        "ja": "選択された変数を計算できませんでした。",
    },
    "stats.range_label": {"en": "Range: {range}", "ja": "対象範囲: {range}"},
    "stats.range_with_count": {
        "en": "{start:.3f}–{end:.3f}s ({count:,} rows)",
        "ja": "{start:.3f}–{end:.3f}s ({count:,}行)",
    },
    "stats.col_variable": {"en": "Variable", "ja": "変数"},
    "stats.col_count": {"en": "Count", "ja": "件数"},
    "stats.col_nan": {"en": "Missing", "ja": "欠損値"},
    "stats.col_min": {"en": "Min", "ja": "最小"},
    "stats.col_max": {"en": "Max", "ja": "最大"},
    "stats.col_mean": {"en": "Mean", "ja": "平均"},
    "stats.col_std": {"en": "Std Dev", "ja": "標準偏差"},
    "stats.col_median": {"en": "Median", "ja": "中央値"},
    "view.name_label": {"en": "View name:", "ja": "ビュー名:"},
    "view.invalid_name": {
        "en": "That view name can't be used:\n{error}",
        "ja": "ビュー名が使用できません:\n{error}",
    },
    "settings.save_failed": {
        "en": "The settings were applied but could not be saved to disk; they will be lost when the app closes.",
        "ja": "設定は反映しましたが、ディスクに保存できませんでした。アプリを閉じると失われます。",
    },
    "view.save_failed": {"en": "Could not save the view:\n{error}", "ja": "ビューを保存できませんでした:\n{error}"},
    "view.saved": {"en": "Saved: {path}", "ja": "保存しました: {path}"},
    "export.busy": {
        "en": "Plots are still being drawn. Please try again once drawing has finished.",
        "ja": "プロットを描画中です。描画完了後にもう一度実行してください。",
    },
    "export.failed": {"en": "Could not save the image:\n{error}", "ja": "画像を保存できませんでした:\n{error}"},
    "export.saved": {"en": "Image saved: {path}", "ja": "画像を保存しました: {path}"},
    "export.stamp_view": {"en": "View: {name}", "ja": "ビュー: {name}"},
    "export.stamp_range": {"en": "Range: {start} – {end}", "ja": "表示範囲: {start} – {end}"},
    "export.stamp_exported": {"en": "Exported: {when}", "ja": "出力日時: {when}"},
    "export.filter_png": {"en": "PNG image (*.png)", "ja": "PNG画像 (*.png)"},
    "export.filter_jpeg": {"en": "JPEG image (*.jpg *.jpeg)", "ja": "JPEG画像 (*.jpg *.jpeg)"},
    "export.filter_tiff": {"en": "TIFF image (*.tif *.tiff)", "ja": "TIFF画像 (*.tif *.tiff)"},
    "export.filter_svg": {"en": "SVG image (*.svg)", "ja": "SVG画像 (*.svg)"},
    "export.nothing_to_export": {
        "en": "There are no plotted variables to save.",
        "ja": "保存するプロットがありません。",
    },
    "export.filter_pdf": {"en": "PDF document (*.pdf)", "ja": "PDF文書 (*.pdf)"},
    "view.none_saved": {"en": "There are no saved views.", "ja": "保存済みのビューがありません。"},
    "view.select_label": {"en": "View:", "ja": "ビュー:"},
    "view.manager_title": {"en": "Views", "ja": "ビュー"},
    "view.load_button": {"en": "Load", "ja": "読込"},
    "view.delete_button": {"en": "Delete", "ja": "削除"},
    "view.close_button": {"en": "Close", "ja": "閉じる"},
    "view.detail_source": {"en": "Source: {path}", "ja": "元ファイル: {path}"},
    "view.detail_source_tooltip": {"en": "Path type: {type}", "ja": "パス種別: {type}"},
    "view.detail_saved": {"en": "Saved: {time}", "ja": "保存日時: {time}"},
    "view.detail_time_range": {"en": "Time range: {start} \u2013 {end}", "ja": "時間範囲: {start} \u2013 {end}"},
    "view.detail_plot": {"en": "Plot {n} ({k} channels)", "ja": "プロット {n}（{k} チャンネル）"},
    "view.detail_derived": {"en": "Derived variables ({k})", "ja": "仮想変数（{k}）"},
    "view.detail_time_column": {"en": "Time axis: {name}", "ja": "時間軸: {name}"},
    "view.time_column_missing": {
        "en": "Time axis column {name} is not in the open file (kept the current time axis)",
        "ja": "時間軸の列 {name} がありません（今の時間軸のままです）",
    },
    "view.detail_downsample": {"en": "Downsampling: {state}", "ja": "ダウンサンプリング: {state}"},
    "view.state_on": {"en": "on", "ja": "オン"},
    "view.state_off": {"en": "off", "ja": "オフ"},
    "view.detail_missing_summary": {
        "en": "{m} channel(s) not in the open file",
        "ja": "開いているファイルにないチャンネル: {m}件",
    },
    "view.detail_missing_suffix": {"en": " \u2014 not in the open file", "ja": " \u2014 開いているファイルにありません"},
    "view.detail_missing_tooltip": {
        "en": "This channel is not in the open file and will be skipped when the view is loaded.",
        "ja": "このチャンネルは開いているファイルにないため、ビュー読込時にスキップされます。",
    },
    "view.detail_unreadable": {"en": "Could not read this view: {error}", "ja": "このビューを読み込めません: {error}"},
    "view.delete_confirm": {
        "en": 'Delete the view "{name}"?\nThis cannot be undone.',
        "ja": "ビュー「{name}」を削除しますか？\nこの操作は元に戻せません。",
    },
    "view.delete_failed": {"en": "Could not delete the view:\n{error}", "ja": "ビューを削除できませんでした:\n{error}"},
    "view.format_required": {"en": "{path}: this item is required", "ja": "{path}: 必要な項目がありません"},
    "view.format_wrong_type": {
        "en": "{path}: {expected} required ({actual})",
        "ja": "{path}: {expected}が必要です（{actual}）",
    },
    "view.type_object": {"en": "an object", "ja": "オブジェクト"},
    "view.type_list": {"en": "a list", "ja": "リスト"},
    "view.type_string": {"en": "a string", "ja": "文字列"},
    "view.type_number": {"en": "a number", "ja": "数値"},
    "view.type_pair": {"en": "a list of two numbers", "ja": "数値2つのリスト"},
    "view.type_bool": {"en": "boolean", "ja": "真偽値"},
    "view.type_null": {"en": "null", "ja": "null"},
    "view.type_top": {"en": "(top level)", "ja": "（最上位）"},
    "view.load_error_title": {"en": "View load error", "ja": "ビュー読込エラー"},
    "view.load_failed_body": {
        "en": "Failed to load the view:\n{error}",
        "ja": "ビューの読み込みに失敗しました:\n{error}",
    },
    "view.partial_restore_failed": {
        "en": 'Could not fully restore the layout for view "{name}" (the data file itself '
        "is already loaded):\n{error}",
        "ja": "ビュー「{name}」のレイアウトを一部復元できませんでした"
        "（データファイルは読み込み済みです）:\n{error}",
    },
    "view.skipped_variables": {
        "en": 'The following variables from view "{name}" don\'t exist in the currently open '
        "file and were not restored (everything else was restored):\n{list}",
        "ja": "ビュー「{name}」のうち、現在開いているファイルに存在しない変数は"
        "復元されませんでした（他の部分は復元済みです）:\n{list}",
    },
    "settings.auto_redraw_checkbox": {
        "en": "Automatically redraw while panning/zooming (when off, press R to redraw manually)",
        "ja": "パン/ズーム時に自動で再描画する（オフの場合はRで手動再描画）",
    },
    "settings.downsample_density_label": {
        "en": "Downsample density (points per px)",
        "ja": "ダウンサンプリング密度 (px当たりの点数)",
    },
    "settings.eager_load_limit_label": {"en": "Large-column threshold", "ja": "大容量列のしきい値"},
    "settings.eager_load_limit_tooltip": {
        "en": "A column larger than this won't be fully loaded when its file is opened -- "
        "only the currently visible range is read from disk each time "
        "(detailed_specification.md 13.5.1). Takes effect the next time a file is opened.",
        "ja": "この値を超える列は、開いた時点で全体を読み込まず、\n"
        "表示中の範囲だけを都度ディスクから読み込むようになります\n"
        "（detailed_specification.md 13.5.1）。次回ファイルを開いた時から反映されます。",
    },
    "settings.section_general": {"en": "General", "ja": "全般"},
    "settings.section_scroll": {"en": "Scroll controls", "ja": "スクロール操作"},
    "settings.section_shortcuts": {"en": "Shortcuts", "ja": "ショートカット"},
    "settings.log_retention": {"en": "Log retention (days)", "ja": "ログの保存期間（日）"},
    "settings.log_retention_tooltip": {
        "en": "Log files older than this are deleted when the app starts (takes effect at the next start).",
        "ja": "これより古いログファイルはアプリ起動時に削除されます（次回の起動から有効）。",
    },
    "settings.open_log_folder": {"en": "Open log folder", "ja": "ログのフォルダを開く"},
    "settings.open_log_folder_tooltip": {
        "en": "Open the folder holding the operation log (attach today's *_ops_*.log to a bug report).",
        "ja": "操作ログのフォルダを開きます（不具合の報告には当日の *_ops_*.log を添付してください）。",
    },
    "settings.language_label": {"en": "Language", "ja": "言語"},
    "settings.language_restart_note": {
        "en": "Takes effect the next time the app is started.",
        "ja": "アプリを再起動すると反映されます。",
    },
    "shortcut.home": {"en": "Home (fit all)", "ja": "ホーム (全体表示)"},
    "shortcut.redraw": {"en": "Redraw", "ja": "再描画"},
    "shortcut.open_parquet": {"en": "Open", "ja": "開く"},
    "shortcut.add_plot": {"en": "Add plot", "ja": "プロット追加"},
    "shortcut.fft": {"en": "FFT", "ja": "周波数解析"},
    "shortcut.stats": {"en": "Stats", "ja": "統計"},
    "shortcut.save_view": {"en": "Save view", "ja": "ビュー保存"},
    "shortcut.load_view": {"en": "View manager", "ja": "ビュー管理"},
    "shortcut.toggle_downsample": {"en": "Toggle coarse preview", "ja": "ダウンサンプリング有効/無効切替"},
    "shortcut.hover_values": {"en": "Toggle value readout", "ja": "値の表示 切替"},
    "shortcut.cursor": {"en": "Toggle cursor", "ja": "カーソル有効/無効切替"},
    "shortcut.remove_series": {"en": "Remove selected series", "ja": "選択中の系列を削除"},
    "shortcut.export_image": {"en": "Save image", "ja": "画像を保存"},
    "scroll_action.x_zoom": {"en": "Time (X) zoom", "ja": "時間(X)軸ズーム"},
    "scroll_action.y_zoom": {"en": "Amp (Y) zoom", "ja": "Amp(Y)軸ズーム"},
    "scroll_action.pan": {"en": "Time pan", "ja": "時間移動（パン）"},
    "scroll_modifier.none": {"en": "Plain scroll", "ja": "無印スクロール"},
    "scroll_modifier.ctrl": {"en": "Ctrl + scroll", "ja": "Ctrl + スクロール"},
    "scroll_modifier.shift": {"en": "Shift + scroll", "ja": "Shift + スクロール"},
    "plot.move_up": {"en": "Move up", "ja": "上に移動"},
    "plot.move_down": {"en": "Move down", "ja": "下に移動"},
    "plot.remove": {"en": "Remove plot", "ja": "プロットを削除"},
    "plot.delete_series": {"en": "Delete series", "ja": "系列を削除"},
    "plot.toggle_series_visible": {"en": "Show/hide series", "ja": "系列の表示/非表示"},
    "plot.separate_series": {"en": "Separate series into new plot", "ja": "系列を新規プロットに分離"},
    "plot.link_x": {"en": "Link X axis", "ja": "X軸を連動"},
    "plot.link_x_tooltip": {
        "en": "Link this plot's X axis with the other plots (click to unlink / relink)",
        "ja": "このプロットのX軸を他のプロットと連動（クリックで連動を外す/戻す）",
    },
    "plot.stats_this_range": {"en": "Stats for this range", "ja": "この範囲で統計"},
    "plot.separate": {"en": "Separate into new plot", "ja": "新規プロットとして分離"},
    "expr.name_placeholder": {"en": "New variable name", "ja": "新しい変数名"},
    "expr.expr_placeholder": {
        "en": "e.g. abs(A) - sqrt(B), delta(rpm)",
        "ja": "例: abs(A) - sqrt(B), delta(rpm)",
    },
    "expr.expr_tooltip": {
        "en": "rolling_mean(a, n): moving average; a NaN in the window makes the result NaN.\n"
        "rolling_nanmean(a, n): moving average ignoring NaN samples (NaN only if the whole window is NaN).",
        "ja": "rolling_mean(a, n): 移動平均。窓の中に欠損(NaN)があると結果も欠損になる。\n"
        "rolling_nanmean(a, n): 欠損を除いた移動平均（窓が全部欠損のときだけ欠損）。",
    },
    "expr.add_button": {"en": "Add", "ja": "追加"},
    "expr.update_button": {"en": "Update", "ja": "更新"},
    "expr.cancel_button": {"en": "Cancel", "ja": "キャンセル"},
    "expr.warning_title": {"en": "Expression", "ja": "演算式"},
    "expr.warning_body": {
        "en": "Please enter both a variable name and an expression.",
        "ja": "変数名と式の両方を入力してください。",
    },
    "expr.error_title": {"en": "Expression error", "ja": "演算式エラー"},
    "variable.edit_expression": {"en": "Edit expression...", "ja": "式を編集..."},
    "variable.delete": {"en": "Delete", "ja": "削除"},
    "variable.delete_title": {"en": "Delete derived variable", "ja": "仮想変数の削除"},
    "variable.delete_confirm": {
        "en": "Delete derived variable \"{name}\"?\nIt will also be removed from every plot showing it.",
        "ja": "仮想変数「{name}」を削除しますか？\n表示中のプロットからも取り除かれます。",
    },
    "error.circular_definition": {
        "en": "Circular definition: \"{name}\" cannot reference itself or a variable derived from it.",
        "ja": "循環定義です: 「{name}」は自分自身や自分から派生した変数を参照できません。",
    },
    "error.derived_in_use": {
        "en": "\"{name}\" is still referenced by: {users}\nDelete or edit those first.",
        "ja": "「{name}」は次の変数から参照されています: {users}\n先にそちらを削除または編集してください。",
    },
    "error.not_derived_variable": {
        "en": "\"{name}\" is not a derived variable.",
        "ja": "「{name}」は仮想変数ではありません。",
    },
    "toolbar.functions": {"en": "Functions", "ja": "関数"},
    "toolbar.maps": {"en": "Maps", "ja": "マップ"},
    "shortcut.user_functions": {"en": "Function manager", "ja": "関数管理"},
    "shortcut.maps": {"en": "Map manager", "ja": "マップ管理"},
    "startup.map_warning": {"en": "Map not loaded: {detail}", "ja": "マップを読み込めません: {detail}"},
    "startup.function_warning": {"en": "User function: {detail}", "ja": "ユーザー関数: {detail}"},
    "startup.functions_unreadable": {"en": "The user functions file could not be read ({error}); functions are unavailable and saving is disabled.", "ja": "ユーザー関数のファイルを読めません（{error}）。関数は使えず、保存も無効です。"},
    "error.unknown_function": {"en": "Function/map \"{name}\" not found", "ja": "関数/マップ「{name}」がありません"},
    "function.manager_title": {"en": "User functions", "ja": "ユーザー関数"},
    "function.tab_definition": {"en": "Definition", "ja": "定義"},
    "function.tab_apply": {"en": "Apply to signals", "ja": "信号に適用"},
    "function.name": {"en": "Name", "ja": "名前"},
    "function.params": {"en": "Parameters", "ja": "引数"},
    "function.params_placeholder": {"en": "comma separated, e.g. flow_lpm, press_kpa", "ja": "カンマ区切り（例: flow_lpm, press_kpa）"},
    "function.name_placeholder": {"en": "e.g. Power_flow_by_press", "ja": "例: Power_flow_by_press"},
    "function.expression_placeholder": {"en": "e.g. flow_lpm * press_kpa / k", "ja": "例: flow_lpm * press_kpa / k"},
    "function.description_placeholder": {"en": "Flow x pressure -> power [kW]", "ja": "流量×圧力 → 動力 [kW]"},
    "function.constants_hint": {"en": "e.g. k = 60000", "ja": "例: k = 60000"},
    "function.expression_tooltip": {
        "en": "Operators: + - * / ** % //\n"
              "Functions: abs, sqrt, sin, cos, tan, exp, log, log10, sign, floor, ceil, round\n"
              "min(a, b), max(a, b), clip(x, lo, hi) (element-wise)\n"
              "delta(a), rolling_mean(a, window), rolling_nanmean(a, window)\n"
              "Use parameter and constant names as variables.",
        "ja": "演算子: + - * / ** % //\n"
              "関数: abs, sqrt, sin, cos, tan, exp, log, log10, sign, floor, ceil, round\n"
              "min(a, b), max(a, b), clip(x, 下限, 上限)（要素ごと）\n"
              "delta(a), rolling_mean(a, 窓幅), rolling_nanmean(a, 窓幅)\n"
              "引数名・定数名を変数として使えます。",
    },
    "function.insert_example": {"en": "Insert example", "ja": "例を入れる"},
    "function.example_confirm": {"en": "Replace the current definition fields with the example?", "ja": "現在の入力内容を例で置き換えますか？"},
    "function.constants": {"en": "Constants", "ja": "定数"},
    "function.const_name": {"en": "Name", "ja": "名前"},
    "function.const_default": {"en": "Default", "ja": "デフォルト値"},
    "function.add_constant": {"en": "Add row", "ja": "行を追加"},
    "function.remove_constant": {"en": "Remove row", "ja": "行を削除"},
    "function.expression": {"en": "Expression", "ja": "式"},
    "function.description": {"en": "Description", "ja": "説明"},
    "function.new": {"en": "New", "ja": "新規"},
    "function.save": {"en": "Save", "ja": "保存"},
    "function.delete": {"en": "Delete", "ja": "削除"},
    "function.bad_default": {"en": "Constant \"{name}\": \"{text}\" is not a number.", "ja": "定数「{name}」: 「{text}」は数値ではありません。"},
    "function.save_disabled": {"en": "The user functions file could not be read at startup, so saving is disabled to avoid overwriting it.", "ja": "起動時にユーザー関数のファイルを読めなかったため、上書きを避けて保存を無効にしています。"},
    "function.save_failed": {"en": "Could not save the functions file: {error}", "ja": "関数ファイルを保存できませんでした: {error}"},
    "function.rename_in_use": {"en": "\"{name}\" is in use and cannot be renamed: {users}", "ja": "「{name}」は使用中のため名前を変更できません: {users}"},
    "function.breaks_function": {"en": "This change breaks function \"{name}\": {reason}", "ja": "この変更で関数「{name}」が成り立たなくなります: {reason}"},
    "function.rolled_back": {"en": "Not saved: these derived variables would fail to recompute.", "ja": "保存を取り消しました。次の仮想変数を再計算できません。"},
    "function.saved": {"en": "Saved \"{name}\".", "ja": "「{name}」を保存しました。"},
    "function.delete_in_use": {"en": "\"{name}\" is in use and cannot be deleted: {users}", "ja": "「{name}」は使用中のため削除できません: {users}"},
    "function.delete_confirm": {"en": "Delete function \"{name}\"?", "ja": "関数「{name}」を削除しますか？"},
    "function.user_function_ref": {"en": "function {name}", "ja": "関数 {name}"},
    "function.apply_select": {"en": "Select a function in the list.", "ja": "一覧から関数を選んでください。"},
    "function.drop_here": {"en": "Drag a variable here", "ja": "変数をここへドラッグ"},
    "function.result_name": {"en": "Result name", "ja": "結果の名前"},
    "function.create": {"en": "Create", "ja": "作成"},
    "function.args_missing": {"en": "Fill in every parameter.", "ja": "すべての引数を入力してください。"},
    "function.result_missing": {"en": "Enter a name for the result.", "ja": "結果の名前を入力してください。"},
    "function.created": {"en": "Created \"{name}\" = {expression}", "ja": "「{name}」= {expression} を作成しました"},
    "map.manager_title": {"en": "Maps", "ja": "マップ"},
    "map.import_title": {"en": "Import map", "ja": "マップの取り込み"},
    "map.import_button": {"en": "Import...", "ja": "取り込み..."},
    "map.reimport_button": {"en": "Re-import", "ja": "再取り込み"},
    "map.delete_button": {"en": "Delete", "ja": "削除"},
    "map.apply_settings": {"en": "Apply settings", "ja": "設定を適用"},
    "map.browse": {"en": "Browse...", "ja": "参照..."},
    "map.csv_filter": {"en": "CSV files (*.csv);;All files (*)", "ja": "CSVファイル (*.csv);;すべてのファイル (*)"},
    "map.file": {"en": "CSV file", "ja": "CSVファイル"},
    "map.name": {"en": "Name", "ja": "名前"},
    "map.kind": {"en": "Kind", "ja": "種類"},
    "map.kind_auto": {"en": "Auto", "ja": "自動判定"},
    "map.kind_curve": {"en": "Curve", "ja": "カーブ"},
    "map.kind_map": {"en": "Map", "ja": "マップ"},
    "map.interpolation": {"en": "Interpolation", "ja": "補間方法"},
    "map.interp_linear": {"en": "Linear", "ja": "線形"},
    "map.interp_nearest": {"en": "Nearest", "ja": "最近傍"},
    "map.interp_previous": {"en": "Previous (step)", "ja": "前の値で保持（階段）"},
    "map.interp_spline": {"en": "Cubic spline", "ja": "3次スプライン"},
    "map.extrapolation": {"en": "Outside the table", "ja": "テーブルの範囲外"},
    "map.extrap_clamp": {"en": "Hold edge value", "ja": "端の値を保持"},
    "map.extrap_zero": {"en": "Zero", "ja": "0"},
    "map.extrap_linear": {"en": "Extend linearly", "ja": "線形で延長"},
    "map.extrap_nan": {"en": "NaN", "ja": "NaN"},
    "map.none_selected": {"en": "No map selected.", "ja": "マップが選択されていません。"},
    "map.info": {"en": "Source: {source}    Imported: {imported}", "ja": "元ファイル: {source}    取り込み日時: {imported}"},
    "map.preview_table": {"en": "Table", "ja": "テーブル"},
    "map.preview_result": {"en": "Interpolated", "ja": "補間結果"},
    "map.before": {"en": "Before (saved)", "ja": "変更前（保存済み）"},
    "map.after": {"en": "After (editing)", "ja": "変更後（編集中）"},
    "map.after_invalid_title": {"en": "After: not drawn (invalid values)", "ja": "変更後: 値が不正なため描画しません"},
    "map.after_invalid": {
        "en": "The edited values are not valid, so \"After\" is not drawn: {reason}",
        "ja": "編集中の値が不正なため、変更後は描画しません: {reason}",
    },
    "map.imported": {"en": "Imported \"{name}\".", "ja": "「{name}」を取り込みました。"},
    "map.settings_saved": {"en": "Settings for \"{name}\" saved.", "ja": "「{name}」の設定を保存しました。"},
    "map.rolled_back": {"en": "Not applied: these derived variables would fail to recompute.", "ja": "適用を取り消しました。次の仮想変数を再計算できません。"},
    "map.source_missing": {"en": "Source file not found: {path}", "ja": "元のファイルが見つかりません: {path}"},
    "map.reimport_failed": {"en": "Re-import failed.", "ja": "再取り込みに失敗しました。"},
    "map.reimported": {"en": "Re-imported \"{name}\".", "ja": "「{name}」を再取り込みしました。"},
    "map.delete_in_use": {"en": "\"{name}\" is in use and cannot be deleted: {users}", "ja": "「{name}」は使用中のため削除できません: {users}"},
    "map.delete_confirm": {"en": "Delete map \"{name}\"?", "ja": "マップ「{name}」を削除しますか？"},
    "map.edit_button": {"en": "Edit...", "ja": "編集..."},
    "map.edit_title": {"en": "Edit map", "ja": "マップの編集"},
    "map.axis_names": {"en": "Axis names (blank = default)", "ja": "軸の名前（空欄 = 既定）"},
    "map.axis_x": {"en": "X axis", "ja": "X軸"},
    "map.axis_y": {"en": "Y axis", "ja": "Y軸"},
    "map.axis_z": {"en": "Z (value)", "ja": "Z（値）"},
    "map.rename_in_use": {"en": "\"{name}\" is in use and cannot be renamed: {users}", "ja": "「{name}」は使用中のため名前を変更できません: {users}"},
    "map.renamed": {"en": "Renamed \"{old}\" to \"{new}\". Saved views still using the old name will skip it with a warning.", "ja": "「{old}」を「{new}」に変更しました。保存済みのビューに古い名前が残っていると、読み込み時にスキップして警告します。"},
    "map.edited": {"en": "Saved \"{name}\".", "ja": "「{name}」を保存しました。"},
    "map.values_save": {"en": "Save values", "ja": "値を保存"},
    "map.values_revert": {"en": "Revert", "ja": "元に戻す"},
    "map.values_saved": {"en": "Saved the values of \"{name}\".", "ja": "「{name}」の値を保存しました。"},
    "map.unsaved_confirm": {"en": "\"{name}\" has unsaved value edits. Save them?", "ja": "「{name}」の値の編集が保存されていません。保存しますか？"},
    "map.reimport_edited_confirm": {"en": "\"{name}\" has been edited (or has unsaved edits). Re-importing overwrites those edits with the source file. Continue?", "ja": "「{name}」は編集されています（または未保存の編集があります）。再取り込みすると編集内容が元のファイルで上書きされます。続けますか？"},
    "error.bad_series_shape": {
        "en": "The result has shape {shape}; a series needs a single value or {n} values.",
        "ja": "結果の形が{shape}です。系列には単一の値または{n}個の値が必要です。",
    },
    "function.reserved_name": {
        "en": "\"{name}\" is the name of a function in the file that could not be loaded; it is kept as is. Fix or remove it in the file first.",
        "ja": "「{name}」は読み込めなかった関数の名前としてファイルに残っています（そのまま保持されます）。先にファイル側を修正・削除してください。",
    },
    "folder.open_dialog_title": {"en": "Open Parquet file", "ja": "Parquetファイルを開く"},
    "fnref.desc.abs": {"en": "Absolute value of each sample.", "ja": "各サンプルの絶対値。"},
    "fnref.desc.sqrt": {"en": "Square root of each sample (negative values give NaN).", "ja": "各サンプルの平方根（負の値は欠損になる）。"},
    "fnref.desc.delta": {"en": "First difference: a[i] - a[i-1] (the first sample is NaN).", "ja": "1階差分 a[i] - a[i-1]（先頭は欠損）。"},
    "fnref.desc.rolling_mean": {"en": "Moving average over a window of n samples. One NaN in the window makes the result NaN (NaN propagates).", "ja": "n点の窓の移動平均。窓の中に欠損(NaN)が1つでもあると結果も欠損になる（欠損が伝播する）。"},
    "fnref.desc.rolling_nanmean": {"en": "Moving average over n samples ignoring NaN; NaN only when the whole window is NaN.", "ja": "n点の窓の、欠損を除いた移動平均。窓が全部欠損のときだけ欠損になる。"},
    "fnref.desc.sin": {"en": "Sine (argument in radians).", "ja": "正弦（引数はラジアン）。"},
    "fnref.desc.cos": {"en": "Cosine (argument in radians).", "ja": "余弦（引数はラジアン）。"},
    "fnref.desc.tan": {"en": "Tangent (argument in radians).", "ja": "正接（引数はラジアン）。"},
    "fnref.desc.exp": {"en": "e raised to the power of each sample.", "ja": "eの各サンプル乗。"},
    "fnref.desc.log": {"en": "Natural logarithm.", "ja": "自然対数。"},
    "fnref.desc.log10": {"en": "Base-10 logarithm.", "ja": "常用対数。"},
    "fnref.desc.sign": {"en": "Sign of each sample: -1, 0 or 1.", "ja": "符号（-1, 0, 1）。"},
    "fnref.desc.floor": {"en": "Round down to an integer.", "ja": "切り捨て（小さい方の整数へ）。"},
    "fnref.desc.ceil": {"en": "Round up to an integer.", "ja": "切り上げ（大きい方の整数へ）。"},
    "fnref.desc.round": {"en": "Round to the nearest integer (halves go to even).", "ja": "四捨五入（ちょうど半分は偶数へ）。"},
    "fnref.desc.min": {"en": "Element-wise minimum of a and b (not the Python reduction min).", "ja": "aとbの要素ごとの小さい方（Pythonの縮約minとは異なる）。"},
    "fnref.desc.max": {"en": "Element-wise maximum of a and b, e.g. max(a, 0) clamps a to be at least 0.", "ja": "aとbの要素ごとの大きい方。例: max(a, 0)はaを0以上に切り上げる。"},
    "fnref.desc.clip": {"en": "Limit each sample of x to the range lo..hi (element-wise).", "ja": "xの各サンプルを下限lo〜上限hiの範囲に収める（要素ごと）。"},
    "fnref.title": {"en": "Function reference", "ja": "関数一覧"},
    "fnref.button": {"en": "Functions...", "ja": "関数一覧..."},
    "fnref.button_tooltip": {"en": "Show the functions, operators, user functions and maps usable in expressions.", "ja": "式で使える関数・演算子・ユーザー関数・マップの一覧を表示する。"},
    "fnref.search_placeholder": {"en": "Search by name or description", "ja": "名前・説明で検索"},
    "fnref.category": {"en": "Category", "ja": "分類"},
    "fnref.cat_all": {"en": "All", "ja": "すべて"},
    "fnref.cat_basic": {"en": "Basic", "ja": "基本"},
    "fnref.cat_analysis": {"en": "Analysis", "ja": "解析"},
    "fnref.cat_trig": {"en": "Trigonometric", "ja": "三角関数"},
    "fnref.cat_explog": {"en": "Exponential / log", "ja": "指数・対数"},
    "fnref.cat_round": {"en": "Rounding", "ja": "丸め"},
    "fnref.cat_compare": {"en": "Compare / range", "ja": "比較・範囲"},
    "fnref.cat_operator": {"en": "Operator", "ja": "演算子"},
    "fnref.cat_user": {"en": "User function", "ja": "ユーザー関数"},
    "fnref.cat_curve": {"en": "Curve", "ja": "カーブ"},
    "fnref.cat_map": {"en": "Map", "ja": "マップ"},
    "fnref.col_name": {"en": "Name", "ja": "名前"},
    "fnref.col_category": {"en": "Category", "ja": "分類"},
    "fnref.col_signature": {"en": "Usage", "ja": "書き方"},
    "fnref.col_description": {"en": "Description", "ja": "説明"},
    "fnref.col_example": {"en": "Example", "ja": "使用例"},
    "fnref.insert": {"en": "Insert into expression", "ja": "式に挿入"},
    "fnref.insert_disabled_tooltip": {"en": "The expression field this window was opened from is closed.", "ja": "開いた元の式の入力欄が閉じられています。"},
    "fnref.map_desc": {"en": "Lookup {kind}. Interpolation: {interp}. Outside the table: {extrap}.", "ja": "ルックアップ{kind}。補間: {interp}。範囲外: {extrap}。"},
    "fnref.user_no_description": {"en": "(no description)", "ja": "（説明なし）"},
    "fnref.notes": {"en": "Missing values (NaN): rolling_mean propagates NaN (one NaN in the window makes the result NaN); rolling_nanmean ignores NaN; statistics (Stats) ignore NaN. min/max/clip are element-wise.", "ja": "欠損(NaN)の扱い: rolling_meanは欠損が伝播する（窓の中に1つでもあれば結果も欠損）。rolling_nanmeanは欠損を除いて平均する。統計は欠損を除いて計算する。min/max/clipは要素ごとの演算。"},
    "fnref.op.add": {"en": "Addition", "ja": "加算"},
    "fnref.op.sub": {"en": "Subtraction (or unary minus)", "ja": "減算（単項のマイナスも可）"},
    "fnref.op.mul": {"en": "Multiplication", "ja": "乗算"},
    "fnref.op.div": {"en": "Division", "ja": "除算"},
    "fnref.op.pow": {"en": "Power", "ja": "べき乗"},
    "fnref.op.mod": {"en": "Remainder", "ja": "剰余"},
    "fnref.op.floordiv": {"en": "Floor division", "ja": "切り捨て除算"},
}


def set_language(language: str) -> None:
    """Falls back to DEFAULT_LANGUAGE for anything not in LANGUAGES -- same
    defensive stance as io.settings.load_settings() for a hand-edited/corrupt
    settings.json.
    """
    global _current_language
    _current_language = language if language in LANGUAGES else DEFAULT_LANGUAGE


def get_language() -> str:
    return _current_language


def tr(key: str, **kwargs: object) -> str:
    entry = _STRINGS[key]
    text = entry.get(_current_language, entry[DEFAULT_LANGUAGE])
    return text.format(**kwargs) if kwargs else text


def shortcut_label(shortcut_id: str) -> str:
    return tr(f"shortcut.{shortcut_id}")


def scroll_action_label(action_id: str) -> str:
    return tr(f"scroll_action.{action_id}")


def scroll_modifier_label(modifier_id: str) -> str:
    return tr(f"scroll_modifier.{modifier_id}")
