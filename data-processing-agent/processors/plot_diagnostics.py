"""
Deterministic diagnostic markers for flow plots.

The chart layer should show evidence that already exists in verified facts.
This module turns CUSUM, gaps, quality, flatline, and attribution outputs into a
small stable marker schema that both matplotlib and the UI can consume.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from processors.continuity import detect_gaps


Marker = Dict[str, Any]

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _window_bounds(timestamps: np.ndarray) -> tuple[int | None, int | None]:
    ts = np.asarray(timestamps, dtype=float)
    valid = ts[~np.isnan(ts)]
    if len(valid) == 0:
        return None, None
    return int(np.nanmin(valid)), int(np.nanmax(valid))


def _alarm_severity(total_alarms: int) -> str:
    if total_alarms >= 20:
        return "high"
    if total_alarms >= 3:
        return "medium"
    return "low"


def _gap_severity(duration_seconds: float, cap_seconds: float) -> str:
    cap = max(cap_seconds, 1.0)
    if duration_seconds >= cap * 10:
        return "high"
    if duration_seconds >= cap * 3:
        return "medium"
    return "low"


def _quality_severity(interval: Dict[str, Any], flagged_percent: float) -> str:
    duration = _safe_float(interval.get("duration_seconds"))
    if duration >= 900 or flagged_percent >= 20:
        return "high"
    if duration >= 300 or flagged_percent >= 5:
        return "medium"
    return "low"


def _drift_marker(facts: Dict[str, Any]) -> Marker | None:
    cusum = _as_dict(facts.get("cusum_drift"))
    if cusum.get("skipped"):
        return None
    direction = str(cusum.get("drift_detected") or "none")
    if direction in {"", "none", "None"}:
        return None

    up = _safe_int(cusum.get("positive_alarm_count"))
    down = _safe_int(cusum.get("negative_alarm_count"))
    total = up + down
    ts = cusum.get("first_alarm_timestamp")
    if ts is None:
        ts = _as_dict(cusum.get("first_positive_alarm")).get("timestamp")
    if ts is None:
        ts = _as_dict(cusum.get("first_negative_alarm")).get("timestamp")
    if ts is None:
        return None

    label = {
        "upward": "Upward drift alarm",
        "downward": "Downward drift alarm",
        "mixed": "Mixed drift alarms",
    }.get(direction, "Drift alarm")
    return {
        "type": "drift",
        "label": label,
        "severity": _alarm_severity(total),
        "timestamp": int(float(ts)),
        "explanation": (
            f"CUSUM detected a sustained {direction} shift "
            f"({up} upward alarms, {down} downward alarms)."
        ),
        "source": "cusum_drift",
    }


def _gap_markers(timestamps: np.ndarray, facts: Dict[str, Any]) -> List[Marker]:
    cap = _safe_float(facts.get("max_healthy_inter_arrival_seconds"), 60.0)
    gaps = detect_gaps(np.asarray(timestamps, dtype=float), None)
    largest = sorted(gaps, key=lambda g: _safe_float(g.get("duration_seconds")), reverse=True)[:3]
    out: List[Marker] = []
    for gap in largest:
        dur = _safe_float(gap.get("duration_seconds"))
        missing = _safe_int(gap.get("expected_points_missing"))
        out.append(
            {
                "type": "gap",
                "label": "Missing data",
                "severity": _gap_severity(dur, cap),
                "start": _safe_int(gap.get("start_timestamp")),
                "end": _safe_int(gap.get("end_timestamp")),
                "explanation": (
                    f"No samples were received for about {dur:.0f}s; "
                    f"roughly {missing} readings may be missing."
                ),
                "source": "gaps",
            }
        )
    return out


def _quality_markers(facts: Dict[str, Any]) -> List[Marker]:
    sq = _as_dict(facts.get("signal_quality"))
    intervals = sq.get("low_quality_intervals")
    if not isinstance(intervals, list):
        return []
    flagged_percent = _safe_float(sq.get("flagged_percent"))
    ranked = sorted(
        [iv for iv in intervals if isinstance(iv, dict)],
        key=lambda iv: (_safe_float(iv.get("duration_seconds")), _safe_int(iv.get("point_count"))),
        reverse=True,
    )[:2]
    out: List[Marker] = []
    for iv in ranked:
        points = _safe_int(iv.get("point_count"))
        mean_q = _safe_float(iv.get("mean_quality_score"))
        out.append(
            {
                "type": "low_quality",
                "label": "Low signal quality",
                "severity": _quality_severity(iv, flagged_percent),
                "start": _safe_int(iv.get("start_timestamp")),
                "end": _safe_int(iv.get("end_timestamp")),
                "explanation": (
                    f"{points} readings had quality at or below the threshold; "
                    f"mean quality was {mean_q:.0f}."
                ),
                "source": "signal_quality",
            }
        )
    return out


def _flatline_marker(timestamps: np.ndarray, facts: Dict[str, Any]) -> Marker | None:
    flat = _as_dict(facts.get("flatline"))
    if not flat.get("flag"):
        return None
    start, end = _window_bounds(timestamps)
    if start is None or end is None:
        return None
    note = str(flat.get("note") or "The flow series is nearly constant.")
    return {
        "type": "flatline",
        "label": "Near-constant flow",
        "severity": "medium",
        "start": start,
        "end": end,
        "explanation": note,
        "source": "flatline",
    }


def _baseline_marker(timestamps: np.ndarray, facts: Dict[str, Any]) -> Marker | None:
    attribution = _as_dict(facts.get("anomaly_attribution"))
    quiet = _as_dict(facts.get("quiet_flow_baseline"))
    quiet_median = _safe_float(quiet.get("quiet_flow_median"))
    primary = attribution.get("primary_type")
    if primary != "possible_leak_or_baseline_rise" and quiet_median < 0.05:
        return None
    start, end = _window_bounds(timestamps)
    if start is None or end is None:
        return None
    severity = str(attribution.get("severity") or "low")
    if severity not in {"low", "medium", "high"}:
        severity = "low"
    return {
        "type": "baseline",
        "label": "Possible baseline rise",
        "severity": severity,
        "start": start,
        "end": end,
        "explanation": (
            f"The quiet-flow median is {quiet_median:.3f} gal/min, "
            "so the lowest-flow readings are not near zero."
        ),
        "source": "anomaly_attribution",
    }


def build_diagnostic_markers(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    verified_facts: Dict[str, Any] | None,
    *,
    max_markers: int = 8,
) -> List[Marker]:
    """Return compact chart markers derived only from deterministic processors."""
    del values, quality  # Marker evidence comes from verified facts plus timestamps.
    facts = verified_facts or {}
    markers: List[Marker] = []

    drift = _drift_marker(facts)
    if drift:
        markers.append(drift)
    markers.extend(_gap_markers(timestamps, facts))
    markers.extend(_quality_markers(facts))

    flat = _flatline_marker(timestamps, facts)
    if flat:
        markers.append(flat)
    baseline = _baseline_marker(timestamps, facts)
    if baseline:
        markers.append(baseline)

    def sort_key(marker: Marker) -> tuple[int, float, str]:
        sev = _SEVERITY_RANK.get(str(marker.get("severity")), 3)
        t = marker.get("timestamp", marker.get("start", 0))
        return sev, _safe_float(t), str(marker.get("type") or "")

    return sorted(markers, key=sort_key)[:max_markers]


def diagnostic_caption(
    markers: List[Marker],
    verified_facts: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Build the caption payload attached to the diagnostic timeline plot."""
    facts = verified_facts or {}
    attribution = _as_dict(facts.get("anomaly_attribution"))
    summary = str(attribution.get("summary") or "").strip()
    if not summary:
        summary = (
            "Diagnostic markers highlight drift, data gaps, signal quality, "
            "and baseline evidence on the same timeline."
        )
    next_checks = attribution.get("next_checks")
    return {
        "plot_type": "diagnostic_timeline",
        "summary": summary,
        "diagnostic_markers": markers,
        "marker_count": len(markers),
        "next_actions": next_checks[:4] if isinstance(next_checks, list) else [],
    }
