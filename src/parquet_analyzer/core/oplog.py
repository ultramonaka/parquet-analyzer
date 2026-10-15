"""Always-on structured operation log (detailed_specification.md 11.1 / 30).

Every user operation (`log_op`), the *result* the app settled into afterwards (a state
snapshot plus the diff against the previous one), background jobs, messages shown to
the user and unexpected exceptions are appended as JSON Lines to
`log/parquet_analyzer/parquet_analyzer_ops_{YYYYMMDD}.log` (30.1).

This module stays GUI-independent: the window registers `set_state_provider(fn)` and
this module only calls it. Nothing is written until `configure()` has been called
(the app's `__main__` does; tests opt in), so importing the app or constructing a
`MainWindow` never touches the real `log/` directory by accident.
"""

from __future__ import annotations

import json
import logging
import math
import platform
import re
import sys
import threading
import time
import traceback
import weakref
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import config

_LOGGER_NAME = "parquet_analyzer.ops"
_logger = logging.getLogger(_LOGGER_NAME)  # debug-mode console echo only (30.1)
_main_logger = logging.getLogger("parquet_analyzer")

DEFAULT_RETENTION_DAYS = 7  # Settings.log_retention_days default (30.1)
MAX_LIST_ITEMS = 50  # 30.1: longer lists are cut with a "...(+N)" marker
MAX_STRING_CHARS = 2000
# Repeated operations (wheel zoom / pan / range changes / drags) get ONE result, written
# once they have been quiet this long; everything else waits a short moment so the
# redraw it triggers can start (and mark itself busy) before we look (30.2).
BURSTY_ACTIONS = frozenset({"range_changed", "zoom", "pan", "navigator_range", "cursor_moved"})
QUIET_BURST_S = 0.300
QUIET_NORMAL_S = 0.150
SETTLE_TIMEOUT_S = 3.0
_TICK_MS = 40

# Both the main log and the operation log are subject to retention (30.1).
_FILE_RE = re.compile(r"^parquet_analyzer_(?:ops_)?(\d{8})\.log$")


# -- state ----------------------------------------------------------------------------


class _Pending:
    def __init__(self, seq: int, action: str, bursty: bool, now: float):
        self.seq = seq
        self.first_seq = seq
        self.action = action
        self.bursty = bursty
        self.last_event = now
        self.merged = 1
        # False until the event loop has turned once since this op became pending: ops
        # arriving before that happened inside the same handler, so they are children
        # of this op rather than new operations (30.2).
        self.ticked = False
        self.deferred: dict | None = None  # last op of a coalesced burst, written at flush (non-debug)


class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.log_dir: Path | None = None
        self.retention_days = DEFAULT_RETENTION_DAYS
        self.debug = False
        self.session = ""
        self.seq = 0
        self.fh = None
        self.fh_date: date | None = None
        self.write_failed = False
        self.state_provider: Callable[[], Any] | None = None
        self.busy_probe: Callable[[], Any] | None = None
        self.pending: _Pending | None = None
        self.current_op = 0  # op number events are attributed to (pending op, else the last op)
        self.last_state: dict | None = None
        self.timer = None
        self.scope_depth = 0
        self.scope_owner: int | None = None
        self.prev_excepthook = None
        self.prev_threading_hook = None
        self.hooks_installed = False


_S = _State()


def _make_ref(fn: Callable | None):
    """Hold a bound method weakly (so a closed window is not kept alive by the log)."""
    if fn is None:
        return None
    if hasattr(fn, "__self__") and hasattr(fn, "__func__"):
        ref = weakref.WeakMethod(fn)
        return lambda: (lambda m: m() if m is not None else None)(ref())
    return fn


def set_state_provider(fn: Callable[[], dict] | None, busy_probe: Callable[[], bool] | None = None) -> None:
    """Register what produces the `state` of a `result` record (30.2). `busy_probe`
    returns True while background drawing is still in flight."""
    _S.state_provider = _make_ref(fn)
    _S.busy_probe = _make_ref(busy_probe)


# -- configuration / session -----------------------------------------------------------


