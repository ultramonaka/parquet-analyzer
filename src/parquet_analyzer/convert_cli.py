"""CLI entry point for tools/convert_to_parquet.sh — see core/convert.py for the
actual conversion logic. Kept separate from __main__.py (the GUI entry point) since
this has its own argument parsing and doesn't touch Qt at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.convert import ConversionError, convert_mat_to_parquet, convert_mdf_to_parquet

_MDF_EXTENSIONS = {".mf4", ".mdf", ".dat"}
_MAT_EXTENSIONS = {".mat"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="convert_to_parquet",
        description="Convert an MDF (.mf4/.mdf/.dat) or MATLAB (.mat) measurement file to Parquet.",
    )
    parser.add_argument("input", type=Path, help="source .mf4/.mdf/.dat or .mat file")
    parser.add_argument(
        "output", type=Path, nargs="?", default=None, help="destination .parquet file (default: input with .parquet extension)"
    )
    parser.add_argument(
        "--time-var",
        default=None,
        help="MAT files only: name of the variable to use as the time axis (auto-detected if omitted)",
    )
    parser.add_argument(
        "--raster",
        type=float,
        default=None,
        help="MDF files only: resampling interval in seconds shared across all channels "
        "(default: asammdf's own choice, the union of every channel's timestamps)",
    )
    args = parser.parse_args(argv)

    input_path: Path = args.input
    output_path: Path = args.output or input_path.with_suffix(".parquet")
    suffix = input_path.suffix.lower()

    try:
        if suffix in _MDF_EXTENSIONS:
            result = convert_mdf_to_parquet(input_path, output_path, raster=args.raster)
        elif suffix in _MAT_EXTENSIONS:
            result = convert_mat_to_parquet(input_path, output_path, time_var=args.time_var)
        else:
            print(
                f"error: unrecognized extension {suffix!r} (expected one of "
                f"{sorted(_MDF_EXTENSIONS | _MAT_EXTENSIONS)})",
                file=sys.stderr,
            )
            return 1
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"wrote {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
