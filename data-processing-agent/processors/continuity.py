"""
Continuity Processor

Detects structural issues in the time series: missing data gaps and
sustained zero-flow periods. No interpolation or imputation is performed.
"""

from typing import Any, Dict, List

import numpy as np

from processors.sampling_physics import gap_threshold_cap_seconds


def detect_gaps(
    timestamps: np.ndarray,
    expected_interval_seconds: float | None = None,
    tolerance_factor: float = 1.5,
) -> List[Dict[str, Any]]:
    """
    Detect time gaps where consecutive readings exceed a **data-driven** threshold.

    Nominal spacing is often irregular (e.g. LoRaWAN 12–60 s). A single median
    interval with a tight multiplier would falsely flag normal long-ish pauses.
    We therefore start from the larger of:

    - ``expected_interval_seconds * tolerance_factor`` (default: median spacing), and
    - ``~2×`` the 90th percentile of positive inter-arrival deltas, and
    - ``~1.5×`` the 95th percentile of positive inter-arrival deltas

    then **cap** that value so pauses longer than about one minute (configurable) are
    never treated as “normal” — typical meters do not exceed ~60 s between points when online.

    ``expected_points_missing`` uses the median positive delta as the nominal step.

    Args:
        timestamps:               Unix timestamps (seconds); sorted internally
        expected_interval_seconds: Optional override for the nominal step used in the
                                   first threshold term and for missing-count estimates.
                                   Defaults to the median positive inter-arrival delta.
        tolerance_factor:         Base multiplier before comparing with percentile-based floors.

    Returns:
        List of gap dicts: start, end, duration_seconds, expected_points_missing
    """
    if len(timestamps) < 2:
        return []

    ts = np.sort(timestamps.astype(float))
    deltas = np.diff(ts)
    positive = deltas[deltas > 1e-9]
    if len(positive) == 0:
        return []

    med = float(np.median(positive))
    nominal = float(expected_interval_seconds) if expected_interval_seconds is not None else med
    if nominal <= 0:
        nominal = max(med, 1e-9)

    cap = gap_threshold_cap_seconds()
    if len(positive) >= 2:
        p90 = float(np.percentile(positive, 90))
        p95 = float(np.percentile(positive, 95))
        threshold = min(
            max(
                nominal * tolerance_factor,
                p90 * 2.0,
                p95 * 1.5,
            ),
            cap,
        )
    else:
        threshold = min(nominal * tolerance_factor, cap)

    gaps = []
    for i, delta in enumerate(deltas):
        if delta <= 1e-9:
            continue
        if delta > threshold:
            gaps.append(
                {
                    "start_timestamp": int(ts[i]),
                    "end_timestamp": int(ts[i + 1]),
                    "duration_seconds": float(delta),
                    "expected_points_missing": max(0, int(delta / nominal) - 1),
                }
            )

    return gaps


def detect_zero_flow_periods(
    timestamps: np.ndarray,
    values: np.ndarray,
    min_duration_seconds: float = 60.0,
    zero_threshold: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Detect continuous periods where flow rate is at or below zero_threshold.

    Args:
        timestamps:            Sorted Unix timestamps (seconds)
        values:                Flow rate values aligned with timestamps
        min_duration_seconds:  Minimum span to report as a zero-flow period (default 60s)
        zero_threshold:        Values <= this are considered zero flow (default 0.0)

    Returns:
        List of period dicts: start_timestamp, end_timestamp, duration_seconds, point_count
    """
    if len(values) == 0:
        return []

    zero_mask = values <= zero_threshold
    periods = []
    in_period = False
    start_idx = 0

    for i, is_zero in enumerate(zero_mask):
        if is_zero and not in_period:
            in_period = True
            start_idx = i
        elif not is_zero and in_period:
            duration = float(timestamps[i - 1] - timestamps[start_idx])
            if duration >= min_duration_seconds:
                periods.append(
                    {
                        "start_timestamp": int(timestamps[start_idx]),
                        "end_timestamp": int(timestamps[i - 1]),
                        "duration_seconds": duration,
                        "point_count": i - start_idx,
                    }
                )
            in_period = False

    # Handle a period that extends to the end of the series
    if in_period:
        duration = float(timestamps[-1] - timestamps[start_idx])
        if duration >= min_duration_seconds:
            periods.append(
                {
                    "start_timestamp": int(timestamps[start_idx]),
                    "end_timestamp": int(timestamps[-1]),
                    "duration_seconds": duration,
                    "point_count": len(timestamps) - start_idx,
                }
            )

    return periods
