"""
Model catalog: maps model IDs → provider + metadata.

Add entries here to expose new models in the orchestrator's picker and
to let sub-agents resolve provider/key from a model env var.
"""

MODEL_CATALOG: dict[str, dict] = {
    # ── Anthropic Claude ──────────────────────────────────────────────────────
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "label": "Haiku 4.5",
        "tier": "fast",
        "description": "Fast + cheap; great default for routine analysis.",
        "tpm_input_guide_tokens": 50_000,
        "context_window": 200_000,
    },
    "claude-sonnet-4-5": {
        "provider": "anthropic",
        "label": "Sonnet 4.5",
        "tier": "balanced",
        "description": "Balanced quality / cost; better multi-step reasoning.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 200_000,
    },
    "claude-sonnet-4-6": {
        "provider": "anthropic",
        "label": "Sonnet 4.6",
        "tier": "balanced",
        "description": "Latest Sonnet; strong reasoning and tool use.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 200_000,
    },
    "claude-opus-4-5": {
        "provider": "anthropic",
        "label": "Opus 4.5",
        "tier": "max",
        "description": "Highest quality Claude; slowest + most expensive.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 200_000,
    },
    # ── OpenAI ────────────────────────────────────────────────────────────────
    "gpt-4o": {
        "provider": "openai",
        "label": "GPT-4o",
        "tier": "balanced",
        "description": "OpenAI flagship; strong reasoning and tool use.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 128_000,
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "label": "GPT-4o Mini",
        "tier": "fast",
        "description": "Fast, cost-effective GPT-4o variant.",
        "tpm_input_guide_tokens": 200_000,
        "context_window": 128_000,
    },
    "o3-mini": {
        "provider": "openai",
        "label": "o3-mini",
        "tier": "reasoning",
        "description": "OpenAI reasoning model; excellent for complex multi-step tasks.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 200_000,
    },
    # ── Google Gemini (via OpenAI-compatible endpoint) ────────────────────────
    "gemini-2.5-pro": {
        "provider": "gemini",
        "label": "Gemini 2.5 Pro",
        "tier": "max",
        "description": "Google's most capable model with very long context.",
        "tpm_input_guide_tokens": 30_000,
        "context_window": 1_000_000,
    },
    "gemini-2.5-flash": {
        "provider": "gemini",
        "label": "Gemini 2.5 Flash",
        "tier": "balanced",
        "description": "Balanced Gemini model with strong tool use.",
        "tpm_input_guide_tokens": 50_000,
        "context_window": 1_000_000,
    },
    "gemini-2.0-flash": {
        "provider": "gemini",
        "label": "Gemini 2.0 Flash",
        "tier": "fast",
        "description": "Fast and cost-effective Gemini model.",
        "tpm_input_guide_tokens": 100_000,
        "context_window": 1_000_000,
    },
}

# Cheapest model per provider — used for compression, summarization, intent routing
CHEAP_MODEL_BY_PROVIDER: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}


def get_provider_name(model_id: str) -> str:
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        raise ValueError(
            f"Unknown model {model_id!r}. Add it to meter_agent/llm/registry.py."
        )
    return entry["provider"]


def get_cheap_model(model_id: str) -> str:
    """Return the cheapest model for the same provider as *model_id*."""
    provider = get_provider_name(model_id)
    return CHEAP_MODEL_BY_PROVIDER.get(provider, model_id)
