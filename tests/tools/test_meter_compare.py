"""
Tests for ``tools.meter_compare`` — the cross-meter diff tool.

We mock the underlying ``get_meter_profile`` and ``check_meter_status``
functions so these tests never touch the network or a subprocess.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from tools import meter_compare as mc


# ---------------------------------------------------------------------------
# Fixtures: factory builders for profile / status result envelopes that match
# the real shape of get_meter_profile / check_meter_status returns.
# ---------------------------------------------------------------------------


def _profile_ok(
    serial: str,
    *,
    model: str = "X-100",
    network_type: str = "wifi",
    tz: str = "America/New_York",
    commissioned: bool = True,
    installed: bool = True,
    active: bool = True,
    installedOn: str = "2025-01-16T19:33:05.797Z",
    label: str = "Meter A",
    category: str = "water",
) -> Dict[str, Any]:
    return {
        "success": True,
        "serial_number": serial,
        "network_type": network_type,
        "classification": {"network_type": network_type, "reason": "", "expected_cadence_hint": None},
        "profile": {
            "serialNumber": serial,
            "label": label,
            "model": model,
            "category": category,
            "deviceTimeZone": tz,
            "commissioned": commissioned,
            "installed": installed,
            "installedOn": installedOn,
            "active": active,
            "organization_name": "Acme",
        },
        "transducer_angle_options": None,
        "error": None,
    }


def _profile_err(serial: str, err: str = "HTTP 404") -> Dict[str, Any]:
    return {
        "success": False,
        "serial_number": serial,
        "network_type": None,
        "classification": None,
        "profile": None,
        "error": err,
    }


def _status_ok(
    serial: str,
    *,
    online: bool = True,
    signal_score: int = 87,
    signal_level: str = "good",
    signal_reliable: bool = True,
    comm_status: str = "fresh",
    nominal_size: str = '3/4"',
    pipe_standard: str = "CPVC",
    inner_id_mm: float = 20.93,
    health_score: float = 92.0,
    health_verdict: str = "healthy",
) -> Dict[str, Any]:
    return {
        "success": True,
        "report": "# Meter report",
        "status_data": {
            "serial_number": serial,
            "online": online,
            "last_message_at": "2026-04-07T21:39:53.712Z",
            "staleness": {
                "seconds_since": 42,
                "communication_status": comm_status,
                "status_description": "ok",
            },
            "signal": {
                "score": signal_score,
                "level": signal_level,
                "reliable": signal_reliable,
                "action_needed": not signal_reliable,
            },
            "pipe_config": {
                "outer_diameter_mm": 26.7,
                "inner_diameter_mm": inner_id_mm,
                "nominal_size": nominal_size,
                "pipe_standard": pipe_standard,
            },
            "health_score": {
                "score": health_score,
                "verdict": health_verdict,
                "components": {"staleness": {"score": 100}},
            },
            "errors": {},
        },
        "error": None,
    }


def _status_err(serial: str, err: str = "timeout") -> Dict[str, Any]:
    return {"success": False, "report": None, "status_data": None, "error": err}


@pytest.fixture
def fake_fetch(monkeypatch):
    """Patch get_meter_profile / check_meter_status with caller-supplied tables."""

    def _install(
        profiles: Dict[str, Dict[str, Any]] | None = None,
        statuses: Dict[str, Dict[str, Any]] | None = None,
    ):
        profiles = profiles or {}
        statuses = statuses or {}
        monkeypatch.setattr(
            mc, "get_meter_profile", lambda sn, tok: profiles[sn]
        )
        monkeypatch.setattr(
            mc,
            "check_meter_status",
            lambda sn, tok, *, anthropic_api_key=None: statuses[sn],
        )

    return _install


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_no_token_short_circuits(self):
        res = mc.compare_meters(["BB1", "BB2"], "")
        assert res["success"] is False
        assert "Bearer token" in res["error"]

    def test_too_few_serials(self):
        res = mc.compare_meters(["BB1"], "tok")
        assert res["success"] is False
        assert "at least 2" in res["error"]

    def test_too_many_serials(self):
        res = mc.compare_meters([f"BB{i}" for i in range(11)], "tok")
        assert res["success"] is False
        assert "at most 10" in res["error"]

    def test_duplicates_dedup_then_validated(self):
        # Two strings, same serial → dedups to 1 → below minimum.
        res = mc.compare_meters(["BB1", "BB1"], "tok")
        assert res["success"] is False
        assert "at least 2" in res["error"]
        assert res["serial_numbers"] == ["BB1"]

    def test_whitespace_and_empty_serials_dropped(self):
        res = mc.compare_meters(["  BB1  ", "", "   ", "BB2"], "tok")
        # Before fetch — cleaned list is what matters for validation.
        assert res["serial_numbers"] == ["BB1", "BB2"]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestDiffLogic:
    def test_all_uniform(self, fake_fetch):
        fake_fetch(
            profiles={"BB1": _profile_ok("BB1"), "BB2": _profile_ok("BB2")},
            statuses={"BB1": _status_ok("BB1"), "BB2": _status_ok("BB2")},
        )
        res = mc.compare_meters(["BB1", "BB2"], "tok")
        assert res["success"] is True
        assert res["successful_count"] == 2
        assert res["failed_count"] == 0
        assert res["differences"] == {}
        # All 13 diff fields should be uniform.
        assert set(res["uniform_fields"]) == set(mc._DIFF_FIELDS)
        assert "No field disagreements" in res["summary"]

    def test_model_differs_groups_serials(self, fake_fetch):
        fake_fetch(
            profiles={
                "BB1": _profile_ok("BB1", model="X-100"),
                "BB2": _profile_ok("BB2", model="X-100"),
                "BB3": _profile_ok("BB3", model="X-200"),
            },
            statuses={
                "BB1": _status_ok("BB1"),
                "BB2": _status_ok("BB2"),
                "BB3": _status_ok("BB3"),
            },
        )
        res = mc.compare_meters(["BB1", "BB2", "BB3"], "tok")
        assert res["success"] is True
        assert "model" in res["differences"]
        groups = res["differences"]["model"]["groups"]
        assert sorted(groups["X-100"]) == ["BB1", "BB2"]
        assert groups["X-200"] == ["BB3"]
        assert "Disagree on: model" in res["summary"]

    def test_signal_level_diff_flags_bad_meter(self, fake_fetch):
        fake_fetch(
            profiles={
                "BB1": _profile_ok("BB1"),
                "BB2": _profile_ok("BB2"),
            },
            statuses={
                "BB1": _status_ok("BB1", signal_score=87, signal_level="good"),
                "BB2": _status_ok(
                    "BB2", signal_score=42, signal_level="poor", signal_reliable=False
                ),
            },
        )
        res = mc.compare_meters(["BB1", "BB2"], "tok")
        assert "signal_level" in res["differences"]
        assert res["differences"]["signal_level"]["groups"]["good"] == ["BB1"]
        assert res["differences"]["signal_level"]["groups"]["poor"] == ["BB2"]
        # Continuous fields are NOT in differences (score differs but isn't diffed).
        assert "signal_score" not in res["differences"]
        assert "signal_score" not in res["uniform_fields"]
        # Per-meter still surfaces the numeric score for the LLM.
        by_sn = {m["serial_number"]: m for m in res["per_meter"]}
        assert by_sn["BB1"]["signal_score"] == 87
        assert by_sn["BB2"]["signal_score"] == 42

    def test_health_score_surfaces_per_meter(self, fake_fetch):
        fake_fetch(
            profiles={"BB1": _profile_ok("BB1"), "BB2": _profile_ok("BB2")},
            statuses={
                "BB1": _status_ok("BB1", health_score=95, health_verdict="healthy"),
                "BB2": _status_ok("BB2", health_score=55, health_verdict="unhealthy"),
            },
        )
        res = mc.compare_meters(["BB1", "BB2"], "tok")
        by_sn = {m["serial_number"]: m for m in res["per_meter"]}
        assert by_sn["BB1"]["health_score"] == 95
        assert by_sn["BB2"]["health_verdict"] == "unhealthy"
        assert by_sn["BB1"]["health_score_components"]["staleness"]["score"] == 100


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_one_meter_fully_unreachable(self, fake_fetch):
        fake_fetch(
            profiles={
                "BB1": _profile_ok("BB1"),
                "BB2": _profile_ok("BB2"),
                "BB3": _profile_err("BB3", "HTTP 404"),
            },
            statuses={
                "BB1": _status_ok("BB1"),
                "BB2": _status_ok("BB2"),
                "BB3": _status_err("BB3", "fetch failed"),
            },
        )
        res = mc.compare_meters(["BB1", "BB2", "BB3"], "tok")
        assert res["success"] is True
        assert res["successful_count"] == 2
        assert res["failed_count"] == 1
        assert res["failures"] == [{"serial_number": "BB3", "error": "HTTP 404"}]
        # BB3 is not in per_meter since both sources failed.
        assert {m["serial_number"] for m in res["per_meter"]} == {"BB1", "BB2"}
        # The diff runs on the surviving 2 meters.
        assert res["differences"] == {}
        assert "Unreachable: BB3" in res["summary"]

    def test_profile_failure_but_status_ok_keeps_meter(self, fake_fetch):
        # BB2 has no profile but a working status — it should still contribute
        # status fields to the diff.
        fake_fetch(
            profiles={
                "BB1": _profile_ok("BB1"),
                "BB2": _profile_err("BB2", "HTTP 404"),
            },
            statuses={
                "BB1": _status_ok("BB1", signal_level="good"),
                "BB2": _status_ok("BB2", signal_level="poor"),
            },
        )
        res = mc.compare_meters(["BB1", "BB2"], "tok")
        assert res["successful_count"] == 2
        assert res["failed_count"] == 0
        by_sn = {m["serial_number"]: m for m in res["per_meter"]}
        # BB2 profile failed — profile-derived fields are None, but status lives.
        assert by_sn["BB2"]["profile_error"] == "HTTP 404"
        assert by_sn["BB2"]["model"] is None
        assert by_sn["BB2"]["signal_level"] == "poor"
        # Model: BB1 has "X-100", BB2 has __missing__ → still a diff.
        assert "model" in res["differences"]
        assert "__missing__" in res["differences"]["model"]["groups"]
