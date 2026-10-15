"""Small correctness check for generate_wide_parquet.py (data.ja.md 58.2 / 58.6): 10k rows x 5
columns in row groups of 1000. The 1e9-row measurement itself is not a test."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import generate_wide_parquet as gw  # noqa: E402

ROWS = 10_000


def _make(path, **kw):
    opts = dict(columns=5, row_group_rows=1000, progress=False, free_space_fn=lambda _p: 10**15)
    opts.update(kw)
    return gw.generate(path, ROWS, **opts)


def _cols(path) -> dict[str, np.ndarray]:
    t = pq.read_table(path)
    return {n: t[n].to_numpy().astype(np.float64) for n in t.column_names if n != "time"}


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    path = tmp_path_factory.mktemp("wide") / "w.parquet"
    _make(path, seed=3)
    return path


def test_shape_names_types_row_groups(table):
    pf = pq.ParquetFile(table)
    assert pf.metadata.num_rows == ROWS
    assert pf.metadata.num_row_groups == 10
    schema = pf.schema_arrow
    assert schema.names == ["time", "sig_00", "sig_01", "sig_02", "sig_03", "sig_04"]
    assert schema.field("time").type == pa.timestamp("ns")
    assert schema.field("time").type.tz is None
    assert all(schema.field(n).type == pa.float32() for n in schema.names[1:])


def test_float64_dtype(tmp_path):
    _make(tmp_path / "d.parquet", dtype="float64", compression="snappy")
    assert pq.read_schema(tmp_path / "d.parquet").field("sig_04").type == pa.float64()


def test_last_row_group_is_partial(tmp_path):
    gw.generate(tmp_path / "p.parquet", 2500, columns=2, row_group_rows=1000, progress=False)
    md = pq.ParquetFile(tmp_path / "p.parquet").metadata
    assert [md.row_group(i).num_rows for i in range(md.num_row_groups)] == [1000, 1000, 500]


def test_known_relations(table):
    df = _cols(table)
    s0, s1, s2 = (df[c] for c in ("sig_00", "sig_01", "sig_02"))
    assert np.max(np.abs(s0**2 + s1**2 - 1)) < 2e-3
    assert np.max(np.abs((s2 - s0) - 10)) < 1e-6
    assert s0.max() > 0.99 and s0.min() < -0.99  # a real sine, not a constant


def test_signal_kinds_cycle(tmp_path):
    gw.generate(tmp_path / "k.parquet", 3000, columns=8, row_group_rows=1000, progress=False)
    df = _cols(tmp_path / "k.parquet")
    ramp, noise, steps, counter = (df[c] for c in ("sig_03", "sig_04", "sig_05", "sig_06"))
    assert np.all(counter == np.floor(counter)) and counter.max() < 1024  # integer counter
    assert counter[0] == 0 and counter[10] == 1
    assert np.all(steps == np.floor(steps)) and 0 <= steps.min() and steps.max() <= 10
    assert noise.std() > 0.1
    assert ramp[-1] != ramp[0]  # slowly changing
    assert np.isfinite(df["sig_07"]).all()


def test_same_seed_same_content_and_other_seed_differs(tmp_path):
    _make(tmp_path / "a.parquet", seed=5)
    _make(tmp_path / "b.parquet", seed=5)
    _make(tmp_path / "c.parquet", seed=6)
    a, b, c = (pq.read_table(tmp_path / f"{x}.parquet") for x in "abc")
    assert a.equals(b)
    assert not a.equals(c)  # the noise column depends on the seed


def test_time_axis_and_gaps(table):
    t = pq.read_table(table)["time"].to_numpy()  # datetime64[ns]
    d = np.diff(t).astype("int64")
    jumps = d[d > 10**6]
    assert len(jumps) == 2  # default --gaps 2
    assert (jumps == 10**6 + gw.GAP_SECONDS * 10**9).all()
    assert (d == 10**6).sum() == ROWS - 1 - 2


@pytest.mark.parametrize("gaps", [0, 1, 4])
def test_gap_count(tmp_path, gaps):
    _make(tmp_path / "g.parquet", gaps=gaps, interval_ms=2)
    d = np.diff(pq.read_table(tmp_path / "g.parquet")["time"].to_numpy()).astype("int64")
    assert (d > 2 * 10**6).sum() == gaps


def test_gap_rows_inside_file():
    assert list(gw.gap_rows(100, 3)) == [25, 50, 75]
    assert len(gw.gap_rows(100, 0)) == 0


def test_parse_rows():
    assert gw.parse_rows("1e9") == 10**9
    assert gw.parse_rows("100_000") == 100000
    with pytest.raises(ValueError):
        gw.parse_rows("1.5")
    with pytest.raises(ValueError):
        gw.parse_rows("0")


def test_check_disk_function():
    gw.check_disk(10e9, 21e9)
    with pytest.raises(gw.DiskSpaceError):
        gw.check_disk(10e9, 19e9)  # estimate + 10 GB margin does not fit
    gw.check_disk(10e9, 19e9, force=True)


def test_refuses_when_free_space_too_small_and_removes_partial(tmp_path):
    out = tmp_path / "x.parquet"
    with pytest.raises(gw.DiskSpaceError):
        _make(out, free_space_fn=lambda _p: 1000)  # far below estimate + 10 GB
    assert not out.exists()


def test_force_ignores_disk_check(tmp_path):
    out = tmp_path / "f.parquet"
    info = _make(out, force=True, free_space_fn=lambda _p: 1000)
    assert out.exists() and info["size_bytes"] > 0
    assert info["estimated_bytes"] > 0


def test_cli_exit_codes(tmp_path):
    out = tmp_path / "cli.parquet"
    argv = ["--output", str(out), "--rows", "5e3", "--columns", "2", "--row-group-rows", "1000"]
    assert gw.main(argv, free_space_fn=lambda _p: 1000) == 2
    assert not out.exists()
    assert gw.main([*argv, "--force"], free_space_fn=lambda _p: 1000) == 0
    assert pq.ParquetFile(out).metadata.num_rows == 5000


# --- --values {quantised,full} (58.5.1) -------------------------------------------------------

def test_quantised_default_is_explicit_quantised(tmp_path):
    """The default output is the quantised output of earlier versions: an explicit
    ``values="quantised"`` run writes the same bytes."""
    _make(tmp_path / "def.parquet", seed=2)
    _make(tmp_path / "q.parquet", seed=2, values="quantised")
    assert (tmp_path / "def.parquet").read_bytes() == (tmp_path / "q.parquet").read_bytes()


def test_full_shape_names_types_row_groups(tmp_path):
    _make(tmp_path / "full.parquet", values="full")
    pf = pq.ParquetFile(tmp_path / "full.parquet")
    assert pf.metadata.num_rows == ROWS
    assert pf.metadata.num_row_groups == 10
    schema = pf.schema_arrow
    assert schema.names == ["time", "sig_00", "sig_01", "sig_02", "sig_03", "sig_04"]
    assert schema.field("time").type == pa.timestamp("ns")
    assert all(schema.field(n).type == pa.float32() for n in schema.names[1:])


def test_full_known_relations_with_widened_tolerance(tmp_path):
    _make(tmp_path / "full.parquet", seed=3, values="full")
    df = _cols(tmp_path / "full.parquet")
    s0, s1, s2 = (df[c] for c in ("sig_00", "sig_01", "sig_02"))
    assert np.max(np.abs(s0**2 + s1**2 - 1)) < gw.FULL_TOLERANCE
    assert np.max(np.abs((s2 - s0) - 10)) < gw.FULL_TOLERANCE
    assert s0.max() > 0.99 and s0.min() < -0.99


def test_full_values_are_not_quantised(tmp_path):
    """Unquantised: the sine is not on the 2**-10 grid, and the counter/steps are no longer
    integers after the noise is added."""
    _make(tmp_path / "full.parquet", seed=3, values="full", columns=8)
    df = _cols(tmp_path / "full.parquet")
    grid = df["sig_00"] * 1024
    assert np.max(np.abs(grid - np.round(grid))) > 0.01
    assert not np.all(df["sig_06"] == np.floor(df["sig_06"]))  # counter column


def test_full_same_seed_same_content_and_other_seed_differs(tmp_path):
    _make(tmp_path / "a.parquet", seed=5, values="full")
    _make(tmp_path / "b.parquet", seed=5, values="full")
    _make(tmp_path / "c.parquet", seed=6, values="full")
    a, b, c = (pq.read_table(tmp_path / f"{x}.parquet") for x in "abc")
    assert a.equals(b)
    assert not a.equals(c)


def test_full_is_noticeably_larger_than_quantised(tmp_path):
    q = _make(tmp_path / "q.parquet", seed=1, values="quantised")
    f = _make(tmp_path / "f.parquet", seed=1, values="full")
    assert f["size_bytes"] > 1.5 * q["size_bytes"]


def test_invalid_values_rejected(tmp_path):
    with pytest.raises(ValueError):
        gw.build_row_group(0, 10, rows=10, columns=1, dtype="float32", interval_ms=1, gaps=0, seed=0,
                           values="bogus")
    with pytest.raises(ValueError):
        _make(tmp_path / "bad.parquet", values="bogus")


def test_cli_values_option(tmp_path):
    out = tmp_path / "cli_full.parquet"
    argv = ["--output", str(out), "--rows", "3000", "--columns", "2", "--row-group-rows", "1000",
            "--values", "full"]
    assert gw.main(argv, free_space_fn=lambda _p: 10**15) == 0
    assert pq.ParquetFile(out).metadata.num_rows == 3000
