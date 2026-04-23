"""
meter_status.py — Orchestrator tool wrapper for the meter-status-agent.

Runs the meter-status-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import os
import subprocess
import sys

from subprocess_env import tool_subprocess_env


def _stderr_for_user(stderr: str, returncode: int) -> str:
    """
    Subprocess stderr may contain log lines plus a Python traceback.
    Keep a short, user-safe message for tool_result / timeline (no stack frames).
    """
    raw = (stderr or "").strip()
    if not raw:
        return f"Meter status agent exited with code {returncode}."
    if "Traceback (most recent call last)" in raw:
        for line in reversed(raw.splitlines()):
            t = line.strip()
            if not t or t.startswith("^"):
                continue
            # Final "SomeError: message" line after trace frames
            if ": " in t:
                head = t.split(":", 1)[0]
                if "Error" in head or head.endswith("Exception"):
                    return t[:600]
        return "Meter status failed (unexpected error; check server logs)."
    return raw[:600]

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


def check_meter_status(
    serial_number: str,
    token: str,
    *,
    anthropic_api_key: str | None = None,
) -> dict:
    """
    Run the meter-status-agent for a meter (by serial number) and return its report.

    Returns:
        {"success": bool, "report": str | None, "error": str | None}
    """
    env = tool_subprocess_env(token, anthropic_api_key)
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
        "error": _stderr_for_user(result.stderr, result.returncode),
    }
