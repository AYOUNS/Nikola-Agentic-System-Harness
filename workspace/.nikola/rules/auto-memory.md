---
description: Automatically save important information to long-term memory
enabled: true
---

# Automatic Memory Storage Rule

You MUST proactively use the `remember` tool to save important information to long-term memory without waiting for explicit user requests.

## When to Automatically Remember

Save information immediately when you encounter:

### Facts (kind="fact")
- **Project details**: Tech stack, versions, frameworks, libraries
- **Architecture decisions**: Why something was designed a certain way
- **Configuration**: Ports, endpoints, database connections, file paths
- **Constraints**: Security rules, limitations, requirements from specs
- **Environment**: Container setup, dependencies, build tools
- **Error solutions**: How specific bugs were fixed

**Examples:**
```
remember(
  content="Project uses Python 3.12 with Qdrant vector DB on localhost:6333 for semantic search",
  kind="fact"
)

remember(
  content="Delete operations (file/folder) are forbidden by design per AGENTS.md security policy",
  kind="fact"
)

remember(
  content="Fixed Qdrant healthcheck failure by removing wget dependency and using service_started condition in compose.yaml",
  kind="fact"
)
```

### Preferences (kind="preference")
- **Code style preferences**: Type hints, naming conventions, formatting
- **Tool preferences**: Which libraries or approaches the user prefers
- **Workflow preferences**: How the user likes to work
- **Communication style**: Level of detail, technical depth

**Examples:**
```
remember(
  content="User prefers detailed explanations with code examples and clear reasoning",
  kind="preference"
)

remember(
  content="User wants docstrings in Go-doc format: summary → behavior → params → returns",
  kind="preference"
)
```

### Summaries (kind="summary")
- **Major milestones**: Week completions, feature implementations
- **Significant debugging sessions**: What was wrong and how it was fixed
- **Architecture changes**: Major refactorings or structural updates
- **Important discussions**: Key decisions made during conversation

**Examples:**
```
remember(
  content="Week 2 complete: Implemented MCP stdio integration, long-term memory with SQLite+Qdrant, created remember/search_memory tools, renamed nikola to .nikola directory",
  kind="summary"
)
```

## When NOT to Remember

Do NOT save:
- ❌ Temporary information only relevant to current conversation
- ❌ Information already visible in code/files (check first with grep/read_file)
- ❌ Trivial details that don't affect future work
- ❌ Uncertain information or assumptions (verify first)
- ❌ Duplicate information (search memory first)

## Workflow

1. **Before saving**: Quickly search memory to avoid duplicates
   ```
   search_memory(query="Python version")  # Check if already stored
   ```

2. **Then save**: If new or additional context
   ```
   remember(content="...", kind="fact")
   ```

3. **Be concise but complete**: Include context but keep it focused

## Automatic Triggers

Save memory automatically when:

✅ User mentions project setup or tech stack  
✅ You help solve a significant bug or error  
✅ User expresses a preference or coding style  
✅ You make an architecture or design decision  
✅ User provides important context about their project  
✅ You complete a major task or milestone  
✅ User corrects you (remember the correction)  
✅ You discover important constraints or requirements  

## Examples in Context

**User says:** "We're using FastAPI for the REST API and Redis for caching"

**You do:**
```
# Search first
search_memory(query="FastAPI Redis")

# If not found, remember
remember(
  content="Project uses FastAPI for REST API and Redis for caching layer",
  kind="fact"
)
```

---

**User says:** "I prefer functions under 20 lines"

**You do:**
```
remember(
  content="User prefers functions under 20 lines for better readability",
  kind="preference"
)
```

---

**After fixing a complex bug:**

**You do:**
```
remember(
  content="Fixed Qdrant healthcheck by removing wget dependency (not in v1.7.4 image). Solution: removed healthcheck entirely and changed dependency to service_started in compose.yaml",
  kind="fact"
)
```

## Pinned Memories

Use `pinned=True` ONLY for critical constraints that affect ALL work:

```
remember(
  content="CRITICAL: Never implement delete_file or delete_folder tools per AGENTS.md security policy",
  kind="fact",
  pinned=True
)
```

**Limit**: Maximum 3-5 pinned memories (they cost context on every turn)

## Search Before Store

ALWAYS search before saving to avoid duplicates:

```python
# Pattern:
result = search_memory(query="relevant keywords")
if "specific detail" not in result:
    remember(content="new information", kind="fact")
```

## Be Proactive, Not Reactive

❌ **Don't wait for**: "please remember this"  
✅ **Just do it**: When you encounter important information

This helps maintain context across sessions and makes future interactions more efficient and personalized.
