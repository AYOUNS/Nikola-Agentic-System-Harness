# Agent Architecture: Subdirectory Reference

!!! danger "Out of date — not published"
    This file predates the current `agent/` layout and is excluded from the site via
    `exclude_docs` in `mkdocs.yml`. Known drift: the `agent/permissions/` section below
    describes `policy.py`, `gate.py`, `rules.py` and `cache.py` with classes
    (`PermissionGate`, `PermissionCache`, `RuleEngine`, `make_researcher_policy`) that do
    not exist — that package is a single `module.py`. The slash-command list names
    `/reset`, `/export` and `/memory`, none of which are registered.

    For accurate, verified coverage use
    [System Overview](architecture/overview.md) and the [Concepts](concepts/tools.md)
    pages. Either rewrite this file against the code or delete it.


This document provides detailed descriptions of each subdirectory in the `agent/` package, explaining their purpose, components, and how they fit into the overall ReAct agent harness architecture.

---

## Overview

The agent harness is a Python 3.12 CLI application that implements a ReAct (Reasoning + Acting) loop with LLM-powered autonomous task execution. The architecture follows a modular design with clear separation of concerns across multiple subdirectories.

```
agent/
├── cli/           Command-line interface and REPL
├── context/       Token budget and context management
├── hooks/         Observability lifecycle hooks
├── llm/           LLM client and dispatcher layer
├── mcp/           Model Context Protocol integration
├── memory/        Long-term memory persistence
├── permissions/   Permission management system
├── prompts/       System prompts and role definitions
├── skills/        Loadable instruction bundles
├── subagents/     Sub-agent delegation system
└── tools/         Tool registry and built-in tools
```

---

## 1. `agent/cli/` — Command-Line Interface

**Purpose**: Provides the primary user interface for interacting with the agent harness through terminal commands, REPL mode, and one-shot prompts.

### Key Components

- **`__init__.py`** - Main entry point (`app` Typer application)
  - Initializes all subsystems (LLM client, tool registry, MCP, memory, permissions)
  - Configures structured JSON logging to `.nikola/logs/agent.jsonl`
  - Orchestrates session lifecycle and cleanup
  - Command-line flags: `--config`, `--model`, `--prompt`, `--mode`, `--stream`, `--thinking`, `--doctor`

- **`repl.py`** - Interactive REPL runner
  - Routes user input to slash commands or turn processing
  - Manages session state through `Session` object
  - Integrates Tab completion and command history
  - Delegates presentation to `ConsoleUI`

- **`session.py`** - Session state management
  - Maintains conversation history (list of `ChatCompletionMessageParam`)
  - Loads system prompts from `agent/prompts/<mode>.md`
  - Tracks context budget and triggers compaction when needed
  - Provides session persistence and restoration

- **`ui.py`** - Console UI rendering
  - Styled output with Rich library
  - Streaming token display with spinner
  - Tool call visualization
  - User confirmation dialogs for permission requests
  - Error and warning formatting

- **`completion.py`** - Tab completion support
  - Command completion for slash commands
  - File path completion for tool arguments
  - Self-test diagnostics (`--doctor` flag)

- **`commands/`** - Slash command implementations
  - `/help`, `/reset`, `/export`, `/memory`, `/permissions`, `/mcp`, etc.
  - Command registry pattern for extensibility

### Integration Points

- Calls `agent.loop.make_process_turn_fn()` to get turn processor
- Uses `agent.config.load_config()` for YAML configuration
- Integrates with all other subsystems as orchestration layer

---

## 2. `agent/context/` — Context Management

**Purpose**: Manages token budgets and context window compaction to prevent the conversation from exceeding the LLM's maximum context length.

### Key Components

- **`budget.py`** - Token estimation and budget tracking
  - `estimate_tokens()` - Approximates token count for messages
  - `is_within_budget()` - Checks if messages fit within context window
  - `get_budget_info()` - Returns budget statistics for monitoring

- **`compaction.py`** - Context compaction strategies
  - `compact()` - Main entry point for context compaction
  - `summarize_messages()` - Creates LLM-generated summary of dropped messages
  - `replace_dropped_with_summary()` - Replaces old messages with summary
  - `find_summary_index()` - Locates existing summary messages
  - `summary_message()` - Creates properly formatted summary message

