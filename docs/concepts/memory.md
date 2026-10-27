# Long-term Memory

The harness persists information across Sessions using a two-backend memory system:
**SQLite** as the source of truth and **Qdrant** as an optional semantic index.

---

## Concepts

### Memory

Persistent information stored across sessions. Memories survive application restarts and are
classified by **Kind** and **Source**.

### Kind

| Kind | Description |
|---|---|
| `fact` | Objective information (e.g. "the project uses Python 3.12") |
| `preference` | User likes/dislikes (e.g. "prefer concise answers") |
| `summary` | Compaction recap — automatically stored when context is compacted |

### Source

| Source | Description |
|---|---|
| `user` | Memory was stored by an explicit `remember()` tool call |
| `compaction` | Memory was stored automatically when the context window was compacted |

### Pinned Memory

A memory with `pinned=True` is injected into the system prompt at every Session startup.
Pinned memories are capped at **10 rows or 2 000 characters**, whichever binds first, to
prevent them from dominating the context budget.

---

## Storage split

| Backend | Role | Required | Location |
|---|---|---|---|
| **SQLite** | Source of truth — stores all memories | Yes | `config.memory.db_path` (ships as `/workspace/.agent/memory.db`) |
| **Qdrant** | Vector index for semantic search | No (degrades gracefully) | `config.memory.qdrant.url` (default `http://qdrant:6333`) |

When Qdrant is unavailable the harness automatically falls back to SQLite FTS5 keyword search.
The mandatory guarantee — "store in session A, retrieve after restart in session B" — has no
runtime dependency on Qdrant.

See [ADR 0003 — Memory Split](../decisions/0003-memory-split.md) for the rationale.

---

## Write policy

Memories are written in two ways:

| Trigger | Kind | Source |
|---|---|---|
| LLM calls `remember(content, kind, pinned)` | Any | `user` |
| Context compaction completes | `summary` | `compaction` |

**Guards:**

- Empty content is rejected
- Error strings (content starting with `"Error:"`) are rejected
- Compaction summaries **replace** any existing summary — they do not append

---

## Read policy

| When | What | How |
|---|---|---|
| Session startup | All pinned memories | Injected into system prompt via rules mechanism (capped at 10 / 2 000 chars) |
| Every Turn | Top-3 relevant memories for the user's message | Semantic search (Qdrant) or FTS5 (SQLite fallback) |
| On demand | Any memory matching a query | LLM calls `search_memory(query)` |

---

## Tools

### `remember`

```
remember(content: str, kind: str = "fact", pinned: bool = False) -> str
```

Store a memory. `kind` must be `"fact"`, `"preference"`, or `"summary"`.
Set `pinned=True` to inject the memory into every future session's system prompt.

### `search_memory`

```
search_memory(query: str) -> str
```

Search memories by keyword or semantic similarity. Returns a formatted list of matching
memories with their kind, source, and content.

---

## What is never stored

- Credentials (API keys, tokens, passwords)
- Transient state (current working directory, open file handles)
- Raw LLM responses (only validated summaries from compaction)

---

## Inspecting memories

The **sqlite-web** service at <http://localhost:8081> provides a browser UI for the
memory database.

!!! warning "Two paths disagree — reconcile before demoing the browser"
    The agent writes `config.memory.db_path`, which ships as
    `/workspace/.agent/memory.db` (`config.yaml`, and the same default in
    `agent/config.py`). The `sqlite-web` service in `compose.yaml` is pointed at
    `/data/.nikola/.memory/memories.db` instead, and `.gitignore` ignores that second
    path. Until one side is changed the browser at :8081 opens a file the agent never
    writes. Fix it in whichever direction you prefer — change `db_path` in `config.yaml`
    to `/workspace/.nikola/.memory/memories.db`, or repoint the `sqlite_web -x` argument
    at `/data/.agent/memory.db`.

At the shell (paths below follow the shipped `config.yaml`):

```bash
# All memories
sqlite3 workspace/.agent/memory.db "SELECT kind, source, pinned, content FROM memories"

# Pinned memories only
sqlite3 workspace/.agent/memory.db "SELECT content FROM memories WHERE pinned=1"

# Memory operations in the structured log
jq 'select(.event_type == "memory_operation")' workspace/.nikola/logs/agent.jsonl
```

Qdrant dashboard at <http://localhost:6333/dashboard#> shows the vector collection and
allows inspection of embeddings.
