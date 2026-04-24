"""
Per-turn tool result cache — covers three cases:

1. Read-only tool called twice in one turn ⇒ second call served from cache.
2. Write tool called twice ⇒ both hit the device (never dedup'd).
3. Write tool on serial X invalidates a prior cached read on serial X.
"""

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
    name = "meter_orchestrator_agent_dedupe_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ScriptedProvider:
    """Yield a predefined sequence of LLMResponse objects on successive .stream() calls."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._i = 0

    def count_tokens(self, model, messages, system, tools):
        return 100

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        if self._i >= len(self._responses):
            raise AssertionError(
                f"Fake provider ran out of scripted responses (called {self._i + 1}x)"
            )
        r = self._responses[self._i]
        self._i += 1
        return r


def _tool_use_response(orch, calls: list[tuple[str, str, dict]]):
    """Build an LLMResponse that triggers one or more tool_use blocks."""
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


@pytest.fixture
def event_log_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("BLUEBOT_EVENT_LOG_PATH", str(path))
    monkeypatch.delenv("BLUEBOT_EVENT_LOG_STDERR", raising=False)
    import observability as obs  # pyright: ignore[reportMissingImports]

    obs._reset_for_tests()
    yield path
    obs._reset_for_tests()


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(s)
        for s in path.read_text(encoding="utf-8").splitlines()
        if s.strip()
    ]


def _stub_common(monkeypatch, orch):
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")


def test_read_only_tool_is_deduped_within_one_turn(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True, "serial_number": inp.get("serial_number")})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    ("t1", "check_meter_status", {"serial_number": "BB1"}),
                    ("t2", "check_meter_status", {"serial_number": "BB1"}),
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "status?"}],
        token="tok",
        model=orch._MODEL,
    )
    assert reply == "done"
    assert len(dispatch_calls) == 1, dispatch_calls

    events = _read_events(event_log_path)
    hits = [e for e in events if e.get("event") == "tool_dedupe_hit"]
    assert len(hits) == 1
    assert hits[0]["tool"] == "check_meter_status"
    assert hits[0]["serial_number"] == "BB1"


def test_write_tools_are_never_deduped(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
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
                        {"serial_number": "BB1", "transducer_angle": 30},
                    ),
                    (
                        "w2",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": 30},
                    ),
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
    )
    assert len(dispatch_calls) == 2

    events = _read_events(event_log_path)
    assert not any(e.get("event") == "tool_dedupe_hit" for e in events)


def test_write_invalidates_cached_read_for_same_serial(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True, "serial_number": inp.get("serial_number")})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch, [("r1", "check_meter_status", {"serial_number": "BB1"})]
            ),
            _tool_use_response(
                orch,
                [
                    (
                        "w1",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": 30},
                    )
                ],
            ),
            _tool_use_response(
                orch, [("r2", "check_meter_status", {"serial_number": "BB1"})]
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "status, set, verify"}],
        token="tok",
        model=orch._MODEL,
    )

    names = [name for (name, _args) in dispatch_calls]
    assert names == [
        "check_meter_status",
        "set_transducer_angle_only",
        "check_meter_status",
    ]

    events = _read_events(event_log_path)
    assert any(e.get("event") == "tool_dedupe_invalidate" for e in events)
    assert not any(e.get("event") == "tool_dedupe_hit" for e in events)


def test_dedupe_key_is_canonical_regardless_of_arg_order(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "g1",
                        "get_meter_profile",
                        {"serial_number": "BB1", "include_history": True},
                    ),
                    (
                        "g2",
                        "get_meter_profile",
                        {"include_history": True, "serial_number": "BB1"},
                    ),
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "get profile"}],
        token="tok",
        model=orch._MODEL,
    )
    assert len(dispatch_calls) == 1

    events = _read_events(event_log_path)
    assert any(e.get("event") == "tool_dedupe_hit" for e in events)
