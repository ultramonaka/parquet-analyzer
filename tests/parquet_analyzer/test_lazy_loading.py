from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer.core.column import LazyColumn, MaterializedColumn
from parquet_analyzer.ui.main_window import MainWindow

"""Regression tests for detailed_specification.md 13.5.1 Phase C: load_parquet must
not read a column from disk until it's actually used (plotted, referenced by an
expression, selected for stats, or made the time axis) -- a file with columns
nothing ever touches shouldn't cost anything beyond a small LazyColumn wrapper for
each. test_load_robustness.py/test_fft.py/test_stats.py already cover the
*behavioral* side (still-correct values/results) via the public
MainWindow.variable_values() accessor; these tests specifically assert the
*laziness* itself, at the internal Column level.
"""


def _write_parquet(path, columns: dict, n: int = 200):
    times = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    table = pa.table({"time": pa.array(times), **columns})
    pq.write_table(table, path)


def _is_unmaterialized(column) -> bool:
    return isinstance(column, LazyColumn) and column._array is None


@pytest.fixture
def multi_var_path(tmp_path):
    path = tmp_path / "multi.parquet"
    _write_parquet(
        path,
        {
            "a": np.arange(200, dtype=float),
            "b": np.arange(200, dtype=float) * 2,
            "c": np.arange(200, dtype=float) * 3,
        },
    )
    return path


def test_load_parquet_materializes_only_the_time_column_and_the_navigator_fallback(qapp, isolated_config, multi_var_path):
    """"a" (the file's first raw variable) is still eagerly materialized here, not
    because load_parquet itself reads it, but because _refresh_navigator_overview's
    "nothing plotted yet" fallback does (`main_window.py`'s documented, deliberately
    deferred gap -- see detailed_specification.md 13.5.1's Phase B note). "b"/"c"
    stay lazy since nothing has touched them at all.
    """
    win = MainWindow()
    win.load_parquet(str(multi_var_path))

    assert isinstance(win._file_columns["time"], MaterializedColumn)
    assert not _is_unmaterialized(win._raw_variables["a"])  # navigator overview fallback read it
    assert _is_unmaterialized(win._raw_variables["b"])
    assert _is_unmaterialized(win._raw_variables["c"])
    win.close()


def test_plotting_one_variable_does_not_materialize_the_others(qapp, isolated_config, multi_var_path):
    win = MainWindow()
    win.load_parquet(str(multi_var_path))

    plot = win.plot_grid.plots[0]
    win._on_variable_dropped(plot, "a")

    assert not _is_unmaterialized(win._raw_variables["a"])
    assert _is_unmaterialized(win._raw_variables["b"])
    assert _is_unmaterialized(win._raw_variables["c"])
    win.close()


def test_variable_values_materializes_and_caches(qapp, isolated_config, multi_var_path):
    win = MainWindow()
    win.load_parquet(str(multi_var_path))

    out1 = win.variable_values("a")
    np.testing.assert_array_equal(out1, np.arange(200, dtype=float))
    out2 = win.variable_values("a")
    assert out1 is out2  # cached on the LazyColumn, not re-read
    win.close()


def test_derived_variable_only_materializes_columns_the_expression_references(qapp, isolated_config, multi_var_path):
    win = MainWindow()
    win.load_parquet(str(multi_var_path))

    win._on_add_derived_variable("k", "a * 2")

    assert not _is_unmaterialized(win._raw_variables["a"])  # referenced by the expression
    assert _is_unmaterialized(win._raw_variables["b"])  # never referenced, still lazy
    assert _is_unmaterialized(win._raw_variables["c"])
    np.testing.assert_array_equal(win.variable_values("k"), win.variable_values("a") * 2)
    win.close()


def test_set_time_column_materializes_exactly_the_new_time_column(qapp, isolated_config, multi_var_path):
    win = MainWindow()
    win.load_parquet(str(multi_var_path))

    win.set_time_column("a")

    assert not _is_unmaterialized(win._file_columns["a"])  # now the time axis, needed eagerly
    assert _is_unmaterialized(win._file_columns["b"])
    assert _is_unmaterialized(win._file_columns["c"])
    # the old time column comes back as an ordinary (already-materialized) variable
    assert isinstance(win._file_columns["time"], MaterializedColumn)
    win.close()
