"""Wait for the background phase of `MainWindow.load_view` (expressions 55.3).

`load_view` returns after registering the definitions; when the view plots derived variables
they are computed in the background and the layout follows. Tests that look at the laid-out
view call `wait_view_loaded(win)` first. Waits in real time (CI machines are slower)."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication


def wait_view_loaded(win, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while win._view_compute is not None:
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("the view's derived variables were not computed in time")
        time.sleep(0.002)
    QApplication.processEvents()
