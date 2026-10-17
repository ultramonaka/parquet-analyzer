"""Memory and disk guards of the Parquet export (export.ja.md 57.9.5, 57.9.6, 57.9.6a). No Qt.

Pure helpers: how much physical / available memory and free disk there is (standard library only,
`None` when it cannot be read), the memory budget, and the checks made *before* an export starts
(`preflight`) and between files (`can_start_new_file`). The run-time part (slow down, wait, fail
one file) lives in `parquet_export.py` where the chunk loop is. The probes are module functions so
tests can replace them.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

GB = 1024**3
MB = 1024**2

BUDGET_FRACTION = 0.4  # 57.9.5: of the physical memory
BUDGET_FALLBACK = 4 * GB
# 57.9.6a: the room left on the volume besides the bytes still to be written. Fixed and small on
# purpose: a nearly full drive (e.g. 800 MB free) must still take a small export.
DISK_MARGIN = 100 * MB  # the default; `disk_margin()` is what the three checks read
_margin_bytes = DISK_MARGIN
MEMORY_SLOW_BELOW = 2 * GB  # 57.9.6a: halve the chunk, start no new file
MEMORY_PAUSE_BELOW = 1 * GB  # 57.9.6a: pause the running file
MEMORY_WAIT_LIMIT = 30.0  # seconds a paused file waits before it fails
DISK_YIELD_WAIT = 10.0  # seconds a more advanced file waits for a less advanced one to stop (57.9.6a (c))
MIN_CHUNK_ROWS = 10_000  # 57.9.6a: the smallest chunk the memory guard shrinks to


def disk_margin() -> int:
    """The room kept free besides the bytes still to be written (57.9.6a): the preflight, the start of a
    further file and the stop of a running one all read this one value."""
    return _margin_bytes


def set_disk_margin_mb(mb: int | None) -> None:
    """Apply the setting `export_disk_margin_mb` (None / invalid: the default)."""
    global _margin_bytes
    _margin_bytes = int(mb) * MB if isinstance(mb, int) and not isinstance(mb, bool) and mb > 0 else DISK_MARGIN


def physical_memory_bytes() -> int | None:
    """Installed physical memory, or None."""
    try:
        if sys.platform == "win32":
            return _win_memory_status()[0]
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, AttributeError, KeyError):
        return None


def available_memory_bytes() -> int | None:
    """Memory the OS says can be used without swapping, or None (the guard is then skipped)."""
    try:
        if sys.platform == "win32":
            return _win_memory_status()[1]
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
            return None
        if sys.platform == "darwin":
            return _darwin_available()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return None


def _darwin_available() -> int | None:
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5, check=False).stdout
    page = 4096
    pages: dict[str, int] = {}
    for line in out.splitlines():
        if "page size of" in line:
            page = int(line.split("page size of")[1].split()[0])
        elif ":" in line:
            key, _, val = line.partition(":")
            try:
                pages[key.strip()] = int(val.strip().rstrip("."))
            except ValueError:
                pass
    # free + inactive + speculative: what macOS hands out before it swaps. "Pages purgeable" is not
    # added: those pages are already inside the active / inactive counts (checked: free + active +
    # inactive + speculative + wired + compressor ~ all pages), so adding it would count them twice.
    use = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative"))
    return use * page if use else None


def _win_memory_status() -> tuple[int, int]:
    class _Status(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        raise OSError("GlobalMemoryStatusEx failed")
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def disk_free_bytes(path: str | os.PathLike) -> int | None:
    """Free space of the volume `path` (or its nearest existing parent) lives on, or None."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return int(shutil.disk_usage(p).free)
    except OSError:
        return None


def memory_budget_bytes() -> int:
    """How much memory the exports together may be estimated to use (57.9.5)."""
    total = physical_memory_bytes()
    return int(total * BUDGET_FRACTION) if total else BUDGET_FALLBACK


# -- before an export starts (57.9.6a) ----------------------------------------------------------------


@dataclass(frozen=True)
class PreflightItem:
    """One output file to be written: where, an upper bound of its size, an estimate of its memory."""

    path: str
    max_bytes: int
    memory_bytes: int


