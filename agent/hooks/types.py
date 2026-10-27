"""Hook types and protocols for observability.

Defines the hook interface that can be implemented for monitoring agent
lifecycle events: LLM calls, tool dispatches, sub-agent executions, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class RunStartEvent:
    """Event fired when a top-level agent run (one user turn) begins.
    
    Attributes:
        session_id: Agent session ID
        run_id: Unique identifier for this run
        agent: Agent label (e.g. "main")
        timestamp: When the run started (UTC)
    """
    session_id: str
    run_id: str
    agent: str = "main"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RunEndEvent:
    """Event fired when a top-level agent run completes.
    
    Attributes:
        session_id: Agent session ID
        run_id: Unique identifier for this run
        agent: Agent label (e.g. "main")
        status: "success", "guard" (iteration/time limit hit) or "error"
        iterations: Number of ReAct loop iterations executed
        tool_calls: Number of tool calls made
        duration_ms: Run duration in milliseconds
        timestamp: When the run ended (UTC)
    """
    session_id: str
    run_id: str
    agent: str
    status: str
    iterations: int
    tool_calls: int
    duration_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SubAgentStartEvent:
    """Event fired when a sub-agent execution begins.
    
    Attributes:
        parent_session_id: Parent agent's session ID
        subagent_id: Unique identifier for this sub-agent run
        role_name: Sub-agent role (e.g. "file-scout", "scheduler")
        task: Task description from parent
        timestamp: When the sub-agent started (UTC)
    """
    parent_session_id: str
    subagent_id: str
    role_name: str
    task: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SubAgentEndEvent:
    """Event fired when a sub-agent execution completes.
    
    Attributes:
        parent_session_id: Parent agent's session ID
        subagent_id: Unique identifier for this sub-agent run
        role_name: Sub-agent role
        success: Whether the sub-agent completed successfully
        result: Result string returned to parent (or error message)
        iterations: Number of ReAct loop iterations executed
        tools_used: List of tool names called during execution
        duration_ms: Execution time in milliseconds
        timestamp: When the sub-agent ended (UTC)
    """
    parent_session_id: str
    subagent_id: str
    role_name: str
    success: bool
    result: str
    iterations: int
    tools_used: list[str]
    duration_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SubAgentProgressEvent:
    """Event fired repeatedly while a sub-agent runs.

    Start and end bracket the run; this reports the counters *during* it, so a
    live display can show work accumulating instead of freezing until the
    sub-agent returns. Emitted after every LLM response and before every batch
    of tool calls, which is often — a hook that does real work per event should
    debounce.

    Attributes:
        parent_session_id: Parent agent's session ID
        subagent_id: Unique identifier for this sub-agent run
        role_name: Sub-agent role
        tool_calls: Tool calls dispatched so far (calls, not distinct tools)
        total_tokens: Prompt + completion tokens consumed so far
        current_tool: Tool being dispatched right now, None between batches
        timestamp: When the counters were sampled (UTC)
    """
    parent_session_id: str
    subagent_id: str
    role_name: str
    tool_calls: int
    total_tokens: int
    current_tool: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ToolCallEvent:
    """Event fired when a tool is dispatched.
    
    Attributes:
        session_id: Current agent/sub-agent session ID
        tool_name: Name of the tool called
        arguments: Tool arguments (may be redacted for sensitive data)
        result: Tool result (success or error message)
        permission_outcome: Permission decision ("allow", "deny", "ask→yes", "ask→no", "none")
        duration_ms: Tool execution time in milliseconds
        success: Whether the tool ran and returned without error
        timestamp: When the tool was called (UTC)
    """
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: str
    permission_outcome: str  # "allow", "deny", "ask→yes", "ask→no", "none"
    duration_ms: float
    success: bool = True
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LLMCallEvent:
    """Event fired after an LLM call completes.
    
    Attributes:
        session_id: Current agent/sub-agent session ID
        model: Model name used
        prompt_tokens: Input token count (if available)
        completion_tokens: Output token count (if available)
        total_tokens: Total token count
        duration_ms: LLM call duration in milliseconds
        success: Whether the call succeeded
        timestamp: When the call completed (UTC)
    """
    session_id: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    duration_ms: float
    success: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MCPServerEvent:
    """Event fired when an MCP server connects, disconnects, or fails.

    Attributes:
        server_name: Name of the MCP server as configured in mcp.json
        event: "connected", "disconnected", or "failed"
        transport: Transport type ("stdio" or "http")
        tools_discovered: Number of tools registered from this server (connect only)
        error: Error message when event == "failed"
        session_id: Agent session ID, when known
        timestamp: When the event occurred (UTC)
    """
    server_name: str
    event: str  # "connected", "disconnected", "failed"
    transport: str
    tools_discovered: int = 0
    error: str | None = None
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MemoryOperationEvent:
    """Event fired after a long-term memory operation completes.
    
    Attributes:
        operation: "write", "update", "replace_summary", "get_pinned", "search"
            or "delete"
        backend: "sqlite" (source of truth, keyword search) or "qdrant" (vector index)
        success: Whether the operation completed without error
        duration_ms: Operation duration in milliseconds
        results: Memories returned (reads/searches only)
        session_id: Session the memory belongs to, when known
        timestamp: When the operation completed (UTC)
    """
    operation: str
    backend: str
    success: bool
    duration_ms: float
    results: int | None = None
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class ObservabilityHook(Protocol):
    """Protocol for observability hooks.
    
    Implementations receive lifecycle events and can log them, emit metrics,
    send traces, etc. All methods are optional — only implement what you need.
    
    """
    
    def on_run_start(self, event: RunStartEvent) -> None:
        """Called when a top-level agent run begins."""
        ...
    
    def on_run_end(self, event: RunEndEvent) -> None:
        """Called when a top-level agent run completes."""
        ...
    
    def on_subagent_start(self, event: SubAgentStartEvent) -> None:
        """Called when a sub-agent execution begins.
        
        Args:
            event: Sub-agent start event
        """
        ...
    
    def on_subagent_end(self, event: SubAgentEndEvent) -> None:
        """Called when a sub-agent execution completes.

        Args:
            event: Sub-agent end event
        """
        ...

    def on_subagent_progress(self, event: SubAgentProgressEvent) -> None:
        """Called repeatedly while a sub-agent runs.

        Fires far more often than the other events — once per LLM response and
        once per tool-call batch. Hooks that persist anything should sample or
        ignore it; it exists for live displays.

        Args:
            event: Sub-agent progress event
        """
        ...

    def on_tool_call(self, event: ToolCallEvent) -> None:
        """Called after a tool is dispatched.
        
        Args:
            event: Tool call event
        """
        ...
    
    def on_llm_call(self, event: LLMCallEvent) -> None:
        """Called after an LLM call completes.
        
        Args:
            event: LLM call event
        """
        ...
    
    def on_memory_operation(self, event: MemoryOperationEvent) -> None:
        """Called after a long-term memory operation completes."""
        ...

    def on_mcp_server(self, event: MCPServerEvent) -> None:
        """Called when an MCP server connects, disconnects, or fails."""
        ...


class HookRegistry:
    """Registry for observability hooks.
    
    Hooks can be registered globally and will receive all lifecycle events.
    Multiple hooks can be registered and will be called in registration order.
    """
    
    def __init__(self) -> None:
        self._hooks: list[ObservabilityHook] = []
        self._failed: set[tuple[str, str]] = set()

    def register(self, hook: ObservabilityHook) -> None:
        """Register a hook to receive lifecycle events.

        Args:
            hook: ObservabilityHook implementation
        """
        self._hooks.append(hook)

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks.clear()
        self._failed.clear()

    def _emit(self, method: str, event: object) -> None:
        """Call ``method`` on every hook that implements it.

        A hook must never break the agent, so failures are caught — but they are
        reported once per hook and method, because a hook that throws on every
        event used to be indistinguishable from no events at all.
        """
        for hook in self._hooks:
            handler = getattr(hook, method, None)
            if handler is None:
                continue
            try:
                handler(event)
            except Exception:
                key = (type(hook).__name__, method)
                if key not in self._failed:
                    self._failed.add(key)
                    logger.warning(
                        "Observability hook failed; further failures from it are not reported",
                        extra={"event_type": "hook_error", "hook": key[0], "method": method},
                        exc_info=True,
                    )

    def emit_run_start(self, event: RunStartEvent) -> None:
        """Emit a run start event to all registered hooks."""
        self._emit("on_run_start", event)

    def emit_run_end(self, event: RunEndEvent) -> None:
        """Emit a run end event to all registered hooks."""
        self._emit("on_run_end", event)

    def emit_subagent_start(self, event: SubAgentStartEvent) -> None:
        """Emit a sub-agent start event to all registered hooks."""
        self._emit("on_subagent_start", event)

    def emit_subagent_end(self, event: SubAgentEndEvent) -> None:
        """Emit a sub-agent end event to all registered hooks."""
        self._emit("on_subagent_end", event)

    def emit_subagent_progress(self, event: SubAgentProgressEvent) -> None:
        """Emit a sub-agent progress event to all registered hooks."""
        self._emit("on_subagent_progress", event)

    def emit_tool_call(self, event: ToolCallEvent) -> None:
        """Emit a tool call event to all registered hooks."""
        self._emit("on_tool_call", event)

    def emit_llm_call(self, event: LLMCallEvent) -> None:
        """Emit an LLM call event to all registered hooks."""
        self._emit("on_llm_call", event)

    def emit_memory_operation(self, event: MemoryOperationEvent) -> None:
        """Emit a memory operation event to all registered hooks."""
        self._emit("on_memory_operation", event)

    def emit_mcp_server(self, event: MCPServerEvent) -> None:
        """Emit an MCP server lifecycle event to all registered hooks."""
        self._emit("on_mcp_server", event)


# Global hook registry instance
_global_registry = HookRegistry()


def get_hook_registry() -> HookRegistry:
    """Get the global hook registry.
    
    Returns:
        Global HookRegistry instance
    """
    return _global_registry
