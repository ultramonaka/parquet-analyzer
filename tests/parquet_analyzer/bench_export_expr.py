"""Parquet export speed of derived variables: lookup maps against if-style expressions (export 57.9).

NOT a pytest test (the name does not match ``test_*.py``): a standalone script that only measures.
Results are recorded in the docs by hand.

Every variant is ONE derived variable ``out`` (element-wise, so the export stays chunked), exported
alone with the time column through ``core.parquet_export.export_parquet`` (stage A: polars streaming
writer). ``raw`` exports the raw ``x`` column and is the baseline; ``arith`` (``x*0.5 + y``) is the
cheapest derived variable. The others use ``if`` / ``where`` / ``and`` / ``or``, curves and 2-D maps
(small to 513x513 grids, linear and nearest) so the cost of a lookup table on a 4 GB-class file shows.

The map axes come from the data: the first ``--sample-rows`` rows give min / max (and the median
used as a threshold) of x, y, z; the tables cover that range with evenly spaced axes.

Per variant it records wall seconds, process CPU seconds (cores = cpu / wall), peak RSS (MB;
``ru_maxrss`` is the peak of the WHOLE process so far, so it only ever grows across variants), rows/s,
output size, the writer, ``chunk_halvings`` and ``memory_wait``. The output is deleted after each
variant (also on an error or Ctrl-C), and a variant is skipped when the volume lacks the room.

Usage (repo root)::

    uv run python tests/parquet_analyzer/bench_export_expr.py --file data/raw/bench_100m.parquet \\
        [--cols temperature,pressure,rpm] [--rows N] [--repeat 1] [--out-dir DIR] \\
        [--variants arith,map_m] [--json out.jsonl]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from parquet_analyzer.core import parquet_export as pe  # noqa: E402
from parquet_analyzer.core.expression import _parse, check_allowed_syntax, evaluate_expression  # noqa: E402
from parquet_analyzer.core.functions import FunctionRegistry  # noqa: E402
from parquet_analyzer.core.maps import LookupTable  # noqa: E402

DEFAULT_COLS = ("temperature", "pressure", "rpm")
VARS = ("x", "y", "z")  # the names the expressions use; mapped to the file columns by --cols
BASELINE = "raw"
REFERENCE = "arith"


@dataclass(frozen=True)
class VariantSpec:
    name: str
    expression: str  # "" for the raw baseline
    tables: tuple[str, ...] = ()  # lookup tables the expression needs


# -- sample statistics and tables ----------------------------------------------------------------------


def _is_numeric(t) -> bool:
    return pa.types.is_integer(t) or pa.types.is_floating(t)


def numeric_columns(path: str | os.PathLike) -> list[str]:
    return [f.name for f in pq.read_schema(path) if _is_numeric(f.type)]


def default_cols(path: str | os.PathLike) -> tuple[str, str, str]:
    """`temperature,pressure,rpm` when the file has them, else the first three numeric columns."""
    names = pq.read_schema(path).names
    if all(c in names for c in DEFAULT_COLS):
        return DEFAULT_COLS
    nums = numeric_columns(path)
    if len(nums) < 3:
        raise SystemExit(f"{path}: needs three numeric columns, found {len(nums)}")
    return tuple(nums[:3])  # type: ignore[return-value]


def sample_stats(path: str | os.PathLike, cols: Sequence[str], n: int = 1_000_000) -> dict:
    """min / max / median of each of `cols` over the first `n` rows (a lazy head, never the whole file)."""
    aggs = []
    for v, c in zip(VARS, cols):
        col = pl.col(c).cast(pl.Float64).filter(pl.col(c).cast(pl.Float64).is_finite())
        aggs += [col.min().alias(f"{v}_min"), col.max().alias(f"{v}_max"), col.median().alias(f"{v}_med")]
    row = pl.scan_parquet(str(path)).head(n).select(aggs).collect().row(0, named=True)
    stats: dict = {"cols": list(cols), "rows": n}
    for v in VARS:
        lo, hi, med = row[f"{v}_min"], row[f"{v}_max"], row[f"{v}_med"]
        if lo is None or hi is None:
            raise SystemExit(f"column for {v} has no finite values in the first {n} rows")
        if not hi > lo:  # strictly ascending axes need a span
            hi = lo + 1.0
        stats[v] = {"min": float(lo), "max": float(hi), "med": float(med)}
    return stats


def _surface(gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """A smooth finite z(x, y) of shape (len(gy), len(gx))."""
    sx = (gx - gx[0]) / (gx[-1] - gx[0])
    sy = (gy - gy[0]) / (gy[-1] - gy[0])
    return 10.0 * np.sin(3.0 * sx)[None, :] * np.cos(2.0 * sy)[:, None] + 5.0 * sy[:, None] + sx[None, :]


TABLE_SPECS = {  # name -> (kind, points per axis, interpolation)
    "cv_s": ("curve", 33, "linear"),
    "cv_l": ("curve", 2049, "linear"),
    "mp_s": ("map", 9, "linear"),
    "mp_m": ("map", 65, "linear"),
    "mp_l": ("map", 513, "linear"),
    "mp_nearest": ("map", 65, "nearest"),
}


def build_table(name: str, stats: dict) -> LookupTable:
    kind, n, interp = TABLE_SPECS[name]
    gx = np.linspace(stats["x"]["min"], stats["x"]["max"], n)
    if kind == "curve":
        return LookupTable(name, "curve", gx, 10.0 * np.sin(np.linspace(0.0, 6.0, n)), interpolation=interp)
    gy = np.linspace(stats["y"]["min"], stats["y"]["max"], n)
    return LookupTable(name, "map", gx, gy, _surface(gx, gy), interpolation=interp)


def build_registry(stats: dict, names: Sequence[str] | None = None) -> FunctionRegistry:
    """A registry holding the tables `names` (default: all of TABLE_SPECS)."""
    reg = FunctionRegistry()
    for name in names if names is not None else TABLE_SPECS:
        reg.maps[name] = build_table(name, stats)
    return reg


# -- variants ------------------------------------------------------------------------------------------


def _num(v: float) -> str:
    return repr(float(f"{float(v):.6g}"))


def build_variants(stats: dict) -> dict[str, VariantSpec]:
    """The ordered variant list (add a line to extend it). Thresholds are medians of the sample."""
    mx, my, mz = (_num(stats[v]["med"]) for v in VARS)
    items = [
        VariantSpec("raw", ""),
        VariantSpec("arith", "x*0.5 + y"),
        VariantSpec("if_simple", f"x if y > {my} else -x"),
        VariantSpec("if_nested", f"x if y > {my} else (y if z > {mz} else (-z if x > {mx} else z))"),
        VariantSpec("where_simple", f"where(y > {my}, x, -x)"),
        VariantSpec("where_and", f"where((x > {mx}) and (y < {my}) or (z > {mz}), x, y)"),
        VariantSpec("curve_s", "cv_s(x)", ("cv_s",)),
        VariantSpec("curve_l", "cv_l(x)", ("cv_l",)),
        VariantSpec("map_s", "mp_s(x, y)", ("mp_s",)),
        VariantSpec("map_m", "mp_m(x, y)", ("mp_m",)),
        VariantSpec("map_l", "mp_l(x, y)", ("mp_l",)),
        VariantSpec("map_nearest", "mp_nearest(x, y)", ("mp_nearest",)),
        VariantSpec("map_plus_if", f"mp_m(x, y) if z > {mz} else x", ("mp_m",)),
        VariantSpec("two_maps", "mp_m(x, y) + mp_s(y, z)", ("mp_m", "mp_s")),
    ]
    return {v.name: v for v in items}


def validate_expression(expression: str, registry: FunctionRegistry) -> None:
    """The project's own checks: allowed syntax, then an evaluation on a few rows (raises ExpressionError)."""
    check_allowed_syntax(_parse(expression))
    probe = {v: np.linspace(0.0, 1.0, 5) for v in VARS}
    out = evaluate_expression(expression, probe, registry)
    if np.shape(out) != (5,):
        raise ValueError(f"{expression}: bad shape {np.shape(out)}")


