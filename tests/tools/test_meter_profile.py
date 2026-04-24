"""
Tests for ``tools.meter_profile``: network-type classification and the HTTP
contract with the Bluebot management API (mocked via ``respx``).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tools.meter_profile import (
    classify_network_type,
    get_meter_profile,
)


# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------


class TestClassifyNetworkType:
    def test_missing_nui_is_unknown(self):
        c = classify_network_type("BB8100000000", None)
        assert c["network_type"] == "unknown"
        assert "missing" in c["reason"].lower()
        assert c["expected_cadence_hint"] is None

    def test_empty_nui_is_unknown(self):
        c = classify_network_type("BB8100000000", "   ")
        assert c["network_type"] == "unknown"

    def test_ff_prefix_is_lorawan(self):
        c = classify_network_type("BB8100000000", "FF0123456789AB")
        assert c["network_type"] == "lorawan"
        assert "FF" in c["reason"]
        assert "12" in c["expected_cadence_hint"]  # mentions the 12–60 s cadence

    def test_ff_prefix_lowercase_is_lorawan(self):
        c = classify_network_type("BB8100000000", "ff0123456789ab")
        assert c["network_type"] == "lorawan"

    def test_matching_serial_is_wifi(self):
        c = classify_network_type("BB8100013600", "BB8100013600")
        assert c["network_type"] == "wifi"
        assert c["expected_cadence_hint"] == "typical inter-arrival ~2 s"

    def test_matching_serial_case_insensitive(self):
        c = classify_network_type("bb8100013600", "BB8100013600")
        assert c["network_type"] == "wifi"

    def test_mismatched_ids_is_unknown(self):
        c = classify_network_type("BB8100013600", "EE0123456789AB")
        assert c["network_type"] == "unknown"
        assert c["expected_cadence_hint"] is None

    def test_empty_serial_and_nui(self):
        c = classify_network_type("", "")
        assert c["network_type"] == "unknown"


# ---------------------------------------------------------------------------
# HTTP (mocked)
# ---------------------------------------------------------------------------


_DEVICE_URL = "https://prod.bluebot.com/management/v1/device"


@pytest.fixture
def profile_row():
    return {
        "id": "abc",
        "serialNumber": "BB8100013600",
        "label": "bluebot L parc",
        "model": "Model X",
        "category": "water",
        "deviceType": None,
        "networkUniqueIdentifier": "BB8100013600",
        "commissioned": True,
        "installed": True,
        "installedOn": "2025-01-16T19:33:05.797Z",
        "active": True,
        "deviceTimeZone": "America/New_York",
        "organizationId": "org-1",
        "accountId": "acct-1",
        "organization": {"name": "L Parc"},
        "deviceToDeviceGroups": [
            {
                "deviceGroup": {
                    "name": "Mains",
                    "description": "Main line meters",
                    "parentGroupId": None,
                }
            }
        ],
    }


class TestGetMeterProfile:
    def test_missing_token_short_circuits(self):
        res = get_meter_profile("BB8100013600", "")
        assert res["success"] is False
        assert res["network_type"] is None
        assert "Bearer token" in res["error"]

    @respx.mock
    def test_wifi_meter_happy_path(self, profile_row):
        respx.get(_DEVICE_URL).mock(
            return_value=httpx.Response(200, json=[profile_row])
        )
        res = get_meter_profile("BB8100013600", "tok")
        assert res["success"] is True
        assert res["network_type"] == "wifi"
        assert res["classification"]["network_type"] == "wifi"
        assert res["profile"]["serialNumber"] == "BB8100013600"
        assert res["profile"]["organization_name"] == "L Parc"
        assert res["profile"]["device_groups"][0]["name"] == "Mains"
        assert res["transducer_angle_options"] == ["15º", "25º", "35º", "45º"]
        assert res["error"] is None

    @respx.mock
    def test_lorawan_meter(self, profile_row):
        profile_row["networkUniqueIdentifier"] = "FF0123456789AB"
        respx.get(_DEVICE_URL).mock(
            return_value=httpx.Response(200, json=[profile_row])
        )
        res = get_meter_profile("BB8100013600", "tok")
        assert res["success"] is True
        assert res["network_type"] == "lorawan"
        assert res["transducer_angle_options"] == [
            "10º",
            "15º",
            "20º",
            "25º",
            "30º",
            "35º",
            "40º",
            "45º",
        ]

    @respx.mock
    def test_404_returns_structured_error(self):
        respx.get(_DEVICE_URL).mock(
            return_value=httpx.Response(404, text="not found")
        )
        res = get_meter_profile("BB8100000000", "tok")
        assert res["success"] is False
        assert res["network_type"] is None
        assert "404" in res["error"]
        assert "No device found" in res["error"]

    @respx.mock
    def test_401_returns_structured_error(self):
        respx.get(_DEVICE_URL).mock(
            return_value=httpx.Response(401, text="invalid")
        )
        res = get_meter_profile("BB8100013600", "tok")
        assert res["success"] is False
        assert "401" in res["error"]
        assert "expired" in res["error"].lower() or "invalid" in res["error"].lower()

    @respx.mock
    def test_empty_array_is_treated_as_no_device(self):
        respx.get(_DEVICE_URL).mock(return_value=httpx.Response(200, json=[]))
        res = get_meter_profile("BB8100013600", "tok")
        assert res["success"] is False
        assert "No device profile" in res["error"]

    @respx.mock
    def test_management_base_env_override(self, monkeypatch, profile_row):
        monkeypatch.setenv("BLUEBOT_MANAGEMENT_BASE", "https://staging.bluebot.com")
        route = respx.get("https://staging.bluebot.com/management/v1/device").mock(
            return_value=httpx.Response(200, json=[profile_row])
        )
        res = get_meter_profile("BB8100013600", "tok")
        assert route.called
        assert res["success"] is True

    @respx.mock
    def test_admin_header_and_bearer_are_sent(self, profile_row):
        route = respx.get(_DEVICE_URL).mock(
            return_value=httpx.Response(200, json=[profile_row])
        )
        get_meter_profile("BB8100013600", "tok-123")
        req = route.calls.last.request
        assert req.headers["x-admin-query"] == "true"
        assert req.headers["authorization"] == "Bearer tok-123"
        assert req.url.params.get("serialNumber") == "BB8100013600"
