# Agent Harness

A terminal-based ReAct agent harness that pairs an LLM with filesystem tools, running inside a container sandbox.

## Module Layout

```
agent/
├── cli/
│   ├── __init__.py      ← Typer app; --prompt / --mode / --config / --stream / --thinking / --permissions
│   ├── repl.py          ← run_repl(process_turn_fn, one_shot); routes input to a command or a Turn
│   ├── session.py       ← Session state (messages, mode, model, tools, ui) + load_system_prompt
│   ├── ui.py            ← ConsoleUI: the only writer to the terminal; the loop's on_output sink
│   ├── completion.py    ← readline Tab completion + prompt history, driven by the registry
│   └── commands/
│       ├── __init__.py  ← Command, CommandRegistry, make_default_command_registry()
│       └── builtin.py   ← @command declarations: /help /exit /clear /mode /tools /model
├── loop.py              ← make_process_turn_fn(dispatch, registry, config, hooks) → process_turn_fn
├── prompts/
│   └── default.md       ← default system prompt (one file per Agent Mode)
├── tools/
│   ├── __init__.py      ← ToolRegistry class + make_default_tool_registry()
│   ├── filesystem.py    ← 10 filesystem tools (no delete)
│   └── execute.py       ← execute_command tool (screened, sandboxed)
├── permissions/         ← Week 3 ✅ IMPLEMENTED
│   ├── __init__.py      ← exports (policy, gate)
│   ├── policy.py        ← PermissionPolicy (allowlist/denylist/asklist arrays), auto-deny for sub-agents
│   └── gate.py          ← PermissionGate wraps dispatch, enforces policy, generates error messages
├── subagents/           ← Week 3 ✅ IMPLEMENTED (sub-agent auto-deny)
│   ├── __init__.py      ← exports (roles, orchestrator)
│   ├── roles.py         ← SubAgentRole definitions (file-scout, scheduler)
│   └── orchestrator.py  ← execute_subagent(), make_delegate_tool()
├── hooks/               ← Week 3 (TODO: observability)
│   └── __init__.py
├── memory/              ← Week 2 (SQLite + Qdrant)
│   ├── store.py         ← MemoryStore (SQLite + FTS5)
│   ├── vectors.py       ← VectorStore (Qdrant semantic search)
│   ├── tools.py         ← remember(), search_memory()
│   └── policy.py        ← write/read policy, pinned memory caps
├── mcp/                 ← Week 2 (MCP client)
│   ├── config.py        ← parse .nikola/mcp.json
│   ├── client.py        ← MCPClient interface
│   ├── stdio_client.py  ← JSON-RPC over subprocess
│   ├── http_client.py   ← JSON-RPC over HTTP
│   └── manager.py       ← lifecycle, discovery, registration
├── context/             ← Week 2 (budget + compaction)
│   ├── budget.py        ← token estimation
│   ├── compaction.py    ← 3-tier compaction (truncate, drop, summarize)
│   └── rules.py         ← load .nikola/rules/*.md
├── skills/              ← Week 2 (progressive disclosure)
│   ├── loader.py        ← discover and load skills
│   ├── tool.py          ← load_skill tool
│   ├── filesystem.md    ← built-in skill
│   └── memory.md        ← built-in skill
└── llm/                 ← existing: client.py, dispatcher.py, types.py
```

### Adding a slash command

Write a handler in `agent/cli/commands/builtin.py` and decorate it — that is the entire
procedure. The decorator carries name, summary, usage and aliases, `/help` renders from the
registry, and `make_default_command_registry()` picks it up:

```python
@command("history", "Show how many messages the conversation holds.")
def history_command(session: Session, args: str) -> None:
    session.ui.info(f"{len(session.messages)} messages")
```

Pass `complete=` to offer Tab completions for the command's argument; the completer is read
from the same declaration, so completion never drifts from what the handler accepts:

```python
@command("deploy", "Deploy to an environment.", usage="/deploy <env>",
         complete=lambda partial: [e for e in ("staging", "production") if e.startswith(partial)])
def deploy_command(session: Session, args: str) -> None:
    ...
```

## Language

### Execution Hierarchy

**Session**:
The full lifetime of one agent process — from startup to exit. A Session contains zero or more Turns.
_Avoid_: run, instance, process

**Turn**:
One user message and the single agent response that closes it. A Turn spans as many internal Steps as the ReAct loop requires before producing a final answer.
_Avoid_: run, request, exchange, iteration

