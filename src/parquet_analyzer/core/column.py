from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Protocol

import numpy as np

from .expression import ExpressionError
from .numeric import NonNumericColumnError, to_numeric
from .pyramid import DEFAULT_BUCKET_ROWS, ColumnPyramid, build_pyramid, build_pyramid_from_windows

if TYPE_CHECKING:
    from .data_source import ParquetDataSource

logger = logging.getLogger(__name__)

MAX_PYRAMID_FAILURES = 3  # detailed_specification.md 28.2: give up on a column's pyramid after this many failed builds
_UNSET = object()  # sentinel distinguishing "bounds not looked up yet" from "no bounds exist"


def _numeric_column(raw: np.ndarray, name: str) -> np.ndarray:
    """to_numeric() for the file column `name`: a column that cannot become a 1-D float64
    series (string, binary, time-of-day, list, struct, ...) raises NonNumericColumnError
    naming the column, instead of a bare numpy/polars error (data 29.5)."""
    try:
        return to_numeric(raw)
    except NonNumericColumnError as e:
        raise NonNumericColumnError(f"column {name!r} is not numeric: {e}") from e


class Column(Protocol):
    """A variable's data, materialized or not yet. `MainWindow._file_columns`/
    `_raw_variables` hold these instead of raw ndarrays (detailed_specification.md
    13.5.1 Phase C) so a column loaded from a file but never actually used doesn't
    have to cost anything beyond holding this small wrapper.
    """

    def values(self) -> np.ndarray: ...

    def window(self, row_start: int, row_end: int) -> np.ndarray: ...


class MaterializedColumn:
    """A column whose data is already a real, resident ndarray — what a derived
    variable's computed result becomes, and what the currently-selected time
    column is (still read eagerly at open; see LazyColumn)."""

    __slots__ = ("_array",)

    def __init__(self, array: np.ndarray):
        self._array = array

    def values(self) -> np.ndarray:
        return self._array

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        return self._array[row_start:row_end]


class LazyColumn:
    """A file column not read from disk until `values()`/`window()` is first
    called, then cached on this object for the rest of the session (Phase C:
    "materialize on first use"). Used when the whole column comfortably fits in
    memory (under Settings.eager_load_limit_mb — see WindowedColumn for the
    over-threshold case, Phase D). A file with 20 columns of which the user only
    ever plots 2 now only ever materializes those 2, instead of all 20 at open
    time.
    """

    __slots__ = ("_data_source", "_name", "_array")

    def __init__(self, data_source: ParquetDataSource, name: str):
        self._data_source = data_source
        self._name = name
        self._array: np.ndarray | None = None

    def values(self) -> np.ndarray:
        if self._array is None:
            raw = self._data_source.read_columns([self._name])[self._name]
            self._array = _numeric_column(raw, self._name)
        return self._array

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        return self.values()[row_start:row_end]


