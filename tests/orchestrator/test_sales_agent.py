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
    for name in ("api", "store", "agent", "sales_agent", "sales_tools"):
        sys.modules.pop(name, None)

    import api as api_mod  # noqa: WPS433
    import store  # noqa: WPS433

    importlib.reload(api_mod)
    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()
    return TestClient(api_mod.app), api_mod, store


def test_sales_tool_set_excludes_live_device_and_write_tools():
    import sales_agent

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
    import sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "human support" in prompt
    assert "Denis Zaff" in prompt
    assert "4085858829" in prompt
    assert "denis@bluebot.com" in prompt


def test_sales_prompt_rejects_off_topic_requests_kindly():
    import sales_agent

    prompt = sales_agent._SYSTEM_PROMPT
    assert "Off-topic guardrail" in prompt
    assert "kindly" in prompt
    assert "Do not answer the unrelated substance" in prompt
    assert "do not call tools solely for an off-topic request" in prompt
    assert "redirect to what you can help with" in prompt
    assert "bluebot product fit" in prompt


def test_sales_verifier_partially_validates_pipe_size_claims():
    from sales_verifier import validate_sales_answer_points

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
    from sales_verifier import verify_sales_response

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
    from sales_verifier import verify_sales_response

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
    import sales_agent

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
    assert any(e.get("verdict") == "pass" for e in events)


def test_run_sales_turn_large_pipe_question_never_uses_generic_fallback(monkeypatch):
    from llm.base import LLMResponse
    import sales_agent

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
    import sales_tools

    result = sales_tools.search_sales_kb("will it damage my pipe or cause pressure drop")
    assert result["success"] is True
    ids = {row["id"] for row in result["results"]}
    assert "pipe-impact" in ids
    assert any(link["url"].startswith("https://") for link in result["relevant_links"])


def test_capture_lead_summary_persists_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "lead_summary.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("store", "sales_tools"):
        sys.modules.pop(name, None)
    import store
    import sales_tools

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
    import sales_tools

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
