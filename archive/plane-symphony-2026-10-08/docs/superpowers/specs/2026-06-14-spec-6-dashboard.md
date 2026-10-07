# Spec 6: 管理 Dashboard（Management Dashboard）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐ 中  
**实现难度**: 较难（3-5 天）  

---

## 🎯 目标

提供独立的 Web 界面，实时显示任务列表、统计图表、Codex 对话历史。

---

## 📝 功能描述

### 6.1 任务列表页 (`/tasks`)

**功能**:
- 显示所有 AI 处理的任务（进行中 + 历史）
- 筛选：状态（成功/失败/超时）、项目、日期范围
- 排序：创建时间、耗时
- 点击任务 → 跳转详情页

**UI 设计**:
```
┌─────────────────────────────────────────────────────────────┐
│ plane-symphony Dashboard                        [刷新] [设置] │
├─────────────────────────────────────────────────────────────┤
│ 筛选: [全部状态 ▼] [全部项目 ▼] [最近 7 天 ▼]  [搜索工单ID] │
├────────┬─────────┬──────────┬─────────┬────────┬──────────┤
│ 工单ID  │ 标题     │ 状态      │ 耗时     │ 项目    │ 开始时间  │
├────────┼─────────┼──────────┼─────────┼────────┼──────────┤
│ TASK-1 │ Fix bug │ ✅ 成功   │ 3m 42s  │ Backend │ 15:30    │
│ TASK-2 │ Feature │ ⏳ 进行中 │ 1m 12s  │ Frontend│ 15:28    │
│ TASK-3 │ Refactor│ ❌ 失败   │ 5m 0s   │ Backend │ 15:20    │
└────────┴─────────┴──────────┴─────────┴────────┴──────────┘
```

---

### 6.2 任务详情页 (`/tasks/{work_item_id}`)

**功能**:
- Codex 完整对话历史（工具调用 + 输出）
- 执行的命令及输出
- 修改的文件（git diff）
- 耗时分解（启动 Codex / 执行 / 提交）
- 错误信息（如果失败）
- 操作：重新运行、停止执行、导出对话

**UI 设计**:
```
┌─────────────────────────────────────────────────────────────┐
│ ← 返回列表  TASK-123: Fix authentication bug               │
├─────────────────────────────────────────────────────────────┤
│ 状态: ✅ 成功  │  耗时: 3m 42s  │  项目: Backend           │
│ 开始: 2026-06-14 15:30:10  │  结束: 15:33:52            │
├─────────────────────────────────────────────────────────────┤
│ 🤖 Codex 对话历史                                           │
│ ┌─────────────────────────────────────────────────────────┐│
│ │ [15:30:12] User                                         ││
│ │ Fix the authentication bug in login endpoint           ││
│ │                                                         ││
│ │ [15:30:15] Assistant                                    ││
│ │ Let me analyze the login code...                       ││
│ │ <tool_use name="Read">                                 ││
│ │   <file_path>src/auth.py</file_path>                   ││
│ │ </tool_use>                                            ││
│ │                                                         ││
│ │ [15:30:18] Tool Result                                  ││
│ │ [code content...]                                       ││
│ │                                                         ││
│ │ [15:30:22] Assistant                                    ││
│ │ Found the issue - missing token validation...          ││
│ └─────────────────────────────────────────────────────────┘│
│                                                             │
│ 📁 修改的文件                                                │
│ • src/auth.py (+5 -2)                                       │
│ • tests/test_auth.py (+12 -0)                               │
│                                                             │
│ ⏱️ 耗时分解                                                  │
│ • Codex 启动: 2.1s                                          │
│ • 代码分析: 15s                                             │
│ • 实现修复: 180s                                            │
│ • 运行测试: 25s                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 6.3 统计概览页 (`/stats`)

**功能**:
- 实时指标：队列长度、进行中任务数、成功率
- 历史图表：每日任务数、平均耗时趋势、失败率趋势
- Top 项目（按任务数）
- Top 失败原因

**UI 设计**:
```
┌─────────────────────────────────────────────────────────────┐
│ 📊 实时指标                    [最近 24 小时 ▼]              │
├──────────────┬──────────────┬──────────────┬──────────────┤
│ 队列长度      │ 进行中        │ 成功率        │ 平均耗时      │
│   3 tasks    │   2 tasks    │   87.5%      │   4m 23s     │
└──────────────┴──────────────┴──────────────┴──────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 📈 任务数趋势                                                │
│  20│              ●                                          │
│    │            ●   ●                                        │
│  15│          ●       ●                                      │
│    │        ●           ●                                    │
│  10│      ●               ●                                  │
│    │    ●                   ●                                │
│   5│  ●                       ●                              │
│    └────────────────────────────────────────────────────────│
│     Mon  Tue  Wed  Thu  Fri  Sat  Sun                       │
└─────────────────────────────────────────────────────────────┘

┌────────────────────────┬────────────────────────────────────┐
│ 🏆 Top 项目             │ ⚠️ Top 失败原因                     │
│ • Backend (45 tasks)   │ • Timeout (12 次)                  │
│ • Frontend (32 tasks)  │ • Network error (8 次)             │
│ • API (18 tasks)       │ • Test failure (5 次)              │
└────────────────────────┴────────────────────────────────────┘
```

---

## 🛠️ 技术方案

### 架构

```
┌──────────────────┐
│  Browser         │
└────────┬─────────┘
         │ HTTP
         ↓
