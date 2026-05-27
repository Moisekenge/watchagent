"""Robust statistics for per-city baselines.

We use the **modified z-score** (Iglewicz & Hoaglin) built on the median and
the Median Absolute Deviation (MAD) rather than mean and standard deviation.

Why robust statistics? The quantity we are trying to detect *is* an outlier.
A single heat spike inflates the standard deviation and can mask itself; the
median and MAD are resistant to exactly that contamination. This also gives the
per-city calibration the challenge hints at for free: maritime Vancouver has a
small temperature MAD, so the same absolute swing yields a larger z-score there
than in continental Ottawa — sensitivity is *learned from each city's own
data* rather than hard-coded.
"""

from __future__ import annotations

import statistics

# 0.6745 is the 0.75 quantile of the standard normal; it scales MAD so the
# modified z-score is comparable to a standard z-score for normal data.
_MAD_SCALE = 0.6745
# 1.253314 = sqrt(pi/2); used in the mean-absolute-deviation fallback when MAD
# is zero (i.e. at least half the window is identical).
_MEANAD_SCALE = 1.253314


def median(values: list[float]) -> float:
    return statistics.median(values)


def mad(values: list[float], center: float | None = None) -> float:
    """Median absolute deviation."""
    if not values:
        return 0.0
    c = center if center is not None else median(values)
    return median([abs(v - c) for v in values])


def robust_stats(values: list[float]) -> tuple[float, float]:
    """Return (median, MAD) for a window of values."""
    med = median(values)
    return med, mad(values, med)


def modified_zscore(x: float, values: list[float]) -> float:
    """Robust z-score of ``x`` against the distribution ``values``.

    Falls back to a mean-absolute-deviation estimate when the MAD is zero, and
    returns 0.0 only when the window is constant (no deviation possible).
    """
    if len(values) < 2:
        return 0.0
    med = median(values)
    m = mad(values, med)
    if m > 0:
        return _MAD_SCALE * (x - med) / m
    mean = sum(values) / len(values)
    mean_ad = sum(abs(v - mean) for v in values) / len(values)
    if mean_ad > 0:
        return (x - mean) / (_MEANAD_SCALE * mean_ad)
    return 0.0


def consecutive_deltas(values: list[float]) -> list[float]:
    """Hour-over-hour changes: [v1-v0, v2-v1, ...]."""
    return [b - a for a, b in zip(values, values[1:], strict=False)]
