"""
Tests for ``tools.fleet_triage``.
"""

from __future__ import annotations

from tools import fleet_triage as ft


def _listing(*serials: str, truncated: bool = False) -> dict:
    return {
        "success": True,
        "email": "alice@example.com",
        "error": None,
        "total_count": len(serials) + (1 if truncated else 0),
        "returned_count": len(serials),
        "truncated": truncated,
        "meters": [
            {"serialNumber": serial, "label": f"Meter {idx}"}
            for idx, serial in enumerate(serials, start=1)
        ],
        "notice": None,
    }


def _ranked(*serials: str, success: bool = True) -> dict:
    return {
        "success": success,
        "meters": [
            {
                "serial_number": serial,
                "label": f"Meter {idx}",
                "health_score": 45 + idx,
                "health_verdict": "degraded",
                "top_concern": f"concern {idx}",
                "online": idx % 2 == 0,
                "communication_status": "fresh",
                "seconds_since": 30 + idx,
                "signal_score": 80 - idx,
                "signal_level": "good",
                "network_type": "wifi",
                "deviceTimeZone": "America/Denver",
                "profile_error": None,
                "status_error": None,
            }
            for idx, serial in enumerate(serials, start=1)
        ],
        "failed_serials": None,
        "truncated": False,
        "error": None if success else "rank failed",
    }


def test_triage_fleet_for_account_lists_and_ranks(monkeypatch):
    calls: dict[str, object] = {}

    def fake_list(email, token, *, limit=None):
        calls["list"] = (email, token, limit)
        return _listing("BB2", "BB1", truncated=True)

    def fake_rank(serials, token, *, anthropic_api_key=None):
        calls["rank"] = (serials, token, anthropic_api_key)
        return _ranked("BB1", "BB2")

    monkeypatch.setattr(ft, "list_meters_for_account", fake_list)
    monkeypatch.setattr(ft, "rank_fleet_by_health", fake_rank)

    out = ft.triage_fleet_for_account(
        " alice@example.com ",
        "tok",
        anthropic_api_key="anth",
    )

    assert out["success"] is True
    assert calls["list"] == ("alice@example.com", "tok", ft._MAX_METERS)
    assert calls["rank"] == (["BB2", "BB1"], "tok", "anth")
    assert out["email"] == "alice@example.com"
    assert out["truncated"] is True
    assert out["total_count"] == 3
    assert [m["serial"] for m in out["meters"]] == ["BB1", "BB2"]
    assert out["meters"][0]["signal"] == {"score": 79, "level": "good"}
    assert out["meters"][0]["last_seen_age_seconds"] == 31
    assert out["meters"][0]["top_concern"] == "concern 1"


def test_triage_fleet_for_account_propagates_listing_failure(monkeypatch):
    def fake_list(email, token, *, limit=None):
        return {
            "success": False,
            "email": email,
            "error": "No bluebot account found",
            "error_stage": "account_lookup",
            "error_code": "not_found",
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "notice": None,
            "meters": [],
        }

    monkeypatch.setattr(ft, "list_meters_for_account", fake_list)

    out = ft.triage_fleet_for_account("alice@example.com", "tok")

    assert out["success"] is False
    assert out["meters"] == []
    assert out["error"] == "No bluebot account found"
    assert out["error_stage"] == "account_lookup"
    assert out["error_code"] == "not_found"


def test_triage_fleet_for_account_handles_empty_listing(monkeypatch):
    monkeypatch.setattr(
        ft,
        "list_meters_for_account",
        lambda email, token, *, limit=None: {
            "success": True,
            "email": email,
            "error": None,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "notice": "No meters found.",
            "meters": [],
        },
    )

    out = ft.triage_fleet_for_account("alice@example.com", "tok")

    assert out["success"] is True
    assert out["meters"] == []
    assert out["notice"] == "No meters found."
    assert out["error"] is None


def test_triage_fleet_for_account_validates_inputs(monkeypatch):
    called = False

    def fake_list(email, token, *, limit=None):
        nonlocal called
        called = True
        return _listing("BB1")

    monkeypatch.setattr(ft, "list_meters_for_account", fake_list)

    missing_email = ft.triage_fleet_for_account(" ", "tok")
    missing_token = ft.triage_fleet_for_account("alice@example.com", "")

    assert missing_email["success"] is False
    assert "Email is required" in missing_email["error"]
    assert missing_token["success"] is False
    assert "Bearer token" in missing_token["error"]
    assert called is False
