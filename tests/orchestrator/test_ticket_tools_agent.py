from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ORCH_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "admin_chat" / "turn_loop.py"
_ORCH_DIR = str(_ORCH_PATH.parent.parent)


def _load_agent():
    sys.path.insert(0, _ORCH_DIR)
    for name in list(sys.modules):
        if name == "processors" or name.startswith("processors."):
            sys.modules.pop(name, None)
    name = "meter_orchestrator_agent_ticket_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ScriptedProvider:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self._i = 0

    def count_tokens(self, model, messages, system, tools):
        return 100

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        if self._i >= len(self._responses):
            raise AssertionError("Fake provider ran out of scripted responses")
        response = self._responses[self._i]
        self._i += 1
        return response


def _tool_use_response(calls: list[tuple[str, str, dict]]):
    from llm.base import LLMResponse, ToolCall

    assistant_content = []
    tool_calls = []
    for tc_id, name, inp in calls:
        assistant_content.append(
            {"type": "tool_use", "id": tc_id, "name": name, "input": inp}
        )
        tool_calls.append(ToolCall(id=tc_id, name=name, input=inp))
    return LLMResponse(
        text="",
        stop_reason="tool_use",
        tool_calls=tool_calls,
        assistant_content=assistant_content,
        input_tokens=100,
        output_tokens=10,
    )


def _end_turn_response(text="done"):
    from llm.base import LLMResponse

    return LLMResponse(
        text=text,
        stop_reason="end_turn",
        assistant_content=[{"type": "text", "text": text}],
        input_tokens=100,
        output_tokens=2,
    )


@pytest.fixture
def orch_with_store(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "agent_tickets.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")
    orch = _load_agent()
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)
    import store

    store._bootstrapped.clear()
    store._ensure_ready()
    return orch, store


def test_agent_can_create_ticket_when_user_asks(monkeypatch, orch_with_store):
    orch, store = orch_with_store
    cid = store.create_conversation("alice", "track")
    events: list[dict] = []
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                [
                    (
                        "t1",
                        "create_ticket",
                        {
                            "title": "Follow up low signal",
                            "serial_number": "BB1",
                            "success_criteria": "Signal is verified above acceptable threshold.",
                            "priority": "high",
                            "owner_type": "agent",
                            "agent_checkable": True,
                        },
                    )
                ]
            ),
            _end_turn_response("Tracked it."),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "Track low signal follow-up for BB1"}],
        token="tok",
        model=orch._MODEL,
        conversation_id=cid,
        on_event=events.append,
    )

    tickets = store.list_tickets("alice", conversation_id=cid)
    assert len(tickets) == 1
    assert tickets[0]["title"] == "Follow up low signal"
    assert tickets[0]["owner_type"] == "agent"
    assert any(e.get("tool") == "create_ticket" and e.get("ticket") for e in events)


def test_simple_status_turn_does_not_create_ticket(monkeypatch, orch_with_store):
    orch, store = orch_with_store
    cid = store.create_conversation("alice", "status")
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                [("s1", "check_meter_status", {"serial_number": "BB1"})]
            ),
            _end_turn_response("Meter is online."),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(
        orch,
        "check_meter_status",
        lambda serial, token, **kwargs: {
            "success": True,
            "status_data": {"serial_number": serial, "online": True},
            "report": "Online.",
        },
    )

    orch.run_turn(
        [{"role": "user", "content": "Check BB1"}],
        token="tok",
        model=orch._MODEL,
        conversation_id=cid,
        on_event=lambda e: None,
    )

    assert store.list_tickets("alice", conversation_id=cid) == []
