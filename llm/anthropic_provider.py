"""Anthropic provider — wraps the official Anthropic SDK."""

from __future__ import annotations

import json
from typing import Callable

import anthropic
import httpx

from .base import LLMProvider, LLMResponse, LLMRateLimitError, ToolCall

_TURN_ACTIVITY_TYPE = "turn_activity"


def _strip_turn_activity(messages: list[dict]) -> list[dict]:
    """Remove UI-only turn_activity blocks before sending to the API."""
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, list):
            cleaned = [
                b for b in content
                if not (isinstance(b, dict) and b.get("type") == _TURN_ACTIVITY_TYPE)
            ]
            out.append({**m, "content": cleaned})
        else:
            out.append(m)
    return out


def _normalize(response) -> LLMResponse:
    text = ""
    tool_calls: list[ToolCall] = []
    assistant_content: list[dict] = []
    stop = getattr(response, "stop_reason", "end_turn") or "end_turn"

    for block in response.content:
        btype = (
            block.get("type")
            if isinstance(block, dict)
            else getattr(block, "type", None)
        )
        if btype == "text":
            t = (
                block.get("text")
                if isinstance(block, dict)
                else getattr(block, "text", "")
            ) or ""
            text = t
            assistant_content.append({"type": "text", "text": t})
        elif btype == "tool_use":
            bid = (
                block.get("id") if isinstance(block, dict) else getattr(block, "id", "")
            ) or ""
            bname = (
                block.get("name") if isinstance(block, dict) else getattr(block, "name", "")
            ) or ""
            binput = (
                block.get("input") if isinstance(block, dict) else getattr(block, "input", {})
            ) or {}
            binput = dict(binput)
            tool_calls.append(ToolCall(id=bid, name=bname, input=binput))
            assistant_content.append(
                {"type": "tool_use", "id": bid, "name": bname, "input": binput}
            )

    stop_reason = "tool_use" if stop == "tool_use" else "end_turn"
    usage = getattr(response, "usage", None)
    return LLMResponse(
        text=text,
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        assistant_content=assistant_content,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, timeout: httpx.Timeout | None = None):
        kwargs: dict = {"api_key": api_key}
        if timeout is not None:
            kwargs["timeout"] = timeout
        self.client = anthropic.Anthropic(**kwargs)

    def complete(self, model, messages, *, system, tools, max_tokens) -> LLMResponse:
        safe = _strip_turn_activity(messages)
        try:
            response = self.client.messages.create(
                model=model,
                messages=safe,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
            )
        except anthropic.RateLimitError as exc:
            retry_after = _parse_retry_after(exc)
            raise LLMRateLimitError(str(exc), retry_after=retry_after) from exc
        return _normalize(response)

    def stream(self, model, messages, *, system, tools, max_tokens, on_text_delta) -> LLMResponse:
        safe = _strip_turn_activity(messages)
        try:
            with self.client.messages.stream(
                model=model,
                messages=safe,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
            ) as s:
                for delta in s.text_stream:
                    on_text_delta(delta)
                response = s.get_final_message()
        except anthropic.RateLimitError as exc:
            retry_after = _parse_retry_after(exc)
            raise LLMRateLimitError(str(exc), retry_after=retry_after) from exc
        return _normalize(response)

    def count_tokens(self, model, messages, *, system, tools) -> int:
        safe = _strip_turn_activity(messages)
        try:
            resp = self.client.messages.count_tokens(
                model=model,
                messages=safe,
                system=system,
                tools=tools,
            )
            return resp.input_tokens
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            return _rough_token_estimate(system, tools, safe)


def _parse_retry_after(exc: anthropic.RateLimitError) -> float | None:
    try:
        raw = exc.response.headers.get("retry-after") if exc.response else None
        if raw is not None:
            v = float(raw)
            return v if v > 0 else None
    except (TypeError, ValueError):
        pass
    return None


def _rough_token_estimate(system: str, tools: list[dict], messages: list[dict]) -> int:
    char_n = len(system) + sum(len(json.dumps(t)) for t in tools)
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            char_n += len(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    char_n += len(str(b.get("text") or b.get("content") or ""))
    return min(max(char_n // 4 + 12_000, 1), 200_000)
