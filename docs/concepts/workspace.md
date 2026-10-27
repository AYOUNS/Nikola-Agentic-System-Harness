# Workspace Sandbox

The agent runs inside a **container**, but that alone is not enough — a container still
has a full filesystem. The workspace sandbox adds a second layer: every filesystem tool
call is validated against a single root directory before it runs. Anything outside that
root is rejected, even if the container would technically permit it.

This matters because:

- The agent can write code and files on your behalf. Without a boundary, a prompt
  injection or a hallucinated path could touch files outside your project directory.
- The delete operation does not exist at all — it is not registered in the ToolRegistry
  and cannot be unlocked by any permission rule.
- Path escapes (`../../../etc/passwd`, absolute host paths) are caught before any tool
  runs and returned to the agent as an `Observation` error, not a silent failure.

---

## Quick reference

| I want to… | What to change | CLI to verify |
|---|---|---|
| Change the workspace root | `workspace:` in `config.yaml` or `AGENT_WORKSPACE` env var | `/context` |
| Add a persistent instruction | Create `.nikola/rules/my-rule.md` | `/rules` |
| Add a loadable skill | Create `.nikola/skills/my-skill.md` | `/skills list` |
| Connect an MCP server | Add entry to `.nikola/mcp.json` | `/mcp` |
| Allow an MCP tool silently | Add `allow` rule in `.nikola/permissions.yaml` | `/permissions status` |
| Disable a rule | Set `enabled: false` in `.nikola/rules/<file>.md` | `/rules` |
| Change default tool action | Edit `default:` in `.nikola/permissions.yaml` | `/permissions status` |

---

## How the sandbox root is resolved

The workspace root is resolved **once at startup**, in this precedence order:

| Source | How | Example |
|---|---|---|
| `AGENT_WORKSPACE` env var | Overrides everything (12-factor) | `export AGENT_WORKSPACE=$(pwd)/workspace` |
| `config.yaml` → `workspace` | Config file value | `workspace: /workspace` |
| Built-in default | Container default | `/workspace` |

### Default in `config.yaml`

```yaml
# config.yaml
workspace: /workspace
```

This is the path the agent sees inside the container. When you mount a host directory
with `-v $(pwd)/workspace:/workspace`, the agent writes there.

### Override for a local run

```bash
export AGENT_WORKSPACE=$(pwd)/my-project
python -m agent.cli
```

The code that reads these two sources:

```python
# agent/tools/filesystem.py — get_workspace_root()
workspace_env = os.environ.get("AGENT_WORKSPACE")
if workspace_env:
    return Path(workspace_env).resolve()
return Path(config_workspace).resolve()
```

---

## Path safety — how escapes are blocked

Every filesystem tool call passes through `resolve_safe_path()` before anything touches disk.

```python
def resolve_safe_path(rel_path: str) -> Path:
    root = get_workspace_root()
    target = (root / rel_path).resolve()   # resolve symlinks, ../ etc.
    if not (target == root or root in target.parents):
        raise PermissionError(
            f"Access denied: '{rel_path}' escapes workspace sandbox '{root}'"
        )
    return target
```

| Input | What happens |
|---|---|
| `notes/todo.md` | Resolves inside root → ✅ allowed |
| `../../etc/passwd` | Resolves outside root → `PermissionError` |
| `/etc/passwd` | Absolute path not under root → `PermissionError` |
| `symlink-to-outside` | Symlink target resolves outside root → `PermissionError` |

The error is returned to the agent as an Observation — the loop reads it and
reports to the user rather than silently failing.

---

## What the agent can and cannot do

| Operation | Allowed |
|---|---|
| Create folder | ✅ |
| Write / create file | ✅ |
| Read file | ✅ |
| Modify file | ✅ |
| List / navigate folder | ✅ |
| Search in files (`grep`, `glob`, `search_text`) | ✅ |
| **Delete file or folder** | ❌ — no tool registered, cannot be unlocked |
| **Access paths outside workspace** | ❌ — path guard at every call |

Delete is not a policy toggle — the tool simply does not exist in the ToolRegistry.

---

## The `.nikola/` subtree

The harness writes its own configuration and state inside a `.nikola/` directory at the
workspace root. The long-term memory database is the one exception — it sits at whatever
`config.memory.db_path` names, which ships as `/workspace/.agent/memory.db`.

```
workspace/
├── .agent/
│   └── memory.db           ← SQLite long-term memory (config.memory.db_path)
└── .nikola/
    ├── permissions.yaml       ← workspace-level permission rules (optional)
    ├── logs/
    │   └── agent.jsonl        ← structured JSON log (tailed by Alloy → Loki)
    ├── rules/                 ← persistent instructions appended to every system prompt
    │   ├── auto-memory.md     ← default: proactive memory storage behaviour
    │   ├── memory.md          ← default: memory usage guidelines
    │   └── example.md.example ← rename to .md to activate a new rule
    ├── skills/                ← user-defined skills (loaded on demand)
    │   ├── mensa-passau/
    │   │   └── SKILL.md
    │   ├── dhl-tracking/
    │   │   └── SKILL.md
    │   └── outfit-advisor/
    │       └── SKILL.md
    ├── mcp.json               ← MCP server declarations
    └── README.md              ← what the harness keeps in here
```

!!! note "`.agent/` vs `.nikola/.memory/`"
    `compose.yaml` points the `sqlite-web` browser at `.nikola/.memory/memories.db`, which
    does not match the shipped `db_path` — see the warning under
    [Memory → Inspecting memories](memory.md#inspecting-memories).

### Sub-agents and the workspace

Sub-agents **inherit** the parent's workspace root — they operate on the same filesystem
under the same path guard. A sub-agent cannot escape the workspace any more than the
parent can.

!!! note "Sub-agents read /workspace, not the repository source"
    Point tasks at `.nikola/skills`, `.nikola/rules`, or files created earlier in the
    session — not at `agent/`, `tests/`, or other repo directories that are not mounted
    into the container workspace.

---

## Related pages

| Topic | Where to read |
|---|---|
| Always-active system prompt instructions | [Rules](rules.md) |
| On-demand domain instructions | [Skills](skills.md) |
| Which tools the agent may call | [Permissions](permissions.md) |
| Connecting external MCP servers | [MCP Integration](mcp.md) |
| Memory database layout | [Long-term Memory](memory.md) |
