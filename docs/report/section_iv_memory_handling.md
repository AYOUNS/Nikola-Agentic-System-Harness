# Section IV — Memory Handling

## IV.A Overview

Large Language Models are stateless: given the same input twice, they produce equivalent output and retain nothing between calls. A useful agent must therefore carry memory the model itself cannot. The harness closes this gap by partitioning memory into two tiers with deliberately different properties — *short-term memory*, which is fast, ephemeral, and scoped to a single session, and *long-term memory*, which is persistent and survives container restarts. These tiers are connected by a well-defined promotion path that moves information from short-term context into long-term storage at the exact moment it would otherwise be discarded.

---

## IV.B Short-Term Memory: Context Window Management

### IV.B.1 Structure

Short-term memory is realised as a Python `list[ChatCompletionMessageParam]` owned by the `Session` object in `agent/cli/session.py`. The full list is passed to the LLM dispatcher on every call, giving the model its complete working memory. The list follows a strict structural invariant:

```
[0] system message    — agent prompt, user rules, pinned long-term memories
[1] user: "..."       — first user turn
[2] assistant: "..."  — assistant reply (optionally with tool_calls)
[3] tool: "..."       — observation (must immediately follow tool_calls)
[4] user: "..."       — next user turn
...
```

The *pairing invariant* — that every assistant message containing `tool_calls` is immediately followed by its matching `role: "tool"` observations — is structural and enforced by compaction. Violating it produces HTTP 400 errors from the API.

### IV.B.2 Context Rot and Three-Tier Compaction

A critical problem identified in the specification is *context rot*: model performance degrades as the window fills. The harness addresses this through a three-tier compaction strategy in `agent/context/compaction.py`.

**Tier 1 — Observation truncation.** Any tool observation exceeding `max_observation_chars` (10,000 by default) is truncated with a `[truncated]` marker. No LLM call required; this recovers significant budget when the agent reads large files.

**Tier 2 — Complete turn dropping.** When truncation is insufficient, whole turns — a user message, the assistant reply, all tool calls, and their results — are dropped from the front of the list. Turns must be dropped atomically to preserve the pairing invariant.

**Tier 3 — Summarisation (specified, not reached).** As designed, the turns dropped by Tier 2 are rendered as a plain-text transcript, sent to the LLM with a summarisation prompt, and the resulting summary is inserted back into the surviving conversation. We report the tier's current state as a gap rather than a feature: `summarize_messages()` and `replace_dropped_with_summary()` are implemented and unit-tested, but no production caller reaches them. `compact()` stops after Tier 2 and returns the dropped turns in `dropped_messages`, and `MainLoopContextAdapter.compact()` in `agent/loop_adapters.py` discards that value. A budget-triggered compaction therefore loses the dropped transcript rather than retaining a recap of it. Wiring the tier in requires only that a dispatcher be threaded through to the context adapter.

The one path that does summarise is the operator-issued `/compact`, which condenses the whole conversation unconditionally rather than only what Tier 2 dropped. The measurement below comes from there, on the same call Tier 3 would issue. Reasoning is explicitly disabled (`reasoning_effort: none`): on the deployed Qwen model, chain-of-thought executes before any answer text, so with reasoning enabled a 6-turn transcript consumes 1,016 completion tokens before producing a summary versus 81 tokens with reasoning disabled — for identical output.

**Table IV-1** — Compaction tiers.

| Tier | Mechanism | LLM Call | Token Recovery |
|------|-----------|----------|----------------|
| 1 | Truncate oversized observations | No | High (file-heavy sessions) |
| 2 | Drop oldest complete turns | No | Moderate |
| 3 | Summarise dropped turns | Yes (reasoning off) | **Never reached** — no production caller; only `/compact` summarises |

---

## IV.C Long-Term Memory: Persistent Storage

### IV.C.1 Storage Architecture

Long-term memory uses two backends. SQLite is the mandatory source of truth; Qdrant is an optional vector index. This split was formalised in ADR 0003 after evaluating three alternatives:

**Table IV-2** — Storage architecture alternatives.

| Alternative | Persistence Guarantee | Semantic Search | Hard Dependency |
|-------------|----------------------|-----------------|-----------------|
| SQLite-only (FTS5) | Unconditional | Keyword only | None |
| Qdrant-only | Broken on failure | Full | Network service |
| **Split: SQLite + Qdrant (adopted)** | **Unconditional** | **Full / keyword fallback** | **None** |

The rationale is asymmetric risk. SQLite is a single file on the container volume — it survives restarts, requires no running service, and writes atomically. Qdrant is a networked service that may be unavailable due to startup ordering or crashes. Making persistence contingent on Qdrant would cause silent data loss under common failure modes.

### IV.C.2 Schema and Classification

Every record is a `Memory` object with eight fields. The `kind` field classifies the information's epistemic role: *facts* are objective technical claims; *preferences* are user style or workflow preferences; *summaries* are compaction-generated recaps promoted automatically when context is compacted. The `source` field separates *explicit writes* (LLM called `remember()`) from *automatic writes* (compaction produced a summary), which matters for trust and for debugging.

**Table IV-3** — Memory record schema.

| Field | Type | Purpose |
|-------|------|---------|
| `id` | INTEGER PK | Primary key |
| `content` | TEXT | Memory text |
| `kind` | fact / preference / summary | Classification |
| `pinned` | BOOLEAN | Inject at session startup |
| `source` | user / compaction | Origin |
| `session_id` | TEXT | Writing session |
| `created_at` | ISO 8601 | Creation timestamp |
| `embedded` | BOOLEAN | Qdrant vector present |

### IV.C.3 Retrieval: FTS5 and Semantic Search