**Step**:
One Thought/Action/Observation cycle inside the ReAct loop. A Step consists of one LLM call (Thought + optional Action selection) followed by zero or one Tool invocations (Observation). A Turn ends when a Step produces a final answer instead of a Tool call. The signal is structural: if the LLM response contains `tool_calls`, the Step is an Action; if it has no `tool_calls` (finish reason `"stop"`), it is a final answer. No text parsing or special prefixes are used.
_Avoid_: iteration, cycle, tick

### Entry Points

**REPL mode**:
Interactive Session where the user types prompts at a prompt and the agent responds, repeating until the user exits.
_Avoid_: interactive mode, chat mode

**One-shot mode**:
A Session that processes exactly one Turn — the prompt supplied as a CLI argument — and exits immediately after the final answer.
_Avoid_: single-turn mode, batch mode

**Agent Mode**:
A named behavioural configuration that selects the system prompt loaded at Session start. The mode name maps to a file at `agent/prompts/<mode>.md`; `"default"` is the fallback. Examples: `default`, `readonly`, `planner`. Not to be confused with REPL mode vs one-shot mode — those are entry-point variants, while Agent Mode governs what the LLM is instructed to do.
_Avoid_: persona, role, profile

**Loop Guard**:
A termination condition that bounds the ReAct loop so it always terminates: iteration cap, token budget, or time budget. When a guard fires, `process_turn_fn` returns a synthetic final-answer string describing the reason (e.g. `"[Loop terminated: max iterations reached]"`); it does not raise an exception.
_Avoid_: timeout, circuit breaker, safety check

### Core Abstractions

**process_turn_fn**:
A callable with signature `(messages: list[dict]) -> str` that owns the full ReAct loop for one Turn. `messages` is the full OpenAI-spec conversation history (`[{"role": ..., "content": ...}]`) owned and grown by the REPL; the callable appends its own assistant reply before returning the final answer string. Injected into the REPL so the loop is testable independently of I/O. The REPL implementation uses streaming dispatch internally (tokens printed as they arrive); non-streaming dispatch is used by programmatic callers such as sub-agents and tests.
_Avoid_: agent function, handler

**ToolRegistry**:
The single registry through which all Tool invocations are dispatched. The LLM never calls a Tool directly; it emits a tool-call request that the harness resolves through the ToolRegistry via `dispatch(tool_name: str, arguments: dict) -> str`. The registry is decoupled from the OpenAI SDK — the loop extracts name and arguments from the SDK response before calling dispatch.
_Avoid_: tool manager, plugin registry

**Tool**:
A deterministic, schema-defined capability exposed to the LLM. Tools have bounded, clearly described behaviour and are registered in the ToolRegistry. Delete operations are explicitly excluded. A Tool always returns a string: the result on success, or `"Error: <reason>"` on failure. Tools from MCP servers are registered with the naming pattern `mcp_<server_name>__<tool_name>` (e.g. `mcp_weather__get_forecast`); built-in tools use simple names (e.g. `read_file`).
_Avoid_: plugin, function, action

**Observation**:
The string result of a Tool invocation, appended to `messages` as a `{"role": "tool", ...}` entry after each Step that includes an Action. On Tool failure the Observation is the error string; the LLM reads it and can reason about retrying or reporting to the user.
_Avoid_: tool output, tool result, response

**Workspace**:
The container-local directory tree the agent is permitted to read and write. All filesystem Tool operations are restricted to the Workspace; paths that escape it (e.g. `../`, absolute host paths) are rejected. The root is resolved from `config["workspace"]`; `AGENT_WORKSPACE` env var overrides it (12-factor pattern, same as `OPENAI_BASE_URL`).
_Avoid_: sandbox root, working directory

**config.yaml**:
The main harness configuration file. Single source of truth for LLM settings, workspace path, loop limits, and observability settings. Week 3 adds an `observability` section with keys: `prometheus_port` (default 9090), `enable_metrics` (default true), `enable_structured_logs` (default true). Example:
```yaml
llm:
  model: qwen-agentworld-35b-a3b
  timeout: 30
workspace: /workspace
limits:
  max_iterations: 10
  max_tokens: 100000
observability:
  prometheus_port: 9090
  enable_metrics: true
  enable_structured_logs: true
```
_Avoid_: settings.yaml, harness.yaml


### MCP Integration (Week 2)

