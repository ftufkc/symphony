# 2026-10-08 真实编码验收

- 服务：本 fork 的 Elixir 编排器、Plane CE v1 work-items、Codex CLI 0.160.1 app-server。
- Plane：本机 `http://localhost:3002/api/v1`，独立工作区 `symphony-e2e-20261008`、项目 `SymphonyValidation`、工单 `SYME2E-1`；没有领取原有项目工单。
- 编码目标：`neo-css/neo-css-studio` 的远端隔离克隆；用户原本的 checkout 保持干净。
- 任务：仅新增文本转换函数的 Vitest 回归测试，禁止推送、PR/MR、部署和业务数据库访问。
- 交付：分支 `codex/symphony-plane-validation`，commit `d0096169d6fc83bfa2df71879fdeaa709db65be3`；仅新增 `src/lib/utils.markup.test.ts`，95 行、24 个用例。
- 验证：新增测试及原有 UUID 测试共 27/27 通过；新增文件 ESLint 零错误、零警告。agent 执行后，外层验收又独立重跑确认。
- 写回：agent 自己通过动态工具发布中文总结，并将 `AI Doing` 改为 `AI Done`。
- 续接：在已完成工单中发布真实人工 @bot 评论，将真实评论事件签名投递到 HTTP listener；同一事件重复投递均返回 200，错误签名返回 401。
- 结果：只执行一次 mention，恢复原 Codex thread `01a1176a-f226-7091-a390-8460137da2fb`，重跑测试并写回中文回复；代码、commit 和 Plane 的 `AI Done` 状态保持不变。

验收发现并修复了普通任务缺少触发原因字段、中文默认模板产生无效 UTF-8 两处问题。默认策略模板改用英文，中文工单正文和标题的 UTF-8 渲染已纳入测试；未修改上游模板引擎。

当前 CE 公开 `/api/v1` 没有 webhook 注册端点，管理接口在登录态 app API。首次验收只验证了手动签名投递；后续已补齐自动回调验收：

- 在本机 Plane 的 ignored `.local/.env` 中加入 `WEBHOOK_ALLOWED_HOSTS=host.docker.internal`，保留原配置备份，并重新创建 API/worker 容器使配置生效。
- 仅为独立验证工作区配置 webhook，目标 `http://host.docker.internal:18091/webhook`。
- 通过 Plane API 发布真实 @bot 评论 `4aa946c5-00b7-4d48-9b90-40d0600b7db8`，没有向 Symphony 手动发送 webhook。
- Plane worker 自动投递 `issue_comment/created`，WebhookLog 记录 HTTP 200、retry_count=0。
- Symphony 恢复同一 Codex thread，写回中文复核评论 `bafa2ff0-e6e8-494f-a69e-e1aad6fa2567`；27/27 测试通过，代码和 `AI Done` 状态保持不变。
- bot 回复对应的自动 webhook 同样返回 200，且未触发第二次 agent 运行，防自激生效。
- 验收后停用测试 webhook 并停止测试监听服务；回调白名单配置保留。

验收工作区、补丁和配置保存在 checkout 的 ignored `.tmp/` 中，不把私有目标仓库源码、API token、webhook secret 或运行日志提交到公开 fork。临时 Symphony 监听服务在验收结束后停止；Plane 测试工单和隔离提交保留供复核。

最终 `mise exec -- make all` 全部通过：313 个测试、0 失败、6 个外部环境测试跳过；格式、公开函数 specs、严格 lint、100% 覆盖率门槛（沿用对应网络/运行时层的上游排除约定）及 Dialyzer 均通过。

## 2026-10-08 移除额外执行总时限后的复验

按用户要求删除 Plane 的 30 分钟总时限，编码直接运行在上游调度器监控的 worker 中；保留上游超时、卡死检测，以及当前 Plane 异常转人工复核策略。

- `make all`：314 个测试、0 失败、6 跳过；格式、specs、严格 lint、覆盖率门槛和 Dialyzer 通过。
- 测试验证旧 `run_timeout_ms` 配置不再截断执行，并验证 worker 终止会取消关联工作、保留一次写回兜底。
- 在同一隔离工单发布真实 @bot 评论 `32f32796-1520-4488-aeee-91d8ce03ec9b`，Plane 自动回调 HTTP 200、retry_count=0。
- 复用原 Codex 会话，目标项目 27/27 测试和新增文件 ESLint 通过；回复评论 `99866d1d-6709-418e-a208-166762968518`，代码、提交和 `AI Done` 状态保持不变。
- 测试 webhook 与监听服务在验收后停止。

## 2026-10-08 移除跨次会话续接，等待集中验证

按用户要求移除跨次执行时恢复原 Codex 对话的能力，改为每次 worker 执行新建线程，同一次执行中的多轮继续使用当前线程。上面的会话恢复验收记录对应移除此功能前的版本。

此次已更新代码、测试预期和配置说明，未运行测试或真实验证；后续由用户明确要求后集中执行验证。失败策略保持现状，待后续讨论。

## 当前版本：独立执行异常状态，等待集中验证

用户确认增加 `AI Error`，区别于用于审核、澄清与决策的 `Human Review`。异常执行尝试写故障评论，并仅在普通任务仍为 `AI Doing` 时转异常态；已有进度评论不会抑制故障通知。异常态不自动领取、不清理工作区，修复后改回 `AI Todo` 请求重新执行。

正常达到单次轮数上限不再强制转人工，由上游继续；异常信号仍向上游传递，状态写回不可用时保留退避。mention 和用户已选择的状态保持不变。状态初始化命令已包含默认或配置指定的异常态；本轮未对真实 Plane 执行初始化。

已更新代码、配置说明及回归测试预期，只做格式整理与源码差异检查，未编译、运行测试、启动服务或执行真实验证。以上历史通过结果不覆盖此次失败策略及之前的会话续接删除；集中验证仍等待用户明确指示。
