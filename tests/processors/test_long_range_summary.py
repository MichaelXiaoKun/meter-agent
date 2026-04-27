from __future__ import annotations

import pandas as pd

from processors.long_range_summary import (
    build_long_range_summary,
    resolve_analysis_mode,
)
from processors.verified_facts import build_verified_facts


def _df() -> pd.DataFrame:
    rows = []
    start = 1_700_000_000
    # Four days of hourly-ish points, with one sparse / low-quality stretch.
    for i in range(96):
        ts = start + i * 3600
        if 36 <= i <= 42:
            quality = 40.0
            flow = 0.0 if i <= 38 else 8.0
        else:
            quality = 95.0
            flow = 1.0 + (i % 6) * 0.1
        rows.append(
            {
                "timestamp": ts,
                "flow_rate": flow,
                "flow_amount": 0.0,
                "quality": quality,
            }
        )
    return pd.DataFrame(rows)


def test_resolve_analysis_mode_defaults_to_summary_for_long_range(monkeypatch) -> None:
    monkeypatch.delenv("BLUEBOT_LONG_RANGE_SECONDS", raising=False)
    assert resolve_analysis_mode(
        "auto",
        start=0,
        end=15 * 86400,
        row_count=10,
    )["resolved_mode"] == "summary"


def test_resolve_analysis_mode_keeps_short_range_detailed() -> None:
    assert resolve_analysis_mode(
        "auto",
        start=0,
        end=6 * 3600,
        row_count=10,
    )["resolved_mode"] == "detailed"


def test_resolve_analysis_mode_explicit_detailed_wins_for_long_range() -> None:
    out = resolve_analysis_mode(
        "detailed",
        start=0,
        end=60 * 86400,
        row_count=200_000,
    )
    assert out["resolved_mode"] == "detailed"
    assert out["reasons"] == ["explicit_detailed"]


def test_resolve_analysis_mode_uses_row_threshold(monkeypatch) -> None:
    monkeypatch.setenv("BLUEBOT_LONG_RANGE_ROWS", "5")
    out = resolve_analysis_mode("auto", start=0, end=60, row_count=6)
    assert out["resolved_mode"] == "summary"
    assert "row_count_exceeds_threshold" in out["reasons"]


def test_long_range_rollups_are_bounded_and_include_anomaly_windows() -> None:
    df = _df()
    facts = build_verified_facts(df)

    summary = build_long_range_summary(df, facts, max_anomaly_windows=3)

    assert summary["rollup_highlights"]["daily_window_count"] >= 4
    assert summary["rollup_highlights"]["six_hour_window_count"] >= 16
    assert summary["rollup_highlights"]["anomaly_window_limit"] == 3
    assert len(summary["anomaly_windows"]) <= 3

    first_daily = summary["daily_rollups"][0]
    assert set(first_daily) >= {
        "n_points",
        "expected_points_approx",
        "coverage_ratio",
        "coverage_status",
        "flow",
        "quality_issue_share",
        "zero_flow_period_count",
        "largest_gap_seconds",
        "anomaly_flags",
    }
    assert set(first_daily["flow"]) == {"min", "median", "max", "mean"}
