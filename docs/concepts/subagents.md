# Sub-agents

A **sub-agent** is a separate harness instance spawned to handle a delegated task. It runs
with its own role configuration, executes independently, and returns a condensed summary
to the parent. Sub-agents do not share context with the parent beyond what is explicitly
passed at delegation time.

---

## Delegation tools

### `delegate`

```
delegate(task: str, role: str) -> str
```

Spawn one sub-agent synchronously. Blocks until the sub-agent completes and returns its
summary string.

### `delegate_parallel`

```
delegate_parallel(tasks: list[{task: str, role: str}]) -> str
```

Spawn multiple sub-agents concurrently. All run simultaneously; wall time is the slowest
task, not the sum of all tasks. The parent receives a combined summary when the last one
finishes.

While sub-agents run, **live progress cards** appear in the terminal showing each role's
current tool and counters. The parent's context grows by only the final summaries —
never by the sub-agents' full conversation histories.

---

## Delegation contract

| Direction | What is passed |
|---|---|
| Parent → sub-agent | `task` string + `role` name |
| Sub-agent → parent | Condensed summary (max `role.max_result_chars`) |

The sub-agent's full conversation history is discarded after it finishes. Only the summary
travels back to the parent as a tool Observation.

---

## Built-in roles

Three roles are pre-configured in `agent/subagents/roles.py`:

### `file-scout`

Read-only research. Searches many files and returns a digest.

| Property | Value |
|---|---|
| Allowed tools | `read_file`, `list_files`, `search_text`, `glob`, `grep` |
| Denied tools | `write_file`, `modify_file`, `create_folder`, `execute_command`, `delegate`, `remember` |
| Max iterations | 8 |
| Max result length | 2 000 characters |

### `scheduler`

Calendar operations via the MCP calendar server.

| Property | Value |
|---|---|
| Allowed tools | `mcp_calendar__*`, `read_file`, `list_files` |
| Denied tools | `write_file`, `execute_command`, `delegate` |
| Max iterations | 10 |
| Max result length | 1 500 characters |

!!! warning "Scheduler requires an explicit allow rule"
    MCP tools fall through to `default: ask`, and `ask` is auto-denied inside a sub-agent.
    Add `- tool: "mcp_calendar__*" action: allow` to `permissions.yaml` for the `scheduler`
    role to function.

### `general`

General-purpose research and explanation. Same read access as `file-scout` but with a
larger iteration and result budget.

| Property | Value |
|---|---|
| Allowed tools | `read_file`, `list_files`, `search_text`, `glob`, `grep` |
| Denied tools | `write_file`, `execute_command`, `delegate` |
| Max iterations | 15 |
| Max result length | 4 000 characters |

---

## Auto-deny for `ask` tools

Sub-agents have no terminal to prompt the user. Any tool with `action: ask` in the
policy is **automatically denied** when called from a sub-agent.

Error message returned to the LLM:

```
Tool '<name>' requires confirmation, but sub-agents cannot prompt the user.
Update role configuration to use 'allow' or 'deny'.
```

To fix: add an explicit `allow` rule for that tool in `permissions.yaml`, or exclude
the tool from the role's allowlist.

See [ADR 0002 — Sub-agent Auto-deny](../decisions/0002-subagent-autodeny.md) for the rationale.

---

## Workspace inheritance

Sub-agents inherit the parent's workspace root — they operate on the same filesystem.
They can read any file the parent can read. Write access is controlled by the role's
tool allowlist (most roles deny write tools entirely).

!!! note "Sub-agents read /workspace, not the repository source"
    Point tasks at `.nikola/skills`, `.nikola/rules`, or files created earlier in the
    session — not at `agent/`, `tests/`, or other repo directories that are not mounted
    into the container workspace.

---

## Observability

Sub-agent activity appears in both metrics and logs:

```bash
# Prometheus: runs per role
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'

# Structured log: sub-agent completions
jq 'select(.event_type == "subagent_end")' workspace/.nikola/logs/agent.jsonl

# Tie a sub-agent back to its parent
jq 'select(.parent_session == "<parent-session-id>")' workspace/.nikola/logs/agent.jsonl
```

The Grafana dashboard shows **Sub-agent runs by role**, **Active sub-agents**, and
**Sub-agent latency** panels.
