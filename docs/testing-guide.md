# Testing Guide — All Features (Docker)

Complete step-by-step guide to verify every feature of the AI Engineering Lab Week 3 harness
inside the Docker environment. Run the scenarios **in order** — each section tells you exactly
what to type and what to look for.

---

## Prerequisites

### 1. Token and workspace

Every shell you open needs these two variables exported before anything else:

```bash
export INNKUBE_ACCESS_TOKEN=<your_token_here>
export AGENT_WORKSPACE=./workspace
```

> The token is consumed by `docker compose` and injected into the `agent` container at start
> time. If you forget it the `agent` service starts but the first LLM call fails with an
> authentication error.

### 2. `.env` file (optional but convenient)

Avoids re-exporting in every new terminal:

```bash
cp .env.example .env
# Edit .env and set INNKUBE_ACCESS_TOKEN=<your_token>
```

`compose.yaml` reads `.env` automatically when the file exists (`required: false`).

---

## Stack Startup

### Step 1 — Build and start all services

Open **Terminal 1** and run:

```bash
docker compose up --build -d
```

All ten services come up:

| Service | URL / purpose |
|---|---|
| `agent` | CLI at stdin; Prometheus scrape at `http://localhost:9464/metrics` |
| `mcp_calendar` | MCP calendar server at `http://localhost:3000` |
| `mitmproxy` | Reverse proxy for LLM traffic on port `8080`; web UI on port `9091` |
| `qdrant` | Vector DB REST at `http://localhost:6333/dashboard` |
| `sqlite-web` | Memory DB browser at `http://localhost:8081` |
| `prometheus` | Metrics at `http://localhost:9090` |
| `grafana` | Dashboard at `http://localhost:3001` (no login required) |
| `loki` | Log store at `http://localhost:3100` |
| `alloy` | Tails the agent log file into Loki |
| `workspace_init` | One-shot init — sets workspace permissions then exits |

Verify all services are running:

```bash
docker compose ps
```

All long-lived services should show `Up` or `running (healthy)`.

### Step 2 — Attach to the agent

Open **Terminal 2**. Widen this terminal window **before** attaching (the container takes its
size from the window at attach time):

=== "Docker"

    ```bash
    docker compose restart agent && docker compose attach agent
    ```

=== "Podman"

    ```bash
    podman compose restart agent && podman compose attach agent
    ```

You should see the startup banner with **Agent Mode**, the active model, and the workspace path.

> **Detach without stopping the agent:** `CTRL-P CTRL-Q`
> **End the session:** `CTRL-C`
> **Read the startup screen after the fact:** `docker compose logs agent`

---

## Feature 1 — Filesystem Tools

These are the core built-in tools. Test them end to end before moving to permissions.

### 1.1 Create folder and write file

In the agent terminal type:

```
Create a folder called test_fs and write a file test_fs/hello.txt with the content "Hello from the agent"
```

**Expected:** The agent calls `create_folder` then `write_file`. Because both are `ask` in
`permissions.yaml` a confirmation menu appears for each — approve both.

Verify on the **host**:

```bash
cat workspace/test_fs/hello.txt
```

Expected output: `Hello from the agent`

### 1.2 Read file

```
Read the file test_fs/hello.txt and tell me what it says
```

**Expected:** `read_file` runs without a prompt (it is `allow` in the policy) and the agent
prints the content.

### 1.3 List files

```
List all files in the test_fs folder
```

**Expected:** `list_files` runs without a prompt and shows `hello.txt`.

### 1.4 Search in files

```
Search for the word "Hello" in all files under test_fs
```

**Expected:** `search_text` (or `grep`) runs without a prompt and returns the match.

### 1.5 Modify file

```
Append the line "Second line" to test_fs/hello.txt
```

**Expected:** A confirmation prompt for `modify_file` — approve. Verify:

```bash
cat workspace/test_fs/hello.txt
```

Expected:
```
Hello from the agent
Second line
```

---

## Feature 2 — Permission Management

### Scenario 2.1 — `ask` rule (write tools prompt on every call)

```
Create a folder called output and write output/numbers.md with the numbers 1 to 20 in words.
```

**What happens:**
- `create_folder` is `ask` — a four-option menu appears: **Yes / Yes, don't ask again / No /
  No, never ask**. Navigate with ↑↓ and press Enter to confirm.
- `write_file` is also `ask` — the menu appears again for the file write. "Yes, don't ask again"
  does **not** suppress future `ask` tools — it only builds a session cache for `allow` tools.

Verify:

```bash
cat workspace/output/numbers.md
```

Expected: a Markdown list with `one` through `twenty`.

### Scenario 2.2 — `deny` rule blocks the tool entirely

```
Use the execute_command tool to run `ls -la` inside the workspace.
```

