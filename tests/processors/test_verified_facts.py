"""
End-to-end (in-process) test of ``build_verified_facts`` + the prompt slimmer.

No subprocess or HTTP: we hand the function a synthetic DataFrame, confirm the
bundle shape, and confirm the baseline-quality stub is stripped from the
prompt copy but retained in the full bundle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processors.verified_facts import (
    build_verified_facts,
    slim_verified_facts_for_prompt,
)


@pytest.fixture
def synthetic_df() -> pd.DataFrame:
    # 30 minutes of 2-second Wi-Fi cadence: quiet first half, flow second half.
    ts = np.arange(1_700_000_000, 1_700_000_000 + 30 * 60, 2, dtype=float)
    n = len(ts)
    flow = np.concatenate([np.zeros(n // 2), np.full(n - n // 2, 8.0)])
    quality = np.ones(n, dtype=float)
    return pd.DataFrame({"timestamp": ts, "flow_rate": flow, "quality": quality})


def test_build_verified_facts_core_keys(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    for key in [
        "n_rows",
        "flow_rate_descriptive",
        "sampling_median_interval_seconds",
        "sampling_p75_interval_seconds",
        "sampling_irregular",
        "max_healthy_inter_arrival_seconds",
        "sampling_caps",
        "gap_event_count",
        "largest_gap_duration_seconds",
        "zero_flow_period_count",
        "signal_quality",
        "quiet_flow_baseline",
        "flatline",
        "cusum_drift",
        "coverage_6h",
        "baseline_quality",
        "filter_applied",
        "anomaly_attribution",
    ]:
        assert key in facts, f"missing key: {key}"

    assert facts["n_rows"] == len(synthetic_df)
    assert facts["sampling_median_interval_seconds"] == pytest.approx(2.0)
    assert facts["gap_event_count"] == 0
    assert facts["zero_flow_period_count"] >= 1
    assert facts["cusum_drift"]["skipped"] is False
    assert facts["anomaly_attribution"]["primary_type"] in {
        "normal",
        "real_flow_change",
        "possible_leak_or_baseline_rise",
        "sensor_or_install_issue",
        "communications_or_sampling_issue",
        "insufficient_data",
    }


def test_empty_dataframe_short_circuits():
    facts = build_verified_facts(pd.DataFrame(columns=["timestamp", "flow_rate"]))
    assert facts["n_rows"] == 0
    assert facts["error"] == "empty_dataframe"
    assert facts["baseline_quality"]["state"] == "not_requested"
    assert facts["filter_applied"]["state"] == "not_requested"


def test_baseline_quality_stub_default_state(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    bq = facts["baseline_quality"]
    assert bq["state"] == "not_requested"
    assert bq["reliable"] is False


def test_slim_drops_not_requested_baseline_quality(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    slim = slim_verified_facts_for_prompt(facts)

    # The full bundle keeps the stub; the prompt copy drops it.
    assert "baseline_quality" in facts
    assert "baseline_quality" not in slim


def test_slim_keeps_non_default_baseline_quality(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    facts["baseline_quality"] = {
        "state": "no_history",
        "reliable": False,
        "reasons_refused": ["No reference days supplied."],
    }
    slim = slim_verified_facts_for_prompt(facts)
    assert slim["baseline_quality"]["state"] == "no_history"


def test_filter_applied_stub_default_state(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    fa = facts["filter_applied"]
    assert fa["state"] == "not_requested"
    assert fa["applied"] is False


def test_slim_drops_not_requested_filter_applied(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    slim = slim_verified_facts_for_prompt(facts)
    assert "filter_applied" in facts
    assert "filter_applied" not in slim


def test_slim_keeps_compact_anomaly_attribution(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    facts["anomaly_attribution"]["evidence"] = [
        {"code": f"E{i}", "message": f"evidence {i}", "source": "test"}
        for i in range(10)
    ]
    slim = slim_verified_facts_for_prompt(facts)
    attr = slim["anomaly_attribution"]
    assert attr["primary_type"] == facts["anomaly_attribution"]["primary_type"]
    assert len(attr["evidence"]) == 3


def test_slim_keeps_non_default_filter_applied(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    facts["filter_applied"] = {
        "state": "applied",
        "applied": True,
        "n_rows_input": 100,
        "n_rows_kept": 42,
        "fraction_kept": 0.42,
        "predicate_used": {"timezone": "America/Denver", "weekdays": [0, 1, 2, 3, 4]},
    }
    slim = slim_verified_facts_for_prompt(facts)
    assert slim["filter_applied"]["state"] == "applied"
    assert slim["filter_applied"]["n_rows_kept"] == 42


def test_filters_apply_before_downstream_metrics(synthetic_df):
    ts = synthetic_df["timestamp"].to_numpy()
    midpoint = len(ts) // 2
    facts = build_verified_facts(
        synthetic_df,
        filters={
            "include_sub_ranges": [
                {"start": int(ts[0]), "end": int(ts[midpoint])},
            ],
        },
    )

    fa = facts["filter_applied"]
    assert fa["state"] == "applied"
    assert fa["n_rows_input"] == len(synthetic_df)
    assert fa["n_rows_kept"] == midpoint
    assert facts["n_rows"] == midpoint
    assert facts["flow_rate_descriptive"]["max"] == pytest.approx(0.0)


def test_filter_refusal_short_circuits_downstream_metrics(synthetic_df):
    facts = build_verified_facts(
        synthetic_df,
        filters={"weekdays": [0]},
    )

    assert facts["filter_applied"]["state"] == "invalid_spec"
    assert facts["baseline_quality"]["state"] == "not_requested"
    assert "flow_rate_descriptive" not in facts
    assert "anomaly_attribution" not in facts


def test_slim_omits_low_quality_intervals_but_keeps_count(synthetic_df):
    facts = build_verified_facts(synthetic_df)
    facts["signal_quality"]["low_quality_intervals"] = [
        {"start": 0, "end": 10},
        {"start": 30, "end": 40},
    ]
    slim = slim_verified_facts_for_prompt(facts)
    sq = slim["signal_quality"]
    assert "low_quality_intervals" not in sq
    assert sq["low_quality_intervals_omitted"] == 2
