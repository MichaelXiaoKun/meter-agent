"""Unit tests for tool_registry: Tool and ToolRegistry."""

import pytest

from orchestrator.tool_registry import Tool, ToolRegistry


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_dispatch_unknown_tool_returns_error(self) -> None:
        """Calling dispatch on an unknown tool returns an error dict."""
        registry = ToolRegistry()
        result = registry.dispatch("nonexistent_tool", {})
        assert result == {"success": False, "error": "Unknown tool: 'nonexistent_tool'"}

    def test_dispatch_known_tool_calls_handler(self) -> None:
        """Calling dispatch on a registered tool calls its handler."""
        registry = ToolRegistry()

        def handler(query: str) -> dict[str, str]:
            return {"success": True, "result": f"Searched for: {query}"}

        tool = Tool(
            definition={"name": "search", "description": "Search", "input_schema": {}},
            handler=handler,
        )
        registry.register(tool)

        result = registry.dispatch("search", {"query": "test"})
        assert result == {"success": True, "result": "Searched for: test"}

    def test_dispatch_filters_context_params(self) -> None:
        """Only context params named in tool.context_params are forwarded to the handler.

        This prevents TypeError when the LLM passes context like token/conversation_id
        that the handler doesn't accept.
        """
        registry = ToolRegistry()

        # Handler accepts only 'value', not 'token' or 'conversation_id'
        def handler(value: str) -> dict[str, str]:
            return {"success": True, "value": value}

        tool = Tool(
            definition={"name": "simple", "description": "Simple", "input_schema": {}},
            handler=handler,
            context_params=frozenset(),  # No context params accepted
        )
        registry.register(tool)

        # Even though we pass token and conversation_id, the handler receives neither
        result = registry.dispatch(
            "simple",
            {"value": "hello"},
            token="abc123",
            conversation_id="conv-456",
        )
        assert result == {"success": True, "value": "hello"}

    def test_dispatch_injects_requested_context_params(self) -> None:
        """Context params named in tool.context_params are injected into the handler."""
        registry = ToolRegistry()

        # Handler accepts 'value' + 'token'
        def handler(value: str, *, token: str | None = None) -> dict[str, str]:
            return {"success": True, "value": value, "token": token or "none"}

        tool = Tool(
            definition={"name": "with_token", "description": "With token", "input_schema": {}},
            handler=handler,
            context_params=frozenset({"token"}),
        )
        registry.register(tool)

        result = registry.dispatch(
            "with_token",
            {"value": "hello"},
            token="secret123",
            conversation_id="conv-456",  # This is NOT in context_params, so not passed
        )
        assert result == {"success": True, "value": "hello", "token": "secret123"}

    def test_dispatch_handles_handler_exception(self) -> None:
        """If the handler raises an exception, dispatch returns an error dict."""
        registry = ToolRegistry()

        def handler(x: int) -> dict:
            raise ValueError(f"Invalid x: {x}")

        tool = Tool(
            definition={"name": "failing", "description": "Fails", "input_schema": {}},
            handler=handler,
        )
        registry.register(tool)

        result = registry.dispatch("failing", {"x": 42})
        assert result == {"success": False, "error": "Invalid x: 42"}

    def test_tool_definitions_all_tools(self) -> None:
        """definitions() with no filter returns all tool definitions."""
        registry = ToolRegistry()
        registry.register(Tool(
            definition={"name": "tool1", "description": "First", "input_schema": {}},
            handler=lambda: {"success": True},
        ))
        registry.register(Tool(
            definition={"name": "tool2", "description": "Second", "input_schema": {}},
            handler=lambda: {"success": True},
        ))

        defs = registry.definitions()
        assert len(defs) == 2
        assert defs[0]["name"] == "tool1"
        assert defs[1]["name"] == "tool2"

    def test_tool_definitions_filtered_by_names(self) -> None:
        """definitions(names=[...]) returns only those tool definitions."""
        registry = ToolRegistry()
        registry.register(Tool(
            definition={"name": "tool1", "description": "First", "input_schema": {}},
            handler=lambda: {"success": True},
        ))
        registry.register(Tool(
            definition={"name": "tool2", "description": "Second", "input_schema": {}},
            handler=lambda: {"success": True},
        ))
        registry.register(Tool(
            definition={"name": "tool3", "description": "Third", "input_schema": {}},
            handler=lambda: {"success": True},
        ))

        defs = registry.definitions(names=["tool1", "tool3"])
        assert len(defs) == 2
        names = [d["name"] for d in defs]
        assert "tool1" in names
        assert "tool3" in names
        assert "tool2" not in names

    def test_registry_names(self) -> None:
        """names() returns a frozenset of all registered tool names."""
        registry = ToolRegistry()
        registry.register(Tool(
            definition={"name": "search", "description": "Search", "input_schema": {}},
            handler=lambda: {"success": True},
        ))
        registry.register(Tool(
            definition={"name": "create", "description": "Create", "input_schema": {}},
            handler=lambda: {"success": True},
        ))

        names = registry.names()
        assert names == frozenset({"search", "create"})


class TestTool:
    """Tests for Tool dataclass."""

    def test_tool_name_property(self) -> None:
        """Tool.name returns the definition's name field."""
        tool = Tool(
            definition={"name": "my_tool", "description": "Desc", "input_schema": {}},
            handler=lambda: {},
        )
        assert tool.name == "my_tool"

    def test_tool_defaults(self) -> None:
        """Tool has sensible defaults for metadata fields."""
        tool = Tool(
            definition={"name": "tool", "description": "", "input_schema": {}},
            handler=lambda: {},
        )
        assert tool.context_params == frozenset()
        assert tool.is_write is False
        assert tool.is_serial_only is False
        assert tool.is_dedupable_read is False
        assert tool.is_heartbeat_progress is False

    def test_tool_custom_metadata(self) -> None:
        """Tool respects custom metadata values."""
        tool = Tool(
            definition={"name": "tool", "description": "", "input_schema": {}},
            handler=lambda: {},
            context_params=frozenset({"token", "conversation_id"}),
            is_write=True,
            is_serial_only=True,
            is_dedupable_read=False,
            is_heartbeat_progress=True,
        )
        assert tool.context_params == frozenset({"token", "conversation_id"})
        assert tool.is_write is True
        assert tool.is_serial_only is True
        assert tool.is_dedupable_read is False
        assert tool.is_heartbeat_progress is True
