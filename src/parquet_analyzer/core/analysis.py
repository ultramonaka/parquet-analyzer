from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# detailed_specification.md 32: FFT needs evenly spaced samples. Allowed relative deviation of any
# step from the median step, and a step above GAP_FACTOR * median counts as a gap. Not user settings.
FFT_JITTER_TOLERANCE = 0.01
FFT_GAP_FACTOR = 1.5


def delta(y: np.ndarray) -> np.ndarray:
    """First-order difference (specification.md 5.7). Same length as y; first value is 0."""
    out = np.empty_like(y, dtype=np.result_type(y, 0.0))
    if len(y) == 0:
        return out
    out[0] = 0
    out[1:] = np.diff(y)
    return out


def rolling_mean(y: np.ndarray, window: float) -> np.ndarray:
    """Centered moving average over `window` samples (specification.md 5.5, expression
    function `rolling_mean(a, window)`). Same length as y; near the edges the window is
    only as wide as the data available there (not padded with NaN), via a
    convolve-and-normalize trick, so the whole series stays plottable.
    NaN propagates: any window containing a NaN yields NaN (expressions.ja.md 31); use
    `rolling_nanmean` to average the non-NaN samples instead.
    """
    w = int(window)
    if w < 1:
        raise ValueError("rolling_mean window must be a positive integer")
    n = len(y)
    if n == 0:
        return np.empty_like(y, dtype=np.result_type(y, 0.0))
    w = min(w, n)
    kernel = np.ones(w)
    sums = np.convolve(y, kernel, mode="same")
    counts = np.convolve(np.ones(n), kernel, mode="same")
    return sums / counts


def rolling_nanmean(y: np.ndarray, window: float) -> np.ndarray:
    """NaN-ignoring centered moving average (expressions.ja.md 31, expression function
    `rolling_nanmean(a, window)`). Same edge handling as `rolling_mean`; each output is
    (sum of non-NaN samples in the window) / (count of non-NaN samples), NaN when the
    window has no non-NaN sample.
    """
    w = int(window)
    if w < 1:
        raise ValueError("rolling_nanmean window must be a positive integer")
    n = len(y)
    if n == 0:
        return np.empty_like(y, dtype=np.result_type(y, 0.0))
    w = min(w, n)
    kernel = np.ones(w)
    yf = np.asarray(y, dtype=float)
    valid = ~np.isnan(yf)
    sums = np.convolve(np.where(valid, yf, 0.0), kernel, mode="same")
    # Counts are exact small integers; round to remove convolve float noise.
    counts = np.round(np.convolve(valid.astype(float), kernel, mode="same"))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = sums / counts
    out[counts == 0] = np.nan
    return out


def trailing_mean(y: np.ndarray, window: float) -> np.ndarray:
    """Past-only moving average (expressions.ja.md 31.2, expression function
    `trailing_mean(a, window)`). For each position i, averages y[max(0, i-window+1):i+1]
    (current point plus up to `window-1` past points). At the start, averages only the
    available points (no NaN output). Same length as y.
    NaN propagates: any window containing a NaN yields NaN, but NaN does not propagate
    beyond `window-1` points after it. Use `trailing_nanmean` to average the non-NaN
    samples instead.
    """
    w = int(window)
    if w < 1:
        raise ValueError("trailing_mean window must be a positive integer")
    n = len(y)
    if n == 0:
        return np.empty_like(y, dtype=np.result_type(y, 0.0))
    w = min(w, n)
    # Sum of the past w samples (convolve sums only the overlap, so a NaN/Inf reaches just the
    # w outputs whose window holds it); count is min(i+1, w).
    sums = np.convolve(np.asarray(y, dtype=float), np.ones(w))[:n]
    return sums / np.minimum(np.arange(1, n + 1), w)


