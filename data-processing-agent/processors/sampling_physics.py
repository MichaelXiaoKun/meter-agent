"""
Domain limits for ultrasonic / LoRaWAN / Wi-Fi flow telemetry.

Typical reporting cadence is variable — Wi-Fi meters tick roughly every ~2 s,
LoRaWAN meters burst every ~12–60 s. Healthy links rarely exceed their typical
spacing by a large margin; longer pauses are treated as missing data, not
"normal jitter."

Resolution order for the *healthy inter-arrival cap* each run:

1. ``BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S`` (explicit override — always wins)
2. ``BLUEBOT_METER_NETWORK_TYPE`` (wifi | lorawan | unknown — set by the
   orchestrator when ``get_meter_profile`` has classified the meter)
3. Conservative default (60 s) that covers LoRaWAN cadences
"""

from __future__ import annotations

import os

_DEFAULT_MAX_INTER_ARRIVAL_S = 60.0

# Network-type → sensible healthy inter-arrival cap (seconds).
# Tight enough that genuine outages are flagged, loose enough to absorb normal jitter.
_NETWORK_TYPE_CAP_S: dict[str, float] = {
    "wifi": 5.0,       # typical cadence ~2 s; 5 s swallows small hiccups
    "lorawan": 60.0,   # typical cadence 12–60 s; keep the 1-minute ceiling
    "unknown": 60.0,
}


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _network_type_hint() -> str | None:
    raw = os.environ.get("BLUEBOT_METER_NETWORK_TYPE")
    if raw is None:
        return None
    t = raw.strip().lower()
    return t or None


def max_healthy_inter_arrival_seconds() -> float:
    """
    Upper bound on plausible spacing between consecutive samples when the meter is online.

    Used to cap adaptive gap thresholds and the coverage nominal rate.

    Precedence:
      1. ``BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S`` explicit override
      2. ``BLUEBOT_METER_NETWORK_TYPE`` hint (wifi → 5 s, lorawan/unknown → 60 s)
      3. 60 s default
    """
    explicit = _env_float("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S")
    if explicit is not None:
        return max(2.0, min(explicit, 600.0))

    hint = _network_type_hint()
    if hint and hint in _NETWORK_TYPE_CAP_S:
        return _NETWORK_TYPE_CAP_S[hint]

    return _DEFAULT_MAX_INTER_ARRIVAL_S


def gap_threshold_cap_seconds() -> float:
    """
    Gaps longer than this (seconds) are always flagged, regardless of high percentiles.

    ``max_healthy_inter_arrival * slack``; slack from ``BLUEBOT_GAP_SLACK`` (default 1.5).
    """
    slack = _env_float("BLUEBOT_GAP_SLACK")
    if slack is None:
        slack = 1.5
    return max_healthy_inter_arrival_seconds() * max(1.0, slack)


def describe_sampling_caps() -> dict[str, object]:
    """
    Small audit record for reports and the analysis bundle.
    """
    return {
        "max_healthy_inter_arrival_seconds": max_healthy_inter_arrival_seconds(),
        "gap_threshold_cap_seconds": gap_threshold_cap_seconds(),
        "network_type_hint": _network_type_hint(),
        "explicit_override": _env_float("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S") is not None,
    }
