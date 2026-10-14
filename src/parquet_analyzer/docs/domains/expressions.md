# Expressions (derived variables, user functions, maps)

> Overview specification: [overview.md](../overview.md) (shared rules, glossary, old section
> number table). This document goes "Requirements" → "Design". Section numbers are those of the
> old specification documents.

**Scope of this domain**: inter-variable expressions and their evaluation, derived variables
(adding, editing, deleting, dependencies), Δ analysis, the function registry, user functions, maps
(lookup tables).

## Requirements

#### 5.5 Inter-variable expressions
- Besides arithmetic (+, -, ×, ÷, power, modulo, floor division), absolute value (abs) and square
  root (sqrt), trigonometric functions (sin/cos/tan), exponent and logarithms (exp/log/log10),
  rounding (sign/floor/ceil/round), range operations (min/max/clip, all elementwise) and a moving
  average (rolling_mean) can be applied to variables (columns). Details and the list of functions
  in [detailed_specification ch. 6](#6-expression-input-114).
- The result is added to the list of plottable variables as a new derived variable.
- If the derived variable's name collides with an existing column name it is an error, preventing
  data from being overwritten by accident.
- The input method and UI for expressions were settled in 11.4.
- Variables created by expressions (derived variables) are also saved in views as their **defining
  expression (the expression string and the source variable names)**, not raw data. When a view is
  loaded, they are recomputed from the source columns. Reason: they follow along if the source data
  is updated, and the saved file stays light.
- **Editing and deleting derived variables** (added 2026-09-30, user request; details in
  [detailed_specification ch. 18](#18-editing-and-deleting-derived-variables-added-2026-09-30-user-request)):
  right-clicking a derived variable in the variable list offers "Edit expression..." and
  "Delete". Neither is shown for columns from the file (they are real data, not expressions).
  - Editing happens not in a separate dialog but in the same expression bar used for adding: the
    bar switches to edit mode with the name fixed and the current expression filled in. While
    editing, variable names can still be inserted by dragging from or double-clicking the variable
    list. "Update" applies, "Cancel" returns to add mode.
  - Editing changes only the expression; the name stays (other expressions refer to it by name, so
    renaming is out of scope). The new expression is validated as when creating, and on error
    nothing changes. On success, that variable **and every derived variable depending on it** are
    recomputed, and the curves showing them are updated in place (same plot, same color).
  - Deleting, after a confirmation dialog, removes it from the variable list and from every plot
    showing it. A derived variable referenced by other derived variables can't be deleted, and the
    names of the dependents are listed (the user deletes or edits those first; there is no silent
    cascading deletion).
  - An expression referring to itself, directly or indirectly (a circular definition), is an
    error.
  - In the variable list, derived variables are shown so they can be told apart from file columns
    (italics, with `name = expression` in the tooltip).
  - Edits and deletions apply to the current session and are made permanent, like other layout
    changes, by saving the view (overwriting the view of the same name). Derived variables are
    always written in dependency order, so a saved view reloads correctly.

#### 5.7 Δ (delta) analysis
- Compute the difference between adjacent samples (first-order difference) of a selected variable
  and show it as a time series.

#### 5.20 User functions (added 2026-10-02, user request)
- A derived variable ([5.5](#55-inter-variable-expressions)) writes the open file's column names
  directly, so it can't be reused with files whose column names differ. A **user function** is a
  reusable expression with named arguments, usable in common across every file and view:
  ```
  Function:     Power_flow_by_press
  Arguments:    flow_lpm, press_kpa
  Constants:    k = 60000            (each with a default value)
  Expression:   flow_lpm * press_kpa / k
  Description:  flow × pressure → power [kW]
  ```
- Used in expressions like a built-in function. Constants take their default unless overridden by
  name:
  ```
  power1 = Power_flow_by_press(signal1, signal2)
  power2 = Power_flow_by_press(signal1, signal2, k=1000)
  ```
  The result is an ordinary derived variable.
- A user function's expression can use its own arguments and constants, built-in functions, maps
  ([5.21](#521-maps-tables-added-2026-10-02-user-request)) and other user functions. It can't
  refer to the file's columns directly. A circular definition is an error. Names follow Python's
  identifier rules and can't duplicate built-in functions, other user functions or maps.
- **Function manager window** ("Functions" on the toolbar): list, add, edit, delete. Plus an
  **"Apply to signals"** panel: pick a function, drag signals from the variable list onto each
  argument's field, adjust the constants (defaults filled in), name the result and create it —
  it just builds the expression above.
- Saved in `cfg/parquet_analyzer/user_functions.json` (shared by the whole app).
- Editing a function recomputes every derived variable that uses it (directly or through other
  functions) and updates the plotted lines (as with editing a derived variable). A function used by
  derived variables or other functions can't be deleted (the users are listed).
- **A view stores only the function name inside the derived variable's expression.** If the
  function doesn't exist when the view is opened, that derived variable (and what depends on it) is
  skipped with the usual consolidated warning, and the view manager shows it as missing too.
- Development feature flag `user_functions` ([overview 9](../overview.md)).
- Details in [detailed_specification ch. 26](#26-function-registry-and-user-functions-added-2026-10-02-user-request).

#### 5.21 Maps (tables) (added 2026-10-02, user request)
- A **lookup table** is read from CSV and used like a function in expressions:
  - **Curve** (one input): two columns `x, y`. Used as `name(x)`.
  - **Map** (two inputs): the first row holds the X-axis values, the first column the Y-axis
    values, and the body the Z values. Used as `name(x, y)`.
  ```
  eff = map_eff(rpm, torque)
  k   = curve_temp_corr(temperature)
  ```
- **Interpolation** (per map): linear (default) / nearest / hold previous value (stepwise) / cubic
  spline (curves only).
- **Outside the table** (per map): hold the edge value (default) / 0 / linear extrapolation / NaN.
- Interpolation and out-of-range handling are kept as a "name → processing" table, so one more can
  be added later by adding one entry.
- **Map manager window** ("Maps" on the toolbar): import (CSV file, name, kind, interpolation,
  out-of-range handling), list, change settings, re-import from the source file, delete, preview
  (the table, and a line for curves / a heat map for maps; a 3D surface too once stage 2 of
  [analysis 5.19](./analysis.md) exists).
- On import the CSV is **copied** to `data/parquet_analyzer/maps/`. Moving or deleting the source
  file doesn't break the map. "Re-import" reads the file from its original location again.
- (Added 2026-10-03, user request) **Editing a map**: the name can be changed and axis names (the
  display names of X, Y, Z, shown on the axes of the preview and the 3D surface) can be set. A map
  in use can't be renamed (as with user functions; the users are shown).
- (Added 2026-10-03, user request) **Editing values**: a map's values can be rewritten directly in
  the manager's table and saved. The height between the table and the graph can be changed by
  dragging.
- (Added 2026-10-14, user request) **Freezing the table's axes; clicking a graph moves to the table cell**: when the map's
  table is scrolled, the axes (X in the first row, Y in the first column) stay visible (the same as "freeze panes" in a
  spreadsheet). Clicking a cell of the preview graph (a heatmap cell or a curve point) selects that cell in the table and
  scrolls to it. Details in 27.11. For the 3D surface too, clicking a grid point moves to the same table cell (added 2026-10-14, 27.12).
- A map in use can't be deleted (as with user functions). A view stores only the map name, and if
  the map is missing that derived variable is skipped with a warning (as when a user function is
  missing).
- Development feature flag `maps` ([overview 9](../overview.md)).
- Details in [detailed_specification ch. 27](#27-maps-tables-added-2026-10-02-user-request).

#### 5.24 Missing values in the moving average (added 2026-10-02)
- `rolling_mean` gives a missing result if even one value in the window is missing (NaN) (missing
  values propagate). State this in the spec. Statistics (5.12) are computed excluding missing
  values, so make the difference clear to the user (in the expression descriptions and tooltips).
- For averaging while ignoring missing values, add **`rolling_nanmean(a, window)`** (averages only
  the non-missing values in the window; if the window is all missing, that point is missing).
- Details in [ch. 31](#31-missing-values-in-the-moving-average-added-2026-10-02).
- (Added 2026-10-04) **The window is centered**: `rolling_mean`/`rolling_nanmean` average over a
  window that straddles the point (a centered moving average), not only past values. The function description, tooltip,
  function list and README state this explicitly. A trailing-only window (a `mode` argument) is not added for now. Details in 31.1.
- (Added 2026-10-08, user decision) **Trailing moving averages `trailing_mean(a, window)` and `trailing_nanmean(a, window)`**:
  they average only the point itself and the points before it (no future values are used). They are separate function names,
  not a `mode` argument (the expression grammar and the arguments of existing functions do not change). Missing values are
  handled as in `rolling_mean`/`rolling_nanmean` (propagate / exclude). At the start of the data, where fewer past points than the
  window exist, the average is taken over **the points available** (as at the edges of `rolling_mean`; the start is not made missing).
  Details in 31.2.

#### 5.31 Function list window (added 2026-10-03, user request)
- Provide a window inside the app listing the functions usable in expressions. It shows **built-in
  functions, user functions and maps** together, each with its syntax (arguments), description and
  an example.
- An example chosen from the list can be inserted into the expression input (whether adding or
  editing).
- It also lists the operators (`+ - * / ** % //`) and the differences in handling missing values
  (NaN) (`rolling_mean` vs `rolling_nanmean`, etc.).
- Details in [ch. 38](#38-function-list-window-added-2026-10-03-user-request).

#### 5.34 Conditionals (added 2026-10-06, user request, high priority)
- In an expression or in the body of a user function, the value can be switched by a condition (for example, torque only
  while the engine speed is above 3000, and 0 otherwise). Two ways of writing it are provided, and both give the same result:
  - Function: `where(condition, value if true, value if false)` (equivalent to Excel's `IF`).
  - Python conditional expression: `value if true else value if false`.
- Conditions can use comparisons (`<` `<=` `>` `>=` `==` `!=`; chained comparisons such as `0 < a < 5` are also allowed),
  and `and` / `or` / `not`. A comparison yields the number 1 (true) or 0 (false), so it can also be used in arithmetic,
  as in `(a > 0) * a`.
- `isnan(a)` (1 if missing, otherwise 0) is added for testing missing values (NaN).
- The result of a comparison or condition that involves a missing value is missing (NaN). This keeps "unknown" from turning into
  0 or 1. To replace missing values with another value, write `where(isnan(a), 0, a)`.
- Everything is computed element by element (per time step). Multi-line user functions (writing named intermediate values)
  are not included in this version (dev notes).
- Details in [ch. 43](#43-conditionals-added-2026-10-06-user-request).

## Design

### 6. Expression input (11.4)

- A Python-like expression is typed directly as text into `ExpressionBar` (e.g. `A + B`,
  `abs(A) - sqrt(B)`, `rolling_mean(A, 20) ** 2`).
- Double-clicking a variable in `VariablePanel` inserts its name at the cursor position (input
  assistance).
- Evaluation validates the AST with `ast.parse` and, whitelist-style, allows only permitted
  operators, functions and known variable names (no raw `eval`). The purpose isn't defending
  against malicious input but preventing crashes and unintended side effects from mistyped input.
- **The input string is normalized with `unicodedata.normalize("NFKC", ...)` before parsing**
  (added 2026-09-17. A real bug: a Japanese IME in full-width mode turned `+` into `＋` (U+FF0B),
  and `ast.parse`'s raw Python syntax error `invalid character '＋' (U+FF0B)` was shown to the user
  as is via `ExpressionError`). NFKC normalization turns full-width letters, digits and symbols
  (`＋－＊／（）` etc.) and the full-width space (U+3000) into their half-width counterparts, so
  input typed without noticing the IME conversion is still understood.
  - Operators: `+ - * / ** % //` (power, modulo and floor division supported; unary `+ -` too).
  - Functions (all elementwise; registered in `_ALLOWED_FUNCS` as
    `{name: (expected argument count, implementation)}`):
    | Category | Functions | Arguments |
    |---|---|---|
    | Basic | `abs`, `sqrt` | 1 |
    | Analysis | `delta` (first-order difference), `rolling_mean` (moving average, the second argument is the window width; if even one value in the window is missing (NaN), that point's result is missing too), `rolling_nanmean` (moving average excluding missing values; averages only the non-missing points in the window, with the same edge handling as `rolling_mean`; missing only where the whole window is missing; ch. 31), `trailing_mean`/`trailing_nanmean` (moving averages over a trailing window only; 31.2) | 1 / 2 / 2 / 2 / 2 |
    | Trigonometric | `sin`, `cos`, `tan` | 1 |
    | Exponent / log | `exp`, `log` (natural log), `log10` | 1 |
    | Rounding | `sign`, `floor`, `ceil`, `round` | 1 |
    | Comparison / range | `min`, `max` (elementwise comparison, unlike Python's built-in reducing min/max), `clip` (value, lower, upper) | 2 / 2 / 3 |
    | Conditional | `where` (condition, value if true, value if false), `isnan` (1 if missing, otherwise 0; ch. 43) | 3 / 1 |
  - Note that `min`/`max`/`clip` are **elementwise** (e.g. `max(a, 0)` clamps each sample of a
    below 0 to 0; it is not an aggregate returning a single scalar — a derived variable must be an
    array as long as the time axis).
  - `rolling_mean(a, window)` is a moving average implemented by convolution (`np.convolve`,
    `mode="same"`). At the edges it averages only over the part that actually overlaps (no NaN
    padding), so the whole series can be plotted as is (`core/analysis.py`).
- **Functions also check the number of arguments.** `abs`/`sqrt` are numpy ufuncs whose second
  argument is taken as `out=` (the output buffer), so letting an expression like `sqrt(a, b)`
  through without checking the count silently overwrote `b`'s real data (a real bug found and fixed
  in review; the same check is applied to the newly added functions).
- If a column name isn't a valid Python identifier (contains spaces or symbols, etc.), the variable
  can't be referred to by that name in an expression (it can't be parsed as an `ast.Name`). There
  is currently no column-name alias feature (13.4).
- **A derived variable name colliding with an existing column name is an error**
  (`MainWindow._on_add_derived_variable`). It used to overwrite silently (found and fixed in
  review). Redefining a derived variable of the same name with only a different expression is
  allowed (taken as an intentional redefinition; it goes through the same processing as editing
  from the context menu, see [ch. 18](#18-editing-and-deleting-derived-variables-added-2026-09-30-user-request)).
- The created derived variable appears in `VariablePanel` like an ordinary variable and can be
  plotted and saved (see `specification.ja.md` 5.5).

#### 6.1 Handling abnormal input (added 2026-10-03, user request "strengthen the error-case tests")

**Principle: every failure in evaluation or validation leaves as `ExpressionError` (or its
subclass `UnknownFunctionError`).** Callers (the expression input, editing, loading views with
`load_view`, the function and map managers) catch only `ExpressionError` and treat it as "just that
expression failed", so if another exception leaks out, in view loading **one expression makes the
whole view fail to load**.

Bugs found by actually trying abnormal input (2026-10-03) and the decided handling:

| Example input | Previous result | Decided handling |
|---|---|---|
| `2 ** -1` | raw `ValueError` (numpy integer to a negative power) | every numeric constant is evaluated as a **float** → `0.5` |
| `2 ** 10000`, `10 ** 400` | **silently `0`** (int64 overflow) | evaluated as float → `inf` (an overflowed value is infinity) |
| a 400-digit integer constant | — | a constant that can't become a float is an `ExpressionError` |
| `True`, `a + False` | passed as numbers (bool is a subclass of int) | `ExpressionError` (booleans can't be used) |
| thousands of stacked `-` | raw `RecursionError` | `ExpressionError("expression too complex")` (the same for too-deep recursion during evaluation) |
| `rolling_mean(a, b)` (an array as the window) | raw `TypeError` | `ExpressionError` (the window is a finite scalar) |
| `rolling_mean(a, 1.5)` | silently truncated to `1` | `ExpressionError` (the window is a positive **integer**; an integer-valued float such as `3.0` is fine) |
| `rolling_mean(a, 0)`, a negative window | raw `ValueError` | `ExpressionError` |
| `delta(1)` (a scalar) | raw `TypeError` | a scalar is taken as "the same value at every time" → `0` (the difference is 0) |
| a `TypeError`/`ValueError` raised by a built-in function's or map's implementation | leaks as is | wrapped in an `ExpressionError` with the function name (the last line of defense; cases with their own decided handling take precedence) |

Deliberately **not errors** (numpy semantics; tests pin the current behavior):

- Division by zero and out-of-domain: `a / 0` → `inf`; `0 / 0`, `log(-1)`, `sqrt(-a)` → `NaN`;
  `a % 0` → `NaN`. NaN and inf are handled by the drawing side (data 29.4). numpy's
  `RuntimeWarning`s during evaluation aren't emitted (evaluation runs under
  `np.errstate(all="ignore")` to keep warnings from flowing to standard error on every
  expression).
- `clip(a, 3, 1)` (lower > upper): numpy semantics (everything becomes the upper value).
- `rolling_mean(a, 100)` (window longer than the data): truncated to the data length (as now).
- Syntax that isn't allowed (all `ExpressionError`, as now): subscripts, attributes, lists/tuples,
  lambdas, strings, `None`, complex numbers, `@`, bitwise operations, `~`, f-strings, `...`,
  assignment, `;`, `*a`, empty string, whitespace only. Comparisons, `and`/`or`/`not` and conditional
  expressions are allowed from ch. 43 (2026-10-06) (`is` and `in` comparisons remain disallowed).
  Unknown functions such as `__import__('os')` are `UnknownFunctionError`.

- **The syntax of a user function's body is checked at save time** (decided 2026-10-03).
  Previously a body using syntax that can't be evaluated, such as `p < 1`, `p.real`, `p[0]` or
  `[p]`, could be saved and only failed when called. The body's syntax tree is checked with the same
  allow-list as evaluation (operators, unary operators, function calls, names, numeric constants),
  and if it falls outside, `validate_user_function` raises an `ExpressionError` (it can't be
  saved). Such functions already saved in `user_functions.json` are skipped one by one with a
  warning by the validation on load (they couldn't be called anyway, so no real harm). The
  allow-list lives in one place for both evaluation and checking so they don't drift apart.

#### 6.2 Test plan (strengthened error cases)

Added under `tests/parquet_analyzer/` (not overlapping existing tests):

- **Expressions (`core/expression.py`)**: each row of the table above (rows whose previous result
  was a bug are confirmed to fail before the fix), the values of the non-error cases, exhaustive
  coverage of disallowed syntax (parametrized), that errors are always `ExpressionError`
  (`pytest.raises(ExpressionError)`, written so a raw `ValueError` etc. won't pass), mixed
  full-width operators, brackets and spaces, operator precedence (`-2**2 == -4`, `2**3**2 == 512`,
  `7//2`, `-7%3`), scalar-only expressions, zero-length variables, variables containing NaN and inf,
  and the behavior of `referenced_names`/`referenced_functions` on syntax errors.
- **User functions (`core/functions.py`)**: passing `True`, `inf`, `nan`, an array or `1/0` as a
  constant override is an error; parameter names duplicating constant names; built-in names and
  keywords (`lambda` etc.) as names; an empty body or a body with a syntax error; the caller's
  variables are not visible in the body; evaluating functions made circular by hand-editing the file
  becomes an `ExpressionError` at the depth limit (not a `RecursionError`); each error of
  `check_expression_calls`.
- **Maps (`core/maps.py`)**: NaN, inf, zero length, mixed scalars and arrays as arguments; the wrong
  number of arguments; keyword arguments are errors.
- **Saving (`io/user_functions.py`, `io/maps.py`)**: with breakage not in existing tests (fields of
  the wrong type, `constants` not a dict, a numeric name, huge values) the other functions still
  load and a warning is given.
- **`MainWindow`**: entering the buggy expressions above in the input shows a warning and leaves the
  state unchanged (no derived variable or plot added). **Even if a view contains one of the buggy
  expressions above, only that derived variable (and its dependents) is skipped and the rest loads**
  (previously the whole view failed).

### 18. Editing and deleting derived variables (added 2026-09-30, user request)

Requirement: `specification.ja.md` 5.5. Background: derived variables could already be saved and
reloaded via views (verified: save → open → add an expression → save → open keeps everything), but
there was no way to edit or delete them. Also, the only "editing" — re-adding the same name in
`ExpressionBar` — replaced that variable's cached array but left (a) other derived variables
referring to it and (b) curves already plotted **with the old values** (a bug).

#### 18.1 Tracking dependencies (`core/`)

- `core/expression.py` `referenced_names(expr) -> set[str]`: the variable names an expression
  refers to (every `ast.Name` except `Call` function names). Parsed after the same NFKC
  normalization as `evaluate_expression`. A syntax error is an `ExpressionError`.
- `core/variable.py`:
  - `dependency_order(exprs: Mapping[str, str]) -> list[str]` — returns `{name: expression}` in an
    order where each derived variable comes after the derived variables it refers to (names not in
    the mapping = file columns are ignored). A stable sort that keeps the input order as far as the
    dependencies allow. If there are cycles or unparseable expressions, the rest are appended in
    input order (no exception here; callers treat them as ordinary evaluation errors).
  - `dependents_of(name, exprs) -> list[str]` — every derived variable depending on `name` directly
    or indirectly, in `dependency_order` order.

#### 18.2 `MainWindow` operations

- `edit_derived_variable(name, expression)`:
  1. `name` must be an existing derived variable.
  2. Circularity check: if `referenced_names(expression)` contains `name` itself or any of
     `dependents_of(name)`, `ExpressionError` (`error.circular_definition`).
  3. Evaluate the new expression, then the dependent derived variables in dependency order, on an
     overlay where the new values shadow the old cache. Nothing is committed until all succeed (a
     failure midway leaves the original state).
  4. Commit: update `_derived` and the `MaterializedColumn`s in `_raw_variables`, and call
     `TimePlotWidget.replace_series_y(name, y)` on every time-domain plot showing a recomputed name
     (replaces the resident Y array and redraws keeping the curve, color and legend position, also
     updating the cursor labels). If the anchor plot is affected, the navigator is updated too.
- `_on_add_derived_variable` (via `ExpressionBar`) delegates to `edit_derived_variable` if the name
  is an existing derived variable. So redefining from the bar doesn't leave dependents or curves
  stale either.
- `delete_derived_variable(name)`:
  - If other derived variables refer to `name` directly, `ExpressionError` (`error.derived_in_use`,
    listing those variable names). No cascading deletion.
  - Otherwise remove the series from every time-domain plot (FFT plots are not affected; their
    series are computed results named `"<name> (FFT...)"`, not references to the variable), remove
    it from `_derived`/`_raw_variables`/`VariablePanel`, and update the navigator.
- Both are recorded in the operation log (`edit_derived_variable`, `delete_derived_variable`).

#### 18.3 UI

- `VariablePanel` knows which items are derived variables (`set_variables(names, derived=
  {name: expression})`, `add_variable(name, expression=None)`, `remove_variable(name)`). Derived
  variables are shown in italics with `name = expression` in the tooltip. Right-clicking a derived
  variable shows a context menu "Edit expression..." (`editDerivedRequested(str)`) and "Delete"
  (`deleteDerivedRequested(str)`). No menu for file columns.
- Editing happens in `ExpressionBar`, not a dialog: when `MainWindow` calls
  `ExpressionBar.start_edit(name, expression)` the bar enters edit mode (name field read-only,
  current expression filled in, the "Add" button becomes "Update", and a "Cancel" button appears).
  "Update" goes through the same callback as "Add" (`_on_add_derived_variable` already delegates to
  `edit_derived_variable` for existing derived variable names), so on `ExpressionError` it shows the
  error and stays in edit mode keeping the input, and on success it returns to add mode.
  `cancel_edit()` (the Cancel button, or Esc in the expression field) returns to add mode without
  changing anything.
  - Why not a dialog (the first implementation was a dialog, changed the same day after the user
    pointed it out): a modal `QInputDialog` blocks the variable list, so variable names can't be
    dragged or double-click-inserted while editing — exactly the input assistance (ch. 6) the
    expression bar is there to provide.
  - If the variable being edited stops existing (deleted, or the set of derived variables is reset
    by `load_parquet`/`load_view`), `MainWindow` leaves edit mode.
- Deleting first confirms with `QMessageBox.question`; if refused, `error.derived_in_use` is shown
  as a warning.
- Every place that rebuilds the panel (`load_parquet`, `set_time_column`, `load_view`) passes the
  current derived-variable mapping so the visual distinction isn't lost.

#### 18.4 Views

- `save_view_dialog` writes `derived_variables` in `dependency_order` order (insertion order isn't
  enough: editing `a` to refer to a later-defined `b` would make a file whose first entry can't be
  evaluated on load).
- `load_view` also applies saved derived variables in `dependency_order` order, so badly ordered old
  view files load too.
- No schema change (`schema_version` stays `"1"`).

### 26. Function registry and user functions (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.20. Feature flag `user_functions` (ch. 21). It shares with
ch. 27 (maps) the foundation for "adding functions usable in expressions from outside" (26.1), so
26.1 is built before ch. 27.

#### 26.1 The function registry (extending `core/expression.py`)

- The current `_ALLOWED_FUNCS` (fixed, `{name: (argument count, implementation)}`) stays as the
  **built-in functions**, and extra functions can be passed at evaluation time:
  - `class FunctionRegistry` (`core/functions.py`, new): bundles `builtins` (`_ALLOWED_FUNCS`),
    `user_functions: Mapping[str, UserFunction]` and `maps: Mapping[str, LookupTable]` (ch. 27),
    with `resolve(name) -> callable definition | None` and `kind(name) -> "builtin" | "user" |
    "map" | None`. Duplicate names are forbidden at registration (26.3), so lookup order doesn't
    matter, but just in case it is builtin → user → map.
  - `evaluate_expression(expr, variables, registry: FunctionRegistry | None = None)`: `None` means
    built-ins only (existing calls and tests keep working).
  - The `ast.Call` branch of `_eval_node` looks up with `registry.resolve(name)`. Built-ins and maps
    take **positional arguments only, with a matching count** as before (keyword arguments stay an
    `ExpressionError`). Only user functions allow keyword arguments (26.2). An unknown name is
    `UnknownFunctionError(ExpressionError)` (with the function name in `.name`; added in review so
    the UI can show a translated "function/map <name> not found" without parsing the text; i18n
    message `error.unknown_function`).
- Dependencies: `referenced_functions(expr) -> set[str]` (new; the non-built-in function names of
  `Call`s). `referenced_names` also counts the `Name`s inside keyword argument values as variables
  (`ast.walk` already picks them up; a constant override must be numeric, so a variable there fails
  at evaluation).
- `MainWindow` holds one `self._function_registry` and passes it wherever derived variables are
  evaluated (every place calling `evaluate_expression`: adding, editing (ch. 18), loading views,
  `variable_window`, etc.). When the set of user functions or maps changes, the registry's contents
  are replaced.

#### 26.2 Defining and evaluating user functions (`core/functions.py`)

- `@dataclass UserFunction`: `name: str`, `params: list[str]` (one or more),
  `constants: list[tuple[str, float]]` (ordered, zero or more, `(name, default)`), `expression:
  str`, `description: str = ""`.
- Validation `validate_user_function(fn, registry, *, replaces: str | None = None) -> None`
  (raises `ExpressionError`). `replaces` says "editing/renaming which existing function" (`None` =
  new; for a new one an existing function of the same name is a duplicate error). (Changed in
  review: with the original default "same name means editing itself", a create screen forgetting to
  pass it silently overwrote an existing function.) What is checked:
  - `name`, each of `params` and each constant name are Python identifiers (`str.isidentifier()` and
    not `keyword.iskeyword`). No duplicates between argument names and constant names.
  - `name` doesn't duplicate a built-in function, another user function or a map (except itself when
    editing).
  - (Added in review) The function calls in the body are checked statically: the number of
    arguments (of built-ins, maps and user functions) and keyword argument names (only user
    functions' constant names; no keyword arguments to built-ins or maps). A definition whose call
    chain exceeds the evaluation depth limit (32) is also rejected by validation (so no definition
    remains that only fails when evaluated).
  - `expression` parses, and `referenced_names(expression)` is a subset of `params ∪ constant
    names` (it can't refer to file columns directly; a violation names it with
    `error.function_unknown_name`).
  - Each name in `referenced_functions(expression)` is in the registry.
  - No cycles: follow the call graph among user functions; coming back to itself is an error
    (reusing `error.circular_definition`).
  - Constants' defaults are finite numbers.
- Evaluation (the user-function call in `_eval_node`):
  1. The number of positional arguments == `len(params)` (too many or too few is an error;
     constants can't be passed positionally).
  2. Keyword argument names are constant names (unknown names and duplicates are errors). The value
     is evaluated as an expression and must be a scalar (`np.ndim(v) == 0`) and finite (`k=-1.5` and
     `k=2*3` are fine).
  3. Evaluate the positional arguments to get arrays/scalars.
  4. Evaluate the body with a mapping holding only `{argument name: value, constant name: value
     (override or default)}` as variables (the same registry is passed, so the body can call other
     functions).
  - Recursion depth is prevented by validation, but evaluation also cuts off at depth 32 (against
    corrupt files).

#### 26.3 Saving (`io/user_functions.py`, new)

- Location `config.PARQUET_ANALYZER_CFG_DIR / "user_functions.json"`:
  ```json
  {
    "schema_version": "1",
    "functions": [
      {
        "name": "Power_flow_by_press",
        "params": ["flow_lpm", "press_kpa"],
        "constants": [{"name": "k", "default": 60000}],
        "expression": "flow_lpm * press_kpa / k",
        "description": "flow × pressure → power [kW]"
      }
    ]
  }
  ```
- `load_user_functions() -> list[UserFunction]`: empty if the file doesn't exist. If it is broken
  as JSON or has the wrong shape, **the original file is renamed aside to
  `user_functions.json.bad-<YYYYmmdd_HHMMSS>`**, a `logger.warning` is written, and it is treated as
  empty (so the next save doesn't wipe the content; in line with ch. 12's policy for a corrupt
  settings file). A validation error of an individual function skips just that function, and the
  warnings are collected and returned (`load_user_functions` returns `(functions, warnings)`).
  - (Added in review) The renaming aside is also put in the returned `warnings` (with only a log
    entry, to the user it just looks as if all functions vanished).
  - (Added in review) **A failure to read at all (`OSError`: permissions, a Windows file lock, etc.)
    is not renamed aside.** The caller is told it "couldn't be read", and saving is refused
    meanwhile (saving without having read it would overwrite an intact file with empty content).
  - (Added in review) So abnormal hand-edited content (an integer with an extreme number of digits →
    `OverflowError`, an extremely deep expression → `MemoryError`, etc.) doesn't crash startup, the
    per-function validation also catches exceptions other than `ExpressionError` and skips just
    that function.
- `save_user_functions(functions)`: write to a temporary file, then `os.replace` (never broken by a
  crash midway).
- (Added in review) **Functions skipped when loading (validation errors, a map they use missing,
  etc.) are kept with their original content and written back unchanged on every save.** Their names
  are also treated as in use by the duplicate-name checks of new functions and map imports. (In the
  first version, skipped functions silently disappeared from the file on the next save.)

#### 26.4 Manager window (`ui/function_manager.py`, new)

- "Functions" on the toolbar (shortcut configurable, `user_functions`, none by default). Non-modal
  (so variables can be dragged from the variable panel). Only one at a time (a second open brings the
  existing one to the front).
- Left: the list of functions. Right: two tabs.
  - **Definition** tab: name, arguments (one comma-separated line), constants (a table: name and
    default, add/remove rows), expression, description. "New", "Save" and "Delete" buttons.
    (Added 2026-10-03, user request) **Show input examples**: each field shows a faint example
    (placeholder: name `Power_flow_by_press`, arguments `flow_lpm, press_kpa`, expression
    `flow_lpm * press_kpa / k`, description `flow × pressure → power [kW]`) plus a short description
    of the usable operators and functions (tooltip). In addition, an **"Insert example" button** puts
    this whole example (including the constant `k = 60000`) into the empty definition fields (if
    something is already entered it asks before replacing; it doesn't save). Saving runs 26.2's
    validation, and errors are shown inside the window (not modal).
  - **Apply to signals** tab: for the function selected in the list, a field per argument
    (`QLineEdit` that accepts drops = text from the variable panel; double-click insertion is out of
    scope), a field per constant (default filled in), a field for the result's name, and a "Create"
    button. Creating builds the expression `<function>(<arg1>, <arg2>[, constant=value ...])`
    (writing only constants that differ from the default) and passes it down the existing path for
    adding derived variables (`MainWindow._on_add_derived_variable(name, expr)`). The result,
    including errors, is shown in the tab.
- (Changed during implementation) Changes are applied with
  `MainWindow.update_registry(user_functions=, maps=, touched=, persist=)`. Only if every dependent
  derived variable recomputes successfully does it proceed to `persist` (saving to the file) → commit
  (a failed save changes nothing). On save, every other user function is also re-validated against the
  new definitions.
- **The shape of a derived variable's result** (added in review; ch. 18 too): adding, editing and
  recomputing after a function/map change all go through one common routine that makes it "a 1-D
  float64 as long as the time axis". A scalar (e.g. the expression `2`, a function not using its
  arguments) becomes a constant series broadcast to the time-axis length. Any other shape mismatch is
  a failure, detected and rolled back before committing (in the first version it raised while drawing,
  after the commit).
- Applying an edit: when saving changes a function's expression, arguments or constants,
  `MainWindow.on_user_functions_changed()` replaces the registry and recomputes the derived variables
  that use the function (directly or through other user functions) the same way as ch. 18's
  `edit_derived_variable` — **commit only if all succeed** — and updates the plotted curves with
  `replace_series_y`. If even one fails (e.g. changing the argument count so existing calls no longer
  match), the save is cancelled and the failing derived variables and reasons are shown.
- Renaming: a function used elsewhere can't be renamed (the users are shown). Allowed if unused.
- Deleting: refused if used by derived variables or other user functions, listing the users.
  Deleted after a confirmation if unused.
- Operation log: `user_function_save`, `user_function_delete`, `user_function_apply`.

#### 26.5 Views, view manager

- Views store only derived variables' expressions, as before (no schema change). Function
  definitions are not copied.
- `load_view`: a derived variable whose function is missing goes down the existing "skip with a
  consolidated warning" path via the `ExpressionError` at evaluation (ch. 8). Dependent derived
  variables and series are skipped likewise. The warning makes clear that "a function is missing"
  (the `error.unknown_function` text).
- The view manager's (ch. 20) missing marks must follow the same rules as `load_view` (see dev rules):
  if any function used by a derived variable's expression (`referenced_functions`) isn't in the
  registry, that derived variable is treated as missing, spreading transitively to its dependents.
  Shown as "function <name> not found". `ViewManagerDialog` is given the set of available function
  names. (Changed during implementation, accepted in review) The text is the combined "function/map
  "<name>" not found" (a missing name is in neither the function nor the map namespace, so which one
  it was can't be known). Besides whether the name exists, marks also come from a static check of the
  argument count and keyword argument names (the same rules as 26.2's validation), matching what
  `load_view` actually skips.

#### 26.6 Test plan

- Evaluation: `Power_flow_by_press(a, b)` gives the right value with the default constant;
  overriding with `k=1000`; `k=-1.5` and `k=2*3` are fine; `k=a` (an array) is an error; unknown
  keywords, duplicates and too many/few positional arguments are errors; the body can call built-ins
  and other user functions; keyword arguments to built-ins are errors as before.
- Validation: a name that isn't an identifier, duplicates with built-in or map names, referring to a
  column directly in the body, an unknown function, direct and indirect cycles, and a non-finite
  default are each errors.
- Saving: round trip; no file; renaming aside broken JSON (a `.bad-*` appears and the original
  content survives); with one invalid function the others still load and a warning is returned;
  writes go through a temporary file.
- `MainWindow`: a derived variable made with a function can be plotted; editing the function updates
  the values and the curve, and the dependent derived variables too. If an edit breaks existing calls,
  the save is cancelled. Deleting or renaming a function in use is refused.
- Apply to signals: putting variable names in the fields and creating → a derived variable with the
  expected expression (constants equal to the default aren't written in the expression).
- Views: loading a view containing a derived variable that uses a function, with the function
  deleted → only that derived variable and its dependents are skipped, and the warning names the
  function. The view manager's marks match `load_view`'s actual skips (a test checking both on the same
  view).
- Flag: with `user_functions` disabled it isn't on the toolbar (the registry and evaluation stay
  active — opening a view in an environment that already has `user_functions.json` still evaluates the
  expressions).

### 27. Maps (tables) (added 2026-10-02, user request)

Requirement: `specification.ja.md` 5.21. Feature flag `maps` (ch. 21). Uses 26.1's registry.

#### 27.1 Data and interpolation (`core/maps.py`, new)

- `@dataclass LookupTable`: `name`, `kind: "curve" | "map"`, `x: np.ndarray` (strictly
  increasing), `y: np.ndarray` (curve: the output values, `len(x)` of them / map: the Y-axis values,
  strictly increasing), `z: np.ndarray | None` (map: shape `(len(y), len(x))`), `interpolation:
  str`, `extrapolation: str`.
- Calling: a curve takes one argument `table(x)`, a map two `table(x, y)`. Arguments are arrays or
  scalars (broadcast). The return value is a float64 array of the same shape as the input. A NaN
  input gives a NaN output.
- **The list of interpolation methods** (`INTERPOLATIONS: dict[str, InterpolationSpec]`, with
  `kinds` saying whether it works for curve/map). Shaped so one more can be added later by adding one
  entry (the same idea as ch. 6's `_ALLOWED_FUNCS`):
  | Name | curve | map | What |
  |---|---|---|---|
  | `linear` (default) | ○ | ○ (bilinear) | linear interpolation between neighboring points |
  | `nearest` | ○ | ○ | the value of the nearest grid point |
  | `previous` | ○ | ○ | on each axis, the value at "the largest grid point not above the value" (stepwise) |
  | `spline` | ○ | × | natural cubic spline |
  - `spline` is implemented with numpy, without scipy (a tridiagonal linear system; scipy is only in
    the `convert` dependency group, and adding it to the main dependencies would make the distribution
    bigger). For a curve with fewer than 3 points it falls back to `linear` (with a warning at import).
- **The list of out-of-range handling** (`EXTRAPOLATIONS`), applied per axis:
  | Name | What |
  |---|---|
  | `clamp` (default) | the edge value (the input is clamped to the axis range before interpolating) |
  | `zero` | 0 outside the range |
  | `linear` | extend with the edge slope: for linear/bilinear interpolation, the slope of the two edge points (the edge grid cells for a map); for `spline`, the slope of the edge **tangent** (a natural spline has zero second derivative at the edges, so extending along the tangent joins smoothly; changed in review: the original "slope between the two edge points" made a kink at the table's edge). For `nearest` and `previous`, the edge value |
  | `nan` | NaN outside the range |
  - Combinations that make little sense, such as `previous` with `linear` extrapolation, are not
    forbidden (`previous` is defined to give the edge value outside the range).
- Implemented vectorized (`np.searchsorted`), so even an input of 10 million points is handled in one
  call.
- With `nearest`, a value exactly halfway between two grid points takes the larger grid point's value.
- When an input is ±inf, `linear` extrapolation gives ±inf or NaN as computed (no exception).

#### 27.2 Reading the CSV (`io/maps.py`, new)

- Encoding: read as `utf-8-sig`, falling back to `cp932` on failure (for Japanese CSVs saved by
  Excel). Comma-separated. Blank lines and surrounding whitespace are ignored.
- **Curve**: two columns. If the first row isn't numeric it is skipped as a header. Each row `x, y`.
- **Map**: row 1 = [the top-left cell (empty or any text), X-axis values…], rows 2+ = [Y-axis value,
  Z values…]. If a row's column count differs from row 1's, an error (with the row number).
- The kind is chosen in the import dialog (default is automatic: two columns means a curve; three or
  more with the top-left cell empty or non-numeric means a map). **Three or more columns with a
  numeric top-left cell can't be detected automatically and are an error**, and the user chooses
  curve/map in the dialog (clarified in review: so an all-numeric `x, y1, y2` file isn't silently read
  as a strange map).
- The first row is skipped as a header only when **no cell can be read as a number**. A row where only
  some cells are non-numeric is an ordinary error with row and column numbers (found in review: a row
  with even one non-numeric cell was treated as a header, so the first data point vanished silently).
- Validation (errors are `MapError(ValueError)` with row and column numbers in the message): cells
  that can't be converted to numbers, duplicate or non-monotonic axes (**not ascending is an error**;
  it isn't sorted — so mix-ups get noticed), non-finite values in the axes or Z, fewer than 2 points
  in a curve, fewer than 2 points on either axis of a map.
- Returns a `LookupTable` (`interpolation`/`extrapolation` from the import dialog).

#### 27.3 Saving (`data/parquet_analyzer/maps/`)

- `config.PARQUET_ANALYZER_DATA_DIR / "maps"`. Importing creates:
  - `<name>.csv` — **a straight copy** of the source CSV (`shutil.copy2`).
  - `<name>.json` — `{"schema_version": "1", "name", "kind", "interpolation", "extrapolation",
    "source_path" (absolute path of the source file, for re-import / reference), "imported_at" (ISO
    8601)}`.
- `list_maps()`, `load_map(name) -> LookupTable` (re-reads the copied CSV with 27.2),
  `save_map_settings(name, interpolation, extrapolation)`, `reimport_map(name)` (re-reads from
  `source_path` and overwrites the CSV; an error if the source file is gone), `delete_map(name)`
  (deletes both files).
- Name validation works like view names (`Path(name).name == name`), plus it must be a Python
  identifier (it is called from expressions) and must not duplicate a built-in function, user function
  or other map.
  - (Added in review) Validation happens not only at import but **wherever a file path is built** (on
    every path: loading, changing settings, re-importing, deleting) (with it only at import,
    `delete_map("../x")` could delete a file outside `maps/`). `list_maps` / loading all maps skip, with
    a warning, files whose names are invalid or equal a built-in function (the files aren't deleted).
- At startup (`MainWindow.__init__`) all maps are loaded and registered in the registry. Maps that
  can't be read aren't registered, with one consolidated warning (the files aren't deleted).

#### 27.4 Manager window (`ui/map_manager.py`, new)

- "Maps" on the toolbar (shortcut configurable, `maps`, none by default). Non-modal, one at a time.
- Left: the list of maps (name, kind). Right: the selected map's settings (interpolation and
  out-of-range handling as combos; `spline`, usable only for curves, can't be chosen for maps), the
  source file and import time, and a preview:
  - A table (`QTableWidget`, read-only).
  - (Added in review) A map's original-grid heat map is drawn **at the real (possibly uneven) axis
    value positions** (`PColorMeshItem`, with cell boundaries at the midpoints between neighboring
    grid points). The interpolation result is shown as a second heat map next to the original grid
    (not overlaid). The 3D switch comes once 25.3 exists.
  - Graph: a line for curves (with points at the grid points), a heat map for maps (`ImageItem` + a
    color bar, axes at the real X/Y values). When 25.3's 3D widget is usable (`xyz_3d` flag on and
    OpenGL available), a "3D" switch shows a surface (`set_surface`). In 3D the surface replaces the
    two heat maps, and the overlaid interpolation result and the color bar aren't shown (2026-10-03,
    confirmed in review). A map's grid is validated at import to hold only finite values, so
    `set_surface`'s NaN handling belongs to the general API (it doesn't happen for maps).
  - In addition, the **interpolated curve/surface** computed with the selected interpolation and
    out-of-range handling is overlaid so the differences between settings can be seen (curve: computed
    on fine points extended 10% beyond either end of the range).
- Buttons: "Import…" (file chooser → a dialog for name, kind, interpolation and out-of-range handling;
  the default name is the file name turned into an identifier), "Re-import", "Delete".
- Applying a settings change or re-import: as in 26.4, recompute every derived variable using the map
  and commit if all succeed, roll back on failure.
- Deleting: refused if used by derived variables or user functions, showing the users.
- Operation log: `map_import`, `map_reimport`, `map_settings`, `map_delete`.

#### 27.7 Editing a map (name, axis names) (added 2026-10-03, user request)

- The manager's "Edit…" button (or double-clicking the list) opens a dialog for the name and the axis
  names (X, Y, Z; X, Y for a curve).
- Renaming: the same validation as 27.3 (an identifier; no duplicate with built-in functions, user
  functions or other maps). **Refused if used by derived variables or user functions**, showing the
  users (the same rule as renaming user functions in ch. 26; expressions are never rewritten
  automatically). If unused, `<name>.csv`/`<name>.json` are renamed to the new name and the registry is
  re-registered.
  - Old names remaining in saved views are not rewritten (loading such a view skips with a warning, as
    in 27.5).
  - A failure midway restores the original state (the renames are committed only when both files have
    been renamed).
- Axis names: `axis_names` (`{"x": str, "y": str, "z": str}`, empty means the default "X", "Y", "Z") is
  added to the JSON. Old JSON (without the key) reads as the defaults. A curve CSV's header row (e.g.
  `rpm,gain`), if any, becomes the default at import. Used for the preview (table headers, heat map and
  curve axes) and the 3D surface's axes.
- Operation log: `map_rename` (`old`, `new`), `map_axis_names`.
- Tests: renaming an unused map changes the files and the registry, and expressions work with the new
  name; refused when in use (showing the users); duplicate and invalid names refused; a failure midway
  through renaming files restores the original; saving axis names and reading old JSON; defaults from a
  curve's header row.

#### 27.8 Editing map values, preview height (added 2026-10-03, user request)

- **Editing values**: the manager's table (`QTableWidget`) becomes editable, and cell values (Z and the
  X/Y axis values; x and y for a curve) can be rewritten. While editing, the "Save" and "Revert" buttons
  are enabled and changed cells are marked by color.
  - Validation (on save): all finite numbers, axes strictly increasing (the same rules as 27.2; a
    violation shows the row and column and doesn't save).
  - Saving: the saved copy of the CSV (27.3's `<name>.csv`) is rewritten in 27.2's format, and
    `edited_at` (ISO 8601) is added to the JSON. As in 26.4, every derived variable using the map is
    recomputed, committing on success and rolling back on failure (the CSV is restored too). The
    preview is updated.
  - "Revert": discard unsaved edits and reload the table. Selecting another map or closing the window
    while editing asks whether to save.
  - Re-importing overwrites edits, so if there is `edited_at` it asks first.
  - Adding or removing rows and columns is out of scope in this version.
- **Preview height**: the table and the graph (heat map, curve) are in a `QSplitter`, so the height can
  be changed by dragging. The ratio is saved in the settings (`map_manager_splitter`).
- Operation log: `map_values_edit` (on save; the number of changed cells), `map_values_revert`.
- Tests: editing a cell and saving changes the CSV and the registry and the derived variable's values
  change. Invalid values and descending axes aren't saved (the display stays). A failed recompute
  rolls back. "Revert". Switching or closing while editing asks. Re-importing asks. Saving and restoring
  the splitter ratio.

#### 27.9 Before/after display and 3D while editing (added 2026-10-03, from the user's real-machine check)

- **Before / after**: while the table has unsaved changes, the two graphs (currently "table" and
  "interpolation result") become "**Before**" (the saved values) and "**After**" (the values being
  edited, updated on every cell change). Both are drawn with the same color range (the min and max over
  both sets of values) so the difference shows. Saving or reverting goes back to the original two. For a
  curve, before (thin gray) and after (thick color) are overlaid in one graph. While the edited values
  are invalid (non-numeric, axes not ascending), "after" isn't drawn and the reason is shown.
- **Switching to 3D**: while editing, the map manager's "3D" switch (with axis names, 25.5) shows two 3D
  surfaces, before and after (two `Xyz3DView`s side by side). The two cameras are linked (rotating one
  gives the other the same viewpoint; Home, XY, YZ, ZX apply to both). The Z range (normalization) is
  decided from both sets of values together (so the same height means the same value). Switching heat
  map ⇔ 3D works at any time, including while editing.
- No operation-log additions (27.8's `map_values_edit`/`map_values_revert` as is).
- Tests: editing switches to the before/after graphs, and saving or reverting switches back. The color
  range covers both. While values are invalid, "after" isn't drawn. In 3D there are two surfaces and the
  cameras are linked (one's `setCameraPosition` is reflected in the other). Check images on the real
  machine.

#### 27.10 Maps and curves with empty cells (added 2026-10-06, from analysis ch. 44)

Background: when "keep empty cells empty" is chosen for a map made from measurements (analysis ch. 44), the map is registered as is
so that it can be used in expressions. Decision (2026-10-06, user): the handling of empty cells is selectable (the default is to fill).

- **CSV** (27.2): for the Z of a map and the y of a curve, read an **empty cell** (empty string or whitespace only) as missing (NaN).
  Axis values still do not allow empty or non-finite values. Cells written as `nan` or `NaN` are also read as missing (the export writes empty cells).
  `inf` is still an error. A map or curve with no finite value at all is an error.
  At import, warn once: "there are n empty cells (the values that depend on them become missing)" (the import continues).
- **Interpolation** (27.1):
  - `linear` (bilinear / linear): if any grid point used in the calculation (one with non-zero weight) is missing, the result is missing.
    An input exactly on a grid point gives that grid point's value (missing if it is missing).
  - `nearest`, `previous`: the value of the selected grid point (missing if it is missing).
  - `spline` (curves): not selectable for a curve that contains missing values (import or setting change gives an error, and points to `linear`).
  - Out-of-range handling: `clamp` and `linear` extrapolation use the edge grid points and edge slope. If those are missing, the result is missing.
    `zero` and `nan` are as before.
- **Manager window** (27.4, 27.8, 27.9): empty cells in the table are shown blank, and can be made empty by editing (the validation on save
  allows "a finite number or empty"; axes are as before). The heatmap and 3D surface previews do not draw empty cells (the same drawing as 44.3).
  Curves are not connected across missing points.
- **Expression evaluation** (26, 36.8): missing values pass through expressions just like ordinary values, so there is no change on the evaluation side.
- Tests: reading CSVs with empty cells (map and curve, the `nan` notation, all-empty is an error, empty axis is an error, the warning appears once);
  the extent to which missing values propagate in each interpolation and out-of-range handling (in bilinear, only the 4 cells touching a missing grid
  point become missing, and the others keep their previous values); rejection of `spline`; empty a cell in the table, save, CSV has an empty cell,
  reload gives the same; the preview draws without exception; the values of an existing map without empty cells do not change (regression).

#### 27.11 Freezing the table's axes; clicking a graph moves to the table cell (added 2026-10-14, user request, dev notes item 42)

Requirement: the last item of 5.21. Background (dev notes "additional features", from the user): when a large map's table is scrolled,
it is no longer clear which value corresponds to which axis value. When a point of interest is found in the graph, finding the same cell in
the table is tedious. Decision (2026-10-14, user): the **first row and first column are frozen** (the option of putting values in the headers
is not adopted). Only **one direction, from the 2D graph to the table**, is supported (3D is out of scope; no markers from table to graph).

**(1) Freezing the table's first row and first column (maps only)**

- Current table (layout of 27.8): first row = X-axis values, first column = Y-axis values, top-left = the corner cell `<Y name> \ <X name>`.
  Qt's headers are hidden. This layout, the ability to edit the axis values in cells (27.8), and the coloring and bold of the axis cells and the
  italic of filled cells (44.4) do not change.
- The freeze is built the same way as Qt's "Frozen Column" example: over the table (`QTableWidget`), **three `QTableView`s that share the same
  model and the same selection model are overlaid** (the top row, the left column, and the top-left corner).
  - Top: shows only the height of row 0, and its horizontal scrolling is synchronized with the table (no vertical movement). Left: shows only the
    width of column 0, and its vertical scrolling is synchronized with the table (no horizontal movement). Corner: only the intersection of row 0
    and column 0. The overlaid views show no scroll bars or headers and have no frame.
  - Position and size are aligned to the top-left of the table's `viewport`, and re-aligned whenever the height of row 0, the width of column 0 or the
    size of the table changes (`resizeEvent`, `sectionResized`, and changes to the number of rows or columns of the model).
  - Because the model is the same, editing a cell in an overlaid view is simply editing a cell of the table (`itemChanged` fires, and the change
    marks, "Save" and "Revert" of 27.8 and the before/after graphs work as they are). Because the selection model is the same, there is one selection.
  - Keep the current cell from being hidden under the frozen part: when moving to a cell in row 1 or later, or column 1 or later, by key or by `scrollTo`,
    scroll with an offset equal to the frozen rows and columns (the same idea as overriding `moveCursor`/`scrollTo` in Qt's example).
  - Implement this as a small subclass of `QTableWidget` (e.g. `FrozenAxesTable`), and `MapManagerWindow` only replaces the current `preview_table` with it.
    Whether freezing is on is switched with `set_frozen(bool)`.
- **Curves** (two columns, which show Qt's headers) are not frozen (`set_frozen(False)`; the vertical headers already remain when scrolling).
  When switching the selection between map and curve, the overlaid tables must not remain or stay shown.
- When the table's rows and columns fit on one screen, the freeze may still be shown (the appearance is the same as now).

**(2) Clicking a graph moves to the table cell**

- Targets: all 2D previews: heatmaps (the two normal views "table" and "interpolated result", and the two while editing, "before" and "after", 27.9)
  and curves (normal and while editing). 3D (27.4, 27.9) is out of scope.
- Click detection: only a left-button press and release without dragging. Received via pyqtgraph's `scene().sigMouseClicked` (it does not react
  to drag = pan), and converted to `ViewBox` coordinates. Plots are recreated each time the plot is redrawn (`_reset_plots`), so the connection is made each time.
- How the cell is chosen (which axis values decide it):
  - Heatmap: not the **grid** the graph draws, but the **table grid** (normally the axes of the saved map; while editing, "after" uses the axes being
    edited, and "before" uses the saved axes): using `cell_edges` (27.4), find the cell `(iy, ix)` containing the clicked X and Y. The
    "interpolated result" is drawn on a finer grid, but it is mapped back to the table cells. Outside the table (outside the edge cells), do nothing.
  - Curve: the row `i` of the cell containing the clicked X (`cell_edges` of X). Y is ignored (the user does not need to aim near the line).
    While editing, use the "after" axes (or the saved axes if the edited ones cannot be drawn).
  - Empty cells (27.10) and filled cells (44.4) move in the same way.
- Moving: for a map, select the table cell `(iy+1, ix+1)` as the current cell; for a curve, `(i, 1)`; scroll with `scrollTo` so it is visible
  (not hidden under the frozen part of (1)). Move focus to the table (so typing a number then edits it as in 27.8).
  Unsaved changes while editing are not discarded, and the graphs are not redrawn.
- Operation log: `map_preview_cell_jump` (`name`, `cell` = `[iy, ix]` or `[i]`, `plot` = `"before"|"after"|"table"|"result"|"curve"`).
  Written at the very start of the click.

**(3) Test plan** (new file `tests/parquet_analyzer/test_map_table_frozen.py`, following how the existing `test_map_values_edit.py` and `test_map_compare.py` are built)

- Freezing: show a map of about 30 × 30, scroll the table to the bottom right; the horizontal scroll value of the overlaid top table equals the table's,
  its vertical is 0; the left overlaid table's vertical equals the table's, its horizontal is 0. The overlaid tables are at the top-left of the table's
  `viewport`, with height = height of row 0 and width = width of column 0. Changing the width of column 0 also changes the width of the overlaid table.
- Editing in the frozen part: rewriting an axis cell in the model of the top overlaid table changes the same cell of the table; the change mark and
  "Save" and "Revert" become enabled (the same path as 27.8). It can be saved.
- Not hidden: scrolled to the bottom right, `setCurrentCell(1, 1)` plus `scrollTo`, then the cell's `visualItemRect` does not overlap the frozen part.
  The left arrow key moves from column 1 to column 0.
- Curves: the overlaid tables are not visible. Switching map → curve → map leaves exactly one set of overlaid tables, which appear correctly.
- Click: in the heatmap's "table" and "interpolated result", click the centre of a cell (`QTest.mouseClick` on the plot's `viewport`; coordinates from
  `ViewBox.mapViewToScene` → `mapFromScene`) → the current cell of the table is `(iy+1, ix+1)`. On an irregular axis, clicks on either side of a cell
  boundary (midpoint) move to the adjacent cell. Clicks outside the table do not change it. Dragging (panning) does not change it. Clicking the X of a
  curve point → `(i, 1)`. While editing, "after" with an edited axis is decided by the axis being edited. Unsaved changes remain. One
  `map_preview_cell_jump` record in the operation log.
- Visual: take an image of a larger map scrolled to the bottom right, and confirm the frozen row and column are visible.

#### 27.12 Clicking a 3D surface moves to the table cell (added 2026-10-14, user request, dev notes item 44)

Background: after the user checked 27.11 on a real machine, the request came: "can this also work in 3D?" (2026-10-14). 27.11 excluded 3D point
selection as difficult, but the 3D component (`Xyz3DView`, analysis 25.3) already picks the grid point nearest to the mouse position to show its
value (`pick_at`, within `HOVER_RADIUS_PX` = 12 pixels on screen), so clicks can be handled by the same mechanism. Decision (2026-10-14, approved by the user):
extend 27.11 (2) to 3D.

- Targets: the surface of the 3D view in the map manager (27.4), and the two surfaces (before and after) while editing (27.9). The 3D of the XY window
  (25.3) is out of scope (it has no table).
- Click detection (`Xyz3DView`): only when the left button is pressed and released **without moving** (movement between press and release within a few pixels,
  e.g. less than `QApplication.startDragDistance()`). Dragging (rotation, pan) works as now. Double-click keeps its current "return to home" behavior
  (the first click of a double-click may move too, since moving does not change any value).
  - At the release position, call `pick_at`; if a grid point is hit, emit a new signal (e.g. `gridPointClicked(iy, ix)`). If none is hit, do nothing.
    Grid points of empty cells (NaN, 27.10) cannot be selected in the first place (the rule of `set_surface`), so there is no move.
  - `pick_at` returns an index into `scene["pos"]`, so `set_surface` also keeps the grid index `(iy, ix)` of each candidate, and converts to it.
    For non-surface displays (scatter plots), no signal is emitted.
- Map manager window (`MapManagerWindow`): each time a 3D view is created, connect the signal and pass it to the same `_jump` processing as 27.11
  (select and scroll to the table cell `(iy+1, ix+1)`, and move focus to the table; unsaved changes are not discarded, and nothing is redrawn).
  The grid is the axes being edited for the "after" surface, and otherwise the saved axes (as in 27.11).
- Implementation note (2026-10-14): `set_surface` always keeps `scene["grid_index"]` (the vertex number per candidate = `ix * ny + iy`); previously
  `extra_index` existed only when the function had `extra`. `Xyz3DView.grid_index_of(i)` converts to `(iy, ix)`.
  The press position is recorded by `eventFilter` when the left button is pressed, and on release it is judged to be a click by comparing (Manhattan distance)
  with `startDragDistance()`. No signal on the right button or on a release without a press. The 3D in the map manager is reused rather than recreated
  depending on editing, so the operation-log name of the view before is decided by `_plots_mode` at the time of the press (`3d_before` when it is `3d_pair`).
- Operation log: `plot="3d"` for the `map_preview_cell_jump` of 27.11 (before and after: `"3d_before"` / `"3d_after"`).
- Test plan (GL cannot be drawn without a screen, so build `Xyz3DView` the same way as the existing 3D tests, which replace the GL check and the flags):
  - Without replacing `pick_at`, press and release at a position where a known grid point is projected on screen → the signal's `(iy, ix)` is correct.
    When released after moving a little (drag), and at a position far from any point, no signal. No signal on an empty grid point.
  - In the map manager, switch to 3D and click the same way → the current cell of the table is `(iy+1, ix+1)`, and the operation log has one record.
    While editing, before and after are each decided by their own axes, and unsaved changes remain.
  - In environments where GL cannot be tested, skip as the existing 3D tests do. Confirm on a real display (distinguishing click from rotation).

#### 27.5 Views

- The same as 26.5 (only expressions saved; a missing map means skip + warning; the view manager's marks
  follow the same rules; "map <name> not found").

#### 27.6 Test plan

- Interpolation (checked with known values): curve `linear`/`nearest`/`previous`/`spline` (the natural
  spline passes through points of a cubic polynomial with zero second derivative at the edges, and equals
  the input on the points); map bilinear, `nearest`, `previous`; equals Z on the grid points.
- Out of range: `clamp`/`zero`/`linear`/`nan` on each axis of curves and maps (left/right, top/bottom,
  corners).
- NaN input → NaN output, scalar input, broadcasting, empty arrays.
- Performance: evaluating a curve and a map on 10 million points takes a few seconds or less on the test
  machine (a guideline; it must not be extremely slow).
- CSV: curves (with/without header), maps, `utf-8-sig` and `cp932`, blank lines; mismatched column
  counts, non-numeric values, duplicate axes, descending axes, non-finite values and too few points are
  each errors with row numbers.
- Saving: importing creates the CSV copy and the JSON; `load_map` works after deleting the source file;
  re-importing changes the content (an error if the source is gone); round-tripping settings changes;
  deleting; name validation (identifier, duplicates).
- Expressions: derived variables can be made with `map_eff(rpm, torque)` and `curve_k(temperature)`; the
  wrong number of arguments is an error; keyword arguments are errors. A user function's body can call a
  map.
- Applying: changing the interpolation updates derived variables and curves. A map in use can't be
  deleted.
- Views: deleting a map and loading skips + warns; the view manager's marks match.
- By eye: check the preview (a curve's line, a map's heat map with the overlaid interpolation result) in
  an image.
- Flag: with `maps` disabled it isn't on the toolbar (loading and evaluation stay active).

### 31. Missing values in the moving average (added 2026-10-02)

Requirement: 5.24. Background: `rolling_mean` in `core/analysis.py` is computed with `np.convolve`, so a
missing value in the window makes the result missing. Statistics (`basic_stats`) exclude missing values,
so the behaviors differ, but which behavior applied wasn't written in the spec (external review 1, #5).

- Add "if there are missing values in the window the result is missing too" to `rolling_mean`'s
  description in ch. 6's function table.
- Add `rolling_nanmean(y, window)` to `core/analysis.py`: convolve the sum with missing values as 0 and,
  separately, the count of non-missing points (the same edge handling as `rolling_mean`), then
  `sum / count` (missing where the count is 0).
- Add one line `"rolling_nanmean": (2, rolling_nanmean)` to `_ALLOWED_FUNCS` (also usable as a built-in
  of the function registry, 26.1). The window check is the same as `rolling_mean`'s.
- Describe the difference between the two in the expression input's function descriptions (tooltips,
  documentation).
- Tests: on data without missing values it equals `rolling_mean`; missing values in the middle, at the
  start and at the end; a window that is all missing; window width 1; a window longer than the data;
  integer arrays.

#### 31.1 Stating the window position (center) (added 2026-10-04)

Background: the implementation is a **centered** moving average using `np.convolve(mode="same")`, but the function description, tooltip and README
only say "moving average", so it could be misread as a moving average of past values only (for public-version users).

- Window range: for window width n, output i is the average of input `y[i - ⌊n/2⌋]` to `y[i + ⌈n/2⌉ - 1]` (for odd n, (n-1)/2 points on each side;
  for even n, n/2 points before and n/2 - 1 after). At the ends the window is shrunk to the range of the data (as now).
- Places to rewrite (English and Japanese): the function descriptions in `ui/i18n.py` (`rolling_mean`/`rolling_nanmean`), the descriptions in the
  function list (`fnref.desc.rolling_mean`/`fnref.desc.rolling_nanmean`), and the function descriptions in the README (English and Japanese).
  Say briefly "centered moving average (the window around the point)", and the bias for even widths.
- The calculation does not change. A `mode` argument (a trailing-only window) is not added for now (to be specified separately if needed).
- Tests: for odd and even windows, with an input that is 1 at a single point, the output position matches the range above (a regression test that fixes the window position).

#### 31.2 Trailing-only moving averages `trailing_mean`/`trailing_nanmean` (added 2026-10-08, dev notes item 28)

Requirement: addendum to 5.24. Background: the centered moving average (31.1) uses future values too, so it cannot be used for "the average of values known
up to that time" (reproducing control or real-time processing). A `mode` argument to switch to a trailing window was considered, but it is a **separate function
name** (2026-10-08, user decision: the expression grammar and the number of arguments of existing functions do not change, and it can be found as a separate
item in the function list).

- Window range: for window width n, output i is the average of input `y[max(0, i-n+1)]` to `y[i]` (the past n points including that point). At the start
  (i < n-1), the average is taken over the points available (as at the edges of `rolling_mean`; not made missing, 2026-10-08 user decision). When the window
  width is longer than the data, it is truncated to the length (the result equals the cumulative average).
- Add `trailing_mean(y, window)` and `trailing_nanmean(y, window)` to `core/analysis.py`. The calculation may use a difference of cumulative sums (`O(n)`) or
  convolution, but the **handling of NaN in the window** must be as follows:
  - `trailing_mean`: if the window has even one missing value, the result is missing (as `rolling_mean`). If computed with cumulative sums, make sure NaN does not
    propagate to all later points: sum the values with missing set to 0, count the missing values with a separate cumulative sum, and make a point missing when the
    window contains one or more missing values.
  - `trailing_nanmean`: average only the non-missing points in the window; only points whose window is entirely missing are missing (as `rolling_nanmean`).
  - ±Inf enters the calculation as is, as with `rolling_mean` (if the window has an Inf, the result is Inf or NaN). If Inf enters a cumulative sum, all later
    differences become NaN, so when using cumulative sums, handle non-finite values separately, as with missing values, or compute by convolution
    (`np.convolve(y, ones(n))[:len(y)]` = the sum of the past n points, with the count `min(i+1, n)`). **Agreement with the naive implementation** (slicing the
    window at each point and using `np.mean`/`np.nanmean`) is checked by tests.
  - An input of length 0 returns a result of length 0. An integer array gives a float result.
- Checking the window width is the same as for `rolling_mean` (table in 6.1: a finite scalar, a positive integer, and `3.0` are allowed; anything else is `ExpressionError`).
  If there is a common check, use it.
- Registration: two lines in `_ALLOWED_FUNCS` (2 arguments), and **`(n-1, 0)`** in `_FUNC_MARGINS` (n-1 rows before, 0 after; so that the `DerivedColumn` of
  data 36.6 C can be computed correctly with windowed reads). As with `_rolling_margin`, if the window width is not a constant, `None` (full computation).
- Descriptions (English and Japanese): the expression function descriptions and the function list in `ui/i18n.py` (`fnref.desc.*` and examples `fnref.example.*`
  etc., the same set of keys as the existing `rolling_*`), category "Analysis", and the function descriptions in the README (English and Japanese). Add one line
  to the description of `rolling_*` as well: "for a trailing-only window, use `trailing_mean`".
- Item 13.4 (dev notes), "`rolling_mean` cannot specify a time-based window", is out of scope for this change (the window remains in sample counts).

**Test plan**:
- Agreement with the naive implementation (lengths 0 to several hundred, window widths 1 to length + 5, NaN and ±Inf mixed in, integer arrays). Add property tests
  for `trailing_*` built the same way as the existing `test_property_analysis.py` (the `rolling_mean` row in 46.2).
- Window position regression test: with an input that is 1 at a single point (position k), the output is 1/n at `k` to `k+n-1` (at the ends, 1/count).
- Start handling: `trailing_mean([1,2,3,4], 3)` == `[1, 1.5, 2, 3]`.
- Missing values: `trailing_mean` is missing up to n-1 points after a missing value, then recovers (NaN does not propagate to all later points).
  `trailing_nanmean` is missing only for windows that are entirely missing.
- Invalid window widths (0, negative, 1.5, arrays, NaN) raise `ExpressionError`.
- Usable from an expression (`trailing_mean(sine, 10)`). For a windowed derived variable (`DerivedColumn`), `window(lo, hi)` equals `[lo:hi]` of the full calculation
  (the margin `(n-1, 0)` is in effect).
- The function list test (38.1), "every built-in function has a description and example in both en and ja", passes (detects forgotten entries). The examples evaluate.

### 38. Function list window (added 2026-10-03, user request)

Requirement: 5.31. Background: the built-in functions are written in ch. 6's table, the README and
tooltips, but there was no way inside the app to choose one while seeing its description and an example.

- `FunctionReferenceDialog` in `ui/function_reference.py` (non-modal, one at a time). Entry points: a
  "Functions..." button next to the expression input, and a button next to the expression field in the
  function manager (26.4).
- The content is **built from the function registry (26.1)** (the list isn't written by hand; the
  built-ins in `_ALLOWED_FUNCS` are matched with a table of description/example texts (i18n keys), and a
  test detects any built-in function without a description):
  - Built-in functions: category (as in ch. 6's table), syntax (e.g. `rolling_mean(a, window)`),
    description, example.
  - User functions: syntax (arguments and constants' defaults), description (the definition's
    description field), example (a call using the argument names as they are).
  - Maps: kind (curve/map), syntax (`name(x)`/`name(x, y)`), interpolation and out-of-range handling.
  - The list of operators, and a note on missing values (`rolling_mean` propagates, `rolling_nanmean`
    excludes, statistics exclude).
- A search field (substring match on name and description) and filtering by category.
- "Insert into expression" (also by double-click): inserts the example at the current cursor position of
  the expression input it was opened from. Disabled if that input has been closed.
  `log_op("function_reference_insert", name=...)`.
- The list follows the adding and deleting of user functions and maps (26.4/27.4).
- No feature flag (it is a reference window and doesn't change existing behavior). i18n in en/ja.

#### 38.1 Test plan
- Every built-in function (every key of `_ALLOWED_FUNCS`) appears in the list with description and
  example text in both en and ja (fails otherwise = prevents forgetting them when adding a new function).
- User functions and maps appear in the list and it updates on adding and deleting.
- Search and category filtering.
- "Insert into expression" inserts at the cursor position of the input it was opened from (both the
  expression input and the function manager).
- The inserted example can be evaluated as is (the built-in examples evaluate with `feature_test`'s
  column names).

### 43. Conditionals (added 2026-10-06, user request)

Requirement: 5.34. Background: a calculation of the form "use the value only while a condition holds" could not be written; the only
workaround was combining `clip`, `sign` and `max`. No feature flag is used (the change only adds grammar, and the results of expressions
that could already be written do not change).

#### 43.1 Grammar and values (`core/expression.py`)

Truth values are represented as **float64 1.0 / 0.0** (no boolean arrays are created; derived variables are always float64, ch. 18 and 26).
When a value is received as a condition, "non-zero is true, zero is false, NaN is unknown".

| Grammar (AST) | Result | Handling of missing values (NaN) |
|---|---|---|
| Comparison `a < b` etc. (`ast.Compare`; only `Lt LtE Gt GtE Eq NotEq`) | 1.0 / 0.0 | **NaN** if either side is NaN (numpy comparisons return False, so NaN is set explicitly) |
| Chained comparison `a < b < c` | same as `(a < b) and (b < c)` (`b` is evaluated only once) | same as above |
| `x and y`, `x or y` (`ast.BoolOp`; three or more also allowed) | 1.0 / 0.0 (it does not return the operand itself as Python does) | **Three-valued logic** (changed in 43.5, 2026-10-07): `or` gives 1.0 if any operand is true; `and` gives 0.0 if any operand is false. If not decided that way and there is a NaN, NaN |
| `not x` (`Not` in `ast.UnaryOp`) | 1.0 / 0.0 | NaN if NaN |
| `a if c else b` (`ast.IfExp`), `where(c, a, b)` | element is `a` where `c` is true, `b` where false | element is NaN where `c` is NaN (NaN in `a` or `b` passes through as is) |
| `isnan(a)` | 1.0 / 0.0 | gives 1.0 for NaN (it never produces NaN); ±inf gives 0.0 |

- **±inf** is compared as an ordinary number (`inf > 1` is 1.0). Non-zero, so it is true as a condition.
- **Both branches are always computed** (no per-element short-circuit): `where(x > 0, log(x), 0)` computes `log` even for negative `x`, but evaluation
  runs inside `np.errstate(all="ignore")`, so no warning appears, and the value of the branch not selected is discarded (as in 6.1).
- **Scalars and arrays**: the condition and each branch may be scalars or arrays (numpy broadcasting). If all are scalars, the result is a scalar
  (`where(1 > 0, 2, 3)` is `2.0`). Making lengths match for derived variables is done by the existing helper (ch. 18).
- **The constants `True`/`False` remain disallowed** (6.1). Write `1`/`0` when a truth value is needed.
- `is` / `is not` / `in` / `not in` raise `ExpressionError` (they are not numeric comparisons).
- `where` and `isnan` are registered in `_ALLOWED_FUNCS` as built-in functions (argument counts 3 / 1; the checks of 6.1 apply).
  A user function or map with the same name cannot be created because of the existing rule (no duplicates, 26.2). **If a user function or map named
  `where` or `isnan` is already saved**, it is skipped one at a time by the validation on load, with a warning (existing behavior, 26.3 and 27.3).
  If a view's expression uses it, that derived variable is skipped.
- The allow-list is the **same set** in three places: evaluation (`_eval_node`), syntax checking (`check_allowed_syntax`, which is also used when saving user
  functions), and the margin of the range (`_margin`). (If they differed, a user function could be saved but not called, and so on; the policy of 6.1.)

#### 43.2 Large files (`DerivedColumn`, data 36.8)

- Comparison, `and`/`or`/`not`, conditional expressions, `where` and `isnan` are all element-wise, so `_margin` returns the maximum of the children's margins
  (`_combine`) (`where` and `isnan` are registered in `_FUNC_MARGINS` as `(0, 0)`). Without this, an expression containing a condition would go back to
  full computation and become slow and heavy on 100 million rows (the note in 36.8).
- Tests confirm that the result computed window by window matches the result of the whole computation (including the edge rows of the window).

#### 43.3 Screen and documentation

- Function list (ch. 38): add `where` and `isnan` under a new category "Conditional" (descriptions and examples in en and ja; the test of 38.1 detects forgotten entries).
  Add comparisons, `and`/`or`/`not`, and conditional expressions to the operator list, plus the handling of missing values (the table above).
- Add to the function and operator tables in the tooltip of the expression input and in the README (en/ja). Add a "Conditional" row to the function table in ch. 6.
- Error messages: for disallowed comparisons (such as `is`), use the existing "this syntax can't be used" family of messages (no new i18n keys are expected;
  if added, add them in en and ja).

#### 43.4 Test plan

- **Values** (parametrized): six comparisons × (arrays, scalars, NaN, ±inf); chained comparisons (`0 < a < 5`, with the middle term evaluated only once);
  `and`/`or`/`not` (including three or more operands); `a if c else b` and `where` give the same result; mixed scalars and arrays in conditions and branches;
  all-scalar gives a scalar; `isnan`.
- **Missing values**: NaN propagates as in the table above (NaN in comparisons, `not` and conditions gives NaN; `and`/`or` use three-valued logic (43.5); NaN in branches
  passes through; `isnan` does not propagate).
- **Types**: the result is always float64 (no boolean or integer arrays leave the evaluator). `(a > 0) * a` can be computed.
- **Errors**: `is`/`in` comparisons, `True`, a wrong number of arguments to `where`, keyword arguments → `ExpressionError`.
  Remove comparisons, `and`/`or`/`not` and conditional expressions from the parametrized "disallowed grammar" list of 6.1, and confirm the allowance here
  (confirm the tests fail on the implementation before the change).
- **User functions**: a function whose body contains conditional expressions, `where` or comparisons can be saved and evaluated (`check_allowed_syntax`
  allows it). A function named `where` cannot be created. A function named `where` already in `user_functions.json` is skipped on load, with a warning.
- **Large files**: an expression containing a condition becomes a `DerivedColumn` (it does not return to full computation); the results window by window match the full calculation.
- **Screen**: adding `where(sine > 0, sine, 0)` from the expression input can be plotted, and saving to a view and loading restores it. `where` and `isnan`
  appear in the function list (the existing test of 38.1 passes).

#### 43.5 Fixes found in review (2026-10-07, approved by the user)

Found in the review after merging (2026-10-07).

- **Make `and`/`or` three-valued** (changes the table in 43.1). Element by element:
  - `or`: if any operand is true (a finite non-zero value, or ±inf), the result is 1.0. Otherwise, if any operand is NaN, the result is NaN. Otherwise 0.0.
  - `and`: if any operand is false (0), the result is 0.0. Otherwise, if any operand is NaN, the result is NaN. Otherwise 1.0.
  - Reason: with the original rule "NaN if any operand is NaN", the natural way to replace NaN, such as `where(isnan(x) or x < 0, 0, x)`, does not work
    in exactly the rows where NaN occurs (which makes adding `isnan` pointless). The NaN handling of `not`, comparisons and conditions is unchanged.
- **Do not change the kind of error raised inside comparisons and `and`/`or`**: currently the evaluation of operands is inside `try/except (TypeError,
  ValueError)`, so `UnknownFunctionError` (a child of `ValueError`) turns into a plain `ExpressionError` with a "comparison: …" or "and: …" prefix
  (`1 < foo(x)`, `x > 0 and foo(x)`; `foo(x) < 1` is unchanged, so the result depends on order). As a result, the translated "unknown function" message
  does not appear in expression input or view loading (`main_window`). Operands are now evaluated outside the `try` (as in `IfExp`), and the `try` wraps
  only the operations between arrays.
- Tests: (1) the truth table of three-valued logic (all combinations of true/false/NaN, two and three operands, arrays and scalars); (2) `where(isnan(x) or x < 0, 0, x)`
  gives 0 in rows where x is NaN; (3) `1 < foo(x)`, `x > 0 and foo(x)`, and `not foo(x)` raise `UnknownFunctionError`, and the message for an unknown
  variable in `x < (1 < y)` does not contain "comparison:"; (4) entering `1 < foo(x)` in the expression input shows the translated "unknown function" message.
  (1), (2) and (3) must fail before the fix.

## Relations to other domains

- **Data** ([data.md](./data.md)): expression evaluation reads only the needed columns via
  `LazyVariables`.
- **Plots** ([plots.md](./plots.md)): editing derived variables, user functions or maps updates the
  curves shown.
- **Persistence** ([persistence.md](./persistence.md)): views store only expressions (function and map
  definitions aren't copied). The view manager's "missing" marks are judged by the same rules as
  `load_view`'s skips, including whether functions and maps exist. User functions are saved in `cfg/`,
  maps in `data/`.
- **Analysis** ([analysis.md](./analysis.md)): the map preview uses the 3D widget (25.3).
- **App foundation** ([app.md](./app.md)): feature flags `user_functions`, `maps`.
- **Data** (ch. 31, 2026-10-03): `rolling_nanmean` lives in `core/analysis.py`.
- **Persistence** (ch. 33): derived variables are recomputed after switching to the view's time-axis
  column.
- **App foundation** (ch. 30): operations in the function and map managers are recorded in the operation
  log, and confirmation dialogs go through `ui/messages`.
- **Data** (36.8, 2026-10-04): on large files, an expression using only functions with a margin
  definition (`_FUNC_MARGINS`) becomes a `DerivedColumn` and is computed in the background for the
  visible range only. When adding a function, add its margin definition too (without one it falls back
  to computing in full). Background computation uses a snapshot of the functions and maps taken when the
  column was built.
- **Data** (43.2, 2026-10-06): conditional grammar, `where` and `isnan` are also element-wise, so `_margin` treats them as margin 0 and they are eligible for `DerivedColumn`.
