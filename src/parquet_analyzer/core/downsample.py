from __future__ import annotations

import warnings

import numpy as np


def lttb(x: np.ndarray, y: np.ndarray, n_out: int) -> tuple[np.ndarray, np.ndarray]:
    """Largest-Triangle-Three-Buckets downsampling (specification.md 5.4).

    Reduces (x, y) to at most n_out points while keeping the visual shape
    (peaks/troughs) intact, unlike naive stride sampling.
    """
    n = len(x)
    if n_out >= n or n_out <= 2:
        return x, y

    sampled_x = np.empty(n_out, dtype=x.dtype)
    sampled_y = np.empty(n_out, dtype=y.dtype)
    sampled_x[0], sampled_y[0] = x[0], y[0]
    sampled_x[-1], sampled_y[-1] = x[-1], y[-1]

    # n-2 interior points are bucketed into n_out-2 buckets.
    bucket_size = (n - 2) / (n_out - 2)
    a = 0  # index of the previously selected point

    # NaN/Inf are rare in real data, and np.nanmean is ~5x slower than np.mean — always
    # paying that cost regardless would needlessly slow down the common (finite-only)
    # case. Check once up front (cheap: ~2ms for 13M points) rather than guarding
    # every bucket, and only switch to the nan-safe mean when actually needed.
    has_nonfinite = not (np.isfinite(x).all() and np.isfinite(y).all())
    mean = np.nanmean if has_nonfinite else np.mean

    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # nanmean on an all-NaN slice

        for i in range(n_out - 2):
            bucket_start = int(np.floor(i * bucket_size)) + 1
            bucket_end = min(int(np.floor((i + 1) * bucket_size)) + 1, n - 1)
            bucket_end = max(bucket_end, bucket_start + 1)

            next_start = bucket_end
            next_end = min(int(np.floor((i + 2) * bucket_size)) + 1, n)
            next_end = max(next_end, next_start + 1)
            # nan-safe (when needed) so a single NaN in the look-ahead window doesn't
            # poison the whole triangle's third vertex — with a plain mean, that
            # turned every candidate area in the *current* bucket into NaN, discarding
            # the real peak/trough in favor of an effectively arbitrary
            # argmax(NaN-only) result.
            avg_x = mean(x[next_start:next_end])
            avg_y = mean(y[next_start:next_end])
            if has_nonfinite and (not np.isfinite(avg_x) or not np.isfinite(avg_y)):
                # look-ahead window is entirely NaN/Inf: fall back to the anchor point
                # so the triangle degenerates instead of forcing every area to NaN.
                avg_x = x[a] if np.isfinite(avg_x) else avg_x
                avg_y = y[a] if np.isfinite(avg_y) else avg_y

            point_ax, point_ay = x[a], y[a]
            bucket_x = x[bucket_start:bucket_end]
            bucket_y = y[bucket_start:bucket_end]

            areas = np.abs(
                (point_ax - avg_x) * (bucket_y - point_ay)
                - (point_ax - bucket_x) * (avg_y - point_ay)
            )
            if has_nonfinite:
                # Points whose own area is NaN (NaN in bucket_x/bucket_y) must never
                # win the argmax over a real neighbor; -inf only loses to an all-NaN
                # bucket, where argmax then deterministically falls back to the
                # bucket's first point.
                areas = np.nan_to_num(areas, nan=-np.inf)
            max_idx = bucket_start + int(np.argmax(areas))

            sampled_x[i + 1] = x[max_idx]
            sampled_y[i + 1] = y[max_idx]
            a = max_idx

    return sampled_x, sampled_y
