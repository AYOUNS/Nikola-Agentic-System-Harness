# Testing

The test suite has three layers — unit, E2E, and integration — and runs in CI on every push.

---

## Running tests locally

### Unit tests (no token or services required)

```bash
# Docker
docker compose run --rm --no-deps --entrypoint pytest agent tests/unit -q

# Podman
podman compose run --rm --no-deps --entrypoint pytest agent tests/unit -q
```

`--no-deps` skips starting Prometheus, Grafana, Loki, and the MCP server — the unit suite
has no network dependencies.

**Run a specific Week 3 subset:**

```bash
docker compose run --rm --no-deps --entrypoint pytest agent \
  tests/unit/hooks tests/unit/permissions tests/unit/subagents -q
```

### E2E tests (token + full stack required)

```bash
export INNKUBE_ACCESS_TOKEN=<your_token>

docker compose run --rm --entrypoint pytest agent tests/e2e -q
```

E2E tests pre-populate filesystem state in `workspace/` before each scenario and assert on
the files the agent creates.

### Integration tests

```bash
docker compose run --rm --entrypoint pytest agent tests/integration -q
```

---

## What each suite covers

### `tests/unit/`

Fast, no I/O, no LLM calls. Tests one module in isolation.

| Directory | What it tests |
|---|---|
| `tests/unit/config/` | `agent.config` — YAML loading, defaults, env overrides |
| `tests/unit/llm_client/` | `agent.llm.client` — client factory, env var reads |
| `tests/unit/tools/` | `agent.tools.filesystem` — path safety, tool outputs |
| `tests/unit/permissions/` | `agent.permissions` — rule evaluation, auto-deny, session cache |
| `tests/unit/hooks/` | `agent.hooks` — HookRegistry dispatch, failure isolation |
| `tests/unit/subagents/` | `agent.subagents` — roles, orchestrator, delegation contract |
| `tests/unit/memory/` | `agent.memory` — write guards, read policy, FTS5 fallback |
| `tests/unit/mcp_integration/` | `agent.mcp` — config parsing, tool naming convention |
| `tests/unit/cli/` | `agent.cli` — slash commands, completion, REPL dispatch, UI rendering |
| `tests/unit/context/` | `agent.context` — token budget, compaction, rule loading |
| `tests/unit/engine/` | `agent.engine` — the pure ReAct state machine (`run_turn`, `TurnLimits`) driven by fake adapters |
| `tests/unit/llm/` | `agent.llm` — dispatcher behaviour and model definitions |
| `tests/unit/loop/` | `agent.loop` — ReAct iteration and loop guards |
| `tests/unit/skills/` | `agent.skills` — skill discovery and loading |

### `tests/e2e/`

Full ReAct loop with real LLM calls. Requires `INNKUBE_ACCESS_TOKEN` and the compose stack.

**Canonical cases** (from spec §8.2):

**numbers-in-words**  
Tools used: `create_folder`, `write_file`, `search_text`  
Pre-state: empty workspace  
Prompt: "Write the numbers 1 to 20 in words to `output/numbers.md`"  
Assertions:
- `workspace/output/` directory exists
- `workspace/output/numbers.md` contains the byte-exact strings `'one'` and `'twenty'`
- Final agent turn acknowledges completion

**find-and-inspect**  
Tools used: `list_files`, `read_file`  
Pre-state: `workspace/pathABC/data/notes.txt` ("This\nfile\nhas\none\nline"), `workspace/readme.md`, `workspace/empty/`  
Prompt: "List the files in pathABC and tell me how many lines notes.txt has"  
Assertions:
- Agent lists both files
- Agent reports exactly 5 lines

### `tests/integration/`

Tests interactions between two or more modules (e.g. permissions gate + ToolRegistry, MCP
client + manager lifecycle).

---

## CI pipeline

```
lint → test:unit → test:e2e → test:integration → pages (deploy)
```

| Stage | Job | When it runs |
|---|---|---|
| `lint` | `ruff check agent/ tests/` | Every push |
| `test` | `test:unit` | Every push |
| `test` | `test:e2e` | Every push (requires `INNKUBE_ACCESS_TOKEN` secret) |
| `test` | `test:integration` | Every push |
| `deploy` | `pages` — `mkdocs build` | Pushes to the default branch only |

The `pages` job publishes to GitLab Pages. Check **CI/CD → Pipelines → deploy stage** to
confirm it passes after a push to the default branch.

`INNKUBE_ACCESS_TOKEN` must be set as a protected CI/CD secret variable in
**GitLab → Settings → CI/CD → Variables**. It is never committed to the repo.

---

## Writing new tests

**Unit test conventions:**

- One file per module tested: `tests/unit/<module>/test_<module>.py`
- Use `pytest` fixtures — no class-based tests
- Cover empty input, permission-denied, and path-escape edge cases for any filesystem operation
- Mock the LLM dispatcher with a `FakeDispatcher` that returns canned responses

**E2E test conventions:**

- Pre-populate `workspace/` in a fixture using `tmp_path` or a dedicated test workspace dir
- Assert on file contents with `Path.read_text()` — use byte-exact strings for spec cases
- Clean up after each test (or use isolated subdirectories)
