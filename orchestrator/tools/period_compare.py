"""
period_compare.py — Compare one meter across two time windows.

This is an orchestrator-level composition tool: it reuses ``analyze_flow_data``
for each window, then reads the machine-readable analysis bundles to compute
deterministic period-B-minus-period-A deltas.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from tools.flow_analysis import analyze_flow_data, _coerce_unix_seconds


TOOL_DEFINITION: dict[str, Any] = {
    "name": "compare_periods",
    "description": (
        "Compare one meter's flow analysis across two explicit time windows. "
        "Use when the user asks period-over-period questions such as 'compare "
        "this week to last week', 'before vs after', or 'period A vs period B'. "
        "Always call resolve_time_range first for relative windows. Returns "
        "period summaries plus deterministic deltas computed as period_b minus "
        "period_a."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": "Meter serial number to compare.",
            },
            "period_a": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "Unix seconds, UTC."},
                    "end": {"type": "integer", "description": "Unix seconds, UTC."},
                },
                "required": ["start", "end"],
                "description": "Reference or earlier comparison window.",
            },
            "period_b": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "Unix seconds, UTC."},
                    "end": {"type": "integer", "description": "Unix seconds, UTC."},
                },
                "required": ["start", "end"],
                "description": "Comparison window; deltas are period_b minus period_a.",
            },
            "network_type": {
                "type": "string",
                "enum": ["wifi", "lorawan", "unknown"],
                "description": "Optional meter network category for sampling physics.",
            },
            "meter_timezone": {
                "type": "string",
                "description": "Optional IANA meter timezone for plots and local grouping.",
            },
        },
        "required": ["serial_number", "period_a", "period_b"],
    },
}


def _coerce_window(name: str, raw: object) -> tuple[int, int]:
    if not isinstance(raw, dict):
        raise TypeError(f"{name} must be an object with start/end")
    start = _coerce_unix_seconds(f"{name}.start", raw.get("start"))
    end = _coerce_unix_seconds(f"{name}.end", raw.get("end"))
    if start > end:
        raise ValueError(f"{name}.start ({start}) must be <= {name}.end ({end})")
    return start, end


def _load_verified_facts(result: dict) -> tuple[dict | None, str | None]:
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


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _quality_ratio(facts: dict) -> float | None:
    sq = facts.get("signal_quality") if isinstance(facts, dict) else None
    if not isinstance(sq, dict):
        return None
    flagged = _safe_float(sq.get("flagged_count"))
    total = _safe_float(sq.get("total_count"))
    if flagged is None or total is None or total <= 0:
        return None
    return flagged / total


def _metrics_from_facts(facts: dict, *, start: int, end: int) -> dict:
    duration_hours = max((int(end) - int(start)) / 3600.0, 1e-9)
    desc = facts.get("flow_rate_descriptive") if isinstance(facts.get("flow_rate_descriptive"), dict) else {}
    volume = facts.get("flow_volume") if isinstance(facts.get("flow_volume"), dict) else {}
    gap_count = int(facts.get("gap_event_count") or 0)
    return {
        "volume_gallons": _safe_float(volume.get("total_volume_gallons")),
        "mean_flow": _safe_float(desc.get("mean")),
        "peak_count": int(facts.get("peak_count") or 0),
        "gap_rate_per_hour": float(gap_count) / duration_hours,
        "low_quality_ratio": _quality_ratio(facts),
    }


def _delta(b: float | int | None, a: float | int | None) -> float | None:
    if a is None or b is None:
        return None
    return float(b) - float(a)


def _pct_delta(b: float | None, a: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return ((b - a) / a) * 100.0


def _period_payload(
    label: str,
    result: dict,
    *,
    start: int,
    end: int,
) -> dict:
    facts, err = _load_verified_facts(result) if result.get("success") else (None, result.get("error"))
    metrics = _metrics_from_facts(facts, start=start, end=end) if facts is not None else None
    return {
        "label": label,
        "success": bool(result.get("success")) and facts is not None,
        "start": int(start),
        "end": int(end),
        "display_range": result.get("display_range"),
        "plot_timezone": result.get("plot_timezone"),
        "analysis_json_path": result.get("analysis_json_path"),
        "report_path": result.get("report_path"),
        "metrics": metrics,
        "error": err,
    }


def compare_periods(
    serial_number: str,
    period_a: dict,
    period_b: dict,
    token: str,
    *,
    display_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    network_type: str | None = None,
    meter_timezone: str | None = None,
) -> dict:
    """Run two flow analyses and compute period-B-minus-period-A deltas."""
    if not token:
        return {
            "success": False,
            "serial_number": serial_number,
            "periods": {},
            "deltas": None,
            "error": "Bearer token required.",
        }
    try:
        a_start, a_end = _coerce_window("period_a", period_a)
        b_start, b_end = _coerce_window("period_b", period_b)
    except (TypeError, ValueError) as exc:
        return {
            "success": False,
            "serial_number": serial_number,
            "periods": {},
            "deltas": None,
            "error": str(exc),
        }

    def _run(start: int, end: int) -> dict:
        return analyze_flow_data(
            serial_number,
            start,
            end,
            token,
            display_timezone=display_timezone,
            anthropic_api_key=anthropic_api_key,
            network_type=network_type,
            meter_timezone=meter_timezone,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_run, a_start, a_end)
        fut_b = pool.submit(_run, b_start, b_end)
        result_a = fut_a.result()
        result_b = fut_b.result()

    period_a_payload = _period_payload("period_a", result_a, start=a_start, end=a_end)
    period_b_payload = _period_payload("period_b", result_b, start=b_start, end=b_end)
    periods = {"period_a": period_a_payload, "period_b": period_b_payload}

    if not (period_a_payload["success"] and period_b_payload["success"]):
        return {
            "success": False,
            "serial_number": serial_number,
            "periods": periods,
            "deltas": None,
            "error": "One or both period analyses failed.",
        }

    a = period_a_payload["metrics"] or {}
    b = period_b_payload["metrics"] or {}
    deltas = {
        "volume_delta_gallons": _delta(b.get("volume_gallons"), a.get("volume_gallons")),
        "volume_delta_pct": _pct_delta(b.get("volume_gallons"), a.get("volume_gallons")),
        "mean_flow_delta": _delta(b.get("mean_flow"), a.get("mean_flow")),
        "peak_count_delta": int(b.get("peak_count", 0)) - int(a.get("peak_count", 0)),
        "gap_rate_delta": _delta(b.get("gap_rate_per_hour"), a.get("gap_rate_per_hour")),
        "low_quality_ratio_delta": _delta(b.get("low_quality_ratio"), a.get("low_quality_ratio")),
    }

    return {
        "success": True,
        "serial_number": serial_number,
        "periods": periods,
        "deltas": deltas,
        "error": None,
    }
