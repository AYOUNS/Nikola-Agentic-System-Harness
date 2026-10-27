# Observability Module

Observability hooks for Week 3 requirements — structured logging, metrics collection, and lifecycle monitoring.

## Overview

The observability module provides reusable lifecycle hooks that capture agent execution events:
- **Sub-agent delegation**: start/end events with success status and performance metrics
- **Tool calls**: permission outcomes and execution latency
- **LLM calls**: token usage, cost estimation, and throughput metrics

All hooks implement the `ObservabilityHook` protocol defined in [`agent/hooks/types.py`](../hooks/types.py).

## Components

### LoggingHook

Custom structured logging with configurable verbosity levels.

**Features:**
- ✅ Entry/exit logging for method flow tracking
- ✅ Configurable enable/disable
- ✅ Debug mode with detailed metrics
- ✅ Structured JSON output (via `agent.logging`)
- ✅ Performance metrics (tokens/sec, cost estimates)

**Basic Usage:**

```python
from agent.observability import LoggingHook
from agent.hooks import get_hook_registry

# Basic logging (INFO level)
registry = get_hook_registry()
registry.register(LoggingHook())

# Detailed logging (DEBUG level with entry/exit)
registry.register(LoggingHook(debug=True))

# Disabled
registry.register(LoggingHook(enabled=False))
```

**Configuration (config.yaml):**

```yaml
logging:
  enabled: true    # Enable observability logging
  debug: false     # Enable debug-level logging
  file:            # Log file path (null = default)
```

**Output Examples:**

INFO level (default):
```json
{
  "ts": "2025-01-15T10:30:45.123Z",
  "level": "INFO",
  "logger": "agent.observability.logging_hook",
  "session": "sess-abc123",
  "run": "turn-5",
  "msg": "Tool called",
  "event_type": "tool_call",
  "tool": "read_file",
  "permission_outcome": "allow",
  "duration_ms": 42
}
```

DEBUG level (with debug=True):
```json
{
  "ts": "2025-01-15T10:30:45.120Z",
  "level": "DEBUG",
  "logger": "agent.observability.logging_hook",
  "msg": "→ ENTRY: on_tool_call",
  "event_type": "hook_entry",
  "hook_method": "on_tool_call",
  "tool": "read_file",
  "session": "sess-abc123"
}
{
  "ts": "2025-01-15T10:30:45.123Z",
  "level": "INFO",
  "logger": "agent.observability.logging_hook",
  "msg": "Tool called",
  "event_type": "tool_call",
  "tool": "read_file",
  "permission_outcome": "allow",
  "duration_ms": 42
}
{
  "ts": "2025-01-15T10:30:45.124Z",
  "level": "DEBUG",
  "logger": "agent.observability.logging_hook",
  "msg": "Tool 'read_file' execution completed",
  "permission": "allow",
  "latency_ms": 42,
  "session": "sess-abc123"
}
{
  "ts": "2025-01-15T10:30:45.125Z",
  "level": "DEBUG",
  "logger": "agent.observability.logging_hook",
  "msg": "← EXIT: on_tool_call",
  "event_type": "hook_exit",
  "hook_method": "on_tool_call",
  "tool": "read_file",
  "duration_ms": 42
}
```

**Debug Mode Enhancements:**

When `debug=True`, additional metrics are logged:

| Event Type | Additional Fields |
|---|---|
| Sub-agent start | `full_task`, `task_length` |
| Sub-agent end | `result_preview`, `tools_list`, `efficiency_score` |
| Tool call | `permission`, `latency_ms` |
| LLM call | `tokens_per_second`, `estimated_cost_usd`, `token_ratio` |

**Query Examples (jq):**

```bash
# All tool calls
jq 'select(.event_type == "tool_call")' agent.jsonl

# Failed LLM calls
jq 'select(.event_type == "llm_call" and .success == false)' agent.jsonl

# Sub-agent metrics by ID
jq 'select(.subagent_id)' agent.jsonl | jq -s 'group_by(.subagent_id)'

# Hook entry/exit flow
jq 'select(.event_type | startswith("hook_"))' agent.jsonl

# LLM performance metrics (debug mode)
jq 'select(.tokens_per_second)' agent.jsonl

# All ERROR level logs
jq 'select(.level == "ERROR")' agent.jsonl

# Live tail with pretty print
tail -f agent.jsonl | jq .
```

### PrometheusHook

Records lifecycle events as Prometheus metrics; `start_metrics_server()` serves them on
`/metrics`. Both are started by the CLI when `metrics.enabled` is true.

```yaml
metrics:
  enabled: true
  host: 0.0.0.0
  port: 9464
```

