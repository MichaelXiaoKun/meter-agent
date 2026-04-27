"""
Reasoning schema — compact evidence / regime / hypothesis / next_checks block.

The goal of this module is to take the deterministic ``verified_facts`` output
and distil it into a *fixed-shape, bounded-size* structure that the LLM can use
as an anchor for its "what should we do next?" reasoning without having to
re-derive every fact from the Markdown narrative.

Design tenets
-------------
- Pure function of ``verified_facts`` (deterministic, no randomness).
- JSON-serialisable; safe to embed in prompts, reports, and analysis bundles.
- Fixed field names; bounded list lengths (``_MAX_*`` constants) so the token
  footprint cannot grow unbounded with data size.
- Codes are short enum-like strings so the LLM can treat them as categorical
  tokens rather than free prose.

Ultrasonic flow meter + IoT framing
-----------------------------------
The codes split deliberately along two physical axes so the model can route
follow-up actions quickly:

- **Physical / transducer side** — ``E_ZERO_FLOW_LONG``, ``E_PEAK_CLUSTER``,
  ``E_LOW_QUALITY_SUSTAINED``, ``E_FLATLINE``.
- **IoT / link side** — ``E_GAP_LONG``, ``E_SAMPLING_IRREGULAR``,
  ``E_COVERAGE_SPARSE``.

Hypotheses combine these axes (``H_COMMS_INSTABILITY``,
``H_SENSOR_OR_INSTALL_ISSUE``, ``H_AIR_BUBBLES_OR_DRAINAGE``,
``H_REAL_PROCESS_CHANGE``, ``H_METER_OFFLINE_OR_DRAINED``). The next-check
actions mirror real field/ops interventions so the orchestrator can chain to
the correct operator tool on the next turn.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Bounds (token-budget guard rails)
# ---------------------------------------------------------------------------

_MAX_EVIDENCE = 6
_MAX_HYPOTHESES = 3
_MAX_NEXT_CHECKS = 3

# Severity tiers; keep ordered for comparisons.
_SEV_LOW = "low"
_SEV_MEDIUM = "medium"
_SEV_HIGH = "high"
_SEVERITY_RANK = {_SEV_LOW: 0, _SEV_MEDIUM: 1, _SEV_HIGH: 2}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _coverage_issue_ratio(facts: Dict[str, Any]) -> float:
    cov = facts.get("coverage_6h")
    if not isinstance(cov, dict):
        return 0.0
    n = _safe_int(cov.get("n_buckets"))
    issues = _safe_int(cov.get("buckets_with_issues"))
    if n <= 0:
        return 0.0
    return issues / n


def _low_quality_fraction(facts: Dict[str, Any]) -> Tuple[float, int, int]:
    sq = facts.get("signal_quality")
    if not isinstance(sq, dict):
        return 0.0, 0, 0
    flagged = _safe_int(sq.get("flagged_count"))
    total = _safe_int(sq.get("total_count"))
    if total <= 0:
        return 0.0, flagged, total
    return flagged / total, flagged, total


def _longest_low_q_seconds(facts: Dict[str, Any]) -> float:
    sq = facts.get("signal_quality")
    if not isinstance(sq, dict):
        return 0.0
    longest = sq.get("longest_low_quality_stretch")
    if not isinstance(longest, dict):
        return 0.0
    return _safe_float(longest.get("duration_seconds"))


# ---------------------------------------------------------------------------
# Regime classification (single label, derived from facts only)
# ---------------------------------------------------------------------------


_REGIMES = (
    "NO_DATA",
    "ZERO_FLOW_DOMINANT",
    "STEADY_LOW_FLOW",
    "STEADY_FLOW",
    "CONTINUOUS_FLOW",
    "INTERMITTENT_BURST",
    "NOISY_OR_INSTALL_ISSUE",
    "CONSTANT_VALUE",
    "UNKNOWN",
)


def classify_regime(facts: Dict[str, Any]) -> str:
    """
    Deterministic single-label classifier over the verified-facts bundle.

    The rules are intentionally simple and auditable; each branch maps to one
    concrete operational stance the next-step reasoning can take.
    """
    n_rows = _safe_int(facts.get("n_rows"))
    if n_rows <= 0 or facts.get("error") == "empty_dataframe":
        return "NO_DATA"

    desc = facts.get("flow_rate_descriptive") or {}
    flat = facts.get("flatline") or {}

    flat_flag = flat.get("flag") if isinstance(flat, dict) else None
    if flat_flag in ("constant_flow_series",):
        return "CONSTANT_VALUE"

    low_q_frac, _, total_q = _low_quality_fraction(facts)
    # Sustained low quality OR flatline-adjacent → installation/sensor signal.
    if total_q > 0 and low_q_frac >= 0.20:
        return "NOISY_OR_INSTALL_ISSUE"
    if flat_flag == "near_constant_flow":
        return "NOISY_OR_INSTALL_ISSUE"

    if isinstance(desc, dict) and "median" in desc:
        median = _safe_float(desc.get("median"))
        p95 = _safe_float(desc.get("p95"))
        std = _safe_float(desc.get("std"))
        cv = desc.get("cv")
        cv_val = _safe_float(cv) if cv is not None else None

        zero_periods = _safe_int(facts.get("zero_flow_period_count"))

        # Zero-flow domination: median at/near zero and at least one long zero run.
        if median <= 1e-6 and zero_periods >= 1 and p95 <= max(0.5, median * 3.0):
            return "ZERO_FLOW_DOMINANT"

        # Bursty: big spread between median and p95 or noticeable CV with peaks.
        burst_ratio = (p95 / median) if median > 1e-6 else (p95 / 1e-6)
        if burst_ratio >= 5.0 or (cv_val is not None and cv_val >= 1.0):
            return "INTERMITTENT_BURST"

        # Steady flow classes: small CV, median above zero.
        if cv_val is not None and cv_val <= 0.15 and median > 1e-6:
            return "STEADY_LOW_FLOW" if median < 2.0 else "STEADY_FLOW"

        if median > 1e-6 and std <= max(0.25 * median, 1e-6):
            return "STEADY_LOW_FLOW" if median < 2.0 else "STEADY_FLOW"

        if median > 0.0:
            return "CONTINUOUS_FLOW"

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------


def _severity_from_ratio(ratio: float, low: float, high: float) -> str:
    if ratio >= high:
        return _SEV_HIGH
    if ratio >= low:
        return _SEV_MEDIUM
    return _SEV_LOW


def _build_evidence(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map verified facts to a bounded list of structured evidence entries."""
    items: List[Dict[str, Any]] = []

    # --- IoT-link side ---------------------------------------------------
    gap_count = _safe_int(facts.get("gap_event_count"))
    largest_gap_s = _safe_float(facts.get("largest_gap_duration_seconds"))
    cap_s = _safe_float(facts.get("max_healthy_inter_arrival_seconds"), 60.0)
    if gap_count > 0 and largest_gap_s > 0:
        sev_ratio = largest_gap_s / max(cap_s, 1.0)
        severity = _severity_from_ratio(sev_ratio, low=2.0, high=10.0)
        items.append(
            {
                "code": "E_GAP_LONG",
                "severity": severity,
                "value": {
                    "gap_event_count": gap_count,
                    "largest_gap_seconds": round(largest_gap_s, 2),
                    "healthy_cap_seconds": cap_s,
                },
                "source": "gap_event_count,largest_gap_duration_seconds",
            }
        )

    if bool(facts.get("sampling_irregular")):
        med_s = _safe_float(facts.get("sampling_median_interval_seconds"))
        p75_s = _safe_float(facts.get("sampling_p75_interval_seconds"))
        jitter = (p75_s / med_s) if med_s > 1e-9 else 0.0
        severity = _severity_from_ratio(jitter, low=1.35, high=2.5)
        items.append(
            {
                "code": "E_SAMPLING_IRREGULAR",
                "severity": severity,
                "value": {
                    "median_seconds": round(med_s, 3),
                    "p75_seconds": round(p75_s, 3),
                    "jitter_ratio": round(jitter, 3),
                },
                "source": "sampling_median_interval_seconds,sampling_p75_interval_seconds",
            }
        )

    cov_ratio = _coverage_issue_ratio(facts)
    if cov_ratio > 0.0:
        severity = _severity_from_ratio(cov_ratio, low=0.1, high=0.33)
        cov = facts.get("coverage_6h") or {}
        items.append(
            {
                "code": "E_COVERAGE_SPARSE",
                "severity": severity,
                "value": {
                    "issue_ratio": round(cov_ratio, 3),
                    "buckets_with_issues": _safe_int(cov.get("buckets_with_issues")),
                    "n_buckets": _safe_int(cov.get("n_buckets")),
                },
                "source": "coverage_6h",
            }
        )

    # --- Physical / transducer side --------------------------------------
    low_q_frac, flagged_q, total_q = _low_quality_fraction(facts)
    if total_q > 0 and flagged_q > 0:
        severity = _severity_from_ratio(low_q_frac, low=0.05, high=0.20)
        items.append(
            {
                "code": "E_QUALITY_DROP",
                "severity": severity,
                "value": {
                    "low_quality_fraction": round(low_q_frac, 4),
                    "flagged_count": flagged_q,
                    "total_count": total_q,
                },
                "source": "signal_quality",
            }
        )

    longest_low_q = _longest_low_q_seconds(facts)
    if longest_low_q >= 300.0:  # 5+ minutes counts as sustained
        severity = _severity_from_ratio(longest_low_q, low=900.0, high=3600.0)
        items.append(
            {
                "code": "E_LOW_QUALITY_SUSTAINED",
                "severity": severity,
                "value": {
                    "longest_stretch_seconds": round(longest_low_q, 1),
                },
                "source": "signal_quality.longest_low_quality_stretch",
            }
        )

    zero_periods = _safe_int(facts.get("zero_flow_period_count"))
    if zero_periods > 0:
        severity = _severity_from_ratio(zero_periods, low=1, high=5)
        items.append(
            {
                "code": "E_ZERO_FLOW_LONG",
                "severity": severity,
                "value": {"zero_flow_period_count": zero_periods},
                "source": "zero_flow_period_count",
            }
        )

    flat = facts.get("flatline") or {}
    flat_flag = flat.get("flag") if isinstance(flat, dict) else None
    if flat_flag in ("constant_flow_series", "near_constant_flow"):
        items.append(
            {
                "code": "E_FLATLINE",
                "severity": _SEV_HIGH if flat_flag == "constant_flow_series" else _SEV_MEDIUM,
                "value": {
                    "flag": flat_flag,
                    "unique_flow_values": _safe_int(flat.get("unique_flow_values")),
                    "coefficient_of_variation": _safe_float(
                        flat.get("coefficient_of_variation")
                    ),
                },
                "source": "flatline",
            }
        )

    # Sort by severity (high first) then by code for stable output, trim.
    items.sort(
        key=lambda it: (-_SEVERITY_RANK.get(it["severity"], 0), it["code"])
    )
    return items[:_MAX_EVIDENCE]


