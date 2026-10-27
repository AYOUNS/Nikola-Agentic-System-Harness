"""SQLite-backed memory store with FTS5 keyword search.

The store is the source of truth for all memories. It persists to a local file
and survives container restarts. Qdrant (when available) is an index only, not
a record store.

Schema:
- memories: main table with content, kind, source, session_id, timestamps
- memories_fts: FTS5 virtual table for keyword search
- Triggers keep FTS5 in sync with the main table

All operations return strings (result or "Error: ...") and never raise into the
loop.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent.memory.instrumentation import memory_operation

logger = logging.getLogger(__name__)

MemoryKind = Literal["fact", "preference", "summary"]
MemorySource = Literal["user", "compaction"]

_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")
"""Word characters only. Everything FTS5 treats as syntax is therefore excluded."""


def to_fts_query(query: str) -> str:
    """Convert natural-language text into a safe FTS5 MATCH expression.

    The per-turn read policy searches with the user's whole prompt, which is not FTS5
    syntax: a dot, hyphen or quote in it ("what changed in config.yaml?") raises
    OperationalError, and because per-turn retrieval swallows its errors, that surfaced as
    memory silently never being injected for exactly the prompts that name a file.

    Terms are joined with OR rather than FTS5's implicit AND. A whole sentence ANDed
    together matches nothing; OR with rank ordering is what makes the top-N meaningful.

    Args:
        query: Arbitrary user text

    Returns:
        A quoted OR-expression, or "" if the text holds no searchable term — the caller
        should then skip the query rather than send an empty MATCH.
    """
    terms = _FTS_TOKEN.findall(query or "")
    return " OR ".join(f'"{term}"' for term in terms)


class Memory:
    """A single memory record.
    
    Attributes:
        id: Primary key (None for unsaved memories)
        content: The memory text
        kind: Classification (fact/preference/summary)
        pinned: Whether this memory is injected at startup
        source: Origin (user/compaction)
        session_id: Session that created this memory
        created_at: ISO timestamp
        embedded: Whether this memory has been embedded to Qdrant
    """
    
    def __init__(
        self,
        content: str,
        kind: MemoryKind,
        source: MemorySource,
        session_id: str,
        pinned: bool = False,
        id: int | None = None,
        created_at: str | None = None,
        embedded: bool = False,
    ):
        """Initialize a Memory instance.
        
        Args:
            content: The memory text
            kind: Classification (fact/preference/summary)
            source: Origin (user/compaction)
            session_id: Session that created this memory
            pinned: Whether this memory is injected at startup
            id: Primary key (None for unsaved memories)
            created_at: ISO timestamp (auto-generated if None)
            embedded: Whether this memory has been embedded to Qdrant
        """
        self.id = id
        self.content = content
        self.kind = kind
        self.pinned = pinned
        self.source = source
        self.session_id = session_id
        self.created_at = created_at or datetime.now(UTC).isoformat()
        self.embedded = embedded
    
    def __repr__(self) -> str:
        return f"Memory(id={self.id}, kind={self.kind}, pinned={self.pinned}, content={self.content[:50]}...)"


class MemorySchema:
    """Database schema for the memory store."""
    
    TABLE_DEFINITION = """
    CREATE TABLE IF NOT EXISTS memories (
      id         INTEGER PRIMARY KEY,
      content    TEXT NOT NULL,
      kind       TEXT NOT NULL CHECK (kind IN ('fact','preference','summary')),
      pinned     INTEGER NOT NULL DEFAULT 0,
      source     TEXT NOT NULL CHECK (source IN ('user','compaction')),
      session_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      embedded   INTEGER NOT NULL DEFAULT 0
    );
    """
    
    FTS_TABLE_DEFINITION = """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
      USING fts5(content, content='memories', content_rowid='id');
    """
    
    INSERT_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
    """
    
    DELETE_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES('delete', old.id, old.content);
    END;
    """
    
    UPDATE_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES('delete', old.id, old.content);
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
    """
    
    @classmethod
    def get_schema(cls) -> str:
        """Get the complete schema DDL as a single script.
        
        Returns:
            SQL script to create all tables and triggers
        """
        return f"{cls.TABLE_DEFINITION}\n{cls.FTS_TABLE_DEFINITION}\n{cls.INSERT_TRIGGER}\n{cls.DELETE_TRIGGER}\n{cls.UPDATE_TRIGGER}"


