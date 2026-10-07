# AGENTS.md

本文件是在本仓库工作的 AI agent（及人）的上手说明。**权威设计见 [`docs/superpowers/specs/2026-06-14-plane-symphony-design.md`](docs/superpowers/specs/2026-06-14-plane-symphony-design.md)**，本文件只做概览与约定。

## 这是什么

`plane-symphony`：一个 Python 守护服务，从自托管 **Plane** 看板领取**通用任务**（非编码），用 **OpenAI Codex** 自主完成，并把结果**写回** work item（评论 + 改状态）。

是 [OpenAI symphony](https://github.com/openai/symphony) 的"通用任务"变体——symphony 接 Linear 跑编码任务（git/PR），本项目接 Plane 跑任意通用工作，**不碰 GitHub/代码服务**。沿用其哲学：**引擎通用，任务策略写在 `WORKFLOW.md`（配置+提示词）里，由 Codex 自行决策。**

## 核心机制（一句话版）

- **触发**：work item 状态 → `AI Todo`（**轮询**发现）；或评论 @bot（**webhook** 发现）。两路汇入同一队列。
- **执行**：编排器认领（改 `AI Doing`）→ Codex 线程在 `workspace_write` 沙箱里干活 → 经 **Plane MCP server** 自己写回。
- **结局**：Codex 自选——`AI Done`（有把握完成）/ `Human Review`（需人工）；mention 触发不改状态。**任何情况都必须留一条 symphony 式总结评论**（做了什么 / 结果 / 问题）。
- **无数据库**：Plane 是真相源；Codex 自存线程；运行态在内存。

## 环境（重要，勿假设）

- **Python**：`/opt/homebrew/Caskroom/miniforge/base/bin/python`（3.10）。已装：`openai-codex` 0.1.0b3、`openai-codex-cli-bin`、`codex` CLI（`/opt/homebrew/bin/codex`）、`openai`、`openai-agents`。**待补**：`httpx`、`mcp`。
- **Plane（本地）**：自托管，源在 `/Users/haoxu/Downloads/plane-dev`，`docker compose -f docker-compose-local.yml up -d` 启动（改配置：该目录 `down`→改→`up -d`）。app `http://localhost:3000`、god-mode `http://localhost:3001/god-mode`；**REST 基址 `http://localhost:8000/api/v1`**（⚠️ 不是 :3000——:3000 是前端 SPA 不代理 API；Django API 在 api 容器 :8000，已发布宿主机），认证头 `X-API-Key`。Plane 在容器内，webhook 回调本服务须用 `http://host.docker.internal:<port>/webhook`。
- **Spike 已验证常量（2026-06-14）**：workspace slug=`css`；项目 `neocssstudio` id=`7853b1c5-42b4-4435-a4d1-547e6cd7fc47`（identifier `NEOCSSSTUD`）；bot user id=`55e60d52-af3b-41ce-ad4e-ba90b5c2d4d6`（= test@gmail.com）；PAT 放 `.env` 的 `PLANE_API_TOKEN`（已生成、永不过期，`plane_api_` 前缀）。分页 cursor 信封（`results`/`next_cursor`/`next_page_results`）；限流 60/min（`x-ratelimit-*` 头）→ 处理 429。Codex 自主档=`ApprovalMode.auto_review`（**勿用 `deny_all`，会拒 MCP**）；MCP 经 `config_overrides`（`mcp_servers.<n>.command`/`.args=["..."]`/`.env.K=V`）已验证。
- **⚠️ 状态命名**：项目现有 `AI Todo`(unstarted)/`AI Start`(started)，与设计目标 `AI Todo/AI Doing/Human Review/AI Done` 不符且缺后两个。`setup_states.py` 规范化（重命名/补建）为这 4 个；`resolve_state_id` 大小写不敏感且**多匹配报错**（防 `AI Todo` vs `AI TODO` 撞名）。
- **当前项目示例**：`css` 项目 id `7853b1c5-42b4-4435-a4d1-547e6cd7fc47`（但服务面向**所有项目**）。
- 凭证放 `.env`（gitignore）与 MCP server 进程 env，**绝不写日志/提交**。

## 关键约定与坑（务必遵守）

1. **状态按项目独立**：`AI Todo / AI Doing / Human Review / AI Done` 要在**每个项目**各建一份，id 各不同。代码一律**按名字**在目标项目内解析 state id，**绝不硬编码单个 id**。
2. **防自激**：bot 自己写评论/改状态会触发 webhook。凡 `activity.actor`/`created_by == bot 自身 user id` 一律忽略。bot 自身 id 经 `GET /api/v1/users/me/` 取。
3. **去重**：内存认领集合（key=work_item_id），轮询与 webhook 先到先得。
4. **Webhook 验签**：对**原始请求体 bytes** 算 `HMAC-SHA256(secret_key, body)`，`hmac.compare_digest` 比对 `X-Plane-Signature`；勿重新序列化。**实测坑**：Plane 实际发的 `action` 是过去式 `created`/`updated`（非 create/update），`parse_event` 两种都接受；本地 webhook 投递受 SSRF 校验拦截，需在 Plane 的 `apps/api/.env` 设 `WEBHOOK_ALLOWED_HOSTS=host.docker.internal` 并重建 api/worker 容器（已做）。
5. **能力不封顶**：MCP 除便捷工具外提供 `plane_request(method, path, ...)` 原始透传（对标 symphony `linear_graphql`）。
6. **正文/评论是 HTML**：work item 用 `description_html`，评论用 `comment_html`；mention 是 `<mention-component entity_identifier="<uuid>">`。
7. **列表接口不能服务端按 state 过滤**：轮询要客户端筛。

## 工作流程（本仓库）

- 本仓库用 **superpowers / brainstorming → writing-plans → executing-plans** 流程。设计已定稿于 specs。
- 实现前请先读设计文档；改动遵循"小单元、清晰接口、可独立测试"。
- 还**未进入编码**——当前阶段产物是设计与实现计划。开始编码前先产出实现 plan（`docs/superpowers/plans/`）。

## 建议目录结构（实现时）

```
src/plane_symphony/
  config.py  models.py  plane_client.py
  poller.py  webhook.py  triggers.py
  orchestrator.py  runner.py  prompt.py  store.py
  plane_mcp/ (__main__.py, server.py)
scripts/ (setup_states.py, register_webhook.py)
WORKFLOW.md   # 设置 frontmatter + agent 提示词
```

## 待核实（Spike，编码前先验证）

- Codex SDK：`ApprovalMode` 各值与"全自动"对应；`Sandbox.workspace_write` 网络开关；MCP 经 `CodexConfig.config_overrides`（`mcp_servers.*`）的精确写法。
- Plane（对 localhost 实跑）：`/users/me/`、列项目/状态/work item、建评论、改状态；自托管的 `work-items` vs `issues` 路径、`per_page` 默认、限流。

## 参考代码

`.tmp/symphony/`（已克隆）——尤其 `SPEC.md`、`elixir/lib/symphony_elixir/orchestrator.ex`（编排/对账/认领）、`codex/app_server.ex`（app-server 协议）、`codex/dynamic_tool.ex`（`linear_graphql` 原始透传）、`elixir/WORKFLOW.md`（策略写在提示词的范式）。