- **`rules.py`** - Compaction rules and policies
  - Defines which messages can be dropped (never drop system prompt)
  - Determines compaction trigger thresholds
  - Manages summary placement in message history

### Behavior

When the conversation approaches the token budget:
1. Calculate current token usage with `estimate_tokens()`
2. If over threshold, call `compact()` with LLM dispatcher
3. Select messages to drop (oldest user/assistant pairs, preserving structure)
4. Generate summary of dropped content via LLM
5. Replace dropped messages with single summary message
6. Persist summary to long-term memory via `agent.memory.persist_summary_to_memory()`

### Integration Points

- Called by `agent.loop` before each LLM turn
- Uses `agent.llm.dispatcher` to generate summaries
- Writes summaries to `agent.memory` for cross-session persistence

---

## 3. `agent/hooks/` — Observability Lifecycle Hooks

**Purpose**: Provides extensible event hooks for monitoring and observability (Week 3 requirement). Enables structured logging, metrics collection, and tracing without polluting business logic.

### Key Components

- **`types.py`** - Hook protocols and event types
  - `ObservabilityHook` - Protocol for hook implementations
  - `HookRegistry` - Singleton registry for registered hooks
  - `get_hook_registry()` - Access global hook registry
  - Event types:
    - `SubAgentStartEvent` - Sub-agent delegation begins
    - `SubAgentEndEvent` - Sub-agent completes with result
    - `ToolCallEvent` - Tool invocation with arguments and result
    - `LLMCallEvent` - LLM API call with token usage

- **`logging_hook.py`** - Default structured logging hook
  - `LoggingHook` - Emits JSON log events for all lifecycle events
  - Integrates with `agent.logging` for consistent log format
  - Captures timing, arguments, results, and metadata

### Usage Pattern

```python
from agent.hooks import get_hook_registry
from agent.observability import LoggingHook

# Register hooks at startup
registry = get_hook_registry()
registry.register(LoggingHook())

# Hooks fire automatically during execution
# No business logic changes needed
```

### Integration Points

- Called by `agent.loop` after LLM calls
- Called by `agent.tools.ToolRegistry.dispatch()` after tool execution
- Called by `agent.subagents.orchestrator` during sub-agent lifecycle
- Feeds data to Prometheus metrics endpoint (Week 3 observability)

---

## 4. `agent/llm/` — LLM Connectivity Layer

**Purpose**: Three-layer abstraction for connecting to the InnKube LLM API with clean separation between client creation, request dispatch, and type definitions.

### Architecture

```
config.yaml + INNKUBE_ACCESS_TOKEN
        ↓
client.py       make_client(config)
                → openai.OpenAI(api_key, base_url)
        ↓
dispatcher.py   make_chat_completions_effect(client, config)
                → dispatch(request) → ChatCompletion
                
                make_chat_completions_stream_effect(client, config)
                → dispatch(request) → Iterator[ChatCompletionChunk]
        ↓
InnKube API (HTTPS POST to llm-keys.innkube.fim.uni-passau.de)
```

### Key Components

- **`client.py`** - OpenAI client factory
  - **ONLY** file that reads `os.environ` or imports `openai.OpenAI`
  - `make_client(config)` - Creates configured OpenAI client
  - Reads `INNKUBE_ACCESS_TOKEN` from environment
  - Respects `OPENAI_BASE_URL` override (12-factor pattern)

- **`dispatcher.py`** - Request dispatchers
  - `make_chat_completions_effect()` - Returns non-streaming dispatch function
  - `make_chat_completions_stream_effect()` - Returns streaming dispatch function
  - Never touches `os.environ` - receives pre-configured client
  - Model, timeout, base URL resolved once at factory construction
  - Callers only pass `messages` in request dict

- **`types.py`** - Type definitions
  - `ChatCompletionCall` - Protocol for non-streaming dispatch
  - `ChatCompletionStreamCall` - Protocol for streaming dispatch
  - Never imports `openai` directly - only `openai.types.*`

- **`models.py`** - Model configuration
  - `get_context_config()` - Queries model context window size
  - Maps model names to context budgets
  - Available models: `qwen-agentworld-35b-a3b`, `gemma4-31b-it`, `octen-embedding-8b`

### Critical Constraints