class MemoryStore:
    """SQLite-backed memory store with FTS5 keyword search.
    
    The store persists to a local database file and survives container restarts.
    All methods return results or error strings, never raising into the loop.
    """
    
    def __init__(self, db_path: str | Path):
        """Initialize the memory store.
        
        Creates the database file and tables if they don't exist.
        Enables WAL mode for better concurrency.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(MemorySchema.get_schema())
        self.conn.commit()
        
        logger.info(f"Memory store initialized at {self.db_path}")
    
    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
    
    def write(self, memory: Memory) -> Memory:
        """Write a new memory to the store.
        
        Args:
            memory: Memory instance to write
            
        Returns:
            Memory object with assigned ID
            
        Raises:
            sqlite3.IntegrityError: If kind or source is invalid
        """
        with memory_operation("write", "sqlite") as op:
            op.session_id = memory.session_id
            return self._insert(memory)
    
    def _insert(self, memory: Memory) -> Memory:
        """Insert a memory row and assign its ID (uninstrumented)."""
        cursor = self.conn.execute(
            """
            INSERT INTO memories (content, kind, source, session_id, created_at, pinned, embedded)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.content,
                memory.kind,
                memory.source,
                memory.session_id,
                memory.created_at,
                1 if memory.pinned else 0,
                1 if memory.embedded else 0,
            ),
        )
        self.conn.commit()
        
        memory.id = cursor.lastrowid
        return memory
    
    def update(
        self,
        memory_id: int,
        content: str | None = None,
        pinned: bool | None = None,
        embedded: bool | None = None,
    ) -> bool:
        """Update fields of an existing memory.
        
        Args:
            memory_id: ID of memory to update
            content: New content (or None to keep current)
            pinned: New pinned status (or None to keep current)
            embedded: New embedded status (or None to keep current)
            
        Returns:
            True if a row was updated, False if memory_id not found
        """
        updates = []
        params = []
        
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if pinned is not None:
            updates.append("pinned = ?")
            params.append(1 if pinned else 0)
        if embedded is not None:
            updates.append("embedded = ?")
            params.append(1 if embedded else 0)
        
        if not updates:
            return True  # No changes requested
        
        params.append(memory_id)
        with memory_operation("update", "sqlite") as op:
            cursor = self.conn.execute(
                f"UPDATE memories SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self.conn.commit()
            op.success = cursor.rowcount > 0
        
        return cursor.rowcount > 0
    
    def get_summary_ids_for_session(self, session_id: str) -> list[int]:
        """Return the ids of this session's summary rows.

        replace_summary_for_session deletes those rows. SQLite is the source of truth and
        does not know Qdrant exists, so the caller reads the ids first and evicts the
        matching vectors itself — otherwise the vectors outlive their rows and a semantic
        search spends a top-k slot on an id that resolves to nothing.

        Args:
            session_id: Session ID to look up

        Returns:
            Ids of existing summary rows for that session (empty if none)
        """
        cursor = self.conn.execute(
            "SELECT id FROM memories WHERE session_id = ? AND kind = 'summary'",
            (session_id,),
        )
        return [row[0] for row in cursor.fetchall()]

    def replace_summary_for_session(
        self,
        session_id: str,
        new_content: str,
    ) -> Memory:
        """Replace any existing summary for this session with a new one.
        
        Compaction generates summaries incrementally. This ensures only the latest
        summary is kept, not a stack of overlapping ones.
        
        Args:
            session_id: Session ID to replace summary for
            new_content: New summary text
            
        Returns:
            Memory object for the new summary
        """
        with memory_operation("replace_summary", "sqlite") as op:
            op.session_id = session_id
            
            # Delete any existing summaries for this session
            self.conn.execute(
                "DELETE FROM memories WHERE session_id = ? AND kind = 'summary'",
                (session_id,),
            )
            
            # Write the new summary
            memory = Memory(
                content=new_content,
                kind="summary",
                source="compaction",
                session_id=session_id,
                pinned=False,
            )
            return self._insert(memory)
    
    def get_pinned(self, max_count: int = 10, max_chars: int = 2000) -> list[Memory]:
        """Retrieve pinned memories for startup injection.
        
        Returns up to max_count memories, stopping early if total content exceeds
        max_chars. Ordered by creation time (oldest first).
        
        Args:
            max_count: Maximum number of memories to return
            max_chars: Maximum total characters across all memories
            
        Returns:
            List of Memory objects
        """
        with memory_operation("get_pinned", "sqlite") as op:
            cursor = self.conn.execute(
                """
                SELECT id, content, kind, pinned, source, session_id, created_at, embedded
                FROM memories
                WHERE pinned = 1
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max_count,),
            )
        
            memories = []
            total_chars = 0
        
            for row in cursor:
                content_len = len(row[1])
                if total_chars + content_len > max_chars and memories:
                    # Would exceed budget, stop here
                    break
            
                memories.append(Memory(
                    id=row[0],
                    content=row[1],
                    kind=row[2],
                    pinned=bool(row[3]),
                    source=row[4],
                    session_id=row[5],
                    created_at=row[6],
                    embedded=bool(row[7]),
                ))
                total_chars += content_len
        
            op.results = len(memories)
            return memories
    
    def search_keyword(self, query: str, limit: int = 3) -> list[Memory]:
        """Search memories using FTS5 keyword search.

        This is the fallback when Qdrant is unavailable or embedding fails.

        Args:
            query: Natural-language text, not FTS5 syntax. It is converted by
                to_fts_query before it reaches MATCH — see that function for why.
            limit: Maximum number of results

        Returns:
            List of Memory objects, ordered by FTS5 rank. Empty if the query has no
            searchable terms.
        """
        match_query = to_fts_query(query)
        if not match_query:
            return []

        with memory_operation("search", "sqlite") as op:
            cursor = self.conn.execute(
                """
                SELECT m.id, m.content, m.kind, m.pinned, m.source, m.session_id, m.created_at, m.embedded
                FROM memories_fts f
                JOIN memories m ON f.rowid = m.id
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_query, limit),
            )
        
            memories = [
                Memory(
                    id=row[0],
                    content=row[1],
                    kind=row[2],
                    pinned=bool(row[3]),
                    source=row[4],
                    session_id=row[5],
                    created_at=row[6],
                    embedded=bool(row[7]),
                )
                for row in cursor
            ]
            op.results = len(memories)
            return memories
    
    def get_stats(self) -> dict[str, int]:
        """Get statistics about stored memories.
        
        Returns:
            Dict with keys: total, fact_count, preference_count, summary_count
        """
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN kind = 'fact' THEN 1 ELSE 0 END) as fact_count,
                   SUM(CASE WHEN kind = 'preference' THEN 1 ELSE 0 END) as preference_count,
                   SUM(CASE WHEN kind = 'summary' THEN 1 ELSE 0 END) as summary_count
            FROM memories
            """
        )
        row = cursor.fetchone()
        
        return {
            "total": row[0] or 0,
            "fact_count": row[1] or 0,
            "preference_count": row[2] or 0,
            "summary_count": row[3] or 0,
        }
    
    def get_by_id(self, memory_id: int) -> Memory | None:
        """Retrieve a single memory record by its primary key.

        Used by search_memory tool to resolve Qdrant result IDs to full records,
        replacing the raw store.conn.execute workaround.

        Args:
            memory_id: SQLite row id.

        Returns:
            Memory object, or None if memory_id does not exist.
        """
        cursor = self.conn.execute(
            """
            SELECT id, content, kind, pinned, source, session_id, created_at, embedded
            FROM memories WHERE id = ?
            """,
            (memory_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return Memory(
            id=row[0],
            content=row[1],
            kind=row[2],
            pinned=bool(row[3]),
            source=row[4],
            session_id=row[5],
            created_at=row[6],
            embedded=bool(row[7]),
        )

    def get_unembedded(self, limit: int = 100) -> list[Memory]:
        """Retrieve memories that haven't been embedded yet.
        
        Used for backfilling Qdrant after embedding failures.
        
        Args:
            limit: Maximum number of memories to return
            
        Returns:
            List of Memory objects with embedded=False
        """
        cursor = self.conn.execute(
            """
            SELECT id, content, kind, pinned, source, session_id, created_at, embedded
            FROM memories
            WHERE embedded = 0
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        
        return [
            Memory(
                id=row[0],
                content=row[1],
                kind=row[2],
                pinned=bool(row[3]),
                source=row[4],
                session_id=row[5],
                created_at=row[6],
                embedded=bool(row[7]),
            )
            for row in cursor
        ]
