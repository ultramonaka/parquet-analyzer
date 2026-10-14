"""Whole-range statistics of windowed columns are aggregated per window in the worker
(data 5.46 / 53; test plan 53.3).

With `eager_load_limit_mb = 0` every column is a `WindowedColumn`. A stats request must then

  * give the same numbers as `basic_stats` over the same rows (count/NaN/min/max/median exact,
    mean/std to rtol 1e-9), also over several chunks and across block borders,
  * never read through a `WindowedColumn` on the UI thread,
  * read through `read_uncached` (so the block cache is not flushed) and only inside the range,
  * report a failed read per variable (the others still show values), exactly once, with
    the busy flag cleared,
  * drop its results when another file is opened meanwhile (app 46.5 S-5).
"""
from __future__ import annotations

import threading

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget
from waiting import wait_until

from parquet_analyzer.core.analysis import basic_stats, chunked_stats
from parquet_analyzer.core.column import WindowedColumn
from parquet_analyzer.core.data_source import ParquetDataSource
from parquet_analyzer.ui import main_window as main_window_module
from parquet_analyzer.ui.main_window import MainWindow
from parquet_analyzer.ui.stats_dialog import StatsDialog

N = 350_000  # 3.5 blocks of the default 100,000-row block size
TIMEOUT = 60.0  # upper bound only; waits return as soon as the condition holds


