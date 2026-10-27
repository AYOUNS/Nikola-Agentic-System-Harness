"""Unit tests for Prometheus metrics collection.

Covers PrometheusHook metric recording, the /metrics HTTP endpoint, and the
emission points that feed it (tool calls, top-level runs, streamed LLM calls,
memory operations).
"""

from __future__ import annotations

import urllib.request
from unittest.mock import MagicMock, Mock

import pytest
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from prometheus_client import CollectorRegistry

from agent.hooks import (
    HookRegistry,
    LLMCallEvent,
    MemoryOperationEvent,
    RunEndEvent,
    RunStartEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
)
from agent.observability import PrometheusHook, start_metrics_server
from agent.observability.prometheus_hook import permission_outcome_label, tool_source


@pytest.fixture
def metrics() -> tuple[PrometheusHook, CollectorRegistry]:
    registry = CollectorRegistry()
    return PrometheusHook(registry=registry), registry


@pytest.fixture
def captured(monkeypatch) -> list[object]:
    """Route the global hook registry to a recorder for emission-point tests."""
    events: list[object] = []

    class Recorder:
        def on_run_start(self, event): events.append(event)
        def on_run_end(self, event): events.append(event)
        def on_tool_call(self, event): events.append(event)
        def on_llm_call(self, event): events.append(event)
        def on_memory_operation(self, event): events.append(event)

    hooks = HookRegistry()
    hooks.register(Recorder())
    # All emission now flows through agent.hooks.instrumentation; the dispatcher
    # is pure transport so it no longer imports get_hook_registry at all.
    for module in ("agent.hooks.instrumentation", "agent.memory.instrumentation"):
        monkeypatch.setattr(f"{module}.get_hook_registry", lambda: hooks)
    return events


def _value(registry: CollectorRegistry, name: str, **labels: str) -> float | None:
    return registry.get_sample_value(name, labels)


# ---------------------------------------------------------------------------
# PrometheusHook
# ---------------------------------------------------------------------------

