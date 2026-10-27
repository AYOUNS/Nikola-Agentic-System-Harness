# Documentation Plan — Group 3 Agent Harness

> **Internal working document — not published.** `mkdocs.yml` lists this file under
> `exclude_docs`, so it stays in the repo and out of the site. All 19 deliverables below
> are complete; the checklist is kept as a record of scope.

Goal: publish a complete, navigable product-quality documentation site via **GitLab Pages**
using **mkdocs-carbon** (IBM Carbon Design System). The CI job (`pages:` stage in `.gitlab-ci.yml`) already runs
`mkdocs build --site-dir public` on every push to the default branch.

---

## Progress key

- [ ] Not started
- [-] In progress
- [x] Done

---

## Deliverables checklist

### 🔧 Infrastructure (do first — nothing else works without this)

- [x] **`mkdocs.yml`** — root-level site config: site name, theme (mkdocs-carbon), nav tree,
      plugins (search), repo URL, palette (dark/light toggle).  
      *Blocked by: nothing — do this first. Needed to run `mkdocs serve` locally.*

---

### 🏠 Home

- [x] **`docs/index.md`** — landing page.  
      Content: one-paragraph project summary, services table (all ports), quick-start
      snippet (3 commands to get running), links to Getting Started and Demo.  
      *Source: `README.md` intro + services table.*

---

### 🚀 Getting Started

- [x] **`docs/getting-started/installation.md`** — prerequisites and first run.  
      Content: Python 3.12 + Podman/Docker requirement, `cp .env.example .env`,
      `INNKUBE_ACCESS_TOKEN` + `AGENT_WORKSPACE` export, `compose up --build -d`,
      `restart && attach` pattern, window-sizing tip, CTRL-P CTRL-Q vs CTRL-C table,
      `compose down`.  
      *Source: `README.md` Prerequisites + Quick Start sections.*

- [x] **`docs/getting-started/services.md`** ⭐ **ADDED** — services table, Prometheus metrics
      reference, Grafana dashboard panels, Loki LogQL examples, troubleshooting table.
      *Migrated from `site-docs/services.md` — consolidates content that was split across
      `index.md` (ports table) and `architecture/observability.md` (Grafana/Loki).*

- [x] **`docs/getting-started/configuration.md`** — complete config reference.
      Content: fully annotated `config.yaml` (every key: type, default, description —
      `llm.*`, `loop.*`, `context.*`, `workspace`, `logging.*`, `metrics.*`, `memory.*`),
      all env vars (`INNKUBE_ACCESS_TOKEN`, `AGENT_WORKSPACE`, `OPENAI_BASE_URL`),
      `permissions.yaml` format + example, `.nikola/mcp.json` format + example.  
      *Source: `config.yaml`, `CONTEXT.md`, `README.md`.*

---

### 🏗️ Architecture

- [x] **`docs/architecture/overview.md`** — how a request flows through the system.  
      Content: prose walkthrough (User → CLI → REPL → Loop → LLM Dispatcher → ToolRegistry
      → Tool → Observation → Answer), Mermaid flowchart of the full request path,
      module layout tree (from `CONTEXT.md`), brief role of each module.  
      *Source: `CONTEXT.md` Module Layout, `docs/agent_architecture.md`.*

- [x] **`docs/architecture/observability.md`** — the full observability stack.  
      Content: hook protocol diagram, event types table (8 events: RunStart/End,
      SubAgentStart/End/Progress, ToolCall, LLMCall, MemoryOperation, MCPServer),
      Prometheus metrics table (all `agent_*` series with type + labels),
      Loki structured log fields (indexed labels vs metadata), Grafana dashboard
      panels list, mitmproxy role, services table with ports.  
      *Source: `agent/observability/README.md`, `agent/hooks/types.py`,
      `README.md` Observability section.*

---

### 📖 Concepts (one page per subsystem — what it is and how it behaves, not implementation)

- [x] **`docs/concepts/execution-model.md`** — Session, Turn, Step, Loop Guard,
      REPL mode, One-shot mode, Agent Mode, `process_turn_fn`, context budget / compaction.  
      *Source: `CONTEXT.md` Execution Hierarchy + Entry Points + Core Abstractions.*

