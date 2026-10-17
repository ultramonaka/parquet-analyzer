"""bench_export_expr.py: the pure parts of the export-speed benchmark (lookup maps vs if expressions).
Small synthetic file only; nothing here touches data/raw. No Qt."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core import parquet_export as pe

_spec = importlib.util.spec_from_file_location("bench_export_expr", Path(__file__).with_name("bench_export_expr.py"))
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench_export_expr"] = bench
_spec.loader.exec_module(bench)

N = 20_000
COLS = ("x", "y", "z")


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    rng = np.random.default_rng(3)
    t = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(N, dtype="int64") * np.timedelta64(10, "ms")
    table = pa.table({
        "ts": pa.array(t),
        "x": rng.normal(20, 5, N), "y": rng.normal(100, 30, N), "z": rng.uniform(0, 3000, N),
    })
    path = tmp_path_factory.mktemp("bench") / "small.parquet"
    pq.write_table(table, path, row_group_size=5_000)
    return path


@pytest.fixture(scope="module")
def stats(small):
    return bench.sample_stats(small, COLS, 10_000)


@pytest.fixture(scope="module")
def registry(stats):
    return bench.build_registry(stats)


def _ids():
    flat = {v: {"min": 0.0, "max": 1.0, "med": 0.5} for v in COLS}
    return list(bench.build_variants(flat))


VARIANT_NAMES = _ids()


def test_variant_list_is_complete():
    assert VARIANT_NAMES == [
        "raw", "arith", "if_simple", "if_nested", "where_simple", "where_and", "curve_s", "curve_l",
        "map_s", "map_m", "map_l", "map_nearest", "map_plus_if", "two_maps",
    ]


# (1) plans and validation ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", VARIANT_NAMES, ids=VARIANT_NAMES)
def test_variant_builds_a_plan_and_validates(name, small, stats, registry):
    spec = bench.build_variants(stats)[name]
    assert set(spec.tables) <= set(registry.maps)
    plan = bench.make_plan(small, spec, COLS, registry)
    assert plan.rows == N
    assert [c.name for c in plan.columns] == ["out"]
    if spec.expression:
        assert dict(plan.derived) == {"out": spec.expression}
        bench.validate_expression(spec.expression, registry)  # the project's syntax check + a probe evaluation
        assert plan.columns[0].source is None
    else:
        assert plan.derived == () and plan.columns[0].source == "x"


def test_tables_have_the_documented_sizes(registry):
    assert len(registry.maps["cv_s"].x) == 33 and len(registry.maps["cv_l"].x) == 2049
    assert registry.maps["mp_s"].z.shape == (9, 9)
    assert registry.maps["mp_m"].z.shape == (65, 65)
    assert registry.maps["mp_l"].z.shape == (513, 513)
    assert registry.maps["mp_nearest"].interpolation == "nearest"
    assert all(np.isfinite(t.values).all() for t in registry.maps.values())


def test_sample_stats_reads_only_the_head(small):
    s = bench.sample_stats(small, COLS, 100)
    full = pq.read_table(small)
    head_x = full["x"].to_numpy()[:100]
    assert s["x"]["min"] == head_x.min() and s["x"]["max"] == head_x.max()
    assert s["x"]["med"] == pytest.approx(np.median(head_x))


def test_default_cols_falls_back_to_first_three_numeric(small):
    assert bench.default_cols(small) == COLS  # no temperature/pressure/rpm in the file


def test_degenerate_axis_still_ascends(tmp_path):
    path = tmp_path / "c.parquet"
    pq.write_table(pa.table({"t": np.arange(10.0), "a": np.ones(10), "b": np.arange(10.0), "c": np.arange(10.0)}), path)
    s = bench.sample_stats(path, ("a", "b", "c"), 10)
    assert s["x"]["max"] > s["x"]["min"]
    bench.build_registry(s)  # LookupTable raises for a non-ascending axis


# (2) running every variant ---------------------------------------------------------------------------------

FIELDS = {"variant", "expression", "error", "seconds", "cpu_s", "cores", "rows", "rows_per_s", "peak_rss_mb",
          "out_bytes", "writer", "chunk_halvings", "memory_wait"}


@pytest.mark.parametrize("name", VARIANT_NAMES, ids=VARIANT_NAMES)
def test_run_variant_writes_then_removes_the_file(name, small, stats, registry, tmp_path):
    spec = bench.build_variants(stats)[name]
    seen = {}

    def look(path):
        seen["exists"] = path.exists()
        seen["table"] = pq.read_table(path)

    rec = bench.run_variant(small, spec, COLS, registry, tmp_path, on_output=look)
    assert rec["error"] is None, rec
    assert FIELDS <= set(rec)
    assert rec["rows"] == N and rec["out_bytes"] > 0 and rec["seconds"] > 0
    assert rec["writer"] in ("polars", "pyarrow")
    assert seen["exists"] and seen["table"].num_rows == N
    out = seen["table"]["out"].to_numpy()
    assert np.isfinite(out).all()
    assert list(tmp_path.iterdir()) == []  # neither the output nor a .part is left


def test_rows_limit(small, stats, registry, tmp_path):
    rec = bench.run_variant(small, bench.build_variants(stats)["arith"], COLS, registry, tmp_path, rows=5_000)
    assert rec["rows"] == 5_000


# (3) numeric correctness -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exported(small, stats, registry, tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("out")
    got = {}
    for name, spec in bench.build_variants(stats).items():
        def grab(path, name=name):
            got[name] = pq.read_table(path)["out"].to_numpy()

        rec = bench.run_variant(small, spec, COLS, registry, out_dir, on_output=grab)
        assert rec["error"] is None, rec
    return got


@pytest.fixture(scope="module")
def raw(small):
    t = pq.read_table(small)
    return {c: t[c].to_numpy() for c in COLS}


def test_if_simple_equals_where_simple(exported):
    assert np.array_equal(exported["if_simple"], exported["where_simple"])
    assert not np.array_equal(exported["if_simple"], exported["raw"])  # the condition really branches


@pytest.mark.parametrize("name,table,args", [
    ("curve_s", "cv_s", "x"), ("curve_l", "cv_l", "x"), ("map_s", "mp_s", "xy"), ("map_l", "mp_l", "xy"),
    ("map_m", "mp_m", "xy"),
], ids=["curve_s", "curve_l", "map_s", "map_l", "map_m"])
def test_lookup_variants_equal_direct_evaluation(name, table, args, exported, raw, registry):
    expected = registry.maps[table](*[raw[a] for a in args])
    assert np.allclose(exported[name], expected, rtol=1e-12, atol=1e-12)


def test_map_nearest_is_nearest_neighbour(exported, raw, registry):
    t = registry.maps["mp_nearest"]
    for i in (0, 17, 4_321, N - 1):
        ix = int(np.argmin(np.abs(t.x - raw["x"][i])))
        iy = int(np.argmin(np.abs(t.y - raw["y"][i])))
        assert exported["map_nearest"][i] == t.z[iy, ix]


def test_combined_variants(exported, raw, registry, stats):
    assert np.allclose(exported["arith"], raw["x"] * 0.5 + raw["y"])
    assert np.array_equal(exported["raw"], raw["x"])
    mz = stats["z"]["med"]
    expected = np.where(raw["z"] > mz, registry.maps["mp_m"](raw["x"], raw["y"]), raw["x"])
    assert np.allclose(exported["map_plus_if"], expected)
    assert np.allclose(exported["two_maps"], registry.maps["mp_m"](raw["x"], raw["y"]) + registry.maps["mp_s"](raw["y"], raw["z"]))


# (4) table formatting ---------------------------------------------------------------------------------------


def _rec(name, seconds, error=None):
    if error:
        return {"variant": name, "expression": "e", "error": error}
    return {"variant": name, "expression": "e", "error": None, "seconds": seconds, "rows_per_s": 1e6 / seconds,
            "cores": 2.0, "peak_rss_mb": 500.0}


def test_ratios_and_error_row():
    res = [_rec("raw", 1.0), _rec("arith", 2.0), _rec("map_m", 6.0), _rec("map_l", 0, error="boom")]
    by = {r["variant"]: r for r in bench.with_ratios(res)}
    assert by["map_m"]["vs_arith"] == pytest.approx(3.0) and by["map_m"]["vs_raw"] == pytest.approx(6.0)
    assert by["arith"]["vs_arith"] == pytest.approx(1.0)
    assert by["map_l"]["vs_arith"] is None
    text = bench.format_table(res)
    lines = text.splitlines()
    assert lines[0].split()[:1] == ["variant"]
    assert "ERR" in next(ln for ln in lines if ln.startswith("map_l"))
    assert "3.00" in next(ln for ln in lines if ln.startswith("map_m"))
    assert "map_l: boom" in text


def test_ratios_without_baseline_are_none():
    by = bench.with_ratios([_rec("map_m", 6.0)])[0]
    assert by["vs_arith"] is None and by["vs_raw"] is None
    assert "-" in bench.format_table([_rec("map_m", 6.0)])


# (5) cleanup on failure ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()], ids=["error", "interrupt"])
def test_nothing_is_left_when_the_export_raises(exc, small, stats, registry, tmp_path, monkeypatch):
    def fake(plan, path, **kw):
        Path(path).write_bytes(b"x")
        Path(str(path) + ".part").write_bytes(b"x")
        raise exc

    monkeypatch.setattr(pe, "export_parquet", fake)
    spec = bench.build_variants(stats)["arith"]
    if isinstance(exc, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            bench.run_variant(small, spec, COLS, registry, tmp_path)
    else:
        rec = bench.run_variant(small, spec, COLS, registry, tmp_path)
        assert "boom" in rec["error"] and rec["variant"] == "arith"
    assert list(tmp_path.iterdir()) == []


def test_skipped_when_the_disk_is_too_small(small, stats, registry, tmp_path, monkeypatch):
    monkeypatch.setattr(bench.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    rec = bench.run_variant(small, bench.build_variants(stats)["arith"], COLS, registry, tmp_path)
    assert rec["error"].startswith("skipped")
    assert list(tmp_path.iterdir()) == []


# (6) main ---------------------------------------------------------------------------------------------------


def test_main_prints_table_and_writes_json(small, tmp_path, capsys):
    jsonl = tmp_path / "r.jsonl"
    out_dir = tmp_path / "o"
    rc = bench.main(["--file", str(small), "--rows", "5000", "--repeat", "1", "--out-dir", str(out_dir),
                     "--variants", "raw,arith,map_m", "--json", str(jsonl)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "vs arith" in out and "map_m" in out and "ERR" not in out
    recs = [json.loads(ln) for ln in jsonl.read_text().splitlines()]
    assert [r["variant"] for r in recs] == ["raw", "arith", "map_m"]
    assert all(r["rows"] == 5000 for r in recs)
    assert list(out_dir.iterdir()) == []


def test_main_unknown_variant_is_an_error(small, capsys):
    with pytest.raises(SystemExit):
        bench.main(["--file", str(small), "--variants", "nope"])
