# Permissions

The permission system controls which tools the agent may use, which require user confirmation,
and which are always blocked. It applies to **all tools** — built-in, MCP, and delegation tools.

---

## Components

### PermissionGate

`PermissionGate` sits between the ToolRegistry and every tool call. Before any tool runs,
the gate evaluates the current policy and returns one of three actions.

The gate is injected into the ToolRegistry at construction — it is structurally impossible
to bypass, even for tests.

### PermissionPolicy

Loaded from `permissions.yaml`. Contains a `default` action and a list of `rules`.
Rules are evaluated **top-to-bottom; the first match wins**.

---

## Actions

| Action | Effect |
|---|---|
| `allow` | Tool runs immediately, no prompt. |
| `deny` | Tool is blocked. The agent receives `"Tool '<name>' is denied by a permissions.yaml rule."` plus a do-not-retry directive. No prompt appears. |
| `ask` | User is prompted with a four-option menu (Yes · Yes, don't ask again · No · No, never ask). An `ask` rule re-prompts on **every** call — the session cache is deliberately bypassed, because require-user-confirmation means every invocation. To stop prompting, move the tool to an `allow` rule. |

### Mode-specific behaviour

| Context | `ask` becomes |
|---|---|
| REPL mode | Pauses; prompts user |
| One-shot mode (`--prompt`) | Still prompts — one-shot uses the same confirmation channel as the REPL. With non-interactive stdin (piped input, CI) the menu reads EOF and the call is denied. |
| Sub-agent | `deny` — see [ADR 0002](../decisions/0002-subagent-autodeny.md) |

---

## Rule evaluation

```
┌──────────────────────────────────────────┐
│  permissions.yaml                        │
│  default: ask                            │
│  rules:                                  │
│    1. tool: read_file    → allow  ←─ first match for read_file wins │
│    2. tool: write_file   → ask    ←─ checked only if rule 1 didn't match │
│    3. tool: "*.env"      → deny   │
│    4. default: ask       ←─ fallback when no rule matches │
└──────────────────────────────────────────┘
```

### Rule fields

| Field | Required | Description |
|---|---|---|
| `tool` | Yes | Tool name or glob pattern (e.g. `mcp_calendar__*`, `*delete*`) |
| `action` | Yes | `allow`, `deny`, or `ask` |
| `path_pattern` | No | Shell glob matched against the tool's `path` argument |

`path_pattern` is only meaningful for tools that take a `path` argument. It lets you write
rules like "deny `write_file` when the path matches `.env*`" while still allowing `write_file`
to other paths via a later rule.

---

## Policy sources and precedence

| Source | Location | Precedence |
|---|---|---|
| Runtime session cache | In-memory (per session) | Highest — "Yes, don't ask again" at a prompt |
| CLI override | `--permissions <path>` flag | Replaces every file tier below for one session |
| Workspace policy | `${WORKSPACE}/.nikola/permissions.yaml` | Second |
| Project-root policy | `./permissions.yaml` (baked into the image at `/app`) | Third |
| Global policy | `~/.nikola/permissions.yaml` | Fourth |
| Built-in defaults | Hardcoded in `agent/permissions/` | Fallback when no file exists |

The first file that exists wins outright — policies are **not** merged, so the workspace file
replaces the project-root one rather than layering on top of it.

!!! warning "The workspace root is not searched"
    Only `.nikola/permissions.yaml` counts as the workspace policy. A file at
    `${WORKSPACE}/permissions.yaml` is **ignored**, and the agent silently falls through to the
    project-root policy — so the loader logs a warning naming both paths when it finds one there.

### Built-in defaults

Applied when no `permissions.yaml` exists:

| Tools | Default action |
|---|---|
| `read_file`, `list_files`, `search_text`, `glob`, `grep`, `search_memory` | `allow` |
| `write_file`, `create_file`, `modify_file`, `search_and_replace`, `create_folder`, `remember` | `ask` |
| `execute_command` | `deny` |
| All MCP tools | `ask` (via `default: ask`) |
| Any tool matching `*delete*` or `*remove*` | `deny` |

---

## Confirmation channel

When a tool's action is `ask`, the harness pauses the loop and displays:

```
Tool request: write_file
  path: output/notes.md
Allow? [y/n/a (always)] _
```

| Response | Effect |
|---|---|
| `y` | Allow this call; cache `allow` for this tool for the duration of the session |
| `a` | Same as `y` — allow this call and cache for the session |
| `n` | Deny this call; the agent receives an error Observation and continues |

---

## Session cache

After a user answers `y` or `a` at a prompt, the tool name is added to the session-level
`allow` cache. Subsequent calls to that tool skip the prompt for the rest of the session.
The cache is in-memory only — it resets when the session ends.

Check the cache at any time:

```
/permissions status
```

---

## Example `permissions.yaml`

```yaml
default: ask

rules:
  # Read-only tools: always allow
  - tool: read_file
    action: allow
  - tool: list_files
    action: allow
  - tool: search_text
    action: allow
  - tool: glob
    action: allow
  - tool: grep
    action: allow
  - tool: search_memory
    action: allow

  # Sensitive paths: deny writes (must be BEFORE the generic write_file rule)
  - tool: write_file
    path_pattern: ".env*"
    action: deny
  - tool: write_file
    path_pattern: "*secret*"
    action: deny

  # Generic write tools: require confirmation
  - tool: write_file
    action: ask
  - tool: modify_file
    action: ask
  - tool: create_folder
    action: ask

  # Delegation: allow
  - tool: delegate
    action: allow
  - tool: delegate_parallel
    action: allow

  # Shell execution: always deny
  - tool: execute_command
    action: deny

  # Explicitly allow a trusted MCP server
  # - tool: "mcp_calendar__*"
  #   action: allow
```
