"""
Transducer angle processor (separate from MQTT transport).

Maps a human-readable angle label to the firmware **ssa** payload string using
Wi-Fi vs LoRaWAN code tables. Radio type comes from **pipe_resolution** (from
resolve_device_and_pipe_specs: is_lorawan / NUI vs serial).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_WIFI_ANGLE_MAP = {"45º": "0", "35º": "1", "25º": "2", "15º": "3"}

_LORAWAN_ANGLE_MAP = {
    "45º": "0",
    "40º": "1",
    "35º": "2",
    "30º": "3",
    "25º": "4",
    "20º": "5",
    "15º": "6",
    "10º": "7",
}


def normalize_angle_label(angle: str) -> str:
    s = (angle or "").strip().replace("°", "º")
    s = re.sub(r"\s+", "", s)
    m = re.fullmatch(r"(\d{1,2})", s)
    if m:
        return f"{m.group(1)}º"
    m2 = re.fullmatch(r"(\d{1,2})º", s)
    if m2:
        return f"{m2.group(1)}º"
    return s


def _ssa_code(is_lorawan: bool, angle: str) -> Tuple[Optional[str], Optional[str]]:
    label = normalize_angle_label(angle)
    m = _LORAWAN_ANGLE_MAP if is_lorawan else _WIFI_ANGLE_MAP
    if label not in m:
        return None, (
            f"Unknown transducer angle {angle!r} (normalized {label!r}) for "
            f"{'LoRaWAN' if is_lorawan else 'Wi-Fi'} meters. "
            f"Valid keys: {', '.join(sorted(m.keys()))}"
        )
    return m[label], None


def allowed_angle_labels(*, is_lorawan: bool | None) -> list[str]:
    """
    Human-readable labels valid for this radio.

    ``is_lorawan`` None means radio unknown — only labels supported on **both**
    Wi-Fi and LoRaWAN are returned (safe subset).
    """
    if is_lorawan is True:
        return sorted(_LORAWAN_ANGLE_MAP.keys())
    if is_lorawan is False:
        return sorted(_WIFI_ANGLE_MAP.keys())
    both = set(_WIFI_ANGLE_MAP.keys()) & set(_LORAWAN_ANGLE_MAP.keys())
    return sorted(both)


def preflight_validate_angle(angle: str, *, is_lorawan: bool | None) -> str | None:
    """
    Return None if *angle* is allowed for the radio; else a short user-facing error.

    ``is_lorawan``: True = LoRaWAN table, False = Wi-Fi, None = unknown (intersection only).
    """
    label = normalize_angle_label(angle)
    if is_lorawan is True:
        m = _LORAWAN_ANGLE_MAP
    elif is_lorawan is False:
        m = _WIFI_ANGLE_MAP
    else:
        allowed = set(_WIFI_ANGLE_MAP.keys()) & set(_LORAWAN_ANGLE_MAP.keys())
        if label not in allowed:
            return (
                f"Unknown or ambiguous transducer angle {angle!r} (normalized {label!r}) "
                f"when the meter radio type is uncertain. "
                f"Valid without knowing Wi-Fi vs LoRaWAN: {', '.join(sorted(allowed))}. "
                f"Use a device profile lookup first, or choose one of those angles."
            )
        return None
    if label not in m:
        return (
            f"Unknown transducer angle {angle!r} (normalized {label!r}) for "
            f"{'LoRaWAN' if is_lorawan else 'Wi-Fi'} meters. "
            f"Valid: {', '.join(sorted(m.keys()))}"
        )
    return None


def resolve_transducer_angle(
    pipe_resolution: Dict[str, Any],
    transducer_angle: str,
) -> Dict[str, Any]:
    """
    Resolve **transducer_angle** to MQTT **ssa** numeric string using pipe_resolution flags.

    Args:
        pipe_resolution: Successful output from resolve_device_and_pipe_specs (error=null).
        transducer_angle: User label, e.g. '45º' / '35°' / '25'.

    Returns:
        {"error": None, "ssa_code": "2", "normalized_angle_label": "25º", "radio": "LoRaWAN"|"Wi-Fi"}
        or {"error": "..."}.
    """
    if pipe_resolution.get("error"):
        return {"error": "pipe_resolution has error; resolve device and catalog before angle."}

    is_lorawan = bool(pipe_resolution.get("is_lorawan"))
    code, err = _ssa_code(is_lorawan, transducer_angle)
    if err:
        return {"error": err}

    return {
        "error": None,
        "ssa_code": str(code),
        "normalized_angle_label": normalize_angle_label(transducer_angle),
        "radio": "LoRaWAN" if is_lorawan else "Wi-Fi",
    }
