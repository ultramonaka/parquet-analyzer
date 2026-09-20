from __future__ import annotations

import numpy as np


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


def fft(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Amplitude spectrum of y (specification.md 5.6). Assumes x is uniformly spaced.

    Returns (frequencies, amplitudes) for the positive-frequency half.
    """
    n = len(y)
    if n == 0:
        return np.array([]), np.array([])
    dt = float(np.median(np.diff(x))) if n > 1 else 1.0
    freqs = np.fft.rfftfreq(n, d=dt)
    amps = np.abs(np.fft.rfft(y)) / n
    return freqs, amps
