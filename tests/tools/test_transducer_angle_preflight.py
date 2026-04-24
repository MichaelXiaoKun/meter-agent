"""Orchestrator preflight vs pipe-configuration-agent angle tables."""

from __future__ import annotations

from tools.transducer_angle_preflight import (
    allowed_labels_for_network_type,
    preflight_validate_transducer_angle,
)


def test_wifi_allows_45_rejects_40():
    assert preflight_validate_transducer_angle("45", "wifi") is None
    assert preflight_validate_transducer_angle("40º", "wifi") is not None


def test_lorawan_allows_40():
    assert preflight_validate_transducer_angle("40°", "lorawan") is None


def test_unknown_network_only_intersection():
    assert preflight_validate_transducer_angle("25", "unknown") is None
    err = preflight_validate_transducer_angle("40", "unknown")
    assert err is not None
    assert "ambiguous" in err.lower() or "uncertain" in err.lower()


def test_allowed_labels_wifi_vs_lorawan():
    w = allowed_labels_for_network_type("wifi")
    assert "40º" not in w
    assert "45º" in w
    l = allowed_labels_for_network_type("lorawan")
    assert "40º" in l
