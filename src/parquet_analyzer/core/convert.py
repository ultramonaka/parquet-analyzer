"""Convert MDF (.mf4/.mdf/.dat) and MATLAB (.mat, v5/v7 and v7.3) measurement files to
the flat "time column + numeric columns" Parquet layout this app expects
(detailed_specification.md 15章). GUI-independent (core/) — the
heavy dependencies (asammdf/scipy/h5py) live in the optional `convert` dependency group
(pyproject.toml) precisely so importing this module is the only thing that needs them,
not the main app.
"""

from __future__ import annotations

import math
import os
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


def _is_real_numeric(dtype: np.dtype) -> bool:
    """True for integer/float dtypes. Complex is excluded on purpose: casting it to
    float64 would silently drop the imaginary part (wrong data), so callers skip it
    with a warning instead (detailed_specification.md 15.4)."""
    if not isinstance(dtype, np.dtype):
        return False  # pandas extension dtypes (StringDtype, ...) make np.issubdtype raise TypeError
    return bool(np.issubdtype(dtype, np.number)) and not np.issubdtype(dtype, np.complexfloating)


def _check_paths(input_path: Path, output_path: Path) -> None:
    """Fail early with a clear message instead of a library traceback (detailed_specification.md 15.5): a missing
    input, or an output that is the input file itself (which would overwrite the source
    measurement with Parquet bytes)."""
    if not input_path.is_file():
        raise FileNotFoundError(f"input file not found: {input_path}")
    try:
        same = output_path.exists() and os.path.samefile(input_path, output_path)
    except OSError:
        same = False
    if same:
        raise ConversionError(f"output path is the input file itself: {output_path}")


def _warn_time_axis(name: str, values: np.ndarray) -> None:
    """Warn (don't fail) when the time column is not ascending or holds NaN; the app warns
    again on load (data ch.29). Names the variable and the first offending index
    (detailed_specification.md 15.5)."""
    values = np.asarray(values, dtype=np.float64)
    nan_idx = np.flatnonzero(np.isnan(values))
    if nan_idx.size:
        _warn(f"time variable {name!r} contains NaN (first at index {int(nan_idx[0])})")
    if values.size > 1:
        with np.errstate(invalid="ignore"):
            bad = np.flatnonzero(np.diff(values) < 0)
        if bad.size:
            _warn(f"time variable {name!r} is not ascending (first decrease at index {int(bad[0]) + 1})")


def _write_parquet(time_name: str, time_values: np.ndarray, columns: dict[str, np.ndarray], output_path: Path) -> Path:
    if not columns:
        raise ConversionError("no numeric data columns found alongside the time variable")
    if len(time_values) == 0:
        raise ConversionError("the conversion produced 0 rows (empty variables/channels)")  # 15.5
    _warn_time_axis(time_name, time_values)
    table = pa.table({time_name: time_values, **columns})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file and rename, so a failure mid-write never leaves a
    # truncated .parquet (nor clobbers an existing good one) behind (detailed_specification.md 15.5).
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
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
    _check_paths(input_path, output_path)
    if raster is not None and not (math.isfinite(raster) and raster > 0):
        raise ConversionError(f"--raster must be a positive finite number of seconds, got {raster!r}")
    try:
        with MDF(input_path) as mdf:
            for gi in range(len(mdf.groups)):
                master = np.asarray(mdf.get_master(gi), dtype=np.float64)
                if master.size > 1 and bool(np.any(np.diff(master) < 0)):
                    # asammdf's resampling would silently collapse this to ~1 row (15.5)
                    raise ConversionError(f"channel group {gi} has non-ascending timestamps; refusing to resample")
            df = mdf.to_dataframe(raster=raster, time_from_zero=False)
            start_epoch = mdf.header.start_time.timestamp()
    except ConversionError:
        raise
    except Exception as e:  # noqa: BLE001 (asammdf raises MdfException and assorted others)
        raise ConversionError(f"couldn't read {input_path} as MDF: {e}") from e

    time_values = start_epoch + df.index.to_numpy(dtype=np.float64)
    columns: dict[str, np.ndarray] = {}
    for col in df.columns:
        series = df[col]
        if not _is_real_numeric(series.dtype):
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
        if array.ndim == 1 and np.issubdtype(array.dtype, np.complexfloating):
            _warn(f"skipping complex variable {name!r}")
        elif array.ndim == 1 and _is_real_numeric(array.dtype):
            variables[name] = array
    return variables


def _load_mat_v73(input_path: Path) -> dict[str, np.ndarray]:
    import h5py

    variables: dict[str, np.ndarray] = {}
    with h5py.File(input_path, "r") as f:
        for name in f:
            if name.startswith("#"):
                continue  # HDF5-internal MATLAB bookkeeping (e.g. "#refs#")
            if isinstance(f[name], h5py.Group):
                _warn(f"skipping {name!r}: not numeric (struct/group)")  # 15.5
                continue
            array = np.asarray(f[name]).squeeze()
            if array.ndim == 1 and np.issubdtype(array.dtype, np.complexfloating):
                _warn(f"skipping complex variable {name!r}")
            elif array.ndim == 1 and _is_real_numeric(array.dtype):
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
    _check_paths(input_path, output_path)
    try:
        try:
            variables = _load_mat_v5_or_v7(input_path)
        except NotImplementedError:
            # scipy.io.loadmat's own error for v7.3 files points at h5py; this is that path.
            variables = _load_mat_v73(input_path)
    except Exception as e:  # noqa: BLE001 (scipy/h5py raise ValueError, IndexError, MatReadError, OSError...)
        raise ConversionError(f"couldn't read {input_path} as a MAT file: {e}") from e

    if not variables:
        raise ConversionError(f"no 1-D numeric variables found in {input_path}")

    time_name, time_values, columns = _select_time_and_columns(variables, time_var)
    return _write_parquet(time_name, time_values, columns, output_path)
