"""Generate sample curve/map CSVs for manually checking the map feature (expressions ch.27).

Both tables are built to be used with the columns of generate_feature_test_parquet.py's
`feature_test.parquet` (sine/cosine range over -1..1), with results that can be checked by eye:

    file              kind   table                                   expression to try -> expected
    ----------------  -----  --------------------------------------  -----------------------------
    sample_curve.csv  curve  y = x**3 at 9 unevenly spaced x in -1..1  sample_curve(sine) -> ~sine**3
    sample_map.csv    map    z = x**2 + y**2, x/y axes in -1..1      sample_map(sine, cosine) -> ~1
                             (uneven axes, denser near 0)

"~": linear/bilinear interpolation between grid points, so the result is close to (not exactly)
the formula between points; on the grid points themselves it is exact. Import them from the
"Maps" window ("Import...", kind auto-detected: 2 columns -> curve, empty top-left cell -> map),
keeping the file name as the map name, then use the names in an expression. The map also has a
3D surface (the "3D" toggle, needs the xyz_3d feature) shaped like a bowl.

Output goes to data/raw/ by default (gitignored, per-machine test data).

Usage:
    uv run python tests/parquet_analyzer/generate_sample_maps.py [--output-dir data/raw]
"""

from __future__ import annotations

import argparse
from pathlib import Path

CURVE_X = [-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0]
MAP_AXIS = [-1.0, -0.6, -0.3, -0.1, 0.0, 0.1, 0.3, 0.6, 1.0]


def curve_rows() -> list[str]:
    return ["x,y"] + [f"{x},{x ** 3:.6g}" for x in CURVE_X]


def map_rows() -> list[str]:
    rows = ["," + ",".join(str(x) for x in MAP_AXIS)]
    for y in MAP_AXIS:
        rows.append(f"{y}," + ",".join(f"{x * x + y * y:.6g}" for x in MAP_AXIS))
    return rows


def write(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name, rows in (("sample_curve.csv", curve_rows()), ("sample_map.csv", map_rows())):
        path = output_dir / name
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        out.append(path)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    for path in write(args.output_dir):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