# -- one run -------------------------------------------------------------------------------------------


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024  # bytes on macOS, KB on Linux


def make_plan(path, spec: VariantSpec, cols: Sequence[str], registry: FunctionRegistry, rows: int | None = None):
    meta = pq.read_metadata(path)
    total = meta.num_rows
    time_col = meta.schema.names[0]
    end = total if rows is None else min(total, rows)
    file_columns = dict(zip(VARS, cols))
    if spec.expression:
        signals, derived = [pe.ExportColumn("out")], {"out": spec.expression}
    else:
        signals, derived = [pe.ExportColumn("out", cols[0])], {}
    return pe.build_plan(
        source_path=str(path), total_rows=total, row_range=(0, end), time=pe.ExportColumn(time_col, time_col),
        signals=signals, derived_expressions=derived, file_columns=file_columns, registry=registry,
    )


def run_variant(
    path,
    spec: VariantSpec,
    cols: Sequence[str],
    registry: FunctionRegistry,
    out_dir: str | os.PathLike,
    rows: int | None = None,
    on_output: Callable[[Path], None] | None = None,
) -> dict:
    """Export one variant once; returns the result record (`error` set on a failure or a skip).
    The output and its `.part` never survive; `on_output(path)` sees the finished file first."""
    rec: dict = {"variant": spec.name, "expression": spec.expression or "(raw x)", "error": None}
    out = Path(out_dir) / f"bench_export_expr_{spec.name}_{os.getpid()}.parquet"
    part = out.with_name(out.name + ".part")
    try:
        plan = make_plan(path, spec, cols, registry, rows)
        need = pe.estimate_output_bytes(plan)
        free = shutil.disk_usage(out_dir).free
        if free < need:
            rec["error"] = f"skipped: free disk {free / 1e9:.1f} GB < estimate {need / 1e9:.1f} GB"
            return rec
        w0, c0 = time.perf_counter(), time.process_time()
        result = pe.export_parquet(plan, out, overwrite=True)
        wall, cpu = time.perf_counter() - w0, time.process_time() - c0
        rec.update(
            seconds=wall, cpu_s=cpu, cores=cpu / wall if wall > 0 else None, rows=result.rows,
            rows_per_s=result.rows / wall if wall > 0 else None, peak_rss_mb=_peak_rss_mb(),
            out_bytes=out.stat().st_size, writer=result.writer, chunk_halvings=result.chunk_halvings,
            memory_wait=result.memory_wait,
        )
        if on_output is not None:
            on_output(out)
    except Exception as e:  # noqa: BLE001 (a failing variant is a record, the run goes on)
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        for p in (out, part):
            with contextlib.suppress(OSError):
                p.unlink()
    return rec


