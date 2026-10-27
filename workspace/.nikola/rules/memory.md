---
description: Guidelines for using long-term memory
enabled: true
---

# Memory Usage Guidelines

The agent has access to long-term memory that persists across sessions. Use memory strategically to maintain context and improve future interactions.

## When to Use Memory

**DO remember:**
- Important facts about the user's project, tech stack, or architecture
- User preferences for code style, tools, or approaches
- Key decisions made during conversations and their rationale
- Recurring patterns or common tasks the user performs
- Session summaries when completing significant work

**DON'T remember:**
- Temporary information that's only relevant to the current conversation
- Information already visible in files or documentation
- Trivial details that don't affect future work
- Personal information unless explicitly requested by the user

## Memory Types (kind parameter)

### fact
Objective information about the project, environment, or technical details.

**Examples:**
- "Project uses Python 3.12 with FastAPI framework"
- "Database runs on Qdrant at localhost:6333"
- "CI/CD pipeline uses GitHub Actions"
- "Container base image is python:3.12-slim-bookworm"

### preference
User likes, dislikes, or preferred approaches.

**Examples:**
- "User prefers type hints on all functions"
- "User wants descriptive variable names over abbreviations"
- "User prefers async/await over callbacks"
- "User likes docstrings in Google format"

### summary
Recap of completed work or major milestones.

**Examples:**
- "Implemented Week 2 MCP integration with Context7 and GitHub servers"
- "Fixed Qdrant healthcheck issue by removing wget dependency"
- "Added memory tools: remember and search_memory"

## Pinned Memories

Set `pinned=true` for critical information that should be loaded at every startup:
- Core project constraints (e.g., "Never use delete operations")
- Critical security rules
- Essential context that affects all work

**Warning:** Pinned memories cost context tokens on every turn. Use sparingly (2-5 max).

## Search Strategy

Before storing new information, search memory first to avoid duplicates:
```
search_memory("Python version")  # Check if already recorded
```

## Best Practices

1. **Be specific and concise** - Memory content should be clear and actionable
2. **Use descriptive search queries** - "FastAPI setup" not just "setup"
3. **Verify before storing** - Don't remember assumptions or uncertain information
4. **Update, don't duplicate** - Search first, then store if new
5. **Group related facts** - One memory can contain multiple related details

## Examples

### Good Memory Usage

**Storing project context:**
```
remember(
  content="Project structure: agent/ (core harness), tests/ (pytest suite), workspace/ (sandboxed execution root). Database stored at workspace/.nikola/.memory/memories.db",
  kind="fact"
)
```

**Storing a preference:**
```
remember(
  content="User prefers tools to return detailed error messages with suggestions for fixes",
  kind="preference"
)
```

**Storing a session summary:**
```
remember(
  content="Week 2 completion: Implemented MCP integration with stdio transport, added long-term memory with SQLite + Qdrant, created remember and search_memory tools, fixed Docker Compose Qdrant healthcheck issue",
  kind="summary"
)
```

### Poor Memory Usage

❌ Too vague: `"Fixed a bug"`
✅ Better: `"Fixed Qdrant healthcheck failure caused by missing wget binary in v1.7.4 image"`

❌ Redundant: `"User is working on a Python project"`
✅ Better: Check files first - this is already obvious from the codebase

❌ Temporary: `"User asked about the /tools command"`
✅ Better: Don't store - this is just a one-time question

## Technical Details

- **Storage:** SQLite database at `workspace/.nikola/.memory/memories.db`
- **Vector search:** Qdrant at `http://localhost:6333` (semantic search)
- **Fallback:** FTS5 keyword search when Qdrant unavailable
- **Persistence:** Survives container restarts
- **Session ID:** Automatically generated UUID per session

## Commands

- **Store memory:** Use `remember` tool in conversation
- **Search memory:** Use `search_memory` tool in conversation
- **View memory database:** `sqlite3 workspace/.nikola/.memory/memories.db`

## Limitations

- No delete operation (memories are permanent once stored)
- `search_memory` returns 3 results by default, 20 at most — a higher limit is rejected
- Semantic search requires Qdrant; without it search falls back to FTS5 keywords, and
  anything stored during the outage is indexed on the next startup that reaches Qdrant
- Memory is per-workspace, not global across all projects
