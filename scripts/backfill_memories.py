#!/usr/bin/env python3
"""Backfill unembedded memories from SQLite to Qdrant.

This script finds memories in SQLite that haven't been embedded yet and
embeds them to Qdrant. It's safe to run multiple times (idempotent).
"""

import sys
import time
from pathlib import Path

# Add parent directory to path to import agent modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.config import load_config
from agent.memory.store import MemoryStore
from agent.memory.vectors import VectorStore, make_embedding_function


def wait_for_qdrant(url: str, max_attempts: int = 30, delay: float = 1.0) -> bool:
    """Wait for Qdrant to be ready."""
    import urllib.request
    import urllib.error
    
    for attempt in range(1, max_attempts + 1):
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=2)
            return True
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError):
            if attempt < max_attempts:
                time.sleep(delay)
    return False


def backfill_memories() -> int:
    """Backfill unembedded memories from SQLite to Qdrant.
    
    Returns:
        Exit code: 0 for success, 1 for failure
    """
    try:
        # Load configuration
        cfg = load_config()
        
        # Get memory settings - try both container paths
        memory_db_paths = [
            Path("/workspace/.nikola/.memory/memories.db"),
            Path("/data/.nikola/.memory/memories.db"),
            Path("./workspace/.nikola/.memory/memories.db"),
        ]
        
        memory_db_path = None
        for path in memory_db_paths:
            if path.exists():
                memory_db_path = path
                break
        
        if memory_db_path is None:
            print("No memory database found - skipping backfill")
            return 0
        
        print(f"Found memory database at: {memory_db_path}")
        
        # Get Qdrant settings
        qdrant_url = cfg.get("memory", {}).get("qdrant", {}).get("url", "http://qdrant:6333")
        collection_name = cfg.get("memory", {}).get("qdrant", {}).get("collection", "agent_memories")
        vector_size = 4096
        
        # Wait for Qdrant
        if not wait_for_qdrant(qdrant_url):
            print("ERROR: Qdrant is not available")
            return 1
        
        # Initialize stores
        memory_store = MemoryStore(memory_db_path)
        embedding_fn = make_embedding_function(cfg["llm"])
        
        vector_store = VectorStore(
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            embedding_fn=embedding_fn,
            vector_size=vector_size,
        )
        
        if not vector_store.is_available():
            print("WARNING: Vector store not available - memories will use keyword search")
            return 0
        
        # Get unembedded memories
        unembedded = memory_store.get_unembedded(limit=1000)
        
        if not unembedded:
            print("✓ All memories are already embedded")
            return 0
        
        print(f"Found {len(unembedded)} unembedded memories - backfilling...")
        
        # Embed each one
        success_count = 0
        for memory in unembedded:
            if vector_store.upsert(memory.id, memory.content):
                memory_store.update(memory.id, embedded=True)
                success_count += 1
                print(f"  ✓ Embedded memory {memory.id}: {memory.content[:60]}...")
            else:
                print(f"  ✗ Failed to embed memory {memory.id}")
        
        print(f"✓ Backfill complete: {success_count}/{len(unembedded)} memories embedded")
        return 0
            
    except Exception as e:
        print(f"ERROR: Failed to backfill memories: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(backfill_memories())
