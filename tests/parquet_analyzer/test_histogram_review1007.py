"""Review fixes of 2026-10-07 (analysis.ja.md 42.9): numeric time axis = UTC offset 0,
one centred bin for a constant variable, HIST_CHUNK_ROWS."""

from __future__ import annotations

import time

import numpy as np
import pytest

from parquet_analyzer.core import histogram as H
from parquet_analyzer.core.histogram import HistogramSettings, bucket_edges, compute_histogram
from parquet_analyzer.core.histogram_export import build_columns


@pytest.fixture
def tz(monkeypatch):
    def set_tz(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_tz
    monkeypatch.undo()
    time.tzset()


def run(v, t, time_is_datetime=True, chunk_rows=None, **kw):
    return compute_histogram(
        lambda nm, a, b: v[a:b], lambda a, b: t[a:b], ["v"], HistogramSettings(**kw), 0, len(t),
        chunk_rows=chunk_rows, time_is_datetime=time_is_datetime,
    )


def test_chunk_rows_constant():
    assert H.HIST_CHUNK_ROWS == 1_000_000


def test_numeric_axis_buckets_use_utc_offset_zero_in_tokyo(tz):
    tz("Asia/Tokyo")
    t = np.arange(0.0, 3 * 86400.0, 600.0)
    e = bucket_edges(t[0], t[-1], 86400, 0, is_datetime=False)
    np.testing.assert_array_equal(e, [0.0, 86400.0, 172800.0, 259200.0])
    # a datetime axis in the same zone still follows local midnight
    assert bucket_edges(t[0], t[-1], 86400, 0)[0] == -9 * 3600.0
    r = run(np.ones(len(t)), t, time_is_datetime=False, mode="bucket", bucket_s=86400.0, agg="sum")
    assert r.bucket_count == 3
    np.testing.assert_array_equal(r.bucket_start, [0.0, 86400.0, 172800.0])
    cols, _ = build_columns(r, False)
    assert cols["window_start"][0] == 0.0 and cols["window_end"][-1] == 259200.0


def test_numeric_axis_half_hour_zone_does_not_shift_hourly_buckets(tz):
    tz("Asia/Kolkata")
    e = bucket_edges(0.0, 7200.0, 3600, 0, is_datetime=False)
    np.testing.assert_array_equal(e, np.arange(25) * 3600.0)  # from axis 0, not :30


def test_numeric_axis_origin_and_negative_start():
    e = bucket_edges(-100.0, 90000.0, 86400, 6 * 3600, is_datetime=False)
    np.testing.assert_array_equal(e, [-64800.0, 21600.0, 108000.0])


@pytest.mark.parametrize("mode", ["plain", "bucket"])
def test_constant_value_is_one_centred_bin(mode):
    t = np.arange(0.0, 3 * 86400.0, 600.0)
    r = run(np.ones(len(t)), t, time_is_datetime=False, mode=mode, bucket_s=86400.0, agg="mean")
    assert len(r.edges) == 2 and len(r.variables[0].counts) == 1
    assert r.edges[0] < 1.0 < r.edges[1]
    assert (r.edges[0] + r.edges[1]) / 2 == pytest.approx(1.0)
    assert r.variables[0].counts[0] == (len(t) if mode == "plain" else 3)
    assert r.variables[0].mean == pytest.approx(1.0)


@pytest.mark.parametrize("mode", ["plain", "bucket"])
def test_constant_value_one_bin_even_with_bins_set_and_big_value(mode):
    t = np.arange(0.0, 3 * 86400.0, 600.0)
    r = run(np.full(len(t), 1e20), t, time_is_datetime=False, mode=mode, bins=50, bucket_s=86400.0, agg="max")
    assert len(r.edges) == 2
    assert np.isfinite(r.edges).all() and r.edges[1] > r.edges[0]
    assert r.edges[0] < 1e20 < r.edges[1]


def test_constant_zero_width_rule():
    lo, hi = H._span_range(5.0, 5.0)
    assert (lo, hi) == (4.5, 5.5)
    lo, hi = H._span_range(1e12, 1e12)
    assert hi - lo == pytest.approx(2e3)  # |v| * 1e-9 * 2


def test_chunk_boundary_results_do_not_depend_on_chunk_rows():
    rng = np.random.default_rng(0)
    v = rng.normal(size=5000)
    t = np.arange(5000.0)
    a = run(v, t, chunk_rows=700)
    b = run(v, t, chunk_rows=None)
    np.testing.assert_array_equal(a.variables[0].counts, b.variables[0].counts)
