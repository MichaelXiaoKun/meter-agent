"""Public pre-login sales agent for bluebot flow-meter qualification."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ORCHESTRATOR_DIR.parent
for _path in (_ORCHESTRATOR_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from llm import get_provider
from llm.registry import MODEL_CATALOG

from sales_chat.tools import SALES_TOOL_NAMES, TOOL_DEFINITIONS, dispatch_sales_tool, _SALES_REGISTRY
from sales_chat.verifier import (
    active_sales_verifier_model,
    classify_sales_validation,
    rough_validate_sales_response,
    same_provider_api_key_override,
    sales_response_general_validation_mode,
    sales_response_verification_attempts,
    sales_response_verification_enabled,
    verify_sales_response,
)
from shared.base_agent import Agent
from shared.message_sanitize import messages_for_anthropic_api

_PROMPT_PATH = _ORCHESTRATOR_DIR / "prompts" / "sales_system_v1.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
_DEFAULT_SALES_MODEL = "claude-haiku-4-5"
_GENERAL_OPENING_RE = re.compile(
    r"\b("
    r"hi|hello|hey|howdy|good morning|good afternoon|good evening|"
    r"how are you|what's up|whats up|sup|can you help|could you help|"
    r"what can you do|help me|thanks|thank you|ok|okay|got it"
    r")\b",
    re.IGNORECASE,
)
_SALES_DETAIL_RE = re.compile(
    r"\b("
    r"bluebot|pipe|pipes|meter|meters|product|products|fit|fits|compatible|"
    r"compatibility|install|installation|price|pricing|quote|support|flow|"
    r"water|irrigation|monitor|monitoring|wifi|wi-fi|cellular|battery|"
    r"prolink|prime|flagship|mini"
    r")\b",
    re.IGNORECASE,
)
_GENERAL_OPENING_HOW_ARE_YOU_RE = re.compile(
    r"\b(how are you|how's it going|hows it going|how are things)\b",
    re.IGNORECASE,
)
_GENERAL_OPENING_THANKS_RE = re.compile(
    r"\b(thanks|thank you)\b",
    re.IGNORECASE,
)
_GENERAL_OPENING_ACK_RE = re.compile(
    r"\b(ok|okay|got it|understood)\b",
    re.IGNORECASE,
)
_GENERAL_OPENING_HELP_RE = re.compile(
    r"\b(can you help|could you help|what can you do|help me)\b",
    re.IGNORECASE,
)


def _active_sales_model() -> str:
    model = (
        os.environ.get("SALES_AGENT_MODEL")
        or os.environ.get("ORCHESTRATOR_MODEL")
        or _DEFAULT_SALES_MODEL
    ).strip()
    return model if model in MODEL_CATALOG else _DEFAULT_SALES_MODEL


def _max_sales_rounds() -> int:
    raw = (os.environ.get("SALES_AGENT_MAX_TOOL_ROUNDS") or "").strip()
    if not raw:
        return 8
    try:
        return max(2, min(int(raw), 16))
    except ValueError:
        return 8


def _api_key_override(value: str | None) -> str | None:
    key = (value or "").strip()
    return key or None


def _event(emit, payload: dict[str, Any]) -> None:
    if emit:
        emit(payload)


def _last_plain_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


def _is_general_opening_turn(messages: list[dict]) -> bool:
    text = _last_plain_user_text(messages)
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) > 80:
        return False
    return bool(_GENERAL_OPENING_RE.search(compact)) and not _SALES_DETAIL_RE.search(compact)


def _safe_general_opening_reply(messages: list[dict]) -> str:
    compact = re.sub(r"\s+", " ", _last_plain_user_text(messages)).strip().lower()
    if _GENERAL_OPENING_HOW_ARE_YOU_RE.search(compact):
        return (
            "I'm doing well, thanks for asking. "
            "I can help narrow down Bluebot product fit once I understand the setup. "
            "What application or pipe context are you looking at?"
        )
    if _GENERAL_OPENING_THANKS_RE.search(compact):
        return (
            "You're welcome, I can help keep narrowing Bluebot product fit once I understand "
            "the setup. What application are you looking at, "
            "and do you know the pipe size?"
        )
    if _GENERAL_OPENING_ACK_RE.search(compact):
        return (
            "Sounds good, I can help keep narrowing Bluebot product fit once I understand "
            "the setup. What application are you looking at, "
            "and do you know the pipe size?"
        )
    if _GENERAL_OPENING_HELP_RE.search(compact):
        return (
            "Sure, I can help narrow down Bluebot product fit from the application and pipe context. "
            "What setup are you looking at?"
        )
    return (
        "Hi! I can help narrow down Bluebot product fit once I understand the setup. "
        "What application or pipe context are you looking at?"
    )


def _claim_free_general_opening_text(
    final_text: str,
    messages: list[dict],
    *,
    decision_mode: str,
    general_validation_mode: str,
) -> str:
    if general_validation_mode == "strong":
        return final_text
    if decision_mode != "strong" or not _is_general_opening_turn(messages):
        return final_text
    return _safe_general_opening_reply(messages)


def run_sales_turn(
    messages: list[dict],
    *,
    conversation_id: str,
    on_event=None,
    llm_api_key: str | None = None,
) -> str:
    """Process one public sales turn and append assistant/tool messages in place."""
    active_model = _active_sales_model()
    provider = get_provider(active_model, api_key_override=_api_key_override(llm_api_key))
    verification_enabled = sales_response_verification_enabled()
    general_validation_mode = sales_response_general_validation_mode()
    verifier_model = active_sales_verifier_model(active_model) if verification_enabled else None
    max_rounds = _max_sales_rounds()

    for round_ix in range(1, max_rounds + 1):
        api_messages = messages_for_anthropic_api(messages)
        try:
            token_count = provider.count_tokens(
                active_model,
                api_messages,
                system=_SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
            )
            context_window = int(
                MODEL_CATALOG.get(active_model, {}).get("context_window", 200_000)
            )
            _event(
                on_event,
                {
                    "type": "token_usage",
                    "tokens": token_count,
                    "pct": token_count / context_window,
                    "model": active_model,
                    "draft_model": active_model,
                    "validator_model": verifier_model,
                    "verification_enabled": verification_enabled,
                    "general_validation_mode": general_validation_mode,
                },
            )
        except Exception:
            # Token counting is diagnostic only for the public sales surface.
            pass

        _event(on_event, {"type": "thinking"})
        response = provider.complete(
            active_model,
            api_messages,
            system=_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            max_tokens=2048,
        )

        if response.stop_reason == "end_turn":
            final_text = response.text or "(No response)"
            if verification_enabled and verifier_model:
                validation_messages = messages_for_anthropic_api(messages)
                decision = classify_sales_validation(
                    final_text,
                    validation_messages,
                    configured_mode=general_validation_mode,
                )
                final_text = _claim_free_general_opening_text(
                    final_text,
                    messages,
                    decision_mode=decision.mode,
                    general_validation_mode=general_validation_mode,
                )
                if final_text != response.text:
                    decision = classify_sales_validation(
                        final_text,
                        validation_messages,
                        configured_mode=general_validation_mode,
                    )
                if decision.mode == "strong":
                    verifier_provider = get_provider(
                        verifier_model,
                        api_key_override=same_provider_api_key_override(
                            verifier_model=verifier_model,
                            draft_model=active_model,
                            api_key_override=llm_api_key,
                        ),
                    )
                    verified = verify_sales_response(
                        final_text,
                        validation_messages,
                        verifier_provider=verifier_provider,
                        verifier_model=verifier_model,
                        draft_model=active_model,
                        validation_mode="strong",
                        escalated=decision.escalated,
                        max_attempts=sales_response_verification_attempts(),
                        on_event=on_event,
                    )
                else:
                    verified = rough_validate_sales_response(
                        final_text,
                        validation_messages,
                        decision=decision,
                        draft_model=active_model,
                        validator_model=(
                            "rough-deterministic"
                            if decision.mode == "rough"
                            else "skipped"
                        ),
                        on_event=on_event,
                    )
                    if not verified.passed:
                        verifier_provider = get_provider(
                            verifier_model,
                            api_key_override=same_provider_api_key_override(
                                verifier_model=verifier_model,
                                draft_model=active_model,
                                api_key_override=llm_api_key,
                            ),
                        )
                        verified = verify_sales_response(
                            final_text,
                            validation_messages,
                            verifier_provider=verifier_provider,
                            verifier_model=verifier_model,
                            draft_model=active_model,
                            validation_mode="strong",
                            escalated=True,
                            max_attempts=sales_response_verification_attempts(),
                            on_event=on_event,
                        )
                final_text = verified.answer
            _event(on_event, {"type": "text_delta", "text": final_text})
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": final_text}]}
            )
            return final_text

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.assistant_content})
        tool_results: list[dict[str, Any]] = []
        for call in response.tool_calls:
            inp = dict(call.input or {})
            _event(on_event, {"type": "tool_call", "tool": call.name, "input": inp})
            if call.name not in SALES_TOOL_NAMES:
                result = {
                    "success": False,
                    "error": f"Tool {call.name!r} is not available in public sales chat.",
                }
            else:
                result = dispatch_sales_tool(
                    call.name,
                    inp,
                    conversation_id=conversation_id,
                )
            success = bool(result.get("success"))
            ev: dict[str, Any] = {
                "type": "tool_result",
                "tool": call.name,
                "success": success,
            }
            if "lead_summary" in result:
                ev["lead_summary"] = result["lead_summary"]
                ev["completion_score"] = result.get("completion_score")
                _event(
                    on_event,
                    {
                        "type": "lead_summary",
                        "lead_summary": result["lead_summary"],
                        "completion_score": result.get("completion_score"),
                        "missing_fields": result.get("missing_fields") or [],
                    },
                )
            _event(on_event, ev)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    msg = (
        "I hit the public sales chat step limit while trying to qualify this. "
        "Could you restate the main application and pipe details in one message?"
    )
    messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
    _event(on_event, {"type": "tool_round_limit", "limit": max_rounds})
    return msg


# ---- Agent instance ----

_sales_agent = Agent(
    _SALES_REGISTRY,
    system_prompt=_SYSTEM_PROMPT,
    model=_DEFAULT_SALES_MODEL,
    max_rounds=_max_sales_rounds(),
)


__all__ = [
    "SALES_TOOL_NAMES",
    "TOOL_DEFINITIONS",
    "run_sales_turn",
]
