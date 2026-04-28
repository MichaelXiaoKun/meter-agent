"""
Deterministic summary of key metrics for the same series the LLM analyzes.

Used to anchor the model and to append a non-LLM "verified" block to the report.
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from processors.baseline_compare import compute_today_vs_baseline
from processors.baseline_quality import (
    BaselineQualityConfig,
    evaluate_baseline_quality,
    not_requested_stub as _baseline_not_requested,
)
from processors.continuity import detect_gaps, detect_zero_flow_periods
from processors.mask_by_local_time import (
    apply_filter,
    not_requested_stub as _filter_not_requested,
)
from processors.sampling_physics import (
    describe_sampling_caps,
    max_healthy_inter_arrival_seconds,
)
from processors.coverage_buckets import compute_coverage_buckets, slim_coverage_for_prompt
from processors.descriptive import compute_descriptive_stats
from processors.flatline import summarize_flatline
from processors.change_point import compute_cusum_facts
from processors.anomaly_attribution import (
    build_anomaly_attribution,
    slim_anomaly_attribution_for_prompt,
)
from processors.quality import detect_low_quality_readings
from processors.quiet_baseline import summarize_quiet_flow_baseline
from processors.reasoning_schema import build_reasoning_schema


def _positive_delta_stats(timestamps: np.ndarray) -> tuple[float, float]:
    """Median and P75 of positive inter-arrival gaps (duplicate timestamps ignored)."""
    if len(timestamps) < 2:
        return 1.0, 1.0
    deltas = np.diff(np.sort(timestamps.astype(float)))
    positive = deltas[deltas > 1e-9]
    if len(positive) == 0:
        return 1e-9, 1e-9
    med = max(float(np.median(positive)), 1e-9)
    p75 = max(float(np.percentile(positive, 75)), med)
    return med, p75


def build_verified_facts(
    df: pd.DataFrame,
    *,
    filters: Optional[Dict[str, Any]] = None,
    reference_rollups: Optional[List[Dict[str, Any]]] = None,
    today_partial: Optional[Dict[str, Any]] = None,
    target_weekday: Optional[int] = None,
    fraction_of_day_elapsed: Optional[float] = None,
    today_missing_bucket_ratio: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run the same core processors as the tool loop on the full dataframe.
    JSON-serialisable; safe to embed in prompts and reports.

    Optional baseline arguments
    ---------------------------
    When ``filters`` is supplied, :func:`processors.mask_by_local_time.apply_filter`
    runs before any other processor. Applied filters replace ``df`` for all
    downstream metrics and set ``out["filter_applied"]`` to the provenance
    block. Invalid specs or valid specs that match zero rows short-circuit:
    the function returns only ``n_rows``, ``baseline_quality`` (not requested),
    and the filter refusal block so callers cannot accidentally narrate the
    unfiltered series as if it had been scoped.

    When ``reference_rollups`` is supplied (already filtered to the relevant
    window by the caller — see :mod:`processors.daily_rollup`), the real
    :func:`processors.baseline_quality.evaluate_baseline_quality` runs and
    populates ``out["baseline_quality"]``. When the verdict is ``reliable`` we
    additionally call :func:`processors.baseline_compare.compute_today_vs_baseline`
    to populate ``out["today_vs_baseline"]``. When the baseline is *not*
    reliable we leave ``today_vs_baseline`` absent so the system prompt rule
    "if not reliable, relay refusal verbatim" is structurally enforced.

    Leaving ``reference_rollups`` as ``None`` preserves the legacy stub
    behaviour (``baseline_quality.state == "not_requested"``) so callers that
    do not opt into the comparison loop are unaffected.
    """
    filter_applied: Dict[str, Any] = _filter_not_requested()
    if filters is not None:
        filtered_df, filter_result = apply_filter(df, filters)
        filter_applied = filter_result.to_dict()
        if not filter_result.applied:
            return {
                "n_rows": int(len(df)),
                "baseline_quality": _baseline_not_requested(),
                "filter_applied": filter_applied,
            }
        df = filtered_df

    n = len(df)
    out: Dict[str, Any] = {"n_rows": n}
    if n == 0:
        out["error"] = "empty_dataframe"
        out["baseline_quality"] = _baseline_not_requested()
        out["filter_applied"] = filter_applied
        return out

    ts = df["timestamp"].values.astype(float)
    values = df["flow_rate"].values.astype(float)
    quality = (
        df["quality"].values.astype(float)
        if "quality" in df.columns
        else np.full(n, np.nan)
    )

    try:
        out["flow_rate_descriptive"] = compute_descriptive_stats(values)
    except ValueError as exc:
        out["flow_rate_descriptive"] = {"error": str(exc)}

    interval_med, p75_delta = _positive_delta_stats(ts)
    out["sampling_median_interval_seconds"] = interval_med
    out["sampling_p75_interval_seconds"] = p75_delta
    out["sampling_irregular"] = (
        (p75_delta / interval_med) > 1.35 if interval_med > 1e-8 else False
    )
    cap = max_healthy_inter_arrival_seconds()
    out["max_healthy_inter_arrival_seconds"] = cap
    out["sampling_caps"] = describe_sampling_caps()
    # Conservative nominal for coverage: max(median, P75), but not slower than ~1 min when healthy.
    interval_coverage = min(max(interval_med, p75_delta), cap)

    gaps = detect_gaps(ts, None)
    out["gap_event_count"] = len(gaps)
    out["largest_gap_duration_seconds"] = (
        max((g["duration_seconds"] for g in gaps), default=0.0)
    )

    zf = detect_zero_flow_periods(ts, values, 60.0)
    out["zero_flow_period_count"] = len(zf)

    out["signal_quality"] = detect_low_quality_readings(ts, values, quality, 60.0)
    out["quiet_flow_baseline"] = summarize_quiet_flow_baseline(ts, values, quality)

    out["flatline"] = summarize_flatline(values)
    out["cusum_drift"] = compute_cusum_facts(ts, values)
    low_ratio = 0.22 if out["sampling_irregular"] else 0.30
    out["coverage_6h"] = compute_coverage_buckets(
        ts, interval_coverage, low_ratio_threshold=low_ratio
    )

    # Baseline-comparison: when the orchestrator passes a baseline window the
    # caller hands us already-built ``reference_rollups`` and (when applicable)
    # a ``today_partial`` rollup. We run the real refusal evaluator so the
    # state field carries one of:
    #   not_requested | no_history | insufficient_clean_days |
    #   regime_change_too_recent | partial_today_unsuitable | reliable.
    # If reference_rollups is None we keep the stub for schema stability —
    # downstream consumers slim it out of the prompt automatically.
    if reference_rollups is None:
        out["baseline_quality"] = _baseline_not_requested()
    else:
        verdict = evaluate_baseline_quality(
            reference_rollups=reference_rollups,
            today_partial=today_partial,
            target_weekday=target_weekday,
            fraction_of_day_elapsed=fraction_of_day_elapsed,
            today_missing_bucket_ratio=today_missing_bucket_ratio,
            config=BaselineQualityConfig.from_env(),
        )
        out["baseline_quality"] = verdict.to_dict()
        # Today-vs-baseline metrics are only meaningful when the verdict is
        # reliable. Skipping them on refusal is intentional: it makes the
        # "if not reliable, relay state/reasons_refused verbatim" rule a
        # structural property of the bundle rather than a prompt-only guard.
        if verdict.reliable:
            comparison = compute_today_vs_baseline(
                reference_rollups=reference_rollups,
                today_partial=today_partial,
                target_weekday=target_weekday,
                fraction_of_day_elapsed=fraction_of_day_elapsed,
            )
            if comparison is not None:
                out["today_vs_baseline"] = comparison

    out["filter_applied"] = filter_applied

    # Deterministic diagnostic interpretation: classify the dominant anomaly
    # type after every processor signal is available. Reasoning schema and the
    # LLM prompt treat this as the priority explanation anchor.
    out["anomaly_attribution"] = build_anomaly_attribution(out)

    # Compact evidence/hypothesis/next_checks anchor derived from the same
    # facts above. Bounded in size (≤ 6 evidence, ≤ 3 hypotheses, ≤ 3 checks)
    # so it can REPLACE narrative redundancy without inflating token budgets.
    # Built last so every dependent field is already populated.
    network_hint = os.environ.get("BLUEBOT_METER_NETWORK_TYPE") or None
    out["reasoning_schema"] = build_reasoning_schema(out, network_type=network_hint)

    return out


