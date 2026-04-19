"""
Detect near-constant flow rate series (quantization, caching, or genuinely steady flow).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np


def summarize_flatline(
    values: np.ndarray,
    *,
    relative_std_floor: float = 1e-12,
) -> Dict[str, Any]:
    """
    Flag series with no or negligible spread in flow_rate.

    Returns JSON-serialisable fields for verified_facts / prompts.
    """
    clean = np.asarray(values, dtype=float)
    clean = clean[~np.isnan(clean)]
    if clean.size == 0:
        return {
            "flag": "no_valid_data",
            "unique_flow_values": 0,
            "note": None,
        }

    uniq = np.unique(clean)
    nuniq = int(uniq.size)
    vmin = float(np.min(clean))
    vmax = float(np.max(clean))
    rng = vmax - vmin
    std = float(np.std(clean, ddof=1)) if clean.size > 1 else 0.0
    mean_abs = max(abs(float(np.mean(clean))), 1e-12)
    cv = float(std / mean_abs)

    flag: str | None = None
    note: str | None = None

    if nuniq == 1 or rng <= 0.0:
        flag = "constant_flow_series"
        note = (
            "Every sample has the same flow_rate, so sample-to-sample variability cannot be assessed. "
            "Steady demand is one explanation; also consider quantization, repeated telemetry, or export artifacts."
        )
    elif std <= relative_std_floor * mean_abs or cv < 1e-10:
        flag = "near_constant_flow"
        note = (
            "Flow rate spread is negligible relative to the mean; treat fine-grained variability conclusions with caution."
        )

    return {
        "unique_flow_values": nuniq,
        "flow_rate_min": vmin,
        "flow_rate_max": vmax,
        "flow_rate_range": rng,
        "sample_std": std,
        "coefficient_of_variation": cv,
        "flag": flag,
        "note": note,
    }
