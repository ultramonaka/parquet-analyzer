"""generate_sample_maps.py writes tables the app imports with auto-detection, with the
documented values on the grid points."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from generate_sample_maps import write  # noqa: E402

from parquet_analyzer.io.maps import parse_table_csv  # noqa: E402


def test_sample_tables_parse_with_documented_values(tmp_path):
    curve_path, map_path = write(tmp_path)

    curve = parse_table_csv(curve_path, "sample_curve")
    assert curve.kind == "curve"
    np.testing.assert_allclose(curve.y, curve.x ** 3, atol=1e-6)

    table = parse_table_csv(map_path, "sample_map")
    assert table.kind == "map"
    gx, gy = np.meshgrid(table.x, table.y)
    np.testing.assert_allclose(table.z, gx ** 2 + gy ** 2, atol=1e-6)