# ---------------------------------------------------------------------------
# Hypotheses
# ---------------------------------------------------------------------------


def _confidence_from_severities(codes: List[str], evidence_map: Dict[str, Dict[str, Any]]) -> float:
    """Average severity-as-weight across the cited evidence codes; clamp to [0, 1]."""
    if not codes:
        return 0.0
    weights = []
    for c in codes:
        ev = evidence_map.get(c)
        if not ev:
            continue
        rank = _SEVERITY_RANK.get(ev.get("severity"), 0)
        weights.append((rank + 1) / 3.0)  # 1/3, 2/3, 1.0
    if not weights:
        return 0.0
    return round(min(1.0, sum(weights) / len(weights)), 2)


def _build_hypotheses(
    evidence: List[Dict[str, Any]],
    regime: str,
) -> List[Dict[str, Any]]:
    """Deterministic evidence → candidate-cause mapping with confidences."""
    if not evidence:
        return []
    ev_map = {e["code"]: e for e in evidence}

    candidates: List[Dict[str, Any]] = []

    # H_COMMS_INSTABILITY: gaps + sparse coverage ± irregular cadence.
    comms_codes = [
        c for c in ("E_GAP_LONG", "E_COVERAGE_SPARSE", "E_SAMPLING_IRREGULAR") if c in ev_map
    ]
    if comms_codes:
        candidates.append(
            {
                "code": "H_COMMS_INSTABILITY",
                "confidence": _confidence_from_severities(comms_codes, ev_map),
                "because": comms_codes,
            }
        )

    # H_SENSOR_OR_INSTALL_ISSUE: sustained low quality / flatline.
    sensor_codes = [c for c in ("E_LOW_QUALITY_SUSTAINED", "E_FLATLINE") if c in ev_map]
    if sensor_codes:
        candidates.append(
            {
                "code": "H_SENSOR_OR_INSTALL_ISSUE",
                "confidence": _confidence_from_severities(sensor_codes, ev_map),
                "because": sensor_codes,
            }
        )

    # H_AIR_BUBBLES_OR_DRAINAGE: intermittent low quality without a long stretch.
    if "E_QUALITY_DROP" in ev_map and "E_LOW_QUALITY_SUSTAINED" not in ev_map:
        candidates.append(
            {
                "code": "H_AIR_BUBBLES_OR_DRAINAGE",
                "confidence": _confidence_from_severities(["E_QUALITY_DROP"], ev_map),
                "because": ["E_QUALITY_DROP"],
            }
        )

    # H_METER_OFFLINE_OR_DRAINED: long zero-flow with sustained low quality.
    if "E_ZERO_FLOW_LONG" in ev_map and (
        "E_LOW_QUALITY_SUSTAINED" in ev_map or "E_FLATLINE" in ev_map
    ):
        codes = [
            c
            for c in ("E_ZERO_FLOW_LONG", "E_LOW_QUALITY_SUSTAINED", "E_FLATLINE")
            if c in ev_map
        ]
        candidates.append(
            {
                "code": "H_METER_OFFLINE_OR_DRAINED",
                "confidence": _confidence_from_severities(codes, ev_map),
                "because": codes,
            }
        )

    # H_REAL_PROCESS_CHANGE: bursty regime without sustained quality issues.
    if regime == "INTERMITTENT_BURST" and "E_LOW_QUALITY_SUSTAINED" not in ev_map:
        process_codes = [
            c for c in ("E_ZERO_FLOW_LONG",) if c in ev_map
        ] or ["E_FLOW_REGIME"]
        candidates.append(
            {
                "code": "H_REAL_PROCESS_CHANGE",
                "confidence": round(
                    min(
                        1.0,
                        0.5 + 0.1 * len([c for c in process_codes if c in ev_map]),
                    ),
                    2,
                ),
                "because": process_codes,
            }
        )

    # De-duplicate by code; sort by confidence desc, code asc for stable output.
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for h in candidates:
        if h["code"] in seen:
            continue
        seen.add(h["code"])
        unique.append(h)
    unique.sort(key=lambda h: (-_safe_float(h.get("confidence")), h["code"]))
    return unique[:_MAX_HYPOTHESES]


