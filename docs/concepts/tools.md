# Tools & Registry

A **Tool** is a deterministic, schema-defined capability exposed to the LLM. Every tool call
goes through the **ToolRegistry** — the LLM never calls a tool directly.

---

## ToolRegistry

`ToolRegistry` is the single dispatch point for all tool invocations.

```python
registry.dispatch(tool_name: str, arguments: dict) -> str
```

The loop extracts `tool_name` and `arguments` from the LLM's `tool_calls` response and passes
them here. The registry looks up the handler, calls it, and returns the result string.

**All tools return a plain string.** On success: the result. On failure: `"Error: <reason>"`.
Tools never raise exceptions to the loop — errors become Observations the LLM reasons about.

Before dispatching, the registry passes the call through the **PermissionGate**. If the gate
blocks the call, the registry returns an error Observation immediately without running the tool.

---

## Built-in filesystem tools

All filesystem tools are restricted to the [Workspace Sandbox](workspace.md).
Paths that escape the sandbox (via `../` traversal or absolute paths outside the root)
are rejected with `"Error: Access denied"`.

**Delete is absent by design** — no tool in the registry can remove a file or folder.
This is structurally enforced in `agent/tools/filesystem.py`, not in permissions.

| Tool | Arguments | Description |
|---|---|---|
| `create_folder` | `path` | Create a directory (and parents) inside the workspace |
| `write_file` | `path`, `content` | Write (or overwrite) a file |
| `read_file` | `path` | Read a file's full content |
| `modify_file` | `path`, `old_string`, `new_string` | Replace first occurrence of `old_string` |
| `search_and_replace` | `path`, `old_string`, `new_string` | Replace all occurrences of `old_string` |
| `list_files` | `path` (default `.`) | List directory contents recursively |
| `search_text` | `pattern`, `path` (default `.`) | Full-text search across files |
| `glob` | `pattern`, `path` (default `.`) | Find files by name pattern |
| `grep` | `pattern`, `path`, `flags` | Regex search across file contents |
| `update_todo_list` | `todos` | Write a markdown task list to `.nikola/todos.md` |

---

## Built-in execution tool

| Tool | Description |
|---|---|
| `execute_command` | Run a shell command inside the workspace sandbox |

`execute_command` is **denied** by default in `permissions.yaml`. It is sandboxed to the
workspace root and has a command screener that rejects certain dangerous patterns, but it
requires an explicit `allow` rule to be usable. Most setups leave it denied.

---

## Memory tools

| Tool | Arguments | Description |
|---|---|---|
| `remember` | `content`, `kind`, `pinned` | Store a memory in long-term storage |
| `search_memory` | `query` | Search memories by keyword or semantic similarity |

See [Long-term Memory](memory.md) for the full memory model.

---

## Skills tool

| Tool | Arguments | Description |
|---|---|---|
| `load_skill` | `name` | Load a skill by name, injecting its body into the conversation as a system message |

`load_skill` is also accessible via the slash command `/skills load <name>`. Skills discovered
at session start are shown by `/skills list`.

See [Skills](skills.md) for the discovery model and how to write your own.

---

## Delegation tools

| Tool | Arguments | Description |
|---|---|---|
| `delegate` | `task`, `role` | Spawn a sub-agent to handle a task; blocks until complete |
| `delegate_parallel` | `tasks` (list of `{task, role}`) | Spawn multiple sub-agents concurrently |

See [Sub-agents](subagents.md) for the delegation model and built-in roles.

---

## MCP tools

MCP tools are registered at Session start when MCP servers connect. They are named:

```
mcp_<server-name>__<tool-name>
```

For example, the calendar server's `create_event` tool is registered as
`mcp_calendar__create_event`. MCP tools are treated identically to built-in tools for
permission checking — they match rules by name or glob (e.g. `mcp_calendar__*`).

MCP tools that match no rule fall through to `default: ask`.

---

## Tool schema

Every tool exposes a JSON Schema that the LLM sees as part of the context. The schema is
defined alongside the Python handler using a `FilesystemTool` TypedDict. Example shape:

```json
{
  "name": "read_file",
  "description": "Read the full content of a file inside the workspace.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "Relative path inside the workspace"
      }
    },
    "required": ["path"]
  }
}
```

Tools are designed to be **token-efficient** — descriptions are concise and non-overlapping
so the LLM makes clear choices without long disambiguation text.

---

## Observation

An Observation is the string result of a tool call, appended to `messages` as a
`{"role": "tool", …}` entry after each Step that includes an Action. On tool failure the
Observation is the error string; the LLM reads it and can reason about retrying or reporting
to the user.
