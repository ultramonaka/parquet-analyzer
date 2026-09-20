from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterator, Mapping, Protocol

import numpy as np

from .numeric import to_numeric
from .pyramid import ColumnPyramid, DEFAULT_BUCKET_ROWS, build_pyramid

if TYPE_CHECKING:
    from .data_source import ParquetDataSource

_UNSET = object()  # sentinel distinguishing "bounds not looked up yet" from "no bounds exist"


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

    def __init__(self, data_source: "ParquetDataSource", name: str):
        self._data_source = data_source
        self._name = name
        self._array: np.ndarray | None = None

    def values(self) -> np.ndarray:
        if self._array is None:
            raw = self._data_source.read_columns([self._name])[self._name]
            self._array = to_numeric(raw)
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
    MainWindow.variable_window() (FFT/stats) do (detailed_specification.md
    13.5.1 Phase D).
    """

    __slots__ = ("_data_source", "_name", "_bounds", "_pyramid", "_pyramid_building")

    def __init__(self, data_source: "ParquetDataSource", name: str):
        self._data_source = data_source
        self._name = name
        self._bounds: tuple[float, float] | None | object = _UNSET
        self._pyramid: ColumnPyramid | None = None
        self._pyramid_building = False

    @property
    def name(self) -> str:
        return self._name

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        if row_end <= row_start:
            return np.array([], dtype=np.float64)
        raw = self._data_source.read_window([self._name], row_start, row_end)[self._name]
        return to_numeric(raw)

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

    def request_pyramid(self) -> Callable[[], ColumnPyramid] | None:
        """Call when a caller (TimePlotWidget, at a wide-enough zoom level) wants
        this column's pyramid and it isn't ready yet. Returns a zero-arg job to
        run on a background thread (e.g. via BackgroundWorker) if a build isn't
        already in flight — the caller must call set_pyramid() with the result
        when that job finishes — or None if a build is already running or
        already done (nothing more to do). Building here rather than eagerly at
        load time: a WindowedColumn the user never actually zooms far out on
        never pays this cost at all (detailed_specification.md 13.5.1 Phase D's
        pyramid-wiring follow-up).
        """
        if self._pyramid is not None or self._pyramid_building:
            return None
        self._pyramid_building = True
        path = self._data_source.path
        name = self._name

        def job() -> ColumnPyramid:
            return build_pyramid(path, [name], DEFAULT_BUCKET_ROWS)[name]

        return job

    def set_pyramid(self, pyramid: ColumnPyramid) -> None:
        self._pyramid = pyramid
        self._pyramid_building = False


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
        return self._columns[key].values()

    def __contains__(self, key: object) -> bool:
        return key in self._columns

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)