class TestPrometheusHook:

    def test_run_lifecycle(self, metrics):
        hook, reg = metrics
        hook.on_run_start(RunStartEvent(session_id="s", run_id="r"))
        assert _value(reg, "agent_active_runs", agent="main") == 1

        hook.on_run_end(RunEndEvent(
            session_id="s", run_id="r", agent="main", status="success",
            iterations=3, tool_calls=2, duration_ms=1500,
        ))
        assert _value(reg, "agent_active_runs", agent="main") == 0
        assert _value(reg, "agent_runs_total", agent="main", status="success") == 1
        assert _value(reg, "agent_run_duration_seconds_sum", agent="main") == 1.5
        assert _value(reg, "agent_run_iterations_sum", agent="main") == 3

    def test_llm_call_counts_tokens(self, metrics):
        hook, reg = metrics
        hook.on_llm_call(LLMCallEvent(
            session_id="s", model="m", prompt_tokens=100, completion_tokens=20,
            total_tokens=120, duration_ms=250, success=True,
        ))
        assert _value(reg, "agent_llm_calls_total", model="m", status="success") == 1
        assert _value(reg, "agent_llm_tokens_total", model="m", type="prompt") == 100
        assert _value(reg, "agent_llm_tokens_total", model="m", type="completion") == 20
        assert _value(reg, "agent_llm_call_duration_seconds_sum", model="m") == 0.25

    def test_failed_llm_call_without_usage(self, metrics):
        hook, reg = metrics
        hook.on_llm_call(LLMCallEvent(
            session_id="s", model="m", prompt_tokens=None, completion_tokens=None,
            total_tokens=None, duration_ms=10, success=False,
        ))
        assert _value(reg, "agent_llm_calls_total", model="m", status="error") == 1
        assert _value(reg, "agent_llm_tokens_total", model="m", type="prompt") is None

    def test_tool_call_records_source_status_and_permission(self, metrics):
        hook, reg = metrics
        hook.on_tool_call(ToolCallEvent(
            session_id="s", tool_name="mcp_calendar__list", arguments={}, result="ok",
            permission_outcome="ask→yes", duration_ms=40,
        ))
        hook.on_tool_call(ToolCallEvent(
            session_id="s", tool_name="write_file", arguments={}, result="Error: denied",
            permission_outcome="deny", duration_ms=1, success=False,
        ))
        assert _value(reg, "agent_tool_calls_total", tool="mcp_calendar__list", source="mcp", status="success") == 1
        # A policy denial must land in status="denied", not "error".
        assert _value(reg, "agent_tool_calls_total", tool="write_file", source="builtin", status="denied") == 1
        assert _value(reg, "agent_tool_calls_total", tool="write_file", source="builtin", status="error") is None
        assert _value(reg, "agent_permission_decisions_total", tool="mcp_calendar__list", outcome="ask_yes") == 1
        assert _value(reg, "agent_permission_decisions_total", tool="write_file", outcome="deny") == 1

    def test_ask_no_is_denied_not_error(self, metrics):
        """User-declined (ask→no) calls must also be status="denied"."""
        hook, reg = metrics
        hook.on_tool_call(ToolCallEvent(
            session_id="s", tool_name="execute_command", arguments={}, result="Error: denied by user",
            permission_outcome="ask→no", duration_ms=2, success=False,
        ))
        assert _value(reg, "agent_tool_calls_total", tool="execute_command", source="builtin", status="denied") == 1
        assert _value(reg, "agent_tool_calls_total", tool="execute_command", source="builtin", status="error") is None

    def test_genuine_tool_error_is_not_denied(self, metrics):
        """A tool that ran and broke must be status="error", not "denied"."""
        hook, reg = metrics
        hook.on_tool_call(ToolCallEvent(
            session_id="s", tool_name="read_file", arguments={}, result="Error: file not found",
            permission_outcome="allow", duration_ms=5, success=False,
        ))
        assert _value(reg, "agent_tool_calls_total", tool="read_file", source="builtin", status="error") == 1
        assert _value(reg, "agent_tool_calls_total", tool="read_file", source="builtin", status="denied") is None

    def test_subagent_lifecycle(self, metrics):
        hook, reg = metrics
        hook.on_subagent_start(SubAgentStartEvent(
            parent_session_id="p", subagent_id="a", role_name="file-scout", task="t",
        ))
        assert _value(reg, "agent_active_subagents", role="file-scout") == 1

        hook.on_subagent_end(SubAgentEndEvent(
            parent_session_id="p", subagent_id="a", role_name="file-scout", success=False,
            result="", iterations=5, tools_used=[], duration_ms=2000,
        ))
        assert _value(reg, "agent_active_subagents", role="file-scout") == 0
        assert _value(reg, "agent_subagent_runs_total", role="file-scout", status="failure") == 1
        assert _value(reg, "agent_subagent_duration_seconds_sum", role="file-scout") == 2

    def test_memory_operations(self, metrics):
        hook, reg = metrics
        hook.on_memory_operation(MemoryOperationEvent(
            operation="search", backend="qdrant", success=True, duration_ms=300, results=2,
        ))
        hook.on_memory_operation(MemoryOperationEvent(
            operation="write", backend="sqlite", success=False, duration_ms=2,
        ))
        assert _value(reg, "agent_memory_operations_total", operation="search", backend="qdrant", status="success") == 1
        assert _value(reg, "agent_memory_operations_total", operation="write", backend="sqlite", status="error") == 1
        assert _value(reg, "agent_memory_operation_duration_seconds_sum", operation="search", backend="qdrant") == 0.3
        assert _value(reg, "agent_memory_search_results_sum", backend="qdrant") == 2
        assert _value(reg, "agent_memory_search_results_count", backend="sqlite") is None

    def test_label_helpers(self):
        assert tool_source("mcp_weather__get_forecast") == "mcp"
        assert tool_source("read_file") == "builtin"
        assert permission_outcome_label("ask→no") == "ask_no"
        assert permission_outcome_label("allow") == "allow"

    def test_registry_skips_hooks_without_run_methods(self):
        """Hooks written before run events existed must not break emission."""
        hooks = HookRegistry()
        seen = []

        class OldHook:
            def on_tool_call(self, event): seen.append(event)

        hooks.register(OldHook())
        hooks.emit_run_start(RunStartEvent(session_id="s", run_id="r"))
        assert seen == []


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------