**MCP Server**:
An external process that exposes capabilities (tools, resources, prompts) via the Model Context Protocol. MCP Servers are configured in `nikola/mcp.json` and connected at Session start. Each server runs as a separate subprocess (stdio) or HTTP endpoint. The harness discovers available tools from each server and registers them in the ToolRegistry alongside built-in tools.
_Avoid_: plugin, service, extension

**MCP Client**:
The harness-side abstraction that connects to one MCP Server and translates between the harness's Tool schema and the MCP protocol. Two variants: `StdioMCPClient` (subprocess with JSON-RPC over stdin/stdout) and `HttpMCPClient` (JSON-RPC over HTTP). Clients are created and owned by the MCP Manager.
_Avoid_: connector, adapter, bridge

**MCP Manager**:
Orchestrates the lifecycle of all configured MCP Servers: loads `nikola/mcp.json`, instantiates clients, connects them at Session start, discovers their tools, registers those tools in the ToolRegistry, and disconnects them at Session end. The manager does not own subprocesses or protocol details — clients encapsulate those.
_Avoid_: MCP service, MCP coordinator

**nikola/mcp.json**:
The user-facing configuration file that declares which MCP Servers to connect. Located at `${WORKSPACE}/nikola/mcp.json` (per-workspace config). Format matches Claude Desktop's `mcp.json` schema: `{"mcpServers": {"server-name": {"command": "...", "args": [...], "env": {...}}}}`. Supports environment variable substitution using `${VAR}` syntax in `env` values. Week 2: read-only (agent cannot modify). Week 3: write capability with user confirmation.
_Avoid_: mcp-config.json, servers.json


### Long-term Memory (Week 2)

**Memory**:
Persistent information stored across sessions. Memories survive application restarts and are classified by Kind (fact/preference/summary). SQLite is the source of truth; Qdrant is an optional index for semantic search that falls back to FTS5 keyword search when unavailable.
_Avoid_: cache, state, knowledge base

**Pinned Memory**:
A Memory with `pinned=True` that is injected into the system prompt at startup. Pinned memories cost context on every turn and are capped at 10 rows or 2000 characters, whichever binds first.
_Avoid_: sticky memory, permanent context

**Kind**:
Memory classification: `fact` (objective information), `preference` (user likes/dislikes), or `summary` (compaction recap). The Kind determines how the memory is displayed and searched.
_Avoid_: type, category, tag

**Source**:
Memory origin: `user` (explicit remember tool call) or `compaction` (automatic summary persistence). Distinguishes deliberate storage from automatic context management.
_Avoid_: origin, creator

**What is NEVER stored**:
- Credentials (API keys, tokens, passwords)
- Transient state (current working directory, open file handles)
- Raw LLM responses (only validated summaries from compaction)

