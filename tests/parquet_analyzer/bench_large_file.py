"""Baseline time/memory measurement on a ~100M-row Parquet file (detailed_specification.md 41).

NOT a pytest test (the name does not match ``test_*.py``): a standalone measurement script.
It measures only; it changes nothing in the app. Results are recorded in
docs/domains/data.ja.md 36.4.

For each (file, eager_load_limit_mb) pair, a child process drives an offscreen ``MainWindow``
through the stages below and prints one JSON line per stage. The parent enforces a per-stage
time limit: when a stage does not report in time, the child is killed, the stage is recorded as
"timeout", and a fresh child runs the remaining stages (re-doing any prerequisite stages
silently). So a hang never stops the whole run.

Stages: load (open file) / plot (one column, until background jobs finish) / home1..3
(go_home) / zoom10, pan10, zoom1000, pan1000 (visible range 1/10, 1/1000 of the data; pan =
3 steps) / stats_zoom (stats of the visible range, at 1/1000) / home_back / stats_full (stats of
the whole range) / derived (create ``temperature + pressure``) / derived_plot (plot it) /
time_switch, time_switch_back (use another column as the time axis and return).
Scenario stages (36.6; each runs in its OWN child process so they do not disturb each other or
the main chain, and their RSS is a clean per-scenario figure): plot3 (plot temperature, pressure,
rpm), derived2_plot (create two derived variables, plot both), burst20 (plot, then 20 rapid
zoom/pan steps 15 ms apart: wall = first step until the last draw settles), open_to_first_draw
(open file -> first draw settled, total).

"Jobs discarded as stale" (burst20 and every stage's ``jobs`` field): the child wraps
``TimePlotWidget._start_series_worker`` (jobs submitted) and ``_on_downsampled`` (jobs delivered;
a delivery whose per-series ``generation`` no longer matches the entry's, or whose series is
gone, is counted as stale = its result was thrown away). Failure deliveries are not counted.

Per stage it records: wall seconds, ru_maxrss (process peak so far, MB), RSS before/after
(MB, psutil if importable, else ``ps``). ru_maxrss is cumulative, so read it as "peak up to
the end of this stage".

Usage (from the repo root):
    # generate the data (sized from the generator's ~46 bytes/row; record the actual rows)
    uv run python tests/parquet_analyzer/generate_test_parquet.py \\
        --output data/raw/bench_100m.parquet --target-size 4620MB
    uv run python tests/parquet_analyzer/generate_test_parquet.py \\
        --output data/raw/bench_10m.parquet --target-size 462MB
    # measure (both eager_load_limit_mb settings: default 512 and 0), JSON + Markdown table
    uv run --with psutil python tests/parquet_analyzer/bench_large_file.py \\
        data/raw/bench_10m.parquet data/raw/bench_100m.parquet \\
        --eager default --eager 0 --json bench.json --markdown bench.md
More options: ``--features "big_a_thin_first_draw=on,..."`` (child's PARQUET_ANALYZER_FEATURES;
unknown names are passed through, the app warns and ignores them; recorded in the JSON),
``--repeat N`` (N runs per config, recorded separately), ``--label NAME`` (config label for
--compare, default: the features string or "baseline").
Compare: ``... --compare base.json a.json b.json`` prints a side-by-side Markdown table
(time s / RSS end MB / peak MB per stage per config; repeats -> median).
Options: ``--stage-timeout S`` (default 180), ``--load-timeout S`` (default 600),
``--stages a,b,c`` (subset). Uses a temporary PARQUET_ANALYZER_ROOT, so the real
config/log are untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STAGES = [
    "load", "plot", "home1", "home2", "home3",
    "zoom10", "pan10", "zoom1000", "pan1000", "stats_zoom",
    "home_back", "stats_full", "derived", "derived_plot",
    "time_switch", "time_switch_back",
    "plot3", "derived2_plot", "burst20", "open_to_first_draw",
]
# Stages run in their own child (clean process each), not chained with the main stages.
ISOLATED = ["plot3", "derived2_plot", "burst20", "open_to_first_draw"]
BURST_STEPS = 20
BURST_INTERVAL_S = 0.015
# Stages that must have run (unrecorded when replayed after a killed child) before a stage.
NEEDS = {
    "plot": ["load"],
    "home1": ["load", "plot"], "home2": ["load", "plot"], "home3": ["load", "plot"],
    "zoom10": ["load", "plot"], "pan10": ["load", "plot", "zoom10"],
    "zoom1000": ["load", "plot"], "pan1000": ["load", "plot", "zoom1000"],
    "stats_zoom": ["load", "plot", "zoom1000"],
    "home_back": ["load", "plot"], "stats_full": ["load", "plot", "home_back"],
    "derived": ["load"], "derived_plot": ["load", "plot", "derived"],
    "time_switch": ["load", "plot"], "time_switch_back": ["load", "plot", "time_switch"],
    "plot3": ["load"], "derived2_plot": ["load"], "burst20": ["load", "plot"],
    "open_to_first_draw": [],
}
JOBS = {"submitted": 0, "delivered": 0, "stale": 0}  # child only; see module docstring
MB = 1024 * 1024


# --------------------------------------------------------------------------- child

def _rss_mb(pid: int | None = None) -> float | None:
    pid = pid or os.getpid()
    try:
        import psutil

        return psutil.Process(pid).memory_info().rss / MB
    except ImportError:
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) / 1024
    except Exception:  # noqa: BLE001
        return None


def _maxrss_mb() -> float:
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / MB if sys.platform == "darwin" else v / 1024  # bytes on macOS, KiB on Linux


class _Runner:
    def __init__(self, path: str, eager: int | None):
        from PySide6.QtWidgets import QApplication

        from parquet_analyzer import __main__ as app_main
        from parquet_analyzer.ui import messages
        from parquet_analyzer.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        app_main._start_gc_guard()  # same GC policy as the real app (37.2)
        for name in ("information", "warning", "critical"):
            setattr(messages, name, lambda *a, **k: None)  # a modal dialog would hang the run
        self._instrument_jobs()
        self.win = MainWindow()
        self.win.settings.eager_load_limit_mb = self.win.settings.eager_load_limit_mb if eager is None else eager
        self.win.show()
        self.path = path
        self.stats_done = 0
        self.win._show_stats_dialog = self._on_stats_dialog
        self.base = None  # (lo, hi) of the time axis
        self.t_lo = self.t_hi = 0.0

    @staticmethod
    def _instrument_jobs() -> None:
        """Count series jobs submitted / delivered / delivered-but-stale (see module docstring)."""
        from parquet_analyzer.ui.plot_widget import TimePlotWidget

        orig_start, orig_done = TimePlotWidget._start_series_worker, TimePlotWidget._on_downsampled

        def start(self, name, entry, job):
            JOBS["submitted"] += 1
            return orig_start(self, name, entry, job)

        def done(self, name, generation, *rest):
            JOBS["delivered"] += 1
            entry = self._series.get(name)
            if entry is None or generation != entry["generation"]:
                JOBS["stale"] += 1
            return orig_done(self, name, generation, *rest)

        TimePlotWidget._start_series_worker, TimePlotWidget._on_downsampled = start, done

    def _on_stats_dialog(self, batch) -> None:
        self.stats_done += 1

    def settle(self, limit: float = 1e9) -> None:
        """Process events until no background job is in flight (first draw done)."""
        win = self.win
        deadline = time.monotonic() + limit
        quiet = 0
        while time.monotonic() < deadline:
            self.app.processEvents()
            if win._is_settling():
                quiet = 0
                time.sleep(0.002)
            else:
                quiet += 1
                if quiet >= 5:
                    return
                time.sleep(0.002)

    def plot0(self):
        return self.win.plot_grid.plots[0]

    def set_range(self, lo: float, hi: float) -> None:
        self.win.plot_grid.set_x_range(lo, hi)
        self.settle()

    def run(self, stage: str) -> None:
        win = self.win
        if stage == "load":
            assert win.load_parquet(self.path)
            tv = win._time_values
            self.t_lo, self.t_hi = float(tv[0]), float(tv[-1])
            self.settle()
        elif stage == "plot":
            win._add_variable_to_plot(self.plot0(), "temperature")
            self.settle()
        elif stage in ("home1", "home2", "home3", "home_back"):
            win.plot_grid.go_home()
            self.settle()
        elif stage in ("zoom10", "zoom1000"):
            div = 10 if stage == "zoom10" else 1000
            span = (self.t_hi - self.t_lo) / div
            mid = (self.t_lo + self.t_hi) / 2
            self._span = span
            self.set_range(mid - span / 2, mid + span / 2)
        elif stage in ("pan10", "pan1000"):
            div = 10 if stage == "pan10" else 1000
            span = (self.t_hi - self.t_lo) / div
            lo = (self.t_lo + self.t_hi) / 2 - span / 2
            for _ in range(3):
                lo += span * 0.5
                self.set_range(lo, lo + span)
        elif stage in ("stats_zoom", "stats_full"):
            before = self.stats_done
            win._on_stats_requested()
            deadline = time.monotonic() + 1e9
            while self.stats_done == before and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.002)
            self.settle()
        elif stage == "derived":
            win._on_add_derived_variable("bench_ab", "temperature + pressure")
        elif stage == "derived_plot":
            win._add_variable_to_plot(self.plot0(), "bench_ab")
            self.settle()
        elif stage == "plot3":
            for col in ("temperature", "pressure", "rpm"):
                win._add_variable_to_plot(self.plot0(), col)
            self.settle()
        elif stage == "derived2_plot":
            win._on_add_derived_variable("bench_ab", "temperature + pressure")
            win._on_add_derived_variable("bench_cd", "rpm - voltage")
            win._add_variable_to_plot(self.plot0(), "bench_ab")
            win._add_variable_to_plot(self.plot0(), "bench_cd")
            self.settle()
        elif stage == "burst20":
            # zoom in/out and pan alternately, BURST_INTERVAL_S apart (wheel-like cadence), no settling
            # in between; then wait for the last draw. Wall = first step -> settled.
            full = self.t_hi - self.t_lo
            mid = (self.t_lo + self.t_hi) / 2
            span = full / 2
            for i in range(BURST_STEPS):
                if i % 4 == 0:
                    span = max(span * 0.5, full / 1000)
                elif i % 4 == 2:
                    span = min(span * 1.6, full)
                mid = min(max(mid + span * 0.25, self.t_lo + span / 2), self.t_hi - span / 2)
                win.plot_grid.set_x_range(mid - span / 2, mid + span / 2)
                end = time.monotonic() + BURST_INTERVAL_S
                while time.monotonic() < end:
                    self.app.processEvents()
                    time.sleep(0.001)
            self.settle()
        elif stage == "open_to_first_draw":
            assert win.load_parquet(self.path)
            win._add_variable_to_plot(self.plot0(), "temperature")
            self.settle()
        elif stage == "time_switch":
            self._orig_time = win._time_column
            win.set_time_column("pressure")
            self.settle()
        elif stage == "time_switch_back":
            win.set_time_column(self._orig_time)
            self.settle()
        else:
            raise ValueError(stage)


def child_main(args) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    runner = _Runner(args.file, None if args.eager == "default" else int(args.eager))
    wanted = args.stages.split(",")
    replay = [s for s in STAGES if s not in wanted and any(s in NEEDS.get(w, []) for w in wanted)]
    # Prerequisites run first (silently), in STAGES order; wanted stages are recorded.
    for stage in [s for s in STAGES if s in wanted or s in replay]:
        silent = stage in replay and stage not in wanted
        rss0 = _rss_mb()
        jobs0 = dict(JOBS)
        t0 = time.perf_counter()
        error = None
        try:
            runner.run(stage)
        except Exception as exc:  # noqa: BLE001 - recorded, run continues
            error = f"{type(exc).__name__}: {exc}"
        wall = time.perf_counter() - t0
        if not silent:
            print("RESULT " + json.dumps({
                "stage": stage, "wall_s": round(wall, 3), "rss_before_mb": rss0, "rss_after_mb": _rss_mb(),
                "maxrss_mb": round(_maxrss_mb(), 1), "status": "error" if error else "ok", "error": error,
                "jobs": {k: JOBS[k] - jobs0[k] for k in JOBS},
            }), flush=True)
    return 0


# --------------------------------------------------------------------------- parent

def run_config(path: str, eager: str, stages: list[str], stage_timeout: float, load_timeout: float, env: dict) -> list[dict]:
    results: dict[str, dict] = {}
    remaining = list(stages)
    while remaining:
        cmd = [sys.executable, __file__, "--child", "--file", path, "--eager", eager, "--stages", ",".join(remaining)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        # Reader thread so that the parent can enforce a deadline on a blocking readline.
        import queue
        import threading

        q: queue.Queue = queue.Queue()

        def _pump(proc=proc, q=q):
            for line in proc.stdout:
                q.put(line)
            q.put(None)

        threading.Thread(target=_pump, daemon=True).start()
        timed_out_stage = None
        peak_rss = None
        first = True
        while remaining:
            stage = remaining[0]
            limit = load_timeout if stage == "load" else stage_timeout
            wait = limit
            if first:  # a fresh child: start-up + silently replayed prerequisites get extra allowance
                wait += 120 + load_timeout * ("load" in NEEDS.get(stage, [])) + stage_timeout * len(NEEDS.get(stage, []))
            try:
                line = q.get(timeout=wait)
            except queue.Empty:
                line = "TIMEOUT"
            if line is None:
                # child exited early: remaining stages never reported
                for s in remaining:
                    results[s] = {"stage": s, "status": "child-exited", "wall_s": None}
                remaining = []
                break
            if line == "TIMEOUT":
                timed_out_stage = stage
                peak_rss = _rss_mb(proc.pid)
                break
            if not line.startswith("RESULT "):
                continue
            first = False
            rec = json.loads(line[7:])
            results[rec["stage"]] = rec
            if rec["stage"] in remaining:
                remaining.remove(rec["stage"])
        if timed_out_stage:
            proc.kill()
            results[timed_out_stage] = {"stage": timed_out_stage, "status": "timeout", "wall_s": None,
                                        "limit_s": limit, "rss_at_kill_mb": peak_rss}
            remaining.remove(timed_out_stage)
        proc.wait()
    return [results[s] for s in stages if s in results]


def file_info(path: str) -> dict:
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    return {"path": str(path), "rows": pf.metadata.num_rows, "row_groups": pf.metadata.num_row_groups,
            "columns": pf.metadata.num_columns, "size_bytes": os.path.getsize(path)}


def machine_info() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0]}
    try:
        info["cpu"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        info["memory_gb"] = round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1024**3, 1)
    except Exception:  # noqa: BLE001
        pass
    return info


def to_markdown(report: dict) -> str:
    lines = [f"Machine: {json.dumps(report['machine'])}", ""]
    for run in report["runs"]:
        fi = run["file"]
        lines.append(f"### {Path(fi['path']).name} ({fi['rows']:,} rows, {fi['size_bytes'] / 1e9:.2f} GB, "
                     f"{fi['row_groups']} row groups) -- eager_load_limit_mb={run['eager']}, "
                     f"features={run.get('features') or '(none)'}, run {run.get('repeat', 1)}")
        lines.append("")
        lines.append("| stage | time (s) | RSS before (MB) | RSS after (MB) | peak RSS so far (MB) | jobs sub/deliv/stale | status |")
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for r in run["stages"]:
            def f(v, nd=0):
                return "-" if v is None else (f"{v:.{nd}f}")
            status = r["status"] if r["status"] != "timeout" else f"TIMEOUT (> {r.get('limit_s', 0):.0f}s)"
            j = r.get("jobs")
            jobs = "-" if not j else f"{j['submitted']}/{j['delivered']}/{j['stale']}"
            lines.append(f"| {r['stage']} | {f(r.get('wall_s'), 2)} | {f(r.get('rss_before_mb'))} | "
                         f"{f(r.get('rss_after_mb'))} | {f(r.get('maxrss_mb'))} | {jobs} | {status} |")
        lines.append("")
    return "\n".join(lines)


def parent_main(args) -> int:
    stages = args.stages.split(",") if args.stages else STAGES
    # main chain in one child; each isolated scenario stage gets its own child (clean process)
    chains = [[s for s in stages if s not in ISOLATED]] + [[s] for s in stages if s in ISOLATED]
    chains = [c for c in chains if c]
    features = args.features or ""
    report = {"machine": machine_info(), "features": features, "label": args.label or features or "baseline",
              "runs": []}
    for path in args.files:
        info = file_info(path)
        for eager in args.eager or ["default", "0"]:
            for rep in range(1, max(1, args.repeat) + 1):
                res = []
                for chain in chains:
                    with tempfile.TemporaryDirectory() as root:
                        env = dict(os.environ, PARQUET_ANALYZER_ROOT=root, QT_QPA_PLATFORM="offscreen")
                        if features:
                            env["PARQUET_ANALYZER_FEATURES"] = features
                        else:
                            env.pop("PARQUET_ANALYZER_FEATURES", None)
                        print(f"== {path} eager={eager} features={features!r} rep={rep} stages={','.join(chain)}", flush=True)
                        res += run_config(path, eager, chain, args.stage_timeout, args.load_timeout, env)
                for r in res:
                    print(f"  {r['stage']:18s} {r.get('wall_s')}  {r['status']}", flush=True)
                report["runs"].append({"file": info, "eager": eager, "repeat": rep, "features": features, "stages": res})
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    md = to_markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(md)
    print(md)
    return 0


def _median(vals: list) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2


def compare(paths: list[str]) -> str:
    """Side-by-side Markdown table of several result JSONs: per (file, eager) group, one row per
    stage, and per config three columns: time (s) / RSS at stage end (MB) / peak RSS so far (MB).
    Repeats of the same config are reduced to their median; when no repeat succeeded the status
    (TIMEOUT, error) is shown instead of a time."""
    reports = []
    for p in paths:
        rep = json.loads(Path(p).read_text())
        reports.append((rep.get("label") or Path(p).stem, rep))
    groups: dict[tuple, dict] = {}  # (file name, eager) -> {config idx: {stage: [records]}}
    info: dict[tuple, dict] = {}
    for ci, (_, rep) in enumerate(reports):
        for run in rep["runs"]:
            key = (Path(run["file"]["path"]).name, run["eager"])
            info.setdefault(key, run["file"])
            per = groups.setdefault(key, {}).setdefault(ci, {})
            for r in run["stages"]:
                per.setdefault(r["stage"], []).append(r)
    labels = [lab for lab, _ in reports]
    out = []
    for (fname, eager), by_cfg in groups.items():
        fi = info[(fname, eager)]
        out.append(f"### {fname} ({fi['rows']:,} rows) -- eager_load_limit_mb={eager}")
        out.append("")
        out.append("Per config: time (s) / RSS end (MB) / peak RSS (MB). " + "; ".join(
            f"{lab}: features={rep.get('features') or '(none)'}" for lab, rep in reports))
        out.append("")
        out.append("| stage |" + "".join(f" {lab} time | {lab} RSS end | {lab} peak |" for lab in labels))
        out.append("|---|" + "---:|---:|---:|" * len(labels))
        stage_order = [s for s in STAGES if any(s in by_cfg.get(ci, {}) for ci in range(len(labels)))]
        for st in stage_order:
            cells = []
            for ci in range(len(labels)):
                recs = by_cfg.get(ci, {}).get(st, [])
                ok = [r for r in recs if r.get("status") == "ok"]
                if ok:
                    cells += [f"{_median([r['wall_s'] for r in ok]):.2f}",
                              f"{_median([r.get('rss_after_mb') for r in ok]) or 0:.0f}",
                              f"{_median([r.get('maxrss_mb') for r in ok]) or 0:.0f}"]
                elif recs:
                    cells += ["TIMEOUT" if recs[0]["status"] == "timeout" else recs[0]["status"], "-", "-"]
                else:
                    cells += ["-", "-", "-"]
            out.append(f"| {st} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*")
    p.add_argument("--eager", action="append", help="eager_load_limit_mb: 'default' or an int (repeatable)")
    p.add_argument("--stages", default="")
    p.add_argument("--stage-timeout", type=float, default=180)
    p.add_argument("--load-timeout", type=float, default=600)
    p.add_argument("--json")
    p.add_argument("--markdown")
    p.add_argument("--features", default="", help="PARQUET_ANALYZER_FEATURES for the child, e.g. 'big_a_thin_first_draw=on'")
    p.add_argument("--repeat", type=int, default=1, help="runs per config (recorded separately)")
    p.add_argument("--label", default="", help="config label used by --compare")
    p.add_argument("--compare", nargs="+", metavar="JSON", help="print a side-by-side table of result JSONs and exit")
    p.add_argument("--child", action="store_true")
    p.add_argument("--file")
    args = p.parse_args()
    if args.compare:
        print(compare(args.compare))
        return 0
    if args.child:
        if not args.eager:
            args.eager = "default"
        else:
            args.eager = args.eager[0]
        return child_main(args)
    return parent_main(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    sys.exit(main())
