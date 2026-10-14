from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .. import config


@dataclass
class SourceDef:
    parquet_path: str
    path_type: str = "relative"  # "relative" | "absolute"
    # Path from the view JSON's directory (os.path.relpath), written next to the absolute
    # `parquet_path` so a moved data+view folder still opens (persistence 34). None when it
    # can't be made (e.g. another drive) or in a view saved before this key existed.
    relative_path: str | None = None


@dataclass
class DerivedVariableDef:
    name: str
    expression: str


@dataclass
class SeriesDef:
    variable: str
    color: str
    visible: bool = True  # hidden series stay hidden across save/load (23.5); old views -> True


@dataclass
class PlotDef:
    plot_id: str
    series: list[SeriesDef]
    y_axis_range: tuple[float | None, float | None] = (None, None)
    height_ratio: float | None = None  # share of the plot area's height (22.2); None = equal split
    x_linked: bool = True  # in the X-linked group (24.5); old views -> True
    x_axis_range: tuple[float, float] | None = None  # own X range, only for an independent plot (24.5)


@dataclass
class View:
    """A saved screen layout (specification.md 5.8, detailed_specification.md 8章)."""

    view_name: str
    source: SourceDef
    plots: list[PlotDef]
    derived_variables: list[DerivedVariableDef] = field(default_factory=list)
    x_axis_range: tuple[float, float] | None = None
    downsample_enabled: bool = True
    time_column: str | None = None  # the X-axis column at save time (persistence 33); None = old view
    schema_version: str = "1"


_WIN_RESERVED_CHARS = '<>:"|?*'


def view_path(view_name: str) -> Path:
    # `Path.__truediv__` treats a right-hand operand starting with "/" as an absolute
    # path, *discarding* the left side entirely — so an unvalidated view_name like
    # "/tmp/evil" would silently write outside PARQUET_ANALYZER_DATA_DIR altogether.
    # Requiring the name to survive a round-trip through Path(...).name rules that out,
    # along with "..", other embedded separators, and empty/blank names.
    # (a NUL would only fail later, at the file-system call, with an obscure message)
    if not view_name.strip() or "\x00" in view_name or view_name != Path(view_name).name:
        raise ValueError(f"invalid view name: {view_name!r}")
    # Windows rejects these in file names (and a trailing dot/space); fail with a clear
    # ValueError instead of an obscure OSError from the file system call
    if sys.platform == "win32" and (any(c in _WIN_RESERVED_CHARS for c in view_name) or view_name[-1] in ". "):
        raise ValueError(f"invalid view name on Windows: {view_name!r}")
    return config.PARQUET_ANALYZER_DATA_DIR / f"{view_name}.json"


def list_views() -> list[str]:
    data_dir = config.PARQUET_ANALYZER_DATA_DIR
    if not data_dir.exists():
        return []
    return sorted(p.stem for p in data_dir.glob("*.json"))


def delete_view(view_name: str) -> None:
    """Remove a saved view's JSON (detailed_specification.md 20.1). Same name validation
    as `view_path`; raises FileNotFoundError if it doesn't exist."""
    view_path(view_name).unlink()


def view_saved_at(view_name: str) -> datetime:
    """When the view file was last written (mtime, local time). The schema has no
    saved-at field (detailed_specification.md 20.1)."""
    return datetime.fromtimestamp(view_path(view_name).stat().st_mtime)


def save_view(view: View) -> Path:
    path = view_path(view.view_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": view.schema_version,
        "view_name": view.view_name,
        "source": asdict(view.source),
        "time_column": view.time_column,
        "derived_variables": [asdict(v) for v in view.derived_variables],
        "plots": [
            {
                "plot_id": p.plot_id,
                "series": [asdict(s) for s in p.series],
                "y_axis_range": list(p.y_axis_range),
                "height_ratio": p.height_ratio,
                "x_linked": p.x_linked,
                "x_axis_range": list(p.x_axis_range) if p.x_axis_range else None,
            }
            for p in view.plots
        ],
        "x_axis_range": list(view.x_axis_range) if view.x_axis_range else None,
        "downsample": {"enabled": view.downsample_enabled},
    }
    # Write to a temp file and rename over the target, like settings.json (persistence 35.1): a failed
    # or interrupted write (disk full, ...) must not destroy the view that was already saved
    # under this name. The ".json.tmp" name is not matched by `list_views`' "*.json" glob.
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return path


