"""
Structured, terse captions for each plot type.

The LLM path in this repo does NOT consume raster images — the ``llm/`` layer
only ships text messages. When the model sees a Markdown image reference in
the report it can cite the path but it cannot read the pixels. To close that
gap without shipping vision features (expensive in tokens and model support),
every plot handler also returns a ``caption`` dict: a handful of scalar fields
that describe what the chart would show.

These captions are:

- Deterministic (pure function of the same arrays used to draw the chart).
- Tiny (≤ ~8 scalar fields per caption) so adding one per plot stays within
  noise in the total token budget.
- JSON-serialisable; they ride along in the tool-result dict and in the
  orchestrator's ``plot_summaries`` output.

Design: the caption describes *what the eye would notice* — slope direction,
peak density, variability class, low-quality share. It deliberately avoids
duplicating headline metrics that already live in ``verified_facts`` and the
reasoning schema.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def _finite(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return a
    return a[~np.isnan(a)]


def _slope_sign(timestamps: np.ndarray, values: np.ndarray) -> str:
    """Very rough trend direction from first/last-half means; no regression needed."""
    clean_mask = ~np.isnan(values)
    if clean_mask.sum() < 4:
        return "insufficient_data"
    v = values[clean_mask]
    mid = v.size // 2
    if mid <= 0:
        return "flat"
    first_half = float(np.mean(v[:mid]))
    last_half = float(np.mean(v[mid:]))
    # Normalise by data scale so a 0.001 shift on a mean-of-1 series is "flat".
    scale = max(abs(first_half), abs(last_half), 1e-6)
    delta = (last_half - first_half) / scale
    if delta > 0.05:
        return "positive"
    if delta < -0.05:
        return "negative"
    return "flat"


def _variability_class(values: np.ndarray) -> str:
    clean = _finite(values)
    if clean.size < 2:
        return "insufficient_data"
    mean_abs = max(abs(float(np.mean(clean))), 1e-9)
    std = float(np.std(clean, ddof=1))
    cv = std / mean_abs
    if cv < 0.05:
        return "near_constant"
    if cv < 0.25:
        return "low"
    if cv < 1.0:
        return "moderate"
    return "high"


def _peak_density_class(values: np.ndarray) -> str:
    """Coarse bucket for how many peaks find_peaks would flag vs series length."""
    clean = _finite(values)
    n = clean.size
    if n < 8:
        return "insufficient_data"
    std = float(np.std(clean, ddof=1))
    if std <= 0:
        return "none"
    # Inline import to avoid pulling scipy at module load.
    from scipy.signal import find_peaks

    peaks, _ = find_peaks(clean, prominence=std)
    ratio = len(peaks) / n
    if ratio == 0.0:
        return "none"
    if ratio < 0.01:
        return "low"
    if ratio < 0.05:
        return "moderate"
    return "high"


def _low_quality_fraction(quality: Optional[np.ndarray], threshold: float = 60.0) -> Optional[float]:
    if quality is None:
        return None
    q = np.asarray(quality, dtype=float)
    valid = q[~np.isnan(q)]
    if valid.size == 0:
        return None
    return round(float((valid <= threshold).sum()) / valid.size, 4)


def _gap_marker_count(timestamps: np.ndarray, cap_seconds: float) -> int:
    ts = np.asarray(timestamps, dtype=float)
    if ts.size < 2 or cap_seconds <= 0:
        return 0
    deltas = np.diff(np.sort(ts))
    return int(np.sum(deltas > cap_seconds))


def caption_time_series(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: Optional[np.ndarray],
    *,
    healthy_cap_seconds: float = 60.0,
) -> Dict[str, Any]:
    return {
        "plot_type": "time_series",
        "slope_sign": _slope_sign(timestamps, values),
        "variability": _variability_class(values),
        "low_quality_fraction": _low_quality_fraction(quality),
        "gap_markers": _gap_marker_count(timestamps, healthy_cap_seconds),
        "n_points": int(np.asarray(timestamps).size),
    }


def caption_flow_duration_curve(values: np.ndarray) -> Dict[str, Any]:
    clean = _finite(values)
    if clean.size == 0:
        return {"plot_type": "flow_duration_curve", "shape": "empty"}
    sorted_desc = np.sort(clean)[::-1]
    n = sorted_desc.size

    def _q(pct: int) -> float:
        idx = min(int(pct / 100.0 * n), n - 1)
        return float(sorted_desc[idx])

    q10 = _q(10)
    q50 = _q(50)
    q90 = _q(90)
    # Classify the curve shape: "flashy" (big Q10/Q50 gap) vs "flat" (Q10≈Q90).
    if q50 <= 1e-9:
        shape = "intermittent_zero_dominated"
    else:
        spread = q10 / max(q50, 1e-9)
        if spread >= 5.0:
            shape = "flashy"
        elif spread >= 2.0:
            shape = "moderate"
        else:
            shape = "flat"
    return {
        "plot_type": "flow_duration_curve",
        "shape": shape,
        "q10": round(q10, 6),
        "q50": round(q50, 6),
        "q90": round(q90, 6),
        "n_points": int(n),
    }


def caption_peaks_annotated(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    peak_count: int,
) -> Dict[str, Any]:
    return {
        "plot_type": "peaks_annotated",
        "peak_count": int(peak_count),
        "peak_density": _peak_density_class(values),
        "variability": _variability_class(values),
        "n_points": int(np.asarray(timestamps).size),
    }


def caption_signal_quality(
    quality: np.ndarray,
    *,
    threshold: float = 60.0,
) -> Dict[str, Any]:
    q = np.asarray(quality, dtype=float)
    valid = q[~np.isnan(q)]
    if valid.size == 0:
        return {"plot_type": "signal_quality", "state": "no_valid_quality"}
    below = int((valid <= threshold).sum())
    mean_q = float(np.mean(valid))
    if below == 0:
        state = "clean"
    elif below / valid.size < 0.05:
        state = "occasional_dips"
    elif below / valid.size < 0.20:
        state = "frequent_dips"
    else:
        state = "widespread_low_quality"
    return {
        "plot_type": "signal_quality",
        "state": state,
        "low_quality_fraction": round(below / valid.size, 4),
        "mean_quality": round(mean_q, 2),
        "threshold": threshold,
        "n_points": int(valid.size),
    }
