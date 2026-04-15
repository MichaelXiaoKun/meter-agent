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
