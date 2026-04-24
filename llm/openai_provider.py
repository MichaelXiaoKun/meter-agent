"""
OpenAI-compatible provider.

Works for:
  - OpenAI (api.openai.com)           — OPENAI_API_KEY
  - Google Gemini (OpenAI-compat API) — GEMINI_API_KEY + GEMINI_BASE_URL

Canonical (Anthropic-format) messages are translated to OpenAI wire format
on each call; the response is normalized back to LLMResponse.
"""

from __future__ import annotations

import json
from typing import Callable

from .base import LLMProvider, LLMResponse, LLMRateLimitError, ToolCall

_TURN_ACTIVITY_TYPE = "turn_activity"


# ── Format translation ──────────────────────────────────────────────────────


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """
    Convert canonical (Anthropic) format → OpenAI chat messages.

    Key translation rules:
    - tool_result blocks  →  separate {"role": "tool", ...} messages
    - tool_use blocks     →  tool_calls on the assistant message
    - turn_activity       →  dropped
    """
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            elif isinstance(content, list):
                tool_results: list[dict] = []
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        tool_results.append(block)
                    elif btype == "text":
                        text_parts.append(block.get("text", ""))
                    # turn_activity and others are silently dropped
                for tr in tool_results:
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr.get("content", ""),
                    })
                if text_parts:
                    out.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                out.append({"role": "user", "content": str(content or "")})

        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text = ""
                tool_calls: list[dict] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text", "")
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                    # turn_activity dropped
                msg_out: dict = {"role": "assistant", "content": text or None}
                if tool_calls:
                    msg_out["tool_calls"] = tool_calls
                out.append(msg_out)
            else:
                out.append({"role": "assistant", "content": str(content or "")})

    return out


def _normalize_completion(response) -> LLMResponse:
    choice = response.choices[0]
    msg = choice.message
    text = msg.content or ""
    tool_calls: list[ToolCall] = []
    assistant_content: list[dict] = []
    if text:
        assistant_content.append({"type": "text", "text": text})
    for tc in msg.tool_calls or []:
        try:
            inp = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            inp = {}
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))
        assistant_content.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.function.name,
            "input": inp,
        })
    finish = choice.finish_reason
    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
    usage = response.usage
    return LLMResponse(
        text=text,
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        assistant_content=assistant_content,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


# ── Provider ────────────────────────────────────────────────────────────────


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str | None = None, supports_stream_options: bool = True):
        import openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._supports_stream_options = supports_stream_options

    def complete(self, model, messages, *, system, tools, max_tokens) -> LLMResponse:
        import openai
        oai_messages = [{"role": "system", "content": system}] + _messages_to_openai(messages)
        oai_tools = _tools_to_openai(tools) if tools else None
        kwargs: dict = dict(model=model, messages=oai_messages, max_tokens=max_tokens)
        if oai_tools:
            kwargs["tools"] = oai_tools
        try:
            response = self.client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), retry_after=_oai_retry_after(exc)) from exc
        return _normalize_completion(response)

    def stream(self, model, messages, *, system, tools, max_tokens, on_text_delta) -> LLMResponse:
        import openai
        oai_messages = [{"role": "system", "content": system}] + _messages_to_openai(messages)
        oai_tools = _tools_to_openai(tools) if tools else None
        kwargs: dict = dict(
            model=model,
            messages=oai_messages,
            max_tokens=max_tokens,
            stream=True,
        )
        if self._supports_stream_options:
            kwargs["stream_options"] = {"include_usage": True}
        if oai_tools:
            kwargs["tools"] = oai_tools

        text_parts: list[str] = []
        # Keyed by tool-call id (preferred) so parallel calls stay separate even
        # when a provider (e.g. Gemini) sends all chunks with index=0 or index=None.
        # Falls back to tc.index when no id has arrived yet for that slot.
        tc_by_id: dict[str, dict] = {}   # id  → entry
        tc_by_idx: dict[int, dict] = {}  # idx → entry (fallback)
        tc_order: list[dict] = []        # insertion order for final assembly
        finish_reason = None
        usage = None

        def _get_or_create_tc_entry(tc) -> dict:
            # Prefer id-based lookup so parallel calls with bad indices stay separate.
            if tc.id and tc.id in tc_by_id:
                return tc_by_id[tc.id]
            idx = tc.index if tc.index is not None else -1
            if tc.id:
                # First chunk for a new tool call that has an id.
                entry = {"id": tc.id, "name": "", "arguments": ""}
                tc_by_id[tc.id] = entry
                tc_by_idx[idx] = entry
                tc_order.append(entry)
                return entry
            # No id yet — fall back to index bucket.
            if idx not in tc_by_idx:
                entry = {"id": "", "name": "", "arguments": ""}
                tc_by_idx[idx] = entry
                tc_order.append(entry)
            return tc_by_idx[idx]

        try:
            for chunk in self.client.chat.completions.create(**kwargs):
                if chunk.usage:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta.content:
                    text_parts.append(delta.content)
                    on_text_delta(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        entry = _get_or_create_tc_entry(tc)
                        if tc.id and not entry["id"]:
                            entry["id"] = tc.id
                            tc_by_id[tc.id] = entry
                        if tc.function:
                            if tc.function.name:
                                # Name is always complete in the first chunk — assign, don't append.
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc), retry_after=_oai_retry_after(exc)) from exc

        text = "".join(text_parts)
        tool_calls: list[ToolCall] = []
        assistant_content: list[dict] = []
        if text:
            assistant_content.append({"type": "text", "text": text})
        for entry in tc_order:
            try:
                inp = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                inp = {}
            tool_calls.append(ToolCall(id=entry["id"], name=entry["name"], input=inp))
            assistant_content.append({
                "type": "tool_use",
                "id": entry["id"],
                "name": entry["name"],
                "input": inp,
            })

        stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
        return LLMResponse(
            text=text,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            assistant_content=assistant_content,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def count_tokens(self, model, messages, *, system, tools) -> int:
        # OpenAI has no public count_tokens endpoint; estimate from character count.
        char_n = len(system)
        for t in tools:
            char_n += len(json.dumps(t))
        for m in _messages_to_openai(messages):
            c = m.get("content")
            if isinstance(c, str):
                char_n += len(c)
            elif c:
                char_n += len(str(c))
        return min(max(char_n // 4 + 2_000, 1), 1_000_000)


def _oai_retry_after(exc) -> float | None:
    try:
        raw = exc.response.headers.get("retry-after") if exc.response else None
        if raw is not None:
            v = float(raw)
            return v if v > 0 else None
    except (TypeError, ValueError, AttributeError):
        pass
    return None
