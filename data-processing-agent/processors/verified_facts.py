"""
Deterministic summary of key metrics for the same series the LLM analyzes.

Used to anchor the model and to append a non-LLM "verified" block to the report.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

import numpy as np
import pandas as pd

from processors.baseline_quality import not_requested_stub as _baseline_not_requested
from processors.continuity import detect_gaps, detect_zero_flow_periods
from processors.mask_by_local_time import not_requested_stub as _filter_not_requested
from processors.sampling_physics import (
    describe_sampling_caps,
    max_healthy_inter_arrival_seconds,
)
from processors.coverage_buckets import compute_coverage_buckets, slim_coverage_for_prompt
from processors.descriptive import compute_descriptive_stats
from processors.flatline import summarize_flatline
from processors.quality import detect_low_quality_readings
from processors.quiet_baseline import summarize_quiet_flow_baseline


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


def build_verified_facts(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Run the same core processors as the tool loop on the full dataframe.
    JSON-serialisable; safe to embed in prompts and reports.
    """
    n = len(df)
    out: Dict[str, Any] = {"n_rows": n}
    if n == 0:
        out["error"] = "empty_dataframe"
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
    low_ratio = 0.22 if out["sampling_irregular"] else 0.30
    out["coverage_6h"] = compute_coverage_buckets(
        ts, interval_coverage, low_ratio_threshold=low_ratio
    )

    # Baseline-comparison scaffolding: the stub is always present so the output
    # schema is stable. When the baseline pipeline is wired, replace this with
    # ``evaluate_baseline_quality(reference_rollups=..., today_partial=...)``.
    out["baseline_quality"] = _baseline_not_requested()

    # Local-time filter scaffolding (future business-hours / weekend slicing).
    # Pipeline is not wired yet; stub keeps the output schema stable so
    # downstream consumers (report, orchestrator) can rely on the key.
    out["filter_applied"] = _filter_not_requested()

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

    # Drop the baseline-quality stub from the prompt until the feature is wired.
    # The full verdict is still in the analysis bundle for audit.
    bq = slim.get("baseline_quality")
    if isinstance(bq, dict) and bq.get("state") == "not_requested":
        slim.pop("baseline_quality", None)

    # Same treatment for the filter-applied stub: drop when no filter was used,
    # surface verbatim (including refusal states) when the feature is wired.
    fa = slim.get("filter_applied")
    if isinstance(fa, dict) and fa.get("state") == "not_requested":
        slim.pop("filter_applied", None)

    return slim
