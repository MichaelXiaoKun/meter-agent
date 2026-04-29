"""
fleet_health.py — Rank meters by composite health.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from tools.meter_profile import get_meter_profile
from tools.meter_status import check_meter_status


_MAX_METERS = 50
_MAX_WORKERS = 8


TOOL_DEFINITION: dict[str, Any] = {
    "name": "rank_fleet_by_health",
    "description": (
        "Rank 1–50 meters by composite health score. Use when the user "
        "provides a list of serial numbers and asks which meters need attention, "
        "which are healthiest, or how to triage a fleet. The tool fans out to "
        "meter status and profile reads, and can include an optional historical "
        "flow window so gap-density and drift affect the sorted per-meter table."
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
            },
            "flow_window": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "Unix seconds, UTC."},
                    "end": {"type": "integer", "description": "Unix seconds, UTC."},
                },
                "required": ["start", "end"],
                "description": (
                    "Optional explicit historical flow window. When provided, the ranking "
                    "also runs flow analysis per meter and feeds verified_facts into the "
                    "composite health score so gap-density and CUSUM drift affect the rank. "
                    "Call resolve_time_range first for relative windows."
                ),
            },
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


def analyze_flow_data(*args, **kwargs) -> dict:
    """Lazy import keeps status-only fleet tests free of flow-analysis stubs."""
    from tools.flow_analysis import analyze_flow_data as _impl

    return _impl(*args, **kwargs)


def _coerce_unix_seconds(field: str, value: object) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a Unix timestamp in seconds, not a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError(f"{field} is empty")
        return int(float(s))
    raise TypeError(
        f"{field} must be a number (Unix seconds, UTC); got {type(value).__name__!r}"
    )


def _coerce_flow_window(flow_window: object | None) -> tuple[int, int] | None:
    if flow_window is None:
        return None
    if not isinstance(flow_window, dict):
        raise TypeError("flow_window must be an object with start/end")
    start = _coerce_unix_seconds("flow_window.start", flow_window.get("start"))
    end = _coerce_unix_seconds("flow_window.end", flow_window.get("end"))
    if start > end:
        raise ValueError(f"flow_window.start ({start}) must be <= flow_window.end ({end})")
    return start, end


def _load_verified_facts(result: dict | None) -> tuple[dict | None, str | None]:
    if not isinstance(result, dict):
        return None, "flow analysis unavailable"
    if not result.get("success"):
        return None, result.get("error") or "flow analysis failed"
    path = result.get("analysis_json_path")
    if not isinstance(path, str) or not path.strip():
        return None, "analysis_json_path missing from flow analysis result"
    try:
        with open(path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
    except OSError as exc:
        return None, f"could not read analysis bundle: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"analysis bundle was not valid JSON: {exc}"
    facts = bundle.get("verified_facts") if isinstance(bundle, dict) else None
    if not isinstance(facts, dict):
        return None, "analysis bundle missing verified_facts"
    return facts, None


def _flow_summary(result: dict | None, facts: dict | None, err: str | None) -> dict | None:
    if result is None and facts is None and err is None:
        return None
    cusum = facts.get("cusum_drift") if isinstance(facts, dict) else {}
    return {
        "success": facts is not None,
        "analysis_json_path": (
            result.get("analysis_json_path") if isinstance(result, dict) else None
        ),
        "display_range": result.get("display_range") if isinstance(result, dict) else None,
        "gap_event_count": facts.get("gap_event_count") if isinstance(facts, dict) else None,
        "largest_gap_duration_seconds": (
            facts.get("largest_gap_duration_seconds") if isinstance(facts, dict) else None
        ),
        "drift_detected": cusum.get("drift_detected") if isinstance(cusum, dict) else None,
        "error": err,
    }


def _top_concern(
    status_data: dict | None,
    profile_result: dict | None,
    status_error: str | None,
) -> str:
    if status_error:
        return status_error
    if not isinstance(status_data, dict):
        return "status unavailable"
    health = (
        status_data.get("health_score")
        if isinstance(status_data.get("health_score"), dict)
        else {}
    )
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
    staleness = (
        status_data.get("staleness")
        if isinstance(status_data.get("staleness"), dict)
        else {}
    )
    comm = staleness.get("communication_status")
    if comm and comm != "fresh":
        return staleness.get("status_description") or f"communication_status={comm}"
    profile = profile_result.get("profile") if isinstance(profile_result, dict) else None
    if isinstance(profile, dict) and profile.get("active") is False:
        return "profile is inactive"
    return "no immediate concern"


def _row(
    serial: str,
    profile_result: dict,
    status_result: dict,
    *,
    flow_result: dict | None = None,
    flow_facts: dict | None = None,
    flow_error: str | None = None,
) -> dict:
    profile = (
        profile_result.get("profile")
        if isinstance(profile_result.get("profile"), dict)
        else {}
    )
    status_data = (
        status_result.get("status_data")
        if isinstance(status_result.get("status_data"), dict)
        else {}
    )
    staleness = (
        status_data.get("staleness")
        if isinstance(status_data.get("staleness"), dict)
        else {}
    )
    signal = status_data.get("signal") if isinstance(status_data.get("signal"), dict) else {}
    health = (
        status_data.get("health_score")
        if isinstance(status_data.get("health_score"), dict)
        else {}
    )
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
        "health_score_components": health.get("components"),
        "flow_analysis": _flow_summary(flow_result, flow_facts, flow_error),
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
    flow_window: object | None = None,
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
    try:
        resolved_flow_window = _coerce_flow_window(flow_window)
    except (TypeError, ValueError) as exc:
        return {
            "success": False,
            "meters": [],
            "failed_serials": cleaned,
            "truncated": truncated,
            "flow_window": None,
            "error": str(exc),
        }

    def _fetch(serial: str) -> dict:
        try:
            profile_result = get_meter_profile(serial, token)
        except Exception as exc:
            profile_result = {
                "success": False,
                "profile": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        flow_result: dict | None = None
        flow_facts: dict | None = None
        flow_error: str | None = None
        if resolved_flow_window is not None:
            profile = (
                profile_result.get("profile")
                if isinstance(profile_result, dict)
                else None
            )
            meter_tz = profile.get("deviceTimeZone") if isinstance(profile, dict) else None
            start, end = resolved_flow_window
            try:
                flow_result = analyze_flow_data(
                    serial,
                    start,
                    end,
                    token,
                    display_timezone=meter_tz if isinstance(meter_tz, str) else None,
                    anthropic_api_key=anthropic_api_key,
                    network_type=(
                        profile_result.get("network_type")
                        if isinstance(profile_result, dict)
                        else None
                    ),
                    meter_timezone=meter_tz if isinstance(meter_tz, str) else None,
                    analysis_mode="summary",
                )
                flow_facts, flow_error = _load_verified_facts(flow_result)
            except Exception as exc:
                flow_error = f"{type(exc).__name__}: {exc}"
        try:
            if flow_facts is not None:
                status_result = check_meter_status(
                    serial,
                    token,
                    anthropic_api_key=anthropic_api_key,
                    verified_facts=flow_facts,
                )
            else:
                status_result = check_meter_status(
                    serial,
                    token,
                    anthropic_api_key=anthropic_api_key,
                )
        except Exception as exc:
            status_result = {
                "success": False,
                "status_data": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return _row(
            serial,
            profile_result,
            status_result,
            flow_result=flow_result,
            flow_facts=flow_facts,
            flow_error=flow_error,
        )

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
        "flow_window": (
            {"start": resolved_flow_window[0], "end": resolved_flow_window[1]}
            if resolved_flow_window is not None
            else None
        ),
        "error": (
            None
            if any(r.get("success") for r in rows)
            else "No meter status or profile data could be fetched."
        ),
    }
