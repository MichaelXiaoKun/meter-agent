"""
Unit tests for ``processors.daily_rollup``.

These cover the contract the baseline-quality refusal pipeline depends on:

* Local-tz day grouping (not UTC) so ``YYYY-MM-DD`` values match what users
  see on plot x-axes.
* Trapezoidal volume integration in ``gallons`` per the existing
  :func:`processors.flow_metrics.compute_total_volume` convention.
* Coverage ratio sized against the requested nominal interval.
* Quality threshold (≤ 60 = "low") matching the rest of the pipeline.
* Weekday tagging (0=Mon..6=Sun) suitable for the
  ``baseline_quality.target_weekday`` gate.
* DST correctness — spring-forward and fall-back days must produce the
  expected number of distinct ``local_date`` values without crashing.
* Partial-today rollup respects the ``fraction_of_day_elapsed`` hint and the
  midnight-local span fallback when no hint is supplied.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processors.daily_rollup import (
    build_daily_rollups,
    build_today_partial_rollup,
    fraction_of_day_elapsed,
    today_missing_bucket_ratio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(rows: list[tuple[float, float, float | None]]) -> pd.DataFrame:
    """Build a flow dataframe from ``[(unix_ts, flow_rate, quality), ...]``."""
    return pd.DataFrame(
        {
            "timestamp": [int(r[0]) for r in rows],
            "flow_rate": [float(r[1]) for r in rows],
            "quality": [
                float(r[2]) if r[2] is not None and not math.isnan(r[2]) else float("nan")
                for r in rows
            ],
        }
    )


def _midnight_unix(year: int, month: int, day: int, tz: str) -> int:
    """``datetime(year, month, day, 00:00) → Unix seconds`` in ``tz``."""
    ts = pd.Timestamp(year=year, month=month, day=day, hour=0, minute=0, tz=tz)
    return int(ts.tz_convert("UTC").timestamp())


# ---------------------------------------------------------------------------
# build_daily_rollups
# ---------------------------------------------------------------------------


class TestBuildDailyRollups:
    def test_groups_by_local_calendar_day(self):
        # Two timestamps that straddle midnight UTC but both fall on the same
        # local day in America/Denver (UTC-7 in winter = MST). 23:55 UTC and
        # 00:10 UTC on consecutive UTC days are 16:55 and 17:10 on the same
        # Denver day. Compute Unix seconds directly so the test is unambiguous.
        utc_pair = [
            pd.Timestamp("2026-01-15 23:55", tz="UTC").timestamp(),
            pd.Timestamp("2026-01-16 00:10", tz="UTC").timestamp(),
        ]
        rows = [(int(utc_pair[0]), 5.0, 80.0), (int(utc_pair[1]), 5.0, 80.0)]
        df = _frame(rows)
        rollups = build_daily_rollups(df, tz="America/Denver")
        assert len(rollups) == 1
        assert rollups[0]["local_date"] == "2026-01-15"
        assert rollups[0]["tz"] == "America/Denver"

    def test_volume_uses_trapezoidal_integration_in_gallons(self):
        # 1 hour of constant 60 gal/min → 3600 gallons by trapezoid.
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        rows = [(midnight + i, 60.0, 80.0) for i in range(0, 3601, 60)]
        df = _frame(rows)
        rollups = build_daily_rollups(df, tz="UTC")
        assert len(rollups) == 1
        assert rollups[0]["volume_gallons"] == pytest.approx(3600.0, rel=1e-6)

    def test_low_quality_ratio_uses_60_threshold(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        rows = [
            (midnight + 60, 1.0, 80.0),   # good
            (midnight + 120, 1.0, 90.0),  # good
            (midnight + 180, 1.0, 50.0),  # low (≤ 60)
            (midnight + 240, 1.0, 60.0),  # low (boundary inclusive)
        ]
        df = _frame(rows)
        rollups = build_daily_rollups(df, tz="UTC")
        assert rollups[0]["low_quality_ratio"] == pytest.approx(2.0 / 4.0)

    def test_weekday_is_zero_indexed_monday(self):
        # 2026-04-27 is a Monday → weekday 0.
        midnight = _midnight_unix(2026, 4, 27, "UTC")
        df = _frame([(midnight + 60, 1.0, 80.0)])
        rollups = build_daily_rollups(df, tz="UTC")
        assert rollups[0]["weekday"] == 0
        # 2026-04-28 → Tuesday → 1
        midnight_tue = _midnight_unix(2026, 4, 28, "UTC")
        df2 = _frame([(midnight_tue + 60, 1.0, 80.0)])
        rollups2 = build_daily_rollups(df2, tz="UTC")
        assert rollups2[0]["weekday"] == 1

    def test_n_gaps_counts_pauses_above_cap(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        rows = [
            (midnight + 60, 1.0, 80.0),
            (midnight + 75, 1.0, 80.0),     # 15 s gap → < cap (60 s) → not counted
            (midnight + 200, 1.0, 80.0),    # 125 s gap → > cap → counted
            (midnight + 215, 1.0, 80.0),
        ]
        df = _frame(rows)
        rollups = build_daily_rollups(
            df, tz="UTC", healthy_gap_cap_seconds=60.0
        )
        assert rollups[0]["n_gaps"] == 1

    def test_empty_dataframe_returns_empty_list(self):
        df = pd.DataFrame({"timestamp": [], "flow_rate": [], "quality": []})
        assert build_daily_rollups(df) == []

    def test_dst_spring_forward_no_crash_and_correct_dates(self):
        # 2026-03-08 is the US spring-forward day in America/Denver.
        midnight = _midnight_unix(2026, 3, 7, "America/Denver")
        # One sample on 2026-03-07, one on 2026-03-08, one on 2026-03-09.
        rows = [
            (midnight + 3600, 1.0, 80.0),                # 2026-03-07 01:00 MST
            (midnight + 86400 + 3600, 1.0, 80.0),       # 2026-03-08 01:00 (DST shift)
            (midnight + 86400 + 86400 + 3600, 1.0, 80.0),  # 2026-03-09
        ]
        df = _frame(rows)
        rollups = build_daily_rollups(df, tz="America/Denver")
        assert [r["local_date"] for r in rollups] == [
            "2026-03-07",
            "2026-03-08",
            "2026-03-09",
        ]

    def test_dst_fall_back_no_crash(self):
        # 2026-11-01 is the US fall-back day in America/Denver.
        midnight = _midnight_unix(2026, 11, 1, "America/Denver")
        rows = [
            (midnight + 3600, 1.0, 80.0),
            (midnight + 86400 + 3600, 1.0, 80.0),
        ]
        df = _frame(rows)
        rollups = build_daily_rollups(df, tz="America/Denver")
        assert [r["local_date"] for r in rollups] == ["2026-11-01", "2026-11-02"]

    def test_missing_quality_column_no_crash(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        df = pd.DataFrame(
            {
                "timestamp": [midnight + 60, midnight + 120],
                "flow_rate": [1.0, 1.0],
            }
        )
        rollups = build_daily_rollups(df, tz="UTC")
        assert len(rollups) == 1
        assert rollups[0]["low_quality_ratio"] is None  # no quality column ⇒ unknown


# ---------------------------------------------------------------------------
# build_today_partial_rollup
# ---------------------------------------------------------------------------


class TestBuildTodayPartialRollup:
    def test_returns_none_when_no_samples_on_target_day(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        df = _frame([(midnight + 60, 1.0, 80.0)])
        result = build_today_partial_rollup(
            df, target_local_date="2026-05-02", tz="UTC"
        )
        assert result is None

    def test_uses_fraction_hint_for_coverage_denominator(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        # 6 hours of samples at 60 s cadence → 361 samples
        rows = [(midnight + i * 60, 1.0, 80.0) for i in range(361)]
        df = _frame(rows)
        result = build_today_partial_rollup(
            df,
            target_local_date="2026-05-01",
            tz="UTC",
            nominal_interval_seconds=60.0,
            fraction_of_day_elapsed=0.25,  # 6 h of 24 h
        )
        # 0.25 × 86400 / 60 = 360 expected; 361 actual ⇒ ratio ≈ 1.003.
        assert result is not None
        assert result["coverage_ratio"] == pytest.approx(361.0 / 360.0, rel=0.01)

    def test_falls_back_to_midnight_local_span_when_no_hint(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        rows = [(midnight + 60, 1.0, 80.0), (midnight + 3600, 1.0, 80.0)]
        df = _frame(rows)
        # 1 hour span, 2 samples, nominal 60 s → expected ~60, ratio ≈ 0.033.
        result = build_today_partial_rollup(
            df,
            target_local_date="2026-05-01",
            tz="UTC",
            nominal_interval_seconds=60.0,
        )
        assert result is not None
        assert 0.0 < result["coverage_ratio"] < 0.1


# ---------------------------------------------------------------------------
# fraction_of_day_elapsed / today_missing_bucket_ratio
# ---------------------------------------------------------------------------


class TestDayHelpers:
    def test_fraction_of_day_elapsed_at_midnight_is_zero(self):
        midnight = _midnight_unix(2026, 5, 1, "America/Denver")
        assert fraction_of_day_elapsed(
            end_timestamp=float(midnight), tz="America/Denver"
        ) == pytest.approx(0.0, abs=1e-6)

    def test_fraction_of_day_elapsed_at_noon_is_half(self):
        midnight = _midnight_unix(2026, 5, 1, "America/Denver")
        noon = midnight + 12 * 3600
        assert fraction_of_day_elapsed(
            end_timestamp=float(noon), tz="America/Denver"
        ) == pytest.approx(0.5, abs=1e-6)

    def test_today_missing_bucket_ratio_full_coverage_is_zero(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        # 6 hours, one sample per minute → all 6 hour-buckets occupied.
        rows = [(midnight + i * 60, 1.0, 80.0) for i in range(0, 6 * 60 + 1)]
        df = _frame(rows)
        ratio = today_missing_bucket_ratio(
            df,
            target_local_date="2026-05-01",
            tz="UTC",
            fraction_of_day_elapsed=0.25,
        )
        assert ratio == pytest.approx(0.0, abs=1e-6)

    def test_today_missing_bucket_ratio_half_missing(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        # 6 elapsed hours; only the first 2 contain samples (samples land in
        # bucket indices 0 and 1) → 4 of 6 buckets missing.
        rows = [(midnight + i * 60, 1.0, 80.0) for i in range(0, 2 * 60)]
        df = _frame(rows)
        ratio = today_missing_bucket_ratio(
            df,
            target_local_date="2026-05-01",
            tz="UTC",
            fraction_of_day_elapsed=0.25,
        )
        # 6 expected buckets, 2 occupied → 4/6 ≈ 0.667 missing.
        assert ratio == pytest.approx(4.0 / 6.0, abs=1e-6)

    def test_today_missing_bucket_ratio_no_fraction_returns_none(self):
        midnight = _midnight_unix(2026, 5, 1, "UTC")
        df = _frame([(midnight + 60, 1.0, 80.0)])
        assert (
            today_missing_bucket_ratio(
                df, target_local_date="2026-05-01", tz="UTC"
            )
            is None
        )
