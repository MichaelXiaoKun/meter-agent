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
