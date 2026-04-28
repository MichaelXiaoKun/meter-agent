"""
Tests for threshold event detection.
"""

from __future__ import annotations

import pandas as pd
import pytest

from processors.event_detector import detect_threshold_events


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [0, 60, 120, 180, 240, 300, 360],
            "flow_rate": [0, 12, 14, 3, 16, 17, 0],
            "quality": [90, 90, 55, 50, 90, 90, 90],
        }
    )


def test_detects_contiguous_flow_events_with_min_duration():
    events = detect_threshold_events(
        _df(),
        predicate="flow > 10",
        min_duration_seconds=60,
    )

    assert len(events) == 2
    assert events[0]["start_ts"] == 60
    assert events[0]["end_ts"] == 120
    assert events[0]["duration_seconds"] == 60
    assert events[0]["peak_value"] == 14
    assert events[0]["sample_count"] == 2
    assert events[1]["start_ts"] == 240
    assert events[1]["end_ts"] == 300


def test_filters_out_short_events():
    events = detect_threshold_events(
        _df(),
        predicate="quality < 60",
        min_duration_seconds=120,
    )

    assert events == []


def test_zero_duration_events_are_allowed_when_minimum_is_zero():
    events = detect_threshold_events(
        _df(),
        predicate="flow == 0",
        min_duration_seconds=0,
    )

    assert len(events) == 2
    assert all(ev["duration_seconds"] == 0 for ev in events)


@pytest.mark.parametrize(
    "predicate",
    ["flow between 1 and 2", "pressure > 10", "", "flow ~= 10"],
)
def test_invalid_predicate_refuses(predicate):
    with pytest.raises(ValueError):
        detect_threshold_events(
            _df(),
            predicate=predicate,
            min_duration_seconds=0,
        )


def test_missing_quality_column_refuses():
    with pytest.raises(ValueError):
        detect_threshold_events(
            _df().drop(columns=["quality"]),
            predicate="quality < 60",
            min_duration_seconds=0,
        )
