"""
Deterministic long-range flow summaries.

Large windows should not require an internal LLM pass just to restate processor
facts. This module builds bounded daily / 6-hour rollups plus a compact Markdown
summary that the orchestrator can pass upstream cheaply.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd

from processors.continuity import detect_gaps, detect_zero_flow_periods

ANALYSIS_MODES = frozenset({"auto", "detailed", "summary"})
DEFAULT_LONG_RANGE_SECONDS = 14 * 86400
DEFAULT_LONG_RANGE_ROWS = 100_000
DEFAULT_MAX_ANOMALY_WINDOWS = 24


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def long_range_seconds_threshold() -> int:
    return max(3600, _env_int("BLUEBOT_LONG_RANGE_SECONDS", DEFAULT_LONG_RANGE_SECONDS))


def long_range_rows_threshold() -> int:
    return max(1, _env_int("BLUEBOT_LONG_RANGE_ROWS", DEFAULT_LONG_RANGE_ROWS))


def resolve_analysis_mode(
    requested_mode: str | None,
    *,
    start: int,
    end: int,
    row_count: int,
) -> Dict[str, Any]:
    """
    Resolve ``auto`` / ``detailed`` / ``summary`` to an executable mode.

    Explicit ``detailed`` and ``summary`` always win. ``auto`` flips to summary
    when the requested span or fetched row count crosses the configured limits.
    """
    requested = (requested_mode or "auto").strip().lower()
    if requested not in ANALYSIS_MODES:
        requested = "auto"

    span_seconds = max(0, int(end) - int(start))
    span_threshold = long_range_seconds_threshold()
    row_threshold = long_range_rows_threshold()
    reasons: list[str] = []

    if requested == "summary":
        mode = "summary"
        reasons.append("explicit_summary")
    elif requested == "detailed":
        mode = "detailed"
        reasons.append("explicit_detailed")
    elif span_seconds > span_threshold:
        mode = "summary"
        reasons.append("range_exceeds_threshold")
    elif int(row_count) > row_threshold:
        mode = "summary"
        reasons.append("row_count_exceeds_threshold")
    else:
        mode = "detailed"
        reasons.append("within_detailed_thresholds")

    return {
        "requested_mode": requested,
        "resolved_mode": mode,
        "span_seconds": span_seconds,
        "span_threshold_seconds": span_threshold,
        "row_count": int(row_count),
        "row_threshold": row_threshold,
        "reasons": reasons,
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _round(value: Any, digits: int = 6) -> float | None:
    f = _safe_float(value)
    if f is None:
        return None
    return round(f, digits)


def _positive_delta_stats(timestamps: np.ndarray) -> tuple[float, float]:
    if len(timestamps) < 2:
        return 1.0, 1.0
    deltas = np.diff(np.sort(timestamps.astype(float)))
    positive = deltas[deltas > 1e-9]
    if len(positive) == 0:
        return 1.0, 1.0
    med = max(float(np.median(positive)), 1e-9)
    p75 = max(float(np.percentile(positive, 75)), med)
    return med, p75


def _flow_stats(values: np.ndarray) -> dict[str, float | None]:
    clean = values[np.isfinite(values)]
    if len(clean) == 0:
        return {"min": None, "median": None, "max": None, "mean": None}
    return {
        "min": _round(np.min(clean)),
        "median": _round(np.median(clean)),
        "max": _round(np.max(clean)),
        "mean": _round(np.mean(clean)),
    }


def _anomaly_flags(
    *,
    coverage_status: str,
    quality_issue_share: float | None,
    zero_flow_count: int,
    largest_gap_seconds: float,
    flow_stats: dict[str, float | None],
    healthy_gap_cap_seconds: float,
) -> list[str]:
    flags: list[str] = []
    if coverage_status != "ok":
        flags.append("missing_or_sparse_coverage")
    if largest_gap_seconds > healthy_gap_cap_seconds:
        flags.append("long_gap")
    if quality_issue_share is not None and quality_issue_share >= 0.20:
        flags.append("low_signal_quality")
    if zero_flow_count > 0:
        flags.append("zero_flow_periods")
    median = flow_stats.get("median")
    max_v = flow_stats.get("max")
    if median is not None and max_v is not None:
        base = max(abs(float(median)), 1e-6)
        if float(max_v) / base >= 5.0 and float(max_v) > 0.5:
            flags.append("high_variability")
    return flags


def _bucket_rollups(
    df: pd.DataFrame,
    *,
    bucket_seconds: int,
    nominal_interval_seconds: float,
    low_ratio_threshold: float,
    healthy_gap_cap_seconds: float,
) -> List[Dict[str, Any]]:
    if df.empty:
        return []

    work = df[["timestamp", "flow_rate", "quality"]].copy()
    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp")
    if work.empty:
        return []

    start = int(work["timestamp"].iloc[0])
    end = int(work["timestamp"].iloc[-1])
    bucket_start = start - (start % bucket_seconds)
    buckets: list[dict[str, Any]] = []

    while bucket_start <= end:
        bucket_end = bucket_start + bucket_seconds
        is_last = bucket_end > end
        if is_last:
            mask = (work["timestamp"] >= bucket_start) & (work["timestamp"] <= end)
            effective_end = end
            width = max(1.0, float(effective_end - bucket_start))
        else:
            mask = (work["timestamp"] >= bucket_start) & (work["timestamp"] < bucket_end)
            effective_end = bucket_end
            width = float(bucket_seconds)

        part = work.loc[mask]
        count = int(len(part))
        expected = (
            max(1, int(round(width / nominal_interval_seconds)))
            if nominal_interval_seconds > 0
            else None
        )
        coverage_ratio = (count / expected) if expected else None
        if count == 0:
            coverage_status = "missing"
        elif coverage_ratio is not None and coverage_ratio < low_ratio_threshold:
            coverage_status = "low"
        else:
            coverage_status = "ok"

        ts = part["timestamp"].to_numpy(dtype=float) if count else np.array([], dtype=float)
        values = part["flow_rate"].to_numpy(dtype=float) if count else np.array([], dtype=float)
        quality = part["quality"].to_numpy(dtype=float) if count else np.array([], dtype=float)
        valid_quality = quality[np.isfinite(quality)]
        quality_issue_share = (
            float(np.sum(valid_quality <= 60.0) / len(valid_quality))
            if len(valid_quality)
            else None
        )
        gaps = detect_gaps(ts, None) if count else []
        largest_gap = max((float(g.get("duration_seconds") or 0.0) for g in gaps), default=0.0)
        zero_count = len(detect_zero_flow_periods(ts, values, 60.0)) if count else 0
        stats = _flow_stats(values)
        flags = _anomaly_flags(
            coverage_status=coverage_status,
            quality_issue_share=quality_issue_share,
            zero_flow_count=zero_count,
            largest_gap_seconds=largest_gap,
            flow_stats=stats,
            healthy_gap_cap_seconds=healthy_gap_cap_seconds,
        )

        buckets.append(
            {
                "start_ts": int(bucket_start),
                "end_ts": int(effective_end),
                "start_utc": datetime.fromtimestamp(bucket_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_utc": datetime.fromtimestamp(effective_end, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "n_points": count,
                "expected_points_approx": expected,
                "coverage_ratio": _round(coverage_ratio, 4),
                "coverage_status": coverage_status,
                "flow": stats,
                "quality_issue_share": _round(quality_issue_share, 4),
                "zero_flow_period_count": zero_count,
                "largest_gap_seconds": _round(largest_gap, 3) or 0.0,
                "anomaly_flags": flags,
            }
        )
        bucket_start = bucket_end

    return buckets


def _severity_score(rollup: dict[str, Any]) -> float:
    flags = rollup.get("anomaly_flags") or []
    score = float(len(flags))
    coverage = rollup.get("coverage_status")
    if coverage == "missing":
        score += 4.0
    elif coverage == "low":
        score += 2.0
    q = _safe_float(rollup.get("quality_issue_share")) or 0.0
    score += min(q * 5.0, 5.0)
    gap = _safe_float(rollup.get("largest_gap_seconds")) or 0.0
    score += min(gap / 3600.0, 6.0)
    zeros = int(rollup.get("zero_flow_period_count") or 0)
    score += min(float(zeros), 4.0)
    return round(score, 4)


def _bounded_anomaly_windows(rollups: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates = []
    for r in rollups:
        if r.get("anomaly_flags") or r.get("coverage_status") != "ok":
            item = dict(r)
            item["severity_score"] = _severity_score(r)
            candidates.append(item)
    candidates.sort(key=lambda r: (-float(r.get("severity_score") or 0.0), int(r.get("start_ts") or 0)))
    return candidates[:limit]


def build_long_range_summary(
    df: pd.DataFrame,
    verified_facts: Dict[str, Any],
    *,
    max_anomaly_windows: int | None = None,
) -> Dict[str, Any]:
    """Build bounded deterministic rollups and anomaly highlights."""
    if df.empty:
        return {
            "daily_rollups": [],
            "six_hour_rollups": [],
            "anomaly_windows": [],
            "rollup_highlights": {
                "n_rows": 0,
                "daily_window_count": 0,
                "six_hour_window_count": 0,
                "six_hour_problem_window_count": 0,
            },
        }

    ts = df["timestamp"].to_numpy(dtype=float)
    med, p75 = _positive_delta_stats(ts)
    cap = float(verified_facts.get("max_healthy_inter_arrival_seconds") or 60.0)
    nominal = min(max(med, p75), cap)
    low_ratio = 0.22 if verified_facts.get("sampling_irregular") else 0.30

    daily = _bucket_rollups(
        df,
        bucket_seconds=86400,
        nominal_interval_seconds=nominal,
        low_ratio_threshold=low_ratio,
        healthy_gap_cap_seconds=cap,
    )
    six_hour = _bucket_rollups(
        df,
        bucket_seconds=21600,
        nominal_interval_seconds=nominal,
        low_ratio_threshold=low_ratio,
        healthy_gap_cap_seconds=cap,
    )
    limit = max_anomaly_windows
    if limit is None:
        limit = max(1, _env_int("BLUEBOT_LONG_RANGE_MAX_ANOMALY_WINDOWS", DEFAULT_MAX_ANOMALY_WINDOWS))
    anomaly_windows = _bounded_anomaly_windows(six_hour, limit)

    problem_six = [r for r in six_hour if r.get("anomaly_flags") or r.get("coverage_status") != "ok"]
    problem_days = [r for r in daily if r.get("anomaly_flags") or r.get("coverage_status") != "ok"]
    highlights = {
        "n_rows": int(len(df)),
        "sampling_nominal_interval_seconds": _round(nominal, 3),
        "daily_window_count": len(daily),
        "daily_problem_window_count": len(problem_days),
        "six_hour_window_count": len(six_hour),
        "six_hour_problem_window_count": len(problem_six),
        "anomaly_window_count_returned": len(anomaly_windows),
        "anomaly_window_limit": int(limit),
    }
    if len(problem_six) > len(anomaly_windows):
        highlights["anomaly_windows_omitted"] = len(problem_six) - len(anomaly_windows)

    return {
        "daily_rollups": daily,
        "six_hour_rollups": six_hour,
        "anomaly_windows": anomaly_windows,
        "rollup_highlights": highlights,
    }


def _fmt_float(value: Any, suffix: str = "") -> str:
    f = _safe_float(value)
    if f is None:
        return "n/a"
    return f"{f:.6g}{suffix}"


def _reasoning_lines(schema: dict[str, Any] | None) -> list[str]:
    if not isinstance(schema, dict):
        return []
    lines: list[str] = []
    regime = schema.get("regime")
    if regime:
        lines.append(f"- Regime: `{regime}`")
    evidence = schema.get("evidence") if isinstance(schema.get("evidence"), list) else []
    codes = [str(e.get("code")) for e in evidence if isinstance(e, dict) and e.get("code")]
    if codes:
        lines.append(f"- Evidence codes: {', '.join(codes[:6])}")
    checks = schema.get("next_checks") if isinstance(schema.get("next_checks"), list) else []
    if checks:
        lines.append(f"- Suggested drill-down: {'; '.join(str(c) for c in checks[:3])}")
    return lines


def format_long_range_summary_markdown(
    *,
    serial_number: str,
    summary: Dict[str, Any],
    verified_facts: Dict[str, Any],
    mode_selection: Dict[str, Any],
    plot_paths: list[str] | None = None,
) -> str:
    """Human-readable deterministic summary for long ranges."""
    desc = verified_facts.get("flow_rate_descriptive") if isinstance(verified_facts, dict) else {}
    sq = verified_facts.get("signal_quality") if isinstance(verified_facts, dict) else {}
    highlights = summary.get("rollup_highlights") or {}
    anomaly_windows = summary.get("anomaly_windows") or []
    daily = summary.get("daily_rollups") or []

    lines = [
        "## Long-range summary mode\n\n",
        "This range was summarized deterministically to keep analysis fast and token-bounded. ",
        "The internal LLM report loop was skipped; the metrics below come from code processors.\n\n",
        "### Headline metrics\n\n",
        f"- Meter: `{serial_number}`\n",
        f"- Rows analyzed: {int(highlights.get('n_rows') or verified_facts.get('n_rows') or 0):,}\n",
        f"- Flow min / median / max: {_fmt_float((desc or {}).get('min'))} / "
        f"{_fmt_float((desc or {}).get('median'))} / {_fmt_float((desc or {}).get('max'))} gal/min\n",
        f"- Mean flow: {_fmt_float((desc or {}).get('mean'), ' gal/min')}\n",
        f"- Gap events: {verified_facts.get('gap_event_count', 0)}; largest gap "
        f"{_fmt_float(verified_facts.get('largest_gap_duration_seconds'), ' s')}\n",
        f"- Zero-flow periods: {verified_facts.get('zero_flow_period_count', 0)}\n",
    ]
    if isinstance(sq, dict) and sq.get("total_count") is not None:
        lines.append(
            f"- Quality <= {sq.get('threshold', 60)}: {sq.get('flagged_count', 0)} of "
            f"{sq.get('total_count', 0)} ({sq.get('flagged_percent', 0)}%)\n"
        )

    lines.extend(
        [
            "\n### Rollup coverage\n\n",
            f"- Daily windows: {highlights.get('daily_window_count', 0)}; "
            f"problem days: {highlights.get('daily_problem_window_count', 0)}\n",
            f"- 6-hour windows: {highlights.get('six_hour_window_count', 0)}; "
            f"problem windows: {highlights.get('six_hour_problem_window_count', 0)}\n",
        ]
    )
    omitted = highlights.get("anomaly_windows_omitted")
    if omitted:
        lines.append(f"- Additional anomalous 6-hour windows omitted from prompt payload: {omitted}\n")

    reasoning = _reasoning_lines(verified_facts.get("reasoning_schema"))
    if reasoning:
        lines.append("\n### Diagnostic anchor\n\n")
        lines.extend(line + "\n" for line in reasoning)

    if anomaly_windows:
        lines.append("\n### Highest-priority 6-hour windows\n\n")
        for w in anomaly_windows[:12]:
            flags = ", ".join(w.get("anomaly_flags") or [w.get("coverage_status") or "flagged"])
            lines.append(
                f"- {w.get('start_utc')} -> {w.get('end_utc')}: {flags}; "
                f"points {w.get('n_points')} / ~{w.get('expected_points_approx')}; "
                f"median {_fmt_float((w.get('flow') or {}).get('median'))} gpm\n"
            )
    elif daily:
        lines.append("\n### Daily rollup sample\n\n")
        for d in daily[:7]:
            lines.append(
                f"- {str(d.get('start_utc'))[:10]}: {d.get('n_points')} pts, "
                f"median {_fmt_float((d.get('flow') or {}).get('median'))} gpm, "
                f"coverage {d.get('coverage_status')}\n"
            )

    if plot_paths:
        lines.append("\n### Plots\n\n")
        for path in plot_paths:
            lines.append(f"![Analysis plot]({path})\n")

    lines.extend(
        [
            "\n### Next step\n\n",
            "Use this long-range pass to identify suspicious windows, then drill into a narrower day or 6-hour span for full detailed analysis.\n",
            f"\nMode selection: `{mode_selection.get('resolved_mode')}` from `{mode_selection.get('requested_mode')}` "
            f"({', '.join(mode_selection.get('reasons') or [])}).\n",
        ]
    )
    return "".join(lines)
