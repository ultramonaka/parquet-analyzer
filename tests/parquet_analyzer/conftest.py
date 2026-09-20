from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must be set before Qt is imported

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point config's runtime dirs at a tmp_path so tests never touch the
    developer's real cfg/data/log directories (specification.md 3.1/7/8).
    """
    from parquet_analyzer import config

    monkeypatch.setattr(config, "PARQUET_ANALYZER_CFG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "PARQUET_ANALYZER_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "PARQUET_ANALYZER_LOG_DIR", tmp_path / "log")
    return config


@pytest.fixture
def sample_parquet_path(tmp_path):
    """A small synthetic Parquet file, standing in for data/raw/sensor_log_4y.parquet
    (which is gitignored and shouldn't be a test dependency).
    """
    n = 50_000
    rng = np.random.default_rng(0)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + np.arange(n, dtype="int64") * np.timedelta64(10, "ms")
    table = pa.table(
        {
            "time": pa.array(times),
            "a": rng.normal(size=n),
            "b": rng.normal(size=n),
            "c": rng.normal(size=n),
        }
    )
    path = tmp_path / "sample.parquet"
    pq.write_table(table, path)
    return path
