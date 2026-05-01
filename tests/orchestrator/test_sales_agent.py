"""Public sales-agent tests."""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)


def _client_and_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "sales_agent.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("api", "store", "agent", "sales_chat.agent", "sales_chat.tools"):
        sys.modules.pop(name, None)

    import api as api_mod  # noqa: WPS433
    import store  # noqa: WPS433

    importlib.reload(api_mod)
    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()
    return TestClient(api_mod.app), api_mod, store


_FORBIDDEN_GENERAL_OPENING_CLAIMS = (
    "bluebot can",
    "bluebot supports",
    "bluebot offers",
    "flow meter",
    "ultrasonic",
    "compatible",
    "installation",
    "connectivity",
    "can monitor",
)


def _assert_safe_general_opening(reply: str) -> None:
    lowered = reply.lower()
    assert "?" in reply
    assert "bluebot product fit" in lowered
    assert "application" in lowered or "pipe" in lowered
    assert not any(fragment in lowered for fragment in _FORBIDDEN_GENERAL_OPENING_CLAIMS)


def test_sales_tool_set_excludes_live_device_and_write_tools():
    from sales_chat import agent as sales_agent

    forbidden = {
        "check_meter_status",
        "get_meter_profile",
        "list_meters_for_account",
        "analyze_flow_data",
        "configure_meter_pipe",
        "set_transducer_angle_only",
        "sweep_transducer_angles",
        "set_zero_point",
    }
    assert forbidden.isdisjoint(sales_agent.SALES_TOOL_NAMES)
    assert {t["name"] for t in sales_agent.TOOL_DEFINITIONS} == sales_agent.SALES_TOOL_NAMES


def test_sales_prompt_includes_human_support_handoff_contact():
    from sales_chat import agent as sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "human support" in prompt
    assert "Denis Zaff" in prompt
    assert "4085858829" in prompt
    assert "denis@bluebot.com" in prompt


def test_sales_prompt_rejects_off_topic_requests_kindly():
    from sales_chat import agent as sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "Off-topic guardrail" in prompt
    assert "kindly" in prompt
    assert "Do not answer the unrelated substance" in prompt
    assert "do not call tools solely for an off-topic request" in prompt
    assert "redirect to what you can help with" in prompt
    assert "bluebot product fit" in prompt


def test_sales_prompt_keeps_greetings_general_without_tool_or_claim():
    from sales_chat import agent as sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "greeting or general opening" in prompt
    assert "do not call tools" in prompt
    assert "do not make Bluebot product" in prompt
    assert "ask one concise discovery question" in prompt


def test_sales_prompt_matches_admin_facing_style_without_internal_names():
    from sales_chat import agent as sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "Match the admin assistant's user-facing style" in prompt
    assert "concise, practical, and direct" in prompt
    assert "avoid internal tool" in prompt
    assert "The UI shows process status" in prompt


def test_sales_verifier_defaults_to_stronger_model_for_fast_drafts(monkeypatch):
    from sales_chat.verifier import active_sales_verifier_model

    monkeypatch.delenv("SALES_RESPONSE_VERIFIER_MODEL", raising=False)

    assert active_sales_verifier_model("claude-haiku-4-5") == "claude-sonnet-4-6"
    assert active_sales_verifier_model("gpt-4o-mini") == "gpt-4o"
    assert active_sales_verifier_model("gemini-2.0-flash") == "gemini-2.5-pro"


def test_sales_verifier_rejects_weaker_override_unless_explicitly_allowed(monkeypatch):
    from sales_chat.verifier import active_sales_verifier_model

    monkeypatch.setenv("SALES_RESPONSE_VERIFIER_MODEL", "claude-haiku-4-5")
    monkeypatch.delenv("SALES_RESPONSE_ALLOW_WEAKER_VERIFIER", raising=False)
    assert active_sales_verifier_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    monkeypatch.setenv("SALES_RESPONSE_ALLOW_WEAKER_VERIFIER", "true")
    assert active_sales_verifier_model("claude-sonnet-4-6") == "claude-haiku-4-5"


