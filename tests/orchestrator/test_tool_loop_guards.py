"""Guards against unbounded tool loops and redundant idempotent tool runs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ORCH_AGENT_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "agent.py"
_ORCH_DIR = str(_ORCH_AGENT_PATH.parent)


def _load_orchestrator_agent():
    import sys

    sys.path.insert(0, _ORCH_DIR)
    name = "meter_orchestrator_agent_loop_guards"
    spec = importlib.util.spec_from_file_location(name, _ORCH_AGENT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator_agent()


def test_dedupe_key_analyze_flow_matches():
    k1 = orch._per_turn_tool_dedupe_key(
        "analyze_flow_data",
        {"serial_number": "BB81", "start": 1, "end": 2},
    )
    k2 = orch._per_turn_tool_dedupe_key(
        "analyze_flow_data",
        {"serial_number": "BB81", "start": 1, "end": 2},
    )
    assert k1 == k2


def test_write_tools_never_get_a_dedupe_key():
    # Mutations must always reach the device, even with identical args.
    assert (
        orch._per_turn_tool_dedupe_key(
            "set_transducer_angle_only",
            {"serial_number": "X", "transducer_angle": "45"},
        )
        is None
    )
    assert orch._per_turn_tool_dedupe_key("configure_meter_pipe", {}) is None


def test_read_tools_are_dedup_keyed_and_canonical():
    # Read-only tools are now cache-eligible within a single turn; the whole
    # args dict is canonicalised so argument-order differences still hit.
    k_status = orch._per_turn_tool_dedupe_key(
        "check_meter_status", {"serial_number": "BB81"}
    )
    k_profile = orch._per_turn_tool_dedupe_key(
        "get_meter_profile", {"serial_number": "BB81"}
    )
    assert k_status and k_profile and k_status != k_profile

    k1 = orch._per_turn_tool_dedupe_key(
        "get_meter_profile", {"serial_number": "BB81", "include_history": True}
    )
    k2 = orch._per_turn_tool_dedupe_key(
        "get_meter_profile", {"include_history": True, "serial_number": "BB81"}
    )
    assert k1 == k2

    assert orch._per_turn_tool_dedupe_key("unknown_tool", {}) is None


def test_write_invalidates_serial_tagged_read_entries():
    cache: dict[str, tuple[str, str | None]] = {
        "read-BB1": ("{}", "BB1"),
        "read-BB2": ("{}", "BB2"),
        "untagged": ("{}", None),
    }
    dropped = orch._invalidate_dedupe_for_write(
        cache,
        "set_transducer_angle_only",
        {"serial_number": "BB1", "transducer_angle": 30},
    )
    assert dropped == ["read-BB1"]
    assert "read-BB1" not in cache
    assert "read-BB2" in cache
    assert "untagged" in cache

    # Non-write tool must not touch the cache.
    assert (
        orch._invalidate_dedupe_for_write(cache, "check_meter_status", {"serial_number": "BB2"})
        == []
    )
    assert "read-BB2" in cache


def test_max_tool_rounds_clamped(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MAX_TOOL_ROUNDS", "500")
    assert orch._max_tool_rounds_per_turn() == 128
    monkeypatch.setenv("ORCHESTRATOR_MAX_TOOL_ROUNDS", "2")
    assert orch._max_tool_rounds_per_turn() == 4
