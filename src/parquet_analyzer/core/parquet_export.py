"""Export the view's signals to a Parquet file (export.ja.md 57.2, requirement 5.51). No Qt.

`build_plan` turns the UI's snapshot (which signals, which rows, the derived expressions, the
function registry) into an immutable `ExportPlan`; `export_parquet` runs it: only the selected
derived variables and the derived variables they (transitively) use are computed, chunk by chunk
(`chunk_rows` rows, one Parquet row group per chunk), each chunk from the raw columns read for the
chunk widened by the expressions' margin (data 36.8 C: the same rule as `DerivedColumn.window`),
so the result equals the whole-array evaluation. Variables whose margin is unknown are computed
over all rows first (`ExportResult.full_computed`).

The output is written to `<path>.part` and moved over `<path>` at the end; a cancel or a failure
removes the `.part` and leaves any file that was at `<path>` untouched.

Writing (57.9.3): the chunks are streamed through polars' `sink_parquet` (multi-core encoding) when
every column keeps its Arrow type, else through pyarrow's `ParquetWriter`. Between chunks a guard
(57.9.6a, `export_resources`) checks free disk and memory. Progress reports are throttled (57.9.7).

The plan is built from the chunk loop's point of view: a later `source_row` column (data 56.4,
sorted display) is one more entry of `_chunk_table`'s column list; it is not implemented yet.
"""

from __future__ import annotations

import bisect
import dataclasses
import json
import math
import os
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from . import export_resources as res
from .data_source import ParquetDataSource
from .expression import (
    ExpressionError,
    evaluate_expression,
    expression_margin,
    referenced_functions,
    referenced_names,
)
from .functions import FunctionRegistry
from .numeric import to_numeric
from .signal_aliases import resolve_aliases
from .variable import dependency_order
from .view_check import Problem, classify_derived, effective_available

if TYPE_CHECKING:  # the view's JSON model lives in io/; core only reads its attributes
    from ..io.view import View

DEFAULT_CHUNK_ROWS = 1_000_000
# 57.9.3: the automatic chunk size keeps one chunk's Arrow table near this many bytes, within these rows
CHUNK_TARGET_BYTES = 256 * 1024**2
MIN_AUTO_CHUNK_ROWS = 65_536
MAX_AUTO_CHUNK_ROWS = 1_000_000
# 57.9.7: the worker reports progress at most this often (seconds); phase changes and the last chunk always
PROGRESS_INTERVAL = 0.1
# 57.9.6a: the available memory is read at most this often (seconds; reading it can cost a process call)
MEMORY_PROBE_INTERVAL = 0.5
_clock = time.monotonic  # replaced by tests
_sleep = time.sleep
METADATA_KEY = b"parquet_analyzer"
DERIVED_DTYPES = ("float64", "float32")  # 57.9.6b
# progress(0, total) markers with total < 0 (no percentage): the phase computing variables over
# all rows (57.3) and the phase searching the saved time range (57.7.4)
PROGRESS_FULL = -1
PROGRESS_RANGE = -2


class ExportError(Exception):
    """The plan cannot be run (unknown variable, target is the source file...)."""


class NoRowsInRange(ExportError):
    """The saved time range contains no rows (57.7.4): a failure, not an empty file."""

    def __init__(self) -> None:
        super().__init__("no rows in the range")


class RangeAxisMissing(ExportError):
    """The view's saved range cannot be used for this file: the range is in the units of the view's own time
    axis, and that column does not exist here (57.8.3). `name` is that column."""

    def __init__(self, name: str) -> None:
        super().__init__(f"the view's time axis for the saved range is not in the file: {name}")
        self.name = name


class DiskFull(ExportError):
    """The free space of the output volume fell below the bytes still to be written plus
    `DISK_MARGIN` (57.9.6a): this file fails and its `.part` goes, so the disk is not filled to the brim."""

    def __init__(self) -> None:
        super().__init__("disk full")


class MemoryLow(ExportError):
    """The available memory stayed below the pause limit for `MEMORY_WAIT_LIMIT` seconds (57.9.6a)."""

    def __init__(self) -> None:
        super().__init__("not enough free memory")


class ExportCancelled(Exception):
    """`cancelled()` became true; the `.part` file is already removed."""


@dataclass(frozen=True)
class ExportColumn:
    """One output column. `source` is the file column for a raw column / the time axis; None for a
    derived variable (its expression is in `ExportPlan.derived`)."""

    name: str
    source: str | None = None


@dataclass(frozen=True)
class ExportPlan:
    """Everything a run needs, as an immutable snapshot taken on the UI thread (57.2)."""

    source_path: str
    row_start: int
    row_end: int
    total_rows: int  # rows of the source file
    time: ExportColumn
    columns: tuple[ExportColumn, ...]  # the selected signals, output order (time column excluded)
    derived: tuple[tuple[str, str], ...]  # (name, expression) to compute, dependency order
    inputs: tuple[tuple[str, str], ...]  # (variable name, file column) of every raw name a derived uses
    registry: FunctionRegistry  # a private snapshot nobody mutates afterwards
    metadata: Mapping[str, object] = field(default_factory=dict)  # view, software, ...
    chunk_rows: int = DEFAULT_CHUNK_ROWS
    # A time range in the screen's numbers (to_numeric) instead of known rows (57.7.4): the run's
    # first phase turns it into row_start / row_end; until then those are the whole file.
    time_range: tuple[float, float] | None = None
    # The type the derived variables are written in (57.9.6b): "float64" or "float32". Time axis and raw
    # columns always keep the file's own types.
    derived_dtype: str = "float64"
    # The file column the saved time range is searched on (57.8.3): the view's own time axis, which is what
    # the range's numbers belong to, even when another column is chosen as the output's first column.
    # None: the output's time column (`time.source`).
    time_range_column: str | None = None

    @property
    def rows(self) -> int:
        return max(0, self.row_end - self.row_start)