def test_general_sales_reply_uses_rough_validation_without_verifier_provider(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = "Hi! I can help with Bluebot product fit. What pipe size are you working with?"
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales should not stream")

    provider_calls: list[str] = []

    def fake_get_provider(model, **_kwargs):
        provider_calls.append(model)
        if model == "claude-haiku-4-5":
            return DraftProvider()
        raise AssertionError("rough validation should not allocate a verifier provider")

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.delenv("SALES_RESPONSE_GENERAL_VALIDATION", raising=False)
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "hello"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert "What pipe size" in reply
    assert provider_calls == ["claude-haiku-4-5"]
    assert any(e.get("validation_mode") == "rough" for e in events)
    assert not any(e.get("validation_mode") == "strong" for e in events)


def test_claimy_greeting_draft_is_normalized_before_validation(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = (
                "Hi! Bluebot offers ultrasonic flow meters for water monitoring. "
                "What pipe size are you working with?"
            )
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales should not stream")

    provider_calls: list[str] = []

    def fake_get_provider(model, **_kwargs):
        provider_calls.append(model)
        if model == "claude-haiku-4-5":
            return DraftProvider()
        raise AssertionError("general greeting should not allocate a verifier provider")

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.delenv("SALES_RESPONSE_GENERAL_VALIDATION", raising=False)
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "hello"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert reply != "Hi! What pipe size or application are you looking at?"
    assert reply.startswith("Hi!")
    assert "narrow down Bluebot product fit" in reply
    _assert_safe_general_opening(reply)
    assert provider_calls == ["claude-haiku-4-5"]
    assert not any(e.get("validation_mode") == "strong" for e in events)
    assert any(e.get("validation_mode") == "rough" for e in events)


def test_how_are_you_greeting_draft_is_normalized_before_validation(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = "I'm doing well! Bluebot can monitor water use and flow."
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales should not stream")

    def fake_get_provider(model, **_kwargs):
        if model == "claude-haiku-4-5":
            return DraftProvider()
        raise AssertionError("general greeting should not allocate a verifier provider")

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.delenv("SALES_RESPONSE_GENERAL_VALIDATION", raising=False)
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "how are you"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert reply != "Hi! What pipe size or application are you looking at?"
    assert "I'm doing well, thanks for asking." in reply
    _assert_safe_general_opening(reply)
    assert not any(e.get("validation_mode") == "strong" for e in events)


def test_safe_general_opening_templates_vary_by_user_intent():
    from sales_chat import agent as sales_agent

    hello = sales_agent._safe_general_opening_reply([{"role": "user", "content": "hello"}])
    how_are_you = sales_agent._safe_general_opening_reply(
        [{"role": "user", "content": "how are you"}]
    )
    thanks = sales_agent._safe_general_opening_reply([{"role": "user", "content": "thanks"}])
    help_me = sales_agent._safe_general_opening_reply(
        [{"role": "user", "content": "can you help"}]
    )

    assert hello != how_are_you
    assert how_are_you != thanks
    assert help_me != hello
    for reply in (hello, how_are_you, thanks, help_me):
        _assert_safe_general_opening(reply)


def test_run_sales_turn_strips_persisted_turn_activity_before_provider(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, model, messages, *, system, tools):
            self._assert_no_turn_activity(messages)
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            self._assert_no_turn_activity(messages)
            text = "I can help narrow down Bluebot product fit. What application are you looking at?"
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales should not stream")

        @staticmethod
        def _assert_no_turn_activity(messages):
            for msg in messages:
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    assert not any(
                        isinstance(block, dict) and block.get("type") == "turn_activity"
                        for block in content
                    )

    def fake_get_provider(model, **_kwargs):
        return DraftProvider()

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "off")
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Hi!"},
                {"type": "turn_activity", "v": 1, "events": [{"type": "done"}]},
            ],
        },
        {"role": "user", "content": "how are you"},
    ]

    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test")

    assert "Bluebot product fit" in reply
    assert messages[-1]["role"] == "assistant"


def test_general_validation_classifier_keeps_neutral_replies_rough():
    from sales_chat.verifier import classify_sales_validation

    assert classify_sales_validation(
        "Thanks, what pipe size is it?",
        [{"role": "user", "content": "I want to monitor irrigation."}],
    ).mode == "rough"
    assert classify_sales_validation(
        "I can't help with that unrelated request, but I can help with Bluebot product fit.",
        [{"role": "user", "content": "write a poem about the moon"}],
    ).mode == "rough"
    assert classify_sales_validation(
        "I can help with Bluebot product fit. Bluebot can monitor irrigation.",
        [{"role": "user", "content": "Can it help with irrigation?"}],
    ).mode == "strong"


