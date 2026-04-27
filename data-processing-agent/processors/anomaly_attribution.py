"""
Deterministic anomaly attribution for flow analysis.

This layer combines existing verified facts into an operator-facing diagnostic
interpretation. It does not introduce a new statistical detector; it decides
which existing processor signals should lead the explanation.
"""

from __future__ import annotations

from typing import Any, Dict, List


PRIMARY_TYPES = {
    "real_flow_change",
    "possible_leak_or_baseline_rise",
    "sensor_or_install_issue",
    "communications_or_sampling_issue",
    "insufficient_data",
    "normal",
}

SEVERITIES = {"none", "low", "medium", "high"}
CONFIDENCES = {"low", "medium", "high"}

_MAX_EVIDENCE = 5
_MAX_NEXT_CHECKS = 4


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


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coverage_issue_ratio(facts: Dict[str, Any]) -> float:
    cov = _as_dict(facts.get("coverage_6h"))
    n = _safe_int(cov.get("n_buckets"))
    if n <= 0:
        return 0.0
    return _safe_int(cov.get("buckets_with_issues")) / n


def _low_quality_fraction(facts: Dict[str, Any]) -> float:
    sq = _as_dict(facts.get("signal_quality"))
    total = _safe_int(sq.get("total_count"))
    flagged = _safe_int(sq.get("flagged_count"))
    if total <= 0:
        return 0.0
    return flagged / total


def _longest_low_quality_seconds(facts: Dict[str, Any]) -> float:
    sq = _as_dict(facts.get("signal_quality"))
    longest = _as_dict(sq.get("longest_low_quality_stretch"))
    return _safe_float(longest.get("duration_seconds"))


def _evidence(code: str, message: str, source: str, value: Any = None) -> Dict[str, Any]:
    out = {"code": code, "message": message, "source": source}
    if value is not None:
        out["value"] = value
    return out


def _severity_for_alarms(total_alarms: int) -> str:
    if total_alarms >= 20:
        return "high"
    if total_alarms >= 3:
        return "medium"
    if total_alarms > 0:
        return "low"
    return "none"


def _quality_ok(facts: Dict[str, Any]) -> bool:
    return _low_quality_fraction(facts) < 0.05 and _longest_low_quality_seconds(facts) < 300.0


def _gaps_low(facts: Dict[str, Any]) -> bool:
    gap_count = _safe_int(facts.get("gap_event_count"))
    largest_gap = _safe_float(facts.get("largest_gap_duration_seconds"))
    cap = max(_safe_float(facts.get("max_healthy_inter_arrival_seconds"), 60.0), 1.0)
    return gap_count == 0 or (largest_gap <= cap * 2.0 and _coverage_issue_ratio(facts) < 0.10)


def _next_checks(*items: str) -> List[str]:
    return [item for item in items if item][:_MAX_NEXT_CHECKS]


def _result(
    *,
    primary_type: str,
    severity: str,
    confidence: str,
    primary_cause: str,
    summary: str,
    evidence: List[Dict[str, Any]],
    counter_evidence: List[Dict[str, Any]] | None = None,
    next_checks: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "state": "ready",
        "primary_type": primary_type if primary_type in PRIMARY_TYPES else "normal",
        "severity": severity if severity in SEVERITIES else "low",
        "confidence": confidence if confidence in CONFIDENCES else "low",
        "primary_cause": primary_cause,
        "summary": summary,
        "evidence": evidence[:_MAX_EVIDENCE],
        "counter_evidence": (counter_evidence or [])[:_MAX_EVIDENCE],
        "next_checks": (next_checks or [])[:_MAX_NEXT_CHECKS],
    }