# ---------------------------------------------------------------------------
# Next-check actions
# ---------------------------------------------------------------------------


# Ordered mapping: hypothesis → (action, expected-observation).
# Earlier entries are considered higher-priority when multiple hypotheses fire.
_ACTION_PLAYBOOK: List[Tuple[str, str, str]] = [
    (
        "H_COMMS_INSTABILITY",
        "check_uplink_rssi_and_packet_loss",
        "gap_events_drop_after_reconnect_or_gateway_swap",
    ),
    (
        "H_SENSOR_OR_INSTALL_ISSUE",
        "verify_transducer_mounting_and_coupling",
        "quality_score_recovers_after_reseating_pads",
    ),
    (
        "H_METER_OFFLINE_OR_DRAINED",
        "inspect_pipe_for_drainage_and_confirm_service_state",
        "flow_returns_after_service_reopened_or_pipe_refilled",
    ),
    (
        "H_AIR_BUBBLES_OR_DRAINAGE",
        "inspect_pipe_for_air_pockets_or_recent_service_interruption",
        "quality_spikes_align_with_known_refill_or_maintenance",
    ),
    (
        "H_REAL_PROCESS_CHANGE",
        "crosscheck_shift_schedule_vs_burst_windows",
        "bursts_align_with_declared_operational_windows",
    ),
]


