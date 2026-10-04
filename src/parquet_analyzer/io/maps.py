"""CSV import and on-disk storage of lookup tables (detailed_specification.md 27.2/27.3).

Row numbers in error messages are 1-based line numbers of the file (blank lines count),
column numbers are 1-based.
"""
from __future__ import annotations

import json
import keyword
import logging
import math
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

from .. import config
from ..core.expression import _ALLOWED_FUNCS
from ..core.maps import EXTRAPOLATIONS, INTERPOLATIONS, LookupTable, normalize_axis_names

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


class MapError(ValueError):
    """Invalid CSV content, map name, settings or storage state (27.2/27.3)."""


# ---------------------------------------------------------------- CSV parsing (27.2)


def _read_rows(path: Path) -> list[tuple[int, list[str]]]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp932")  # Japanese Excel CSV
        except UnicodeDecodeError as e:
            raise MapError(f"cannot decode {Path(path).name} as UTF-8 or CP932") from e
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        rows.append((lineno, [c.strip() for c in line.split(",")]))
    return rows


def _num(cell: str, row: int, col: int) -> float:
    try:
        v = float(cell)
    except ValueError:
        raise MapError(f"row {row}, column {col}: not a number: {cell!r}") from None
    if not math.isfinite(v):
        raise MapError(f"row {row}, column {col}: non-finite value: {cell!r}")
    return v


def parse_cell(cell: str, row: int, col: int) -> float:
    """A finite number from an edited table cell; errors name the (1-based) row/column (27.8)."""
    return _num(cell.strip(), row, col)


def check_axis(values: list[float], positions: list[tuple[int, int]], label: str) -> None:
    """Strictly-ascending check with row/column in the message (27.2, reused by 27.8)."""
    _check_axis(values, positions, label)


def _is_number(cell: str) -> bool:
    try:
        float(cell)
        return True
    except ValueError:
        return False


def _check_axis(values: list[float], positions: list[tuple[int, int]], label: str) -> None:
    for i in range(1, len(values)):
        if values[i] == values[i - 1]:
            r, c = positions[i]
            raise MapError(f"row {r}, column {c}: duplicate {label} value {values[i]:g}")
        if values[i] < values[i - 1]:
            r, c = positions[i]
            raise MapError(f"row {r}, column {c}: {label} axis must be ascending ({values[i]:g} after {values[i - 1]:g})")


