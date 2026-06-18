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

from shared.subprocess_env import tool_subprocess_env


_STATUS_JSON_MARKER = "__BLUEBOT_STATUS_JSON__"
_DEFAULT_STATUS_AGENT_TIMEOUT_SECONDS = 30.0


def _status_agent_timeout_seconds() -> float:
    raw = os.environ.get("BLUEBOT_METER_STATUS_AGENT_TIMEOUT_SECONDS", "")
    if not raw.strip():
        return _DEFAULT_STATUS_AGENT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_STATUS_AGENT_TIMEOUT_SECONDS


def _captured_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _value_text(value: Any) -> str:
    return "unknown" if value in (None, "") else str(value)


def _deterministic_report(
    serial_number: str,
    status_data: dict,
    *,
    timeout_seconds: float | None = None,
) -> str:
    staleness = status_data.get("staleness") if isinstance(status_data.get("staleness"), dict) else {}
    signal = status_data.get("signal") if isinstance(status_data.get("signal"), dict) else {}
    pipe = status_data.get("pipe_config") if isinstance(status_data.get("pipe_config"), dict) else {}
    health = status_data.get("health_score") if isinstance(status_data.get("health_score"), dict) else {}
    note = ""
    if timeout_seconds is not None:
        note = (
            "\n\nNote: deterministic status facts were available, but the "
            f"meter-status summary agent timed out after {timeout_seconds:g}s."
        )
    pipe_bits = [
        f"nominal size {_value_text(pipe.get('nominal_size'))}",
        f"inner diameter {_value_text(pipe.get('inner_diameter_mm'))} mm",
    ]
    if pipe.get("pipe_standard"):
        pipe_bits.append(f"standard {pipe.get('pipe_standard')}")
    signal_bits = [
        f"score {_value_text(signal.get('score'))}",
        f"level {_value_text(signal.get('level'))}",
        f"reliable {_value_text(signal.get('reliable'))}",
    ]
    return (
        f"# Meter Status Report\n\n"
        f"Serial: {serial_number}\n\n"
        f"- Online: {_value_text(status_data.get('online'))}\n"
        f"- Last message: {_value_text(status_data.get('last_message_at'))}\n"
        f"- Communication: {_value_text(staleness.get('communication_status'))}"
        f" ({_value_text(staleness.get('status_description'))})\n"
        f"- Signal: {', '.join(signal_bits)}\n"
        f"- Pipe: {', '.join(pipe_bits)}\n"
        f"- Health: score {_value_text(health.get('score'))}, "
        f"verdict {_value_text(health.get('verdict'))}"
        f"{note}"
    )


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
        "pipe configuration (inner/outer diameter, wall thickness), and a "
        "composite health score. "
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
    verified_facts: dict | None = None,
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
            "health_score": {"score": float, "verdict": "healthy"|"degraded"|"unhealthy", ...} | None,
            "errors": {"signal": "KeyError: ...", ...}  # per-processor failures, empty on full success
        }
    """
    env = tool_subprocess_env(token, anthropic_api_key)
    if isinstance(verified_facts, dict):
        try:
            env["BLUEBOT_VERIFIED_FACTS_JSON"] = json.dumps(verified_facts, sort_keys=True)
        except (TypeError, ValueError):
            pass
    timeout_seconds = _status_agent_timeout_seconds()
    try:
        result = subprocess.run(
            [_PYTHON, "main.py", "--serial", serial_number],
            cwd=_AGENT_DIR,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _captured_text(exc.stderr)
        status_data = _collect_status_data(stderr)
        if status_data:
            return {
                "success": True,
                "report": _deterministic_report(
                    serial_number,
                    status_data,
                    timeout_seconds=timeout_seconds,
                ),
                "status_data": status_data,
                "error": (
                    "Meter status summary timed out; returned deterministic "
                    "status facts instead."
                ),
                "timed_out": True,
            }
        return {
            "success": False,
            "report": None,
            "status_data": None,
            "error": (
                f"Meter status agent timed out after {timeout_seconds:g}s."
            ),
            "timed_out": True,
        }
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
