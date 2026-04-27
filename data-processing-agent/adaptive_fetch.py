"""
Adaptive Fetch — bounded retry over ``fetch_flow_data_range``.

When a processor declares a minimum sample count via DATA_REQUIREMENTS, this
helper sizes an initial window from cadence physics, fetches the slice, and
expands the window (2x each round) up to a hard cap if the result fails the
adequacy check. Fully deterministic; no LLM in the loop.

Used by:
  - Future ``monitor`` / batch-backfill services that need "give me at least N
    points or fail explicitly" semantics without any conversational context.
  - Tests / eval harnesses that want to materialise a known-adequate slice
    around a target timestamp.

NOT wired into the live conversational path yet (``interface.run`` still uses
the explicit (start, end) the orchestrator provides). When the planner upgrade
in the plan lands, ``interface.run`` will gain a ``requirements=`` parameter
and route through here.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_client import fetch_flow_data_range
from processors.data_adequacy import check_adequacy, estimate_window_seconds


# Hard caps. Governed by env so ops can loosen during incident response without
# code changes — but the defaults are conservative.
_MAX_WINDOW_SECONDS_DEFAULT = 30 * 86400   # 30 days
_MAX_RETRIES_DEFAULT = 2                   # 3 fetch attempts total
_EXPANSION_FACTOR = 2.0


def _max_window_seconds() -> int:
    import os
    raw = os.environ.get("BLUEBOT_ADAPTIVE_FETCH_MAX_WINDOW_S")
    if not raw:
        return _MAX_WINDOW_SECONDS_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _MAX_WINDOW_SECONDS_DEFAULT
    return max(3600, n)


def _max_retries() -> int:
    import os
    raw = os.environ.get("BLUEBOT_ADAPTIVE_FETCH_MAX_RETRIES")
    if not raw:
        return _MAX_RETRIES_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _MAX_RETRIES_DEFAULT
    return max(0, n)


def fetch_for_analysis(
    serial_number: str,
    end_timestamp: int,
    requirements: Dict[str, Any],
    *,
    initial_window_seconds: Optional[int] = None,
    token: Optional[str] = None,
    max_retries: Optional[int] = None,
    max_window_seconds: Optional[int] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any], List[Dict[str, Any]]]:
    """
    Fetch a slice ending at ``end_timestamp`` that meets ``requirements``,
    expanding the window up to ``max_retries`` times when it doesn't.

    Args:
        serial_number:           Meter serial for the high-res Flow API.
        end_timestamp:           Inclusive Unix-seconds upper bound. The lower
                                 bound is derived as ``end - window``.
        requirements:            ``DATA_REQUIREMENTS`` dict from a processor.
        initial_window_seconds:  Override for the first window. Defaults to
                                 ``estimate_window_seconds(min_points)``.
        token:                   Bearer token; falls back to BLUEBOT_TOKEN.
        max_retries:             Number of *additional* attempts after the
                                 first. Defaults to env / 2.
        max_window_seconds:      Cap on the expanded window. Defaults to env /
                                 30 days.

    Returns:
        (df, final_adequacy, history)

        ``df`` is the most recent fetch (returned even on failure so callers can
        still inspect what the meter does report). ``final_adequacy`` is the
        adequacy report for ``df``. ``history`` is one entry per attempt in the
        order they ran:

            {"attempt": int, "window_seconds": int, "points": int,
             "adequacy": AdequacyReport, "elapsed_seconds": float}
    """
    retries_left = max_retries if max_retries is not None else _max_retries()
    cap = max_window_seconds if max_window_seconds is not None else _max_window_seconds()

    target_min = int(requirements.get("min_points", 0))
    window = int(initial_window_seconds) if initial_window_seconds else estimate_window_seconds(target_min)
    window = min(window, cap)

    history: List[Dict[str, Any]] = []
    df: pd.DataFrame = pd.DataFrame()
    adequacy: Dict[str, Any] = {}

    attempt = 0
    while True:
        start = max(0, int(end_timestamp) - window)
        t0 = time.monotonic()
        df = fetch_flow_data_range(serial_number, start, int(end_timestamp), token=token, verbose=False)
        elapsed = time.monotonic() - t0
        timestamps = df["timestamp"].values if "timestamp" in df.columns else []
        adequacy = check_adequacy(timestamps, requirements)
        history.append(
            {
                "attempt": attempt + 1,
                "window_seconds": window,
                "points": int(len(df)),
                "adequacy": adequacy,
                "elapsed_seconds": round(float(elapsed), 3),
            }
        )

        if adequacy["ok"]:
            return df, adequacy, history

        if attempt >= retries_left:
            return df, adequacy, history

        next_window = int(window * _EXPANSION_FACTOR)
        if next_window > cap:
            next_window = cap
        if next_window <= window:
            # Cap reached and no further expansion possible — stop now.
            return df, adequacy, history

        window = next_window
        attempt += 1
