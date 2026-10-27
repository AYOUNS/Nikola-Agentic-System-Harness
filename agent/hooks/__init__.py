"""Lifecycle hooks protocol and event types (Week 3).

Defines the hook interface for monitoring agent execution:
- Event types: RunStartEvent, RunEndEvent, SubAgentStartEvent, SubAgentEndEvent, ToolCallEvent, LLMCallEvent
- Protocol: ObservabilityHook
- Registry: HookRegistry (global singleton)
- Context: RunContext (ambient session/run id via ContextVar)
- Instrumentation: run_scope, subagent_scope, InstrumentedToolAdapter, instrument_dispatch

Hook implementations (LoggingHook, PrometheusHook, etc.) live in agent.observability.

Example usage:
    from agent.hooks import get_hook_registry
    from agent.observability import LoggingHook

    registry = get_hook_registry()
    registry.register(LoggingHook())
"""

from agent.hooks.context import (
    RunContext,
    current_context,
    current_session_id,
    use_context,
)
from agent.hooks.instrumentation import (
    InstrumentedToolAdapter,
    RunOutcome,
    SubAgentOutcome,
    classify_status,
    instrument_dispatch,
    instrument_stream_dispatch,
    run_scope,
    subagent_scope,
)
from agent.hooks.types import (
    HookRegistry,
    LLMCallEvent,
    MCPServerEvent,
    MemoryOperationEvent,
    ObservabilityHook,
    RunEndEvent,
    RunStartEvent,
    SubAgentEndEvent,
    SubAgentProgressEvent,
    SubAgentStartEvent,
    ToolCallEvent,
    get_hook_registry,
)

__all__ = [
    "HookRegistry",
    "InstrumentedToolAdapter",
    "LLMCallEvent",
    "MCPServerEvent",
    "MemoryOperationEvent",
    "ObservabilityHook",
    "RunContext",
    "RunEndEvent",
    "RunOutcome",
    "RunStartEvent",
    "SubAgentEndEvent",
    "SubAgentOutcome",
    "SubAgentProgressEvent",
    "SubAgentStartEvent",
    "ToolCallEvent",
    "classify_status",
    "current_context",
    "current_session_id",
    "get_hook_registry",
    "instrument_dispatch",
    "instrument_stream_dispatch",
    "run_scope",
    "subagent_scope",
    "use_context",
]
