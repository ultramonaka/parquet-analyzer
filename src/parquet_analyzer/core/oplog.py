from __future__ import annotations

import logging

_LOGGER_NAME = "parquet_analyzer.ops"
_logger = logging.getLogger(_LOGGER_NAME)


def log_op(action: str, **details: object) -> None:
    """Record a user-triggered operation (button/menu/drag-drop/zoom) for the
    debug-mode operation log (--debug, see __main__.py). A no-op unless debug
    mode enabled the `parquet_analyzer.ops` logger.
    """
    if not _logger.isEnabledFor(logging.DEBUG):
        return
    detail_str = " ".join(f"{k}={v!r}" for k, v in details.items())
    _logger.debug("%s %s", action, detail_str)
