# Memory Architecture in Agent Harnesses: Short-Term and Long-Term Memory

## 1. Introduction

Large Language Models (LLMs) are stateless. Given the same input twice, they produce equivalent output. They retain nothing between calls. This is a fundamental constraint: a useful agent must remember what happened earlier in a conversation, what tools it called, what results it received, and what users have told it across previous sessions. The harness — the infrastructure layer surrounding the LLM — is responsible for closing this gap.

Memory in an agent harness divides cleanly into two tiers with fundamentally different properties. Short-term memory is fast, ephemeral, and scoped to a single session. It is the live conversation context the LLM reads on every call. Long-term memory is persistent, structured, and survives process restarts. It holds knowledge the agent should carry forward across sessions: facts, preferences, and summaries of completed work.

This paper describes the design and implementation of both memory tiers in a ReAct-based agent harness built during this lab, with specific reference to the specification in the course document and the concrete implementation in the codebase. It covers the architecture of each tier, the policies governing when information moves between them, the retrieval strategies, and the design decisions that shaped the final implementation.

---

## 2. Short-Term Memory: The Context Window

### 2.1 Definition and Role

The course specification defines short-term memory explicitly as the **Context** component:

> "Context: The short-term memory, updated after every loop step, that includes system instructions, conversation history, tool schemas, and outputs."

In the implementation, this is a Python list called `messages` of type `list[ChatCompletionMessageParam]`, owned by the `Session` object in `agent/cli/session.py`. Every turn, this list is passed directly to the LLM dispatcher. The LLM sees the entire list on every call — system prompt, all prior user messages, all prior assistant replies, all tool calls and their observations. This is the model's working memory.

### 2.2 Structure

The message list has a defined structure that must be respected or the LLM API returns errors:

```
[0] system message    ← always first; contains the agent mode prompt,
                         user rules, and pinned long-term memories
[1] user: "..."       ← first user turn
[2] assistant: "..."  ← assistant reply, possibly with tool_calls
[3] tool: "..."       ← tool observation (must follow a tool_call)
[4] user: "..."       ← next user turn
...
```

The pairing invariant — that every assistant message containing `tool_calls` must be immediately followed by the matching `role: "tool"` observation messages — is structural and enforced by the compaction logic. Breaking it causes HTTP 400 errors from the API.

### 2.3 Lifecycle

Short-term memory has a clearly defined lifecycle:

- **Created**: at session start, initialised with the system message.
- **Grown**: on every turn, the user message is appended before the LLM call; the assistant reply and any tool observations are appended after.
- **Trimmed**: when the context approaches the token budget, compaction runs.
- **Cleared**: when the user runs `/clear`, `reset_history()` replaces the list with just the system message.
- **Destroyed**: when the process exits, the list is gone.

This ephemeral nature is intentional. Replaying a full prior conversation at startup would immediately exhaust the context window and provide stale tool observations that no longer reflect the current filesystem state.

### 2.4 Context Rot and Compaction

A critical problem identified in the specification is **context rot**: "LLM performance degrades as the window fills." As the message list grows, older turns become noise. The harness manages this through a three-tier compaction strategy in `agent/context/compaction.py`:

**Tier 1 — Truncate oversized observations.** Tool observations (file reads, directory listings) can be very large. Any observation exceeding `max_observation_chars` (10,000 by default) is truncated with a `[truncated]` marker appended. This is free — no LLM call required — and recovers significant budget when the agent has been reading large files.

**Tier 2 — Drop oldest complete turns.** When truncation is not enough, whole turns (a user message, the assistant reply, all tool calls and their results) are dropped from the front of the list. The pairing invariant means turns must be dropped as units — never split.

**Tier 3 — Summarize dropped turns.** The dropped turns are sent to the LLM with a summarization prompt, and the resulting summary is inserted back into the conversation as a single message. This preserves the semantic content of the dropped work — paths, decisions, error text — while dramatically reducing token count. The summarization call deliberately disables reasoning (`reasoning_effort: none`) because the target model spends its completion budget on chain-of-thought before writing any answer text, and on a 6-turn transcript this difference is 1016 tokens with reasoning versus 81 without, for the same summary.

---

## 3. Long-Term Memory: Persistent Storage

### 3.1 Definition and Role

The specification defines long-term memory as:

> "Persistent information retained across runs, such as user preferences, learned facts, or summaries of previous conversations. It lives outside the context and is consulted on demand."

In the implementation, long-term memory is a SQLite database stored at `workspace/.nikola/.memory/memories.db`, managed by the `MemoryStore` class in `agent/memory/store.py`. It survives container restarts because it is on the workspace volume, which is mounted as a Docker volume rather than baked into the image.

### 3.2 Schema and Memory Classification

Every record in the database is a `Memory` object with the following fields:

| Field | Type | Purpose |
|---|---|---|
| `id` | integer | Primary key |
| `content` | text | The memory text |
| `kind` | fact / preference / summary | Classification |
| `pinned` | boolean | Whether to inject at startup |
| `source` | user / compaction | How it was created |
| `session_id` | text | Which session wrote it |
| `created_at` | ISO timestamp | When it was written |
| `embedded` | boolean | Whether Qdrant has indexed it |

The `kind` field distinguishes three types of information with different purposes:

- **fact**: Objective, verifiable information — technical stack details, architecture decisions, environment configuration.
- **preference**: User style and workflow preferences discovered during conversations.
- **summary**: Compaction-generated recaps of completed work, persisted so the agent can reconstruct prior context in a new session.

The `source` field separates **explicit writes** (the user called `remember()`) from **automatic writes** (compaction produced a summary). This distinction matters for debugging and for trust: user-sourced memories are deliberate; compaction-sourced summaries are mechanically generated.

### 3.3 Full-Text Search and Vector Search

The database includes an FTS5 virtual table `memories_fts` kept in sync with the main table through three SQLite triggers (insert, delete, update). This provides keyword search as the baseline retrieval strategy: the `search_keyword()` method issues a `MATCH` query against the FTS5 index, returning results ordered by rank.

For semantic search — finding memories that are conceptually related but do not share keywords — the harness optionally connects to a Qdrant vector database. When available, the `VectorStore` class in `agent/memory/vectors.py` embeds memory content using the `octen-embedding-8b` embedding model (producing 4096-dimensional vectors) and stores them in a Qdrant collection. At retrieval time, the query is embedded and a cosine similarity search is performed, returning the IDs of the most similar stored vectors.

A key design decision is that **SQLite is always the source of truth; Qdrant is only an index**. If Qdrant is unavailable at startup, the agent prints a notice and falls back to keyword search automatically. If Qdrant was available when a memory was written but fails at search time, the FTS5 fallback activates. This means the persistence guarantee — "store in one session, retrieve after restart" — has no runtime dependency on Qdrant. The `embedded` field on each memory record tracks whether Qdrant has an up-to-date vector, enabling a backfill path when Qdrant comes back online after an outage.

---

## 4. Memory Policies: When to Read and Write

A key requirement from the specification is that the mechanism governing what to store and when to retrieve must be **explicitly stated**. This is implemented in `agent/memory/policy.py` as documented constants and functions, rather than ad-hoc decisions scattered through the code.

### 4.1 Write Policy

Information is written to long-term memory through two paths:

**Explicit writes** happen when the agent calls the `remember()` tool, which is registered in the tool registry and exposed to the LLM. The user or the agent can call it at any time with a `content`, a `kind`, and an optional `pinned` flag. The content is validated by `should_store_content()` before writing: empty content, whitespace-only content, and strings that start with `"Error:"` are all rejected. This prevents tool failure messages from being stored as facts.

**Automatic writes** happen when context compaction produces a summary. After `compact_command` in `builtin.py` replaces the conversation with a summary, `persist_summary_to_memory()` is called. This wires the short-term and long-term tiers together: the summary produced by the short-term context management system is promoted into the long-term store before the old turns are discarded. Crucially, summaries for a given session replace rather than append: calling `replace_summary_for_session()` deletes any prior summary for that session before inserting the new one, so repeated compactions produce a single evolving summary rather than a stack of overlapping ones.

What is **never stored**: credentials (API keys, tokens, passwords), transient state (current working directory, open file handles), and raw LLM responses. Only validated summaries from compaction are stored automatically; raw conversation turns are never persisted wholesale.

### 4.2 Read Policy

Long-term memory is read at three points in the agent's lifecycle, implementing the specification's requirement for "dropping small, high-value files into the context up front" and "keeping lightweight identifiers that let the agent load information just-in-time":

