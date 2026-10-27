# ADR 0001 — Permission Rule Resolution Order

**Status:** Accepted  
**Date:** 2025

---

## Context

The permission system must evaluate multiple rules to decide whether a tool call is allowed,
denied, or needs confirmation. When more than one rule could match a given tool call, the
system needs a deterministic resolution strategy.

Four strategies were considered:

| Strategy | Description |
|---|---|
| **First-match-wins** | Rules are evaluated top-to-bottom; the first matching rule determines the outcome |
| **Most-specific-match** | The rule with the most specific match (e.g. exact name beats glob) wins |
| **Deny-wins** | Any matching deny rule overrides all allow/ask rules |
| **Error-on-ambiguity** | If more than one rule matches, raise an error |

---

## Decision

**First-match-wins (top-to-bottom).**

Rules in `permissions.yaml` are evaluated in declaration order. The first rule whose `tool`
pattern and optional `path_pattern` match the call determines the outcome. No further rules
are checked.

---

## Rationale

**Predictability.** The operator can read the file from top to bottom and know exactly which
rule will fire for any given tool call. There is no ambiguity resolution algorithm to reason
about.

**Expressiveness.** Path-specific overrides work naturally. A deny rule for `.env*` paths can
appear above a generic `ask` rule for `write_file`, and the deny fires first. With deny-wins
the operator cannot grant an exception above the deny; with most-specific-match the semantics
of "specificity" for glob patterns are non-obvious.

**Familiarity.** First-match-wins is the same strategy used by firewall rules, `.gitignore`,
nginx `location` blocks, and most permission/routing systems. Operators already have a mental
model for it.

**Simplicity of implementation.** A linear scan with early exit is a two-line loop. Most-specific
and deny-wins require pattern scoring or multi-pass evaluation.

---

## Consequences

**Rule ordering is the operator's responsibility.** More specific or higher-priority rules must
appear above more general ones. The system does not warn about shadowed rules.

**Example ordering error:**

```yaml
# WRONG — the generic ask rule fires first; the deny rule is never reached
- tool: write_file
  action: ask

- tool: write_file
  path_pattern: ".env*"
  action: deny
```

```yaml
# CORRECT — deny fires for .env* paths; ask fires for everything else
- tool: write_file
  path_pattern: ".env*"
  action: deny

- tool: write_file
  action: ask
```
