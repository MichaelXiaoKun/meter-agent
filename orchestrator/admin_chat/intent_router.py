"""Intent-routing helpers for the admin chat turn loop."""

from __future__ import annotations

from admin_chat.turn_loop import (
    _INTENT_LABELS,
    _TOOL_NAMES_BY_INTENT,
    _extract_first_serial,
    _intent_router_mode,
    _last_user_text,
    _looks_like_angle_diagnostic_request,
    _parse_haiku_intent_json,
    _pipe_correctness_asserted,
    _plain_text_from_user_message,
    _recent_user_text_for_routing,
    _resolve_routed_tools,
    _route_intent_haiku,
    _route_intent_rules,
    _tools_for_intent_label,
)

__all__ = [
    "_INTENT_LABELS",
    "_TOOL_NAMES_BY_INTENT",
    "_extract_first_serial",
    "_intent_router_mode",
    "_last_user_text",
    "_looks_like_angle_diagnostic_request",
    "_parse_haiku_intent_json",
    "_pipe_correctness_asserted",
    "_plain_text_from_user_message",
    "_recent_user_text_for_routing",
    "_resolve_routed_tools",
    "_route_intent_haiku",
    "_route_intent_rules",
    "_tools_for_intent_label",
]
