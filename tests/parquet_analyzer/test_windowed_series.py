from __future__ import annotations

import time

import numpy as np
import pytest

from parquet_analyzer.core.pyramid import ColumnPyramid
from parquet_analyzer.ui import plot_widget as plot_widget_module
from parquet_analyzer.ui.plot_widget import TimePlotWidget


def _make_plot(qapp, width=900):
    plot = TimePlotWidget("p1")
    plot.resize(width, 400)
    plot.show()
    return plot


def _drain(qapp, iterations=50, delay=0.005):
    for _ in range(iterations):
        qapp.processEvents()
        time.sleep(delay)


class _FakeWindowSource:
    """Stands in for core.column.WindowedColumn: wraps a real array but only
    ever answers window(row_start, row_end), never the whole array, and counts
    calls so tests can assert nothing over-fetched. Also implements the
    pyramid protocol (pyramid/request_pyramid/set_pyramid) so pyramid-wiring
    behavior can be tested without a real Parquet file.
    """

    def __init__(self, full_array: np.ndarray, bucket_rows: int = 100):
        self._full = full_array
        self._bucket_rows = bucket_rows
        self.calls: list[tuple[int, int]] = []
        self._pyramid: ColumnPyramid | None = None
        self.pyramid_build_requests = 0

    def window(self, row_start: int, row_end: int) -> np.ndarray:
        self.calls.append((row_start, row_end))
        return self._full[row_start:row_end]

    @property
    def pyramid(self) -> ColumnPyramid | None:
        return self._pyramid

    def request_pyramid(self):
        self.pyramid_build_requests += 1
        if self._pyramid is not None:
            return None

        def job() -> ColumnPyramid:
            n = len(self._full)
            starts = np.arange(0, n, self._bucket_rows, dtype=np.int64)
            mins = np.array([self._full[s : s + self._bucket_rows].min() for s in starts])
            maxs = np.array([self._full[s : s + self._bucket_rows].max() for s in starts])
            return ColumnPyramid(row_starts=starts, mins=mins, maxs=maxs)

        return job

    def set_pyramid(self, pyramid: ColumnPyramid) -> None:
        self._pyramid = pyramid


@pytest.fixture
def x_y():
    n = 50_000
    x = np.linspace(1_767_225_600.0, 1_767_226_100.0, n)
    y = np.sin(np.linspace(0, 20 * np.pi, n))
    return x, y


def test_add_windowed_series_sets_initial_range_from_true_bounds_not_data(qapp, x_y):
    """The initial view range must come from y_bounds (e.g. footer statistics),
    not from fetching data -- the whole point of a windowed series.
    """
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    x_lo, x_hi = plot.getViewBox().viewRange()[0]
    assert x_lo <= x[0] and x_hi >= x[-1]
    y_lo, y_hi = plot.getViewBox().viewRange()[1]
    assert y_lo <= y.min() and y_hi >= y.max()


def test_windowed_series_redraw_fetches_only_the_visible_range(qapp, x_y):
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)
    source.calls.clear()

    # Zoom into the first ~10% of the range.
    x_lo, x_hi = x[0], x[0] + (x[-1] - x[0]) * 0.1
    plot.setXRange(x_lo, x_hi, padding=0)
    _drain(qapp)

    assert source.calls, "no fetch happened"
    for row_start, row_end in source.calls:
        assert row_end - row_start < len(y) * 0.5  # never the whole array


def test_windowed_series_curve_shows_correct_data(qapp, x_y):
    x, y = x_y
    plot = _make_plot(qapp)
    plot.resize(2000, 400)  # wide enough that n_out > visible points, no downsampling
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    x_lo, x_hi = x[100], x[200]
    plot.setXRange(x_lo, x_hi, padding=0)
    _drain(qapp)

    displayed_x, displayed_y = plot._series["a"]["curve"].getData()
    assert len(displayed_x) > 0
    # every displayed y value should be a real sample from the source array
    for yv in displayed_y[:5]:
        assert np.any(np.isclose(y, yv))


def test_windowed_series_data_returns_none_for_y(qapp, x_y):
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    returned_x, returned_y, _color = plot.series_data("a")
    assert returned_y is None
    np.testing.assert_array_equal(returned_x, x)


def test_series_source_roundtrips_for_windowed_and_none_for_resident(qapp, x_y):
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("windowed", x, source, (0.0, 1.0))
    plot.add_series("resident", x, y)
    qapp.processEvents()
    _drain(qapp)

    result = plot.series_source("windowed")
    assert result is not None
    assert result[0] is source
    assert result[1] == (0.0, 1.0)

    assert plot.series_source("resident") is None


