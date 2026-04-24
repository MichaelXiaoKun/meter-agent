"""
Shared LLM provider abstractions.

All agents (orchestrator + sub-agents) use these types to stay provider-agnostic.
Canonical message format is Anthropic-style dicts:

  User text:   {"role": "user", "content": "..."}
  Tool result: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}
  Assistant:   {"role": "assistant", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]}

Providers translate this canonical format to their native wire format on each call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    stop_reason: str  # "end_turn" | "tool_use"
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Canonical content blocks to append as the assistant turn in history
    assistant_content: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMRateLimitError(Exception):
    """Raised by providers when the upstream API returns a rate-limit response."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        model: str,
        messages: list[dict],
        *,
        system: str,
        tools: list[dict],
        max_tokens: int,
        on_text_delta: Callable[[str], None],
    ) -> LLMResponse: ...

    @abstractmethod
    def count_tokens(
        self,
        model: str,
        messages: list[dict],
        *,
        system: str,
        tools: list[dict],
    ) -> int: ...
