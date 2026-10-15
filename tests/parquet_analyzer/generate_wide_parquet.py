"""Generate a wide, very long Parquet file for the 1-billion-row measurement (data.ja.md 58.2).

Not a pytest test (see test_generate_wide_parquet.py for a small correctness check of the same
logic). A standalone generator; the output belongs in ``data/raw/`` (gitignored).

Memory is constant in the number of rows: one row group is built with vectorised numpy and
appended with ``pyarrow.parquet.ParquetWriter``, so 1e9 rows need no more RAM than 1e6.

Columns
-------
``time``   datetime[ns] (naive), ``2024-01-01 00:00:00`` + ``row * interval_ms``, plus
           ``--gaps`` jumps of ``GAP_SECONDS`` (30 s) spread evenly through the file
           (after row ``k * rows / (gaps + 1)``, k = 1..gaps).
``sig_NN`` ``--columns`` signals (NN zero-padded), float32 by default. All values are quantised
           to a power-of-two step (sine/cosine 2^-10, noise 2^-6, ramp 2^-8) so that they are
           exactly representable in float32 and Parquet's dictionary encoding + zstd shrink
           them well (about 5-8x), which keeps a 1e9-row x 20-column file near 25-40 GB.
           ``--values full`` (58.5.1) skips the quantisation and adds Gaussian noise (sigma 1e-6,
           seeded by (seed, start), its own stream) to every signal, so the mantissas are random
           and zstd barely compresses them: a realistic size for measuring disk reads.

Known relations (tolerances come from the quantisation; t = seconds since the first row):

    sig_00 = sine, period 10 s            (needs --columns >= 1)
    sig_01 = cosine, period 10 s          sig_00**2 + sig_01**2 = 1  (abs error < 2e-3)
    sig_02 = sig_00 + 10                  sig_02 - sig_00 = 10       (exact)

With ``--values full`` the tolerances are widened to ``FULL_TOLERANCE`` (1e-4): the noise is
1e-6 per value, and float32 keeps about 1e-6 absolute precision near 10.

The remaining signals (sig_03 ...) cycle through five kinds, in this order:
    ramp     sawtooth rising 0.01..0.04 per second (+ column offset), wrapping at 100
    noise    Gaussian, sigma 0.5 (seeded per row group)
    steps    integer level 0..10 that changes every 5000+ rows
    counter  integer counter 0..1023, +1 every 10 rows
    sine     sine, period 5+column seconds, amplitude 1..3

Usage:
    uv run python tests/parquet_analyzer/generate_wide_parquet.py \\
        --output data/raw/wide_100m_f32.parquet --rows 1e8
    uv run python tests/parquet_analyzer/generate_wide_parquet.py \\
        --output data/raw/wide_1b_f32.parquet --rows 1e9 --columns 20
    # 58.5.1: 5e8 rows x 25 float64 that zstd barely compresses (about 100 GB)
    uv run python tests/parquet_analyzer/generate_wide_parquet.py \\
        --output data/raw/wide_500m_f64_full.parquet --rows 5e8 --columns 25 --dtype float64 --values full

Before writing everything it estimates the final size from the first row group and refuses to
continue when the free space is below estimate + 10 GB (``--force`` overrides).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

GAP_SECONDS = 30
FULL_NOISE_SIGMA = 1e-6  # --values full: added to every signal
FULL_TOLERANCE = 1e-4  # the known-relation tolerance for --values full (the tests use it)
VALUES = ("quantised", "full")
DISK_MARGIN_BYTES = 10 * 1000**3
BASE_TIME = np.datetime64("2024-01-01T00:00:00", "ns")
_KINDS = ("ramp", "noise", "steps", "counter", "sine")
_COMPRESSION = {"zstd": "zstd", "snappy": "snappy", "none": "NONE"}


class DiskSpaceError(RuntimeError):
    """The estimated output does not fit in the free space (58.2)."""


def parse_rows(text: str) -> int:
    """'1e9', '100000000', '1_000_000' -> int."""
    value = Decimal(text.replace("_", "").strip())
    if value != value.to_integral_value() or value <= 0:
        raise ValueError(f"--rows must be a positive integer, got {text!r}")
    return int(value)


def column_names(columns: int) -> list[str]:
    width = max(2, len(str(max(columns - 1, 0))))
    return [f"sig_{j:0{width}d}" for j in range(columns)]


def gap_rows(rows: int, gaps: int) -> np.ndarray:
    """Row numbers k where a gap is inserted before row k (strictly inside the file)."""
    if gaps <= 0:
        return np.empty(0, dtype=np.int64)
    pos = (np.arange(1, gaps + 1, dtype=np.int64) * rows) // (gaps + 1)
    return np.unique(pos[(pos > 0) & (pos < rows)])


def _q(x: np.ndarray, bits: int) -> np.ndarray:
    """Quantise to a multiple of 2**-bits (exact in float32 and float64)."""
    step = float(1 << bits)
    return np.round(x * step) / step


def _signal(j: int, idx: np.ndarray, t_s: np.ndarray, rng: np.random.Generator,
            values: str = "quantised", noise_rng: np.random.Generator | None = None) -> np.ndarray:
    """Signal column j. ``values="full"`` returns the unquantised value plus noise from
    ``noise_rng``; ``"quantised"`` (the default) is the original output, byte for byte."""
    if values == "full":
        x = _signal_exact(j, idx, t_s, rng)
        return x + noise_rng.standard_normal(len(idx)) * FULL_NOISE_SIGMA
    return _signal_quantised(j, idx, t_s, rng)


def _signal_exact(j: int, idx: np.ndarray, t_s: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The signal before quantisation (the ``full`` path); the shapes are those of the quantised path."""
    if j == 0:
        return np.sin(2 * np.pi * t_s / 10.0)
    if j == 1:
        return np.cos(2 * np.pi * t_s / 10.0)
    if j == 2:
        return np.sin(2 * np.pi * t_s / 10.0) + 10.0
    kind = _KINDS[(j - 3) % len(_KINDS)]
    if kind == "ramp":
        return np.mod(t_s * 0.01 * (1 + j % 4) + j, 100.0)
    if kind == "noise":
        return rng.standard_normal(len(idx)) * 0.5
    if kind == "steps":
        return ((idx // (5000 + 37 * j)) * 7 + j) % 11 + 0.0
    if kind == "counter":
        return (idx // 10) % 1024 + 0.0
    return (1 + j % 3) * np.sin(2 * np.pi * t_s / (5.0 + j))


def _signal_quantised(j: int, idx: np.ndarray, t_s: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if j == 0:
        return _q(np.sin(2 * np.pi * t_s / 10.0), 10)
    if j == 1:
        return _q(np.cos(2 * np.pi * t_s / 10.0), 10)
    if j == 2:
        return _q(np.sin(2 * np.pi * t_s / 10.0), 10) + 10.0
    kind = _KINDS[(j - 3) % len(_KINDS)]
    if kind == "ramp":
        return _q(np.mod(t_s * 0.01 * (1 + j % 4) + j, 100.0), 8)
    if kind == "noise":
        return _q(rng.standard_normal(len(idx)) * 0.5, 6)
    if kind == "steps":
        return ((idx // (5000 + 37 * j)) * 7 + j) % 11 + 0.0
    if kind == "counter":
        return (idx // 10) % 1024 + 0.0
    return _q((1 + j % 3) * np.sin(2 * np.pi * t_s / (5.0 + j)), 10)


def build_row_group(start: int, count: int, *, rows: int, columns: int, dtype: str, interval_ms: float,
                    gaps: int, seed: int, values: str = "quantised") -> pa.Table:
    """Rows [start, start + count) of the file as an Arrow table (deterministic for the same
    arguments; the only randomness, the noise, is seeded by (seed, start))."""
    if values not in VALUES:
        raise ValueError(f"values must be one of {VALUES}, got {values!r}")
    idx = np.arange(start, start + count, dtype=np.int64)
    interval_ns = round(interval_ms * 1e6)
    offs = idx * interval_ns + np.searchsorted(gap_rows(rows, gaps), idx, side="right") * (GAP_SECONDS * 10**9)
    time_col = BASE_TIME + offs.astype("timedelta64[ns]")
    t_s = idx * (interval_ms / 1000.0)
    rng = np.random.default_rng([seed, start])
    noise_rng = np.random.default_rng([seed, start, 1])  # --values full only; separate stream
    np_dtype = np.float32 if dtype == "float32" else np.float64
    arrays = [pa.array(time_col, type=pa.timestamp("ns"))]
    for j in range(columns):
        arrays.append(pa.array(_signal(j, idx, t_s, rng, values, noise_rng).astype(np_dtype)))
    return pa.Table.from_arrays(arrays, names=["time", *column_names(columns)])


def check_disk(estimate_bytes: float, free_bytes: float, force: bool = False,
               margin: int = DISK_MARGIN_BYTES) -> None:
    """Raise `DiskSpaceError` when free space < estimate + margin (unless `force`)."""
    if not force and free_bytes < estimate_bytes + margin:
        raise DiskSpaceError(
            f"estimated output {estimate_bytes / 1e9:.1f} GB + {margin / 1e9:.0f} GB margin does not fit in "
            f"{free_bytes / 1e9:.1f} GB free (use --force to continue anyway)"
        )


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path.resolve().parent).free


def generate(output: str | Path, rows: int, *, columns: int = 20, dtype: str = "float32",
             row_group_rows: int = 1_000_000, compression: str = "zstd", interval_ms: float = 1,
             gaps: int = 2, seed: int = 0, force: bool = False,
             free_space_fn: Callable[[Path], int] = _free_bytes, progress: bool = True,
             values: str = "quantised") -> dict:
    """Write the file; returns {'rows', 'size_bytes', 'seconds', 'estimated_bytes'}.
    Raises `DiskSpaceError` (partial file removed) when the first row group predicts a file that
    does not fit. `free_space_fn(path)` is injectable for tests."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    names = ["time", *column_names(columns)]
    np_type = pa.float32() if dtype == "float32" else pa.float64()
    schema = pa.schema([("time", pa.timestamp("ns")), *[(n, np_type) for n in names[1:]]])
    t0 = time.monotonic()
    last_print = t0
    done = 0
    estimate = 0.0
    if values not in VALUES:
        raise ValueError(f"values must be one of {VALUES}, got {values!r}")
    kwargs = dict(rows=rows, columns=columns, dtype=dtype, interval_ms=interval_ms, gaps=gaps, seed=seed,
                  values=values)
    sink = pa.OSFile(str(output), "wb")
    try:
        with pq.ParquetWriter(sink, schema, compression=_COMPRESSION[compression]) as writer:
            while done < rows:
                n = min(row_group_rows, rows - done)
                writer.write_table(build_row_group(done, n, **kwargs), row_group_size=n)
                done += n
                if estimate == 0.0:
                    estimate = sink.tell() / done * rows  # size of the first group, scaled
                    check_disk(estimate, free_space_fn(output), force)
                now = time.monotonic()
                if progress and (now - last_print >= 10 or done == rows):
                    last_print = now
                    el = now - t0
                    eta = el / done * (rows - done)
                    print(f"{done:,}/{rows:,} rows ({done / rows:6.1%})  elapsed {el:,.0f}s  ETA {eta:,.0f}s  "
                          f"written {sink.tell() / 1e9:.2f} GB", file=sys.stderr, flush=True)
    except DiskSpaceError:
        sink.close()
        output.unlink(missing_ok=True)  # do not leave a useless partial file behind
        raise
    except BaseException:
        sink.close()
        raise
    sink.close()
    return {"rows": rows, "size_bytes": os.path.getsize(output), "seconds": time.monotonic() - t0,
            "estimated_bytes": estimate}


def main(argv: list[str] | None = None, free_space_fn: Callable[[Path], int] = _free_bytes) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", required=True)
    p.add_argument("--rows", type=parse_rows, required=True, help="e.g. 100000000 or 1e9")
    p.add_argument("--columns", type=int, default=20, help="signal columns, excluding time (default 20)")
    p.add_argument("--dtype", choices=("float32", "float64"), default="float32")
    p.add_argument("--row-group-rows", type=int, default=1_000_000)
    p.add_argument("--compression", choices=tuple(_COMPRESSION), default="zstd")
    p.add_argument("--interval-ms", type=float, default=1)
    p.add_argument("--gaps", type=int, default=2, help="number of 30 s time jumps (default 2)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--values", choices=VALUES, default="quantised",
                   help="quantised (default): power-of-two steps, compresses well; "
                        "full: unquantised + sigma 1e-6 noise, barely compresses (58.5.1)")
    p.add_argument("--force", action="store_true", help="ignore the free-disk check")
    a = p.parse_args(argv)
    try:
        info = generate(a.output, a.rows, columns=a.columns, dtype=a.dtype, row_group_rows=a.row_group_rows,
                        compression=a.compression, interval_ms=a.interval_ms, gaps=a.gaps, seed=a.seed,
                        force=a.force, free_space_fn=free_space_fn, values=a.values)
    except DiskSpaceError as exc:
        print(f"refusing to continue: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {a.output}: {info['rows']:,} rows, {info['size_bytes'] / 1e9:.2f} GB, "
          f"{info['seconds']:.0f} s ({info['rows'] / info['seconds']:,.0f} rows/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
