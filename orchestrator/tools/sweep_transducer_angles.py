"""
sweep_transducer_angles.py — deterministic multi-angle transducer sweep.

The tool resolves the angle list before confirmation, then executes the
confirmed sweep by reusing the existing SSA-only write path and checking meter
status after each successful angle write.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from tools.meter_profile import get_meter_profile
from tools.meter_status import check_meter_status
from tools.set_transducer_angle import set_transducer_angle_only
from tools.transducer_angle_preflight import (
    allowed_labels_for_network_type,
    normalize_transducer_angle_label,
    preflight_validate_transducer_angle,
)

TOOL_DEFINITION = {
    "name": "sweep_transducer_angles",
    "description": (
        "Deterministically try multiple transducer angles on one meter and compare signal quality. "
        "Use for requests like 'try all allowed angles', 'compare angles', 'find the best angle', "
        "or 'optimize transducer angle'. Resolves the allowed angle list, then requires one user "
        "confirmation before sending any MQTT SSA writes. After confirmation, sets each angle in "
        "sequence and runs check_meter_status after each successful set. Set apply_best_after_sweep "
        "to true only when the user asks to optimize/find/set the best angle; otherwise leave false "
        "to report the comparison and leave the meter at the last successfully tested angle."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": "Physical meter serial number for management lookup.",
            },
            "transducer_angles": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional explicit angle labels to test. Omit or pass an empty list for all "
                    "allowed angles for the meter radio type."
                ),
            },
            "apply_best_after_sweep": {
                "type": "boolean",
                "description": (
                    "True when the user asks to optimize/find/set the best angle. False for "
                    "compare/try/sweep requests."
                ),
            },
        },
        "required": ["serial_number"],
    },
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _angle_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() in {"all", "all allowed", "each", "every"}:
            return []
        return [part.strip() for part in s.replace(";", ",").split(",") if part.strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def _wait_seconds_for_network(network_type: str | None) -> int:
    nt = (network_type or "").strip().lower()
    if nt == "lorawan":
        return int(os.environ.get("BLUEBOT_SSA_ONLY_WAIT_SLOW_SEC", "60"))
    return int(os.environ.get("BLUEBOT_SSA_ONLY_WAIT_SEC", "20"))


def estimate_sweep_duration_seconds(
    *,
    angle_count: int,
    network_type: str | None,
    apply_best_after_sweep: bool,
) -> int:
    write_count = max(0, int(angle_count)) + (1 if apply_best_after_sweep else 0)
    # Status checks add HTTP/subprocess overhead, so pad each step lightly.
    return write_count * (_wait_seconds_for_network(network_type) + 5)


def prepare_sweep_confirmation_inputs(
    inputs: dict[str, Any],
    token: str,
    *,
    profile_lookup: Callable[[str, str], dict[str, Any]] = get_meter_profile,
) -> dict[str, Any]:
    """
    Resolve and validate the sweep before creating a pending configuration action.

    Returns ``{"success": True, "inputs": normalized_inputs, "current_values": ...}``
    or ``{"success": False, "error": ...}``.
    """
    serial_number = str(inputs.get("serial_number") or "").strip()
    if not serial_number:
        return {"success": False, "error": "serial_number is required for angle sweep."}

    apply_best = _as_bool(inputs.get("apply_best_after_sweep"))
    profile = profile_lookup(serial_number, token)
    if not profile.get("success"):
        return {
            "success": False,
            "error": profile.get("error")
            or "Could not load the device profile to resolve transducer angle options.",
        }

    network_type = profile.get("network_type")
    explicit_angles = _angle_values(inputs.get("transducer_angles"))
    source_angles = explicit_angles or list(profile.get("transducer_angle_options") or [])
    if not source_angles:
        source_angles = allowed_labels_for_network_type(network_type)

    resolved: list[str] = []
    seen: set[str] = set()
    for angle in source_angles:
        normalized = normalize_transducer_angle_label(str(angle))
        err = preflight_validate_transducer_angle(normalized, network_type)
        if err:
            return {"success": False, "error": err}
        if normalized not in seen:
            resolved.append(normalized)
            seen.add(normalized)

    if not resolved:
        return {
            "success": False,
            "error": "No transducer angles were provided or available for this meter.",
        }

    final_policy = (
        "set_best_after_sweep" if apply_best else "leave_last_successful_tested_angle"
    )
    estimate = estimate_sweep_duration_seconds(
        angle_count=len(resolved),
        network_type=network_type,
        apply_best_after_sweep=apply_best,
    )
    normalized_inputs = {
        "serial_number": serial_number,
        "transducer_angles": resolved,
        "apply_best_after_sweep": apply_best,
        "network_type": network_type,
        "estimated_duration_seconds": estimate,
        "final_angle_policy": final_policy,
    }
    return {
        "success": True,
        "inputs": normalized_inputs,
        "current_values": {
            "serial_number": serial_number,
            "profile_success": True,
            "network_type": network_type,
            "transducer_angle_options": profile.get("transducer_angle_options"),
            "change_type": "transducer_angle_sweep",
            "estimated_duration_seconds": estimate,
            "final_angle_policy": final_policy,
            "label": (profile.get("profile") or {}).get("label")
            if isinstance(profile.get("profile"), dict)
            else None,
            "timezone": (profile.get("profile") or {}).get("deviceTimeZone")
            if isinstance(profile.get("profile"), dict)
            else None,
        },
    }


def _signal_summary(status_result: dict[str, Any]) -> dict[str, Any] | None:
    status = status_result.get("status_data")
    if not isinstance(status, dict):
        return None
    signal = status.get("signal")
    if not isinstance(signal, dict):
        return None
    keep = ("score", "level", "reliable", "snr", "rssi")
    return {k: signal.get(k) for k in keep if signal.get(k) is not None}


def _score(signal: dict[str, Any] | None) -> float | None:
    if not isinstance(signal, dict):
        return None
    value = signal.get("score")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _status_fields(status_result: dict[str, Any]) -> dict[str, Any]:
    status = status_result.get("status_data")
    if not isinstance(status, dict):
        return {}
    return {
        "online": status.get("online"),
        "last_message_at": status.get("last_message_at"),
        "signal": _signal_summary(status_result),
    }


def _ranking(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in results:
        signal = item.get("signal") if isinstance(item.get("signal"), dict) else None
        score = _score(signal)
        row = {
            "angle": item.get("angle"),
            "signal_score": score,
            "signal_level": signal.get("level") if isinstance(signal, dict) else None,
            "reliable": signal.get("reliable") if isinstance(signal, dict) else None,
        }
        if score is None:
            missing.append(row)
        else:
            scored.append(row)
    scored.sort(key=lambda row: float(row["signal_score"]), reverse=True)
    return scored + missing


def sweep_transducer_angles(
    serial_number: str,
    transducer_angles: list[str] | None,
    token: str,
    *,
    apply_best_after_sweep: bool = False,
    anthropic_api_key: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    profile_lookup: Callable[[str, str], dict[str, Any]] = get_meter_profile,
    set_angle_func: Callable[..., dict[str, Any]] = set_transducer_angle_only,
    check_status_func: Callable[..., dict[str, Any]] = check_meter_status,
) -> dict[str, Any]:
    resolved_angles = [
        normalize_transducer_angle_label(str(angle))
        for angle in (transducer_angles or [])
        if str(angle).strip()
    ]
    if not resolved_angles:
        prepared = prepare_sweep_confirmation_inputs(
            {
                "serial_number": serial_number,
                "transducer_angles": transducer_angles or [],
                "apply_best_after_sweep": apply_best_after_sweep,
            },
            token,
            profile_lookup=profile_lookup,
        )
        if not prepared.get("success"):
            return {
                "success": False,
                "serial_number": serial_number,
                "network_type": None,
                "resolved_angles": [],
                "results": [],
                "ranking": [],
                "best_angle": None,
                "final_angle": None,
                "final_action": "not_started",
                "error": prepared.get("error"),
            }
        resolved_angles = list(prepared["inputs"]["transducer_angles"])
        network_type = prepared["inputs"].get("network_type")
    else:
        profile = profile_lookup(serial_number, token)
        network_type = profile.get("network_type") if profile.get("success") else None

    results: list[dict[str, Any]] = []
    last_successful_angle: str | None = None

    for ix, angle in enumerate(resolved_angles, start=1):
        if on_progress:
            on_progress(
                {
                    "phase": "set_angle",
                    "angle": angle,
                    "index": ix,
                    "total": len(resolved_angles),
                }
            )
        write_result = set_angle_func(
            serial_number,
            angle,
            token,
            anthropic_api_key=anthropic_api_key,
        )
        row: dict[str, Any] = {
            "angle": angle,
            "write_success": bool(write_result.get("success")),
            "write_error": write_result.get("error"),
            "status_success": None,
            "status_error": None,
            "online": None,
            "last_message_at": None,
            "signal": None,
        }
        if write_result.get("success"):
            last_successful_angle = angle
            if on_progress:
                on_progress(
                    {
                        "phase": "check_status",
                        "angle": angle,
                        "index": ix,
                        "total": len(resolved_angles),
                    }
                )
            status_result = check_status_func(
                serial_number,
                token,
                anthropic_api_key=anthropic_api_key,
            )
            row["status_success"] = bool(status_result.get("success"))
            row["status_error"] = status_result.get("error")
            row.update(_status_fields(status_result))
        results.append(row)

    ranking = _ranking(results)
    best_angle = ranking[0]["angle"] if ranking and ranking[0].get("signal_score") is not None else None
    final_action = "left_at_last_successful_tested_angle"
    final_angle = last_successful_angle
    final_verification: dict[str, Any] | None = None
    error: str | None = None
    notice: str | None = None

    if apply_best_after_sweep:
        if best_angle:
            if on_progress:
                on_progress({"phase": "set_best", "angle": best_angle})
            final_write = set_angle_func(
                serial_number,
                str(best_angle),
                token,
                anthropic_api_key=anthropic_api_key,
            )
            final_status = None
            if final_write.get("success"):
                if on_progress:
                    on_progress({"phase": "verify_best", "angle": best_angle})
                final_status = check_status_func(
                    serial_number,
                    token,
                    anthropic_api_key=anthropic_api_key,
                )
                final_angle = str(best_angle)
                final_action = "set_best_after_sweep"
            else:
                final_action = "failed_to_set_best_after_sweep"
                error = final_write.get("error") or "Could not set the best measured angle."
            final_verification = {
                "angle": best_angle,
                "write_success": bool(final_write.get("success")),
                "write_error": final_write.get("error"),
                "status_success": bool(final_status.get("success")) if isinstance(final_status, dict) else None,
                "status_error": final_status.get("error") if isinstance(final_status, dict) else None,
                **(_status_fields(final_status) if isinstance(final_status, dict) else {}),
            }
        else:
            final_action = "best_not_set_no_reliable_score"
            notice = "No reliable numeric signal score was available, so no best angle was applied."

    all_writes_failed = not any(item.get("write_success") for item in results)
    if all_writes_failed:
        error = error or "No transducer angle write succeeded during the sweep."

    return {
        "success": (not all_writes_failed) and error is None,
        "serial_number": serial_number,
        "network_type": network_type,
        "resolved_angles": resolved_angles,
        "results": results,
        "ranking": ranking,
        "best_angle": best_angle,
        "final_angle": final_angle,
        "final_action": final_action,
        "final_verification": final_verification,
        "notice": notice,
        "error": error,
    }
