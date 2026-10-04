"""Abnormal-input tests for core/convert.py (detailed_specification.md 15章).

Skipped as a whole without the optional `convert` dependency group
(`uv run --group dev --group convert pytest ...`)."""

from __future__ import annotations

import datetime as dt
import os
import stat

import numpy as np
import pyarrow.parquet as pq
import pytest

pytest.importorskip("asammdf")
sio = pytest.importorskip("scipy.io")
h5py = pytest.importorskip("h5py")

from parquet_analyzer.core import convert as convert_module  # noqa: E402
from parquet_analyzer.core.convert import (  # noqa: E402
    ConversionError,
    convert_mat_to_parquet,
    convert_mdf_to_parquet,
)

T = np.linspace(0.0, 10.0, 100)


def _mat(tmp_path, name="a.mat", **variables):
    path = tmp_path / name
    sio.savemat(path, variables)
    return path


def _mdf(tmp_path, name, channels, start=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)):
    from asammdf import MDF, Signal

    mdf = MDF()
    mdf.header.start_time = start
    if channels:
        mdf.append([Signal(samples=y, timestamps=t, name=n) for n, (t, y) in channels.items()])
    path = tmp_path / name
    mdf.save(path, overwrite=True)
    return path


def _v73(tmp_path, name="v73.mat", **datasets):
    """A file scipy recognizes as MATLAB v7.3 (HDF5 + 128-byte MATLAB header in the userblock),
    so convert_mat_to_parquet takes its real NotImplementedError -> h5py fallback."""
    path = tmp_path / name
    with h5py.File(path, "w", userblock_size=512) as f:
        for key, value in datasets.items():
            f.create_dataset(key, data=value)
    header = b"MATLAB 7.3 MAT-file, Platform: test".ljust(116, b" ") + b"\x00" * 8 + b"\x00\x02IM"
    with open(path, "r+b") as fh:
        fh.write(header)
    return path


# ---- MAT: selection of variables --------------------------------------------------------


def test_mat_time_name_is_case_insensitive(tmp_path):
    out = convert_mat_to_parquet(_mat(tmp_path, Time=T, x=T), tmp_path / "o.parquet")
    assert pq.read_table(out).schema.names == ["Time", "x"]


def test_mat_time_var_not_found_lists_candidates(tmp_path):
    src = _mat(tmp_path, time=T, x=T)
    with pytest.raises(ConversionError, match="'zz'.*time"):
        convert_mat_to_parquet(src, tmp_path / "o.parquet", time_var="zz")
    assert not (tmp_path / "o.parquet").exists()


def test_mat_no_guessable_time_variable_asks_for_time_var(tmp_path):
    src = _mat(tmp_path, clock=T, x=T)
    with pytest.raises(ConversionError, match="--time-var"):
        convert_mat_to_parquet(src, tmp_path / "o.parquet")


def test_mat_only_time_variable_has_no_data_columns(tmp_path):
    with pytest.raises(ConversionError, match="no numeric data columns"):
        convert_mat_to_parquet(_mat(tmp_path, time=T), tmp_path / "o.parquet")
    assert not (tmp_path / "o.parquet").exists()


def test_mat_without_numeric_1d_variables(tmp_path):
    src = _mat(tmp_path, s="text", scalar=3.0, matrix=np.ones((3, 3)))
    with pytest.raises(ConversionError, match="no 1-D numeric"):
        convert_mat_to_parquet(src, tmp_path / "o.parquet")


def test_mat_skips_2d_struct_scalar_and_string_variables(tmp_path):
    src = _mat(tmp_path, time=T, x=T, m=np.ones((100, 3)), s={"a": 1}, sc=3.0, txt="hello")
    out = convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert pq.read_table(out).schema.names == ["time", "x"]


def test_mat_complex_variable_is_skipped_not_silently_truncated(tmp_path, capsys):
    """Regression: complex arrays passed the np.number check and were cast to float64,
    silently discarding the imaginary part."""
    src = _mat(tmp_path, time=T, c=T + 1j * T, x=T)
    out = convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert pq.read_table(out).schema.names == ["time", "x"]
    assert "complex" in capsys.readouterr().err


