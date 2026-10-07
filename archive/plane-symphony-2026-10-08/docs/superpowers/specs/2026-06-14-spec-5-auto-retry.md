# Spec 5: 自动重试机制（Auto-Retry with Exponential Backoff）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐ 中  
**实现难度**: 中等（2-3 天）  

---

## 🎯 目标

任务失败后自动重试，使用指数退避策略，减少人工干预。

---

## 📝 功能描述

### 现状问题

**现状**: 失败任务直接移到 "Human Review"，不自动重试

**问题**:
- 偶发性错误（网络超时、API 限流）需要人工重新触发
- 增加人工负担
- 降低整体成功率

---

### 改进方案：Exponential Backoff 重试

**策略**:
```
首次失败 → 5 分钟后重试（自动）
二次失败 → 15 分钟后重试（自动）
三次失败 → 移到 "Human Review"（人工介入）
```

**退避公式**:
```python
delay_seconds = base_delay * (backoff_factor ** retry_count)

# 示例：base_delay=300, backoff_factor=3
# retry 1: 300s (5min)
# retry 2: 900s (15min)
# retry 3: 2700s (45min) → 超过 max_retries，不再重试
```

**配置** (WORKFLOW.md frontmatter):
```yaml
retry:
  enabled: true
  max_retries: 2              # 最多重试 2 次
  base_delay_seconds: 300     # 基础延迟 5 分钟
  backoff_factor: 3           # 指数因子
  retriable_errors:           # 可重试的错误类型
    - "timeout"
    - "network_error"
    - "api_rate_limit"
```

---

## 🛠️ 技术方案

### 数据库表（依赖 Spec 4）

```sql
-- 重试状态表
CREATE TABLE retry_state (
    work_item_id TEXT PRIMARY KEY,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    last_error_type TEXT,         -- "timeout" | "network_error" | "other"
    next_retry_at TIMESTAMP,      -- NULL = 不再重试
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_retry_next_retry_at ON retry_state(next_retry_at);
```

### 核心实现

**store.py**:
```python
class PersistentStore:
    def record_failure_for_retry(
        self,
        work_item_id: str,
        error_message: str,
        error_type: str,
        settings: Settings,
    ) -> bool:
        """记录失败，计算下次重试时间
        
        Returns:
            True: 应该重试
            False: 已达最大重试次数
        """
        cursor = self._conn.execute(
            "SELECT retry_count FROM retry_state WHERE work_item_id = ?",
            (work_item_id,),
        )
        row = cursor.fetchone()
        retry_count = row[0] if row else 0
        
        # 检查是否达到最大重试次数
        if retry_count >= settings.max_retries:
            # 清除重试状态（不再重试）
            self._conn.execute(
                "UPDATE retry_state SET next_retry_at = NULL WHERE work_item_id = ?",
                (work_item_id,),
            )
            self._conn.commit()
            return False
        
        # 计算下次重试时间
        import time
        delay = settings.base_delay_seconds * (settings.backoff_factor ** retry_count)
        next_retry_at = time.time() + delay
        
        self._conn.execute(
            """
            INSERT INTO retry_state 
                (work_item_id, retry_count, last_error, last_error_type, next_retry_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                retry_count = retry_count + 1,
                last_error = excluded.last_error,
                last_error_type = excluded.last_error_type,
                next_retry_at = excluded.next_retry_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            (work_item_id, retry_count + 1, error_message, error_type, next_retry_at),
        )
        self._conn.commit()
        return True
    
    def get_pending_retries(self) -> list[str]:
        """获取所有待重试的 work_item_id（已到重试时间）"""
        import time
        cursor = self._conn.execute(
            """
            SELECT work_item_id FROM retry_state
            WHERE next_retry_at IS NOT NULL 
              AND next_retry_at <= ?
            """,
            (time.time(),),
        )
        return [row[0] for row in cursor.fetchall()]
    
    def clear_retry_state(self, work_item_id: str):
        """任务成功后清除重试状态"""
        self._conn.execute(
            "DELETE FROM retry_state WHERE work_item_id = ?",
            (work_item_id,),
        )
        self._conn.commit()
```

