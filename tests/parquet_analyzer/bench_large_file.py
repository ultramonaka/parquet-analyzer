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
1-billion-row stages (data.ja.md 58.3): hist_full (whole-range histogram of one column, waits for
the background job), derived_full (every row of ``a*b+c`` streamed through ``chunked_stats``),
parquet_export (skipped with a note while MainWindow has no export entry point, ch.57), and
view_load (own child: a saved view with ``--view-derived`` derived variables over ``--view-raw`` raw
columns, ``--view-plotted`` of them plotted (defaults 4 / 10 / 3), loaded into an empty window =
open + apply + first draw settled; 58.5.1). It also waits for the background full computation of
the deferred derived variables (``view_load_full``: time + RSS) and records each derived
variable's column kind at both points (Derived / Deferred / Materialized). The whole-range stats of a raw column is the
existing ``stats_full``. The stage columns are the generate_test_parquet.py names when the file has
them, else the first four numeric columns (generate_wide_parquet.py's ``sig_NN``). Every stage also
records ``cpu_s`` / ``cores`` (process CPU time over wall time, all threads).
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
Disk reads (58.5.1): ``read_bytes`` = the child's ``psutil`` ``io_counters().read_bytes`` delta over
the stage, and ``read_mb_s`` = that / wall seconds. psutil has no ``io_counters`` on macOS, so
there both are None and the Markdown column shows "-" (no estimate is substituted; a read speed
needs the real counter, e.g. on the Linux machine). The counter is the storage reads of the
process, so a stage that is served from the page cache reads ~0 bytes.

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
``--purge`` drops the OS page cache before each measured stage (macOS ``sudo -n purge``; Linux
``sync`` + ``sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches'``; run ``sudo -v`` first); the
purge time is not part of the measurement.
``--verify`` / ``--no-verify`` (default ON; data.ja.md 59.1): after each stage's time / RSS are recorded, the
stage's result is compared with values computed by an independent route (pyarrow + numpy, see
bench_verify.py; the app's data source / TimeIndex / pyramid builder are not used). Per stage the JSON
gets ``verify`` (``ok`` / ``failed: <what>`` / ``skipped: <why>``; absent = nothing to check for that
stage) and ``verify_s``; the Markdown table has a "verify" column. A failure never stops the run, and the
check time is not part of the stage time. ``--verify-timeout S`` (default 3600) bounds one stage's check.
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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_verify as bv  # noqa: E402  (the independent reference route + pure comparisons, 59.1)

STAGES = [
    "load", "plot", "home1", "home2", "home3",
    "zoom10", "pan10", "zoom1000", "pan1000", "stats_zoom",
    "home_back", "stats_full", "derived", "derived_plot",
    "time_switch", "time_switch_back",
    "plot3", "derived2_plot", "burst20", "open_to_first_draw",
    # 58.3 (1-billion-row measurement); all four run in their own child (a clean RSS: the
    # chained time_switch leaves GBs resident), view_load opens the file itself.
    "hist_full", "derived_full", "parquet_export", "view_load",
]
# Stages run in their own child (clean process each), not chained with the main stages.
ISOLATED = ["plot3", "derived2_plot", "burst20", "open_to_first_draw", "hist_full", "derived_full", "parquet_export", "view_load"]
VIEW_NAME = "bench_view"
VIEW_RAW = 4  # defaults of --view-raw / --view-derived / --view-plotted (the stage-1 shape)
VIEW_DERIVED = 10
VIEW_PLOTTED = 3
VIEW_COLORS = ("#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf")
LEGACY_MAX_DERIVED = 10  # the stage-1 expression list has this many entries
BENCH_COLUMNS = ("temperature", "pressure", "rpm", "voltage")  # generate_test_parquet.py
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
    "hist_full": ["load", "plot", "home_back"], "derived_full": ["load"], "parquet_export": ["load"],
    "view_load": [],
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


def _read_bytes() -> int | None:
    """Bytes this process read from storage so far (psutil ``io_counters``; None where unsupported,
    e.g. macOS)."""
    try:
        import psutil

        return int(psutil.Process().io_counters().read_bytes)
    except (ImportError, AttributeError, NotImplementedError, OSError):
        return None


def _maxrss_mb() -> float:
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / MB if sys.platform == "darwin" else v / 1024  # bytes on macOS, KiB on Linux


class Skipped(Exception):
    """A stage that cannot run in this build (recorded as status "skipped" with the reason)."""


def _numeric_columns(path: str) -> tuple[list[str], list[str]]:
    """(all column names, numeric column names in file order)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pq.read_schema(path)
    numeric = [f.name for f in schema if pa.types.is_floating(f.type) or pa.types.is_integer(f.type)]
    return list(schema.names), numeric


def pick_columns(path: str) -> list[str]:
    """Four numeric columns to drive the stages: the generate_test_parquet.py names when present,
    else the first four non-time columns (generate_wide_parquet.py's ``sig_NN``)."""
    names, numeric = _numeric_columns(path)
    if all(n in names for n in BENCH_COLUMNS):
        return list(BENCH_COLUMNS)
    if len(numeric) < 4:
        raise ValueError(f"{path}: need at least 4 numeric columns, found {len(numeric)}")
    return numeric[:4]


def view_raw_columns(path: str, n: int) -> list[str]:
    """The raw columns the view_load view uses (58.5.1): the first ``n`` numeric non-time columns.
    A generate_test_parquet.py file (the four BENCH_COLUMNS) cannot go beyond 4. Raises ValueError
    when the file does not have enough."""
    names, numeric = _numeric_columns(path)
    if all(c in names for c in BENCH_COLUMNS):
        if n > len(BENCH_COLUMNS):
            raise ValueError(f"{path}: the benchmark file has only {len(BENCH_COLUMNS)} signal columns, "
                             f"--view-raw {n} is too many")
        return list(BENCH_COLUMNS[:n])
    if len(numeric) < n:
        raise ValueError(f"{path}: --view-raw {n} needs {n} numeric columns, found {len(numeric)}")
    return numeric[:n]


def _is_default_shape(raw: list[str], n_derived: int) -> bool:
    return len(raw) == 4 and n_derived <= LEGACY_MAX_DERIVED


def build_view_exprs(raw: list[str], n_derived: int) -> list[tuple[str, str]]:
    """The (name, expression) list of the view_load view, deterministic (58.5.1).

    The default shape (4 raw columns, up to 10 derived variables) is exactly the stage-1 list, so
    results stay comparable. Otherwise: the first half is elementwise over the raw columns (every
    raw column used at least once; one ``rolling_mean(<raw>, 100)``), the second half is chained
    over earlier derived variables (``delta(<derived>)``, ``where(<raw> > 0, <derived>, <raw>)``,
    then ``d[k-1] <op> d[k//2]`` / ``d[k-1] * <raw>``, so the chain gets deep). Every expression has
    a known margin, and refers only to a raw column or an earlier derived variable."""
    names = [f"d{i:02d}" for i in range(n_derived)]
    if _is_default_shape(raw, n_derived):
        a, b, c, d = raw
        legacy = [f"{a} * {b} + {c}", f"{a} + {b}", f"{b} - {c}", f"{c} * {d}", f"{a} - {d}",
                  f"{a} + {b} + {c}", f"{b} * {d}", f"{c} + {d}", f"{a} * {c}", f"{b} + {d}"]
        return list(zip(names, legacy[:n_derived]))
    n = len(raw)
    first = (n_derived + 1) // 2
    ops = ("+", "*", "-")
    exprs: list[str] = []
    for i in range(first):
        x = raw[(2 * i) % n]  # two raw columns per expression: n raw columns are all used once 2 * first >= n
        y = raw[(2 * i + 1 + (2 * i) // n) % n]
        if i == min(1, first - 1):
            exprs.append(f"rolling_mean({y}, 100)")
        elif n == 1:
            exprs.append(f"{x} {ops[i % 3]} {i + 1}")
        else:
            exprs.append(f"{x} {ops[i % 3]} {y}")
    for k in range(first, n_derived):
        j = k - first
        prev = names[k - 1]
        if j == 0:
            exprs.append(f"delta({prev})")
        elif j == 1:
            exprs.append(f"where({raw[0]} > 0, {prev}, {raw[-1]})")
        elif j % 3 == 2:
            exprs.append(f"{prev} * {raw[k % n]}")
        else:
            exprs.append(f"{prev} {ops[j % 3]} {names[k // 2]}")
    return list(zip(names, exprs))


def plotted_names(raw: list[str], n_derived: int, n_plotted: int) -> list[str]:
    """Which derived variables the view plots. Default shape: the first ``n_plotted`` (as in
    stage 1). Otherwise half raw-only (first-half) expressions and half chained ones, the chained
    ones being the last (deepest) derived variables."""
    names = [f"d{i:02d}" for i in range(n_derived)]
    k = min(n_plotted, n_derived)
    if _is_default_shape(raw, n_derived):
        return names[:k]
    first = (n_derived + 1) // 2
    n_chain = min(k // 2, n_derived - first)
    n_raw = min(k - n_chain, first)
    n_chain = min(k - n_raw, n_derived - first)
    return names[:n_raw] + names[n_derived - n_chain:]


def prepare_view(path: str, raw: list[str], n_derived: int = VIEW_DERIVED, n_plotted: int = VIEW_PLOTTED) -> None:
    """Write the view for the view_load stage (not timed): ``n_derived`` derived variables over the
    ``raw`` columns, ``n_plotted`` of them plotted. Goes to the child's temporary config root."""
    from parquet_analyzer.io import view as view_io

    derived = [view_io.DerivedVariableDef(name=nm, expression=e) for nm, e in build_view_exprs(raw, n_derived)]
    series = [view_io.SeriesDef(variable=nm, color=VIEW_COLORS[i % len(VIEW_COLORS)])
              for i, nm in enumerate(plotted_names(raw, n_derived, n_plotted))]
    view = view_io.View(
        view_name=VIEW_NAME,
        source=view_io.SourceDef(parquet_path=str(Path(path).resolve()), path_type="absolute"),
        plots=[view_io.PlotDef(plot_id="plot1", series=series)],
        derived_variables=derived,
    )
    view_io.save_view(view)


class PurgeError(RuntimeError):
    """Dropping the page cache failed (typically sudo wants a password)."""


def purge_commands(platform_name: str | None = None) -> list[list[str]]:
    """The commands that drop the OS page cache (58.5.1), run in order. PurgeError on other systems."""
    plat = sys.platform if platform_name is None else platform_name
    if plat == "darwin":
        return [["sudo", "-n", "purge"]]
    if plat.startswith("linux"):
        return [["sync"], ["sudo", "-n", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"]]
    raise PurgeError(f"--purge is not supported on {plat}")


def purge_page_cache(run=subprocess.run, platform_name: str | None = None) -> None:
    """Drop the page cache; sudo may not ask for a password. PurgeError on failure."""
    for cmd in purge_commands(platform_name):
        try:
            res = run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PurgeError(f"{' '.join(cmd)}: {exc}") from exc
        if res.returncode != 0:
            detail = (res.stderr or "").strip()
            raise PurgeError(f"{' '.join(cmd)} failed (exit {res.returncode})" + (f": {detail}" if detail else ""))


PURGE_HINT = ("--purge must drop the page cache without a password prompt: run `sudo -v` first, "
              "or allow purge (macOS) / writing drop_caches (Linux) without a password.")


def column_kind(column) -> str:
    """Kind label of a derived variable's column (58.5.1)."""
    from parquet_analyzer.core.column import DeferredColumn, DerivedColumn, MaterializedColumn

    if isinstance(column, DerivedColumn):
        return "DerivedColumn"
    if isinstance(column, DeferredColumn):
        return "DeferredColumn:ready" if column.is_ready else "DeferredColumn:pending"
    if isinstance(column, MaterializedColumn):
        return "MaterializedColumn"
    return type(column).__name__


def summarize_kinds(by_name: dict[str, str]) -> dict:
    """Counts of a name -> kind map: derived / deferred (of which ready) / materialized / other."""
    kinds = list(by_name.values())
    known = ("DerivedColumn", "DeferredColumn:ready", "DeferredColumn:pending", "MaterializedColumn")
    return {
        "derived": kinds.count("DerivedColumn"),
        "deferred": sum(k.startswith("DeferredColumn") for k in kinds),
        "deferred_ready": kinds.count("DeferredColumn:ready"),
        "materialized": kinds.count("MaterializedColumn"),
        "other": sum(k not in known for k in kinds),
    }


def format_kinds(counts: dict | None) -> str:
    """'Derived 18 / Deferred 2 / Materialized 0' (with '(r ready)' after Deferred when some are computed)."""
    if not counts:
        return "-"
    deferred = f"Deferred {counts['deferred']}" + (f" ({counts['deferred_ready']} ready)" if counts["deferred_ready"] else "")
    text = f"Derived {counts['derived']} / {deferred} / Materialized {counts['materialized']}"
    return text + (f" / Other {counts['other']}" if counts.get("other") else "")


FAULT_ENV = "BENCH_VERIFY_FAULT"  # test hook (59.1): set only by test_bench_verify.py, never by the production path
PYRAMID_ROWS_ENV = "BENCH_VERIFY_PYRAMID_THRESHOLD"  # test hook: draw from a pyramid already below this many visible rows
FAULTS = ("stats_max", "pyramid_bucket", "derived_window")
VERIFY_MARGIN_ROWS = 1000  # rows read around a view_load window (more than the view's expression margins)
VERIFY_WINDOW_ROWS = 10_000
VERIFY_MAX_WINDOW_ROWS = 2_000_000  # cap of the visible-range window check


def install_fault(kind: str) -> None:
    """Deliberately corrupt one app result so a test can see the matching check fail (59.1).
    No-op for an empty / unknown name."""
    if kind == "stats_max":  # the stats worker's max is one too large
        from parquet_analyzer.ui import main_window as mw

        for nm in ("chunked_stats", "basic_stats"):
            def wrap(*a, _o=getattr(mw, nm), **k):
                r = dict(_o(*a, **k))
                r["max"] = r["max"] + 1.0
                return r
            setattr(mw, nm, wrap)
    elif kind == "pyramid_bucket":  # the last bucket's max is one too large
        from parquet_analyzer.core import column as col

        orig = col.build_pyramid

        def build(*a, **k):
            out = orig(*a, **k)
            for pyr in out.values():
                pyr.maxs[-1] += 1.0
            return out
        col.build_pyramid = build
    elif kind == "derived_window":  # one value of every derived window is one too large
        from parquet_analyzer.core import column as col

        orig_w = col.DerivedColumn.window

        def window(self, lo, hi):
            out = orig_w(self, lo, hi)
            if len(out):
                out = out.copy()
                out[len(out) // 2] += 1.0
            return out
        col.DerivedColumn.window = window


class _Runner:
    def __init__(self, path: str, eager: int | None, stage_timeout: float = 180.0):
        from PySide6.QtWidgets import QApplication

        from parquet_analyzer import __main__ as app_main
        from parquet_analyzer.ui import messages
        from parquet_analyzer.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        app_main._start_gc_guard()  # same GC policy as the real app (37.2)
        for name in ("information", "warning", "critical"):
            setattr(messages, name, lambda *a, **k: None)  # a modal dialog would hang the run
        self._instrument_jobs()
        install_fault(os.environ.get(FAULT_ENV, ""))
        if os.environ.get(PYRAMID_ROWS_ENV):  # a small test file would never build a pyramid otherwise
            from parquet_analyzer.ui import plot_widget

            plot_widget._PYRAMID_PREFERRED_ROW_THRESHOLD = int(os.environ[PYRAMID_ROWS_ENV])
        self.win = MainWindow()
        self.win.settings.eager_load_limit_mb = self.win.settings.eager_load_limit_mb if eager is None else eager
        self.win.show()
        self.path = path
        self.stats_done = 0
        self.win._show_stats_dialog = self._on_stats_dialog
        self.base = None  # (lo, hi) of the time axis
        self.t_lo = self.t_hi = 0.0
        self.c = pick_columns(path)
        self.stage_timeout = stage_timeout
        self.view_info: dict | None = None  # filled by the view_load stage (58.5.1)
        self.view_shape: tuple[list[str], int] = (self.c, VIEW_DERIVED)  # (raw columns, n derived); child_main sets it
        self._ref: bv.ParquetRef | None = None
        self.last_stats: tuple[dict, tuple[int, int]] | None = None  # (stats batch, row range) of the last stats stage
        self.hist_result = None  # HistogramResult of the hist_full stage
        self.derived_full_stats: dict | None = None
        self._stats_range = (0, 0)

    def derived_kinds(self) -> dict[str, str]:
        """Column kind of every derived variable now (name -> kind label)."""
        win = self.win
        return {n: column_kind(win._raw_variables.get(n)) for n in win._derived}

    def _full_computation_running(self) -> bool:
        from parquet_analyzer.core.column import DeferredColumn

        win = self.win
        if win._view_compute_active() or win._is_settling():
            return True
        return any(isinstance(c, DeferredColumn) and c._lock.locked() for c in win._raw_variables.values())

    def wait_full_computation(self, limit: float) -> bool:
        """Process events until the background computation of the deferred derived variables is
        done and drawing has settled again; False when `limit` seconds were not enough."""
        deadline = time.monotonic() + limit
        quiet = 0
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self._full_computation_running():
                quiet = 0
            else:
                quiet += 1
                if quiet >= 5:
                    return True
            time.sleep(0.002)
        return False

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
        self.last_stats = (batch, self._stats_range)

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

    # ------------------------------------------------------------------ verify (59.1)

    @property
    def ref(self) -> bv.ParquetRef:
        if self._ref is None:
            self._ref = bv.ParquetRef(self.path)
        return self._ref

    def verify(self, stage: str) -> tuple[str, float] | None:
        """Check the result of `stage` against the independent route. (verdict, seconds), or None
        when the stage has nothing to check. Never raises: a crashing check is a failure."""
        fn = getattr(self, "_verify_" + stage, None)
        if fn is None:
            return None
        t0 = time.perf_counter()
        try:
            problems = fn()
            verdict = "ok" if not problems else "failed: " + "; ".join(problems[:3]) + (
                f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
        except Skipped as exc:
            verdict = f"skipped: {exc}"
        except Exception as exc:  # noqa: BLE001
            verdict = f"failed: check crashed: {type(exc).__name__}: {exc}"
        return verdict, round(time.perf_counter() - t0, 3)

    @staticmethod
    def _add(problems: list[str], what: str, verdict: str | None) -> None:
        if verdict:
            problems.append(f"{what}: {verdict}")

    def _ref_stats(self, name: str, lo: int, hi: int) -> dict | None:
        """Reference stats of a plotted/derived variable the bench knows how to rebuild, else None."""
        names = self.ref.pf.schema_arrow.names
        if name in names:
            return self.ref.stats([name], lo=lo, hi=hi)
        if name == "bench_ab":
            a, b = self.c[:2]
            return self.ref.stats([a, b], lambda d: d[a] + d[b], lo, hi)
        return None

    def _verify_stats(self) -> list[str]:
        if self.last_stats is None:
            raise Skipped("the stats dialog never reported")
        batch, (lo, hi) = self.last_stats
        problems: list[str] = []
        checked = 0
        for name in batch["names"]:
            res = batch["results"].get(name, {})
            if "error" in res:
                problems.append(f"{name}: app reported an error: {res['error']}")
                continue
            ref = self._ref_stats(name, lo, hi)
            if ref is None:
                continue
            checked += 1
            self._add(problems, f"stats {name}", bv.compare_stats(res, ref))
        if not checked and not problems:
            raise Skipped("no variable of the stats request can be rebuilt by the reference route")
        return problems

    def _verify_stats_zoom(self) -> list[str]:
        return self._verify_stats() + self._verify_time_rows()

    def _verify_stats_full(self) -> list[str]:
        return self._verify_stats()

    def _verify_derived_full(self) -> list[str]:
        if self.derived_full_stats is None:
            raise Skipped("the stage produced no stats")
        a, b, c = self.c[:3]
        ref = self.ref.stats([a, b, c], lambda d: d[a] * d[b] + d[c])
        return [m for m in [bv.compare_stats(self.derived_full_stats, ref)] if m]

    def _verify_hist_full(self) -> list[str]:
        res = self.hist_result
        if res is None or not res.variables:
            raise Skipped("the stage produced no histogram")
        var = res.variables[0]
        ref_counts, nfin = self.ref.histogram(self.c[0], res.edges, res.row_start, res.row_end)
        problems: list[str] = []
        self._add(problems, "histogram", bv.compare_hist_counts(var.counts, ref_counts, nfin))
        if int(var.n) != nfin:
            problems.append(f"histogram: app used {var.n} values, finite values are {nfin}")
        return problems

    def _verify_home1(self) -> list[str]:
        col = self.win._raw_variables.get(self.c[0])
        pyr = getattr(col, "pyramid", None)
        if pyr is None:
            raise Skipped("no pyramid (the column is not windowed, or its build had not finished)")
        n = self.ref.n_rows
        n_buckets = -(-n // bv.BUCKET_ROWS)
        problems: list[str] = []
        self._add(problems, "pyramid starts", bv.check_pyramid_starts(pyr.row_starts))
        if len(pyr.row_starts) != n_buckets:
            problems.append(f"pyramid has {len(pyr.row_starts)} buckets for {n} rows")
        full = self.ref.stats([self.c[0]], cache_key=self.c[0])
        self._add(problems, "pyramid bounds", bv.compare_pyramid_bounds(pyr.mins, pyr.maxs, full["min"], full["max"]))
        idx = self.ref.random_buckets(n_buckets)
        idx = idx[idx < len(pyr.row_starts)]
        r_min, r_max = self.ref.bucket_minmax(self.c[0], idx)
        self._add(problems, "pyramid buckets", bv.compare_buckets(idx, pyr.mins[idx], pyr.maxs[idx], r_min, r_max))
        return problems

    def _verify_time_rows(self) -> list[str]:
        """Time -> row: the app's row for the visible range ends and for seeded probe times is
        bracketed by the directly read neighbours (the _visible_row_range convention)."""
        win, ref = self.win, self.ref
        tv, tcol = win._time_values, win._time_column
        n = len(tv)
        items: list[tuple[float, str, int]] = []
        grp = win._group_x_range()
        if grp is not None:
            rs, re = win._visible_row_range()
            items += [(float(grp[1]), "left", rs), (float(grp[2]), "right", re)]
        for x in ref.probe_times(tcol):
            for side in ("left", "right"):
                items.append((x, side, int(tv.searchsorted(x, side=side))))
        problems: list[str] = []
        for x, side, row in items:
            if not np.isfinite(x):
                continue
            prev, cur = ref.bracket(tcol, row)
            self._add(problems, f"time->row x={x!r}", bv.check_bracket(x, side, row, n, prev, cur))
        return problems

    _verify_zoom10 = _verify_time_rows

    def _verify_zoom1000(self) -> list[str]:
        problems = self._verify_time_rows()
        rs, re = self.win._visible_row_range()
        re = min(re, rs + VERIFY_MAX_WINDOW_ROWS)
        col = self.win._raw_variables[self.c[0]]
        self._add(problems, f"window {self.c[0]}", bv.compare_window(col.window(rs, re), self.ref.read_rows(self.c[0], rs, re)))
        tv = self.win._time_values
        self._add(problems, "window time", bv.compare_window(np.asarray(tv[rs:re]), self.ref.read_rows(self.win._time_column, rs, re)))
        return problems

    def _verify_derived_plot(self) -> list[str]:
        rs, re = self.win._visible_row_range()
        re = min(re, rs + VERIFY_MAX_WINDOW_ROWS)
        a, b = self.c[:2]
        d = self.ref.read_rows(a, rs, re) + self.ref.read_rows(b, rs, re)
        app = self.win._raw_variables["bench_ab"].window(rs, re)
        return [m for m in [bv.compare_window(app, d, rel=1e-12, label="window bench_ab")] if m]

    def _verify_parquet_export(self) -> list[str]:
        raise Skipped("the parquet_export stage does not export yet")

    def _verify_view_load(self) -> list[str]:
        """20 derived variables x 3 windows (start / middle / end) of 10k rows: the raw columns are
        read directly with a fixed margin and the chain is evaluated in order (59.1)."""
        from parquet_analyzer.core.expression import evaluate_expression

        raw, n_derived = self.view_shape
        exprs = build_view_exprs(raw, n_derived)
        n = self.ref.n_rows
        w = min(VERIFY_WINDOW_ROWS, n)
        starts = sorted({0, max(0, n // 2 - w // 2), n - w})
        which = sorted({int(i) for i in np.linspace(0, len(exprs) - 1, min(20, len(exprs)))})
        problems: list[str] = []
        for lo in starts:
            hi = lo + w
            a, b = max(0, lo - VERIFY_MARGIN_ROWS), min(n, hi + VERIFY_MARGIN_ROWS)
            env = {c: self.ref.read_rows(c, a, b) for c in raw}
            for k, (name, expr) in enumerate(exprs):  # in chain order; later ones read earlier results
                env[name] = np.broadcast_to(np.asarray(evaluate_expression(expr, env, None), dtype=np.float64), (b - a,))
                if k not in which:
                    continue
                col = self.win._raw_variables.get(name)
                if col is None:
                    problems.append(f"{name}: not in the window's variables")
                    continue
                self._add(problems, f"view {name} rows {lo}-{hi}",
                          bv.compare_window(col.window(lo, hi), env[name][lo - a : hi - a], rel=1e-9, label="window"))
        return problems

    def run(self, stage: str) -> None:
        win = self.win
        if stage == "load":
            assert win.load_parquet(self.path)
            tv = win._time_values
            self.t_lo, self.t_hi = float(tv[0]), float(tv[-1])
            self.settle()
        elif stage == "plot":
            win._add_variable_to_plot(self.plot0(), self.c[0])
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
            self._stats_range = win._visible_row_range()  # the range the request below will use
            win._on_stats_requested()
            deadline = time.monotonic() + 1e9
            while self.stats_done == before and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.002)
            self.settle()
        elif stage == "derived":
            win._on_add_derived_variable("bench_ab", f"{self.c[0]} + {self.c[1]}")
        elif stage == "derived_plot":
            win._add_variable_to_plot(self.plot0(), "bench_ab")
            self.settle()
        elif stage == "plot3":
            for col in self.c[:3]:
                win._add_variable_to_plot(self.plot0(), col)
            self.settle()
        elif stage == "derived2_plot":
            win._on_add_derived_variable("bench_ab", f"{self.c[0]} + {self.c[1]}")
            win._on_add_derived_variable("bench_cd", f"{self.c[2]} - {self.c[3]}")
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
            win._add_variable_to_plot(self.plot0(), self.c[0])
            self.settle()
        elif stage == "hist_full":
            # whole-range histogram of one column (analysis 42); wait for the background job
            from parquet_analyzer.ui.histogram_window import HistogramWindow

            window = HistogramWindow(win, [self.c[0]])
            window.recompute()
            while window._workers:
                self.app.processEvents()
                time.sleep(0.002)
            self.settle()
            ok = window._result is not None
            self.hist_result = window._result
            window.close()
            if not ok:
                raise RuntimeError("histogram produced no result")
        elif stage == "derived_full":
            # every row of a derived variable ``a*b+c``, streamed in windows (chunked_stats = the
            # chapter-53 path), so the full computation is timed without holding the result
            from parquet_analyzer.core.analysis import chunked_stats

            a, b, c = self.c[:3]
            win._on_add_derived_variable("bench_abc", f"{a} * {b} + {c}")
            n = len(win._time_values)
            stats = chunked_stats(win._raw_variables["bench_abc"].window, 0, n)
            self.derived_full_stats = stats
            if stats["count"] != n:
                raise RuntimeError(f"derived_full covered {stats['count']} of {n} rows")
        elif stage == "parquet_export":
            # 57: no MainWindow export entry point yet -> skip cleanly (recorded as "skipped")
            export = next((getattr(win, nm) for nm in ("export_parquet", "export_parquet_dialog") if hasattr(win, nm)), None)
            if export is None:
                raise Skipped("no Parquet export entry point in MainWindow yet (data.ja.md ch.57 not implemented)")
            raise Skipped("export entry point exists but the bench does not drive it yet")
        elif stage == "view_load":
            # open the file via a saved view and wait until drawing settles = "view open" end to
            # end (55); then until the background full computation of the deferred derived
            # variables is done too (58.5.1). The view file is written by prepare_view().
            t0, cpu0 = time.perf_counter(), time.process_time()
            win.load_view(VIEW_NAME)
            self.settle()
            settled = self.derived_kinds()
            self.view_info = {
                "settled_cpu_s": round(time.process_time() - cpu0, 3), "settled_maxrss_mb": round(_maxrss_mb(), 1),
                "kinds_settled": {"by_name": settled, "counts": summarize_kinds(settled)},
                "settled_wall_s": round(time.perf_counter() - t0, 3), "settled_rss_mb": _rss_mb(),
            }
            ok = self.wait_full_computation(self.stage_timeout)
            full = self.derived_kinds()
            self.view_info.update({
                "full_wall_s": round(time.perf_counter() - t0, 3), "full_rss_mb": _rss_mb(),
                "full_maxrss_mb": round(_maxrss_mb(), 1), "full_timed_out": not ok,
                "kinds_full": {"by_name": full, "counts": summarize_kinds(full)},
            })
        elif stage == "time_switch":
            self._orig_time = win._time_column
            win.set_time_column(self.c[1])
            self.settle()
        elif stage == "time_switch_back":
            win.set_time_column(self._orig_time)
            self.settle()
        else:
            raise ValueError(stage)


def child_main(args) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    runner = _Runner(args.file, None if args.eager == "default" else int(args.eager), args.stage_timeout)
    wanted = args.stages.split(",")
    replay = [s for s in STAGES if s not in wanted and any(s in NEEDS.get(w, []) for w in wanted)]
    # Prerequisites run first (silently), in STAGES order; wanted stages are recorded.
    for stage in [s for s in STAGES if s in wanted or s in replay]:
        silent = stage in replay and stage not in wanted
        if stage == "view_load":
            raw_cols = view_raw_columns(args.file, args.view_raw)
            prepare_view(args.file, raw_cols, args.view_derived, args.view_plotted)  # not timed
            runner.view_shape = (raw_cols, args.view_derived)
        purge_error = None
        if args.purge and not silent:
            try:
                purge_page_cache()  # before the stage's counters start: not part of the measurement
            except PurgeError as exc:
                purge_error = f"purge failed: {exc}"
        rss0 = _rss_mb()
        jobs0 = dict(JOBS)
        io0 = _read_bytes()
        cpu0 = time.process_time()  # user + system CPU of all threads of this process
        t0 = time.perf_counter()
        error = None
        status = "ok"
        try:
            if purge_error:
                raise RuntimeError(purge_error)  # a stage that should be cold is not measured warm
            runner.run(stage)
        except Skipped as exc:
            error, status = str(exc), "skipped"
        except Exception as exc:  # noqa: BLE001 - recorded, run continues
            error, status = f"{type(exc).__name__}: {exc}", "error"
        wall = time.perf_counter() - t0
        cpu = time.process_time() - cpu0
        io1 = _read_bytes()
        read_bytes = None if io0 is None or io1 is None else io1 - io0
        if not silent:
            rec = {
                "stage": stage, "wall_s": round(wall, 3), "rss_before_mb": rss0, "rss_after_mb": _rss_mb(),
                "maxrss_mb": round(_maxrss_mb(), 1), "status": status, "error": error,
                "cpu_s": round(cpu, 3), "cores": round(cpu / wall, 2) if wall > 0 else None,
                "jobs": {k: JOBS[k] - jobs0[k] for k in JOBS},
                "read_bytes": read_bytes,
                "read_mb_s": round(read_bytes / MB / wall, 1) if read_bytes is not None and wall > 0 else None,
            }
            info = runner.view_info if stage == "view_load" and status == "ok" else None
            if info:
                # the view_load row is the settled point (as in stage 1); the full computation is its own row
                sw = info["settled_wall_s"]
                rec["full"] = {"wall_s": info["full_wall_s"], "rss_after_mb": info["full_rss_mb"],
                               "maxrss_mb": info["full_maxrss_mb"], "timed_out": info["full_timed_out"]}
                rec.update(wall_s=sw, rss_after_mb=info["settled_rss_mb"], maxrss_mb=info["settled_maxrss_mb"],
                           cpu_s=info["settled_cpu_s"], cores=round(info["settled_cpu_s"] / sw, 2) if sw > 0 else None,
                           read_mb_s=None, kinds_settled=info["kinds_settled"], kinds_full=info["kinds_full"])
            print("RESULT " + json.dumps(rec), flush=True)
            if getattr(args, "verify", False):  # after the stage's numbers are recorded: not part of its time (59.1)
                out = None
                if status == "ok":
                    out = runner.verify(stage)
                elif hasattr(runner, "_verify_" + stage):
                    out = (f"skipped: {error or 'the stage ended with status ' + status}", 0.0)
                v = {"stage": stage, "verify": out[0] if out else None, "verify_s": out[1] if out else None}
                print("VERIFY " + json.dumps(v), flush=True)
    return 0


# --------------------------------------------------------------------------- parent

def run_config(path: str, eager: str, stages: list[str], stage_timeout: float, load_timeout: float, env: dict,
               extra_args: tuple[str, ...] = (), purge: bool = False, verify: bool = False,
               verify_timeout: float = 3600) -> list[dict]:
    results: dict[str, dict] = {}
    remaining = list(stages)
    while remaining:
        cmd = [sys.executable, __file__, "--child", "--file", path, "--eager", eager, "--stages", ",".join(remaining),
               "--stage-timeout", str(stage_timeout), *extra_args]
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
        pending_verify = None  # a RESULT arrived and its VERIFY line (the check ran after it) has not yet
        verify_killed = False
        while remaining or pending_verify:
            if not remaining:  # only the last stage's check is outstanding
                stage, limit = pending_verify, verify_timeout
            else:
                stage = remaining[0]
                limit = load_timeout if stage == "load" else stage_timeout
            # view_load also waits (up to stage_timeout) for the full computation; a purge takes a while
            wait = limit + (stage_timeout if stage == "view_load" else 0) + (120 if purge else 0)
            if pending_verify and remaining:
                wait += verify_timeout  # the previous stage's check runs before this stage reports
            if first:  # a fresh child: start-up + silently replayed prerequisites get extra allowance
                wait += 120 + load_timeout * ("load" in NEEDS.get(stage, [])) + stage_timeout * len(NEEDS.get(stage, []))
            try:
                line = q.get(timeout=wait)
            except queue.Empty:
                line = "TIMEOUT"
            if line == "TIMEOUT" and pending_verify:
                # the check of the previous stage never ended: keep that stage's numbers, drop the check
                results[pending_verify]["verify"] = "skipped: the check timed out"
                pending_verify = None
                verify_killed = True
                break
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
            if line.startswith("VERIFY "):
                v = json.loads(line[7:])
                if v["stage"] in results and v.get("verify") is not None:
                    results[v["stage"]]["verify"], results[v["stage"]]["verify_s"] = v["verify"], v["verify_s"]
                pending_verify = None
                continue
            if not line.startswith("RESULT "):
                continue
            first = False
            rec = json.loads(line[7:])
            results[rec["stage"]] = rec
            pending_verify = rec["stage"] if verify else None
            if rec["stage"] in remaining:
                remaining.remove(rec["stage"])
        if verify_killed:
            proc.kill()
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


def verify_cell(rec: dict) -> str:
    """The "verify" column: verdict (+ check seconds); '-' when the stage has no check or --no-verify."""
    v = rec.get("verify")
    if v is None:
        return "-"
    return str(v).replace("|", "/") + (f" ({rec['verify_s']:.1f}s)" if rec.get("verify_s") is not None else "")


def to_markdown(report: dict) -> str:
    lines = [f"Machine: {json.dumps(report['machine'])}", ""]
    for run in report["runs"]:
        fi = run["file"]
        lines.append(f"### {Path(fi['path']).name} ({fi['rows']:,} rows, {fi['size_bytes'] / 1e9:.2f} GB, "
                     f"{fi['row_groups']} row groups) -- eager_load_limit_mb={run['eager']}, "
                     f"features={run.get('features') or '(none)'}, purge={'on' if run.get('purge') else 'off'}, "
                     f"view={run.get('view') or '-'}, run {run.get('repeat', 1)}")
        lines.append("")
        lines.append("| stage | time (s) | RSS before (MB) | RSS after (MB) | peak RSS so far (MB) | CPU cores | read MB/s | jobs sub/deliv/stale | derived column kinds | status | verify |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
        for r in run["stages"]:
            def f(v, nd=0):
                return "-" if v is None else (f"{v:.{nd}f}")
            status = r["status"] if r["status"] != "timeout" else f"TIMEOUT (> {r.get('limit_s', 0):.0f}s)"
            j = r.get("jobs")
            jobs = "-" if not j else f"{j['submitted']}/{j['delivered']}/{j['stale']}"
            kinds = format_kinds((r.get("kinds_settled") or {}).get("counts"))
            lines.append(f"| {r['stage']} | {f(r.get('wall_s'), 2)} | {f(r.get('rss_before_mb'))} | "
                         f"{f(r.get('rss_after_mb'))} | {f(r.get('maxrss_mb'))} | {f(r.get('cores'), 1)} | "
                         f"{f(r.get('read_mb_s'), 1)} | {jobs} | {kinds} | {status} | {verify_cell(r)} |")
            full = r.get("full")
            if full:  # the point where the background full computation of the deferred variables is done
                done = "ok" if not full.get("timed_out") else "TIMEOUT (full computation not done)"
                lines.append(f"| {r['stage']}_full | {f(full.get('wall_s'), 2)} | - | {f(full.get('rss_after_mb'))} | "
                             f"{f(full.get('maxrss_mb'))} | - | - | - | "
                             f"{format_kinds((r.get('kinds_full') or {}).get('counts'))} | {done} | - |")
        lines.append("")
    return "\n".join(lines)


def parent_main(args) -> int:
    stages = args.stages.split(",") if args.stages else STAGES
    # main chain in one child; each isolated scenario stage gets its own child (clean process)
    chains = [[s for s in stages if s not in ISOLATED]] + [[s] for s in stages if s in ISOLATED]
    chains = [c for c in chains if c]
    features = args.features or ""
    if not (args.view_raw >= 1 and args.view_derived >= 1 and 0 <= args.view_plotted <= args.view_derived):
        print("error: need --view-raw >= 1, --view-derived >= 1 and 0 <= --view-plotted <= --view-derived", file=sys.stderr)
        return 2
    view_shape = f"{args.view_raw} raw / {args.view_derived} derived / {args.view_plotted} plotted"
    if "view_load" in stages:  # stop before starting when a file cannot give that many raw columns
        for path in args.files:
            try:
                view_raw_columns(path, args.view_raw)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    if args.purge:  # try once up front: sudo must work without a password prompt
        try:
            purge_page_cache()
        except PurgeError as exc:
            print(f"error: {exc}\n{PURGE_HINT}", file=sys.stderr)
            return 2
    child_args = ("--view-raw", str(args.view_raw), "--view-derived", str(args.view_derived),
                  "--view-plotted", str(args.view_plotted), *(("--purge",) if args.purge else ()),
                  *(("--verify",) if args.verify else ()))
    report = {"machine": machine_info(), "features": features, "label": args.label or features or "baseline",
              "purge": bool(args.purge), "verify": bool(args.verify),
              "view": view_shape if "view_load" in stages else None, "runs": []}
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
                        res += run_config(path, eager, chain, args.stage_timeout, args.load_timeout, env,
                                          child_args, args.purge, args.verify, args.verify_timeout)
                for r in res:
                    print(f"  {r['stage']:18s} {r.get('wall_s')}  {r['status']}", flush=True)
                report["runs"].append({"file": info, "eager": eager, "repeat": rep, "features": features,
                                       "purge": bool(args.purge), "verify": bool(args.verify), "view": report["view"], "stages": res})
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
    p.add_argument("--view-raw", type=int, default=VIEW_RAW, help="view_load: raw columns the view uses (default 4)")
    p.add_argument("--view-derived", type=int, default=VIEW_DERIVED, help="view_load: derived variables (default 10)")
    p.add_argument("--view-plotted", type=int, default=VIEW_PLOTTED, help="view_load: of which plotted (default 3)")
    p.add_argument("--purge", action="store_true", help="drop the OS page cache before each measured stage (needs sudo -n)")
    p.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True,
                   help="check each stage's result against an independent pyarrow/numpy route (default ON; --no-verify)")
    p.add_argument("--verify-timeout", type=float, default=3600, help="seconds one stage's check may take")
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
