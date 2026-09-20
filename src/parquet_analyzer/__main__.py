from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from PySide6.QtWidgets import QApplication

from . import __version__, config
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
        help="Verbose (DEBUG) logging + a dumped operation log "
        "(log/parquet_analyzer/parquet_analyzer_ops_<date>.log). "
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

    ops_logger = logging.getLogger(_OPS_LOGGER_NAME)
    if debug:
        # Dedicated file (and console echo), kept separate from the main log
        # so per-click/zoom noise doesn't drown out application events.
        ops_file = config.PARQUET_ANALYZER_LOG_DIR / f"parquet_analyzer_ops_{today}.log"
        formatter = logging.Formatter("%(asctime)s %(message)s")
        file_handler = logging.FileHandler(ops_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        ops_logger.addHandler(file_handler)
        ops_logger.addHandler(console_handler)
        ops_logger.setLevel(logging.DEBUG)
        ops_logger.propagate = False
        logging.getLogger(__name__).info("debug mode: operation log at %s", ops_file)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _configure_logging(args.debug)

    app = QApplication(sys.argv)
    window = MainWindow(debug=args.debug)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
