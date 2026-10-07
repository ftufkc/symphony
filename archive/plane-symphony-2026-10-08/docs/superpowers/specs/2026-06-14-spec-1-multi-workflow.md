# Spec 1: 多工作流系统（Multi-Workflow System）

**创建时间**: 2026-06-14  
**优先级**: ⭐⭐⭐ 高  
**实现难度**: 简单→中等（1-4 天）  

---

## 🎯 目标

支持基于 Plane label 的工作流路由，让不同类型的任务使用不同的 prompt 策略。

---

## 📝 功能描述

### 1.1 基于 Label 的工作流路由

**现状**: 所有任务使用 `WORKFLOW.md`

**改进**:
- 默认：`WORKFLOW.md`（通用任务）
- 特殊标签匹配：
  - Label `code_analysis` → `WORKFLOW_CODE_ANALYSIS.md`
  - Label `bug_fix` → `WORKFLOW_BUG_FIX.md`
  - Label `feature` → `WORKFLOW_FEATURE.md`
  - Label `refactor` → `WORKFLOW_REFACTOR.md`

**路由规则**:
```python
def resolve_workflow(work_item: WorkItem) -> str:
    """根据 label 选择 workflow 文件"""
    labels = work_item.label_ids  # Plane API 返回的 label 列表
    
    # 优先级：code_analysis > bug_fix > feature > refactor
    priority_map = {
        "code_analysis": "WORKFLOW_CODE_ANALYSIS.md",
        "bug_fix": "WORKFLOW_BUG_FIX.md",
        "feature": "WORKFLOW_FEATURE.md",
        "refactor": "WORKFLOW_REFACTOR.md",
    }
    
    for label_name in priority_map:
        if label_name in [l.name.lower() for l in labels]:
            return priority_map[label_name]
    
    return "WORKFLOW.md"  # 默认
```

---

### 1.2 动态 Prompt 模板（Jinja2）

**问题**: Plane 没有明确的"任务类型"字段

**解决方案**: 从多个维度推断类型
1. **Label**（最可靠）：`bug_fix`, `feature`, `refactor`
2. **标题关键词**：`[BUG]`, `[FEAT]`, `[REFACTOR]`
3. **State 历史**：从 "Backlog" 直接到 "AI Todo" = 新需求

**模板示例** (`WORKFLOW_BUG_FIX.md.j2`):

```jinja2
---
poll_interval_ms: 15000
max_concurrent: 3
turn_timeout_ms: 1800000
outcomes:
  done: AI Done
  review: Human Review
---

You are debugging issue {{ work_item.name }}.

**Context:**
- Reporter: {{ work_item.created_by }}
- Priority: {{ work_item.priority }}
- Labels: {{ work_item.labels | join(", ") }}

**Bug Description:**
{{ work_item.description }}

**Recent Comments:**
{% for comment in recent_comments %}
- {{ comment.actor }}: {{ comment.text }}
{% endfor %}

**Your Task:**
1. Reproduce the bug
2. Identify root cause
3. Fix with minimal changes
4. Add regression test
5. Update issue with fix summary
```

---

### 1.3 附件解析与注入

**用例**: 用户上传 PDF 设计文档、图片截图、Excel 数据

**流程**:
1. Plane webhook 包含 attachments[]
2. orchestrator 下载附件到临时目录
3. 调用解析服务：
   - PDF → markdown (pdfplumber / PyMuPDF)
   - 图片 → OCR 文本 (Tesseract / Claude Vision API)
   - Excel → CSV / markdown table (pandas)