**What happens:** The agent attempts the call; the permission gate returns an error observation
immediately — no prompt, no execution. The agent reports it cannot run the command.

!!! note "Retry behaviour"
    Depending on the model, you may see the agent attempt `execute_command` two or three
    times before producing a final answer. Each attempt is blocked identically — the error
    observation includes a "Do NOT retry this tool" directive, but model compliance varies.
    All retries show `"permission_outcome": "deny"` in the log; none execute.

Verify in the log:

```bash
jq 'select(.event_type=="permission_decision" and .outcome=="deny")' \
  workspace/.nikola/logs/agent.jsonl | tail -5
```

Expected: one or more records with `"outcome": "deny"` and `"tool": "execute_command"`.

### Scenario 2.3 — Path-based deny (same tool, different outcome)

```
Write the text "test" into a file called .env
```

**What happens:** `write_file` is normally `ask`, but the `.env*` path rule sits **above** it
in `permissions.yaml` and first-match-wins — so this is denied outright without prompting.

Verify:

```bash
ls workspace/.env 2>/dev/null && echo EXISTS || echo "Correctly blocked"
```

Expected: `Correctly blocked`

Also verify in the log:

```bash
jq 'select(.event_type=="permission_decision" and .outcome=="deny" and .tool=="write_file")' \
  workspace/.nikola/logs/agent.jsonl | tail -5
```

### Scenario 2.4 — MCP tools fall through to `default: ask`

```
Schedule a team meeting tomorrow at 10am for 1 hour called "Weekly Sync"
```

**What happens:** `mcp_calendar__*` tools match no explicit rule and fall through to
`default: ask`. Each calendar tool call pops the confirmation menu. Approve them.

After approving, verify the calendar entry was created:

```bash
cat workspace/calendar.ics
```

Then check what the session approved:

```
/permissions status
```

Expected output lists every tool that received a session-level approval this run.

!!! tip "Stop the prompts permanently"
    Uncomment the rule in `permissions.yaml`:

    ```yaml
    - tool: "mcp_calendar__*"
      action: allow
    ```

    Then re-attach (`docker compose restart agent && docker compose attach agent`
    or `podman compose restart agent && podman compose attach agent`).
    This is also **required** for Scenario 5.2 branch 3 — sub-agents cannot show the
    confirmation menu, so `ask` auto-denies inside them. An explicit `allow` rule is what
    lets the `scheduler` role use the calendar.

---

## Feature 3 — MCP Integration (Calendar Server)

The calendar server runs at `http://localhost:3000`.

### 3.1 Health check

```bash
curl -s http://localhost:3000/health
```

Expected: `{"status":"ok"}` or similar.

### 3.2 Create an event via the agent

```
Add a calendar event: "Code Review" on Friday at 2pm for 30 minutes
```

Approve the MCP tool calls when prompted. Verify:

```bash
cat workspace/calendar.ics
```

Look for `SUMMARY:Code Review`.

### 3.3 Query the calendar

```
What events do I have this week?
```

The agent calls `mcp_calendar__list_events` (approve when prompted) and summarises the results.

---

## Feature 4 — Long-term Memory

Memory uses SQLite (persistent) and Qdrant (semantic search). Browse both while testing.

### 4.1 Store a memory

```
Remember that my preferred code style is PEP 8 and I dislike trailing whitespace
```

A confirmation prompt appears for `remember` (it is `ask`). Approve.

Verify in the SQLite browser at **http://localhost:8081** — the `memories` table should have
a new row. Or from the host:

```bash
sqlite3 workspace/.nikola/.memory/memories.db \
  "SELECT content FROM memories ORDER BY created_at DESC LIMIT 3;"
```

### 4.2 Retrieve a memory (semantic)

Start a **new session** (CTRL-C to end, re-attach as in Step 2) then:

```
What do you know about my code style preferences?
```

**Expected:** The agent calls `search_memory` (which is `allow`) and returns the stored fact
without you having told it again in this session.

### 4.3 Memory persists across container restarts

```bash
docker compose restart agent && docker compose attach agent
```

Then ask:

```
What coding preferences do you have stored for me?
```

**Expected:** The agent finds the earlier memory — it survived the restart because it is stored
in `workspace/.nikola/.memory/memories.db`, which is a host-mounted volume.

---

## Feature 5 — Sub-agents

### Scenario 5.1 — Parallel sub-agents (same role)

```
Use delegate_parallel: one file-scout to find every skill in .nikola/skills that calls an
external HTTP API, and at the same time another file-scout to list every rule in
.nikola/rules and what it instructs.
```

**What happens:**
- Two live cards appear in the terminal, each showing:
  `⠙ Running · file-scout · N tools · N.Nk · Ns`