def log_path_for(day: date | None = None, log_dir: Path | None = None) -> Path:
    base = log_dir or _S.log_dir or config.PARQUET_ANALYZER_LOG_DIR
    return Path(base) / f"parquet_analyzer_ops_{(day or date.today()).strftime('%Y%m%d')}.log"


def purge_old_logs(log_dir: Path, retention_days: int, today: date | None = None) -> list[Path]:
    """Delete main-log and operation-log files whose filename date is older than
    `retention_days` (30.1); today's file and files with other names are never touched.
    Returns what was removed."""
    removed: list[Path] = []
    today = today or date.today()
    from ..io.settings import (
        normalize_log_retention_days,  # the one validation (1..365, else default)
    )

    retention_days = normalize_log_retention_days(retention_days)
    cutoff = today - timedelta(days=retention_days)
    try:
        entries = list(Path(log_dir).iterdir())
    except OSError:
        return removed
    for p in entries:
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if d < cutoff and d < today:
            try:
                p.unlink()
                removed.append(p)
            except OSError:
                pass
    return removed


def configure(
    log_dir: Path | None = None,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    debug: bool = False,
    app_version: str | None = None,
) -> None:
    """Start recording: purge old files, write the `session` start record."""
    provider, probe = _S.state_provider, _S.busy_probe  # registered by a window created earlier
    reset()
    _S.state_provider, _S.busy_probe = provider, probe
    _S.log_dir = Path(log_dir) if log_dir is not None else Path(config.PARQUET_ANALYZER_LOG_DIR)
    _S.retention_days = retention_days
    _S.debug = debug
    _S.session = datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{id(_S) % 0xFFFF:04x}"
    try:
        _S.log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    purge_old_logs(_S.log_dir, retention_days)
    info: dict[str, Any] = {
        "event": "start",
        "app_version": app_version,
        "debug": debug,
        "utc_offset": datetime.now().astimezone().strftime("%z"),
        "python": platform.python_version(),
        "os": platform.platform(),
    }
    try:
        import PySide6

        info["pyside6"] = PySide6.__version__
    except Exception:  # noqa: BLE001
        pass
    try:
        from .. import features

        info["features"] = features.effective_features()
    except Exception:  # noqa: BLE001
        pass
    _emit("session", op=0, **info)
    flush()


def is_active() -> bool:
    return _S.log_dir is not None


def shutdown() -> None:
    """Write the session-end record and close the file."""
    if not is_active():
        return
    flush_pending()
    _emit("session", op=_S.current_op, event="end")
    reset()


def reset() -> None:
    """Close the file and forget all state (used by shutdown() and test fixtures)."""
    with _S.lock:
        if _S.timer is not None:
            try:
                _S.timer.stop()
            except Exception:  # noqa: BLE001
                pass
        if _S.fh is not None:
            try:
                _S.fh.close()
            except Exception:  # noqa: BLE001
                pass
        uninstall_excepthooks()
        _S.__init__()  # type: ignore[misc]


def _write_failed(e: Exception) -> None:
    if not _S.write_failed:
        _S.write_failed = True
        _main_logger.warning("operation log write failed (%s); further failures are silent", e)


def flush() -> None:
    with _S.lock:
        if _S.fh is not None:
            try:
                _S.fh.flush()
            except Exception as e:  # noqa: BLE001
                _write_failed(e)


# -- writing ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:  # noqa: BLE001 (a broken __str__ must not drop the whole record)
        return f"<unprintable {type(value).__name__}>"


def _clean_str(value: str) -> str:
    """UTF-8-encodable and at most MAX_STRING_CHARS long."""
    # A lone surrogate (e.g. a file name with undecodable bytes, surrogateescape) cannot be
    # encoded as UTF-8: the write would fail and the whole record be lost (30.1 "never raises").
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        value = value.encode("utf-8", "replace").decode("utf-8")
    return value if len(value) <= MAX_STRING_CHARS else value[:MAX_STRING_CHARS] + f"...(+{len(value) - MAX_STRING_CHARS})"


