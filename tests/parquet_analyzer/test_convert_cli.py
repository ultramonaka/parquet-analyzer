"""convert_cli.main (tools/convert_to_parquet.sh, detailed_specification.md 15章).

The extension/argument handling needs no conversion libraries; the end-to-end cases skip
without the optional `convert` dependency group."""

from __future__ import annotations

import datetime as dt
import runpy
import sys

import numpy as np
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import convert_cli

T = np.linspace(0.0, 10.0, 100)


def _make_mat(path, **variables):
    sio = pytest.importorskip("scipy.io")
    sio.savemat(path, variables)
    return path


# ---- argument handling (no optional deps needed) ---------------------------------------------


def test_no_arguments_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        convert_cli.main([])
    assert exc.value.code == 2
    assert "input" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        convert_cli.main(["-h"])
    assert exc.value.code == 0
    assert "--time-var" in capsys.readouterr().out


def test_non_numeric_raster_is_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        convert_cli.main([str(tmp_path / "a.mf4"), "--raster", "abc"])
    assert exc.value.code == 2


@pytest.mark.parametrize("name", ["data.txt", "data.parquet", "data", "data.mat.bak"])
def test_unrecognized_extension_returns_1_and_lists_expected(tmp_path, capsys, name):
    src = tmp_path / name
    src.write_bytes(b"x")
    assert convert_cli.main([str(src)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: unrecognized extension")
    assert ".mat" in err and ".mf4" in err
    assert not list(tmp_path.glob("*.parquet")) or name == "data.parquet"


def test_dispatch_by_extension_is_case_insensitive(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(convert_cli, "convert_mdf_to_parquet", lambda i, o, raster=None: calls.append(("mdf", i, o, raster)) or o)
    monkeypatch.setattr(convert_cli, "convert_mat_to_parquet", lambda i, o, time_var=None: calls.append(("mat", i, o, time_var)) or o)
    for name in ("a.MF4", "a.mdf", "a.DAT"):
        assert convert_cli.main([str(tmp_path / name), "--raster", "0.5"]) == 0
    assert convert_cli.main([str(tmp_path / "a.MAT"), "--time-var", "clock"]) == 0
    assert [c[0] for c in calls] == ["mdf", "mdf", "mdf", "mat"]
    assert calls[0][3] == 0.5 and calls[3][3] == "clock"
    # default output = input with .parquet extension
    assert calls[0][2] == tmp_path / "a.parquet"
    assert "wrote" in capsys.readouterr().out


def test_conversion_error_and_oserror_become_exit_code_1(tmp_path, monkeypatch, capsys):
    from parquet_analyzer.core.convert import ConversionError

    def fail_conversion(*a, **k):
        raise ConversionError("nothing usable")

    monkeypatch.setattr(convert_cli, "convert_mat_to_parquet", fail_conversion)
    assert convert_cli.main([str(tmp_path / "a.mat")]) == 1
    assert capsys.readouterr().err == "error: nothing usable\n"

    def fail_os(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(convert_cli, "convert_mat_to_parquet", fail_os)
    assert convert_cli.main([str(tmp_path / "a.mat")]) == 1
    assert "Permission denied" in capsys.readouterr().err


def test_python_dash_m_entry_point_runs_main(tmp_path):
    with pytest.raises(SystemExit) as exc:
        sys.argv = ["convert_cli", str(tmp_path / "x.unknown")]
        try:
            runpy.run_module("parquet_analyzer.convert_cli", run_name="__main__")
        finally:
            sys.argv = ["pytest"]
    assert exc.value.code == 1


# ---- end to end (needs the convert group) -----------------------------------------------------


def test_cli_converts_mat_with_default_output(tmp_path, capsys):
    src = _make_mat(tmp_path / "run.mat", time=T, x=np.sin(T))
    assert convert_cli.main([str(src)]) == 0
    out = tmp_path / "run.parquet"
    assert capsys.readouterr().out.strip() == f"wrote {out}"
    assert pq.read_table(out).schema.names == ["time", "x"]


def test_cli_time_var_option_and_explicit_output(tmp_path):
    src = _make_mat(tmp_path / "run.mat", clock=T, x=T)
    out = tmp_path / "elsewhere" / "o.parquet"
    assert convert_cli.main([str(src), str(out), "--time-var", "clock"]) == 0
    assert pq.read_table(out).schema.names == ["clock", "x"]


def test_cli_unknown_time_var_exits_1_without_traceback(tmp_path, capsys):
    src = _make_mat(tmp_path / "run.mat", time=T, x=T)
    assert convert_cli.main([str(src), "--time-var", "zz"]) == 1
    assert "error: --time-var 'zz'" in capsys.readouterr().err
    assert not (tmp_path / "run.parquet").exists()


def test_cli_missing_input_exits_1(tmp_path, capsys):
    """Regression: used to print a misleading scipy OSError traceback."""
    assert convert_cli.main([str(tmp_path / "nope.mat")]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_garbage_mat_exits_1(tmp_path, capsys):
    pytest.importorskip("scipy.io")
    src = tmp_path / "bad.mat"
    src.write_bytes(b"garbage" * 10)
    assert convert_cli.main([str(src)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_cli_garbage_mdf_exits_1(tmp_path, capsys):
    pytest.importorskip("asammdf")
    src = tmp_path / "bad.mf4"
    src.write_bytes(b"garbage" * 50)
    assert convert_cli.main([str(src)]) == 1
    assert "error: couldn't read" in capsys.readouterr().err


def test_cli_output_is_directory_exits_1(tmp_path, capsys):
    src = _make_mat(tmp_path / "run.mat", time=T, x=T)
    out = tmp_path / "d"
    out.mkdir()
    assert convert_cli.main([str(src), str(out)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


@pytest.mark.parametrize("raster", ["0", "-1", "nan"])
def test_cli_invalid_raster_exits_1(tmp_path, capsys, raster):
    asammdf = pytest.importorskip("asammdf")
    mdf = asammdf.MDF()
    mdf.header.start_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    mdf.append([asammdf.Signal(samples=T, timestamps=T, name="x")])
    src = tmp_path / "a.mf4"
    mdf.save(src, overwrite=True)
    assert convert_cli.main([str(src), "--raster", raster]) == 1
    assert "--raster" in capsys.readouterr().err
    assert not (tmp_path / "a.parquet").exists()