**At startup**, pinned memories are injected into the system message. The `system_message()` function in `session.py` calls `memory_store.get_pinned()`, which returns up to ten memories, capped at 2,000 characters total, ordered by creation time. These are formatted as a `## Long-term Memory` section appended to the system prompt, so the LLM sees them on every single turn without needing to search. Only memories with `pinned=True` are included; pinning is a deliberate action because pinned memories cost context on every call.

**Every turn**, the top three most relevant memories for the current user prompt are retrieved and prepended to the user message content. This is implemented in `run_turn()` inside `agent/cli/repl.py`: before the user message is appended to `session.messages`, `search_keyword(prompt, limit=3)` is called and the results are formatted by `format_per_turn_retrieval()`. If no relevant memories are found, nothing is added and the prompt is sent unmodified. A failure in retrieval is silently swallowed — a broken memory store must never interrupt a conversation turn.

**On demand**, the `search_memory()` tool lets the agent search explicitly for up to twenty results. The LLM decides when to call it, typically when the per-turn injection did not surface something specific enough. This is the just-in-time load path: the agent retrieves detailed information only when the current task requires it, keeping the context lean the rest of the time.

---

## 5. The Promotion Path: Short-Term to Long-Term

The most architecturally significant connection between the two tiers is the **promotion path** through compaction. Without it, every session would begin cold — the agent would have no knowledge of prior work.

The path works as follows. During a long session, the context window fills. Compaction runs: Tier 2 drops old turns, Tier 3 summarises them into a single paragraph that captures paths, decisions, and error text from the dropped work. This summary is inserted into the surviving conversation so the LLM can continue with context. Simultaneously, the same summary is written to the SQLite database as a `kind="summary"`, `source="compaction"` record. When a new session starts the next day, the startup injection reads pinned memories, and the per-turn retrieval surfaces the summary when relevant queries arrive. The agent effectively reconstructs its prior context from the durable summary rather than from the raw message history.

This design keeps the two tiers loosely coupled. The compaction system does not need to know about the SQLite schema; it produces a string. The memory system does not need to know about compaction tiers; it accepts a string and stores it. The `persist_summary_to_memory()` function in `agent/memory/__init__.py` is the thin bridge between them, validating the content and delegating to `replace_summary_for_session()`.

---

## 6. Design Decisions and Trade-offs

Several concrete design decisions shaped this implementation.

**SQLite over a dedicated vector database as the source of truth.** Qdrant provides superior semantic retrieval but is an optional external service. Requiring it for persistence would mean a failed Qdrant container causes memory loss. SQLite runs in-process, writes atomically, and needs no network. The decision to make Qdrant an index-only component means the mandatory guarantee has no network dependency.

**FTS5 triggers for index synchronisation.** Rather than manually updating the FTS5 index on every write and update, three SQLite triggers (after insert, after delete, after update) keep it in sync automatically. This is particularly important for the update path: when a memory's content is changed via `update()`, the old text must be removed from the FTS5 index before the new text is added, or searches would return stale results.

**Explicit policy module.** The `policy.py` module exists specifically so the write and read policies can be read, tested, and pointed at in documentation. Scattering `if len(content) > 10` checks through the codebase would produce an implicit, untestable policy. Having named constants (`PER_TURN_RETRIEVAL_LIMIT = 3`, `PINNED_MAX_COUNT = 10`) means a single change to the policy is reflected everywhere.

**Per-turn retrieval prepended to user content, not injected as a system message.** Injecting retrieved memories as a system message would create multiple system messages in the conversation, which some models reject. Prepending them as text inside the user message is structurally safe and keeps the system message at position zero.

---

## 7. Conclusion

The two-tier memory architecture in this harness directly realises the specification's distinction between context (short-term, in-window, ephemeral) and long-term memory (persistent, out-of-window, consulted on demand). Short-term memory is the raw `messages` list managed by `Session`, shaped by a three-tier compaction strategy that keeps it within the token budget. Long-term memory is a SQLite database with an optional Qdrant vector index, governed by an explicit write policy (two entry points: explicit `remember()` and automatic compaction promotion) and a three-point read policy (startup injection, per-turn retrieval, on-demand search). The promotion path through compaction is the key architectural bridge that allows an agent to begin a new session with meaningful context from prior work, without replaying the full conversation history.
