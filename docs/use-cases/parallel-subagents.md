# Use Case: Parallel Sub-agents

The harness can delegate multiple independent tasks concurrently using `delegate_parallel`.
All sub-agents run simultaneously; wall time is the slowest task, not the sum.

---

## When to use parallel delegation

Use `delegate_parallel` when:

- The tasks are **independent** — they do not depend on each other's output
- Each task benefits from a **dedicated role** (e.g. read-only research vs calendar access)
- You want to **save time** — parallel execution vs sequential adds up on long tasks

Do not use it when task B depends on the output of task A — use sequential `delegate` calls
instead.

---

## Example: two file-scouts at once

**Prompt:**

```
Use delegate_parallel: one file-scout to find every skill in .nikola/skills that calls
an external HTTP API, and at the same time another file-scout to list every rule in
.nikola/rules and what each one instructs.
```

**What happens:**

1. The parent calls `delegate_parallel` with two tasks, both using the `file-scout` role.
2. Two sub-agents start simultaneously. Live progress cards appear:

```
⠙ Running · file-scout · 5 tools · 22.4k · 6s
⠙ Running · file-scout · 3 tools · 18.1k · 6s
```

3. Both finish. The parent receives two summaries and writes one combined answer.
4. `/context` shows the parent's history grew by the two summaries only.

---

## Example: three different roles at once

**Prompt:**

```
I need three things done simultaneously:
1. Search .nikola/skills for every file that references "json" — return a digest
2. Explain how the mensa-passau skill turns an HTTP response into terminal text —
   read the relevant code and give a self-contained explanation with file:line citations
3. Check my calendar — what events do I have today?

Run all three concurrently.
```

**Roles selected:**

| Branch | Role | Why |
|---|---|---|
| 1 | `file-scout` | Read-only file search |
| 2 | `general` | Explanation with citations, larger result budget |
| 3 | `scheduler` | Calendar access via MCP |

**Verify in metrics:**

```bash
curl -s localhost:9464/metrics | grep '^agent_subagent_runs_total'
```

Three distinct `role` label values — `file-scout`, `general`, `scheduler` — confirm all
three roles ran.

---

## Progress cards

While sub-agents run, each gets a live card in the terminal:

```
⠙ Running · file-scout  · 5 tools · 22.4k tokens ·  6s
⠙ Running · general     · 2 tools · 31.2k tokens ·  6s
⠙ Running · scheduler   · 1 tools ·  8.1k tokens ·  6s
```

Fields: spinner · role · tool calls so far · tokens consumed · elapsed seconds.
The card updates after every LLM response and tool call inside the sub-agent.

---

## Context efficiency

The parent's context grows only by the final summaries, not by the sub-agents'
full conversation histories. For tasks that read many files or make many tool calls,
this keeps the parent's budget manageable.

Run `/context` before and after a parallel delegation to see the difference.

---

## Constraints

- Sub-agents cannot prompt the user — tools with `action: ask` are auto-denied.
  See [Sub-agents — Auto-deny](../concepts/subagents.md#auto-deny-for-ask-tools).
- Sub-agents read `/workspace`, not the repository source (`agent/`, `tests/`, etc.).
- Each role has a maximum iteration count and result length — summarise the task clearly
  so the sub-agent does not exceed its budget.
