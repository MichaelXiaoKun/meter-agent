"""Tool registry for agent-agnostic tool registration and dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    """A single tool that an agent can invoke.

    Attributes:
        definition: Anthropic-style tool schema dict (name, description, input_schema).
        handler: Callable that executes the tool. Signature: (**tool_input, **context_params) -> dict | str.
        context_params: Names of context kwargs (token, conversation_id, etc.) this handler accepts.
            The registry filters context to only these params before calling the handler.
        is_write: Tool performs a mutation (meter config, angle, ticket creation).
        is_serial_only: Tool cannot be parallelized; runs in the tool loop serially.
        is_dedupable_read: Read-only tool whose results can be cached/deduplicated within a turn.
        is_heartbeat_progress: Tool emits progress events (long analysis, fleet triage).
    """

    definition: dict
    handler: Callable[..., Any]
    context_params: frozenset[str] = field(default_factory=frozenset)
    is_write: bool = False
    is_serial_only: bool = False
    is_dedupable_read: bool = False
    is_heartbeat_progress: bool = False

    @property
    def name(self) -> str:
        return self.definition["name"]


class ToolRegistry:
    """Registry for tools that an agent can invoke.

    Manages tool definitions, metadata, and dispatches tool calls to handlers.
    Filters context kwargs to prevent TypeError when handlers don't accept certain context.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool in the registry."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name, or None if not found."""
        return self._tools.get(name)

    def definitions(self, names: list[str] | None = None) -> list[dict]:
        """Get Anthropic-style tool definitions, optionally filtered by name list.

        Args:
            names: If provided, only return definitions for tools in this list.
                   If None, return all tool definitions.

        Returns:
            List of tool definition dicts.
        """
        if names is None:
            return [t.definition for t in self._tools.values()]
        wanted = set(names)
        return [t.definition for t in self._tools.values() if t.name in wanted]

    def dispatch(
        self,
        name: str,
        tool_input: dict[str, Any],
        **context_kwargs: Any,
    ) -> dict[str, Any] | str:
        """Call a tool handler by name.

        Args:
            name: Tool name.
            tool_input: The unpacked input dict from the LLM's tool_use block.
            **context_kwargs: Context params like token, conversation_id, client_timezone, etc.
                Only kwargs named in the tool's context_params are forwarded to the handler.

        Returns:
            The tool handler's return value (typically a dict, but may be a string).
            If the tool is not found, returns {"success": False, "error": "..."}.
        """
        tool = self._tools.get(name)
        if tool is None:
            return {"success": False, "error": f"Unknown tool: {name!r}"}

        ctx = {k: v for k, v in context_kwargs.items() if k in tool.context_params}
        try:
            return tool.handler(**tool_input, **ctx)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def names(self) -> frozenset[str]:
        """Return the set of all registered tool names."""
        return frozenset(self._tools)
