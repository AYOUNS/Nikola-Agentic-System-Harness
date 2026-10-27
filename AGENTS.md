# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

**AI Engineering Lab 2026** — build a ReAct-loop agent harness as a Python 3.12 CLI application over 3 weekly sprints. Full spec: [`docs/AI_Engineering_Lab_2026.pdf`](docs/AI_Engineering_Lab_2026.pdf).

## Stack

- **Language**: Python 3.12 (required — no other version)
- **Container**: `python:3.12-slim-bookworm` (Debian-based, Podman or Docker — no other base image)
- **LLM**: InnKube API (OpenAI-compatible) — active model set in `config.yaml`; token via `INNKUBE_ACCESS_TOKEN` env var
- **LLM SDK**: `openai>=1.0.0` pointed at `https://llm-keys.innkube.fim.uni-passau.de/`
- **Config**: `config.yaml` (YAML) — single source of truth for all non-secret settings
- **Testing**: pytest (unit + E2E)
- **Observability** (Week 3): Prometheus client library + scrape endpoint + dashboard

## Available Models

| Model | config.yaml value |
|---|---|
| Qwen AgentWorld 35B A3B | `qwen-agentworld-35b-a3b` (default) |
| Gemma 4 31B-it | `gemma4-31b-it` |
| Octen Embedding 8B | `octen-embedding-8b` |

Switch models by changing `llm.model` in `config.yaml` — no code changes needed.

## Critical Constraints

- **`INNKUBE_ACCESS_TOKEN`** must be an env var — never committed, never hardcoded; token expires Sep 30
- **No delete tools**: agent CANNOT delete files or folders — enforced in ToolRegistry, not negotiable
- **CLI only**: no GUI, no web frontend
- **Container must be self-contained**: single `podman run` / `docker run` enters agent mode immediately
- **Thursday 11am**: runnable version committed on `stable` branch or as a version tag on `main` each week
- **Do not pre-solve next week's deliverables** — spec explicitly warns against it; scope is per-sprint

## Architecture (ReAct Loop)

```
User → Terminal CLI → ReAct Loop ──→ LLM Dispatcher (InnKube)
                           ↑                ↓
                      Observation     Tool selection
                           ↑                ↓
                       ToolRegistry ← Tool call
                           ↓
                    Restricted Workspace (container fs)
```

Loop terminates on: final answer, iteration cap, token/time budget exceeded, or unrecoverable error.
**Context rot**: LLM performance degrades as context fills — harness must manage the budget (compaction, offloading).

## LLM Layer (implemented)

Three-layer dispatcher pattern — `agent/llm/`:

```
config.yaml + INNKUBE_ACCESS_TOKEN
        ↓
agent/llm/client.py       make_client(config)
                          → openai.OpenAI(api_key, base_url)
        ↓
agent/llm/dispatcher.py   make_chat_completions_effect(client, config)
                          → dispatch(request) → ChatCompletion

                          make_chat_completions_stream_effect(client, config)
                          → dispatch(request) → Iterator[ChatCompletionChunk]
        ↓
InnKube API  (HTTPS POST to llm-keys.innkube.fim.uni-passau.de)
```

**Rules for this layer (must not be broken):**
- `agent/llm/client.py` is the **only** file that reads `os.environ` or imports `openai.OpenAI`
- `agent/llm/dispatcher.py` never touches `os.environ` — receives the client object only
- `agent/llm/types.py` never imports `openai` directly — only `openai.types.*`
- Model, timeout, and base URL are resolved **once at factory construction** from `config.yaml`
- `OPENAI_BASE_URL` env var overrides `config.yaml` base URL (12-factor pattern)
- The caller (ReAct loop) only passes `messages` in the request dict — all other params come from config

## Harness Components (from spec §2)

| Component | Notes |
|---|---|
| Loop | State machine: reasoning ↔ acting; must always terminate via guard conditions |
| Context | Short-term memory; includes system prompt, history, tool schemas, observations |
| Tools | Schema-defined, deterministic interface; keep token-efficient and non-overlapping |
| Skills | Loadable instruction bundles, disclosed just-in-time (not always in context) — Week 2 |
| Long-term memory | Persists across restarts; explicit write-policy and read/retrieval strategy required — Week 2 |
| Hooks | Lifecycle callbacks (pre/post tool, post LLM); primary observability insertion point — Week 3 |
| Permission mgmt | `allow` / `deny` / `require-user-confirmation`; applies to built-in and MCP tools — Week 3 |
| Sandboxing | Container boundary; reject path-escape attempts (`../`, absolute host paths) |
| Sub-agents | Separate harness instance with own context, role, limits, permissions — Week 3 |

## Component Roadmap

**Week 1**: ReAct loop · Terminal CLI · JSON/YAML config · ToolRegistry · Filesystem tools · pytest suite · architecture diagram  
**Week 2**: MCP server integration · custom MCP tool (non-trivial, non-fs domain) · Long-term memory · skills · updated diagram  
**Week 3**: Prometheus observability hooks · permission management · sub-agents (≥2 configs) · updated diagram

