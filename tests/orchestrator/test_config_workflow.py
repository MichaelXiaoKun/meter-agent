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


def test_sweep_emits_one_pending_confirmation_with_resolved_wifi_angles(
    monkeypatch: pytest.MonkeyPatch,
):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    dispatch_calls: list[str] = []

    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Kitchen meter", "deviceTimeZone": "America/New_York"},
            "transducer_angle_options": ["15º", "25º", "35º", "45º"],
        },
    )
    monkeypatch.setattr(
        orch,
        "_dispatch",
        lambda name, inp, token, *, client_timezone, anthropic_api_key: dispatch_calls.append(name)
        or json.dumps({"success": True}),
    )
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "s1",
                        "sweep_transducer_angles",
                        {"serial_number": "BB1"},
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "try all angles"}],
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
    assert workflow["tool"] == "sweep_transducer_angles"
    assert workflow["proposed_values"]["transducer_angles"] == ["15º", "25º", "35º", "45º"]
    assert workflow["proposed_values"]["apply_best_after_sweep"] is False
    assert workflow["proposed_values"]["final_angle_policy"] == "leave_last_successful_tested_angle"


def test_sweep_lorawan_all_allowed_uses_lorawan_options(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []

    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "lorawan",
            "profile": {"label": "Field meter"},
            "transducer_angle_options": [
                "10º",
                "15º",
                "20º",
                "25º",
                "30º",
                "35º",
                "40º",
                "45º",
            ],
        },
    )
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [("s1", "sweep_transducer_angles", {"serial_number": "BB-LORA"})],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "try each allowed angle"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        on_event=events.append,
    )

    workflow = [
        e
        for e in events
        if e.get("type") == "config_confirmation_required"
    ][0]["config_workflow"]
    assert workflow["proposed_values"]["network_type"] == "lorawan"
    assert workflow["proposed_values"]["transducer_angles"] == [
        "10º",
        "15º",
        "20º",
        "25º",
        "30º",
        "35º",
        "40º",
        "45º",
    ]


def test_sweep_explicit_angles_are_normalized_and_deduped(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []

    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Kitchen meter"},
            "transducer_angle_options": ["15º", "25º", "35º", "45º"],
        },
    )
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "s1",
                        "sweep_transducer_angles",
                        {
                            "serial_number": "BB1",
                            "transducer_angles": ["45", "45º", "35°"],
                        },
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "try 45 then 35"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        on_event=events.append,
    )

    workflow = [
        e
        for e in events
        if e.get("type") == "config_confirmation_required"
    ][0]["config_workflow"]
    assert workflow["proposed_values"]["transducer_angles"] == ["45º", "35º"]


def test_sweep_invalid_angle_fails_before_pending_confirmation(
    monkeypatch: pytest.MonkeyPatch,
):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []

    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Kitchen meter"},
            "transducer_angle_options": ["15º", "25º", "35º", "45º"],
        },
    )
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "s1",
                        "sweep_transducer_angles",
                        {"serial_number": "BB1", "transducer_angles": ["40"]},
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "try 40 degrees"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        on_event=events.append,
    )

    assert not [e for e in events if e.get("type") == "config_confirmation_required"]
    failed = [
        e
        for e in events
        if e.get("type") == "tool_result"
        and e.get("tool") == "sweep_transducer_angles"
        and e.get("success") is False
    ]
    assert failed
    assert "Valid" in failed[0]["message"]


def test_confirmed_sweep_runs_every_angle_and_status_check(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    events: list[dict] = []
    set_calls: list[str] = []
    status_calls: list[str] = []
    scores = {"15º": 65, "25º": 80, "35º": 72, "45º": 76}
    action = orch.create_pending_action(
        conversation_id="conv",
        user_scope=orch.user_scope_from_token("tok"),
        tool_name="sweep_transducer_angles",
        inputs={
            "serial_number": "BB1",
            "transducer_angles": ["15º", "25º", "35º", "45º"],
            "apply_best_after_sweep": False,
            "network_type": "wifi",
        },
    )

    def fake_set(serial, angle, token, *, anthropic_api_key=None):
        set_calls.append(angle)
        return {"success": True, "error": None}

    def fake_status(serial, token, *, anthropic_api_key=None):
        angle = set_calls[-1]
        status_calls.append(angle)
        return {
            "success": True,
            "status_data": {
                "serial_number": serial,
                "online": True,
                "last_message_at": "2026-04-26T12:00:00Z",
                "signal": {"level": "good", "score": scores[angle], "reliable": True},
            },
        }

    monkeypatch.setattr(orch, "set_transducer_angle_only", fake_set)
    monkeypatch.setattr(orch, "check_meter_status", fake_status)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "confirm"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        confirmed_action_id=action.action_id,
        on_event=events.append,
    )

    assert set_calls == ["15º", "25º", "35º", "45º"]
    assert status_calls == ["15º", "25º", "35º", "45º"]
    assert "swept 4 transducer angle" in reply
    result_event = [
        e
        for e in events
        if e.get("type") == "tool_result" and e.get("tool") == "sweep_transducer_angles"
    ][0]
    assert result_event["success"] is True
    assert result_event["tool_activity"].endswith("best 25º, final 45º")


def test_confirmed_optimize_sweep_sets_best_again(monkeypatch: pytest.MonkeyPatch):
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    set_calls: list[str] = []
    status_calls: list[str] = []
    scores = {"15º": 60, "25º": 91, "35º": 70}
    action = orch.create_pending_action(
        conversation_id="conv",
        user_scope=orch.user_scope_from_token("tok"),
        tool_name="sweep_transducer_angles",
        inputs={
            "serial_number": "BB1",
            "transducer_angles": ["15º", "25º", "35º"],
            "apply_best_after_sweep": True,
            "network_type": "wifi",
        },
    )

    def fake_set(serial, angle, token, *, anthropic_api_key=None):
        set_calls.append(angle)
        return {"success": True, "error": None}

    def fake_status(serial, token, *, anthropic_api_key=None):
        angle = set_calls[-1]
        status_calls.append(angle)
        return {
            "success": True,
            "status_data": {
                "serial_number": serial,
                "online": True,
                "last_message_at": "2026-04-26T12:00:00Z",
                "signal": {"level": "excellent", "score": scores[angle], "reliable": True},
            },
        }

    monkeypatch.setattr(orch, "set_transducer_angle_only", fake_set)
    monkeypatch.setattr(orch, "check_meter_status", fake_status)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "confirm"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
        confirmed_action_id=action.action_id,
    )

    assert set_calls == ["15º", "25º", "35º", "25º"]
    assert status_calls == ["15º", "25º", "35º", "25º"]
    assert "I set the best measured angle, 25º" in reply


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
