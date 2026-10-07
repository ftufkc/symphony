# Plane CE migration to upstream Symphony

## Confirmed scope

- Fork `openai/symphony` to `ftufkc/symphony`; work in the separate `symphony-plane` checkout.
- Preserve the original Python repository in `archive/`, without credentials or runtime files.
- Target **coding tasks**. Repository preparation, tests, PR/MR creation and review policy remain in a Plane-specific WORKFLOW file.
- Use the user-confirmed **Plane CE `/api/v1/.../work-items/`** API, not Cloud API v2.
- Preserve upstream implementations and behavior for every existing tracker. Change only Plane registration and the necessary optional runner/session extension points.

## Implementation

1. Archive the tracked legacy source and record its exact commit provenance.
2. Add a Plane client and tracker adapter: workspace-wide project discovery, cursor pagination, 429 handling, per-project state-name resolution, normalized identities and HTML descriptions. Use project/work-item composite dispatch IDs to support refreshes without a database.
3. Add host-authenticated Plane tools, including a raw `plane_request` passthrough and convenience tools for tasks, comments and state changes. Keep session credentials bound to the selected configuration and out of Codex child environments.
4. Add a Plane-only OTP runtime and runner wrapper. Preserve claiming, comments in initial context, completion/failure comments, review fallback, claim-failure comment suppression, startup orphan recovery and a total run timeout. Run the unchanged upstream AgentRunner as the coding engine.
5. Add an optional signed webhook endpoint for mentions, self-event filtering and in-memory comment deduplication. Pending mentions are exposed as scheduling candidates with virtual active states; actual Plane state remains unchanged. They share the upstream claim/concurrency machinery with normal runs.
6. Add optional named-thread lookup/resume in AppServer and pass these options through AgentRunner. Existing trackers retain the current fresh-thread behavior. A mention uses one turn; ordinary coding runs keep upstream continuation behavior.
7. Document configuration, startup, migration limits and GitLab/self-built repository hooks. Do not add SQLite, a second scheduler, new task types, or an attachment pipeline that the old implementation did not have.

## Ownership and recovery

- Upstream Orchestrator remains the sole scheduler and owner of concurrency, retries, cancellation and workspace cleanup.
- Plane Runtime owns only mention bookkeeping and Plane-run lifecycle records; it does not start independent coding runs.
- Plane Runner captures configuration for an attempt and delegates actual coding execution to upstream AgentRunner.
- Threads remain persisted by Codex. In-memory lookup is supplemented by named-thread search after restart; resume failure creates a new thread.
- Orphan reset assumes one service per configured Plane scope, matching the legacy deployment boundary.
- Normal finish and failure fallback are best-effort API writes. An API outage cannot provide an absolute comment/state guarantee.

## Validation

- First challenge lifecycle behavior: duplicate mentions, self-authored events, invalid signatures, mention on a completed item, stale state before claim, failed claim, timeout, API failures, restart and configuration reload.
- Test real HTTP requests against a local test Plane server and real OTP runtime behavior; simulate Codex protocol for deterministic lifecycle acceptance.
- Verify thread start versus resume through the actual app-server protocol shape and prove no regression for existing trackers.
- Run targeted tests, then the upstream required `make all` gates (format, specs, strict lint, coverage and Dialyzer).
- Audit archive and final staged changes for credentials. Record changed upstream integration points and remote commit provenance.
- Do not start the production poller or alter real Plane tasks merely to verify installation; live acceptance uses explicitly scoped fixtures when current credentials are available.

## 2026-10-08 后续调整

按用户要求移除 Plane 额外的 30 分钟执行总限制和 `run_timeout_ms` 配置，直接在上游调度器监控的 worker 中执行编码，保留上游超时与卡死检测。此次不调整异常后的人工复核策略、工作区策略或会话续接策略。验证覆盖执行不中途截断、worker 取消与写回兜底，并运行完整上游检查。
