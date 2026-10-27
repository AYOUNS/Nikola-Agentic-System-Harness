# Installation

Everything runs inside a container. You need one container runtime and the `INNKUBE_ACCESS_TOKEN`
from the course portal — nothing else is installed on your host.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Docker** or **Podman** | Either works — commands are identical, just swap the binary name |
| `INNKUBE_ACCESS_TOKEN` | Issued by the course; expires Sep 30 — do not commit it |
| **Python 3.12** | Only needed if you want to run tests or `mkdocs serve` outside the container |

---

## 1 — Export the required environment variables

Open a terminal and export both variables. You must do this in **every** shell you use —
the one that starts the stack and the one that runs the tests:

```bash
export INNKUBE_ACCESS_TOKEN=<your_token>
export AGENT_WORKSPACE="./workspace"
```

`AGENT_WORKSPACE` sets the sandbox root the agent may read and write. The default is
`./workspace` (relative to the repo root). The token is never written to disk; Compose
substitutes it into the `agent` service at startup.

---

## 2 — Build and start the stack

**Terminal 1** — build images and start all services in the background:

=== "Docker"

    ```bash
    docker compose up --build -d
    ```

=== "Podman"

    ```bash
    podman compose up --build -d
    ```

This starts the full observability stack (Prometheus, Grafana, Loki, Alloy), the MCP calendar
server, the mitmproxy LLM proxy, and the agent itself. See
[Services & Endpoints](services.md) for the full port map.

---

## 3 — Attach to the agent

**Terminal 2** — widen this window first, then restart and attach:

=== "Docker"

    ```bash
    docker compose restart agent && docker compose attach agent
    ```

=== "Podman"

    ```bash
    podman compose restart agent && podman compose attach agent
    ```

!!! tip "Why restart first?"
    Restarting before attaching puts you on the stream **before** the agent boots, so you see
    the full startup screen — banner, Agent Mode, model name, workspace path. `attach` alone
    replays nothing; if the agent is already running you land on a blank terminal. Press
    **Enter** to repaint the prompt, or read the startup screen with
    `docker compose logs agent` (or `podman compose logs agent`).

!!! tip "Window size matters"
    The container's terminal size is taken from your window at attach time. Widen the window
    *before* attaching — a narrow window gives a narrow prompt box. To fix a narrow prompt,
    detach and re-attach after resizing.

---

## 4 — Detach without stopping the agent

| Key | Effect |
|---|---|
| **CTRL-P CTRL-Q** | Detach — the agent keeps running in the background. **This is the correct way out.** |
| **CTRL-C** | Ends the session. The container has a TTY, so CTRL-C reaches the process as SIGINT regardless of `--sig-proxy=false`. |

!!! warning "Attach, don't `exec` or `run`"
    The container entrypoint is already the CLI, and that process owns the metrics port `9464`.
    A second agent started with `compose exec` cannot bind `9464` — the bind failure is caught
    and logged, so the session works, but metrics are not published.
    `compose run` gets no `agent` network alias.
    Use `docker compose exec agent bash` (or `podman compose exec agent bash`) as a second
    shell for `curl`/`jq` diagnostics — just don't start a second `agent.cli` in it.

---

## 5 — Tear down

```bash
docker compose down   # or: podman compose down
```

This stops and removes all containers. Data written to `./workspace` on the host persists.

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Blank screen after `attach` | Agent was already running — press **Enter** to repaint, or `restart && attach` |
| Narrow prompt box | Window was too small when you attached — detach, resize, re-attach |
| `INNKUBE_ACCESS_TOKEN variable is not set` | Not exported in this shell — every new terminal needs it |
| Grafana panels are all flat | Normal before the first turn; if you've run turns, check you used `attach` not `exec`/`run`. See <http://localhost:9090/targets> |
| Sub-agent refuses a tool | Expected when that tool is `ask` — sub-agents cannot prompt you, so `ask` becomes `deny`. Add an explicit `allow` rule in `permissions.yaml` |
| `chrome-devtools  failed · 0 tools` at startup | **Expected** — the container is Python-only and has no `npx`. The `chrome-devtools` stdio MCP entry in `.nikola/mcp.json` requires Node.js, which is not installed. Remove that entry from `mcp.json` if you don't need browser automation, or ignore the warning — all other tools continue to work. |
