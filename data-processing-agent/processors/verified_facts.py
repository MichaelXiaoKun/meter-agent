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
from processors.event_detector import detect_threshold_events
from processors.flatline import summarize_flatline
from processors.frequency_domain import compute_dominant_frequencies
from processors.flow_metrics import compute_total_volume, detect_peaks
from processors.change_point import compute_cusum_facts
from processors.anomaly_attribution import (
    build_anomaly_attribution,
    slim_anomaly_attribution_for_prompt,
)
from processors.quality import detect_low_quality_readings
from processors.quiet_baseline import summarize_quiet_flow_baseline
from processors.reasoning_schema import build_reasoning_schema
from processors.seasonality import (
    build_diurnal_profile,
    not_requested_stub as _seasonality_not_requested,
    score_against_diurnal,
)


def _threshold_events_not_requested() -> Dict[str, Any]:
    return {
        "state": "not_requested",
        "reliable": False,
        "event_sets": [],
        "requested_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "reasons_refused": [],
    }


def _threshold_events_not_evaluated(reason: str) -> Dict[str, Any]:
    return {
        "state": "not_evaluated",
        "reliable": False,
        "event_sets": [],
        "requested_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "reasons_refused": [reason],
    }


def _frequency_domain_not_evaluated(reason: str) -> Dict[str, Any]:
    return {
        "state": "insufficient_cadence",
        "reliable": False,
        "dominant_frequencies": [],
        "window_seconds": 0.0,
        "network_type_hint": (os.environ.get("BLUEBOT_METER_NETWORK_TYPE") or None),
        "reasons_refused": [reason],
    }


