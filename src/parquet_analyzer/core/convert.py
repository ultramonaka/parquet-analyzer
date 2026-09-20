"""Convert MDF (.mf4/.mdf/.dat) and MATLAB (.mat, v5/v7 and v7.3) measurement files to
the flat "time column + numeric columns" Parquet layout this app expects
(detailed_specification.md 15章). GUI-independent (core/) — the
heavy dependencies (asammdf/scipy/h5py) live in the optional `convert` dependency group
(pyproject.toml) precisely so importing this module is the only thing that needs them,
not the main app.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


class ConversionError(ValueError):
    """Raised when a source file can't be converted (unreadable, no usable time
    variable found, etc.) — turns a confusing library exception into an actionable
    message, the same role ExpressionError plays for expression.py."""


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _write_parquet(time_name: str, time_values: np.ndarray, columns: dict[str, np.ndarray], output_path: Path) -> Path:
    if not columns:
        raise ConversionError("no numeric data columns found alongside the time variable")
    table = pa.table({time_name: time_values, **columns})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path)
    return output_path


def _select_time_and_columns(
    variables: dict[str, np.ndarray], time_var: str | None
) -> tuple[str, np.ndarray, dict[str, np.ndarray]]:
    """Given every candidate 1-D numeric variable found in a source file, pick the one
    to use as the time axis and keep only the other same-length ones as data columns
    (mismatched-length variables can't share a row axis with the time column, so
    they're dropped with a warning rather than silently truncated/padded).
    """
    if time_var is not None:
        if time_var not in variables:
            raise ConversionError(f"--time-var {time_var!r} not found among: {sorted(variables)}")
    else:
        candidates = [name for name in variables if name.lower() in ("time", "t", "timestamp", "timestamps")]
        if not candidates:
            raise ConversionError(
                f"couldn't guess which variable is the time axis among: {sorted(variables)}; "
                "pass --time-var to pick one explicitly"
            )
        time_var = candidates[0]

    time_values = variables[time_var]
    n = len(time_values)
    columns: dict[str, np.ndarray] = {}
    for name, values in variables.items():
        if name == time_var:
            continue
        if len(values) != n:
            _warn(f"skipping {name!r}: length {len(values)} does not match time variable {time_var!r} ({n})")
            continue
        columns[name] = values.astype(np.float64, copy=False)
    return time_var, time_values.astype(np.float64, copy=False), columns


def convert_mdf_to_parquet(input_path: str | Path, output_path: str | Path, raster: float | None = None) -> Path:
    """MDF (ASAM Measurement Data Format, .mf4/.mdf/.dat) -> Parquet.

    Channel groups can run at different sample rates; `MDF.to_dataframe` resamples
    them onto one shared time axis (via `raster`, seconds between samples — omit to
    use asammdf's own default, the union of all channels' master timestamps, which can
    be irregular/large for multi-rate files). The time column is converted from
    seconds-since-recording-start to epoch seconds (using the file's recorded absolute
    start time) so it matches what `MainWindow._to_numeric`/`pg.DateAxisItem` expect —
    relative seconds alone would render as dates near 1970-01-01 in the app.
    """
    from asammdf import MDF  # deferred: only importable when `convert` deps are installed

    input_path = Path(input_path)
    output_path = Path(output_path)
    with MDF(input_path) as mdf:
        df = mdf.to_dataframe(raster=raster, time_from_zero=False)
        start_epoch = mdf.header.start_time.timestamp()

    time_values = start_epoch + df.index.to_numpy(dtype=np.float64)
    columns: dict[str, np.ndarray] = {}
    for col in df.columns:
        series = df[col]
        if not np.issubdtype(series.dtype, np.number):
            _warn(f"skipping non-numeric channel {col!r} ({series.dtype})")
            continue
        columns[str(col)] = series.to_numpy(dtype=np.float64)

    return _write_parquet("time", time_values, columns, output_path)


def _load_mat_v5_or_v7(input_path: Path) -> dict[str, np.ndarray]:
    import scipy.io

    raw = scipy.io.loadmat(input_path, squeeze_me=True)
    variables: dict[str, np.ndarray] = {}
    for name, value in raw.items():
        if name.startswith("__"):
            continue  # scipy's __header__/__version__/__globals__ metadata entries
        array = np.asarray(value)
        if array.ndim == 1 and np.issubdtype(array.dtype, np.number):
            variables[name] = array
    return variables


def _load_mat_v73(input_path: Path) -> dict[str, np.ndarray]:
    import h5py

    variables: dict[str, np.ndarray] = {}
    with h5py.File(input_path, "r") as f:
        for name in f:
            if name.startswith("#"):
                continue  # HDF5-internal MATLAB bookkeeping (e.g. "#refs#")
            array = np.asarray(f[name]).squeeze()
            if array.ndim == 1 and np.issubdtype(array.dtype, np.number):
                variables[name] = array
    return variables


def convert_mat_to_parquet(input_path: str | Path, output_path: str | Path, time_var: str | None = None) -> Path:
    """MATLAB .mat (v5/v7 via scipy, v7.3 via h5py — v7.3 is HDF5-based, which is how
    MATLAB stores variables over 2GB) -> Parquet.

    Unlike MDF, a .mat file has no format-level notion of "the time channel" — it's
    just named variables, arbitrarily shaped. Only 1-D numeric variables are even
    considered; a name matching time/t/timestamp/timestamps (case-insensitive) is
    picked automatically, otherwise pass `time_var` explicitly.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    try:
        variables = _load_mat_v5_or_v7(input_path)
    except NotImplementedError:
        # scipy.io.loadmat's own error for v7.3 files points at h5py; this is that path.
        variables = _load_mat_v73(input_path)

    if not variables:
        raise ConversionError(f"no 1-D numeric variables found in {input_path}")

    time_name, time_values, columns = _select_time_and_columns(variables, time_var)
    return _write_parquet(time_name, time_values, columns, output_path)
