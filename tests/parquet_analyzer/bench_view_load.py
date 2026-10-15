"""Measurement of loading a view with many derived variables (expressions 55.6).

NOT a pytest test (the name does not match ``test_*.py``): a standalone script that only measures.

Builds a Parquet file (time + 8 float columns), 20 user functions, 2 lookup tables (a curve and a
2-D map) and a view with 40 derived variables of which 8 are plotted, then loads the view in a
child process driving an offscreen ``MainWindow`` and records:

* ``stall_ms``  - the longest time the UI thread did not process events (a 10 ms timer's largest lateness);
* ``loaded_s``  - ``load_view`` call -> every plot drawn (``_busy_sources`` empty);
* ``returned_s`` - how long the ``load_view`` call itself blocked the UI thread;
* ``rss_mb``    - the child's peak resident memory.

Modes: ``eager`` reproduces the behaviour before chapter 55 (every derived variable computed
on the UI thread while the view loads); ``deferred`` is the new behaviour, with the number of
variables computed at the same time given by ``--parallel`` (0 = the application's default).

Usage (repo root)::

    uv run python tests/parquet_analyzer/bench_view_load.py --rows 2000000 20000000
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

N_FUNCS = 20
N_DERIVED = 40
PLOT_EVERY = 5  # derived variables 0, 5, 10, ... are plotted (8 of 40)


def _function_defs() -> list[tuple[str, list[str], str]]:
    kinds = [
        "x * 2 + y",
        "where(x > y, x - y, y - x)",
        "abs(x) + sqrt(abs(y))",
        "rolling_mean(x, 5) + y",
        "x * y - cv(x)",
    ]
    return [(f"f{k}", ["x", "y"], kinds[k % len(kinds)]) for k in range(N_FUNCS)]


def _derived_defs() -> list[tuple[str, str]]:
    out = []
    for i in range(N_DERIVED):
        s, s2 = f"s{i % 8}", f"s{(i + 3) % 8}"
        kind = i % 4
        if kind == 0 or i < 2:
            expr = f"f{i % N_FUNCS}({s}, {s2})"
        elif kind == 1:
            expr = f"cv({s}) + d{i - 1}"
        elif kind == 2:
            expr = f"mp({s}, {s2}) * 2"
        else:
            expr = f"f{(i + 7) % N_FUNCS}(d{i - 2}, {s})"
        out.append((f"d{i}", expr))
    return out


def make_file(path: Path, rows: int) -> None:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(1)
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(rows, dtype="int64") * np.timedelta64(10, "ms")
    cols = {"time": pa.array(t)}
    for k in range(8):
        cols[f"s{k}"] = np.sin(np.arange(rows) * (0.001 * (k + 1))) * 40 + rng.normal(size=rows)
    pq.write_table(pa.table(cols), path, row_group_size=1_000_000)


def child(args) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    root = Path(args.workdir) / "root"
    os.environ["PARQUET_ANALYZER_ROOT"] = str(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from parquet_analyzer.core.functions import UserFunction
    from parquet_analyzer.core.maps import LookupTable
    from parquet_analyzer.io import view as view_io
    from parquet_analyzer.ui import main_window as mw

    app = QApplication([])
    win = mw.MainWindow()
    win.resize(1200, 800)
    win.show()
    reg = win.function_registry
    xs = np.linspace(-120, 120, 200)
    reg.maps["cv"] = LookupTable("cv", "curve", xs, np.sin(xs / 20.0) * 10 + xs * 0.1)
    reg.maps["mp"] = LookupTable("mp", "map", xs[::4], xs[::4], np.add.outer(xs[::4] ** 2, xs[::4] ** 2) * 1e-3)
    for name, params, expr in _function_defs():
        reg.user_functions[name] = UserFunction(name, params, [], expr)

    file_path = Path(args.workdir) / f"bench_{args.rows}.parquet"
    derived = _derived_defs()
    plotted = [n for i, (n, _e) in enumerate(derived) if i % PLOT_EVERY == 0]
    plots = [
        view_io.PlotDef("p1", [view_io.SeriesDef(n, "#cc0000") for n in plotted[:4]]),
        view_io.PlotDef("p2", [view_io.SeriesDef(n, "#0000cc") for n in plotted[4:]]),
    ]
    view_io.save_view(
        view_io.View(
            view_name="bench",
            source=view_io.SourceDef(str(file_path), "absolute"),
            plots=plots,
            derived_variables=[view_io.DerivedVariableDef(n, e) for n, e in derived],
        )
    )
    assert win.load_parquet(str(file_path))
    for _ in range(20):
        app.processEvents()

    if args.mode == "eager":  # the behaviour before chapter 55: everything computed while loading
        original = mw.MainWindow._on_add_derived_variable
        mw.MainWindow._on_add_derived_variable = lambda self, name, expr, *, deferred=False: original(
            self, name, expr, deferred=False
        )
    elif args.parallel:
        mw._VIEW_COMPUTE_PARALLEL = args.parallel

    stalls = {"last": time.perf_counter(), "max": 0.0}
    phase3 = {"s": 0.0}
    real_finish = mw.MainWindow._finish_load_view

    def timed_finish(self, ctx, result):  # the synchronous layout (expressions 55.3, phase 3)
        t = time.perf_counter()
        try:
            return real_finish(self, ctx, result)
        finally:
            phase3["s"] = time.perf_counter() - t

    mw.MainWindow._finish_load_view = timed_finish

    def tick():
        now = time.perf_counter()
        stalls["max"] = max(stalls["max"], now - stalls["last"] - 0.010)
        stalls["last"] = now

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(tick)
    timer.start()
    app.processEvents()
    stalls["last"] = time.perf_counter()

    t0 = time.perf_counter()
    win.load_view("bench")
    returned = time.perf_counter() - t0
    stalls["last_check"] = time.perf_counter()
    stalls["max"] = max(stalls["max"], returned - 0.010)  # the call itself blocked the loop
    while win._view_compute is not None or win._busy_sources or win._is_settling():
        app.processEvents()
        time.sleep(0.002)
    loaded = time.perf_counter() - t0
    for _ in range(10):
        app.processEvents()
    n_series = sum(len(p.series_names()) for p in win.plot_grid.plots)
    computed = sum(
        1 for n in win._derived if getattr(win._raw_variables[n], "is_ready", True)  # eager columns have no flag
    )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
    print(
        "RESULT "
        + json.dumps(
            {
                "rows": args.rows,
                "mode": args.mode,
                "parallel": args.parallel,
                "stall_ms": round(stalls["max"] * 1000, 1),
                "layout_s": round(phase3["s"], 2),
                "returned_s": round(returned, 2),
                "loaded_s": round(loaded, 2),
                "rss_mb": round(rss_mb),
                "series": n_series,
                "computed_vars": computed,
            }
        )
    )
    win.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, nargs="+", default=[2_000_000])
    ap.add_argument("--parallels", type=int, nargs="+", default=[1, 0], help="deferred runs (0 = app default)")
    ap.add_argument("--no-eager", action="store_true", help="skip the run that reproduces the old behaviour")
    ap.add_argument("--workdir", default=None, help="folder for the generated files (default: a temp folder)")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mode", default="deferred", choices=["eager", "deferred"], help=argparse.SUPPRESS)
    ap.add_argument("--parallel", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        args.rows = args.rows[0]
        child(args)
        return
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="bench_view_load_"))
    workdir.mkdir(parents=True, exist_ok=True)
    configs = ([] if args.no_eager else [("eager", 0)]) + [("deferred", p) for p in args.parallels]
    rows_out = []
    for rows in args.rows:
        path = workdir / f"bench_{rows}.parquet"
        if not path.exists():
            make_file(path, rows)
        for mode, parallel in configs:
            cmd = [sys.executable, __file__, "--child", "--rows", str(rows), "--mode", mode,
                   "--parallel", str(parallel), "--workdir", str(workdir)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
            if line is None:
                print(proc.stdout[-2000:], proc.stderr[-2000:])
                raise SystemExit(f"child failed for {rows} {mode} {parallel}")
            rows_out.append(json.loads(line[len("RESULT "):]))
            print(line, flush=True)
    print("\n| rows | mode | parallel | longest UI stall (ms) | load_view call (s) | drawn after (s) | peak RSS (MB) |")
    print("|---|---|---|---|---|---|---|")
    for r in rows_out:
        par = "-" if r["mode"] == "eager" else (r["parallel"] or "default")
        print(
            f"| {r['rows']:,} | {r['mode']} | {par} | {r['stall_ms']} | {r['returned_s']} | {r['loaded_s']} | {r['rss_mb']} |"
        )


if __name__ == "__main__":
    main()
