"""Histogram computation (analysis.ja.md 5.33 / 42.3). Pure numpy, no Qt.

Two kinds of aggregation:

* "plain": the distribution of the values of each row of a range, counted by rows or by time
  (each row lasts until the next row; "how many hours at what kW"). Two passes over the
  range (min/max, then a fine 65,536-bin histogram) plus, for an exact row-count median, a
  third pass that collects only the values of the middle bin(s) -- the `chunked_stats`
  approach (data 36.8). The display bins are built by merging fine bins, so no further read
  is needed.
* "bucket": the time axis is cut into buckets of a fixed length (local time, like the screen
  axis), each bucket is reduced to one value (sum / integral / mean / max / min), and the
  distribution of those values is drawn (e.g. daily energy). The bucket values stay in memory.

The ranges are read in chunks of `HIST_CHUNK_ROWS` through `fetch_value(name, a, b)` /
`fetch_time(a, b)`; each chunk reads one extra time stamp (the next row's) so the row's
duration `w_i = x[i+1] - x[i]` is known. The last row of the range has `w = 0`; a duration
longer than the gap threshold also becomes 0 and is counted as a gap.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import numpy as np

from .numeric import TimeOrderScanner

HIST_CHUNK_ROWS = 1_000_000  # ~175 MB working peak for bucketed two-stage runs (42.9)
HIST_FINE_BINS = 65_536
HIST_MAX_BUCKETS = 1_000_000
HIST_COMPLETE_RATIO = 0.9
HIST_MEDIAN_COLLECT_MAX = 1_000_000  # most values collected in memory for the exact median (42.3)
HIST_GAP_FACTOR = 5.0  # auto gap = 5 x the median adjacent interval
HIST_MIN_BINS = 2
HIST_MAX_BINS = 1000
HIST_TREND_BAR_MAX = 2000  # more buckets than this: the trend is drawn as a step line, not bars (42.12)

DAY_S = 86400

VIEWS = ("dist", "duration", "trend", "timeofday")  # display kinds (42.11, 42.13); "duration" / "timeofday" compute differently
TOD_SLOTS_S = (600.0, 900.0, 1800.0, 3600.0, 7200.0, 10800.0)  # time-of-day slot lengths (42.13)
TOD_AGGREGATIONS = ("mean", "integral")
AGGREGATIONS = ("sum", "integral", "mean", "max", "min")
STAGE2_AGGREGATIONS = ("max", "min", "mean", "sum")  # 42.8: no integral on the second stage
INTEGRAL_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0}


class HistogramError(ValueError):
    """A range/setting that cannot be computed; `code` is a stable id the UI maps to text."""

    def __init__(self, code: str, **info) -> None:
        super().__init__(code)
        self.code = code
        self.info = info


class Cancelled(Exception):
    """Raised inside a computation when `cancelled()` turned true (the window closed/superseded)."""


@dataclass
class TimeFilter:
    """Time-of-day / weekday filter (42.10). `start_s` / `end_s`: seconds after local midnight
    (start < end: [start, end); start > end: crosses midnight; start == end: all day); `weekdays`:
    0 = Monday .. 6 = Sunday, the weekday of the day a window STARTS on."""

    enabled: bool = False
    start_s: int = 0
    end_s: int = 0
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)


@dataclass
class HistogramSettings:
    mode: str = "plain"  # "plain" | "bucket"
    weight: str = "rows"  # plain only: "rows" | "time"
    bucket_s: float = DAY_S  # bucket length in seconds
    origin_s: int = 0  # bucket start time of day, seconds from local midnight
    agg: str = "sum"
    integral_unit_s: float = 3600.0
    exclude_incomplete: bool = False
    gap_s: float | None = None  # None = auto
    bins: int | None = None  # None = auto (Freedman-Diaconis)
    # Two-stage aggregation (42.8): windows of `stage1_s` reduced by `stage1_agg` first, then the
    # complete windows of each bucket (`bucket_s`, reduced by `agg`) -> one value per bucket.
    two_stage: bool = False
    stage1_s: float = 180.0
    stage1_agg: str = "mean"  # integral uses `integral_unit_s`
    view: str = "dist"  # "dist" | "duration" | "trend" (42.11); "duration" also fills duration_x / duration_v
    time_filter: TimeFilter | None = None  # None / disabled: every row counts (42.10)
    # Time of day (42.13, view "timeofday"): slot length, value (mean = time-weighted / integral) and
    # whether an integral is divided by the number of days that had valid time in the slot.
    slot_s: float = 3600.0
    tod_agg: str = "mean"
    tod_per_day: bool = True


@dataclass
class VariableHistogram:
    name: str
    counts: np.ndarray  # per display bin: rows / seconds (time weight) / buckets
    n: int = 0  # values used (rows, or buckets)
    excluded: int = 0  # rows dropped for NaN/Inf (value or time)
    mean: float = math.nan
    std: float = math.nan
    median: float = math.nan
    median_approx: bool = False  # time-weighted median
    buckets_total: int = 0  # bucket mode: buckets holding at least one row
    incomplete: int = 0  # bucket mode: buckets with valid time / length < HIST_COMPLETE_RATIO
    excluded_buckets: int = 0  # incomplete buckets left out ("exclude" on)
    values: np.ndarray | None = None  # bucket mode: the aggregated values that were histogrammed
    stage1_incomplete: int = 0  # two-stage: first-stage windows with data but incomplete (left out of stage 2)
    # Per-bucket table (42.8), aligned with HistogramResult.bucket_start/end (every bucket that
    # overlaps the range, whatever "exclude incomplete" says): value (NaN = none), complete, and for
    # max / min the time of the extreme (epoch seconds, NaN = none / not max-min).
    bucket_values: np.ndarray | None = None
    bucket_complete: np.ndarray | None = None
    bucket_at: np.ndarray | None = None
    # Duration curve (42.11, view "duration"): duration_v descending, duration_x ascending = the
    # seconds / rows / buckets at or above that value (raw, the window scales it for percent / unit).
    duration_x: np.ndarray | None = None
    duration_v: np.ndarray | None = None
    vmax: float = math.nan  # the largest value used (legend of the duration curve)
    # Time of day (42.13): per slot the value (NaN = none), the valid time (s) summed over the days and
    # the number of days that had valid time in the slot, and how many of those days covered the slot only
    # partly (range ends, gap edges, DST days; the per-day integral still counts them as whole days).
    slot_values: np.ndarray | None = None
    slot_valid_s: np.ndarray | None = None
    slot_days: np.ndarray | None = None
    slot_partial_days: np.ndarray | None = None


@dataclass
class HistogramResult:
    settings: HistogramSettings
    row_start: int
    row_end: int
    edges: np.ndarray  # shared display bin edges (len = bins + 1)
    variables: list[VariableHistogram] = field(default_factory=list)
    y_unit: str = "rows"  # "rows" | "seconds" | "buckets"
    gap_s: float | None = None  # threshold used (None: rows weighting / no time axis involved)
    gap_count: int = 0  # intervals of the time axis longer than the gap threshold (counted once, 42.3)
    bins_auto: bool = True
    bucket_count: int = 0
    time_name: str | None = None
    bucket_start: np.ndarray | None = None  # epoch seconds of the buckets overlapping the range (42.8)
    bucket_end: np.ndarray | None = None
    target_seconds: float | None = None  # time filter on: target time inside [first, last time] (42.10)
    slot_start: np.ndarray | None = None  # time of day view: seconds after the origin of each slot (42.13)

    @property
    def bins(self) -> int:
        return max(0, len(self.edges) - 1)


# ---------------------------------------------------------------------------
# Bucket boundaries (local time)
# ---------------------------------------------------------------------------


def validate_bucket(length_s: float) -> str | None:
    """None if `length_s` is a legal bucket length, else the reason id (42.2): at most one day
    -> must divide 24 h; more than a day -> a whole number of days."""
    if not (math.isfinite(length_s) and length_s > 0):
        return "bucket_not_positive"
    if length_s <= DAY_S:
        ratio = DAY_S / length_s
        if abs(ratio - round(ratio)) > 1e-9 * max(1.0, ratio):
            return "bucket_not_divisor"
        return None
    days = length_s / DAY_S
    if abs(days - round(days)) > 1e-9:
        return "bucket_not_whole_days"
    return None


def validate_two_stage(stage1_s: float, bucket_s: float) -> str | None:
    """None if (first-stage window, second-stage bucket) is legal, else the reason id (42.8): the
    window divides a day and divides the bucket, and the bucket is longer."""
    if not (math.isfinite(stage1_s) and stage1_s > 0):
        return "stage1_not_positive"
    if stage1_s > DAY_S or validate_bucket(stage1_s) is not None:
        return "stage1_not_divisor"
    reason = validate_bucket(bucket_s)
    if reason:
        return reason
    if bucket_s <= stage1_s * (1.0 + 1e-9):
        return "stage2_not_longer"
    ratio = bucket_s / stage1_s
    if abs(ratio - round(ratio)) > 1e-9 * max(1.0, ratio):
        return "stage1_not_divide_stage2"
    return None


def _local_ts(d: date, origin_s: int) -> float:
    """Epoch seconds of local date `d` at `origin_s` seconds after local midnight (naive local
    time, like `datetime.fromtimestamp` on the screen axis)."""
    return datetime.combine(d, dtime(0)).__add__(timedelta(seconds=origin_s)).timestamp()


_EPOCH = datetime(1970, 1, 1)


def _plain_ts(d: date, origin_s: int) -> float:
    """Axis value of date `d` at `origin_s` after its start, with axis 0 = 1970-01-01 00:00 and no
    time zone (numeric time axis, 42.9)."""
    return float((d - _EPOCH.date()).days * 86400 + origin_s)


def _to_datetime(t: float, is_datetime: bool) -> datetime:
    """The (local, or axis-0 based) calendar time of a time-axis value; a value too large to be a date
    (e.g. a microsecond counter used as a numeric axis) is a "not computed" reason, not a crash."""
    try:
        return datetime.fromtimestamp(t) if is_datetime else _EPOCH + timedelta(seconds=t)
    except (OverflowError, OSError, ValueError) as exc:
        raise HistogramError("time_too_large", value=t) from exc


def bucket_edges(t0: float, t1: float, length_s: float, origin_s: int, is_datetime: bool = True) -> np.ndarray:
    """Bucket edges (epoch seconds, ascending) covering [t0, t1], so that the last edge is > t1.

    The first bucket starts at the day start (`origin_s` after local midnight) of the local day
    containing t0. A length of at most one day tiles every local day from its start, cutting at
    the day's end (a DST day of 23/25 h has the same bucket length inside, a shorter/longer
    last bucket); a multiple of days counts calendar days. Raises HistogramError for an
    illegal length or more than HIST_MAX_BUCKETS buckets.

    With `is_datetime` false (a numeric time axis) there is no time zone: axis value 0 is the
    start of a day, days are always 86400 long and nothing is converted to local time (42.9)."""
    reason = validate_bucket(length_s)
    if reason:
        raise HistogramError(reason)
    span = max(0.0, t1 - t0)
    per_day = max(1, round(DAY_S / length_s)) if length_s <= DAY_S else 1
    step_days = max(1, round(length_s / DAY_S)) if length_s > DAY_S else 1
    est = (span / DAY_S + 2) * per_day / step_days
    if est > HIST_MAX_BUCKETS:
        raise HistogramError("too_many_buckets", estimate=int(est))
    ts = _local_ts if is_datetime else _plain_ts
    dt0 = _to_datetime(t0, is_datetime)
    first = dt0.date()
    if dt0.hour * 3600 + dt0.minute * 60 + dt0.second + dt0.microsecond / 1e6 < origin_s:
        first -= timedelta(days=1)  # before today's start time: still yesterday's "day"
    edges: list[float] = []
    d = first
    day_start = ts(d, origin_s)
    if day_start > t0:  # DST oddities: step back until the start is not after t0
        d -= timedelta(days=1)
        day_start = ts(d, origin_s)
    while True:
        next_d = d + timedelta(days=step_days)
        day_end = ts(next_d, origin_s)
        if length_s <= DAY_S:
            count = 1 if length_s >= DAY_S else max(1, int(math.ceil((day_end - day_start) / length_s - 1e-9)))  # last bucket <= L (short = incomplete)
            edges.extend((np.arange(count, dtype=np.float64) * length_s + day_start).tolist())
        else:
            edges.append(day_start)
        if len(edges) > HIST_MAX_BUCKETS:
            raise HistogramError("too_many_buckets", estimate=len(edges))
        d, day_start = next_d, day_end
        if day_start > t1:
            edges.append(day_start)
            break
    return np.asarray(edges, dtype=np.float64)


# ---------------------------------------------------------------------------
# Time filter: the target time (42.10)
# ---------------------------------------------------------------------------


def _utc_offset(x: float) -> float:
    """Local UTC offset (seconds) at epoch second `x`."""
    try:
        return (datetime.fromtimestamp(x) - datetime.fromtimestamp(x, UTC).replace(tzinfo=None)).total_seconds()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _wall_to_real(d: date, a_s: float, b_s: float) -> list[tuple[float, float]]:
    """The real (epoch-second) intervals whose local wall clock lies in [d 00:00 + a_s, d 00:00 + b_s)
    (a_s / b_s may exceed 24 h: the window runs into the next day). Wall-clock semantics on DST days
    (42.10): wall times that do not exist (spring) are left out, times that happen twice (autumn)
    count both times."""
    base = datetime.combine(d, dtime(0))
    a_dt, b_dt = base + timedelta(seconds=a_s), base + timedelta(seconds=b_s)
    a0, b0 = a_dt.timestamp(), b_dt.timestamp()
    if a0 == a_dt.replace(fold=1).timestamp() and b0 == b_dt.replace(fold=1).timestamp():
        return [(a0, b0)]  # both ends unambiguous: the plain real interval is exactly the wall set
    # an end falls into a DST gap / overlap: cut the real line at the offset changes and intersect
    aw, bw = (a_dt - _EPOCH).total_seconds(), (b_dt - _EPOCH).total_seconds()
    lo, hi = min(a0, b0) - 4 * 3600.0, max(a0, b0) + 4 * 3600.0
    cuts = [lo]
    t = lo
    while t < hi:
        nxt = min(t + 3600.0, hi)
        if _utc_offset(t) != _utc_offset(nxt):
            left, right = t, nxt  # bisect the change to a second
            while right - left > 1.0:
                mid = (left + right) / 2.0
                left, right = (mid, right) if _utc_offset(mid) == _utc_offset(left) else (left, mid)
            cuts.append(right)
        t = nxt
    cuts.append(hi)
    out = []
    for p, q in zip(cuts[:-1], cuts[1:]):
        off = _utc_offset((p + q) / 2.0)
        lo_, hi_ = max(p, aw - off), min(q, bw - off)
        if hi_ > lo_:
            out.append((lo_, hi_))
    return out


class _Target:
    """The target time `A` of a time filter inside a range: sorted disjoint intervals plus the
    cumulative target time `F(t)` (slope 1 inside the intervals, flat outside; 42.10)."""

    def __init__(self, intervals: list[tuple[float, float]]) -> None:
        merged: list[list[float]] = []
        for lo, hi in sorted(intervals):
            if hi <= lo:
                continue
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        arr = np.asarray(merged, dtype=np.float64).reshape(-1, 2)
        self.starts, self.ends = arr[:, 0].copy(), arr[:, 1].copy()
        lens = self.ends - self.starts
        cum = np.concatenate(([0.0], np.cumsum(lens)))
        self.total = float(cum[-1])
        self._knots = np.empty(2 * len(lens))
        self._vals = np.empty(2 * len(lens))
        self._knots[0::2], self._knots[1::2] = self.starts, self.ends
        self._vals[0::2], self._vals[1::2] = cum[:-1], cum[1:]

    def f(self, x) -> np.ndarray:
        """Cumulative target time at `x` (an array of finite epoch seconds)."""
        x = np.asarray(x, dtype=np.float64)
        if not len(self._knots):
            return np.zeros(x.shape)
        return np.interp(x, self._knots, self._vals)

    def contains(self, x) -> np.ndarray:
        """Whether each (finite) `x` lies in a target interval [start, end)."""
        x = np.asarray(x, dtype=np.float64)
        if not len(self.starts):
            return np.zeros(x.shape, dtype=bool)
        i = np.searchsorted(self.starts, x, side="right") - 1
        return (i >= 0) & (x < self.ends[np.maximum(i, 0)])

    def weights(self, tt: np.ndarray, w: np.ndarray, tok: np.ndarray) -> np.ndarray:
        """Row durations restricted to the target time: F(x + w) - F(x), never above `w` (the gap test
        stays on the original `w`, which the caller has already zeroed for gaps)."""
        x = np.where(tok, tt, 0.0)
        wf = self.f(x + w) - self.f(x)
        return np.where(tok, np.minimum(np.maximum(wf, 0.0), w), 0.0)


def active_filter(settings: HistogramSettings) -> TimeFilter | None:
    tf = settings.time_filter
    return tf if tf is not None and tf.enabled else None


def build_target(tf: TimeFilter, t0: float, t1: float, is_datetime: bool = True) -> _Target:
    """The target time of `tf` for the range [t0, t1] (42.10): per local day the window
    [day + start, day + end) (end + 24 h when it crosses midnight, start == end = the 24 h from
    start), kept when its start day's weekday is chosen. A numeric time axis has axis value 0 as the
    day start and no weekdays (42.9). Raises HistogramError for no weekday / too many intervals."""
    if not (math.isfinite(t0) and math.isfinite(t1)):
        return _Target([])
    weekdays = set(tf.weekdays) if is_datetime else set(range(7))
    if not weekdays:
        raise HistogramError("no_weekdays")
    a = float(tf.start_s)
    b = float(tf.end_s) if tf.end_s > tf.start_s else float(tf.end_s) + DAY_S
    dt0, dt1 = _to_datetime(t0, is_datetime), _to_datetime(t1, is_datetime)
    first, last = dt0.date() - timedelta(days=1), dt1.date()
    n_days = (last - first).days + 1
    if 2 * n_days > HIST_MAX_BUCKETS:
        raise HistogramError("too_many_buckets", estimate=2 * n_days)
    out: list[tuple[float, float]] = []
    d = first
    for _ in range(n_days):
        if d.weekday() in weekdays:
            if is_datetime:
                out.extend(_wall_to_real(d, a, b))
            else:
                base = float((d - _EPOCH.date()).days * 86400)
                out.append((base + a, base + b))
        d += timedelta(days=1)
    return _Target(out)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _merge_moments(state: list[float], w: float, mean: float, m2: float) -> None:
    """Merge a chunk's (weight, mean, sum of squared deviations) into `state` = [W, mean, M2]."""
    if w <= 0:
        return
    tot = state[0] + w
    d = mean - state[1]
    state[2] += m2 + d * d * state[0] * w / tot
    state[1] += d * w / tot
    state[0] = tot