def test_mat_integer_columns_become_float64(tmp_path):
    src = _mat(tmp_path, time=T, i=np.arange(100))
    table = pq.read_table(convert_mat_to_parquet(src, tmp_path / "o.parquet"))
    assert str(table.schema.field("i").type) == "double"
    np.testing.assert_array_equal(table.column("i").to_numpy(), np.arange(100, dtype=float))


def test_mat_mismatched_length_warns_on_stderr(tmp_path, capsys):
    src = _mat(tmp_path, time=T, x=T, short=np.arange(7.0))
    convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert "skipping 'short'" in capsys.readouterr().err


def test_mat_single_sample_variables_are_scalars_and_unusable(tmp_path):
    # scipy's squeeze_me turns a length-1 vector into a 0-d scalar, so nothing is 1-D.
    src = _mat(tmp_path, time=np.array([1.0]), x=np.array([2.0]))
    with pytest.raises(ConversionError):
        convert_mat_to_parquet(src, tmp_path / "o.parquet")


def test_mat_zero_length_variables_are_a_conversion_error(tmp_path):
    """15.5: a 0-row conversion is an error and writes nothing."""
    src = _mat(tmp_path, time=np.array([]), x=np.array([]))
    with pytest.raises(ConversionError, match="0 rows"):
        convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert not (tmp_path / "o.parquet").exists()


def test_mat_descending_time_converts_with_warning(tmp_path, capsys):
    """15.5: still converted (the app warns on load, data ch.29), but warned here too."""
    out = convert_mat_to_parquet(_mat(tmp_path, "d.mat", time=T[::-1].copy(), x=T), tmp_path / "d.parquet")
    np.testing.assert_array_equal(pq.read_table(out).column("time").to_numpy(), T[::-1])
    err = capsys.readouterr().err
    assert "'time'" in err and "not ascending" in err and "index 1" in err


def test_mat_nan_time_converts_with_warning(tmp_path, capsys):
    t_nan = T.copy()
    t_nan[5] = np.nan
    out = convert_mat_to_parquet(_mat(tmp_path, "n.mat", time=t_nan, x=T), tmp_path / "n.parquet")
    assert np.isnan(pq.read_table(out).column("time").to_numpy()[5])
    err = capsys.readouterr().err
    assert "'time'" in err and "NaN" in err and "index 5" in err


def test_mat_ascending_time_gives_no_warning(tmp_path, capsys):
    convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), tmp_path / "o.parquet")
    assert capsys.readouterr().err == ""


# ---- MAT: v7.3 via the real fallback ----------------------------------------------------


def test_mat_v73_real_header_takes_h5py_fallback(tmp_path):
    src = _v73(tmp_path, time=T.reshape(-1, 1), x=T.reshape(1, -1), m=np.ones((3, 100)), sc=3.0)
    out = convert_mat_to_parquet(src, tmp_path / "o.parquet")
    table = pq.read_table(out)
    assert table.schema.names == ["time", "x"]  # column/row vectors squeezed; 2-D/scalar skipped
    np.testing.assert_allclose(table.column("x").to_numpy(), T)


def test_mat_v73_group_is_skipped_as_not_numeric(tmp_path, capsys):
    """15.5: a struct (HDF5 group) used to be reported as 'length 0'."""
    src = _v73(tmp_path, time=T, x=T)
    with h5py.File(src, "r+") as f:
        f.create_group("g")
    out = convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert pq.read_table(out).schema.names == ["time", "x"]
    err = capsys.readouterr().err
    assert "'g'" in err and "not numeric" in err and "length 0" not in err


def test_mat_v73_complex_is_skipped(tmp_path, capsys):
    src = _v73(tmp_path, time=T, x=T, c=T + 1j * T)
    out = convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert pq.read_table(out).schema.names == ["time", "x"]
    assert "complex" in capsys.readouterr().err


# ---- MAT: bad files / bad paths -----------------------------------------------------------


