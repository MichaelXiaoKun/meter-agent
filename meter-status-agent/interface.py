"""
Leaf Agent Interface

Callable entry point for orchestrator integration.

Example:
    from meter_status_agent.interface import run

    result = run(device_id="BB8100015261", token="...")
    if result["success"]:
        print(result["report"])
    else:
        print(result["error"])
"""

import traceback
from typing import Optional

from data_client import fetch_meter_status
from agent import analyze
from report import format_report


def run(
    device_id: str,
    token: Optional[str] = None,
) -> dict:
    """
    Fetch and analyse the current status of a flow meter device.

    Never raises — all errors are captured in result["error"].

    Args:
        device_id:  Device identifier (e.g. "BB8100015261")
        token:      Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        {
            "success":    bool,
            "device_id":  str,
            "online":     bool | None,
            "report":     str | None,
            "error":      str | None,
        }
    """
    base = {"device_id": device_id}

    try:
        status = fetch_meter_status(device_id, token=token)
        analysis = analyze(status, device_id)
        report = format_report(analysis, device_id)

        return {
            **base,
            "success": True,
            "online": status.get("online"),
            "report": report,
            "error": None,
        }

    except Exception as exc:
        return {
            **base,
            "success": False,
            "online": None,
            "report": None,
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }
