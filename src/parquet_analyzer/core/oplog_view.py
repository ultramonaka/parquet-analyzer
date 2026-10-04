"""Readable timeline of an operation log (detailed_specification.md 30.4).

    python -m parquet_analyzer.core.oplog_view [FILE] [--last N] [--session]

Prints each operation followed (indented) by what changed as a result, the background
jobs it started, messages shown to the user and exceptions. Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, TextIO

from .. import config


def read_records(path: Path) -> list[dict]:
    """Parse a JSON Lines log; lines that are not valid JSON objects are skipped."""
    records: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def _time_of(rec: dict) -> str:
    ts = str(rec.get("ts", ""))
    return ts[11:23] if len(ts) >= 23 else ts


def _short(value: object, limit: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _fmt_details(details: dict | None) -> str:
    return " ".join(f"{k}={_short(v, 80)}" for k, v in (details or {}).items())


def select_records(records: list[dict], *, session_only: bool = False, last: int | None = None) -> list[dict]:
    """--session: only the last session's records; --last N: from the Nth-last operation on."""
    if session_only and records:
        sid = records[-1].get("session")
        records = [r for r in records if r.get("session") == sid]
    if last is not None and last > 0:
        # by position, not seq: seq restarts at 1 in every session of a multi-session file
        starts = [i for i, r in enumerate(records) if r.get("kind") == "op" and r.get("parent") is None]
        if len(starts) > last:
            records = records[starts[-last]:]
    return records


def format_timeline(records: Iterable[dict]) -> list[str]:
    """One text block per record. A record that parses as JSON but has the wrong shape
    (hand-damaged / truncated-and-merged log) is shown raw instead of aborting the whole
    timeline (30.4: this tool is what you reach for when something went wrong)."""
    lines: list[str] = []
    for rec in records:
        try:
            lines.extend(_format_record(rec))
        except Exception:  # noqa: BLE001
            lines.append(f"    ? {_short(rec)}")
    return lines


def _format_record(rec: dict) -> list[str]:
    lines: list[str] = []
    kind = rec.get("kind")
    t = _time_of(rec)
    if kind == "session":
        extra = {k: v for k, v in rec.items() if k not in ("ts", "seq", "kind", "op", "session", "event")}
        lines.append(
            f"=== session {rec.get('event')} {t} [{rec.get('session')}] {_short(extra, 300) if extra else ''}".rstrip()
        )
    elif kind == "op":
        indent = "    " if rec.get("parent") is not None else ""
        lines.append(f"{indent}#{rec['seq']} {t} {rec.get('action')} {_fmt_details(rec.get('details'))}".rstrip())
    elif kind == "result":
        tail = ""
        if rec.get("merged"):
            tail += f" ({rec['merged']} ops merged)"
        if rec.get("interrupted_by") is not None:
            tail += f" (cut short by #{rec['interrupted_by']})"
        if rec.get("settled") is False:
            tail += " (NOT settled: still busy)"
        changes = rec.get("changes") or {}
        lines.append(f"    -> result{tail}" + ("" if changes else ": no change"))
        for path, pair in changes.items():
            before, after = (list(pair) + [None, None])[:2] if isinstance(pair, list) else (None, pair)
            lines.append(f"        {path}: {_short(before)} -> {_short(after)}")
    elif kind == "job":
        extra = f" {rec['ms']}ms" if "ms" in rec else ""
        err = f" ERROR {rec['error']}" if rec.get("error") else ""
        lines.append(f"    job {rec.get('job')} {rec.get('event')} {rec.get('target', '')}{extra}{err}".rstrip())
    elif kind == "message":
        lines.append(f"    message [{rec.get('level')}/{rec.get('where')}] {rec.get('text')}")
    elif kind == "exception":
        lines.append(f"    EXCEPTION {rec.get('type')}: {rec.get('message')}")
        tb = [ln for ln in str(rec.get("traceback", "")).splitlines() if ln.strip()]
        for ln in tb[-6:]:
            lines.append(f"        {ln}")
    else:
        lines.append(f"    ? {_short(rec)}")
    return lines


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="show_ops_log", description="Show an operation log as a readable timeline.")
    parser.add_argument("file", nargs="?", help="log file (default: today's ops log)")
    parser.add_argument("--last", type=int, metavar="N", help="only the last N operations")
    parser.add_argument("--session", action="store_true", help="only the last session (app start)")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    path = Path(args.file) if args.file else (
        Path(config.PARQUET_ANALYZER_LOG_DIR) / f"parquet_analyzer_ops_{date.today():%Y%m%d}.log"
    )
    if not path.is_file():
        print(f"log file not found: {path}", file=sys.stderr)
        return 1
    try:
        records = select_records(read_records(path), session_only=args.session, last=args.last)
    except OSError as e:
        print(f"cannot read {path}: {e}", file=sys.stderr)
        return 1
    for line in format_timeline(records):
        print(line, file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