class TestMetricsServer:

    def test_serves_metrics(self, metrics):
        hook, reg = metrics
        hook.on_run_start(RunStartEvent(session_id="s", run_id="r"))
        server = start_metrics_server(port=0, host="127.0.0.1", registry=reg)
        assert server is not None
        try:
            body = urllib.request.urlopen(server.url, timeout=5).read().decode()
        finally:
            server.stop()
        assert 'agent_active_runs{agent="main"} 1.0' in body

    def test_bind_failure_returns_none(self, metrics, monkeypatch):
        # Simulated: Windows lets a second SO_REUSEADDR socket share the port, so a real
        # double bind is not a reliable failure across platforms.
        _, reg = metrics

        def refuse(*_, **__):
            raise OSError("Address already in use")

        monkeypatch.setattr("agent.observability.metrics_server.start_http_server", refuse)
        assert start_metrics_server(port=9464, host="127.0.0.1", registry=reg) is None


# ---------------------------------------------------------------------------
# Emission points
# ---------------------------------------------------------------------------

def _tool_call(name: str, arguments: str = "{}") -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(id="c1", type="function", function=Function(name=name, arguments=arguments))


class TestToolCallEmission:

    async def test_emits_allowed_call(self, captured):
        from agent.hooks.instrumentation import InstrumentedToolAdapter
        from agent.loop_adapters import MainLoopToolAdapter
        from agent.permissions import PermissionAction, PermissionDecision
        from agent.tools import ToolRegistry

        registry = ToolRegistry()
        registry.register(name="echo", fn=lambda **_: "hi", schema={})
        permissions = MagicMock()
        permissions.check.return_value = PermissionDecision(action=PermissionAction.ALLOW)

        adapter = InstrumentedToolAdapter(
            MainLoopToolAdapter(registry=registry, permission_module=permissions)
        )
        await adapter.dispatch_all([_tool_call("echo")])

        (event,) = captured
        assert isinstance(event, ToolCallEvent)
        assert event.tool_name == "echo"
        assert event.permission_outcome == "allow"
        assert event.success is True

    async def test_emits_user_denied_call(self, captured):
        from agent.hooks.instrumentation import InstrumentedToolAdapter
        from agent.loop_adapters import MainLoopToolAdapter
        from agent.permissions import PermissionAction, PermissionDecision
        from agent.tools import ToolRegistry

        permissions = MagicMock()
        permissions.check.return_value = PermissionDecision(action=PermissionAction.ASK)

        adapter = InstrumentedToolAdapter(
            MainLoopToolAdapter(
                registry=ToolRegistry(),
                permission_module=permissions,
                confirmation_fn=lambda *_: (False, "once"),
            )
        )
        await adapter.dispatch_all([_tool_call("write_file")])

        (event,) = captured
        assert event.permission_outcome == "ask→no"
        assert event.success is False

    async def test_emits_parse_error(self, captured):
        from agent.hooks.instrumentation import InstrumentedToolAdapter
        from agent.loop_adapters import MainLoopToolAdapter
        from agent.tools import ToolRegistry

        adapter = InstrumentedToolAdapter(
            MainLoopToolAdapter(registry=ToolRegistry())
        )
        await adapter.dispatch_all([_tool_call("echo", "{bad")])

        (event,) = captured
        assert event.permission_outcome == "none"
        assert event.success is False