@dataclass
class ExportResult:
    path: str
    rows: int
    row_groups: int
    columns: int
    full_computed: tuple[str, ...]
    elapsed: float
    writer: str = "pyarrow"  # "polars" (streaming sink) or "pyarrow" (57.9.3)
    chunk_halvings: int = 0  # times the memory guard halved the chunk (57.9.6a)
    memory_wait: float = 0.0  # seconds spent paused for free memory (57.9.6a)
    float32_overflow: tuple[str, ...] = ()  # derived variables with values too large for float32 (now inf, 57.9.6b)


def build_plan(
    *,
    source_path: str,
    total_rows: int,
    row_range: tuple[int, int],
    time: ExportColumn,
    signals: list[ExportColumn],
    derived_expressions: Mapping[str, str],
    file_columns: Mapping[str, str],
    registry: FunctionRegistry,
    metadata: Mapping[str, object] | None = None,
    chunk_rows: int | None = None,
    derived_dtype: str = "float64",
) -> ExportPlan:
    """The plan for `signals` (raw columns have a `source`, derived variables have none).

    `derived_expressions`: every derived variable of the session; only the selected ones and
    their transitive derived dependencies enter the plan. `chunk_rows` None: sized from the output row
    width (`auto_chunk_rows`, 57.9.3). `file_columns`: variable name -> file
    column for every raw name an expression may use (aliases map to their source). Duplicates and
    the time column among `signals` are dropped. Raises ExportError for a name nothing defines.
    """
    if derived_dtype not in DERIVED_DTYPES:
        raise ExportError(f"unknown type for derived variables: {derived_dtype}")
    seen = {time.name}
    columns: list[ExportColumn] = []
    for col in signals:
        if col.name not in seen:
            seen.add(col.name)
            columns.append(col)
    needed: set[str] = set()
    frontier = [c.name for c in columns if c.source is None]
    while frontier:
        name = frontier.pop()
        if name in needed:
            continue
        if name not in derived_expressions:
            raise ExportError(f"unknown derived variable: {name}")
        needed.add(name)
        try:
            refs = referenced_names(derived_expressions[name])
        except ExpressionError as e:
            raise ExportError(f"{name}: {e}") from e
        frontier.extend(r for r in refs if r in derived_expressions)
    order = dependency_order({n: derived_expressions[n] for n in derived_expressions if n in needed})
    derived = tuple((n, derived_expressions[n]) for n in order)
    inputs: dict[str, str] = {}
    for _name, expr in derived:
        for ref in referenced_names(expr):
            if ref in derived_expressions:
                continue
            if ref not in file_columns:
                raise ExportError(f"unknown variable: {ref}")
            inputs[ref] = file_columns[ref]
    plan = ExportPlan(
        source_path=str(source_path),
        row_start=max(0, row_range[0]),
        row_end=min(total_rows, row_range[1]),
        total_rows=total_rows,
        time=time,
        columns=tuple(columns),
        derived=derived,
        inputs=tuple(inputs.items()),
        registry=registry,
        metadata=dict(metadata or {}),
        chunk_rows=max(1, int(chunk_rows)) if chunk_rows is not None else DEFAULT_CHUNK_ROWS,
        derived_dtype=derived_dtype,
    )
    if chunk_rows is None:
        plan = dataclasses.replace(plan, chunk_rows=auto_chunk_rows(plan))
    return plan


# -- sizes and memory (57.9.3, 57.9.5) ----------------------------------------------------------------