┌──────────────────┐     ┌──────────────────┐
│  Dashboard       │────→│  plane-symphony  │
│  FastAPI         │     │  SQLite DB       │
│  (port 8788)     │     │  (.data/)        │
└──────────────────┘     └──────────────────┘
         │
         ↓
┌──────────────────┐
│  React SPA       │
│  (静态文件)       │
└──────────────────┘
```

### 后端：FastAPI

**文件结构**:
```
plane-symphony/
├── dashboard/
│   ├── backend/
│   │   ├── api.py              # FastAPI 主入口
│   │   ├── models.py           # Pydantic 模型
│   │   └── stats.py            # 统计计算
│   ├── frontend/               # React 项目
│   │   ├── src/
│   │   │   ├── App.tsx
│   │   │   ├── pages/
│   │   │   │   ├── TaskList.tsx
│   │   │   │   ├── TaskDetail.tsx
│   │   │   │   └── Stats.tsx
│   │   │   └── components/
│   │   ├── package.json
│   │   └── vite.config.ts
│   └── README.md
```

**backend/api.py**:
```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import sqlite3

app = FastAPI(title="plane-symphony Dashboard")

# 连接数据库
DB_PATH = Path(__file__).parent.parent.parent / ".data" / "plane_symphony.db"

@app.get("/api/tasks")
def list_tasks(
    status: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
):
    """获取任务列表"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    query = "SELECT * FROM run_history WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    
    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    
    return [dict(row) for row in rows]

@app.get("/api/tasks/{work_item_id}")
def get_task_detail(work_item_id: str):
    """获取任务详情"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    cursor = conn.execute(
        "SELECT * FROM run_history WHERE work_item_id = ? ORDER BY started_at DESC LIMIT 1",
        (work_item_id,),
    )
    row = cursor.fetchone()
    
    if not row:
        return {"error": "Task not found"}
    
    return dict(row)

@app.get("/api/stats")
def get_stats():
    """获取统计数据"""
    conn = sqlite3.connect(DB_PATH)
    
    # 队列长度（假设从内存读取，实际需要暴露 orchestrator 状态）
    queue_size = 0  # TODO: 需要实现
    
    # 进行中任务数
    cursor = conn.execute(
        "SELECT COUNT(*) FROM run_history WHERE status = 'in_progress'"
    )
    in_progress = cursor.fetchone()[0]
    
    # 成功率（最近 24 小时）
    cursor = conn.execute(
        """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success
        FROM run_history
        WHERE started_at > datetime('now', '-24 hours')
        """
    )
    row = cursor.fetchone()
    total, success = row[0], row[1]
    success_rate = (success / total * 100) if total > 0 else 0
    
    # 平均耗时
    cursor = conn.execute(
        """
        SELECT AVG(duration_ms) 
        FROM run_history 
        WHERE started_at > datetime('now', '-24 hours')
          AND duration_ms IS NOT NULL
        """
    )
    avg_duration_ms = cursor.fetchone()[0] or 0
    
    return {
        "queue_size": queue_size,
        "in_progress": in_progress,
        "success_rate": round(success_rate, 1),
        "avg_duration_seconds": round(avg_duration_ms / 1000, 1),
    }

# 挂载前端静态文件
app.mount("/", StaticFiles(directory="dashboard/frontend/dist", html=True), name="static")
```

### 前端：React + Vite

**frontend/src/pages/TaskList.tsx**:
```typescript
import { useEffect, useState } from 'react';

interface Task {
  work_item_id: string;
  project_id: string;
  status: string;
  started_at: string;
  duration_ms: number;
}

export function TaskList() {
  const [tasks, setTasks] = useState<Task[]>([]);
  
  useEffect(() => {
    fetch('/api/tasks')
      .then(res => res.json())
      .then(setTasks);
  }, []);
  
  return (
    <div>
      <h1>任务列表</h1>
      <table>
        <thead>
          <tr>
            <th>工单ID</th>
            <th>状态</th>
            <th>耗时</th>
            <th>项目</th>
            <th>开始时间</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(task => (
            <tr key={task.work_item_id}>
              <td><a href={`/tasks/${task.work_item_id}`}>{task.work_item_id}</a></td>
              <td>{task.status}</td>
              <td>{(task.duration_ms / 1000).toFixed(1)}s</td>
              <td>{task.project_id}</td>
              <td>{new Date(task.started_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

## 📊 实现难度

⭐⭐⭐ 较难（3-5 天）
- 后端 API：1 天
- 前端 UI：2-3 天（取决于设计复杂度）
- 集成测试：1 天

---

## 🔗 依赖关系

**强依赖**:
- **Spec 4**（持久化系统）：读取 run_history 表

---

## ⚡ 优先级建议

**中优先级** - 提升运维体验，但不影响核心功能

---

## 🚀 部署方式

```bash
# 开发模式
cd dashboard/backend
uvicorn api:app --reload --port 8788

cd dashboard/frontend
npm run dev

# 生产模式
cd dashboard/frontend
npm run build

cd dashboard/backend
uvicorn api:app --host 0.0.0.0 --port 8788
```

---

## 📦 新增依赖

```toml
# pyproject.toml
[project.optional-dependencies]
dashboard = [
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.23.0",
]
```

```json
// dashboard/frontend/package.json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.14.0",
    "recharts": "^2.7.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.0.0",
    "vite": "^4.4.0",
    "typescript": "^5.0.0"
  }
}
```
