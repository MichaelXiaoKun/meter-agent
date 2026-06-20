"""
End-to-end check that ``run_turn`` emits JSONL events when
``BLUEBOT_EVENT_LOG_PATH`` is set.

Uses a fake LLM provider (no network). Assertions only inspect the
``turn_*`` and ``api_call_*`` events; tool loops are not exercised here.
"""

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
    name = "meter_orchestrator_agent_obs_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeProvider:
    def count_tokens(self, model, messages, system, tools):
        return 100

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        from llm.base import LLMResponse

        return LLMResponse(
            text="Short reply",
            stop_reason="end_turn",
            assistant_content=[{"type": "text", "text": "Short reply"}],
            input_tokens=100,
            output_tokens=5,
        )


class _ClarificationProvider:
    def __init__(self) -> None:
        self.stream_calls = 0

    def count_tokens(self, model, messages, system, tools):
        assert tools == []
        assert "Ask exactly one concise clarifying question" in system
        return 80

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        from llm.base import LLMResponse

        assert tools == []
        assert "Ask exactly one concise clarifying question" in system
        self.stream_calls += 1
        text = "Would you like me to check status, analyze flow history, or review pipe/angle configuration?"
        on_text_delta(text)
        return LLMResponse(
            text=text,
            stop_reason="end_turn",
            assistant_content=[{"type": "text", "text": text}],
            input_tokens=80,
            output_tokens=18,
        )


class _QuestionnairePlannerProvider:
    def __init__(self, plan_text: str, stream_text: str = "Final analysis") -> None:
        self.plan_text = plan_text
        self.stream_text = stream_text
        self.complete_calls = 0
        self.stream_calls = 0
        self.complete_messages: list | None = None
        self.stream_messages: list | None = None

    def count_tokens(self, model, messages, system, tools):
        return 90

    def complete(self, model, messages, system, tools, max_tokens):
        from llm.base import LLMResponse

        assert tools == []
        assert "planning layer" in system
        self.complete_calls += 1
        self.complete_messages = messages
        return LLMResponse(
            text=self.plan_text,
            stop_reason="end_turn",
            assistant_content=[{"type": "text", "text": self.plan_text}],
            input_tokens=90,
            output_tokens=30,
        )

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        from llm.base import LLMResponse

        self.stream_calls += 1
        self.stream_messages = messages
        on_text_delta(self.stream_text)
        return LLMResponse(
            text=self.stream_text,
            stop_reason="end_turn",
            assistant_content=[{"type": "text", "text": self.stream_text}],
            input_tokens=90,
            output_tokens=8,
        )


@pytest.fixture
def event_log_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "orchestr.jsonl"
    monkeypatch.setenv("BLUEBOT_EVENT_LOG_PATH", str(path))
    monkeypatch.delenv("BLUEBOT_EVENT_LOG_STDERR", raising=False)
    from shared import observability as obs  # pyright: ignore[reportMissingImports]

    obs._reset_for_tests()
    yield path
    obs._reset_for_tests()


def test_run_turn_emits_jsonl_turn_and_api_events(event_log_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = _load_agent()
    fake = _FakeProvider()
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")

    messages: list = [{"role": "user", "content": "Hello"}]
    reply, replaced = orch.run_turn(messages, token="test-token", model=orch._MODEL)  # noqa: SLF001
    assert reply == "Short reply"
    assert replaced is False

    lines = [json.loads(s) for s in event_log_path.read_text(encoding="utf-8").splitlines() if s.strip()]
    events = [r["event"] for r in lines if "event" in r]
    assert "turn_start" in events
    assert "turn_end" in events
    assert "api_call_start" in events
    assert "api_call_end" in events

    turn_ids = {r.get("turn_id") for r in lines if "turn_id" in r}
    assert len(turn_ids) == 1
    tid = next(iter(turn_ids))
    assert isinstance(tid, str) and len(tid) >= 8

    end = next(r for r in lines if r.get("event") == "turn_end")
    assert end.get("outcome") == "ok"
    assert end.get("api_calls", 0) >= 1

    start = next(r for r in lines if r.get("event") == "turn_start")
    assert start.get("prompt_version") == orch._SYSTEM_PROMPT_VERSION  # noqa: SLF001
    assert start.get("prompt_version"), "prompt_version must be non-empty for audit"


def test_run_turn_clarification_guard_uses_model_without_meter_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _load_agent()
    events: list[dict] = []
    fake = _ClarificationProvider()

    def fail_meter_context(*args, **kwargs):
        raise AssertionError("clarification guard should not prefetch meter context")

    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "build_meter_context_packet", fail_meter_context)
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "_resolve_tpm_input_guide_tokens", lambda *a, **k: 30_000)
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)

    messages: list = [{"role": "user", "content": "can you check?"}]
    reply, replaced = orch.run_turn(
        messages,
        token="test-token",
        model=orch._MODEL,  # noqa: SLF001
        on_event=events.append,
    )

    assert replaced is False
    assert fake.stream_calls == 1
    assert "status" in reply
    assert "flow history" in reply
    assert "pipe/angle configuration" in reply
    assert messages == [
        {"role": "user", "content": "can you check?"},
        {"role": "assistant", "content": [{"type": "text", "text": reply}]},
    ]
    assert not [e for e in events if e.get("type") == "tool_call"]
    assert any(e.get("type") == "intent_route" and e.get("intent") == "clarify" for e in events)
    assert any(e.get("type") == "thinking" for e in events)
    assert any(e.get("type") == "text_delta" and e.get("text") == reply for e in events)
    assert any(e.get("type") == "clarification_requested" for e in events)


