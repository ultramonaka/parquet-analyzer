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
    "toolbar.stats": {"en": "Stats", "ja": "統計"},
    "toolbar.save_view": {"en": "Save view", "ja": "ビュー保存"},
    "toolbar.load_view": {"en": "Load view", "ja": "ビュー読込"},
    "toolbar.toggle_downsample": {"en": "Allow coarse preview", "ja": "簡易表示を許可"},
    "toolbar.cursor": {"en": "Cursor", "ja": "カーソル"},
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
    "view.saved": {"en": "Saved: {path}", "ja": "保存しました: {path}"},
    "view.none_saved": {"en": "There are no saved views.", "ja": "保存済みのビューがありません。"},
    "view.select_label": {"en": "View:", "ja": "ビュー:"},
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
    "shortcut.load_view": {"en": "Load view", "ja": "ビュー読込"},
    "shortcut.toggle_downsample": {"en": "Toggle coarse preview", "ja": "ダウンサンプリング有効/無効切替"},
    "shortcut.cursor": {"en": "Toggle cursor", "ja": "カーソル有効/無効切替"},
    "scroll_action.x_zoom": {"en": "Time (X) zoom", "ja": "時間(X)軸ズーム"},
    "scroll_action.y_zoom": {"en": "Amp (Y) zoom", "ja": "Amp(Y)軸ズーム"},
    "scroll_action.pan": {"en": "Time pan", "ja": "時間移動（パン）"},
    "scroll_modifier.none": {"en": "Plain scroll", "ja": "無印スクロール"},
    "scroll_modifier.ctrl": {"en": "Ctrl + scroll", "ja": "Ctrl + スクロール"},
    "scroll_modifier.shift": {"en": "Shift + scroll", "ja": "Shift + スクロール"},
    "plot.move_up": {"en": "Move up", "ja": "上に移動"},
    "plot.move_down": {"en": "Move down", "ja": "下に移動"},
    "plot.remove": {"en": "Remove from this plot", "ja": "このプロットから削除"},
    "plot.separate": {"en": "Separate into new plot", "ja": "新規プロットとして分離"},
    "expr.name_placeholder": {"en": "New variable name", "ja": "新しい変数名"},
    "expr.expr_placeholder": {
        "en": "e.g. abs(A) - sqrt(B), delta(rpm)",
        "ja": "例: abs(A) - sqrt(B), delta(rpm)",
    },
    "expr.add_button": {"en": "Add", "ja": "追加"},
    "expr.warning_title": {"en": "Expression", "ja": "演算式"},
    "expr.warning_body": {
        "en": "Please enter both a variable name and an expression.",
        "ja": "変数名と式の両方を入力してください。",
    },
    "expr.error_title": {"en": "Expression error", "ja": "演算式エラー"},
    "folder.open_dialog_title": {"en": "Open Parquet file", "ja": "Parquetファイルを開く"},
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
