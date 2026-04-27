from __future__ import annotations

import numpy as np

from processors.data_adequacy import (
    REASON_BELOW_MINIMUM,
    REASON_EMPTY,
    REASON_OK,
    REASON_TOO_MANY_GAPS,
    check_adequacy,
    estimate_window_seconds,
)


def test_estimate_window_seconds_uses_target_cadence_and_safety() -> None:
    assert estimate_window_seconds(10, cadence_seconds=2.0, safety=2.0) == 60
    assert estimate_window_seconds(100, cadence_seconds=2.0, safety=2.0) == 400


def test_check_adequacy_empty_series() -> None:
    report = check_adequacy(np.array([]), {"min_points": 10, "max_gap_pct": 50})
    assert report["ok"] is False
    assert report["reason"] == REASON_EMPTY
    assert report["actual_points"] == 0


def test_check_adequacy_accepts_dense_series() -> None:
    ts = np.arange(100, dtype=float)
    report = check_adequacy(
        ts,
        {"min_points": 50, "ideal_points": 100, "max_gap_pct": 10},
        cadence_seconds=1.0,
    )
    assert report["ok"] is True
    assert report["reason"] == REASON_OK
    assert report["gap_pct"] == 0.0


def test_check_adequacy_flags_below_minimum() -> None:
    ts = np.arange(5, dtype=float)
    report = check_adequacy(ts, {"min_points": 10, "max_gap_pct": 100}, cadence_seconds=1.0)
    assert report["ok"] is False
    assert report["reason"] == REASON_BELOW_MINIMUM


def test_check_adequacy_flags_patchy_series() -> None:
    ts = np.array([0, 1, 2, 100, 101, 102], dtype=float)
    report = check_adequacy(ts, {"min_points": 5, "max_gap_pct": 50}, cadence_seconds=1.0)
    assert report["ok"] is False
    assert report["reason"] == REASON_TOO_MANY_GAPS
    assert report["gap_pct"] > 50.0
