"""
Factory: create an LLMProvider from a model ID.

API keys are read from environment variables:
  ANTHROPIC_API_KEY  — Claude models (claude-*)
  OPENAI_API_KEY     — OpenAI models (gpt-*, o1, o3-*)
  GEMINI_API_KEY     — Google Gemini via OpenAI-compatible endpoint (gemini-*)

The optional *api_key_override* takes precedence over env vars so the web UI
can forward per-request keys without storing them server-side.
"""

from __future__ import annotations

import os

import httpx

from .base import LLMProvider
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAICompatibleProvider
from .registry import get_provider_name

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Generous timeouts: flow-analysis sub-agent calls can be long.
_ANTHROPIC_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=600.0)


def get_provider(
    model_id: str,
    *,
    api_key_override: str | None = None,
) -> LLMProvider:
    """Return the LLMProvider for *model_id*, wired with the right API key."""
    provider_name = get_provider_name(model_id)

    if provider_name == "anthropic":
        key = (api_key_override or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "Missing Anthropic API key. Set ANTHROPIC_API_KEY or provide it via the UI."
            )
        return AnthropicProvider(api_key=key, timeout=_ANTHROPIC_TIMEOUT)

    if provider_name == "openai":
        key = (api_key_override or "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing OpenAI API key. Set OPENAI_API_KEY.")
        return OpenAICompatibleProvider(api_key=key)

    if provider_name == "gemini":
        key = (api_key_override or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "Missing Gemini API key. Set GEMINI_API_KEY."
            )
        return OpenAICompatibleProvider(api_key=key, base_url=_GEMINI_BASE_URL, supports_stream_options=False)

    raise ValueError(f"Unknown provider {provider_name!r} for model {model_id!r}")
