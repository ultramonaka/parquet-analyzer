"""Per-bucket table of a histogram result, as Parquet or CSV (analysis.ja.md 5.37 / 42.8). No Qt.

One row per bucket overlapping the range -- incomplete buckets included whatever the "exclude
incomplete" setting is (the `<var>_complete` column tells them apart). Columns: `window_start`,
`window_end` (a datetime time axis: local wall-clock time like the screen, `timestamp[ms]` without a
time zone; a numeric time axis: float64 seconds), then per variable `<name>` (float64, null = no
value), `<name>_complete` (bool) and, for max / min, `<name>_at` (the time of the extreme, same type
as the window columns). Parquet carries the settings as key-value metadata `parquet_analyzer`
(JSON); CSV has columns only and the same JSON goes to a sidecar `<file>.json`.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .histogram import HistogramResult, format_clock

METADATA_KEY = "parquet_analyzer"
HOUR_S = 3600.0


class ExportError(ValueError):
    """Nothing to export (no bucket table in the result)."""


# ---------------------------------------------------------------------------
# Local time (the same wall clock as the screen axis: datetime.fromtimestamp)
# ---------------------------------------------------------------------------


def local_offset_s(x: float) -> float:
    """Local UTC offset (seconds) at epoch second `x` (0 when the time cannot be converted)."""
    try:
        return (datetime.fromtimestamp(x) - datetime.fromtimestamp(x, UTC).replace(tzinfo=None)).total_seconds()
    except (OverflowError, OSError, ValueError):
        return 0.0


def local_datetime_ms(x: np.ndarray) -> np.ndarray:
    """Epoch seconds -> naive local `datetime64[ms]` (NaT for non-finite). The UTC offset is looked
    up once per hour and only the rows whose hour contains an offset change (a DST transition) are
    converted one by one, so a million rows stay fast and exact."""
    x = np.asarray(x, dtype=np.float64)
    ok = np.isfinite(x)
    out = np.full(len(x), np.iinfo(np.int64).min, dtype=np.int64)  # NaT
    if not ok.any():
        return out.view("datetime64[ms]")
    xv = x[ok]
    hour = np.floor(xv / HOUR_S).astype(np.int64)
    lo, hi = int(hour.min()), int(hour.max())
    if hi - lo > 200_000:  # a very sparse span: one lookup per value instead of one per hour
        out[ok] = np.round((xv + np.array([local_offset_s(float(v)) for v in xv])) * 1000.0).astype(np.int64)
        return out.view("datetime64[ms]")
    grid = np.array([local_offset_s(h * HOUR_S) for h in range(lo, hi + 2)])  # offset at each hour start (+1 past the end)
    off = grid[hour - lo]
    changes = grid[hour - lo] != grid[hour - lo + 1]  # an offset change inside this hour
    for i in np.flatnonzero(changes):
        off[i] = local_offset_s(float(xv[i]))
    out[ok] = np.round((xv + off) * 1000.0).astype(np.int64)
    return out.view("datetime64[ms]")


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def build_columns(result: HistogramResult, time_is_datetime: bool) -> tuple[dict[str, np.ndarray], dict]:
    """(ordered columns, name map {window_start, window_end, variables: {variable: value column}}). Values are numpy arrays: float64 / bool / datetime64[ms]
    (a datetime axis; NaT = null). Raises ExportError when the result has no bucket table.
    The time-of-day view (42.13) writes one row per slot instead: `slot_start` ("HH:MM" text), then per
    variable `<name>` (the slot value, null = none), `<name>_valid_s` (valid time summed over the days) and
    `<name>_days`."""
    if result.settings.view == "timeofday":
        return _build_slot_columns(result)
    if result.bucket_start is None or not len(result.bucket_start):
        raise ExportError("no_buckets")
    conv = local_datetime_ms if time_is_datetime else (lambda a: np.asarray(a, dtype=np.float64))
    cols: dict[str, np.ndarray] = {}
    mapping: dict[str, object] = {}
    var_cols: dict[str, str] = {}
    start_name, end_name = "window_start", "window_end"
    cols[start_name] = conv(result.bucket_start)
    cols[end_name] = conv(result.bucket_end)
    used = [start_name, end_name]
    for v in result.variables:
        want = [v.name, v.name + "_complete"] + ([v.name + "_at"] if v.bucket_at is not None else [])
        # a variable's columns must not collide with any column already there (or its own siblings)
        got = []
        for nm in want:
            cand, k = nm, 1
            while cand in used:
                k += 1
                cand = f"{nm}_{k}"
            used.append(cand)
            got.append(cand)
        cols[got[0]] = np.asarray(v.bucket_values, dtype=np.float64)
        cols[got[1]] = np.asarray(v.bucket_complete, dtype=bool)
        var_cols[v.name] = got[0]
        if v.bucket_at is not None:
            cols[got[2]] = conv(v.bucket_at)
    mapping.update(window_start=start_name, window_end=end_name, variables=var_cols)
    return cols, mapping


def _build_slot_columns(result: HistogramResult) -> tuple[dict[str, np.ndarray], dict]:
    if result.slot_start is None or not len(result.slot_start):
        raise ExportError("no_buckets")
    origin = result.settings.origin_s
    cols: dict[str, np.ndarray] = {"slot_start": np.array([format_clock(origin + float(x)) for x in result.slot_start])}
    used = ["slot_start"]
    var_cols: dict[str, str] = {}
    valid_cols: dict[str, str] = {}
    days_cols: dict[str, str] = {}
    for v in result.variables:
        got = []
        for nm in (v.name, v.name + "_valid_s", v.name + "_days"):
            cand, k = nm, 1
            while cand in used:
                k += 1
                cand = f"{nm}_{k}"
            used.append(cand)
            got.append(cand)
        cols[got[0]] = np.asarray(v.slot_values, dtype=np.float64)
        cols[got[1]] = np.asarray(v.slot_valid_s, dtype=np.float64)
        cols[got[2]] = np.asarray(v.slot_days, dtype=np.int64)
        var_cols[v.name], valid_cols[v.name], days_cols[v.name] = got
    return cols, {"slot_start": "slot_start", "variables": var_cols, "valid_s": valid_cols, "days": days_cols}


def build_table(cols: dict[str, np.ndarray]) -> pa.Table:
    arrays, names = [], []
    for name, arr in cols.items():
        if arr.dtype.kind == "U":
            arrays.append(pa.array(arr.tolist(), type=pa.string()))
        elif arr.dtype.kind == "i":
            arrays.append(pa.array(arr, type=pa.int64()))
        elif arr.dtype.kind == "M":
            arrays.append(pa.array(arr, type=pa.timestamp("ms"), mask=np.isnat(arr)))
        elif arr.dtype.kind == "f":
            arrays.append(pa.array(arr, type=pa.float64(), mask=np.isnan(arr)))
        else:
            arrays.append(pa.array(arr, type=pa.bool_()))
        names.append(name)
    return pa.table(dict(zip(names, arrays)))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def build_metadata(
    result: HistogramResult, *, source_path: str, time_column: str | None, time_is_datetime: bool,
    range_s: tuple[float, float] | None, app_version: str, columns: dict | None = None,
) -> dict:
    """The settings behind the table (42.8): source file, time column, range, variables, bucket
    settings (stage 1 included for two stages), gap setting, app version."""
    s = result.settings
    rng = None
    if range_s is not None:
        rng = {"start": range_s[0], "end": range_s[1]}
        if time_is_datetime:
            rng["start_local"] = _fmt_local(range_s[0])
            rng["end_local"] = _fmt_local(range_s[1])
    buckets = {
        "length_s": s.bucket_s, "origin_s": s.origin_s, "aggregation": s.agg, "two_stage": bool(s.two_stage),
    }
    if s.agg == "integral" or (s.two_stage and s.stage1_agg == "integral"):
        buckets["integral_unit_s"] = s.integral_unit_s
    if s.two_stage:
        buckets["stage1"] = {"window_s": s.stage1_s, "aggregation": s.stage1_agg}
    tf = s.time_filter
    time_filter = None
    if tf is not None and tf.enabled:  # 42.10: the filter behind the table (absent buckets are not written)
        time_filter = {
            "enabled": True, "start": f"{tf.start_s // 3600}:{tf.start_s % 3600 // 60:02d}",
            "end": f"{tf.end_s // 3600}:{tf.end_s % 3600 // 60:02d}", "weekdays": list(tf.weekdays),
            "target_seconds": result.target_seconds,
        }
    tod = None
    if s.view == "timeofday":  # 42.13: the slot settings behind the table
        tod = {"slot_s": s.slot_s, "origin_s": s.origin_s, "aggregation": s.tod_agg,
               "per_day": bool(s.tod_per_day)}
        if s.tod_agg == "integral":
            tod["integral_unit_s"] = s.integral_unit_s
    meta = {
        "app": "ParquetAnalyzer",
        "app_version": app_version,
        "source_path": source_path,
        "time_column": time_column,
        "time_is_datetime": bool(time_is_datetime),
        "time_zone_note": "window times are local wall-clock time (no time zone)" if time_is_datetime else "seconds",
        "range": rng,
        "variables": [v.name for v in result.variables],
        "buckets": None if s.view == "timeofday" else buckets,
        "gap": {"mode": "auto" if s.gap_s is None else "fixed", "seconds_used": result.gap_s if result.gap_s is not None and math.isfinite(result.gap_s) else None},
        "view": s.view,
        "time_filter": time_filter,
        "time_of_day": tod,
        "columns": columns or {},
    }
    if s.view == "timeofday":
        meta["slot_count"] = int(len(result.slot_start)) if result.slot_start is not None else 0
    else:
        meta["bucket_count"] = int(len(result.bucket_start)) if result.bucket_start is not None else 0
    return meta


def _fmt_local(x: float) -> str:
    try:
        return datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (OverflowError, OSError, ValueError):
        return repr(x)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_parquet(path: str | Path, cols: dict[str, np.ndarray], metadata: dict) -> int:
    table = build_table(cols).replace_schema_metadata({METADATA_KEY: json.dumps(metadata, ensure_ascii=False)})
    pq.write_table(table, str(path))
    return table.num_rows


def _csv_column(arr: np.ndarray) -> list[str]:
    if arr.dtype.kind in "Ui":
        return [str(x) for x in arr.tolist()]
    if arr.dtype.kind == "M":
        text = np.datetime_as_string(arr, unit="ms")  # 2026-03-02T12:00:00.000 ('NaT' for null)
        return ["" if t == "NaT" else t.replace("T", " ") for t in text.tolist()]
    if arr.dtype.kind == "f":
        return ["" if math.isnan(x) else repr(x) for x in arr.tolist()]
    return ["True" if b else "False" for b in arr.tolist()]


def write_csv(path: str | Path, cols: dict[str, np.ndarray], metadata: dict) -> int:
    """Columns only (no comment lines, so other tools read it plainly); the settings go to the
    sidecar `<file>.json` (same stem). Numbers use repr(float) (44.4), times YYYY-MM-DD HH:MM:SS.fff."""
    path = Path(path)
    text = [_csv_column(a) for a in cols.values()]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(list(cols.keys()))
        w.writerows(zip(*text))
    sidecar_path(path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(text[0]) if text else 0


def sidecar_path(path: str | Path) -> Path:
    return Path(path).with_suffix(".json")


# ---------------------------------------------------------------------------
# Default file name
# ---------------------------------------------------------------------------


def length_token(seconds: float) -> str:
    """"1d" / "3h" / "3min" / "90s" for a window or bucket length in a file name."""
    for tok, div in (("d", 86400.0), ("h", 3600.0), ("min", 60.0)):
        if seconds >= div and abs(seconds / div - round(seconds / div)) < 1e-9:
            return f"{round(seconds / div)}{tok}"
    return f"{seconds:g}s"


def default_file_stem(result: HistogramResult) -> str:
    """`<first variable>_<bucket>_<aggregation>`, e.g. `power_kw_1d_3min-mean-max` (two stages:
    window length, stage-1 and stage-2 aggregation)."""
    s = result.settings
    first = result.variables[0].name if result.variables else "histogram"
    first = re.sub(r"[^\w.\-]+", "_", first).strip("_") or "histogram"
    if s.view == "timeofday":
        return f"{first}_tod_{length_token(s.slot_s)}_{s.tod_agg}"
    agg = f"{length_token(s.stage1_s)}-{s.stage1_agg}-{s.agg}" if s.two_stage else s.agg
    return f"{first}_{length_token(s.bucket_s)}_{agg}"