def _build_next_checks(hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map hypotheses to concrete operator actions, preserving playbook ordering."""
    if not hypotheses:
        return []
    present = {h["code"] for h in hypotheses}
    out: List[Dict[str, Any]] = []
    for code, action, expect in _ACTION_PLAYBOOK:
        if code in present:
            out.append(
                {
                    "priority": len(out) + 1,
                    "action": action,
                    "for_hypothesis": code,
                    "expect": expect,
                }
            )
        if len(out) >= _MAX_NEXT_CHECKS:
            break
    return out


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_reasoning_schema(
    facts: Dict[str, Any],
    *,
    network_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deterministic, bounded reasoning block derived from ``build_verified_facts``.

    Keys
    ----
    - ``schema_version``: bump if the consumer contract changes.
    - ``regime``: single enum label (see ``_REGIMES``).
    - ``evidence``: list (≤ 6) of ``{code, severity, value, source}``.
    - ``hypotheses``: list (≤ 3) of ``{code, confidence, because[...]}``.
    - ``next_checks``: list (≤ 3) of ``{priority, action, for_hypothesis, expect}``.
    - ``conflict_policy``: one-line directive the consumer must honour.
    - ``context``: small metadata block (``network_type``, totals).

    Token budget
    ------------
    At most ~250 JSON tokens at serialisation; designed to REPLACE redundant
    narrative in the Markdown report, not add to it.
    """
    facts = facts or {}
    regime = classify_regime(facts)
    evidence = _build_evidence(facts)
    hypotheses = _build_hypotheses(evidence, regime)
    next_checks = _build_next_checks(hypotheses)
    attribution = facts.get("anomaly_attribution")
    attribution_anchor = None
    if isinstance(attribution, dict):
        attribution_anchor = {
            "primary_type": attribution.get("primary_type"),
            "severity": attribution.get("severity"),
            "confidence": attribution.get("confidence"),
            "summary": attribution.get("summary"),
            "next_checks": (attribution.get("next_checks") or [])[:3],
        }

    return {
        "schema_version": 1,
        "regime": regime,
        "attribution": attribution_anchor,
        "evidence": evidence,
        "hypotheses": hypotheses,
        "next_checks": next_checks,
        "conflict_policy": (
            "If the narrative disagrees with anomaly_attribution, these codes, or verified_facts, "
            "trust anomaly_attribution and verified_facts."
        ),
        "context": {
            "n_rows": _safe_int(facts.get("n_rows")),
            "network_type": network_type,
            "healthy_cap_seconds": _safe_float(
                facts.get("max_healthy_inter_arrival_seconds"), 60.0
            ),
        },
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def schema_to_compact_markdown(schema: Dict[str, Any]) -> str:
    """
    Render the schema as a terse Markdown block suitable for the report.

    Designed to be *shorter* than the verbose narrative it replaces — each
    section is a single line where possible, which keeps the prompt/report
    token count flat while giving the LLM a clean anchor to cite.
    """
    if not isinstance(schema, dict) or not schema:
        return ""

    lines = [
        "\n---\n\n",
        "## Reasoning anchors (code-generated)\n\n",
        "Use these codes as the primary cite for next-step reasoning. "
        "If narrative disagrees, trust these codes and verified_facts.\n\n",
    ]

    regime = schema.get("regime") or "UNKNOWN"
    lines.append(f"- **regime:** `{regime}`\n")

    attribution = schema.get("attribution")
    if isinstance(attribution, dict) and attribution.get("primary_type"):
        ptype = attribution.get("primary_type")
        sev = attribution.get("severity") or "unknown"
        conf = attribution.get("confidence") or "unknown"
        summary = attribution.get("summary") or ""
        lines.append(f"- **attribution:** `{ptype}` ({sev}, {conf}) — {summary}\n")

    evidence = schema.get("evidence") or []
    if evidence:
        ev_parts = [f"{e['code']}:{e.get('severity', 'low')}" for e in evidence]
        lines.append(f"- **evidence:** {', '.join(ev_parts)}\n")
    else:
        lines.append("- **evidence:** (none flagged)\n")

    hypotheses = schema.get("hypotheses") or []
    if hypotheses:
        hy_parts = []
        for h in hypotheses:
            code = h.get("code")
            conf = h.get("confidence")
            because = ",".join(h.get("because") or [])
            if conf is None:
                hy_parts.append(f"{code}[{because}]")
            else:
                hy_parts.append(f"{code}({conf}|{because})")
        lines.append(f"- **hypotheses:** {'; '.join(hy_parts)}\n")
    else:
        lines.append("- **hypotheses:** (none)\n")

    next_checks = schema.get("next_checks") or []
    if next_checks:
        nc_parts = []
        for nc in next_checks:
            pri = nc.get("priority")
            action = nc.get("action")
            for_h = nc.get("for_hypothesis")
            nc_parts.append(f"{pri}) {action}→{for_h}")
        lines.append(f"- **next_checks:** {' | '.join(nc_parts)}\n")
    else:
        lines.append("- **next_checks:** (none)\n")

    lines.append("")
    return "".join(lines)