The database includes an FTS5 virtual table `memories_fts` maintained by three SQLite triggers (after insert, delete, and update). Using triggers rather than application-level synchronisation ensures the index cannot drift even if writes bypass the `MemoryStore` API. The update trigger is particularly important: it must evict old content from the FTS5 index before inserting new content, or stale results are returned.

When Qdrant is available, the `VectorStore` class in `agent/memory/vectors.py` embeds content using the `octen-embedding-8b` model (4,096-dimensional vectors, cosine distance). The `embedded` flag enables a backfill path: records written while Qdrant was unavailable survive in SQLite with `embedded=False` and can be indexed when the service recovers.

---

## IV.D Memory Policies

### IV.D.1 Write Policy

Information enters long-term memory through exactly two paths:

**Explicit writes** occur when the LLM calls `remember(content, kind, pinned)`. Before any write, `should_store_content()` in `agent/memory/policy.py` validates the content: empty strings, whitespace-only strings, and strings beginning with `"Error:"` are rejected. The last guard prevents tool failure messages from being recorded as facts.

**Summary promotion** occurs when the operator issues `/compact`. After `compact_command` in `agent/cli/commands/builtin.py` replaces the conversation history, `persist_summary_to_memory()` promotes the same summary string to SQLite by calling `replace_summary_for_session()`. This deletes any prior summary for the session before inserting the new one, so repeated compaction cycles produce a single evolving summary per session rather than a growing stack of overlapping ones. This path is operator-gated, not automatic: it is the sole call site of `persist_summary_to_memory()`, and the budget-triggered tier that would also trigger it does not run (Section IV.B.2).

What is *never stored*: credentials, transient state (working directory, file handles), and raw LLM responses.

### IV.D.2 Read Policy

Long-term memory is consulted at three distinct points in the agent lifecycle:

**At startup**, `get_pinned()` returns up to `PINNED_MAX_COUNT = 10` memories capped at `PINNED_MAX_CHARS = 2,000` characters, which are appended to the system prompt as a `## Long-term Memory` section. Pinned memories are visible on every turn without any search overhead. Pinning is deliberate because each pinned memory costs context tokens on every call.

**Every turn**, the top `PER_TURN_RETRIEVAL_LIMIT = 3` relevant memories for the user's current prompt are retrieved and prepended to the user message content before the LLM call. Injecting into user content — rather than adding a second system message — is structurally safe across providers. If retrieval fails, the error is silently swallowed; a broken memory store must never interrupt a conversation turn.

**On demand**, the `search_memory(query)` tool lets the agent explicitly retrieve up to 20 memories. The LLM calls it when per-turn injection did not surface something specific enough for the current task.

**Table IV-4** — Read policy summary.

| Trigger | Mechanism | Limit | Failure Mode |
|---------|-----------|-------|-------------|
| Session startup | Inject pinned memories into system prompt | 10 / 2,000 chars | Silently omitted |
| Every turn | Prepend top-N to user message | 3 records | Silently omitted |
| On demand | `search_memory()` tool call | 20 records | Returns empty |

---

## IV.E The Promotion Path

The architecturally significant bridge between the two tiers is the *promotion path* through summarisation. When the operator issues `/compact`, the resulting summary is both substituted into the surviving conversation and written to SQLite by `persist_summary_to_memory()`. The summariser produces a plain string and does not know the SQLite schema; the memory system accepts a string and does not know about compaction tiers. The bridge function in `agent/memory/__init__.py` is the thin coupling point between them.

When a new session starts, per-turn retrieval surfaces that summary when the user's current prompt is contextually related. The agent reconstructs prior context from the durable summary rather than replaying raw message history, which would exhaust the context window immediately.

The bridge is narrower than the design intends, and we report it as the weakest link in the two-tier architecture. Because Tier 3 has no production caller (Section IV.B.2), nothing is promoted when the context budget triggers compaction on its own. Cross-session continuity therefore rests on an operator compacting by hand, or on the model calling `remember()` deliberately.

**Figure IV-1** — Promotion path from short-term to long-term memory.

```
Context window fills                    Operator runs /compact
        │                                        │
        ▼                                        ▼
Tier 1 truncate → Tier 2 drop turns     summarise whole transcript
        │                                        │
        ╎ Tier 3 summarise                       ├──► replace history
        ╎ (NO CALLER — see IV.B.2)               │     with recap
        ▼                                        ▼
  dropped turns lost              persist_summary_to_memory()
                                                 │
                                                 ▼
                                        SQLite (kind=summary)
                                                 │
                                   Qdrant vector index (optional)
                                   FTS5 keyword index (always)
```

---

## IV.F Design Decisions and Trade-offs

**SQLite as source of truth.** The mandatory persistence guarantee — store in session A, retrieve after restart in session B — has no network dependency. Qdrant adds semantic retrieval quality when available and degrades transparently to FTS5 when absent.

**FTS5 trigger synchronisation.** Three SQLite triggers maintain the full-text index automatically, correctly handling content updates where stale text must be evicted before new text is inserted.

**Explicit policy module.** `policy.py` exists so that write and read policies can be read in one place, tested independently, and cited in documentation. Named constants (`PER_TURN_RETRIEVAL_LIMIT = 3`, `PINNED_MAX_COUNT = 10`) mean a policy change requires editing one value in one file.

**Summaries replace rather than append.** A session that compacts multiple times produces overlapping summaries. Replacing rather than appending means the database holds one evolving summary per session, preventing retrieval from surfacing redundant, conflicting recaps and keeping the context budget lean.

**An unwired tier is worse than an absent one.** Tier 3 is fully implemented and carries five passing unit tests, which is precisely why its absence from the budget-triggered path went unnoticed: the tests prove the function works, and nothing proves it is called. We record this as a lesson about test coverage rather than as an isolated defect, and an integration test asserting that a budget-triggered compaction produces a recap would have caught it.