@pytest.mark.parametrize("payload", [b"", b"garbage" * 10, b"\x00" * 200])
def test_mat_unreadable_file_raises_conversion_error(tmp_path, payload):
    """Regression: scipy leaked IndexError/MatReadError/ValueError (CLI traceback)."""
    src = tmp_path / "bad.mat"
    src.write_bytes(payload)
    with pytest.raises(ConversionError, match="MAT file"):
        convert_mat_to_parquet(src, tmp_path / "o.parquet")
    assert not (tmp_path / "o.parquet").exists()


def test_mat_missing_input_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="nope.mat"):
        convert_mat_to_parquet(tmp_path / "nope.mat", tmp_path / "o.parquet")


def test_mat_input_that_is_a_directory(tmp_path):
    (tmp_path / "dir.mat").mkdir()
    with pytest.raises(FileNotFoundError):
        convert_mat_to_parquet(tmp_path / "dir.mat", tmp_path / "o.parquet")


def test_mat_output_equal_to_input_is_refused_and_input_untouched(tmp_path):
    """Regression: the source .mat used to be overwritten with Parquet bytes."""
    src = _mat(tmp_path, time=T, x=T)
    before = src.read_bytes()
    with pytest.raises(ConversionError, match="input file itself"):
        convert_mat_to_parquet(src, src)
    assert src.read_bytes() == before


def test_mat_output_in_missing_directory_is_created(tmp_path):
    out = convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), tmp_path / "sub" / "deep" / "o.parquet")
    assert out.is_file()


def test_mat_existing_output_is_overwritten(tmp_path):
    out = tmp_path / "o.parquet"
    out.write_text("old")
    convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), out)
    assert pq.read_table(out).num_rows == 100


def test_mat_output_is_directory_raises_oserror_and_leaves_no_temp(tmp_path):
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    with pytest.raises(OSError):
        convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), out_dir)
    assert list(out_dir.iterdir()) == []
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_mat_unwritable_output_directory_raises_oserror(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), ro / "o.parquet")
    finally:
        ro.chmod(stat.S_IRWXU)


def test_failed_write_leaves_no_partial_output_and_keeps_old_file(tmp_path, monkeypatch):
    """Regression: a failure mid-write used to leave a truncated .parquet behind
    (or clobber an existing good one); now it goes through a temp file + rename."""
    out = tmp_path / "o.parquet"
    out.write_bytes(b"previous good content")

    def boom(table, where, *a, **k):
        with open(where, "wb") as fh:
            fh.write(b"PAR1 partial")
        raise OSError("disk full")

    monkeypatch.setattr(convert_module.pq, "write_table", boom)
    with pytest.raises(OSError, match="disk full"):
        convert_mat_to_parquet(_mat(tmp_path, time=T, x=T), out)
    assert out.read_bytes() == b"previous good content"
    assert not list(tmp_path.glob("*.tmp"))


# ---- MDF ----------------------------------------------------------------------------------


@pytest.fixture
def mdf_file(tmp_path):
    return _mdf(tmp_path, "a.mf4", {"x": (T, np.sin(T))})


