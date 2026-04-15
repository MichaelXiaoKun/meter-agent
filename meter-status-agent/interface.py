"""
Leaf Agent Interface

Callable entry point for orchestrator integration.

Example:
    from meter_status_agent.interface import run

    result = run(serial_number="BB8100015261", token="...")
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
    serial_number: str,
    token: Optional[str] = None,
) -> dict:
    """
    Fetch and analyse the current status of a flow meter device.

    Never raises — all errors are captured in result["error"].

    Args:
        serial_number:  Meter serial number (e.g. "BB8100015261")
        token:          Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        {
            "success":        bool,
            "serial_number":  str,
            "online":         bool | None,
            "report":         str | None,
            "error":          str | None,
        }
    """
    base = {"serial_number": serial_number}

    try:
        status = fetch_meter_status(serial_number, token=token)
        analysis = analyze(status, serial_number)
        report = format_report(analysis, serial_number)

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