- [x] **`docs/concepts/tools.md`** — Tool, ToolRegistry, Observation,
      the 10 built-in filesystem tools (table with name + description),
      no-delete policy (structurally enforced), MCP tool naming convention
      (`mcp_<server>__<tool>`), `execute_command` tool (sandboxed, screened).  
      *Source: `CONTEXT.md` Core Abstractions, `agent/tools/filesystem.py`,
      `agent/tools/execute.py`.*

- [x] **`docs/concepts/workspace.md`** ⭐ **NEW** — the harness sandbox.  
      Content:
      - What the Workspace is: the container-local directory the agent may read/write
      - Root resolution: `AGENT_WORKSPACE` env var → `config.yaml workspace` → `/workspace`
        (12-factor precedence, resolved in `get_workspace_root()`)
      - Path safety: `resolve_safe_path()` rejects `../` traversal and absolute paths
        that land outside the root — raises `PermissionError: Access denied`
      - Two rejection cases with examples:
        1. Relative escape — `../../etc/passwd` → resolves outside root → denied
        2. Absolute path — `/etc/passwd` → not under root → denied
      - The `.nikola/` subtree: special dirs the harness writes inside the workspace
        (`.nikola/logs/agent.jsonl`, `.nikola/rules/*.md`, `.nikola/skills/`,
        `.nikola/mcp.json`) — plus the memory DB at `config.memory.db_path`,
        which ships *outside* `.nikola/` as `/workspace/.agent/memory.db`
      - Sub-agents inherit the parent's workspace root — they operate on the same fs
      - Delete is always absent — no filesystem tool can remove files or folders  
      *Source: `agent/tools/filesystem.py` (`get_workspace_root`, `resolve_safe_path`),
      `CONTEXT.md` Workspace entry, `agent/cli/__init__.py`.*

- [x] **`docs/concepts/mcp.md`** — MCP Server, MCP Client (stdio vs HTTP variants),
      MCP Manager lifecycle (connect at Session start → discover → register → disconnect),
      `.nikola/mcp.json` format + env var substitution, tool naming convention.  
      *Source: `CONTEXT.md` MCP Integration, `agent/mcp/`.*

- [x] **`docs/concepts/memory.md`** — Memory, Pinned Memory, Kind (fact/preference/summary),
      Source (user/compaction), Write Policy (explicit + automatic + guards),
      Read Policy (startup pinned inject + per-turn top-3 + on-demand search),
      SQLite + Qdrant split, what is never stored.  
      *Source: `CONTEXT.md` Long-term Memory, `agent/memory/`.*

- [x] **`docs/concepts/permissions.md`** — PermissionModule, PermissionAction (allow/ask/deny),
      rule evaluation (first-match-wins, top-to-bottom), glob patterns (`*delete*`),
      four sources with precedence (runtime cache → workspace → global → built-in defaults),
      built-in defaults table, Confirmation Channel (four-option menu; `ask` bypasses the
      session cache and re-prompts every call), one-shot mode (same channel as the REPL),
      sub-agent auto-deny, `--permissions` CLI flag.  
      *Source: `CONTEXT.md` Permission Management, `agent/permissions/module.py`.*

- [x] **`docs/concepts/subagents.md`** — Sub-agent, Delegation tool (`delegate` + `delegate_parallel`),
      three built-in Roles (file-scout · scheduler · general) with allowlists + limits table,
      auto-deny for `ask` tools with exact error message, Delegation Contract
      (what parent passes, what sub-agent returns), workspace inheritance,
      parallel delegation (live progress cards, wall-clock vs sum).  
      *Source: `CONTEXT.md` Sub-agents, `agent/subagents/roles.py`,
      `agent/subagents/orchestrator.py`.*

---

### 📋 Decisions (ADRs — hard to reverse, surprising without context, result of real trade-offs)

- [x] **`docs/decisions/index.md`** — ADR index: table with number, title, status, date,
      one-line summary.