def parse_table_csv(
    path: Path | str,
    name: str = "table",
    kind: str = "auto",
    interpolation: str = "linear",
    extrapolation: str = "clamp",
) -> LookupTable:
    """Read a curve/map CSV into a LookupTable (27.2). `kind` is "auto", "curve" or "map".

    Auto-detect: 2 columns -> curve; 3 or more columns with an empty/non-numeric top-left
    cell -> map; anything else is an error (choose the kind explicitly). A curve's first row
    is a header only if neither cell is a number (a mixed row is reported as an error).
    """
    if kind not in ("auto", "curve", "map"):
        raise MapError(f"unknown kind: {kind}")
    rows = _read_rows(Path(path))
    if not rows:
        raise MapError("file is empty")
    if kind == "auto":
        ncols = len(rows[0][1])
        if ncols == 2:
            kind = "curve"
        elif ncols >= 3 and not _is_number(rows[0][1][0]):
            kind = "map"  # top-left cell empty or text
        else:
            raise MapError(
                f"row {rows[0][0]}: cannot auto-detect the table kind ({ncols} columns, "
                "numeric top-left cell); choose curve or map"
            )

    _check_settings(kind, interpolation, extrapolation)
    if kind == "curve":
        xs: list[float] = []
        ys: list[float] = []
        xpos: list[tuple[int, int]] = []
        header: tuple[str, ...] = ()
        for k, (lineno, cells) in enumerate(rows):
            if len(cells) != 2:
                raise MapError(f"row {lineno}: expected 2 columns, got {len(cells)}")
            if k == 0 and not (_is_number(cells[0]) or _is_number(cells[1])):
                header = tuple(cells)  # header row: the default axis names (27.7)
                continue
            xs.append(_num(cells[0], lineno, 1))
            ys.append(_num(cells[1], lineno, 2))
            xpos.append((lineno, 1))
        if len(xs) < 2:
            raise MapError("a curve needs at least 2 points")
        _check_axis(xs, xpos, "X")
        table = LookupTable(name, "curve", np.array(xs), np.array(ys), None, interpolation, extrapolation,
                            axis_names=normalize_axis_names(header))
    else:
        first_line, first = rows[0]
        width = len(first)
        xs = [_num(c, first_line, j + 2) for j, c in enumerate(first[1:])]
        if len(xs) < 2:
            raise MapError(f"row {first_line}: a map needs at least 2 X axis values")
        _check_axis(xs, [(first_line, j + 2) for j in range(len(xs))], "X")
        ys = []
        zrows = []
        for lineno, cells in rows[1:]:
            if len(cells) != width:
                raise MapError(f"row {lineno}: expected {width} columns, got {len(cells)}")
            ys.append(_num(cells[0], lineno, 1))
            zrows.append([_num(c, lineno, j + 2) for j, c in enumerate(cells[1:])])
        if len(ys) < 2:
            raise MapError("a map needs at least 2 Y axis values (rows)")
        _check_axis(ys, [(rows[1 + i][0], 1) for i in range(len(ys))], "Y")
        table = LookupTable(name, "map", np.array(xs), np.array(ys), np.array(zrows), interpolation, extrapolation)
    return table


def _check_settings(kind: str, interpolation: str, extrapolation: str) -> None:
    if interpolation not in INTERPOLATIONS:
        raise MapError(f"unknown interpolation: {interpolation}")
    if kind not in INTERPOLATIONS[interpolation].kinds:
        raise MapError(f"interpolation {interpolation} is not available for a {kind}")
    if extrapolation not in EXTRAPOLATIONS:
        raise MapError(f"unknown extrapolation: {extrapolation}")


# ---------------------------------------------------------------- storage (27.3)


def maps_dir() -> Path:
    return config.PARQUET_ANALYZER_DATA_DIR / "maps"


def validate_map_name(name: str, existing_names: Iterable[str] = ()) -> None:
    """`existing_names`: user function / other map names to clash with (builtins are always checked)."""
    if not name or Path(name).name != name or not name.isidentifier() or keyword.iskeyword(name):
        raise MapError(f"invalid map name (must be a Python identifier): {name!r}")
    if name in _ALLOWED_FUNCS or name in set(existing_names):
        raise MapError(f"name already in use: {name}")


@dataclass
class MapInfo:
    name: str
    kind: str
    interpolation: str
    extrapolation: str
    source_path: str
    imported_at: str
    axis_names: tuple[str, str, str] = ("X", "Y", "Z")
    edited_at: str = ""  # ISO 8601 of the last in-manager value edit; "" = never (27.8)


def _safe(name: str) -> str:
    """Every on-disk entry point goes through here so a name can never escape maps_dir()."""
    validate_map_name(name)
    return name


def _csv_path(name: str) -> Path:
    _safe(name)
    return maps_dir() / f"{name}.csv"


def _json_path(name: str) -> Path:
    _safe(name)
    return maps_dir() / f"{name}.json"


