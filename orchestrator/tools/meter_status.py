"""
meter_status.py — Orchestrator tool wrapper for the meter-status-agent.

Runs the meter-status-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.

The subprocess emits a ``__BLUEBOT_STATUS_JSON__<json>`` line on stderr with
the deterministic processor output (staleness / signal quality / pipe config).
We parse that out so callers get both the human-readable Markdown report and
a structured dict they can diff / sort / filter without re-parsing text.
"""

import json
import os
import subprocess
import sys
from typing import Any

from subprocess_env import tool_subprocess_env


_STATUS_JSON_MARKER = "__BLUEBOT_STATUS_JSON__"


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


def _collect_status_data(stderr: str) -> dict | None:
    """Parse the ``__BLUEBOT_STATUS_JSON__`` marker out of subprocess stderr.

    Returns None when the marker is missing or malformed. Marker format is one
    stderr line: the literal marker followed by a single ``json.dumps(...)``
    payload with no embedded newlines (same convention as the
    ``__BLUEBOT_ANALYSIS_JSON__`` marker in ``tools/flow_analysis.py``).
    """
    if not stderr:
        return None
    idx = stderr.find(_STATUS_JSON_MARKER)
    if idx == -1:
        return None
    tail = stderr[idx + len(_STATUS_JSON_MARKER):].strip()
    line = tail.splitlines()[0] if tail else ""
    if not line:
        return None
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


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
        {
            "success":     bool,
            "report":      str | None,    # Markdown report (LLM output)
            "status_data": dict | None,   # structured processor output; may be
                                          # present even when success is False
                                          # (e.g. if the LLM step failed after
                                          # the fetch succeeded)
            "error":       str | None,
        }

    ``status_data`` shape (on success):
        {
            "serial_number": str,
            "online": bool | None,
            "last_message_at": str | None,
            "staleness": {"seconds_since": int, "communication_status": str, ...} | None,
            "signal": {"score": int, "level": str, "reliable": bool, ...} | None,
            "pipe_config": {"inner_diameter_mm": float, "nominal_size": str|None, ...} | None,
            "errors": {"signal": "KeyError: ...", ...}  # per-processor failures, empty on full success
        }
    """
    env = tool_subprocess_env(token, anthropic_api_key)
    result = subprocess.run(
        [_PYTHON, "main.py", "--serial", serial_number],
        cwd=_AGENT_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    status_data = _collect_status_data(result.stderr)
    if result.returncode == 0:
        return {
            "success": True,
            "report": result.stdout.strip(),
            "status_data": status_data,
            "error": None,
        }
    return {
        "success": False,
        "report": None,
        "status_data": status_data,
        "error": _stderr_for_user(result.stderr, result.returncode),
    }
