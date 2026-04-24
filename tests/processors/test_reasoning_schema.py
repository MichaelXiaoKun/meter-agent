"""
Tests for ``processors.reasoning_schema`` — the compact evidence / hypothesis
/ next-check anchor block derived from ``verified_facts``.

The schema is contractually bounded (fixed field names, capped list lengths)
and deterministic, so these tests exercise the full shape + each of the code
buckets an orchestrator / LLM downstream will rely on.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from processors.reasoning_schema import (
    _MAX_EVIDENCE,
    _MAX_HYPOTHESES,
    _MAX_NEXT_CHECKS,
    build_reasoning_schema,
    classify_regime,
    schema_to_compact_markdown,
)


# ---------------------------------------------------------------------------
# Helpers: minimal facts builders so each test reads at a glance
# ---------------------------------------------------------------------------


def _base_facts(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "n_rows": 1000,
        "flow_rate_descriptive": {
            "min": 0.0,
            "median": 1.0,
            "max": 2.0,
            "mean": 1.0,
            "std": 0.05,
            "p25": 0.9,
            "p75": 1.1,
            "p95": 1.5,
            "cv": 0.05,
        },
        "sampling_median_interval_seconds": 2.0,
        "sampling_p75_interval_seconds": 2.0,
        "sampling_irregular": False,
        "max_healthy_inter_arrival_seconds": 60.0,
        "gap_event_count": 0,
        "largest_gap_duration_seconds": 0.0,
        "zero_flow_period_count": 0,
        "signal_quality": {
            "flagged_count": 0,
            "total_count": 1000,
            "longest_low_quality_stretch": None,
        },
        "flatline": {"flag": None, "unique_flow_values": 200, "coefficient_of_variation": 0.05},
        "coverage_6h": {"n_buckets": 4, "buckets_with_issues": 0, "buckets": []},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Shape / contract
# ---------------------------------------------------------------------------


def test_schema_shape_is_stable():
    schema = build_reasoning_schema(_base_facts())
    for key in (
        "schema_version",
        "regime",
        "evidence",
        "hypotheses",
        "next_checks",
        "conflict_policy",
        "context",
    ):
        assert key in schema, f"missing key: {key}"
    assert schema["schema_version"] == 1
    assert isinstance(schema["evidence"], list)
    assert isinstance(schema["hypotheses"], list)
    assert isinstance(schema["next_checks"], list)
    assert "trust" in schema["conflict_policy"].lower()


def test_schema_is_bounded_by_design():
    # A deliberately messy facts payload that should trigger nearly every code.
    facts = _base_facts(
        gap_event_count=5,
        largest_gap_duration_seconds=3600.0,
        sampling_irregular=True,
        sampling_median_interval_seconds=2.0,
        sampling_p75_interval_seconds=10.0,
        zero_flow_period_count=12,
        signal_quality={
            "flagged_count": 400,
            "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 7200.0},
        },
        flatline={"flag": "near_constant_flow", "unique_flow_values": 2, "coefficient_of_variation": 0.001},
        coverage_6h={"n_buckets": 4, "buckets_with_issues": 3, "buckets": []},
    )
    schema = build_reasoning_schema(facts)
    assert len(schema["evidence"]) <= _MAX_EVIDENCE
    assert len(schema["hypotheses"]) <= _MAX_HYPOTHESES
    assert len(schema["next_checks"]) <= _MAX_NEXT_CHECKS


def test_empty_facts_yields_no_data_regime_and_clean_schema():
    schema = build_reasoning_schema({"n_rows": 0, "error": "empty_dataframe"})
    assert schema["regime"] == "NO_DATA"
    assert schema["evidence"] == []
    assert schema["hypotheses"] == []
    assert schema["next_checks"] == []


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------


def test_regime_steady_low_flow_when_cv_small_and_median_low():
    facts = _base_facts(
        flow_rate_descriptive={
            "median": 0.4, "mean": 0.4, "min": 0.3, "max": 0.5, "std": 0.02,
            "p25": 0.38, "p75": 0.42, "p95": 0.45, "cv": 0.05,
        }
    )
    assert classify_regime(facts) == "STEADY_LOW_FLOW"


def test_regime_intermittent_burst_when_p95_dwarfs_median():
    facts = _base_facts(
        flow_rate_descriptive={
            "median": 0.5, "mean": 2.0, "min": 0.0, "max": 50.0, "std": 8.0,
            "p25": 0.2, "p75": 1.0, "p95": 40.0, "cv": 4.0,
        }
    )
    assert classify_regime(facts) == "INTERMITTENT_BURST"


def test_regime_zero_flow_dominant():
    facts = _base_facts(
        flow_rate_descriptive={
            "median": 0.0, "mean": 0.01, "min": 0.0, "max": 0.1, "std": 0.01,
            "p25": 0.0, "p75": 0.0, "p95": 0.05, "cv": 1.0,
        },
        zero_flow_period_count=3,
    )
    assert classify_regime(facts) == "ZERO_FLOW_DOMINANT"


def test_regime_constant_value_when_flatline_flag_set():
    facts = _base_facts(
        flatline={"flag": "constant_flow_series", "unique_flow_values": 1, "coefficient_of_variation": 0.0}
    )
    assert classify_regime(facts) == "CONSTANT_VALUE"


def test_regime_noisy_when_quality_widely_low():
    facts = _base_facts(
        signal_quality={"flagged_count": 400, "total_count": 1000, "longest_low_quality_stretch": None},
    )
    assert classify_regime(facts) == "NOISY_OR_INSTALL_ISSUE"


# ---------------------------------------------------------------------------
# Evidence codes
# ---------------------------------------------------------------------------


def test_evidence_flags_long_gap():
    facts = _base_facts(gap_event_count=2, largest_gap_duration_seconds=900.0)
    schema = build_reasoning_schema(facts)
    codes = [e["code"] for e in schema["evidence"]]
    assert "E_GAP_LONG" in codes


def test_evidence_flags_quality_drop_and_sustained():
    facts = _base_facts(
        signal_quality={
            "flagged_count": 300, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 3600.0},
        }
    )
    schema = build_reasoning_schema(facts)
    codes = {e["code"] for e in schema["evidence"]}
    assert {"E_QUALITY_DROP", "E_LOW_QUALITY_SUSTAINED"} <= codes


def test_evidence_flags_sampling_irregular_when_flag_is_true():
    facts = _base_facts(
        sampling_irregular=True,
        sampling_median_interval_seconds=2.0,
        sampling_p75_interval_seconds=8.0,
    )
    schema = build_reasoning_schema(facts)
    codes = {e["code"] for e in schema["evidence"]}
    assert "E_SAMPLING_IRREGULAR" in codes


def test_evidence_omitted_when_nothing_abnormal():
    schema = build_reasoning_schema(_base_facts())
    assert schema["evidence"] == []
    assert schema["hypotheses"] == []
    assert schema["next_checks"] == []


# ---------------------------------------------------------------------------
# Hypothesis routing
# ---------------------------------------------------------------------------


def test_comms_instability_fires_on_gap_plus_coverage():
    facts = _base_facts(
        gap_event_count=2,
        largest_gap_duration_seconds=1200.0,
        coverage_6h={"n_buckets": 4, "buckets_with_issues": 2, "buckets": []},
    )
    schema = build_reasoning_schema(facts)
    codes = {h["code"] for h in schema["hypotheses"]}
    assert "H_COMMS_INSTABILITY" in codes
    checks = {c["action"] for c in schema["next_checks"]}
    assert "check_uplink_rssi_and_packet_loss" in checks


def test_sensor_install_fires_on_sustained_low_quality():
    facts = _base_facts(
        signal_quality={
            "flagged_count": 200, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 3600.0},
        }
    )
    schema = build_reasoning_schema(facts)
    codes = {h["code"] for h in schema["hypotheses"]}
    assert "H_SENSOR_OR_INSTALL_ISSUE" in codes


def test_air_bubbles_fires_on_intermittent_quality_without_sustained_stretch():
    facts = _base_facts(
        signal_quality={
            "flagged_count": 80, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 60.0},  # below 5-min threshold
        }
    )
    schema = build_reasoning_schema(facts)
    codes = {h["code"] for h in schema["hypotheses"]}
    assert "H_AIR_BUBBLES_OR_DRAINAGE" in codes
    assert "H_SENSOR_OR_INSTALL_ISSUE" not in codes


def test_meter_offline_fires_on_zero_flow_plus_low_quality():
    facts = _base_facts(
        zero_flow_period_count=3,
        signal_quality={
            "flagged_count": 100, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 1800.0},
        },
    )
    schema = build_reasoning_schema(facts)
    codes = {h["code"] for h in schema["hypotheses"]}
    assert "H_METER_OFFLINE_OR_DRAINED" in codes


# ---------------------------------------------------------------------------
# Next-check ordering & priority
# ---------------------------------------------------------------------------


def test_next_checks_follow_playbook_priority():
    facts = _base_facts(
        gap_event_count=1, largest_gap_duration_seconds=900.0,
        signal_quality={
            "flagged_count": 150, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 1800.0},
        },
    )
    schema = build_reasoning_schema(facts)
    assert len(schema["next_checks"]) >= 1
    # Priority numbering must be 1, 2, 3, …
    for idx, nc in enumerate(schema["next_checks"], start=1):
        assert nc["priority"] == idx


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_markdown_rendering_contains_headline_codes():
    facts = _base_facts(
        gap_event_count=1, largest_gap_duration_seconds=900.0,
        signal_quality={
            "flagged_count": 200, "total_count": 1000,
            "longest_low_quality_stretch": {"duration_seconds": 3600.0},
        },
    )
    schema = build_reasoning_schema(facts)
    md = schema_to_compact_markdown(schema)
    assert "Reasoning anchors" in md
    assert "regime:" in md
    assert "evidence:" in md
    assert "hypotheses:" in md
    assert "next_checks:" in md
    # The codes themselves should appear literally so the LLM can cite them.
    assert "E_GAP_LONG" in md or "E_QUALITY_DROP" in md


def test_markdown_rendering_handles_empty_schema():
    md = schema_to_compact_markdown({})
    assert md == ""


def test_confidence_is_in_unit_interval():
    facts = _base_facts(
        gap_event_count=2, largest_gap_duration_seconds=600.0,
        coverage_6h={"n_buckets": 4, "buckets_with_issues": 2, "buckets": []},
    )
    schema = build_reasoning_schema(facts)
    for h in schema["hypotheses"]:
        c = h.get("confidence")
        assert isinstance(c, (int, float))
        assert 0.0 <= c <= 1.0


def test_context_propagates_network_type():
    schema = build_reasoning_schema(_base_facts(), network_type="wifi")
    assert schema["context"]["network_type"] == "wifi"
