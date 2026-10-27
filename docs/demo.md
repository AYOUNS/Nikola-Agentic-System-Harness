# Demo Walkthrough

Seven scenarios that cover the full Week 3 feature set: permissions, MCP tools, sub-agents,
and observability. Run them in order inside the agent REPL.

**Prerequisites:** start the full stack first — see [Installation](getting-started/installation.md).

---

## CLI Quick Reference

All interaction happens inside the agent terminal. Type any of these at the `›` prompt:

| Command | What it does |
|---|---|
| `/help` | List every available command |
| `/tools` | List built-in tools |
| `/mcp` | Show connected MCP servers and their tools |
| `/model` | Show the active LLM model |
| `/mode <name>` | Switch Agent Mode (restarts conversation) |
| `/context` | Show context window usage and token budget |
| `/compact [hint]` | Summarise the conversation and free context |
| `/permissions status` | Show which tools you have approved this session |
| `/rules` | List loaded user rules and their token cost |
| `/skills [list\|load <name>]` | List available skills or manually load one |
| `/clear` | Forget the conversation (keep the mode prompt) |
| `/exit` | End the session |

**Navigation keys:**

| Key | Effect |
|---|---|
| **↑ / ↓** | Navigate permission confirmation menus |
| **Enter** | Confirm selection |
| **CTRL-P CTRL-Q** | Detach from the container — the agent keeps running |
| **CTRL-C** | End the session |

**Attach / detach:**

```bash
# Docker — restart to see the full startup banner, then attach
docker compose restart agent && docker compose attach agent

# Podman
podman compose restart agent && podman compose attach agent
```

Detach with **CTRL-P CTRL-Q**. Use `docker compose logs agent` to read the startup screen
after the fact.

!!! warning "Attach, don't `exec` or `run`"
    The container entrypoint is already the CLI and owns metrics port `9464`.
    A second process via `compose exec` cannot bind that port — metrics stop publishing.
    Use `docker compose exec agent bash` as a diagnostic shell only; never start a
    second `agent.cli` in it.

---

## Scenario 1 — A tool that asks first

**Prompt:**

```
Create a folder called output and write output/numbers.md with the numbers 1 to 20 in words.
```

**What to observe:**  
`create_folder` and `write_file` are both `ask` in `permissions.yaml`. The agent **pauses
before each write** and shows an amber permission panel with an arrow-key menu:

```
⚠ Permission needed — This will create a new folder

  path: output

Allow create_folder to run?

  ❯ Yes
    Yes, don't ask again for this tool
    No
    No, never ask for this tool
```

Pick **Yes** with ↑/↓ and Enter; the agent calls the tool and continues.

!!! note "`ask` tools prompt on *every* call"
    "Yes, don't ask again for this tool" writes to the session cache, but an `ask`
    policy deliberately bypasses that cache — require-user-confirmation means every
    invocation, not just the first. So the second write in this scenario prompts again
    even if you chose it. To stop the prompts permanently, move the tool to an `allow`
    rule in `permissions.yaml`.

**Verify:**

```bash
cat workspace/output/numbers.md
```

**Check the session cache:**

```
/permissions status
```

---

## Scenario 2 — A tool that is denied

**Prompt:**

```
Use the execute_command tool to run `ls -la` inside the workspace.
```

**What to observe:**  
`execute_command` is `deny` in `permissions.yaml`. No prompt appears — the agent receives
an error Observation immediately and reports it cannot run the command.

!!! note "Retry behaviour"
    Depending on the model, you may see the agent attempt `execute_command` two or three
    times before producing a final answer. Each attempt is blocked identically — the error
    observation includes a "Do NOT retry this tool" directive, but model compliance varies.
    All retries show `"permission_outcome": "deny"` in the log; none execute.

**Verify the gate fired (not the system prompt):**

```bash
jq 'select(.event_type=="tool_call" and .tool=="execute_command")' \
  workspace/.nikola/logs/agent.jsonl
```

A record with `"permission_outcome": "deny"` confirms the gate blocked it.

> Scope the command to the workspace, as above. Asked to run `ls -la /`, the model often
> declines by itself on sandbox grounds and never calls the tool at all — which proves the
> system prompt, not the policy. The log record distinguishes which one fired.

---

## Scenario 3 — Denied by path, not by tool

**Prompt:**

```
Write the text "test" into a file called .env
```

**What to observe:**  
`write_file` is normally `ask`. But the `.env*` rule sits **above** it in `permissions.yaml`
and first-match-wins, so this call is denied outright. The agent receives:

```
Tool 'write_file' is denied by a permissions.yaml rule. Do NOT retry this tool - the
result will be identical. Use a different tool, or tell the user what permission change
would be needed.
```

No prompt appears — the gate fires before any user interaction. Same tool, different
argument, different outcome from Scenario 1.

**Verify the file was never created:**

```bash
ls workspace/.env 2>/dev/null && echo EXISTS || echo "Correctly blocked"
```

---

## Scenario 4 — Permissions cover MCP tools too

**Prompt:**

```
Schedule a team meeting tomorrow at 10am for 1 hour called "Weekly Sync"
```

**What to observe:**  
MCP tools (`mcp_calendar__*`) match no explicit rule and fall through to `default: ask`.
The agent pauses for each calendar tool call. Approve them and the event is written to
`workspace/calendar.ics`.

Then run:

```
/permissions status
```

This shows which tools you approved for the session.

**Verify the event was created:**

```bash
cat workspace/calendar.ics
```