- **Token from env var only** - Never hardcoded, never committed
- **Single responsibility** - Each layer has one job
- **Timeout on every call** - Explicitly required by spec
- **Model configured once** - Set in `config.yaml`, not per-request

---

## 5. `agent/mcp/` — Model Context Protocol Integration

**Purpose**: Connects to external MCP servers (Week 2) to dynamically discover and invoke tools from other systems. Enables integration with external APIs, databases, and services.

### Key Components

- **`manager.py`** - MCP server lifecycle orchestration
  - `MCPManager` - Manages multiple MCP server connections
  - `connect_all()` - Connects to all configured servers
  - `disconnect_all()` - Graceful cleanup on shutdown
  - Registers discovered tools in parent `ToolRegistry`
  - Naming pattern: `mcp_<server>__<tool>`

- **`client.py`** - Base MCP client interface
  - `MCPClient` - Abstract base class for transports
  - Tool discovery via `list_tools()` protocol
  - Tool invocation via `call_tool()` protocol

- **`stdio_client.py`** - Standard I/O transport (Week 2 scope)
  - `StdioMCPClient` - Subprocess-based MCP communication
  - Launches MCP server as child process
  - JSON-RPC protocol over stdin/stdout

- **`http_client.py`** - HTTP transport (future work)
  - `HttpMCPClient` - REST/SSE-based MCP communication
  - Streaming support for long-running operations

- **`config.py`** - MCP server configuration
  - Loads server definitions from `config.yaml`
  - Schema validation for server specs
  - Transport-specific parameters (command, args, env, url)

### Example Configuration

```yaml
mcp:
  servers:
    weather:
      command: "uvx"
      args: ["mcp-server-weather"]
      transport: "stdio"
```

### Integration Points

- Initialized in `agent.cli.__init__.py` during startup
- Registers tools in parent `ToolRegistry`
- Gracefully handles connection failures (logs warning, continues)
- Tools appear alongside built-in tools in LLM tool schemas

---

## 6. `agent/memory/` — Long-Term Memory

**Purpose**: Persists knowledge across sessions (Week 2 requirement) using SQLite storage with optional Qdrant vector search. Enables the agent to remember past conversations, decisions, and learned facts.

### Key Components

- **`store.py`** - SQLite persistence
  - `MemoryStore` - SQLite schema and CRUD operations
  - Schema: `memories` table with id, content, tags, timestamp, session_id
  - FTS5 (Full-Text Search) keyword indexing for fallback search
  - `write()` - Store new memory
  - `search()` - Keyword search with FTS5
  - `replace_summary_for_session()` - Update session summaries

- **`vectors.py`** - Semantic search with Qdrant
  - `VectorStore` - Qdrant client wrapper
  - `make_embedding_function()` - Creates embedding function using InnKube API
  - Uses `octen-embedding-8b` model (4096-dimensional embeddings)
  - `search()` - Similarity search with configurable threshold
  - `is_available()` - Health check for Qdrant connection
  - Fallback to keyword search when unavailable

- **`policy.py`** - Read/write decision rules
  - `should_store_content()` - Validates content before storage
  - `should_retrieve_memories()` - Determines when to search
  - Constants: `MIN_CONTENT_LENGTH`, `MAX_SEARCH_RESULTS`, `SIMILARITY_THRESHOLD`

- **`tools.py`** - Agent-facing memory tools
  - `remember` - Store information for future reference
  - `search_memory` - Retrieve relevant past memories
  - Schema definitions for LLM tool calling

### Storage Location

- SQLite database: `<workspace>/.nikola/.memory/memories.db`
- Qdrant service: `http://qdrant:6333` (containerized)
- Collection name: `agent_memories`

### Write Policy

- Compaction summaries automatically persisted (see `agent.context.compaction`)
- Explicit `remember` tool calls
- Content validation: minimum length, no PII, proper formatting

### Read Policy

- Just-in-time retrieval via `search_memory` tool
- Semantic search preferred (falls back to keyword)
- Returns top-k results with relevance scores

---

## 7. `agent/permissions/` — Permission Management

**Purpose**: Implements allow/deny/ask permission policies (Week 3) to control which tools the agent can invoke. Sub-agents have restricted permissions and cannot prompt users.

### Key Components

