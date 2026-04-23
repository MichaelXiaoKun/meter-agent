"""
flow_analysis.py — Orchestrator tool wrapper for the data-processing-agent.

Runs the data-processing-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import json
import logging
import math
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

from processors.time_range import display_tz_name_for_user, format_unix_range_display
from subprocess_env import tool_subprocess_env
from tools.plot_tz import resolve_plot_tz_name as _resolve_plot_tz_name

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data-processing-agent")
)

# Use the agent's own venv Python if it exists, else use the current interpreter.
_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

_PLOT_PATHS_MARKER = "__BLUEBOT_PLOT_PATHS__"
_ANALYSIS_JSON_MARKER = "__BLUEBOT_ANALYSIS_JSON__"

_TRUNCATION_NOTE = "\n\n…*(Report truncated for length; increase `BLUEBOT_FLOW_REPORT_MAX_CHARS` if needed.)*"

# Human-readable titles — keep in sync with ``frontend/src/plotLabels.ts`` for UX parity.
_PLOT_TYPE_TITLES: dict[str, str] = {
    "time_series": "Flow rate (time series)",
    "flow_duration_curve": "Flow duration curve",
    "peaks_annotated": "Demand peaks",
    "signal_quality": "Signal quality",
}


def _plot_summaries(plot_paths: list[str], plot_tz: str) -> list[dict]:
    """
    Per-file metadata for the React UI (captions / alt text). Order matches
    ``plot_paths`` so the client can zip arrays without guessing.
    """
    out: list[dict] = []
    for p in plot_paths:
        name = os.path.basename(p)
        if not name.lower().endswith(".png"):
            continue
        stem = name[:-4]
        parts = stem.split("_")
        if len(parts) >= 3:
            # Filenames are ``{serial}_{unix_start}_{plot_type}``; plot_type may
            # contain underscores (e.g. ``time_series``).
            plot_type = "_".join(parts[2:])
            title = _PLOT_TYPE_TITLES.get(
                plot_type,
                plot_type.replace("_", " ").title(),
            )
        else:
            plot_type = "unknown"
            title = "Analysis plot"
        out.append(
            {
                "filename": name,
                "plot_type": plot_type,
                "title": title,
                "plot_timezone": plot_tz,
            }
        )
    return out


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


def _collect_analysis_json_path(stderr: str) -> str | None:
    """Absolute path written by data-processing-agent main.py (machine-readable bundle)."""
    if not stderr:
        return None
    idx = stderr.find(_ANALYSIS_JSON_MARKER)
    if idx == -1:
        return None
    tail = stderr[idx + len(_ANALYSIS_JSON_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
        if isinstance(data, dict) and isinstance(data.get("path"), str):
            p = data["path"]
            if isinstance(p, str) and ".." not in p and "\x00" not in p:
                return p
    except json.JSONDecodeError:
        pass
    return None


TOOL_DEFINITION = {
    "name": "analyze_flow_data",
    "description": (
        "Analyse historical flow rate data for a device over a time range. "
        "Computes descriptive statistics, detects gaps, zero-flow periods, peaks, "
        "trend direction, and flags low signal-quality readings. "
        "Always call resolve_time_range first when the user expresses the time range "
        "in natural language (e.g. 'last 6 hours', 'yesterday morning'). "
        "When available, call get_meter_profile first and pass the resulting "
        "``network_type`` (``wifi`` ≈ 2 s cadence, ``lorawan`` ≈ 12–60 s cadence) "
        "so gap detection and coverage expectations match the meter's physics. "
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
            "network_type": {
                "type": "string",
                "enum": ["wifi", "lorawan", "unknown"],
                "description": (
                    "Meter network category from get_meter_profile. Tunes the sampling "
                    "caps used by gap detection and coverage: ``wifi`` ≈ 5 s healthy "
                    "inter-arrival cap (~2 s cadence), ``lorawan``/``unknown`` ≈ 60 s cap "
                    "(12–60 s bursty cadence). Omit if unknown."
                ),
            },
            "meter_timezone": {
                "type": "string",
                "description": (
                    "IANA timezone of the meter (e.g. ``America/Denver``). Pass the "
                    "``deviceTimeZone`` field returned by get_meter_profile so plot "
                    "x-axes render in the meter's local clock (matching the verified-"
                    "facts report). Falls back to the user's browser timezone, then UTC."
                ),
            },
        },
        "required": ["serial_number", "start", "end"],
    },
}


def analyze_flow_inputs_error_payload(
    inputs: object,
    *,
    display_timezone: str | None = None,
) -> dict | None:
    """
    If ``analyze_flow_data`` cannot run because required fields are missing or
    empty, return the same error-shaped dict as a failed analysis run.

    Otherwise return ``None``. Used by the orchestrator so a malformed tool
    call does not surface as a bare ``KeyError`` (e.g. ``'start'``) to the UI.
    """
    issues: list[str] = []
    if not isinstance(inputs, dict):
        issues.append("tool input must be a JSON object")
    else:
        sn = inputs.get("serial_number")
        if sn is None or (isinstance(sn, str) and not str(sn).strip()):
            issues.append("serial_number is missing or empty")
        for k in ("start", "end"):
            if k not in inputs:
                issues.append(f"{k} is missing")
            else:
                v = inputs[k]
                if v is None:
                    issues.append(f"{k} is null")
                elif isinstance(v, str) and not str(v).strip():
                    issues.append(f"{k} is empty")
    if not issues:
        return None
    tz_name = display_tz_name_for_user(display_timezone)
    meter_tz = inputs.get("meter_timezone") if isinstance(inputs, dict) else None
    plot_tz = _resolve_plot_tz_name(
        meter_timezone=meter_tz,
        display_timezone=tz_name,
    )
    hint = (
        "Call resolve_time_range first when the user gave a relative range "
        "(e.g. last 12 hours), then pass the returned Unix start/end here."
    )
    err = (
        "Invalid analyze_flow_data input — "
        + "; ".join(issues)
        + ". "
        + hint
    )
    return {
        "success": False,
        "report": None,
        "report_truncated": False,
        "plot_paths": [],
        "plot_summaries": [],
        "analysis_json_path": None,
        "display_range": "",
        "plot_timezone": plot_tz,
        "error": err,
    }


_ALLOWED_NETWORK_TYPES = {"wifi", "lorawan", "unknown"}


def _normalize_network_type(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if v in _ALLOWED_NETWORK_TYPES else None


def _coerce_unix_seconds(field: str, value: object) -> int:
    """
    Tool APIs sometimes deliver JSON numbers as strings. ``datetime.fromtimestamp`` and
    the data-processing CLI require real ints; reject bools (``True`` is an ``int`` in Python).
    """
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a Unix timestamp in seconds, not a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError(f"{field} is empty")
        return int(float(s))
    raise TypeError(
        f"{field} must be a number (Unix seconds, UTC); got {type(value).__name__!r}"
    )


def analyze_flow_data(
    serial_number: str,
    start: int | str | float,
    end: int | str | float,
    token: str,
    *,
    display_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    network_type: str | None = None,
    meter_timezone: str | None = None,
) -> dict:
    """
    Run the data-processing-agent for a meter (by serial number) over a time range.

    Returns:
        {
            "success":           bool,
            "report":            str | None,   # Markdown (may be truncated — see report_truncated)
            "report_truncated":  bool,         # True if report was shortened for token/length limits
            "plot_paths":        list[str],     # absolute PNG paths embedded in the report
            "plot_summaries":  list[dict],   # one entry per plot_paths item (filename, title, tz, type)
            "analysis_json_path": str | None, # absolute path to analysis_*.json (verified_facts bundle)
            "display_range": str,          # wall times for start/end (user TZ when set)
            "plot_timezone": str,          # IANA zone the plot x-axes were rendered in
            "error":         str | None,
        }
    """
    try:
        start = _coerce_unix_seconds("start", start)
        end = _coerce_unix_seconds("end", end)
    except (TypeError, ValueError) as e:
        err = str(e)
        tz_name = display_tz_name_for_user(display_timezone)
        plot_tz = _resolve_plot_tz_name(
            meter_timezone=meter_timezone, display_timezone=tz_name
        )
        return {
            "success": False,
            "report": None,
            "report_truncated": False,
            "plot_paths": [],
            "plot_summaries": [],
            "analysis_json_path": None,
            "display_range": "",
            "plot_timezone": plot_tz,
            "error": err,
        }
    if start > end:
        err = f"start ({start}) must be <= end ({end})"
        tz_name = display_tz_name_for_user(display_timezone)
        plot_tz = _resolve_plot_tz_name(
            meter_timezone=meter_timezone, display_timezone=tz_name
        )
        return {
            "success": False,
            "report": None,
            "report_truncated": False,
            "plot_paths": [],
            "plot_summaries": [],
            "analysis_json_path": None,
            "display_range": "",
            "plot_timezone": plot_tz,
            "error": err,
        }
    tz_name = display_tz_name_for_user(display_timezone)
    display_range = format_unix_range_display(start, end, tz_name=tz_name)
    plot_tz = _resolve_plot_tz_name(
        meter_timezone=meter_timezone, display_timezone=tz_name
    )
    env = tool_subprocess_env(token, anthropic_api_key)
    nt = _normalize_network_type(network_type)
    if nt:
        env["BLUEBOT_METER_NETWORK_TYPE"] = nt
    env["BLUEBOT_PLOT_TZ"] = plot_tz
    logger.info(
        "analyze_flow_data subprocess start serial=%r start=%s end=%s",
        serial_number,
        start,
        end,
    )
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
        stderr = result.stderr or ""
        plot_paths = _collect_plot_paths(raw_report, stderr, _AGENT_DIR)
        report, truncated = _maybe_truncate_report(raw_report)
        if truncated:
            plot_paths = _collect_plot_paths(report, stderr, _AGENT_DIR)
        summaries = _plot_summaries(plot_paths, plot_tz)
        logger.info(
            "analyze_flow_data ok serial=%r returncode=0 plots=%s report_truncated=%s",
            serial_number,
            len(plot_paths),
            truncated,
        )
        return {
            "success": True,
            "report": report,
            "report_truncated": truncated,
            "plot_paths": plot_paths,
            "plot_summaries": summaries,
            "analysis_json_path": _collect_analysis_json_path(stderr),
            "display_range": display_range,
            "plot_timezone": plot_tz,
            "error": None,
        }
    err_text = result.stderr.strip() or f"Process exited with code {result.returncode}"
    # Subprocess stderr was only in the tool JSON; surface it in server logs for ops debugging.
    _tail = err_text if len(err_text) <= 8000 else f"{err_text[:8000]}\n…[stderr truncated for log]"
    logger.error(
        "analyze_flow_data failed serial=%r start=%s end=%s returncode=%s python=%s cwd=%s\n%s",
        serial_number,
        start,
        end,
        result.returncode,
        _PYTHON,
        _AGENT_DIR,
        _tail,
    )
    # Uvicorn/reload can swallow or split app loggers; mirror to uvicorn.error + raw stderr.
    logging.getLogger("uvicorn.error").error(
        "BBOT analyze_flow_data failed serial=%r returncode=%s",
        serial_number,
        result.returncode,
    )
    print(
        f"BBOT_FLOW_FAIL serial={serial_number!r} returncode={result.returncode} start={start} end={end}\n{_tail}",
        file=sys.stderr,
        flush=True,
    )
    return {
        "success": False,
        "report": None,
        "report_truncated": False,
        "plot_paths": [],
        "plot_summaries": [],
        "analysis_json_path": None,
        "display_range": display_range,
        "plot_timezone": plot_tz,
        "error": err_text,
    }
