from __future__ import annotations

import sys
from pathlib import Path


_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)

from admin_chat import meter_context as mc  # noqa: E402
from admin_chat import recent_flow_snapshot as rfs  # noqa: E402


class _FakeDataFrame:
    def __init__(self, records):
        self._records = list(records)

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._records)


def test_resolve_active_serial_prefers_latest_user_message():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "turn_activity",
                    "events": [
                        {
                            "type": "meter_context",
                            "meter_context": {"serial_number": "BBOLD"},
                        }
                    ],
                }
            ],
        },
        {"role": "user", "content": "check BB8100017587"},
    ]

    assert mc.resolve_active_serial(messages) == "BB8100017587"


def test_resolve_active_serial_skips_multiple_latest_serials():
    messages = [{"role": "user", "content": "compare BB1 and BB2"}]

    assert mc.resolve_active_serial(messages) is None


def test_resolve_active_serial_falls_back_to_workspace_context():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "turn_activity",
                    "events": [
                        {
                            "type": "meter_context",
                            "meter_context": {"serial_number": "BB8100010001"},
                        }
                    ],
                }
            ],
        },
        {"role": "user", "content": "what about yesterday?"},
    ]

    assert mc.resolve_active_serial(messages) == "BB8100010001"


def test_build_meter_context_packet_uses_timeout_status_facts():
    def fake_profile(serial, _token):
        return {
            "success": True,
            "serial_number": serial,
            "network_type": "wifi",
            "profile": {
                "label": "Kitchen",
                "deviceTimeZone": "America/New_York",
                "installed": True,
            },
        }

    def fake_status(serial, _token, **_kwargs):
        return {
            "success": True,
            "timed_out": True,
            "status_data": {
                "serial_number": serial,
                "online": True,
                "last_message_at": "2026-06-16T12:00:00Z",
                "staleness": {"communication_status": "fresh"},
                "signal": {"score": 87, "level": "good", "reliable": True},
                "pipe_config": {"nominal_size": '3/4"', "inner_diameter_mm": 20.9},
                "health_score": {"score": 88, "verdict": "healthy"},
            },
        }

    def fake_recent_flow(serial, _token, **kwargs):
        assert serial == "BB8100017587"
        assert kwargs["network_type"] == "wifi"
        return {
            "state": "checked",
            "serial_number": serial,
            "window_seconds": 300,
            "sample_count": 3,
            "valid_flow_count": 3,
            "latest_sample_age_seconds": 2,
            "latest_sample_fresh": True,
            "latest_flow_rate": 1.25,
            "mean_flow_rate": 1.1,
            "largest_gap_seconds": 2,
            "gap_count": 0,
            "snapshot_quality": "usable",
        }

    result = mc.build_meter_context_packet(
        [{"role": "user", "content": "check BB8100017587"}],
        "tok",
        get_profile=fake_profile,
        check_status=fake_status,
        get_recent_flow=fake_recent_flow,
    )

    assert result is not None
    packet = result.event["meter_context"]
    assert packet["serial_number"] == "BB8100017587"
    assert packet["label"] == "Kitchen"
    assert packet["network_type"] == "wifi"
    assert packet["health_score"] == 88
    assert packet["health_verdict"] == "healthy"
    assert packet["status_summary_timed_out"] is True
    assert packet["diagnostic_signals"][0]["state"] == "ok"
    assert packet["diagnostic_signals"][3]["name"] == "recent_flow"
    assert packet["diagnostic_signals"][3]["state"] == "ok"
    assert packet["recent_flow"]["state"] == "checked"
    assert packet["recent_flow"]["sample_count"] == 3
    assert "recent_flow_not_checked" not in packet["known_missing"]
    assert "open_tickets" not in packet
    assert result.profile_result_json
    assert result.status_result_json


def test_recent_flow_window_seconds_by_network_type():
    assert rfs.recent_flow_window_seconds("wifi") == 5 * 60
    assert rfs.recent_flow_window_seconds("lorawan") == 60 * 60
    assert rfs.recent_flow_window_seconds("unknown") == 15 * 60
    assert rfs.recent_flow_window_seconds(None) == 15 * 60


def test_recent_flow_snapshot_summarizes_success():
    now = 1_000

    def fake_fetch(serial, start, end, **kwargs):
        assert serial == "BBFLOW"
        assert start == 700
        assert end == now
        assert kwargs["timeout"] == 3
        return (
            _FakeDataFrame(
                [
                    {"timestamp": 992, "flow_rate": 1.0, "quality": 90},
                    {"timestamp": 996, "flow_rate": 2.0, "quality": 91},
                    {"timestamp": 999, "flow_rate": 3.0, "quality": 92},
                ]
            ),
            {"chunk_count": 1, "fetch_elapsed_seconds": 0.01},
        )

    snapshot = rfs.build_recent_flow_snapshot(
        "BBFLOW",
        "tok",
        network_type="wifi",
        now=now,
        timeout_seconds=3,
        fetch_flow_data_range=fake_fetch,
    )

    assert snapshot["state"] == "checked"
    assert snapshot["window_seconds"] == 300
    assert snapshot["sample_count"] == 3
    assert snapshot["valid_flow_count"] == 3
    assert snapshot["latest_sample_age_seconds"] == 1
    assert snapshot["latest_sample_fresh"] is True
    assert snapshot["latest_flow_rate"] == 3.0
    assert snapshot["mean_flow_rate"] == 2.0
    assert snapshot["min_flow_rate"] == 1.0
    assert snapshot["max_flow_rate"] == 3.0
    assert snapshot["gap_count"] == 0
    assert snapshot["snapshot_quality"] == "usable"
    assert snapshot["signal_quality"]["latest"] == 92


def test_recent_flow_snapshot_empty_is_checked_absence():
    def fake_fetch(*_args, **_kwargs):
        return (
            _FakeDataFrame([]),
            {"chunk_count": 1},
        )

    snapshot = rfs.build_recent_flow_snapshot(
        "BBFLOW",
        "tok",
        network_type="unknown",
        now=1_000,
        fetch_flow_data_range=fake_fetch,
    )

    assert snapshot["state"] == "empty"
    assert snapshot["sample_count"] == 0
    assert snapshot["valid_flow_count"] == 0
    assert "No high-res flow samples" in snapshot["reason"]


def test_recent_flow_snapshot_timeout_fails_open():
    def fake_fetch(*_args, **_kwargs):
        raise TimeoutError("too slow")

    snapshot = rfs.build_recent_flow_snapshot(
        "BBFLOW",
        "tok",
        network_type="wifi",
        now=1_000,
        timeout_seconds=1,
        fetch_flow_data_range=fake_fetch,
    )

    assert snapshot["state"] == "timed_out"
    assert snapshot["timeout_seconds"] == 1
    assert "timed out" in snapshot["reason"]


def test_prompt_only_allows_no_recent_flow_for_empty_state():
    prompt = mc.format_meter_context_for_prompt(
        {"serial_number": "BB1", "recent_flow": {"state": "timed_out"}}
    )

    assert "only say there is no recent flow data when recent_flow.state is 'empty'" in prompt
    assert "'not_checked', 'timed_out', or 'unavailable'" in prompt
