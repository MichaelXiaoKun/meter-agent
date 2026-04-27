from __future__ import annotations

import numpy as np

from processors.change_point import DATA_REQUIREMENTS, compute_cusum, compute_cusum_facts


def test_cusum_skips_when_data_is_inadequate() -> None:
    ts = np.arange(DATA_REQUIREMENTS["min_points"] - 1, dtype=float)
    values = np.zeros_like(ts)
    result = compute_cusum(ts, values)
    assert result["skipped"] is True
    assert result["algorithm"] == "cusum_drift"
    assert result["adequacy"]["ok"] is False


def test_cusum_reports_no_drift_for_stable_series() -> None:
    ts = np.arange(300, dtype=float)
    values = np.zeros_like(ts)
    result = compute_cusum(ts, values, target_mean=0.0, target_std=1.0)
    assert result["skipped"] is False
    assert result["drift_detected"] == "none"
    assert result["positive_alarm_count"] == 0
    assert result["negative_alarm_count"] == 0


def test_cusum_detects_upward_drift() -> None:
    ts = np.arange(400, dtype=float)
    values = np.concatenate([np.zeros(250), np.full(150, 5.0)])
    result = compute_cusum(ts, values, target_mean=0.0, target_std=1.0)
    assert result["skipped"] is False
    assert result["drift_detected"] == "upward"
    assert result["positive_alarm_count"] > 0
    assert result["first_alarm_timestamp"] is not None


def test_cusum_facts_omit_full_alarm_arrays() -> None:
    ts = np.arange(400, dtype=float)
    values = np.concatenate([np.zeros(250), np.full(150, 5.0)])
    facts = compute_cusum_facts(ts, values)
    assert facts["skipped"] is False
    assert "positive_alarms" not in facts
    assert facts["positive_alarms_omitted"] > 0
    assert "first_positive_alarm" in facts
