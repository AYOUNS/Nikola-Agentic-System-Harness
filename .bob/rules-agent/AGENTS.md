# AGENTS.md — Coding Rules

This file provides guidance to agents when writing code in this repository.

## Language & Runtime

- **Python 3.12** only — no other Python version
- Container base image: `python:3.12-slim-bookworm` — do not change this

## LLM Client

- Token: read from `INNKUBE_ACCESS_TOKEN` env var — hardcoded values are a blocker
- Add timeout handling to every LLM API call (explicitly required in spec)
- Recommended models: `qwen-agentworld-35b-a3b` or `gemma4-31b-it`

## ToolRegistry

- All agent capabilities are registered and dispatched through `ToolRegistry` — never call tools directly from the loop
- Unit tests must cover empty dirs and permission errors (spec explicitly names these)
- **Delete (file/folder) must not exist** — do not implement it under any name

## Filesystem Tool Security

- All file operations must be restricted to the container workspace root
- Reject paths that escape the sandbox (e.g. `../`, absolute paths to host)
- Allowed: create folder, create/write file, read file, modify file, list/navigate folder, search in files
- Forbidden: delete file, delete folder

## ReAct Loop

- Implement multi-turn chat (no tools) first — add filesystem tools in a separate step
- Loop must always terminate: enforce iteration cap, token budget, and time budget as guard conditions

## Week 2 Additions

- MCP integration: connect, discover tools at runtime, invoke, append result to context, handle errors
- Long-term memory: persist across restarts; define explicit when-to-store / when-to-retrieve policy

## Week 3 Additions

- Use Prometheus client library for metrics — expose a Prometheus-compatible scrape endpoint
- Observability via reusable lifecycle hooks (not ad-hoc per-tool logging)
- Permission policy outcomes: `allow`, `deny`, `require-user-confirmation`
- Sub-agents: separate instance with own role, context, limits, and permissions; parent receives condensed result

## Testing

- Framework: pytest
- Include both unit tests and E2E tests
- E2E tests must pre-populate filesystem state where needed (see spec Section 8.2 for canonical examples)
- Test suite must remain runnable after every sprint

## Daily Reports

- New reports: `daily_reports/{YourName}_{WeekX}_{DayOfWeek}.md`
- Contribution prefixes: `impl:`, `test:`, `review:`, `design:`, `planning:`
- Commit to GitLab by end of day Mon–Thu
