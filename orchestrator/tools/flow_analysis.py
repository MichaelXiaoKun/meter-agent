"""
flow_analysis.py — Orchestrator tool wrapper for the data-processing-agent.

Runs the data-processing-agent as a subprocess using its own virtual environment
if present, otherwise falls back to the current Python interpreter.
"""

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
from collections import OrderedDict
from copy import deepcopy

logger = logging.getLogger(__name__)

from processors.time_range import display_tz_name_for_user, format_unix_range_display
from shared.subprocess_env import tool_subprocess_env
from tools.plot_tz import resolve_plot_tz_name as _resolve_plot_tz_name

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data-processing-agent")
)

# Use the agent's own venv Python if it exists, else use the current interpreter.
_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

_PLOT_PATHS_MARKER = "__BLUEBOT_PLOT_PATHS__"
_ANALYSIS_JSON_MARKER = "__BLUEBOT_ANALYSIS_JSON__"
_PLOT_CAPTIONS_MARKER = "__BLUEBOT_PLOT_CAPTIONS__"
_REASONING_SCHEMA_MARKER = "__BLUEBOT_REASONING_SCHEMA__"
_ANALYSIS_DETAILS_MARKER = "__BLUEBOT_ANALYSIS_DETAILS__"
_ANALYSIS_METADATA_MARKER = "__BLUEBOT_ANALYSIS_METADATA__"
_DOWNLOAD_ARTIFACTS_MARKER = "__BLUEBOT_DOWNLOAD_ARTIFACTS__"

_TRUNCATION_NOTE = "\n\n…*(Report truncated for length; increase `BLUEBOT_FLOW_REPORT_MAX_CHARS` if needed.)*"

# Human-readable titles — keep in sync with ``frontend/src/plotLabels.ts`` for UX parity.
_PLOT_TYPE_TITLES: dict[str, str] = {
    "time_series": "Flow rate (time series)",
    "flow_duration_curve": "Flow duration curve",
    "peaks_annotated": "Demand peaks",
    "signal_quality": "Signal quality",
    "diagnostic_timeline": "Diagnostic timeline",
}

# 3A TimeseriesContext result cache: avoids rerunning the specialist subprocess
# when the same high-res window is requested repeatedly in a short session.
# Cache key includes result-shaping optional inputs (baseline window and
# local-time filters) so follow-up requests do not silently reuse stale output.
_ResultCacheKey = tuple[
    str,
    int,
    int,
    str,
    str | None,
    str,
    str,
    str,
    tuple[int, int] | None,
    str | None,
    str | None,
]
_RESULT_CACHE: OrderedDict[_ResultCacheKey, dict] = OrderedDict()
_RESULT_CACHE_RESOLUTION = "high-res"


def _result_cache_max_entries() -> int:
    raw = os.environ.get("BLUEBOT_FLOW_RESULT_CACHE_SIZE", "16")
    try:
        n = int(raw)
    except ValueError:
        return 16
    return max(0, n)


def _token_cache_scope(token: str | None) -> str:
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _result_cache_get(
    key: _ResultCacheKey,
) -> dict | None:
    if _result_cache_max_entries() <= 0:
        return None
    hit = _RESULT_CACHE.get(key)
    if hit is None:
        return None
    _RESULT_CACHE.move_to_end(key)
    return deepcopy(hit)


def _result_cache_put(
    key: _ResultCacheKey,
    value: dict,
) -> None:
    max_entries = _result_cache_max_entries()
    if max_entries <= 0:
        _RESULT_CACHE.clear()
        return
    _RESULT_CACHE[key] = deepcopy(value)
    _RESULT_CACHE.move_to_end(key)
    while len(_RESULT_CACHE) > max_entries:
        _RESULT_CACHE.popitem(last=False)


def clear_result_cache() -> None:
    """Test/ops hook: clear cached analyze_flow_data results."""
    _RESULT_CACHE.clear()


def _plot_summaries(
    plot_paths: list[str],
    plot_tz: str,
    plot_captions: dict[str, dict] | None = None,
) -> list[dict]:
    """
    Per-file metadata for the React UI (captions / alt text). Order matches
    ``plot_paths`` so the client can zip arrays without guessing.

    When ``plot_captions`` is provided (keyed by absolute path), the structured
    caption from the data-processing-agent is attached under ``"caption"`` so
    the outer LLM can cite the visual evidence without reading raster pixels.
    """
    caps = plot_captions or {}
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
        entry = {
            "filename": name,
            "plot_type": plot_type,
            "title": title,
            "plot_timezone": plot_tz,
        }
        cap = caps.get(p)
        if isinstance(cap, dict) and cap:
            entry["caption"] = cap
        out.append(entry)
    return out