4. 解析结果注入到 prompt：
   - 方式 A：追加到 WORKFLOW.md 底部（作为额外上下文）
   - 方式 B：保存到 workspace/{work_item_id}/attachments/*.md
5. Codex 可以读取这些文件

**配置** (WORKFLOW.md frontmatter):
```yaml
attachment_handling:
  enabled: true
  parsers:
    pdf: "pdfplumber"        # pdfplumber | pymupdf | off
    image: "claude_vision"   # tesseract | claude_vision | off
    excel: "pandas"          # pandas | off
  inject_mode: "workspace"   # workspace | prompt_append
```

---

## 🛠️ 技术方案

### 文件结构
```
plane-symphony/
├── workflows/
│   ├── WORKFLOW.md                    # 默认
│   ├── WORKFLOW_CODE_ANALYSIS.md      # 代码审查
│   ├── WORKFLOW_BUG_FIX.md.j2         # Bug 修复（Jinja2 模板）
│   ├── WORKFLOW_FEATURE.md.j2         # 新功能
│   └── WORKFLOW_REFACTOR.md           # 重构
├── src/plane_symphony/
│   ├── workflow_resolver.py           # 新增：路由逻辑
│   ├── attachment_parser.py           # 新增：附件解析
│   └── prompt.py                      # 修改：支持 Jinja2
```

### 核心代码

**workflow_resolver.py**:
```python
from jinja2 import Environment, FileSystemLoader

class WorkflowResolver:
    def __init__(self, workflow_dir: str = "workflows"):
        self.workflow_dir = Path(workflow_dir)
        self.jinja_env = Environment(loader=FileSystemLoader(workflow_dir))
    
    def resolve(self, work_item: WorkItem) -> tuple[Settings, str]:
        """返回 (settings, prompt_body)"""
        # 1. 根据 label 选择文件
        workflow_path = self._select_workflow(work_item)
        
        # 2. 检查是否为模板
        if workflow_path.endswith(".j2"):
            return self._render_template(workflow_path, work_item)
        else:
            return load_settings(workflow_path)
    
    def _render_template(self, template_path: str, work_item: WorkItem):
        template = self.jinja_env.get_template(template_path)
        rendered = template.render(
            work_item=work_item,
            recent_comments=work_item.comments[-5:],
        )
        return parse_workflow(rendered)
```

**attachment_parser.py**:
```python
import pdfplumber
from PIL import Image
import pytesseract

class AttachmentParser:
    def parse(self, attachment_url: str) -> str:
        """下载并解析附件，返回 markdown 文本"""
        ext = Path(attachment_url).suffix.lower()
        
        if ext == ".pdf":
            return self._parse_pdf(attachment_url)
        elif ext in [".png", ".jpg", ".jpeg"]:
            return self._parse_image(attachment_url)
        elif ext in [".xlsx", ".csv"]:
            return self._parse_excel(attachment_url)
        else:
            return f"Unsupported format: {ext}"
    
    def _parse_pdf(self, url: str) -> str:
        with pdfplumber.open(url) as pdf:
            text = "\n\n".join(page.extract_text() for page in pdf.pages)
        return f"# PDF Content\n\n{text}"
    
    def _parse_image(self, url: str) -> str:
        # 选项 A: Tesseract OCR
        img = Image.open(url)
        text = pytesseract.image_to_string(img, lang="eng+chi_sim")
        return f"# Image OCR\n\n{text}"
        
        # 选项 B: Claude Vision API（更准确但需调用外部 API）
```

---

## 📊 实现难度

- **基础路由**（基于 label）：⭐ 简单（1 天）
- **Jinja2 模板**：⭐⭐ 中等（2 天）
- **附件解析**：⭐⭐⭐ 较难（3-4 天）

---

## 🔗 依赖关系

无外部依赖，可独立实现

---

## ⚡ 优先级建议

**高优先级** - 多工作流是核心扩展能力，直接提升适配性

---

## 📦 新增依赖

```toml
# pyproject.toml
[project.dependencies]
jinja2 = "^3.1.0"
pdfplumber = "^0.10.0"      # 可选
pytesseract = "^0.3.10"     # 可选
pillow = "^10.0.0"          # 可选
pandas = "^2.0.0"           # 可选
```