def slim_verified_facts_for_prompt(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Drop large arrays before embedding in the LLM prompt (same scalar metrics retained).
    """
    slim = deepcopy(facts)
    sq = slim.get("signal_quality")
    if isinstance(sq, dict) and "low_quality_intervals" in sq:
        n = len(sq["low_quality_intervals"])
        del sq["low_quality_intervals"]
        sq["low_quality_intervals_omitted"] = n

    cov = slim.get("coverage_6h")
    if isinstance(cov, dict) and cov.get("buckets"):
        slim["coverage_6h"] = slim_coverage_for_prompt(cov)

    # Drop the baseline-quality stub from the prompt when no baseline was
    # requested (state == "not_requested"). Every other state — refusals
    # (no_history / insufficient_clean_days / regime_change_too_recent /
    # partial_today_unsuitable) AND the success state (reliable) — must
    # survive into the prompt: the system prompt rule for the data-processing
    # agent requires the narrative to relay reasons_refused verbatim or, on
    # success, lead with the today-vs-baseline verdict.
    bq = slim.get("baseline_quality")
    if isinstance(bq, dict) and bq.get("state") == "not_requested":
        slim.pop("baseline_quality", None)
    elif isinstance(bq, dict):
        # Trim audit-only ballast that does not change the narrative — keep
        # state, reliable, reasons_refused, recommendations, n_days_used,
        # n_days_rejected, change_point_*, today_missing_bucket_ratio, and
        # n_same_weekday_days_used. Drop the verbose ``days_rejected`` array
        # and the echoed ``config_used`` block; both stay in the analysis
        # bundle for audit and add no value to the LLM's interpretation.
        bq.pop("days_rejected", None)
        bq.pop("config_used", None)

    # Same treatment for the filter-applied stub: drop when no filter was used,
    # surface verbatim (including refusal states) when the feature is wired.
    fa = slim.get("filter_applied")
    if isinstance(fa, dict) and fa.get("state") == "not_requested":
        slim.pop("filter_applied", None)

    attribution = slim.get("anomaly_attribution")
    if isinstance(attribution, dict):
        slim["anomaly_attribution"] = slim_anomaly_attribution_for_prompt(attribution)

    return slim
