# Plots (display, interaction, cursors, layout)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: the main window's plots (multiple plots, overlays), the navigator,
zoom/pan interaction, time-axis formatting, cursors, plot height / reordering / time labels,
per-series select / remove / show-hide, independent X axes per plot.

## Requirements

#### 5.1 Main window
- Multiple plots: several time-series graphs can be shown tiled (split vertically, etc.).
- Overlays: several variables can be overlaid in one plot area.
- Each plot can be zoomed and panned independently.
- Each plot shows a legend (the list of variable names), updated as series are added/removed.
- "Move up" / "Move down" in a plot's context menu swaps the vertical order (added 2026-09-17).
  Choosing "Move up" on the top plot or "Move down" on the bottom plot does nothing (moves out of
  range are ignored). After reordering, the X-axis linking between time-domain plots
  ([5.3](#53-interaction-v2-settled-after-real-use-feedback)) is always rebuilt around **the plot
  currently on top** (details in `detailed_specification.ja.md` ch. 5).
- See [5.15](#515-changing-plot-height-reordering-added-2026-10-02-user-request) for changing plot
  height and drag-reordering, [5.16](#516-time-labels-only-where-needed-added-2026-10-02-user-request)
  for how time labels are shown, [5.17](#517-per-series-operations-select-remove-showhide-added-2026-10-02-user-request)
  for per-series select / remove / show-hide, and
  [5.18](#518-independent-x-axis-per-plot-added-2026-10-02-user-request) for independent X axes per
  plot (all added 2026-10-02).

#### 5.2 Navigator
- A navigator shared by all plots ~~or one per plot~~ (a reduced view + a rectangle showing the
  current visible range) is always on screen.
- Dragging/clicking on the navigator moves the visible range.
- It moves along the time axis; the vertical axis stays fixed. Coarse is fine.
- The variable shown as the overview waveform is **the first variable overlaid on the top plot**
  (changed 2026-09-17; it used to always show the first variable in the file's column order). If
  nothing is overlaid yet it falls back to that old behavior. It follows every change to the top
  plot's content (adding overlays, reordering, etc.). Hidden series
  ([5.17](#517-per-series-operations-select-remove-showhide-added-2026-10-02-user-request)) are
  skipped, and the first shown series is used.
- If some plots' X axes are not linked
  ([5.18](#518-independent-x-axis-per-plot-added-2026-10-02-user-request)), the navigator shows and
  controls only the **linked group's** range (added 2026-10-02).

#### 5.3 Interaction (v2, settled after real-use feedback)
| Action | Effect |
|---|---|
| Mouse wheel scroll | zoom the X axis (time) only |
| Ctrl + scroll | zoom the Y axis (amplitude) only |
| Shift + scroll | pan along the X axis (time) (move, no zoom) |
| H key / Home button | reset the view to the full data range |
| R key / Redraw button | redraw the plots for the current range (including the downsampling recompute) |

> **Changes from v1**: originally plain scroll = zoom both axes, Ctrl = X axis only, Shift = Y axis
> only, and "zoom both axes" could be turned off in the settings (the old `both_axis_scroll_zoom`
> setting). In actual use the feedback was that it felt bad, so it was changed to the current scheme
> that fully separates three operations: "plain = time zoom, Ctrl = amplitude zoom, Shift = time
> move". With plain scroll always dedicated to time zoom, the both-axes zoom setting was no longer
> needed and was removed.
>
> Then on the real machine (macOS) Ctrl/Shift had no effect and only time zoom ever happened. The
> causes were two: Qt reports the physical Ctrl key as `MetaModifier` on macOS (Cmd/Ctrl swap), and
> some trackpads report Shift+scroll as a horizontal wheel value. Both are handled (details in
> [detailed_specification ch. 4](#4-interaction-settled-113-v2)).

- The ticks of the time (X) axis change dynamically with the zoom level: date/month over wide
  ranges, automatically refined down to hour:minute:second (milliseconds) when zoomed into seconds
  to minutes.

#### 5.13 Cursors (added 2026-09-19, user request)
- The "Cursor" toggle on the toolbar switches cursor placement mode on/off. Off by default; neither
  state affects ordinary pan/zoom.
- While on, clicking anywhere on a time-domain plot places a cursor: a draggable vertical line and
  a text overlay showing its X (time) value and the nearest sample value of **each series currently
  shown on the same plot**. With several plots open, the same cursor (same X) is placed **on every
  other time-domain plot** too, each showing the values of its own series (frequency-axis (FFT) plots
  are excluded because their X domain differs).
- Clicking an existing cursor directly removes it (from every plot showing it). Dragging moves it
  (synced across every plot showing it likewise). Turning the toggle off doesn't delete existing
  cursors but **hides** them (can't be dragged or removed, not shown). Turning it on again restores
  them at the same positions. While off, mouse actions are treated as ordinary pan/zoom.
- Any number of cursors can be placed at once, independently.
- Opening a new file or switching the time-axis column clears all cursors (an X position on the old
  time axis has no meaning on the new one).
- Details in `detailed_specification.ja.md` ch. 16.

#### 5.15 Changing plot height, reordering (added 2026-10-02, user request)
- **The bug this change fixes**: "Move up" / "Move down" squashed the moved plot (three rows of
  about 225 px became 293/88/293 px after one move, getting worse with every move). Reordering must
  not change any plot's height.
- **Changing height**: drag the border between two plots to change their heights. Adding or removing
  a plot redistributes all heights evenly (as before).
- **Drag to reorder**: each plot gets a narrow handle column at its left edge. Dragging its grip up
  or down shows a line at the insertion point, and releasing moves the plot there (every plot keeps
  its height). A time-domain plot can't be dropped among frequency-domain (FFT) plots (nor the
  reverse; no insertion line is shown at such positions) — the same constraint as "Move up" / "Move
  down". The context menu's "Move up" / "Move down" stay.
- **Height ratios are saved in the view** and restored on load (ratios, not pixels, so the
  proportions are the same in a window of a different size). Views saved before this change load
  with even heights.
- **Equal Y-axis widths** (added 2026-10-02, pointed out by the user during the real-machine check):
  make the Y axes (the tick-label column) of stacked plots as wide as the widest. When tick text
  lengths differ between plots (e.g. `-0.5` vs `2`), the left edges of the plot areas shift, so the
  same time isn't at the same position above and below, which looks wrong. It follows changes of the
  tick text from zooming etc.
- Details in [detailed_specification ch. 22](#22-plot-height-reordering-time-labels-added-2026-10-02-user-request).

#### 5.16 Time labels only where needed (added 2026-10-02, user request)
- Time-domain plots whose X axes are linked show the same time range, so repeating the same
  date-time tick labels under every plot wastes height. **Only the bottom one of the linked plots
  shows time labels**; the others keep their tick marks and grid and hide only the label text.
- Plots whose X axis is **not linked** ([5.18](#518-independent-x-axis-per-plot-added-2026-10-02-user-request))
  have a different range, so they always show their own time labels. Frequency-domain (FFT) plots
  always show their own Hz labels.
- Reapplied after every add, remove, reorder and linking change.
- Image export ([export 5.14](./export.md)) follows the same rule.

#### 5.17 Per-series operations: select, remove, show/hide (added 2026-10-02, user request)
- **Select**: clicking a series' **name** in a plot's legend selects that series (thicker line,
  bold name in the legend). Only one selection across all plots. Click the same name again, click
  an empty part of a plot, or press Esc to deselect.
- **Remove**: the **Delete** key (also macOS's "delete" key = Backspace) removes the selected series
  from its plot. The key can be changed in the settings (default `Del`). It doesn't react while
  typing in a text field such as the expression input. Removing the last series leaves the plot
  itself, empty.
- **Show/hide**: clicking a series' **line sample** in the legend (the short line left of the name)
  toggles that series' visibility (pyqtgraph's standard behavior already toggled the look; this makes
  it a proper state). A hidden series' legend entry is dimmed. Hidden series are **excluded** from:
  the default target of statistics ([analysis 5.12](./analysis.md)), the cursor value labels
  ([5.13](#513-cursors-added-2026-09-19-user-request)), image export ([export 5.14](./export.md)),
  fitting the Y range on "Home", and the navigator's overview waveform. The hidden state is saved in
  the view.
- **Context menu additions** (each with a submenu listing that plot's series): "Delete series ▸",
  "Show/hide series ▸", "Separate series into new plot ▸" (the existing "Separate into new plot"
  moves all series but the first; this moves just the one chosen).
- The existing context-menu item "Remove from this plot" actually removed **the whole plot** (which
  its wording didn't make clear), so it is renamed **"Remove plot"** (same behavior).
- Details in [detailed_specification ch. 23](#23-per-series-select-remove-showhide-added-2026-10-02-user-request).

#### 5.18 Independent X axis per plot (added 2026-10-02, user request)
- Each time-domain plot gets an **"X-axis linked"** on/off. It can be switched from the plot's context
  menu ("Link X axis", with a check) or from the chain icon in the handle column
  ([5.15](#515-changing-plot-height-reordering-added-2026-10-02-user-request)). Linked by default (as
  before).
- An unlinked plot zooms and pans on its own. Linking it again fits it to the linked group's current
  range.
- At least one time-domain plot is always linked: unlinking the last one isn't accepted (the menu
  item and icon are shown disabled). The same idea as the existing "at least one time-domain plot
  remains".
- What the linked group keeps doing:
  - **Navigator** ([5.2](#52-navigator)): shows and controls only the linked group's range.
  - **"The visible range"** of statistics and FFT (and the future XY and histogram): the linked
    group's range. An unlinked plot's context menu offers **"Stats for this range"**, computing
    statistics over that plot's own range.
  - **Home**: returns every plot to the full range, linked or not (the linking state doesn't change).
  - **Cursors**: synced by time across all time-domain plots as before. A cursor outside an unlinked
    plot's visible range is simply not visible on that plot.
- **Views** save each plot's linking state and the X range of unlinked plots. Views saved before this
  change load with all plots linked.
- **Image export**: linked plots share one aligned X axis as before; unlinked plots have their own X
  axis and time labels. The annotation's "range" line is the linked group's range.
- Development feature flag `independent_x_axis` ([overview 9](../overview.md)).
- Details in [detailed_specification ch. 24](#24-independent-x-axis-per-plot-added-2026-10-02-user-request).

#### 5.32 Showing the values under the mouse (hover readout) (added 2026-10-03, user request)
- Hovering over a plot shows a dotted vertical line (the hover line) at that position, and the same
  time's line on **every plot whose X axis is linked**. At the **fixed top right** of each plot, the
  time and the value of each shown series are displayed (in the same format as the cursor labels).
  They disappear when the mouse leaves the plot.
- Values: while the mouse moves, the value of the nearest point of the data drawn on screen
  (decimated) is shown at once (prefixed "≈"); after the mouse stops for about 100 ms it is replaced
  by the real sample value (the "≈" goes away). For columns entirely in memory, real sample values
  from the start. The screen must not stutter even with large files.
- On/off with the "Values" toggle on the toolbar (on by default, saved in the settings, shortcut
  configurable).
- Hidden series aren't shown. FFT plots show Hz and values and are not linked with other plots. The
  XY plot window is out of scope.
- It looks different from placed cursors (5.13), and the labels don't overlap. Not included in image
  export. Mouse movement isn't written to the operation log (only on/off is recorded).
- Details in [ch. 39](#39-showing-the-values-under-the-mouse-hover-readout-added-2026-10-03-user-request).

## Design

### 4. Interaction (settled, 11.3, v2)

| Action | Effect (current defaults — configurable, see below) |
|---|---|
| Mouse wheel scroll | pan along the X axis (time) (move only, no zoom) |
| Ctrl + scroll | zoom the X axis (time) only |
| Shift + scroll | zoom the Y axis (amplitude) only |
| H key / Home button | reset the view to the full data range |
| R key / Redraw button | redraw the plots for the current range (including the downsampling recompute) |

- Implemented in `TimePlotWidget.wheelEvent` (`ui/plot_widget.py`). The three operations are fully
  separated (no modifier serves two or more actions), and **which modifier does which action is
  configurable** (`Settings.scroll_bindings`, a bijection from `{"none", "ctrl", "shift"}` to
  `{"x_zoom", "y_zoom", "pan"}`, in the settings dialog's "Scroll controls" section; for how this
  differs from the abolished "zoom both axes" setting (v1), and why the table above doesn't match
  the original v2 assignment, see the v3 background below).
- The Shift pan step is "15% of the current visible X span per wheel notch (120)"
  (`_PAN_STEP_FRACTION = 0.15`). The direction is fixed as "scroll up = back in time". If the reverse
  feels more natural, just flip the sign in `wheelEvent`.
- **Two cross-platform traps** (found from real-machine feedback, handled in `wheelEvent`):
  - On macOS, Qt reports the physical Ctrl key as `Qt.KeyboardModifier.MetaModifier` (Qt swaps
    Cmd/Ctrl to match cross-platform shortcut conventions). Checking only `ControlModifier` makes
    physical-Ctrl operations fall through to "plain scroll" (time zoom). Handled by treating both
    `ControlModifier | MetaModifier` as "Ctrl".
  - Some trackpads/OSes report the Shift+vertical-scroll gesture as a horizontal wheel event (the
    value is in `.x()` of `angleDelta()`, not `.y()`). Looking only at `.y()` makes Shift-pan silently
    do nothing. Handled by using whichever of `angle_delta.y() or angle_delta.x()` is non-zero.

> **Background (v1→v2)**: the original design was plain scroll = zoom both axes, Ctrl = X-axis zoom
> only, Shift = Y-axis zoom only, with plain scroll's both-axes zoom switchable to X-only via the
> `both_axis_scroll_zoom` setting (the old spec; before that, plain and Ctrl both zoomed X, a
> contradiction that version fixed). After actual use the feedback was "it feels bad", so it was
> changed to fully separated operations: plain = time zoom, Ctrl = amplitude zoom, Shift = time move.
> With plain scroll always dedicated to time zoom, the setting switching between both-axes and
> single-axis became unnecessary and was removed (the `Settings.both_axis_scroll_zoom` field and the
> `TimePlotWidget`/`PlotGridWidget` constructor arguments).

> **v3 (2026-09-19, user request): which modifier does which action became configurable again, but
> it is not v1's setting.** v1's setting switched **the scheme itself** — "does plain scroll zoom both
> axes or only X" — and was abolished because both schemes turned out to feel bad. This one is a
> different kind: the fixed v2 scheme of three fully separated operations stays (no modifier serves
> two or more actions; every action is always reachable by exactly one modifier), and only **which
> modifier does which action** can move. `Settings.scroll_bindings: dict[str, str]`
> (`io/settings.py`) is a bijection from `{"none", "ctrl", "shift"}` to `{"x_zoom", "y_zoom",
> "pan"}`. At first the v2 scheme (`{"none": "x_zoom", "ctrl": "y_zoom", "shift": "pan"}`) was the
> default, but it was changed the same day after actually using it under the new setting: the actual
> values (`{"none": "pan", "ctrl": "x_zoom", "shift": "y_zoom"}`) were read directly from the user's
> own `cfg/parquet_analyzer/settings.json` and made the new `DEFAULT_SCROLL_BINDINGS` — at the
> explicit request "make the settings I've already tuned the default for new installs, not a guess".
> `normalize_scroll_bindings()` falls back to the default wholesale, without attempting partial
> repair, unless the mapping is a complete bijection (missing modifiers, duplicate or unknown actions,
> etc.) — a half-broken mapping could make some action unreachable by scrolling at all, and
> `TimePlotWidget.wheelEvent` has no way to detect and recover from that on the spot.
> `TimePlotWidget.scroll_bindings` (normalized in `__init__`, changeable at runtime with
> `set_scroll_bindings()`) is consulted in `wheelEvent`, replacing the old `if shift: ... elif ctrl:
> ...` branching (shift still takes priority over ctrl when both are pressed). It is propagated to
> `PlotGridWidget` (constructor + `set_scroll_bindings()`, applied to plots added later too) and
> `MainWindow` (constructor argument, `apply_settings()`) along the same path as
> `downsample_pixel_ratio`. `SettingsDialog` has one `QComboBox` per modifier (each offering all three
> actions). Choosing an action already assigned to another modifier swaps the two combos
> automatically, so two modifiers never stay assigned to the same action. Tests:
> `test_normalize_scroll_bindings_accepts_a_valid_permutation`,
> `test_normalize_scroll_bindings_falls_back_to_default_on_anything_invalid`,
> `test_load_settings_falls_back_when_scroll_bindings_is_corrupt`
> (`tests/parquet_analyzer/test_io.py`),
> `test_custom_scroll_bindings_swap_which_modifier_zooms_which_axis`,
> `test_set_scroll_bindings_updates_behavior_live`
> (`tests/parquet_analyzer/test_plot_widget.py`),
> `test_settings_dialog_scroll_binding_combo_swaps_the_conflicting_modifier`, and an extension of the
> existing `test_apply_settings_propagates_to_plots_and_actions`
> (`tests/parquet_analyzer/test_settings_dialog.py`).

#### 4.1 Dynamic time-axis format

- The X (time) axis ticks change granularity automatically with the zoom level: date/month when a
  wide range is shown, switching automatically down to hour:minute:second (milliseconds) when zoomed
  into seconds to minutes. E.g. month labels like "Feb", "Mar" over a 4-year span; sub-second labels
  like "50.250" over a 3-second span.
- Implemented with pyqtgraph's standard `DateAxisItem` as the bottom axis of `TimePlotWidget`/
  `NavigatorWidget` (`pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})`). The X data is assumed
  to be epoch seconds (`float`) (already converted by `MainWindow._to_numeric`).
- The X axis of a frequency-analysis (FFT) plot is frequency [Hz], not time, so it doesn't use
  `DateAxisItem` (`TimePlotWidget(..., x_axis_datetime=False)`, `plot.setLabel("bottom", "Frequency",
  units="Hz")`). Switched with `PlotGridWidget.add_plot(x_axis_datetime=...)`. Because the X axis
  means something different (time vs frequency), plots with `x_axis_datetime=False` are not X-linked
  with time plots (decided inside `PlotGridWidget.add_plot`).

### 5. Multi-plot / overlay operations (11.7)

- Dragging a variable from `VariablePanel` onto a plot adds it to that plot as a series (overlay).
- Plot context menu:
  - "Move up", "Move down" (swap the vertical order, added 2026-09-17;
    `PlotGridWidget.move_plot_up`/`move_plot_down`. Nothing happens at the boundaries (move up on the
    top plot, move down on the bottom plot))
  - "Remove from this plot" (`PlotGridWidget.remove_plot` — removes the plot row itself, including
    all overlaid series. There is no "−" button on the toolbar; this context menu is the only way to
    remove a plot)
  - "Separate into new plot" (moves all series but the selected one to a new plot)
  - (2026-10-02) "Remove from this plot" is renamed "Remove plot", and per-series "Delete series ▸",
    "Show/hide series ▸", "Separate series into new plot ▸" are added (ch. 23). "Link X axis" and
    "Stats for this range" are added (ch. 24). Reordering moves to a `QSplitter` to keep heights, and
    drag-reordering is added (ch. 22). The anchor's definition becomes "the first linked time-domain
    plot" (24.1).
- The toolbar's "+" button appends an empty plot row.
- **Rebuilding X-axis links after reordering/removing**: a new time plot is always X-linked to "the
  current top time plot" (star topology). Moving up/down changes the order, so `plots[0]` (= the link
  target) becomes a different widget; `PlotGridWidget._relink_anchor()` therefore re-links every time
  plot around the current `plots[0]` each time (previously only removal handled it, case by case with
  a `was_anchor` check; with the reorder feature it was extracted as a shared routine called from both
  removal and reordering).
- **The navigator's overview waveform follows the first series of `plots[0]`**
  (`MainWindow._refresh_navigator_overview()`, added 2026-09-17). It used to show the first variable
  in the file's column order unconditionally via `next(iter(self._raw_variables.values()))`, which had
  nothing to do with what the user was looking at. Only when `plots[0].series_names()` is empty
  (nothing overlaid yet) is that old fallback used. Called from: `load_parquet` / `set_time_column` /
  dropping a variable (only when the target is `plots[0]`) / `load_view` (exactly once in a `finally`,
  whether it fully or partly succeeded) / the `PlotGridWidget.layoutChanged` signal (emitted on plot
  removal and reordering).
  - **Caveat (a real bug found during implementation)**: the values of `plots[0]`'s first series are
    taken from `plot.series_data(name)` (the array the plot itself holds), not
    `MainWindow.variable_values(name)` (name resolution from `_raw_variables`/`_derived` — this method
    was called `_variable_array` until made public in 13.5.1 Phase C). Reason: when the user overlays a
    column not currently used as the time axis and then switches the time axis to that column, the
    column name is removed from `_raw_variables` (it becomes the time axis itself). The series keeps
    being drawn validly, but resolving the name with `variable_values` raises `ExpressionError: unknown
    variable` (found in a situation like `test_set_time_column_refits_an_already_plotted_series_to_the_new_x_data`
    in `tests/parquet_analyzer/test_load_robustness.py`).

### 16. Cursors (added 2026-09-19, user request)

(2026-10-02) Hidden series are not shown in the value labels (23.1). A cursor outside an unlinked
plot's range is simply not visible on that plot (24.4).

`TimePlotWidget` in `ui/plot_widget.py` and `PlotGridWidget` in `ui/plot_grid.py` implement the cursor
feature of specification.ja.md 5.13. The division of roles: `TimePlotWidget` handles **drawing** the
cursor (a `pg.InfiniteLine` + `pg.TextItem` pair, nearest-sample lookup) and **detecting user
operations** on that one plot (click to add/remove, drag to move, show/hide), while `PlotGridWidget`
handles **sync across plots** (propagating a cursor added/moved/removed on one plot to the other plots
of the same X domain, and applying existing cursors to newly added plots).

#### 16.1 Placing and removing

`TimePlotWidget.__init__` connects `self.scene().sigMouseClicked` to `_on_scene_mouse_clicked`, filtered
by both `self._cursor_mode_enabled` (off by default; switched by `MainWindow`'s toolbar "Cursor" toggle →
`PlotGridWidget.set_cursor_mode_enabled()` → each plot's `set_cursor_mode_enabled()`) and
`ev.button() == Qt.MouseButton.LeftButton`. A click that hit one of this plot's existing cursors (line or
label) (`ev.currentItem` — pyqtgraph's `GraphicsScene.sendClickEvent` emits `sigMouseClicked` itself
unconditionally on every click, but always sets this to the item that actually received the click) is
ignored here — removal is handled by `pg.InfiniteLine`'s own `sigClicked` (emitted from
`mouseClickEvent`; Qt calls it only for a press+release without movement, and an actual drag calls
`mouseDragEvent`/`sigDragged` instead, so both never fire for the same gesture) via
`_on_cursor_line_clicked` → `cursorRemoveRequested`. Otherwise (a background click) the scene
coordinates are converted to view (data) coordinates (`ViewBox.mapSceneToView`) and emitted as
`cursorAddRequested(self, x)`.

`PlotGridWidget._on_cursor_add_requested` assigns a new id (`f"c{n}"`), records `(x_axis_datetime, x)`
in `self._cursor_positions` (the authoritative data for applying existing cursors to plots added later —
see 16.3), and calls `add_cursor(id, x)` on every plot of the same domain as the source
(`plot.x_axis_datetime == source.x_axis_datetime`, the same domain rule as `add_plot`'s X linking).
`_on_cursor_remove_requested` handles removal the same way.

#### 16.2 Dragging, labels, show/hide

The actual movement is left to `pg.InfiniteLine(movable=True)`'s built-in behavior; `TimePlotWidget` only
subscribes to `sigPositionChanged` (`_on_cursor_line_moved`), recomputes that cursor's own label and
re-emits `cursorMoveRequested(self, id, x)`. `PlotGridWidget._on_cursor_move_requested` then calls
`set_cursor_x(id, x)` on the **other** plots of the same domain (not the plot where the drag actually
happened — its line is already at the new position, and calling `setValue` on it again would fire the
same signal again). `set_cursor_x` blocks the line's own signals while applying the exact position, to
prevent this echo.

A cursor's label content (`_update_cursor_label`) is recomputed only on an actual add/move or when the
set of series on that plot changes (`refresh_cursor_labels()`, called from `_install_series` /
`remove_series` / `transfer_series`), and not from `_on_range_changed` (which fires continuously while
panning by mouse drag). Getting a value of a `WindowedColumn` series (`_series_value_at`) actually reads
from disk with `y_source.window(row, row+1)` (one row, but still), so calling it every frame of a pan
could reintroduce the "per-frame I/O on the UI thread" stutter the drawing path has carefully avoided
(ch. 10, 13.5.1). So `_on_range_changed` calls only `_reposition_cursor_overlays()`, which updates just
the position of the existing label items (pinned near the top of the current Y range, so they follow
pan/zoom rather than being left behind) and doesn't touch the text.

`set_cursor_mode_enabled(enabled)` just calls `setVisible(enabled)` (and `line.setMovable(enabled)`) on
the `line`/`label` of every existing cursor; it neither deletes nor "pins" them: turning the toolbar
toggle off hides cursors completely (they don't get in the way of normal panning/zooming), and turning it
on again restores them at the same `x` — the position itself was never changed, so no separate
"restore" is needed.

#### 16.3 Sync across plots and clearing

`PlotGridWidget._cursor_positions` (`cursor_id -> (domain, x)`) is the coordinator's own record,
separate from each `TimePlotWidget._cursors`. `add_plot()` applies every position of the matching domain
to the new plot, so plots added **after** placing cursors show the existing cursors too.

A cursor's X value is meaningful only for the time-axis column at the time it was placed, so
`TimePlotWidget.set_x_data()` (switching the time-axis column) and `MainWindow._clear_plot_grid()` (opening
a new file) both discard all cursors via `TimePlotWidget.clear_cursors()` and
`PlotGridWidget.clear_cursors()` respectively — rather than leaving numeric positions that no longer
correspond to anything.

### 22. Plot height, reordering, time labels (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.15, 5.16.

#### 22.0 Cause of the bug (reordering squashes plots)

`PlotGridWidget._move_plot` swapped plots with `QVBoxLayout.removeWidget()` → `insertWidget()`.
Reproduced offscreen (2026-10-02, three rows of about 225 px each): moving the first row down gave
293/88/293 px, and moving further gave 372/185/117 px. `QVBoxLayout` doesn't remember each row's height
and gives the re-inserted widget a small height. Fixed by replacing it with a `QSplitter`, which manages
heights explicitly.

#### 22.1 `PlotGridWidget` layout

- The vertical stack becomes a `QSplitter(Qt.Vertical)` (the only child of `PlotGridWidget`'s
  `QVBoxLayout`). `setChildrenCollapsible(False)`, minimum row height 60 px, default handle width.
- The splitter's items are not the `TimePlotWidget`s themselves but **`PlotPane(QWidget)`** (new, in
  `ui/plot_grid.py`): side by side [handle column, `TimePlotWidget`].
  - `PlotGridWidget.plots` still returns the list of `TimePlotWidget`s (the external API doesn't
    change). `PlotPane` is internal. A `plot` → `pane` mapping is kept.
  - The handle column (`PlotHandle(QWidget)`, 14 px wide): a chain-icon toggle at the top (ch. 24;
    hidden if the `independent_x_axis` flag is off), and a grip below it (grip pattern, cursor
    `Qt.OpenHandCursor` / `ClosedHandCursor` while dragging).
- After adding (`add_plot`) or removing (`remove_plot`), all rows are divided evenly:
  `splitter.setSizes([total // n] * n)`.
- (Added in review) **A time-domain plot is inserted before the first frequency-domain (FFT) plot**
  (`add_plot(x_axis_datetime=True)`; "Separate into new plot" goes the same way). It used to always
  append, so with an FFT plot present a time-domain plot landed after it, breaking the overview's
  section 2 rule "time domain first and contiguous" (a latent bug, not visible while FFT is hidden by
  its feature flag). In that broken state, `move_plot_to`, which checks the whole order, refused almost
  every move.
- **Reordering keeps heights**: `move_plot_to(plot, new_index)` (new; `move_plot_up/down` call it).
  1. `sizes = splitter.sizes()` (old order).
  2. Permute `_plots` and the sizes the same way.
  3. `splitter.insertWidget(new_index, pane)` (`QSplitter.insertWidget` moves an existing child).
  4. `splitter.setSizes(permuted sizes)`.
  5. `_relink_anchor()`, update the time labels (22.4), `layoutChanged`.
  A move across the time-domain/frequency-domain boundary is refused as before (nothing happens if the
  plot at `new_index` is of a different domain; precisely, only moves that keep "time-domain plots
  contiguous at the start" are allowed).
- `remove_plot`: after `pane.hide()`, take it out of the splitter with `pane.setParent(None)`, and do
  `deleteLater` (on the whole `pane`) following the existing rule "delete after the in-flight job count
  reaches 0". (Changed during implementation: originally it didn't call `setParent(None)`, but a pane
  that is only hidden still occupies a slot in `QSplitter.sizes()` and throws the height ratios off, so
  it is taken out. Review confirmed the rule of delaying deletion until jobs finish still holds.)

#### 22.2 Height ratios (API, views)

- `plot_height_ratios() -> list[float]`: `splitter.sizes()` divided by their sum (summing to 1.0). If
  the splitter isn't shown yet, returns the pending ratios if any, otherwise even values.
- `set_plot_height_ratios(ratios: list[float])`: ignored (even heights) if the length differs from
  `plots`, any value is negative, or the sum is 0. If the splitter's current height is 0 (not shown yet),
  the values are kept pending and applied at the first `resizeEvent`/`showEvent` (pitfall: `setSizes`
  before showing computes against a height of 0 and the ratios are lost). If `move_plot_to` reorders
  while ratios are pending, the pending ratios are permuted the same way.
- A `height_ratio` read from a view is accepted only as a finite number (bools excluded); anything else
  is `None` (so a hand-edited string or NaN doesn't make `load_view` fail midway).
- Views (ch. 8): `PlotDef.height_ratio: float | None` (default `None`). `save_view_dialog` puts
  `plot_height_ratios()` into each `PlotDef`. At the end of rebuilding the layout, `load_view` applies the
  values if every plot has one, or even heights if any is `None`. If plots were skipped and dropped while
  loading, the remaining plots' values are renormalized.

#### 22.3 Drag to reorder

- In `PlotHandle`'s grip, `mousePressEvent` → once the movement exceeds
  `QApplication.startDragDistance()`, start a `QDrag`. The MIME type is
  **`application/x-parquet-analyzer-plot`** with the `plot_id` as data. **No** text (`text/plain`) is
  included — `TimePlotWidget`'s variable drop checks `hasText()`, so text would be misread as a variable.
- The drop target is `PlotGridWidget` (`setAcceptDrops(True)`). `dragMoveEvent` computes the insertion
  index (0–n) from the cursor's Y; at an allowed position a thin horizontal line (a `QFrame`, highlight
  color, 2 px high, a child widget laid over the splitter) is shown there; at a disallowed position
  (crossing a domain boundary, or a move that wouldn't change the position) the line is hidden and
  `event.ignore()`. `dropEvent` calls `move_plot_to`. `dragLeaveEvent` hides the line.
- **`dragEnterEvent` always accepts (`accept`) if the MIME is the plot type, regardless of position.**
  The allowed/disallowed decision is made only in `dragMoveEvent` and `dropEvent`. (A bug found in
  review: Qt sends no `dragMoveEvent`/`dropEvent` to a widget that rejected `dragEnterEvent` until the
  pointer enters another drop target. A drag always starts at its own grip = a disallowed position, so
  rejecting at the entrance made almost every drag unresponsive.)
- The insertion line's `y` is clamped to `[0, height-2]` so it is visible at the end (index n) too.
- A drag over a plot reaches the child `TimePlotWidget` (`setAcceptDrops(True)`) first.
  `TimePlotWidget.dragEnterEvent`/`dragMoveEvent` don't accept `application/x-parquet-analyzer-plot` and
  call `event.ignore()` (non-text is ignored as before). **A rejected `DragEnter` is passed by
  `QApplication::notify` to the nearest ancestor widget with `acceptDrops` (`PlotGridWidget`)**, so no
  event filter is needed (confirmed with the real delivery path on Qt 6.11.2; the original idea of "an
  event filter if it doesn't propagate" was dropped as unnecessary). The insertion index is computed in
  `PlotGridWidget`'s coordinates.
- Operation log: `move_plot` (the same as the existing one, with `via="drag"`).

#### 22.4 Time labels only where needed (5.16)

- `TimePlotWidget.set_x_tick_labels_visible(visible: bool)`: `setStyle(showValues=visible)` on the
  bottom axis. Tick marks and grid stay. (`axis.setHeight` isn't needed: with `showValues=False`
  pyqtgraph shrinks the bottom axis to 0 px automatically, confirmed; 20 px when shown.)
- `PlotGridWidget._update_x_tick_labels()`: among time-domain plots, only **the last one in order among
  the linked ones (ch. 24; all of them if the flag is off)** and **every unlinked one** get `True`; the
  other time-domain plots get `False`. Frequency-domain (FFT) plots are always `True`.
- When to call: after `add_plot`, `remove_plot`, `move_plot_to` and switching the linking (ch. 24).
- Image export (ch. 19) follows the same rule (the current "only the bottom of the shared group" is the
  same for the linked group; 24.6 adds the handling of unlinked plots).

#### 22.5 Test plan

- **Regression**: with three rows shown, record each row's height → `move_plot_down(plots[0])` → each
  plot's height equals its own height before the move (±2 px). Then the same with `move_plot_up`.
  Confirm it fails on the code before the fix.
- `move_plot_to` to any position (start, end, middle); out of range and the same position do nothing;
  moves across the domain boundary are refused (FFT plots are made with `add_plot(x_axis_datetime=False)`).
- Adding and removing return to even heights.
- Even if `set_plot_height_ratios` is called before showing, the ratios are right after showing. Invalid
  input is ignored.
- Saving and loading a view restores the ratios (±0.02). Old views without `height_ratio` get even
  heights. When one plot is skipped on load, the rest are renormalized.
- Drag (real delivery path): sending `QDragEnterEvent`/`QDragMoveEvent`/`QDropEvent` to
  `grid.windowHandle()` with `QCoreApplication.sendEvent` goes through Qt's real delivery path
  (`QWidgetWindow` → finding the drop target → `QApplication::notify`) instead of `QDrag.exec`. With it,
  confirm the order changes correctly for (A) grip → down the handle column → drop at the bottom edge of
  row 3, (B) upper half of row 2 (disallowed) → drop in the lower half (allowed), (C) straight to row 3,
  and that a variable (`text/plain`) drag still reaches `TimePlotWidget` as before.
- Drag (unit level): `QDrag.exec` is synchronous and hard to test, so test `PlotGridWidget`'s insertion
  index computation (`_insertion_index_at(y)`, the allowed check) directly, and call `dropEvent` with a
  synthesized `QDropEvent` (MIME `application/x-parquet-analyzer-plot`) to confirm the order changes.
  Also confirm `TimePlotWidget.dragEnterEvent` doesn't accept the same MIME (`isAccepted()` false).
- Time labels: with three (linked) rows, only the bottom has `showValues` true. Reapplied after
  reordering, adding, removing. FFT plots are always true.
- Y-axis widths (22.6): showing series with different tick text widths (`feature_test.parquet`'s `noise`
  and `ramp`, etc.) in separate plots makes the left x of every plot's view box equal (±1 px). Still equal
  after adding, removing, reordering plots, and after zooming changes the tick text. Removing the plot
  with wide text re-aligns the rest to the narrower width. Frequency-domain plots are aligned too.
- By eye: save the screen with three rows after changing heights and reordering as an image and check
  it (nothing squashed, labels only at the bottom, handle columns visible).

#### 22.6 Equal Y-axis widths (added 2026-10-02, pointed out by the user during the real-machine check)

- Background: pyqtgraph's left axis (`AxisItem`) sizes itself to its tick text width, so the width
  differs per plot. Measured (`feature_test.parquet`, four rows): `sine`/`ramp`/`with_nan` 35 px, `noise`
  19 px → the left edges of the plot areas are 16 px apart.
- Add `AlignedAxisItem(pg.AxisItem)` (for the left axis) to `ui/plot_widget.py` and use it via
  `TimePlotWidget`'s `axisItems={"left": ...}`.
  - It keeps its **natural width** computed from its own text width (`natural_width()`) and emits
    `naturalWidthChanged` when it changes (overriding pyqtgraph's width-recomputation `_updateWidth` to
    record the natural width before notifying).
  - The actual width is set from outside with `set_aligned_width(w)` (`None` means the natural width).
- `PlotGridWidget._align_y_axes()`: sets the maximum `natural_width()` over all plots (time and frequency
  domain — aligning the left edges of every stacked row) via `set_aligned_width` on every plot. Called on
  any axis's `naturalWidthChanged` and after `add_plot`/`remove_plot`. Setting the width doesn't change
  the natural width, so there's no notification loop.
- When the tick text gets shorter, the maximum drops too and every row narrows (it doesn't stay wide).
- Image export (ch. 19) already aligns left edges with matplotlib's `constrained` layout, so it is out of
  scope.

### 23. Per-series select, remove, show/hide (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.17.

#### 23.1 Series state (`TimePlotWidget`)

- Add `"visible": bool` (default `True`) to `_series[name]`. **This is the single source of truth for
  visibility**; the curve's `setVisible` just follows it.
- `set_series_visible(name, visible)`: updates the curve and legend display, updates the cursor labels,
  and emits the `seriesVisibilityChanged(self, name, visible)` signal. Hidden series aren't recomputed by
  downsampling (skipped at the start of `_redraw_series`). When shown again, `_redraw_series(name)` is
  called to redraw for the current range.
- `is_series_visible(name)`, `visible_series_names()` (in display order).
- Where hidden series are excluded (matching the list in 5.17):
  - `export_snapshot()` (19.1) — hidden series aren't included.
  - Cursor labels (16.2) — no value line.
  - The Y range of `go_home()` — computed from shown series only. If all are hidden, from all series
    (never an empty range).
  - `MainWindow._plotted_variable_names()` (the default target of statistics) — shown series only.
  - `MainWindow._refresh_navigator_overview()` — the anchor plot's first **shown** series. If none, the
    old fallback.
- Legend: the sample (`ItemSample`) of pyqtgraph's `LegendItem` toggles the curve's `setVisible` directly
  on click by default. This is replaced with `LegendItem(sampleType=_SeriesSample)`, whose
  `_SeriesSample.mouseClickEvent` doesn't toggle by itself but calls `set_series_visible(name, not
  visible)` (going through the source of truth). A hidden series' legend label is a gray clearly darker
  than the normal label color (changed in review: the original `#888888` was nearly the same as
  pyqtgraph's normal label color `#969696` and couldn't be told apart). The eye icon pyqtgraph draws on a
  hidden sample stays.

#### 23.2 Selection (one, managed across the grid by `PlotGridWidget`)

- `PlotGridWidget._selected: tuple[TimePlotWidget, str] | None`.
- Clicking a legend label (the text part of the series name) → `TimePlotWidget.seriesLabelClicked(self,
  name)` → `PlotGridWidget.select_series(plot, name)`. If the same series is already selected, deselect.
- Display: the selected curve's line width is 2.5 (normally the default width) and its legend label is
  bold. Deselecting restores them. The pen is decided from the `entry` state (`selected`) so the selection
  look survives the pen being recreated by a downsampling redraw.
- Deselected when: the same label is clicked again, an empty part of a plot is clicked (a click in cursor
  mode places a cursor first and doesn't deselect), Esc, the selected series or plot is removed, the file
  is reopened, a view is loaded, the time-axis column is switched.
- A `selectionChanged(object)` signal (`(plot, name)` or `None`).
- (Decided during implementation, confirmed in review)
  - When the selected series is moved (separated) to another plot, the selection is cleared rather than
    moved.
  - Even in cursor mode, clicks on the legend (sample, name) are treated as select / show-hide and don't
    place a cursor (decided by whether the legend already accepted the click).
  - Esc is caught by the window. When the expression input has focus, the input's own Esc (cancelling an
    edit) wins and the selection isn't cleared.

#### 23.3 Removing

- `PlotGridWidget.remove_selected_series()`: does nothing without a selection. Otherwise
  `plot.remove_series(name)`, deselect, `layoutChanged`, `log_op("remove_series", plot=, name=)`. The plot
  isn't removed even if it becomes empty.
- Key: `MainWindow`'s shortcut-configurable action `remove_series` (added to `DEFAULT_SHORTCUTS`, default
  `"Del"`, not on the toolbar but added to the window with `addAction`). In addition, when the setting is
  `Del`, **`Backspace` is assigned to the same action** (`QAction.setShortcuts`) — macOS keyboards' "delete"
  key sends Backspace. The label in the settings is i18n (`shortcut.remove_series`).
- No accidental firing while typing: `QLineEdit` takes Delete/Backspace first via ShortcutOverride, so the
  action doesn't fire while the expression input has focus. Confirmed by a test.
- If the anchor plot's first series disappears, the navigator is updated (confirm it is already called via
  `layoutChanged`).
- (Confirmed in review) Delete while the variable panel has focus also removes the selected **plot series**
  (deleting a derived variable in the variable panel is done from its context menu, ch. 18).

#### 23.4 Context menu (`TimePlotWidget.contextMenuEvent`)

- The wording of the existing "Remove from this plot" (`plot.remove`) changes to "Remove plot" /
  「プロットを削除」 (same behavior).
- Added (disabled on a plot with no series):
  - "Delete series ▸" → a list of series names → `removeSeriesRequested(self, name)`.
  - "Show/hide series ▸" → a checkable list of series names → `set_series_visible`.
  - "Separate series into new plot ▸" → a list of series names → `separateSeriesRequested(self, name)` →
    `PlotGridWidget` creates a new plot of the same domain at the end (the end of that domain's range) and
    moves just that one series with `transfer_series`. Visibility and color carry over (`transfer_series`
    moves `visible` too). On a plot with only one series this submenu is disabled (moving it would just
    leave the original plot empty; changed in review: originally it could be chosen and did nothing).
- The existing "Separate into new plot" (all series but the first) stays.
- (Added in review) A new plot created by separating fits its X range to the original plot's current
  range (Y fits the moved series). Previously the new plot showed the series' full range, and via X-axis
  linking every plot's zoom went back to the full range (the existing "Separate into new plot" too).

#### 23.5 Views

- `SeriesDef.visible: bool = True`. Written on save, applied with `set_series_visible` on load. Old views
  (without the key) are shown.

#### 23.6 Test plan

- Clicking a legend sample (calling `_SeriesSample.mouseClickEvent` with a synthesized event) toggles
  `visible`, hides the curve, and makes the label darker than normal (checked by comparing against the
  normal label color, not by the color string).
- (Added in review) **Real clicks**: send `QTest.mouseMove` → `QTest.mouseClick(plot.viewport(), ...)` to
  the positions of a legend sample and name (`sceneBoundingRect().center()` converted with
  `plot.mapFromScene`) and confirm show/hide, select/deselect, deselect by clicking empty space, and in
  cursor mode "a cursor on empty space, no cursor on the legend" (as in ch. 22, tests that only call the
  handlers directly miss bugs in the real delivery path).
- A hidden series: is not in `export_snapshot()`, isn't in the cursor labels, doesn't affect `go_home`'s Y
  range (hiding a series with extreme values shrinks the Y range), isn't in the default statistics target,
  isn't used for the navigator overview (hiding the first series makes the second be used). `go_home`
  doesn't break when all are hidden.
- While hidden, no recompute jobs are issued (`pending_downsample_count()` stays 0 after zooming), and
  showing it again redraws.
- Selection: clicking a label selects; clicking it again, clicking empty space or Esc deselects; selecting
  a series in another plot deselects the previous one; the selection look (line width) survives redraws
  after zooming.
- Removing: select and trigger the `remove_series` action → the series disappears and the plot remains.
  Nothing happens without a selection. `Backspace` works too. Doesn't fire while the expression input has
  focus (the input's text is deleted instead).
- Menus: "Delete series ▸", "Show/hide series ▸", "Separate series into new plot ▸" each work (`QMenu.exec`
  monkeypatched to return the chosen item). Separating keeps the color, visibility and the kind of a
  windowed series (13.5.1 Phase D). The wording of "Remove plot".
- A view round trip keeps `visible`; old views show everything.
- i18n: every new string has `en`/`ja`.

### 24. Independent X axis per plot (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.18. While the feature flag `independent_x_axis` (ch. 21) is off,
only the switching UI (menu item, chain icon) is hidden; the internal mechanism below is always active
(behaving as all plots linked = the same state as before).

#### 24.1 State and links

- `TimePlotWidget.x_linked: bool` (default `True`). Meaningless for frequency-domain (FFT) plots and
  always treated as `False` (they never join the linked group, as before).
- The definition of the **anchor** changes: `PlotGridWidget.anchor_plot()` = the first plot in order that
  is "time domain and `x_linked`" (previously the first time-domain plot).
- `_relink_anchor()`: `setXLink(None)` on the anchor, `setXLink(anchor)` on the other linked plots, and
  **`setXLink(None)` on unlinked time-domain plots**.
- `set_plot_x_linked(plot, linked)` (new):
  - With `linked=False`, if it is the last linked plot, do nothing (return `False`). The UI shows it
    disabled beforehand using `can_unlink(plot)`.
  - When going back to `linked=True`, fit it to the linked group's current range with
    `plot.setXRange(*anchor.viewRange()[0], padding=0)` before `_relink_anchor()`.
  - Afterwards: `_relink_anchor()`, update the time labels (22.4), `layoutChanged`,
    `log_op("set_x_linked", plot=, linked=)`.
- An unlinked plot's `rangeChanged` is **not forwarded to the navigator**: `_on_plot_range_changed`
  (connected in `add_plot`) checks `source.x_linked` and ignores it.
- `set_x_range` (navigator → plots) sets only the anchor as before (linked plots follow through the link,
  unlinked ones don't move).
- A newly added plot (`add_plot`) is added linked and linked to the anchor (as before). When "Separate
  series into new plot" is used on an unlinked plot, the new plot is also created linked and shown at the
  linked group's range.
- (Decided during implementation, confirmed in review)
  - When re-linking a plot that comes first and becomes the new anchor, it fits **the previous anchor's
    range** (the linked group as a whole doesn't move).
  - If the last linked plot is removed and only unlinked time-domain plots remain, the first remaining
    time-domain plot is linked again automatically (keeping its own range; to keep "at least one linked").
  - **After switching linking, reordering or removing, the navigator still shows the linked group's
    (anchor's) current range** (found in review: the routine that rebuilds the overview waveform on every
    layout change reset the selection to the whole data; an existing bug that also happened with ch. 22's
    reordering).
- Switching the time-axis column (`set_time_axis_data`) replaces the X values of every time-domain plot,
  unlinked ones included, and does `go_home` (as before; linking states are kept).
- `go_home()` calls `go_home()` on every plot (as before). Unlinked plots return to their full range too.

#### 24.2 "The visible range"

- `MainWindow._visible_row_range(plot=None)`: with no argument, **the linked group's** range (if the
  anchor is empty, the first linked plot with data; if none, the full range — the current "first non-empty
  time-domain plot" becomes "the first non-empty linked plot"). With `plot`, the row range from that plot's
  `viewRange()`.
- Statistics: add a `plot` argument to `_on_stats_requested(plot=None)` and use `_visible_row_range(plot)`.
  The range label (`stats.range_with_count`) has the same format either way.
- An unlinked plot's context menu gets "Stats for this range" (`plot.stats_this_range`) →
  `statsForRangeRequested(self)` → `MainWindow._on_stats_requested(plot=self)`. The target variables are
  chosen as in ordinary statistics (the variable panel's selection, otherwise **this plot's** shown
  series).
- (Decided in review) With the toolbar's "Stats" and nothing selected in the variable panel, the default
  target is the shown series of **linked plots** only (the range computed is the linked group's range, so
  series that are only on an unlinked plot aren't computed over a different range). An unlinked plot's
  series are computed with that plot's "Stats for this range".
- The computation of "the visible range" (the linked group) lives in one place, and statistics, the image
  annotation and the view's saved X range all use it (found in review: only the image annotation computed
  it differently and wrote an unlinked plot's range).
- FFT (disabled) and the future XY (ch. 25) use the no-argument form = the linked group's range.

#### 24.3 UI

- The context menu gets "Link X axis" (checkable, `plot.link_x`). On the last linked plot the check can't
  be removed (the item is disabled). Not shown on FFT plots.
- The chain icon in the handle column (22.1): a checkable `QToolButton`, a closed chain while linked and a
  broken chain while independent (the icon may be a small custom drawing or the character "🔗" rather than
  a standard `QStyle` icon — check the look in an image during implementation). Tooltip
  `plot.link_x_tooltip`. It calls the same `set_plot_x_linked` as the menu, and when the state changes
  both displays are synced.
- An unlinked plot's handle column gets a slightly different background color so it can be told apart.
- Even with the feature flag off, an unlinked plot loaded from a view gets the handle-column color and
  "Stats for this range" (the flag hides only the entry point for switching links). FFT plots get neither
  the chain icon nor the linking item.
- The chain icon is drawn as two joined rings while linked and two separate rings with a diagonal break
  while independent, with visual changes on hover and press (changed in review: the first version was
  small and looked only like "8" or "o o").

#### 24.4 Cursors

- No change (synced by time across all time-domain plots). A cursor outside an unlinked plot's range is
  simply not visible because its `InfiniteLine` is outside the visible range. Confirm the label position
  computation (`_reposition_cursor_overlays`) doesn't raise when out of range.

#### 24.5 Views (ch. 8)

- `PlotDef.x_linked: bool = True`, `PlotDef.x_axis_range: tuple[float, float] | None = None` (written only
  for unlinked plots). `View.x_axis_range` is the linked group's range (clarifying its meaning; the value
  comes from the first linked plot with series — linked plots share a range, so it equals the anchor's).
- `load_view`: after rebuilding the layout, apply each plot's `x_linked` (an instruction to unlink the last
  linked plot is ignored and it stays linked — against corrupt files), apply `View.x_axis_range` to the
  linked group and each `PlotDef.x_axis_range` to its unlinked plot. Old views are all linked.
- Even with the feature flag off, `x_linked=False` in a view is applied as is (it just can't be switched
  from the UI).

#### 24.6 Image export (ch. 19)

- Add `x_linked: bool` and `height_ratio: float` (22.2) to `PlotSnapshot`.
- `build_figure`: `sharex` only among subplots that are "time domain and linked". An unlinked time-domain
  subplot has its own X axis (the same `ConciseDateFormatter`) and tick labels. In the linked group, only
  the last linked subplot in order has tick labels. Subplot heights follow the on-screen ratios via
  `gridspec_kw={"height_ratios": [...]}`.
- The annotation's "range" line is the linked group's range (from the anchor, as before).

#### 24.7 Test plan

- With two rows, unlink the bottom → zooming only the bottom doesn't change the top's range, and zooming
  the top doesn't change the bottom. Link again → the bottom fits the top's range.
- The last linked plot can't be unlinked (returns `False`, the menu item is disabled).
- Unlink the anchor (first) → the second row becomes the new anchor, the third and later follow it, and the
  navigator shows the new anchor's range (forwarding of `rangeChanged`). Moving an unlinked plot doesn't
  move the navigator.
- Links stay correct after reordering and removing (an unlinked plot that comes first doesn't become the
  anchor).
- `_visible_row_range()` is the linked group's range; `_visible_row_range(plot)` is that plot's range.
  "Stats for this range" computes over that plot's range and that plot's series.
- Time labels (22.4): unlinked plots always show them; the linked group only at the last.
- Home: unlinked plots return to their full range too. Switching the time-axis column keeps the linking
  states.
- Cursors: no exception when a cursor is outside an unlinked plot's range.
- View round trip: `x_linked` and each plot's range are restored. Old views are all linked.
- Image export: unlinked subplots aren't in `sharex` and have their own tick labels (checked on the `Axes`
  returned by `build_figure`); height ratios follow the screen. Check one by eye too.
- With the flag off: no menu item and no chain icon. `x_linked=False` in a view is applied.

#### 24.8 Abnormal operations and values (added 2026-10-03, from bugs found while strengthening the tests)

Principle: never let an exception out of a signal slot (a screen operation). Never hand pyqtgraph a
non-finite coordinate or range (data 29.4). Operations on stale references (removed plots, cursors) do
nothing.

| Input | Handling |
|---|---|
| A range change from a plot after it was removed | Ignored (a plot not in the grid has its `rangeChanged` not forwarded to the navigator). The **fifth path** of the navigator re-sync bug (CLAUDE.md, LEARNINGS 2026-10-02) |
| A non-finite X to `set_x_range`, the navigator's `set_selected_range`, or adding/moving a cursor | Ignored |
| Moving a removed cursor ID, removing/moving a removed plot | Nothing happens (no exception) |
| Dropping a string that isn't a variable name (a selection from another app, an old name, the time-axis column) | Ignored and logged. Dropping a non-numeric column is data 29.5 |
| 3D ticks (`nice_ticks`) whose width overflows, is subnormal, or whose ticks overlap after rounding | Return `[lo]` / merge overlapping ticks into one |
| A name not present in `series_data` | Stays a `KeyError` (internal calls only, where the name is always known) |
| A range beyond 1e300, a zero-width range | Left to pyqtgraph's rounding and widening (no exception) |

Test plan: `test_plots_errors.py` (degenerate series and ranges, the navigator, drops, removing/moving/
linking in the grid, cursors, switching the time axis, real clicks and wheel). A bug fix comes with a
regression test that fails before the fix.

### 39. Showing the values under the mouse (hover readout) (added 2026-10-03, user request)

Requirement: 5.32. In the discussion in `idea.md`, all five open questions were decided with the
recommended options (2026-10-03).

#### 39.1 Parts
- `TimePlotWidget` gets a hover line (`pg.InfiniteLine`, dotted and semi-transparent, not movable,
  `ignoreBounds=True`) and a value label pinned at the top right (a `pg.TextItem` placed at the view's top
  right and repositioned when the range changes, or a `LabelItem` laid over the layout). Separate from
  placed cursors (ch. 16).
- The mouse position: from `scene().sigMouseMoved` (throttled with a `SignalProxy`, about 60 Hz),
  converted to view coordinates. When it leaves the plot (`leaveEvent` / outside the view), hide the line
  and label and tell the linked plots to hide too.
- Linking: `PlotGridWidget` distributes the hover time to the other plots of the same X-axis group (ch. 24's
  linked, time-domain plots) (`set_hover_x(x | None)`). FFT plots (`x_axis_datetime=False`) only themselves.
- Shown in cursor mode (ch. 16) too (it previews where a click would place a cursor).

#### 39.2 How values are found
- While moving: from **the data of the curve each series currently draws** (`curve.getData()`, decimated),
  the nearest point's value is found with `np.searchsorted` and shown at once. "≈" marks it as a
  decimated value. For columns entirely in memory (with `entry["y"]`) that aren't decimated, it is the
  real sample, so no "≈".
- After stopping: a `QTimer` of about 100 ms after the last move (single-shot, restarted on every move)
  replaces it with the real sample value using `_series_value_at`, the same as ch. 16's cursors (windowed
  columns read one row each time). It reads as many as there are shown series, so it is light, but if
  reading row by row on the UI thread causes a delay, measure and report it.
- The time and value formats use the same functions as the cursor labels (no duplication). Series names in
  the series' color.
- No exception or stall even when the time axis has non-finite values or isn't ascending (ch. 29) ("—"
  when a value can't be given).

#### 39.3 On/off, settings
- A "Values" item on the toolbar (a checkable `QAction`, id `hover_values`, shortcut configurable, none by
  default). Saved in `Settings.hover_values_enabled` (default `True`). When off, neither line nor label is
  shown and mouse movement isn't processed.
- Operation log: `hover_values` (`enabled`). Mouse movement and the shown values aren't recorded.

#### 39.4 Display details
- The label is at the top right. If it would overlap a placed cursor's label (ch. 16), the hover label is
  shifted a little below the top right. A semi-transparent white background so it hides less of the curves.
- Hidden series (23.1) aren't shown. The selected series (ch. 23) is bold.
- Image export (ch. 19, `export_snapshot`) doesn't include the hover line or label.

#### 39.5 Test plan
- Sending mouse moves through the real path (`QTest.mouseMove` to the viewport) shows a line on that plot
  and every plot linked with it, and the label shows the time and the values of the shown series. Leaving
  makes them disappear.
- Right after moving there is "≈" (for a large series being decimated), and after about 100 ms it changes
  to the real sample value (confirmed by running the event loop, qtbot-style). A small series entirely in
  memory has no "≈" from the start.
- Hidden series don't appear. FFT plots aren't linked. Nothing with the toggle off. Saving and loading the
  setting.
- No exception with a time axis containing NaN or not ascending. Not included in image export.
- Drive MainWindow headlessly and check the look of the hover in a screenshot.

## Relations to other domains

- **Data** ([data.md](./data.md)): drawing depends on downsampling and windowed reads. Switching the
  time-axis column.
- **Persistence** ([persistence.md](./persistence.md)): views save the plot configuration (series, colors,
  Y range, X range, height ratios, X-axis linking, series visibility). When changing these, check the view
  schema (ch. 8) and `load_view` too.
- **Export** ([export.md](./export.md)): `export_snapshot()` hands over the on-screen state. Keep the
  handling of hidden series, heights and X-axis linking consistent.
- **Analysis** ([analysis.md](./analysis.md)): provides "the visible range" (the linked group), the default
  statistics target (shown series), and cursor positions (the XY plot's highlighted points).
- **Expressions** ([expressions.md](./expressions.md)): editing or deleting a derived variable replaces or
  removes the plotted series (`replace_series_y`/`remove_series`).
- **App foundation** ([app.md](./app.md)): feature flag `independent_x_axis`, shortcuts (`remove_series`,
  etc.).
- **App foundation** (ch. 30, 2026-10-03): the state record (`state_snapshot`) reads the plots' layout,
  ranges, series and navigator. Adding plot state to the record too makes bugs easier to investigate (the
  navigator bug was found through this record).
- **Analysis** (ch. 25): the XY plot uses cursor positions (`cursorsChanged`) and the linked group's range
  (`_group_x_range`).
- **Data** (36.8, 2026-10-04): with large files the time axis becomes a `TimeIndex` (an index that doesn't
  hold everything) and derived variables become `DerivedColumn`s (computed for the visible range only).
  Code for the visible range, cursors, hover and navigator must not assume arrays. On the UI thread, no
  reads proportional to the number of drawn points and no reads per mouse move (the hover uses the drawn
  curve's values while moving even when the time axis is a `TimeIndex`).
