"""
meter_status.py — Orchestrator tool wrapper for the meter-status-agent.

Runs the meter-status-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import os
import subprocess
import sys

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "meter-status-agent")
)

# Use the agent's own venv Python if it exists, else use the current interpreter.
_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

TOOL_DEFINITION = {
    "name": "check_meter_status",
    "description": (
        "Fetch the current health status of a bluebot flow meter: "
        "online/offline state, signal quality score, time since last message, "
        "and pipe configuration (inner/outer diameter, wall thickness). "
        "Use when the user asks about meter health, connectivity, or current state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": (
                    "Serial number for the status API path — use the exact string the user "
                    "provided (e.g. BB8100015261)."
                ),
            }
        },
        "required": ["serial_number"],
    },
}


def check_meter_status(serial_number: str, token: str) -> dict:
    """
    Run the meter-status-agent for a meter (by serial number) and return its report.

    Returns:
        {"success": bool, "report": str | None, "error": str | None}
    """
    env = {**os.environ, "BLUEBOT_TOKEN": token}
    result = subprocess.run(
        [_PYTHON, "main.py", "--serial", serial_number],
        cwd=_AGENT_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return {"success": True, "report": result.stdout.strip(), "error": None}
    return {
        "success": False,
        "report": None,
        "error": result.stderr.strip() or f"Process exited with code {result.returncode}",
    }