- Both spin concurrently — wall time is the slower task, not the sum.
- The parent receives only the two digests; it never paid for the raw file content.

Verify sub-agent metrics:

```bash
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'
```

Expected: a line like `agent_subagent_runs_total{role="file-scout"} 2`

Check context growth:

```
/context
```

The parent's history should contain the two summaries, not the full file contents.

### Scenario 5.2 — Three different roles simultaneously

```
I need three independent things done at the same time:
1. Search .nikola/skills and find every file that references "json" — return a digest of
   where it is used
2. Explain how the mensa-passau skill turns an HTTP response into the text shown in the
   terminal — read the relevant code and give me a self-contained explanation with citations
3. Check my calendar: what is today's date, and do I have any events today?

Run all three concurrently.
```

> **Note on branch 3:** MCP tools are `ask` by default. Because sub-agents cannot prompt you,
> `ask` becomes auto-denied inside a sub-agent. To make branch 3 work, first allow the calendar
> tool. Edit `permissions.yaml` and uncomment:
>
> ```yaml
> - tool: "mcp_calendar__*"
>   action: allow
> ```
>
> Then restart the agent: `docker compose restart agent && docker compose attach agent`
> (or `podman compose restart agent && podman compose attach agent`)

**What happens:**
- The parent picks `file-scout` for branch 1, `general` for branch 2, `scheduler` for branch 3.
- Three live cards spin simultaneously.
- Three label sets appear in metrics — this fills the **Sub-agent runs by role** panel in Grafana.

Verify:

```bash
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'
```

Expected three lines:

```
agent_subagent_runs_total{role="file-scout"} N
agent_subagent_runs_total{role="general"} N
agent_subagent_runs_total{role="scheduler"} N
```

---

## Feature 6 — Observability (Prometheus + Grafana + Loki)

### 6.1 Prometheus scrape endpoint

```bash
curl -s http://localhost:9464/metrics | head -40
```

Look for these metric families:

| Metric | What it tracks |
|---|---|
| `agent_runs_total` | Total completed runs |
| `agent_run_duration_seconds` | Per-run latency histogram |
| `agent_llm_calls_total` | LLM invocations |
| `agent_tool_calls_total` | Tool invocations, labelled by tool and source (`builtin`/`mcp`) |
| `agent_permission_decisions_total` | Outcomes labelled `allow`/`deny`/`ask` |
| `agent_subagent_runs_total` | Sub-agent completions labelled by role |
| `agent_tokens_used_total` | Token consumption labelled by type |
| `agent_active_runs` | Gauge — runs currently in progress |

```bash
# Focus on permission and sub-agent metrics after running scenarios 2 and 5
curl -s localhost:9464/metrics | grep -E "^agent_(permission|subagent)"
```

### 6.2 Prometheus target health

Open **http://localhost:9090/targets** in a browser.

Expected: the `agent` target shows **UP** with a recent scrape timestamp.

### 6.3 Grafana dashboard

Open **http://localhost:3001** (anonymous admin, no login).

The **Agent Metrics** dashboard opens automatically. After running the scenarios above you should
see data in every panel:

| Panel | Populated by |
|---|---|
| Agent throughput / active runs | Scenarios 1–6 |
| Run / LLM / tool / sub-agent latency | All runs |
| Success and failure rates | All runs |
| Token usage | All LLM calls |
| Tool calls — built-in vs MCP | Scenarios 1–4 |
| Permission decisions | Scenarios 2.1–2.4 |
| Sub-agent runs by role | Scenario 5.2 |

> **Flat panels before the first turn are normal.** The agent publishes nothing until the first
> run completes.

### 6.4 Structured logs — per-run drilldown

#### From the host filesystem

```bash
# All log entries for a single session (replace with your session ID)
SESSION=$(jq -r '.session' workspace/.nikola/logs/agent.jsonl | tail -1)
jq --arg s "$SESSION" 'select(.session==$s)' workspace/.nikola/logs/agent.jsonl

# Every tool call in the log
jq 'select(.event_type=="tool_call")' workspace/.nikola/logs/agent.jsonl

# Sub-agent completions
jq 'select(.event_type=="subagent_end")' workspace/.nikola/logs/agent.jsonl

# Denied permission decisions only
jq 'select(.event_type=="permission_decision" and .outcome=="deny")' \
  workspace/.nikola/logs/agent.jsonl

# All errors
jq 'select(.level=="ERROR")' workspace/.nikola/logs/agent.jsonl
```

#### From Grafana → Drilldown → Logs

In Grafana click **Drilldown → Logs** (left sidebar) and enter LogQL:

```logql
# All agent logs
{service="agent"}

# Filter to one session
{service="agent"} | session="<paste-session-id-here>"

# Tool calls only
{service="agent"} | event_type="tool_call"

# Sub-agent completions
{service="agent"} | event_type="subagent_end"
```

