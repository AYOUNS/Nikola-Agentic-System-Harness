# AGENTS.md — Documentation Context

This file provides guidance to agents answering questions about this repository.

## What this project is

**AI Engineering Lab 2026** — a 3-week university lab where a team of 3 implements a Python 3.12 ReAct-loop agent harness as a terminal CLI application. Full spec: [`docs/AI_Engineering_Lab_2026.pdf`](docs/AI_Engineering_Lab_2026.pdf).

## Non-obvious context

- **LLM provider is InnKube** (internal university cluster), not OpenAI/Anthropic. Token expires Sep 30. Endpoint docs at `innkube.pages.gitlab.innkube.fim.uni-passau.de`.
- **No delete tools** — intentional design constraint from the spec, not an oversight.
- **Deletion tool absence** applies even if a user asks the agent to delete something — the harness must refuse.
- **Daily reports are graded**: missing the GitLab commit deadline 3× = course failure.
- **Thursday 11am**: runnable version must be committed each week for TA review.
- **Architecture diagrams are deliverables**: must be committed to GitLab, not just drawn locally.
- **`daily_reports/` folder** contains both team planning context (Week 1 decisions) and graded academic submissions.
- The project has three distinct scope layers: Week 1 (core harness) → Week 2 (MCP + memory) → Week 3 (observability + permissions + sub-agents). Do not pre-solve next week's deliverables.
- Individual written report must be 3,000–4,000 words (excluding tables/graphics/appendices).