**Write Policy**:
- **Explicit**: User calls `remember()` tool
- **Automatic**: Compaction writes its summary before discarding turns
- **Guards**: Reject empty content, reject error strings; summaries replace (don't append)

**Read Policy**:
- **At startup**: Inject pinned memories via rules mechanism (max 10 rows / 2000 chars)
- **Every turn**: Retrieve top 3 relevant memories for the user's message
- **On demand**: User calls `search_memory()` tool

**Storage Split**:
- **SQLite** (`/workspace/.agent/memory.db`): Source of truth, runs locally, survives restarts, required
- **Qdrant** (compose service): Vector index, rebuildable, optional, degrades to FTS5 when unavailable
This split ensures the mandatory guarantee (store in one session, retrieve after restart) has no runtime dependency on Qdrant.


### Permission Management (Week 3)

**PermissionGate**:
A separate component that evaluates permission policies before tool execution. `ToolRegistry` receives a `PermissionGate` instance at construction and calls `gate.check(tool_name, args)` internally from the public `dispatch` method. The gate raises `PermissionDenied` if blocked; otherwise dispatch proceeds. This makes permission checks structurally enforced (impossible to bypass) while keeping the gate testable in isolation. The gate governs **all tools** (built-in + MCP), not just filesystem operations.
_Avoid_: permission checker, access control, authorization layer

**Permission Policy**:
A `permissions.yaml` file containing a `default` action and a list of `rules`. Format:
```yaml
permissions:
  default: ask
  rules:
    - { tools: [read_file, list_files], action: allow }
    - { tools: ["*delete*"], action: deny }
```
Rules are matched top-to-bottom; the first matching rule wins. Tool names support glob patterns (`*delete*` matches any tool containing "delete"). Sources in precedence order: runtime confirmation > workspace `.nikola/permissions.yaml` > project-root `./permissions.yaml` > global `~/.nikola/permissions.yaml` > built-in defaults. The first file that exists wins outright; policies are not merged. The workspace root itself is not searched — a file at `${WORKSPACE}/permissions.yaml` is ignored, and the loader warns when it finds one. CLI flag `--permissions <path>` replaces every file tier for that session.
_Avoid_: access policy, security config

**Action**:
The permission decision for a tool call: `allow` (proceed silently), `ask` (require user confirmation), or `deny` (reject with error). Actions are specified in permission rules and as the policy default. The `ask` action is only meaningful in REPL mode; in one-shot mode `ask` becomes `deny` (no interactive channel exists).
_Avoid_: policy outcome, decision, verdict

**Permission Rule**:
A single entry in the `rules` list. Each rule specifies a list of `tools` (with glob pattern support) and an `action`. Rules are evaluated top-to-bottom; **the first rule whose `tools` list matches the requested tool name determines the action**. If no rule matches, the `default` action applies.
_Avoid_: policy entry, access rule, filter

**Confirmation Channel**:
The mechanism for prompting the user when a tool call has `action: ask`. In REPL mode, the harness pauses and displays the tool name and arguments; the user types `y` or `n`. Confirmations granted (`y`) are cached as runtime `allow` overrides for that specific tool for the remainder of the session. In one-shot mode, `ask` is treated as `deny`.
_Avoid_: approval prompt, user prompt, interactive gate

**Built-in Permission Defaults**:
The fallback policy when no `permissions.yaml` file exists:
- `default: ask` — conservative baseline; unknown tools (including all MCP tools) require confirmation
- `allow`: read-only built-in tools (`read_file`, `list_files`, `search_text`, `glob`, `grep`)
- `allow`: sandbox-edit built-in tools (`create_folder`, `write_file`, `update_todo_list`) — contained by path guards, reversible
- `ask`: destructive built-in tools (`modify_file`, `search_and_replace`) — can mangle existing work with loose patterns
- `deny`: any tool matching `*delete*` or `*remove*` — explicitly excluded from the harness
MCP tools are NOT in the built-in allow list; they fall through to `default: ask`. Users must explicitly allow trusted MCP tools via workspace or global `permissions.yaml`.


### Sub-agents (Week 3) ✅ IMPLEMENTED

**Sub-agent**:
A separate harness instance spawned to handle a delegated task. The sub-agent runs with its own role configuration (system prompt, limits, permissions), executes independently, and returns a condensed summary to the parent. Sub-agents do NOT share context with the parent beyond the explicit task description passed at delegation time. **Sub-agents do NOT have access to the confirmation channel** — any tool with `action: ask` in the sub-agent's permission policy is **automatically denied with a clear error message** explaining that sub-agents cannot prompt users and suggesting role configuration fixes. The parent's observability system records sub-agent execution as part of the parent run.
_Avoid_: child agent, worker, subprocess

**Delegation**:
The act of spawning a sub-agent to handle a task. Exposed as a single tool `delegate(task, role)` where `task` is the work description and `role` selects the sub-agent configuration. The parent blocks until the sub-agent completes and returns its summary. Delegation is a tool like any other — it can be blocked by permission rules. Implemented in `agent/subagents/orchestrator.py::make_delegate_tool()`.
_Avoid_: spawn, fork, offload

**Role (Sub-agent)**:
A named sub-agent configuration that defines permissions, limits, and behavior. Each role defines its own **allowlist/denylist/asklist arrays** for tools. Built-in roles: `file-scout` (read-only research: read_file, list_files, search_text, glob, grep; 8 iterations, 2000-char result) and `scheduler` (calendar operations: MCP calendar tools, read tools; 10 iterations, 1500-char result). Roles are defined as Python factories in `agent/subagents/roles.py`. Sub-agents DO inherit the parent's workspace path — they operate on the same filesystem.
_Avoid_: sub-agent type, profile, persona

**PermissionPolicy (allowlist/denylist/asklist)**:
Core permission model implemented as three arrays:
- `allowlist`: Tools explicitly allowed (action: allow)
- `denylist`: Tools explicitly denied (action: deny)
- `asklist`: Tools requiring confirmation (action: ask)
- `default_action`: Fallback for unlisted tools
- `is_subagent`: Flag that controls auto-deny behavior

**get_action() precedence**: denylist > allowlist > asklist > default_action.

**Sub-agent auto-deny**: When `is_subagent=True`, tools in `asklist` return DENY instead of ASK. Error message: `"Tool 'X' requires confirmation, but sub-agents cannot prompt the user. Update role configuration to use 'allow' or 'deny'."` This forces explicit role design and prevents silent failures.

**Built-in Roles** (Week 3 requirement: ≥2 specialized configurations):
1. **file-scout**: Read-only research agent. Allowlist: `[read_file, list_files, search_text, glob, grep]`. Denylist: `[write_file, modify_file, create_folder, execute_command, delegate, remember]`. Limits: 8 iterations, 2000 chars. Use case: searches many files and returns digest.
2. **scheduler**: Calendar operations. Allowlist: `[mcp_calendar__*, read_file, list_files]`. Denylist: `[write_file, execute_command, delegate]`. Limits: 10 iterations, 1500 chars. Use case: manages calendar events via MCP server.

**Delegation Contract**:
Parent provides: `task` (string), `role` (string). Sub-agent returns: condensed summary (string, max `role.max_result_chars`). The sub-agent's full conversation history is NOT returned — only the final summary. Parent appends summary as tool observation. Implemented in `agent/subagents/orchestrator.py::execute_subagent()`.
_Avoid_: sub-agent API, delegation protocol


_Avoid_: default policy, base permissions


### Observability (Week 3)

**Lifecycle Hook**:
A callback invoked at specific points in the harness execution. Hooks are the primary observability insertion point — all metrics collection and structured logging happens through hooks, not ad-hoc per-component logging. Hooks receive context about the event (e.g., tool name, latency, outcome) and publish metrics to Prometheus or structured logs. Week 3 implements five hooks: `on_turn_end`, `on_llm_response`, `on_tool_result`, `on_permission_check`, `on_delegation_end`.
_Avoid_: callback, event handler, interceptor

**HookHandler**:
A Protocol (typing.Protocol) defining the hook interface. The loop and other harness components receive a `hooks: HookHandler` parameter and call methods like `hooks.on_turn_end(...)` at appropriate points. The observability module implements this protocol (e.g., `PrometheusHookHandler`). This decouples the harness from Prometheus — the loop doesn't import observability; it only depends on the protocol. Test code can inject a mock `HookHandler` for isolated testing.
_Avoid_: hook interface, observer interface, event bus

**on_turn_end**:
Hook fired when a Turn completes (final answer returned or loop guard fired). Receives: turn latency, message count, outcome (success | guard_fired | error). Used to record turn-level metrics: throughput, success rate, latency distribution.
_Avoid_: turn_completed, after_turn

**on_llm_response**:
Hook fired after each LLM call returns (streaming or non-streaming). Receives: prompt tokens, completion tokens, model name, latency. Used to track token usage (per-turn and cumulative) and LLM latency.
_Avoid_: llm_called, after_llm

**on_tool_result**:
Hook fired after each tool invocation completes (success or error). Receives: tool name, arguments (sanitized — no secrets), latency, outcome (success | error), result length. Used to track tool usage frequency, tool-specific latency, and success/failure rates. Includes both built-in and MCP tools.
_Avoid_: tool_executed, after_tool

**on_permission_check**:
Hook fired after the PermissionGate evaluates a tool call. Receives: tool name, action from policy (allow | ask | deny), final outcome (allowed | denied | user_approved | user_rejected). Used to track permission decisions and user confirmation rates.
_Avoid_: permission_evaluated, access_check

**on_delegation_end**:
Hook fired when a sub-agent completes and returns its summary. Receives: role name, task description length, sub-agent latency, summary length, outcome (success | error | guard_fired). Used to track sub-agent activity: delegation frequency, role distribution, delegation latency.
_Avoid_: sub_agent_completed, delegation_finished

**Prometheus Scrape Endpoint**:
An HTTP endpoint (default: `http://localhost:9090/metrics`) that exposes Prometheus-compatible metrics collected via hooks. The harness runs a small HTTP server (separate thread) that serves the `/metrics` endpoint. Prometheus scrapes this endpoint at a configured interval (e.g., every 15s) to collect time-series data.
_Avoid_: metrics endpoint, stats endpoint

**Dashboard**:
A Prometheus + Grafana visualization showing aggregated harness metrics: agent throughput (turns/min), active runs, turn latency (p50/p95/p99), success/failure rates, LLM token usage (total and per-turn), tool usage frequency (built-in + MCP), permission decision breakdown (allow/deny/ask), sub-agent activity (delegations/min, role distribution). The dashboard queries Prometheus; Prometheus scrapes the harness's metrics endpoint.
_Avoid_: monitoring UI, metrics UI







