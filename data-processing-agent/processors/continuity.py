"""
Continuity Processor

Detects structural issues in the time series: missing data gaps and
sustained zero-flow periods. No interpolation or imputation is performed.
"""

from typing import Any, Dict, List

import numpy as np


def detect_gaps(
    timestamps: np.ndarray,
    expected_interval_seconds: float,
    tolerance_factor: float = 1.5,
) -> List[Dict[str, Any]]:
    """
    Detect time gaps where consecutive readings exceed the expected interval.

    A gap is flagged when:
        delta_t > expected_interval_seconds * tolerance_factor

    Args:
        timestamps:               Sorted Unix timestamps (seconds)
        expected_interval_seconds: The nominal sampling period
        tolerance_factor:         Multiplier on the expected interval before flagging (default 1.5)

    Returns:
        List of gap dicts: start, end, duration_seconds, expected_points_missing
    """
    if len(timestamps) < 2:
        return []

    threshold = expected_interval_seconds * tolerance_factor
    gaps = []

    deltas = np.diff(timestamps.astype(float))
    for i, delta in enumerate(deltas):
        if delta > threshold:
            gaps.append(
                {
                    "start_timestamp": int(timestamps[i]),
                    "end_timestamp": int(timestamps[i + 1]),
                    "duration_seconds": float(delta),
                    "expected_points_missing": max(0, int(delta / expected_interval_seconds) - 1),
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