def test_go_home_uses_true_bounds_for_windowed_series(qapp, x_y):
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (-100.0, 100.0))  # deliberately wider than real data
    qapp.processEvents()
    _drain(qapp)

    plot.setYRange(-1, 1, padding=0)  # zoom in first
    plot.go_home()

    # setRange() applies its own 2% padding on top of true bounds, so allow for that.
    y_lo, y_hi = plot.getViewBox().viewRange()[1]
    assert y_lo == pytest.approx(-100.0, abs=10.0)
    assert y_hi == pytest.approx(100.0, abs=10.0)


def test_go_home_combines_resident_and_windowed_series_without_one_polluting_the_other(qapp, x_y):
    """Regression guard: an all-NaN/empty series' own (0.0, 1.0) fallback must not
    narrow the combined range of a *different*, real-data series when go_home()
    aggregates bounds across mixed resident + windowed series.
    """
    x, y = x_y
    plot = _make_plot(qapp)
    empty_series_y = np.full_like(y, np.nan)
    plot.add_series("empty", x, empty_series_y)
    source = _FakeWindowSource(y * 1000)  # a real, large-magnitude windowed series
    plot.add_windowed_series("big", x, source, (float((y * 1000).min()), float((y * 1000).max())))
    qapp.processEvents()
    _drain(qapp)

    plot.go_home()
    y_lo, y_hi = plot.getViewBox().viewRange()[1]
    assert y_lo < -500  # the "big" series' true range must survive, not get pulled toward 0..1
    assert y_hi > 500


def test_transfer_series_preserves_windowed_source(qapp, x_y):
    x, y = x_y
    plot1 = _make_plot(qapp)
    plot2 = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot1.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    plot1.transfer_series("a", plot2)

    assert "a" not in plot1.series_names()
    assert "a" in plot2.series_names()
    result = plot2.series_source("a")
    assert result is not None
    assert result[0] is source
    # the transferred series still redraws correctly via the windowed path
    _drain(qapp)
    displayed_x, _displayed_y = plot2._series["a"]["curve"].getData()
    assert len(displayed_x) > 0


def test_stale_windowed_fetch_is_discarded_like_stale_downsample(qapp, x_y):
    """Same per-series generation-counter discipline that protects LTTB results
    must also protect windowed fetches -- a fetch started before a newer
    pan/zoom must not clobber the curve with outdated data once it lands.
    """
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    # Two rapid range changes in a row; only the second's result should win.
    plot.setXRange(x[0], x[0] + (x[-1] - x[0]) * 0.1, padding=0)
    plot.setXRange(x[0], x[0] + (x[-1] - x[0]) * 0.5, padding=0)
    _drain(qapp)

    entry = plot._series["a"]
    assert entry["generation"] >= 2


def test_below_pyramid_threshold_never_requests_a_pyramid(qapp, x_y, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000_000)
    x, y = x_y  # 50,000 rows, well under the threshold
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    assert source.pyramid_build_requests == 0


def test_above_threshold_without_a_pyramid_falls_back_to_real_fetch_and_requests_one(qapp, x_y, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000)
    x, y = x_y  # 50,000 rows, over this lowered threshold
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    assert source.pyramid_build_requests >= 1  # a build was requested
    assert source.calls  # but this redraw still used a real fetch (no pyramid yet)


def test_above_threshold_with_a_ready_pyramid_skips_the_real_fetch(qapp, x_y, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000)
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    # Pre-build the pyramid before ever adding the series.
    job = source.request_pyramid()
    source.set_pyramid(job())

    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    assert not source.calls  # no real fetch -- the pyramid satisfied the redraw
    curve_x, curve_y = plot._series["a"]["curve"].getData()
    assert len(curve_x) > 0


def test_pyramid_becomes_ready_and_a_later_redraw_uses_it(qapp, x_y, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000)
    x, y = x_y
    plot = _make_plot(qapp)
    source = _FakeWindowSource(y)
    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)  # first redraw: real fetch + background pyramid build + _on_pyramid_ready redraw

    assert source.pyramid is not None  # the background build finished
    calls_after_settle = len(source.calls)

    # A further redraw at the same (still over-threshold) zoom level should now
    # use the pyramid exclusively -- no additional real fetch.
    plot.setXRange(x[0], x[-1], padding=0)
    _drain(qapp)
    assert len(source.calls) == calls_after_settle


def test_downsample_disabled_bypasses_pyramid_even_above_threshold(qapp, x_y, monkeypatch):
    monkeypatch.setattr(plot_widget_module, "_PYRAMID_PREFERRED_ROW_THRESHOLD", 1_000)
    x, y = x_y
    plot = _make_plot(qapp)
    plot.set_downsample_enabled(False)
    source = _FakeWindowSource(y)
    job = source.request_pyramid()
    source.set_pyramid(job())  # a pyramid exists...

    plot.add_windowed_series("a", x, source, (float(y.min()), float(y.max())))
    qapp.processEvents()
    _drain(qapp)

    # ...but downsampling is off, so the real (full-precision) fetch must still
    # be used, not the pyramid's coarse envelope.
    assert source.calls
