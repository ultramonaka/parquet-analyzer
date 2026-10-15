"""Tests for the ``--verify`` checks of bench_large_file.py (data.ja.md 59.1).

The pure comparison functions are tested directly (match / mismatch). The end-to-end tests run the
measurement script on a small generated file (100k rows, row groups of 7000, float32 columns, one
30 s time gap) in child processes. The fault switches (env vars) are read only by the bench child; the
app never sets them.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import bench_large_file as bl  # noqa: E402
import bench_verify as bv  # noqa: E402
import generate_wide_parquet as gw  # noqa: E402

SCRIPT = Path(__file__).parent / "bench_large_file.py"
ROWS = 100_000
ROW_GROUP = 7000


# ----------------------------------------------------------------- pure comparisons

def _stats(**kw):
    base = {"count": 10, "nan_count": 1, "min": -1.0, "max": 2.0, "mean": 0.5, "std": 1.25}
    return {**base, **kw}


def test_compare_stats_match_and_tolerances():
    assert bv.compare_stats(_stats(), _stats()) is None
    assert bv.compare_stats(_stats(mean=0.5 * (1 + 1e-10)), _stats()) is None  # inside 1e-9
    nan = {"count": 3, "nan_count": 3, "min": math.nan, "max": math.nan, "mean": math.nan, "std": math.nan}
    assert bv.compare_stats(nan, dict(nan)) is None


@pytest.mark.parametrize("key,val", [("count", 11), ("nan_count", 2), ("min", -1.0000001), ("max", 2.0 + 1e-12),
                                     ("mean", 0.5 * (1 + 1e-6)), ("std", 1.3)])
def test_compare_stats_mismatch(key, val):
    msg = bv.compare_stats(_stats(**{key: val}), _stats())
    assert msg is not None and key in msg


def test_compare_stats_nan_vs_number():
    assert bv.compare_stats(_stats(max=math.nan), _stats()) is not None


def test_compare_hist_counts():
    assert bv.compare_hist_counts([1, 2, 3], [1, 2, 3], 6) is None
    assert "differ" in bv.compare_hist_counts([1, 2, 3], [1, 3, 2])
    assert "bin count" in bv.compare_hist_counts([1, 2], [1, 2, 3])
    assert "sum" in bv.compare_hist_counts([1, 2, 3], [1, 2, 3], 7)


def test_check_pyramid_starts():
    assert bv.check_pyramid_starts(np.arange(5) * 8192) is None
    assert bv.check_pyramid_starts([0, 8192, 16385]) is not None  # shifted
    assert bv.check_pyramid_starts([0, 8192, 24576]) is not None  # gap
    assert bv.check_pyramid_starts([8192, 16384]) is not None  # does not start at 0


def test_compare_pyramid_bounds_and_buckets():
    mins, maxs = np.array([1.0, np.nan, -3.0]), np.array([5.0, np.nan, 4.0])
    assert bv.compare_pyramid_bounds(mins, maxs, -3.0, 5.0) is None
    assert bv.compare_pyramid_bounds(mins, maxs, -3.0, 5.5) is not None
    idx = [0, 1, 2]
    assert bv.compare_buckets(idx, mins, maxs, mins.copy(), maxs.copy()) is None  # NaN == NaN
    shifted = maxs.copy()
    shifted[2] += 1e-9
    assert "bucket 2 max" in bv.compare_buckets(idx, mins, maxs, mins, shifted)
    no_nan = mins.copy()
    no_nan[1] = 0.0  # the app says "no finite value", the reference found one
    assert bv.compare_buckets(idx, mins, maxs, no_nan, maxs) is not None


def test_check_bracket_left_and_right_conventions():
    # t = [0, 1, 1, 1, 2]; x = 1: left -> 1 (t[0] < 1 <= t[1]); right -> 4 (t[3] <= 1 < t[4])
    assert bv.check_bracket(1.0, "left", 1, 5, 0.0, 1.0) is None
    assert bv.check_bracket(1.0, "right", 4, 5, 1.0, 2.0) is None
    assert bv.check_bracket(1.0, "left", 2, 5, 1.0, 1.0) is not None  # t[1] == x is not < x
    assert bv.check_bracket(1.0, "right", 3, 5, 1.0, 1.0) is not None  # t[3] == x is not > x
    assert bv.check_bracket(0.5, "left", 0, 5, None, 0.0) is not None  # x > t[0]
    assert bv.check_bracket(9.0, "right", 5, 5, 2.0, None) is None  # past the end
    assert bv.check_bracket(9.0, "left", 7, 5, 2.0, None) is not None  # row out of range


def test_compare_window_exact_relative_and_nan_positions():
    ref = np.array([1.0, np.nan, 3.0, -2.0])
    assert bv.compare_window(ref.copy(), ref) is None
    off = ref.copy()
    off[2] += 1e-12
    assert bv.compare_window(off, ref) is not None  # exact mode
    assert bv.compare_window(off, ref, rel=1e-9) is None
    off[2] += 1e-3
    assert bv.compare_window(off, ref, rel=1e-9) is not None
    moved = np.array([np.nan, 1.0, 3.0, -2.0])  # same values elsewhere, NaN at another place
    assert "NaN position" in bv.compare_window(moved, ref, rel=1e-3)
    assert "length" in bv.compare_window(ref[:3], ref)
    inf = np.array([np.inf, 1.0])
    assert bv.compare_window(inf, inf.copy()) is None
    assert bv.compare_window(np.array([-np.inf, 1.0]), inf) is not None


# ----------------------------------------------------------------- reference route

@pytest.fixture(scope="module")
def small_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("verify") / "small.parquet"
    gw.generate(path, ROWS, columns=6, row_group_rows=ROW_GROUP, gaps=1, progress=False,
                free_space_fn=lambda _p: 10**15)
    return path


def test_reference_route_matches_numpy_on_the_whole_file(small_file):
    ref = bv.ParquetRef(str(small_file))
    whole = pq.read_table(small_file).column("sig_00").to_numpy().astype(np.float64)
    assert ref.n_rows == ROWS and len(ref.group_rows) == 15
    st = ref.stats(["sig_00"])
    assert st["count"] == ROWS and st["min"] == whole.min() and st["max"] == whole.max()
    assert abs(st["mean"] - whole.mean()) <= 1e-12 and abs(st["std"] - whole.std()) <= 1e-12
    sub = ref.stats(["sig_00"], lo=6990, hi=14100)  # crosses two row group boundaries
    assert sub["count"] == 14100 - 6990 and sub["max"] == whole[6990:14100].max()
    np.testing.assert_array_equal(ref.read_rows("sig_00", 6990, 14100), whole[6990:14100])
    counts, nfin = ref.histogram("sig_00", np.linspace(whole.min(), whole.max(), 11))
    assert nfin == ROWS and counts.sum() == ROWS
    lo, hi = ref.bucket_minmax("sig_00", [0, 3, 12])
    assert hi[1] == whole[3 * 8192:4 * 8192].max() and lo[2] == whole[12 * 8192:].min()
    times = ref.probe_times("time")
    assert len(times) >= 20 and all(math.isfinite(t) for t in times)
    # the time column is a timestamp: seconds, strictly after the epoch
    assert ref.read_rows("time", 0, 1)[0] > 1e9


# ----------------------------------------------------------------- end to end (child processes)

def _run(path: Path, tmp_path: Path, stages: str, *, env_extra: dict | None = None, extra: tuple[str, ...] = (),
         eager: str = "0") -> tuple[dict, str]:
    out, md = tmp_path / "res.json", tmp_path / "res.md"
    env = None
    if env_extra:
        import os

        env = dict(os.environ, **env_extra)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--eager", eager, "--stages", stages, "--stage-timeout", "120",
         "--json", str(out), "--markdown", str(md), *extra],
        capture_output=True, text=True, timeout=900, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text())
    return {r["stage"]: r for r in report["runs"][0]["stages"]}, md.read_text()


PYRAMID_ENV = {bl.PYRAMID_ROWS_ENV: "100"}  # draw from the pyramid at this small size (test hook)
ALL_STAGES = ("load,plot,home1,zoom10,zoom1000,stats_zoom,home_back,stats_full,derived,derived_plot,"
              "hist_full,derived_full,parquet_export,view_load")


def test_verify_run_all_stages_ok_or_skipped(small_file, tmp_path):
    recs, md = _run(small_file, tmp_path, ALL_STAGES, env_extra=PYRAMID_ENV)
    for stage, rec in recs.items():
        assert rec["status"] in ("ok", "skipped"), rec
        v = rec.get("verify")
        assert v is None or v.startswith(("ok", "skipped")), (stage, v)
        if v is not None:
            assert isinstance(rec["verify_s"], float)
    must_be_ok = ("home1", "zoom10", "zoom1000", "stats_zoom", "stats_full", "derived_plot", "hist_full",
                  "derived_full", "view_load")
    for stage in must_be_ok:
        assert recs[stage]["verify"] == "ok", (stage, recs[stage].get("verify"))
    assert recs["parquet_export"]["verify"].startswith("skipped")
    assert recs["load"].get("verify") is None  # nothing to check there
    assert "| verify |" in md and "| ok (" in md


def test_no_verify_records_nothing(small_file, tmp_path):
    recs, md = _run(small_file, tmp_path, "load,plot,home_back,stats_full", extra=("--no-verify",))
    assert all("verify" not in r for r in recs.values())
    assert "| verify |" in md


@pytest.mark.parametrize("fault,stages,failing,needle", [
    ("stats_max", "load,plot,home_back,stats_full", "stats_full", "max"),
    ("pyramid_bucket", "load,plot,home1", "home1", "pyramid"),
    ("derived_window", "load,plot,derived,derived_plot", "derived_plot", "window bench_ab"),
])
def test_corrupted_app_result_fails_that_stage(small_file, tmp_path, fault, stages, failing, needle):
    env = {bl.FAULT_ENV: fault, **PYRAMID_ENV}
    recs, _md = _run(small_file, tmp_path, stages, env_extra=env)
    assert recs[failing]["status"] == "ok"  # the measurement itself still completes
    verdict = recs[failing]["verify"]
    assert verdict.startswith("failed:") and needle in verdict, verdict
    # the run goes on: every other stage was still recorded
    assert set(recs) == set(stages.split(","))


def test_fault_switch_is_inert_when_unset_or_unknown():
    bl.install_fault("")
    bl.install_fault("no_such_fault")  # no exception, nothing patched
    assert set(bl.FAULTS) == {"stats_max", "pyramid_bucket", "derived_window"}


def test_markdown_verify_cell_and_compare_still_work(tmp_path):
    assert bl.verify_cell({"status": "ok"}) == "-"
    assert bl.verify_cell({"verify": "ok", "verify_s": 1.5}) == "ok (1.5s)"
    assert "|" not in bl.verify_cell({"verify": "failed: a|b", "verify_s": 0.0})
    rec = {"stage": "load", "status": "ok", "wall_s": 1.0, "rss_after_mb": 10, "maxrss_mb": 11, "verify": "ok", "verify_s": 0.1}
    fi = {"path": "x.parquet", "rows": 5, "row_groups": 1, "columns": 2, "size_bytes": 10}
    rep = {"label": "a", "features": "", "machine": {}, "runs": [{"file": fi, "eager": "0", "stages": [rec]}]}
    p = tmp_path / "a.json"
    p.write_text(json.dumps(rep))
    assert "| load |" in bl.compare([str(p), str(p)])