!!! tip "Stop the prompts permanently"
    Uncomment the rule in `permissions.yaml`:

    ```yaml
    - tool: "mcp_calendar__*"
      action: allow
    ```

    Then re-attach (`docker compose restart agent && docker compose attach agent`
    or `podman compose restart agent && podman compose attach agent`).
    This is also **required** for Scenario 6 branch 3 — sub-agents cannot show the
    confirmation menu, so `ask` auto-denies inside them. An explicit `allow` rule is what
    lets the `scheduler` role use the calendar.

---

## Scenario 5 — Two sub-agents at once

**Prompt:**

```
Use delegate_parallel: one file-scout to find every skill in .nikola/skills that calls an
external HTTP API, and at the same time another file-scout to list every rule in
.nikola/rules and what it instructs.
```

**What to observe:**  
Two **live progress cards** appear and spin simultaneously:

```
⠙ Running · file-scout · 5 tools · 22.4k · 6s
⠙ Running · file-scout · 3 tools · 18.1k · 6s
```

Wall time is the slower task, not the sum of both. Each sub-agent runs the same harness
with its own read-only context; the parent receives only the two digest summaries.

**Run `/context` before and after** — the parent's history grows by the summaries alone,
not by the sub-agents' full conversation histories.

**Verify sub-agent metrics:**

```bash
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'
```

!!! note "Sub-agents read `/workspace`, not the repo source"
    Point tasks at `.nikola/skills`, `.nikola/rules`, or files an earlier scenario created.
    Aimed at repository symbols (`agent/`, `tests/`, etc.) the sub-agents still run and
    report honestly — but they find nothing, because that source is not in the workspace
    they can read.

!!! note "Sub-agents cannot prompt you"
    Any tool that is `ask` under the policy is auto-denied inside a sub-agent. This is
    why the `scheduler` role's calendar tools do not work until `mcp_calendar__*` has an
    explicit `allow` rule in `permissions.yaml`.

---

## Scenario 6 — All three roles at once

**Prompt:**

```
I need three independent things done at the same time:
1. Search .nikola/skills and find every file that references "json" — return a digest
2. Explain how the mensa-passau skill turns an HTTP response into the text shown in the
   terminal — read the relevant code and give me a self-contained explanation with citations
3. Check my calendar: what is today's date, and do I have any events today?

Run all three concurrently.
```

**What to observe:**  
The parent picks a **different role for each branch**:

- Branch 1 → `file-scout` (file search digest)
- Branch 2 → `general` (explanation with citations)
- Branch 3 → `scheduler` (calendar query)

Three cards spin together and the parent writes one answer from three results.

**Check which roles ran:**

```bash
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'
```

Three distinct label sets fill the **Sub-agent runs by role** panel in Grafana.

!!! note "Branch 3 requires an explicit `allow` rule"
    If `mcp_calendar__*` has no `allow` rule, branch 3 auto-denies (sub-agents cannot
    prompt). Add the rule to `permissions.yaml` to enable it. The denial itself is worth
    observing once — it demonstrates the same mechanism described in Scenario 2 from the
    sub-agent's perspective.

!!! note "Where scenario 6 differs from scenario 5"
    Scenario 5 names `delegate_parallel` outright and uses one role. Here the prompt never
    mentions a tool and the three branches are deliberately unlike — so the parent has to
    reason about which role fits each task. This is the scenario that fills all three rows
    of the **Sub-agent runs by role** panel in Grafana.

---

## Scenario 7 — See what just happened

Open **Grafana** at <http://localhost:3001> (anonymous admin, opens on **Agent Metrics**).

The run from scenarios 1–6 should be visible across all panels:

- **Agent throughput** — turns completed
- **Run latency** — end-to-end time per turn
- **Token usage** — prompt + completion tokens
- **Tool calls** — built-in vs MCP split
- **Permission decisions** — allow / deny / ask_yes / ask_no breakdown
- **Sub-agent runs by role** — `file-scout`, `scheduler`, `general` each with their own bar

**Drill into a single run via Loki** — go to **Drilldown → Logs**:

```logql
{service="agent"} | event_type="tool_call"
{service="agent"} | event_type="subagent_end"
{service="agent"} | session="<paste-session-id-here>"
```

Get the session ID from the startup banner or from:

```bash
jq -r '.session' workspace/.nikola/logs/agent.jsonl | head -1
```

A fresh agent that has run no turns will show flat panels — that is normal.

---

## If something looks wrong

| Symptom | Cause & fix |
|---|---|
| Blank screen after attach, or narrow prompt box | Agent was already running — press **Enter** to repaint, or `docker compose restart agent && docker compose attach agent`. Widen the window before attaching. |
| `INNKUBE_ACCESS_TOKEN variable is not set` | Not exported in this shell — every new terminal needs it |
| Grafana panels all flat | Normal before the first turn; if turns ran, check you used `attach` not `exec`/`run`. See <http://localhost:9090/targets> |
| Sub-agent refuses a tool | Expected when that tool is `ask` — sub-agents cannot prompt you, so `ask` becomes `deny`. Add an explicit `allow` rule in `permissions.yaml` |
| Sub-agent finds nothing | It reads `/workspace`, not the repo source — target `.nikola/skills`, `.nikola/rules`, or files an earlier scenario created |
| Calendar branch auto-denied in sub-agent | `mcp_calendar__*` has no `allow` rule — uncomment it in `permissions.yaml` and re-attach |
| `chrome-devtools  failed · 0 tools` at startup | **Expected** — the container has no `npx`. Remove that entry from `.nikola/mcp.json` if you don't need browser automation, or ignore it — all other tools work normally. |