def build_anomaly_attribution(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combine CUSUM, adequacy, gaps, coverage, quality, flatline, and quiet baseline.

    The output schema is stable and intentionally compact so the LLM can explain
    the deterministic result without inventing extra causes.
    """
    facts = facts or {}
    if _safe_int(facts.get("n_rows")) <= 0 or facts.get("error") == "empty_dataframe":
        return _result(
            primary_type="insufficient_data",
            severity="low",
            confidence="high",
            primary_cause="No usable flow samples were available.",
            summary="There is not enough data to make a reliable flow diagnosis.",
            evidence=[
                _evidence("NO_DATA", "No usable flow samples were available.", "n_rows")
            ],
            next_checks=_next_checks("Widen the analysis window", "Check meter connectivity"),
        )

    cusum = _as_dict(facts.get("cusum_drift"))
    adequacy = _as_dict(cusum.get("adequacy"))
    adequacy_ok = adequacy.get("ok")
    adequacy_reason = str(adequacy.get("reason") or "")
    cusum_skipped = bool(cusum.get("skipped")) or adequacy_ok is False

    gap_count = _safe_int(facts.get("gap_event_count"))
    largest_gap = _safe_float(facts.get("largest_gap_duration_seconds"))
    cap = max(_safe_float(facts.get("max_healthy_inter_arrival_seconds"), 60.0), 1.0)
    coverage_ratio = _coverage_issue_ratio(facts)
    low_q_fraction = _low_quality_fraction(facts)
    longest_low_q = _longest_low_quality_seconds(facts)
    flat = _as_dict(facts.get("flatline"))
    flat_flag = flat.get("flag")
    quiet = _as_dict(facts.get("quiet_flow_baseline"))
    quiet_median = _safe_float(quiet.get("quiet_flow_median"))
    zero_periods = _safe_int(facts.get("zero_flow_period_count"))

    communication_issue = (
        adequacy_reason in {"too_many_gaps", "below_minimum_and_gaps"}
        or (gap_count > 0 and largest_gap > cap * 2.0)
        or coverage_ratio >= 0.25
    )
    sparse_not_patchy = adequacy_reason in {"empty", "below_minimum"} or (
        cusum_skipped and _safe_int(adequacy.get("actual_points")) < _safe_int(adequacy.get("target_min"))
    )
    sensor_issue = (
        low_q_fraction >= 0.20
        or longest_low_q >= 900.0
        or flat_flag in {"constant_flow_series", "near_constant_flow", "no_valid_data"}
    )
    drift = str(cusum.get("drift_detected") or "none")
    pos_alarms = _safe_int(cusum.get("positive_alarm_count"))
    neg_alarms = _safe_int(cusum.get("negative_alarm_count"))
    total_alarms = pos_alarms + neg_alarms
    drift_detected = not cusum_skipped and drift in {"upward", "downward", "both"}
    quiet_baseline_rise = quiet_median >= 0.05 and zero_periods <= 1 and _quality_ok(facts) and _gaps_low(facts)

    if sparse_not_patchy:
        actual = _safe_int(adequacy.get("actual_points"))
        target = _safe_int(adequacy.get("target_min"))
        return _result(
            primary_type="insufficient_data",
            severity="low",
            confidence="high",
            primary_cause="The analysis window does not contain enough usable samples.",
            summary=(
                "The data is not sufficient for a reliable anomaly judgment. "
                f"CUSUM was skipped with {actual} samples available and {target} required."
            ),
            evidence=[
                _evidence(
                    "ADEQUACY_FAILED",
                    f"Only {actual} samples were available; {target} are required.",
                    "cusum_drift.adequacy",
                    {"actual_points": actual, "target_min": target, "reason": adequacy_reason},
                )
            ],
            next_checks=_next_checks("Widen the analysis window", "Check meter connectivity"),
        )

    if communication_issue:
        severity = "high" if largest_gap > cap * 10.0 or coverage_ratio >= 0.50 else "medium"
        confidence = "high" if gap_count > 0 or coverage_ratio >= 0.25 else "medium"
        counter = []
        if drift_detected:
            counter.append(
                _evidence(
                    "CUSUM_DRIFT_PRESENT",
                    "CUSUM did detect drift, but sampling gaps reduce confidence that it is hydraulic.",
                    "cusum_drift",
                    {"direction": drift, "alarms": total_alarms},
                )
            )
        return _result(
            primary_type="communications_or_sampling_issue",
            severity=severity,
            confidence=confidence,
            primary_cause="Missing or sparse samples make the flow window less reliable.",
            summary=(
                "The strongest signal is a communications or sampling issue, so drift conclusions "
                "should be treated cautiously until coverage improves."
            ),
            evidence=[
                _evidence(
                    "GAPS_OR_COVERAGE",
                    f"{gap_count} gap event(s), largest gap {largest_gap:.0f}s, coverage issue ratio {coverage_ratio:.0%}.",
                    "gap_event_count,largest_gap_duration_seconds,coverage_6h",
                    {
                        "gap_event_count": gap_count,
                        "largest_gap_seconds": largest_gap,
                        "healthy_cap_seconds": cap,
                        "coverage_issue_ratio": round(coverage_ratio, 3),
                    },
                )
            ],
            counter_evidence=counter,
            next_checks=_next_checks("Check meter connectivity", "Widen the analysis window", "Retry after coverage improves"),
        )

    if sensor_issue:
        evidence = []
        if low_q_fraction >= 0.20 or longest_low_q >= 900.0:
            evidence.append(
                _evidence(
                    "LOW_SIGNAL_QUALITY",
                    f"{low_q_fraction:.0%} of quality readings are low; longest low-quality stretch is {longest_low_q:.0f}s.",
                    "signal_quality",
                    {
                        "low_quality_fraction": round(low_q_fraction, 3),
                        "longest_low_quality_seconds": longest_low_q,
                    },
                )
            )
        if flat_flag:
            evidence.append(
                _evidence(
                    "FLATLINE",
                    f"Flow variability processor flagged {flat_flag}.",
                    "flatline",
                    {"flag": flat_flag},
                )
            )
        return _result(
            primary_type="sensor_or_install_issue",
            severity="high" if flat_flag == "constant_flow_series" or longest_low_q >= 3600.0 else "medium",
            confidence="high" if flat_flag or longest_low_q >= 900.0 else "medium",
            primary_cause="Signal quality or flatline evidence points to a sensor/install condition.",
            summary="The data is more consistent with a sensor, installation, or acoustic signal issue than with confirmed flow behavior.",
            evidence=evidence,
            next_checks=_next_checks(
                "Check signal quality now",
                "Verify transducer pads and coupling",
                "Verify pipe material, size, and transducer angle",
            ),
        )

    if drift_detected and _quality_ok(facts) and _gaps_low(facts):
        direction = "upward" if drift == "upward" else "downward" if drift == "downward" else "mixed"
        severity = _severity_for_alarms(total_alarms)
        return _result(
            primary_type="real_flow_change",
            severity=severity,
            confidence="high",
            primary_cause=f"CUSUM found sustained {direction} drift with adequate data and no major quality/gap caveats.",
            summary=f"The strongest interpretation is a real sustained {direction} flow change in this window.",
            evidence=[
                _evidence(
                    "CUSUM_DRIFT",
                    f"CUSUM detected {drift} drift with {pos_alarms} upward and {neg_alarms} downward alarm(s).",
                    "cusum_drift",
                    {
                        "direction": drift,
                        "positive_alarm_count": pos_alarms,
                        "negative_alarm_count": neg_alarms,
                        "first_alarm_timestamp": cusum.get("first_alarm_timestamp"),
                    },
                ),
                _evidence(
                    "DATA_ADEQUATE",
                    "Adequacy passed and sampling/quality checks do not show a major caveat.",
                    "cusum_drift.adequacy,signal_quality,coverage_6h",
                    {"actual_points": adequacy.get("actual_points"), "gap_pct": adequacy.get("gap_pct")},
                ),
            ],
            next_checks=_next_checks("Compare against the previous day", "Check operational schedule", "Check signal quality now"),
        )

    if quiet_baseline_rise:
        severity = "medium" if quiet_median >= 1.0 else "low"
        return _result(
            primary_type="possible_leak_or_baseline_rise",
            severity=severity,
            confidence="medium",
            primary_cause="Quiet-period flow is above zero while quality and coverage are acceptable.",
            summary=(
                "The window suggests a possible leak or elevated baseline flow, but V1 treats this "
                "as a heuristic and not a definitive leak diagnosis."
            ),
            evidence=[
                _evidence(
                    "QUIET_BASELINE_NONZERO",
                    f"Quiet-flow median is {quiet_median:.3g} gal/min with few zero-flow periods.",
                    "quiet_flow_baseline,zero_flow_period_count",
                    {"quiet_flow_median": quiet_median, "zero_flow_period_count": zero_periods},
                )
            ],
            counter_evidence=[
                _evidence("NO_CUSUM_DRIFT", "CUSUM did not find sustained drift in this window.", "cusum_drift")
            ],
            next_checks=_next_checks("Compare overnight baseline", "Inspect for continuous usage", "Check meter health"),
        )

    return _result(
        primary_type="normal",
        severity="none",
        confidence="high" if adequacy_ok is True else "medium",
        primary_cause="No major drift, gap, quality, flatline, or baseline warning dominates this window.",
        summary="No clear anomaly was detected in the available flow window.",
        evidence=[
            _evidence(
                "NO_MAJOR_FLAGS",
                "Adequacy, CUSUM, gap, quality, and flatline checks do not point to a dominant issue.",
                "verified_facts",
            )
        ],
        next_checks=_next_checks("Continue monitoring", "Compare another window if the operator observed a symptom"),
    )


def slim_anomaly_attribution_for_prompt(attribution: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the prompt-facing attribution compact while preserving the decision."""
    if not isinstance(attribution, dict):
        return {}
    return {
        "state": attribution.get("state"),
        "primary_type": attribution.get("primary_type"),
        "severity": attribution.get("severity"),
        "confidence": attribution.get("confidence"),
        "primary_cause": attribution.get("primary_cause"),
        "summary": attribution.get("summary"),
        "evidence": (attribution.get("evidence") or [])[:3],
        "counter_evidence": (attribution.get("counter_evidence") or [])[:2],
        "next_checks": (attribution.get("next_checks") or [])[:4],
    }
