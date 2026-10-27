# Execution Model

This page defines the terms the harness uses for time, structure, and control flow.
Understanding these makes the rest of the docs precise.

---

## Hierarchy

```
Session
└── Turn (1…N)
    └── Step (1…N)
        ├── LLM call  (Thought + optional Action selection)
        └── Tool call (Observation)
```

### Session

The full lifetime of one agent process — from startup to exit. A Session contains zero or more
Turns. Everything loaded at startup lives for the whole Session:

- System prompt (selected by Agent Mode)
- Registered tools (built-in + MCP)
- MCP server connections
- Pinned memories (injected into the system prompt)
- Enabled rules (appended to every turn's system prompt)
- Discovered skills (available for on-demand loading)

### Turn

One user message and the single agent response that closes it. A Turn spans as many internal
Steps as the ReAct loop needs before producing a final answer.

### Step

One Thought/Action/Observation cycle inside the ReAct loop:

1. One LLM call → produces a Thought and optionally an Action (tool selection)
2. If an Action is present → one Tool call → produces an Observation

A Turn ends when a Step produces a **final answer** instead of a tool call. The signal is
structural: if the LLM response contains `tool_calls`, the Step is an Action; if finish reason
is `"stop"` with no `tool_calls`, the content is the final answer. No text parsing or special
prefixes are involved.

---

## Entry points

### REPL mode

Interactive Session where the user types prompts and the agent responds, repeating until the
user types `/exit` or presses CTRL-C. This is the default when the container is started with
`podman attach`.

### One-shot mode

A Session that processes exactly one Turn — the prompt supplied via `--prompt` on the CLI —
and exits immediately after the final answer. Used for scripting and tests.

```bash
# Run one task and exit
docker compose exec agent python -m agent.cli --prompt "List all files in /workspace"
# or: podman compose exec agent python -m agent.cli --prompt "List all files in /workspace"
```

One-shot mode shares the REPL's confirmation channel, so a tool with `action: ask` still
pauses for approval. Pipe the input (or run it in CI) and the menu reads EOF, which counts
as a "No" — so scripted runs should rely on `allow` rules rather than on answering prompts.
Sub-agents are the case with no channel at all; there `ask` is turned into `deny`.

---

## Agent Mode

A named behavioural configuration that selects the system prompt loaded at Session start.
The mode name maps to `agent/prompts/<mode>.md`; `"default"` is the fallback.

| Mode | Prompt file | Intended use |
|---|---|---|
| `default` | `agent/prompts/default.md` | General-purpose assistant — the only mode that ships |

`available_modes()` globs `agent/prompts/*.md` rather than reading a hardcoded list, so
dropping a new `<name>.md` in that directory adds the mode: `--mode <name>` loads it and
`/mode` completion offers it immediately. `agent/prompts/internal/` and
`agent/prompts/subagents/` sit in subdirectories precisely so they are *not* offered as
modes.

Switch modes at startup:

```bash
python -m agent.cli --mode default
```

Or at runtime with the slash command:

```
/mode <name>
```

!!! note "Mode ≠ entry point"
    Agent Mode governs what the LLM is instructed to do. REPL vs one-shot is a separate
    choice about *how* the Session is driven. They are independent.

---

## Loop Guard

A termination condition that bounds the ReAct loop so it always terminates.
When a guard fires, `process_turn_fn` returns a synthetic final-answer string — it never raises.

| Guard | Config key | Default | Synthetic answer when fired |
|---|---|---|---|
| Iteration cap | `loop.max_iterations` | `100` | `[Loop terminated: max iterations reached]` |
| Token budget | `context.window_tokens` | `262144` | `[Loop terminated: context budget exceeded]` |
| Time budget | `llm.timeout_seconds` | `60` | `[Loop terminated: LLM timeout]` |

---

## Context budget and compaction

The harness tracks an estimate of how many tokens the current `messages` list occupies.
When the budget nears `window_tokens - reserve_output_tokens`, it applies **3-tier compaction**:

1. **Truncate** — drop the oldest tool observations beyond `max_observation_chars`
2. **Drop** — remove the oldest non-pinned turns
3. **Summarize** — if still over budget, call the LLM to produce a summary of dropped turns
   and persist it to long-term memory (source: `compaction`)

The summary call uses `summary_max_tokens` as its completion cap and
`summary_reasoning_effort: none` to avoid spending the budget on thinking.

---

## `process_turn_fn`

The ReAct loop is encapsulated as a single callable:

```python
process_turn_fn(messages: list[dict]) -> str
```

`messages` is the full OpenAI-spec conversation history owned and grown by the REPL.
The callable appends its own assistant reply before returning the final answer string.

This design makes the loop **testable independently of I/O**: tests call `process_turn_fn`
directly with a prepared message list and assert on the returned string.

The REPL uses the streaming variant internally (tokens printed as they arrive); sub-agents
and tests use the non-streaming variant.

---

## Slash commands

Commands entered with a `/` prefix are dispatched by the REPL to the command registry —
they never reach the ReAct loop.

| Command | Description |
|---|---|
| `/help` | List all available commands |
| `/exit` | End the session |
| `/clear` | Clear conversation history |
| `/mode <name>` | Switch Agent Mode |
| `/tools` | List registered tools |
| `/model` | Show active LLM model |
| `/context` | Show context budget usage |
| `/permissions status` | Show session permission cache |
| `/mcp` | Show connected MCP servers and their tools |
| `/compact [what to keep]` | Replace the conversation with a summary of it, freeing context |
| `/rules` | List loaded user rules and their token cost |
| `/skills [list\|load <name>]` | List or load skills |
