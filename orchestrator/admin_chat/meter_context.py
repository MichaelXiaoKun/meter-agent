"""Meter Context Packet helpers for admin turns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable


_SERIAL_RE = re.compile(r"\bBB[A-Z0-9-]{1,32}\b", re.IGNORECASE)


@dataclass(frozen=True)
class MeterContextBuildResult:
    """Built packet plus raw tool outputs used to seed per-turn dedupe."""

    serial_number: str
    event: dict[str, Any]
    profile_result_json: str | None = None
    status_result_json: str | None = None


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return " ".join(parts)
    return ""


def extract_serials(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _SERIAL_RE.finditer(text or ""):
        serial = match.group(0).strip().upper()
        if serial not in seen:
            seen.add(serial)
            out.append(serial)
    return out


def latest_persisted_meter_context(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "turn_activity":
                continue
            events = block.get("events")
            if not isinstance(events, list):
                continue
            for event in reversed(events):
                if not isinstance(event, dict):
                    continue
                ctx = event.get("meter_context")
                if isinstance(ctx, dict) and ctx.get("serial_number"):
                    return ctx
    return None


def resolve_active_serial(messages: list[dict[str, Any]]) -> str | None:
    """Resolve one active serial, latest user first, then persisted workspace."""

    latest_serials = extract_serials(latest_user_text(messages))
    if len(latest_serials) == 1:
        return latest_serials[0]
    if len(latest_serials) > 1:
        return None

    ctx = latest_persisted_meter_context(messages)
    serial = str(ctx.get("serial_number") or "").strip().upper() if ctx else ""
    return serial or None


def _compact_signal(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "score",
        "level",
        "reliable",
        "action_needed",
        "status",
        "rssi",
        "snr",
    )
    return {k: value.get(k) for k in keys if value.get(k) is not None} or None


def _compact_pipe_config(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "material",
        "standard",
        "pipe_standard",
        "nominal_size",
        "pipe_size",
        "inner_diameter_mm",
        "outer_diameter_mm",
        "wall_thickness_mm",
        "transducer_angle",
    )
    return {k: value.get(k) for k in keys if value.get(k) is not None} or None


def _health_value(status_data: dict[str, Any], key: str) -> Any:
    health = status_data.get("health_score")
    if isinstance(health, dict):
        return health.get(key)
    if key == "score":
        return health
    return None


def _signal_state(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "unknown"
    if signal.get("action_needed") is True:
        return "attention"
    level = str(signal.get("level") or signal.get("status") or "").lower()
    if level in {"excellent", "good", "ok", "fair"}:
        return "ok"
    if level in {"poor", "bad", "weak", "critical"}:
        return "attention"
    reliable = signal.get("reliable")
    if reliable is True:
        return "ok"
    if reliable is False:
        return "attention"
    return "unknown"


def _telemetry_state(status_data: dict[str, Any]) -> str:
    if status_data.get("online") is False:
        return "attention"
    staleness = status_data.get("staleness")
    communication = (
        str(staleness.get("communication_status") or "").lower()
        if isinstance(staleness, dict)
        else ""
    )
    if communication in {"fresh", "online", "ok", "healthy"}:
        return "ok"
    if communication in {"stale", "offline", "missing", "delayed", "unhealthy"}:
        return "attention"
    if status_data.get("online") is True:
        return "ok"
    return "unknown"


def _recent_flow_state(recent_flow: dict[str, Any] | None) -> str:
    if not isinstance(recent_flow, dict):
        return "not_checked"
    return str(recent_flow.get("state") or "not_checked").strip().lower() or "not_checked"


def _confidence(
    *,
    profile_ok: bool,
    status_ok: bool,
    recent_flow: dict[str, Any] | None,
) -> dict[str, str]:
    recent_state = _recent_flow_state(recent_flow)
    if recent_state in {"checked", "empty"}:
        recent_confidence = "high"
    elif recent_state == "not_checked":
        recent_confidence = "not_checked"
    else:
        recent_confidence = "missing"
    return {
        "profile": "high" if profile_ok else "missing",
        "status": "high" if status_ok else "missing",
        "recent_flow": recent_confidence,
    }


def _known_missing(packet: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not packet.get("label"):
        missing.append("profile.label")
    if not packet.get("network_type"):
        missing.append("profile.network_type")
    if packet.get("online") is None:
        missing.append("status.online")
    if not packet.get("pipe_config"):
        missing.append("status.pipe_config")
    recent_state = _recent_flow_state(packet.get("recent_flow"))
    if recent_state == "not_checked":
        missing.append("recent_flow_not_checked")
    elif recent_state == "timed_out":
        missing.append("recent_flow_timed_out")
    elif recent_state == "unavailable":
        missing.append("recent_flow_unavailable")
    return missing


def _next_tools(packet: dict[str, Any]) -> list[str]:
    serial = str(packet.get("serial_number") or "").strip() or "this meter"
    return [
        f"Refresh health for {serial}",
        f"Run last 24h flow analysis for {serial}",
        f"Check gaps or outages for {serial}",
        f"Compare today vs yesterday for {serial}",
        f"Inspect pipe configuration for {serial}",
    ]


def _recent_flow_signal(recent_flow: dict[str, Any] | None) -> dict[str, str]:
    state = _recent_flow_state(recent_flow)
    if state == "checked":
        if not isinstance(recent_flow, dict):
            signal_state = "missing"
        elif (
            recent_flow.get("valid_flow_count", 0) > 0
            and recent_flow.get("latest_sample_fresh") is not False
            and int(recent_flow.get("gap_count") or 0) == 0
        ):
            signal_state = "ok"
        else:
            signal_state = "attention"
        return {
            "name": "recent_flow",
            "state": signal_state,
            "confidence": "high",
        }
    if state == "empty":
        return {
            "name": "recent_flow",
            "state": "attention",
            "confidence": "high",
        }
    return {
        "name": "recent_flow",
        "state": "missing",
        "confidence": "not_checked" if state == "not_checked" else "missing",
    }


def _packet_from_results(
    serial_number: str,
    profile_result: dict[str, Any] | None,
    status_result: dict[str, Any] | None,
    recent_flow_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet: dict[str, Any] = {"serial_number": serial_number}
    profile_ok = bool(profile_result and profile_result.get("success") is True)
    status_data = (
        status_result.get("status_data")
        if isinstance(status_result, dict) and isinstance(status_result.get("status_data"), dict)
        else None
    )
    status_ok = bool(status_data)

    profile = profile_result.get("profile") if isinstance(profile_result, dict) else None
    if isinstance(profile, dict):
        packet.update(
            {
                "label": profile.get("label"),
                "network_type": profile_result.get("network_type"),
                "timezone": profile.get("deviceTimeZone"),
                "installed": profile.get("installed"),
                "commissioned": profile.get("commissioned"),
                "active": profile.get("active"),
            }
        )
    elif isinstance(profile_result, dict) and profile_result.get("network_type"):
        packet["network_type"] = profile_result.get("network_type")

    if isinstance(status_data, dict):
        signal = _compact_signal(status_data.get("signal"))
        pipe_config = _compact_pipe_config(status_data.get("pipe_config"))
        staleness = status_data.get("staleness")
        packet.update(
            {
                "serial_number": status_data.get("serial_number") or serial_number,
                "online": status_data.get("online"),
                "last_message_at": status_data.get("last_message_at"),
                "communication_status": (
                    staleness.get("communication_status")
                    if isinstance(staleness, dict)
                    else None
                ),
                "signal": signal,
                "pipe_config": pipe_config,
                "health_score": _health_value(status_data, "score"),
                "health_verdict": _health_value(status_data, "verdict"),
            }
        )
        packet["diagnostic_signals"] = [
            {
                "name": "telemetry_freshness",
                "state": _telemetry_state(status_data),
                "confidence": "high",
            },
            {
                "name": "signal_quality",
                "state": _signal_state(signal),
                "confidence": "high" if signal else "missing",
            },
            {
                "name": "pipe_config",
                "state": "ok" if pipe_config else "missing",
                "confidence": "high" if pipe_config else "missing",
            },
            _recent_flow_signal(recent_flow_result),
        ]
        if status_result.get("timed_out") is True:
            packet["status_summary_timed_out"] = True

    if isinstance(recent_flow_result, dict) and recent_flow_result.get("state"):
        packet["recent_flow"] = recent_flow_result
        if not packet.get("diagnostic_signals"):
            packet["diagnostic_signals"] = [_recent_flow_signal(recent_flow_result)]
    else:
        packet["recent_flow"] = {
            "state": "not_checked",
            "reason": (
                "The Meter Context Packet v1 did not fetch recent flow data; "
                "do not infer that recent flow data is absent."
            ),
        }
        if not packet.get("diagnostic_signals"):
            packet["diagnostic_signals"] = [_recent_flow_signal(packet["recent_flow"])]

    packet["confidence"] = _confidence(
        profile_ok=profile_ok,
        status_ok=status_ok,
        recent_flow=packet.get("recent_flow"),
    )
    packet["known_missing"] = _known_missing(packet)
    packet["recommended_next_tools"] = _next_tools(packet)
    return {k: v for k, v in packet.items() if v is not None and v != ""}


def build_meter_context_packet(
    messages: list[dict[str, Any]],
    token: str,
    *,
    get_profile: Callable[..., dict[str, Any]],
    check_status: Callable[..., dict[str, Any]],
    get_recent_flow: Callable[..., dict[str, Any]] | None = None,
    anthropic_api_key: str | None = None,
) -> MeterContextBuildResult | None:
    serial = resolve_active_serial(messages)
    if not serial:
        return None

    profile_result: dict[str, Any] | None = None
    status_result: dict[str, Any] | None = None
    recent_flow_result: dict[str, Any] | None = None

    try:
        profile_result = get_profile(serial, token)
    except Exception as exc:  # pragma: no cover - defensive envelope
        profile_result = {"success": False, "serial_number": serial, "error": str(exc)}

    try:
        status_result = check_status(
            serial,
            token,
            anthropic_api_key=anthropic_api_key,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        status_result = {"success": False, "serial_number": serial, "error": str(exc)}

    if get_recent_flow is not None:
        network_type = (
            profile_result.get("network_type")
            if isinstance(profile_result, dict)
            else None
        )
        try:
            recent_flow_result = get_recent_flow(
                serial,
                token,
                network_type=network_type,
            )
        except Exception as exc:  # pragma: no cover - defensive envelope
            recent_flow_result = {
                "state": "unavailable",
                "serial_number": serial,
                "reason": str(exc),
            }

    packet = _packet_from_results(
        serial,
        profile_result,
        status_result,
        recent_flow_result,
    )
    event = {"type": "meter_context", "meter_context": packet}
    return MeterContextBuildResult(
        serial_number=serial,
        event=event,
        profile_result_json=json.dumps(profile_result, default=str),
        status_result_json=json.dumps(status_result, default=str),
    )


def format_meter_context_for_prompt(packet: dict[str, Any]) -> str:
    """Small deterministic context note appended to the system prompt."""

    serial = packet.get("serial_number")
    if not serial:
        return ""
    return (
        "\n\nCurrent Meter Context Packet:\n"
        f"{json.dumps(packet, sort_keys=True, default=str)}\n"
        "Use these structured facts as already-gathered context. Do not repeat "
        "profile/status tools unless the user explicitly asks to refresh or the "
        "facts are insufficient for the question. For recent flow, only say "
        "there is no recent flow data when recent_flow.state is 'empty'. If "
        "recent_flow.state is 'checked', summarize the snapshot facts. If it is "
        "'not_checked', 'timed_out', or 'unavailable', describe that state and do "
        "not say there is no recent flow data or no recent flow."
    )