class WindowedColumn:
    """A file column too large to comfortably keep fully resident (over
    Settings.eager_load_limit_mb): `window()` fetches only the requested row
    range from disk each time, via ParquetDataSource.read_window()'s
    block-cached reads (Phase B) — never the whole column at once. `values()`
    (needed by expression evaluation, and by the "nothing plotted yet"
    stats/navigator fallback, which genuinely want everything) falls back to
    `window(0, row_count)`, so it costs exactly what the old eager-load-the-
    -whole-file path always cost — this class only helps when callers actually
    ask for a sub-range, which TimePlotWidget's windowed series (rendering) and
    MainWindow.variable_window() (FFT) do (detailed_specification.md 13.5.1
    Phase D). Statistics stream the range in chunks through read_uncached()
    (data ch.53).
    """

    __slots__ = ("_data_source", "_name", "_bounds", "_pyramid", "_pyramid_building", "_pyramid_failures")

    def __init__(self, data_source: ParquetDataSource, name: str):
        self._data_source = data_source
        self._name = name
        self._bounds: tuple[float, float] | None | object = _UNSET
        self._pyramid: ColumnPyramid | None = None
        self._pyramid_building = False
        self._pyramid_failures = 0

    @property
    def name(self) -> str:
        return self._name

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        if row_end <= row_start:
            return np.array([], dtype=np.float64)
        raw = self._data_source.read_window([self._name], row_start, row_end)[self._name]
        return _numeric_column(raw, self._name)

    def read_uncached(self, row_start: int, row_end: int) -> np.ndarray:
        """Like `window`, but read straight from the file without going through
        `read_window`'s block cache (data 53.2): for chunked whole-range scans (stats), which
        must not push the blocks the plots use out of the LRU. Reads only rows
        [row_start, row_end) (clamped by `read_columns`, data 29.5); empty range -> empty array."""
        if row_end <= row_start:
            return np.array([], dtype=np.float64)
        raw = self._data_source.read_columns([self._name], row_start, row_end)[self._name]
        return _numeric_column(raw, self._name)

    def values(self) -> np.ndarray:
        return self.window(0, self._data_source.row_count())

    def bounds(self) -> tuple[float, float] | None:
        """Global (min, max) from Parquet footer statistics alone (Phase B's
        column_bounds()) — instant, no data I/O, unlike values().min()/.max().
        Used for go_home()'s true extent without materializing anything.
        """
        if self._bounds is _UNSET:
            self._bounds = self._data_source.column_bounds(self._name)
        return self._bounds

    @property
    def pyramid(self) -> ColumnPyramid | None:
        """The coarse min/max envelope (Phase B's core/pyramid.py), if built yet
        — see request_pyramid()."""
        return self._pyramid

    def thin_job(self, row_start: int, row_end: int, target_points: int) -> Callable[[], tuple[np.ndarray, np.ndarray]]:
        """Zero-arg background job: ~`target_points` evenly strided (row indices, values) over
        [row_start, row_end), reading one row group at a time (36.6 A). Captures only the
        path/name, never an array (37.3)."""
        from .pyramid import thin_column

        path = self._data_source.path
        name = self._name
        return lambda: thin_column(path, name, target_points, row_start, row_end)

    def request_pyramid(self) -> Callable[[], ColumnPyramid] | None:
        """Call when a caller (TimePlotWidget, at a wide-enough zoom level) wants
        this column's pyramid and it isn't ready yet. Returns a zero-arg job to
        run on a background thread (e.g. via WorkerHub) if a build isn't
        already in flight — the caller must call set_pyramid() with the result
        when that job finishes — or None if a build is already running or
        already done (nothing more to do). Building here rather than eagerly at
        load time: a WindowedColumn the user never actually zooms far out on
        never pays this cost at all (detailed_specification.md 13.5.1 Phase D's
        pyramid-wiring follow-up).
        """
        if self._pyramid is not None or self._pyramid_building:
            return None
        if self._pyramid_failures >= MAX_PYRAMID_FAILURES:
            return None  # gave up for this session (28.2): always use real fetches
        self._pyramid_building = True
        path = self._data_source.path
        name = self._name

        def job() -> ColumnPyramid:
            return build_pyramid(path, [name], DEFAULT_BUCKET_ROWS)[name]

        return job

    def pyramid_failed(self) -> None:
        """The background build started by request_pyramid() failed
        (detailed_specification.md 28.2): clear the in-flight mark so a later
        redraw can retry, and count the failure. After MAX_PYRAMID_FAILURES
        failures request_pyramid() stops offering a job for this session."""
        self._pyramid_building = False
        self._pyramid_failures += 1
        if self._pyramid_failures == MAX_PYRAMID_FAILURES:
            logger.warning("pyramid build for column %r failed %d times; giving up for this session", self._name, self._pyramid_failures)

    @property
    def pyramid_given_up(self) -> bool:
        return self._pyramid_failures >= MAX_PYRAMID_FAILURES

    def set_pyramid(self, pyramid: ColumnPyramid) -> None:
        self._pyramid = pyramid
        self._pyramid_building = False


