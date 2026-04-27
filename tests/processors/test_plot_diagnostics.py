from __future__ import annotations

import numpy as np

from processors.plot_diagnostics import build_diagnostic_markers, diagnostic_caption


def test_cusum_upward_drift_creates_marker():
    ts = np.arange(1_700_000_000, 1_700_000_120, 2, dtype=float)
    flow = np.linspace(1.0, 3.0, len(ts))
    quality = np.full_like(ts, 90.0)
    facts = {
        "cusum_drift": {
            "skipped": False,
            "drift_detected": "upward",
            "positive_alarm_count": 4,
            "negative_alarm_count": 0,
            "first_alarm_timestamp": int(ts[20]),
        }
    }
    markers = build_diagnostic_markers(ts, flow, quality, facts)
    drift = next(marker for marker in markers if marker["type"] == "drift")
    assert drift["label"] == "Upward drift alarm"
    assert drift["severity"] == "medium"
    assert drift["source"] == "cusum_drift"


def test_gap_quality_flatline_and_baseline_markers():
    base = 1_700_000_000.0
    ts = np.array([base, base + 2, base + 4, base + 800, base + 802], dtype=float)
    flow = np.full_like(ts, 0.12)
    quality = np.array([90.0, 90.0, 90.0, 50.0, 52.0])
    facts = {
        "max_healthy_inter_arrival_seconds": 60,
        "signal_quality": {
            "flagged_percent": 40.0,
            "low_quality_intervals": [
                {
                    "start_timestamp": int(base + 800),
                    "end_timestamp": int(base + 802),
                    "duration_seconds": 2.0,
                    "point_count": 2,
                    "mean_quality_score": 51.0,
                }
            ],
        },
        "flatline": {"flag": "near_constant", "note": "Flow is nearly constant."},
        "quiet_flow_baseline": {"quiet_flow_median": 0.12},
        "anomaly_attribution": {
            "primary_type": "possible_leak_or_baseline_rise",
            "severity": "low",
            "summary": "Possible baseline rise.",
            "next_checks": ["Check whether usage was expected"],
        },
    }
    markers = build_diagnostic_markers(ts, flow, quality, facts)
    assert {"gap", "low_quality", "flatline", "baseline"}.issubset(
        {marker["type"] for marker in markers}
    )
    cap = diagnostic_caption(markers, facts)
    assert cap["plot_type"] == "diagnostic_timeline"
    assert cap["marker_count"] == len(markers)
    assert cap["next_actions"] == ["Check whether usage was expected"]
