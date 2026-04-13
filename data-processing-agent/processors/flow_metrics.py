"""
Flow Metrics Processor

Domain-specific calculations for water/fluid flow time series:
total volume (trapezoidal integration), peak detection, and
the flow duration curve (standard hydrological analysis).
"""

from typing import Any, Dict, List

import numpy as np
from scipy import signal


def compute_total_volume(
    timestamps: np.ndarray,
    flow_rates: np.ndarray,
) -> Dict[str, Any]:
    """
    Estimate total flow volume via trapezoidal numerical integration.

    Assumes flow_rate is in gal/min and timestamps are Unix seconds.
    Converts time axis to minutes before integrating, so the result is in gallons.

    Method: numpy.trapz — exact for piecewise-linear interpolation between samples.

    Returns:
        total_volume_gallons:  Integrated volume over the window
        time_span_minutes:    Duration of the window
        method:               Integration method label
    """
    clean_mask = ~np.isnan(flow_rates)
    t = (timestamps[clean_mask].astype(float) - timestamps[clean_mask][0]) / 60.0
    v = flow_rates[clean_mask]

    if len(t) < 2:
        return {"total_volume_gallons": 0.0, "time_span_minutes": 0.0, "method": "trapezoidal"}

    volume = float(np.trapezoid(v, t))
    return {
        "total_volume_gallons": volume,
        "time_span_minutes": float(t[-1]),
        "method": "trapezoidal_integration",
    }


def detect_peaks(
    timestamps: np.ndarray,
    values: np.ndarray,
    prominence_multiplier: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    Detect significant flow rate peaks using scipy.signal.find_peaks.

    Prominence threshold = std(values) * prominence_multiplier.
    This makes the threshold scale-invariant relative to the series spread.

    Args:
        prominence_multiplier:  Controls sensitivity. Higher = fewer, larger peaks only.

    Returns:
        List of peak dicts: timestamp, value, z_score, prominence
    """
    clean = values[~np.isnan(values)]
    if len(clean) == 0:
        return []

    mean = float(np.mean(clean))
    std = float(np.std(clean, ddof=1)) if len(clean) > 1 else 0.0
    prominence_threshold = std * prominence_multiplier

    peak_indices, properties = signal.find_peaks(values, prominence=prominence_threshold)

    return [
        {
            "timestamp": int(timestamps[idx]),
            "value": float(values[idx]),
            "z_score": float((values[idx] - mean) / std) if std > 0 else 0.0,
            "prominence": float(properties["prominences"][j]),
        }
        for j, idx in enumerate(peak_indices)
    ]


def compute_flow_duration_curve(values: np.ndarray) -> Dict[str, Any]:
    """
    Compute the flow duration curve (FDC) — a standard hydrological tool.

    The FDC maps exceedance probability to flow rate:
        Q_x = flow rate exceeded x% of the time

    Returns key quantiles Q10 through Q99, plus the full exceedance array
    description for context.
    """
    clean = values[~np.isnan(values)]
    if len(clean) == 0:
        return {"flow_duration_curve": {}, "description": "No valid data"}

    sorted_desc = np.sort(clean)[::-1]
    n = len(sorted_desc)

    quantiles = [10, 25, 50, 75, 90, 95, 99]
    fdc = {}
    for q in quantiles:
        idx = min(int(q / 100.0 * n), n - 1)
        fdc[f"Q{q}"] = float(sorted_desc[idx])

    return {
        "flow_duration_curve": fdc,
        "description": "Qx = flow rate (gal/min) exceeded x% of the time",
        "sample_count": int(n),
    }
