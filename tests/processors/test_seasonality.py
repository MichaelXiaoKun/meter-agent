"""
Unit tests for ``processors.seasonality``.
"""

from __future__ import annotations

import pandas as pd

from processors.seasonality import (
    STATE_INSUFFICIENT_HISTORY,
    STATE_READY,
    STATE_SCORED,
    build_diurnal_profile,
    score_against_diurnal,
)


def _local_ts(tz: str, date_s: str, hour: int) -> int:
    ts = pd.Timestamp(f"{date_s} {hour:02d}:00", tz=tz)
    return int(ts.tz_convert("UTC").timestamp())


def _df(rows: list[tuple[int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [r[0] for r in rows],
            "flow_rate": [r[1] for r in rows],
            "quality": [90.0 for _ in rows],
        }
    )


def test_build_diurnal_profile_requires_seven_local_days():
    rows = [
        (_local_ts("UTC", f"2026-04-{day:02d}", 7), 10.0)
        for day in range(1, 7)
    ]

    profile = build_diurnal_profile(_df(rows), tz="UTC")

    assert profile["state"] == STATE_INSUFFICIENT_HISTORY
    assert profile["reliable"] is False
    assert profile["n_days_used"] == 6


def test_build_diurnal_profile_computes_hourly_quantiles():
    values = [10.0, 11.0, 9.0, 10.0, 12.0, 8.0, 10.0]
    rows = [
        (_local_ts("UTC", f"2026-04-{idx + 1:02d}", 7), value)
        for idx, value in enumerate(values)
    ]

    profile = build_diurnal_profile(_df(rows), tz="UTC")

    assert profile["state"] == STATE_READY
    assert profile["reliable"] is True
    assert profile["n_days_used"] == 7
    assert profile["hour"]["7"] == 10.0
    assert profile["p25"]["7"] == 9.5
    assert profile["p75"]["7"] == 10.5
    assert profile["hour"]["6"] is None
    assert len(profile["hour"]) == 24


def test_score_against_diurnal_flags_known_hour_spike():
    values = [10.0, 11.0, 9.0, 10.0, 12.0, 8.0, 10.0]
    reference_rows = [
        (_local_ts("UTC", f"2026-04-{idx + 1:02d}", 7), value)
        for idx, value in enumerate(values)
    ]
    profile = build_diurnal_profile(_df(reference_rows), tz="UTC")
    today = _df([(_local_ts("UTC", "2026-04-08", 7), 20.0)])

    score = score_against_diurnal(today, profile)

    assert score["state"] == STATE_SCORED
    assert score["n_hours_scored"] == 1
    assert score["departure_score"] > 10.0
    assert score["hourly_scores"]["7"]["observed_median_flow_rate"] == 20.0


def test_diurnal_profile_handles_spring_forward_dst_gap():
    tz = "America/Denver"
    start = pd.Timestamp("2026-03-07 00:00", tz=tz).tz_convert("UTC")
    end = pd.Timestamp("2026-03-15 00:00", tz=tz).tz_convert("UTC")
    times = pd.date_range(start, end, freq="1h", inclusive="left")
    df = _df([(int(ts.timestamp()), 1.0) for ts in times])

    profile = build_diurnal_profile(df, tz=tz)

    assert profile["state"] == STATE_READY
    assert profile["n_days_used"] == 8
    assert len(profile["hour"]) == 24
    assert profile["n_samples_by_hour"]["2"] < profile["n_samples_by_hour"]["3"]
