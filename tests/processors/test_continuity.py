"""
Tests for ``processors.continuity.detect_gaps`` and ``detect_zero_flow_periods``.

The gap detector is adaptive (percentile-based) but **capped** by
``sampling_physics.gap_threshold_cap_seconds`` so a meter that pauses longer
than one healthy inter-arrival is always flagged. These tests exercise both.
"""

from __future__ import annotations

import numpy as np
import pytest

from processors.continuity import detect_gaps, detect_zero_flow_periods


def test_empty_or_single_point_returns_no_gaps():
    assert detect_gaps(np.array([])) == []
    assert detect_gaps(np.array([1000.0])) == []


def test_regular_two_second_cadence_has_no_gaps():
    ts = np.arange(1_000_000, 1_000_000 + 300 * 2, 2, dtype=float)
    assert detect_gaps(ts) == []


def test_large_gap_is_detected():
    ts = np.concatenate(
        [
            np.arange(1_000_000, 1_000_000 + 60 * 2, 2, dtype=float),      # 60 × 2 s
            np.arange(1_000_600, 1_000_600 + 60 * 2, 2, dtype=float),      # after 10 min
        ]
    )
    gaps = detect_gaps(ts)
    assert len(gaps) == 1
    assert gaps[0]["duration_seconds"] == pytest.approx(482.0, rel=0.05)
    assert gaps[0]["expected_points_missing"] > 0


def test_cap_flags_one_minute_pause_even_when_percentiles_are_wide(monkeypatch):
    """
    LoRaWAN-ish series that frequently pauses 30 s should still flag a
    90-second pause once we lower the healthy-inter-arrival cap below it.
    """
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "30")
    monkeypatch.setenv("BLUEBOT_GAP_SLACK", "1.0")

    base = np.arange(0, 600, 30, dtype=float)          # 30 s cadence for 10 min
    ts = np.concatenate([base, np.array([base[-1] + 90.0, base[-1] + 120.0])])
    gaps = detect_gaps(ts)
    assert any(g["duration_seconds"] == pytest.approx(90.0) for g in gaps)


def test_expected_interval_override_respected():
    """
    Caller passes an explicit ``expected_interval_seconds`` that dominates the
    nominal; with a 60 s nominal and tolerance 1.5 a 200 s gap is flagged.
    """
    ts = np.arange(0, 600, 60, dtype=float)
    ts = np.concatenate([ts, np.array([ts[-1] + 200.0])])
    gaps = detect_gaps(ts, expected_interval_seconds=60.0)
    assert len(gaps) == 1
    assert gaps[0]["duration_seconds"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Zero-flow periods
# ---------------------------------------------------------------------------


def test_zero_flow_detects_continuous_zero_span():
    ts = np.arange(0, 300, 2, dtype=float)
    vals = np.zeros_like(ts, dtype=float)
    vals[-30:] = 5.0   # last minute has flow
    periods = detect_zero_flow_periods(ts, vals, min_duration_seconds=60.0)
    assert len(periods) == 1
    assert periods[0]["duration_seconds"] >= 60.0


def test_zero_flow_ignores_short_spans():
    ts = np.arange(0, 120, 2, dtype=float)
    vals = np.full_like(ts, 10.0, dtype=float)
    vals[10:15] = 0.0  # ~10 s of zero
    assert detect_zero_flow_periods(ts, vals, min_duration_seconds=60.0) == []


def test_zero_flow_trailing_span_included():
    ts = np.arange(0, 180, 2, dtype=float)
    vals = np.full_like(ts, 10.0, dtype=float)
    vals[-40:] = 0.0   # ~80 s of trailing zero
    periods = detect_zero_flow_periods(ts, vals, min_duration_seconds=60.0)
    assert len(periods) == 1
    assert periods[0]["duration_seconds"] >= 60.0
