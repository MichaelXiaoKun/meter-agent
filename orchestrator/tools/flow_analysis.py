"""
flow_analysis.py — Orchestrator tool wrapper for the data-processing-agent.

Runs the data-processing-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import json
import os
import re
import subprocess
import sys

from processors.time_range import display_tz_name_for_user, format_unix_range_display
from subprocess_env import tool_subprocess_env

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data-processing-agent")
)

# Use the agent's own venv Python if it exists, else use the current interpreter.
_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

_PLOT_PATHS_MARKER = "__BLUEBOT_PLOT_PATHS__"

_TRUNCATION_NOTE = "\n\n…*(Report truncated for length; increase `BLUEBOT_FLOW_REPORT_MAX_CHARS` if needed.)*"


def _flow_report_max_chars() -> int:
    raw = os.environ.get("BLUEBOT_FLOW_REPORT_MAX_CHARS", "10000")
    try:
        n = int(raw)
    except ValueError:
        return 10000
    return n if n > 0 else 0


def _maybe_truncate_report(text: str) -> tuple[str, bool]:
    limit = _flow_report_max_chars()
    if limit <= 0 or len(text) <= limit:
        return text, False
    budget = max(0, limit - len(_TRUNCATION_NOTE))
    cut = text[:budget]
    nl = cut.rfind("\n\n")
    if nl > budget * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + _TRUNCATION_NOTE, True


def _collect_plot_paths(report: str, stderr: str, agent_dir: str) -> list[str]:
    """
    Prefer machine-emitted paths from the subprocess stderr; fall back to markdown
    in the report with resolution under agent_dir/plots/.
    """
    seen: set[str] = set()
    out: list[str] = []

    if stderr:
        idx = stderr.find(_PLOT_PATHS_MARKER)
        if idx != -1:
            tail = stderr[idx + len(_PLOT_PATHS_MARKER) :].strip()
            line = tail.splitlines()[0] if tail else ""
            try:
                data = json.loads(line)
                if isinstance(data, list):
                    for p in data:
                        # Trust subprocess output; do not require isfile() here (avoids
                        # dropping paths on FS races or symlink quirks — GET /api/plots
                        # still validates the file exists).
                        if (
                            isinstance(p, str)
                            and p.endswith(".png")
                            and ".." not in p
                            and "\x00" not in p
                            and p not in seen
                        ):
                            seen.add(p)
                            out.append(p)
            except json.JSONDecodeError:
                pass
    if out:
        return out

    plots_dir = os.path.join(agent_dir, "plots")
    for raw in re.findall(r"!\[.*?\]\((.*?\.png)\)", report):
        raw = raw.strip()
        if not raw:
            continue
        candidates = [raw]
        if not os.path.isabs(raw):
            candidates.append(os.path.join(plots_dir, os.path.basename(raw)))
            candidates.append(os.path.join(agent_dir, raw.lstrip(os.sep)))
        for c in candidates:
            ap = os.path.abspath(c)
            if os.path.isfile(ap) and ap not in seen:
                seen.add(ap)
                out.append(ap)
                break
    return out


TOOL_DEFINITION = {
    "name": "analyze_flow_data",
    "description": (
        "Analyse historical flow rate data for a device over a time range. "
        "Computes descriptive statistics, detects gaps, zero-flow periods, peaks, "
        "trend direction, and flags low signal-quality readings. "
        "Always call resolve_time_range first when the user expresses the time range "
        "in natural language (e.g. 'last 6 hours', 'yesterday morning'). "
        "The tool result includes display_range: server-formatted wall times for the "
        "start/end Unix seconds — cite that for human-readable times, not your own conversion."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": (
                    "Serial number for the high-res flow API path — use the exact string "
                    "the user provided."
                ),
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
        "required": ["serial_number", "start", "end"],
    },
}


def analyze_flow_data(
    serial_number: str,
    start: int,
    end: int,
    token: str,
    *,
    display_timezone: str | None = None,
    anthropic_api_key: str | None = None,
) -> dict:
    """
    Run the data-processing-agent for a meter (by serial number) over a time range.

    Returns:
        {
            "success":           bool,
            "report":            str | None,   # Markdown (may be truncated — see report_truncated)
            "report_truncated":  bool,         # True if report was shortened for token/length limits
            "plot_paths":        list[str],     # absolute PNG paths embedded in the report
            "display_range": str,          # wall times for start/end (user TZ when set)
            "error":         str | None,
        }
    """
    tz_name = display_tz_name_for_user(display_timezone)
    display_range = format_unix_range_display(start, end, tz_name=tz_name)
    env = tool_subprocess_env(token, anthropic_api_key)
    result = subprocess.run(
        [
            _PYTHON, "main.py",
            "--serial", serial_number,
            "--start", str(start),
            "--end", str(end),
        ],
        cwd=_AGENT_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        raw_report = result.stdout.strip()
        plot_paths = _collect_plot_paths(raw_report, result.stderr or "", _AGENT_DIR)
        report, truncated = _maybe_truncate_report(raw_report)
        if truncated:
            plot_paths = _collect_plot_paths(report, result.stderr or "", _AGENT_DIR)
        return {
            "success": True,
            "report": report,
            "report_truncated": truncated,
            "plot_paths": plot_paths,
            "display_range": display_range,
            "error": None,
        }
    return {
        "success": False,
        "report": None,
        "report_truncated": False,
        "plot_paths": [],
        "display_range": display_range,
        "error": result.stderr.strip() or f"Process exited with code {result.returncode}",
    }