def test_general_validation_skip_mode_only_skips_general_replies():
    from sales_chat.verifier import classify_sales_validation

    general = classify_sales_validation(
        "Hi! I can help with Bluebot product fit. What pipe size are you working with?",
        [{"role": "user", "content": "hello"}],
        configured_mode="skip",
    )
    pipe = classify_sales_validation(
        "Bluebot supports pipes up to 24 inches.",
        [{"role": "user", "content": "What pipe sizes work?"}],
        configured_mode="skip",
    )

    assert general.mode == "skipped"
    assert general.escalated is False
    assert pipe.mode == "strong"
    assert pipe.escalated is True


def test_evidence_claims_escalate_to_strong_validation():
    from sales_chat.verifier import classify_sales_validation

    tool_messages = [
        {"role": "user", "content": "Can Bluebot help with irrigation?"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search_sales_kb",
                    "input": {"query": "irrigation"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "{\"success\": true}",
                }
            ],
        },
    ]

    product = classify_sales_validation(
        "I recommend Bluebot ProLink Prime for this site.",
        [{"role": "user", "content": "What should I buy?"}],
    )
    pipe = classify_sales_validation(
        "Bluebot supports pipes up to 24 inches.",
        [{"role": "user", "content": "What pipe sizes work?"}],
    )
    evidence = classify_sales_validation(
        "Based on Bluebot materials, it can help with irrigation monitoring.",
        tool_messages,
    )
    neutral = classify_sales_validation("Thanks, what pipe size is it?", tool_messages)

    assert product.mode == "strong"
    assert pipe.mode == "strong"
    assert evidence.mode == "strong"
    assert neutral.mode == "rough"
    assert product.escalated is True
    assert pipe.escalated is True


