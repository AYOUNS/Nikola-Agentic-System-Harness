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

### Preferences (kind="preference")
- **Code style preferences**: Type hints, naming conventions, formatting
- **Tool preferences**: Which libraries or approaches the user prefers
- **Workflow preferences**: How the user likes to work
- **Communication style**: Level of detail, technical depth

### Summaries (kind="summary")
- **Major milestones**: Week completions, feature implementations
- **Significant debugging sessions**: What was wrong and how it was fixed
- **Architecture changes**: Major refactorings or structural updates
- **Important discussions**: Key decisions made during conversation

## When NOT to Remember

Do NOT save:
- ❌ Temporary information only relevant to current conversation
- ❌ Information already visible in code/files (check first with grep/read_file)
- ❌ Trivial details that don't affect future work
- ❌ Uncertain information or assumptions (verify first)
- ❌ Duplicate information (search memory first)

## Workflow

1. **Before saving**: Quickly search memory to avoid duplicates
2. **Then save**: If new or additional context
3. **Be concise but complete**: Include context but keep it focused

## Be Proactive, Not Reactive

❌ **Don't wait for**: "please remember this"  
✅ **Just do it**: When you encounter important information
