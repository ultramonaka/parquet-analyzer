"""Persistence of the signal alias dictionary (persistence 54.1):
`cfg/parquet_analyzer/signal_aliases.json`, shared by the whole app.

    {"version": 1, "aliases": [{"alias": "engine_speed", "sources": ["EngSpd", "NE_rpm"]}]}
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .. import config
from ..core.signal_aliases import AliasProblem, validate_entry

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class SignalAliasesUnreadable(OSError):
    """The file exists but could not be read (I/O error): nothing is renamed, and callers must
    not save over it (same rule as the user-function file, 26.3)."""


def signal_aliases_path() -> Path:
    # looked up at call time so tests can redirect config.PARQUET_ANALYZER_CFG_DIR
    return config.PARQUET_ANALYZER_CFG_DIR / "signal_aliases.json"


def _quarantine(path: Path) -> Path:
    """Rename a corrupt file to `<name>.bad-<YYYYmmdd_HHMMSS>` so the next save can't wipe it."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bad = path.with_name(f"{path.name}.bad-{stamp}")
    n = 1
    while bad.exists():
        bad = path.with_name(f"{path.name}.bad-{stamp}-{n}")
        n += 1
    os.replace(path, bad)
    return bad


def load_signal_aliases(
    reserved: Collection[str] = (),
) -> tuple[dict[str, list[str]], list[str | tuple[int, AliasProblem]], list[object]]:
    """`load_signal_aliases_ex` without the disabled set."""
    aliases, _disabled, warnings, skipped = load_signal_aliases_ex(reserved)
    return aliases, warnings, skipped


def load_signal_aliases_ex(
    reserved: Collection[str] = (),
) -> tuple[dict[str, list[str]], set[str], list[str | tuple[int, AliasProblem]], list[object]]:
    """`(aliases, disabled, warnings, skipped_raw)`; `disabled` = aliases with `"enabled": false`
    (persistence 54.9; a missing key means enabled, a non-bool value makes the item invalid).
    `aliases` is ordered `{alias: [sources]}`.
    A warning is plain English text (file-level problems) or `(item number, AliasProblem)` for a
    bad item, which the caller translates (`tr(problem.key, **problem.params)`).

    Missing file -> empty. Unreadable (OSError) -> raises `SignalAliasesUnreadable`. Not JSON /
    wrong shape -> renamed aside, empty, one warning. A bad item (empty / duplicate / no sources /
    a `reserved` function or map name) is skipped with a warning; `skipped_raw` holds those items
    verbatim so a later save writes them back instead of deleting them. Unknown keys are ignored.
    """
    path = signal_aliases_path()
    if not path.exists():
        return {}, set(), [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("aliases"), list):
            raise ValueError("top level must be an object with an 'aliases' list")
    except OSError as e:
        logger.warning("signal aliases file could not be read: %s", e)
        raise SignalAliasesUnreadable(str(e)) from e
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        try:
            bad = _quarantine(path)
            msg = f"signal aliases file is corrupt ({e}); moved to {bad.name}"
        except OSError:
            msg = f"signal aliases file is corrupt ({e}) and could not be moved aside"
        logger.warning("%s", msg)
        return {}, set(), [msg], []

    disabled: set[str] = set()
    aliases: dict[str, list[str]] = {}
    warnings: list[str | tuple[int, AliasProblem]] = []
    skipped: list[object] = []
    for i, entry in enumerate(data["aliases"]):
        try:
            if not isinstance(entry, dict):
                raise AliasProblem("alias.err_empty_name")
            enabled = entry.get("enabled", True)
            if not isinstance(enabled, bool):
                raise AliasProblem("alias.err_bad_enabled", alias=entry.get("alias"))
            alias, sources = validate_entry(
                entry.get("alias"), entry.get("sources"), reserved=reserved, others=aliases.keys()
            )
        except AliasProblem as e:
            warnings.append((i + 1, e))
            skipped.append(entry)
            continue
        aliases[alias] = sources
        if not enabled:
            disabled.add(alias)
    for w in warnings:
        logger.warning("signal aliases: item #%s: %s; skipped", *w)
    return aliases, disabled, warnings, skipped


def save_signal_aliases(
    aliases: Mapping[str, Sequence[str]], skipped_raw: Sequence[object] = (), disabled: Collection[str] = ()
) -> None:
    """Write atomically (temp file in the same directory, then os.replace). Raises OSError on
    failure; the caller keeps its dictionary unchanged in that case. `skipped_raw` is appended
    verbatim after the valid items."""
    path = signal_aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "aliases": [
            {"alias": a, "sources": list(s), **({"enabled": False} if a in disabled else {})}
            for a, s in aliases.items()
        ]
        + list(skipped_raw),
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