def test_general_validation_strong_env_preserves_always_strong_behavior(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = "Hi! What pipe size are you working with?"
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales should not stream")

    class VerifierProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, model, messages, *, system, tools, max_tokens):
            self.calls += 1
            payload = {
                "passed": True,
                "verdict": "pass",
                "message": "General reply is safe.",
                "validation_points": [],
                "issues": [],
                "corrected_answer": "",
            }
            return LLMResponse(
                text=json.dumps(payload),
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": json.dumps(payload)}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("verifier should not stream")

        def count_tokens(self, *args, **kwargs):  # pragma: no cover - not used here
            return 1

    verifier_provider = VerifierProvider()

    def fake_get_provider(model, **_kwargs):
        if model == "claude-sonnet-4-6":
            return verifier_provider
        return DraftProvider()

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.setenv("SALES_RESPONSE_GENERAL_VALIDATION", "strong")
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "hello"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert "pipe size" in reply
    assert verifier_provider.calls == 1
    assert any(
        e.get("validation_mode") == "strong" and e.get("escalated") is False
        for e in events
        if e["type"] == "validation_start"
    )


def test_sales_verifier_partially_validates_pipe_size_claims():
    from sales_chat.verifier import validate_sales_answer_points

    points = validate_sales_answer_points(
        "bluebot can support 1, 2, 3 inches, and up to 24 inches."
    )

    supported = [p for p in points if p["status"] == "supported"]
    unsupported = [p for p in points if p["status"] == "unsupported"]

    assert any("1 inch" in p["claim"] for p in supported)
    assert any("2 inch" in p["claim"] for p in supported)
    assert any("3 inch" in p["claim"] for p in supported)
    assert any("24" in p["claim"] for p in unsupported)
    assert any("4.0 inch" in p["correction"] for p in unsupported)


def test_sales_verifier_rewrites_until_supported_by_scraped_context():
    from llm.base import LLMResponse
    from sales_chat.verifier import verify_sales_response

    class FakeVerifierProvider:
        def __init__(self):
            self.calls = []
            self.responses = [
                {
                    "passed": False,
                    "verdict": "needs_revision",
                    "message": "Found an unsupported pipe-size claim.",
                    "issues": ["unsupported_pipe_size"],
                    "corrected_answer": (
                        "Bluebot public materials list clamp-on meters for 3/4 inch "
                        "through 4.0 inch pipes."
                    ),
                },
                {
                    "passed": True,
                    "verdict": "pass",
                    "message": "Supported by Bluebot public materials.",
                    "issues": [],
                    "corrected_answer": "",
                },
            ]

        def complete(self, model, messages, *, system, tools, max_tokens):
            self.calls.append({"model": model, "messages": messages, "system": system})
            payload = self.responses.pop(0)
            return LLMResponse(
                text=json.dumps(payload),
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": json.dumps(payload)}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("verifier should not stream")

        def count_tokens(self, *args, **kwargs):  # pragma: no cover - not used here
            return 1

    provider = FakeVerifierProvider()
    events = []
    outcome = verify_sales_response(
        "Bluebot supports pipes under 1 inch through 24+ inches.",
        [{"role": "user", "content": "What pipe sizes does Bluebot support?"}],
        verifier_provider=provider,
        verifier_model="claude-sonnet-4-6",
        max_attempts=3,
        on_event=events.append,
    )

    assert outcome.passed is True
    assert outcome.attempts == 2
    assert "24" not in outcome.answer
    assert "4.0 inch" in outcome.answer
    assert "pipe_size_max_in" in provider.calls[0]["messages"][0]["content"]
    assert [e["type"] for e in events].count("validation_start") == 2
    assert any(e.get("next_action") == "revise_answer" for e in events)
    assert events[-1]["verdict"] == "pass"


def test_sales_verifier_uses_evidence_answer_when_model_returns_no_correction():
    from llm.base import LLMResponse
    from sales_chat.verifier import verify_sales_response

    class MalformedVerifierProvider:
        def complete(self, model, messages, *, system, tools, max_tokens):
            return LLMResponse(
                text="not json",
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": "not json"}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("verifier should not stream")

        def count_tokens(self, *args, **kwargs):  # pragma: no cover - not used here
            return 1

    events = []
    outcome = verify_sales_response(
        "Bluebot can support 1, 2, 3 inches, and up to 24 inches.",
        [{"role": "user", "content": "What pipe sizes does Bluebot support?"}],
        verifier_provider=MalformedVerifierProvider(),
        verifier_model="claude-sonnet-4-6",
        max_attempts=1,
        on_event=events.append,
    )

    assert outcome.passed is False
    assert "I want to avoid giving you an unverified answer" not in outcome.answer
    assert "2.5, 3.0, and 4.0 inch" in outcome.answer
    assert "24 inch pipe support" in outcome.answer
    assert any(e.get("next_action") == "send_evidence_backed_answer" for e in events)


def test_run_sales_turn_sends_only_verified_sales_text(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = "Bluebot supports pipes under 1 inch through 24+ inches."
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales final answer should not stream before verification")

    class VerifierProvider:
        def __init__(self):
            self.responses = [
                {
                    "passed": False,
                    "verdict": "needs_revision",
                    "message": "Found an unsupported pipe-size claim.",
                    "issues": ["unsupported_pipe_size"],
                    "corrected_answer": (
                        "Bluebot public materials list clamp-on meters for 3/4 inch "
                        "through 4.0 inch pipes."
                    ),
                },
                {
                    "passed": True,
                    "verdict": "pass",
                    "message": "Supported by Bluebot public materials.",
                    "issues": [],
                    "corrected_answer": "",
                },
            ]

        def complete(self, model, messages, *, system, tools, max_tokens):
            payload = self.responses.pop(0)
            return LLMResponse(
                text=json.dumps(payload),
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": json.dumps(payload)}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("verifier should not stream")

        def count_tokens(self, *args, **kwargs):  # pragma: no cover - not used here
            return 1

    draft_provider = DraftProvider()
    verifier_provider = VerifierProvider()

    def fake_get_provider(model, **_kwargs):
        if model == "claude-sonnet-4-6":
            return verifier_provider
        return draft_provider

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "What pipe sizes does Bluebot support?"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert "24" not in reply
    assert "4.0 inch" in reply
    text_events = [e["text"] for e in events if e["type"] == "text_delta"]
    assert text_events == [reply]
    assert messages[-1]["content"][0]["text"] == reply
    assert any(e["type"] == "validation_start" for e in events)
    assert any(
        e.get("draft_model") == "claude-haiku-4-5"
        and e.get("validator_model") == "claude-sonnet-4-6"
        and e.get("validation_mode") == "strong"
        and e.get("escalated") is True
        for e in events
        if e["type"] == "validation_start"
    )
    assert any(e.get("verdict") == "pass" for e in events)


def test_run_sales_turn_large_pipe_question_never_uses_generic_fallback(monkeypatch):
    from llm.base import LLMResponse
    from sales_chat import agent as sales_agent

    class DraftProvider:
        def count_tokens(self, *args, **kwargs):
            return 1

        def complete(self, model, messages, *, system, tools, max_tokens):
            text = "Bluebot offers devices for large pipes up to 24 inches."
            return LLMResponse(
                text=text,
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": text}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("sales final answer should not stream before verification")

    class MalformedVerifierProvider:
        def complete(self, model, messages, *, system, tools, max_tokens):
            return LLMResponse(
                text="not json",
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": "not json"}],
            )

        def stream(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("verifier should not stream")

        def count_tokens(self, *args, **kwargs):  # pragma: no cover - not used here
            return 1

    def fake_get_provider(model, **_kwargs):
        if model == "claude-sonnet-4-6":
            return MalformedVerifierProvider()
        return DraftProvider()

    monkeypatch.setenv("SALES_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION", "on")
    monkeypatch.setenv("SALES_RESPONSE_VERIFICATION_ATTEMPTS", "2")
    monkeypatch.setattr(sales_agent, "get_provider", fake_get_provider)

    events = []
    messages = [{"role": "user", "content": "what kind of devices do you offer for large pipe?"}]
    reply = sales_agent.run_sales_turn(messages, conversation_id="sales-test", on_event=events.append)

    assert "I want to avoid giving you an unverified answer" not in reply
    assert "2.5, 3.0, and 4.0 inch" in reply
    assert "Bluebot Prime" in reply
    assert "Bluebot ProLink Prime" in reply
    assert any(e.get("next_action") == "send_evidence_backed_answer" for e in events)


def test_sales_kb_retrieves_pipe_impact_guidance():
    from sales_chat import tools as sales_tools

    result = sales_tools.search_sales_kb("will it damage my pipe or cause pressure drop")
    assert result["success"] is True
    ids = {row["id"] for row in result["results"]}
    assert "pipe-impact" in ids
    assert any(link["url"].startswith("https://") for link in result["relevant_links"])


def test_capture_lead_summary_persists_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "lead_summary.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("store", "sales_chat.tools"):
        sys.modules.pop(name, None)
    if "sales_chat" in sys.modules:
        sys.modules["sales_chat"].__dict__.pop("tools", None)
    import store
    sales_tools = importlib.import_module("sales_chat.tools")

    importlib.reload(store)
    importlib.reload(sales_tools)
    store._bootstrapped.clear()
    store._ensure_ready()
    conv_id = store.create_sales_conversation()

    result = sales_tools.capture_lead_summary(
        conv_id,
        {
            "application": "irrigation monitoring",
            "pipe_material": "PVC",
            "pipe_size": "4 inch",
            "liquid": "water",
        },
    )

    assert result["success"] is True
    assert result["lead_summary"]["application"] == "irrigation monitoring"
    assert result["lead_summary"]["pipe_material"] == "PVC"
    assert "expected_flow_range" in result["missing_fields"]
    assert store.load_sales_lead_summary(conv_id)["pipe_size"] == "4 inch"


def test_sqlite_env_can_point_to_volume_directory(tmp_path, monkeypatch):
    volume_dir = tmp_path / "railway-volume"
    volume_dir.mkdir()
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(volume_dir))
    monkeypatch.setenv("DATABASE_URL", "")
    sys.modules.pop("store", None)
    import store

    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()

    conv_id = store.create_sales_conversation("Volume-backed")
    assert conv_id
    assert (volume_dir / "conversations.db").exists()


def test_recommend_product_line_uses_pipe_size_and_wifi_requirements():
    from sales_chat import tools as sales_tools

    small_wifi = sales_tools.recommend_product_line(
        pipe_size="1 inch",
        has_reliable_wifi=True,
        needs_long_range=False,
        application="home water monitoring",
    )
    assert small_wifi["success"] is True
    assert small_wifi["recommendations"]
    assert small_wifi["recommendations"][0]["name"] in {
        "Bluebot Flagship",
        "Bluebot Mini",
    }
    assert "/shop/" in small_wifi["recommendations"][0]["source_url"]
    assert small_wifi["relevant_links"]

    large_no_wifi = sales_tools.recommend_product_line(
        pipe_size="3 inch",
        has_reliable_wifi=False,
        needs_long_range=True,
        application="irrigation monitoring",
    )
    assert large_no_wifi["success"] is True
    assert large_no_wifi["recommendations"][0]["name"] == "Bluebot ProLink Prime"
    assert large_no_wifi["recommendations"][0]["source_url"].endswith("/bluebot-prolink-prime/")


def test_public_sales_api_requires_no_auth_and_persists_lead_summary(tmp_path, monkeypatch):
    client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)

    def fake_run_sales_turn(messages, *, conversation_id, on_event=None, **_kwargs):
        lead = {
            "application": "building water monitoring",
            "pipe_material": "copper",
            "liquid": "water",
        }
        store.update_sales_lead_summary(conversation_id, lead)
        if on_event:
            on_event({"type": "thinking"})
            on_event(
                {
                    "type": "validation_start",
                    "message": "Quick-checking whether this general reply needs Bluebot evidence.",
                    "validation_mode": "rough",
                }
            )
            on_event(
                {
                    "type": "validation_result",
                    "verdict": "pass",
                    "message": "No Bluebot product claims requiring evidence were detected.",
                    "next_action": "send_answer",
                    "validation_mode": "rough",
                }
            )
            on_event({"type": "lead_summary", "lead_summary": lead})
            on_event({"type": "text_delta", "text": "Yes, clamp-on monitoring can be a fit."})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Yes, clamp-on monitoring can be a fit.",
                    }
                ],
            }
        )
        return "Yes, clamp-on monitoring can be a fit."

    monkeypatch.setattr(api_mod, "run_sales_turn", fake_run_sales_turn)
    monkeypatch.setattr(api_mod, "acquire_run_turn_slot", lambda on_wait: None)
    monkeypatch.setattr(api_mod, "release_run_turn_slot", lambda: None)

    create = client.post("/api/public/sales/conversations", json={})
    assert create.status_code == 200
    conv_id = create.json()["id"]

    chat = client.post(
        f"/api/public/sales/conversations/{conv_id}/chat",
        json={"message": "Can this work for building water?", "client_turn_id": "sales-turn-1"},
    )
    assert chat.status_code == 200
    stream_id = chat.json()["stream_id"]

    done = False
    events = []
    cursor = 0
    for _ in range(50):
        poll = client.get(
            f"/api/public/sales/streams/{stream_id}/poll",
            params={"cursor": cursor, "wait_ms": 100},
        )
        assert poll.status_code == 200
        body = poll.json()
        events.extend(body["events"])
        cursor = body["next_cursor"]
        done = body["done"]
        if done:
            break
        time.sleep(0.02)

    assert done is True
    assert any(e["type"] == "lead_summary" for e in events)
    assert any(e.get("text") == "Yes, clamp-on monitoring can be a fit." for e in events)

    loaded = client.get(f"/api/public/sales/conversations/{conv_id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["lead_summary"]["pipe_material"] == "copper"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][-1]["role"] == "assistant"
    content = body["messages"][-1]["content"]
    assert isinstance(content, list)
    activity_blocks = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "turn_activity"
    ]
    assert len(activity_blocks) == 1
    persisted_event_types = [event["type"] for event in activity_blocks[0]["events"]]
    assert "thinking" in persisted_event_types
    assert "validation_start" in persisted_event_types
    assert "validation_result" in persisted_event_types
    assert "text_stream" in persisted_event_types
    assert persisted_event_types[-1] == "done"


def test_public_sales_conversation_crud_history_without_auth(tmp_path, monkeypatch):
    client, _api_mod, _store = _client_and_modules(tmp_path, monkeypatch)

    first = client.post("/api/public/sales/conversations", json={"title": "First"})
    second = client.post("/api/public/sales/conversations", json={"title": "Second"})
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["id"]
    second_id = second.json()["id"]
    assert len(first_id) >= 16

    listed = client.get(
        "/api/public/sales/conversations",
        params={"ids": f"{second_id},{first_id}"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert [row["id"] for row in body] == [second_id, first_id]

    renamed = client.patch(
        f"/api/public/sales/conversations/{first_id}",
        json={"title": "Renamed sales thread"},
    )
    assert renamed.status_code == 200
    loaded = client.get(
        "/api/public/sales/conversations",
        params={"ids": first_id},
    ).json()
    assert loaded[0]["title"] == "Renamed sales thread"

    deleted = client.delete(f"/api/public/sales/conversations/{first_id}")
    assert deleted.status_code == 200
    missing = client.get(f"/api/public/sales/conversations/{first_id}")
    assert missing.status_code == 404


def test_public_sales_share_link_snapshot_and_revoke_without_auth(tmp_path, monkeypatch):
    client, _api_mod, store = _client_and_modules(tmp_path, monkeypatch)

    created = client.post("/api/public/sales/conversations", json={"title": "Share me"})
    assert created.status_code == 200
    conv_id = created.json()["id"]
    store.append_sales_messages(
        conv_id,
        [
            {"role": "user", "content": "Will this damage my pipe?"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Bluebot clamps outside the pipe."}],
            },
        ],
    )

    shared = client.post(f"/api/public/sales/conversations/{conv_id}/share")
    assert shared.status_code == 200
    share_body = shared.json()
    assert share_body["token"]
    assert share_body["revoke_key"]

    public = client.get(f"/api/public/shares/{share_body['token']}")
    assert public.status_code == 200
    snapshot = public.json()
    assert snapshot["title"] == "Share me"
    assert snapshot["messages"][0]["content"] == "Will this damage my pipe?"

    denied_revoke = client.delete(
        f"/api/public/sales/shares/{share_body['token']}",
        params={"revoke_key": "wrong"},
    )
    assert denied_revoke.status_code == 404

    revoked = client.delete(
        f"/api/public/sales/shares/{share_body['token']}",
        params={"revoke_key": share_body["revoke_key"]},
    )
    assert revoked.status_code == 200
    missing = client.get(f"/api/public/shares/{share_body['token']}")
    assert missing.status_code == 404


def test_public_sales_cancel_endpoint_requires_no_auth(tmp_path, monkeypatch):
    client, _api_mod, _store = _client_and_modules(tmp_path, monkeypatch)

    created = client.post("/api/public/sales/conversations", json={"title": "Cancelable"})
    assert created.status_code == 200
    conv_id = created.json()["id"]

    cancelled = client.post(f"/api/public/sales/conversations/{conv_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True


def test_public_sales_status_endpoint_exposes_resumable_stream_without_auth(tmp_path, monkeypatch):
    client, api_mod, _store = _client_and_modules(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow_run_sales_turn(messages, *, conversation_id, on_event=None, **_kwargs):
        started.set()
        if on_event:
            on_event({"type": "thinking"})
        release.wait(timeout=2)
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Recovered after refresh."}],
            }
        )
        return "Recovered after refresh."

    monkeypatch.setattr(api_mod, "run_sales_turn", slow_run_sales_turn)
    monkeypatch.setattr(api_mod, "acquire_run_turn_slot", lambda on_wait: None)
    monkeypatch.setattr(api_mod, "release_run_turn_slot", lambda: None)

    created = client.post("/api/public/sales/conversations", json={"title": "Refresh"})
    assert created.status_code == 200
    conv_id = created.json()["id"]
    chat = client.post(
        f"/api/public/sales/conversations/{conv_id}/chat",
        json={"message": "Start a resumable turn", "client_turn_id": "refresh-turn"},
    )
    assert chat.status_code == 200
    assert started.wait(timeout=2)

    status = client.get(f"/api/public/sales/conversations/{conv_id}/status")
    assert status.status_code == 200
    body = status.json()
    assert body["processing"] is True
    stream_id = chat.json()["stream_id"]
    assert body["stream_id"] == stream_id
    assert body["turn_id"] == "refresh-turn"
    assert body["event_count"] >= 1

    release.set()
    cursor = 0
    for _ in range(50):
        poll = client.get(
            f"/api/public/sales/streams/{stream_id}/poll",
            params={"cursor": cursor, "wait_ms": 100},
        )
        assert poll.status_code == 200
        poll_body = poll.json()
        cursor = poll_body["next_cursor"]
        if poll_body["done"]:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("sales stream did not finish")
