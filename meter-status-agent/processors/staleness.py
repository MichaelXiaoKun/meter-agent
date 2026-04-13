"""
Staleness Processor

Computes how long ago the meter last reported, and classifies
the communication status based on elapsed time.
"""

from datetime import datetime, timezone
from typing import Any, Dict


# Thresholds for communication health classification
THRESHOLDS = {
    "fresh":    3600,       # < 1 hour  → meter is actively reporting
    "stale":    86400,      # < 24 hours → meter has gone quiet but recently seen
    "absent":   604800,     # < 7 days   → meter has been silent for days
    # beyond 7 days → considered lost
}


def compute_staleness(last_message_at: str) -> Dict[str, Any]:
    """
    Compute elapsed time since the meter's last reported message.

    Args:
        last_message_at:  ISO 8601 timestamp string (e.g. "2026-04-07T21:39:53.712Z")

    Returns:
        last_message_at:        The original timestamp
        seconds_since:          Elapsed seconds since last message
        minutes_since:          Elapsed minutes (rounded)
        hours_since:            Elapsed hours (rounded to 2dp)
        days_since:             Elapsed days (rounded to 2dp)
        communication_status:   "fresh" | "stale" | "absent" | "lost"
        status_description:     Human-readable explanation of the status
    """
    last_dt = datetime.fromisoformat(last_message_at.replace("Z", "+00:00"))
    now = datetime.now(tz=timezone.utc)
    elapsed = (now - last_dt).total_seconds()

    if elapsed < THRESHOLDS["fresh"]:
        status = "fresh"
        description = "Meter is actively reporting."
    elif elapsed < THRESHOLDS["stale"]:
        status = "stale"
        description = "Meter has not reported in over an hour but was seen within the last 24 hours."
    elif elapsed < THRESHOLDS["absent"]:
        status = "absent"
        description = "Meter has been silent for more than 24 hours."
    else:
        status = "lost"
        description = "Meter has not reported in over 7 days — may be offline, removed, or malfunctioning."

    return {
        "last_message_at": last_message_at,
        "seconds_since": round(elapsed),
        "minutes_since": round(elapsed / 60),
        "hours_since": round(elapsed / 3600, 2),
        "days_since": round(elapsed / 86400, 2),
        "communication_status": status,
        "status_description": description,
    }
