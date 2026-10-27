"""Long-term memory component for persistent cross-session storage.

Implements SQLite-backed storage with optional Qdrant vector indexing for
semantic search. Falls back to FTS5 keyword search when Qdrant is unavailable.

Components:
- store.py: SQLite schema, write/read operations, keyword search
- vectors.py: Qdrant client, embedding, similarity search
- policy.py: Write/read decision rules and constants
- tools.py: remember and search_memory tools for the agent
"""

import logging


def persist_summary_to_memory(
    summary: str,
    session_id: str,
    memory_store,
    vector_store=None,
) -> None:
    """Persist a compaction summary to long-term memory.

    This is called by both compaction paths — the loop's budget-triggered compaction and
    the operator's /compact — and never raises into either. A failure to remember must not
    cost the caller its conversation.

    Args:
        summary: The summary text to store
        session_id: Current session ID
        memory_store: MemoryStore instance (or None)
        vector_store: Optional VectorStore, so a promoted summary is semantically
            searchable like an explicitly remembered fact. Omitting it leaves the summary
            keyword-searchable only.
    """
    if memory_store is None:
        return

    from agent.memory.policy import should_store_content

    logger = logging.getLogger(__name__)

    # Validate content
    should_store, reason = should_store_content(summary)
    if not should_store:
        logger.debug(f"Skipping summary storage: {reason}")
        return

    try:
        # The ids are read before the replace, because afterwards the rows are gone and
        # their vectors would have nothing left to point at.
        stale_ids = (
            memory_store.get_summary_ids_for_session(session_id)
            if vector_store is not None
            else []
        )

        # Replace any existing summary for this session
        memory = memory_store.replace_summary_for_session(session_id, summary)
        logger.info(f"Persisted compaction summary to long-term memory (session: {session_id})")
    except Exception as e:
        logger.warning(f"Failed to persist summary to memory: {e}")
        return

    # The summary is durable in SQLite from here on. Indexing it is an enhancement, so its
    # failures are logged and swallowed separately rather than reported as a failed write.
    if vector_store is None:
        return
    try:
        if not vector_store.is_available():
            return
        for stale_id in stale_ids:
            vector_store.delete(stale_id)
        if memory.id and vector_store.upsert(memory.id, summary):
            memory_store.update(memory.id, embedded=True)
    except Exception as e:
        logger.warning(f"Failed to index promoted summary (it remains keyword-searchable): {e}")


def backfill_embeddings(memory_store, vector_store, limit: int = 100) -> int:
    """Embed memories that were written while the vector store was unavailable.

    The embedded flag exists so a Qdrant outage costs semantic search rather than the
    memory itself: writes land in SQLite with embedded=False and are indexed later. This
    is the "later" — run it once at startup, after the vector store reports available.

    Args:
        memory_store: MemoryStore instance (or None)
        vector_store: VectorStore instance (or None)
        limit: Maximum number of memories to index in one sweep

    Returns:
        Number of memories successfully indexed (0 if either store is absent, the vector
        store is unreachable, or nothing was pending).
    """
    if memory_store is None or vector_store is None:
        return 0

    logger = logging.getLogger(__name__)

    try:
        if not vector_store.is_available():
            return 0
        pending = memory_store.get_unembedded(limit=limit)
    except Exception as e:
        logger.warning(f"Could not read unembedded memories: {e}")
        return 0

    indexed = 0
    for memory in pending:
        try:
            if memory.id and vector_store.upsert(memory.id, memory.content):
                memory_store.update(memory.id, embedded=True)
                indexed += 1
        except Exception as e:
            # One unembeddable record must not abandon the rest of the sweep.
            logger.warning(f"Backfill failed for memory {memory.id}: {e}")

    if indexed:
        logger.info(f"Backfilled {indexed} memory embedding(s) into the vector store")
    return indexed
