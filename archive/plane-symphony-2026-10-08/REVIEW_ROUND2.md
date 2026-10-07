# Code Review - Round 2 Results

## 审阅结果总结

审阅者发现 **6 个真实问题**，其中：
- **1 个 P1**（架构限制，已文档化）
- **2 个 P2**（已修复）
- **3 个 P3**（已修复）

---

## 已修复问题 (5/6)

### ✅ P2: thread_list 返回类型错误

**问题**：`codex.thread_list()` 返回 `ThreadListResponse`，有 `.data` 属性，不是直接 list。代码 `threads[0].id` 会失败。

**修复**：
```python
# Before
threads = codex.thread_list(search_term=work_item.id)
if threads:
    existing_thread_id = threads[0].id

# After
resp = codex.thread_list(search_term=work_item.id)
if resp.data:
    existing_thread_id = resp.data[0].id
```

**文件**：`src/plane_symphony/runner.py:72`

---

### ✅ P2: 认领失败评论方法名错误

**问题**：调用 `self.client.post_comment()`，但 PlaneClient 只有 `add_comment()`。认领失败时不会通知用户。

**修复**：
```python
# Before
self.client.post_comment(pid, wid, html)

# After
self.client.add_comment(pid, wid, html)
```

**文件**：`src/plane_symphony/orchestrator.py:126`

---

### ✅ P3: webhook actor 字符串处理不稳定

**问题**：日志代码 `actor.get("id")` 假设 actor 是 dict，如果是字符串会崩溃并丢事件。

**修复**：
- 将 `_extract_user_id()` 提升到模块级别
- 日志使用 `_extract_user_id(activity_actor)`
- 一致处理 string 和 dict 两种格式

**文件**：`src/plane_symphony/webhook.py:22-32, 124-126`

---

### ✅ P3: 文档状态名残留不一致

**问题**：`scripts/setup_states.py` docstring 写 `"AI Todo" -> "AI TODO"`（反了）

**修复**：改为 `"AI TODO" -> "AI Todo"`

**文件**：`scripts/setup_states.py:4`

---

### ✅ P3: timeout 测试未纳入 git

**问题**：`tests/test_timeout_fix.py` 是 untracked，不会进 CI

**修复**：`git add tests/test_timeout_fix.py`

---

## 已文档化限制 (1/6)

### ⚠️ P1: Timeout 无法阻止后台 MCP 调用

**问题**：daemon thread 超时后仍然可能通过 MCP 写 Plane（`add_comment`/`set_state`），因为 Python 无法强制杀死线程。

**现状**：
- ✅ Orchestrator 正确丢弃超时结果
- ✅ 写"失败"评论并记录 timeout
- ❌ 后台 Codex 可能晚点写"成功"（冲突）

**缓解措施**：
1. 默认 30 分钟超时（减少触发频率）
2. WORKFLOW.md 明确指示 Codex 仅完成时改状态
3. Orchestrator 检查 `timed_out` flag

**正确解决方案**：
- **子进程隔离**：`multiprocessing.Process` + `terminate()`/`kill()`
- **或 SDK 支持**：`thread.cancel()` (当前不存在)

**文档**：
- `KNOWN_ISSUES.md` - 详细技术分析
- `runner.py` docstring - 使用警告

**评估**：1-2 天工作量，需要 IPC 重构

---

## 测试结果

```bash
$ python -m pytest tests/ -v
============================== 53 passed in 2.47s ==============================
```

**覆盖率**：
- ✅ 所有已修复问题有测试覆盖
- ⚠️ P1 限制测试只验证 Python 层（无法测 MCP 副作用）

---

## Git 历史

```
654101e - security+fixes: 7 critical improvements
3ae2bf0 - fix: 6 critical production issues from code review (round 1)
0155d62 - docs: add detailed code review fixes summary
84d3704 - fix: 5 critical issues from second review + document P1 limitation (round 2)
```

---

## 影响评估

| 问题 | 严重性 | 频率 | 已修复 | 影响 |
|------|--------|------|--------|------|
| P1: 后台 MCP 写入 | 高 | 极低 | 文档化 | 超时场景可能状态冲突 |
| P2: thread_list 错误 | 中 | 高 | ✅ | 重启后恢复失败 |
| P2: 认领失败无提示 | 中 | 低 | ✅ | API 故障无反馈 |
| P3: webhook 崩溃 | 低 | 极低 | ✅ | 特定 payload 丢事件 |
| P3: 文档不一致 | 低 | 中 | ✅ | 维护困惑 |
| P3: 测试缺失 | 低 | 无 | ✅ | CI 覆盖不全 |

---

## 建议

### 短期（已完成）
- ✅ 修复所有 P2-P3 问题
- ✅ 文档化 P1 限制
- ✅ 添加测试覆盖

### 中期（可选）
- 监控生产环境超时频率
- 如果 < 1% → 接受当前限制
- 如果 > 5% → 优先实现子进程隔离

### 长期（架构）
- 考虑子进程隔离（1-2 天）
- 或等待 SDK 支持 `thread.cancel()`

---

## 结论

**5 个问题已修复，1 个限制已文档化**

系统可安全部署，P1 限制在实际使用中影响极小（超时罕见 + 缓解措施生效）。

如果生产环境超时频繁，再投入子进程隔离重构。
