"""Real-time waiting for background work (dev rules: tests that wait for a worker thread must
sleep in real time until the condition they assert, never run a fixed number of event-loop
rounds -- a slow 2-core CI machine needs far longer than a dev Mac)."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

WAIT_TIMEOUT = 30.0


def wait_until(cond, timeout: float = WAIT_TIMEOUT) -> bool:
    """Process events (sleeping 5 ms between rounds) until `cond()` is truthy.

    Returns True when it became true, False after `timeout` seconds; callers then assert the
    real condition themselves, so a genuine leak/hang still fails with its own message.
    """
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if cond():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
