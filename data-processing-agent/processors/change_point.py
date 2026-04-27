"""
Change-Point / Drift Detection

Bidirectional CUSUM (Cumulative Sum) for detecting persistent shifts in flow rate
relative to a target mean. Robust defaults: median for the target, MAD-derived
sigma for slack/threshold scaling, so the test is largely scale- and outlier-free
without requiring the operator to tune parameters.

CUSUM equations (per side, reset after alarm):
    S+[i] = max(0, S+[i-1] + (x[i] - mu0 - k))   alarms when S+ > h  (upward drift)
    S-[i] = max(0, S-[i-1] - (x[i] - mu0 + k))   alarms when S- > h  (downward drift)

with k = k_sigma * sigma, h = h_sigma * sigma. Choices follow Pignatiello & Runger:
k_sigma = 0.5 detects shifts of ~1*sigma; h_sigma = 5.0 keeps the false-alarm rate
acceptable on healthy data (~1 false alarm per several thousand samples).

The DATA_REQUIREMENTS block at the top is the L1 contract used by
``processors.data_adequacy.check_adequacy`` — adequacy must be OK before the
algorithm runs, otherwise the function returns a structured "skipped" result.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from processors.data_adequacy import adequacy_stub_result, check_adequacy


# L1 contract: minimum data shape this processor needs to produce reliable output.
# Read by check_adequacy; consumed by adaptive_fetch when it sizes windows.
DATA_REQUIREMENTS: Dict[str, Any] = {
    "min_points": 200,
    "ideal_points": 500,
    "max_gap_pct": 25.0,
}

# Cap so a runaway alarm series cannot bloat the prompt or analysis bundle.
_MAX_ALARMS_PER_SIDE = 50

# Sigma floor — protects against MAD = 0 on a flat series, which would make
# k = h = 0 and trigger an alarm on every sample.
_SIGMA_FLOOR_FRACTION = 1e-6


def _robust_sigma(values: np.ndarray) -> float:
    """
    MAD-based sigma estimate, scaled to match a Gaussian std (factor 1.4826).
    Falls back to sample std when MAD collapses to 0 (all duplicates around the
    median); ultimately to a non-zero floor so downstream math stays defined.
    """
    if values.size == 0:
        return 0.0
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    sigma = mad * 1.4826
    if sigma <= 0:
        sigma = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    if sigma <= 0:
        # Final safety: use a tiny fraction of |mean| or 1.0 absolute.
        sigma = max(abs(float(np.mean(values))) * _SIGMA_FLOOR_FRACTION, 1e-9)
    return sigma


def _run_cusum_one_sided(
    deviations: np.ndarray,
    h: float,
    sign: int,
) -> tuple[List[int], np.ndarray, float]:
    """
    Walk a series of mean-corrected, slack-adjusted deviations and emit alarm
    indices whenever the cumulative sum exceeds ``h``. The CUSUM resets to 0
    after each alarm so consecutive shifts are detectable.

    ``sign`` is +1 for upward (positive) drift, -1 for downward (negative). The
    cumulative trace is returned with the chosen sign so callers can plot it.
    """
    n = deviations.size
    cum = np.zeros(n, dtype=float)
    s = 0.0
    alarm_idx: List[int] = []
    peak = 0.0
    for i in range(n):
        s = max(0.0, s + sign * float(deviations[i]))
        cum[i] = sign * s
        if s > peak:
            peak = s
        if s > h and len(alarm_idx) < _MAX_ALARMS_PER_SIDE:
            alarm_idx.append(i)
            s = 0.0  # reset after alarm
    return alarm_idx, cum, peak


def _alarm_records(
    alarm_idx: List[int],
    timestamps: np.ndarray,
    values: np.ndarray,
    cum: np.ndarray,
) -> List[Dict[str, Any]]:
    """JSON-friendly alarm rows with the cumulative score at the alarm index."""
    out: List[Dict[str, Any]] = []
    for i in alarm_idx:
        out.append(
            {
                "timestamp": int(timestamps[i]),
                "value": float(values[i]),
                "cumulative_score": float(cum[i]),
            }
        )
    return out


def compute_cusum(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    target_mean: Optional[float] = None,
    target_std: Optional[float] = None,
    k_sigma: float = 0.5,
    h_sigma: float = 5.0,
) -> Dict[str, Any]:
    """
    Run a bidirectional CUSUM drift test against a reference mean.

    Args:
        timestamps:    Unix timestamps aligned with values.
        values:        Flow rate samples. NaNs are dropped before processing.
        target_mean:   Reference mean ``mu0``. Defaults to the in-series median
                       (robust against outliers and skew).
        target_std:    Reference sigma. Defaults to MAD-derived robust sigma.
        k_sigma:       Slack multiplier. Default 0.5 → CUSUM responds to shifts
                       of about 1 standard deviation.
        h_sigma:       Alarm threshold multiplier. Default 5.0 → ~1 false alarm
                       per several thousand healthy samples.

    Returns:
        JSON-serialisable dict. When the input fails adequacy, returns
        ``adequacy_stub_result`` with no statistics — callers must check
        ``"skipped"`` before reading drift fields.

        On success:
        {
            "skipped":         False,
            "adequacy":        AdequacyReport,
            "target_mean":     float,
            "target_std":      float,
            "k":               float,
            "h":               float,
            "max_S_plus":      float,
            "max_S_minus":     float,
            "positive_alarm_count": int,
            "negative_alarm_count": int,
            "positive_alarms": list[{timestamp, value, cumulative_score}],
            "negative_alarms": list[...],
            "drift_detected":  "none" | "upward" | "downward" | "both",
            "first_alarm_timestamp": int | None,
        }
    """
    ts = np.asarray(timestamps, dtype=float)
    vals = np.asarray(values, dtype=float)

    # Align mask: drop entries where either timestamp or value is non-finite.
    mask = np.isfinite(ts) & np.isfinite(vals)
    ts = ts[mask]
    vals = vals[mask]

    adequacy = check_adequacy(ts, DATA_REQUIREMENTS)
    if not adequacy["ok"]:
        return adequacy_stub_result("cusum_drift", adequacy)

    # Sort by timestamp — robustness against unsorted upstream data.
    order = np.argsort(ts)
    ts = ts[order]
    vals = vals[order]

    mu0 = float(target_mean) if target_mean is not None else float(np.median(vals))
    sigma = float(target_std) if target_std is not None else _robust_sigma(vals)
    k = max(0.0, float(k_sigma)) * sigma
    h = max(0.0, float(h_sigma)) * sigma

    # Pre-compute deviations relative to the target. Slack enters per-side.
    base = vals - mu0
    pos_alarms, pos_cum, pos_peak = _run_cusum_one_sided(base - k, h, sign=+1)
    neg_alarms, neg_cum, neg_peak = _run_cusum_one_sided(base + k, h, sign=-1)

    drift = "none"
    if pos_alarms and neg_alarms:
        drift = "both"
    elif pos_alarms:
        drift = "upward"
    elif neg_alarms:
        drift = "downward"

    first_alarm: Optional[int] = None
    candidate_indices = []
    if pos_alarms:
        candidate_indices.append(pos_alarms[0])
    if neg_alarms:
        candidate_indices.append(neg_alarms[0])
    if candidate_indices:
        first_alarm = int(ts[min(candidate_indices)])

    return {
        "skipped": False,
        "adequacy": adequacy,
        "target_mean": mu0,
        "target_std": sigma,
        "k": k,
        "h": h,
        "k_sigma": float(k_sigma),
        "h_sigma": float(h_sigma),
        "max_S_plus": float(pos_peak),
        "max_S_minus": float(neg_peak),
        "positive_alarm_count": len(pos_alarms),
        "negative_alarm_count": len(neg_alarms),
        "positive_alarms": _alarm_records(pos_alarms, ts, vals, pos_cum),
        "negative_alarms": _alarm_records(neg_alarms, ts, vals, neg_cum),
        "drift_detected": drift,
        "first_alarm_timestamp": first_alarm,
    }


def compute_cusum_facts(
    timestamps: np.ndarray,
    values: np.ndarray,
) -> Dict[str, Any]:
    """
    Slim, deterministic fact block for verified_facts pre-compute.

    Strips the per-alarm row arrays so the prompt embedding stays small
    (full alarm list is still available via the on-demand tool call).
    """
    full = compute_cusum(timestamps, values)
    if full.get("skipped"):
        # Stub already JSON-serialisable; nothing to slim.
        return full

    slim = dict(full)
    pos = slim.pop("positive_alarms", [])
    neg = slim.pop("negative_alarms", [])
    slim["positive_alarms_omitted"] = len(pos)
    slim["negative_alarms_omitted"] = len(neg)
    if pos:
        slim["first_positive_alarm"] = pos[0]
    if neg:
        slim["first_negative_alarm"] = neg[0]
    return slim


__all__ = [
    "DATA_REQUIREMENTS",
    "compute_cusum",
    "compute_cusum_facts",
]