class TestRunEmission:

    async def test_emits_start_and_end(self, captured, monkeypatch):
        from agent.engine.types import TurnResult
        from agent.loop import make_process_turn_fn_v2
        from agent.tools import ToolRegistry

        async def fake_run_turn(**_):
            return TurnResult(answer="done", iterations=2, guard_fired=None, tool_calls_made=1)

        monkeypatch.setattr("agent.loop.run_turn", fake_run_turn)
        process_turn = make_process_turn_fn_v2(dispatch=Mock(), registry=ToolRegistry(), config={})

        assert await process_turn([]) == "done"

        start, end = captured
        assert isinstance(start, RunStartEvent)
        assert isinstance(end, RunEndEvent)
        assert end.run_id == start.run_id
        assert (end.status, end.iterations, end.tool_calls) == ("success", 2, 1)

    async def test_marks_guard_and_errors(self, captured, monkeypatch):
        from agent.engine.types import TurnResult
        from agent.loop import make_process_turn_fn_v2
        from agent.tools import ToolRegistry

        results = iter([
            TurnResult(answer="[Loop terminated]", iterations=30, guard_fired="max_iterations", tool_calls_made=0),
            TurnResult(answer="[Error: LLM call failed: boom]", iterations=1, guard_fired=None, tool_calls_made=0),
        ])

        async def fake_run_turn(**_):
            return next(results)

        monkeypatch.setattr("agent.loop.run_turn", fake_run_turn)
        process_turn = make_process_turn_fn_v2(dispatch=Mock(), registry=ToolRegistry(), config={})
        await process_turn([])
        await process_turn([])

        statuses = [e.status for e in captured if isinstance(e, RunEndEvent)]
        assert statuses == ["guard", "error"]


class TestStreamingLLMEmission:

    def _config(self):
        return {"llm": {"model": "cfg-model", "timeout_seconds": 60}, "loop": {}}

    def test_emits_usage_from_final_chunk(self, captured):
        from agent.hooks.instrumentation import instrument_stream_dispatch
        from agent.llm.dispatcher import make_chat_completions_stream_effect

        usage = Mock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        chunks = [Mock(usage=None, model="served-model"), Mock(usage=usage, model="served-model")]
        client = Mock()
        client.chat.completions.create.return_value = iter(chunks)

        raw = make_chat_completions_stream_effect(client, self._config())
        dispatch = instrument_stream_dispatch(raw, model="cfg-model")
        assert list(dispatch({"messages": []})) == chunks

        (event,) = captured
        assert isinstance(event, LLMCallEvent)
        assert (event.model, event.total_tokens, event.success) == ("served-model", 15, True)
        assert client.chat.completions.create.call_args.kwargs["stream_options"] == {"include_usage": True}

    def test_emits_failure_when_stream_breaks(self, captured):
        from agent.hooks.instrumentation import instrument_stream_dispatch
        from agent.llm.dispatcher import make_chat_completions_stream_effect

        def broken():
            yield Mock(usage=None, model="m")
            raise RuntimeError("connection reset")

        client = Mock()
        client.chat.completions.create.return_value = broken()
        raw = make_chat_completions_stream_effect(client, self._config())
        dispatch = instrument_stream_dispatch(raw, model="cfg-model")

        with pytest.raises(RuntimeError):
            list(dispatch({"messages": []}))

        (event,) = captured
        assert event.success is False
        assert event.model == "m"


# ---------------------------------------------------------------------------
# Memory operation emission
# ---------------------------------------------------------------------------

