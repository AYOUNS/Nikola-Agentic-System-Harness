# ADR 0002 — Sub-agent Confirmation Auto-deny

**Status:** Accepted  
**Date:** 2025

---

## Context

The permission system supports an `ask` action that pauses the agent and prompts the user for
confirmation before a tool runs. Sub-agents are separate harness instances that execute
asynchronously with no access to the parent's terminal session.

When a sub-agent encounters a tool with `action: ask`, the harness must decide what to do.

Four alternatives were considered:

| Alternative | Description |
|---|---|
| **Auto-deny** | `ask` tools are automatically denied inside sub-agents with a clear error message |
| **Bubble to parent** | The sub-agent suspends and asks the parent to relay the prompt to the user |
| **Share session cache** | Sub-agents inherit the parent's runtime approval cache so already-approved tools don't ask again |
| **Pre-flight confirmation** | At delegation time, the parent lists all `ask` tools the sub-agent might call and prompts the user once |

---

## Decision

**Auto-deny (`ask` → `deny` when `is_subagent=True`).**

When a tool with `action: ask` is called inside a sub-agent, the gate immediately returns
a denial with the message:

```
Tool '<name>' requires confirmation, but sub-agents cannot prompt the user.
Update role configuration to use 'allow' or 'deny'.
```

---

## Rationale

**No interactive channel.** Sub-agents run concurrently with the parent's REPL loop. There is
no safe mechanism to suspend a sub-agent, relay a prompt through the parent to the user, wait
for a response, and resume — especially when multiple sub-agents are running in parallel.

**Forces explicit role design.** Auto-deny means the role designer must explicitly decide which
tools a sub-agent is allowed to use. A sub-agent that silently does nothing because all its
tools are stuck waiting for confirmation is harder to debug than one that fails immediately
with a clear error.

**Bubbling is complex and fragile.** The parent would need a multiplexed prompt channel, a
way to suspend and resume sub-agent goroutines/threads, and handling for the case where the
user rejects the prompt after the sub-agent has already started work. The complexity is not
justified when the alternative (explicit role allowlists) is available and simple.

**Sharing session cache is insufficient.** The parent's cache only covers tools the parent has
already been asked about. A sub-agent may call tools the parent never touched, and it would
still hit `ask` for those.

**Pre-flight confirmation is speculative.** The harness does not know ahead of delegation which
tools the sub-agent will call. Prompting for every tool in the role's allowlist (not just
the ones actually used) is both annoying and incomplete — the sub-agent's path through its
task determines which tools it needs.

---

## Consequences

**Sub-agents that need `ask` tools will fail immediately** with a clear error message pointing
to the fix. This is intentional — the failure surfaces the misconfiguration.

**The fix is always simple:** add an explicit `allow` rule for the tool in `permissions.yaml`,
or configure the role's allowlist accordingly.

**The scheduler role requires an explicit allow for MCP tools.** MCP tools fall through to
`default: ask`, which auto-denies inside sub-agents. Adding `- tool: "mcp_calendar__*" action:
allow` to `permissions.yaml` is all that is required.
