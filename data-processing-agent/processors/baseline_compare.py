"""
Baseline Compare Processor
==========================

Given a list of clean reference daily rollups (already filtered by
:mod:`processors.baseline_quality`) and an optional partial-today rollup,
produce a compact ``today_vs_baseline`` block describing how today is
tracking against the historical baseline.

This module is **only** meaningful when
``baseline_quality.evaluate_baseline_quality(...).reliable is True``. The
caller is responsible for that gate; this function does no refusal logic of
its own beyond degenerate-input guards (empty reference set ⇒ ``None``).

Output shape (kept stable so the verified-facts JSON schema is forward-
compatible with the system prompt rules that already enforce relaying it):

    {
        "reference_period": {
            "n_days": int,
            "n_same_weekday_days": int | None,
            "first_local_date": str,
            "last_local_date": str,
        },
        "reference_distribution": {
            "median_volume_gallons": float,
            "p25_volume_gallons": float,
            "p75_volume_gallons": float,
            "mad_volume_gallons": float,        # robust spread (1.4826 * MAD)
            "iqr_volume_gallons": float,
        },
        "today_partial": {                      # absent when today_partial is None
            "local_date": str,
            "fraction_of_day_elapsed": float | None,
            "volume_gallons_so_far": float,
        },
        "projection": {                         # absent when today_partial is None or fraction is None
            "method": "linear",
            "projected_full_day_volume_gallons": float,
            "expected_volume_gallons": float,
            "expected_band_low_gallons": float,
            "expected_band_high_gallons": float,
            "ratio_actual_vs_expected": float,
            "ratio_projected_vs_expected": float,
            "z_score_robust": float,
        },
        "verdict": "typical" | "elevated" | "below_normal" | "indeterminate",
        "verdict_reason": str,
    }

The classification follows a simple robust rule consumed by the system
prompt: ``ratio < 0.7`` → ``below_normal``, ``ratio > 1.3`` → ``elevated``,
else ``typical``. Bounds are env-tunable via ``BLUEBOT_BASELINE_COMPARE_LOW``
/ ``BLUEBOT_BASELINE_COMPARE_HIGH`` so ops can adjust without a code change.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence


__all__ = ["compute_today_vs_baseline"]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile in [0, 100]; matches numpy default."""
    finite = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not finite:
        return float("nan")
    if len(finite) == 1:
        return finite[0]
    pos = (q / 100.0) * (len(finite) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return finite[lo]
    frac = pos - lo
    return finite[lo] * (1.0 - frac) + finite[hi] * frac


def _median(values: Sequence[float]) -> float:
    return _percentile(values, 50.0)


def _mad(values: Sequence[float], med: float) -> float:
    """Median absolute deviation (no scale factor)."""
    devs = [abs(float(v) - med) for v in values if v is not None and math.isfinite(float(v))]
    if not devs:
        return 0.0
    return _median(devs)


def compute_today_vs_baseline(
    reference_rollups: Sequence[Dict[str, Any]],
    today_partial: Optional[Dict[str, Any]] = None,
    *,
    target_weekday: Optional[int] = None,
    fraction_of_day_elapsed: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Compute the ``today_vs_baseline`` block.

    Returns ``None`` when ``reference_rollups`` is empty (caller should not
    have invoked us; treat as a defensive guard).

    Notes on the ``z_score_robust``:
        Uses the standard 1.4826 MAD→σ consistency factor. When MAD is zero
        (all reference days identical), z falls back to ``0.0`` rather than
        ``inf`` — a reference distribution with no spread cannot tell us
        anything is "abnormal".
    """
    refs = [r for r in reference_rollups if isinstance(r, dict)]
    if not refs:
        return None

    volumes = [
        float(r["volume_gallons"])
        for r in refs
        if r.get("volume_gallons") is not None
        and math.isfinite(float(r["volume_gallons"]))
    ]
    if not volumes:
        return None

    median_v = _median(volumes)
    p25_v = _percentile(volumes, 25.0)
    p75_v = _percentile(volumes, 75.0)
    mad_v = _mad(volumes, median_v)
    robust_sigma = 1.4826 * mad_v

    n_same_weekday: Optional[int] = None
    if target_weekday is not None:
        same = [
            float(r["volume_gallons"])
            for r in refs
            if r.get("weekday") == target_weekday
            and r.get("volume_gallons") is not None
            and math.isfinite(float(r["volume_gallons"]))
        ]
        n_same_weekday = len(same)
        # Use the same-weekday subset as the expected value when it's large
        # enough — otherwise the broader median wins. Threshold mirrors the
        # baseline_quality.min_same_weekday_days default (3) so behaviour
        # tracks that gate.
        if len(same) >= 3:
            median_v = _median(same)
            p25_v = _percentile(same, 25.0)
            p75_v = _percentile(same, 75.0)
            mad_v = _mad(same, median_v)
            robust_sigma = 1.4826 * mad_v

    iqr_v = max(0.0, p75_v - p25_v)
    first_date = sorted(str(r.get("local_date", "")) for r in refs)[0]
    last_date = sorted(str(r.get("local_date", "")) for r in refs)[-1]

    out: Dict[str, Any] = {
        "reference_period": {
            "n_days": len(refs),
            "n_same_weekday_days": n_same_weekday,
            "first_local_date": first_date,
            "last_local_date": last_date,
        },
        "reference_distribution": {
            "median_volume_gallons": float(median_v),
            "p25_volume_gallons": float(p25_v),
            "p75_volume_gallons": float(p75_v),
            "mad_volume_gallons": float(robust_sigma),
            "iqr_volume_gallons": float(iqr_v),
        },
    }

    if today_partial is None:
        out["verdict"] = "indeterminate"
        out["verdict_reason"] = "today_partial not supplied"
        return out

    today_volume = today_partial.get("volume_gallons")
    try:
        today_volume_f = float(today_volume) if today_volume is not None else None
    except (TypeError, ValueError):
        today_volume_f = None

    out["today_partial"] = {
        "local_date": str(today_partial.get("local_date", "") or ""),
        "fraction_of_day_elapsed": (
            float(fraction_of_day_elapsed)
            if fraction_of_day_elapsed is not None
            and math.isfinite(float(fraction_of_day_elapsed))
            else None
        ),
        "volume_gallons_so_far": today_volume_f,
    }

    if today_volume_f is None or fraction_of_day_elapsed is None:
        out["verdict"] = "indeterminate"
        out["verdict_reason"] = (
            "today's partial volume or elapsed-day fraction unavailable"
        )
        return out

    frac = max(1e-6, min(1.0, float(fraction_of_day_elapsed)))
    projected = today_volume_f / frac
    expected = float(median_v)

    # Apply the ``fraction`` to the expected value when judging today's
    # *actual* progress, so a sane "we're at 35 % of typical at 35 % of the
    # day" reads as ratio≈1.0 (typical) rather than ratio≈0.35 (below normal).
    expected_so_far = expected * frac
    ratio_actual = (
        today_volume_f / expected_so_far if expected_so_far > 0 else float("nan")
    )
    ratio_projected = projected / expected if expected > 0 else float("nan")

    if robust_sigma > 0 and math.isfinite(robust_sigma):
        z_score = (projected - expected) / robust_sigma
    else:
        z_score = 0.0

    band_low = expected - robust_sigma
    band_high = expected + robust_sigma

    out["projection"] = {
        "method": "linear",
        "projected_full_day_volume_gallons": float(projected),
        "expected_volume_gallons": float(expected),
        "expected_band_low_gallons": float(band_low),
        "expected_band_high_gallons": float(band_high),
        "ratio_actual_vs_expected": float(ratio_actual),
        "ratio_projected_vs_expected": float(ratio_projected),
        "z_score_robust": float(z_score),
    }

    low_threshold = _env_float("BLUEBOT_BASELINE_COMPARE_LOW", 0.7)
    high_threshold = _env_float("BLUEBOT_BASELINE_COMPARE_HIGH", 1.3)

    if not math.isfinite(ratio_projected):
        out["verdict"] = "indeterminate"
        out["verdict_reason"] = "expected volume is zero — cannot form a ratio"
    elif ratio_projected < low_threshold:
        out["verdict"] = "below_normal"
        out["verdict_reason"] = (
            f"projected {projected:.0f} gal vs typical {expected:.0f} gal "
            f"(ratio {ratio_projected:.2f} < {low_threshold:.2f})"
        )
    elif ratio_projected > high_threshold:
        out["verdict"] = "elevated"
        out["verdict_reason"] = (
            f"projected {projected:.0f} gal vs typical {expected:.0f} gal "
            f"(ratio {ratio_projected:.2f} > {high_threshold:.2f})"
        )
    else:
        out["verdict"] = "typical"
        out["verdict_reason"] = (
            f"projected {projected:.0f} gal vs typical {expected:.0f} gal "
            f"(ratio {ratio_projected:.2f} within "
            f"[{low_threshold:.2f}, {high_threshold:.2f}])"
        )

    return out
