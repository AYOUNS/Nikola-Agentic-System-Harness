"""Qdrant vector search integration for semantic memory retrieval.

This module provides embedding and similarity search via Qdrant. It's an
optional enhancement over FTS5 keyword search - the memory system works
without it, and failures gracefully degrade to keyword search.

All operations catch exceptions and return empty results rather than raising,
so Qdrant unavailability never crashes the agent.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

from agent.memory.instrumentation import memory_operation

logger = logging.getLogger(__name__)


class VectorStore:
    """Qdrant-backed vector search for semantic memory retrieval.
    
    Handles embedding, upserting vectors, and similarity search. Designed to
    fail gracefully - all methods return empty/False on error rather than raising.
    """
    
    def __init__(
        self,
        qdrant_url: str,
        collection_name: str,
        embedding_fn: callable,
        vector_size: int = 4096,
    ):
        """Initialize the vector store.
        
        Args:
            qdrant_url: Qdrant server URL (e.g. http://qdrant:6333)
            collection_name: Collection name for this agent's vectors
            embedding_fn: Callable that takes text and returns a vector
            vector_size: Dimension of embedding vectors (default: 4096 for octen-embedding-8b)
        """
        self.qdrant_url = qdrant_url
        self.collection_name = collection_name
        self.embedding_fn = embedding_fn
        self.vector_size = vector_size
        self.client = None
        self._available = False
        
        self._initialize()
    
    def _initialize(self) -> None:
        """Initialize Qdrant client and create collection if needed.
        
        Catches all errors and sets _available=False on failure.
        """
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            
            # qdrant-client warns on stderr when it cannot reach the server to
            # compare versions — the very case the except clauses below already
            # handle. Captured so it lands in the log rather than above the
            # banner, where a bare traceback-shaped warning reads as a crash.
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.client = QdrantClient(url=self.qdrant_url, timeout=5.0)
            for entry in caught:
                logger.warning(f"qdrant-client: {entry.message}")

            # Check connection
            self.client.get_collections()
            
            # Create collection if it doesn't exist
            collections = self.client.get_collections().collections
            if not any(c.name == self.collection_name for c in collections):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection: {self.collection_name}")
            
            self._available = True
            logger.info(f"Qdrant vector store initialized at {self.qdrant_url}")
            
        except ImportError:
            logger.warning("qdrant-client not installed - vector search unavailable")
            self._available = False
        except Exception as e:
            logger.warning(f"Qdrant unavailable ({e}) - will use keyword search fallback")
            self._available = False
    
    def close(self) -> None:
        """Explicitly close the Qdrant HTTP client.

        Call this before process exit so the client tears down while Python's
        import system is still intact, avoiding the ``__del__``-triggered
        ``ImportError: sys.meta_path is None`` noise on shutdown.
        """
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def is_available(self) -> bool:
        """Check if Qdrant is available for use.
        
        Returns:
            True if Qdrant is connected and ready
        """
        return self._available
    
    def upsert(self, memory_id: int, content: str) -> bool:
        """Embed and upsert a memory into Qdrant.
        
        Args:
            memory_id: SQLite row ID (used as Qdrant point ID)
            content: Memory text to embed
            
        Returns:
            True if upsert succeeded, False on any error
        """
        if not self._available or not self.client:
            return False
        
        with memory_operation("write", "qdrant") as op:
            try:
                # Get embedding
                vector = self.embedding_fn(content)
                if not vector:
                    logger.debug(f"Embedding failed for memory {memory_id}")
                    op.success = False
                    return False
                
                # Upsert point
                from qdrant_client.models import PointStruct
                
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        PointStruct(
                            id=memory_id,
                            vector=vector,
                            payload={"content": content},
                        )
                    ],
                )
                return True
                
            except Exception as e:
                logger.warning(f"Failed to upsert memory {memory_id} to Qdrant: {e}")
                op.success = False
                return False
    
    def search(self, query: str, limit: int = 3) -> list[int]:
        """Search for similar memories using semantic similarity.
        
        Args:
            query: Search query text
            limit: Maximum number of results
            
        Returns:
            List of memory IDs (SQLite row IDs), ordered by similarity.
            Empty list on any error (including Qdrant unavailable).
        """
        if not self._available or not self.client:
            return []
        
        with memory_operation("search", "qdrant") as op:
            try:
                # Embed query
                vector = self.embedding_fn(query)
                if not vector:
                    logger.debug("Query embedding failed")
                    op.success = False
                    return []
                
                # Search using query_points (qdrant-client v1.x API)
                results = self.client.query_points(
                    collection_name=self.collection_name,
                    query=vector,
                    limit=limit,
                )
                
                # Extract IDs - results is a QueryResponse with points list
                ids = [point.id for point in results.points]
                op.results = len(ids)
                return ids
                
            except Exception as e:
                logger.warning(f"Qdrant search failed: {e}")
                op.success = False
                return []
    
    def delete(self, memory_id: int) -> bool:
        """Delete a memory vector from Qdrant.
        
        Args:
            memory_id: Memory ID to delete
            
        Returns:
            True if delete succeeded or point didn't exist, False on error
        """
        if not self._available or not self.client:
            return False
        
        with memory_operation("delete", "qdrant") as op:
            try:
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=[memory_id],
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to delete memory {memory_id} from Qdrant: {e}")
                op.success = False
                return False


def make_embedding_function(llm_config: dict[str, Any]) -> callable:
    """Create an embedding function using InnKube's embedding model.
    
    Args:
        llm_config: Config dict with base_url and embedding model name
        
    Returns:
        Callable that takes text and returns a vector (or None on error)
    """
    import os

    from openai import OpenAI
    
    # Use embedding model from config or default
    embedding_model = llm_config.get("embedding_model", "octen-embedding-8b")
    base_url = os.environ.get("OPENAI_BASE_URL") or llm_config.get("base_url")
    api_key = os.environ.get("INNKUBE_ACCESS_TOKEN", "")
    
    if not api_key:
        logger.warning("INNKUBE_ACCESS_TOKEN not set - embeddings will fail")
        return lambda text: None
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    def embed(text: str) -> list[float] | None:
        """Embed text using InnKube embedding model.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector or None on error
        """
        if not text or not text.strip():
            return None
        
        try:
            response = client.embeddings.create(
                model=embedding_model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None
    
    return embed
