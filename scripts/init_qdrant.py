#!/usr/bin/env python3
"""Initialize Qdrant collection for agent memory storage.

This script ensures the agent_memories collection exists in Qdrant before the
agent starts. It's idempotent - safe to run multiple times.
"""

import sys
import time
from pathlib import Path

# Add parent directory to path to import agent modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.config import load_config
from agent.memory.vectors import VectorStore, make_embedding_function


def wait_for_qdrant(url: str, max_attempts: int = 30, delay: float = 1.0) -> bool:
    """Wait for Qdrant to be ready.
    
    Args:
        url: Qdrant server URL
        max_attempts: Maximum connection attempts
        delay: Delay between attempts in seconds
        
    Returns:
        True if Qdrant is ready, False otherwise
    """
    import urllib.request
    import urllib.error
    
    print(f"Waiting for Qdrant at {url}...")
    
    for attempt in range(1, max_attempts + 1):
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=2)
            print(f"✓ Qdrant is ready (attempt {attempt})")
            return True
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError):
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                print(f"✗ Qdrant not ready after {max_attempts} attempts")
                return False
    
    return False


def init_qdrant_collection() -> int:
    """Initialize Qdrant collection for agent memories.
    
    Returns:
        Exit code: 0 for success, 1 for failure
    """
    try:
        # Load configuration
        cfg = load_config()
        
        # Get Qdrant settings from config
        qdrant_url = cfg.get("memory", {}).get("qdrant", {}).get("url", "http://qdrant:6333")
        collection_name = cfg.get("memory", {}).get("qdrant", {}).get("collection", "agent_memories")
        vector_size = 4096  # octen-embedding-8b dimension
        
        print(f"Initializing Qdrant collection '{collection_name}'...")
        
        # Wait for Qdrant to be ready
        if not wait_for_qdrant(qdrant_url):
            print("ERROR: Qdrant is not available")
            return 1
        
        # Create embedding function
        embedding_fn = make_embedding_function(cfg["llm"])
        
        # Initialize VectorStore (creates collection if it doesn't exist)
        vector_store = VectorStore(
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            embedding_fn=embedding_fn,
            vector_size=vector_size,
        )
        
        if vector_store.is_available():
            print(f"✓ Collection '{collection_name}' is ready ({vector_size}-dimensional vectors)")
            
            # Now backfill any unembedded memories
            print("\nBackfilling unembedded memories...")
            from pathlib import Path
            import subprocess
            
            backfill_script = Path(__file__).parent / "backfill_memories.py"
            result = subprocess.run(
                ["python3", str(backfill_script)],
                capture_output=False,
            )
            
            if result.returncode != 0:
                print("WARNING: Memory backfill failed, but collection is ready")
            
            return 0
        else:
            print(f"✗ Failed to initialize collection '{collection_name}'")
            return 1
            
    except Exception as e:
        print(f"ERROR: Failed to initialize Qdrant: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(init_qdrant_collection())
