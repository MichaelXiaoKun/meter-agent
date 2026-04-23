"""
Regression tests for per-turn intent routing: tool subsets and rule-based labels.

``pythonpath`` includes ``data-processing-agent``, whose ``agent.py`` shadows the
orchestrator's ``agent`` module. Load ``orchestrator/agent.py`` by file path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ORCH_AGENT_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "agent.py"
_ORCH_DIR = str(_ORCH_AGENT_PATH.parent)


def _load_orchestrator_agent():
    # ``pythonpath`` lists ``data-processing-agent`` before ``orchestrator``; both have a
    # top-level ``processors`` package, but only the orchestrator has ``time_range``. Prepend
    # the orchestrator tree so ``import processors...`` resolves correctly.
    sys.path.insert(0, _ORCH_DIR)
    name = "meter_orchestrator_agent_intent_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_AGENT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator_agent()


def _names(tools: list) -> set[str]:
    return {t["name"] for t in tools if t.get("name") is not None}


class TestToolsForIntentLabel:
    def test_flow_includes_analyze_flow_data_only_in_flow(self) -> None:
        flow = _names(orch._tools_for_intent_label("flow"))
        assert "analyze_flow_data" in flow
        for label in ("status", "general", "config"):
            assert "analyze_flow_data" not in _names(orch._tools_for_intent_label(label))

    def test_config_includes_mutations_not_analyze(self) -> None:
        cfg = _names(orch._tools_for_intent_label("config"))
        assert "configure_meter_pipe" in cfg
        assert "set_transducer_angle_only" in cfg
        assert "analyze_flow_data" not in cfg

    def test_status_and_general_are_read_only_base(self) -> None:
        for label in ("status", "general"):
            n = _names(orch._tools_for_intent_label(label))
            assert n == {
                "resolve_time_range",
                "check_meter_status",
                "get_meter_profile",
                "list_meters_for_account",
            }


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Is BB8100015261 online?", "status"),
        ("What is the signal quality?", "status"),
        ("List meters for alice@example.com", "status"),
        ("Show flow chart for last 24 hours", "flow"),
        ("Analyze flow yesterday for BB81", "flow"),
        ("Set transducer angle to 45 degrees", "config"),
        ("PVC pipe schedule 40 install", "config"),
        ("Hello, what can you do?", "general"),
        ("", "general"),
    ],
)
def test_route_intent_rules(text: str, expected: str) -> None:
    assert orch._route_intent_rules(text) == expected


def test_resolve_routed_tools_off_full_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")

    tools, label, src = orch._resolve_routed_tools(
        MagicMock(), [{"role": "user", "content": "anything"}], emit=None
    )
    assert label == "full"
    assert src == "off"
    assert len(tools) == len(orch.TOOLS)
    assert _names(tools) == _names(orch.TOOLS)


def test_resolve_routed_tools_rules_status_and_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "rules")

    events: list[dict] = []

    def emit(e: dict) -> None:
        events.append(e)

    tools, label, src = orch._resolve_routed_tools(
        MagicMock(),
        [{"role": "user", "content": "Is the meter online?"}],
        emit=emit,
    )
    assert src == "rules"
    assert label == "status"
    assert "analyze_flow_data" not in _names(tools)
    assert any(e.get("type") == "intent_route" and e.get("intent") == "status" for e in events)