def _flow_report_max_chars(analysis_mode: str | None = None) -> int:
    env_name = (
        "BLUEBOT_FLOW_SUMMARY_REPORT_MAX_CHARS"
        if analysis_mode == "summary"
        else "BLUEBOT_FLOW_REPORT_MAX_CHARS"
    )
    default = "5000" if analysis_mode == "summary" else "10000"
    raw = os.environ.get(env_name, default)
    try:
        n = int(raw)
    except ValueError:
        return int(default)
    return n if n > 0 else 0


def _maybe_truncate_report(text: str, analysis_mode: str | None = None) -> tuple[str, bool]:
    limit = _flow_report_max_chars(analysis_mode)
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


def _collect_plot_captions(stderr: str) -> dict[str, dict]:
    """Per-plot structured captions emitted alongside __BLUEBOT_PLOT_PATHS__."""
    if not stderr:
        return {}
    idx = stderr.find(_PLOT_CAPTIONS_MARKER)
    if idx == -1:
        return {}
    tail = stderr[idx + len(_PLOT_CAPTIONS_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, dict] = {}
    for k, v in data.items():
        if (
            isinstance(k, str)
            and isinstance(v, dict)
            and ".." not in k
            and "\x00" not in k
        ):
            clean[k] = v
    return clean


def _collect_reasoning_schema(stderr: str) -> dict | None:
    """Compact evidence/hypothesis/next_checks block emitted by the subprocess."""
    if not stderr:
        return None
    idx = stderr.find(_REASONING_SCHEMA_MARKER)
    if idx == -1:
        return None
    tail = stderr[idx + len(_REASONING_SCHEMA_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _collect_analysis_details(stderr: str) -> dict:
    """Small processor summaries emitted for the activity timeline."""
    if not stderr:
        return {}
    idx = stderr.find(_ANALYSIS_DETAILS_MARKER)
    if idx == -1:
        return {}
    tail = stderr[idx + len(_ANALYSIS_DETAILS_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _collect_analysis_metadata(stderr: str) -> dict:
    """Small execution metadata emitted by data-processing-agent main.py."""
    if not stderr:
        return {}
    idx = stderr.find(_ANALYSIS_METADATA_MARKER)
    if idx == -1:
        return {}
    tail = stderr[idx + len(_ANALYSIS_METADATA_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _collect_download_artifacts(stderr: str) -> list[dict]:
    """Downloadable analysis artifacts emitted by data-processing-agent main.py."""
    if not stderr:
        return []
    idx = stderr.find(_DOWNLOAD_ARTIFACTS_MARKER)
    if idx == -1:
        return []
    tail = stderr[idx + len(_DOWNLOAD_ARTIFACTS_MARKER) :].strip()
    line = tail.splitlines()[0] if tail else ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        filename = item.get("filename")
        path = item.get("path")
        if kind != "csv" or not isinstance(filename, str) or not filename.endswith(".csv"):
            continue
        if not isinstance(path, str) or ".." in path or "\x00" in path:
            continue
        clean = {
            "kind": "csv",
            "title": item.get("title") if isinstance(item.get("title"), str) else "Flow data CSV",
            "filename": filename,
            "path": path,
        }
        if isinstance(item.get("row_count"), int):
            clean["row_count"] = item["row_count"]
        out.append(clean)
    return out


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
            "analysis_mode": {
                "type": "string",
                "enum": ["auto", "detailed", "summary"],
                "description": (
                    "auto (default) uses deterministic summary for long or very large "
                    "windows; detailed forces the full internal LLM analysis loop; "
                    "summary forces the compact deterministic rollup path."
                ),
            },
            "baseline_window": {
                "description": (
                    "Optional reference window for an 'is this normal?' / "
                    "'vs typical' comparison. Pass either a semantic key "
                    "(``\"auto\"`` ⇒ trailing 28 days, ``\"trailing_7_days\"``, "
                    "``\"trailing_28_days\"``, ``\"prior_week\"``) or an explicit "
                    "object ``{\"start\": <unix_seconds>, \"end\": <unix_seconds>}``. "
                    "When set, the meter analysis builds local-tz daily rollups over "
                    "the reference window and runs the baseline-quality refusal "
                    "evaluator. The result includes ``baseline_quality`` "
                    "(state ∈ no_history | insufficient_clean_days | "
                    "regime_change_too_recent | partial_today_unsuitable | reliable) "
                    "and, when reliable, a ``today_vs_baseline`` block. Omit when "
                    "the user is not asking a comparative question."
                ),
                "oneOf": [
                    {
                        "type": "string",
                        "enum": [
                            "auto",
                            "trailing_7_days",
                            "trailing_28_days",
                            "prior_week",
                        ],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "integer",
                                "description": "Reference window start (Unix seconds, UTC).",
                            },
                            "end": {
                                "type": "integer",
                                "description": "Reference window end (Unix seconds, UTC).",
                            },
                        },
                        "required": ["start", "end"],
                    },
                ],
            },
            "filters": {
                "type": "object",
                "description": (
                    "Optional DST-aware local-time and sub-range filter. Use when "
                    "the user asks for a scoped analysis such as weekdays only, "
                    "business hours, weekends, exclude holidays, or specific "
                    "sub-ranges. Local rules require an IANA ``timezone``. "
                    "``weekdays`` uses integers with 0=Mon and 6=Sun. "
                    "``hour_ranges`` are local, non-overnight ``[start_hour, "
                    "end_hour)`` spans; split overnight requests into two ranges. "
                    "``exclude_dates`` are ``YYYY-MM-DD`` local dates. "
                    "``include_sub_ranges`` are Unix-second ``[start, end)`` "
                    "intervals."
                ),
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for local weekday/hour/date rules.",
                    },
                    "weekdays": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 6},
                        "minItems": 1,
                        "description": "Local weekdays to keep, where 0=Monday and 6=Sunday.",
                    },
                    "hour_ranges": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_hour": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 24,
                                },
                                "end_hour": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 24,
                                },
                            },
                            "required": ["start_hour", "end_hour"],
                        },
                        "description": (
                            "Local non-overnight hour spans using [start_hour, end_hour)."
                        ),
                    },
                    "exclude_dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Local dates to exclude, formatted YYYY-MM-DD.",
                    },
                    "include_sub_ranges": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": {
                                    "type": "integer",
                                    "description": "Unix seconds, inclusive.",
                                },
                                "end": {
                                    "type": "integer",
                                    "description": "Unix seconds, exclusive.",
                                },
                            },
                            "required": ["start", "end"],
                        },
                        "description": "Explicit UTC Unix-second intervals to keep.",
                    },
                },
            },
            "event_predicates": {
                "type": "array",
                "description": (
                    "Optional threshold-event detectors to run on the analysis "
                    "series. Use when the user asks for events such as 'flow "
                    "above 10 gpm for at least 5 minutes', 'zero flow lasting "
                    "60 seconds', or 'quality below 60'. Predicate language is "
                    "small: ``flow``/``flow_rate``/``quality`` plus one of "
                    ">, >=, <, <=, ==, != and a numeric threshold."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short label for this event set.",
                        },
                        "predicate": {
                            "type": "string",
                            "description": "Predicate such as 'flow > 10' or 'quality < 60'.",
                        },
                        "min_duration_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Minimum contiguous duration required for an event.",
                        },
                    },
                    "required": ["name", "predicate", "min_duration_seconds"],
                },
            },
        },
        "required": ["serial_number", "start", "end"],
    },
}


