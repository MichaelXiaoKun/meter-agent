"""
Tests for ``processors.sampling_physics``: resolution precedence of the
healthy inter-arrival cap and its audit record.
"""

from __future__ import annotations

import pytest

from processors.sampling_physics import (
    describe_sampling_caps,
    gap_threshold_cap_seconds,
    max_healthy_inter_arrival_seconds,
)


# ---------------------------------------------------------------------------
# Precedence: explicit override > network-type hint > default
# ---------------------------------------------------------------------------


def test_default_cap_is_60_seconds_when_no_env():
    assert max_healthy_inter_arrival_seconds() == 60.0


@pytest.mark.parametrize(
    "network_type,expected_cap",
    [
        ("wifi", 5.0),
        ("WiFi", 5.0),          # case-insensitive
        ("lorawan", 60.0),
        ("LORAWAN", 60.0),
        ("unknown", 60.0),
    ],
)
def test_network_type_hint_sets_cap(monkeypatch, network_type, expected_cap):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", network_type)
    assert max_healthy_inter_arrival_seconds() == expected_cap


def test_unrecognised_network_type_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "zigbee")
    assert max_healthy_inter_arrival_seconds() == 60.0


def test_explicit_override_wins_over_network_hint(monkeypatch):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "15")
    assert max_healthy_inter_arrival_seconds() == 15.0


def test_explicit_override_is_clamped(monkeypatch):
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "0.1")   # below floor
    assert max_healthy_inter_arrival_seconds() == 2.0
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "9999")  # above ceiling
    assert max_healthy_inter_arrival_seconds() == 600.0


def test_invalid_override_falls_back(monkeypatch):
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "not-a-number")
    assert max_healthy_inter_arrival_seconds() == 60.0


# ---------------------------------------------------------------------------
# Gap cap = healthy × slack
# ---------------------------------------------------------------------------


def test_gap_cap_default_slack_1_5(monkeypatch):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
    assert gap_threshold_cap_seconds() == pytest.approx(5.0 * 1.5)


def test_gap_cap_honours_custom_slack(monkeypatch):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "lorawan")
    monkeypatch.setenv("BLUEBOT_GAP_SLACK", "2.0")
    assert gap_threshold_cap_seconds() == pytest.approx(60.0 * 2.0)


def test_gap_cap_clamps_slack_below_one(monkeypatch):
    monkeypatch.setenv("BLUEBOT_GAP_SLACK", "0.5")
    # slack below 1 is clamped to 1.0 so the gap cap is never tighter than
    # the healthy inter-arrival itself.
    assert gap_threshold_cap_seconds() == 60.0


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------


def test_describe_sampling_caps_reports_network_hint(monkeypatch):
    monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "lorawan")
    rec = describe_sampling_caps()
    assert rec["max_healthy_inter_arrival_seconds"] == 60.0
    assert rec["network_type_hint"] == "lorawan"
    assert rec["explicit_override"] is False


def test_describe_sampling_caps_reports_explicit_override(monkeypatch):
    monkeypatch.setenv("BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S", "10")
    rec = describe_sampling_caps()
    assert rec["max_healthy_inter_arrival_seconds"] == 10.0
    assert rec["explicit_override"] is True
    