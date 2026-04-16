"""
Quality Processor

Analyses the ultrasonic signal quality scores reported alongside each flow rate reading.

Quality reflects how cleanly the ultrasonic sensor received its signal through the pipe wall.
A low score (≤ 60) means the measurement is unreliable. The two main physical causes are:

  1. No water detected in the pipe — common when air bubbles are travelling through the pipe,
     or the pipe section has been drained entirely.

  2. Poor acoustic coupling — the ultrasonic coupling pads between the meter transducer and
     the pipe wall are not properly seated, preventing a clean signal transmission.

Sustained low quality over a period points to drainage or a coupling installation issue.
Intermittent low-quality spikes suggest passing air bubbles.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np

# Merged low-Q intervals returned to the LLM (longest first if capped). Keeps tool JSON small.
_DEFAULT_MAX_INTERVALS = 120


def _max_intervals_returned() -> int:
    raw = os.environ.get("BLUEBOT_LOW_QUALITY_MAX_INTERVALS", str(_DEFAULT_MAX_INTERVALS))
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_MAX_INTERVALS
    return max(10, min(n, 5000))


def _merge_low_quality_intervals(
    t_valid: np.ndarray,
    f_valid: np.ndarray,
    q_valid: np.ndarray,
    low_mask: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    Contiguous runs where low_mask is True, in time order.
    Each interval: start/end timestamp, duration, point counts, quality stats for that run.
    """
    n = int(low_mask.size)
    if n == 0:
        return []

    out: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        if not bool(low_mask[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and bool(low_mask[j + 1]):
            j += 1

        ts_seg = t_valid[i : j + 1].astype(float)
        qs_seg = q_valid[i : j + 1].astype(float)
        fs_seg = f_valid[i : j + 1]

        out.append(
            {
                "start_timestamp": int(ts_seg[0]),
                "end_timestamp": int(ts_seg[-1]),
                "duration_seconds": float(ts_seg[-1] - ts_seg[0]),
                "point_count": int(j - i + 1),
                "min_quality_score": float(np.min(qs_seg)),
                "max_quality_score": float(np.max(qs_seg)),
                "mean_quality_score": float(np.mean(qs_seg)),
                "min_flow_rate": float(np.nanmin(fs_seg)) if fs_seg.size else None,
                "max_flow_rate": float(np.nanmax(fs_seg)) if fs_seg.size else None,
            }
        )
        i = j + 1

    return out


def detect_low_quality_readings(
    timestamps: np.ndarray,
    flow_rates: np.ndarray,
    quality: np.ndarray,
    threshold: float = 60.0,
) -> Dict[str, Any]:
    """
    Identify readings where quality score is at or below the threshold.

    Returns aggregate statistics and merged time intervals of low quality (not per-point rows)
    to keep LLM tool payloads bounded.

    Args:
        timestamps:   Sorted Unix timestamps (seconds)
        flow_rates:   Flow rate values aligned with timestamps
        quality:      Quality scores aligned with timestamps
        threshold:    Quality score at or below which a reading is flagged (default 60)

    Returns:
        threshold, flagged_count, total_count, flagged_percent, quality_stats
        low_quality_intervals: merged contiguous low-quality stretches (possibly capped)
        interval_count: total number of merged intervals before any cap
        interval_list_capped: True if the interval list was truncated for length
    """
    valid_mask = ~np.isnan(quality)
    q_valid = quality[valid_mask]
    t_valid = timestamps[valid_mask]
    f_valid = flow_rates[valid_mask]

    low_mask = q_valid <= threshold

    total = int(len(q_valid))
    flagged = int(np.sum(low_mask))

    intervals_all = _merge_low_quality_intervals(t_valid, f_valid, q_valid, low_mask)
    interval_count = len(intervals_all)

    max_ret = _max_intervals_returned()
    longest_interval: Dict[str, Any] | None = None
    if intervals_all:
        longest = max(intervals_all, key=lambda x: x["duration_seconds"])
        longest_interval = {
            "start_timestamp": longest["start_timestamp"],
            "end_timestamp": longest["end_timestamp"],
            "duration_seconds": longest["duration_seconds"],
            "point_count": longest["point_count"],
        }

    interval_list_capped = interval_count > max_ret
    if interval_list_capped:
        # Prefer sustained episodes: keep the longest stretches by duration.
        intervals_out = sorted(
            intervals_all,
            key=lambda x: (x["duration_seconds"], x["point_count"]),
            reverse=True,
        )[:max_ret]
    else:
        intervals_out = intervals_all

    first_ts: int | None = None
    last_ts: int | None = None
    if intervals_all:
        first_ts = min(iv["start_timestamp"] for iv in intervals_all)
        last_ts = max(iv["end_timestamp"] for iv in intervals_all)

    return {
        "threshold": threshold,
        "flagged_count": flagged,
        "total_count": total,
        "flagged_percent": round(flagged / total * 100, 2) if total > 0 else 0.0,
        "quality_stats": {
            "min": float(np.min(q_valid)) if total > 0 else None,
            "max": float(np.max(q_valid)) if total > 0 else None,
            "mean": float(np.mean(q_valid)) if total > 0 else None,
        },
        "first_low_quality_timestamp": first_ts,
        "last_low_quality_timestamp": last_ts,
        "longest_low_quality_stretch": longest_interval,
        "interval_count": interval_count,
        "low_quality_intervals": intervals_out,
        "interval_list_capped": interval_list_capped,
        "intervals_returned": len(intervals_out),
    }
