"""Tests for the view_load shape options and --purge of bench_large_file.py (data.ja.md 58.5.1).

The measurement script is not a test (see its docstring); these check its pure helpers and one
small end-to-end child run. The real ``sudo`` is never called.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import bench_large_file as bl  # noqa: E402
import generate_wide_parquet as gw  # noqa: E402

from parquet_analyzer.core.expression import (  # noqa: E402
    expression_margin,
    referenced_names,
)

SCRIPT = Path(__file__).parent / "bench_large_file.py"

# the stage-1 list (a, b, c, d = the first four raw columns)
LEGACY = ["{a} * {b} + {c}", "{a} + {b}", "{b} - {c}", "{c} * {d}", "{a} - {d}",
          "{a} + {b} + {c}", "{b} * {d}", "{c} + {d}", "{a} * {c}", "{b} + {d}"]


def _raw(n):
    return [f"sig_{i:02d}" for i in range(n)]


# ----------------------------------------------------------------- expression builder

def test_default_shape_equals_stage1_list():
    raw = _raw(4)
    got = bl.build_view_exprs(raw, 10)
    a, b, c, d = raw
    assert got == [(f"d{i:02d}", e.format(a=a, b=b, c=c, d=d)) for i, e in enumerate(LEGACY)]
    assert bl.plotted_names(raw, 10, 3) == ["d00", "d01", "d02"]
    assert bl.VIEW_COLORS[:3] == ("#1f77b4", "#d62728", "#2ca02c")


@pytest.mark.parametrize("m", [1, 4, 7])
def test_default_shape_with_fewer_derived_is_a_prefix(m):
    raw = _raw(4)
    assert bl.build_view_exprs(raw, m) == bl.build_view_exprs(raw, 10)[:m]


@pytest.fixture(scope="module")
def shape_10_20():
    raw = _raw(10)
    return raw, bl.build_view_exprs(raw, 20)


def test_10_20_every_raw_column_used(shape_10_20):
    raw, exprs = shape_10_20
    used = set().union(*(referenced_names(e) for _, e in exprs))
    assert set(raw) <= used


def test_10_20_names_unique_and_count(shape_10_20):
    _, exprs = shape_10_20
    names = [n for n, _ in exprs]
    assert len(names) == 20 == len(set(names))


def test_10_20_has_chain_and_special_functions(shape_10_20):
    raw, exprs = shape_10_20
    names = {n for n, _ in exprs}
    chained = [n for n, e in exprs if referenced_names(e) & names]
    assert chained  # second half uses earlier derived variables
    assert any(e.startswith("rolling_mean(") and ", 100)" in e for _, e in exprs)
    assert sum(e.startswith("delta(d") for _, e in exprs) == 1
    assert sum(e.startswith("where(") for _, e in exprs) == 1
    assert sum(e.startswith("rolling_mean(") for _, e in exprs) == 1


def test_10_20_syntax_references_and_margins(shape_10_20):
    raw, exprs = shape_10_20
    seen = set()
    margins = {c: (0, 0) for c in raw}
    for name, expr in exprs:
        refs = referenced_names(expr)  # raises on a syntax error
        assert refs <= set(raw) | seen, (name, expr)  # only raw columns or EARLIER derived variables
        m = expression_margin(expr, None, margins)
        assert m is not None, (name, expr)  # a known margin (DerivedColumn-capable shape)
        margins[name] = m
        seen.add(name)


def test_10_20_chain_gets_deep(shape_10_20):
    _, exprs = shape_10_20
    names = {n for n, _ in exprs}
    depth: dict[str, int] = {}
    for n, e in exprs:
        deps = referenced_names(e) & names
        depth[n] = 1 + max((depth[x] for x in deps), default=0)
    assert max(depth.values()) >= 8


@pytest.mark.parametrize("k", [0, 1, 3, 5, 6, 20])
def test_plotted_count_and_membership(k, shape_10_20):
    raw, exprs = shape_10_20
    got = bl.plotted_names(raw, 20, k)
    assert len(got) == k == len(set(got))
    assert set(got) <= {n for n, _ in exprs}


def test_plotted_mix_raw_only_and_chained(shape_10_20):
    raw, exprs = shape_10_20
    names = {n for n, _ in exprs}
    by_name = dict(exprs)
    got = bl.plotted_names(raw, 20, 5)
    chained = [n for n in got if referenced_names(by_name[n]) & names]
    raw_only = [n for n in got if n not in chained]
    assert len(raw_only) == 3 and len(chained) == 2
    assert "d19" in got  # the deepest one


@pytest.mark.parametrize("shape", [(1, 3), (2, 5), (3, 10), (4, 12), (5, 20), (35, 40)])
def test_other_shapes_valid(shape):
    n, m = shape
    raw = _raw(n)
    exprs = bl.build_view_exprs(raw, m)
    assert len(exprs) == m
    seen = set()
    for name, expr in exprs:
        assert referenced_names(expr) <= set(raw) | seen
        seen.add(name)
    if n <= m:  # enough elementwise expressions to touch every raw column
        assert set(raw) <= set().union(*(referenced_names(e) for _, e in exprs))


def test_deterministic(shape_10_20):
    raw, exprs = shape_10_20
    assert bl.build_view_exprs(raw, 20) == exprs


# ----------------------------------------------------------------- raw column choice

def test_view_raw_columns_wide_and_too_many(tmp_path):
    path = tmp_path / "w.parquet"
    gw.generate(path, 2000, columns=6, row_group_rows=1000, progress=False, free_space_fn=lambda _p: 10**15)
    assert bl.view_raw_columns(str(path), 5) == [f"sig_{i:02d}" for i in range(5)]  # time is not numeric
    with pytest.raises(ValueError):
        bl.view_raw_columns(str(path), 7)


def test_view_raw_columns_bench_file_limited_to_four(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "b.parquet"
    pq.write_table(pa.table({c: [1.0, 2.0] for c in bl.BENCH_COLUMNS}), path)
    assert bl.view_raw_columns(str(path), 4) == list(bl.BENCH_COLUMNS)
    with pytest.raises(ValueError):
        bl.view_raw_columns(str(path), 5)


# ----------------------------------------------------------------- kinds

def test_summarize_and_format_kinds():
    by_name = {"a": "DerivedColumn", "b": "DerivedColumn", "c": "DeferredColumn:ready",
               "d": "DeferredColumn:pending", "e": "MaterializedColumn", "f": "Weird"}
    counts = bl.summarize_kinds(by_name)
    assert counts == {"derived": 2, "deferred": 2, "deferred_ready": 1, "materialized": 1, "other": 1}
    assert bl.format_kinds(counts) == "Derived 2 / Deferred 2 (1 ready) / Materialized 1 / Other 1"
    assert bl.format_kinds(bl.summarize_kinds({"a": "DerivedColumn"})) == "Derived 1 / Deferred 0 / Materialized 0"
    assert bl.format_kinds(None) == "-"


# ----------------------------------------------------------------- purge

def test_purge_commands_per_os():
    assert bl.purge_commands("darwin") == [["sudo", "-n", "purge"]]
    assert bl.purge_commands("linux") == [
        ["sync"], ["sudo", "-n", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"]]
    with pytest.raises(bl.PurgeError):
        bl.purge_commands("win32")


def _fake_run(returncodes, calls):
    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncodes.pop(0), stdout="", stderr="sudo: a password is required")
    return run


def test_purge_runs_commands_in_order():
    calls: list = []
    bl.purge_page_cache(_fake_run([0, 0], calls), "linux")
    assert calls[0] == ["sync"] and calls[1][:3] == ["sudo", "-n", "sh"]


def test_purge_failure_raises_and_stops():
    calls: list = []
    with pytest.raises(bl.PurgeError, match="password is required"):
        bl.purge_page_cache(_fake_run([0, 1], calls), "linux")
    assert len(calls) == 2
    calls = []
    with pytest.raises(bl.PurgeError):
        bl.purge_page_cache(_fake_run([1], calls), "darwin")


def test_purge_missing_command_is_purge_error():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(bl.PurgeError):
        bl.purge_page_cache(run, "darwin")


def test_parent_stops_before_starting_when_purge_fails(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        raise bl.PurgeError("sudo -n purge failed (exit 1)")

    monkeypatch.setattr(bl, "purge_page_cache", boom)
    monkeypatch.setattr(bl, "view_raw_columns", lambda p, n: [])
    monkeypatch.setattr(bl, "run_config", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.setattr(bl, "file_info", lambda p: pytest.fail("must not start"))
    monkeypatch.setattr(sys, "argv", ["bench", "x.parquet", "--stages", "view_load", "--purge"])
    assert bl.main() == 2
    assert "sudo -v" in capsys.readouterr().err


def test_parent_stops_when_view_raw_exceeds_file(monkeypatch, tmp_path, capsys):
    path = tmp_path / "w.parquet"
    gw.generate(path, 2000, columns=3, row_group_rows=1000, progress=False, free_space_fn=lambda _p: 10**15)
    monkeypatch.setattr(bl, "run_config", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.setattr(sys, "argv", ["bench", str(path), "--stages", "view_load", "--view-raw", "10"])
    assert bl.main() == 2
    assert "--view-raw 10" in capsys.readouterr().err


def test_child_marks_stage_error_when_purge_fails_midway(monkeypatch):
    """A purge failure inside the child errors that stage (not run warm) and the run continues."""
    calls = []

    class FakeRunner:
        view_info = None
        c = ["a", "b", "c", "d"]

        def __init__(self, *a, **k):
            pass

        def run(self, stage):
            calls.append(stage)

    def boom(*a, **k):
        raise bl.PurgeError("no password")

    monkeypatch.setattr(bl, "_Runner", FakeRunner)
    monkeypatch.setattr(bl, "purge_page_cache", boom)
    out = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: out.append(a[0]))
    args = type("A", (), dict(file="x", eager="default", stages="home1,home2", stage_timeout=1.0, purge=True,
                              view_raw=4, view_derived=10, view_plotted=3))
    assert bl.child_main(args) == 0
    recs = [json.loads(o[7:]) for o in out if o.startswith("RESULT ")]
    # home1/home2 need load+plot: replayed silently (no purge); the two measured stages error out
    assert [r["stage"] for r in recs] == ["home1", "home2"]
    assert all(r["status"] == "error" and "purge failed" in r["error"] for r in recs)
    assert "home1" not in calls and "home2" not in calls


# ----------------------------------------------------------------- end to end (small file, child process)

def test_view_load_end_to_end_small_file(tmp_path):
    path = tmp_path / "wide.parquet"
    gw.generate(path, 100_000, columns=12, row_group_rows=50_000, progress=False, free_space_fn=lambda _p: 10**15)
    out = tmp_path / "res.json"
    md = tmp_path / "res.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--eager", "default", "--stages", "view_load",
         "--view-raw", "10", "--view-derived", "20", "--view-plotted", "5", "--stage-timeout", "120",
         "--json", str(out), "--markdown", str(md)],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text())
    assert report["purge"] is False and report["view"] == "10 raw / 20 derived / 5 plotted"
    (rec,) = report["runs"][0]["stages"]
    assert rec["status"] == "ok", rec
    for key in ("kinds_settled", "kinds_full"):
        counts = rec[key]["counts"]
        assert counts["derived"] + counts["deferred"] + counts["materialized"] + counts["other"] == 20
        assert len(rec[key]["by_name"]) == 20
    assert rec["full"]["wall_s"] >= rec["wall_s"] and not rec["full"]["timed_out"]
    text = md.read_text()
    assert "view_load_full" in text and "Derived " in text and "purge=off" in text
