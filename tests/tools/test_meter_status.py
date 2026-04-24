"""
Tests for ``tools.meter_status``: parsing of the ``__BLUEBOT_STATUS_JSON__``
marker emitted by the meter-status-agent subprocess, and the shape of the
return dict.

The subprocess itself is mocked — we don't care about the LLM here, only that
the wrapper extracts structured data from stderr and returns the expected
envelope.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from tools import meter_status as ms


# ---------------------------------------------------------------------------
# _collect_status_data (pure parser)
# ---------------------------------------------------------------------------


class TestCollectStatusData:
    def test_missing_marker_returns_none(self):
        assert ms._collect_status_data("nothing here") is None
        assert ms._collect_status_data("") is None

    def test_parses_marker_payload(self):
        payload = {"serial_number": "BB1", "online": True}
        stderr = f"some log\n{ms._STATUS_JSON_MARKER}{json.dumps(payload)}\nmore log\n"
        got = ms._collect_status_data(stderr)
        assert got == payload

    def test_malformed_json_returns_none(self):
        stderr = f"{ms._STATUS_JSON_MARKER}{{not json}}\n"
        assert ms._collect_status_data(stderr) is None

    def test_non_dict_payload_returns_none(self):
        # Arrays / strings are valid JSON but not our shape.
        stderr = f'{ms._STATUS_JSON_MARKER}["a", "b"]\n'
        assert ms._collect_status_data(stderr) is None

    def test_marker_on_its_own_line(self):
        # Marker is the last line, no trailing newline.
        payload = {"serial_number": "BB2"}
        stderr = f"log\n{ms._STATUS_JSON_MARKER}{json.dumps(payload)}"
        assert ms._collect_status_data(stderr) == payload


# ---------------------------------------------------------------------------
# check_meter_status (subprocess mocked)
# ---------------------------------------------------------------------------


def _fake_completed(stdout: str, stderr: str, returncode: int = 0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture
def structured_payload():
    return {
        "serial_number": "BB8100015261",
        "online": True,
        "last_message_at": "2026-04-07T21:39:53.712Z",
        "staleness": {
            "seconds_since": 42,
            "communication_status": "fresh",
            "status_description": "Meter is actively reporting.",
        },
        "signal": {
            "score": 87,
            "level": "good",
            "reliable": True,
            "action_needed": False,
        },
        "pipe_config": {
            "outer_diameter_mm": 26.7,
            "inner_diameter_mm": 20.93,
            "nominal_size": '3/4"',
            "pipe_standard": "CPVC",
        },
        "errors": {},
    }


class TestCheckMeterStatus:
    def test_success_parses_structured_data(self, monkeypatch, structured_payload):
        stderr = (
            "Fetching status for serial BB8100015261...\n"
            f"{ms._STATUS_JSON_MARKER}{json.dumps(structured_payload)}\n"
            "Running analysis...\n"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _fake_completed("# Report\nAll good.", stderr, 0),
        )
        res = ms.check_meter_status("BB8100015261", "tok")
        assert res["success"] is True
        assert res["report"] == "# Report\nAll good."
        assert res["status_data"] == structured_payload
        assert res["error"] is None

    def test_success_without_marker_keeps_none_status_data(self, monkeypatch):
        # A legacy subprocess (pre-marker) should still work — report is returned
        # and status_data is None, not an error.
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _fake_completed("report text", "harmless logs\n", 0),
        )
        res = ms.check_meter_status("BB1", "tok")
        assert res["success"] is True
        assert res["report"] == "report text"
        assert res["status_data"] is None
        assert res["error"] is None

    def test_failure_still_surfaces_structured_data_if_emitted(
        self, monkeypatch, structured_payload
    ):
        # Fetch succeeded (marker emitted) but the LLM step crashed. We want the
        # structured data anyway so callers like compare_meters can still use it.
        stderr = (
            f"{ms._STATUS_JSON_MARKER}{json.dumps(structured_payload)}\n"
            "Running analysis...\n"
            "Traceback (most recent call last):\n"
            '  File "agent.py", line 99, in analyze\n'
            "RuntimeError: LLM quota exhausted\n"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _fake_completed("", stderr, 1),
        )
        res = ms.check_meter_status("BB1", "tok")
        assert res["success"] is False
        assert res["status_data"] == structured_payload
        # User-facing error message uses the final traceback line, no stack frames.
        assert res["error"].startswith("RuntimeError")
        assert "Traceback" not in res["error"]

    def test_failure_without_marker_returns_none_status_data(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _fake_completed(
                "", "Error: Bearer token required.\n", 1
            ),
        )
        res = ms.check_meter_status("BB1", "")
        assert res["success"] is False
        assert res["status_data"] is None
        assert "Bearer token required" in res["error"]