def _sanitize(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy with size caps (30.1)."""
    if depth > 8:
        return _clean_str(_safe_str(value))
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return _clean_str(value)
    if isinstance(value, dict):
        items = list(value.items())
        out = {_clean_str(_safe_str(k)): _sanitize(v, depth + 1) for k, v in items[:MAX_LIST_ITEMS]}
        if len(items) > MAX_LIST_ITEMS:
            out["..."] = f"(+{len(items) - MAX_LIST_ITEMS})"
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = list(value)
        out = [_sanitize(v, depth + 1) for v in seq[:MAX_LIST_ITEMS]]
        if len(seq) > MAX_LIST_ITEMS:
            out.append(f"...(+{len(seq) - MAX_LIST_ITEMS})")
        return out
    item = getattr(value, "item", None)  # numpy scalar
    if callable(item):
        try:
            return _sanitize(item(), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    return _clean_str(_safe_str(value))


def _open_file() -> None:
    today = date.today()
    if _S.fh is not None and _S.fh_date == today:
        return
    if _S.fh is not None:
        try:
            _S.fh.close()
        except Exception:  # noqa: BLE001
            pass
    if _S.log_dir is None:  # reset() raced with us: never fall back to the real log dir
        raise RuntimeError("operation log is not configured")
    path = log_path_for(today, _S.log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _S.fh = open(path, "a", encoding="utf-8")
    _S.fh_date = today


def _emit(kind: str, op: int | None = None, **fields: Any) -> dict | None:
    """Write one record; returns it. Never raises (30.1)."""
    try:
        with _S.lock:
            if not is_active():  # re-checked under the lock: reset() may have just run
                return None
            _S.seq += 1
            rec: dict[str, Any] = {
                "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "seq": _S.seq,
                "kind": kind,
                "op": _S.seq if op is None and kind == "op" else (_S.current_op if op is None else op),
                "session": _S.session,
            }
            rec.update(_sanitize(fields))
            line = json.dumps(rec, ensure_ascii=False)
            try:
                _open_file()
                _S.fh.write(line + "\n")
                if kind in ("exception", "session"):
                    _S.fh.flush()
            except Exception as e:  # noqa: BLE001 (disk full etc. must not stop the app)
                _write_failed(e)
            if _S.debug:
                _logger.debug("%s", format_echo(rec))
            return rec
    except Exception:  # noqa: BLE001
        return None


def format_echo(rec: dict) -> str:
    """Readable one-line form for the debug-mode console (30.1)."""
    rest = {k: v for k, v in rec.items() if k not in ("ts", "seq", "kind", "op", "session")}
    body = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in rest.items())
    return f"#{rec['seq']} {rec['kind']} (op {rec['op']}) {body}"


# -- public recording API --------------------------------------------------------------


def has_pending() -> bool:
    """Whether an operation is still waiting for its result."""
    return _S.pending is not None


def current_op() -> int:
    """Number of the operation that events happening now belong to (0 = none yet)."""
    return _S.current_op


class compound_op:
    """Context manager for a handler that logs several `log_op`s of its own (e.g.
    load_view -> add_plot ...): only the first becomes the operation that gets a
    result; later ones are recorded as its children (`parent`)."""

    def __init__(self, owner: int | None = None):
        # `owner`: an operation whose result is still pending but whose handler has returned
        # (load_view waiting for its background computation, expressions 55.3): the ops logged
        # inside this scope become its children although the event loop has turned since.
        self._owner = owner

    def __enter__(self):
        _S.scope_depth += 1
        if self._owner is not None and _S.scope_owner is None:
            _S.scope_owner = self._owner
        return self

    def __exit__(self, *exc):
        _S.scope_depth -= 1
        if _S.scope_depth == 0:
            _S.scope_owner = None
        return False


def compound(fn):
    """Decorator form of compound_op for a whole handler method."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with compound_op():
            return fn(*args, **kwargs)

    return wrapper


def log_op(action: str, **details: object) -> None:
    """Record a user-triggered operation (button/menu/drag-drop/zoom) and start waiting
    for its result (30.1/30.2). Signature unchanged since the debug-only version.

    An op logged before the event loop has turned since the pending op was logged (i.e.
    from inside the same handler, e.g. `remove_plot_requested` -> `remove_plot`) is a
    *child* of it (`parent`), not a new operation."""
    if not is_active():
        return
    now = time.monotonic()
    bursty = action in BURSTY_ACTIONS
    with _S.lock:
        p = _S.pending
        if p is not None and bursty and p.bursty and p.action == action:
            # same repeated gesture: one result for the whole burst
            p.last_event = now
            p.merged += 1
            if _S.debug:
                rec = _emit("op", action=action, details=details)
                if rec:
                    p.seq = rec["seq"]
                    _S.current_op = p.seq
            else:  # only the first and the last op line of a burst are kept
                p.deferred = dict(details)
            return
        parent: int | None = None
        if _S.scope_depth > 0 and _S.scope_owner is not None:
            parent = _S.scope_owner
        elif p is not None and not p.ticked:
            parent = p.seq
        if parent is not None:
            _emit("op", action=action, details=details, parent=parent)
            if _S.scope_depth > 0 and _S.scope_owner is None:
                _S.scope_owner = parent
            if p is not None:
                p.last_event = now  # the quiet period starts after the last consequence
            return
        if p is not None:
            # A new operation closes the previous one's result first; it was cut short, so
            # "settled" reflects whether it was really still busy.
            flush_pending(settled=not _is_busy(), interrupted=True)
        if _S.last_state is None:
            _S.last_state = _snapshot()  # baseline = "before the first operation"
        rec = _emit("op", action=action, details=details)
        if rec is None:
            return
        if _S.scope_depth > 0:
            _S.scope_owner = rec["seq"]  # the written record's own number, never a guess
        _S.current_op = rec["seq"]
        _S.pending = _Pending(rec["seq"], action, bursty, now)
        _arm(_S.pending)


def record_job(job: str, event: str, *, target: object = None, ms: float | None = None, error: str | None = None,
               op: int | None = None) -> None:
    """A background job's start/finish/fail, attributed to the operation in effect when
    it was queued (30.3)."""
    fields: dict[str, Any] = {"job": job, "event": event}
    if target is not None:
        fields["target"] = target
    if ms is not None:
        fields["ms"] = round(ms, 1)
    if error is not None:
        fields["error"] = error
    _emit("job", op=op, **fields)


def job_recorded_always(job: str) -> bool:
    """Downsample jobs are frequent: only failures are recorded unless debugging (30.3)."""
    return job != "downsample" or _S.debug


def record_message(level: str, where: str, text: str, title: str | None = None) -> None:
    fields: dict[str, Any] = {"level": level, "where": where, "text": text}
    if title:
        fields["title"] = title
    _emit("message", **fields)


def record_exception(exc_type, exc, tb, source: str = "main") -> None:
    try:
        tb_text = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:  # noqa: BLE001
        tb_text = _safe_str(exc)
    _emit("exception", type=getattr(exc_type, "__name__", str(exc_type)), message=_safe_str(exc),
          traceback=tb_text, source=source)
    _main_logger.error("unexpected exception (%s): %s", source, _safe_str(exc), exc_info=(exc_type, exc, tb))


def install_excepthooks() -> None:
    """Route uncaught exceptions (incl. those from Qt slots) to the log; the app keeps running."""
    if _S.hooks_installed:
        return
    _S.prev_excepthook = sys.excepthook
    _S.prev_threading_hook = threading.excepthook

    def _hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            record_exception(exc_type, exc, tb)
        prev = _S.prev_excepthook or sys.__excepthook__
        try:
            prev(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    def _thread_hook(args):
        record_exception(args.exc_type, args.exc_value, args.exc_traceback, source="thread")
        prev = _S.prev_threading_hook
        if prev is not None:
            try:
                prev(args)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    _S.hooks_installed = True


def uninstall_excepthooks() -> None:
    if not _S.hooks_installed:
        return
    sys.excepthook = _S.prev_excepthook or sys.__excepthook__
    if _S.prev_threading_hook is not None:
        threading.excepthook = _S.prev_threading_hook
    _S.hooks_installed = False


# -- results ---------------------------------------------------------------------------


def _snapshot() -> dict:
    fn = _S.state_provider
    if fn is None:
        return {}
    try:
        provider = fn()  # may be a weak-method resolver returning the bound method
        state = provider() if callable(provider) else provider
        return state if isinstance(state, dict) else {}
    except Exception as e:  # noqa: BLE001 (a broken snapshot must never break the app)
        return {"snapshot_error": repr(e)}


def _is_busy() -> bool:
    fn = _S.busy_probe
    if fn is None:
        return False
    try:
        probe = fn()
        return bool(probe() if callable(probe) else probe)
    except Exception:  # noqa: BLE001
        return False


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b or math.isclose(a, b, rel_tol=1e-9, abs_tol=0.0)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


_MISSING = object()


def diff_states(before: dict | None, after: dict | None) -> dict:
    """Path -> [before, after] for every item that changed (30.2). Dicts are recursed
    into; lists are compared whole. A key present on one side only has null on the other."""
    changes: dict[str, list] = {}

    def walk(path: str, a: Any, b: Any) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in list(a.keys()) + [k for k in b.keys() if k not in a]:
                walk(f"{path}.{k}" if path else str(k), a.get(k, _MISSING), b.get(k, _MISSING))
            return
        if a is _MISSING and b is _MISSING:
            return
        if a is _MISSING or b is _MISSING or not _equal(a, b):
            changes[path] = [None if a is _MISSING else a, None if b is _MISSING else b]

    walk("", before or {}, after or {})
    return changes


def flush_pending(settled: bool = True, interrupted: bool = False) -> None:
    """Write the result of the pending operation now (30.2)."""
    with _S.lock:
        p = _S.pending
        if p is None:
            return
        _S.pending = None
        if p.deferred is not None:  # last op line of a coalesced burst
            rec = _emit("op", action=p.action, details=p.deferred)
            if rec:
                p.seq = rec["seq"]
                _S.current_op = p.seq
        state = _snapshot()
        changes = diff_states(_S.last_state, state)
        _S.last_state = state
        extra: dict[str, Any] = {"settled": bool(settled)}
        if p.merged > 1:
            extra["merged"] = p.merged
            extra["first_op"] = p.first_seq
        if interrupted:
            extra["interrupted_by"] = _S.seq + 2  # this result, then the op that cut it short
        _emit("result", op=p.seq, action=p.action, state=state, changes=changes, **extra)
        flush()


def _tick() -> None:
    p = _S.pending
    if p is None:
        _stop_timer()
        return
    p.ticked = True
    now = time.monotonic()
    quiet = now - p.last_event
    need = QUIET_BURST_S if p.bursty else QUIET_NORMAL_S
    busy = _is_busy()
    if not busy and quiet >= need:
        flush_pending(True)
    elif quiet >= max(need, SETTLE_TIMEOUT_S):
        flush_pending(not busy)
    if _S.pending is None:
        _stop_timer()


def _arm(p: _Pending) -> None:
    """Start polling for `p`'s result and mark the first event-loop turn."""
    try:
        from PySide6.QtCore import QCoreApplication, QThread, QTimer

        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            p.ticked = True  # no event loop to wait for
            return
        if _S.timer is None:
            _S.timer = QTimer()
            _S.timer.setInterval(_TICK_MS)
            _S.timer.timeout.connect(_tick)
        if not _S.timer.isActive():
            _S.timer.start()

        def mark(p=p):
            p.ticked = True

        QTimer.singleShot(0, mark)
    except Exception:  # noqa: BLE001
        p.ticked = True


def _stop_timer() -> None:
    if _S.timer is not None:
        try:
            _S.timer.stop()
        except Exception:  # noqa: BLE001
            pass


def format_time(x: float) -> str:
    """Epoch seconds -> "YYYY-MM-DD HH:MM:SS.mmm" in LOCAL time, like the plots' date axis
    (pyqtgraph DateAxisItem) and `format_x`; non-finite or out-of-range values come back
    as text."""
    try:
        return datetime.fromtimestamp(float(x)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (ValueError, OverflowError, OSError):
        return str(x)
