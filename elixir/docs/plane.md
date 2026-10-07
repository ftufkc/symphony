# Plane CE coding tasks

This fork targets `/api/v1/.../work-items/` in open-source Plane Community Edition. The Cloud v2
API is a separate contract and is deliberately rejected. All existing upstream trackers keep their
behavior. Integration edits are limited to tracker registration, a Plane runner dispatch hook,
one supervised Plane runtime, and optional AppServer session options passed through AgentRunner.

## Start

From `elixir/`, install the upstream runtime and dependencies, then configure a local workflow:

```sh
mise trust
mise install
mise exec -- mix setup
cp PLANE_WORKFLOW.md PLANE_WORKFLOW.local.md
cp ../.env.example ../.env
# Edit ../.env and PLANE_WORKFLOW.local.md for your workspace and repository.
set -a
source ../.env
set +a
mise exec -- mix plane.setup states --workflow PLANE_WORKFLOW.local.md
mise exec -- mix build
mise exec -- ./bin/symphony PLANE_WORKFLOW.local.md --i-understand-that-this-will-be-running-without-the-usual-guardrails
```

`CODE_REPO_URL` is a Git URL accessible by your existing SSH key or credential helper. Customize
`after_create` when different projects use different repositories. Hooks and the workflow own git,
tests, branch creation and PR/MR policy; Plane itself need not host the code. Upstream remote-worker
support still applies, with git/Codex access configured on the chosen worker.

The example uses an English policy template and preserves Chinese task titles/comments as UTF-8.
The current upstream Solid version can corrupt long Chinese literal templates; the shipped template
and a full runner/stdio test guard against that failure without modifying upstream rendering.

The example runs coding tasks. It does not retain the archived project's generic-research prompt.
No old Python SDK, database or MCP subprocess is needed; tools run through the upstream host-side
app-server bridge. Tokens and declared custom token env references are removed from the Codex child.

## Configuration and lifecycle

- `api_url`: CE base URL ending in `/api/v1`, including the correct Django/API port for your instance.
- `api_key`, `workspace_slug`: literal values or `$ENV_VAR` references; prefer env references for secrets.
- `project_ids`: optional project ID list. Omission discovers the workspace's accessible projects.
- `trigger_state`, `working_state`, `review_state`, `error_state`: default `AI Todo`, `AI Doing`,
  `Human Review`, `AI Error`.
  Match `active_states` to your trigger/working names. State IDs are resolved within each project,
  case-insensitively; ambiguous names fail. Explicit setup normalizes legacy `AI Start` and creates
  missing states, including the configured error-state name. It never runs automatically. Run the
  explicit setup command for existing projects when upgrading to the error-state policy.
  The error state must be a nonempty name distinct from trigger/working/review states and must not
  appear in `active_states` or `terminal_states`; invalid configuration is rejected.
- `webhook_port`, `webhook_secret`: optional signed mention listener; both must be configured.

Plane execution uses the upstream `codex.read_timeout_ms`, `codex.turn_timeout_ms`,
`codex.stall_timeout_ms`, and hook timeout controls. There is no additional Plane execution deadline.
The removed `tracker.provider.run_timeout_ms` field is no longer used.

Poll state candidates client-side (Plane lists do not provide the required state filter). Cursor
pagination and bounded 429 retries honor `Retry-After` (up to 60 seconds per delay). Project/work-item
composite IDs prevent identity collisions. Normal runs re-check eligibility and claim `AI Doing`,
include ordered comments in initial context, and delegate coding to the upstream runner.

Write a summary before final status. If the run exits without a summary, the service attempts a
fallback comment. A failed attempt receives an error notice even if it already posted a progress
comment. Only failed state-triggered attempts still in `AI Doing` attempt `AI Error`.
Existing terminal/operator-selected states are preserved, and mentions preserve the real state.
Failed claims receive at most one notice per five minutes. API outages can prevent write-back;
inspect service logs and retry after recovery.
State checks and writes are separate CE requests, so an operator change racing the claim or error
handoff cannot be atomically excluded by this API.

One service instance may own each Plane scope. On startup, orphan `AI Doing` items in that scope are
reset to `AI Todo`. Do not run two independent services over the same projects. Tracker settings and
resolved auth are captured for each attempt; future attempts use reloaded config. Scope changes are
deferred while attempts run. Restart for webhook secret changes. Tokens should be outside the target
repository and never embedded in a git URL or workflow prompt.

Each worker session starts a fresh Codex thread, including retries and new mentions on an existing
issue. The initial prompt includes the work item and current comments; the existing workspace/git
state remains available. Turns within the same worker use the same live thread, following upstream
continuation behavior. There is no cross-attempt thread lookup, naming, or resume policy.

## Failure policy and workspaces

The five default states distinguish scheduling, execution faults and human decisions:

| State | Meaning |
| --- | --- |
| `AI Todo` | Waiting for a new attempt, including a user-requested retry. |
| `AI Doing` | Active coding work; may also be awaiting upstream continuation or retry. |
| `AI Error` | An execution fault stopped the attempt; waiting for the user to fix it and retry. |
| `Human Review` | The coding agent needs result review, requirement clarification or a human decision. |
| `AI Done` | Completed according to the task workflow. |

