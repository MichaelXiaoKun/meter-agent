"""
Quality Processor

Analyses the ultrasonic signal quality scores reported alongside each flow rate reading.

Quality reflects how cleanly the ultrasonic sensor received its signal through the pipe wall.
A low score (≤ 60) means the measurement is unreliable. The two main physical causes are:

  1. No water detected in the pipe — common when air bubbles are travelling through the pipe,
     or when the pipe section has been drained entirely.

  2. Poor acoustic coupling — the ultrasonic coupling pads between the meter transducer and
     the pipe wall are not properly seated, preventing a clean signal transmission.

Sustained low quality over a period points to drainage or a coupling installation issue.
Intermittent low-quality spikes suggest passing air bubbles.
"""

from typing import Any, Dict, List

import numpy as np


def detect_low_quality_readings(
    timestamps: np.ndarray,
    flow_rates: np.ndarray,
    quality: np.ndarray,
    threshold: float = 60.0,
) -> Dict[str, Any]:
    """
    Identify readings where quality score is at or below the threshold.

    Args:
        timestamps:   Sorted Unix timestamps (seconds)
        flow_rates:   Flow rate values aligned with timestamps
        quality:      Quality scores aligned with timestamps
        threshold:    Quality score at or below which a reading is flagged (default 60)

    Returns:
        threshold:          The threshold used
        flagged_count:      Number of low-quality readings
        total_count:        Total readings with a quality score
        flagged_percent:    Percentage of readings that are low quality
        readings:           List of flagged readings, each with:
                                timestamp, flow_rate, quality_score
        quality_stats:      min, max, mean quality across all readings
    """
    valid_mask = ~np.isnan(quality)
    q_valid = quality[valid_mask]
    t_valid = timestamps[valid_mask]
    f_valid = flow_rates[valid_mask]

    low_mask = q_valid <= threshold
    flagged_t = t_valid[low_mask]
    flagged_f = f_valid[low_mask]
    flagged_q = q_valid[low_mask]

    readings = [
        {
            "timestamp": int(flagged_t[i]),
            "flow_rate": float(flagged_f[i]) if not np.isnan(flagged_f[i]) else None,
            "quality_score": float(flagged_q[i]),
        }
        for i in range(len(flagged_t))
    ]

    total = int(len(q_valid))
    flagged = int(len(readings))

    return {
        "threshold": threshold,
        "flagged_count": flagged,
        "total_count": total,
        "flagged_percent": round(flagged / total * 100, 2) if total > 0 else 0.0,
        "readings": readings,
        "quality_stats": {
            "min": float(np.min(q_valid)) if total > 0 else None,
            "max": float(np.max(q_valid)) if total > 0 else None,
            "mean": float(np.mean(q_valid)) if total > 0 else None,
        },
    }