class TestMemoryEmission:

    @pytest.fixture
    def store(self, tmp_path):
        from agent.memory.store import MemoryStore
        store = MemoryStore(tmp_path / "memory.db")
        yield store
        store.close()

    def _ops(self, captured):
        return [(e.operation, e.backend, e.success, e.results) for e in captured]

    def test_sqlite_operations(self, store, captured):
        from agent.memory.store import Memory

        stored = store.write(Memory(content="uses pytest", kind="fact", source="user", session_id="s1", pinned=True))
        store.update(stored.id, embedded=True)
        store.update(9999, pinned=False)
        store.replace_summary_for_session("s1", "summary of work")
        store.search_keyword("pytest", limit=3)
        store.get_pinned()

        assert self._ops(captured) == [
            ("write", "sqlite", True, None),
            ("update", "sqlite", True, None),
            ("update", "sqlite", False, None),
            ("replace_summary", "sqlite", True, None),  # no nested "write" event
            ("search", "sqlite", True, 1),
            ("get_pinned", "sqlite", True, 1),
        ]
        assert captured[0].session_id == "s1"
        assert all(e.duration_ms >= 0 for e in captured)

    def test_sqlite_error_is_recorded_and_raised(self, store, captured):
        """A genuine SQLite failure is still recorded as a failed operation and re-raised.

        Provoked by removing the index rather than by malformed query text: user text is
        now converted to a safe MATCH expression before it reaches SQLite, so punctuation
        no longer reaches the engine as syntax (see test below).
        """
        import sqlite3

        store.conn.execute("DROP TABLE memories_fts")

        with pytest.raises(sqlite3.OperationalError):
            store.search_keyword("anything", limit=3)

        assert self._ops(captured) == [("search", "sqlite", False, None)]

    def test_punctuated_query_searches_instead_of_raising(self, store, captured):
        """Prompts naming a file used to raise fts5 syntax errors and return nothing."""
        store.search_keyword("what changed in config.yaml?", limit=3)

        assert self._ops(captured) == [("search", "sqlite", True, 0)]

    def test_qdrant_operations(self, captured):
        from agent.memory.vectors import VectorStore

        vectors = VectorStore.__new__(VectorStore)
        vectors.collection_name = "c"
        vectors.client = MagicMock()
        vectors.client.query_points.return_value = Mock(points=[Mock(id=1), Mock(id=2)])
        vectors._available = True

        vectors.embedding_fn = lambda text: [0.1]
        vectors.search("q")
        vectors.delete(1)
        vectors.embedding_fn = lambda text: None
        vectors.upsert(1, "text")

        vectors._available = False
        vectors.search("q")  # skipped entirely: nothing happened, nothing recorded

        assert self._ops(captured) == [
            ("search", "qdrant", True, 2),
            ("delete", "qdrant", True, None),
            ("write", "qdrant", False, None),
        ]


# ---------------------------------------------------------------------------
# MCP server hook
# ---------------------------------------------------------------------------

class TestMCPServerHook:

    def test_connected_increments_connections_and_tools(self, metrics):
        from agent.hooks import MCPServerEvent
        hook, reg = metrics
        hook.on_mcp_server(MCPServerEvent(
            server_name="calendar", event="connected", transport="http", tools_discovered=3,
        ))
        assert _value(reg, "agent_mcp_connections_total", server="calendar", transport="http", event="connected") == 1
        assert _value(reg, "agent_mcp_tools_discovered_total", server="calendar", transport="http") == 3

    def test_failed_increments_connections_no_tools(self, metrics):
        from agent.hooks import MCPServerEvent
        hook, reg = metrics
        hook.on_mcp_server(MCPServerEvent(
            server_name="weather", event="failed", transport="stdio", error="connection refused",
        ))
        assert _value(reg, "agent_mcp_connections_total", server="weather", transport="stdio", event="failed") == 1
        assert _value(reg, "agent_mcp_tools_discovered_total", server="weather", transport="stdio") is None

    def test_disconnected_increments_connections_no_tools(self, metrics):
        from agent.hooks import MCPServerEvent
        hook, reg = metrics
        hook.on_mcp_server(MCPServerEvent(
            server_name="calendar", event="disconnected", transport="http",
        ))
        assert _value(reg, "agent_mcp_connections_total", server="calendar", transport="http", event="disconnected") == 1
        assert _value(reg, "agent_mcp_tools_discovered_total", server="calendar", transport="http") is None

    def test_connected_with_zero_tools_skips_tools_counter(self, metrics):
        from agent.hooks import MCPServerEvent
        hook, reg = metrics
        hook.on_mcp_server(MCPServerEvent(
            server_name="empty-server", event="connected", transport="stdio", tools_discovered=0,
        ))
        assert _value(reg, "agent_mcp_connections_total", server="empty-server", transport="stdio", event="connected") == 1
        # tools_discovered=0 is falsy — counter must not be touched
        assert _value(reg, "agent_mcp_tools_discovered_total", server="empty-server", transport="stdio") is None

    def test_unknown_transport_is_accepted(self, metrics):
        from agent.hooks import MCPServerEvent
        hook, reg = metrics
        hook.on_mcp_server(MCPServerEvent(
            server_name="legacy", event="connected", transport="unknown", tools_discovered=1,
        ))
        assert _value(reg, "agent_mcp_connections_total", server="legacy", transport="unknown", event="connected") == 1