def relative_source_path(view_name: str, parquet_path: str) -> str | None:
    """`parquet_path` relative to the directory the view JSON will live in (persistence 34),
    or None if it can't be expressed (different drive on Windows, etc.)."""
    try:
        base = os.path.abspath(view_path(view_name).parent)
        return os.path.relpath(os.path.abspath(parquet_path), base)
    except ValueError:
        return None


def resolve_source(view: View) -> tuple[Path, str]:
    """(path, how) for the view's Parquet file (persistence 34). `how` is "absolute",
    "relative_path" (the saved `relative_path`, resolved from the view JSON's directory),
    "relative" (legacy `path_type="relative"` where `parquet_path` itself is relative) or
    "unresolved" (nothing exists; the absolute/legacy path is returned so the caller's usual
    open error names it)."""
    src = view.source
    base = view_path(view.view_name).parent
    if src.path_type == "absolute":
        absolute = Path(src.parquet_path)
        if absolute.exists():
            return absolute, "absolute"
        if src.relative_path:
            candidate = (base / src.relative_path).resolve()
            if candidate.exists():
                return candidate, "relative_path"
        return absolute, "unresolved"
    return (base / src.parquet_path).resolve(), "relative"


def resolve_source_path(view: View) -> Path:
    return resolve_source(view)[0]


def _valid_ratio(v) -> float | None:
    """height_ratio only if it is a finite non-negative number (hand-edited JSON may hold
    anything); else None = equal split."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _valid_y_range(v) -> tuple[float | None, float | None]:
    """A plot's saved Y range with every non-finite / non-numeric end turned into None
    (= "no saved range"): hand-edited JSON can hold NaN/Infinity (Python's json accepts
    them) and pyqtgraph's setYRange raises on them, which aborted the rest of the view
    load (persistence 35.1, dev rules "never hand pyqtgraph a non-finite range")."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return (None, None)
    return tuple(float(x) if _is_num(x) and math.isfinite(x) else None for x in v)  # type: ignore[return-value]


