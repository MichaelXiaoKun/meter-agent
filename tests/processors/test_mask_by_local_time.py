"""
Unit tests for ``processors.mask_by_local_time``.

The module is the scaffolding for the future per-window analysis feature
(business-hours slices, weekend-only comparisons, etc.). These tests lock in:

- all four states (``not_requested``, ``invalid_spec``, ``empty_mask``, ``applied``);
- structural validation of every field;
- weekday + hour predicate composition with correct timezone handling,
  including a spring-forward DST day;
- ``exclude_dates`` and ``include_sub_ranges`` semantics;
- provenance counts (``n_rows_input``, ``n_rows_kept``, ``fraction_kept``);
- the ``apply_filter`` wrapper's fail-safe on missing ``timestamp`` column.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from processors.mask_by_local_time import (
    STATE_APPLIED,
    STATE_EMPTY_MASK,
    STATE_INVALID_SPEC,
    STATE_NOT_REQUESTED,
    apply_filter,
    expand_to_mask,
    not_requested_stub,
    validate_filter_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Unix seconds for a UTC wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def _local_ts(tz: str, year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Unix seconds for a local wall-clock time in ``tz``."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp()


def _hourly_utc_range(year, month, day, start_hour, end_hour) -> np.ndarray:
    return np.array(
        [_utc_ts(year, month, day, h) for h in range(start_hour, end_hour)],
        dtype=float,
    )


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class TestNotRequested:
    def test_none_spec_is_not_requested(self):
        ts = np.array([1_700_000_000.0, 1_700_000_060.0])
        mask, res = expand_to_mask(ts, None)
        assert res.state == STATE_NOT_REQUESTED
        assert res.applied is False
        assert mask.tolist() == [True, True]    # caller must not apply when not_requested
        assert res.n_rows_input == 2
        assert res.n_rows_kept == 0

    def test_not_requested_stub_matches_direct_call(self):
        direct = expand_to_mask(np.array([], dtype=float), None)[1].to_dict()
        assert not_requested_stub() == direct


# ---------------------------------------------------------------------------
# Validation / invalid_spec
# ---------------------------------------------------------------------------


class TestValidation:
    def test_empty_spec_ok(self):
        assert validate_filter_spec({}) == []

    def test_none_is_ok(self):
        assert validate_filter_spec(None) == []

    def test_local_rules_require_timezone(self):
        errs = validate_filter_spec({"weekdays": [0, 1]})
        assert any("timezone is required" in e for e in errs)

    def test_unknown_timezone_rejected(self):
        errs = validate_filter_spec(
            {"timezone": "Mars/Olympus_Mons", "weekdays": [0]}
        )
        assert any("not a known IANA zone" in e for e in errs)

    def test_weekday_out_of_range(self):
        errs = validate_filter_spec(
            {"timezone": "America/Denver", "weekdays": [0, 9]}
        )
        assert any("weekdays entry" in e for e in errs)

    def test_weekday_must_be_non_empty(self):
        errs = validate_filter_spec({"timezone": "UTC", "weekdays": []})
        assert any("non-empty list" in e for e in errs)

    def test_hour_range_requires_start_less_than_end(self):
        errs = validate_filter_spec(
            {
                "timezone": "UTC",
                "hour_ranges": [{"start_hour": 22, "end_hour": 6}],
            }
        )
        assert any("split overnight spans" in e for e in errs)

    def test_hour_range_out_of_bounds(self):
        errs = validate_filter_spec(
            {
                "timezone": "UTC",
                "hour_ranges": [{"start_hour": -1, "end_hour": 25}],
            }
        )
        assert any("start_hour/end_hour" in e for e in errs)

    def test_exclude_date_format(self):
        errs = validate_filter_spec(
            {"timezone": "UTC", "exclude_dates": ["2026/04/15"]}
        )
        assert any("YYYY-MM-DD" in e for e in errs)

    def test_sub_range_requires_start_lt_end(self):
        errs = validate_filter_spec(
            {"include_sub_ranges": [{"start": 100, "end": 50}]}
        )
        assert any("start < end" in e for e in errs)

    def test_invalid_spec_state_with_errors_attached(self):
        mask, res = expand_to_mask(
            np.array([_utc_ts(2026, 4, 14, 12)]),
            {"timezone": "UTC", "weekdays": [99]},
        )
        assert res.state == STATE_INVALID_SPEC
        assert res.applied is False
        assert res.validation_errors and any(
            "weekdays entry" in e for e in res.validation_errors
        )
        assert mask.tolist() == [False]


# ---------------------------------------------------------------------------
# Weekday + hour predicate (plain, non-DST)
# ---------------------------------------------------------------------------


class TestWeekdayAndHour:
    def test_business_hours_tuesday_denver(self):
        # 2026-04-14 is a Tuesday. Build 24 hourly samples in UTC covering
        # the full day. Business hours = 8–17 Denver local (MDT = UTC-6).
        #   local hour 8  ⇒ UTC 14
        #   local hour 17 ⇒ UTC 23 (exclusive)
        ts = _hourly_utc_range(2026, 4, 14, 0, 24)
        mask, res = expand_to_mask(
            ts,
            {
                "timezone": "America/Denver",
                "weekdays": [1],
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            },
        )
        assert res.state == STATE_APPLIED
        assert res.applied is True
        assert res.n_rows_input == 24
        # UTC 14..22 inclusive → 9 samples
        assert res.n_rows_kept == 9
        assert res.fraction_kept == pytest.approx(9 / 24)
        kept_utc_hours = [
            datetime.fromtimestamp(t, tz=timezone.utc).hour
            for t, keep in zip(ts, mask)
            if keep
        ]
        assert kept_utc_hours == list(range(14, 23))

    def test_weekend_filter_drops_weekdays(self):
        # Samples on Tue/Sat at local noon.
        ts = np.array(
            [
                _local_ts("America/Denver", 2026, 4, 14, 12),   # Tue
                _local_ts("America/Denver", 2026, 4, 18, 12),   # Sat
                _local_ts("America/Denver", 2026, 4, 19, 12),   # Sun
            ]
        )
        _, res = expand_to_mask(
            ts, {"timezone": "America/Denver", "weekdays": [5, 6]}
        )
        assert res.state == STATE_APPLIED
        assert res.n_rows_kept == 2

    def test_two_hour_ranges_union(self):
        ts = np.array(
            [
                _local_ts("UTC", 2026, 4, 14, 7),    # in 6-10
                _local_ts("UTC", 2026, 4, 14, 12),   # neither
                _local_ts("UTC", 2026, 4, 14, 15),   # in 14-18
                _local_ts("UTC", 2026, 4, 14, 20),   # neither
            ]
        )
        _, res = expand_to_mask(
            ts,
            {
                "timezone": "UTC",
                "hour_ranges": [
                    {"start_hour": 6, "end_hour": 10},
                    {"start_hour": 14, "end_hour": 18},
                ],
            },
        )
        assert res.n_rows_kept == 2

    def test_end_hour_is_exclusive(self):
        ts = np.array(
            [_local_ts("UTC", 2026, 4, 14, h) for h in (8, 16, 17)]
        )
        _, res = expand_to_mask(
            ts,
            {
                "timezone": "UTC",
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            },
        )
        assert res.n_rows_kept == 2  # 17:00 excluded


# ---------------------------------------------------------------------------
# DST correctness
# ---------------------------------------------------------------------------


class TestDST:
    def test_spring_forward_morning_in_denver(self):
        # 2026-03-08 is the US spring-forward date (2 AM local skipped).
        # After the transition Denver = UTC-6 (MDT). Samples at UTC 14..22
        # map to local 08..16 and must all be kept for the 8-17 window.
        ts = _hourly_utc_range(2026, 3, 8, 14, 23)          # 9 samples
        _, res = expand_to_mask(
            ts,
            {
                "timezone": "America/Denver",
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            },
        )
        assert res.state == STATE_APPLIED
        assert res.n_rows_kept == 9

    def test_fall_back_morning_in_denver(self):
        # 2025-11-02 is the US fall-back date (1–2 AM local repeats).
        # After the transition Denver = UTC-7 (MST). Samples at UTC 15..23
        # map to local 08..16 and must all be kept for the 8-17 window.
        ts = _hourly_utc_range(2025, 11, 2, 15, 24)
        _, res = expand_to_mask(
            ts,
            {
                "timezone": "America/Denver",
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            },
        )
        assert res.state == STATE_APPLIED
        assert res.n_rows_kept == 9


# ---------------------------------------------------------------------------
# exclude_dates / include_sub_ranges
# ---------------------------------------------------------------------------


class TestExcludeAndSubRanges:
    def test_exclude_date_drops_only_that_local_date(self):
        # Samples at local noon on Apr 14, 15, 16 in Denver. Exclude Apr 15.
        ts = np.array(
            [_local_ts("America/Denver", 2026, 4, d, 12) for d in (14, 15, 16)]
        )
        _, res = expand_to_mask(
            ts,
            {"timezone": "America/Denver", "exclude_dates": ["2026-04-15"]},
        )
        assert res.n_rows_kept == 2

    def test_include_sub_ranges_union(self):
        ts = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
        _, res = expand_to_mask(
            ts,
            {
                "include_sub_ranges": [
                    {"start": 150, "end": 250},   # keeps 200
                    {"start": 350, "end": 450},   # keeps 400
                ]
            },
        )
        assert res.state == STATE_APPLIED
        assert res.n_rows_kept == 2

    def test_sub_ranges_compose_with_local_rules(self):
        # Samples at 08, 12, 16 local on Apr 14 (Tuesday). Weekday Tue OK.
        # Sub-range restricts to the middle sample only.
        ts = np.array(
            [_local_ts("America/Denver", 2026, 4, 14, h) for h in (8, 12, 16)]
        )
        sub_start = int(ts[1] - 30)
        sub_end = int(ts[1] + 30)
        _, res = expand_to_mask(
            ts,
            {
                "timezone": "America/Denver",
                "weekdays": [1],
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
                "include_sub_ranges": [{"start": sub_start, "end": sub_end}],
            },
        )
        assert res.n_rows_kept == 1

    def test_empty_mask_refusal(self):
        # Valid spec that simply matches no rows ⇒ refuse with state.
        ts = np.array([_local_ts("America/Denver", 2026, 4, 14, 12)])
        _, res = expand_to_mask(
            ts,
            {"timezone": "America/Denver", "weekdays": [5, 6]},   # Tue ⇒ dropped
        )
        assert res.state == STATE_EMPTY_MASK
        assert res.applied is False
        assert res.n_rows_kept == 0
        assert res.fraction_kept == 0.0
        assert res.reasons_refused and "zero rows" in res.reasons_refused[0]


# ---------------------------------------------------------------------------
# apply_filter wrapper
# ---------------------------------------------------------------------------


class TestApplyFilter:
    def test_missing_timestamp_column_refuses(self):
        df = pd.DataFrame({"flow_rate": [1.0, 2.0]})
        out, res = apply_filter(df, {"timezone": "UTC", "weekdays": [0]})
        assert res.state == STATE_INVALID_SPEC
        assert res.applied is False
        assert out is df           # unchanged
        assert any("timestamp" in e for e in res.validation_errors)

    def test_not_requested_returns_df_unchanged(self):
        df = pd.DataFrame({"timestamp": [1.0, 2.0], "flow_rate": [10.0, 20.0]})
        out, res = apply_filter(df, None)
        assert res.state == STATE_NOT_REQUESTED
        assert out is df

    def test_applied_returns_new_indexed_df(self):
        ts = [_local_ts("America/Denver", 2026, 4, 14, h) for h in (6, 10, 14, 18)]
        df = pd.DataFrame({"timestamp": ts, "flow_rate": [1.0, 2.0, 3.0, 4.0]})
        out, res = apply_filter(
            df,
            {
                "timezone": "America/Denver",
                "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            },
        )
        assert res.state == STATE_APPLIED
        assert list(out["flow_rate"]) == [2.0, 3.0]
        assert list(out.index) == [0, 1]    # reset_index


# ---------------------------------------------------------------------------
# Provenance shape
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_predicate_used_is_canonical(self):
        spec = {
            "timezone": "America/Denver",
            "weekdays": [4, 1, 3],           # unsorted on input
            "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
            "exclude_dates": ["2026-04-15", "2026-04-10"],
        }
        ts = np.array([_local_ts("America/Denver", 2026, 4, 14, 12)])
        _, res = expand_to_mask(ts, spec)
        assert res.predicate_used["weekdays"] == [1, 3, 4]       # sorted
        assert res.predicate_used["exclude_dates"] == ["2026-04-10", "2026-04-15"]
        assert res.predicate_used["timezone"] == "America/Denver"

    def test_fraction_kept_none_on_empty_input(self):
        mask, res = expand_to_mask(
            np.array([], dtype=float),
            {"timezone": "UTC", "weekdays": [0]},
        )
        # No rows + valid spec ⇒ still "empty_mask" since kept == 0.
        assert res.state == STATE_EMPTY_MASK
        assert res.n_rows_input == 0
        assert res.n_rows_kept == 0
        assert res.fraction_kept is None
