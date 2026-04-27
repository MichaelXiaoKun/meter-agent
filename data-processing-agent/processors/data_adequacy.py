"""
Data Adequacy

Deterministic preflight: given an algorithm's data requirements and a fetched
DataFrame, decide whether the series is dense enough and clean enough for the
algorithm to run with confidence. Replaces "let the LLM decide if data is
sufficient" with a structured, replayable check.

Layer model (see plan):
  L1: each processor declares ``DATA_REQUIREMENTS = {min_points, ideal_points,
      max_gap_pct}`` at its file head.
  L2: this module + ``adaptive_fetch.py`` — both pure / deterministic.
  L3: specialist LLM is only invoked when ``AdequacyReport.ok == False`` to
      translate the structured failure into a user-facing caveat / fallback.

The adequacy report is JSON-serialisable so it can be embedded in tool results,
verified_facts, and analysis bundles.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from processors.sampling_physics import max_healthy_inter_arrival_seconds


# Sentinel reasons. Stable strings — downstream LLM caveats and tests match on these.
REASON_OK = "ok"
REASON_BELOW_MINIMUM = "below_minimum"
REASON_TOO_MANY_GAPS = "too_many_gaps"
REASON_BELOW_MIN_AND_GAPS = "below_minimum_and_gaps"
REASON_EMPTY = "empty"


def estimate_window_seconds(
    target_points: int,
    *,
    safety: float = 1.5,
    cadence_seconds: Optional[float] = None,
) -> int:
    """
    Estimate the window length needed to fetch at least ``target_points`` samples.

    Uses ``max_healthy_inter_arrival_seconds`` as the cadence proxy unless an
    explicit ``cadence_seconds`` is provided (e.g. measured from a prior fetch).
    A safety multiplier widens the window to absorb jitter and minor outages.

    Args:
        target_points:    Desired sample count (typically a processor's min_points).
        safety:           Window multiplier; >= 1. Default 1.5 covers normal jitter.
        cadence_seconds:  Override for the per-sample interval. When ``None`` the
                          network-type-aware cap from sampling_physics is used.

    Returns:
        Window span in seconds, clamped to >= 60 (one minute floor).
    """
    if target_points <= 0:
        return 60
    cadence = float(cadence_seconds) if cadence_seconds is not None else max_healthy_inter_arrival_seconds()
    cadence = max(cadence, 1e-3)
    factor = max(safety, 1.0)
    return max(60, int(target_points * cadence * factor))


def _gap_percent(timestamps: np.ndarray, cadence: float) -> float:
    """
    Fraction of total span that falls in inter-arrival deltas exceeding ``cadence * 1.5``.

    A coarse but cheap "patchy" indicator that doesn't require running the full
    detect_gaps adaptive logic — adequacy lives in the hot path of every analysis,
    so we keep it linear-time and dependency-free.
    """
    n = len(timestamps)
    if n < 2:
        return 0.0
    ts = np.sort(timestamps.astype(float))
    deltas = np.diff(ts)
    span = float(ts[-1] - ts[0])
    if span <= 0:
        return 0.0
    threshold = max(cadence * 1.5, 1.0)
    over = deltas[deltas > threshold]
    if over.size == 0:
        return 0.0
    return float(np.sum(over) / span * 100.0)


def check_adequacy(
    timestamps: np.ndarray,
    requirements: Dict[str, Any],
    *,
    cadence_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Score a fetched series against a processor's ``DATA_REQUIREMENTS``.

    Args:
        timestamps:       1-D array of Unix timestamps (seconds). Order is not assumed.
        requirements:     Dict with keys ``min_points`` (required), ``ideal_points``,
                          ``max_gap_pct``. Missing keys fall back to permissive defaults
                          so half-specified processors still get a meaningful answer.
        cadence_seconds:  Override for expected per-sample cadence. Defaults to
                          ``sampling_physics.max_healthy_inter_arrival_seconds()``.

    Returns:
        JSON-serialisable adequacy report. Stable schema:

        {
            "ok":              bool,
            "reason":          str,            # one of REASON_* constants
            "actual_points":   int,
            "target_min":      int,
            "target_ideal":    int,
            "gap_pct":         float,          # rounded to 2 dp
            "max_gap_pct":     float,
            "cadence_seconds": float,
            "note":            str | None,
        }
    """
    target_min = int(requirements.get("min_points", 0))
    target_ideal = int(requirements.get("ideal_points", target_min))
    max_gap_pct = float(requirements.get("max_gap_pct", 100.0))
    cadence = float(cadence_seconds) if cadence_seconds is not None else max_healthy_inter_arrival_seconds()

    n = int(len(timestamps))
    if n == 0:
        return {
            "ok": False,
            "reason": REASON_EMPTY,
            "actual_points": 0,
            "target_min": target_min,
            "target_ideal": target_ideal,
            "gap_pct": 0.0,
            "max_gap_pct": max_gap_pct,
            "cadence_seconds": cadence,
            "note": "Fetched series is empty; algorithm cannot run.",
        }

    gap_pct = round(_gap_percent(np.asarray(timestamps), cadence), 2)
    points_short = n < target_min
    too_patchy = gap_pct > max_gap_pct

    if not points_short and not too_patchy:
        reason = REASON_OK
        note = None
    elif points_short and too_patchy:
        reason = REASON_BELOW_MIN_AND_GAPS
        note = (
            f"Got {n} points over a series with {gap_pct:.1f}% gap coverage; "
            f"need >= {target_min} points and <= {max_gap_pct:.0f}% gaps."
        )
    elif points_short:
        reason = REASON_BELOW_MINIMUM
        note = (
            f"Got {n} points; need >= {target_min} for reliable output "
            f"(ideal: {target_ideal})."
        )
    else:
        reason = REASON_TOO_MANY_GAPS
        note = (
            f"Series spans {gap_pct:.1f}% in detected gaps; threshold is "
            f"{max_gap_pct:.0f}%. Likely connectivity issue, not a data problem."
        )

    return {
        "ok": reason == REASON_OK,
        "reason": reason,
        "actual_points": n,
        "target_min": target_min,
        "target_ideal": target_ideal,
        "gap_pct": gap_pct,
        "max_gap_pct": max_gap_pct,
        "cadence_seconds": cadence,
        "note": note,
    }


def adequacy_stub_result(
    algorithm_name: str,
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a uniform "skipped" result for processors that bail out when adequacy fails.

    Use this from inside a processor when ``check_adequacy`` returns ``ok=False`` so
    the LLM tool result and verified_facts entry have a consistent, recognisable shape.
    """
    return {
        "skipped": True,
        "algorithm": algorithm_name,
        "adequacy": report,
    }
