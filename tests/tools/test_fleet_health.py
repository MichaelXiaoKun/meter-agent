"""
Tests for ``tools.fleet_health``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from tools import fleet_health as fh


_HEALTH_SCORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "meter-status-agent"
    / "processors"
    / "health_score.py"
)


def _load_health_score_module():
    spec = importlib.util.spec_from_file_location(
        "meter_status_health_score_for_fleet",
        _HEALTH_SCORE_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hs = _load_health_score_module()


def _profile_ok(serial: str, *, label: str | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "serial_number": serial,
        "network_type": "wifi",
        "profile": {
            "serialNumber": serial,
            "label": label or f"Meter {serial}",
            "deviceTimeZone": "America/Denver",
            "active": True,
        },
        "error": None,
    }


def _profile_err(serial: str, error: str = "profile failed") -> dict[str, Any]:
    return {
        "success": False,
        "serial_number": serial,
        "network_type": None,
        "profile": None,
        "error": error,
    }


def _status_ok(
    serial: str,
    *,
    score: float | None = 90.0,
    verdict: str | None = "healthy",
    signal_score: int = 88,
    comm_status: str = "fresh",
    weakest: str = "signal_quality",
) -> dict[str, Any]:
    return {
        "success": True,
        "status_data": {
            "serial_number": serial,
            "online": True,
            "staleness": {
                "seconds_since": 42,
                "communication_status": comm_status,
                "status_description": "ok",
            },
            "signal": {
                "score": signal_score,
                "level": "good" if signal_score >= 70 else "poor",
                "reliable": signal_score >= 70,
                "action_needed": signal_score < 70,
                "interpretation": "weak signal" if signal_score < 70 else "good signal",
            },
            "health_score": {
                "score": score,
                "verdict": verdict,
                "components": {
                    "staleness": {
                        "available": True,
                        "score": 100,
                        "reason": "fresh telemetry",
                    },
                    weakest: {
                        "available": True,
                        "score": 35,
                        "reason": f"{weakest} weak",
                    },
                },
            },
        },
        "error": None,
    }


def _status_err(serial: str, error: str = "status failed") -> dict[str, Any]:
    return {"success": False, "status_data": None, "error": error}


def test_rank_fleet_by_health_sorts_lowest_health_first(monkeypatch):
    profiles = {
        "BB1": _profile_ok("BB1", label="Healthy"),
        "BB2": _profile_ok("BB2", label="Needs attention"),
        "BB3": _profile_ok("BB3", label="Middle"),
    }
    statuses = {
        "BB1": _status_ok("BB1", score=95, verdict="healthy"),
        "BB2": _status_ok("BB2", score=42, verdict="unhealthy", signal_score=40),
        "BB3": _status_ok("BB3", score=75, verdict="degraded", weakest="gap_density"),
    }
    monkeypatch.setattr(fh, "get_meter_profile", lambda serial, token: profiles[serial])
    monkeypatch.setattr(
        fh,
        "check_meter_status",
        lambda serial, token, *, anthropic_api_key=None: statuses[serial],
    )

    out = fh.rank_fleet_by_health(["BB1", "BB2", "BB3"], "tok")

    assert out["success"] is True
    assert [m["serial_number"] for m in out["meters"]] == ["BB2", "BB3", "BB1"]
    assert out["meters"][0]["health_score"] == 42
    assert out["meters"][0]["health_verdict"] == "unhealthy"
    assert out["meters"][0]["top_concern"] == "signal_quality: signal_quality weak"
    assert out["meters"][0]["label"] == "Needs attention"
    assert out["failed_serials"] is None
    assert out["truncated"] is False


def test_rank_fleet_by_health_uses_flow_verified_facts_for_health_score(
    monkeypatch,
    tmp_path,
):
    def bundle(serial: str, facts: dict[str, Any]) -> str:
        path = tmp_path / f"{serial}.json"
        path.write_text(json.dumps({"verified_facts": facts}), encoding="utf-8")
        return str(path)

    good_facts = {
        "gap_event_count": 0,
        "largest_gap_duration_seconds": 0,
        "coverage_6h": {"n_buckets": 2, "buckets_with_issues": 0},
        "cusum_drift": {
            "skipped": False,
            "drift_detected": "none",
            "positive_alarm_count": 0,
            "negative_alarm_count": 0,
        },
    }
    bad_facts = {
        "gap_event_count": 6,
        "largest_gap_duration_seconds": 7200,
        "coverage_6h": {"n_buckets": 2, "buckets_with_issues": 1},
        "cusum_drift": {
            "skipped": False,
            "drift_detected": "upward",
            "positive_alarm_count": 5,
            "negative_alarm_count": 0,
        },
    }
    analysis_paths = {
        "BB_GOOD": bundle("BB_GOOD", good_facts),
        "BB_BAD": bundle("BB_BAD", bad_facts),
    }
    flow_calls: list[tuple[str, int, int, dict[str, Any]]] = []
    status_facts: dict[str, dict[str, Any] | None] = {}

    monkeypatch.setattr(
        fh,
        "get_meter_profile",
        lambda serial, token: _profile_ok(serial, label=serial),
    )

    def fake_analyze(serial, start, end, token, **kwargs):
        flow_calls.append((serial, start, end, kwargs))
        return {
            "success": True,
            "analysis_json_path": analysis_paths[serial],
            "display_range": f"{start}-{end}",
            "plot_timezone": kwargs.get("meter_timezone") or "UTC",
            "error": None,
        }

    def fake_status(serial, token, *, anthropic_api_key=None, verified_facts=None):
        status_facts[serial] = verified_facts
        status_data = {
            "serial_number": serial,
            "online": True,
            "staleness": {
                "seconds_since": 30,
                "communication_status": "fresh",
                "status_description": "fresh telemetry",
            },
            "signal": {
                "score": 90,
                "level": "good",
                "reliable": True,
                "action_needed": False,
            },
        }
        status_data["health_score"] = hs.compute_health_score(
            status=status_data,
            verified_facts=verified_facts,
        )
        return {"success": True, "status_data": status_data, "error": None}

    monkeypatch.setattr(fh, "analyze_flow_data", fake_analyze)
    monkeypatch.setattr(fh, "check_meter_status", fake_status)

    out = fh.rank_fleet_by_health(
        ["BB_GOOD", "BB_BAD"],
        "tok",
        flow_window={"start": 1_700_000_000, "end": 1_700_086_400},
    )

    assert out["success"] is True
    assert out["flow_window"] == {"start": 1_700_000_000, "end": 1_700_086_400}
    assert [m["serial_number"] for m in out["meters"]] == ["BB_BAD", "BB_GOOD"]
    assert status_facts["BB_BAD"] == bad_facts
    assert status_facts["BB_GOOD"] == good_facts
    assert out["meters"][0]["health_score"] < out["meters"][1]["health_score"]
    assert out["meters"][0]["top_concern"].startswith("gap_density:")
    assert out["meters"][0]["flow_analysis"]["gap_event_count"] == 6
    assert out["meters"][0]["flow_analysis"]["drift_detected"] == "upward"
    assert {call[0] for call in flow_calls} == {"BB_GOOD", "BB_BAD"}
    assert all(call[3]["analysis_mode"] == "summary" for call in flow_calls)
    assert all(call[3]["network_type"] == "wifi" for call in flow_calls)
    assert all(call[3]["meter_timezone"] == "America/Denver" for call in flow_calls)


def test_rank_fleet_by_health_dedups_and_truncates(monkeypatch):
    seen: list[str] = []

    def fake_profile(serial, token):
        seen.append(serial)
        return _profile_ok(serial)

    monkeypatch.setattr(fh, "get_meter_profile", fake_profile)
    monkeypatch.setattr(
        fh,
        "check_meter_status",
        lambda serial, token, *, anthropic_api_key=None: _status_ok(serial, score=80),
    )

    serials = ["BB0", "BB0"] + [f"BB{i}" for i in range(1, 55)]
    out = fh.rank_fleet_by_health(serials, "tok")

    assert out["success"] is True
    assert out["truncated"] is True
    assert len(out["meters"]) == fh._MAX_METERS
    assert sorted(set(seen)) == sorted({f"BB{i}" for i in range(50)})


def test_rank_fleet_by_health_keeps_partial_failures(monkeypatch):
    profiles = {
        "BB1": _profile_ok("BB1"),
        "BB2": _profile_err("BB2", "profile unavailable"),
        "BB3": _profile_err("BB3", "profile unavailable"),
    }
    statuses = {
        "BB1": _status_ok("BB1", score=91),
        "BB2": _status_ok("BB2", score=55, verdict="unhealthy"),
        "BB3": _status_err("BB3", "status timeout"),
    }
    monkeypatch.setattr(fh, "get_meter_profile", lambda serial, token: profiles[serial])
    monkeypatch.setattr(
        fh,
        "check_meter_status",
        lambda serial, token, *, anthropic_api_key=None: statuses[serial],
    )

    out = fh.rank_fleet_by_health(["BB1", "BB2", "BB3"], "tok")

    assert out["success"] is True
    rows = {m["serial_number"]: m for m in out["meters"]}
    assert rows["BB2"]["success"] is True
    assert rows["BB2"]["profile_error"] == "profile unavailable"
    assert rows["BB3"]["success"] is False
    assert rows["BB3"]["top_concern"] == "status timeout"
    assert out["failed_serials"] == ["BB3"]


def test_rank_fleet_by_health_validates_inputs(monkeypatch):
    called = False

    def fake_profile(serial, token):
        nonlocal called
        called = True
        return _profile_ok(serial)

    monkeypatch.setattr(fh, "get_meter_profile", fake_profile)

    no_serials = fh.rank_fleet_by_health(["", "  "], "tok")
    no_token = fh.rank_fleet_by_health(["BB1"], "")

    assert no_serials["success"] is False
    assert "at least one" in no_serials["error"]
    assert no_token["success"] is False
    assert no_token["failed_serials"] == ["BB1"]
    assert called is False
