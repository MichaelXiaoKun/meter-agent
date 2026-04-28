"""
fleet_health.py — Rank meters by composite health.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from tools.meter_profile import get_meter_profile
from tools.meter_status import check_meter_status


_MAX_METERS = 50
_MAX_WORKERS = 8


TOOL_DEFINITION: dict[str, Any] = {
    "name": "rank_fleet_by_health",
    "description": (
        "Rank 1–50 meters by current composite health score. Use when the user "
        "provides a list of serial numbers and asks which meters need attention, "
        "which are healthiest, or how to triage a fleet. The tool fans out to "
        "meter status and profile reads, then returns a sorted per-meter table."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _MAX_METERS,
                "description": "Meter serial numbers to rank; duplicates are removed.",
            }
        },
        "required": ["serial_numbers"],
    },
}


def _dedup_serials(serial_numbers: List[str]) -> List[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in serial_numbers or []:
        if not isinstance(raw, str):
            continue
        serial = raw.strip()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        out.append(serial)
    return out


def _top_concern(status_data: dict | None, profile_result: dict | None, status_error: str | None) -> str:
    if status_error:
        return status_error
    if not isinstance(status_data, dict):
        return "status unavailable"
    health = status_data.get("health_score") if isinstance(status_data.get("health_score"), dict) else {}
    components = health.get("components") if isinstance(health.get("components"), dict) else {}
    available = [
        (name, comp)
        for name, comp in components.items()
        if isinstance(comp, dict) and comp.get("available") and comp.get("score") is not None
    ]
    if available:
        name, comp = min(available, key=lambda item: float(item[1].get("score") or 0.0))
        return f"{name}: {comp.get('reason') or 'lowest component'}"
    signal = status_data.get("signal") if isinstance(status_data.get("signal"), dict) else {}
    if signal.get("action_needed"):
        return signal.get("interpretation") or "signal quality needs attention"
    staleness = status_data.get("staleness") if isinstance(status_data.get("staleness"), dict) else {}
    comm = staleness.get("communication_status")
    if comm and comm != "fresh":
        return staleness.get("status_description") or f"communication_status={comm}"
    profile = profile_result.get("profile") if isinstance(profile_result, dict) else None
    if isinstance(profile, dict) and profile.get("active") is False:
        return "profile is inactive"
    return "no immediate concern"


def _row(serial: str, profile_result: dict, status_result: dict) -> dict:
    profile = profile_result.get("profile") if isinstance(profile_result.get("profile"), dict) else {}
    status_data = status_result.get("status_data") if isinstance(status_result.get("status_data"), dict) else {}
    staleness = status_data.get("staleness") if isinstance(status_data.get("staleness"), dict) else {}
    signal = status_data.get("signal") if isinstance(status_data.get("signal"), dict) else {}
    health = status_data.get("health_score") if isinstance(status_data.get("health_score"), dict) else {}
    status_error = None if status_result.get("success") else status_result.get("error")
    profile_error = None if profile_result.get("success") else profile_result.get("error")
    return {
        "serial_number": serial,
        "success": bool(status_result.get("success") or profile_result.get("success")),
        "health_score": health.get("score"),
        "health_verdict": health.get("verdict"),
        "top_concern": _top_concern(status_data, profile_result, status_error),
        "online": status_data.get("online"),
        "communication_status": staleness.get("communication_status"),
        "seconds_since": staleness.get("seconds_since"),
        "signal_score": signal.get("score"),
        "signal_level": signal.get("level"),
        "network_type": profile_result.get("network_type"),
        "deviceTimeZone": profile.get("deviceTimeZone"),
        "label": profile.get("label"),
        "profile_error": profile_error,
        "status_error": status_error,
    }


def _sort_key(row: dict) -> tuple[float, str]:
    score = row.get("health_score")
    try:
        score_f = float(score)
    except (TypeError, ValueError):
        score_f = -1.0
    return (score_f, str(row.get("serial_number") or ""))


def rank_fleet_by_health(
    serial_numbers: List[str],
    token: str,
    *,
    anthropic_api_key: str | None = None,
) -> Dict[str, Any]:
    cleaned = _dedup_serials(serial_numbers)
    truncated = False
    if len(cleaned) > _MAX_METERS:
        cleaned = cleaned[:_MAX_METERS]
        truncated = True
    if not cleaned:
        return {
            "success": False,
            "meters": [],
            "failed_serials": None,
            "truncated": False,
            "error": "rank_fleet_by_health requires at least one serial number.",
        }
    if not token:
        return {
            "success": False,
            "meters": [],
            "failed_serials": cleaned,
            "truncated": truncated,
            "error": "Bearer token required.",
        }

    def _fetch(serial: str) -> dict:
        try:
            profile_result = get_meter_profile(serial, token)
        except Exception as exc:
            profile_result = {"success": False, "profile": None, "error": f"{type(exc).__name__}: {exc}"}
        try:
            status_result = check_meter_status(
                serial,
                token,
                anthropic_api_key=anthropic_api_key,
            )
        except Exception as exc:
            status_result = {"success": False, "status_data": None, "error": f"{type(exc).__name__}: {exc}"}
        return _row(serial, profile_result, status_result)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(cleaned), _MAX_WORKERS)) as pool:
        futs = {pool.submit(_fetch, serial): serial for serial in cleaned}
        for fut in as_completed(futs):
            rows.append(fut.result())

    rows.sort(key=_sort_key)
    failed = [r["serial_number"] for r in rows if not r.get("success")]
    return {
        "success": any(r.get("success") for r in rows),
        "meters": rows,
        "failed_serials": failed or None,
        "truncated": truncated,
        "error": None if any(r.get("success") for r in rows) else "No meter status or profile data could be fetched.",
    }
