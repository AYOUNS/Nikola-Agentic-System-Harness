---
hide:
  - toc
---

# Agent Harness

<p align="center">
  <img src="assets/logo.svg" alt="nikola agent harness" width="320" style="margin:2rem 0;"/>
</p>

A terminal-based **ReAct agent harness** that pairs an LLM with filesystem tools, running inside a
container sandbox. Built for the AI Engineering Lab 2026 at the University of Passau.

[Get Started →](getting-started/installation.md){ .md-button .md-button--primary }
[View Demo](demo.md){ .md-button }

Assignment spec: [AI Engineering Lab 2026 (PDF)](AI_Engineering_Lab_2026.pdf)

---

## What's inside

| Week | Feature | Deliverables |
|------|---------|--------------|
| **1** | Core Loop | ReAct loop · Terminal CLI · ToolRegistry · 10 filesystem tools · pytest suite |
| **2** | Memory & MCP | MCP integration · Long-term memory (SQLite + Qdrant) · Skills · Context compaction |
| **3** | Observability | Permission management · Parallel sub-agents (3 roles) · Prometheus + Grafana |

---

## Quick start

```bash
# 1 — copy env template and set your token
cp .env.example .env
# edit .env → INNKUBE_ACCESS_TOKEN=<your_token>

# 2 — export workspace path (every shell that runs the agent or tests)
export AGENT_WORKSPACE="$(pwd)/workspace"

# 3 — start the full stack and attach
docker compose up --build -d
docker compose attach agent
```

Detach with **CTRL-P CTRL-Q** — the agent keeps running.  
Full guide → [Installation](getting-started/installation.md)

---

## Services

All ports bind to `127.0.0.1` (localhost only).

| Service | URL | Purpose |
|---------|-----|---------|
| **Agent CLI** | `docker compose attach agent` | Main interface |
| **Metrics** | <http://localhost:9464/metrics> | Prometheus scrape endpoint |
| **MCP calendar** | <http://localhost:3000> | Calendar MCP server |
| **mitmproxy proxy** | <http://localhost:8080> | LLM reverse proxy |
| **mitmproxy UI** | <http://localhost:9091> | Live LLM traffic inspection |
| **Qdrant** | <http://localhost:6333/dashboard> | Vector DB for memory |
| **SQLite browser** | <http://localhost:8081> | Browse memory database |
| **Prometheus** | <http://localhost:9090> | Metric queries + scrape health |
| **Grafana** | <http://localhost:3001> | Agent Metrics dashboard + logs |
| **Loki** | <http://localhost:3100> | Structured log store |
| **Alloy** | <http://localhost:12345> | Log shipper — tails `agent.jsonl` into Loki |

---

## Explore the docs

| Section | What you'll find |
|---------|-----------------|
| [Installation](getting-started/installation.md) | Get the stack running in 5 minutes |
| [Configuration](getting-started/configuration.md) | Every `config.yaml` key explained |
| [System Overview](architecture/overview.md) | Request flow · module map · Mermaid diagram |
| [Execution Model](concepts/execution-model.md) | Session · Turn · Step · Loop Guard · Agent Mode |
| [Workspace Sandbox](concepts/workspace.md) | Path safety · `resolve_safe_path()` · `.nikola/` layout |
| [Rules](concepts/rules.md) | Always-active system prompt instructions |
| [Skills](concepts/skills.md) | On-demand domain instructions |
| [Permissions](concepts/permissions.md) | First-match-wins rules · glob patterns · built-in defaults |
| [Sub-agents](concepts/subagents.md) | 3 built-in roles · parallel delegation · auto-deny |
| [Memory](concepts/memory.md) | SQLite + Qdrant · write/read policy · pinned memory |
| [MCP Integration](concepts/mcp.md) | Connect external servers · tool naming · lifecycle |
| [ADR Index](decisions/index.md) | 3 architectural decision records |
| [Demo](demo.md) | 7 hands-on scenarios covering every Week 3 feature |
| [Testing](testing.md) | Unit · E2E · integration · CI pipeline |