def _quantile_frac(counts: np.ndarray, q: float) -> float:
    """Quantile of a histogram as a fraction (0..1) of its range (linear inside the bin); working in
    fractions keeps +-1e308 ranges from overflowing. NaN for an empty histogram."""
    total = float(counts.sum())
    if total <= 0:
        return math.nan
    cum = np.cumsum(counts, dtype=np.float64)
    target = q * total
    i = int(min(np.searchsorted(cum, target, side="left"), len(counts) - 1))
    below = cum[i] - counts[i]
    frac = (target - below) / counts[i] if counts[i] > 0 else 0.0
    return (i + min(max(frac, 0.0), 1.0)) / len(counts)


def _at(lo: float, hi: float, f):
    """The value at fraction `f` of [lo, hi]; `lo*(1-f) + hi*f` does not overflow near +-1e308."""
    return lo * (1.0 - f) + hi * f


def _fd_bins(per_var: list[tuple[float, int, float]], min_iqr: float = 0.0) -> int:
    """Freedman-Diaconis bin count for variables overlaid on one shared span (= 1 here).
    `per_var`: (IQR, n, own range), all as fractions of the shared span. Each variable gets its own
    width 2*IQR*n^(-1/3) (IQR <= `min_iqr`, the histogram's resolution, counts as 0 -> its own
    range / sqrt(n)); the coarsest width wins, so one narrow variable cannot split the shared span
    into hundreds of bins; clamped to HIST_MIN_BINS..HIST_MAX_BINS (42.3)."""
    widest = 0.0
    for iqr, n, own in per_var:
        if n <= 0:
            continue
        if iqr > min_iqr:
            width = 2.0 * iqr * n ** (-1.0 / 3.0)
        elif own > 0:
            width = own / math.sqrt(n)
        else:
            width = 1.0  # a constant variable: no say in the bin width
        widest = max(widest, width)
    nb = int(math.ceil(1.0 / widest)) if widest > 0 else HIST_MAX_BINS
    return min(max(nb, HIST_MIN_BINS), HIST_MAX_BINS)


