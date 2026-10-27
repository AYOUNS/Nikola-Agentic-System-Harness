# Configuration

The agent has three configuration surfaces:

| File | What it controls |
|---|---|
| `config.yaml` | LLM settings, loop guards, context budget, workspace path, logging, metrics, memory |
| `.nikola/permissions.yaml` | Which tools the agent may run, prompt for, or is denied |
| `.nikola/mcp.json` | MCP servers to connect at startup |

Environment variables override specific `config.yaml` values (12-factor pattern).

---

## `config.yaml`

Located at the repo root. All keys are optional — the table below shows defaults and expected types.

### `llm.*`

```yaml
llm:
  model: qwen-agentworld-35b-a3b       # Active inference model
  embedding_model: octen-embedding-8b  # Used for memory embeddings
  base_url: https://llms.innkube.fim.uni-passau.de/
  temperature: 0.7
  timeout_seconds: 60
```

| Key | Type | Default | Description |
|---|---|---|---|
| `model` | string | `qwen-agentworld-35b-a3b` | Model for all inference calls. Switch to `gemma4-31b-it` with no code changes. |
| `embedding_model` | string | `octen-embedding-8b` | Model used to embed memories for semantic search. |
| `base_url` | string | InnKube endpoint | Base URL for the OpenAI-compatible API. Overridden by `OPENAI_BASE_URL`. |
| `temperature` | float | `0.7` | Sampling temperature. |
| `timeout_seconds` | int | `60` | Hard timeout per LLM API call. |

### `loop.*`

```yaml
loop:
  max_iterations: 100
  max_tokens: 8192
```

| Key | Type | Default | Description |
|---|---|---|---|
| `max_iterations` | int | `100` | Maximum reasoning/acting steps per run. Guards against infinite loops. |
| `max_tokens` | int | `8192` | Maximum tokens in the LLM completion. Must be ≤ `context.reserve_output_tokens`. |

### `context.*`

```yaml
context:
  window_tokens: 262144
  reserve_output_tokens: 8192
  chars_per_token: 3.5
  max_observation_chars: 10000
  summary_max_tokens: 1500
  summary_reasoning_effort: none
```

| Key | Type | Default | Description |
|---|---|---|---|
| `window_tokens` | int | `262144` | Total context window in tokens. Overrides model auto-detection (InnKube gateway does not advertise window size). `262144` = 256 K — native window for `qwen-agentworld-35b-a3b`. |
| `reserve_output_tokens` | int | `8192` | Tokens reserved for the output completion. Must be ≥ `loop.max_tokens`. |
| `chars_per_token` | float | `3.5` | Conservative char-to-token ratio used for budget estimation (Qwen / Gemma). |
| `max_observation_chars` | int | `10000` | Tool observations longer than this are truncated before being added to context. |
| `summary_max_tokens` | int | `1500` | Completion cap for the context compaction summary. |
| `summary_reasoning_effort` | string | `none` | Reasoning budget for the summary call. Set to `""` if your provider rejects the parameter. |

### `workspace`

```yaml
workspace: /workspace
```

Absolute path to the sandbox root inside the container. Overridden by the `AGENT_WORKSPACE`
environment variable. The agent rejects any file path that escapes this root — see
[Workspace Sandbox](../concepts/workspace.md).

### `logging.*`

```yaml
logging:
  enabled: true
  debug: true
  file:            # null = <workspace>/.nikola/logs/agent.jsonl
```

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable structured observability logging via hooks. |
| `debug` | bool | `true` | Enable debug-level records and entry/exit tracking. |
| `file` | string \| null | `null` | Log file path. When null, defaults to `<workspace>/.nikola/logs/agent.jsonl`. Rotates at 5 MB, keeping 3 copies. |

Logs are written as JSON lines so the terminal shows only the conversation transcript.
Read them with `jq`:

```bash
jq 'select(.level == "ERROR")' workspace/.nikola/logs/agent.jsonl
tail -f workspace/.nikola/logs/agent.jsonl | jq .
```

### `metrics.*`

```yaml
metrics:
  enabled: true
  host: 0.0.0.0
  port: 9464
```

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Expose the Prometheus scrape endpoint. |
| `host` | string | `0.0.0.0` | Bind address for the `/metrics` HTTP server. Inside the container this is the container interface only — the port is mapped to `127.0.0.1:9464` on the host by Compose. |
| `port` | int | `9464` | Port for the `/metrics` HTTP server. Prometheus scrapes `agent:9464` every 5 s. |

### `memory.*`

```yaml
memory:
  db_path: /workspace/.agent/memory.db
  qdrant:
    url: http://qdrant:6333
    collection: agent_memories
```