**orchestrator.py**:
```python
class Orchestrator:
    def _fail(self, pid: str, wid: str, exc: Exception, reason: str) -> None:
        """失败处理：判断是否重试"""
        # 分类错误类型
        error_type = self._classify_error(exc)
        
        # 检查是否可重试
        is_retriable = (
            self.settings.retry_enabled
            and error_type in self.settings.retriable_errors
        )
        
        if is_retriable:
            should_retry = self.store.record_failure_for_retry(
                wid, str(exc), error_type, self.settings
            )
            
            if should_retry:
                retry_state = self.store.get_retry_state(wid)
                log.info(
                    "Task %s will retry in %ds (attempt %d/%d)",
                    wid,
                    retry_state.next_retry_at - time.time(),
                    retry_state.retry_count,
                    self.settings.max_retries,
                )
                
                # 发评论通知用户
                try:
                    self.client.add_comment(
                        pid, wid,
                        markdown_to_comment_html(
                            f"⚠️ 执行失败（{error_type}），将在 "
                            f"{retry_state.retry_count * 5} 分钟后自动重试\n\n"
                            f"**错误**: {exc}\n\n"
                            f"**重试次数**: {retry_state.retry_count}/{self.settings.max_retries}"
                        ),
                    )
                except Exception:
                    log.exception("failed to post retry comment")
                
                # 保持状态在 "AI Doing"（不移到 review）
                return
        
        # 不可重试或已达最大次数 → 移到 review
        try:
            self.client.add_comment(
                pid, wid,
                markdown_to_comment_html(
                    f"⚠️ 执行失败，需要人工处理\n\n**错误**: {exc}"
                ),
            )
        except Exception:
            log.exception("failed to post failure comment")
        
        if reason == "state":
            self._safe_set_state(pid, wid, self.settings.outcome_review)
    
    def _classify_error(self, exc: Exception) -> str:
        """错误分类"""
        if isinstance(exc, TimeoutError):
            return "timeout"
        if "network" in str(exc).lower() or "connection" in str(exc).lower():
            return "network_error"
        if "rate limit" in str(exc).lower():
            return "api_rate_limit"
        return "other"
```

### 重试调度器

**新增模块** `retry_scheduler.py`:
```python
import logging
import threading
import time

log = logging.getLogger(__name__)

class RetryScheduler:
    """后台线程，定期检查待重试任务"""
    
    def __init__(self, orchestrator: Orchestrator, store: PersistentStore, interval: int = 30):
        self.orchestrator = orchestrator
        self.store = store
        self.interval = interval  # 检查间隔（秒）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("retry scheduler started (interval=%ds)", self.interval)
    
    def stop(self):
        self._stop.set()
    
    def _run(self):
        while not self._stop.is_set():
            try:
                self._check_retries()
            except Exception:
                log.exception("retry scheduler error")
            
            time.sleep(self.interval)
    
    def _check_retries(self):
        pending = self.store.get_pending_retries()
        if not pending:
            return
        
        log.info("found %d task(s) pending retry", len(pending))
        
        for work_item_id in pending:
            try:
                # 重新获取工单信息
                # （需要从 retry_state 表获取 project_id）
                retry_state = self.store.get_retry_state(work_item_id)
                
                # 提交到 orchestrator 队列（复用现有逻辑）
                event = TriggerEvent(
                    work_item_id=work_item_id,
                    project_id=retry_state.project_id,
                    reason="retry",
                )
                self.orchestrator.submit(event)
                log.info("submitted retry for %s", work_item_id)
            except Exception:
                log.exception("failed to submit retry for %s", work_item_id)
```

**app.py 集成**:
```python
def main():
    # ... 现有初始化
    
    retry_scheduler = RetryScheduler(orchestrator, store, interval=30)
    retry_scheduler.start()
    
    # ... 现有启动逻辑
```

---

## 📊 实现难度

⭐⭐ 中等（2-3 天）
- 错误分类需要仔细设计
- 重试调度器需要独立线程
- 需要完善的测试（模拟各种失败场景）

---

## 🔗 依赖关系

**强依赖**:
- **Spec 4**（持久化系统）：存储重试状态

---

## ⚡ 优先级建议

**中优先级** - 提升成功率，减少人工干预

---

## 🎛️ 配置示例

```yaml
# WORKFLOW.md frontmatter
retry:
  enabled: true
  max_retries: 2
  base_delay_seconds: 300
  backoff_factor: 3
  retriable_errors:
    - "timeout"
    - "network_error"
    - "api_rate_limit"
```

---

## 🧪 测试场景

1. **偶发性超时** → 重试后成功
2. **持续失败** → 3 次后移到 Human Review
3. **不可重试错误**（如代码 bug）→ 直接移到 Human Review
4. **重试期间用户手动修复** → 清除重试状态
