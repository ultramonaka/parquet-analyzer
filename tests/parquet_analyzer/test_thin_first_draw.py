"""36.6 A: first draw of a big windowed column via a strided read (feature flag
`big_a_thin_first_draw`), plus thin_column's row-range support."""
from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from parquet_analyzer import features
from parquet_analyzer.core.pyramid import thin_column
from parquet_analyzer.ui import plot_widget as plot_widget_module

from test_windowed_series import _FakeWindowSource, _drain, _make_plot


class _ThinSource(_FakeWindowSource):
    """_FakeWindowSource + thin_job; request_pyramid is held back (as if a build were in flight)
    until `allow_pyramid` is set, so the thin (coarse) stage can be observed."""

    allow_pyramid = False

    def __init__(self, full_array):
        super().__init__(full_array)
        self.thin_calls: list[tuple[int, int, int]] = []

    def thin_job(self, row_start, row_end, target_points):
        self.thin_calls.append((row_start, row_end, target_points))
        stride = max(1, (row_end - row_start) // target_points)
        full = self._full

        def job():
            idx = np.arange(row_start, row_end, stride, dtype=np.int64)
            return idx, full[idx]

        return job

    def request_pyramid(self):
        return super().request_pyramid() if self.allow_pyramid else None


@pytest.fixture
def x_y():
    n = 50_000
    x = np.linspace(1_767_225_600.0, 1_767_226_100.0, n)
    y = np.sin(np.linspace(0, 20 * np.pi, n))
    return x, y


def _flag(monkeypatch, on: bool):
    monkeypatch.setitem(features._EFFECTIVE, "big_a_thin_first_draw", on)
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000)


def test_thin_first_draw_skips_full_window_read_and_is_coarse(qapp, x_y, monkeypatch):
    _flag(monkeypatch, True)
    x, y = x_y
    plot = _make_plot(qapp)
    source = _ThinSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    assert source.thin_calls and not source.calls  # no window() over the visible range
    lo, hi, n = source.thin_calls[-1]
    assert hi - lo > 1_000
    cx, _ = plot._series["a"]["curve"].getData()
    assert 0 < len(cx) <= 2 * n  # ~n (stride is floored)
    assert "a" in plot._coarse_series

    # the pyramid, once built, replaces the coarse thin curve
    gen = plot._series["a"]["generation"]
    source.allow_pyramid = True
    plot._maybe_start_pyramid_build("a", source)
    _drain(qapp)
    assert source.pyramid is not None
    assert not source.calls
    cx2, _ = plot._series["a"]["curve"].getData()
    assert not np.array_equal(cx, cx2)
    assert plot._series["a"]["generation"] > gen


def test_flag_off_uses_full_window_read(qapp, x_y, monkeypatch):
    _flag(monkeypatch, False)
    x, y = x_y
    plot = _make_plot(qapp)
    source = _ThinSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)
    assert source.calls and not source.thin_calls
    assert "a" not in plot._coarse_series


def test_thin_drops_non_finite_x(qapp, x_y, monkeypatch):
    _flag(monkeypatch, True)
    x, y = x_y
    x = x.copy()
    x[::7] = np.nan
    plot = _make_plot(qapp)
    source = _ThinSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    _drain(qapp)
    cx, _ = plot._series["a"]["curve"].getData()
    assert len(cx) > 0 and np.isfinite(cx).all()


def test_real_windowed_column_thin_job(tmp_path):
    from parquet_analyzer.core.column import WindowedColumn
    from parquet_analyzer.core.data_source import ParquetDataSource

    n = 5_000
    path = tmp_path / "w.parquet"
    pq.write_table(pa.table({"t": np.arange(n, dtype=np.float64), "v": np.arange(n, dtype=np.float64) * 2}), path, row_group_size=1_000)
    col = WindowedColumn(ParquetDataSource(path), "v")
    idx, val = col.thin_job(1_000, 4_000, 100)()
    assert idx.min() >= 1_000 and idx.max() < 4_000
    assert np.array_equal(val, idx * 2.0)


def test_thin_column_row_range(tmp_path):
    n = 10_000
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"v": np.arange(n, dtype=np.float64)}), path, row_group_size=1_000)
    idx, val = thin_column(path, "v", 100, 2_500, 7_500)
    assert idx.min() >= 2_500 and idx.max() < 7_500
    assert 90 <= len(idx) <= 110
    assert np.array_equal(val, idx.astype(np.float64))
    assert idx[0] == 2_500 and np.all(np.diff(idx) == 50)
    idx_all, _ = thin_column(path, "v", 100)  # default range: whole column, unchanged behaviour
    assert idx_all[0] == 0 and idx_all[-1] >= n - 100
    assert len(thin_column(path, "v", 100, 5, 5)[0]) == 0
