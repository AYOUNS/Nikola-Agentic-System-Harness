"""Memory tools for the agent: remember and search_memory.

These tools are registered in the ToolRegistry and exposed to the LLM.
All functions return strings (result or "Error: ...") and never raise.
"""

from __future__ import annotations

import logging

from openai.types.chat import ChatCompletionToolParam

from agent.memory.policy import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    should_store_content,
)
from agent.memory.store import Memory, MemoryStore

logger = logging.getLogger(__name__)


def make_remember_tool(store: MemoryStore, session_id: str, vector_store=None) -> dict:
    """Create the remember tool with its schema and implementation.
    
    Args:
        store: MemoryStore instance
        session_id: Current session ID
        vector_store: Optional VectorStore for embedding to Qdrant
        
    Returns:
        Dict with keys: name, fn, schema
    """
    
    def remember(content: str, kind: str, pinned: bool = False) -> str:
        """Store a memory for long-term retrieval.
        
        Args:
            content: The information to remember
            kind: Classification - must be 'fact', 'preference', or 'summary'
            pinned: Whether to inject this memory at startup (default False)
            
        Returns:
            Success message or error string
        """
        # Validate kind
        valid_kinds = {"fact", "preference", "summary"}
        if kind not in valid_kinds:
            return f"Error: kind must be one of {valid_kinds}, got '{kind}'"
        
        # Validate content
        should_store, reason = should_store_content(content)
        if not should_store:
            return f"Error: {reason}"
        
        try:
            memory = Memory(
                content=content,
                kind=kind,  # type: ignore
                source="user",
                session_id=session_id,
                pinned=pinned,
            )
            stored = store.write(memory)
            
            # Try to embed to Qdrant if vector store is available
            if vector_store and vector_store.is_available() and stored.id:
                embedded = vector_store.upsert(stored.id, stored.content)
                if embedded:
                    # Mark as embedded in SQLite
                    store.update(stored.id, embedded=True)
                else:
                    logger.debug(f"Failed to embed memory {stored.id} - will rely on keyword search")
            
            pinned_status = " (pinned)" if pinned else ""
            return f"Remembered as {kind}{pinned_status}: {content[:50]}..."
            
        except Exception as e:
            logger.error(f"Failed to write memory: {e}")
            return f"Error: Failed to store memory: {e}"
    
    schema: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store information in long-term memory for retrieval across sessions. Use this to remember facts about the user's project, preferences, or environment, decisions made during the conversation, or important context that should persist after restart. The memory will survive application restarts and can be retrieved with search_memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember"
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "preference", "summary"],
                        "description": "Classification: fact (objective information), preference (user likes/dislikes), summary (recap of work done)"
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "Whether to inject this memory at startup (pinned memories cost context on every turn)"
                    }
                },
                "required": ["content", "kind"],
                "additionalProperties": False
            }
        }
    }
    
    return {
        "name": "remember",
        "fn": remember,
        "schema": schema,
    }


def make_search_memory_tool(store: MemoryStore, vector_store=None) -> dict:
    """Create the search_memory tool with its schema and implementation.
    
    Args:
        store: MemoryStore instance
        vector_store: Optional VectorStore for semantic search (falls back to keyword if None)
        
    Returns:
        Dict with keys: name, fn, schema
    """
    
    def search_memory(query: str, limit: int = SEARCH_DEFAULT_LIMIT) -> str:
        """Search long-term memory for relevant information.
        
        Args:
            query: Search query (keywords or semantic description)
            limit: Maximum number of results to return
            
        Returns:
            Formatted results or error string
        """
        if not query or not query.strip():
            return "Error: query cannot be empty"
        
        if limit < 1 or limit > SEARCH_MAX_LIMIT:
            return f"Error: limit must be between 1 and {SEARCH_MAX_LIMIT}"
        
        try:
            memories = []
            
            # Try semantic search first if vector store is available
            if vector_store and vector_store.is_available():
                memory_ids = vector_store.search(query, limit=limit)
                if memory_ids:
                    for memory_id in memory_ids:
                        mem = store.get_by_id(memory_id)
                        if mem is not None:
                            memories.append(mem)
            
            # Fall back to keyword search if semantic search failed or unavailable. A
            # semantic hit whose row is gone resolves to None above, so a short result set
            # is topped up rather than accepted: falling back only on an empty one would
            # return two memories out of a requested three and call that a search.
            if len(memories) < limit:
                seen = {memory.id for memory in memories}
                for memory in store.search_keyword(query, limit=limit):
                    if memory.id not in seen:
                        memories.append(memory)
                        seen.add(memory.id)
                    if len(memories) >= limit:
                        break
            
            if not memories:
                return "No relevant memories found."
            
            lines = [f"Found {len(memories)} relevant memor{'y' if len(memories) == 1 else 'ies'}:\n"]
            for i, memory in enumerate(memories, 1):
                kind_label = memory.kind.capitalize()
                lines.append(f"\n{i}. [{kind_label}] {memory.content}")
            
            return "".join(lines)
            
        except Exception as e:
            logger.error(f"Failed to search memory: {e}")
            return f"Error: Search failed: {e}"
    
    schema: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search long-term memory for relevant information. Use this to recall facts or decisions from previous sessions, look up user preferences or project details, or retrieve context that isn't in the current conversation. Memories persist across application restarts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - keywords or description of what you're looking for"
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Maximum number of results (1-{SEARCH_MAX_LIMIT})",
                        "minimum": 1,
                        "maximum": SEARCH_MAX_LIMIT
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    }
    
    return {
        "name": "search_memory",
        "fn": search_memory,
        "schema": schema,
    }
