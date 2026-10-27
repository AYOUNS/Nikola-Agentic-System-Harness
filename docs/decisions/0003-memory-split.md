# ADR 0003 — Memory Storage Split

**Status:** Accepted  
**Date:** 2025

---

## Context

The harness requires long-term memory that persists across application restarts. The spec
mandates the ability to store information in session A and retrieve it after a restart in
session B.

Semantic search (finding memories by meaning rather than exact keyword) is valuable —
it makes retrieval more accurate — but it requires a vector database and an embedding model.

Three storage architectures were considered:

| Alternative | Description |
|---|---|
| **SQLite-only with FTS5** | Single local database; keyword search via SQLite's full-text-search extension |
| **Qdrant-only** | All memories stored and queried via the Qdrant vector database |
| **Split: SQLite + Qdrant** | SQLite as the source of truth; Qdrant as a rebuildable optional index |

---

## Decision

**Split: SQLite is mandatory, Qdrant is optional.**

- **SQLite** (`/workspace/.nikola/.memory/memories.db`) stores all memories persistently.
  It is the single source of truth. Every write goes to SQLite first.
- **Qdrant** stores embeddings of those memories for semantic search. It is populated from
  SQLite and can be rebuilt at any time. When Qdrant is unreachable, the harness automatically
  falls back to SQLite FTS5 keyword search.

---

## Rationale

**The mandatory guarantee requires no runtime dependency.**

The spec requires that a memory stored in session A is retrievable after a restart in session B.
SQLite is a single file on the filesystem — it survives container restarts, does not require
a running service, and has no configuration beyond a file path. Qdrant is a separate service
that may be unavailable (not started, crashed, network issue). Placing the persistence guarantee
in SQLite means the requirement is met regardless of Qdrant's state.

**Qdrant adds semantic search without becoming a hard dependency.**

Keyword search (FTS5) is sufficient for retrieval when the user's query shares words with the
stored memory. Semantic search improves recall when the query is phrased differently from the
stored content. The split architecture delivers semantic search when Qdrant is available and
degrades gracefully to keyword search when it is not — without any data loss.

**Qdrant-only breaks the mandatory restart guarantee.**

If Qdrant is the sole store, a Qdrant crash or a compose startup ordering issue means memories
from prior sessions are unavailable until Qdrant is healthy again. The spec's requirement is
not satisfied.

**SQLite-only loses semantic search quality.**

FTS5 works well for known keywords but misses relevant memories when the user phrases a query
differently. The marginal cost of adding Qdrant as an optional index is low relative to the
retrieval quality improvement it provides.

---

## Consequences

**Startup order.** Qdrant does not need to be running before the agent starts. The harness
attempts to connect and logs a warning if Qdrant is unreachable, then continues with SQLite-only
mode.

**Rebuild.** If Qdrant is reset (e.g. compose volume deleted), its collection can be rebuilt by
re-embedding all rows from SQLite. No memories are lost.

**Write path.** Every `remember()` call writes to SQLite first. If Qdrant is available, the
embedding is written there too. If Qdrant is unavailable during a write, the memory is still
persisted in SQLite (the embedding is skipped, not queued for retry).

**Read path.** On startup, pinned memories are loaded from SQLite. During each turn, the top-3
relevant memories are retrieved via Qdrant semantic search when available, or SQLite FTS5
otherwise.