- [x] **`docs/decisions/0001-permission-rules.md`** — First-match-wins rule resolution.  
      Alternatives: first-match · most-specific · deny-wins · error-on-ambiguity.  
      Decision: first-match-wins (top-to-bottom).  
      *Rewrite — original deleted.*

- [x] **`docs/decisions/0002-subagent-autodeny.md`** — Sub-agent confirmations auto-deny.  
      Alternatives: auto-deny · bubble-to-parent · share-session-cache · pre-flight confirmation.  
      Decision: auto-deny (`is_subagent=True` → `ask` becomes `deny`).  
      *Rewrite — original deleted.*

- [x] **`docs/decisions/0003-memory-split.md`** ⭐ **NEW ADR** — SQLite as source of truth +
      Qdrant as optional index.  
      Context: memory must survive restarts; semantic search is valuable but not critical path.  
      Alternatives: SQLite-only with FTS5 · Qdrant-only · split.  
      Decision: split — SQLite is mandatory, Qdrant degrades gracefully to FTS5.  
      Rationale: the mandatory "store in session A, retrieve after restart in session B"
      guarantee has zero runtime dependency on Qdrant.  
      *New — this decision exists in the code but was never documented.*

---

### 🎬 Demo

- [x] **`docs/demo.md`** — 7-scenario walkthrough.  
      Scenarios: (1) tool that asks, (2) denied tool, (3) path-based deny,
      (4) MCP tools + `/permissions status`, (5) two parallel sub-agents,
      (6) three roles at once, (7) Grafana + Loki inspection.  
      Each scenario: prompt to type → what to observe → verification command.  
      *Source: `README.md` Demo Walkthrough — pull across, lightly expand.*

---

### 🧪 Testing

- [x] **`docs/testing.md`** — test guide.  
      Content: how to run unit tests (no token/services needed), Week 3 subset filter,
      E2E tests, integration tests; what each suite covers; CI pipeline stages
      (lint → unit → e2e → integration → pages); two canonical spec E2E cases
      (numbers-in-words, find-and-inspect) with pre-population requirements.  
      *Source: `README.md` Tests section, `AGENTS.md` Testing Requirements,
      `.gitlab-ci.yml`.*

---

## Build order (recommended)

| # | File | Why this order |
|---|------|----------------|
| 1 | `mkdocs.yml` | Unblocks `mkdocs serve` locally |
| 2 | `docs/index.md` | Smoke-tests the site renders |
| 3 | `getting-started/installation.md` | First thing any reader needs |
| 4 | `getting-started/configuration.md` | Referenced by everything else |
| 5 | `architecture/overview.md` | Sets mental model for all concepts |
| 6 | `concepts/workspace.md` | Core harness behaviour — explains sandbox to all readers |
| 7 | `concepts/execution-model.md` | |
| 8 | `concepts/tools.md` | |
| 9 | `concepts/permissions.md` | |
| 10 | `concepts/subagents.md` | |
| 11 | `concepts/memory.md` | |
| 12 | `concepts/mcp.md` | |
| 13 | `architecture/observability.md` | Best written once all concepts exist |
| 14 | `decisions/0001-permission-rules.md` | |
| 15 | `decisions/0002-subagent-autodeny.md` | |
| 16 | `decisions/0003-memory-split.md` | New — needs writing |
| 17 | `decisions/index.md` | Fill in after all ADRs exist |
| 18 | `demo.md` | Mostly copy from README |
| 19 | `testing.md` | Mostly copy from README + CI |

**Total: 19 files** (1 infrastructure + 1 home + 2 getting-started + 2 architecture +
7 concepts + 4 decisions + 1 demo + 1 testing)

---

## Local preview

```bash
# First time only (already in requirements.txt)
pip install mkdocs mkdocs-carbon pymdown-extensions

# Live-reload server — http://127.0.0.1:8000
mkdocs serve

# Strict build (warnings = errors) — run before pushing
mkdocs build --strict
```

The CI `pages:` job runs on every push to the default branch and publishes to GitLab Pages.
Check **CI/CD → Pipelines → deploy stage** to confirm it passes.
