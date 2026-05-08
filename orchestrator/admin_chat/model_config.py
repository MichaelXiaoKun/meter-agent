"""Model and rate-limit configuration helpers for admin chat."""

from __future__ import annotations

from admin_chat.turn_loop import (
    _MODEL,
    _configured_allowed_models,
    _count_tokens,
    _default_tpm_input_guide_for_active_model,
    _estimate_stream_turn_tpm_cost,
    _resolve_api_key_override,
    _resolve_max_input_tokens_target,
    _resolve_tpm_input_guide_tokens,
    get_rate_limit_config_for_api,
    list_available_models,
    resolve_orchestrator_model,
)

__all__ = [
    "_MODEL",
    "_configured_allowed_models",
    "_count_tokens",
    "_default_tpm_input_guide_for_active_model",
    "_estimate_stream_turn_tpm_cost",
    "_resolve_api_key_override",
    "_resolve_max_input_tokens_target",
    "_resolve_tpm_input_guide_tokens",
    "get_rate_limit_config_for_api",
    "list_available_models",
    "resolve_orchestrator_model",
]