class _WindowVariables(Mapping[str, np.ndarray]):
    """The variables of one DerivedColumn.window() call: each input read for the rows
    [start, end) on first lookup (and only the inputs the expression actually names)."""

    __slots__ = ("_inputs", "_start", "_end", "_cache")

    def __init__(self, inputs: Mapping[str, Column], start: int, end: int):
        self._inputs = inputs
        self._start = start
        self._end = end
        self._cache: dict[str, np.ndarray] = {}

    def __getitem__(self, key: str) -> np.ndarray:
        if key not in self._cache:
            self._cache[key] = self._inputs[key].window(self._start, self._end)
        return self._cache[key]

    def __contains__(self, key: object) -> bool:
        return key in self._inputs

    def __iter__(self) -> Iterator[str]:
        return iter(self._inputs)

    def __len__(self) -> int:
        return len(self._inputs)


class DerivedColumn:
    """A derived variable held as expression + input columns instead of a resident array
    (detailed_specification.md 36.6 C): `window(lo, hi)` computes only that row range, reading
    the inputs for the range widened by the expression's margin (`delta` needs one row before,
    `rolling_mean(a, n)` n-1 on each side...; see core/expression.py `_FUNC_MARGINS`). The result
    equals the same rows of the full computation. Only created by `make_derived_column()`, for
    expressions whose margin is known and which have a WindowedColumn (or another
    DerivedColumn) among their inputs.

    `registry` must be an immutable snapshot (a `FunctionRegistry` nobody mutates afterwards):
    `window()` runs on worker threads while the UI thread may swap the live registry's contents
    (MainWindow.update_registry); a registry change builds new DerivedColumns instead.

    Duck-types the parts of WindowedColumn that TimePlotWidget/MainWindow use for a windowed
    series: `window`, `values`, `bounds`, `pyramid`, `request_pyramid`, `pyramid_failed`,
    `set_pyramid`.
    """

    def __init__(self, expression: str, inputs: Mapping[str, Column], n_rows: int, registry, margin: tuple[int, int]):
        self.expression = expression
        self.inputs = dict(inputs)
        self.n_rows = n_rows
        self.registry = registry
        self.margin = margin
        self._bounds: tuple[float, float] | None | object = _UNSET
        self._pyramid: ColumnPyramid | None = None
        self._pyramid_building = False
        self._pyramid_failures = 0
        # One-shot UI-thread listeners keyed by owner (a plot's series, the navigator): told with
        # True when the pyramid lands, False when its build failed, whoever started the build.
        self._pyramid_listeners: dict[object, Callable[[bool], None]] = {}

    def add_pyramid_listener(self, key: object, callback: Callable[[bool], None]) -> None:
        """Ask to be told (once, `callback(ok)`) when the pyramid build in flight completes. A
        second call with the same `key` replaces the first (a repeated redraw must not stack)."""
        self._pyramid_listeners[key] = callback

    def remove_pyramid_listener(self, key: object) -> None:
        self._pyramid_listeners.pop(key, None)

    def _notify_pyramid(self, ok: bool) -> None:
        listeners, self._pyramid_listeners = self._pyramid_listeners, {}
        for callback in listeners.values():
            try:
                callback(ok)
            except Exception:  # noqa: BLE001 (one listener must not starve the others)
                logger.exception("pyramid listener failed")

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        from .expression import ExpressionError, evaluate_expression  # lazy: keep column.py light

        lo, hi = max(0, row_start), min(self.n_rows, row_end)
        if hi <= lo:
            return np.array([], dtype=np.float64)
        start, end = max(0, lo - self.margin[0]), min(self.n_rows, hi + self.margin[1])
        value = evaluate_expression(self.expression, _WindowVariables(self.inputs, start, end), self.registry)
        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim == 0:
            return np.full(hi - lo, float(arr))
        if arr.shape != (end - start,):
            raise ExpressionError(f"bad result shape {arr.shape} for a window of {end - start} rows")
        if start == lo and end == hi:
            return arr
        return arr[lo - start : hi - start].copy()  # don't keep the margin rows alive

    def values(self) -> np.ndarray:
        return self.window(0, self.n_rows)

    def scan(self, chunk_rows: int = 4_000_000) -> Iterator[np.ndarray]:
        for lo in range(0, self.n_rows, chunk_rows):
            yield self.window(lo, min(self.n_rows, lo + chunk_rows))

    def bounds(self) -> tuple[float, float] | None:
        """Global finite (min, max), by one chunked pass over the column the first time (there
        are no footer statistics for a computed column); cached afterwards."""
        if self._bounds is _UNSET:
            lo, hi = np.inf, -np.inf
            for chunk in self.scan():
                fin = chunk[np.isfinite(chunk)]
                if len(fin):
                    lo, hi = min(lo, float(fin.min())), max(hi, float(fin.max()))
            self._bounds = (lo, hi) if lo <= hi else None
        return self._bounds

    @property
    def pyramid(self) -> ColumnPyramid | None:
        return self._pyramid

    def request_pyramid(self) -> Callable[[], ColumnPyramid] | None:
        """Same contract as WindowedColumn.request_pyramid(): a job that streams the column in
        chunks (never resident as a whole) or None if one is running/done/given up on."""
        if self._pyramid is not None or self._pyramid_building:
            return None
        if self._pyramid_failures >= MAX_PYRAMID_FAILURES:
            return None
        self._pyramid_building = True

        def job() -> ColumnPyramid:
            return build_pyramid_from_windows(self.window, self.n_rows)

        return job

    def pyramid_failed(self) -> None:
        self._pyramid_building = False
        self._pyramid_failures += 1
        self._notify_pyramid(False)

    @property
    def pyramid_given_up(self) -> bool:
        return self._pyramid_failures >= MAX_PYRAMID_FAILURES

    def set_pyramid(self, pyramid: ColumnPyramid) -> None:
        self._pyramid = pyramid
        self._pyramid_building = False
        self._notify_pyramid(True)


