"""Persistence of user functions (detailed_specification.md 26.3):
`cfg/parquet_analyzer/user_functions.json`, shared by the whole app.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .. import config
from ..core.functions import FunctionRegistry, UserFunction, validate_user_function

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


class UserFunctionsUnreadable(OSError):
    """The functions file exists but could not be read (I/O error).

    Raised instead of returning an empty list so the caller can refuse to save and avoid
    overwriting a file that may be perfectly fine. The file is NOT moved aside.
    """


def user_functions_path() -> Path:
    # looked up at call time so tests can redirect config.PARQUET_ANALYZER_CFG_DIR
    return config.PARQUET_ANALYZER_CFG_DIR / "user_functions.json"


def _parse_entry(d: object) -> UserFunction:
    if not isinstance(d, dict):
        raise ValueError("entry is not an object")
    params, consts = d["params"], d.get("constants", [])
    if not isinstance(params, list) or not isinstance(consts, list):
        raise ValueError("params/constants must be lists")
    constants = []
    for c in consts:
        default = c["default"]
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise ValueError(f"constant {c['name']!r}: default must be a number")
        constants.append((c["name"], default))
    desc = d.get("description", "")
    expr = d["expression"]
    if not isinstance(expr, str) or not isinstance(desc, str) or not isinstance(d["name"], str):
        raise ValueError("name/expression/description must be strings")
    return UserFunction(name=d["name"], params=list(params), constants=constants, expression=expr, description=desc)


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


def load_user_functions(maps: Mapping[str, object] | None = None) -> tuple[list[UserFunction], list[str]]:
    """`(functions, warnings)` of `load_user_functions_ex` (26.3)."""
    functions, warnings, _ = load_user_functions_ex(maps)
    return functions, warnings


def load_user_functions_ex(
    maps: Mapping[str, object] | None = None,
) -> tuple[list[UserFunction], list[str], list[dict]]:
    """Return `(functions, warnings, skipped_raw)` (26.3).

    `skipped_raw` holds the verbatim JSON entries that were NOT loaded (malformed, duplicate or
    failing validation, e.g. calling a map that did not load). A later save must write them
    back unchanged so a function that merely failed to load today is not deleted from the file.

    Missing file -> empty. An `OSError` while reading raises `UserFunctionsUnreadable`
    (nothing is renamed; callers must not save over it). A file that is not valid JSON of the expected shape is renamed
    aside (`.bad-<timestamp>`), logged, and treated as empty. An individual function that
    fails validation is skipped with a warning; the others still load. `maps` (name ->
    LookupTable) lets bodies that call maps validate.
    """
    path = user_functions_path()
    if not path.exists():
        return [], [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("functions"), list):
            raise ValueError("top level must be an object with a 'functions' list")
    except OSError as e:
        logger.warning("user functions file could not be read: %s", e)
        raise UserFunctionsUnreadable(str(e)) from e
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        try:
            bad = _quarantine(path)
            msg = f"user functions file is corrupt ({e}); moved to {bad.name}"
        except OSError:
            msg = f"user functions file is corrupt ({e}) and could not be moved aside"
        logger.warning("%s", msg)
        return [], [msg], []

    warnings: list[str] = []
    skipped_raw: list[dict] = []
    raw_by_name: dict[str, dict] = {}
    candidates: dict[str, UserFunction] = {}
    for i, entry in enumerate(data["functions"]):
        try:
            fn = _parse_entry(entry)
        except (KeyError, TypeError, ValueError) as e:
            warnings.append(f"function #{i + 1}: malformed ({e!r}); skipped")
            skipped_raw.append(entry)
            continue
        if fn.name in candidates:
            warnings.append(f"{fn.name}: defined more than once; later definition skipped")
            skipped_raw.append(entry)
            continue
        candidates[fn.name] = fn
        raw_by_name[fn.name] = entry
    # Validate against a registry holding every candidate (a function may call one defined
    # later in the file); drop failures and repeat, since dropping one can invalidate its callers.
    reasons: dict[str, str] = {}
    while True:
        registry = FunctionRegistry(candidates, maps)
        failed = {}
        for name, fn in candidates.items():
            try:
                validate_user_function(fn, registry, replaces=fn.name)
            except Exception as e:  # ExpressionError, but also OverflowError/MemoryError from hostile edits
                failed[name] = str(e) or type(e).__name__
        if not failed:
            break
        for name, msg in failed.items():
            reasons.setdefault(name, msg)
            del candidates[name]
            skipped_raw.append(raw_by_name[name])
    warnings.extend(f"{name}: {msg}; skipped" for name, msg in reasons.items())
    for w in warnings:
        logger.warning("user functions: %s", w)
    return list(candidates.values()), warnings, skipped_raw


def skipped_entry_name(entry: object) -> str | None:
    """The `name` of a skipped raw entry, if it has a usable one (names stay reserved)."""
    return entry.get("name") if isinstance(entry, dict) and isinstance(entry.get("name"), str) else None


def save_user_functions(functions: list[UserFunction], skipped_raw: list[dict] | None = None) -> None:
    """Write atomically: temp file in the same directory, then os.replace (26.3).

    `skipped_raw` (from `load_user_functions_ex`) is appended verbatim after the valid ones."""
    path = user_functions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "functions": [
            {
                "name": f.name,
                "params": list(f.params),
                "constants": [{"name": n, "default": v} for n, v in f.constants],
                "expression": f.expression,
                "description": f.description,
            }
            for f in functions
        ]
        + list(skipped_raw or []),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