- **`policy.py`** - Permission policies
  - `PermissionPolicy` - Allowlist, denylist, asklist arrays
  - `PermissionAction` - Enum: `ALLOW`, `DENY`, `ASK`
  - `make_default_policy()` - Full access for main agent
  - `make_researcher_policy()` - Read-only for file-scout sub-agent

- **`gate.py`** - Permission enforcement
  - `PermissionGate` - Wraps tool dispatch with permission checks
  - `dispatch_with_permission()` - Check, confirm (if needed), then dispatch
  - `check_permission()` - Returns action for given tool
  - Sub-agents auto-deny on `ASK` (cannot prompt user)

- **`rules.py`** - Rule engine for YAML-based policies
  - `RuleEngine` - Pattern matching against tool names/arguments
  - Supports wildcards and regex patterns
  - Loaded from `permissions.yaml` (optional)

- **`cache.py`** - User confirmation caching
  - `PermissionCache` - Stores user decisions for session
  - Scope options: `once`, `always`, `session`
  - `make_cached_confirmation_fn()` - Wraps UI confirmation with cache lookup

- **`config.py`** - Configuration loading
  - `load_permissions_config()` - Parses `permissions.yaml`
  - Schema validation
  - Default fallback when file absent

### Permission Flow

1. Tool requested by LLM
2. `PermissionGate.check_permission(tool_name, arguments)`
3. If `ALLOW`: dispatch immediately
4. If `DENY`: return error message to LLM
5. If `ASK` and main agent: prompt user via UI
6. If `ASK` and sub-agent: auto-deny
7. Cache decision if scope != `once`

### Example `permissions.yaml`

```yaml
rules:
  - pattern: "read_*"
    action: allow
  - pattern: "write_file"
    action: ask
  - pattern: "execute_command"
    action: deny
default_action: ask
```

---

## 8. `agent/prompts/` — System Prompts

**Purpose**: Contains system prompts and role definitions that establish the agent's behavior, capabilities, and constraints.

### Structure

```
agent/prompts/
├── default.md              # Main agent system prompt
├── internal/               # Internal prompts (compaction, etc.)
└── subagents/              # Sub-agent role prompts
    ├── file-scout.md
    └── scheduler.md
```

### Key Files

- **`default.md`** - Main agent system prompt
  - Defines agent persona and capabilities
  - Lists available tools and usage guidelines
  - Establishes ReAct loop behavior
  - Loaded by `agent.cli.session.load_system_prompt(mode="default")`

- **`internal/`** - Internal operation prompts
  - Compaction summary generation
  - Memory storage decisions
  - Not exposed to users

- **`subagents/`** - Sub-agent role definitions
  - `file-scout.md` - Read-only researcher role
  - `scheduler.md` - Calendar operations role
  - Defines role-specific constraints and tool access

### Loading

Prompts are loaded at session initialization:
```python
system_prompt = load_system_prompt(mode="default", workspace=workspace_root)
```

Mode parameter selects which prompt file to load (enables specialized agent modes).

---

## 9. `agent/skills/` — Loadable Instruction Bundles

**Purpose**: Just-in-time instruction loading (Week 2) to keep context lean. Skills are disclosed only when needed, providing detailed guidance for specific tool categories or domains.

### Key Components

- **`loader.py`** - Skill discovery and loading
  - `discover_skills()` - Scans workspace for `.md` skill files
  - `load_skill()` - Reads skill content
  - Returns formatted instruction text

- **`tool.py`** - `load_skill` tool implementation
  - Agent-facing tool for loading skills by name
  - Schema generation with dynamic skill list
  - Tracks loaded skills to prevent duplicates

- **`filesystem.md`** - Filesystem tool skill
  - Detailed guidance for file operations
  - Security constraints (path escaping, no deletion)
  - Best practices for tool usage

- **`memory.md`** - Memory tool skill
  - When and how to use `remember` vs `search_memory`
  - Content formatting guidelines
  - Search strategies

### Skill Format

Skills are Markdown files with frontmatter:
```markdown
---
name: filesystem
description: Filesystem tool usage guidelines
---

# Filesystem Operations

[Detailed instructions...]
```

### Integration

- `load_skill` tool registered in `ToolRegistry`
- LLM calls tool when it needs guidance
- Skill content appended to conversation context
- Tracked in `loaded_skills` set to prevent re-loading

