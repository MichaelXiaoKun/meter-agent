from __future__ import annotations

from copy import deepcopy

from processors.anomaly_attribution import build_anomaly_attribution


def _base_facts() -> dict:
    return {
        "n_rows": 500,
        "cusum_drift": {
            "skipped": False,
            "adequacy": {
                "ok": True,
                "reason": "ok",
                "actual_points": 500,
                "target_min": 200,
                "gap_pct": 0.0,
            },
            "drift_detected": "none",
            "positive_alarm_count": 0,
            "negative_alarm_count": 0,
            "first_alarm_timestamp": None,
        },
        "gap_event_count": 0,
        "largest_gap_duration_seconds": 0.0,
        "max_healthy_inter_arrival_seconds": 60.0,
        "coverage_6h": {"n_buckets": 4, "buckets_with_issues": 0},
        "signal_quality": {
            "flagged_count": 0,
            "total_count": 500,
            "longest_low_quality_stretch": None,
        },
        "flatline": {"flag": None},
        "quiet_flow_baseline": {"quiet_flow_median": 0.0},
        "zero_flow_period_count": 2,
    }


def test_clean_stable_data_is_normal():
    result = build_anomaly_attribution(_base_facts())
    assert result["primary_type"] == "normal"
    assert result["severity"] == "none"
    assert result["confidence"] == "high"


def test_cusum_upward_with_adequacy_and_low_gaps_is_real_flow_change():
    facts = _base_facts()
    facts["cusum_drift"]["drift_detected"] = "upward"
    facts["cusum_drift"]["positive_alarm_count"] = 6
    facts["zero_flow_period_count"] = 0

    result = build_anomaly_attribution(facts)

    assert result["primary_type"] == "real_flow_change"
    assert result["severity"] == "medium"
    assert result["confidence"] == "high"
    assert any(ev["code"] == "CUSUM_DRIFT" for ev in result["evidence"])


def test_cusum_skipped_below_minimum_is_insufficient_data():
    facts = _base_facts()
    facts["cusum_drift"] = {
        "skipped": True,
        "adequacy": {
            "ok": False,
            "reason": "below_minimum",
            "actual_points": 40,
            "target_min": 200,
            "gap_pct": 0.0,
        },
    }

    result = build_anomaly_attribution(facts)

    assert result["primary_type"] == "insufficient_data"
    assert result["confidence"] == "high"


def test_major_gaps_and_sparse_coverage_is_comms_sampling_issue():
    facts = _base_facts()
    facts["cusum_drift"]["adequacy"] = {
        "ok": False,
        "reason": "too_many_gaps",
        "actual_points": 500,
        "target_min": 200,
        "gap_pct": 40.0,
    }
    facts["cusum_drift"]["skipped"] = True
    facts["gap_event_count"] = 3
    facts["largest_gap_duration_seconds"] = 1800.0
    facts["coverage_6h"] = {"n_buckets": 4, "buckets_with_issues": 3}

    result = build_anomaly_attribution(facts)

    assert result["primary_type"] == "communications_or_sampling_issue"
    assert result["severity"] == "high"


def test_sustained_low_quality_or_flatline_is_sensor_install_issue():
    facts = _base_facts()
    facts["signal_quality"] = {
        "flagged_count": 220,
        "total_count": 500,
        "longest_low_quality_stretch": {"duration_seconds": 1200.0},
    }
    facts["flatline"] = {"flag": "near_constant_flow"}

    result = build_anomaly_attribution(facts)

    assert result["primary_type"] == "sensor_or_install_issue"
    assert result["confidence"] == "high"


def test_quiet_nonzero_baseline_is_possible_leak_or_baseline_rise():
    facts = deepcopy(_base_facts())
    facts["quiet_flow_baseline"] = {"quiet_flow_median": 0.35}
    facts["zero_flow_period_count"] = 0

    result = build_anomaly_attribution(facts)

    assert result["primary_type"] == "possible_leak_or_baseline_rise"
    assert result["severity"] == "low"
    assert "heuristic" in result["summary"]
