from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_ORCH_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "admin_chat" / "turn_loop.py"
_ORCH_DIR = str(_ORCH_PATH.parent.parent)


def _clear_tpm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("ORCHESTRATOR_TPM_GUIDE_TOKENS"):
            monkeypatch.delenv(key, raising=False)
        if key.startswith("ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ALLOWED_MODELS", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_LIVE_ANTHROPIC_LIMITS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _load_agent():
    sys.path.insert(0, _ORCH_DIR)
    name = "meter_orchestrator_agent_tpm_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_model_catalog_exposes_distinct_tpm_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tpm_env(monkeypatch)
    orch = _load_agent()

    models = {m["id"]: m for m in orch.list_available_models()}

    assert models["claude-haiku-4-5"]["tpm_input_guide_tokens"] == 50_000
    assert models["claude-sonnet-4-5"]["tpm_input_guide_tokens"] == 30_000
    assert models["gpt-4o-mini"]["tpm_input_guide_tokens"] == 200_000
    assert models["claude-haiku-4-5"]["max_input_tokens_target"] != models["gpt-4o-mini"][
        "max_input_tokens_target"
    ]
    assert models["gpt-4o-mini"]["context_window"] == 128_000
    assert "tpm_sliding_input_tokens_60s" in models["gpt-4o-mini"]


def test_known_models_ignore_legacy_global_tpm_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tpm_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_TPM_GUIDE_TOKENS", "12345")
    monkeypatch.setenv("ORCHESTRATOR_TPM_GUIDE_TOKENS_GPT_4O_MINI", "222222")
    orch = _load_agent()

    assert orch._resolve_tpm_input_guide_tokens("claude-haiku-4-5") == 50_000  # noqa: SLF001
    assert orch._resolve_tpm_input_guide_tokens("gpt-4o-mini") == 222_222  # noqa: SLF001
    assert orch._resolve_tpm_input_guide_tokens("custom-model") == 12_345  # noqa: SLF001


def test_live_anthropic_headers_override_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tpm_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    orch = _load_agent()

    monkeypatch.setattr(
        orch,
        "_live_anthropic_rate_limit_values",
        lambda model: {
            "input_tpm_limit": 450_000,
            "output_tpm_limit": 90_000,
            "rpm_limit": 1_000,
            "total_tpm_limit": 540_000,
        }
        if model == "claude-haiku-4-5"
        else None,
    )

    models = {m["id"]: m for m in orch.list_available_models()}

    assert models["claude-haiku-4-5"]["tpm_input_guide_tokens"] == 450_000
    assert models["claude-haiku-4-5"]["output_tpm_limit"] == 90_000
    assert models["claude-haiku-4-5"]["rpm_limit"] == 1_000
    assert models["claude-haiku-4-5"]["rate_limit_source"] == "anthropic_headers"


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


def test_run_turn_uses_selected_model_tpm_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tpm_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")
    orch = _load_agent()
    fake = _FakeProvider()
    wait_calls: list[tuple[int, int, str | None]] = []

    def fake_wait(estimated_next_input_tokens, tpm_limit, **kwargs):
        wait_calls.append((estimated_next_input_tokens, tpm_limit, kwargs.get("model")))

    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: fake)
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", fake_wait)

    messages: list = [{"role": "user", "content": "Hello"}]
    reply, replaced = orch.run_turn(messages, token="test-token", model="gpt-4o-mini")

    assert reply == "Short reply"
    assert replaced is False
    assert ("gpt-4o-mini" in [call[2] for call in wait_calls])
    assert any(limit == 200_000 for _, limit, model in wait_calls if model == "gpt-4o-mini")
