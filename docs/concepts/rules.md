# Rules

**Rules** are Markdown files placed in `workspace/.nikola/rules/`. They are appended to the system prompt on **every turn**, making them always-active persistent instructions — unlike Skills, which are loaded on demand.

Use rules to encode project-specific constraints, coding style preferences, or any instruction that should apply to every interaction in this workspace.

---

## How rules work

At session start, the harness loads all enabled `.md` files from `workspace/.nikola/rules/`, sorts them alphabetically, and appends them to the system prompt as a `## User Rules` section. The agent reads them before responding to any message.

```
System prompt (Agent Mode)
        +
## User Rules

### 01-coding-style: Python coding preferences
Always use type hints...

### 02-no-delete: Safety reminder
Never attempt to delete files...
```

**Inspect loaded rules:** `/rules` — shows each rule's name, description, and estimated token cost.

---

## Rule file format

```markdown
---
description: Short description shown in /rules output
enabled: true          # optional — defaults to true; set false to disable without deleting
---

Full Markdown rule content here.

Everything after the frontmatter is injected verbatim into the system prompt.
```

Both fields are optional — a file with no frontmatter at all is treated as a rule with no description and `enabled: true`.

---

## Load order

Rules are loaded in **alphabetical order by filename**. Prefix filenames with numbers to control the order:

```
workspace/.nikola/rules/
├── 01-project-context.md    ← loaded first
├── 02-coding-style.md
├── 03-security.md
└── 99-reminders.md          ← loaded last
```

---

## Creating a rule

```bash
mkdir -p workspace/.nikola/rules

cat > workspace/.nikola/rules/01-python-style.md << 'EOF'
---
description: Python coding standards for this project
enabled: true
---

Always use type hints in function signatures.
Prefer dataclasses over plain classes for data containers.
Write docstrings in this format: one-line summary → behaviour → params → returns.
Use `pytest` for all tests; no `unittest`.
EOF
```

Verify it was picked up:
```
/rules
```

---

## Disabling a rule

Set `enabled: false` in the frontmatter — the file stays in the directory but is skipped at load time:

```markdown
---
description: Temporary rule — currently disabled
enabled: false
---

This will not be injected.
```

---

## Token cost

Every rule costs tokens on every turn. Check the budget breakdown with `/context` — rules appear as a separate line showing total tokens consumed.

Keep rules concise. A rule that is always active should earn its token cost by genuinely improving every response in this workspace. If a rule is only relevant for one task, consider making it a [Skill](skills.md) instead.

---

## Rules vs Skills

| | Rules | Skills |
|--|-------|--------|
| **When active** | Every turn, always | Only after explicitly loaded |
| **Best for** | Project-wide constraints, coding style | Domain-specific procedures, tool guides |
| **Token cost** | Every turn | Once per session after loading |
| **Location** | `workspace/.nikola/rules/` | `workspace/.nikola/skills/` |
| **Inspect** | `/rules` | `/skills list` |