def _write_meta(info: MapInfo) -> None:
    payload = {"schema_version": SCHEMA_VERSION, **info.__dict__}
    payload["axis_names"] = dict(zip(("x", "y", "z"), normalize_axis_names(info.axis_names)))
    _json_path(info.name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _axis_names_from_json(raw) -> tuple[str, str, str]:
    """`axis_names` is optional (27.7): a missing/odd value gives the defaults."""
    if not isinstance(raw, dict):
        return normalize_axis_names(())
    return normalize_axis_names([raw.get(k) if isinstance(raw.get(k), str) else "" for k in ("x", "y", "z")])


def _read_meta(name: str) -> MapInfo:
    p = _json_path(name)
    if not p.exists() or not _csv_path(name).exists():
        raise MapError(f"map not found: {name}")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        # wrong-typed settings (e.g. a list) would otherwise surface as a raw TypeError later,
        # e.g. an unhashable dict key (expressions.ja.md 6.1)
        for key in ("kind", "interpolation", "extrapolation"):
            if not isinstance(d[key], str):
                raise TypeError(f"{key} must be a string")
        return MapInfo(
            name=name,
            kind=d["kind"],
            interpolation=d["interpolation"],
            extrapolation=d["extrapolation"],
            source_path=d["source_path"] if isinstance(d.get("source_path"), str) else "",
            imported_at=d["imported_at"] if isinstance(d.get("imported_at"), str) else "",
            axis_names=_axis_names_from_json(d.get("axis_names")),
            edited_at=d["edited_at"] if isinstance(d.get("edited_at"), str) else "",
        )
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise MapError(f"map metadata unreadable: {name}: {e}") from e


def import_map(
    csv_path: Path | str,
    name: str,
    kind: str = "auto",
    interpolation: str = "linear",
    extrapolation: str = "clamp",
    existing_names: Iterable[str] = (),
) -> LookupTable:
    """Parse `csv_path`, copy it (copy2) to `<name>.csv` and write `<name>.json` (27.3).

    Nothing is written if the CSV or any setting is invalid. `existing_names` = user
    function and other map names the new name must not clash with.
    """
    validate_map_name(name, existing_names)
    if _json_path(name).exists() or _csv_path(name).exists():
        raise MapError(f"name already in use: {name}")
    table = parse_table_csv(csv_path, name, kind, interpolation, extrapolation)
    maps_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy2(csv_path, _csv_path(name))
    _write_meta(
        MapInfo(name, table.kind, interpolation, extrapolation, str(Path(csv_path).resolve()),
                datetime.now().isoformat(timespec="seconds"), table.axis_names)
    )
    for w in table.warnings():
        logger.warning("%s", w)
    return table


def list_maps() -> list[MapInfo]:
    """Metadata of every stored map, sorted by name. Unreadable entries are skipped (logged)."""
    d = maps_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(_read_meta(p.stem))
        except MapError as e:
            logger.warning("%s", e)
    return out


def load_map(name: str) -> LookupTable:
    """Re-read the stored CSV copy with the stored settings."""
    info = _read_meta(name)
    table = parse_table_csv(_csv_path(name), name, info.kind, info.interpolation, info.extrapolation)
    table.axis_names = info.axis_names  # the stored names win over a CSV header (27.7)
    return table


def load_all_maps() -> tuple[dict[str, LookupTable], list[str]]:
    """Startup helper: every readable map plus warnings for the ones that are not (files kept)."""
    tables: dict[str, LookupTable] = {}
    warnings: list[str] = []
    d = maps_dir()
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            tables[p.stem] = load_map(p.stem)
        except MapError as e:
            warnings.append(f"{p.stem}: {e}")
    for w in warnings:
        logger.warning("maps: %s", w)
    return tables, warnings


def save_map_settings(name: str, interpolation: str, extrapolation: str) -> LookupTable:
    info = _read_meta(name)
    _check_settings(info.kind, interpolation, extrapolation)
    info.interpolation, info.extrapolation = interpolation, extrapolation
    _write_meta(info)
    return load_map(name)


def save_map_axis_names(name: str, x: str, y: str, z: str) -> LookupTable:
    """Store the axis display names (27.7); blank -> the default "X"/"Y"/"Z"."""
    info = _read_meta(name)
    info.axis_names = normalize_axis_names((x, y, z))
    _write_meta(info)
    return load_map(name)


def rename_map(old: str, new: str, existing_names: Iterable[str] = ()) -> LookupTable:
    """Rename `<old>.csv`/`<old>.json` to `<new>` and update the JSON's name (27.7).

    `existing_names`: user function / other map names `new` must not clash with. All-or-nothing:
    if any step fails, everything already done is undone and the error propagates. (The
    "used by derived variables / functions" check is the caller's -- it needs the registry.)
    """
    validate_map_name(new, existing_names)
    _read_meta(old)  # raises if absent / unreadable
    if _json_path(new).exists() or _csv_path(new).exists():
        raise MapError(f"name already in use: {new}")
    old_csv, old_json = _csv_path(old), _json_path(old)
    new_csv, new_json = _csv_path(new), _json_path(new)
    done: list[tuple[Path, Path]] = []
    try:
        old_csv.rename(new_csv)
        done.append((new_csv, old_csv))
        old_json.rename(new_json)
        done.append((new_json, old_json))
        # the JSON's own "name" entry follows the file name (27.3)
        d = json.loads(new_json.read_text(encoding="utf-8"))
        d["name"] = new
        new_json.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return load_map(new)
    except BaseException:
        for src, dst in reversed(done):
            try:
                src.rename(dst)
            except OSError:  # pragma: no cover - best effort
                logger.exception("could not undo rename %s -> %s", src, dst)
        if done:  # the old JSON's content may have been rewritten; restore its name
            try:
                d = json.loads(old_json.read_text(encoding="utf-8"))
                d["name"] = old
                old_json.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):  # pragma: no cover
                pass
        raise


