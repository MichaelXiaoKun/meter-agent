"""
Threshold event detection for flow-analysis series.
"""

from __future__ import annotations

import operator
import re
from typing import Callable

import numpy as np
import pandas as pd


_PREDICATE_RE = re.compile(
    r"^\s*(?P<field>flow|flow_rate|quality)\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<threshold>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)

_OPS: dict[str, Callable[[np.ndarray, float], np.ndarray]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_FIELD_TO_COLUMN = {
    "flow": "flow_rate",
    "flow_rate": "flow_rate",
    "quality": "quality",
}


def _parse_predicate(predicate: str) -> tuple[str, str, float]:
    if not isinstance(predicate, str) or not predicate.strip():
        raise ValueError("predicate must be a non-empty string")
    match = _PREDICATE_RE.match(predicate)
    if match is None:
        raise ValueError(
            "predicate must look like 'flow > 10', 'flow == 0', or 'quality < 60'"
        )
    field = match.group("field")
    op = match.group("op")
    threshold = float(match.group("threshold"))
    return field, op, threshold


def detect_threshold_events(
    df: pd.DataFrame,
    *,
    predicate: str,
    min_duration_seconds: int,
) -> list[dict]:
    """Return contiguous threshold spans that last at least ``min_duration_seconds``.

    Predicate language is intentionally small:
    ``flow`` / ``flow_rate`` / ``quality`` + one comparison operator + a numeric
    threshold. Events are contiguous runs of samples where the predicate is true.
    A run's duration is ``last_true_timestamp - first_true_timestamp``; a single
    true sample therefore has duration 0 and only survives when the minimum is 0.
    """
    field, op, threshold = _parse_predicate(predicate)
    try:
        min_duration = int(min_duration_seconds)
    except (TypeError, ValueError):
        raise ValueError("min_duration_seconds must be an integer")
    if min_duration < 0:
        raise ValueError("min_duration_seconds must be >= 0")
    if "timestamp" not in df.columns:
        raise ValueError("dataframe must include a timestamp column")
    column = _FIELD_TO_COLUMN[field]
    if column not in df.columns:
        raise ValueError(f"dataframe must include a {column!r} column")
    if df.empty:
        return []

    cols = ["timestamp", "flow_rate"]
    if column not in cols:
        cols.append(column)
    work = df[cols].dropna(subset=["timestamp", column]).sort_values("timestamp")
    if work.empty:
        return []

    ts = work["timestamp"].to_numpy(dtype=float)
    flow = work["flow_rate"].to_numpy(dtype=float)
    values = work[column].to_numpy(dtype=float)
    mask = np.asarray(_OPS[op](values, threshold), dtype=bool)

    events: list[dict] = []
    start_idx: int | None = None
    for idx, is_true in enumerate(mask):
        if is_true and start_idx is None:
            start_idx = idx
        if start_idx is None:
            continue
        is_last = idx == len(mask) - 1
        if (not is_true) or is_last:
            end_idx = idx if is_true and is_last else idx - 1
            if end_idx >= start_idx:
                start_ts = float(ts[start_idx])
                end_ts = float(ts[end_idx])
                duration = max(0.0, end_ts - start_ts)
                if duration >= float(min_duration):
                    flow_slice = flow[start_idx : end_idx + 1]
                    value_slice = values[start_idx : end_idx + 1]
                    events.append(
                        {
                            "start_ts": start_ts,
                            "end_ts": end_ts,
                            "duration_seconds": duration,
                            "peak_value": float(np.nanmax(flow_slice)),
                            "predicate_value_min": float(np.nanmin(value_slice)),
                            "predicate_value_max": float(np.nanmax(value_slice)),
                            "sample_count": int(end_idx - start_idx + 1),
                        }
                    )
            start_idx = None

    return events
