"""
batch_flow_analysis.py — Fan-out wrapper for analyze_flow_data over N meters.

Runs the data-processing-agent subprocess for each serial concurrently
(ThreadPoolExecutor), then returns compact per-meter results in one payload.

Use this instead of N serial analyze_flow_data calls when the user wants to
compare flow health / trends across multiple meters over the same time window
("compare these 5 meters' flow last week", "which meter peaks the highest?").

Per-meter reports are truncated more aggressively than single-meter analysis
(BATCH_REPORT_MAX_CHARS) to keep the total context payload manageable. If the
user needs a full report for one meter, route that to analyze_flow_data directly.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tools.flow_analysis import analyze_flow_data, _coerce_unix_seconds

logger = logging.getLogger(__name__)

_MIN_SERIALS = 2
_MAX_SERIALS = 8
_MAX_WORKERS = 4  # subprocess-heavy; keep modest to avoid CPU saturation
_BATCH_REPORT_MAX_CHARS = 3_000  # per-meter — tighter than single-meter default


TOOL_DEFINITION: dict[str, Any] = {
    "name": "batch_analyze_flow",
    "description": (
        "Analyse historical flow data for 2–8 meters over the same time range "
        "in parallel. Use when the user wants to compare flow trends, peaks, gaps, "
        "or usage across multiple meters "
        "('compare flow for these 5 meters', 'which meter had the highest demand "
        "last week', 'show me all meters' flow over the weekend'). "
        "Always call resolve_time_range first for relative time expressions. "
        "Returns per-meter reports and plot paths in one round trip. "
        "Prefer this over N separate analyze_flow_data calls when serials share "
        "the same time range — it is faster and uses fewer context tokens."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 8,
                "description": "List of 2–8 serial numbers to analyse in parallel.",
            },
            "start": {
                "type": "integer",
                "description": "Range start as Unix timestamp (seconds, UTC).",
            },
            "end": {
                "type": "integer",
                "description": "Range end as Unix timestamp (seconds, UTC).",
            },
            "network_type": {
                "type": "string",
                "enum": ["wifi", "lorawan", "unknown"],
                "description": (
                    "Applied to all meters. Omit if the meters have mixed network "
                    "types — gap detection will use conservative defaults."
                ),
            },
        },
        "required": ["serial_numbers", "start", "end"],
    },
}


def batch_analyze_flow(
    serial_numbers: list[str],
    start: int | str | float,
    end: int | str | float,
    token: str,
    *,
    display_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    network_type: str | None = None,
) -> dict:
    """
    Run analyze_flow_data for multiple serials concurrently.

    Returns:
        {
            "success":       bool,         # True if at least one meter succeeded
            "display_range": str,          # wall-time range from first success
            "meters": [
                {
                    "serial_number":    str,
                    "success":          bool,
                    "report":           str | None,   # truncated to _BATCH_REPORT_MAX_CHARS
                    "report_truncated": bool,
                    "plot_paths":       list[str],
                    "plot_summaries":   list[dict],
                    "analysis_json_path": str | None,
                    "report_path": str | None,
                    "analysis_mode": str | None,
                    "reasoning_schema": dict | None,
                    "analysis_details": dict,
                    "analysis_metadata": dict,
                    "download_artifacts": list[dict],
                    "plot_timezone":    str,
                    "error":            str | None,
                },
                ...
            ],
            "failed_serials": list[str] | None,
        }
    """
    cleaned = [s.strip() for s in serial_numbers if isinstance(s, str) and s.strip()]
    if len(cleaned) < _MIN_SERIALS:
        return {
            "success": False,
            "error": f"batch_analyze_flow requires at least {_MIN_SERIALS} serial numbers.",
            "meters": [],
            "failed_serials": None,
        }
    if len(cleaned) > _MAX_SERIALS:
        logger.warning("batch_analyze_flow: trimming %d serials to %d", len(cleaned), _MAX_SERIALS)
        cleaned = cleaned[:_MAX_SERIALS]

    try:
        start_i = _coerce_unix_seconds("start", start)
        end_i = _coerce_unix_seconds("end", end)
    except (TypeError, ValueError) as exc:
        return {
            "success": False,
            "error": str(exc),
            "meters": [],
            "failed_serials": None,
        }

    results_map: dict[str, dict] = {}

    def _run_one(sn: str) -> None:
        try:
            results_map[sn] = analyze_flow_data(
                sn,
                start_i,
                end_i,
                token,
                display_timezone=display_timezone,
                anthropic_api_key=anthropic_api_key,
                network_type=network_type,
            )
        except Exception as exc:
            results_map[sn] = {
                "success": False,
                "report": None,
                "report_truncated": False,
                "plot_paths": [],
                "plot_summaries": [],
                "reasoning_schema": None,
                "analysis_details": {},
                "analysis_metadata": {},
                "analysis_mode": None,
                "analysis_json_path": None,
                "report_path": None,
                "download_artifacts": [],
                "display_range": "",
                "plot_timezone": "UTC",
                "error": f"{type(exc).__name__}: {exc}",
            }

    n_workers = min(len(cleaned), _MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = {pool.submit(_run_one, sn): sn for sn in cleaned}
        for f in as_completed(futs):
            f.result()  # exceptions are captured inside _run_one; this is a safety net

    meters: list[dict] = []
    failed_serials: list[str] = []
    display_range = ""

    for sn in cleaned:  # preserve caller-supplied order
        r = results_map.get(sn) or {
            "success": False,
            "error": "no result produced",
            "plot_paths": [],
            "plot_summaries": [],
        }
        ok = bool(r.get("success"))
        if not ok:
            failed_serials.append(sn)
        if not display_range and r.get("display_range"):
            display_range = r["display_range"]

        report = r.get("report") or None
        truncated = bool(r.get("report_truncated"))
        if report and len(report) > _BATCH_REPORT_MAX_CHARS:
            cut = report[: _BATCH_REPORT_MAX_CHARS - 90].rstrip()
            report = cut + "\n\n…*(truncated — call analyze_flow_data for the full report)*"
            truncated = True

        meters.append({
            "serial_number": sn,
            "success": ok,
            "report": report,
            "report_truncated": truncated,
            "plot_paths": r.get("plot_paths", []),
            "plot_summaries": r.get("plot_summaries", []),
            "reasoning_schema": r.get("reasoning_schema"),
            "analysis_details": r.get("analysis_details", {}),
            "analysis_metadata": r.get("analysis_metadata", {}),
            "analysis_mode": r.get("analysis_mode"),
            "analysis_json_path": r.get("analysis_json_path"),
            "report_path": r.get("report_path"),
            "download_artifacts": r.get("download_artifacts", []),
            "plot_timezone": r.get("plot_timezone", "UTC"),
            "error": r.get("error"),
        })

    return {
        "success": any(m["success"] for m in meters),
        "display_range": display_range,
        "meters": meters,
        "failed_serials": failed_serials or None,
    }
