"""
Frequency-domain probes for regularly resampled flow series.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import welch


def compute_dominant_frequencies(
    timestamps,
    values,
    *,
    top_k: int = 3,
) -> list[dict]:
    """Return dominant non-zero frequencies after fixed-cadence resampling."""
    ts = np.asarray(timestamps, dtype=float)
    vals = np.asarray(values, dtype=float)
    finite = np.isfinite(ts) & np.isfinite(vals)
    ts = ts[finite]
    vals = vals[finite]
    if len(ts) < 4:
        return []

    order = np.argsort(ts)
    ts = ts[order]
    vals = vals[order]
    unique_ts, unique_idx = np.unique(ts, return_index=True)
    ts = unique_ts
    vals = vals[unique_idx]
    if len(ts) < 4:
        return []

    deltas = np.diff(ts)
    deltas = deltas[deltas > 1e-9]
    if len(deltas) == 0:
        return []
    step = float(np.median(deltas))
    if step <= 0:
        return []

    grid = np.arange(float(ts[0]), float(ts[-1]) + step * 0.5, step)
    if len(grid) < 4:
        return []
    resampled = np.interp(grid, ts, vals)
    resampled = resampled - float(np.nanmean(resampled))
    if not np.any(np.abs(resampled) > 1e-12):
        return []

    fs = 1.0 / step
    nperseg = min(2048, len(resampled))
    freqs, power = welch(resampled, fs=fs, nperseg=nperseg, scaling="spectrum")
    mask = (freqs > 0) & np.isfinite(power) & (power > 0)
    freqs = freqs[mask]
    power = power[mask]
    if len(freqs) == 0:
        return []

    k = max(1, int(top_k))
    top_idx = np.argsort(power)[::-1][:k]
    out: list[dict] = []
    for idx in top_idx:
        frequency = float(freqs[idx])
        if frequency <= 0:
            continue
        out.append(
            {
                "frequency_hz": frequency,
                "amplitude": float(np.sqrt(power[idx])),
                "period_seconds": float(1.0 / frequency),
            }
        )
    return out