@pytest.mark.parametrize("raster", [0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_mdf_invalid_raster_is_a_conversion_error(mdf_file, tmp_path, raster):
    """Regression: raster=nan raised a bare ValueError from numpy, 0/-1 an MdfException."""
    with pytest.raises(ConversionError, match="--raster"):
        convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet", raster=raster)
    assert not (tmp_path / "o.parquet").exists()


def test_mdf_raster_resamples_to_requested_step(mdf_file, tmp_path):
    table = pq.read_table(convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet", raster=0.5))
    times = table.column("time").to_numpy()
    assert table.num_rows == 21
    np.testing.assert_allclose(np.diff(times), 0.5)


def test_mdf_raster_larger_than_span_yields_single_row(mdf_file, tmp_path):
    table = pq.read_table(convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet", raster=1000.0))
    assert table.num_rows == 1


def test_mdf_channels_of_different_length_share_one_axis(tmp_path):
    src = _mdf(tmp_path, "dl.mf4", {"x": (T, T), "y": (T[:10], T[:10])})
    table = pq.read_table(convert_mdf_to_parquet(src, tmp_path / "o.parquet"))
    assert table.schema.names == ["time", "x", "y"]
    assert len(table.column("x")) == len(table.column("y"))


def test_mdf_non_identifier_channel_names_are_kept_verbatim(tmp_path):
    src = _mdf(tmp_path, "ni.mf4", {"a b": (T, T), "1x": (T, T)})
    table = pq.read_table(convert_mdf_to_parquet(src, tmp_path / "o.parquet"))
    assert set(table.schema.names) == {"time", "a b", "1x"}


def test_mdf_naive_start_time_does_not_crash(tmp_path):
    src = _mdf(tmp_path, "naive.mf4", {"x": (T, T)}, start=dt.datetime(2026, 1, 1))
    assert pq.read_table(convert_mdf_to_parquet(src, tmp_path / "o.parquet")).num_rows == 100


def test_mdf_reversed_timestamps_are_refused(tmp_path):
    """15.5: asammdf would silently resample this to a single row."""
    src = _mdf(tmp_path, "rev.mf4", {"x": (T[::-1].copy(), T)})
    with pytest.raises(ConversionError, match="non-ascending"):
        convert_mdf_to_parquet(src, tmp_path / "o.parquet")
    assert not (tmp_path / "o.parquet").exists()


def test_mdf_zero_rows_is_a_conversion_error(mdf_file, tmp_path, monkeypatch):
    import pandas as pd
    from asammdf import MDF

    monkeypatch.setattr(MDF, "to_dataframe", lambda self, **kw: pd.DataFrame({"x": np.array([], dtype=float)}, index=np.array([], dtype=float)))
    with pytest.raises(ConversionError, match="0 rows"):
        convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet")


def test_mdf_nan_time_converts_with_warning(mdf_file, tmp_path, monkeypatch, capsys):
    import pandas as pd
    from asammdf import MDF

    df = pd.DataFrame({"x": np.arange(4.0)}, index=np.array([0.0, 1.0, np.nan, 3.0]))
    monkeypatch.setattr(MDF, "to_dataframe", lambda self, **kw: df)
    convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet")
    err = capsys.readouterr().err
    assert "'time'" in err and "NaN" in err and "index 2" in err


def test_mdf_empty_file_has_no_columns(tmp_path):
    with pytest.raises(ConversionError, match="no numeric data columns"):
        convert_mdf_to_parquet(_mdf(tmp_path, "e.mf4", {}), tmp_path / "o.parquet")


@pytest.mark.parametrize("payload", [b"", b"garbage" * 50])
def test_mdf_invalid_file_raises_conversion_error(tmp_path, payload):
    """Regression: asammdf's MdfException used to escape (traceback from the CLI)."""
    src = tmp_path / "bad.mf4"
    src.write_bytes(payload)
    with pytest.raises(ConversionError, match="MDF"):
        convert_mdf_to_parquet(src, tmp_path / "o.parquet")


def test_mdf_missing_input_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        convert_mdf_to_parquet(tmp_path / "no.mf4", tmp_path / "o.parquet")


def test_mdf_output_equal_to_input_is_refused(mdf_file):
    before = mdf_file.read_bytes()
    with pytest.raises(ConversionError, match="input file itself"):
        convert_mdf_to_parquet(mdf_file, mdf_file)
    assert mdf_file.read_bytes() == before


def test_mdf_non_numeric_columns_are_skipped_with_warning(mdf_file, tmp_path, monkeypatch, capsys):
    """to_dataframe can hand back string/complex columns (pandas>=3 uses StringDtype, which
    np.issubdtype rejects with TypeError); they must be skipped with a warning, not cast/crash."""
    import pandas as pd
    from asammdf import MDF

    df = pd.DataFrame(
        {"x": np.arange(5.0), "txt": list("abcde"), "cplx": np.arange(5) * (1 + 1j)}, index=np.arange(5.0)
    )
    monkeypatch.setattr(MDF, "to_dataframe", lambda self, **kw: df)
    table = pq.read_table(convert_mdf_to_parquet(mdf_file, tmp_path / "o.parquet"))
    assert table.schema.names == ["time", "x"]
    err = capsys.readouterr().err
    assert "'txt'" in err and "'cplx'" in err