_BASELINE_SEMANTIC_KEYS: frozenset[str] = frozenset(
    {"auto", "trailing_7_days", "trailing_28_days", "prior_week"}
)
_BASELINE_AUTO_DEFAULT = "trailing_28_days"


def resolve_baseline_window(
    spec: object,
    *,
    primary_start: int,
    primary_end: int,
) -> tuple[int, int] | None:
    """Translate the ``baseline_window`` tool input into explicit Unix bounds.

    Returns ``None`` when ``spec`` is missing, malformed, or resolves to a
    degenerate range. Centralising this here means the orchestrator agent
    code, the cache key, and the subprocess CLI args all see exactly one
    representation: a ``(start, end)`` tuple or nothing.

    Resolution rules
    ----------------
    * ``"auto"`` → :data:`_BASELINE_AUTO_DEFAULT` (``trailing_28_days``).
    * ``"trailing_7_days"`` → 7 × 86 400 s ending one second before
      ``primary_start``.
    * ``"trailing_28_days"`` → 28 × 86 400 s ending one second before
      ``primary_start``.
    * ``"prior_week"`` → exactly the 7 × 86 400 s window ending one second
      before ``primary_start`` (alias for trailing_7_days at this layer; the
      day-grouping that follows handles the "same days last week" semantics).
    * ``{"start": int, "end": int}`` → returned verbatim after coercion.
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        key = spec.strip().lower()
        if key not in _BASELINE_SEMANTIC_KEYS:
            return None
        if key == "auto":
            key = _BASELINE_AUTO_DEFAULT
        try:
            ps = int(primary_start)
        except (TypeError, ValueError):
            return None
        if key == "trailing_7_days" or key == "prior_week":
            days = 7
        elif key == "trailing_28_days":
            days = 28
        else:  # pragma: no cover - frozenset guards make this unreachable
            return None
        end = ps - 1
        start = end - days * 86400 + 1
        if start >= end:
            return None
        return (start, end)
    if isinstance(spec, dict):
        try:
            s = int(spec.get("start"))
            e = int(spec.get("end"))
        except (TypeError, ValueError):
            return None
        if s >= e:
            return None
        return (s, e)
    return None


def resolve_filters(
    spec: object,
    *,
    primary_start: int,
    primary_end: int,
) -> dict | None:
    """Return a JSON-serialisable local-time filter spec or ``None``.

    Validation of field semantics belongs to the data-processing subprocess so
    refusal states can flow through ``verified_facts["filter_applied"]``. This
    helper only handles the orchestrator boundary: missing, empty, non-object,
    or non-JSON-serialisable values are treated as "not requested".
    """
    _ = (primary_start, primary_end)
    if not isinstance(spec, dict) or not spec:
        return None
    try:
        canonical = json.dumps(spec, sort_keys=True, allow_nan=False)
        parsed = json.loads(canonical)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _filters_cache_key(filters: dict | None) -> str | None:
    if filters is None:
        return None
    try:
        return json.dumps(filters, sort_keys=True)
    except (TypeError, ValueError):
        return None


def resolve_event_predicates(
    spec: object,
    *,
    primary_start: int,
    primary_end: int,
) -> list | None:
    """Return a JSON-serialisable event-predicate list or ``None``.

    Predicate grammar validation belongs to the data-processing subprocess so
    invalid predicates can be represented in ``verified_facts``. The
    orchestrator only drops missing, empty, non-list, or non-JSON values.
    """
    _ = (primary_start, primary_end)
    if not isinstance(spec, list) or not spec:
        return None
    try:
        canonical = json.dumps(spec, sort_keys=True, allow_nan=False)
        parsed = json.loads(canonical)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) and parsed else None


def _event_predicates_cache_key(event_predicates: list | None) -> str | None:
    if event_predicates is None:
        return None
    try:
        return json.dumps(event_predicates, sort_keys=True)
    except (TypeError, ValueError):
        return None


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
        "reasoning_schema": None,
        "analysis_details": {},
        "analysis_metadata": {},
        "analysis_mode": None,
        "analysis_json_path": None,
        "report_path": None,
        "download_artifacts": [],
        "display_range": "",
        "plot_timezone": plot_tz,
        "error": err,
    }


_ALLOWED_NETWORK_TYPES = {"wifi", "lorawan", "unknown"}
_ALLOWED_ANALYSIS_MODES = {"auto", "detailed", "summary"}


def _normalize_network_type(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if v in _ALLOWED_NETWORK_TYPES else None


def _normalize_analysis_mode(value: str | None) -> str:
    if not value:
        return "auto"
    v = value.strip().lower()
    return v if v in _ALLOWED_ANALYSIS_MODES else "auto"


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
    analysis_mode: str | None = None,
    baseline_window: object | None = None,
    filters: object | None = None,
    event_predicates: object | None = None,
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
            "report_path": str | None,       # absolute markdown report artifact when emitted
            "analysis_mode": str | None,     # resolved mode from the subprocess
            "analysis_metadata": dict,       # fetch/mode metadata
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
            "reasoning_schema": None,
            "analysis_details": {},
            "analysis_json_path": None,
            "report_path": None,
            "download_artifacts": [],
            "analysis_mode": None,
            "analysis_metadata": {},
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
            "reasoning_schema": None,
            "analysis_details": {},
            "analysis_json_path": None,
            "report_path": None,
            "download_artifacts": [],
            "analysis_mode": None,
            "analysis_metadata": {},
            "display_range": "",
            "plot_timezone": plot_tz,
            "error": err,
        }
    tz_name = display_tz_name_for_user(display_timezone)
    display_range = format_unix_range_display(start, end, tz_name=tz_name)
    plot_tz = _resolve_plot_tz_name(
        meter_timezone=meter_timezone, display_timezone=tz_name
    )
    nt = _normalize_network_type(network_type)
    mode = _normalize_analysis_mode(analysis_mode)
    baseline_bounds = resolve_baseline_window(
        baseline_window,
        primary_start=int(start),
        primary_end=int(end),
    )
    resolved_filters = resolve_filters(
        filters,
        primary_start=int(start),
        primary_end=int(end),
    )
    filters_key = _filters_cache_key(resolved_filters)
    resolved_event_predicates = resolve_event_predicates(
        event_predicates,
        primary_start=int(start),
        primary_end=int(end),
    )
    event_predicates_key = _event_predicates_cache_key(resolved_event_predicates)
    cache_key = (
        str(serial_number),
        int(start),
        int(end),
        _RESULT_CACHE_RESOLUTION,
        nt,
        plot_tz,
        _token_cache_scope(token),
        mode,
        baseline_bounds,
        filters_key,
        event_predicates_key,
    )
    cached = _result_cache_get(cache_key)
    if cached is not None:
        logger.info(
            "analyze_flow_data cache hit serial=%r start=%s end=%s",
            serial_number,
            start,
            end,
        )
        return cached

    env = tool_subprocess_env(token, anthropic_api_key)
    if nt:
        env["BLUEBOT_METER_NETWORK_TYPE"] = nt
    env["BLUEBOT_PLOT_TZ"] = plot_tz
    if resolved_filters is not None and filters_key is not None:
        env["BLUEBOT_FILTERS_JSON"] = filters_key
    if resolved_event_predicates is not None and event_predicates_key is not None:
        env["BLUEBOT_EVENT_PREDICATES_JSON"] = event_predicates_key
    logger.info(
        "analyze_flow_data subprocess start serial=%r start=%s end=%s",
        serial_number,
        start,
        end,
    )
    cmd = [
        _PYTHON, "main.py",
        "--serial", serial_number,
        "--start", str(start),
        "--end", str(end),
        "--analysis-mode", mode,
    ]
    if baseline_bounds is not None:
        bs, be = baseline_bounds
        cmd.extend(["--baseline-start", str(int(bs)), "--baseline-end", str(int(be))])
    result = subprocess.run(
        cmd,
        cwd=_AGENT_DIR,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        raw_report = result.stdout.strip()
        stderr = result.stderr or ""
        analysis_metadata = _collect_analysis_metadata(stderr)
        resolved_mode = analysis_metadata.get("analysis_mode")
        plot_paths = _collect_plot_paths(raw_report, stderr, _AGENT_DIR)
        report, truncated = _maybe_truncate_report(
            raw_report,
            resolved_mode if isinstance(resolved_mode, str) else mode,
        )
        if truncated:
            plot_paths = _collect_plot_paths(report, stderr, _AGENT_DIR)
        plot_captions = _collect_plot_captions(stderr)
        summaries = _plot_summaries(plot_paths, plot_tz, plot_captions=plot_captions)
        reasoning_schema = _collect_reasoning_schema(stderr)
        analysis_details = _collect_analysis_details(stderr)
        report_path = analysis_metadata.get("report_path")
        download_artifacts = _collect_download_artifacts(stderr)
        if not download_artifacts:
            meta_artifacts = analysis_metadata.get("download_artifacts")
            if isinstance(meta_artifacts, list):
                download_artifacts = [
                    a for a in meta_artifacts if isinstance(a, dict)
                ]
        logger.info(
            "analyze_flow_data ok serial=%r returncode=0 plots=%s report_truncated=%s mode=%s",
            serial_number,
            len(plot_paths),
            truncated,
            resolved_mode or mode,
        )
        payload = {
            "success": True,
            "report": report,
            "report_truncated": truncated,
            "plot_paths": plot_paths,
            "plot_summaries": summaries,
            "reasoning_schema": reasoning_schema,
            "analysis_details": analysis_details,
            "analysis_metadata": analysis_metadata,
            "analysis_mode": resolved_mode or mode,
            "analysis_json_path": _collect_analysis_json_path(stderr),
            "report_path": report_path if isinstance(report_path, str) else None,
            "download_artifacts": download_artifacts,
            "display_range": display_range,
            "plot_timezone": plot_tz,
            "error": None,
        }
        _result_cache_put(cache_key, payload)
        return payload
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
        "reasoning_schema": None,
        "analysis_details": {},
        "analysis_metadata": {},
        "analysis_mode": None,
        "analysis_json_path": None,
        "report_path": None,
        "download_artifacts": [],
        "display_range": display_range,
        "plot_timezone": plot_tz,
        "error": err_text,
    }