def _valid_range(v) -> tuple[float, float] | None:
    """A per-plot X range only if it is a pair of finite numbers (hand-edited JSON may hold
    anything); else None."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
        return None
    return float(v[0]), float(v[1])


class ViewFormatError(ValueError):
    """A view file whose JSON is well-formed but whose shape is wrong (persistence 35).

    `path` is the item path (`plots[0].series[1].color`), `expected` one of the keys of
    `_TYPE_NAMES` or "required" (key missing), `actual` the kind of the value found. The
    UI turns these into a translated message (`ui.view_manager.view_error_text`); `str()`
    is the English fallback."""

    def __init__(self, path: str, expected: str, actual: str):
        self.path, self.expected, self.actual = path, expected, actual
        if expected == "required":
            super().__init__(f"{path}: required item is missing")
        else:
            super().__init__(f"{path}: {expected} required ({actual})")


def _kind(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
}


def _get(obj: dict, key: str, kind: str, path: str, required: bool = False):
    """`obj[key]` checked against `kind` ("object"/"list"/"string"). Absent or null optional
    items give None; an absent required one raises, a null required one raises with "null"."""
    where = f"{path}.{key}" if path else key
    if key not in obj:
        if required:
            raise ViewFormatError(where, "required", "")
        return None
    v = obj[key]
    if v is None and not required:
        return None
    if not _CHECKS[kind](v):
        raise ViewFormatError(where, kind, _kind(v))
    return v


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pair(obj: dict, key: str, path: str, nullable_items: bool):
    """A 2-element list of numbers (items may be null when `nullable_items`), or None."""
    v = _get(obj, key, "list", path)
    if v is None:
        return None
    where = f"{path}.{key}" if path else key
    if len(v) != 2:
        raise ViewFormatError(where, "pair", "list")
    for i, x in enumerate(v):
        if not (_is_num(x) or (nullable_items and x is None)):
            raise ViewFormatError(f"{where}[{i}]", "number", _kind(x))
    return v


def _check_payload(payload) -> None:
    """Validate the shape of a view JSON (persistence 35). Unknown keys are ignored."""
    if not isinstance(payload, dict):
        raise ViewFormatError("(top level)", "object", _kind(payload))
    _get(payload, "view_name", "string", "", required=True)
    src = _get(payload, "source", "object", "", required=True)
    _get(src, "parquet_path", "string", "source", required=True)
    _get(src, "path_type", "string", "source")
    plots = _get(payload, "plots", "list", "", required=True)
    for i, p in enumerate(plots):
        pp = f"plots[{i}]"
        if not isinstance(p, dict):
            raise ViewFormatError(pp, "object", _kind(p))
        _get(p, "plot_id", "string", pp, required=True)
        series = _get(p, "series", "list", pp, required=True)
        for j, s in enumerate(series):
            sp = f"{pp}.series[{j}]"
            if not isinstance(s, dict):
                raise ViewFormatError(sp, "object", _kind(s))
            _get(s, "variable", "string", sp, required=True)
            _get(s, "color", "string", sp, required=True)
        _pair(p, "y_axis_range", pp, nullable_items=True)
    derived = _get(payload, "derived_variables", "list", "")
    for i, d in enumerate(derived or []):
        dp = f"derived_variables[{i}]"
        if not isinstance(d, dict):
            raise ViewFormatError(dp, "object", _kind(d))
        _get(d, "name", "string", dp, required=True)
        _get(d, "expression", "string", dp, required=True)
    _pair(payload, "x_axis_range", "", nullable_items=False)
    _get(payload, "downsample", "object", "")


def load_view(view_name: str) -> View:
    path = view_path(view_name)
    # utf-8-sig: a BOM (Notepad, some editors) is accepted instead of failing the whole view.
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _check_payload(payload)
    # Read known keys only and ignore the rest, so a view written by a newer version
    # (extra keys) still loads (persistence 8章, 2026-10-02).
    src = payload["source"]
    rel = src.get("relative_path")
    source = SourceDef(
        src["parquet_path"], src.get("path_type") or "relative", rel if isinstance(rel, str) and rel else None
    )
    tc = payload.get("time_column")
    derived = [
        DerivedVariableDef(name=d["name"], expression=d["expression"])
        for d in payload.get("derived_variables") or []
    ]
    plots = [
        PlotDef(
            plot_id=p["plot_id"],
            series=[SeriesDef(variable=s["variable"], color=s["color"], visible=s.get("visible", True) is not False) for s in p["series"]],
            y_axis_range=_valid_y_range(p.get("y_axis_range")),
            height_ratio=_valid_ratio(p.get("height_ratio")),
            x_linked=p.get("x_linked", True) is not False,
            x_axis_range=_valid_range(p.get("x_axis_range")),
        )
        for p in payload["plots"]
    ]
    x_range = _valid_range(payload.get("x_axis_range"))  # non-finite -> None (see _valid_y_range)
    enabled = (payload.get("downsample") or {}).get("enabled", True)
    return View(
        view_name=payload["view_name"],
        source=source,
        plots=plots,
        derived_variables=derived,
        x_axis_range=x_range,
        downsample_enabled=enabled if isinstance(enabled, bool) else True,
        time_column=tc if isinstance(tc, str) and tc else None,
        schema_version=payload.get("schema_version", "1"),
    )
