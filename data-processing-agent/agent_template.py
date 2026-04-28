"""
Deterministic template renderer for flow-analysis reports.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from processors.plots import generate_plot
from processors.verified_facts import build_verified_facts


def _fmt(value: object, digits: int = 6) -> str:
    if isinstance(value, (int, float)) and np.isfinite(float(value)):
        return f"{float(value):.{digits}g}"
    return "n/a"


def _quality_array(df: pd.DataFrame) -> np.ndarray:
    if "quality" in df.columns:
        return df["quality"].to_numpy(dtype=float)
    return np.full(len(df), np.nan)


def _make_standard_plots(
    df: pd.DataFrame,
    serial_number: str,
    verified_facts: Dict[str, Any],
) -> None:
    if df.empty or "timestamp" not in df.columns or "flow_rate" not in df.columns:
        return
    timestamps = df["timestamp"].to_numpy(dtype=float)
    values = df["flow_rate"].to_numpy(dtype=float)
    quality = _quality_array(df)
    start = float(timestamps[0]) if len(timestamps) else 0.0
    generate_plot(
        "time_series",
        timestamps,
        values,
        quality,
        serial_number=serial_number,
        start=start,
        verified_facts=verified_facts,
    )
    if np.isfinite(quality).any():
        generate_plot(
            "signal_quality",
            timestamps,
            values,
            quality,
            serial_number=serial_number,
            start=start,
            verified_facts=verified_facts,
        )


def _append_baseline(lines: list[str], facts: Dict[str, Any]) -> None:
    bq = facts.get("baseline_quality") if isinstance(facts.get("baseline_quality"), dict) else None
    if not bq or bq.get("state") == "not_requested":
        return
    lines.append("\n## Baseline comparison\n")
    lines.append(f"- State: `{bq.get('state')}`; reliable={bool(bq.get('reliable'))}\n")
    if bq.get("reliable") and isinstance(facts.get("today_vs_baseline"), dict):
        tvb = facts["today_vs_baseline"]
        lines.append(f"- Verdict: `{tvb.get('verdict')}`\n")
        lines.append(f"- Robust z-score: {_fmt(tvb.get('robust_z_score'), 4)}\n")
    else:
        for reason in (bq.get("reasons_refused") or [])[:4]:
            lines.append(f"- Refusal: {reason}\n")
        for rec in (bq.get("recommendations") or [])[:3]:
            lines.append(f"- Recommendation: {rec}\n")


def _append_requested_events(lines: list[str], facts: Dict[str, Any]) -> None:
    te = facts.get("threshold_events") if isinstance(facts.get("threshold_events"), dict) else None
    if not te or te.get("state") == "not_requested":
        return
    lines.append("\n## Threshold events\n")
    lines.append(f"- State: `{te.get('state')}`; valid={te.get('valid_count', 0)}, invalid={te.get('invalid_count', 0)}\n")
    for event_set in (te.get("event_sets") or [])[:8]:
        if not isinstance(event_set, dict):
            continue
        if event_set.get("state") == "ready":
            lines.append(
                f"- {event_set.get('name')}: {event_set.get('event_count', 0)} event(s) "
                f"for `{event_set.get('predicate')}`\n"
            )
        else:
            lines.append(f"- {event_set.get('name')}: `{event_set.get('state')}`\n")
            for reason in (event_set.get("reasons_refused") or [])[:2]:
                lines.append(f"  - {reason}\n")


def _append_diagnostics(lines: list[str], facts: Dict[str, Any]) -> None:
    attr = facts.get("anomaly_attribution")
    if isinstance(attr, dict) and attr:
        lines.append("\n## Diagnostic interpretation\n")
        lines.append(f"- Primary type: `{attr.get('primary_type')}`\n")
        lines.append(f"- Severity: `{attr.get('severity')}`; confidence: `{attr.get('confidence')}`\n")
        if attr.get("summary"):
            lines.append(f"- Summary: {attr['summary']}\n")
        checks = attr.get("next_checks") if isinstance(attr.get("next_checks"), list) else []
        if checks:
            lines.append(f"- Next checks: {'; '.join(str(c) for c in checks[:4])}\n")


def analyze_template(
    df: pd.DataFrame,
    serial_number: str,
    verified_facts: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a Markdown analysis body without calling an LLM."""
    if verified_facts is None:
        verified_facts = build_verified_facts(df)

    _make_standard_plots(df, serial_number, verified_facts)

    lines: list[str] = [
        "# Flow analysis summary\n\n",
        "## Snapshot\n",
        f"- Meter: `{serial_number}`\n",
        f"- Rows analyzed: {verified_facts.get('n_rows', len(df))}\n",
    ]

    desc = verified_facts.get("flow_rate_descriptive")
    if isinstance(desc, dict) and "median" in desc:
        lines.extend(
            [
                f"- Flow rate min / median / max: {_fmt(desc.get('min'))} / "
                f"{_fmt(desc.get('median'))} / {_fmt(desc.get('max'))} gal/min\n",
                f"- Mean flow rate: {_fmt(desc.get('mean'))} gal/min\n",
            ]
        )
    volume = verified_facts.get("flow_volume")
    if isinstance(volume, dict):
        lines.append(f"- Integrated volume: {_fmt(volume.get('total_volume_gallons'))} gallons\n")

    lines.append("\n## Continuity and quality\n")
    lines.append(f"- Gap events: {verified_facts.get('gap_event_count', 0)}\n")
    lines.append(f"- Largest gap: {_fmt(verified_facts.get('largest_gap_duration_seconds'), 4)} seconds\n")
    lines.append(f"- Zero-flow periods: {verified_facts.get('zero_flow_period_count', 0)}\n")
    sq = verified_facts.get("signal_quality")
    if isinstance(sq, dict) and sq.get("total_count") is not None:
        lines.append(
            f"- Quality flags: {sq.get('flagged_count', 0)} of {sq.get('total_count', 0)} "
            f"({sq.get('flagged_percent', 0)}%) at or below {sq.get('threshold', 60)}\n"
        )

    flat = verified_facts.get("flatline")
    if isinstance(flat, dict) and flat.get("note"):
        lines.append(f"- Flow variability: {flat['note']}\n")

    _append_baseline(lines, verified_facts)
    _append_requested_events(lines, verified_facts)
    _append_diagnostics(lines, verified_facts)

    return "".join(lines).rstrip() + "\n"
