"""Unit tests for agent.tools.ToolRegistry."""

from __future__ import annotations

import pytest
from openai.types.chat import ChatCompletionToolParam

from agent.tools import ToolRegistry, make_default_tool_registry


@pytest.mark.asyncio
async def test_registry_registers_and_dispatches_tool() -> None:
    """ToolRegistry dispatches registered functions with parsed arguments."""
    registry = ToolRegistry()

    def greet(name: str) -> str:
        return f"Hello, {name}!"

    schema: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "greet",
            "description": "Greets a person",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }

    registry.register(name="greet", fn=greet, schema=schema)

    assert registry.get_schemas() == [schema]
    result = await registry.dispatch("greet", {"name": "Alice"})
    assert result == "Hello, Alice!"


@pytest.mark.asyncio
async def test_registry_dispatch_unknown_tool_returns_error_string() -> None:
    """ToolRegistry returns an error string when an unknown tool is dispatched."""
    registry = ToolRegistry()
    result = await registry.dispatch("non_existent_tool", {})
    assert result.startswith("Error: unknown tool 'non_existent_tool'")


@pytest.mark.asyncio
async def test_registry_dispatch_handles_tool_exception() -> None:
    """ToolRegistry catches exceptions during execution and returns an error string."""
    registry = ToolRegistry()

    def failing_tool() -> str:
        raise ValueError("Something went wrong")

    schema: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "failing_tool",
            "description": "Fails",
            "parameters": {"type": "object"},
        },
    }

    registry.register(name="failing_tool", fn=failing_tool, schema=schema)
    result = await registry.dispatch("failing_tool", {})
    assert result == "Error: Something went wrong"


def test_make_default_tool_registry() -> None:
    """make_default_tool_registry creates a registry with all tools including load_skill."""
    registry = make_default_tool_registry()
    schemas = registry.get_schemas()
    assert len(schemas) == 12  # 10 filesystem tools + execute_command + load_skill
    names = [s["function"]["name"] for s in schemas]
    for expected in [
        "create_folder", "write_file", "read_file", "modify_file",
        "list_files", "search_text", "search_and_replace", "glob",
        "grep", "update_todo_list", "execute_command", "load_skill",
    ]:
        assert expected in names
