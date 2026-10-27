"""Unit tests for agent.memory.store module."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent.memory.store import Memory, MemorySchema, MemoryStore


class TestMemory:
    """Tests for the Memory class."""
    
    def test_memory_creation(self):
        """Memory can be created with required fields."""
        memory = Memory(
            content="Python prefers snake_case",
            kind="preference",
            source="user",
            session_id="test-session-1",
        )
        
        assert memory.content == "Python prefers snake_case"
        assert memory.kind == "preference"
        assert memory.source == "user"
        assert memory.session_id == "test-session-1"
        assert memory.pinned is False
        assert memory.embedded is False
        assert memory.id is None
        assert memory.created_at is not None
    
    def test_memory_with_all_fields(self):
        """Memory can be created with all fields specified."""
        memory = Memory(
            id=42,
            content="Test content",
            kind="fact",
            source="compaction",
            session_id="sess-2",
            pinned=True,
            created_at="2024-01-01T00:00:00Z",
            embedded=True,
        )
        
        assert memory.id == 42
        assert memory.pinned is True
        assert memory.embedded is True
        assert memory.created_at == "2024-01-01T00:00:00Z"


class TestMemorySchema:
    """Tests for the MemorySchema class."""
    
    def test_schema_has_table_definition(self):
        """Schema includes table definition."""
        schema = MemorySchema.get_schema()
        assert "CREATE TABLE IF NOT EXISTS memories" in schema
        assert "CHECK (kind IN ('fact','preference','summary'))" in schema
        assert "CHECK (source IN ('user','compaction'))" in schema
    
    def test_schema_has_fts_table(self):
        """Schema includes FTS5 virtual table."""
        schema = MemorySchema.get_schema()
        assert "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts" in schema
        assert "USING fts5" in schema
    
    def test_schema_has_triggers(self):
        """Schema includes all three FTS5 sync triggers."""
        schema = MemorySchema.get_schema()
        assert "CREATE TRIGGER IF NOT EXISTS memories_ai" in schema  # INSERT
        assert "CREATE TRIGGER IF NOT EXISTS memories_ad" in schema  # DELETE
        assert "CREATE TRIGGER IF NOT EXISTS memories_au" in schema  # UPDATE


class TestMemoryStore:
    """Tests for the MemoryStore class."""
    
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_memories.db"
            yield db_path
    
    @pytest.fixture
    def store(self, temp_db):
        """Create a MemoryStore instance."""
        store = MemoryStore(temp_db)
        yield store
        store.close()
    
    def test_store_initialization(self, temp_db):
        """Store creates database file and tables."""
        store = MemoryStore(temp_db)
        
        assert temp_db.exists()
        
        # Check that tables were created
        cursor = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "memories" in tables
        assert "memories_fts" in tables
        
        store.close()
    
    def test_write_memory(self, store):
        """Write creates a memory with an assigned ID."""
        memory = Memory(
            content="Test fact",
            kind="fact",
            source="user",
            session_id="sess-1",
        )
        
        result = store.write(memory)
        
        assert result.id is not None
        assert result.id > 0
        assert result.content == "Test fact"
        assert result.kind == "fact"
    
    def test_write_memory_invalid_kind(self, store):
        """Write with invalid kind raises IntegrityError."""
        memory = Memory(
            content="Test",
            kind="banana",  # type: ignore
            source="user",
            session_id="sess-1",
        )
        
        with pytest.raises(sqlite3.IntegrityError):
            store.write(memory)
    
    def test_write_memory_invalid_source(self, store):
        """Write with invalid source raises IntegrityError."""
        memory = Memory(
            content="Test",
            kind="fact",
            source="alien",  # type: ignore
            session_id="sess-1",
        )
        
        with pytest.raises(sqlite3.IntegrityError):
            store.write(memory)
    
    def test_update_memory_content(self, store):
        """Update can change memory content."""
        memory = Memory(
            content="Original content",
            kind="fact",
            source="user",
            session_id="sess-1",
        )
        stored = store.write(memory)
        
        updated = store.update(stored.id, content="New content")
        
        assert updated is True
        
        # Verify the change
        cursor = store.conn.execute("SELECT content FROM memories WHERE id = ?", (stored.id,))
        row = cursor.fetchone()
        assert row[0] == "New content"
    
    def test_update_memory_pinned(self, store):
        """Update can change pinned status."""
        memory = Memory(
            content="Test",
            kind="fact",
            source="user",
            session_id="sess-1",
            pinned=False,
        )
        stored = store.write(memory)
        
        updated = store.update(stored.id, pinned=True)
        
        assert updated is True
        
        cursor = store.conn.execute("SELECT pinned FROM memories WHERE id = ?", (stored.id,))
        row = cursor.fetchone()
        assert row[0] == 1
    
    def test_update_nonexistent_memory(self, store):
        """Update of nonexistent memory returns False."""
        updated = store.update(9999, content="New content")
        assert updated is False
    
    def test_replace_summary_for_session(self, store):
        """Replace deletes old summary and writes new one."""
        # Write initial summary
        memory1 = Memory(
            content="Old summary",
            kind="summary",
            source="compaction",
            session_id="sess-1",
        )
        store.write(memory1)
        
        # Replace with new summary
        new_memory = store.replace_summary_for_session("sess-1", "New summary")
        
        assert new_memory.content == "New summary"
        assert new_memory.kind == "summary"
        
        # Verify only one summary exists for this session
        cursor = store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ? AND kind = 'summary'",
            ("sess-1",),
        )
        count = cursor.fetchone()[0]
        assert count == 1
    
    def test_replace_summary_does_not_affect_other_sessions(self, store):
        """Replace summary only affects the specified session."""
        # Write summaries for two sessions
        mem1 = Memory(content="Summary 1", kind="summary", source="compaction", session_id="sess-1")
        mem2 = Memory(content="Summary 2", kind="summary", source="compaction", session_id="sess-2")
        store.write(mem1)
        store.write(mem2)
        
        # Replace summary for sess-1
        store.replace_summary_for_session("sess-1", "New summary 1")
        
        # Verify sess-2 summary is unchanged
        cursor = store.conn.execute(
            "SELECT content FROM memories WHERE session_id = ? AND kind = 'summary'",
            ("sess-2",),
        )
        row = cursor.fetchone()
        assert row[0] == "Summary 2"
    
    def test_get_pinned_empty(self, store):
        """Get pinned returns empty list when no pinned memories."""
        result = store.get_pinned()
        assert result == []
    
    def test_get_pinned(self, store):
        """Get pinned returns only pinned memories."""
        # Write some memories
        mem1 = Memory(content="Pinned 1", kind="fact", source="user", session_id="s1", pinned=True)
        mem2 = Memory(content="Not pinned", kind="fact", source="user", session_id="s1", pinned=False)
        mem3 = Memory(content="Pinned 2", kind="fact", source="user", session_id="s1", pinned=True)
        
        store.write(mem1)
        store.write(mem2)
        store.write(mem3)
        
        result = store.get_pinned()
        
        assert len(result) == 2
        assert all(m.pinned for m in result)
        assert result[0].content == "Pinned 1"
        assert result[1].content == "Pinned 2"
    
    def test_get_pinned_respects_max_count(self, store):
        """Get pinned respects max_count limit."""
        for i in range(15):
            mem = Memory(content=f"Memory {i}", kind="fact", source="user", session_id="s1", pinned=True)
            store.write(mem)
        
        result = store.get_pinned(max_count=5)
        
        assert len(result) == 5
    
    def test_get_pinned_respects_max_chars(self, store):
        """Get pinned stops when max_chars would be exceeded."""
        # Each memory has 10 characters of content
        for i in range(10):
            mem = Memory(content="0123456789", kind="fact", source="user", session_id="s1", pinned=True)
            store.write(mem)
        
        # Set max_chars to 25 - should get 2 memories (20 chars)
        result = store.get_pinned(max_chars=25)
        
        assert len(result) == 2
    
    def test_search_keyword_empty(self, store):
        """Keyword search returns empty list when no matches."""
        result = store.search_keyword("nonexistent")
        assert result == []
    
    def test_search_keyword(self, store):
        """Keyword search finds matching memories."""
        mem1 = Memory(content="Python is great", kind="fact", source="user", session_id="s1")
        mem2 = Memory(content="JavaScript is okay", kind="fact", source="user", session_id="s1")
        mem3 = Memory(content="Python has type hints", kind="fact", source="user", session_id="s1")
        
        store.write(mem1)
        store.write(mem2)
        store.write(mem3)
        
        result = store.search_keyword("Python")
        
        assert len(result) == 2
        assert all("Python" in m.content for m in result)
    
    def test_search_keyword_respects_limit(self, store):
        """Keyword search respects limit parameter."""
        for i in range(10):
            mem = Memory(content=f"Python fact {i}", kind="fact", source="user", session_id="s1")
            store.write(mem)
        
        result = store.search_keyword("Python", limit=3)
        
        assert len(result) == 3
    
    def test_search_keyword_with_filename_in_query(self, store):
        """A prompt naming a file must search, not raise fts5 syntax errors.

        The per-turn read policy passes the user's whole prompt here, and retrieval
        failures are swallowed by the caller — so this raising looked like memory simply
        never matching for the prompts most likely to need it.
        """
        store.write(Memory(
            content="config.yaml sets the context window",
            kind="fact",
            source="user",
            session_id="s1",
        ))

        result = store.search_keyword("what did we change in config.yaml?")

        assert len(result) == 1

    def test_search_keyword_with_only_punctuation(self, store):
        """Text with no searchable term returns empty rather than an empty MATCH."""
        store.write(Memory(content="a fact", kind="fact", source="user", session_id="s1"))

        assert store.search_keyword("?!  -- ...") == []

    def test_search_keyword_matches_any_term(self, store):
        """Terms are ORed: a whole sentence ANDed together would match nothing."""
        store.write(Memory(
            content="The deployment uses Podman",
            kind="fact",
            source="user",
            session_id="s1",
        ))

        result = store.search_keyword("Does the deployment use Docker or Podman?")

        assert len(result) == 1

    def test_to_fts_query_quotes_each_term(self):
        """The conversion is pure and independently checkable."""
        from agent.memory.store import to_fts_query

        assert to_fts_query("config.yaml window") == '"config" OR "yaml" OR "window"'
        assert to_fts_query('"unbalanced') == '"unbalanced"'
        assert to_fts_query("a ?") == ""

    def test_fts5_trigger_on_update(self, store):
        """FTS5 index is updated when memory content changes (CRITICAL TEST)."""
        # Write a memory
        memory = Memory(content="Original wording", kind="fact", source="user", session_id="s1")
        stored = store.write(memory)
        
        # Verify it can be found
        result = store.search_keyword("Original")
        assert len(result) == 1
        
        # Update the content
        store.update(stored.id, content="New different wording")
        
        # Old wording should return zero hits
        result_old = store.search_keyword("Original")
        assert len(result_old) == 0
        
        # New wording should be found
        result_new = store.search_keyword("different")
        assert len(result_new) == 1
        assert result_new[0].content == "New different wording"
    
    def test_get_stats_empty(self, store):
        """Get stats returns zeros for empty store."""
        stats = store.get_stats()
        
        assert stats["total"] == 0
        assert stats["fact_count"] == 0
        assert stats["preference_count"] == 0
        assert stats["summary_count"] == 0
    
    def test_get_stats(self, store):
        """Get stats returns correct counts."""
        store.write(Memory(content="Fact 1", kind="fact", source="user", session_id="s1"))
        store.write(Memory(content="Fact 2", kind="fact", source="user", session_id="s1"))
        store.write(Memory(content="Pref 1", kind="preference", source="user", session_id="s1"))
        store.write(Memory(content="Summary 1", kind="summary", source="compaction", session_id="s1"))
        
        stats = store.get_stats()
        
        assert stats["total"] == 4
        assert stats["fact_count"] == 2
        assert stats["preference_count"] == 1
        assert stats["summary_count"] == 1
    
    def test_get_unembedded(self, store):
        """Get unembedded returns only memories with embedded=False."""
        mem1 = Memory(content="Not embedded", kind="fact", source="user", session_id="s1")
        mem2 = Memory(content="Already embedded", kind="fact", source="user", session_id="s1", embedded=True)
        
        stored1 = store.write(mem1)
        _ = store.write(mem2)
        
        result = store.get_unembedded()
        
        assert len(result) == 1
        assert result[0].id == stored1.id
        assert result[0].embedded is False
    
    def test_get_unembedded_after_update(self, store):
        """Get unembedded reflects updated embedded status."""
        mem = Memory(content="Test", kind="fact", source="user", session_id="s1")
        stored = store.write(mem)
        
        # Initially unembedded
        result = store.get_unembedded()
        assert len(result) == 1
        
        # Mark as embedded
        store.update(stored.id, embedded=True)
        
        # Should no longer appear
        result = store.get_unembedded()
        assert len(result) == 0


class TestMemoryStoreRestart:
    """Tests that verify persistence across store restarts."""
    
    def test_write_close_reopen_read(self, tmp_path):
        """Memory persists after closing and reopening the store (restart requirement)."""
        db_path = tmp_path / "persistent.db"
        
        # Write a memory
        store1 = MemoryStore(db_path)
        memory = Memory(content="Persisted fact", kind="fact", source="user", session_id="sess-1")
        stored = store1.write(memory)
        memory_id = stored.id
        store1.close()
        
        # Reopen and read
        store2 = MemoryStore(db_path)
        result = store2.search_keyword("Persisted")
        
        assert len(result) == 1
        assert result[0].id == memory_id
        assert result[0].content == "Persisted fact"
        
        store2.close()