def _make_fine_index(lo: float, hi: float):
    """Function mapping values to fine-bin indices over [lo, hi] (half scale: a +-1e308 range must
    not overflow)."""
    scale = HIST_FINE_BINS / (hi * 0.5 - lo * 0.5)

    def fine_index(v: np.ndarray) -> np.ndarray:
        pos = (v * 0.5 - lo * 0.5) * scale  # == (v - lo) * F / (hi - lo)
        pos = np.nan_to_num(pos, nan=0.0, posinf=HIST_FINE_BINS - 1, neginf=0.0)
        return np.clip(pos.astype(np.int64), 0, HIST_FINE_BINS - 1)

    return fine_index


def _exact_median(reader, get_vals, fine_index, counts: np.ndarray, n: int) -> float:
    """Exact median of the `n` finite values of a variable, from its fine histogram `counts`
    (`chunked_stats` approach, data 36.8) with bounded memory: the values of the middle bin(s) are
    collected only when there are at most HIST_MEDIAN_COLLECT_MAX of them; otherwise the bin's
    min/max are found (equal -> that is the median) and a finer histogram of just those values
    narrows it down, repeatedly. `get_vals(a, b)` returns a chunk's finite values."""
    ranks = sorted({(n - 1) // 2, n // 2})
    sels: list = []  # each maps an array to a boolean mask; together they select the candidate values

    def candidates(a: int, b: int) -> np.ndarray:
        vv = get_vals(a, b)
        for f in sels:
            vv = vv[f(vv)]
        return vv

    counts = np.asarray(counts)
    for _level in range(10):
        cum = np.cumsum(counts)
        bins = [int(np.searchsorted(cum, r, side="right")) for r in ranks]
        b1, b2 = min(bins), max(bins)
        below = int(cum[b1] - counts[b1])
        ranks = [r - below for r in ranks]
        size = int(counts[b1:b2 + 1].sum())
        sels.append(lambda vv, f=fine_index, b1=b1, b2=b2: (lambda i: (i >= b1) & (i <= b2))(f(vv)))
        if size <= HIST_MEDIAN_COLLECT_MAX:
            break
        vmin, vmax = math.inf, -math.inf
        for a, b in reader.chunks():
            vv = candidates(a, b)
            if len(vv):
                vmin, vmax = min(vmin, float(vv.min())), max(vmax, float(vv.max()))
        if vmin == vmax:  # all candidates are one value: no need to collect them
            return vmin
        fine_index = _make_fine_index(vmin, vmax)
        counts = np.zeros(HIST_FINE_BINS, dtype=np.int64)
        for a, b in reader.chunks():
            vv = candidates(a, b)
            if len(vv):
                counts += np.bincount(fine_index(vv), minlength=HIST_FINE_BINS)
    picked = [candidates(a, b) for a, b in reader.chunks()]
    allv = np.sort(np.concatenate([p for p in picked if len(p)]))
    return float(sum(allv[r] for r in ranks) / len(ranks))


def _span_range(lo: float, hi: float) -> tuple[float, float]:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return 0.0, 1.0
    if hi == lo:  # one value: a single centred bin [v-h, v+h] (42.9)
        h = max(0.5, abs(lo) * 1e-9)
        return lo - h, hi + h
    return lo, hi


def _is_single_value(lo: float, hi: float) -> bool:
    """True when the whole range is one finite value (-> one bin, see `_span_range`)."""
    return math.isfinite(lo) and lo == hi


def _merge_fine(fine: np.ndarray, nb: int) -> tuple[np.ndarray, np.ndarray]:
    """(index of the first fine bin of each display bin + the end, merged counts): the display
    edges are rounded to fine-bin boundaries."""
    ix = np.round(np.arange(nb + 1) * (len(fine) / nb)).astype(np.int64)
    ix[-1] = len(fine)
    merged = np.add.reduceat(fine, ix[:-1]) if nb <= len(fine) else fine
    return ix, merged


def row_durations(t: np.ndarray, n: int) -> np.ndarray:
    """Row durations w_i = t[i+1] - t[i] for the first `n` rows of a time chunk `t` (which holds
    n or n+1 values); the last row of the range (no next stamp) gets 0; NaN/negative -> 0."""
    w = np.zeros(n, dtype=np.float64)
    d = np.diff(t)
    k = min(len(d), n)
    w[:k] = d[:k]
    w = np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0)
    w[w < 0] = 0.0
    return w


class _Reader:
    """Chunked access shared by the passes."""

    def __init__(self, fetch_value, fetch_time, row_start, row_end, chunk_rows, cancelled):
        self.fetch_value, self.fetch_time = fetch_value, fetch_time
        self.row_start, self.row_end = row_start, row_end
        self.chunk_rows = max(1, int(chunk_rows))
        self.cancelled = cancelled
        self._time_cache: tuple[tuple[int, int], np.ndarray] | None = None
        self.time_reads = 0  # fetch_time calls (the tests check each chunk is read once per pass)

    def chunks(self):
        a = self.row_start
        while a < self.row_end:
            if self.cancelled is not None and self.cancelled():
                raise Cancelled
            b = min(a + self.chunk_rows, self.row_end)
            yield a, b
            a = b

    def value(self, name: str, a: int, b: int) -> np.ndarray:
        v = np.asarray(self.fetch_value(name, a, b), dtype=np.float64)
        return v

    def time(self, a: int, b: int) -> np.ndarray:
        """Time stamps of rows [a, b) plus the next row's (inside the range) for the durations.
        The last chunk is remembered, so every variable of a chunk shares one read (42.3)."""
        if self._time_cache is not None and self._time_cache[0] == (a, b):
            return self._time_cache[1]
        self.time_reads += 1
        t = np.asarray(self.fetch_time(a, min(b + 1, self.row_end)), dtype=np.float64)
        self._time_cache = ((a, b), t)
        return t


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compute_histogram(
    fetch_value: Callable[[str, int, int], np.ndarray],
    fetch_time: Callable[[int, int], np.ndarray],
    names: list[str],
    settings: HistogramSettings,
    row_start: int,
    row_end: int,
    chunk_rows: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    time_name: str | None = None,
    time_is_datetime: bool = True,
) -> HistogramResult:
    """The histogram of `names` over rows [row_start, row_end) (42.3). Raises HistogramError for
    an uncomputable range/setting and `Cancelled` when `cancelled()` becomes true."""
    reader = _Reader(fetch_value, fetch_time, row_start, row_end, chunk_rows or HIST_CHUNK_ROWS, cancelled)
    if settings.view == "timeofday":
        return _compute_timeofday(reader, names, settings, time_name, time_is_datetime)
    if settings.mode == "bucket":
        return _compute_bucket(reader, names, settings, time_name, time_is_datetime)
    return _compute_plain(reader, names, settings, time_name, time_is_datetime)


class _TimeScan:
    """Order check, first/last finite time and the auto gap, fed chunk by chunk."""

    def __init__(self, reader: _Reader) -> None:
        self.reader = reader
        self.scanner = TimeOrderScanner()
        self.tmin, self.tmax = math.inf, -math.inf
        self.medians: list[float] = []

    def feed(self, a: int, b: int, t: np.ndarray) -> None:
        self.scanner.feed(t[: b - a], a - self.reader.row_start)
        fin = t[np.isfinite(t)]
        if len(fin):
            self.tmin, self.tmax = min(self.tmin, float(fin.min())), max(self.tmax, float(fin.max()))
        d = np.diff(t)
        d = d[np.isfinite(d) & (d > 0)]
        if len(d):
            self.medians.append(float(np.median(d)))

    def finish(self, settings: HistogramSettings) -> tuple[float, float, float]:
        """(t_min, t_max, gap_s); raises HistogramError when the time axis is not ascending."""
        rep = self.scanner.report()
        if rep.decreasing_count:
            raise HistogramError("time_not_ascending", first_bad=rep.first_decreasing_index)
        if settings.gap_s is not None and settings.gap_s > 0:
            gap = float(settings.gap_s)
        else:
            gap = HIST_GAP_FACTOR * float(np.median(self.medians)) if self.medians else math.inf
        return self.tmin, self.tmax, gap


def _scan_time(reader: _Reader, settings: HistogramSettings):
    """Pass over the time axis alone (bucket mode): (t_min, t_max, gap_s)."""
    scan = _TimeScan(reader)
    for a, b in reader.chunks():
        scan.feed(a, b, reader.time(a, b))
    return scan.finish(settings)


def time_weights(t: np.ndarray, n: int, gap: float) -> tuple[np.ndarray, int]:
    """(row durations with every interval above `gap` set to 0, number of such gaps) for the first `n`
    rows of a time chunk `t` (n or n+1 values), 42.3; the XY map's aggregate "time" uses it too (44.14)."""
    w = row_durations(t, n)
    big = w > gap
    n_gaps = int(np.count_nonzero(np.isfinite(t[:n]) & big))
    if n_gaps or big.any():
        w = np.where(big, 0.0, w)
    return w, n_gaps


def scan_time_axis(
    fetch_time: Callable[[int, int], np.ndarray],
    row_start: int,
    row_end: int,
    chunk_rows: int | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[float, float, float]:
    """(t_min, t_max, gap_s) of the time axis over rows [row_start, row_end): the order check and the
    automatic gap (5 x the median adjacent interval, a median of the chunks' medians, so `chunk_rows`
    matters on uneven data: the map keeps the histogram's default), shared with the XY map's aggregate
    "time" (analysis 44.14) so the map does not repeat the computation. Raises HistogramError
    ("time_not_ascending") and `Cancelled`."""
    reader = _Reader(None, fetch_time, row_start, row_end, chunk_rows or HIST_CHUNK_ROWS, cancelled)
    return _scan_time(reader, HistogramSettings())


# ---------------------------------------------------------------------------
# Plain mode
# ---------------------------------------------------------------------------


def _compute_plain(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    with np.errstate(over="ignore", invalid="ignore"):  # +-1e308 values overflow the moments, not the bins
        return _compute_plain_impl(reader, names, settings, time_name, time_is_datetime)


def _compute_plain_impl(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    use_time = settings.weight == "time"
    tf = active_filter(settings)
    n_rows = max(0, reader.row_end - reader.row_start)
    result = HistogramResult(settings, reader.row_start, reader.row_end, np.array([0.0, 1.0]),
                             y_unit="seconds" if use_time else "rows", bins_auto=settings.bins is None,
                             time_name=time_name)
    gap = math.inf
    target: _Target | None = None
    if tf is not None:
        # The time filter needs the time axis even for row counting (42.10): scan it first (order
        # check, first / last time, auto gap), then build the target time for [first, last].
        tmin0, tmax0, gap = _scan_time(reader, settings)
        target = build_target(tf, tmin0, tmax0, time_is_datetime)
        result.target_seconds = float(target.f(tmax0) - target.f(tmin0)) if math.isfinite(tmin0) else 0.0
        if use_time:
            result.gap_s = gap
        scan = None
    else:
        scan = _TimeScan(reader) if use_time else None  # row counting never reads the time axis (42.3)

    def chunk_valid(name, a, b):
        """(values, valid mask, durations or None, eligible rows) of one chunk. `eligible` = the rows
        that can count at all (everything without a filter; with one: rows with a bad time stamp,
        rows inside the target and, for the time weight, rows reaching into it) -- the "excluded
        NaN/Inf" count is eligible minus valid."""
        v = reader.value(name, a, b)
        mask = np.isfinite(v)
        w = None
        elig = b - a
        if use_time or target is not None:
            t = reader.time(a, b)
            tt = t[: b - a]
            tok = np.isfinite(tt)
            mask &= tok
            w = time_weights(t, b - a, gap)[0] if use_time else row_durations(t, b - a)
            if target is not None:
                inside = tok & target.contains(np.where(tok, tt, 0.0))
                if use_time:
                    w = target.weights(tt, w, tok)
                    inside |= w > 0
                mask &= inside
                elig = int(np.count_nonzero(inside | ~tok))
        return v, mask, w, elig

    # Pass 1: valid counts and min/max per variable.
    stats = {nm: {"n": 0, "ex": 0, "lo": math.inf, "hi": -math.inf} for nm in names}
    for a, b in reader.chunks():
        if scan is not None:
            scan.feed(a, b, reader.time(a, b))  # the same read serves every variable below
        for nm in names:
            v, mask, _w, elig = chunk_valid(nm, a, b)
            s = stats[nm]
            k = int(mask.sum())
            s["n"] += k
            s["ex"] += elig - k
            if k:
                vv = v[mask]
                s["lo"], s["hi"] = min(s["lo"], float(vv.min())), max(s["hi"], float(vv.max()))
    if scan is not None:
        _t0, _t1, gap = scan.finish(settings)  # order check + auto gap
        result.gap_s = gap
    lo = min((s["lo"] for s in stats.values() if s["n"]), default=math.inf)
    hi = max((s["hi"] for s in stats.values() if s["n"]), default=-math.inf)
    if n_rows == 0 or not any(s["n"] for s in stats.values()):
        result.edges = np.linspace(0.0, 1.0, HIST_MIN_BINS + 1)
        for nm in names:
            result.variables.append(VariableHistogram(
                nm, np.zeros(HIST_MIN_BINS), n=0, excluded=stats[nm]["ex"]))
        return result
    single = _is_single_value(lo, hi)
    lo, hi = _span_range(lo, hi)
    half_span = hi * 0.5 - lo * 0.5
    fine_index = _make_fine_index(lo, hi)

    # Pass 2: fine histograms (rows and, with the time weight, seconds) and moments.
    fine_rows = {nm: np.zeros(HIST_FINE_BINS, dtype=np.int64) for nm in names}
    fine_time = {nm: np.zeros(HIST_FINE_BINS, dtype=np.float64) for nm in names}
    mom_rows = {nm: [0.0, 0.0, 0.0] for nm in names}
    mom_time = {nm: [0.0, 0.0, 0.0] for nm in names}
    for a, b in reader.chunks():
        if use_time:  # gaps are a property of the time axis: counted once, whatever the values are
            t = reader.time(a, b)
            result.gap_count += time_weights(t, b - a, gap)[1]
        for nm in names:
            v, mask, w, _elig = chunk_valid(nm, a, b)
            if not mask.any():
                continue
            vv = v[mask]
            idx = fine_index(vv)
            fine_rows[nm] += np.bincount(idx, minlength=HIST_FINE_BINS)
            cm = float(vv.mean())
            _merge_moments(mom_rows[nm], float(len(vv)), cm, float(((vv - cm) ** 2).sum()))
            if use_time:
                ww = w[mask]  # already zero for gaps (time_weights)
                fine_time[nm] += np.bincount(idx, weights=ww, minlength=HIST_FINE_BINS)
                tw = float(ww.sum())
                if tw > 0:
                    cmw = float((vv * ww).sum() / tw)
                    _merge_moments(mom_time[nm], tw, cmw, float((ww * (vv - cmw) ** 2).sum()))

    # Display bins: FD per variable (smallest width wins) or the requested number.
    fine_used = fine_time if use_time else fine_rows
    if settings.bins is None:
        per_var = []
        for nm in names:
            c = fine_used[nm].astype(np.float64)
            if c.sum() <= 0:
                continue
            iqr = _quantile_frac(c, 0.75) - _quantile_frac(c, 0.25)
            own = (stats[nm]["hi"] * 0.5 - stats[nm]["lo"] * 0.5) / half_span
            per_var.append((iqr, stats[nm]["n"], own))
        nb = _fd_bins(per_var, min_iqr=1.5 / HIST_FINE_BINS)
    else:
        nb = min(max(int(settings.bins), HIST_MIN_BINS), HIST_MAX_BINS)
    if single:
        nb = 1  # one value -> one bin whatever the setting (42.9)
    ix, _ = _merge_fine(fine_rows[names[0]], nb)
    result.edges = _at(lo, hi, ix / HIST_FINE_BINS)

    # Medians: exact for rows (pass 3 collects only the middle bin(s)), approximate for time.
    medians: dict[str, float] = {}
    for nm in names:
        if not use_time and stats[nm]["n"] and stats[nm]["lo"] < stats[nm]["hi"]:
            medians[nm] = _exact_median(
                reader, lambda a, b, nm=nm: (lambda cv: cv[0][cv[1]])(chunk_valid(nm, a, b)),
                fine_index, fine_rows[nm], stats[nm]["n"])

    for nm in names:
        s = stats[nm]
        counts = _merge_fine(fine_time[nm] if use_time else fine_rows[nm].astype(np.float64), nb)[1]
        var = VariableHistogram(nm, np.asarray(counts, dtype=np.float64), n=s["n"], excluded=s["ex"])
        if s["n"]:
            mr, mt = mom_rows[nm], mom_time[nm]
            if use_time and mt[0] > 0:
                var.mean, var.std = mt[1], math.sqrt(mt[2] / mt[0])
                var.median = _at(lo, hi, _quantile_frac(fine_time[nm], 0.5))
                var.median_approx = True
            else:
                var.mean, var.std = mr[1], math.sqrt(mr[2] / mr[0])
                if use_time:  # all durations were 0: fall back to the row distribution
                    var.median = _at(lo, hi, _quantile_frac(fine_rows[nm].astype(np.float64), 0.5))
                    var.median_approx = True
                elif s["lo"] == s["hi"]:
                    var.median = s["lo"]
                else:
                    var.median = medians[nm]
        if s["n"]:
            var.vmax = s["hi"]
        if settings.view == "duration" and s["n"]:
            _plain_duration_curve(var, fine_time[nm] if use_time else fine_rows[nm].astype(np.float64),
                                  lo, hi, single, s["lo"])
        result.variables.append(var)
    return result


def _plain_duration_curve(var: VariableHistogram, fine: np.ndarray, lo: float, hi: float, single: bool,
                          value: float) -> None:
    """Duration curve of the unbucketed mode (42.11): the fine histogram accumulated from the top.
    One point per non-empty fine bin: (lower edge v, time / rows at or above v) -- exact for that
    threshold, the bin's width being the only approximation; empty bins are dropped."""
    nz = np.flatnonzero(fine > 0)[::-1]  # highest bin first
    if not len(nz):
        return
    cum = np.cumsum(fine[::-1].astype(np.float64))[::-1]  # cum[j] = amount in bins >= j
    var.duration_x = cum[nz]
    var.duration_v = np.full(len(nz), value) if single else _at(lo, hi, nz / HIST_FINE_BINS)


# ---------------------------------------------------------------------------
# Bucket mode
# ---------------------------------------------------------------------------


def _first_hit_times(xs: np.ndarray, gid: np.ndarray, hit: np.ndarray, n: int) -> np.ndarray:
    """Per group (`gid` ascending, 0..n-1) the `xs` of its first row where `hit` is true (NaN if none)."""
    pos = np.flatnonzero(hit)
    out = np.full(n, np.nan)
    if len(pos):
        ug, first = np.unique(gid[pos], return_index=True)
        out[ug] = xs[pos[first]]
    return out


def _held_at_target_starts(edges: np.ndarray, xs: np.ndarray, vs: np.ndarray, ws: np.ndarray, ac: dict,
                           target: _Target) -> None:
    """A value held across the start of a target interval counts for the max / min of the bucket that
    contains that start, with the interval start as its time (42.10: the target time then sees the
    value that is still running, as the mean / integral do). On a tie the earlier time wins."""
    lo, hi = float(xs[0]), float(xs[-1] + ws[-1])
    a, b = np.searchsorted(target.starts, lo, side="right"), np.searchsorted(target.starts, hi, side="left")
    for s0 in target.starts[a:b]:
        k = int(np.searchsorted(xs, s0, side="left")) - 1  # the last row starting before s0
        if k < 0 or not s0 < xs[k] + ws[k]:
            continue
        bk = int(np.searchsorted(edges, s0, side="right")) - 1
        if bk < 0 or bk >= len(edges) - 1:
            continue
        v = float(vs[k])
        if v > ac["max"][bk] or (v == ac["max"][bk] and s0 < ac["max_at"][bk]):
            ac["max"][bk], ac["max_at"][bk] = v, s0
        if v < ac["min"][bk] or (v == ac["min"][bk] and s0 < ac["min_at"][bk]):
            ac["min"][bk], ac["min_at"][bk] = v, s0


def _split_durations(edges: np.ndarray, xs: np.ndarray, vs: np.ndarray, ws: np.ndarray, ac: dict,
                     target: _Target | None = None, wfs: np.ndarray | None = None) -> None:
    """Add one chunk's rows to the per-bucket integral `vw`, valid time `w` and held max/min (42.3).

    Row i holds its value over [xs_i, xs_i + ws_i) (ws = 0 for gap rows), and that interval counts
    for every bucket it overlaps. The value is constant inside an interval, so the cumulative
    integral / cumulative valid time are piecewise linear with knots at the interval ends:
    evaluating them at the bucket edges (np.interp) and differencing gives the per-bucket parts.
    The cumulatives start at 0 for each chunk (no huge running sum); chunk intervals are disjoint
    (the last row's duration uses the next chunk's first time stamp), so the parts just add up.
    A bucket also sees the value held across its start (the previous row's interval, if it reaches
    into the bucket), which counts for max / min; its time is the bucket start (42.8; on a tie it
    wins over the rows of the bucket, being the earliest).

    With a time filter (42.10) `target` / `wfs` are given: `wfs` = the row durations restricted to the
    target time, and the cumulatives at an edge inside row k are its base plus F(edge) - F(x_k) (the
    target time inside the row up to the edge) instead of the linear interpolation; a value held
    across a bucket start counts only when that start is in the target."""
    m = len(xs)
    if target is not None:
        _held_at_target_starts(edges, xs, vs, ws, ac, target)
    wd = ws if wfs is None else wfs
    inc = vs * wd
    c_end = np.cumsum(inc)
    t_end = np.cumsum(wd)
    knots = np.empty(2 * m)
    knots[0::2], knots[1::2] = xs, xs + ws
    k0 = int(np.searchsorted(edges, knots[0], side="right")) - 1
    k1 = int(np.searchsorted(edges, knots[-1], side="left"))  # buckets k0 .. k1-1 overlap the chunk
    if target is None:
        cum_c, cum_t = np.empty(2 * m), np.empty(2 * m)
        cum_c[0::2], cum_c[1::2] = c_end - inc, c_end
        cum_t[0::2], cum_t[1::2] = t_end - ws, t_end
        if k1 > k0:
            e = np.clip(edges[k0:k1 + 1], knots[0], knots[-1])
            ac["vw"][k0:k1] += np.diff(np.interp(e, knots, cum_c))
            ac["w"][k0:k1] += np.diff(np.interp(e, knots, cum_t))
    elif k1 > k0:
        e = np.clip(edges[k0:k1 + 1], knots[0], knots[-1])
        kk = np.searchsorted(xs, e, side="right") - 1  # the last row starting at or before each edge
        d_f = target.f(np.minimum(e, xs[kk] + ws[kk])) - target.f(xs[kk])
        ac["vw"][k0:k1] += np.diff(c_end[kk] - inc[kk] + vs[kk] * d_f)
        ac["w"][k0:k1] += np.diff(t_end[kk] - wd[kk] + d_f)
    # Values held across a bucket start: edges strictly inside a row's interval (each bucket is
    # held by at most one row, the intervals being disjoint).
    i0 = np.searchsorted(edges, xs, side="right")
    i1 = np.searchsorted(edges, xs + ws, side="left")
    counts = np.maximum(i1 - i0, 0)
    total = int(counts.sum())
    if total:
        rep = np.repeat(np.arange(m), counts)
        first = np.cumsum(counts) - counts
        bucket = i0[rep] + np.arange(total) - first[rep]
        held = vs[rep]
        if target is not None:  # a value is held across a bucket start only if that start is target time
            ok = target.contains(edges[bucket])
            bucket, held = bucket[ok], held[ok]
        up = held >= ac["max"][bucket]
        ac["max"][bucket[up]] = held[up]
        ac["max_at"][bucket[up]] = edges[bucket[up]]
        dn = held <= ac["min"][bucket]
        ac["min"][bucket[dn]] = held[dn]
        ac["min_at"][bucket[dn]] = edges[bucket[dn]]


def _compute_bucket(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    with np.errstate(over="ignore", invalid="ignore"):  # +-1e308 values overflow the sums, not the bins
        return _compute_bucket_impl(reader, names, settings, time_name, time_is_datetime)


def _accumulate_windows(reader: _Reader, names, edges: np.ndarray, gap: float, result: HistogramResult,
                        target: _Target | None = None) -> dict:
    """One pass over the range adding every row to the windows `edges` (42.3): per variable the
    row count / sum, integral `vw`, valid time `w`, max / min (with the time of the extreme) and the
    excluded-row count. Counts the gaps once into `result.gap_count`. This is the only computation
    of the single-stage mode and the first stage of the two-stage mode (42.8, reused unchanged).
    With a time filter (42.10): count / sum / max / min take the rows inside the target only, the
    integral and valid time use the target-restricted durations of all rows."""
    nbk = len(edges) - 1
    acc = {nm: {
        "cnt": np.zeros(nbk, dtype=np.int64), "sum": np.zeros(nbk), "vw": np.zeros(nbk), "w": np.zeros(nbk),
        "max": np.full(nbk, -np.inf), "min": np.full(nbk, np.inf),
        "max_at": np.full(nbk, np.nan), "min_at": np.full(nbk, np.nan), "ex": 0,
    } for nm in names}
    for a, b in reader.chunks():
        t = reader.time(a, b)
        n = b - a
        tt = t[:n]
        tok = np.isfinite(tt)
        w, n_gaps = time_weights(t, n, gap)
        result.gap_count += n_gaps
        if target is not None:
            inside = tok & target.contains(np.where(tok, tt, 0.0))
            wf = target.weights(tt, w, tok)
        bidx = np.clip(np.searchsorted(edges, np.where(tok, tt, edges[0]), side="right") - 1, 0, nbk - 1)
        for nm in names:
            v = reader.value(nm, a, b)
            mask = np.isfinite(v) & tok
            ac = acc[nm]
            if target is None:
                ac["ex"] += n - int(mask.sum())
            else:  # same eligibility as the plain mode: bad time stamp, in the target, or reaching into it
                elig = ~tok | inside | (wf > 0)
                ac["ex"] += int(np.count_nonzero(elig)) - int(np.count_nonzero(mask & elig))
            if not mask.any():
                continue
            idx = np.flatnonzero(mask)
            xs, vs, ws, bb = tt[idx], v[idx], w[idx], bidx[idx]
            wfs = None
            if target is None:
                rx, rv, rb = xs, vs, bb
            else:
                wfs = wf[idx]
                keep = inside[idx]
                rx, rv, rb = xs[keep], vs[keep], bb[keep]
            if len(rv):
                _add_row_stats(ac, nbk, rx, rv, rb)
            _split_durations(edges, xs, vs, ws, ac, target, wfs)
    return acc


def _add_row_stats(ac: dict, nbk: int, xs: np.ndarray, vs: np.ndarray, bb: np.ndarray) -> None:
    """Row-based accumulators: count and sum (the row's own bucket; bb is ascending as time is) and
    the max / min with the time of the first row that holds them."""
    ac["cnt"] += np.bincount(bb, minlength=nbk)
    ac["sum"] += np.bincount(bb, weights=vs, minlength=nbk)
    u, start = np.unique(bb, return_index=True)
    gid = np.repeat(np.arange(len(u)), np.diff(np.append(start, len(vs))))
    mx, mn = np.maximum.reduceat(vs, start), np.minimum.reduceat(vs, start)
    better = mx > ac["max"][u]  # strictly: on a tie the earlier time (earlier chunk) stays
    if better.any():
        ac["max"][u[better]] = mx[better]
        ac["max_at"][u[better]] = _first_hit_times(xs, gid, vs == mx[gid], len(u))[better]
    better = mn < ac["min"][u]
    if better.any():
        ac["min"][u[better]] = mn[better]
        ac["min_at"][u[better]] = _first_hit_times(xs, gid, vs == mn[gid], len(u))[better]


def _reduce_windows(ac: dict, agg: str, unit_s: float, length_s, present: np.ndarray | None = None):
    """(has, value, incomplete, at) per window from the accumulators (42.3): `has` = the window has a
    value, `incomplete` = it has one but valid time / length < HIST_COMPLETE_RATIO, `at` = time of the
    extreme for max / min (else None). `length_s` is the window length, or with a time filter the
    per-window target time (the completeness denominator, 42.10); `present` (filter on) marks the
    windows with target time -- the others are absent, neither complete nor incomplete."""
    nbk = len(ac["cnt"])
    at = None
    with np.errstate(invalid="ignore", divide="ignore"):
        if agg == "sum":
            has, val = ac["cnt"] > 0, ac["sum"]
        elif agg == "integral":
            has, val = ac["w"] > 0, ac["vw"] / unit_s
        elif agg == "mean":
            has = ac["w"] > 0
            val = ac["vw"] / np.where(has, ac["w"], 1.0)
        elif agg == "max":
            has, val, at = np.isfinite(ac["max"]), ac["max"], ac["max_at"]
        else:
            has, val, at = np.isfinite(ac["min"]), ac["min"], ac["min_at"]
        if present is not None:
            has = has & present
        length = np.broadcast_to(np.asarray(length_s, dtype=np.float64), (nbk,))
        incomplete = has & ~(ac["w"] / length >= HIST_COMPLETE_RATIO)
    return has, val, incomplete, at


def _reduce_stage2(edges1, edges2, has1, val1, incomplete1, agg2: str, windows_per_bucket: float, at1=None):
    """Second stage (42.8): reduce the complete first-stage windows of each bucket. Returns
    (has, value, complete, at) per bucket. A bucket is complete when complete windows / windows a
    full bucket holds (`windows_per_bucket` = bucket / window length, the nominal count, so a short
    DST-day bucket is incomplete just as in the single stage, 42.3) >= HIST_COMPLETE_RATIO;
    max / min carry the instant of the winning window's own extreme when the first stage is max / min
    (`at1`), else the window start; on a tie the earliest."""
    nb2 = len(edges2) - 1
    g_all = np.clip(np.searchsorted(edges2, edges1[:-1], side="right") - 1, 0, nb2 - 1)
    usable = has1 & ~incomplete1 & np.isfinite(val1)
    idx = np.flatnonzero(usable)
    g, v = g_all[idx], val1[idx]
    n_ok = np.bincount(g, minlength=nb2)
    has = n_ok > 0
    at = np.full(nb2, np.nan)
    if agg2 in ("sum", "mean"):
        tot = np.bincount(g, weights=v, minlength=nb2)
        val = tot if agg2 == "sum" else tot / np.where(has, n_ok, 1)
    else:
        val = np.full(nb2, -np.inf if agg2 == "max" else np.inf)
        (np.maximum if agg2 == "max" else np.minimum).at(val, g, v)
        if len(g):
            times = edges1[:-1][idx] if at1 is None else np.asarray(at1)[idx]
            # the winning windows are in time order, so the first hit is the earliest instant
            at = np.where(has, _first_hit_times(times, g, v == val[g], nb2), np.nan)
        val = np.where(has, val, np.nan)
    wpb = np.asarray(windows_per_bucket, dtype=np.float64)  # scalar, or per bucket with a time filter (42.10)
    complete = has & (n_ok / np.where(wpb > 0, wpb, 1.0) >= HIST_COMPLETE_RATIO)
    return has, val, complete, at


def _compute_bucket_impl(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    two = settings.two_stage
    if two:
        if settings.agg not in STAGE2_AGGREGATIONS or settings.stage1_agg not in AGGREGATIONS:
            raise HistogramError("bad_aggregation")
        reason = validate_two_stage(settings.stage1_s, settings.bucket_s)
    else:
        if settings.agg not in AGGREGATIONS:
            raise HistogramError("bad_aggregation")
        reason = validate_bucket(settings.bucket_s)
    if reason:
        raise HistogramError(reason)
    result = HistogramResult(settings, reader.row_start, reader.row_end, np.array([0.0, 1.0]),
                             y_unit="buckets", bins_auto=settings.bins is None, time_name=time_name)
    result.bucket_start = np.zeros(0)
    result.bucket_end = np.zeros(0)
    tmin, tmax, gap = _scan_time(reader, settings)
    result.gap_s = gap
    if not math.isfinite(tmin):  # empty range / no finite time
        result.edges = np.linspace(0.0, 1.0, HIST_MIN_BINS + 1)
        n_rows = max(0, reader.row_end - reader.row_start)
        result.variables = [VariableHistogram(nm, np.zeros(HIST_MIN_BINS), excluded=n_rows) for nm in names]
        return result
    edges = bucket_edges(tmin, tmax, settings.bucket_s, settings.origin_s, time_is_datetime)
    tf = active_filter(settings)
    if two:
        # With a filter the stage-1 windows must tile every stage-2 bucket completely (the buckets run
        # past the last data day), so the nominal window count of an edge bucket is right (42.10).
        t_end = max(tmax, float(edges[-1]) - 1.0) if tf is not None else tmax
        try:
            edges1 = bucket_edges(tmin, t_end, settings.stage1_s, settings.origin_s, time_is_datetime)
        except HistogramError as exc:
            if exc.code == "too_many_buckets":
                raise HistogramError("too_many_windows", **exc.info) from exc
            raise
        agg1, length1 = settings.stage1_agg, settings.stage1_s
    else:
        edges1, agg1, length1 = edges, settings.agg, settings.bucket_s
    target = None
    if tf is not None:  # the target covers whole buckets / windows: their completeness denominators
        target = build_target(tf, min(float(edges[0]), float(edges1[0])), max(float(edges[-1]), float(edges1[-1])),
                              time_is_datetime)
    # buckets overlapping [first time, last time] (the edges are built per day, so not len(edges) - 1)
    overlap = (edges[1:] > tmin) & (edges[:-1] <= tmax)
    tgt2 = None
    if target is not None:  # a bucket without target time is absent: not counted, not exported (42.10)
        result.target_seconds = float(target.f(tmax) - target.f(tmin))
        tgt2 = np.diff(target.f(edges))
        overlap &= tgt2 > 0
    result.bucket_count = int(np.count_nonzero(overlap))
    result.bucket_start, result.bucket_end = edges[:-1][overlap], edges[1:][overlap]
    present1 = None
    windows_per_bucket = settings.bucket_s / settings.stage1_s
    if target is not None:  # completeness denominators are target times (42.10)
        tgt1 = tgt2 if not two else np.diff(target.f(edges1))
        present1, length1 = tgt1 > 0, tgt1
        if two:  # nominal windows of a bucket = those with target time
            g_all = np.clip(np.searchsorted(edges, edges1[:-1], side="right") - 1, 0, len(edges) - 2)
            windows_per_bucket = np.bincount(g_all[present1], minlength=len(edges) - 1)
    acc = _accumulate_windows(reader, names, edges1, gap, result, target)

    per_values: dict[str, np.ndarray] = {}
    infos: dict[str, VariableHistogram] = {}
    for nm in names:
        has, val, incomplete, at = _reduce_windows(acc[nm], agg1, settings.integral_unit_s, length1, present1)
        stage1_incomplete = int(incomplete.sum())
        if two:
            has, val, complete, at = _reduce_stage2(
                edges1, edges, has, val, incomplete, settings.agg, windows_per_bucket, at1=at)
            incomplete = has & ~complete
        else:
            complete = has & ~incomplete
        keep = has & ~incomplete if settings.exclude_incomplete else has
        var = VariableHistogram(nm, np.zeros(HIST_MIN_BINS), excluded=acc[nm]["ex"],
                                buckets_total=int(has.sum()), incomplete=int(incomplete.sum()),
                                excluded_buckets=int((has & incomplete).sum()) if settings.exclude_incomplete else 0,
                                stage1_incomplete=stage1_incomplete if two else 0)
        vals = np.asarray(val[keep], dtype=np.float64)
        vals = vals[np.isfinite(vals)]
        var.values = vals
        var.n = len(vals)
        # the per-bucket table of 42.8: every overlapping bucket, whatever "exclude incomplete" says
        tab = np.where(has & np.isfinite(val), val, np.nan)
        var.bucket_values = np.asarray(tab[overlap], dtype=np.float64)
        var.bucket_complete = np.asarray(complete[overlap], dtype=bool)
        if at is not None and settings.agg in ("max", "min"):
            var.bucket_at = np.where(np.isfinite(var.bucket_values), np.asarray(at)[overlap], np.nan)
        if len(vals):
            var.mean, var.std, var.median = float(vals.mean()), float(vals.std()), float(np.median(vals))
            var.vmax = float(vals.max())
            if settings.view == "duration":  # exact: the bucket values sorted, largest first (42.11)
                var.duration_v = np.sort(vals)[::-1].copy()
                var.duration_x = np.arange(1, len(vals) + 1, dtype=np.float64)
        per_values[nm], infos[nm] = vals, var

    nonempty = [v for v in per_values.values() if len(v)]
    if not nonempty:
        result.edges = np.linspace(0.0, 1.0, HIST_MIN_BINS + 1)
        result.variables = [infos[nm] for nm in names]
        return result
    lo, hi = min(float(v.min()) for v in nonempty), max(float(v.max()) for v in nonempty)
    single = _is_single_value(lo, hi)
    lo, hi = _span_range(lo, hi)
    if single:
        nb = 1
    elif settings.bins is None:
        half_span = hi * 0.5 - lo * 0.5
        per_var = []
        for v in nonempty:
            q1, q3 = np.percentile(v * 0.5, [25, 75])
            own = (float(v.max()) * 0.5 - float(v.min()) * 0.5) / half_span
            per_var.append((float(q3 - q1) / half_span, len(v), own))
        # also no more bins than the largest number of values (a handful of days is a handful of bins)
        nb = min(_fd_bins(per_var), max(len(v) for v in nonempty))
        nb = max(nb, HIST_MIN_BINS)
    else:
        nb = min(max(int(settings.bins), HIST_MIN_BINS), HIST_MAX_BINS)
    result.edges = _at(lo, hi, np.linspace(0.0, 1.0, nb + 1))
    for nm in names:
        counts, _ = np.histogram(per_values[nm], bins=result.edges)
        infos[nm].counts = counts.astype(np.float64)
        result.variables.append(infos[nm])
    return result


# ---------------------------------------------------------------------------
# Time of day (42.13)
# ---------------------------------------------------------------------------


def format_clock(seconds: float) -> str:
    """"HH:MM" of a time of day given in seconds (wraps at 24 h)."""
    s = int(round(seconds)) % DAY_S
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def _wall_seconds(starts: np.ndarray, is_datetime: bool) -> np.ndarray:
    """Seconds after local midnight (wall clock) of each window start; a numeric axis has axis 0 as the
    day start and no time zone (42.9)."""
    if not is_datetime:
        return np.mod(starts, DAY_S)
    out = np.empty(len(starts))
    for i, x in enumerate(starts.tolist()):
        dt = _to_datetime(x, True)
        out[i] = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
    return out


def _compute_timeofday(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    with np.errstate(over="ignore", invalid="ignore"):
        return _compute_timeofday_impl(reader, names, settings, time_name, time_is_datetime)


def _compute_timeofday_impl(reader: _Reader, names, settings, time_name, time_is_datetime=True) -> HistogramResult:
    """Fold the windows of length `slot_s` (the bucket computation of 42.3, windows tiled per local day
    from the origin) onto one day: slots numbered by the wall-clock time of the window start, the
    integral and the valid time of equal slots added (42.13). Windows are never "incomplete" here: the
    sums are weighted by the valid time."""
    if settings.tod_agg not in TOD_AGGREGATIONS:
        raise HistogramError("bad_aggregation")
    slot_s = float(settings.slot_s)
    reason = validate_bucket(slot_s) if slot_s <= DAY_S else "bucket_not_divisor"
    if reason:
        raise HistogramError(reason)
    n_slots = int(round(DAY_S / slot_s))
    result = HistogramResult(settings, reader.row_start, reader.row_end, np.linspace(0.0, 1.0, HIST_MIN_BINS + 1),
                             y_unit="slots", bins_auto=True, time_name=time_name)
    result.slot_start = np.arange(n_slots, dtype=np.float64) * slot_s
    tmin, tmax, gap = _scan_time(reader, settings)
    result.gap_s = gap

    def empty_var(nm: str, excluded: int = 0) -> VariableHistogram:
        v = VariableHistogram(nm, np.zeros(HIST_MIN_BINS), excluded=excluded)
        v.slot_values = np.full(n_slots, np.nan)
        v.slot_valid_s = np.zeros(n_slots)
        v.slot_days = np.zeros(n_slots, dtype=np.int64)
        v.slot_partial_days = np.zeros(n_slots, dtype=np.int64)
        return v

    if not math.isfinite(tmin):
        n_rows = max(0, reader.row_end - reader.row_start)
        result.variables = [empty_var(nm, n_rows) for nm in names]
        return result
    edges = bucket_edges(tmin, tmax, slot_s, settings.origin_s, time_is_datetime)
    tf = active_filter(settings)
    target = None
    if tf is not None:
        target = build_target(tf, float(edges[0]), float(edges[-1]), time_is_datetime)
        result.target_seconds = float(target.f(tmax) - target.f(tmin))
    acc = _accumulate_windows(reader, names, edges, gap, result, target)
    used = np.zeros(len(edges) - 1, dtype=bool)
    for nm in names:
        used |= acc[nm]["w"] > 0
    slot_of = np.zeros(len(edges) - 1, dtype=np.int64)
    if used.any():
        wall = _wall_seconds(edges[:-1][used], time_is_datetime)
        rel = np.mod(wall - settings.origin_s, DAY_S)
        slot_of[used] = np.minimum(np.floor(rel / slot_s + 1e-9).astype(np.int64), n_slots - 1)
    # What a window could hold at most: its length, or its target time under a time filter. A window
    # with less valid time than that is a partial day (shown in the hover; ユーザー判断 2026-10-15, 42.13).
    span = target.f(edges[1:]) - target.f(edges[:-1]) if target is not None else np.diff(edges)
    unit = settings.integral_unit_s
    for nm in names:
        ac = acc[nm]
        v = empty_var(nm, ac["ex"])
        has = ac["w"] > 0
        sel = np.flatnonzero(has)
        v.slot_valid_s = np.bincount(slot_of[sel], weights=ac["w"][sel], minlength=n_slots)
        sum_vw = np.bincount(slot_of[sel], weights=ac["vw"][sel], minlength=n_slots)
        v.slot_days = np.bincount(slot_of[sel], minlength=n_slots).astype(np.int64)
        partial = sel[ac["w"][sel] < span[sel] * (1 - 1e-9)]
        v.slot_partial_days = np.bincount(slot_of[partial], minlength=n_slots).astype(np.int64)
        got = v.slot_valid_s > 0
        with np.errstate(invalid="ignore", divide="ignore"):
            if settings.tod_agg == "mean":
                val = sum_vw / np.where(got, v.slot_valid_s, 1.0)
            else:
                val = sum_vw / unit
                if settings.tod_per_day:
                    val = val / np.where(got, v.slot_days, 1)
        v.slot_values = np.where(got, val, np.nan)
        v.n = int(v.slot_days.sum())
        v.buckets_total = int(got.sum())
        result.variables.append(v)
    return result
