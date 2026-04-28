"""
Report Formatter

Wraps the agent's analysis in a standardised report header.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from processors.reasoning_schema import schema_to_compact_markdown


def _attribution_markdown(facts: Dict[str, Any]) -> str:
    attr = facts.get("anomaly_attribution")
    if not isinstance(attr, dict) or not attr:
        return ""
    lines = [
        "\n## Diagnostic interpretation (code-generated)\n\n",
        "This is the deterministic attribution layer combining drift, adequacy, gaps, quality, flatline, and baseline signals.\n\n",
    ]
    primary = attr.get("primary_type") or "unknown"
    severity = attr.get("severity") or "unknown"
    confidence = attr.get("confidence") or "unknown"
    summary = attr.get("summary") or ""
    cause = attr.get("primary_cause") or ""
    lines.append(f"- **Primary interpretation:** `{primary}` ({severity} severity, {confidence} confidence)\n")
    if summary:
        lines.append(f"- **Summary:** {summary}\n")
    if cause:
        lines.append(f"- **Primary cause:** {cause}\n")
    evidence = attr.get("evidence") if isinstance(attr.get("evidence"), list) else []
    if evidence:
        ev_bits = []
        for ev in evidence[:3]:
            if isinstance(ev, dict):
                ev_bits.append(str(ev.get("message") or ev.get("code") or "evidence"))
        if ev_bits:
            lines.append(f"- **Evidence:** {'; '.join(ev_bits)}\n")
    checks = attr.get("next_checks") if isinstance(attr.get("next_checks"), list) else []
    if checks:
        lines.append(f"- **Next checks:** {'; '.join(str(c) for c in checks[:4])}\n")
    return "".join(lines)


def _append_filter_applied_markdown(lines: list[str], fa: Dict[str, Any]) -> None:
    state = fa.get("state")
    # Silent for "not_requested" — no filter was asked for.
    if not state or state == "not_requested":
        return
    applied = bool(fa.get("applied"))
    kept = fa.get("n_rows_kept") or 0
    total = fa.get("n_rows_input") or 0
    frac = fa.get("fraction_kept")
    frac_s = f" ({frac:.0%})" if isinstance(frac, (int, float)) else ""
    lines.append(
        f"- **Local-time filter:** `{state}` "
        f"(applied={applied}; kept {kept} of {total} rows{frac_s})\n"
    )
    pred = fa.get("predicate_used") or {}
    if pred:
        tz_s = pred.get("timezone") or ""
        wd_s = pred.get("weekdays")
        hr_s = pred.get("hour_ranges")
        if tz_s:
            lines.append(f"  - timezone: {tz_s}\n")
        if wd_s:
            lines.append(f"  - weekdays: {wd_s}\n")
        if hr_s:
            pretty = ", ".join(
                f"{r['start_hour']}–{r['end_hour']}" for r in hr_s
            )
            lines.append(f"  - hour ranges: {pretty}\n")
    for reason in (fa.get("reasons_refused") or [])[:3]:
        lines.append(f"  - refusal: {reason}\n")
    for verr in (fa.get("validation_errors") or [])[:3]:
        lines.append(f"  - validation: {verr}\n")


def _verified_facts_markdown(facts: Dict[str, Any]) -> str:
    """Append-only block of deterministic metrics (same processors as the agent tools)."""
    lines = [
        "\n---\n\n",
        "## Verified metrics (code-generated)\n\n",
        "These values are computed with the same processors as the analysis tools. "
        "The narrative above should interpret them; if any number conflicts, trust this section.\n\n",
    ]
    desc = facts.get("flow_rate_descriptive")
    if isinstance(desc, dict) and "median" in desc:
        lines.append(
            f"- **Flow rate (gal/min) — min / median / max:** "
            f"{desc['min']:.6g} / {desc['median']:.6g} / {desc['max']:.6g}\n"
        )
        lines.append(f"- **Mean flow rate (gal/min):** {desc['mean']:.6g}\n")
    elif isinstance(desc, dict) and "error" in desc:
        lines.append(f"- **Flow rate descriptive:** {desc['error']}\n")

    fa = facts.get("filter_applied")
    filter_refused = (
        isinstance(fa, dict)
        and fa.get("state") in {"invalid_spec", "empty_mask"}
        and "flow_rate_descriptive" not in facts
    )
    if filter_refused:
        _append_filter_applied_markdown(lines, fa)
        return "".join(lines)

    lines.append(f"- **Gap events:** {facts.get('gap_event_count', 0)}\n")
    lg = float(facts.get("largest_gap_duration_seconds") or 0)
    lines.append(f"- **Largest gap:** {lg:.4g} s ({lg / 3600:.4g} h)\n")

    lines.append(
        f"- **Zero-flow periods (≥60 s at or below 0 gpm):** "
        f"{facts.get('zero_flow_period_count', 0)}\n"
    )

    sq = facts.get("signal_quality")
    if isinstance(sq, dict) and sq.get("total_count") is not None:
        th = sq.get("threshold", 60)
        lines.append(
            f"- **Quality ≤ {th}:** {sq.get('flagged_count', 0)} of {sq.get('total_count', 0)} "
            f"({sq.get('flagged_percent', 0)}%); merged low-quality interval count: "
            f"{sq.get('interval_count', 0)}\n"
        )

    qb = facts.get("quiet_flow_baseline")
    if isinstance(qb, dict):
        if "quiet_flow_median" in qb:
            lines.append(
                f"- **Quiet-flow median (gal/min):** {qb['quiet_flow_median']:.6g}\n"
            )
        elif "error" in qb:
            lines.append(f"- **Quiet-flow baseline:** {qb['error']}\n")

    si = facts.get("sampling_median_interval_seconds")
    p75i = facts.get("sampling_p75_interval_seconds")
    cap = facts.get("max_healthy_inter_arrival_seconds")
    caps_meta = facts.get("sampling_caps") if isinstance(facts.get("sampling_caps"), dict) else None
    net_hint = caps_meta.get("network_type_hint") if caps_meta else None
    if si is not None:
        if p75i is not None:
            if cap is not None:
                net_note = (
                    f" (network_type={net_hint})"
                    if isinstance(net_hint, str) and net_hint
                    else ""
                )
                lines.append(
                    f"- **Inter-arrival spacing:** median {float(si):.6g} s, P75 {float(p75i):.6g} s; "
                    f"coverage nominal is min(max(median, P75), {float(cap):.0f} s){net_note} — "
                    f"healthy links stay within that cap.\n"
                )
            else:
                lines.append(
                    f"- **Inter-arrival spacing:** median {float(si):.6g} s, P75 {float(p75i):.6g} s "
                    f"(coverage uses max(median, P75))\n"
                )
        else:
            lines.append(f"- **Median sampling interval:** {float(si):.6g} s\n")

    flat = facts.get("flatline")
    if isinstance(flat, dict):
        flg = flat.get("flag")
        if flg == "no_valid_data":
            lines.append("- **Flow variability:** No valid flow_rate samples.\n")
        elif flg:
            lines.append(
                f"- **Flow variability:** `{flg}` — {flat.get('note') or '(see JSON)'}\n"
            )
        elif flat.get("note"):
            lines.append(f"- **Flow variability:** {flat['note']}\n")

    cusum = facts.get("cusum_drift")
    if isinstance(cusum, dict):
        adequacy = cusum.get("adequacy") if isinstance(cusum.get("adequacy"), dict) else {}
        if cusum.get("skipped"):
            actual = adequacy.get("actual_points")
            target = adequacy.get("target_min")
            gap = adequacy.get("gap_pct")
            reason = adequacy.get("reason") or "insufficient_data"
            bits = []
            if actual is not None and target is not None:
                bits.append(f"{actual}/{target} points")
            if gap is not None:
                bits.append(f"{float(gap):.3g}% gap coverage")
            suffix = f" ({'; '.join(bits)})" if bits else ""
            lines.append(f"- **CUSUM drift:** skipped — `{reason}`{suffix}\n")
        else:
            drift = cusum.get("drift_detected") or "none"
            pos = int(cusum.get("positive_alarm_count") or 0)
            neg = int(cusum.get("negative_alarm_count") or 0)
            first_alarm = cusum.get("first_alarm_timestamp")
            first_s = f"; first alarm unix {first_alarm}" if first_alarm is not None else ""
            lines.append(
                f"- **CUSUM drift:** `{drift}` "
                f"(upward alarms={pos}, downward alarms={neg}{first_s})\n"
            )

    bq = facts.get("baseline_quality")
    if isinstance(bq, dict):
        state = bq.get("state")
        # Intentionally silent for "not_requested" — no baseline was asked for.
        if state and state != "not_requested":
            reliable = bool(bq.get("reliable"))
            used = bq.get("n_days_used")
            rejected = bq.get("n_days_rejected")
            line = (
                f"- **Baseline comparison:** `{state}` "
                f"(reliable={reliable}; used {used or 0} day(s), rejected {rejected or 0})\n"
            )
            lines.append(line)
            for reason in (bq.get("reasons_refused") or [])[:4]:
                lines.append(f"  - refusal: {reason}\n")
            for rec in (bq.get("recommendations") or [])[:3]:
                lines.append(f"  - hint: {rec}\n")

    if isinstance(fa, dict):
        _append_filter_applied_markdown(lines, fa)

    cov = facts.get("coverage_6h")
    if isinstance(cov, dict) and cov.get("n_buckets"):
        thr = cov.get("low_ratio_threshold")
        thr_s = f", sparse if below {thr:.0%} of expected" if isinstance(thr, (int, float)) else ""
        lines.append(
            f"- **6 h coverage buckets:** {cov['n_buckets']} windows; "
            f"{cov.get('buckets_with_issues', 0)} with missing or sparse data{thr_s}\n"
        )
        problems = [b for b in (cov.get("buckets") or []) if b.get("status") != "ok"]
        for b in problems[:8]:
            lines.append(
                f"  - {b['start_ts']} → {b['end_ts']} UTC (unix): "
                f"{b['n_points']} pts (≈{b.get('expected_points_approx')} expected), **{b['status']}**\n"
            )
        if len(problems) > 8:
            lines.append(f"  - …and {len(problems) - 8} more sparse/missing windows\n")

    lines.append("")
    return "".join(lines)


def format_report(
    analysis: str,
    serial_number: str,
    start: int,
    end: int,
    verified_facts: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Attach a header block to the agent's analysis text.

    Args:
        analysis:       Markdown analysis string returned by agent.analyze()
        serial_number:  Meter serial number
        start:          Range start as Unix timestamp (seconds)
        end:            Range end as Unix timestamp (seconds)
        verified_facts: Optional dict from ``processors.verified_facts.build_verified_facts``;
                        when set, a deterministic metrics section is appended.

    Returns:
        Full report string ready for console output or file write.
    """
    fmt = "%Y-%m-%d %H:%M:%S UTC"
    start_str = datetime.fromtimestamp(start, tz=timezone.utc).strftime(fmt)
    end_str = datetime.fromtimestamp(end, tz=timezone.utc).strftime(fmt)
    generated_str = datetime.now(tz=timezone.utc).strftime(fmt)

    header = (
        "=" * 80 + "\n"
        "FLOW RATE ANALYSIS REPORT\n"
        f"Serial:     {serial_number}\n"
        f"Period:     {start_str}  →  {end_str}\n"
        f"Generated:  {generated_str}\n"
        + "=" * 80 + "\n\n"
    )

    body = header + analysis
    if verified_facts:
        body += _verified_facts_markdown(verified_facts)
        body += _attribution_markdown(verified_facts)
        schema = verified_facts.get("reasoning_schema")
        if isinstance(schema, dict) and schema:
            body += schema_to_compact_markdown(schema)
    return body