---

## Feature 7 — Context Budget and Compaction

The agent manages its own token budget and compacts history when it fills up.

### 7.1 Check current context state

```
/context
```

Shows: window size, used tokens, remaining tokens, message count, and whether compaction has run.

### 7.2 Trigger compaction manually (optional)

Send many messages in a single session to push the context toward its limit, or type:

```
/compact
```

The agent summarises older turns into a single compaction message and continues with more room.

---

## Feature 8 — MCP Traffic Inspection (mitmproxy)

All LLM traffic goes through the mitmproxy reverse proxy.

### 8.1 Web UI

Open **http://localhost:9091**

Every `/v1/chat/completions` request appears here. Click any entry to see:
- Request headers (including the `Authorization` header — verify the token is there)
- Request body (the full `messages` array sent to the LLM)
- Response body (the LLM's reply)

### 8.2 HAR file export

The proxy writes a HAR archive continuously to `har-output/traffic.har`. After a session:

```bash
python3 -m json.tool har-output/traffic.har | grep '"url"' | head -20
```

---

## Feature 9 — Vector Memory (Qdrant)

### 9.1 Dashboard

Open **http://localhost:6333/dashboard**

Navigate to the **Collections** tab — you should see `agent_memories` after the first `remember`
call.

### 9.2 REST API

```bash
# Collection info
curl -s http://localhost:6333/collections/agent_memories | python3 -m json.tool

# Point count
curl -s http://localhost:6333/collections/agent_memories/points/count | python3 -m json.tool
```

---

## Test Suite (automated)

All tests run inside Docker — no local Python environment required.

### Unit tests (no token or running services needed)

```bash
# Full unit suite (456 tests)
docker compose run --rm --no-deps --entrypoint pytest agent tests/unit -q

# Week 3 subsections only
docker compose run --rm --no-deps --entrypoint pytest agent \
  tests/unit/hooks tests/unit/permissions tests/unit/subagents -q
```

### E2E tests (requires the stack to be up)

```bash
docker compose run --rm --entrypoint pytest agent tests/e2e -q
```

The canonical E2E cases (from spec §8.2):

| Test | Tools exercised | Assertion |
|---|---|---|
| **numbers-in-words** | `create_folder`, `write_file`, `search_text` | `output/numbers.md` contains `'one'` and `'twenty'`; final turn acknowledges |
| **find-and-inspect** | `list_files`, `read_file` | Agent lists both files and reports 5 lines from `notes.txt` |

### Specific test directories

```bash
# Hooks instrumentation
docker compose run --rm --no-deps --entrypoint pytest agent tests/unit/hooks -v

# Permission policy (allow/deny/ask, path rules, sub-agent auto-deny)
docker compose run --rm --no-deps --entrypoint pytest agent tests/unit/permissions -v

# Sub-agent orchestration and roles
docker compose run --rm --no-deps --entrypoint pytest agent tests/unit/subagents -v
```

---

## Tear Down

When done testing:

```bash
docker compose down
```

To also remove persistent volumes (Prometheus data, Grafana dashboards, Qdrant storage):

```bash
docker compose down -v
```

> The `workspace/` directory on the host is **not** removed by `compose down` — files the agent
> created during the session persist there.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Blank screen after `docker attach` | Attached to an already-running agent | Press **Enter** to repaint the prompt, or `docker restart … && docker attach …` |
| Narrow prompt box | Window was too small when attaching | Detach (`CTRL-P CTRL-Q`), widen the window, re-attach |
| `INNKUBE_ACCESS_TOKEN variable is not set` | Variable not exported in this shell | `export INNKUBE_ACCESS_TOKEN=<token>` and re-run `compose up` |
| Grafana panels are flat | No runs yet, or attached via `exec`/`run` instead of `attach` | Check http://localhost:9090/targets — the `agent` target must show **UP** |
| Sub-agent refuses a tool | Tool is `ask` — sub-agents cannot prompt, so `ask` → `deny` | Give the tool an explicit `allow` rule in `permissions.yaml` |
| Sub-agent finds nothing | It reads `/workspace`, not the repo source | Target `.nikola/skills`, `.nikola/rules`, or files created in earlier scenarios |
| `mcp_calendar` tool denied in sub-agent | MCP default is `ask`; sub-agent can't prompt | Uncomment `mcp_calendar__*: allow` in `permissions.yaml` and restart the agent |
| `docker compose ps` shows service unhealthy | Dependency not ready | `docker compose logs <service>` to inspect; re-run `compose up` |
| Log file missing | Workspace not writable | `workspace_init` may have failed — check `docker compose logs workspace_init` |
