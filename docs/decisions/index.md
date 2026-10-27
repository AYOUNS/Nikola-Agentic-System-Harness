# Architecture Decision Records

ADRs document decisions that are hard to reverse, surprising without context, or the result
of real trade-offs between alternatives. They record *why* the system works the way it does,
not just *how*.

| # | Decision | Status | Date | Summary |
|---|---|---|---|---|
| [0001](0001-permission-rules.md) | Permission rule resolution order | Accepted | 2025 | First-match-wins (top-to-bottom) over deny-wins or most-specific-match |
| [0002](0002-subagent-autodeny.md) | Sub-agent confirmation auto-deny | Accepted | 2025 | `ask` tools auto-deny inside sub-agents instead of bubbling to parent |
| [0003](0003-memory-split.md) | Memory storage split | Accepted | 2025 | SQLite as mandatory source of truth + Qdrant as optional semantic index |
