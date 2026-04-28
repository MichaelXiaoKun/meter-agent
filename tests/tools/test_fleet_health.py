"""
Tests for ``tools.fleet_health``.
"""

from __future__ import annotations

from typing import Any

from tools import fleet_health as fh


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