Coding-worker exceptions, comment-context read failures and interrupted/stalled workers attempt an
error notice. A normal state-triggered item still in `AI Doing` then attempts `AI Error`. Failed
attempts remain failures to the upstream orchestrator; its next reconciliation/retry check sees the
inactive error state and releases the claim without starting another coding worker. After fixing
the cause, change `AI Error` to `AI Todo` to request another attempt in the retained workspace.
The error state remains paused across service restarts; orphan recovery only resets `AI Doing`.

Normal worker completion does not force a status change. Exhausting `agent.max_turns` is a normal
return to the upstream scheduler: it checks active work for continuation after about one second,
with a fresh Codex thread and the same workspace. The default 20 is a configurable per-worker turn
budget, not a total issue limit. Failing tests that the agent continues to fix are not execution
faults. The agent selects `Human Review` for review/decision blockers via the workflow tools.

Operator-selected states and mention-run states are preserved. Live workers retain their run
ownership throughout write-back; upstream claims prevent duplicate dispatch and new mentions are
ignored until it ends. Interrupted-worker finalizers remain undispatchable until cleanup ends.
Claim failures before coding retain upstream handling.
API outages or a missing/ambiguous error state can prevent write-back; the task may remain active
and retry through upstream backoff. Whole-service crashes still use the existing tracker/filesystem
restart recovery. These API writes are best-effort, not a durable delivery queue.

A workspace is one isolated directory per issue, not the Symphony service checkout or your existing
project checkout. The supplied `after_create` hook clones `CODE_REPO_URL` into each new issue directory.
Another issue gets another clone; a retry for the same issue reuses its existing directory and git
state. Branch creation follows the task/project policy. The engine does not automatically create
Git worktrees. A shared repository plus per-issue worktrees can be implemented in repository hooks,
including worktree registration cleanup in `before_remove`.

The default `AI Done`/cancelled terminal states trigger workspace cleanup. `Human Review` and `AI Error` preserve
the workspace. Publish or otherwise preserve delivery before a terminal transition, because local
unpublished changes are removed with the workspace. The live validation workflow deliberately
omits `AI Done` from terminal states to preserve its isolated commit for inspection and follow-up.

## Signed mentions

Create a webhook in Plane's workspace settings with the work-item/comment events enabled, and
copy its secret into your local `PLANE_WEBHOOK_SECRET`. Current CE exposes webhook management under
the session-authenticated app API, not the PAT-authenticated public `/api/v1` API. The archived
Python `register_webhook.py` is historical and cannot provision it on this CE version.

Use a reachable callback such as `http://host.docker.internal:8091/webhook`. Enable
`webhook_port: 8091` and `webhook_secret: $PLANE_WEBHOOK_SECRET` in the local workflow, then restart
Symphony. For Docker Plane, configure `WEBHOOK_ALLOWED_HOSTS=host.docker.internal` on the API/worker;
otherwise Plane's SSRF protection blocks callback delivery. Keep the secret in an ignored local file.

The listener verifies HMAC-SHA256 on raw bytes and ignores the bot's own events. Both `created` and
`create` comment actions work. Mention events are scoped by re-reading the configured project.
Pending mentions join upstream polling/concurrency via a virtual active state, including when the
real item is completed. They use one turn and tools preserve real state. Pending and handled-comment
IDs are in memory; restart loses this dedup history. Like the legacy implementation, mentions arriving
while an item is already pending/running are ignored. State-change webhooks rely on normal polling.
A mention on `AI Error` can answer a question while keeping that state; use `AI Todo` to requeue
the full coding task.

## Tools and attachments

Convenience tools: `get_work_item`, `list_comments`, `add_comment`, `set_state`, `list_states`,
`search_work_items`, `get_me`. IDs default to the current issue. `add_comment` escapes plain text;
raw `plane_request` permits `comment_html` when rich formatting is needed.

`plane_request` accepts GET/POST/PATCH/PUT/DELETE, a relative CE path, optional query object and JSON
body. Absolute URLs and traversal are rejected; redirects do not forward credentials. The raw tool
covers work-item links, attachments and upload credentials when supported by the installed CE API.
There is no automatic binary attachment download/upload pipeline, matching the legacy implementation.

## Validation and upstream maintenance

Tests use a local HTTP Plane fixture plus real OTP ownership/cancellation and simulated Codex stdio.
Live acceptance uses a dedicated Plane workspace/project and isolated neo-css-studio clone; existing tasks are excluded. Run `mise exec -- make all` from `elixir/`.
Plane network/runtime/HTTP modules follow upstream coverage exclusions for the corresponding layers;
provider adapter/tool branches remain under the 100% gate and lifecycle scenarios have targeted tests.
The latest thread/failure-policy changes have updated test expectations but have not been executed;
the recorded earlier acceptance results do not validate those changes. Concentrated validation is
deferred until the user requests it.

Keep `upstream` pointed at `openai/symphony`, fetch it, then merge upstream changes into the Plane
branch. Review the few integration hooks separately from `lib/symphony_elixir/plane/`. The archived
Python source is historical, including its old AGENTS instructions, and is not a second active service.
