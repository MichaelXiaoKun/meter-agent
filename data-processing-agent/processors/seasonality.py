"""
Diurnal / weekly seasonality helpers.

Build a meter-local hour-of-day profile from reference flow data, then score
the current window against that profile. This is intentionally pure: no
network, no filesystem, and JSON-serialisable outputs.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


STATE_NOT_REQUESTED = "not_requested"
STATE_INSUFFICIENT_HISTORY = "insufficient_history"
STATE_READY = "ready"
STATE_NO_TODAY_DATA = "no_today_data"
STATE_NO_OVERLAP = "no_overlap"
STATE_SCORED = "scored"


def not_requested_stub() -> Dict[str, Any]:
    return {
        "state": STATE_NOT_REQUESTED,
        "reliable": False,
        "reasons_refused": [],
    }


def _safe_tz(tz: Optional[str]) -> str:
    if isinstance(tz, str) and tz.strip():
        return tz.strip()
    return "UTC"


def _flow_by_local_hour(df: pd.DataFrame, *, tz: str) -> pd.DataFrame:
    if df is None or len(df) == 0 or "timestamp" not in df.columns or "flow_rate" not in df.columns:
        return pd.DataFrame(columns=["timestamp", "flow_rate", "_local_date", "_hour"])

    work = df[["timestamp", "flow_rate"]].copy()
    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    work["flow_rate"] = pd.to_numeric(work["flow_rate"], errors="coerce")
    work = work.dropna(subset=["timestamp", "flow_rate"]).sort_values("timestamp")
    if work.empty:
        return pd.DataFrame(columns=["timestamp", "flow_rate", "_local_date", "_hour"])

    local = pd.to_datetime(work["timestamp"].to_numpy(dtype=float), unit="s", utc=True).tz_convert(tz)
    work["_local_date"] = local.strftime("%Y-%m-%d")
    work["_hour"] = local.hour.astype(int)
    return work


def _percentile(values: pd.Series, q: float) -> float | None:
    clean = values.dropna().astype(float)
    if clean.empty:
        return None
    return float(np.percentile(clean.to_numpy(dtype=float), q))


def build_diurnal_profile(
    df: pd.DataFrame,
    *,
    tz: str,
    n_days: int = 28,
) -> Dict[str, Any]:
    """Build a reference profile keyed by meter-local hour.

    The most recent ``n_days`` local dates are used. Success includes all
    24 hour keys with ``None`` for hours not represented in the reference
    data. At least seven local dates are required.
    """
    tz_name = _safe_tz(tz)
    work = _flow_by_local_hour(df, tz=tz_name)
    if work.empty:
        return {
            "state": STATE_INSUFFICIENT_HISTORY,
            "reliable": False,
            "tz": tz_name,
            "n_days_used": 0,
            "reasons_refused": ["Need at least 7 local days to build a diurnal profile."],
        }

    local_dates = sorted(str(d) for d in work["_local_date"].dropna().unique())
    try:
        days_limit = max(1, int(n_days))
    except (TypeError, ValueError):
        days_limit = 28
    selected_dates = set(local_dates[-days_limit:])
    work = work[work["_local_date"].isin(selected_dates)]
    n_days_used = len(selected_dates)

    if n_days_used < 7:
        return {
            "state": STATE_INSUFFICIENT_HISTORY,
            "reliable": False,
            "tz": tz_name,
            "n_days_used": int(n_days_used),
            "reasons_refused": ["Need at least 7 local days to build a diurnal profile."],
        }

    grouped = work.groupby("_hour")["flow_rate"]
    medians = grouped.median()
    p25 = grouped.apply(lambda s: _percentile(s, 25.0))
    p75 = grouped.apply(lambda s: _percentile(s, 75.0))
    counts = grouped.count()

    return {
        "state": STATE_READY,
        "reliable": True,
        "tz": tz_name,
        "n_days_used": int(n_days_used),
        "hour": {
            str(h): (float(medians.loc[h]) if h in medians.index else None)
            for h in range(24)
        },
        "p25": {
            str(h): (float(p25.loc[h]) if h in p25.index and p25.loc[h] is not None else None)
            for h in range(24)
        },
        "p75": {
            str(h): (float(p75.loc[h]) if h in p75.index and p75.loc[h] is not None else None)
            for h in range(24)
        },
        "n_samples_by_hour": {
            str(h): (int(counts.loc[h]) if h in counts.index else 0)
            for h in range(24)
        },
    }


def _hour_z_score(observed: float, expected: float, p25: float | None, p75: float | None) -> float:
    iqr = (
        float(p75) - float(p25)
        if p25 is not None
        and p75 is not None
        and math.isfinite(float(p25))
        and math.isfinite(float(p75))
        else 0.0
    )
    # IQR / 1.349 estimates sigma for normal-ish data. The epsilon fallback
    # keeps constant reference hours scoreable instead of producing inf/NaN.
    scale = max(abs(iqr) / 1.349, 1e-6)
    return float((observed - expected) / scale)


def score_against_diurnal(today_df: pd.DataFrame, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Score observed local-hour medians against a diurnal profile."""
    if not isinstance(profile, dict) or profile.get("state") != STATE_READY:
        return {
            "state": profile.get("state", STATE_INSUFFICIENT_HISTORY) if isinstance(profile, dict) else STATE_INSUFFICIENT_HISTORY,
            "reliable": False,
            "departure_score": None,
            "hourly_scores": {},
            "reasons_refused": (
                profile.get("reasons_refused", []) if isinstance(profile, dict) else ["Diurnal profile is unavailable."]
            ),
        }

    tz_name = _safe_tz(profile.get("tz"))
    work = _flow_by_local_hour(today_df, tz=tz_name)
    if work.empty:
        return {
            "state": STATE_NO_TODAY_DATA,
            "reliable": False,
            "departure_score": None,
            "hourly_scores": {},
            "reasons_refused": ["No current-window flow rows available to score against the diurnal profile."],
        }

    observed = work.groupby("_hour")["flow_rate"].median()
    expected_by_hour = profile.get("hour") if isinstance(profile.get("hour"), dict) else {}
    p25_by_hour = profile.get("p25") if isinstance(profile.get("p25"), dict) else {}
    p75_by_hour = profile.get("p75") if isinstance(profile.get("p75"), dict) else {}

    hourly_scores: Dict[str, Dict[str, Any]] = {}
    z_values: list[float] = []
    for hour, observed_value in observed.items():
        key = str(int(hour))
        expected = expected_by_hour.get(key)
        if expected is None:
            continue
        try:
            expected_f = float(expected)
            observed_f = float(observed_value)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(expected_f) and math.isfinite(observed_f)):
            continue

        p25 = p25_by_hour.get(key)
        p75 = p75_by_hour.get(key)
        z = _hour_z_score(observed_f, expected_f, p25, p75)
        z_values.append(z)
        hourly_scores[key] = {
            "observed_median_flow_rate": observed_f,
            "expected_median_flow_rate": expected_f,
            "expected_p25_flow_rate": p25,
            "expected_p75_flow_rate": p75,
            "z_score": z,
        }

    if not hourly_scores:
        return {
            "state": STATE_NO_OVERLAP,
            "reliable": False,
            "departure_score": None,
            "hourly_scores": {},
            "reasons_refused": ["Current-window hours do not overlap populated diurnal-profile hours."],
        }

    return {
        "state": STATE_SCORED,
        "reliable": True,
        "n_hours_scored": int(len(hourly_scores)),
        "departure_score": float(max(abs(z) for z in z_values)),
        "hourly_scores": hourly_scores,
    }