def _type_bytes(t: pa.DataType) -> int:
    try:
        return max(1, t.bit_width // 8)
    except ValueError:  # strings, binary, nested: a rough width
        return 32


def output_row_bytes(plan: ExportPlan) -> int:
    """Uncompressed bytes of one output row: the raw columns in their file types, 8 for each derived
    variable (float64). Without a readable footer every column counts 8."""
    try:
        schema = pq.read_schema(plan.source_path)
    except (OSError, pa.ArrowException):
        schema = None
    total = 0
    derived_bytes = 4 if plan.derived_dtype == "float32" else 8
    for c in (plan.time, *plan.columns):
        if c.source is None:
            total += derived_bytes
        elif schema is not None and c.source in schema.names:
            total += _type_bytes(schema.field(c.source).type)
        else:
            total += 8
    return max(1, total)


def _calc_columns(plan: ExportPlan) -> tuple[int, int]:
    """(float64 arrays alive while a chunk is computed, arrays alive over all rows): the raw columns
    the expressions use plus the chunk-wise derived variables / the full-row variables and their inputs."""
    chunked, full, _margin = split_by_margin(plan)
    expressions = dict(plan.derived)
    files = dict(plan.inputs)
    full_inputs = {files[r] for name in full for r in referenced_names(expressions[name]) if r in files}
    return len(plan.inputs) + len(chunked), len(full) + len(full_inputs)


def auto_chunk_rows(plan: ExportPlan) -> int:
    """Rows per chunk so that one chunk's tables stay near `CHUNK_TARGET_BYTES` (57.9.3): wide outputs
    get smaller chunks, so the memory does not grow with the column count."""
    try:
        per_chunk, _full = _calc_columns(plan)
        width = output_row_bytes(plan) + 8 * per_chunk
    except Exception:  # noqa: BLE001 (an odd expression is reported by the run, not by sizing)
        return DEFAULT_CHUNK_ROWS
    return int(min(MAX_AUTO_CHUNK_ROWS, max(MIN_AUTO_CHUNK_ROWS, CHUNK_TARGET_BYTES // width)))


def estimate_export_memory(plan: ExportPlan) -> int:
    """Bytes one export is expected to use (57.9.5): per chunk the output row (x3: Arrow table, polars
    copy, write buffer) plus the float64 working arrays, plus the arrays held over all rows, plus 200 MB."""
    per_chunk, full_cols = _calc_columns(plan)
    # a chunk is never larger than the export itself (a 1,000-row export is not charged 1M rows)
    chunk = min(plan.chunk_rows, max(plan.rows, 1)) * (output_row_bytes(plan) * 3 + 8 * per_chunk)
    return chunk + plan.total_rows * 8 * full_cols + 200 * res.MB


def estimate_output_bytes(plan: ExportPlan) -> int:
    """An upper bound of the output size (57.9.6a): rows x uncompressed row width, no credit for compression."""
    return plan.rows * output_row_bytes(plan)


def preflight_item(plan: ExportPlan, path: str | os.PathLike) -> res.PreflightItem:
    return res.PreflightItem(str(path), estimate_output_bytes(plan), estimate_export_memory(plan))


def _shape(value, n: int) -> np.ndarray:
    """1-D float64 of length n (a scalar is broadcast), else ExpressionError (same rule as
    DeferredColumn._shape / MainWindow._series_value)."""
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim == 0:
        return np.full(n, float(arr))
    if arr.shape != (n,):
        raise ExpressionError(f"bad result shape {arr.shape} for {n} rows")
    return arr


def split_by_margin(plan: ExportPlan) -> tuple[dict[str, tuple[int, int]], list[str], tuple[int, int]]:
    """(margins of the chunked variables, the variables to compute over all rows, the largest
    margin): a variable whose margin cannot be determined statically, the variables depending on it
    and the variables it uses are computed in full (57.2)."""
    margins: dict[str, tuple[int, int] | None] = {name: (0, 0) for name, _ in plan.inputs}
    for name, expr in plan.derived:  # dependency order: every dependency has its margin already
        margins[name] = expression_margin(expr, plan.registry, margins)
    expressions = dict(plan.derived)
    full: set[str] = {n for n, _ in plan.derived if margins[n] is None}
    stack = list(full)
    while stack:  # a full variable needs the full values of what it uses
        for ref in referenced_names(expressions[stack.pop()]):
            if ref in expressions and ref not in full:
                full.add(ref)
                stack.append(ref)
    chunked = {n: margins[n] for n, _ in plan.derived if n not in full}
    before = max((m[0] for m in chunked.values()), default=0)
    after = max((m[1] for m in chunked.values()), default=0)
    return chunked, [n for n, _ in plan.derived if n in full], (before, after)


def _used_registry_names(plan: ExportPlan) -> set[str]:
    """Every function / map name the derived expressions use, through user function bodies."""
    used: set[str] = set()
    todo = [expr for _, expr in plan.derived]
    while todo:
        for name in referenced_functions(todo.pop()):
            if name in used:
                continue
            used.add(name)
            fn = plan.registry.user_functions.get(name)
            if fn is not None:
                todo.append(fn.expression)
    return used


def _scalar_text(scalar: pa.Scalar) -> str:
    """One value as text. Arrow's own cast keeps the time zone offset and nanoseconds
    (`as_py()` raises for a `timestamp[ns]` with sub-microsecond digits)."""
    try:
        return scalar.cast(pa.string()).as_py() or ""
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return str(scalar.as_py())


def _metadata_json(plan: ExportPlan, start_text: str, end_text: str, created: str) -> bytes:
    used = _used_registry_names(plan)
    functions = {
        n: {"params": list(f.params), "constants": [[c, v] for c, v in f.constants], "expression": f.expression}
        for n, f in plan.registry.user_functions.items()
        if n in used
    }
    maps = {
        n: {
            "kind": getattr(t, "kind", ""),
            "interpolation": getattr(t, "interpolation", ""),
            "extrapolation": getattr(t, "extrapolation", ""),
        }
        for n, t in plan.registry.maps.items()
        if n in used
    }
    info = {
        "source": str(Path(plan.source_path).resolve()),
        "source_name": Path(plan.source_path).name,
        **{k: v for k, v in plan.metadata.items() if v is not None},
        "time_column": plan.time.name,
        "range": {"rows": [plan.row_start, plan.row_end], "start": start_text, "end": end_text},
        "derived": dict(plan.derived),
        "derived_dtype": plan.derived_dtype,
        "functions": functions,
        "maps": maps,
        "created": created,
    }
    return json.dumps(info, ensure_ascii=False).encode("utf-8")


class _Run:
    """The state of one `export_parquet` call."""

    def __init__(
        self,
        plan: ExportPlan,
        cancelled: Callable[[], bool],
        arbiter: res.DiskArbiter | None = None,
        arbiter_key: object = None,
    ):
        self.plan = plan
        self.cancelled = cancelled
        self.arbiter = arbiter
        self.arbiter_key = arbiter_key
        self.lazy = pl.scan_parquet(plan.source_path)
        self.file_schema = pq.read_schema(plan.source_path)
        self.chunked, self.full_names, (self.m_before, self.m_after) = split_by_margin(plan)
        self.expressions = dict(plan.derived)
        self.input_files = dict(plan.inputs)
        self.full_values: dict[str, np.ndarray] = {}
        self.raw_exports = [c for c in (plan.time, *plan.columns) if c.source is not None]
        self.schema: pa.Schema | None = None
        # the chunk size of the write loop; the memory guard may shrink it (57.9.6a)
        self.chunk_rows = plan.chunk_rows
        self.halvings = 0
        self.memory_wait = 0.0
        self._memory_next = 0.0
        self._memory_low = False  # halved once per drop below the line; re-armed above it (57.9.6a)
        self._row_bytes: int | None = None
        self.f32_overflow: set[str] = set()  # derived variables that overflowed float32 (57.9.6b)

    def check(self) -> None:
        if self.cancelled():
            raise ExportCancelled()

    def gate(self, part: Path, done_rows: int = 0) -> None:
        """The guard between two chunks (57.9.6a): fail this file when the free space is below the
        bytes still to be written plus `res.disk_margin()`, shrink the chunk once per drop of the available
        memory (and grow it back when memory is fine again), wait (the screen keeps running) when it
        is very short. `done_rows`: rows of this export already handed to the writer."""
        if self.arbiter is not None:
            if self.arbiter.stop_requested(self.arbiter_key):  # a more advanced file needs the room (57.9.6a (c))
                raise DiskFull()
            if self.plan.rows > 0:
                self.arbiter.update(self.arbiter_key, done_rows / self.plan.rows)
        free = res.disk_free_bytes(part.parent)
        if free is not None and free < self._disk_still_needed(part, done_rows) + res.disk_margin():
            self._disk_short(part, done_rows)
        now = _clock()
        if now < self._memory_next:
            return
        self._memory_next = now + MEMORY_PROBE_INTERVAL
        avail = res.available_memory_bytes()
        if avail is None:
            return
        if avail >= res.MEMORY_SLOW_BELOW:
            self._memory_low = False
            if self.chunk_rows < self.plan.chunk_rows:  # memory is back: grow toward the planned size
                self.chunk_rows = min(self.plan.chunk_rows, self.chunk_rows * 2)
            return
        if not self._memory_low:  # one halving per drop below the line, not one per probe
            self._memory_low = True
            smaller = max(res.MIN_CHUNK_ROWS, self.chunk_rows // 2)
            if smaller < self.chunk_rows:
                self.chunk_rows = smaller
                self.halvings += 1
        if avail >= res.MEMORY_PAUSE_BELOW:
            return
        started = _clock()
        while True:  # paused at the chunk boundary until memory comes back, or fail after the limit
            self.check()
            if _clock() - started >= res.MEMORY_WAIT_LIMIT:
                self.memory_wait += _clock() - started
                raise MemoryLow()
            _sleep(0.1)
            avail = res.available_memory_bytes()
            if avail is None or avail >= res.MEMORY_PAUSE_BELOW:
                break
        self.memory_wait += _clock() - started
        self._memory_next = 0.0

    def _disk_short(self, part: Path, done_rows: int) -> None:
        """The disk is short for this file (57.9.6a (c)). Alone, or the least advanced of the running files:
        fail it. Otherwise a less advanced file stops first, so this one waits (the screen keeps running)
        until the space comes back, it became the least advanced itself, or `DISK_YIELD_WAIT` ran out; the
        least advanced file on the same volume is told to stop (it raises `DiskFull` at its next gate)."""
        if self.arbiter is None or self.arbiter.is_least_advanced(self.arbiter_key):
            raise DiskFull()
        self.arbiter.request_stop_least(self.arbiter_key)
        started = _clock()
        while _clock() - started < res.DISK_YIELD_WAIT:
            self.check()
            _sleep(0.1)
            free = res.disk_free_bytes(part.parent)
            if free is None or free >= self._disk_still_needed(part, done_rows) + res.disk_margin():
                return
            if self.arbiter.is_least_advanced(self.arbiter_key) or self.arbiter.stop_requested(self.arbiter_key):
                break
            self.arbiter.request_stop_least(self.arbiter_key)  # the marked one may have finished; mark the next
        raise DiskFull()

    def _disk_still_needed(self, part: Path, done_rows: int) -> int:
        """Bytes the rest of this file is expected to take: the rows left x the bytes per row seen so
        far in the `.part` (compression included). 0 until something is on disk: the first chunks are
        covered by the margin, so a file the user confirmed in the preflight (57.9.6a) is not stopped
        by the uncompressed upper bound."""
        if done_rows <= 0:
            return 0
        try:
            written = part.stat().st_size
        except OSError:
            return 0
        left = max(0, self.plan.rows - done_rows)
        return int(left * written / done_rows)

    # -- reading ----------------------------------------------------------------------------

    def read_arrow(self, lo: int, hi: int) -> pa.Table:
        """The time axis and the selected raw columns for rows [lo, hi), in their original Arrow
        types, named as in the output."""
        sources = list(dict.fromkeys(c.source for c in self.raw_exports))
        table = self.lazy.select(sources).slice(lo, max(0, hi - lo)).collect().to_arrow()
        arrays = []
        for c in self.raw_exports:
            col = table.column(c.source)
            want = self.file_schema.field(c.source).type if c.source in self.file_schema.names else None
            if want is not None and col.type != want:
                try:
                    col = col.cast(want)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    pass  # keep what polars gave; the first chunk fixes the schema
            arrays.append(col)
        return pa.table(arrays, names=[c.name for c in self.raw_exports])

    def time_text(self, row: int) -> str:
        """The time axis value of one row as text, for the metadata's range."""
        col = self.lazy.select(self.plan.time.source).slice(row, 1).collect().to_arrow().column(0)
        return _scalar_text(col[0]) if len(col) else ""

    def read_numeric_full(self, files: set[str]) -> dict[str, np.ndarray]:
        """All rows of the numeric columns, read in blocks of `chunk_rows` with a cancel check per
        block (57.3: the full-row phase must be stoppable)."""
        n = self.plan.total_rows
        step = self.plan.chunk_rows
        out: dict[str, np.ndarray] = {}
        for c in sorted(files):
            arr = np.empty(n, dtype=np.float64)
            for lo in range(0, n, step):
                self.check()
                hi = min(n, lo + step)
                arr[lo:hi] = self.read_numeric({c}, lo, hi)[c]
            out[c] = arr
        return out

    def read_numeric(self, files: set[str], lo: int, hi: int) -> dict[str, np.ndarray]:
        # one column at a time, like ParquetDataSource.read_columns (a single multi-column collect
        # would rechunk every column at once); the export has its own reader on purpose: it must
        # not touch the app's data source or its block cache
        out: dict[str, np.ndarray] = {}
        for c in sorted(files):
            df = self.lazy.select(c).slice(max(0, lo), max(0, hi - max(0, lo))).collect()
            out[c] = to_numeric(df[c].to_numpy())
        return out

    # -- computing --------------------------------------------------------------------------

    def evaluate(self, expr: str, variables: Mapping[str, np.ndarray], n: int) -> np.ndarray:
        return _shape(evaluate_expression(expr, variables, self.plan.registry), n)

    def compute_full(self) -> None:
        """The variables computed over all rows (57.2); the others chunk by chunk."""
        if not self.full_names:
            return
        n = self.plan.total_rows
        files = {self.input_files[r] for name in self.full_names for r in referenced_names(self.expressions[name])
                 if r in self.input_files}
        numeric = self.read_numeric_full(files)
        variables = {name: numeric[f] for name, f in self.input_files.items() if f in numeric}
        for name in self.full_names:
            self.check()
            variables[name] = self.full_values[name] = self.evaluate(self.expressions[name], variables, n)
        del variables, numeric
        keep = {c.name for c in self.plan.columns if c.source is None}
        for name, expr in self.plan.derived:
            if name not in self.full_names:
                keep.update(r for r in referenced_names(expr) if r in self.full_values)
        for name in [n for n in self.full_values if n not in keep]:
            del self.full_values[name]  # intermediate results nothing later needs

    def compute_chunk(self, lo: int, hi: int) -> dict[str, np.ndarray]:
        """The selected derived variables for rows [lo, hi)."""
        out: dict[str, np.ndarray] = {}
        for name, arr in self.full_values.items():
            out[name] = arr[lo:hi]
        if not self.chunked:
            return out
        ws, we = max(0, lo - self.m_before), min(self.plan.total_rows, hi + self.m_after)
        files = {self.input_files[r] for name in self.chunked for r in referenced_names(self.expressions[name])
                 if r in self.input_files}
        numeric = self.read_numeric(files, ws, we)
        variables: dict[str, np.ndarray] = {name: numeric[f] for name, f in self.input_files.items() if f in numeric}
        for name, arr in self.full_values.items():
            variables[name] = arr[ws:we]
        for name, expr in self.plan.derived:
            if name in self.chunked:
                variables[name] = self.evaluate(expr, variables, we - ws)
        selected = {c.name for c in self.plan.columns if c.source is None}
        for name in selected & set(self.chunked):
            out[name] = variables[name][lo - ws : hi - ws]
        return out

    def _derived_array(self, name: str, values: np.ndarray) -> pa.Array:
        """A derived variable's chunk in the chosen type. float32: NaN / Inf stay; a finite value too large
        for float32 becomes Inf and is remembered for the final warning (57.9.6b)."""
        if self.plan.derived_dtype != "float32":
            return pa.array(values, type=pa.float64())
        with np.errstate(over="ignore"):
            small = values.astype(np.float32)
        if np.any(np.isinf(small) & np.isfinite(values)):
            self.f32_overflow.add(name)
        return pa.array(small, type=pa.float32())

    def chunk_table(self, lo: int, hi: int) -> pa.Table:
        """The output rows [lo, hi): time axis, then the signals in the plan's order. A
        `source_row` column (data 56.4) would be added here."""
        raw = self.read_arrow(lo, hi) if self.raw_exports else None
        derived = self.compute_chunk(lo, hi)
        arrays, names = [], []
        for c in (self.plan.time, *self.plan.columns):
            names.append(c.name)
            arrays.append(raw.column(c.name) if c.source is not None else self._derived_array(c.name, derived[c.name]))
        table = pa.table(arrays, names=names)
        if self.schema is not None:
            table = table.cast(self.schema)
        return table


class _Throttle:
    """Progress reports at most every `PROGRESS_INTERVAL` seconds (57.9.7). Phase markers
    (`total < 0`) and reports with `force` always pass."""

    def __init__(self, sink: Callable[[int, int], None]):
        self.sink = sink
        self.last = -1e9

    def __call__(self, done: int, total: int, force: bool = False) -> None:
        now = _clock()
        if force or total < 0 or now - self.last >= PROGRESS_INTERVAL:
            self.last = now
            self.sink(done, total)


def _polars_writer_usable(schema: pa.Schema) -> bool:
    """True when the streaming polars writer keeps every column's Arrow type (57.9.3): strings come
    back as large_string, so a file with one is written by pyarrow."""
    try:
        from polars.io.plugins import register_io_source  # noqa: F401

        back = pl.from_arrow(schema.empty_table()).to_arrow().schema
    except Exception:  # noqa: BLE001 (any trouble: use the pyarrow writer)
        return False
    return len(back) == len(schema) and all(back.field(i).type == schema.field(i).type for i in range(len(schema)))


def export_parquet(
    plan: ExportPlan,
    path: str | os.PathLike,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    overwrite: bool = False,
    writer: str | None = None,
    arbiter: res.DiskArbiter | None = None,
    arbiter_key: object = None,
) -> ExportResult:
    """Run `plan`, writing `path` (57.2, 57.8.1). An existing `path` is refused (ExportError, nothing
    written) unless `overwrite` is true; the source file is refused either way. `progress(done_rows, total_rows)` is called before the
    first chunk and after each one (at most every 100 ms, 57.9.7); `cancelled()` is polled between
    chunks (-> ExportCancelled). `writer`: None picks polars' streaming sink when it keeps the column
    types, else pyarrow; "pyarrow" forces the latter (57.9.3).
    Whatever happens, no `.part` file is left behind and an existing `path` is only replaced by a
    complete new file."""
    t0 = time.perf_counter()
    path = Path(path)
    throttled = _Throttle(progress or (lambda done, total: None))
    cancelled = cancelled or (lambda: False)
    try:
        if path.resolve() == Path(plan.source_path).resolve() or (
            path.exists() and os.path.samefile(path, plan.source_path)
        ):
            raise ExportError("the output file is the source file")
    except OSError:
        pass
    if not overwrite and path.exists():  # 57.8.1: only the UI's confirmed path may replace a file
        raise ExportError(f"the output file already exists: {path}")
    if plan.time_range is not None:  # 57.7.4: the first phase, before anything is written
        throttled(0, PROGRESS_RANGE)
        if cancelled():
            raise ExportCancelled()
        start, end = find_row_range(
            plan.source_path, plan.time_range_column or plan.time.source, *plan.time_range,
            chunk_rows=plan.chunk_rows, cancelled=cancelled,
        )
        if end <= start:
            raise NoRowsInRange()
        plan = dataclasses.replace(plan, row_start=start, row_end=end, time_range=None)
    run = _Run(plan, cancelled, arbiter, arbiter_key)
    if arbiter is not None:
        arbiter.register(arbiter_key, res._volume_key(path.resolve().parent))
    total = plan.rows
    part = path.with_name(path.name + ".part")
    row_groups = 0
    used = "pyarrow"
    try:
        run.check()
        if run.full_names:
            throttled(0, PROGRESS_FULL)  # total < 0: the full-row phase, no percentage (57.3)
        run.compute_full()
        run.check()
        # The first chunk (or an empty slice) fixes the schema; the range's edge times are
        # read before the writer exists because the metadata is part of the schema.
        first_hi = min(plan.row_end, plan.row_start + run.chunk_rows)
        first = run.chunk_table(plan.row_start, first_hi)
        start_text = run.time_text(plan.row_start) if total else ""
        end_text = run.time_text(plan.row_end - 1) if total else ""
        created = datetime.now().astimezone().isoformat(timespec="seconds")
        meta = {METADATA_KEY: _metadata_json(plan, start_text, end_text, created)}
        run.schema = first.schema.with_metadata(meta)
        first = first.cast(run.schema)
        throttled(0, total, force=True)
        if total and writer != "pyarrow" and _polars_writer_usable(run.schema):
            used = "polars"
            row_groups = _write_polars(run, plan, part, first, total, throttled)
        else:
            row_groups = _write_pyarrow(run, plan, part, first, total, throttled)
        run.check()  # a cancel during the last chunk must not publish the file
        os.replace(part, path)
    except BaseException:
        _remove_part(part)
        raise
    finally:
        if arbiter is not None:
            arbiter.unregister(arbiter_key)
    return ExportResult(
        path=str(path),
        rows=total,
        row_groups=row_groups,
        columns=1 + len(plan.columns),
        full_computed=tuple(run.full_names),
        elapsed=time.perf_counter() - t0,
        writer=used,
        chunk_halvings=run.halvings,
        memory_wait=run.memory_wait,
        float32_overflow=tuple(n for n in (c.name for c in plan.columns) if n in run.f32_overflow),
    )


def _remove_part(part: Path) -> None:
    """Delete the `.part` once. A polars sink is always allowed to end by itself (see `_write_polars`),
    so its writer thread has closed the file by now and nothing can recreate it later."""
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass


def _chunks(run: _Run, plan: ExportPlan, part: Path, first: pa.Table, total: int, progress: _Throttle):
    """The output chunks in order, as Arrow tables; checks cancel and the resource guard between
    chunks and reports progress as it hands each one over (57.9.3)."""
    done = plan.row_start
    table = first
    while True:
        done += table.num_rows
        run.check()  # before reporting: a cancel must not be followed by a progress report
        progress(done - plan.row_start, total, force=done >= plan.row_end)
        yield table
        if done >= plan.row_end:
            return
        run.check()
        run.gate(part, done - plan.row_start)
        table = run.chunk_table(done, min(plan.row_end, done + run.chunk_rows))


def _write_pyarrow(run: _Run, plan: ExportPlan, part: Path, first: pa.Table, total: int, progress: _Throttle) -> int:
    """The chunks through `ParquetWriter` (one row group per chunk). Dictionary encoding only for
    columns that are not floating point: it costs time and saves nothing there (57.9.1)."""
    schema = run.schema
    assert schema is not None
    dictionary = [f.name for f in schema if not pa.types.is_floating(f.type)]
    w = pq.ParquetWriter(str(part), schema, compression="zstd", use_dictionary=dictionary)
    groups = 0
    try:
        if total:  # nothing to write for 0 rows: the guard must not fail an empty file
            run.gate(part)
        for table in _chunks(run, plan, part, first, total, progress):
            if table.num_rows or total == 0:
                w.write_table(table, row_group_size=max(1, table.num_rows))
                groups += 1 if table.num_rows else 0
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001 (the original error matters more)
            pass
    return groups


def _write_polars(run: _Run, plan: ExportPlan, part: Path, first: pa.Table, total: int, progress: _Throttle) -> int:
    """The chunks through polars' streaming `sink_parquet` (57.9.3): encoding and compression of the
    columns run on several cores. The generator runs on a polars thread and never touches the
    screen. On a cancel / failure inside it the generator stores the exception and ends normally, so
    polars closes its own file; the stored exception is raised afterwards (the caller removes the
    `.part` once). Raising inside the generator instead lets polars' writer thread create the `.part`
    up to ~34 ms after the error came back, which no fixed wait can cover."""
    from polars.io.plugins import register_io_source

    schema = run.schema
    assert schema is not None
    failure: list[BaseException] = []

    def io_source(with_columns, predicate, n_rows, batch_size):  # noqa: ARG001 (polars' call signature)
        try:
            for table in _chunks(run, plan, part, first, total, progress):
                df = pl.from_arrow(table)
                yield df if with_columns is None else df.select(with_columns)
        except Exception as e:  # noqa: BLE001 (kept to be raised as itself)
            failure.append(e)  # and end normally: the sink finishes and closes its file

    # a failure before the first chunk must not start a polars writer at all
    run.check()
    run.gate(part)
    lazy = register_io_source(io_source=io_source, schema=pl.from_arrow(first).schema)
    lazy.sink_parquet(
        str(part), compression="zstd", row_group_size=plan.chunk_rows, statistics=True, arrow_schema=schema
    )
    if failure:
        raise failure[0]
    return pq.read_metadata(str(part)).num_row_groups


# -- a saved view as the source (export 57.7) ---------------------------------------------------


def find_row_range(
    path: str | os.PathLike,
    column: str,
    x_lo: float,
    x_hi: float,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """The rows `[start, end)` from the first row whose `column` value is >= `x_lo` to the last row
    whose value is <= `x_hi`, with the same numbers the screen uses (`to_numeric`) on an ascending
    axis (data 29). The search is by position, so a NaN / null row inside the range stays in it and
    one before it does not shift the window. Row-group statistics narrow it to the two boundary row
    groups, the only data read; with missing statistics, row groups that are not ordered, or a
    boundary group that is not ascending (non-finite rows aside), the axis is streamed in blocks of
    `chunk_rows` (memory = one block). `(0, 0)` when nothing is in range (57.7.4)."""
    cancelled = cancelled or (lambda: False)
    if not (x_lo <= x_hi):  # reversed or NaN bound
        return 0, 0
    ds = ParquetDataSource(path)
    fast = _row_range_by_groups(ds, column, x_lo, x_hi)
    if fast is not None:
        return fast
    first = last = None
    total = ds.row_count()
    step = max(1, int(chunk_rows))
    for lo in range(0, total, step):
        if cancelled():
            raise ExportCancelled()
        values = to_numeric(ds.read_columns([column], lo, min(total, lo + step))[column])
        finite = np.isfinite(values)
        if first is None:
            ge = finite & (values >= x_lo)
            if ge.any():
                first = lo + int(np.argmax(ge))
        le = finite & (values <= x_hi)
        if le.any():
            last = lo + len(values) - 1 - int(np.argmax(le[::-1]))
    if first is None or last is None or last < first:
        return 0, 0
    return first, last + 1


def _finite_ascending(values: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """(positions, values) of the finite entries when they ascend and there is at least one, else None."""
    pos = np.flatnonzero(np.isfinite(values))
    if pos.size == 0:
        return None
    finite = values[pos]
    return (pos, finite) if bool(np.all(np.diff(finite) >= 0)) else None


def _footer_minmax(ds: ParquetDataSource, column: str) -> list[tuple[float | None, float | None, int]]:
    """Per-row-group (min, max, nulls) on the screen's numeric scale. A timestamp column is read from
    the raw int64 statistics: pyarrow cannot turn a nanosecond statistic that is not whole
    microseconds into a datetime, which would make the group look "unknown" (57.7.4)."""
    schema = pq.read_schema(ds.path)
    if column not in schema.names or not pa.types.is_timestamp(schema.field(column).type):
        return ds.column_stats(column)
    unit = schema.field(column).type.unit
    meta = pq.read_metadata(ds.path)
    index = schema.get_field_index(column)
    out: list[tuple[float | None, float | None, int]] = []
    for g in range(meta.num_row_groups):
        st = meta.row_group(g).column(index).statistics
        if st is None or not st.has_min_max:
            out.append((None, None, 0))
            continue
        raw = np.array([st.min_raw, st.max_raw], dtype=np.int64).view(f"datetime64[{unit}]")
        lo, hi = (float(v) for v in to_numeric(raw))
        out.append((lo, hi, st.null_count or 0))
    return out


def _row_range_by_groups(ds: ParquetDataSource, column: str, x_lo: float, x_hi: float) -> tuple[int, int] | None:
    """The statistics path of `find_row_range`; None when it cannot be trusted."""
    try:
        groups = ds.row_groups()
        stats = _footer_minmax(ds, column)
    except (KeyError, OSError, ValueError, TypeError, AttributeError):
        return None
    if not groups or len(stats) != len(groups):
        return None
    mins, maxs = [], []
    prev_max = -math.inf
    for mn, mx, _nulls in stats:
        if mn is None or mx is None or not (math.isfinite(mn) and math.isfinite(mx)) or mn > mx or mn < prev_max:
            return None  # unknown or unordered groups: the statistics cannot narrow anything
        mins.append(mn)
        maxs.append(mx)
        prev_max = mx
    first = bisect.bisect_left(maxs, x_lo)  # first group that reaches x_lo
    last = bisect.bisect_right(mins, x_hi) - 1  # last group that starts at or before x_hi
    if first >= len(groups) or last < first:
        return 0, 0
    off, n = groups[first]
    head = _finite_ascending(to_numeric(ds.read_columns([column], off, off + n)[column]))
    if head is None:
        return None
    k = int(np.searchsorted(head[1], x_lo, side="left"))
    if k >= head[1].size:
        return None
    start = off + int(head[0][k])
    off, n = groups[last]
    tail = head if last == first else _finite_ascending(to_numeric(ds.read_columns([column], off, off + n)[column]))
    if tail is None:
        return None
    k = int(np.searchsorted(tail[1], x_hi, side="right"))
    if k == 0:
        return None
    end = off + int(tail[0][k - 1]) + 1
    return (start, end) if end > start else (0, 0)


@dataclass(frozen=True)
class ViewItem:
    """One row of the export dialog's list for a saved view (57.7.3). `source` is the file column of
    a raw signal (None for a derived variable); `problem` is why it cannot be exported (None =
    usable); `checked` is the default selection (plotted signals yes, other derived variables no)."""

    name: str
    kind: str  # "plot" | "derived"
    source: str | None
    problem: Problem | None
    checked: bool

    @property
    def usable(self) -> bool:
        return self.problem is None


@dataclass(frozen=True)
class ViewExport:
    """Everything the UI needs to show for (view, file): the resolved time axis, the aliases that
    apply, the item list and the saved range."""

    time_name: str | None
    time_source: str | None
    time_problem: Problem | None
    alias_sources: Mapping[str, str]
    items: tuple[ViewItem, ...]
    expressions: Mapping[str, str]  # the usable derived variables of the view
    saved_range: tuple[float, float] | None
    available: frozenset[str]  # names a formula may use: file columns and aliases minus the time axis
    range_name: str | None = None  # the view's own time axis (the saved range's units), whatever column is output first
    range_source: str | None = None  # its file column; None when this file lacks it


def view_export_info(
    view: View,
    file_columns: Sequence[str],
    aliases: Mapping[str, Sequence[str]],
    registry: FunctionRegistry,
    *,
    alias_names: Collection[str] | None = None,
    time_column: str | None = None,
) -> ViewExport:
    """Judge a saved view against a file's columns (57.7.2, 57.7.3) with the one rule shared with
    `load_view` and the view manager (`view_check.classify_derived`). `aliases`: the aliases that
    attach to files (empty while the global switch is off); `alias_names`: every alias of the
    dictionary (a derived variable named like one is skipped by load_view); default = `aliases`."""
    alias_sources = resolve_aliases(file_columns, aliases)
    names = set(file_columns) | set(alias_sources)
    # 57.8.3: a column chosen in the dialog replaces the view's time axis (it is looked up like any other name)
    time_name = time_column or view.time_column or (file_columns[0] if file_columns else None)
    time_problem = None
    time_source = None
    if time_name is not None and time_name in names:
        time_source = alias_sources.get(time_name, time_name)
    else:
        time_problem = Problem("time", time_name or "")
    available = effective_available(names, alias_sources, time_name)
    exprs = {d.name: d.expression for d in view.derived_variables}
    problems = classify_derived(
        exprs, available, alias_names=set(aliases if alias_names is None else alias_names), registry=registry,
        reserved={time_name} if time_name else (),
    )
    axis = names - available
    items: list[ViewItem] = []
    listed: set[str] = set()
    for plot in view.plots:
        for series in plot.series:
            name = series.variable
            if name in listed or (name in axis and name not in exprs):  # the raw time axis itself
                continue  # the time axis is always written; it is not a list entry
            listed.add(name)
            if name in exprs and problems.get(name) is None:
                items.append(ViewItem(name, "plot", None, None, True))
            elif name in available:  # a raw column (also what load_view plots when the derived one is skipped)
                items.append(ViewItem(name, "plot", alias_sources.get(name, name), None, True))
            elif name in exprs:
                items.append(ViewItem(name, "plot", None, problems[name], True))
            else:
                items.append(ViewItem(name, "plot", None, Problem("missing_column", name), True))
    for d in view.derived_variables:
        if d.name not in listed:
            listed.add(d.name)
            items.append(ViewItem(d.name, "derived", None, problems.get(d.name), False))
    usable_exprs = {n: e for n, e in exprs.items() if problems.get(n) is None}
    range_name = view.time_column or (file_columns[0] if file_columns else None)
    range_source = alias_sources.get(range_name, range_name) if range_name is not None and range_name in names else None
    return ViewExport(
        time_name, time_source, time_problem, alias_sources, tuple(items), usable_exprs, view.x_axis_range,
        frozenset(available), range_name, range_source,
    )


def plan_from_view(
    view: View,
    info: ViewExport,
    *,
    source_path: str,
    total_rows: int,
    selected: Sequence[str],
    use_saved_range: bool,
    view_file: str,
    software: str,
    registry: FunctionRegistry,
    chunk_rows: int | None = None,
    derived_dtype: str = "float64",
) -> ExportPlan:
    """The plan for exporting `selected` items of a saved view without opening it (57.7.5). Pure:
    nothing of the app's open file or window is used. Output names stay the view's names (aliases
    included); the content is the source column. Raises ExportError for an unusable selection."""
    if info.time_problem is not None or info.time_name is None or info.time_source is None:
        raise ExportError(f"time axis not found: {info.time_name or ''}")
    by_name = {i.name: i for i in info.items}
    signals: list[ExportColumn] = []
    for name in selected:
        item = by_name.get(name)
        if item is None or not item.usable:
            raise ExportError(f"cannot export: {name}")
        signals.append(ExportColumn(name, item.source))
    if use_saved_range and info.saved_range is None:
        raise ExportError("the view has no saved range")
    if use_saved_range and info.range_source is None:  # 57.8.3: the range's units belong to the view's own axis
        raise RangeAxisMissing(info.range_name or "")
    file_columns = {n: info.alias_sources.get(n, n) for n in info.available}
    plan = build_plan(
        source_path=source_path,
        total_rows=total_rows,
        row_range=(0, total_rows),
        time=ExportColumn(info.time_name, info.time_source),
        signals=signals,
        derived_expressions=info.expressions,
        file_columns=file_columns,
        registry=registry,
        metadata={"view": view.view_name, "view_file": view_file, "software": software},
        chunk_rows=chunk_rows,
        derived_dtype=derived_dtype,
    )
    used = {info.time_name, *(c.name for c in plan.columns), *(n for n, _ in plan.inputs)}
    aliases = {n: info.alias_sources[n] for n in sorted(used) if n in info.alias_sources}
    metadata = dict(plan.metadata)
    if aliases:
        metadata["aliases"] = aliases
    return dataclasses.replace(
        plan, metadata=metadata, time_range=info.saved_range if use_saved_range else None,
        time_range_column=info.range_source if use_saved_range else None,
    )


def time_candidates(schema: pa.Schema) -> list[str]:
    """The columns that can serve as the output's time axis (57.8.3): numeric or temporal types, in file
    order. Booleans, strings and nested types cannot."""
    out = []
    for field_ in schema:
        t = field_.type
        if pa.types.is_boolean(t):
            continue
        if (
            pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_timestamp(t) or pa.types.is_date(t)
            or pa.types.is_time(t) or pa.types.is_duration(t) or pa.types.is_decimal(t)
        ):
            out.append(field_.name)
    return out


def format_time_value(x: float, arrow_type: pa.DataType | None) -> str:
    """A time-axis number (the screen's scale, epoch seconds for datetimes) as text in the file's own
    representation: the same form as the metadata's `range.start` / `end` (`_scalar_text`, e.g.
    `2026-01-01 09:00:10.000000+0900`, the time zone offset kept; naive stays naive), so the dialog and
    the written file name a range the same way (57.7.4). Numbers: `%g`."""
    if arrow_type is not None and (pa.types.is_timestamp(arrow_type) or pa.types.is_date(arrow_type)):
        tz = arrow_type.tz if pa.types.is_timestamp(arrow_type) else None
        try:
            return _scalar_text(pa.scalar(int(round(x * 1e6)), type=pa.timestamp("us", tz=tz)))
        except (OverflowError, ValueError, pa.ArrowInvalid):
            pass
    return f"{x:g}"