def make_derived_column(
    expression: str, columns: Mapping[str, Column], n_rows: int, registry, probe_rows: int = 1000
) -> DerivedColumn | None:
    """A DerivedColumn for `expression` over `columns`, or None when the expression must be
    computed in full (the caller then does what it always did): no input is a WindowedColumn /
    DerivedColumn, a name is unknown, or something in it has no known margin (a builtin without a
    `_FUNC_MARGINS` entry, a rolling window that is not a plain number...). Evaluates a first
    window right away (`probe_rows` rows) so a bad expression raises here, before anything is
    committed — the same all-or-nothing point as the full evaluation had.
    """
    from .expression import ExpressionError, expression_margin, referenced_names

    try:
        names = referenced_names(expression)
    except ExpressionError:
        return None
    if n_rows <= 0 or not names or any(n not in columns for n in names):
        return None
    if not any(isinstance(columns[n], (WindowedColumn, DerivedColumn)) for n in names):
        return None
    name_margins = {n: (columns[n].margin if isinstance(columns[n], DerivedColumn) else (0, 0)) for n in names}
    margin = expression_margin(expression, registry, name_margins)
    if margin is None:
        return None
    col = DerivedColumn(expression, {n: columns[n] for n in names}, n_rows, registry, margin)
    col.window(0, min(n_rows, probe_rows))
    return col


class LazyVariables(Mapping[str, np.ndarray]):
    """A Mapping[str, ndarray] view over a dict[str, Column] that materializes a
    column only when it's actually looked up — handed to
    core.expression.evaluate_expression() so an expression like `a + b` only
    ever pulls in the specific columns it references (a, b), never the other
    loaded-but-unrelated columns. Works with no changes to expression.py: its
    evaluator only ever does `name in variables` / `variables[name]` on the
    mapping it's given, never iterates every value.
    """

    __slots__ = ("_columns",)

    def __init__(self, columns: dict[str, Column]):
        self._columns = columns

    def __getitem__(self, key: str) -> np.ndarray:
        try:
            return self._columns[key].values()
        except NonNumericColumnError as e:
            # an expression naming a string/list column: the evaluator's own error type, so
            # callers that only catch ExpressionError (view load, derived-variable edit) cope
            raise ExpressionError(str(e)) from e

    def __contains__(self, key: object) -> bool:
        return key in self._columns

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)
