"""Shared path definitions for parquet_analyzer and other projects that import this module.

Kept dependency-free (stdlib only) so any project can import it via PYTHONPATH
(e.g. `from parquet_analyzer import config`) without pulling in GUI/plotting dependencies
- `parquet_analyzer/__init__.py` only defines `__version__`, so importing this submodule
alone doesn't drag in PySide6/pyqtgraph/polars.
"""

import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    if env := os.environ.get("PARQUET_ANALYZER_ROOT"):
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


REPO_ROOT = _repo_root()

CFG_DIR = REPO_ROOT / "cfg"
DATA_DIR = REPO_ROOT / "data"
LOG_DIR = REPO_ROOT / "log"

PARQUET_ANALYZER_CFG_DIR = CFG_DIR / "parquet_analyzer"
PARQUET_ANALYZER_DATA_DIR = DATA_DIR / "parquet_analyzer"
PARQUET_ANALYZER_LOG_DIR = LOG_DIR / "parquet_analyzer"


def ensure_dirs() -> None:
    for d in (PARQUET_ANALYZER_CFG_DIR, PARQUET_ANALYZER_DATA_DIR, PARQUET_ANALYZER_LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
