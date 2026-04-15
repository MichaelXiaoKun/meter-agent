"""
time_range.py — Natural language → Unix timestamp resolver.

Converts a user's time description into start/end Unix timestamps (seconds)
that can be passed directly to the data-processing-agent interface.

Uses claude-haiku to parse the expression so it handles the full range of
natural language: relative durations, specific dates, range syntax ("X to Y"),
inline timezones, and foreign languages — without any regex maintenance.

Timezone behaviour:
    Callers may pass ``user_timezone`` (IANA name, e.g. from the browser's
    ``Intl.DateTimeFormat().resolvedOptions().timeZone``). When set, ambiguous
    phrases ("today", "last night", calendar dates without offset) are interpreted
    in that zone. When omitted, the process host's local zone is used (often UTC on
    cloud servers — prefer passing the client zone from the API). If the user
    explicitly names a timezone in their words, the parser should honor it instead.
    Output Unix timestamps are always UTC-based (as required by the API).
"""

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic


def _safe_zoneinfo(name: str | None):
    """Return ZoneInfo for a valid IANA name, else None."""
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s:
        return None
    try:
        return ZoneInfo(s)
    except Exception:
        return None


def display_tz_name_for_user(user_timezone: str | None) -> str | None:
    """Return stripped IANA name if valid, else None (for format_unix_range_display)."""
    return user_timezone.strip() if _safe_zoneinfo(user_timezone) else None


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
        "The server resolves ambiguous dates in the user's local timezone when available; "
        "if the user names a specific timezone in their message, honor that instead. "
        "The result includes display_range (wall times) and resolved_label; "
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
    *,
    user_timezone: str | None = None,
) -> dict:
    """
    Parse a natural language time description into Unix timestamps.

    Args:
        description: Natural language time range from the user.
        reference_timestamp: Optional "now" anchor (Unix seconds). Interpreted as an
            instant; the active zone for display/anchoring follows user_timezone or host.
        user_timezone: Optional IANA name (e.g. ``America/Chicago`` from the browser).
            When set, ambiguous phrases use this zone unless the user explicitly names
            another timezone in *description*.

    Returns:
        {
            "start":           int,        # Unix timestamp, seconds (inclusive)
            "end":             int,        # Unix timestamp, seconds (inclusive)
            "resolved_label":  str | None, # Human-readable confirmation string
            "display_range":   str | None, # Wall times (user zone or DISPLAY_TZ / UTC)
            "error":           str | None, # Set when parsing fails
        }
    """
    utz = _safe_zoneinfo(user_timezone)
    _disp = user_timezone if utz else None

    if reference_timestamp is not None:
        aware_utc = datetime.fromtimestamp(reference_timestamp, tz=timezone.utc)
        if utz:
            now = aware_utc.astimezone(utz)
        else:
            now = aware_utc.astimezone()
    elif utz:
        now = datetime.now(utz)
    else:
        now = datetime.now().astimezone()

    fast = _try_relative_fast_path(description, now)
    if fast is not None:
        fast["display_range"] = format_unix_range_display(
            fast["start"], fast["end"], tz_name=_disp
        )
        return fast

    if utz:
        tz_rules = (
            f"If the user does not specify a timezone in their words, interpret calendar dates, "
            f"'today', 'yesterday', 'this morning', and local clock times in **{user_timezone}** (IANA).\n"
            f"  - If they explicitly ask for a different zone (e.g. UTC, EST, 'London time', or ISO with a fixed offset), use that instead for their expression.\n"
        )
        tz_name_short = now.strftime("%Z")
    else:
        tz_rules = (
            f"If the user does not specify a timezone, use the host's local zone ({now.tzinfo!s}).\n"
            f"  - If they explicitly ask for a specific timezone, use that instead.\n"
        )
        tz_name_short = now.strftime("%Z")

    now_local = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    now_utc = now.astimezone(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    system_prompt = (
        f"You are a time range parser. Convert the user's time expression into precise wall times, "
        f"then emit ISO 8601 with offsets for the extract_time_range tool.\n\n"
        f"Reference times (anchor for 'now' and relative phrases):\n"
        f"  Primary local : {now_local}  (zone key: {tz_name_short})\n"
        f"  UTC            : {now_utc}\n\n"
        f"Rules:\n"
        f"{tz_rules}"
        f"  - 'today' means from local midnight to now in the active zone above.\n"
        f"  - 'yesterday' means the full previous calendar day in that zone.\n"
        f"  - For specific dates without a year, assume the current year in that zone.\n"
        f"  - Always return ISO datetimes with explicit UTC offsets so Unix timestamps are exact.\n"
        f"  - For open-ended expressions like '6 hours ago', set end = the anchor 'now' above."
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
        end_dt = datetime.fromisoformat(result["end_iso"])

        start_s = int(start_dt.timestamp())
        end_s = int(end_dt.timestamp())

        z_disp = utz if utz else (datetime.now().astimezone().tzinfo or timezone.utc)
        start_wall = datetime.fromtimestamp(start_s, tz=timezone.utc).astimezone(z_disp)
        end_wall = datetime.fromtimestamp(end_s, tz=timezone.utc).astimezone(z_disp)
        label = (
            f"{start_wall.strftime('%Y-%m-%d %H:%M %Z')} → "
            f"{end_wall.strftime('%Y-%m-%d %H:%M %Z')}"
        )

        return {
            "start":          start_s,
            "end":            end_s,
            "resolved_label": label,
            "display_range":  format_unix_range_display(start_s, end_s, tz_name=_disp),
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
