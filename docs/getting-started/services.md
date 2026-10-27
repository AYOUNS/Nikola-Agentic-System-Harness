# Services & Endpoints

The full stack is defined in `compose.yaml`. Start it with `docker compose up --build -d`.

All ports bind to `127.0.0.1` on the host (localhost only — not exposed to the network).

---

## Service table

| Service | Endpoint | Purpose |
|---------|----------|---------|
| `agent` | `docker compose attach agent` | Main agent harness CLI |
| `agent` metrics | <http://localhost:9464/metrics> | Prometheus scrape endpoint |
| `mcp_calendar` | <http://localhost:3000> | MCP calendar server |
| `mitmproxy` proxy | <http://localhost:8080> | LLM reverse proxy |
| `mitmproxy` web UI | <http://localhost:9091> | Live LLM traffic inspection |
| `qdrant` | <http://localhost:6333/dashboard> | Vector DB (long-term memory) |
| `sqlite-web` | <http://localhost:8081> | Memory SQLite browser |
| `prometheus` | <http://localhost:9090> | Scrapes agent metrics every 5 s |
| `grafana` | <http://localhost:3001> | Agent Metrics dashboard + log browsing |
| `loki` | <http://localhost:3100> | Structured log store |
| `alloy` | <http://localhost:12345> | Ships `agent.jsonl` into Loki (UI shows pipeline health) |

!!! tip "Use `attach`, not `exec` or `run`"
    The container's entrypoint is already the CLI and it owns the metrics port (9464).
    A second process started with `compose exec` cannot bind 9464 — the session works
    but publishes metrics nowhere. `compose run` gets no `agent` network alias.
    Use `docker compose restart agent && docker compose attach agent`
    (or `podman compose restart agent && podman compose attach agent`).

---

## Prometheus metrics

```bash
curl -s localhost:9464/metrics | grep ^agent_
```

All series are prefixed `agent_` and cover:

| Metric prefix | What it tracks |
|---------------|---------------|
| `agent_runs_total` | Turn throughput and outcomes (success / guard / error) |
| `agent_active_runs` | Concurrently running turns |
| `agent_run_duration_seconds` | End-to-end turn latency |
| `agent_llm_calls_total` | LLM calls by model and status |
| `agent_llm_tokens_total` | Prompt and completion token usage |
| `agent_tool_calls_total` | Tool calls by name, source (builtin/mcp), status |
| `agent_permission_decisions_total` | Permission outcomes (allow/deny/ask→yes/ask→no) |
| `agent_subagent_runs_total` | Sub-agent delegations by role and status |
| `agent_memory_operations_total` | Memory reads/writes by backend (sqlite/qdrant) |

Check scrape health at **<http://localhost:9090/targets>** — the `agent` target should be `UP`.

---

## Grafana dashboard

Open **<http://localhost:3001>** — anonymous admin, opens on **Agent Metrics** automatically.

Panels:

- Agent throughput and active runs
- Run / LLM / tool / sub-agent latency (p50 / p95 / p99)
- Success and failure rates
- LLM token usage (per-turn and cumulative)
- Built-in vs MCP tool call breakdown
- Permission decisions (allow / deny / ask)
- Sub-agent runs by role

!!! note "Empty dashboard on a fresh session"
    A counter has no series until the code path that increments it runs.
    Send one prompt and the dashboard fills within ~20 s.

---

## Structured logs (Loki)

The agent writes structured JSON lines to `workspace/.nikola/logs/agent.jsonl`.
Alloy tails that file into Loki. Browse in Grafana under **Drilldown → Logs**.

**Indexed labels** (fast filtering): `service`, `level`  
**Structured metadata**: `logger`, `session`, `run`, `event_type`, `tool`

```logql
{service="agent"}                           # everything
{service="agent", level="ERROR"}            # errors only
{service="agent"} | event_type="tool_call"  # tool calls
{service="agent"} | event_type="subagent_end"
{service="agent"} | session="<session-id>"  # one session
```

Query the raw file directly:

```bash
jq 'select(.event_type=="subagent_end")' workspace/.nikola/logs/agent.jsonl
tail -f workspace/.nikola/logs/agent.jsonl | jq .
```
