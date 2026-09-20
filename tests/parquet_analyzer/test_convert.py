from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow.parquet as pq
import pytest

asammdf = pytest.importorskip("asammdf")  # optional `convert` dependency group
scipy_io = pytest.importorskip("scipy.io")
h5py = pytest.importorskip("h5py")

from parquet_analyzer.core.convert import (  # noqa: E402
    ConversionError,
    convert_mat_to_parquet,
    convert_mdf_to_parquet,
)


def _write_mdf(path, start_time, channels: dict):
    from asammdf import MDF, Signal

    mdf = MDF()
    mdf.header.start_time = start_time
    mdf.append([Signal(samples=y, timestamps=t, name=name) for name, (t, y) in channels.items()])
    mdf.save(path, overwrite=True)


def test_mdf_time_column_is_absolute_epoch_seconds(tmp_path):
    """Regression-style check: asammdf's dataframe index is seconds-since-recording-
    start, not epoch time — without adding the file's recorded start_time back in,
    the converted "time" column would render as dates near 1970-01-01 in the app.
    """
    t = np.linspace(0.0, 10.0, 1000)
    start = dt.datetime(2026, 3, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    src = tmp_path / "sample.mf4"
    _write_mdf(src, start, {"temperature": (t, np.sin(t)), "pressure": (t, np.cos(t))})

    out = convert_mdf_to_parquet(src, tmp_path / "sample.parquet")

    table = pq.read_table(out)
    assert set(table.schema.names) == {"time", "temperature", "pressure"}
    time_col = table.column("time").to_numpy()
    assert time_col[0] == pytest.approx(start.timestamp(), abs=1e-3)
    assert time_col[-1] == pytest.approx(start.timestamp() + 10.0, abs=1e-2)
    np.testing.assert_allclose(table.column("temperature").to_numpy(), np.sin(t), atol=1e-6)


def test_mdf_conversion_resamples_multi_rate_channels_onto_one_time_axis(tmp_path):
    """Channel groups at different sample rates can't share one Parquet row axis as-is
    -- to_dataframe's resampling is what makes a single flat table possible at all.
    """
    t_fast = np.linspace(0.0, 10.0, 1000)
    t_slow = np.linspace(0.0, 10.0, 100)
    src = tmp_path / "multirate.mf4"
    _write_mdf(
        src,
        dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        {"fast": (t_fast, np.sin(t_fast)), "slow": (t_slow, np.cos(t_slow))},
    )

    out = convert_mdf_to_parquet(src, tmp_path / "multirate.parquet")

    table = pq.read_table(out)
    n = table.num_rows
    assert table.column("fast").null_count < n  # both columns share the same row count
    assert table.column("slow").null_count < n
    assert len(table.column("fast")) == len(table.column("slow"))


def test_mat_v7_auto_detects_time_variable_by_name(tmp_path):
    t = np.linspace(0.0, 10.0, 500)
    src = tmp_path / "sample.mat"
    scipy_io.savemat(src, {"time": t, "rpm": np.sin(t) * 100 + 3000, "vibration": np.abs(np.cos(t))})

    out = convert_mat_to_parquet(src, tmp_path / "sample.parquet")

    table = pq.read_table(out)
    assert set(table.schema.names) == {"time", "rpm", "vibration"}
    np.testing.assert_allclose(table.column("time").to_numpy(), t)


def test_mat_v7_explicit_time_var_overrides_autodetection(tmp_path):
    t = np.linspace(0.0, 10.0, 200)
    src = tmp_path / "sample.mat"
    # deliberately no variable named like "time" -- must fail without --time-var
    scipy_io.savemat(src, {"clock": t, "a": np.sin(t)})

    with pytest.raises(ConversionError):
        convert_mat_to_parquet(src, tmp_path / "sample.parquet")

    out = convert_mat_to_parquet(src, tmp_path / "sample.parquet", time_var="clock")
    table = pq.read_table(out)
    assert set(table.schema.names) == {"clock", "a"}


def test_mat_skips_variables_whose_length_does_not_match_the_time_variable(tmp_path):
    t = np.linspace(0.0, 10.0, 500)
    src = tmp_path / "sample.mat"
    scipy_io.savemat(src, {"time": t, "a": np.sin(t), "mismatched": np.arange(17, dtype=float)})

    out = convert_mat_to_parquet(src, tmp_path / "sample.parquet")

    table = pq.read_table(out)
    assert set(table.schema.names) == {"time", "a"}  # "mismatched" silently dropped, not crashed


def test_mat_v73_hdf5_backed_file_converts(tmp_path):
    """v7.3 .mat files (used for variables >2GB) are HDF5 under the hood; scipy raises
    NotImplementedError and asks for an HDF5 reader for these, which is the fallback
    path convert_mat_to_parquet must take instead of propagating that error.
    """
    t = np.linspace(0.0, 10.0, 300)
    src = tmp_path / "sample_v73.mat"
    with h5py.File(src, "w") as f:
        f.attrs["MATLAB_class"] = b"double"
        f.create_dataset("time", data=t)
        f.create_dataset("voltage", data=12.0 + 0.1 * np.sin(t))

    # Force the v7.3 code path directly: a hand-built HDF5 file lacks MATLAB's own
    # v7.3 header bytes, so scipy.io.loadmat can't even sniff the version on it (it's
    # not something a real MATLAB v5/v7 reader nor scipy's version check recognizes at
    # all). Real v7.3 files raise NotImplementedError instead, which is the branch
    # being verified here.
    from parquet_analyzer.core import convert as convert_module

    original = convert_module._load_mat_v5_or_v7
    convert_module._load_mat_v5_or_v7 = lambda _p: (_ for _ in ()).throw(NotImplementedError)
    try:
        out = convert_mat_to_parquet(src, tmp_path / "sample_v73.parquet")
    finally:
        convert_module._load_mat_v5_or_v7 = original

    table = pq.read_table(out)
    assert set(table.schema.names) == {"time", "voltage"}
