"""
plot_tz.py — pure helpers for resolving the IANA timezone the data-
processing-agent should render plot x-axes in.

Kept dependency-free (only stdlib) so it can be unit-tested without pulling
the orchestrator's ``processors`` namespace, which collides with the
data-processing-agent's ``processors`` package on a shared ``sys.path``.

Resolution precedence used by :func:`resolve_plot_tz_name`:

1. Explicit ``meter_timezone`` (typically the meter's ``deviceTimeZone``
   from ``get_meter_profile``).
2. ``display_timezone`` (the user's browser timezone, validated upstream).
3. ``BLUEBOT_PLOT_TZ`` env var (server-wide override).
4. ``DISPLAY_TZ`` env var (shared with the time-range parser).
5. ``"UTC"`` final fallback.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_iana(name: str | None) -> str | None:
    """Return ``name`` if it is a known IANA zone (or ``"UTC"``), else ``None``.

    Empty / whitespace-only / unknown inputs all collapse to ``None`` so the
    caller can use a single ``if validate_iana(x):`` check instead of guarding
    on every possible bad shape.
    """
    if not name:
        return None
    n = str(name).strip()
    if not n:
        return None
    if n.upper() == "UTC":
        return "UTC"
    try:
        ZoneInfo(n)
        return n
    except ZoneInfoNotFoundError:
        return None


def resolve_plot_tz_name(
    *, meter_timezone: str | None, display_timezone: str | None
) -> str:
    """
    Resolve which IANA zone the plot x-axes should render in.

    See module docstring for the precedence chain. The return value is
    always a non-empty string and is always exported as
    ``BLUEBOT_PLOT_TZ`` for the data-processing-agent subprocess.
    """
    for cand in (
        meter_timezone,
        display_timezone,
        os.environ.get("BLUEBOT_PLOT_TZ"),
        os.environ.get("DISPLAY_TZ"),
    ):
        ok = validate_iana(cand)
        if ok:
            return ok
    return "UTC"
