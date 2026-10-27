# Skills

**Skills** are Markdown files with YAML frontmatter that contain specialized instructions loaded into the agent's context on demand. They implement *progressive disclosure* — only the instructions relevant to the current task are loaded, keeping the context budget small.

---

## How skills work

A skill is a `.md` file. Its body is arbitrary Markdown — instructions, examples, reference tables, anything the agent needs for a specialized domain. The frontmatter declares the skill's identity.

When a user mentions a domain the agent has a skill for, the agent can load it with the `load_skill` tool. The skill body is injected into the conversation as a system message and remains active for the rest of the session.

```
User: "What's on the menu at the Passau mensa today?"
Agent: [loads skill: mensa-passau]
Agent: [calls mensa-passau tool, returns today's menu]
```

**Inspect available skills:** `/skills list`  
**Load a skill:** `/skills load <name>` or let the agent load it automatically

---

## Skill file format

Every skill must have YAML frontmatter with three required fields:

```markdown
---
name: my-skill          # REQUIRED — must match filename (without .md) or parent dir name
description: One-line description shown in /skills list
version: 1.0
---

# My Skill

Full Markdown content here. Can include:
- Step-by-step instructions
- Tool usage examples
- Reference tables
- Code snippets
```

**Name validation:** the `name` field must exactly match:
- the filename without `.md` for flat files (`my-skill.md` → `name: my-skill`)
- the parent directory name for directory-based skills (`my-skill/SKILL.md` → `name: my-skill`)

---

## Discovery — where skills are found

Skills are discovered at session start from two locations, in this precedence order:

| Priority | Location | Notes |
|----------|----------|-------|
| **1 (highest)** | `workspace/.nikola/skills/*.md` | User skills — flat files |
| **1 (highest)** | `workspace/.nikola/skills/*/SKILL.md` | User skills — directory-based |
| **2** | `agent/skills/*.md` | Built-in skills |

User skills **override** built-ins by name. To replace the built-in `filesystem` skill, create `workspace/.nikola/skills/filesystem.md`.

---

## Built-in skills

| Skill | Description |
|-------|-------------|
| `filesystem` | Guide to built-in filesystem tools — patterns, error handling, security model |
| `memory` | Guide to `remember()` and `search_memory()` — kinds, pinning, search strategy |

---

## Creating a user skill

1. Create the file in the workspace:

    ```bash
    mkdir -p workspace/.nikola/skills
    cat > workspace/.nikola/skills/python-style.md << 'EOF'
    ---
    name: python-style
    description: Project Python coding standards
    version: 1.0
    ---

    # Python Style Guide

    - Always use type hints on function signatures
    - Use dataclasses for data containers
    - Docstrings: one-line summary → behaviour → params → returns
    EOF
    ```

2. Verify it was discovered:

    ```
    /skills list
    ```

3. Load it:

    ```
    /skills load python-style
    ```

---

## Skill body is lazy-loaded

The skill body is not read from disk until it is loaded into context. `discover_skills()` reads only the frontmatter — the body is cached on first access. This means listing skills is cheap even with many large skill files.

---

## Token cost

Each loaded skill body adds tokens to the context for the remainder of the session. Check the budget with `/context` — it shows system, rules, skills, history, and tool schemas separately.

Prefer narrow, focused skill files. A skill that covers one domain well is more useful than a large catch-all file that consumes budget on unrelated sessions.
