"""ToolRegistry for registering, describing, and dispatching agent tools.

All agent capabilities are registered and dispatched through ToolRegistry.
The registry is decoupled from the OpenAI SDK — the loop extracts name and
arguments from the SDK response before calling dispatch.
Tool schemas conform to the OpenAI Chat Completion tool specification:
    {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Coroutine
from typing import Any

from openai.types.chat import ChatCompletionToolParam


class ToolRegistry:
    """ToolRegistry manages tool registration, schema retrieval, and tool execution."""

    def __init__(self) -> None:
        """Initialize an empty ToolRegistry."""
        self._tools: dict[str, Callable[..., str] | Callable[..., Coroutine[Any, Any, str]]] = {}
        self._schemas: dict[str, ChatCompletionToolParam] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., str] | Callable[..., Coroutine[Any, Any, str]],
        schema: ChatCompletionToolParam,
    ) -> None:
        """Register a tool with its implementation function and OpenAI tool schema.

        name: Unique identifier for the tool.
        fn: Callable executing the tool (sync or async). Must accept keyword arguments matching schema and return a string.
        schema: OpenAI ChatCompletionToolParam definition dict.
        """
        self._tools[name] = fn
        self._schemas[name] = schema

    def get_schemas(self) -> list[ChatCompletionToolParam]:
        """Return a list of all registered tool schemas formatted according to OpenAI spec."""
        return list(self._schemas.values())

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call with the provided arguments.

        Catches execution exceptions and returns an error string instead of raising.
        Supports both sync and async tool functions transparently.

        tool_name: The name of the tool to invoke.
        arguments: Keyword arguments dict parsed from the LLM tool call.

        Returns:
            The string observation output or an error string starting with 'Error: '.
        """
        if tool_name not in self._tools:
            return f"Error: unknown tool '{tool_name}'"

        fn = self._tools[tool_name]
        try:
            # Check if the function is async
            if inspect.iscoroutinefunction(fn):
                result = await fn(**arguments)
            else:
                # Sync function - call directly (brief blocking acceptable for built-in tools)
                result = fn(**arguments)
            return str(result)
        except Exception as err:
            return f"Error: {err}"


def make_scoped_registry(
    parent: ToolRegistry,
    allowlist: list[str] | set[str],
) -> ToolRegistry:
    """Return a new ToolRegistry containing only the tools named in *allowlist*.

    Tools not present in the parent are silently skipped.  Used to build the
    restricted registry passed to a sub-agent.

    parent: Source registry to copy tools from.
    allowlist: Names of tools the sub-agent is permitted to call.
               Accepts either a list or a set (PermissionPolicy stores a set).

    Returns:
        New ToolRegistry with only the requested tools registered.
    """
    scoped = ToolRegistry()
    for name in allowlist:
        if name in parent._tools:
            scoped.register(name=name, fn=parent._tools[name], schema=parent._schemas[name])
    return scoped


def make_default_tool_registry(
    workspace: str | None = None,
    loaded_skills: set[str] | None = None,
    memory_store = None,
    vector_store = None,
    session_id: str | None = None,
) -> ToolRegistry:
    """Create and return a ToolRegistry populated with all default tools.

    Registers all 10 sandbox filesystem operations, the execute_command tool,
    the load_skill tool, and memory tools (remember, search_memory).

    workspace: Optional workspace root path. When provided it is passed to
               get_filesystem_tools and used for skill discovery.
    loaded_skills: Set to track loaded skills (for load_skill tool).
                   If None, creates a new set.
    memory_store: Optional MemoryStore instance for remember/search_memory tools.
    vector_store: Optional VectorStore instance for semantic memory search.
    session_id: Optional session ID for memory tools.
    """
    from agent.skills.tool import load_skill_tool, make_load_skill_schema
    from agent.tools.execute import get_execute_tools
    from agent.tools.filesystem import get_filesystem_tools

    registry = ToolRegistry()

    # Register filesystem tools
    for tool in get_filesystem_tools(workspace=workspace):
        registry.register(name=tool["name"], fn=tool["fn"], schema=tool["schema"])

    # Register command execution tool (screened: no deletion, no host control)
    for exec_tool in get_execute_tools():
        registry.register(name=exec_tool["name"], fn=exec_tool["fn"], schema=exec_tool["schema"])

    # Register load_skill tool
    if loaded_skills is None:
        loaded_skills = set()
    
    def load_skill_fn(name: str) -> str:
        """Load a skill by name."""
        return load_skill_tool(name, loaded_skills, workspace)
    
    registry.register(
        name="load_skill",
        fn=load_skill_fn,
        schema=make_load_skill_schema(workspace),
    )
    
    # Register memory tools if store is provided
    if memory_store is not None and session_id is not None:
        from agent.memory.tools import make_remember_tool, make_search_memory_tool
        
        remember_tool = make_remember_tool(memory_store, session_id, vector_store)
        registry.register(
            name=remember_tool["name"],
            fn=remember_tool["fn"],
            schema=remember_tool["schema"],
        )
        
        search_tool = make_search_memory_tool(memory_store, vector_store)
        registry.register(
            name=search_tool["name"],
            fn=search_tool["fn"],
            schema=search_tool["schema"],
        )
    
    return registry
