"""E2E tests for long-term memory persistence across restarts."""



from agent.memory.store import Memory, MemoryStore
from agent.memory.tools import make_remember_tool, make_search_memory_tool


class TestMemoryPersistence:
    """End-to-end tests verifying memory survives application restarts."""
    
    def test_remember_and_search_across_restart(self, tmp_path):
        """Session 1 remembers, restart, Session 2 retrieves it (E2E requirement)."""
        db_path = tmp_path / "agent_memory.db"
        
        # ===== Session 1: Remember something =====
        store1 = MemoryStore(db_path)
        remember_tool = make_remember_tool(store1, session_id="session-1")
        remember_fn = remember_tool["fn"]
        
        # User remembers a fact
        result = remember_fn(
            content="The project uses Python 3.12 only",
            kind="fact",
            pinned=False,
        )
        assert "Remembered as fact" in result
        assert not result.startswith("Error:")
        
        store1.close()
        
        # ===== Simulate application restart =====
        # (store1 is closed, process would exit here)
        
        # ===== Session 2: Search for the fact =====
        store2 = MemoryStore(db_path)
        search_tool = make_search_memory_tool(store2)
        search_fn = search_tool["fn"]
        
        # Search should find the memory from session 1
        result = search_fn(query="Python")
        
        assert "Python" in result
        assert "3.12" in result
        assert not result.startswith("Error:")
        assert "No relevant memories found" not in result
        
        store2.close()
    
    def test_pinned_memory_survives_restart(self, tmp_path):
        """Pinned memories persist and can be retrieved at startup."""
        db_path = tmp_path / "agent_memory.db"
        
        # Session 1: Create pinned memory
        store1 = MemoryStore(db_path)
        remember_tool = make_remember_tool(store1, session_id="session-1")
        remember_fn = remember_tool["fn"]
        
        result = remember_fn(
            content="Always use type hints in function signatures",
            kind="preference",
            pinned=True,
        )
        assert "pinned" in result
        
        store1.close()
        
        # Restart
        store2 = MemoryStore(db_path)
        
        # Get pinned memories (simulates startup injection)
        pinned = store2.get_pinned()
        
        assert len(pinned) == 1
        assert pinned[0].content == "Always use type hints in function signatures"
        assert pinned[0].kind == "preference"
        assert pinned[0].pinned is True
        
        store2.close()
    
    def test_summary_replacement_across_sessions(self, tmp_path):
        """Compaction summaries are replaced, not appended, across sessions."""
        db_path = tmp_path / "agent_memory.db"
        
        # Session 1: Create initial summary
        store1 = MemoryStore(db_path)
        store1.replace_summary_for_session("session-1", "First summary of work done")
        
        stats1 = store1.get_stats()
        assert stats1["summary_count"] == 1
        
        store1.close()
        
        # Restart - Session 2: Replace the summary
        store2 = MemoryStore(db_path)
        store2.replace_summary_for_session("session-1", "Updated summary with more context")
        
        stats2 = store2.get_stats()
        assert stats2["summary_count"] == 1  # Still only 1, not 2
        
        # Verify the content is the new one
        results = store2.search_keyword("Updated summary")
        assert len(results) == 1
        assert "Updated summary" in results[0].content
        
        # Old summary should not be found. Searched by the word unique to it: keyword terms
        # are ORed, so "First summary" would match the surviving row on "summary" alone.
        old_results = store2.search_keyword("First")
        assert len(old_results) == 0
        
        store2.close()
    
    def test_multiple_kinds_persist(self, tmp_path):
        """Facts, preferences, and summaries all persist across restart."""
        db_path = tmp_path / "agent_memory.db"
        
        # Session 1: Store different kinds
        store1 = MemoryStore(db_path)
        
        fact = Memory(
            content="The workspace is at /workspace",
            kind="fact",
            source="user",
            session_id="s1",
        )
        pref = Memory(
            content="Prefer explicit over implicit",
            kind="preference",
            source="user",
            session_id="s1",
        )
        summ = Memory(
            content="Configured the agent and ran first test",
            kind="summary",
            source="compaction",
            session_id="s1",
        )
        
        store1.write(fact)
        store1.write(pref)
        store1.write(summ)
        
        store1.close()
        
        # Restart
        store2 = MemoryStore(db_path)
        
        stats = store2.get_stats()
        assert stats["total"] == 3
        assert stats["fact_count"] == 1
        assert stats["preference_count"] == 1
        assert stats["summary_count"] == 1
        
        # All should be searchable
        assert len(store2.search_keyword("workspace")) == 1
        assert len(store2.search_keyword("explicit")) == 1
        assert len(store2.search_keyword("configured")) == 1
        
        store2.close()
    
    def test_empty_content_rejected_consistently(self, tmp_path):
        """Empty content is rejected before and after restart."""
        db_path = tmp_path / "agent_memory.db"
        
        store1 = MemoryStore(db_path)
        remember_tool = make_remember_tool(store1, session_id="s1")
        remember_fn = remember_tool["fn"]
        
        # Try to remember empty content
        result = remember_fn(content="", kind="fact")
        assert result.startswith("Error:")
        assert "empty" in result.lower()
        
        # Verify nothing was stored
        stats1 = store1.get_stats()
        assert stats1["total"] == 0
        
        store1.close()
        
        # Restart - behavior should be the same
        store2 = MemoryStore(db_path)
        remember_tool2 = make_remember_tool(store2, session_id="s1")
        remember_fn2 = remember_tool2["fn"]
        
        result2 = remember_fn2(content="   ", kind="fact")
        assert result2.startswith("Error:")
        assert "whitespace" in result2.lower()
        
        stats2 = store2.get_stats()
        assert stats2["total"] == 0
        
        store2.close()
    
    def test_invalid_kind_rejected(self, tmp_path):
        """Invalid kind parameter is rejected with clear error."""
        db_path = tmp_path / "agent_memory.db"
        
        store = MemoryStore(db_path)
        remember_tool = make_remember_tool(store, session_id="s1")
        remember_fn = remember_tool["fn"]
        
        result = remember_fn(content="Test content", kind="banana")
        
        assert result.startswith("Error:")
        assert "kind must be one of" in result
        assert "fact" in result
        assert "preference" in result
        assert "summary" in result
        
        # Nothing should be stored
        stats = store.get_stats()
        assert stats["total"] == 0
        
        store.close()
    
    def test_search_limit_parameter(self, tmp_path):
        """Search respects limit parameter across restart."""
        db_path = tmp_path / "agent_memory.db"
        
        # Create many memories
        store1 = MemoryStore(db_path)
        for i in range(10):
            mem = Memory(
                content=f"Python fact number {i}",
                kind="fact",
                source="user",
                session_id="s1",
            )
            store1.write(mem)
        
        store1.close()
        
        # Restart and search with limit
        store2 = MemoryStore(db_path)
        search_tool = make_search_memory_tool(store2)
        search_fn = search_tool["fn"]
        
        # Default limit (3)
        result_default = search_fn(query="Python")
        _ = result_default.split("\n")
        # Should have header + 3 results
        assert "Found 3 relevant memor" in result_default
        
        # Custom limit
        result_custom = search_fn(query="Python", limit=5)
        assert "Found 5 relevant memor" in result_custom
        
        store2.close()


class TestMemoryIntegration:
    """Integration tests for memory with other components."""
    
    def test_memory_store_location_outside_workspace(self, tmp_path):
        """Memory database is stored outside the workspace sandbox."""
        # This simulates the requirement that resolve_safe_path must reject the db path
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        
        memory_db = tmp_path / ".agent" / "memory.db"
        
        # Memory store can write to .agent directory
        store = MemoryStore(memory_db)
        mem = Memory(content="Test", kind="fact", source="user", session_id="s1")
        store.write(mem)
        
        assert memory_db.exists()
        # Verify it's NOT under workspace
        assert not str(memory_db).startswith(str(workspace))
        
        store.close()
