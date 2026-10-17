from __future__ import annotations

import gc
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must be set before Qt is imported

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# Property-based tests (app 46.1): "default" is deterministic (same inputs every run, stable xdist
# ids, no .hypothesis/ database); "thorough" is for trying more by hand:
#   HYPOTHESIS_PROFILE=thorough uv run pytest tests/parquet_analyzer/test_property_*.py
# max_examples is set per test with @settings(max_examples=...) (x10 under "thorough": see _examples()
# in each test_property_*.py).
from hypothesis import HealthCheck, settings

settings.register_profile(
    "default", derandomize=True, deadline=None, database=None, print_blob=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.register_profile(
    "thorough", derandomize=False, deadline=None, database=None, print_blob=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))



@pytest.fixture(autouse=True, scope="session")
def _gc_on_main_thread_only():
    """detailed_specification.md 37.2 /: automatic cyclic GC can run on a
    QThreadPool worker and destroy Qt objects off the UI thread (intermittent crash in a
    later, unrelated test). Disable it; `_collect_between_tests` collects on the main thread."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def _collect_between_tests():
    yield
    gc.collect()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:  # same as __main__ (25.3); only possible before the app exists
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture(autouse=True)
def _no_blocking_message_box(monkeypatch):
    """A `QMessageBox` built by hand and run with `exec()` (e.g. `messages.warning_with_button`)
    would wait for a click forever; make it return at once. Tests that care patch
    `messages.warning_with_button` themselves."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)


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


_REAL_PROBES: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _quiet_export_guards(monkeypatch):
    """The export's disk / memory guards (export 57.9.6a) must not read the real machine: plenty of
    room, so a nearly full disk or busy Mac cannot change what a test sees. Tests about the guards
    replace the probes again; `real_export_probes` gives the originals."""
    from parquet_analyzer.core import export_resources as res

    _REAL_PROBES.setdefault("disk_free_bytes", res.disk_free_bytes)
    _REAL_PROBES.setdefault("available_memory_bytes", res.available_memory_bytes)
    monkeypatch.setattr(res, "disk_free_bytes", lambda p: 500 * res.GB)
    monkeypatch.setattr(res, "available_memory_bytes", lambda: 64 * res.GB)


@pytest.fixture
def real_export_probes():
    return dict(_REAL_PROBES)
