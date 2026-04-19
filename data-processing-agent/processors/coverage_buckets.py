"""
Time coverage: sample counts per fixed-duration bucket (default 6 h).
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

DEFAULT_BUCKET_SECONDS = 21600.0  # 6 hours


def compute_coverage_buckets(
    timestamps: np.ndarray,
    nominal_interval_seconds: float,
    *,
    bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
    low_ratio_threshold: float = 0.30,
) -> Dict[str, Any]:
    """
    Partition [first_ts, last_ts] into contiguous buckets of ``bucket_seconds`` (last may be shorter).

    ``expected_points_approx`` uses bucket width / ``nominal_interval_seconds``.
    Pass ``max(median_delta, p75_delta)`` for ``nominal_interval_seconds`` so bursty
    LoRaWAN-style spacing (12–60 s) does not inflate expected counts and false "low" flags.

    ``status`` is ``missing`` (0 points), ``low`` (density below ``low_ratio_threshold`` of
    expected), or ``ok``.
    """
    if len(timestamps) == 0:
        return {
            "bucket_seconds": bucket_seconds,
            "nominal_interval_seconds": nominal_interval_seconds,
            "low_ratio_threshold": low_ratio_threshold,
            "n_buckets": 0,
            "buckets": [],
            "buckets_with_issues": 0,
            "expectation_note": (
                "Expected counts use a conservative nominal interval (e.g. max(median, p75) spacing) "
                "for irregular sampling such as LoRaWAN."
            ),
        }

    ts = np.sort(timestamps.astype(float))
    t0, t1 = float(ts[0]), float(ts[-1])
    span = max(t1 - t0, 0.0)
    if span < 1e-6:
        width = max(1e-9, span)
        expected = (
            max(1, int(round(width / nominal_interval_seconds)))
            if nominal_interval_seconds > 0
            else None
        )
        count = int(ts.size)
        ratio = (count / expected) if expected else None
        status = "ok"
        if expected and count == 0:
            status = "missing"
        elif expected and ratio is not None and ratio < low_ratio_threshold:
            status = "low"
        b = {
            "start_ts": int(t0),
            "end_ts": int(t1),
            "n_points": count,
            "expected_points_approx": expected,
            "coverage_ratio": round(ratio, 4) if ratio is not None else None,
            "status": status,
        }
        return {
            "bucket_seconds": bucket_seconds,
            "nominal_interval_seconds": nominal_interval_seconds,
            "low_ratio_threshold": low_ratio_threshold,
            "n_buckets": 1,
            "buckets": [b],
            "buckets_with_issues": 0 if status == "ok" else 1,
            "expectation_note": (
                "Expected counts use a conservative nominal interval for irregular sampling (e.g. LoRaWAN)."
            ),
        }

    n_b = max(1, int(np.ceil(span / bucket_seconds)))
    buckets: List[Dict[str, Any]] = []

    for i in range(n_b):
        lo = t0 + i * bucket_seconds
        next_lo = lo + bucket_seconds
        is_last = next_lo >= t1 - 1e-9
        if is_last:
            hi = t1
            mask = (ts >= lo) & (ts <= t1)
            width = max(float(hi - lo), 1e-9)
        else:
            hi = next_lo
            mask = (ts >= lo) & (ts < hi)
            width = float(bucket_seconds)

        count = int(np.sum(mask))
        expected = (
            max(1, int(round(width / nominal_interval_seconds)))
            if nominal_interval_seconds > 0
            else None
        )
        ratio = (count / expected) if expected else None
        status = "ok"
        if expected:
            if count == 0:
                status = "missing"
            elif ratio is not None and ratio < low_ratio_threshold:
                status = "low"

        buckets.append(
            {
                "start_ts": int(lo),
                "end_ts": int(hi),
                "n_points": count,
                "expected_points_approx": expected,
                "coverage_ratio": round(ratio, 4) if ratio is not None else None,
                "status": status,
            }
        )

    issues = len([b for b in buckets if b["status"] != "ok"])
    return {
        "bucket_seconds": bucket_seconds,
        "nominal_interval_seconds": nominal_interval_seconds,
        "low_ratio_threshold": low_ratio_threshold,
        "n_buckets": len(buckets),
        "buckets": buckets,
        "buckets_with_issues": issues,
        "expectation_note": (
            "Expected counts use max(median, p75) inter-arrival spacing when provided as the "
            "nominal interval, so variable cadences (2 s through ~60 s LoRaWAN) are not read as missing data."
        ),
    }


def slim_coverage_for_prompt(coverage: Dict[str, Any]) -> Dict[str, Any]:
    """Smaller dict for LLM prompt: summary + problem windows only."""
    buckets: List[Dict[str, Any]] = list(coverage.get("buckets") or [])
    problems = [b for b in buckets if b.get("status") != "ok"]
    out = {
        "bucket_seconds": coverage.get("bucket_seconds"),
        "nominal_interval_seconds": coverage.get("nominal_interval_seconds"),
        "low_ratio_threshold": coverage.get("low_ratio_threshold"),
        "n_buckets": coverage.get("n_buckets"),
        "buckets_with_issues": coverage.get("buckets_with_issues"),
        "expectation_note": coverage.get("expectation_note"),
        "problem_windows": [
            {
                "start_ts": b["start_ts"],
                "end_ts": b["end_ts"],
                "n_points": b["n_points"],
                "expected_points_approx": b.get("expected_points_approx"),
                "status": b.get("status"),
            }
            for b in problems[:32]
        ],
    }
    if len(problems) > 32:
        out["problem_windows_truncated"] = len(problems) - 32
    return out