def _build_threshold_events(
    df: pd.DataFrame,
    event_predicates: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not event_predicates:
        return _threshold_events_not_requested()
    if not isinstance(event_predicates, list):
        return {
            "state": "invalid_predicate",
            "reliable": False,
            "event_sets": [],
            "requested_count": 0,
            "valid_count": 0,
            "invalid_count": 1,
            "reasons_refused": ["event_predicates must be a list"],
        }

    event_sets: list[dict] = []
    reasons: list[str] = []
    valid = 0
    invalid = 0
    for idx, spec in enumerate(event_predicates):
        default_name = f"event_{idx + 1}"
        if not isinstance(spec, dict):
            invalid += 1
            reason = f"{default_name}: event predicate spec must be an object"
            reasons.append(reason)
            event_sets.append(
                {
                    "name": default_name,
                    "state": "invalid_predicate",
                    "predicate": None,
                    "min_duration_seconds": None,
                    "event_count": 0,
                    "events": [],
                    "reasons_refused": [reason],
                }
            )
            continue
        name = str(spec.get("name") or default_name).strip() or default_name
        predicate = spec.get("predicate")
        min_duration = spec.get("min_duration_seconds", 0)
        try:
            events = detect_threshold_events(
                df,
                predicate=predicate,
                min_duration_seconds=min_duration,
            )
        except ValueError as exc:
            invalid += 1
            reason = f"{name}: {exc}"
            reasons.append(reason)
            event_sets.append(
                {
                    "name": name,
                    "state": "invalid_predicate",
                    "predicate": predicate,
                    "min_duration_seconds": min_duration,
                    "event_count": 0,
                    "events": [],
                    "reasons_refused": [reason],
                }
            )
            continue
        valid += 1
        event_sets.append(
            {
                "name": name,
                "state": "ready",
                "predicate": predicate,
                "min_duration_seconds": int(min_duration),
                "event_count": len(events),
                "events": events,
                "reasons_refused": [],
            }
        )

    state = "ready" if valid else "invalid_predicate"
    return {
        "state": state,
        "reliable": bool(valid),
        "event_sets": event_sets,
        "requested_count": len(event_predicates),
        "valid_count": valid,
        "invalid_count": invalid,
        "reasons_refused": reasons,
    }


def _build_frequency_domain(ts: np.ndarray, values: np.ndarray) -> Dict[str, Any]:
    network_hint = (os.environ.get("BLUEBOT_METER_NETWORK_TYPE") or "").strip().lower()
    window_seconds = float(np.nanmax(ts) - np.nanmin(ts)) if len(ts) else 0.0
    reasons: list[str] = []
    if network_hint != "wifi":
        reasons.append("frequency-domain analysis requires Wi-Fi cadence data")
    if window_seconds < 3600.0:
        reasons.append("frequency-domain analysis requires at least 1 hour of data")
    if reasons:
        return {
            "state": "insufficient_cadence",
            "reliable": False,
            "dominant_frequencies": [],
            "window_seconds": window_seconds,
            "network_type_hint": network_hint or None,
            "reasons_refused": reasons,
        }
    dominant = compute_dominant_frequencies(ts, values, top_k=3)
    if not dominant:
        return {
            "state": "insufficient_cadence",
            "reliable": False,
            "dominant_frequencies": [],
            "window_seconds": window_seconds,
            "network_type_hint": network_hint or None,
            "reasons_refused": [
                "frequency-domain analysis could not find a stable non-zero spectral component"
            ],
        }
    return {
        "state": "ready",
        "reliable": True,
        "dominant_frequencies": dominant,
        "window_seconds": window_seconds,
        "network_type_hint": network_hint or None,
        "reasons_refused": [],
    }


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
    event_predicates: Optional[List[Dict[str, Any]]] = None,
    reference_df: Optional[pd.DataFrame] = None,
    seasonality_tz: Optional[str] = None,
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

    When ``reference_df`` is supplied, a meter-local diurnal profile is built
    from the same baseline window and the current ``df`` is scored against it.
    This is an automatic byproduct of baseline-window fetching; no separate
    tool input is needed.
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
                "diurnal_seasonality": _seasonality_not_requested(),
                "threshold_events": (
                    _threshold_events_not_evaluated(
                        "event detection was not evaluated because the requested local-time filter was not applied."
                    )
                    if event_predicates
                    else _threshold_events_not_requested()
                ),
                "frequency_domain": _frequency_domain_not_evaluated(
                    "frequency-domain analysis was not evaluated because the requested local-time filter was not applied."
                ),
            }
        df = filtered_df

    n = len(df)
    out: Dict[str, Any] = {"n_rows": n}
    if n == 0:
        out["error"] = "empty_dataframe"
        out["baseline_quality"] = _baseline_not_requested()
        out["filter_applied"] = filter_applied
        out["diurnal_seasonality"] = _seasonality_not_requested()
        out["threshold_events"] = (
            _threshold_events_not_evaluated(
                "event detection was not evaluated because the analysis dataframe is empty."
            )
            if event_predicates
            else _threshold_events_not_requested()
        )
        out["frequency_domain"] = _frequency_domain_not_evaluated(
            "frequency-domain analysis was not evaluated because the analysis dataframe is empty."
        )
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
    out["flow_volume"] = compute_total_volume(ts, values)
    out["peak_count"] = len(detect_peaks(ts, values))
    out["threshold_events"] = _build_threshold_events(df, event_predicates)
    out["frequency_domain"] = _build_frequency_domain(ts, values)

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

    if reference_df is None:
        out["diurnal_seasonality"] = _seasonality_not_requested()
    else:
        profile = build_diurnal_profile(
            reference_df,
            tz=seasonality_tz or "UTC",
            n_days=28,
        )
        score = score_against_diurnal(df, profile)
        out["diurnal_seasonality"] = {
            "state": score.get("state"),
            "reliable": bool(score.get("reliable")),
            "profile": profile,
            "score": score,
        }

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

    ds = slim.get("diurnal_seasonality")
    if isinstance(ds, dict) and ds.get("state") == "not_requested":
        slim.pop("diurnal_seasonality", None)

    te = slim.get("threshold_events")
    if isinstance(te, dict) and te.get("state") == "not_requested":
        slim.pop("threshold_events", None)
    elif isinstance(te, dict):
        for event_set in te.get("event_sets") or []:
            if not isinstance(event_set, dict):
                continue
            events = event_set.get("events")
            if isinstance(events, list) and len(events) > 10:
                event_set["events"] = events[:10]
                event_set["events_omitted"] = len(events) - 10

    fd = slim.get("frequency_domain")
    if isinstance(fd, dict) and fd.get("state") != "ready":
        slim.pop("frequency_domain", None)

    attribution = slim.get("anomaly_attribution")
    if isinstance(attribution, dict):
        slim["anomaly_attribution"] = slim_anomaly_attribution_for_prompt(attribution)

    return slim
