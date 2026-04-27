from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ORCH_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "agent.py"
_ORCH_DIR = str(_ORCH_PATH.parent)


def _load_agent():
    sys.path.insert(0, _ORCH_DIR)
    for name in list(sys.modules):
        if name == "processors" or name.startswith("processors."):
            sys.modules.pop(name, None)
    name = "meter_orchestrator_agent_config_workflow_tests"
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
        r = self._responses[self._i]
        self._i += 1
        return r


def _tool_use_response(orch, calls: list[tuple[str, str, dict]]):
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


def _end_turn_response():
    from llm.base import LLMResponse

    return LLMResponse(
        text="done",
        stop_reason="end_turn",
        assistant_content=[{"type": "text", "text": "done"}],
        input_tokens=100,
        output_tokens=2,
    )


def _stub_common(monkeypatch: pytest.MonkeyPatch, orch):
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")
    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Kitchen meter", "deviceTimeZone": "America/New_York"},
            "transducer_angle_options": ["30", "45"],
        },
    )
    orch.clear_pending_actions_for_tests()


def test_write_tool_emits_pending_confirmation_without_dispatch(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    dispatch_calls: list[str] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append(name)
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "w1",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": "45"},
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "set angle"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        on_event=events.append,
    )

    assert dispatch_calls == []
    assert provider._i == 1
    pending = [e for e in events if e.get("type") == "config_confirmation_required"]
    assert len(pending) == 1
    failed_results = [
        e
        for e in events
        if e.get("type") == "tool_result" and e.get("success") is False
    ]
    assert failed_results == []
    workflow = pending[0]["config_workflow"]
    assert workflow["status"] == "pending_confirmation"
    assert workflow["proposed_values"]["transducer_angle"] == "45"


def test_pipe_configuration_emits_pending_confirmation_without_dispatch(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    dispatch_calls: list[str] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append(name)
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "p1",
                        "configure_meter_pipe",
                        {
                            "serial_number": "BB1",
                            "pipe_material": "PVC",
                            "pipe_standard": "SCH40",
                            "pipe_size": "2 inch",
                            "transducer_angle": "45",
                        },
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "configure pipe"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        on_event=events.append,
    )

    assert dispatch_calls == []
    assert provider._i == 1
    pending = [e for e in events if e.get("type") == "config_confirmation_required"]
    assert len(pending) == 1
    workflow = pending[0]["config_workflow"]
    assert workflow["tool"] == "configure_meter_pipe"
    assert workflow["status"] == "pending_confirmation"
    assert workflow["proposed_values"]["pipe_material"] == "PVC"
    assert workflow["proposed_values"]["pipe_standard"] == "SCH40"
    assert workflow["proposed_values"]["pipe_size"] == "2 inch"
    assert workflow["proposed_values"]["transducer_angle"] == "45"


def test_confirmed_action_executes_and_verifies(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    calls: list[str] = []
    action = orch.create_pending_action(
        conversation_id="conv",
        user_scope=orch.user_scope_from_token("tok"),
        tool_name="set_transducer_angle_only",
        inputs={"serial_number": "BB1", "transducer_angle": "45"},
    )

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        calls.append(name)
        if name == "check_meter_status":
            return json.dumps(
                {
                    "success": True,
                    "status_data": {
                        "serial_number": "BB1",
                        "online": True,
                        "last_message_at": "2026-04-26T12:00:00Z",
                        "signal": {"level": "good", "score": 82, "reliable": True},
                        "pipe_config": {"nominal_size": "2 inch"},
                    },
                }
            )
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "confirm"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        confirmed_action_id=action.action_id,
        on_event=events.append,
    )

    assert calls == ["set_transducer_angle_only", "check_meter_status"]
    assert "Confirmed" in reply
    workflow_statuses = [
        e.get("config_workflow", {}).get("status")
        for e in events
        if isinstance(e.get("config_workflow"), dict)
    ]
    assert "executed" in workflow_statuses
    assert "verified" in workflow_statuses


def test_cancelled_action_consumes_pending_without_dispatch(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    calls: list[str] = []
    action = orch.create_pending_action(
        conversation_id="conv",
        user_scope=orch.user_scope_from_token("tok"),
        tool_name="set_transducer_angle_only",
        inputs={"serial_number": "BB1", "transducer_angle": "45"},
    )

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        calls.append(name)
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "cancel"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        cancelled_action_id=action.action_id,
        on_event=events.append,
    )

    assert calls == []
    assert reply == "Cancelled. No device changes were sent."
    assert orch.get_pending_action("conv", orch.user_scope_from_token("tok"), action.action_id) is None
    statuses = [
        e.get("config_workflow", {}).get("status")
        for e in events
        if isinstance(e.get("config_workflow"), dict)
    ]
    assert "cancelled" in statuses


def test_superseded_action_consumes_old_action_and_continues_turn(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    dispatch_calls: list[str] = []
    old_action = orch.create_pending_action(
        conversation_id="conv",
        user_scope=orch.user_scope_from_token("tok"),
        tool_name="set_transducer_angle_only",
        inputs={"serial_number": "BB1", "transducer_angle": "45"},
    )

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append(name)
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "w2",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": "30"},
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "instead set it to 30"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        superseded_action_id=old_action.action_id,
        on_event=events.append,
    )

    assert dispatch_calls == []
    assert provider._i == 1
    assert orch.get_pending_action("conv", orch.user_scope_from_token("tok"), old_action.action_id) is None
    statuses = [
        e.get("config_workflow", {}).get("status")
        for e in events
        if isinstance(e.get("config_workflow"), dict)
    ]
    assert "superseded" in statuses
    assert "pending_confirmation" in statuses
    replacement = [
        e
        for e in events
        if e.get("type") == "config_confirmation_required"
    ][0]["config_workflow"]
    assert replacement["proposed_values"]["transducer_angle"] == "30"


def test_exact_match_validation_rejects_changed_payload(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    action = orch.create_pending_action(
        conversation_id="conv",
        user_scope="user",
        tool_name="set_transducer_angle_only",
        inputs={"serial_number": "BB1", "transducer_angle": "45"},
    )

    ok, error = orch.validate_pending_action(
        action,
        tool_name="set_transducer_angle_only",
        inputs={"serial_number": "BB1", "transducer_angle": "30"},
    )

    assert ok is False
    assert "values changed" in (error or "")