@dataclass
class PreflightReport:
    # (folder, bytes needed, bytes free) per volume that cannot hold the outputs with its margin
    disk_short: list[tuple[str, int, int]] = field(default_factory=list)
    # (path, estimated bytes, available bytes) per file whose estimate exceeds the free memory
    memory_short: list[tuple[str, int, int]] = field(default_factory=list)
    memory_over_physical: list[str] = field(default_factory=list)  # estimate > installed memory (57.9.5)

    @property
    def ok(self) -> bool:
        return not (self.disk_short or self.memory_short)


def _volume_key(folder: Path) -> object:
    p = folder
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return os.stat(p).st_dev
    except OSError:
        return str(p)


def preflight(items: Sequence[PreflightItem]) -> PreflightReport:
    """The check made when exports are added to the queue (57.9.6a). Disk: per volume, the sum of the
    upper-bound sizes must fit in the free space minus the margin. Memory: each file's estimate must
    fit in the memory available now. Unreadable free space skips that check."""
    report = PreflightReport()
    by_volume: dict[object, tuple[Path, int]] = {}
    for it in items:
        folder = Path(it.path).resolve().parent
        key = _volume_key(folder)
        prev = by_volume.get(key)
        by_volume[key] = (prev[0] if prev else folder, (prev[1] if prev else 0) + it.max_bytes)
    for folder, need in by_volume.values():
        free = disk_free_bytes(folder)
        if free is not None and need > free - disk_margin():
            report.disk_short.append((str(folder), need, free))
    avail = available_memory_bytes()
    phys = physical_memory_bytes()
    for it in items:
        if avail is not None and it.memory_bytes > avail:
            report.memory_short.append((it.path, it.memory_bytes, avail))
        if phys is not None and it.memory_bytes > phys:
            report.memory_over_physical.append(it.path)
    return report


class DiskArbiter:
    """Which running file stops when the disk runs short (57.9.6a (c)): the least advanced one on the same
    volume, so files that are nearly done still finish. Each running export registers under a key (and
    the volume it writes to) and reports its fraction done. A file that finds the disk short but is not
    the least advanced calls `request_stop_least`; the marked file raises `DiskFull` at its next gate.
    Thread safe (the exports run on pool threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fraction: dict[object, float] = {}
        self._order: dict[object, int] = {}
        self._volume: dict[object, object] = {}
        self._stop: set[object] = set()
        self._count = 0

    def register(self, key: object, volume: object = None) -> None:
        with self._lock:
            self._count += 1
            self._order[key] = self._count
            self._fraction[key] = 0.0
            self._volume[key] = volume

    def unregister(self, key: object) -> None:
        with self._lock:
            for table in (self._fraction, self._order, self._volume):
                table.pop(key, None)
            self._stop.discard(key)

    def update(self, key: object, fraction: float) -> None:
        with self._lock:
            if key in self._fraction:
                self._fraction[key] = fraction

    def _least(self, key: object) -> object:
        """The least advanced registered key on `key`'s volume (a tie: the newest). Lock held by the caller."""
        peers = [k for k in self._fraction if self._volume[k] == self._volume[key]]
        return min(peers, key=lambda k: (self._fraction[k], -self._order[k]))

    def is_least_advanced(self, key: object) -> bool:
        """True when no other export on the same volume is less advanced than `key`."""
        with self._lock:
            return key not in self._fraction or self._least(key) == key

    def request_stop_least(self, key: object) -> object | None:
        """`key` found the disk short: mark the least advanced export on its volume to stop. Returns the
        marked key (None when `key` itself is the least advanced, or unknown)."""
        with self._lock:
            if key not in self._fraction:
                return None
            least = self._least(key)
            if least == key:
                return None
            self._stop.add(least)
            return least

    def stop_requested(self, key: object) -> bool:
        with self._lock:
            return key in self._stop


def can_start_new_file(remaining_bytes: int, folder: str | os.PathLike) -> str | None:
    """Why no further file should be started now (57.9.6a): "disk" when the free space is below the
    bytes still expected to be written plus the margin, "memory" when the available memory is below
    2 GB; None when it may start. For the queue (stage B) and tests."""
    free = disk_free_bytes(folder)
    if free is not None and free < remaining_bytes + disk_margin():
        return "disk"
    avail = available_memory_bytes()
    if avail is not None and avail < MEMORY_SLOW_BELOW:
        return "memory"
    return None