# -- table ---------------------------------------------------------------------------------------------


def with_ratios(results: Sequence[dict]) -> list[dict]:
    """Copies of `results` with `vs_arith` / `vs_raw` (seconds ratio; None when either side failed)."""
    secs = {r["variant"]: r.get("seconds") for r in results if not r.get("error")}
    out = []
    for r in results:
        r = dict(r)
        for key, base in (("vs_arith", REFERENCE), ("vs_raw", BASELINE)):
            s, b = (None if r.get("error") else r.get("seconds")), secs.get(base)
            r[key] = s / b if s is not None and b else None
        out.append(r)
    return out


def format_table(results: Sequence[dict]) -> str:
    head = ["variant", "expression", "seconds", "Mrows/s", "cores", "RSS MB", "vs arith", "vs raw"]

    def f(v, fmt):
        return "-" if v is None else format(v, fmt)

    rows = []
    for r in with_ratios(results):
        if r.get("error"):
            rows.append([r["variant"], r["expression"], "ERR", "-", "-", "-", "-", "-"])
            continue
        rows.append([
            r["variant"], r["expression"], f(r["seconds"], ".2f"), f(r["rows_per_s"] and r["rows_per_s"] / 1e6, ".2f"),
            f(r["cores"], ".1f"), f(r["peak_rss_mb"], ".0f"), f(r["vs_arith"], ".2f"), f(r["vs_raw"], ".2f"),
        ])
    widths = [max(len(str(x[i])) for x in [head, *rows]) for i in range(len(head))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip() for row in [head, *rows]]
    errs = [f"{r['variant']}: {r['error']}" for r in results if r.get("error")]
    return "\n".join(lines + (["", *errs] if errs else []))


# -- main ----------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", required=True)
    ap.add_argument("--cols", default=None, help="x,y,z file columns (default: temperature,pressure,rpm or the first three numeric)")
    ap.add_argument("--rows", type=int, default=None, help="export only the first N rows (default: all)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per variant; the fastest is reported")
    ap.add_argument("--out-dir", default=None, help="folder for the temporary outputs (default: system temp)")
    ap.add_argument("--variants", default=None, help="comma-separated names (default: all)")
    ap.add_argument("--json", default=None, help="append one JSON line per variant to this file")
    ap.add_argument("--sample-rows", type=int, default=1_000_000, help="rows read to size the table axes")
    args = ap.parse_args(argv)

    cols = tuple(c.strip() for c in args.cols.split(",")) if args.cols else default_cols(args.file)
    if len(cols) != 3:
        ap.error("--cols needs exactly three column names")
    stats = sample_stats(args.file, cols, args.sample_rows)
    variants = build_variants(stats)
    if args.variants:
        names = [n.strip() for n in args.variants.split(",") if n.strip()]
        unknown = [n for n in names if n not in variants]
        if unknown:
            ap.error(f"unknown variants {unknown}; available: {', '.join(variants)}")
        variants = {n: variants[n] for n in names}
    registry = build_registry(stats)
    own_dir = args.out_dir is None
    out_dir = Path(tempfile.mkdtemp(prefix="bench_export_expr_") if own_dir else args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    try:
        for spec in variants.values():
            runs = [run_variant(args.file, spec, cols, registry, out_dir, args.rows) for _ in range(max(1, args.repeat))]
            ok = [r for r in runs if not r["error"]]
            rec = min(ok, key=lambda r: r["seconds"]) if ok else runs[-1]
            rec = {**rec, "file": str(args.file), "cols": list(cols), "repeat": len(runs)}
            results.append(rec)
            line = json.dumps(rec)
            print(line, flush=True)
            if args.json:
                with open(args.json, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
    finally:
        if own_dir:
            shutil.rmtree(out_dir, ignore_errors=True)
    print()
    print(format_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
