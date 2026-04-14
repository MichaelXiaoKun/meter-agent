"""
time_range.py — Natural language → Unix timestamp resolver.

Converts a user's time description into start/end Unix timestamps (seconds)
that can be passed directly to the data-processing-agent interface.

Uses claude-haiku to parse the expression so it handles the full range of
natural language: relative durations, specific dates, range syntax ("X to Y"),
inline timezones, and foreign languages — without any regex maintenance.

Timezone behaviour:
    If the user does not specify a timezone, the system's local timezone is
    used. The current local time is injected into the system prompt so the
    LLM can anchor expressions like "today" and "this morning" correctly.
    Output Unix timestamps are always UTC-based (as required by the API).
"""

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic


def _resolve_display_tz(name: str):
    """
    Return a tzinfo for display strings.

    - "" or "UTC" → UTC
    - "local" (case-insensitive) → the process local zone (same as ``datetime.now().astimezone()``;
      respects the host ``TZ`` env on Unix and system settings on macOS/Windows)
    - any other string → IANA name via ZoneInfo, or UTC on failure
    """
    raw = name.strip()
    if not raw or raw.upper() == "UTC":
        return timezone.utc
    if raw.lower() == "local":
        tz = datetime.now().astimezone().tzinfo
        return tz if tz is not None else timezone.utc
    try:
        return ZoneInfo(raw)
    except Exception:
        return timezone.utc


def format_unix_range_display(
    start: int,
    end: int,
    *,
    tz_name: str | None = None,
) -> str:
    """
    Format an inclusive [start, end] Unix-seconds range for logs and tool output.

    Wall times are computed in Python (not by the LLM). tz_name defaults to the
    DISPLAY_TZ environment variable, or UTC if unset or invalid.

    Set DISPLAY_TZ=local to use the machine/container local timezone (often UTC on
    cloud hosts unless TZ is configured). Otherwise use an IANA name, e.g.
    America/New_York.
    """
    name = tz_name if tz_name is not None else os.environ.get("DISPLAY_TZ") or "UTC"
    tz = _resolve_display_tz(name)

    def _one(ts: int) -> str:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

    return f"{_one(start)} → {_one(end)} (Unix {start}–{end})"

# ---------------------------------------------------------------------------
# Tool schema — registered in the orchestrator agent's TOOLS list
# ---------------------------------------------------------------------------

TOOL_DEFINITION = {
    "name": "resolve_time_range",
    "description": (
        "Convert a natural language time description into precise start and end "
        "Unix timestamps (integer seconds, UTC) suitable for querying flow data. "
        "Call this tool whenever the user expresses a time range in words before "
        "calling analyze_flow_data. "
        "Examples: 'last 6 hours', 'yesterday', 'this morning', '3 hours ago', "
        "'April 8 midnight to April 9 midnight EDT', 'past 30 minutes'. "
        "The result includes display_range (server-formatted wall times) and resolved_label; "
        "prefer display_range when quoting times to the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "Natural language time range as stated by the user. "
                    "Pass the phrase verbatim; do not paraphrase."
                ),
            },
        },
        "required": ["description"],
    },
}

# ---------------------------------------------------------------------------
# Fast-path: exact Python arithmetic for "last/past N <unit>" patterns.
# Avoids LLM arithmetic errors for the most common relative expressions.
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(
    r"^\s*(?:last|past)\s+(\d+(?:\.\d+)?)\s+"
    r"(second|minute|hour|day|week|month)s?\s*$",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour":   3_600,
    "day":    86_400,
    "week":   604_800,
    "month":  2_592_000,   # 30 days
}


def _try_relative_fast_path(description: str, now: datetime) -> dict | None:
    """
    Return a resolved dict for simple 'last/past N <unit>' expressions,
    or None if the description doesn't match.
    """
    m = _RELATIVE_RE.match(description)
    if not m:
        return None

    amount = float(m.group(1))
    unit   = m.group(2).lower()
    delta  = timedelta(seconds=amount * _UNIT_SECONDS[unit])

    end_dt   = now
    start_dt = now - delta

    label = (
        f"{start_dt.strftime('%Y-%m-%d %H:%M %Z')} → "
        f"{end_dt.strftime('%Y-%m-%d %H:%M %Z')}"
    )
    start_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    return {
        "start":          start_s,
        "end":            end_s,
        "resolved_label": label,
        "display_range":  format_unix_range_display(start_s, end_s),
        "error":          None,
    }


