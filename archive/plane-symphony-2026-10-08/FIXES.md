# Code Review Fixes - 2026-06-14

## 修复的问题

### P1: 超时后 Codex 继续运行导致状态冲突 ✅

**问题**：使用 daemon thread + join(timeout)，超时时只抛 TimeoutError，后台线程继续运行并可能写 Plane。

**影响**：
- 主线程写"失败"评论 + 改状态为 Human Review
- Codex 晚点完成后写"成功"评论 + 改状态为 AI Done
- 导致状态不一致和混乱的评论序列

**修复**：
- runner 改返回签名：`(result, thread_id, timed_out)` 
- 超时时设置 `timed_out_flag` 并返回 `(None, None, True)`
- worker 检查 flag，超时后完成也不写 result
- orchestrator 检查 `timed_out`，是则当作失败处理

**文件**：
- `src/plane_symphony/runner.py` - 返回签名变更 + flag 机制
- `src/plane_symphony/orchestrator.py` - 处理 timeout 返回值
- `tests/test_orchestrator.py` - 更新 mock 返回 3 个值
- `tests/test_runner.py` - 更新断言

---

### P2: 状态触发认领失败仍执行任务 ✅

**问题**：`_safe_set_state()` 吞掉异常只记日志，状态触发时即使认领失败（改 AI Doing 失败）任务仍会执行。

**影响**：
- Plane API 临时故障时，任务执行但看板无显示（幽灵任务）
- 违背"认领语义" - 应该先成功占位才执行

**修复**：
- `_safe_set_state()` 返回 `bool` 表示成功/失败
- state trigger 时检查认领结果，失败则：
  - 写清晰评论告知用户
  - 记录 `claim_failed` 状态
  - 提前 return，不启动 Codex

**文件**：
- `src/plane_symphony/orchestrator.py` - `_safe_set_state` 返回 bool + 认领检查

---

### P2: Resume 时缺少策略配置 ✅

**问题**：`thread_resume()` 只传 `cwd`，没传 `sandbox`/`approval_mode`/`model`/`base_instructions`，恢复的线程可能配置漂移。

**影响**：
- 旧线程在不同策略下运行
- 可能导致权限、审批行为不一致

**修复**：
- `thread_resume()` 传递完整配置，与 `thread_start()` 一致
- 保证恢复线程策略完全相同

**文件**：
- `src/plane_symphony/runner.py` - resume 传完整参数

---

### P2: 重启后丢失 thread 上下文 ✅

**问题**：`store.get_latest_thread()` 只查内存 `runs` 列表，进程重启后为空，首次 mention 不会续接线程。

**影响**：
- 重启后丧失"连续对话"能力
- 设计文档强调 Codex 自存线程，但未充分利用

**修复**：
- 内存 miss 后调用 `codex.thread_list(search_term=work_item.id)` 
- 从 Codex 恢复线程 ID（通过 `thread.set_name(work_item.id)` 建立的索引）
- 实现真正的跨进程重启线程续接

**文件**：
- `src/plane_symphony/runner.py` - 添加 thread_list fallback
- `tests/test_runner.py` - FakeCodex 添加 thread_list() 方法

---

### P3: Webhook 重复代码 + actor dict 兼容 ✅

**问题**：
- `webhook.py:43-45` 有重复的 `return None`
- `data.get("actor") == bot_user_id` 只处理字符串，Plane payload 若为 `{"id": "..."}` 不会命中防自激

**影响**：
- 代码质量问题
- 潜在防自激漏洞（如果 Plane 改 payload 格式）

**修复**：
- 删除重复 return
- 新增 `_extract_user_id()` 内部函数处理 string 和 dict 两种格式
- 增强防自激兼容性

**文件**：
- `src/plane_symphony/webhook.py` - 清理代码 + 添加 dict 支持

---

### P3: 文档状态名不一致 ✅

**问题**：WORKFLOW.md 配置是 `AI Todo`，但正文里写 `"AI TODO"`，容易误导维护。

**影响**：
- 文档不一致降低可维护性
- 新手可能配置错误

**修复**：
- WORKFLOW.md 统一为 `AI Todo`（与配置一致）

**文件**：
- `WORKFLOW.md` - 统一状态名

---

## 测试结果

```bash
$ python -m pytest tests/ -v
============================== 52 passed in 0.49s ==============================
```

**所有测试通过**，包括：
- 6 个新修复的场景
- 46 个原有测试保持不变

---

## 提交历史

- `654101e` - security+fixes: 7 critical improvements（之前的安全修复）
- `3ae2bf0` - fix: 6 critical production issues from code review（本次修复）

---

## 验证建议

1. **超时防护**：设置 `turn_timeout_ms: 5000`（5秒），触发一个耗时任务，验证超时后日志显示"discarding result"且无重复评论
2. **认领失败**：临时修改 Plane API URL 为无效地址，触发状态，验证写"claim failed"评论且不执行任务
3. **重启恢复**：完成一次任务→重启守护进程→发 @mention，验证日志显示"recovered thread"
4. **Resume配置**：检查日志确认 resume 传递了 sandbox/approval 等参数（或通过 Codex 调试日志）

---

## 影响范围

**高影响修复**（P1-P2）：
- 防止生产状态冲突（超时场景）
- 防止幽灵任务（API 故障场景）
- 增强跨重启连续性（重启场景）

**代码质量提升**（P3）：
- Webhook 健壮性
- 文档一致性

**向后兼容性**：
- ⚠️ `run_task()` 返回签名变更：`(result, thread_id)` → `(result, thread_id, timed_out)`
- 需要更新任何外部调用 `run_task()` 的代码（测试已更新）
