"""Base agent class for conversational orchestrators.

During phases 1-3, this is a thin container. Phase 4 moves the actual
loop logic (streaming, tool dispatch, etc.) into Agent.run_turn().
"""

from __future__ import annotations

from typing import Any, Callable

try:
    from tool_registry import ToolRegistry
except ImportError:  # pragma: no cover - supports package-style imports.
    from .tool_registry import ToolRegistry


class Agent:
    """Base conversational agent.

    Encapsulates system_prompt, tool_registry, model, and max_rounds.
    Provides a uniform interface for tool definitions and dispatch.

    Until phase 4, the actual loop logic (streaming, compression, etc.)
    lives in standalone functions (run_turn, run_sales_turn).
    Phase 4 lifts the loop into Agent.run_turn().
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        system_prompt: str = "",
        model: str = "claude-haiku-4-5",
        max_rounds: int = 32,
    ) -> None:
        """Initialize an agent.

        Args:
            registry: ToolRegistry instance containing all available tools.
            system_prompt: System prompt for the agent.
            model: LLM model ID (e.g., "claude-opus-4-7").
            max_rounds: Max number of tool-use rounds before exiting.
        """
        self.registry = registry
        self.system_prompt = system_prompt
        self.model = model
        self.max_rounds = max_rounds

    def tool_definitions(self, names: list[str] | None = None) -> list[dict]:
        """Get Anthropic-style tool definitions, optionally filtered by name.

        Args:
            names: If provided, only return definitions for these tool names.

        Returns:
            List of tool definition dicts.
        """
        return self.registry.definitions(names)

    def dispatch_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        **context_kwargs: Any,
    ) -> dict[str, Any] | str:
        """Dispatch a tool call to its handler.

        Args:
            name: Tool name.
            tool_input: Unpacked input dict from the LLM.
            **context_kwargs: Context params (token, conversation_id, etc.).
                Only those in the tool's context_params are passed to the handler.

        Returns:
            The tool handler's return value.
        """
        return self.registry.dispatch(name, tool_input, **context_kwargs)

    async def run_turn(
        self,
        messages: list[dict],
        *,
        on_event: Callable | None = None,
        **context_kwargs: Any,
    ) -> str:
        """Run one conversation turn.

        This method is implemented in phase 4. Until then, it raises NotImplementedError.
        The loop logic currently lives in run_sales_turn and run_turn (meter).

        Args:
            messages: List of messages (modified in-place to append assistant/tool messages).
            on_event: Optional callable to emit SSE-style events.
            **context_kwargs: Context params passed to tool dispatch (token, conversation_id, etc.).

        Returns:
            The assistant's final text response.

        Raises:
            NotImplementedError: Until phase 4 when the loop is lifted.
        """
        raise NotImplementedError(
            "Agent.run_turn() is not implemented until phase 4. "
            "Until then, use the standalone run_turn() or run_sales_turn() functions."
        )
