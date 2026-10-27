"""Prometheus metrics observability hook.

Turns lifecycle events into Prometheus counters, gauges and histograms. The hook
only records; exposing the metrics over HTTP is agent.observability.metrics_server.

Labels are kept low-cardinality on purpose: tool, model and role names are bounded
sets, but session/run ids and arguments are never used as labels.

Metrics (all prefixed ``agent_``):
- runs_total{agent,status}, active_runs{agent}, run_duration_seconds{agent},
  run_iterations{agent}
- llm_calls_total{model,status}, llm_call_duration_seconds{model},
  llm_tokens_total{model,type}
- tool_calls_total{tool,source,status}, tool_call_duration_seconds{tool,source}
- permission_decisions_total{tool,outcome}
- subagent_runs_total{role,status}, active_subagents{role},
  subagent_duration_seconds{role}, subagent_iterations{role}
- memory_operations_total{operation,backend,status},
  memory_operation_duration_seconds{operation,backend},
  memory_search_results{backend}
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

from agent.hooks.types import (
    LLMCallEvent,
    MCPServerEvent,
    MemoryOperationEvent,
    RunEndEvent,
    RunStartEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
)

# Agent runs and LLM calls take seconds to minutes; tools are usually sub-second
# but shell commands and MCP round-trips can take much longer.
_RUN_BUCKETS = (0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600)
_LLM_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120)
_TOOL_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_ITERATION_BUCKETS = (1, 2, 3, 5, 8, 13, 20, 30, 50)
# SQLite reads are sub-millisecond; Qdrant operations include an embedding call.
_MEMORY_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)
_MEMORY_RESULT_BUCKETS = (0, 1, 2, 3, 5, 10)


def tool_source(tool_name: str) -> str:
    """Classify a tool as "mcp" (namespaced mcp_<server>__<tool>) or "builtin"."""
    return "mcp" if tool_name.startswith("mcp_") else "builtin"


def permission_outcome_label(outcome: str) -> str:
    """Normalize a permission outcome to an ASCII label value ("ask→yes" → "ask_yes")."""
    return outcome.replace("→", "_").replace("->", "_")


class PrometheusHook:
    """Observability hook that records lifecycle events as Prometheus metrics.

    Args:
        registry: Collector registry to register metrics in. Defaults to the
            global prometheus_client registry; tests pass a fresh one so
            instances do not collide.
        namespace: Metric name prefix. Defaults to "agent".

    Example:
        hook = PrometheusHook()
        get_hook_registry().register(hook)
        start_metrics_server(port=9464)
    """

    def __init__(self, registry: CollectorRegistry = REGISTRY, namespace: str = "agent") -> None:
        self.registry = registry
        ns = namespace

        # Top-level runs
        self.runs_total = Counter(
            "runs_total", "Completed agent runs (user turns)", ["agent", "status"],
            namespace=ns, registry=registry,
        )
        self.active_runs = Gauge(
            "active_runs", "Agent runs currently in progress", ["agent"],
            namespace=ns, registry=registry,
        )
        self.run_duration = Histogram(
            "run_duration_seconds", "Agent run duration", ["agent"],
            buckets=_RUN_BUCKETS, namespace=ns, registry=registry,
        )
        self.run_iterations = Histogram(
            "run_iterations", "ReAct iterations per agent run", ["agent"],
            buckets=_ITERATION_BUCKETS, namespace=ns, registry=registry,
        )

        # LLM
        self.llm_calls_total = Counter(
            "llm_calls_total", "LLM calls", ["model", "status"],
            namespace=ns, registry=registry,
        )
        self.llm_duration = Histogram(
            "llm_call_duration_seconds", "LLM call duration", ["model"],
            buckets=_LLM_BUCKETS, namespace=ns, registry=registry,
        )
        self.llm_tokens_total = Counter(
            "llm_tokens_total", "LLM tokens consumed", ["model", "type"],
            namespace=ns, registry=registry,
        )

        # Tools and permissions
        self.tool_calls_total = Counter(
            "tool_calls_total", "Tool calls", ["tool", "source", "status"],
            namespace=ns, registry=registry,
        )
        self.tool_duration = Histogram(
            "tool_call_duration_seconds", "Tool call duration", ["tool", "source"],
            buckets=_TOOL_BUCKETS, namespace=ns, registry=registry,
        )
        self.permission_decisions_total = Counter(
            "permission_decisions_total", "Permission decisions for tool calls", ["tool", "outcome"],
            namespace=ns, registry=registry,
        )

        # Sub-agents
        self.subagent_runs_total = Counter(
            "subagent_runs_total", "Completed sub-agent runs", ["role", "status"],
            namespace=ns, registry=registry,
        )
        self.active_subagents = Gauge(
            "active_subagents", "Sub-agents currently running", ["role"],
            namespace=ns, registry=registry,
        )
        self.subagent_duration = Histogram(
            "subagent_duration_seconds", "Sub-agent run duration", ["role"],
            buckets=_RUN_BUCKETS, namespace=ns, registry=registry,
        )
        self.subagent_iterations = Histogram(
            "subagent_iterations", "ReAct iterations per sub-agent run", ["role"],
            buckets=_ITERATION_BUCKETS, namespace=ns, registry=registry,
        )

        # MCP servers
        self.mcp_connections_total = Counter(
            "mcp_connections_total", "MCP server lifecycle events", ["server", "transport", "event"],
            namespace=ns, registry=registry,
        )
        self.mcp_tools_discovered_total = Counter(
            "mcp_tools_discovered_total", "MCP tools discovered at connect time", ["server", "transport"],
            namespace=ns, registry=registry,
        )

        # Long-term memory
        self.memory_operations_total = Counter(
            "memory_operations_total", "Long-term memory operations", ["operation", "backend", "status"],
            namespace=ns, registry=registry,
        )
        self.memory_duration = Histogram(
            "memory_operation_duration_seconds", "Long-term memory operation duration", ["operation", "backend"],
            buckets=_MEMORY_BUCKETS, namespace=ns, registry=registry,
        )
        self.memory_search_results = Histogram(
            "memory_search_results", "Memories returned per successful search", ["backend"],
            buckets=_MEMORY_RESULT_BUCKETS, namespace=ns, registry=registry,
        )

    def on_run_start(self, event: RunStartEvent) -> None:
        """Mark a run as active."""
        self.active_runs.labels(agent=event.agent).inc()

    def on_run_end(self, event: RunEndEvent) -> None:
        """Record a finished run."""
        self.active_runs.labels(agent=event.agent).dec()
        self.runs_total.labels(agent=event.agent, status=event.status).inc()
        self.run_duration.labels(agent=event.agent).observe(event.duration_ms / 1000)
        self.run_iterations.labels(agent=event.agent).observe(event.iterations)

    def on_llm_call(self, event: LLMCallEvent) -> None:
        """Record an LLM call and its token usage."""
        status = "success" if event.success else "error"
        self.llm_calls_total.labels(model=event.model, status=status).inc()
        self.llm_duration.labels(model=event.model).observe(event.duration_ms / 1000)
        if event.prompt_tokens:
            self.llm_tokens_total.labels(model=event.model, type="prompt").inc(event.prompt_tokens)
        if event.completion_tokens:
            self.llm_tokens_total.labels(model=event.model, type="completion").inc(event.completion_tokens)

    def on_tool_call(self, event: ToolCallEvent) -> None:
        """Record a tool call and the permission decision that gated it.

        Status mapping:
        - "success"  tool ran without error
        - "denied"   permission check returned deny or ask→no; tool never ran
        - "error"    tool ran but raised, timed out, or arguments failed to parse
        """
        source = tool_source(event.tool_name)
        if event.permission_outcome in ("deny", "ask→no"):
            status = "denied"
        elif event.success:
            status = "success"
        else:
            status = "error"
        self.tool_calls_total.labels(tool=event.tool_name, source=source, status=status).inc()
        self.tool_duration.labels(tool=event.tool_name, source=source).observe(event.duration_ms / 1000)
        self.permission_decisions_total.labels(
            tool=event.tool_name,
            outcome=permission_outcome_label(event.permission_outcome),
        ).inc()

    def on_subagent_start(self, event: SubAgentStartEvent) -> None:
        """Mark a sub-agent as active."""
        self.active_subagents.labels(role=event.role_name).inc()

    def on_subagent_end(self, event: SubAgentEndEvent) -> None:
        """Record a finished sub-agent run."""
        status = "success" if event.success else "failure"
        self.active_subagents.labels(role=event.role_name).dec()
        self.subagent_runs_total.labels(role=event.role_name, status=status).inc()
        self.subagent_duration.labels(role=event.role_name).observe(event.duration_ms / 1000)
        self.subagent_iterations.labels(role=event.role_name).observe(event.iterations)

    def on_mcp_server(self, event: MCPServerEvent) -> None:
        """Record an MCP server lifecycle event (connected / disconnected / failed)."""
        self.mcp_connections_total.labels(
            server=event.server_name,
            transport=event.transport,
            event=event.event,
        ).inc()
        if event.event == "connected" and event.tools_discovered:
            self.mcp_tools_discovered_total.labels(
                server=event.server_name,
                transport=event.transport,
            ).inc(event.tools_discovered)

    def on_memory_operation(self, event: MemoryOperationEvent) -> None:
        """Record a memory operation; searches also record how many memories they found."""
        status = "success" if event.success else "error"
        self.memory_operations_total.labels(
            operation=event.operation, backend=event.backend, status=status,
        ).inc()
        self.memory_duration.labels(
            operation=event.operation, backend=event.backend,
        ).observe(event.duration_ms / 1000)
        if event.operation == "search" and event.success and event.results is not None:
            self.memory_search_results.labels(backend=event.backend).observe(event.results)