def _write(path, seed: int, n: int = N):
    rng = np.random.default_rng(seed)
    base = np.datetime64("2026-01-01", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    b = rng.normal(size=n) * 5 + 3
    b[rng.integers(0, n, 500)] = np.nan
    b[rng.integers(0, n, 50)] = np.inf
    b[rng.integers(0, n, 50)] = -np.inf
    pq.write_table(
        pa.table({
            "time": pa.array(base),
            "a": np.sin(np.arange(n) / 700.0) * 10 + rng.normal(size=n),
            "b": b,
            "c": np.arange(n, dtype="int64"),
        }),
        path,
        row_group_size=60_000,
    )
    return path


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("wstats")
    return [str(_write(d / "one.parquet", 1)), str(_write(d / "two.parquet", 2, 120_000))]


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def win(qapp, isolated_config, files, monkeypatch):
    for w in QApplication.topLevelWidgets():  # windows left open by earlier tests of this process
        if isinstance(w, MainWindow):
            w.close()
            w.deleteLater()
    w = MainWindow()
    w.settings.eager_load_limit_mb = 0
    w.resize(1000, 800)
    w.show()
    qapp.processEvents()
    assert w.load_parquet(files[0])
    assert isinstance(w._raw_variables["a"], WindowedColumn)
    yield w
    w.close()
    wait_until(lambda: not w._stats_workers, 5.0)


def _request(win, names, row_range, monkeypatch):
    class _Item:
        def __init__(self, n):
            self._n = n

        def text(self):
            return self._n

    monkeypatch.setattr(win.variable_panel, "selectedItems", lambda: [_Item(n) for n in names])
    monkeypatch.setattr(win, "_visible_row_range", lambda plot=None: row_range)
    win._on_stats_requested()


def _dialogs(win):
    return win.findChildren(StatsDialog)


def _rows(dialog):
    table = dialog.findChild(QTableWidget)
    return {
        table.item(r, 0).text(): [table.item(r, c).text() if table.item(r, c) else "" for c in range(1, 8)]
        for r in range(table.rowCount())
    }


def _wait_done(win):
    assert wait_until(lambda: not win._stats_workers and "stats" not in win._busy_sources, TIMEOUT)


def _capture_results(monkeypatch):
    got: list[dict] = []
    orig = MainWindow._show_stats_dialog

    def show(self, batch):
        got.append(dict(batch["results"]))
        return orig(self, batch)

    monkeypatch.setattr(MainWindow, "_show_stats_dialog", show)
    return got


def _small_chunks(monkeypatch, chunk_rows: int):
    orig = chunked_stats
    monkeypatch.setattr(
        main_window_module, "chunked_stats",
        lambda window, a, b: orig(window, a, b, chunk_rows=chunk_rows),
    )


RANGES = {
    "whole": (0, N),
    "across_blocks": (73_000, 258_001),
    "block_border_exact": (100_000, 200_000),
    "tiny": (99_990, 100_010),
    "single_row": (150_000, 150_001),
}


@pytest.mark.parametrize("chunk_rows", [4_000_000, 30_000], ids=["one_chunk", "many_chunks"])
@pytest.mark.parametrize("range_name", list(RANGES), ids=list(RANGES))
def test_matches_basic_stats(win, monkeypatch, files, range_name, chunk_rows):
    rs, re_ = RANGES[range_name]
    _small_chunks(monkeypatch, chunk_rows)
    got = _capture_results(monkeypatch)
    _request(win, ["a", "b", "c"], (rs, re_), monkeypatch)
    _wait_done(win)
    assert len(got) == 1
    ref = pq.read_table(files[0])
    for name in ("a", "b", "c"):
        expect = basic_stats(ref[name].to_numpy().astype(np.float64)[rs:re_])
        s = got[0][name]
        assert "error" not in s, s
        for k in ("count", "nan_count", "min", "max", "median"):
            assert s[k] == expect[k] or (np.isnan(s[k]) and np.isnan(expect[k])), (name, k, s[k], expect[k])
        for k in ("mean", "std"):
            np.testing.assert_allclose(s[k], expect[k], rtol=1e-9, err_msg=f"{name}.{k}")


def test_no_windowed_column_read_on_the_ui_thread(win, monkeypatch):
    main = threading.get_ident()
    threads: list[tuple[str, int]] = []
    for cls, attr in ((WindowedColumn, "window"), (WindowedColumn, "read_uncached"), (WindowedColumn, "values"),
                      (ParquetDataSource, "read_window"), (ParquetDataSource, "read_columns")):
        orig = getattr(cls, attr)

        def wrapped(self, *a, _orig=orig, _attr=attr, **k):
            threads.append((_attr, threading.get_ident()))
            return _orig(self, *a, **k)

        monkeypatch.setattr(cls, attr, wrapped)
    _request(win, ["a", "b"], (0, N), monkeypatch)
    _wait_done(win)
    assert any(a == "read_columns" for a, _ in threads), "the worker did not read the data"
    on_ui = [a for a, t in threads if t == main]
    assert not on_ui, f"read on the UI thread: {on_ui}"


def test_reads_stay_inside_the_range_and_skip_the_block_cache(win, monkeypatch):
    ds = win._data_source
    ds.read_window(["a"], 0, 10)  # something in the cache so we can see it stay untouched
    for row in (123_456, 234_566):  # the range label reads the time axis at both ends: warm those blocks
        win._time_values[row]
    info_before = ds._read_block_cached.cache_info()
    spans: list[tuple[int, int]] = []
    orig_rc = ParquetDataSource.read_columns
    monkeypatch.setattr(
        ParquetDataSource, "read_columns",
        lambda self, cols, s=0, e=None: (spans.append((s, e)) if cols == ["b"] else None, orig_rc(self, cols, s, e))[1],
    )  # only column "b": the range label reads two "time" blocks around the range ends
    _small_chunks(monkeypatch, 20_000)
    _request(win, ["b"], (123_456, 234_567), monkeypatch)
    _wait_done(win)
    assert spans and all(123_456 <= s and e <= 234_567 for s, e in spans), spans
    assert len(spans) > 1, "expected several chunks"
    info_after = ds._read_block_cached.cache_info()  # hits may grow (range label); misses/size may not
    assert (info_after.misses, info_after.currsize) == (info_before.misses, info_before.currsize), (
        "stats went through the block cache"
    )


def test_read_uncached_edge_cases(win):
    col = win._raw_variables["c"]
    assert len(col.read_uncached(5, 5)) == 0
    assert len(col.read_uncached(9, 5)) == 0
    np.testing.assert_array_equal(col.read_uncached(99_995, 100_005), np.arange(99_995, 100_005, dtype=np.float64))
    np.testing.assert_array_equal(col.read_uncached(10, 20), col.window(10, 20))
    assert col.read_uncached(N - 3, N + 1000).tolist() == [N - 3.0, N - 2.0, N - 1.0]


def test_failed_read_is_reported_for_that_variable_only(win, monkeypatch):
    orig = WindowedColumn.read_uncached

    def read_uncached(self, s, e):
        if self.name == "b":
            raise OSError("disk went away")
        return orig(self, s, e)

    monkeypatch.setattr(WindowedColumn, "read_uncached", read_uncached)
    finished = []
    orig_fin = MainWindow._on_stats_finished
    monkeypatch.setattr(
        MainWindow, "_on_stats_finished",
        lambda self, name, stats, batch, generation=None: (
            finished.append(name), orig_fin(self, name, stats, batch, generation))[1],
    )
    _request(win, ["a", "b"], (0, N), monkeypatch)
    _wait_done(win)
    assert sorted(finished) == ["a", "b"]  # each answered exactly once
    dialogs = _dialogs(win)
    assert len(dialogs) == 1
    rows = _rows(dialogs[0])
    assert rows["a"][0] == str(N)
    assert "OSError: disk went away" in rows["b"][0]
    assert not win._stats_workers and "stats" not in win._busy_sources


def test_file_switch_during_computation_drops_the_result(win, files, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    orig = WindowedColumn.read_uncached

    def gated(self, s, e):
        entered.set()
        assert release.wait(TIMEOUT)
        return orig(self, s, e)

    monkeypatch.setattr(WindowedColumn, "read_uncached", gated)
    _request(win, ["a"], (0, N), monkeypatch)
    assert wait_until(entered.is_set, TIMEOUT)
    assert "stats" in win._busy_sources
    try:
        assert win.load_parquet(files[1])
    finally:
        release.set()
    _wait_done(win)
    assert _dialogs(win) == []