def reimport_map(name: str) -> LookupTable:
    """Re-read `source_path`, overwrite the stored CSV copy (27.3). Error if the source is gone."""
    info = _read_meta(name)
    src = Path(info.source_path)
    if not info.source_path or not src.is_file():
        raise MapError(f"source file not found: {info.source_path}")
    table = parse_table_csv(src, name, info.kind, info.interpolation, info.extrapolation)  # validate first
    shutil.copy2(src, _csv_path(name))
    info.imported_at = datetime.now().isoformat(timespec="seconds")
    info.edited_at = ""  # the stored copy is the source file again (27.8)
    _write_meta(info)
    return table


def _fmt(v: float) -> str:
    return repr(float(v))  # shortest string that round-trips the float64 exactly


def write_table_csv(table: LookupTable, path: Path | str) -> None:
    """Write `table` in the 27.2 CSV format: a curve as `x,y` rows (no header; the axis names live
    in the JSON), a map with an empty top-left cell, the X axis as the first row and the Y axis
    as the first column."""
    lines: list[str] = []
    if table.kind == "curve":
        lines = [f"{_fmt(x)},{_fmt(y)}" for x, y in zip(table.x, table.y)]
    else:
        lines.append("," + ",".join(_fmt(x) for x in table.x))
        for y, row in zip(table.y, table.z):
            lines.append(_fmt(y) + "," + ",".join(_fmt(v) for v in row))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_map_values(name: str, table: LookupTable) -> LookupTable:
    """Overwrite the stored CSV copy with the edited `table` and set `edited_at` (27.8).

    All-or-nothing: the previous CSV and JSON are restored if writing, re-reading or the
    metadata update fails, and the error propagates.
    """
    info = _read_meta(name)
    csv_p, json_p = _csv_path(name), _json_path(name)
    old_csv, old_json = csv_p.read_bytes(), json_p.read_bytes()
    try:
        write_table_csv(table, csv_p)
        info.edited_at = datetime.now().isoformat(timespec="seconds")
        _write_meta(info)
        return load_map(name)  # re-parse: what is stored must load back
    except BaseException:
        csv_p.write_bytes(old_csv)
        json_p.write_bytes(old_json)
        raise


def delete_map(name: str) -> None:
    _read_meta(name)  # raises if absent
    _csv_path(name).unlink(missing_ok=True)
    _json_path(name).unlink(missing_ok=True)
