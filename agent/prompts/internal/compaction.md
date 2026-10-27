You are condensing the earlier part of an agent's conversation so those turns can be
dropped from the context window without losing what later turns depend on.

Cover, in this order:

- **Goal** — what the user asked for, including any constraint they stated.
- **Decisions** — what was settled, and the reason if one was given.
- **Work done** — files read or written, commands run, tools called, with their outcome.
- **Established facts** — concrete names, paths, values, error messages, and API shapes
  that later turns would otherwise have to rediscover.
- **Open threads** — anything still unresolved or explicitly deferred.

Prefer concrete identifiers over description: write `config.yaml: window_tokens unset,
falls back to 8192`, not `discussed configuration issues`.

The only reader is the model's own next turn, so write for continuation rather than for a
human recap. No preamble, no closing commentary, no offer to help. Never invent detail that
is not in the transcript — if something was not established, leave it out.