## ToolRegistry — Filesystem Operations

| Operation | Allowed |
|---|---|
| create folder | ✅ |
| create / write file | ✅ |
| read file | ✅ |
| modify file | ✅ |
| list / navigate folder | ✅ |
| search in files | ✅ |
| delete file or folder | ❌ |

## Week 2 — MCP Custom Tool Rules

The new MCP tool you implement must:
- Expose a capability **not already available** in the chosen MCP server
- Address a **non-trivial use case unrelated to programming or filesystem manipulation**
- Have a bounded, clearly described capability with full schema, validation, error handling, and tests

These do **NOT** satisfy the requirement: generic shell execution, arbitrary code execution, unrestricted HTTP requests.

## Week 2 — Long-term Memory Rules

- Must persist across application restarts (not just within a session)
- **Write policy**: define explicitly when and what to store (not ad-hoc)
- **Read policy**: define explicitly when and how to retrieve (drop small high-value files up front; use identifiers for just-in-time load)

## Week 3 — Observability Requirements

- Metrics collected via **reusable lifecycle hooks** — not ad-hoc per-tool logging
- Expose a Prometheus-compatible scrape endpoint; configure a Prometheus server to scrape it
- Dashboard must show: agent throughput, active runs, run/component latency, success/failure rates, LLM token usage, built-in + MCP tool usage, permission decisions, sub-agent activity
- **Also required**: structured logs or traces for per-run inspection (not just aggregates)

## Week 3 — Sub-agent Rules

- Each sub-agent is a **separate harness instance** with its own role, context, limits, and permissions
- Parent passes only relevant info + expected result schema; sub-agent returns condensed summary only
- Sub-agent execution must be recorded in the parent run's observability data
- Demonstrate ≥2 specialized sub-agent configurations and ≥1 task that benefits from delegation

## Testing Requirements

- Framework: pytest; unit tests + E2E tests required from Week 1 onward
- E2E tests must pre-populate filesystem state where needed
- Two canonical Week 1 E2E cases (from spec §8.2):
  - **numbers-in-words** — tools: `create_folder`, `write_file`, `search_text`; assert `output/` exists, `output/numbers.md` contains byte-exact `'one'` and `'twenty'`, final agent turn acknowledges
  - **find-and-inspect** — tools: `list_files`, `read_file`; pre-populate `pathABC/data/notes.txt` ("This\nfile\nhas\none\nline") + `readme.md` + `empty/`; assert agent lists both files and reports 5 lines
- Test suite must remain runnable throughout all 3 weeks (regression safety)

## Code Quality (from spec §7.5)

- Docstrings and inline comments required — follow Go-doc format:
  one-line summary → behavior/edge cases → parameters → returns → example → see also
- `README.md` must include setup instructions, usage examples, and architecture overview
- Container config files (Dockerfile/Containerfile, compose.yaml) must be fully committed — no auth keys

## Source Layout

```
agent/
├── config.py          load_config()  — reads config.yaml, returns dict
├── cli/
│   └── __init__.py    main()         — Typer CLI entry-point
└── llm/
    ├── __init__.py
    ├── client.py      make_client()  — creates openai.OpenAI (only os.environ reader)
    ├── types.py       ChatCompletionCall, ChatCompletionStreamCall Protocols
    └── dispatcher.py  make_chat_completions_effect()        — non-streaming
                       make_chat_completions_stream_effect() — streaming

tests/
├── unit/
│   ├── config/        tests for agent.config
│   └── llm_client/    tests for agent.llm.client
└── e2e/
```

## Grading Notes

- Minimum requirements → max grade 4.0 (passing); higher grade requires self-directed extensions beyond weekly scope
- Oral exam (10 min, Sep 30) covers **entire codebase** — AI-assisted code is allowed but you must be able to explain all of it
- Presentation slides submitted as PDF by **9am Friday** to lukas.schulte@uni-passau.de — no updates after submission

## Daily Reports

- Path: `daily_reports/{YourName}_{WeekX}_{DayOfWeek}.md` (e.g. `CarlinFernandes_Week1_Monday.md`)
- Required sections: `# My contributions:` and `# Team:` (see [`daily_reports/report_template.md`](daily_reports/report_template.md))
- Contribution prefixes: `impl:`, `test:`, `review:`, `design:`, `planning:`
- **Deadline**: committed to GitLab by end of each day Mon–Thu; missing 3× = course failure

## Deadlines

| Event | Date | Submission |
|---|---|---|
| Group presentations | Fri Sep 4 & Sep 11 | PDF by 9am to lukas.schulte@uni-passau.de |
| Individual presentation | Fri Sep 18 | PDF by 9am to lukas.schulte@uni-passau.de |
| Written report (3000–4000 words) | Sun Sep 27 midnight | PDF by email |
| Oral exam (10 min) | Wed Sep 30 | Time slots assigned in advance |
| Weekly Thursday check | Each Thu 11am | `stable` branch or version tag on `main` |