def test_run_turn_questionnaire_planner_requests_questionnaire_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _load_agent()
    events: list[dict] = []
    plan = json.dumps(
        {
            "action": "ask_questionnaire",
            "message": "先选几个现场条件，我再继续判断。",
            "questions": [
                {
                    "id": "symptom",
                    "text": "信号差是持续的还是间歇的？",
                    "type": "single_choice",
                    "options": [
                        {"id": "persistent", "label": "长期持续"},
                        {"id": "intermittent", "label": "间歇出现"},
                    ],
                },
                {
                    "id": "context",
                    "text": "现场近期有哪些变化？",
                    "type": "multi_choice",
                    "options": [
                        {"id": "pipe", "label": "管道或安装位置变化"},
                        {"id": "network", "label": "网络/供电变化"},
                        {"id": "unknown", "label": "不确定"},
                    ],
                },
            ],
        }
    )
    fake = _QuestionnairePlannerProvider(plan)

    def fail_meter_context(*args, **kwargs):
        raise AssertionError("questionnaire planner should not prefetch meter context")

    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "build_meter_context_packet", fail_meter_context)
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "_resolve_tpm_input_guide_tokens", lambda *a, **k: 30_000)
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)

    messages: list = [
        {
            "role": "user",
            "content": "这个表长期信号差，是不是安装问题，下一步应该怎么处理？",
        }
    ]
    reply, replaced = orch.run_turn(
        messages,
        token="test-token",
        model=orch._MODEL,  # noqa: SLF001
        on_event=events.append,
    )

    assert replaced is False
    assert reply == "先选几个现场条件，我再继续判断。"
    assert fake.complete_calls == 1
    assert fake.stream_calls == 0
    assert messages[-1]["role"] == "assistant"
    blocks = messages[-1]["content"]
    assert blocks[0] == {"type": "text", "text": reply}
    questionnaire = blocks[1]
    assert questionnaire["type"] == "questionnaire"
    assert questionnaire["status"] == "pending"
    assert len(questionnaire["questions"]) == 2
    assert not [e for e in events if e.get("type") == "tool_call"]
    requested = [e for e in events if e.get("type") == "questionnaire_requested"]
    assert len(requested) == 1
    assert requested[0]["questionnaire"]["id"] == questionnaire["id"]


def test_run_turn_pending_questionnaire_blocks_unanswered_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _load_agent()
    events: list[dict] = []
    fake = _QuestionnairePlannerProvider('{"action":"proceed"}')

    def fail_meter_context(*args, **kwargs):
        raise AssertionError("pending questionnaire should block meter context")

    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "build_meter_context_packet", fail_meter_context)
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "_resolve_tpm_input_guide_tokens", lambda *a, **k: 30_000)

    messages: list = [
        {"role": "user", "content": "这个表长期信号差，下一步怎么处理？"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "先回答几个问题。"},
                {
                    "type": "questionnaire",
                    "id": "qq_test",
                    "status": "pending",
                    "message": "先回答几个问题。",
                    "questions": [
                        {
                            "id": "q1",
                            "text": "信号差持续多久？",
                            "type": "single_choice",
                            "options": [
                                {"id": "days", "label": "几天"},
                                {"id": "months", "label": "几个月"},
                            ],
                        }
                    ],
                },
            ],
        },
        {"role": "user", "content": "先继续分析吧"},
    ]

    reply, replaced = orch.run_turn(
        messages,
        token="test-token",
        model=orch._MODEL,  # noqa: SLF001
        on_event=events.append,
    )

    assert replaced is False
    assert "请先回答" in reply
    assert fake.complete_calls == 0
    assert fake.stream_calls == 0
    assert messages[-1] == {"role": "assistant", "content": [{"type": "text", "text": reply}]}
    requested = [e for e in events if e.get("type") == "questionnaire_requested"]
    assert requested and requested[-1]["pending"] is True
    assert requested[-1]["questionnaire"]["id"] == "qq_test"


def test_run_turn_questionnaire_response_skips_planner_and_reaches_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _load_agent()
    fake = _QuestionnairePlannerProvider('{"action":"ask_questionnaire","questions":[]}', "Thanks, I can continue.")
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "build_meter_context_packet", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_maybe_prepare_angle_experiment_from_validation", lambda **k: (None, None))
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "_resolve_tpm_input_guide_tokens", lambda *a, **k: 30_000)
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)

    messages: list = [
        {"role": "user", "content": "这个表长期信号差，下一步怎么处理？"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "先回答几个问题。"},
                {
                    "type": "questionnaire",
                    "id": "qq_test",
                    "status": "pending",
                    "message": "先回答几个问题。",
                    "questions": [
                        {
                            "id": "q1",
                            "text": "信号差持续多久？",
                            "type": "single_choice",
                            "options": [
                                {"id": "days", "label": "几天"},
                                {"id": "months", "label": "几个月"},
                            ],
                        }
                    ],
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "问卷回答：\n- 信号差持续多久？: 几个月"},
                {
                    "type": "questionnaire_response",
                    "questionnaire_id": "qq_test",
                    "answers": [
                        {
                            "question_id": "q1",
                            "option_ids": ["months"],
                            "labels": ["几个月"],
                        }
                    ],
                },
            ],
        },
    ]

    reply, replaced = orch.run_turn(messages, token="test-token", model=orch._MODEL)  # noqa: SLF001

    assert replaced is False
    assert reply == "Thanks, I can continue."
    assert fake.complete_calls == 0
    assert fake.stream_calls == 1
    assert fake.stream_messages is not None
    latest_user_content = fake.stream_messages[-1]["content"]
    assert latest_user_content == [{"type": "text", "text": "问卷回答：\n- 信号差持续多久？: 几个月"}]
