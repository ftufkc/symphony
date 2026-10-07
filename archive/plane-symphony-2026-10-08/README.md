# plane-symphony

Pick up **general-purpose tasks** from a [Plane](https://github.com/makeplane/plane) board and
complete them autonomously with **OpenAI Codex**, writing the result back to the work item.

A "general task" variant of [OpenAI symphony](https://github.com/openai/symphony): symphony turns
Linear issues into autonomous *coding* runs; this turns Plane work items into autonomous *general
work* runs (research, drafting, summarizing, computing, anything Codex can do) — **no GitHub / code
service required**.

## How it works

```
state -> "AI Todo"   ─poll(15s)─┐
@mention bot in a comment ─webhook─┤→ queue → Orchestrator ─claim→ "AI Doing"
                                   │            (dedup / reconcile)      │
                                   │                                     ▼
                                   │            Codex run (workspace_write + auto_review)
                                   │            with the Plane MCP attached
                                   │                                     │
                                   └──────────────  agent reads/writes Plane via MCP ──────────┘
                                                    ↳ summary comment (always)
                                                    ↳ state -> "AI Done" or "Human Review"
```

- **Engine is generic; task policy lives in `WORKFLOW.md`** (frontmatter config + the agent prompt) — the symphony philosophy.
- **Codex writes back itself** via a **Plane MCP server** that exposes convenience tools
  (`get_work_item` / `add_comment` / `set_state` / …) **plus a `plane_request` raw passthrough** so
  capability is never capped.
- **Always leaves a summary comment** (what it did / result / problems), symphony-style.
- **No database** — Plane is the source of truth; runtime state is in-memory; logs for observability.
  A `Store` seam is left for optional SQLite later.

See [`docs/superpowers/specs/2026-06-14-plane-symphony-design.md`](docs/superpowers/specs/2026-06-14-plane-symphony-design.md)
for the full design and [`AGENTS.md`](AGENTS.md) for repo conventions.

## Prerequisites

- Python at `/opt/homebrew/Caskroom/miniforge/base/bin/python` with `openai-codex`, `mcp`, `httpx`, `pyyaml`.
- Codex authenticated (`~/.codex/auth.json`).
- A running Plane instance. **The REST API is on `:8000`** (`http://localhost:8000/api/v1`), not the
  `:3000` frontend.
- A Plane **bot user** with a **Personal Access Token**, added to every project you want served.

## Setup

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/bin/python
$PY -m pip install -e . --no-deps
cp .env.example .env        # then fill PLANE_API_TOKEN, PLANE_WORKSPACE_SLUG, ...
$PY scripts/setup_states.py # create AI Todo / AI Doing / Human Review / AI Done in each project
```

State names, trigger, outcomes, poll interval, model, etc. are configured in `WORKFLOW.md`.

## Run

```bash
$PY -m plane_symphony
```

Starts the poller (state trigger) + webhook receiver (mention trigger) and processes work items.
On startup it resets orphaned `AI Doing` items back to `AI Todo`.

## Triggers

- **`AI Todo` state** — move a work item to `AI Todo`; the poller picks it up (self-healing).
- **@mention the bot** in a comment — delivered by webhook; the bot replies without changing state.

### Webhook delivery to a local bot (SSRF allowlist)

Plane blocks local/private webhook URLs for SSRF safety, but has a built-in allowlist for trusted
internal-service DNS. Set it on Plane's **api + worker** containers and recreate them:

```bash
# in your Plane deployment's apps/api/.env:
WEBHOOK_ALLOWED_HOSTS=host.docker.internal
# then:
docker compose -f docker-compose-local.yml up -d --force-recreate api worker beat-worker
```

Then register the webhook (Workspace Settings → Webhooks) pointing at
`http://host.docker.internal:<WEBHOOK_PORT>/webhook` and copy its secret into `.env` as
`WEBHOOK_SECRET`. **Verified live**: Plane delivers real `issue` / `issue_comment` events to the
receiver with a valid HMAC signature, correctly parsed (Plane uses past-tense actions
`created`/`updated`). The **state trigger via polling needs no webhook** and always works.

## Tests

```bash
/opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest
```

## Status

Verified end-to-end against a live local Plane + real Codex: state-trigger run → `AI Done` +
summary comment; mention → reply (no state change); restart orphan recovery; **real Plane→webhook
delivery** (signed `issue`/`issue_comment` events received + parsed). 51 unit tests green.
