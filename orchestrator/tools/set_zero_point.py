"""
set_zero_point.py - Orchestrator tool for the zero-point MQTT command.

The write itself is small (``{"szv":"null"}``), but it is only safe after
recent flow evidence suggests no large active flow. The orchestrator uses the
preflight helpers in this module before creating the confirmation action.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import time
from typing import Callable

from subprocess_env import tool_subprocess_env
from tools.flow_analysis import analyze_flow_data
from tools.meter_profile import get_meter_profile
from tools.meter_status import check_meter_status
from tools.pipe_subprocess import run_pipe_configuration_agent, subprocess_error_message

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "pipe-configuration-agent")
)

_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

TOOL_DEFINITION = {
    "name": "set_zero_point",
    "description": (
        "Send the meter zero-point command over MQTT using payload {\"szv\":\"null\"}. "
        "Use only when the user explicitly wants to put the meter into set-zero-point state. "
        "The orchestrator must first check current meter status and recent historical flow data: "
        "large active flow must block the action; small near-zero flow can proceed only through "
        "the normal user confirmation workflow because it may be baseline drift. "
        "After a successful run, verify the meter with check_meter_status."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": "Physical meter serial (management serialNumber query).",
            }
        },
        "required": ["serial_number"],
    },
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            f = float(value)
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _percentile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)


def _rows_from_flow_result(flow_result: dict) -> list[dict[str, float]]:
    raw_rows = flow_result.get("zero_point_preflight_rows") or flow_result.get("preflight_rows")
    if isinstance(raw_rows, list):
        return _coerce_rows(raw_rows)

    artifacts = flow_result.get("download_artifacts")
    if not isinstance(artifacts, list):
        return []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path")
        if not isinstance(path, str) or not path or not os.path.exists(path):
            continue
        rows: list[dict[str, object]] = []
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    rows.append(row)
        except OSError:
            continue
        coerced = _coerce_rows(rows)
        if coerced:
            return coerced
    return []


def _coerce_rows(rows: list[object]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _safe_float(row.get("timestamp"))
        flow = _safe_float(row.get("flow_rate") if "flow_rate" in row else row.get("flow"))
        quality = _safe_float(row.get("quality") if "quality" in row else row.get("signal_quality"))
        if ts is None or flow is None:
            continue
        item = {"timestamp": ts, "flow_rate": flow}
        if quality is not None:
            item["quality"] = quality
        out.append(item)
    out.sort(key=lambda r: r["timestamp"])
    return out


def _recent_rows(rows: list[dict[str, float]], seconds: int) -> list[dict[str, float]]:
    if not rows:
        return []
    latest = rows[-1]["timestamp"]
    recent = [r for r in rows if latest - r["timestamp"] <= seconds]
    return recent or rows[-min(len(rows), 20):]


def _flow_stats(rows: list[dict[str, float]], recent_seconds: int) -> dict:
    recent = _recent_rows(rows, recent_seconds)
    values = [r["flow_rate"] for r in rows]
    abs_values = [abs(v) for v in values]
    recent_values = [r["flow_rate"] for r in recent]
    recent_abs = [abs(v) for v in recent_values]
    return {
        "row_count": len(rows),
        "recent_row_count": len(recent),
        "recent_window_seconds": recent_seconds,
        "latest_flow_gpm": recent_values[-1] if recent_values else None,
        "recent_median_abs_gpm": _percentile(recent_abs, 0.5),
        "recent_p90_abs_gpm": _percentile(recent_abs, 0.9),
        "recent_max_abs_gpm": max(recent_abs) if recent_abs else None,
        "window_median_abs_gpm": _percentile(abs_values, 0.5),
        "window_p90_abs_gpm": _percentile(abs_values, 0.9),
    }


def _analysis_drift_evidence(flow_result: dict) -> dict:
    details = flow_result.get("analysis_details")
    details = details if isinstance(details, dict) else {}
    cusum = details.get("cusum_drift") if isinstance(details.get("cusum_drift"), dict) else {}
    attribution = details.get("attribution") if isinstance(details.get("attribution"), dict) else {}
    direction = cusum.get("drift_detected")
    attribution_type = attribution.get("primary_type")
    drift_detected = (
        direction in {"upward", "downward", "both"}
        or attribution_type in {"possible_leak_or_baseline_rise", "real_flow_change"}
    )
    return {
        "detected": bool(drift_detected),
        "direction": direction,
        "cusum_adequacy_ok": cusum.get("adequacy_ok"),
        "positive_alarm_count": cusum.get("positive_alarm_count"),
        "negative_alarm_count": cusum.get("negative_alarm_count"),
        "first_alarm_timestamp": cusum.get("first_alarm_timestamp"),
        "attribution_type": attribution_type,
        "attribution_summary": attribution.get("summary"),
    }


def _quality_recovery_before_drift(rows: list[dict[str, float]], *, small_threshold: float) -> dict:
    q_rows = [r for r in rows if "quality" in r]
    if len(q_rows) < 6:
        return {"detected": False, "reason": "Not enough signal-quality samples in recent history."}

    early = rows[: max(3, len(rows) // 4)]
    early_abs = [abs(r["flow_rate"]) for r in early]
    early_median = _percentile(early_abs, 0.5) or 0.0
    drift_floor = max(early_median + _env_float("BLUEBOT_ZERO_POINT_DRIFT_DELTA_GPM", 0.03), 0.0)
    drift_candidates = [
        r for r in rows[len(rows) // 3:] if abs(r["flow_rate"]) >= drift_floor and abs(r["flow_rate"]) <= small_threshold
    ]
    drift_ts = drift_candidates[0]["timestamp"] if drift_candidates else None
    if drift_ts is None:
        return {
            "detected": False,
            "reason": "Recent flow did not show a small baseline rise suitable for this pattern check.",
            "early_median_abs_gpm": early_median,
        }

    high = _env_float("BLUEBOT_ZERO_POINT_SIGNAL_HIGH", 75.0)
    low = _env_float("BLUEBOT_ZERO_POINT_SIGNAL_LOW", 60.0)
    saw_high_before = False
    low_started: float | None = None
    low_ended: float | None = None
    for r in q_rows:
        ts = r["timestamp"]
        quality = r.get("quality")
        if quality is None:
            continue
        if ts < drift_ts and quality >= high:
            saw_high_before = True
        if saw_high_before and ts <= drift_ts and quality <= low:
            low_started = low_started or ts
            low_ended = ts
        if low_ended is not None and ts >= low_ended and ts <= drift_ts + 1800 and quality >= high:
            return {
                "detected": True,
                "quality_low_start": low_started,
                "quality_low_end": low_ended,
                "drift_start_estimate": drift_ts,
                "early_median_abs_gpm": early_median,
                "thresholds": {"high": high, "low": low},
            }
    return {
        "detected": False,
        "reason": "No high-low-high signal-quality sequence was found before the estimated drift.",
        "drift_start_estimate": drift_ts,
        "early_median_abs_gpm": early_median,
        "thresholds": {"high": high, "low": low},
    }


def evaluate_zero_point_preflight(
    *,
    status_result: dict,
    flow_result: dict,
) -> dict:
    """
    Classify whether zero-point confirmation may be offered.

    Returns a dict with ``allow_confirmation``. If false, the caller must not
    create a pending config action.
    """
    status = status_result.get("status_data") if isinstance(status_result.get("status_data"), dict) else {}
    if status.get("online") is False:
        return {
            "allow_confirmation": False,
            "flow_state": "blocked_offline",
            "summary": "Meter appears offline, so I cannot safely enter set-zero-point state.",
            "risk": "No zero-point command was sent.",
        }

    if not flow_result.get("success"):
        return {
            "allow_confirmation": False,
            "flow_state": "insufficient_evidence",
            "summary": flow_result.get("error") or "Recent flow analysis did not complete.",
            "risk": "No zero-point command was sent because recent flow evidence is required.",
        }

    rows = _rows_from_flow_result(flow_result)
    recent_seconds = _env_int("BLUEBOT_ZERO_POINT_RECENT_SLICE_SECONDS", 600)
    min_points = max(1, _env_int("BLUEBOT_ZERO_POINT_MIN_RECENT_POINTS", 3))
    stats = _flow_stats(rows, recent_seconds)
    if stats["recent_row_count"] < min_points:
        return {
            "allow_confirmation": False,
            "flow_state": "insufficient_evidence",
            "summary": "Not enough recent flow samples are available to judge whether water is moving.",
            "flow_stats": stats,
            "risk": "No zero-point command was sent because recent flow evidence is required.",
        }

    zero_threshold = _env_float("BLUEBOT_ZERO_POINT_ZERO_FLOW_MAX_GPM", 0.03)
    small_threshold = _env_float("BLUEBOT_ZERO_POINT_SMALL_FLOW_MAX_GPM", 0.25)
    large_threshold = _env_float("BLUEBOT_ZERO_POINT_LARGE_FLOW_GPM", 1.0)
    recent_p90 = stats.get("recent_p90_abs_gpm")
    recent_max = stats.get("recent_max_abs_gpm")
    latest = abs(stats.get("latest_flow_gpm") or 0.0)
    drift = _analysis_drift_evidence(flow_result)
    signal_pattern = _quality_recovery_before_drift(rows, small_threshold=small_threshold)

    if (
        isinstance(recent_max, (int, float))
        and recent_max >= large_threshold
        or latest >= large_threshold
    ):
        return {
            "allow_confirmation": False,
            "flow_state": "large_flow_blocked",
            "summary": (
                f"Recent flow is too high for a zero-point operation "
                f"(latest {latest:.3g} gpm, recent max {float(recent_max):.3g} gpm)."
            ),
            "flow_stats": stats,
            "drift_evidence": drift,
            "signal_quality_recovery_before_drift": signal_pattern,
            "thresholds": {
                "zero_flow_max_gpm": zero_threshold,
                "small_flow_max_gpm": small_threshold,
                "large_flow_gpm": large_threshold,
            },
            "risk": "Large active flow was detected, so the zero-point command is blocked.",
        }

    if isinstance(recent_p90, (int, float)) and recent_p90 <= zero_threshold:
        flow_state = "no_flow"
        summary = (
            f"Recent flow looks effectively zero (recent p90 |flow| {recent_p90:.3g} gpm). "
            "User confirmation is still required before entering set-zero-point state."
        )
    elif isinstance(recent_p90, (int, float)) and recent_p90 <= small_threshold:
        flow_state = "small_flow_possible_drift"
        drift_s = "drift evidence is present" if drift.get("detected") else "drift evidence is not conclusive"
        signal_s = (
            "a prior signal-quality high-low-high pattern was detected"
            if signal_pattern.get("detected")
            else "no prior signal-quality recovery pattern was confirmed"
        )
        summary = (
            f"Recent flow is small but non-zero (recent p90 |flow| {recent_p90:.3g} gpm); "
            f"{drift_s}, and {signal_s}. This may be baseline drift, but the user must confirm no intended water flow."
        )
    else:
        value_s = f"{float(recent_p90):.3g} gpm" if isinstance(recent_p90, (int, float)) else "unknown"
        return {
            "allow_confirmation": False,
            "flow_state": "active_flow_blocked",
            "summary": (
                f"Recent flow is above the small-flow threshold ({value_s}), so I cannot "
                "safely offer set-zero-point."
            ),
            "flow_stats": stats,
            "drift_evidence": drift,
            "signal_quality_recovery_before_drift": signal_pattern,
            "thresholds": {
                "zero_flow_max_gpm": zero_threshold,
                "small_flow_max_gpm": small_threshold,
                "large_flow_gpm": large_threshold,
            },
            "risk": "No zero-point command was sent because water may be actively flowing.",
        }

    return {
        "allow_confirmation": True,
        "flow_state": flow_state,
        "summary": summary,
        "flow_stats": stats,
        "drift_evidence": drift,
        "signal_quality_recovery_before_drift": signal_pattern,
        "thresholds": {
            "zero_flow_max_gpm": zero_threshold,
            "small_flow_max_gpm": small_threshold,
            "large_flow_gpm": large_threshold,
        },
        "risk": (
            "Only run this if the pipe is actually at zero flow. If water is moving, "
            "setting zero point can bake an incorrect offset into the meter."
        ),
    }


def _call_status(func: Callable[..., dict], serial: str, token: str, anthropic_api_key: str | None) -> dict:
    try:
        return func(serial, token, anthropic_api_key=anthropic_api_key)
    except TypeError:
        return func(serial, token)


def _call_flow_analysis(
    func: Callable[..., dict],
    serial: str,
    start: int,
    end: int,
    token: str,
    *,
    display_timezone: str | None,
    anthropic_api_key: str | None,
    network_type: str | None,
    meter_timezone: str | None,
) -> dict:
    large = _env_float("BLUEBOT_ZERO_POINT_LARGE_FLOW_GPM", 1.0)
    event_predicates = [
        {"name": "low_signal_quality", "predicate": "quality < 60", "min_duration_seconds": 60},
        {"name": "large_positive_flow", "predicate": f"flow > {large}", "min_duration_seconds": 30},
    ]
    try:
        return func(
            serial,
            start,
            end,
            token,
            display_timezone=display_timezone,
            anthropic_api_key=anthropic_api_key,
            network_type=network_type,
            meter_timezone=meter_timezone,
            analysis_mode="summary",
            event_predicates=event_predicates,
        )
    except TypeError:
        return func(serial, start, end, token)


def prepare_zero_point_confirmation_inputs(
    inp: dict,
    token: str,
    *,
    profile_lookup: Callable[..., dict] = get_meter_profile,
    status_lookup: Callable[..., dict] = check_meter_status,
    flow_analysis_lookup: Callable[..., dict] = analyze_flow_data,
    now_fn: Callable[[], float] = time.time,
    display_timezone: str | None = None,
    anthropic_api_key: str | None = None,
) -> dict:
    serial = str(inp.get("serial_number") or "").strip()
    if not serial:
        return {"success": False, "error": "serial_number is required for set_zero_point."}

    profile_raw = profile_lookup(serial, token)
    profile = profile_raw if isinstance(profile_raw, dict) else {}
    network_type = profile.get("network_type")
    prof = profile.get("profile") if isinstance(profile.get("profile"), dict) else {}
    meter_timezone = prof.get("deviceTimeZone") if isinstance(prof, dict) else None

    status_result = _call_status(status_lookup, serial, token, anthropic_api_key)
    if not status_result.get("success"):
        return {
            "success": False,
            "error": status_result.get("error") or "Could not read current meter status before set-zero-point.",
            "evidence_results": [
                {"tool_name": "check_meter_status", "input": {"serial_number": serial}, "result": status_result},
            ],
        }

    end = int(now_fn())
    window_s = max(900, _env_int("BLUEBOT_ZERO_POINT_HISTORY_SECONDS", 6 * 3600))
    start = end - window_s
    flow_input = {
        "serial_number": serial,
        "start": start,
        "end": end,
        "network_type": network_type,
        "meter_timezone": meter_timezone,
        "analysis_mode": "summary",
    }
    flow_result = _call_flow_analysis(
        flow_analysis_lookup,
        serial,
        start,
        end,
        token,
        display_timezone=display_timezone,
        anthropic_api_key=anthropic_api_key,
        network_type=network_type if isinstance(network_type, str) else None,
        meter_timezone=meter_timezone if isinstance(meter_timezone, str) else None,
    )
    verdict = evaluate_zero_point_preflight(
        status_result=status_result,
        flow_result=flow_result,
    )
    evidence_results = [
        {"tool_name": "check_meter_status", "input": {"serial_number": serial}, "result": status_result},
        {"tool_name": "analyze_flow_data", "input": flow_input, "result": flow_result},
    ]
    if not verdict.get("allow_confirmation"):
        return {
            "success": False,
            "error": verdict.get("summary") or "Set-zero-point preflight did not pass.",
            "preflight": verdict,
            "evidence_results": evidence_results,
        }

    current_values = {
        "serial_number": serial,
        "profile_success": profile.get("success") if isinstance(profile, dict) else None,
        "network_type": network_type,
        "label": prof.get("label") if isinstance(prof, dict) else None,
        "timezone": meter_timezone,
        "change_type": "set_zero_point",
        "zero_point_preflight": verdict,
    }
    if isinstance(profile, dict) and not profile.get("success"):
        current_values["profile_error"] = profile.get("error")
    inputs = {
        "serial_number": serial,
        "action": "set_zero_point",
        "mqtt_payload": {"szv": "null"},
    }
    workflow_updates = {
        "workflow_type": "zero_point_command",
        "message": "Review and confirm only if the pipe is physically at zero flow.",
        "preflight_summary": verdict.get("summary"),
        "flow_state": verdict.get("flow_state"),
        "risk": verdict.get("risk"),
    }
    return {
        "success": True,
        "inputs": inputs,
        "current_values": current_values,
        "workflow_updates": workflow_updates,
        "preflight": verdict,
        "evidence_results": evidence_results,
    }


def set_zero_point(
    serial_number: str,
    token: str,
    *,
    action: str | None = None,
    mqtt_payload: dict | None = None,
    anthropic_api_key: str | None = None,
) -> dict:
    """
    Run pipe-configuration-agent --zero-point and return its report.

    ``action`` and ``mqtt_payload`` are accepted so the confirmed pending action
    can include explicit UI/audit context without changing the device command.
    """
    _ = (action, mqtt_payload)
    serial_number = str(serial_number or "").strip()
    if not serial_number:
        return {"success": False, "report": None, "error": "serial_number is required."}

    env = tool_subprocess_env(token, anthropic_api_key)
    result = run_pipe_configuration_agent(
        [
            _PYTHON,
            "main.py",
            "--serial",
            serial_number,
            "--zero-point",
        ],
        cwd=_AGENT_DIR,
        env=env,
    )
    if result.returncode == 0:
        return {
            "success": True,
            "report": (result.stdout or "").strip(),
            "error": None,
            "command": "set_zero_point",
            "mqtt_payload": {"szv": "null"},
        }
    return {
        "success": False,
        "report": None,
        "error": subprocess_error_message(result),
        "command": "set_zero_point",
        "mqtt_payload": {"szv": "null"},
    }
