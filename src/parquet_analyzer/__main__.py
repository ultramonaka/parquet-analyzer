from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from PySide6.QtWidgets import QApplication
from pyqtgraph.util.garbage_collector import GarbageCollector

from . import __version__, config
from .core import oplog
from .io.settings import load_settings
from .ui.main_window import MainWindow

_OPS_LOGGER_NAME = "parquet_analyzer.ops"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="parquet_analyzer")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("PARQUET_ANALYZER_DEBUG", "") not in ("", "0"),
        help="Verbose (DEBUG) logging + a readable console echo of the operation log "
        "(the log itself, log/parquet_analyzer/parquet_analyzer_ops_<date>.log, is always written). "
        "Can also be enabled via PARQUET_ANALYZER_DEBUG=1.",
    )
    return parser.parse_args(argv)


def _configure_logging(debug: bool) -> None:
    config.ensure_dirs()
    today = date.today().strftime("%Y%m%d")

    level = logging.DEBUG if debug else logging.INFO
    log_file = config.PARQUET_ANALYZER_LOG_DIR / f"parquet_analyzer_{today}.log"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )

    # The operation log (JSON Lines, core/oplog.py) is ALWAYS written, debug mode or not
    # (detailed_specification.md 30.1); debug mode only adds a readable console echo.
    try:
        retention = load_settings().log_retention_days
    except Exception:  # noqa: BLE001 (logging setup must not stop the app from starting)
        retention = oplog.DEFAULT_RETENTION_DAYS
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, retention_days=retention, debug=debug, app_version=__version__)
    oplog.install_excepthooks()
    if debug:
        ops_logger = logging.getLogger(_OPS_LOGGER_NAME)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        ops_logger.addHandler(console_handler)
        ops_logger.setLevel(logging.DEBUG)
        ops_logger.propagate = False
        logging.getLogger(__name__).info("debug mode: operation log at %s", oplog.log_path_for(date.today()))


# Held for the app's lifetime (detailed_specification.md 37.2): dropping it would stop the timer.
_gc_guard: GarbageCollector | None = None


def _start_gc_guard() -> GarbageCollector:
    """Turn automatic cyclic GC off and collect from a UI-thread timer instead (37.2).

    Automatic GC can run on whichever thread allocates -- including a QThreadPool worker --
    and collecting a cycle that holds Qt objects there destroys them off the UI thread
    (heap corruption). Must be called after the QApplication exists (the timer needs it).
    """
    global _gc_guard
    _gc_guard = GarbageCollector(interval=1.0)
    return _gc_guard


def _enable_shared_gl_contexts() -> None:
    """Must run BEFORE QApplication is created (25.3): pyqtgraph caches GL shader programs per
    class, so every top-level GL widget has to share one context or the second 3D view
    draws only text (GLError 1281)."""
    from PySide6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _configure_logging(args.debug)

    _enable_shared_gl_contexts()
    app = QApplication(sys.argv)
    _start_gc_guard()
    window = MainWindow(debug=args.debug)
    window.show()
    code = app.exec()
    oplog.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
