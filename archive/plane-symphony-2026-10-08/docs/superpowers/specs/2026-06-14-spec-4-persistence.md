# Spec 4: 持久化系统（Persistent Storage）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐⭐ 高  
**实现难度**: 中等（3 天）  

---

## 🎯 目标

将内存状态持久化到数据库，支持重启恢复 + 多实例部署基础。

---

## 📝 功能描述

### 现状问题

```python
# store.py
class Store:
    def __init__(self):
        self._threads: dict[str, str] = {}           # 内存，重启丢失
        self._mentions: set[str] = set()             # 内存，重启丢失
        self._claim_failures: dict[str, float] = {}  # 内存，重启丢失
```

**问题**:
- 服务重启后，所有 `thread_id` 映射丢失，无法 resume
- mention 去重失效，可能重复处理评论
- claim failure 去重失效，可能刷屏评论

---

### 改进方案：SQLite 持久化

**数据库表结构**:

```sql
-- 工单 → Codex thread 映射
CREATE TABLE threads (
    work_item_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 已处理的 mention 评论
CREATE TABLE mentions (
    comment_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    handled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- claim 失败记录（用于去重）
CREATE TABLE claim_failures (
    work_item_id TEXT PRIMARY KEY,
    failed_at TIMESTAMP NOT NULL
);

-- 运行历史（可选，用于统计和审计）
CREATE TABLE run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    reason TEXT NOT NULL,           -- "state" | "mention"
    status TEXT NOT NULL,            -- "ok" | "failed" | "timeout" | "claim_failed"
    error_message TEXT,
    thread_id TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER
);

-- 创建索引
CREATE INDEX idx_run_history_work_item ON run_history(work_item_id);
CREATE INDEX idx_run_history_status ON run_history(status);
CREATE INDEX idx_run_history_started_at ON run_history(started_at);
```

---

## 🛠️ 技术方案

### PersistentStore 实现

```python
# store.py
import sqlite3
import threading
from pathlib import Path

class PersistentStore:
    def __init__(self, db_path: str = "plane_symphony.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._local = threading.local()  # 线程本地连接
        self._init_schema()
    
    @property
    def _conn(self) -> sqlite3.Connection:
        """每个线程独立连接"""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                self.db_path, 
                check_same_thread=False
            )
        return self._local.conn
    
    def _init_schema(self):
        """初始化表结构"""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS threads (
                work_item_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS mentions (
                comment_id TEXT PRIMARY KEY,
                work_item_id TEXT NOT NULL,
                handled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS claim_failures (
                work_item_id TEXT PRIMARY KEY,
                failed_at TIMESTAMP NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_item_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                thread_id TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                duration_ms INTEGER
            );
            
            CREATE INDEX IF NOT EXISTS idx_run_history_work_item 
                ON run_history(work_item_id);
            CREATE INDEX IF NOT EXISTS idx_run_history_status 
                ON run_history(status);
            CREATE INDEX IF NOT EXISTS idx_run_history_started_at 
                ON run_history(started_at);
        """)
        conn.close()
    
    # --- Thread 管理 ---
    def record_thread(self, work_item_id: str, thread_id: str, project_id: str):
        self._conn.execute(
            """
            INSERT INTO threads (work_item_id, thread_id, project_id)
            VALUES (?, ?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (work_item_id, thread_id, project_id),
        )
        self._conn.commit()
    
    def get_latest_thread(self, work_item_id: str) -> str | None:
        cursor = self._conn.execute(
            "SELECT thread_id FROM threads WHERE work_item_id = ?",
            (work_item_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    
    # --- Mention 去重 ---
    def mark_mention_handled(self, comment_id: str, work_item_id: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO mentions (comment_id, work_item_id) VALUES (?, ?)",
            (comment_id, work_item_id),
        )
        self._conn.commit()
    
    def is_mention_handled(self, comment_id: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM mentions WHERE comment_id = ?", 
            (comment_id,)
        )
        return cursor.fetchone() is not None
    
    # --- Claim failure 去重 ---
    def record_claim_failure(self, work_item_id: str):
        import time
        self._conn.execute(
            """
            INSERT INTO claim_failures (work_item_id, failed_at)
            VALUES (?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                failed_at = excluded.failed_at
            """,
            (work_item_id, time.time()),
        )
        self._conn.commit()
    
    def should_retry_claim(self, work_item_id: str, backoff_seconds: float = 300) -> bool:
        import time
        cursor = self._conn.execute(
            "SELECT failed_at FROM claim_failures WHERE work_item_id = ?",
            (work_item_id,),
        )
        row = cursor.fetchone()
        if not row:
            return True
        
        last_failure = row[0]
        return (time.time() - last_failure) > backoff_seconds
    
    # --- 运行历史 ---
    def record_run(self, record: RunRecord):
        self._conn.execute(
            """
            INSERT INTO run_history 
                (work_item_id, project_id, reason, status, error_message, thread_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.work_item_id,
                record.project_id,
                record.reason,
                record.status,
                record.error_message,
                record.thread_id,
            ),
        )
        self._conn.commit()
```

---

## 📊 实现难度

⭐⭐ 中等（3 天）
- SQL 表设计简单
- 需要处理多线程安全（每线程独立连接）
- 需要完善的测试覆盖

---

## 🔗 依赖关系

**被依赖**：
- Spec 2（多阶段工作流）：需要持久化阶段历史
- Spec 3（人机交互）：需要持久化等待状态
- Spec 5（自动重试）：需要持久化重试计数

**建议优先实现此方案**

---

## ⚡ 优先级建议

**高优先级** - 解决重启丢失数据问题，是其他功能的基础

---

## 🔄 迁移方案

从内存 Store 迁移到 PersistentStore：

```python
# config.py 新增配置
@dataclass
class Settings:
    # ... 现有字段
    db_path: str = ".data/plane_symphony.db"  # 新增

# app.py
def main():
    settings = load_settings("WORKFLOW.md")
    client = PlaneClient(...)
    
    # 旧：store = Store()
    # 新：
    store = PersistentStore(settings.db_path)
    
    orchestrator = Orchestrator(settings, client, store)
    # ...
```

**兼容性**：PersistentStore 实现与 Store 相同的接口，无需修改其他代码
