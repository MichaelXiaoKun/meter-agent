"""
Tests for the composite meter health score processor.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


_HEALTH_SCORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "meter-status-agent"
    / "processors"
    / "health_score.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("meter_status_health_score", _HEALTH_SCORE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hs = _load_module()


def test_status_only_score_reweights_available_components():
    out = hs.compute_health_score(
        status={
            "online": True,
            "staleness": {"communication_status": "fresh"},
            "signal": {"score": 90, "level": "good"},
        }
    )

    assert out["score"] > 95
    assert out["verdict"] == "healthy"
    assert out["weights_used"] == 0.7
    assert out["components"]["gap_density"]["available"] is False


def test_poor_signal_and_lost_meter_is_unhealthy():
    out = hs.compute_health_score(
        status={
            "online": False,
            "staleness": {"communication_status": "lost"},
            "signal": {"score": 35, "level": "poor"},
        }
    )

    assert out["score"] < 40
    assert out["verdict"] == "unhealthy"


def test_verified_facts_add_gap_and_drift_components():
    out = hs.compute_health_score(
        status={
            "online": True,
            "staleness": {"communication_status": "fresh"},
            "signal": {"score": 80, "level": "degraded"},
        },
        verified_facts={
            "gap_event_count": 3,
            "largest_gap_duration_seconds": 120,
            "cusum_drift": {
                "skipped": False,
                "drift_detected": "upward",
                "positive_alarm_count": 2,
                "negative_alarm_count": 0,
            },
        },
    )

    assert out["weights_used"] == 1.0
    assert out["components"]["gap_density"]["available"] is True
    assert out["components"]["drift"]["available"] is True
    assert out["verdict"] in {"healthy", "degraded"}
