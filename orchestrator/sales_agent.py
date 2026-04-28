"""Public pre-login sales agent for bluebot flow-meter qualification."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from llm import get_provider
from llm.registry import MODEL_CATALOG

from sales_tools import SALES_TOOL_NAMES, TOOL_DEFINITIONS, dispatch_sales_tool

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "sales_system_v1.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
_DEFAULT_SALES_MODEL = "claude-haiku-4-5"


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
    max_rounds = _max_sales_rounds()

    for round_ix in range(1, max_rounds + 1):
        try:
            token_count = provider.count_tokens(
                active_model,
                messages,
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
                },
            )
        except Exception:
            # Token counting is diagnostic only for the public sales surface.
            pass

        _event(on_event, {"type": "thinking"})
        response = provider.stream(
            active_model,
            messages,
            system=_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            max_tokens=2048,
            on_text_delta=lambda delta: _event(on_event, {"type": "text_delta", "text": delta}),
        )

        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.assistant_content})
            return response.text or "(No response)"

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


__all__ = [
    "SALES_TOOL_NAMES",
    "TOOL_DEFINITIONS",
    "run_sales_turn",
]
