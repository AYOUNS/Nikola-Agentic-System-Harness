---
name: memory
description: Guide to long-term memory with remember and search_memory tools
version: 1.0
---

# Long-Term Memory Guide

The agent has persistent memory that survives restarts. Use it to maintain context across sessions.

## Available Tools

### remember
Store information in long-term memory for retrieval across sessions.

**Parameters:**
- `content` (required): The information to remember
- `kind` (required): Classification - "fact", "preference", or "summary"
- `pinned` (optional): Whether to inject at startup (default: false)

**Example:**
```
remember(
  content="Project uses FastAPI with Python 3.12 and Qdrant for vector storage",
  kind="fact"
)
```

### search_memory
Search long-term memory for relevant information.

**Parameters:**
- `query` (required): Search query (keywords or semantic description)
- `limit` (optional): Maximum results (default: 5, max: 50)

**Example:**
```
search_memory(query="FastAPI setup", limit=5)
```

## Memory Classification (kind)

### fact
Objective, verifiable information about the project or environment.

**When to use:**
- Technical stack details (languages, frameworks, versions)
- Architecture decisions and their rationale
- Database schemas or API endpoints
- Environment configuration (ports, paths, services)
- Security constraints or policies

**Examples:**
- "Container base image is python:3.12-slim-bookworm (required by spec)"
- "Qdrant vector DB runs on localhost:6333 for semantic search"
- "Delete operations (file/folder) are forbidden by design per AGENTS.md"
- "LLM token via INNKUBE_ACCESS_TOKEN env var, expires Sep 30"

### preference
User's likes, dislikes, or preferred approaches.

**When to use:**
- Code style preferences
- Naming conventions
- Tool or library preferences
- Documentation format preferences
- Workflow preferences

**Examples:**
- "User prefers descriptive test names that explain behavior"
- "User wants type hints on all Python function signatures"
- "User likes docstrings in Go-doc format: summary → behavior → params → returns"
- "User prefers async/await over callbacks for I/O operations"

### summary
Recap of completed work, milestones, or session outcomes.

**When to use:**
- End of major feature implementation
- After significant debugging sessions
- Weekly milestone completion
- Architecture changes
- Important discussions or decisions

**Examples:**
- "Week 2: Implemented MCP stdio integration, added long-term memory with SQLite+Qdrant, created remember/search_memory tools"
- "Fixed Qdrant healthcheck by removing wget dependency and using service_started condition"
- "Refactored tool registry to support memory_store and session_id parameters"

## Pinned Memories

Set `pinned=true` for **critical** information that must be available at every startup.

**Cost:** Pinned memories are injected into every turn, consuming context tokens.

**When to pin:**
- Absolute constraints that never change (e.g., security rules)
- Core project invariants (e.g., "No delete operations")
- Critical context needed for all tasks

**How many to pin:** Maximum 2-5. More than this wastes context budget.

**Example:**
```
remember(
  content="CRITICAL: Never implement delete_file or delete_folder tools per AGENTS.md security policy",
  kind="fact",
  pinned=True
)
```

## Search Strategy

### Semantic Search (Qdrant)
When Qdrant is available, searches use embeddings for semantic similarity.

**Best for:**
- Conceptual queries: "database setup"
- Related but differently-worded content
- Finding memories by topic rather than exact keywords

### Keyword Search (FTS5 fallback)
When Qdrant is unavailable, falls back to SQLite full-text search.

**Best for:**
- Exact term matching: "FastAPI"
- Technical identifiers: "localhost:6333"
- Version numbers: "3.12"

### Search Tips
1. **Use descriptive queries** - "FastAPI configuration" not "config"
2. **Search before storing** - Avoid duplicates
3. **Increase limit for broad topics** - `limit=10` for architecture queries
4. **Try variations** - "web framework" vs "FastAPI" vs "API setup"

## Storage Details

### Database Location
`workspace/nikola/.memory/memories.db` (SQLite + FTS5)

### Vector Index
Qdrant at `http://localhost:6333` (when available)

### Persistence
- Survives container restarts
- Shared across all sessions in same workspace
- Not backed up automatically (workspace volume must be preserved)

## Common Patterns

### 1. Record project setup
```
remember(
  content="Tech stack: Python 3.12, FastAPI, SQLite (memories), Qdrant (vectors), pytest (testing), Docker Compose (orchestration)",
  kind="fact"
)
```

### 2. Remember design decisions
```
remember(
  content="Decided to use SQLite as source of truth with Qdrant as index-only. Rationale: SQLite persists reliably, Qdrant is optional feature that gracefully degrades to keyword search",
  kind="fact"
)
```

### 3. Store user preferences discovered during work
```
remember(
  content="User prefers explicit error messages with actionable suggestions over generic 'Error: operation failed' messages",
  kind="preference"
)
```

### 4. Summarize weekly progress
```
remember(
  content="Week 2 complete: MCP stdio integration (Context7, GitHub, Brave servers), long-term memory (SQLite + Qdrant), remember/search_memory tools registered, memory rules documented",
  kind="summary"
)
```

### 5. Check before adding duplicate information
```
# First, search
result = search_memory("Qdrant")

# Only store if new information
if "6333" not in result:
    remember(
      content="Qdrant UI available at http://localhost:6333/dashboard",
      kind="fact"
    )
```

## Troubleshooting

### "Error: content too short"
Memory content must be at least 10 characters. Be descriptive.

### "Error: kind must be one of..."
Use exactly: "fact", "preference", or "summary" (lowercase).

### "No relevant memories found"
- Try different query terms
- Check if information was actually stored
- Verify Qdrant is running for semantic search

### Memory not persisting across sessions
- Check database file exists: `ls workspace/.nikola/.memory/`
- Verify workspace volume is mounted in compose.yaml
- Check logs for SQLite write errors

### Qdrant semantic search not working
- Verify Qdrant is running: `curl http://localhost:6333/healthz`
- Check collection exists: visit `http://localhost:6333/dashboard`
- Falls back to keyword search automatically (no error)

## Best Practices Summary

1. **Search first** - Avoid duplicate information
2. **Be specific** - "FastAPI on port 8000" not "web server"
3. **Use correct kind** - fact/preference/summary have different purposes
4. **Pin sparingly** - Only critical invariants (2-5 max)
5. **Summarize, don't log** - Summaries of work, not every action
6. **Update workflow** - Search memory at session start for context

## Related Commands

- `/tools` - List all available tools including memory tools
- `/skills list` - See this and other available skills
- `/help` - General help

## Implementation Notes

- **Tool registration:** `agent/memory/tools.py`
- **Storage:** `agent/memory/store.py` (SQLite with FTS5)
- **Vectors:** `agent/memory/vectors.py` (Qdrant client)
- **Policy:** `agent/memory/policy.py` (validation rules)
- **Schema:** memories table + memories_fts virtual table
- **Session ID:** Auto-generated UUID per session