def trailing_nanmean(y: np.ndarray, window: float) -> np.ndarray:
    """NaN-ignoring past-only moving average (expressions.ja.md 31.2, expression function
    `trailing_nanmean(a, window)`). For each position i, averages the non-NaN samples in
    y[max(0, i-window+1):i+1]. NaN only when the window has no non-NaN sample. Same length as y.
    """
    w = int(window)
    if w < 1:
        raise ValueError("trailing_nanmean window must be a positive integer")
    n = len(y)
    if n == 0:
        return np.empty_like(y, dtype=np.result_type(y, 0.0))
    w = min(w, n)
    yf = np.asarray(y, dtype=float)
    valid = ~np.isnan(yf)
    kernel = np.ones(w)
    sums = np.convolve(np.where(valid, yf, 0.0), kernel)[:n]
    # Counts are exact small integers; round to remove convolve float noise.
    counts = np.round(np.convolve(valid.astype(float), kernel)[:n])
    with np.errstate(invalid="ignore", divide="ignore"):
        out = sums / counts
    out[counts == 0] = np.nan
    return out


def basic_stats(y: np.ndarray) -> dict[str, float | int]:
    """Summary statistics for one variable over whatever slice it's given
    (specification.md 5.x). NaN/Inf-safe: non-finite samples are excluded from the
    numeric stats but counted separately (`nan_count`), since real sensor logs can
    have NaN gaps (see plot_widget.py's `_finite_bounds`, downsample.py's nan-safe
    LTTB) — silently mixing them into mean/std would make every stat NaN too.
    """
    n = len(y)
    finite = y[np.isfinite(y)]
    if len(finite) == 0:
        return {
            "count": n,
            "nan_count": n,
            "min": float("nan"),
            "max": float("nan"),
            "mean": float("nan"),
            "std": float("nan"),
            "median": float("nan"),
        }
    return {
        "count": n,
        "nan_count": n - len(finite),
        "min": float(finite.min()),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
        "std": float(finite.std()),
        # overwrite_input=True: np.median normally copies its input internally before
        # partitioning/sorting so it doesn't mutate the caller's array; `finite` is
        # already our own copy (from the mask above) and unused after this line, so
        # it's safe to let median sort it in place and skip that extra copy.
        "median": float(np.median(finite, overwrite_input=True)),
    }


