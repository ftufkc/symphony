# Spec 3: 人机交互增强（Human-in-the-Loop）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐⭐ 高  
**实现难度**: 中等→较难（1-3 天/功能）  

---

## 🎯 目标

让 AI 执行过程更透明可控：实时进度反馈 + 中断机制 + 问答模式。

---

## 📝 功能描述

### 3.1 实时进度反馈

**现状**: Codex 运行时无任何反馈，用户只能等待

**改进方案**:

#### 方式 A: 固定间隔更新（1 分钟一次）
- orchestrator 在后台线程每 60 秒读取 Codex 输出
- 解析最新的工具调用或输出文本
- 更新 Plane 评论："🤖 正在执行... 最新动作：运行测试 `pytest tests/`"

#### 方式 B: Codex 主动上报（推荐）
- 通过 Plane MCP 暴露 `report_progress(message: str)` 工具
- Codex 在关键步骤调用：
  ```python
  # Codex 内部
  report_progress("✅ Step 1/5: 测试已通过")
  report_progress("🔄 Step 2/5: 正在重构 auth.py...")
  ```
- MCP 收到调用后，更新 Plane 评论（追加模式）

**评论格式示例**:
```markdown
🤖 **AI 执行进度** (更新时间: 2026-06-14 15:32:10)

- [x] Step 1: 分析代码结构
- [x] Step 2: 编写测试用例
- [ ] Step 3: 实现功能 (进行中...)
- [ ] Step 4: 运行测试
- [ ] Step 5: 提交代码

**最新日志**:
```
$ pytest tests/test_auth.py -v
===== 3 passed in 1.2s =====
```
```

---

### 3.2 中断机制（/stop 命令）

**用例**: 用户发现 AI 方向错了，需要立即停止

**流程**:
1. 用户在评论中输入：`/stop`
2. Webhook 收到 comment 事件
3. orchestrator 检测到 /stop 命令
4. 向 runner 发送终止信号
5. runner 杀死 Codex subprocess
6. orchestrator 在评论中回复："⛔ 已停止执行（用户中断）"
7. 工单状态保持在 "AI Doing"，等待用户下一步指令

**技术实现**:

**orchestrator.py**:
```python
class Orchestrator:
    def __init__(self, ...):
        self._running_tasks: dict[str, threading.Event] = {}
    
    def _dispatch(self, event: TriggerEvent, wi: WorkItem):
        # 创建 stop_event
        stop_event = threading.Event()
        self._running_tasks[wi.id] = stop_event
        
        try:
            result = self._run_task(
                settings, wi, comments, 
                event.reason, self.store, stop_event
            )
        finally:
            del self._running_tasks[wi.id]
    
    def stop_task(self, work_item_id: str):
        """外部调用（由 webhook handler 触发）"""
        stop_event = self._running_tasks.get(work_item_id)
        if stop_event:
            stop_event.set()
            log.info("Stop signal sent to %s", work_item_id)
```

**runner.py**:
```python
def run_task(..., stop_event: threading.Event | None = None):
    process = multiprocessing.Process(...)
    process.start()
    
    timeout_s = settings.turn_timeout_ms / 1000.0
    start_time = time.time()
    
    # 轮询检查 stop_event
    while True:
        if stop_event and stop_event.is_set():
            log.warning("User requested stop for %s", work_item_id)
            _kill_process_group(process)
            raise UserStoppedError("User interrupted execution")
        
        elapsed = time.time() - start_time
        if elapsed > timeout_s:
            break  # 超时
        
        process.join(timeout=0.5)  # 每 0.5 秒检查一次
        if not process.is_alive():
            break  # 正常完成
```

**webhook.py**:
```python
def parse_event(payload: dict) -> TriggerEvent | None:
    if event == "issue_comment" and action in ("create", "created"):
        text = data.get("comment_html") or ""
        
        # 检测 /stop 命令
        if "/stop" in text.lower():
            wid = data.get("issue")
            pid = data.get("project")
            return TriggerEvent(
                work_item_id=wid, project_id=pid, 
                reason="stop_command",
                comment_id=data.get("id"), 
                actor_id=actor_id,
            )
```

---

### 3.3 人机对话（问答模式）

**用例**: Codex 遇到歧义，需要人工决策

**简化方案**（无需特殊代码）:

Codex 直接在输出中写：
```markdown
我遇到了一个问题需要你决策：

**问题**: 应该使用 SQLite 还是 PostgreSQL？
**选项 A**: SQLite（轻量，单文件）
**选项 B**: PostgreSQL（功能强大，支持并发）

请在评论中回复 "A" 或 "B" 并 @ 我。
```

**流程**:
1. orchestrator 无需特殊处理
2. 用户回复 "@ bot 选 B" 后，webhook 触发 mention 事件
3. orchestrator 用同一个 thread_id resume Codex
4. Codex 看到用户回复，继续执行

**✅ 你的理解是正确的**：
> 当前是复用 thread 的所以也没必要额外支持所谓的 pause/resume

因为：
- Codex thread 的对话历史已持久化在 Codex 服务端
- plane-symphony 通过 `thread_id` resume 时，Codex 会加载完整历史
- 用户的回复作为新的 comment，会被注入到 prompt 中
- Codex 能够看到之前的提问 + 用户的回答，继续执行

---

## 🛠️ 技术方案

### 新增模块

```
src/plane_symphony/
├── progress_reporter.py    # 进度上报（Plane MCP 工具）
├── command_parser.py       # 解析 /stop 等命令
└── user_interaction.py     # 等待用户输入的状态管理
```

### Plane MCP 扩展（方式 B 进度反馈）

**plane_mcp.py**:
```python
# 新增 MCP 工具
@server.call_tool()
async def report_progress(arguments: dict) -> list[types.TextContent]:
    """向用户报告执行进度"""
    message = arguments.get("message", "")
    
    # 从环境变量获取当前工单信息
    work_item_id = os.getenv("CODEX_WORK_ITEM_ID")
    project_id = os.getenv("CODEX_PROJECT_ID")
    progress_comment_id = os.getenv("CODEX_PROGRESS_COMMENT_ID")
    
    if not progress_comment_id:
        # 首次调用，创建新评论
        comment = client.add_comment(
            project_id, work_item_id,
            f"🤖 **AI 执行进度**\n\n{message}"
        )
        os.environ["CODEX_PROGRESS_COMMENT_ID"] = comment.id
    else:
        # 追加到现有评论
        comment = client.get_comment(project_id, work_item_id, progress_comment_id)
        updated_text = f"{comment.text}\n{message}"
        client.update_comment(project_id, work_item_id, progress_comment_id, updated_text)
    
    return [types.TextContent(type="text", text="Progress reported")]
```

---

## 📊 实现难度

- **实时进度（方式 A）**: ⭐⭐ 中等（2 天）
- **实时进度（方式 B）**: ⭐⭐⭐ 较难（3 天，需修改 MCP）
- **中断机制**: ⭐⭐⭐ 较难（3 天，轮询 + 进程控制）
- **人机对话**: ⭐ 简单（1 天，复用现有机制）

---

## 🔗 依赖关系

- 无外部依赖
- 建议搭配 **Spec 4**（持久化），记录"等待用户输入"状态

---

## ⚡ 优先级建议

**高优先级** - 大幅提升用户体验和控制感