| Metric | Type | Labels |
|--------|------|--------|
| `agent_runs_total` | counter | `agent`, `status` (success/guard/error) |
| `agent_active_runs` | gauge | `agent` |
| `agent_run_duration_seconds`, `agent_run_iterations` | histogram | `agent` |
| `agent_llm_calls_total` | counter | `model`, `status` |
| `agent_llm_call_duration_seconds` | histogram | `model` |
| `agent_llm_tokens_total` | counter | `model`, `type` (prompt/completion) |
| `agent_tool_calls_total` | counter | `tool`, `source` (builtin/mcp), `status` |
| `agent_tool_call_duration_seconds` | histogram | `tool`, `source` |
| `agent_permission_decisions_total` | counter | `tool`, `outcome` (allow/deny/ask_yes/ask_no/none) |
| `agent_subagent_runs_total` | counter | `role`, `status` |
| `agent_active_subagents` | gauge | `role` |
| `agent_subagent_duration_seconds`, `agent_subagent_iterations` | histogram | `role` |
| `agent_memory_operations_total` | counter | `operation`, `backend` (sqlite/qdrant), `status` |
| `agent_memory_operation_duration_seconds` | histogram | `operation`, `backend` |
| `agent_memory_search_results` | histogram | `backend` |

Events are emitted from: `agent/hooks/instrumentation.py` (run scopes, sub-agent scopes,
tool calls, LLM call wrappers), `agent/llm/dispatcher.py` (transport),
`agent/subagents/orchestrator.py` (sub-agents), `agent/memory/store.py` and
`agent/memory/vectors.py` (memory operations, via `agent/memory/instrumentation.py`).

**Stack:** `docker compose up prometheus grafana` then run the agent. Prometheus
(http://localhost:9090) scrapes `agent:9464` and `host.docker.internal:9464` per
[`prometheus/prometheus.yml`](../../prometheus/prometheus.yml); Grafana
(http://localhost:3001) auto-provisions the **Agent Metrics** dashboard from
[`grafana/dashboards/agent-metrics.json`](../../grafana/dashboards/agent-metrics.json).

## CustomLogger

Internal utility class providing entry/exit tracking and structured logging.

**Features:**
- `log_entry(method, **context)` — Entry point logging (debug only)
- `log_exit(method, **context)` — Exit point logging (debug only)
- `log_event(level, message, **extra)` — Structured event logging
- `info(message, **extra)` — INFO level convenience method
- `warning(message, **extra)` — WARNING level convenience method
- `debug_msg(message, **extra)` — DEBUG level convenience method

**Internal Usage (in hooks):**

```python
class MyHook:
    def __init__(self, enabled: bool = True, debug: bool = False):
        self.logger = CustomLogger(__name__, enabled=enabled, debug=debug)
    
    def on_tool_call(self, event: ToolCallEvent) -> None:
        self.logger.log_entry("on_tool_call", tool=event.tool_name)
        
        self.logger.info(
            "Tool executed",
            event_type="tool_call",
            tool=event.tool_name,
            duration_ms=event.duration_ms,
        )
        
        if self.logger.debug:
            self.logger.debug_msg(
                "Detailed metrics",
                extra_field="extra_value",
            )
        
        self.logger.log_exit("on_tool_call", tool=event.tool_name)
```

## Integration

### Registering Hooks

```python
from agent.hooks import get_hook_registry
from agent.observability import LoggingHook

# At harness startup
registry = get_hook_registry()
registry.register(LoggingHook(debug=False))
```

### From Config

```python
from agent.config import load_app_config
from agent.observability import LoggingHook
from agent.hooks import get_hook_registry

cfg = load_app_config()
registry = get_hook_registry()

# Initialize with config values
hook = LoggingHook(
    enabled=cfg.logging.enabled,
    debug=cfg.logging.debug,
)
registry.register(hook)
```

## Testing

Run unit tests:
```bash
pytest tests/unit/observability/
```

Run integration tests:
```bash
pytest tests/e2e/ -k observability
```

## Week 3 Compliance

✅ **Reusable lifecycle hooks** — not ad-hoc per-tool logging  
✅ **Configurable enable/disable** — via `enabled` parameter  
✅ **Debug level control** — via `debug` parameter  
✅ **Entry/exit logging** — automatic with `debug=True`  
✅ **Structured output** — JSON lines via `agent.logging`  
✅ **Performance metrics** — tokens/sec, cost estimates, efficiency scores  

## See Also

- [`agent/hooks/types.py`](../hooks/types.py) — Hook protocol and event types
- [`agent/logging.py`](../logging.py) — JSON logging infrastructure
- [`config.yaml`](../../config.yaml) — Configuration file
- [`docs/OBSERVABILITY_RESTRUCTURE.md`](../../docs/OBSERVABILITY_RESTRUCTURE.md) — Architecture decisions
