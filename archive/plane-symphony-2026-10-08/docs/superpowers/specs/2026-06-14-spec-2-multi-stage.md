# Spec 2: 多阶段工作流（Multi-Stage Workflow）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐ 中  
**实现难度**: 较难（4-5 天）  

---

## 🎯 目标

支持任务在多个 AI 阶段之间流转，每个阶段等待人工批准后再继续。

---

## 📝 功能描述

### 工作流示例

```
Stage 1: AI 分析阶段
  Trigger: 工单移动到 "AI Todo"
  Action: Codex 分析问题，生成技术方案
  Output: 在评论中发布方案（markdown）
  Next: 移动到 "AI Design Review"（等待人工批准）

Stage 2: AI 实现阶段
  Trigger: 人工在 "AI Design Review" 批准后，移动到 "AI Todo"（第二次）
  Action: Codex 根据方案实现代码
  Output: 提交代码，发布实现总结
  Next: 移动到 "AI Done"（等待人工测试）

Stage 3: AI 修复阶段（可选）
  Trigger: 人工测试发现问题，@ bot 提问题描述
  Action: Codex 修复问题
  Output: 提交修复，更新评论
  Next: 移动到 "AI Done"
```

### 状态机定义

**配置文件** (`WORKFLOW_STAGED.yaml`):
```yaml
stages:
  - name: analyze
    trigger_state: "AI Todo"
    working_state: "AI Analyzing"
    outcome_state: "AI Design Review"
    prompt_template: |
      You are analyzing {{ work_item.name }}.
      
      **Task:** Generate a technical design document with:
      1. Problem analysis
      2. Proposed solution (3 options)
      3. Recommended approach + rationale
      4. Implementation steps
      5. Risks and mitigations
      
      Post the design as a comment, then move to "AI Design Review".
  
  - name: implement
    trigger_state: "AI Todo"
    condition: "previous_stage == 'analyze' AND has_approval_comment"
    working_state: "AI Doing"
    outcome_state: "AI Done"
    prompt_template: |
      You are implementing the approved design for {{ work_item.name }}.
      
      **Approved Design:**
      {{ get_last_design_comment() }}
      
      **Task:** Implement the code, write tests, commit.
  
  - name: fix
    trigger_state: "mention"
    condition: "current_state == 'AI Done'"
    working_state: "AI Fixing"
    outcome_state: "AI Done"
    prompt_template: |
      You are fixing issues in {{ work_item.name }}.
      
      **User Feedback:**
      {{ latest_mention_comment }}
      
      **Task:** Fix the issues, run tests, update comment.
```

### 批准检测逻辑

```python
def has_approval_comment(work_item: WorkItem) -> bool:
    """检测是否有人工批准评论"""
    approval_keywords = ["approved", "lgtm", "looks good", "批准", "同意"]
    
    for comment in reversed(work_item.comments):
        # 排除 bot 自己的评论
        if comment.actor_id == settings.bot_user_id:
            continue
        
        text_lower = comment.text.lower()
        if any(kw in text_lower for kw in approval_keywords):
            return True
        
        # 如果遇到拒绝关键词，立即返回 False
        if any(kw in text_lower for kw in ["reject", "no", "拒绝"]):
            return False
    
    return False
```

---

## 🛠️ 技术方案

### 核心改动

**orchestrator.py**:
```python
class Orchestrator:
    def _dispatch(self, event: TriggerEvent, wi: WorkItem) -> None:
        # 1. 加载工作流配置（可能是多阶段）
        workflow_config = self._load_workflow_config(wi)
        
        # 2. 判断当前应该执行哪个 stage
        stage = self._resolve_stage(wi, workflow_config)
        if not stage:
            log.info("No matching stage for %s", wi.id)
            return
        
        # 3. 检查 stage 的前置条件
        if not self._check_stage_condition(wi, stage):
            log.info("Stage condition not met for %s", wi.id)
            return
        
        # 4. 设置 working_state
        self._safe_set_state(wi.project_id, wi.id, stage.working_state)
        
        # 5. 运行 Codex（使用该 stage 的 prompt）
        prompt = stage.prompt_template.render(work_item=wi)
        result = self._run_task(settings, wi, comments, event.reason, self.store)
        
        # 6. 设置 outcome_state
        self._safe_set_state(wi.project_id, wi.id, stage.outcome_state)
        
        # 7. 记录阶段历史
        self.store.record_stage(wi.id, stage.name)
```

**store.py**:
```python
class Store:
    def __init__(self):
        self._stage_history: dict[str, list[str]] = {}
    
    def record_stage(self, work_item_id: str, stage_name: str):
        if work_item_id not in self._stage_history:
            self._stage_history[work_item_id] = []
        self._stage_history[work_item_id].append(stage_name)
    
    def get_last_stage(self, work_item_id: str) -> str | None:
        history = self._stage_history.get(work_item_id, [])
        return history[-1] if history else None
```

---

## 📊 实现难度

⭐⭐⭐ 较难（4-5 天）
- 状态机逻辑复杂
- 需要追踪阶段历史
- 条件判断需要仔细测试

---

## 🔗 依赖关系

- 建议先实现 **Spec 1**（多工作流），复用模板系统
- 建议搭配 **Spec 4**（持久化），否则重启丢失阶段历史

---

## ⚡ 优先级建议

**中优先级** - 适合复杂任务，但增加系统复杂度
