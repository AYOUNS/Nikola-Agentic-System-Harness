# AGENTS.md — Architecture & Planning Rules

This file provides guidance to agents planning or designing work in this repository.

## Hard architectural constraints

- **Python 3.12 + `python:3.12-slim-bookworm`** — language and base image are fixed by the spec
- **ToolRegistry is the single dispatch layer** — all tool capabilities go through it; no direct calls from the loop
- **Filesystem sandbox is non-negotiable** — must reject path-escape attempts; container provides outer boundary
- **No delete tools** — by design; do not plan for them even as optional/guarded operations
- **ReAct loop must always terminate** — plan guard conditions (iteration cap, token budget, time budget) from day one
- **Config is file-based** (JSON or YAML) — access token via env var only, never in config file

## Weekly scope boundaries

Plan work strictly within the current week's deliverables — the spec explicitly warns against pre-solving next week:

| Week | Core additions |
|---|---|
| 1 | ReAct loop · CLI · Config · ToolRegistry · Filesystem tools · pytest |
| 2 | MCP integration · Long-term memory · Extended tests |
| 3 | Prometheus hooks · Permission management · Sub-agents |

## Week 1 implementation order (agreed)

1. Multi-turn chat with no tools (validate LLM connectivity and loop)
2. Add ToolRegistry and filesystem tools to the loop

## Observability design (Week 3)

- Hooks must be reusable and decoupled — not ad-hoc per-tool logging
- Prometheus metrics endpoint required (not just logs); configure a Prometheus server to scrape it
- Dashboard must show: throughput, active runs, latency, success/failure rates, token usage, tool usage, permission decisions, sub-agent activity
- Structured logs/traces required for per-run inspection (not just aggregates)

## Permission management design (Week 3)

- Three outcomes: `allow`, `deny`, `require-user-confirmation`
- Must apply to both built-in tools and MCP tools consistently

## Sub-agent design (Week 3)

- Each sub-agent is a separate harness instance with its own context, role, limits, and permissions
- Parent provides only relevant info + expected result schema; sub-agent returns condensed summary only
- Sub-agent execution must appear in the parent run's observability data
- Must demonstrate ≥2 specialized configurations and ≥1 task that benefits from delegation

## Testing plan

- pytest; unit tests + E2E tests required from Week 1 onward
- E2E tests pre-populate workspace state before each run
- Canonical Week 1 E2E cases: `numbers-in-words` (create + assert byte-exact content) and `find-and-inspect` (pre-populated tree + line count assertion)
- Test suite must pass end-to-end at every Thursday 11am checkpoint