# ---------------------------------------------------------------------------
# Internal extraction tool — forced output schema for Haiku
#
# The LLM returns ISO 8601 strings (not Unix integers) because LLMs are
# reliable at natural language → calendar date conversion but unreliable at
# Unix timestamp arithmetic. Python's datetime.fromisoformat().timestamp()
# handles the final conversion exactly.
# ---------------------------------------------------------------------------

_EXTRACT_TOOL = {
    "name": "extract_time_range",
    "description": "Extract start and end of a time range as ISO 8601 datetime strings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "start_iso": {
                "type": "string",
                "description": (
                    "Range start as ISO 8601 with UTC offset. "
                    "Example: '2026-04-08T00:00:00-04:00'"
                ),
            },
            "end_iso": {
                "type": "string",
                "description": (
                    "Range end as ISO 8601 with UTC offset. "
                    "Example: '2026-04-09T00:00:00-04:00'"
                ),
            },
        },
        "required": ["start_iso", "end_iso"],
    },
}


# ---------------------------------------------------------------------------
# Public function — called by the orchestrator's tool dispatcher
# ---------------------------------------------------------------------------

def resolve_time_range(
    description: str,
    reference_timestamp: int | None = None,
) -> dict:
    """
    Parse a natural language time description into Unix timestamps.

    Args:
        description:         Natural language time range from the user.
        reference_timestamp: Optional "now" anchor (Unix seconds).
                             Defaults to the actual current local time.

    Returns:
        {
            "start":           int,        # Unix timestamp, seconds (inclusive)
            "end":             int,        # Unix timestamp, seconds (inclusive)
            "resolved_label":  str | None, # Human-readable confirmation string
            "display_range":   str | None, # Server-formatted wall times (DISPLAY_TZ / UTC)
            "error":           str | None, # Set when parsing fails
        }
    """
    now = (
        datetime.fromtimestamp(reference_timestamp).astimezone()
        if reference_timestamp is not None
        else datetime.now().astimezone()
    )

    fast = _try_relative_fast_path(description, now)
    if fast is not None:
        return fast

    tz_name   = now.strftime("%Z")
    now_local = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    now_utc   = now.astimezone(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    system_prompt = (
        f"You are a time range parser. Convert the user's time expression into Unix timestamps.\n\n"
        f"Reference times:\n"
        f"  Local : {now_local}\n"
        f"  UTC   : {now_utc}\n\n"
        f"Rules:\n"
        f"  - If no timezone is specified, use the local timezone ({tz_name}).\n"
        f"  - 'today' means from local midnight to now.\n"
        f"  - 'yesterday' means the full previous calendar day in local time.\n"
        f"  - For specific dates without a year, assume the current year.\n"
        f"  - Always return UTC-based Unix timestamps regardless of input timezone.\n"
        f"  - For open-ended expressions like '6 hours ago', set end = now."
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=system_prompt,
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_time_range"},
            messages=[{"role": "user", "content": description}],
        )

        result = response.content[0].input

        # Convert ISO strings → Unix timestamps in Python (exact arithmetic).
        start_dt = datetime.fromisoformat(result["start_iso"])
        end_dt   = datetime.fromisoformat(result["end_iso"])

        label = (
            f"{start_dt.astimezone().strftime('%Y-%m-%d %H:%M %Z')} → "
            f"{end_dt.astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
        )

        start_s = int(start_dt.timestamp())
        end_s = int(end_dt.timestamp())
        return {
            "start":          start_s,
            "end":            end_s,
            "resolved_label": label,
            "display_range":  format_unix_range_display(start_s, end_s),
            "error":          None,
        }

    except Exception as exc:
        return {
            "start":          None,
            "end":            None,
            "resolved_label": None,
            "display_range":  None,
            "error":          f"Could not parse time description '{description}': {exc}",
        }