---

## 10. `agent/subagents/` — Sub-Agent Delegation

**Purpose**: Enables specialized sub-agents with restricted permissions (Week 3). Parent agents can delegate focused tasks to sub-agents with their own roles, contexts, and tool access.

### Key Components

- **`orchestrator.py`** - Delegation and execution logic
  - `SubAgentContext` - Configuration for sub-agent execution
  - `execute_subagent()` - Runs single sub-agent
  - `execute_subagents_parallel()` - Runs multiple sub-agents concurrently
  - `make_delegate_tool()` - Creates sequential delegation tool
  - `make_delegate_parallel_tool()` - Creates parallel delegation tool

- **`roles.py`** - Role definitions
  - `SubAgentRole` - Role configuration (name, system prompt, policy)
  - `BUILTIN_ROLES` - Registry of available roles
  - `make_file_scout_role()` - Read-only researcher
  - `make_scheduler_role()` - Calendar operations
  - `get_role()` - Lookup by name
  - `list_roles()` - Available roles

### Built-in Roles

**file-scout** (Researcher)
- Tools: `list_folder`, `read_file`, `search_text`, `search_memory`
- Permission: Read-only
- Use case: Code exploration, information gathering
- Cannot: Write files, execute commands, prompt user

**scheduler** (Calendar Manager)
- Tools: Calendar operations (list events, create events, find slots)
- Permission: Calendar read/write
- Use case: Meeting scheduling, calendar queries
- Cannot: File operations, command execution

### Usage Pattern

Parent agent calls delegation tool:
```python
# Sequential
result = await delegate(
    role="file-scout",
    task="Find all references to User class in agent/ directory"
)

# Parallel
results = await delegate_parallel(
    tasks=[
        {"role": "file-scout", "task": "Search agent/ for imports"},
        {"role": "file-scout", "task": "Search tests/ for fixtures"},
        {"role": "file-scout", "task": "List all config files"},
    ]
)
```

### Constraints

- Sub-agents cannot prompt users (`ASK` → auto-deny)
- Separate harness instance with own context
- Parent receives condensed summary only
- Execution recorded in parent's observability data

---

## 11. `agent/tools/` — Tool Registry and Built-in Tools

**Purpose**: Central registry for all agent capabilities. Manages tool registration, schema exposure, and dispatch. All agent tools (built-in, MCP, memory) flow through this system.

### Key Components

- **`__init__.py`** - `ToolRegistry` class
  - `register()` - Add tool with name, function, schema
  - `get_schemas()` - Return OpenAI tool schemas for LLM
  - `dispatch()` - Execute tool by name with arguments
  - `make_default_tool_registry()` - Factory for standard tool set
  - `make_scoped_registry()` - Create restricted registry for sub-agents

- **`filesystem.py`** - Filesystem operations
  - `create_folder` - Create directory
  - `write_file` - Create/overwrite file
  - `read_file` - Read file contents
  - `modify_file` - Edit existing file
  - `list_folder` - List directory contents
  - `navigate_folder` - Change working directory
  - `search_text` - Search files for text
  - `get_file_info` - File metadata
  - `copy_file` - Copy file
  - `move_file` - Move/rename file
  - **NO DELETE** - Deletion forbidden by spec

- **`execute.py`** - Command execution
  - `execute_command` - Run shell commands
  - Sandboxed to workspace root
  - Timeout enforcement
  - Stderr/stdout capture

### Tool Schema Format

Tools conform to OpenAI Chat Completion spec:
```python
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read and return the contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file relative to workspace root"
                }
            },
            "required": ["path"]
        }
    }
}
```

### Security Constraints

- All file operations restricted to workspace root
- Reject path escape attempts (`../`, absolute paths)
- No deletion capability (spec requirement)
- Command execution screened for dangerous patterns

### Integration Points

- Created in `agent.cli.__init__.py`
- Passed to `agent.loop.make_process_turn_fn()`
- MCP tools registered by `agent.mcp.manager`
- Memory tools registered by `agent.memory.tools`
- Sub-agent tools registered by `agent.subagents.orchestrator`

---

## Cross-Cutting Concerns

### Logging

