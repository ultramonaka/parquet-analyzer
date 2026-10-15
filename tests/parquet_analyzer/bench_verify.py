"""Correctness checks for bench_large_file.py ``--verify`` (data.ja.md 59.1).

NOT a pytest test (see test_bench_verify.py for those). Two layers:

* pure comparison functions: numbers / arrays in, a verdict out (``None`` = equal, else a short
  failure text). They know nothing about the app.
* ``ParquetRef``: the reference route. It reads the file with ``pyarrow.parquet`` directly
  (``read_row_group``) and computes with numpy / ``math.fsum``. It deliberately does not use the
  app's ParquetDataSource, WindowedColumn, TimeIndex, chunked_stats or pyramid builder.

The code that pulls the app's results out of the running window lives in bench_large_file.py.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

BUCKET_ROWS = 8192  # the pyramid / TimeIndex anchor grid (data 36.6 D, 59.2)
N_RANDOM = 20  # random buckets / times per check (59.1)
SEED = 59

# --------------------------------------------------------------------------- pure comparisons


def _same(a: float, b: float) -> bool:
    return (a != a and b != b) or a == b


def compare_stats(app: dict, ref: dict, rel: float = 1e-9) -> str | None:
    """count / nan_count / min / max exact (NaN equals NaN), mean / std within `rel`."""
    for key in ("count", "nan_count"):
        if int(app[key]) != int(ref[key]):
            return f"{key}: app {app[key]} != ref {ref[key]}"
    for key in ("min", "max"):
        if not _same(float(app[key]), float(ref[key])):
            return f"{key}: app {app[key]!r} != ref {ref[key]!r}"
    for key in ("mean", "std"):
        a, r = float(app[key]), float(ref[key])
        if a != a and r != r:
            continue
        if not (abs(a - r) <= rel * max(abs(a), abs(r))):
            return f"{key}: app {a!r} vs ref {r!r} (rel {abs(a - r) / max(abs(r), 1e-300):.3g} > {rel:g})"
    return None


def compare_hist_counts(app_counts, ref_counts, n_finite: int | None = None) -> str | None:
    """Every bin count equal; the total equals the number of finite values when given."""
    a, r = np.asarray(app_counts, dtype=np.float64), np.asarray(ref_counts, dtype=np.float64)
    if a.shape != r.shape:
        return f"bin count: app {a.shape} != ref {r.shape}"
    bad = np.flatnonzero(a != r)
    if len(bad):
        i = int(bad[0])
        return f"{len(bad)} bins differ, first bin {i}: app {a[i]:g} != ref {r[i]:g}"
    if n_finite is not None and int(round(a.sum())) != n_finite:
        return f"sum of counts {a.sum():g} != finite values {n_finite}"
    return None


def check_pyramid_starts(row_starts, bucket_rows: int = BUCKET_ROWS) -> str | None:
    """Bucket starts are exactly 0, bucket_rows, 2*bucket_rows, ... (no gap, on the anchor grid)."""
    rs = np.asarray(row_starts, dtype=np.int64)
    exp = np.arange(len(rs), dtype=np.int64) * bucket_rows
    bad = np.flatnonzero(rs != exp)
    if len(bad):
        return f"bucket {int(bad[0])} starts at {int(rs[bad[0]])}, expected {int(exp[bad[0]])}"
    return None


def compare_pyramid_bounds(mins, maxs, ref_min: float, ref_max: float) -> str | None:
    """The envelope's overall finite min / max equal the column's."""
    mins, maxs = np.asarray(mins, dtype=np.float64), np.asarray(maxs, dtype=np.float64)
    fin_lo, fin_hi = mins[np.isfinite(mins)], maxs[np.isfinite(maxs)]
    lo = float(fin_lo.min()) if len(fin_lo) else math.nan
    hi = float(fin_hi.max()) if len(fin_hi) else math.nan
    if not _same(lo, ref_min) or not _same(hi, ref_max):
        return f"pyramid bounds ({lo!r}, {hi!r}) != column ({ref_min!r}, {ref_max!r})"
    return None


def compare_buckets(indices, app_mins, app_maxs, ref_mins, ref_maxs) -> str | None:
    """Selected buckets: min / max exact, NaN (no finite value) in the same places."""
    for name, a, r in (("min", app_mins, ref_mins), ("max", app_maxs, ref_maxs)):
        a, r = np.asarray(a, dtype=np.float64), np.asarray(r, dtype=np.float64)
        bad = np.flatnonzero(~((a == r) | (np.isnan(a) & np.isnan(r))))
        if len(bad):
            k = int(bad[0])
            return f"bucket {int(indices[k])} {name}: app {a[k]!r} != ref {r[k]!r}"
    return None


def check_bracket(x: float, side: str, row: int, n: int, t_prev: float | None, t_cur: float | None) -> str | None:
    """The row `row` the app returned for time `x` follows the searchsorted convention that
    row_range_for_x / _visible_row_range use: left (range start): t[r-1] < x <= t[r];
    right (range end): t[r-1] <= x < t[r]. `t_prev` is None at r == 0, `t_cur` None at r == n."""
    if not 0 <= row <= n:
        return f"row {row} outside 0..{n} for x={x!r}"
    if t_prev is not None:
        if side == "left" and not t_prev < x:
            return f"left: t[{row - 1}]={t_prev!r} is not < x={x!r}"
        if side == "right" and not t_prev <= x:
            return f"right: t[{row - 1}]={t_prev!r} is not <= x={x!r}"
    if t_cur is not None:
        if side == "left" and not x <= t_cur:
            return f"left: x={x!r} is not <= t[{row}]={t_cur!r}"
        if side == "right" and not x < t_cur:
            return f"right: x={x!r} is not < t[{row}]={t_cur!r}"
    return None


def compare_window(app, ref, rel: float = 0.0, label: str = "window") -> str | None:
    """Window values: NaN (and Inf) in the same places; finite values equal (rel == 0) or within
    `rel` of max(|app|, |ref|) plus `rel` of the window's largest magnitude (so values that cancel
    to ~0 are not judged against themselves)."""
    a, r = np.asarray(app, dtype=np.float64), np.asarray(ref, dtype=np.float64)
    if a.shape != r.shape:
        return f"{label}: length app {a.shape} != ref {r.shape}"
    nan_a, nan_r = np.isnan(a), np.isnan(r)
    if not np.array_equal(nan_a, nan_r):
        k = int(np.flatnonzero(nan_a != nan_r)[0])
        return f"{label}: NaN position differs at offset {k} (app {a[k]!r}, ref {r[k]!r})"
    ok = ~nan_a
    a, r = a[ok], r[ok]
    if rel == 0.0:
        bad = np.flatnonzero(a != r)
    else:
        with np.errstate(invalid="ignore"):
            fin = np.isfinite(r)
            scale = float(np.abs(r[fin]).max()) if fin.any() else 0.0
            tol = rel * np.maximum(np.abs(a), np.abs(r)) + rel * scale
            bad = np.flatnonzero(~((a == r) | (np.abs(a - r) <= tol)))
    if len(bad):
        k = int(bad[0])
        pos = int(np.flatnonzero(ok)[k])
        return f"{label}: {len(bad)} values differ, first at offset {pos}: app {a[k]!r} != ref {r[k]!r}"
    return None


# --------------------------------------------------------------------------- reference route


def to_float64(arr: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """Own conversion to the float64 the app plots (spec: float32 -> float64; timestamps ->
    epoch seconds, ns truncated to whole microseconds like the app, NaT -> NaN)."""
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    typ = arr.type
    if pa.types.is_timestamp(typ):
        ints = arr.cast(pa.int64()).to_numpy(zero_copy_only=False)
        null = np.asarray(arr.is_null().to_numpy(zero_copy_only=False))
        if typ.unit == "ns":
            out = (ints // 1000) / 1e6
        else:
            out = ints / {"s": 1.0, "ms": 1e3, "us": 1e6}[typ.unit]
        out = np.asarray(out, dtype=np.float64)
        out[null] = np.nan
        return out
    out = arr.to_numpy(zero_copy_only=False)
    return np.asarray(out, dtype=np.float64)


class ParquetRef:
    """Reads a Parquet file row group by row group with pyarrow and nothing else."""

    def __init__(self, path: str):
        self.path = str(path)
        self.pf = pq.ParquetFile(self.path)
        md = self.pf.metadata
        self.n_rows = md.num_rows
        self.group_rows = [md.row_group(i).num_rows for i in range(md.num_row_groups)]
        self.group_starts = [int(s) for s in np.concatenate([[0], np.cumsum(self.group_rows)[:-1]])]
        self._stat_cache: dict[str, dict] = {}

    def read_rows(self, column: str, lo: int, hi: int) -> np.ndarray:
        """Rows [lo, hi) of one column as float64."""
        lo, hi = max(0, lo), min(self.n_rows, hi)
        parts: list[np.ndarray] = []
        for g, (gs, gn) in enumerate(zip(self.group_starts, self.group_rows)):
            if gs + gn <= lo or gs >= hi:
                continue
            col = self.pf.read_row_group(g, columns=[column]).column(0)
            a, b = max(lo, gs) - gs, min(hi, gs + gn) - gs
            parts.append(to_float64(col.slice(a, b - a)))
        return np.concatenate(parts) if parts else np.array([], dtype=np.float64)

    def iter_groups(self, columns: list[str], lo: int = 0, hi: int | None = None) -> Iterator[dict[str, np.ndarray]]:
        """Row-group-sized pieces of rows [lo, hi): {column: float64 array}."""
        hi = self.n_rows if hi is None else min(hi, self.n_rows)
        for g, (gs, gn) in enumerate(zip(self.group_starts, self.group_rows)):
            if gs + gn <= lo or gs >= hi:
                continue
            tab = self.pf.read_row_group(g, columns=columns)
            a, b = max(lo, gs) - gs, min(hi, gs + gn) - gs
            yield {c: to_float64(tab.column(c).slice(a, b - a)) for c in columns}

    def stats(self, columns: list[str], fn: Callable[[dict[str, np.ndarray]], np.ndarray] | None = None,
              lo: int = 0, hi: int | None = None, cache_key: str | None = None) -> dict:
        """count / nan_count / min / max / mean / std of fn(columns) (default: the one column) over
        rows [lo, hi). Two passes: mean first, then the sum of squared deviations; the partial sums
        of the pieces are added with math.fsum. Population std, like the app's."""
        hi = self.n_rows if hi is None else min(hi, self.n_rows)
        key = f"{cache_key}|{lo}|{hi}" if cache_key else None
        if key and key in self._stat_cache:
            return self._stat_cache[key]
        fn = fn or (lambda d: d[columns[0]])

        def pieces():
            for d in self.iter_groups(columns, lo, hi):
                v = np.asarray(fn(d), dtype=np.float64)
                yield v[np.isfinite(v)], len(v)

        n = nfin = 0
        sums: list[float] = []
        vmin, vmax = math.inf, -math.inf
        for f, k in pieces():
            n += k
            nfin += len(f)
            if len(f):
                sums.append(math.fsum(f.tolist()))
                vmin, vmax = min(vmin, float(f.min())), max(vmax, float(f.max()))
        if nfin == 0:
            out = {"count": n, "nan_count": n, "min": math.nan, "max": math.nan, "mean": math.nan, "std": math.nan}
        else:
            mean = math.fsum(sums) / nfin
            m2 = math.fsum(math.fsum(((f - mean) ** 2).tolist()) for f, _k in pieces() if len(f))
            out = {"count": n, "nan_count": n - nfin, "min": vmin, "max": vmax, "mean": mean,
                   "std": math.sqrt(m2 / nfin)}
        if key:
            self._stat_cache[key] = out
        return out

    def histogram(self, column: str, edges, lo: int = 0, hi: int | None = None) -> tuple[np.ndarray, int]:
        """(np.histogram counts summed over the pieces, number of finite values)."""
        edges = np.asarray(edges, dtype=np.float64)
        counts = np.zeros(len(edges) - 1, dtype=np.int64)
        nfin = 0
        for d in self.iter_groups([column], lo, hi):
            v = d[column]
            v = v[np.isfinite(v)]
            nfin += len(v)
            counts += np.histogram(v, bins=edges)[0]
        return counts, nfin

    def bucket_minmax(self, column: str, indices, bucket_rows: int = BUCKET_ROWS) -> tuple[np.ndarray, np.ndarray]:
        """Finite min / max of the given buckets, read directly (NaN when no finite value)."""
        mins, maxs = [], []
        for k in indices:
            v = self.read_rows(column, int(k) * bucket_rows, (int(k) + 1) * bucket_rows)
            f = v[np.isfinite(v)]
            mins.append(float(f.min()) if len(f) else math.nan)
            maxs.append(float(f.max()) if len(f) else math.nan)
        return np.array(mins), np.array(maxs)

    def random_buckets(self, n_buckets: int, count: int = N_RANDOM) -> np.ndarray:
        """`count` bucket numbers from a fixed seed (always includes the last bucket)."""
        rng = np.random.default_rng(SEED)
        picks = {int(x) for x in rng.integers(0, n_buckets, size=count)}
        picks.add(n_buckets - 1)
        return np.array(sorted(picks), dtype=np.int64)

    def probe_times(self, time_column: str, count: int = N_RANDOM) -> list[float]:
        """Times to look up: first, last, row-group boundaries (first row of up to 10 groups and
        the last row of the group before) and `count` seeded random times inside the range."""
        rows = {0, self.n_rows - 1}
        step = max(1, len(self.group_starts) // 10)
        for gs in self.group_starts[::step]:
            rows.update({gs, max(0, gs - 1)})
        times = [float(self.read_rows(time_column, r, r + 1)[0]) for r in sorted(rows)]
        lo, hi = times[0], times[-1]
        rng = np.random.default_rng(SEED)
        times += [float(x) for x in rng.uniform(lo, hi, size=count)]
        return [t for t in times if math.isfinite(t)]

    def bracket(self, time_column: str, row: int) -> tuple[float | None, float | None]:
        """(t[row-1], t[row]) read directly; None where the row does not exist."""
        win = self.read_rows(time_column, max(0, row - 1), row + 1)
        prev = float(win[0]) if row >= 1 and len(win) else None
        cur_i = 1 if row >= 1 else 0
        cur = float(win[cur_i]) if row < self.n_rows and len(win) > cur_i else None
        return prev, cur
