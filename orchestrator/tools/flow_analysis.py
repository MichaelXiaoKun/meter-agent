"""
flow_analysis.py — Orchestrator tool wrapper for the data-processing-agent.

Runs the data-processing-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import os
import re
import subprocess
import sys

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data-processing-agent")
)

# Use the agent's own venv Python if it exists, else use the current interpreter.
_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

TOOL_DEFINITION = {
    "name": "analyze_flow_data",
    "description": (
        "Analyse historical flow rate data for a device over a time range. "
        "Computes descriptive statistics, detects gaps, zero-flow periods, peaks, "
        "trend direction, and flags low signal-quality readings. "
        "Always call resolve_time_range first when the user expresses the time range "
        "in natural language (e.g. 'last 6 hours', 'yesterday morning')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "Device identifier (e.g. BB8100015261)",
            },
            "start": {
                "type": "integer",
                "description": "Range start as Unix timestamp (seconds, UTC)",
            },
            "end": {
                "type": "integer",
                "description": "Range end as Unix timestamp (seconds, UTC)",
            },
        },
        "required": ["device_id", "start", "end"],
    },
}


def analyze_flow_data(device_id: str, start: int, end: int, token: str) -> dict:
    """
    Run the data-processing-agent for a device over a time range.

    Returns:
        {
            "success":    bool,
            "report":     str | None,   # full Markdown report text
            "plot_paths": list[str],    # absolute PNG paths embedded in the report
            "error":      str | None,
        }
    """
    env = {**os.environ, "BLUEBOT_TOKEN": token}
    result = subprocess.run(
        [
            _PYTHON, "main.py",
            "--device", device_id,
            "--start", str(start),
            "--end", str(end),
        ],
        cwd=_AGENT_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        report = result.stdout.strip()
        # Extract PNG paths embedded by the agent as Markdown images: ![...](path)
        plot_paths = [
            p for p in re.findall(r'!\[.*?\]\((.*?\.png)\)', report)
            if os.path.exists(p)
        ]
        return {"success": True, "report": report, "plot_paths": plot_paths, "error": None}
    return {
        "success": False,
        "report": None,
        "plot_paths": [],
        "error": result.stderr.strip() or f"Process exited with code {result.returncode}",
    }
