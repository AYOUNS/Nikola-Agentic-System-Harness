---
hide:
  - toc
---

# About Nikola

<p align="center">
  <img src="../../assets/logo.svg" alt="nikola agent harness" width="280" style="margin:1.5rem 0;"/>
</p>

---

## The name

**Nikola** comes from the Greek *Nikolaos* — *nike* (victory) + *laos* (people): **"victory of the people"**.

The name was chosen because the harness exists to put the model's abilities in the user's hands, not the other way round. The loop, the tools, the permissions, the sandbox — all of it is infrastructure that removes friction between the user's intent and a useful result.

The name also nods to **Nikola Tesla** — an engineer who turned invisible electrical currents into working systems. The harness does something similar: it takes a stateless language model (a pattern over tokens, invisible and inert on its own) and turns it into a working agent that can read files, schedule meetings, search memory, and delegate to sub-agents.

---

## The logo

The logo is a stylised map of **Passau's three rivers**:

- The **Danube** flows in from the west
- The **Inn** flows in from the south  
- The **Ilz** flows in from the north

All three converge at a single node — the **ReAct loop** — and leave as one unified stream: the agent's answer.

The three rivers stand for the three things that meet in every turn:

| River | Represents |
|-------|-----------|
| The user's task (Danube) | What the user wants |
| The LLM's reasoning (Inn) | How to get there |
| The tools (Ilz) | What the agent can do |

The node where they meet is the loop. The single outgoing stream is the final answer.

---

## Passau

Passau is the *Dreiflüssestadt* — "City of Three Rivers" — in Lower Bavaria on the Austrian border, where the Danube, Inn, and Ilz actually meet. At the confluence you can see three distinct water colours side by side before they blend.

The University of Passau sits on the banks of the Inn. This harness was built there, for the AI Engineering Lab (SoSe 2026), by Group 3.

Passau is also home to St. Stephen's Cathedral, which houses one of the largest pipe organs in the world.

---

## Built by

**Group 3** — AI Engineering Lab, SoSe 2026  
University of Passau, Faculty of Computer Science and Mathematics

The harness was built over three weekly sprints:

| Week | Focus |
|------|-------|
| 1 | ReAct loop, Terminal CLI, ToolRegistry, filesystem tools, tests |
| 2 | MCP integration, long-term memory (SQLite + Qdrant), skills, context compaction |
| 3 | Permission management, sub-agents (3 roles, parallel), Prometheus + Grafana observability |