| Key | Type | Default | Description |
|---|---|---|---|
| `db_path` | string | `/workspace/.agent/memory.db` | SQLite database for persistent memories. Survives container restarts because the workspace volume is mounted from the host. Note that `compose.yaml` points the `sqlite-web` browser at `/data/.nikola/.memory/memories.db` instead — see the warning in [Memory](../concepts/memory.md#inspecting-memories). |
| `qdrant.url` | string | `http://qdrant:6333` | Qdrant REST endpoint for semantic search. If unreachable, the harness degrades to SQLite FTS5. |
| `qdrant.collection` | string | `agent_memories` | Qdrant collection name for memory embeddings. |

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `INNKUBE_ACCESS_TOKEN` | **Yes** | API token for the InnKube LLM gateway. Never commit this. Expires Sep 30. |
| `AGENT_WORKSPACE` | No | Overrides `config.yaml workspace`. Default: `./workspace`. |
| `OPENAI_BASE_URL` | No | Overrides `config.yaml llm.base_url`. Useful for pointing at a local model. |

Export in every shell you use:

```bash
export INNKUBE_ACCESS_TOKEN=<your_token>
export AGENT_WORKSPACE="./workspace"
```

---

## `permissions.yaml`

Lives at `${WORKSPACE}/.nikola/permissions.yaml`, alongside `mcp.json` and `rules/`. Controls
which tools the agent may run without asking, which require confirmation, and which are always
blocked. A copy at the repo root acts as the fallback policy baked into the image; see
[Permissions → Policy sources](../concepts/permissions.md#policy-sources-and-precedence) for
the full search order.

Rules are evaluated **top-to-bottom; the first match wins**.

```yaml
default: ask   # fallback when no rule matches

rules:
  # Always allow read-only tools
  - tool: read_file
    action: allow

  # Deny writes to sensitive paths (must appear BEFORE generic write_file rule)
  - tool: write_file
    path_pattern: ".env*"
    action: deny

  # Require confirmation for all other writes
  - tool: write_file
    action: ask

  # Always block shell execution
  - tool: execute_command
    action: deny
```

### Actions

| Action | Behaviour |
|---|---|
| `allow` | Tool runs immediately. A user can also grant session-level allow by answering "Yes" at a prompt. |
| `deny` | Tool is blocked unconditionally. The agent receives an error message. No user prompt appears. |
| `ask` | User is prompted on every call. Answer `y` to allow once, `a` to allow for the rest of the session. |

### Rule fields

| Field | Required | Description |
|---|---|---|
| `tool` | Yes | Tool name or glob pattern (e.g. `mcp_calendar__*`). |
| `action` | Yes | `allow`, `deny`, or `ask`. |
| `path_pattern` | No | Shell glob matched against the `path` argument of the tool call. Only meaningful for filesystem tools. |

!!! note "Rule ordering matters"
    Path-specific rules must appear **before** generic tool rules, because evaluation stops at the
    first match. In the example above, `.env*` is denied even though `write_file` generically is
    only `ask`.

Full reference → [Permissions](../concepts/permissions.md)

---

## `.nikola/mcp.json`

Located at `<workspace>/.nikola/mcp.json`. Declares MCP servers to connect at session start.
The format follows the Claude Desktop `mcp.json` schema.

```json
{
  "mcpServers": {
    "calendar": {
      "type": "http",
      "url": "http://mcp_calendar:3000/mcp",
      "description": "Calendar management — schedule events, find free slots"
    },
    "chrome-devtools": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest", "--slim", "--headless"],
      "description": "Browser automation and page inspection"
    }
  }
}
```

### Server entry fields

| Field | Required | Description |
|---|---|---|
| `type` | Yes | `http` for HTTP/SSE transport; `stdio` for subprocess transport. |
| `url` | For `http` | Base URL of the HTTP MCP server (e.g. `http://mcp_calendar:3000/mcp`). |
| `command` | For `stdio` | Executable to launch as a subprocess. |
| `args` | For `stdio` | Command-line arguments passed to the subprocess. |
| `env` | No | Extra environment variables for the subprocess. Supports `${VAR}` substitution. |
| `description` | No | Human-readable description (not sent to the LLM). |

Once connected, each server's tools are registered in the ToolRegistry as
`mcp_<server-name>__<tool-name>` and treated identically to built-in tools for the purposes
of permission checking.

!!! warning "Secrets in mcp.json"
    Use `${ENV_VAR}` substitution for API keys rather than hardcoding them:
    ```json
    "env": { "API_KEY": "${MY_SERVICE_API_KEY}" }
    ```
    Never commit a `mcp.json` that contains live credentials.
