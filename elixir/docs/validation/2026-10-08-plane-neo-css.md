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
