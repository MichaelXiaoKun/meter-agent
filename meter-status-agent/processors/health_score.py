"""
Composite meter health score.

Combines current status, optional profile metadata, and optional historical
flow verified facts into a 0-100 score. Missing optional components are not
treated as failures; available component weights are re-normalised.
"""

from __future__ import annotations

from typing import Any, Dict


WEIGHTS = {
    "staleness": 0.40,
    "signal_quality": 0.30,
    "gap_density": 0.20,
    "drift": 0.10,
}


def _component(score: float | None, *, weight: float, reason: str) -> Dict[str, Any]:
    available = score is not None
    return {
        "available": available,
        "score": round(max(0.0, min(100.0, float(score))), 2) if available else None,
        "weight": float(weight),
        "reason": reason,
    }


def _staleness_component(status: dict | None) -> Dict[str, Any]:
    staleness = status.get("staleness") if isinstance(status, dict) else None
    if not isinstance(staleness, dict):
        return _component(None, weight=WEIGHTS["staleness"], reason="staleness unavailable")
    comm = str(staleness.get("communication_status") or "").lower()
    score_by_status = {
        "fresh": 100.0,
        "stale": 70.0,
        "absent": 35.0,
        "lost": 0.0,
    }
    score = score_by_status.get(comm)
    if score is None:
        return _component(None, weight=WEIGHTS["staleness"], reason="unknown staleness state")
    if isinstance(status, dict) and status.get("online") is False:
        score = min(score, 40.0)
    return _component(score, weight=WEIGHTS["staleness"], reason=f"communication_status={comm}")


def _signal_component(status: dict | None) -> Dict[str, Any]:
    signal = status.get("signal") if isinstance(status, dict) else None
    if not isinstance(signal, dict) or signal.get("score") is None:
        return _component(None, weight=WEIGHTS["signal_quality"], reason="signal score unavailable")
    try:
        score = float(signal.get("score"))
    except (TypeError, ValueError):
        return _component(None, weight=WEIGHTS["signal_quality"], reason="signal score invalid")
    level = signal.get("level") or "unknown"
    return _component(score, weight=WEIGHTS["signal_quality"], reason=f"signal_level={level}")


def _gap_component(verified_facts: dict | None) -> Dict[str, Any]:
    if not isinstance(verified_facts, dict):
        return _component(None, weight=WEIGHTS["gap_density"], reason="flow gap facts unavailable")
    gap_count = verified_facts.get("gap_event_count")
    coverage = (
        verified_facts.get("coverage_6h")
        if isinstance(verified_facts.get("coverage_6h"), dict)
        else {}
    )
    coverage_ratio = None
    try:
        issue_buckets = float(coverage.get("buckets_with_issues"))
        n_buckets = float(coverage.get("n_buckets"))
        if n_buckets > 0:
            coverage_ratio = max(0.0, min(1.0, issue_buckets / n_buckets))
    except (TypeError, ValueError):
        coverage_ratio = None
    try:
        gaps = max(0.0, float(gap_count))
    except (TypeError, ValueError):
        if coverage_ratio is None:
            return _component(None, weight=WEIGHTS["gap_density"], reason="gap density unavailable")
        gaps = 0.0
    largest = verified_facts.get("largest_gap_duration_seconds")
    try:
        largest_gap = max(0.0, float(largest or 0.0))
    except (TypeError, ValueError):
        largest_gap = 0.0
    if coverage_ratio is not None:
        score = 100.0 - min(80.0, coverage_ratio * 80.0) - min(40.0, gaps * 4.0)
        reason = f"gap_event_count={int(gaps)}, coverage_issue_ratio={coverage_ratio:.2f}"
    else:
        score = 100.0 - gaps * 8.0
        reason = f"gap_event_count={int(gaps)}"
    if largest_gap >= 3600.0:
        score = min(score, 60.0)
    return _component(score, weight=WEIGHTS["gap_density"], reason=reason)


def _drift_component(verified_facts: dict | None) -> Dict[str, Any]:
    if not isinstance(verified_facts, dict):
        return _component(None, weight=WEIGHTS["drift"], reason="drift facts unavailable")
    cusum = verified_facts.get("cusum_drift")
    if not isinstance(cusum, dict) or cusum.get("skipped"):
        return _component(None, weight=WEIGHTS["drift"], reason="drift unavailable or skipped")
    drift = str(cusum.get("drift_detected") or "none").lower()
    if drift in {"none", "no_drift"}:
        score = 100.0
    else:
        pos = int(cusum.get("positive_alarm_count") or 0)
        neg = int(cusum.get("negative_alarm_count") or 0)
        score = max(30.0, 75.0 - 5.0 * (pos + neg))
    return _component(score, weight=WEIGHTS["drift"], reason=f"drift_detected={drift}")


def _verdict(score: float) -> str:
    if score >= 80.0:
        return "healthy"
    if score >= 60.0:
        return "degraded"
    return "unhealthy"


def compute_health_score(
    *,
    status: dict | None = None,
    profile: dict | None = None,
    verified_facts: dict | None = None,
) -> Dict[str, Any]:
    """Return ``{"score": 0-100, "components": ..., "verdict": ...}``.

    ``profile`` is accepted for the public contract and future scoring
    factors; the current v1 score is intentionally based on status and
    optional flow verified facts only.
    """
    _ = profile
    components = {
        "staleness": _staleness_component(status),
        "signal_quality": _signal_component(status),
        "gap_density": _gap_component(verified_facts),
        "drift": _drift_component(verified_facts),
    }
    available = [
        c
        for c in components.values()
        if c.get("available") and c.get("score") is not None
    ]
    if not available:
        return {
            "score": 0.0,
            "components": components,
            "verdict": "unhealthy",
            "weights_used": 0.0,
        }
    weight_sum = sum(float(c["weight"]) for c in available)
    weighted = sum(float(c["score"]) * float(c["weight"]) for c in available)
    score = weighted / weight_sum if weight_sum > 0 else 0.0
    return {
        "score": round(score, 2),
        "components": components,
        "verdict": _verdict(score),
        "weights_used": round(weight_sum, 2),
    }
