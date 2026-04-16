"""
Quiet-flow baseline — order statistics on the lowest-flow readings among quality-gated points.

Used to screen for sustained offset / "creep" when the line is as still as this window allows,
without claiming leak or calibration fault (interpretation is for the report narrative).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np


def summarize_quiet_flow_baseline(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    *,
    quality_threshold: float = 60.0,
    quiet_percentile: float = 10.0,
    min_good_points: int = 10,
) -> Dict[str, Any]:
    """
    Among points with quality > ``quality_threshold``, compute the
    ``quiet_percentile`` (default 10) flow threshold, then summarise flow
    only for points at or below that threshold (the "quietest" slice).

    Returns JSON-serialisable stats for the quiet subset, plus coverage fractions.
    """
    del timestamps  # reserved for future time-of-day variants

    if quiet_percentile <= 0 or quiet_percentile >= 100:
        return {
            "error": "quiet_percentile must be strictly between 0 and 100.",
        }

    v = np.asarray(values, dtype=float)
    q = np.asarray(quality, dtype=float)

    valid = ~np.isnan(v) & ~np.isnan(q)
    good = valid & (q > quality_threshold)

    n_good = int(good.sum())
    n_total = int(len(v))

    if n_good < min_good_points:
        return {
            "error": (
                f"Need at least {min_good_points} readings with quality > {quality_threshold} "
                f"and valid flow; found {n_good}."
            ),
            "n_good_quality": n_good,
            "n_total": n_total,
        }

    v_good = v[good]
    flow_cutoff = float(np.percentile(v_good, quiet_percentile))

    quiet = good & (v <= flow_cutoff)
    n_quiet = int(quiet.sum())

    if n_quiet < 1:
        return {
            "error": "Quiet subset is empty after applying flow cutoff.",
            "flow_cutoff": flow_cutoff,
            "n_good_quality": n_good,
        }

    qv = v[quiet]
    p25 = float(np.percentile(qv, 25))
    p75 = float(np.percentile(qv, 75))

    return {
        "quality_threshold_used": float(quality_threshold),
        "quiet_percentile_used": float(quiet_percentile),
        "flow_cutoff": flow_cutoff,
        "definition": (
            f"Among points with quality > {quality_threshold}, flow_cutoff is the "
            f"{quiet_percentile:g}th percentile of flow_rate; 'quiet' points are those "
            f"with flow_rate <= flow_cutoff."
        ),
        "n_total_points": n_total,
        "n_good_quality_points": n_good,
        "n_quiet_points": n_quiet,
        "fraction_of_dataset_quiet": n_quiet / n_total if n_total else 0.0,
        "fraction_of_good_in_quiet": n_quiet / n_good if n_good else 0.0,
        "quiet_flow_mean": float(np.mean(qv)),
        "quiet_flow_median": float(np.median(qv)),
        "quiet_flow_std": float(np.std(qv, ddof=1)) if n_quiet > 1 else 0.0,
        "quiet_flow_min": float(np.min(qv)),
        "quiet_flow_max": float(np.max(qv)),
        "quiet_flow_p25": p25,
        "quiet_flow_p75": p75,
        "quiet_flow_iqr": p75 - p25,
        "note": (
            "Screening statistic only: elevated quiet_flow_median vs expected zero may suggest "
            "offset or process flow when the line is still; it is not proof of a leak or miscalibration."
        ),
    }