def chunked_stats(window, row_start: int, row_end: int, chunk_rows: int = 4_000_000, bins: int = 1 << 16) -> dict[str, float | int]:
    """`basic_stats` of rows [row_start, row_end) of a column available only as
    `window(lo, hi)` (a derived variable computed per window, detailed_specification.md 36.6 C),
    aggregated chunk by chunk so the whole range is never resident.

    count/nan_count/min/max are exact; mean/std merge per-chunk moments (equal to numpy's up
    to float rounding); the median is exact too: a histogram pass finds the bin(s) holding
    the middle rank(s), and a second pass collects only those bins' values.
    """
    n = max(0, row_end - row_start)
    chunks = [(lo, min(row_end, lo + chunk_rows)) for lo in range(row_start, row_end, chunk_rows)]

    def finite_chunk(lo: int, hi: int) -> np.ndarray:
        f = np.asarray(window(lo, hi), dtype=np.float64)
        return f[np.isfinite(f)]

    nfin, mean, m2 = 0, 0.0, 0.0
    vmin, vmax = np.inf, -np.inf
    for lo, hi in chunks:
        f = finite_chunk(lo, hi)
        k = len(f)
        if k == 0:
            continue
        cm = float(f.mean())
        cm2 = float(((f - cm) ** 2).sum())
        d = cm - mean
        tot = nfin + k
        m2 += cm2 + d * d * nfin * k / tot
        mean += d * k / tot
        nfin = tot
        vmin = min(vmin, float(f.min()))
        vmax = max(vmax, float(f.max()))
    if nfin == 0:
        return {"count": n, "nan_count": n, "min": float("nan"), "max": float("nan"),
                "mean": float("nan"), "std": float("nan"), "median": float("nan")}
    if vmax == vmin:
        median = vmin
    else:
        # Values near +-1e308 make vmax - vmin overflow to inf: work at half scale then (exact).
        half = 1.0 if np.isfinite(vmax - vmin) else 0.5
        scale = bins / ((vmax * half) - (vmin * half))
        if not np.isfinite(scale):  # a subnormal-wide range: one (correct, if coarse) bin
            scale = 0.0

        def bin_of(v: np.ndarray) -> np.ndarray:
            pos = np.nan_to_num((v * half - vmin * half) * scale, nan=0.0, posinf=bins - 1, neginf=0.0)
            return np.clip(pos.astype(np.int64), 0, bins - 1)

        counts = np.zeros(bins, dtype=np.int64)
        for lo, hi in chunks:
            counts += np.bincount(bin_of(finite_chunk(lo, hi)), minlength=bins)
        ranks = sorted({(nfin - 1) // 2, nfin // 2})
        cum = np.cumsum(counts)
        want = {r: int(np.searchsorted(cum, r, side="right")) for r in ranks}
        wanted_bins = sorted(set(want.values()))
        below = {b: int(cum[b] - counts[b]) for b in wanted_bins}
        picked: dict[int, list[np.ndarray]] = {b: [] for b in wanted_bins}
        for lo, hi in chunks:
            f = finite_chunk(lo, hi)
            idx = bin_of(f)
            for b in wanted_bins:
                sel = f[idx == b]
                if len(sel):
                    picked[b].append(sel)
        sorted_bin = {b: np.sort(np.concatenate(p)) for b, p in picked.items()}
        vals = [float(sorted_bin[want[r]][r - below[want[r]]]) for r in ranks]
        median = float(sum(vals) / len(vals))
    return {
        "count": n,
        "nan_count": n - nfin,
        "min": vmin,
        "max": vmax,
        "mean": float(mean),
        "std": float(np.sqrt(m2 / nfin)),
        "median": median,
    }


def fft(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Amplitude spectrum of y (specification.md 5.6). Assumes x is uniformly spaced.

    Returns (frequencies, amplitudes) for the positive-frequency half.
    """
    n = len(y)
    if n == 0:
        return np.array([]), np.array([])
    dt = float(np.median(np.diff(x))) if n > 1 else 1.0
    if not (np.isfinite(dt) and dt > 0):
        # analysis 32.1: duplicate / reversed / NaN time values. The UI refuses such a range via
        # sampling_report first; as a component this must not surface a ZeroDivisionError.
        raise ValueError(f"sampling interval is not positive (median dt = {dt})")
    freqs = np.fft.rfftfreq(n, d=dt)
    amps = np.abs(np.fft.rfft(y)) / n
    return freqs, amps


@dataclass(frozen=True)
class SamplingReport:
    """Result of sampling_report (detailed_specification.md 32). `reason` is "" when the
    range is usable, else "too_few" (< 3 samples), "not_finite" (NaN/Inf in x),
    "not_ascending" (x decreases or repeats) or "non_uniform"."""

    n: int
    dt_median: float
    dt_min: float
    dt_max: float
    max_rel_deviation: float
    gap_count: int
    uniform: bool
    reason: str = ""


def sampling_report(x: np.ndarray) -> SamplingReport:
    """Check that x (the time axis of the FFT range) is evenly sampled (32). Never raises:
    a NaN/Inf, non-ascending or too-short x just yields uniform=False with a `reason`."""
    nan = float("nan")
    x = np.asarray(x, dtype=np.float64).ravel() if len(x) else np.empty(0)
    n = int(x.size)
    if n < 3:
        return SamplingReport(n, nan, nan, nan, nan, 0, False, "too_few")
    if not np.all(np.isfinite(x)):
        return SamplingReport(n, nan, nan, nan, nan, 0, False, "not_finite")
    d = np.diff(x)
    dt_med = float(np.median(d))
    dt_min, dt_max = float(d.min()), float(d.max())
    if dt_min <= 0 or dt_med <= 0:
        return SamplingReport(n, dt_med, dt_min, dt_max, nan, 0, False, "not_ascending")
    dev = float(np.max(np.abs(d - dt_med)) / dt_med)
    gaps = int(np.count_nonzero(d > FFT_GAP_FACTOR * dt_med))
    uniform = dev <= FFT_JITTER_TOLERANCE and gaps == 0
    return SamplingReport(n, dt_med, dt_min, dt_max, dev, gaps, uniform, "" if uniform else "non_uniform")