All modules use Python's standard `logging` module with structured JSON output:
- Configured in `agent.logging.configure_logging()`
- Session ID included in all log events
- Written to `<workspace>/.nikola/logs/agent.jsonl`
- Queryable with `jq` for analysis

### Error Handling

- Tools never raise exceptions into loop
- Errors converted to observation strings: `"Error: <message>"`
- LLM observes failures and can recover/retry
- Critical errors (config, auth) fail fast at startup

### Testing

Per spec, pytest covers:
- Unit tests: Individual components in isolation
- E2E tests: Full turn execution with file I/O
- Canonical cases: `numbers-in-words`, `find-and-inspect`
- Regression safety: Test suite runs across all 3 weeks

### Configuration

Single source of truth: `config.yaml`
```yaml
llm:
  model: qwen-agentworld-35b-a3b
  timeout: 30
  base_url: https://llm-keys.innkube.fim.uni-passau.de/

workspace: /workspace

context:
  max_tokens: 32768
  compaction_threshold: 0.8

memory:
  qdrant:
    url: http://qdrant:6333
    collection: agent_memories

mcp:
  servers:
    weather:
      command: "uvx"
      args: ["mcp-server-weather"]
      transport: "stdio"
```

---

## Data Flow

### Typical Turn Execution

1. **User Input** → `agent.cli.repl`
2. **Session** adds user message → `agent.cli.session`
3. **Context Check** → `agent.context.budget`
   - If over budget: **Compaction** → `agent.context.compaction`
   - Summary persisted → `agent.memory`
4. **Turn Processing** → `agent.loop`
5. **LLM Call** → `agent.llm.dispatcher`
   - Hook fired → `agent.hooks` (observability)
6. **Tool Call(s)** (if any):
   - Permission check → `agent.permissions.gate`
   - Tool dispatch → `agent.tools.ToolRegistry`
   - Hook fired → `agent.hooks`
   - Observation returned
7. **Final Answer** → `agent.cli.ui`
8. **Session** adds assistant message

### Sub-Agent Delegation

1. **Parent** calls `delegate` tool
2. **Orchestrator** → `agent.subagents.orchestrator`
3. Create **SubAgentContext** with role policy
4. Build **scoped registry** from allowlist
5. Load **role prompt** from `agent/prompts/subagents/`
6. Execute **separate ReAct loop** with restricted tools
7. **Hook** fired for start/end events
8. Return **condensed summary** to parent
9. Parent continues with sub-agent result as observation

---

## Directory Dependencies

```
agent/
├── cli/          → llm, loop, tools, mcp, memory, permissions, subagents
├── context/      → llm (for summary), memory (for persistence)
├── hooks/        → (standalone, consumed by loop/tools/subagents)
├── llm/          → config
├── mcp/          → tools (registers), config
├── memory/       → llm (embeddings), config
├── permissions/  → config
├── prompts/      → (data only, no code dependencies)
├── skills/       → (data + loader, consumed by tools)
├── subagents/    → tools, loop, llm, permissions, prompts
└── tools/        → config
```

---

## Summary

Each subdirectory has a clear, focused responsibility:

| Directory | Primary Responsibility | Key Classes/Functions |
|---|---|---|
| `cli/` | User interface and orchestration | `main()`, `run_repl()`, `ConsoleUI` |
| `context/` | Token budget and compaction | `compact()`, `estimate_tokens()` |
| `hooks/` | Observability and monitoring | `HookRegistry`, `LoggingHook` |
| `llm/` | LLM API connectivity | `make_client()`, `dispatcher.py` |
| `mcp/` | External tool integration | `MCPManager`, `StdioMCPClient` |
| `memory/` | Cross-session persistence | `MemoryStore`, `VectorStore` |
| `permissions/` | Access control | `PermissionGate`, `PermissionPolicy` |
| `prompts/` | System prompts and roles | Markdown files |
| `skills/` | Just-in-time instructions | `load_skill()`, skill `.md` files |
| `subagents/` | Specialized delegation | `execute_subagent()`, `SubAgentRole` |
| `tools/` | Capability registry | `ToolRegistry`, filesystem/execute tools |

This architecture supports the three-week development roadmap:
- **Week 1**: Core ReAct loop, CLI, tools, context management
- **Week 2**: MCP integration, long-term memory, skills
- **Week 3**: Observability hooks, permission system, sub-agents
