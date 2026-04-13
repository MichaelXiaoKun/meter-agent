"""
Leaf Agent Interface

This is the canonical entry point for orchestrator integration.
Import and call `run()` directly — no CLI, no subprocess, no HTTP needed.

Example (from an orchestrator):
    from data_processing_agent.interface import run

    result = run(
        device_id="BB8100015261",
        start=1775588400,
        end=1775590200,
        token="...",
    )
    if result["success"]:
        print(result["report"])
    else:
        print(result["error"])
"""

import traceback
from typing import Optional

from data_client import fetch_flow_data_range
from agent import analyze
from report import format_report
from processors.plots import pop_figures


def run(
    device_id: str,
    start: int,
    end: int,
    token: Optional[str] = None,
) -> dict:
    """
    Fetch, process, and analyse flow rate data for a device over a time range.

    This is the single callable contract exposed to orchestrators.
    All errors are caught and returned in the result dict — this function
    never raises, so orchestrators can call it safely without try/except.

    Args:
        device_id:  Device identifier (e.g. "BB8100015261")
        start:      Range start as Unix timestamp (seconds, inclusive)
        end:        Range end as Unix timestamp (seconds, inclusive)
        token:      bluebot Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        {
            "success":     bool,
            "device_id":   str,
            "start":       int,
            "end":         int,
            "data_points": int | None,   # number of rows fetched
            "report":      str | None,   # full Markdown report
            "error":       str | None,   # populated only on failure
        }
    """
    base = {"device_id": device_id, "start": start, "end": end}

    try:
        df = fetch_flow_data_range(device_id, start, end, token=token, verbose=False)
        analysis = analyze(df, device_id)
        plot_paths = [path for _, path in pop_figures()]
        report = format_report(analysis, device_id, start, end)

        return {
            **base,
            "success": True,
            "data_points": len(df),
            "report": report,
            "plot_paths": plot_paths,
            "error": None,
        }

    except Exception as exc:
        return {
            **base,
            "success": False,
            "data_points": None,
            "report": None,
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }
