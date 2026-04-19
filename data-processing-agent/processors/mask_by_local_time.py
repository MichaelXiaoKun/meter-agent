"""
Local-time Filter Processor
===========================

Pure, deterministic "given a structured filter spec, which timestamps do we
keep?" evaluator.

This is the **scaffolding** for the future per-window analysis feature
("Mon–Fri 8 AM–5 PM local", "weekends only", "exclude holidays", "these
specific sub-ranges"). It is intentionally landed *before* the tool surface
on ``analyze_flow_data`` is extended so the masking behaviour — including
refusals — has a stable contract and a test harness the day the feature is
wired end-to-end.

Design mirrors :mod:`processors.baseline_quality`:

1. **Silence is an option.** No spec ⇒ ``state="not_requested"``, not a
   silent full-window pass.
2. **Bright-line refusals.** A valid spec that matches zero rows yields
   ``state="empty_mask"``; an ill-formed spec yields ``state="invalid_spec"``
   with the specific validation errors attached.
3. **Provenance.** Every return value carries the counts the downstream
   processors need — ``n_rows_input``, ``n_rows_kept``, ``fraction_kept``,
   ``predicate_used`` — so reviewers can audit which rows were dropped.
4. **No side effects.** No filesystem, no network, no pandas mutation;
   ``apply_filter`` returns a new DataFrame.

Filter-spec shape (all fields optional):

.. code-block:: json

    {
      "timezone":        "America/Denver",
      "weekdays":        [0, 1, 2, 3, 4],
      "hour_ranges":     [{"start_hour": 8, "end_hour": 17}],
      "exclude_dates":   ["2026-04-15"],
      "include_sub_ranges": [{"start": 1776200000, "end": 1776300000}]
    }

Semantics:

- When ``include_sub_ranges`` is present, a sample is kept if its timestamp
  (unix seconds) falls inside **any** listed ``[start, end)`` interval.
  Weekday/hour/exclude rules are AND-combined on top.
- Without ``include_sub_ranges``, weekday/hour/exclude rules alone define
  the predicate. ``weekdays`` absent ⇒ all weekdays; ``hour_ranges`` absent
  ⇒ all hours; ``exclude_dates`` absent ⇒ nothing excluded.
- ``timezone`` is required whenever any local-time rule is present
  (``weekdays``, ``hour_ranges``, ``exclude_dates``). It is an IANA name.
- ``hour_ranges`` use ``[start_hour, end_hour)`` with ``start_hour < end_hour``
  in the range ``[0, 24]``. Overnight spans (e.g. 22→06) must be expressed as
  two ranges to keep the predicate unambiguous under DST.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Shape contracts
# ---------------------------------------------------------------------------


class HourRange(TypedDict, total=False):
    start_hour: int
    end_hour: int


class SubRange(TypedDict, total=False):
    start: int    # unix seconds, inclusive
    end: int      # unix seconds, exclusive


class FilterSpec(TypedDict, total=False):
    timezone: str
    weekdays: List[int]
    hour_ranges: List[HourRange]
    exclude_dates: List[str]
    include_sub_ranges: List[SubRange]


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

STATE_NOT_REQUESTED = "not_requested"
STATE_APPLIED = "applied"
STATE_EMPTY_MASK = "empty_mask"
STATE_INVALID_SPEC = "invalid_spec"

_ALL_STATES = frozenset(
    {STATE_NOT_REQUESTED, STATE_APPLIED, STATE_EMPTY_MASK, STATE_INVALID_SPEC}
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FilterResult:
    """JSON-serialisable result of applying a filter spec."""

    state: str = STATE_NOT_REQUESTED
    applied: bool = False
    n_rows_input: int = 0
    n_rows_kept: int = 0
    fraction_kept: Optional[float] = None
    predicate_used: Dict[str, Any] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    reasons_refused: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_filter_spec(spec: Optional[FilterSpec]) -> List[str]:
    """
    Return a list of human-readable validation error messages. Empty list ⇒
    the spec is structurally valid (but may still match zero rows when applied).
    """
    if spec is None:
        return []

    errors: List[str] = []
    if not isinstance(spec, dict):
        return [f"filter spec must be a dict, got {type(spec).__name__}"]

    tz = spec.get("timezone")
    local_rules_present = any(
        k in spec for k in ("weekdays", "hour_ranges", "exclude_dates")
    )

    if tz is not None:
        if not isinstance(tz, str) or not tz.strip():
            errors.append("timezone must be a non-empty IANA zone string")
        else:
            try:
                ZoneInfo(tz)
            except ZoneInfoNotFoundError:
                errors.append(f"timezone {tz!r} is not a known IANA zone")
    elif local_rules_present:
        errors.append(
            "timezone is required when weekdays / hour_ranges / exclude_dates "
            "are provided"
        )

    wd = spec.get("weekdays")
    if wd is not None:
        if not isinstance(wd, (list, tuple)) or not wd:
            errors.append("weekdays must be a non-empty list of integers in [0, 6]")
        else:
            for v in wd:
                if not isinstance(v, int) or v < 0 or v > 6:
                    errors.append(
                        f"weekdays entry {v!r} is invalid; must be int in [0, 6]"
                    )
                    break

    hrs = spec.get("hour_ranges")
    if hrs is not None:
        if not isinstance(hrs, (list, tuple)) or not hrs:
            errors.append("hour_ranges must be a non-empty list")
        else:
            for i, r in enumerate(hrs):
                if not isinstance(r, dict):
                    errors.append(f"hour_ranges[{i}] must be a dict")
                    continue
                s = r.get("start_hour")
                e = r.get("end_hour")
                if (
                    not isinstance(s, int)
                    or not isinstance(e, int)
                    or s < 0
                    or e < 0
                    or s > 24
                    or e > 24
                ):
                    errors.append(
                        f"hour_ranges[{i}] must have integer start_hour/end_hour "
                        "in [0, 24]"
                    )
                elif s >= e:
                    errors.append(
                        f"hour_ranges[{i}] requires start_hour < end_hour "
                        f"(got {s} >= {e}); split overnight spans into two ranges"
                    )

    excl = spec.get("exclude_dates")
    if excl is not None:
        if not isinstance(excl, (list, tuple)):
            errors.append("exclude_dates must be a list of 'YYYY-MM-DD' strings")
        else:
            for d in excl:
                if not isinstance(d, str) or not _DATE_RE.match(d):
                    errors.append(
                        f"exclude_dates entry {d!r} must match YYYY-MM-DD"
                    )
                    break

    subs = spec.get("include_sub_ranges")
    if subs is not None:
        if not isinstance(subs, (list, tuple)) or not subs:
            errors.append("include_sub_ranges must be a non-empty list")
        else:
            for i, r in enumerate(subs):
                if not isinstance(r, dict):
                    errors.append(f"include_sub_ranges[{i}] must be a dict")
                    continue
                s = r.get("start")
                e = r.get("end")
                if not (isinstance(s, int) and isinstance(e, int)):
                    errors.append(
                        f"include_sub_ranges[{i}] must have integer start/end (unix seconds)"
                    )
                elif s < 0 or e <= s:
                    errors.append(
                        f"include_sub_ranges[{i}] requires 0 <= start < end (got {s}, {e})"
                    )

    return errors


# ---------------------------------------------------------------------------
# Mask expansion
# ---------------------------------------------------------------------------


def _predicate_summary(spec: FilterSpec) -> Dict[str, Any]:
    """Canonicalised copy of the spec for provenance reporting."""
    out: Dict[str, Any] = {}
    if "timezone" in spec:
        out["timezone"] = spec["timezone"]
    if "weekdays" in spec:
        out["weekdays"] = sorted(int(w) for w in spec["weekdays"])
    if "hour_ranges" in spec:
        out["hour_ranges"] = [
            {"start_hour": int(r["start_hour"]), "end_hour": int(r["end_hour"])}
            for r in spec["hour_ranges"]
        ]
    if "exclude_dates" in spec:
        out["exclude_dates"] = sorted(spec["exclude_dates"])
    if "include_sub_ranges" in spec:
        out["include_sub_ranges"] = [
            {"start": int(r["start"]), "end": int(r["end"])}
            for r in spec["include_sub_ranges"]
        ]
    return out


def _apply_local_rules(ts: np.ndarray, spec: FilterSpec) -> np.ndarray:
    """
    Build a boolean mask from local-time rules (weekdays / hour_ranges /
    exclude_dates). ``ts`` is unix seconds (float). Rows with NaT are False.
    """
    local_rules = any(
        k in spec for k in ("weekdays", "hour_ranges", "exclude_dates")
    )
    if not local_rules:
        return np.ones(len(ts), dtype=bool)

    tz = ZoneInfo(spec["timezone"])
    # Vectorised localisation via pandas: UTC seconds → tz-aware → local components.
    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    mask = np.ones(len(ts), dtype=bool)

    wd = spec.get("weekdays")
    if wd is not None:
        wd_set = set(int(x) for x in wd)
        mask &= np.isin(idx.weekday, list(wd_set))

    hrs = spec.get("hour_ranges")
    if hrs is not None:
        hours = idx.hour
        minutes = idx.minute
        seconds = idx.second
        # Fractional local hour in [0, 24). Keeps behaviour identical under DST
        # because we use the *local* wall clock after tz_convert.
        frac = hours + minutes / 60.0 + seconds / 3600.0
        hour_mask = np.zeros(len(ts), dtype=bool)
        for r in hrs:
            s = int(r["start_hour"])
            e = int(r["end_hour"])
            hour_mask |= (frac >= s) & (frac < e)
        mask &= hour_mask

    excl = spec.get("exclude_dates")
    if excl:
        local_dates = idx.strftime("%Y-%m-%d").to_numpy()
        mask &= ~np.isin(local_dates, list(excl))

    return mask


def _apply_sub_ranges(ts: np.ndarray, spec: FilterSpec) -> np.ndarray:
    """Mask from ``include_sub_ranges``; all-True if not provided."""
    subs = spec.get("include_sub_ranges")
    if not subs:
        return np.ones(len(ts), dtype=bool)
    mask = np.zeros(len(ts), dtype=bool)
    for r in subs:
        s = int(r["start"])
        e = int(r["end"])
        mask |= (ts >= s) & (ts < e)
    return mask


def expand_to_mask(
    timestamps_unix_seconds: Sequence[float] | np.ndarray,
    spec: Optional[FilterSpec],
) -> Tuple[np.ndarray, FilterResult]:
    """
    Compute the boolean mask and a provenance record for a filter spec.

    Parameters
    ----------
    timestamps_unix_seconds
        Unix seconds as ``np.ndarray`` or any 1-D sequence. Not required to
        be sorted.
    spec
        Optional filter spec; ``None`` ⇒ ``state="not_requested"`` and a mask
        of all True (callers should NOT apply the mask in that state; use the
        result's ``state`` / ``applied`` flags instead).

    Returns
    -------
    (mask, result)
        ``mask`` is a boolean ``np.ndarray`` with one entry per timestamp;
        ``result`` is the JSON-serialisable :class:`FilterResult`.
    """
    ts = np.asarray(timestamps_unix_seconds, dtype=float)
    n = int(len(ts))
    result = FilterResult(n_rows_input=n)

    if spec is None:
        result.state = STATE_NOT_REQUESTED
        return np.ones(n, dtype=bool), result

    errors = validate_filter_spec(spec)
    if errors:
        result.state = STATE_INVALID_SPEC
        result.validation_errors = errors
        result.reasons_refused = ["Filter spec failed validation; see validation_errors."]
        result.predicate_used = _predicate_summary(spec) if isinstance(spec, dict) else {}
        return np.zeros(n, dtype=bool), result

    mask = _apply_local_rules(ts, spec) & _apply_sub_ranges(ts, spec)
    kept = int(mask.sum())
    result.predicate_used = _predicate_summary(spec)
    result.n_rows_kept = kept
    result.fraction_kept = (kept / n) if n > 0 else None

    if kept == 0:
        result.state = STATE_EMPTY_MASK
        result.reasons_refused = [
            "Filter spec matched zero rows; refuse to proceed rather than "
            "silently analyse nothing."
        ]
        return mask, result

    result.state = STATE_APPLIED
    result.applied = True
    return mask, result


def apply_filter(
    df: pd.DataFrame,
    spec: Optional[FilterSpec],
    *,
    timestamp_column: str = "timestamp",
) -> Tuple[pd.DataFrame, FilterResult]:
    """
    Convenience wrapper: return ``(filtered_df, result)``.

    When ``state`` is ``not_requested`` or ``empty_mask`` or ``invalid_spec``,
    the returned DataFrame equals ``df`` unchanged (i.e. callers must consult
    ``result.applied`` before relying on the filtered frame).
    """
    if timestamp_column not in df.columns:
        result = FilterResult(
            state=STATE_INVALID_SPEC,
            n_rows_input=len(df),
            validation_errors=[
                f"DataFrame is missing required column {timestamp_column!r}"
            ],
            reasons_refused=["Cannot apply filter without a timestamp column."],
        )
        return df, result

    mask, result = expand_to_mask(df[timestamp_column].to_numpy(), spec)
    if not result.applied:
        return df, result
    return df.loc[mask].reset_index(drop=True), result


def not_requested_stub() -> Dict[str, Any]:
    """Pre-built verdict for 'no filter supplied'. Schema-stable."""
    return FilterResult(state=STATE_NOT_REQUESTED).to_dict()
