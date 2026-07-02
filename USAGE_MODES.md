## 快速决策树

不确定用哪个？回答一个问题：

```
你只是想开发数据？ ──→ /data-developer（最快，5-10 分钟）
        │
        └── 需要自动化/CI/CD？ ──→ CLI aqueduct dev
                │
                ── 有现成 SQL 要审查？ ─→ aqueduct review
```

更详细的场景 → 往下读。

---

## 开始之前：环境准备

在跑任何命令之前，先确认以下几点：

### 1. LLM 协议要求

**Aqueduct 使用 Anthropic Messages API**（`ClaudeLLM`），不是 OpenAI Chat Completions。

```
Aqueduct (ClaudeLLM) → Anthropic Messages API  ✅
你的公司网关           → OpenAI Chat Completions   不兼容
```

如果你公司用的是 OpenAI 兼容网关（如 vLLM、Ollama、公司统一网关），Aqueduct 的 LLM 层**不能直接对接**。
有三种解决方案：

| 方案 | 做法 | 适用场景 |
|------|------|----------|
| A | 在本地直连 Anthropic API（需要可访问 api.anthropic.com） | 有外网权限 |
| B | 用支持 Anthropic 协议的中转服务 | 公司内网代理 |
| C | 自己扩展 `llm/` 层，加一个 OpenAI 适配器（参考 `ClaudeLLM` 的实现） | 长期方案，需开发 |

### 2. API Key 配置

```bash
# .env 文件（放在项目根目录）
ANTHROPIC_API_KEY=sk-ant-xxxx
# 如果有公司代理
ANTHROPIC_BASE_URL=https://your-proxy.example.com/v1
```

### 3. 虚拟环境

```bash
# 首次或依赖变更后
source .venv/Scripts/activate   # Windows
source .venv/bin/activate       # Linux/macOS

pip install -e ".[dev]"
```

---

## 使用场景详解

### 场景 1：日常数据开发需求（最常见）

**典型情况**：产品经理给了需求文档，你需要开发 ETL SQL 并交付。

**推荐方式**：IDE 插件 + `/data-developer`（VS Code、PyCharm、Cursor 等均可）

**为什么**：
- 需求文档通常不够详细，需要边讨论边完善
- AI 助手能看到完整上下文（需求 + 表结构 + 业务知识库），推理更透彻
- 5-10 分钟出结果，可以快速迭代

**操作**：
```
1. IDE 打开 AI 助手面板（Claude Code / Cursor / Codex 等）
2. 输入：/data-developer E:\需求管理\xxx\Design.md
3. AI 可能会问几个问题（目标表名？分区策略？过滤条件？）
4. 回答后 AI 直接生成所有交付物
5. 检查 output/ 目录下的文件，满意就交付
```

**真实案例**：
- 电商每日订单统计（多表关联 + GMV 计算）

---

### 场景 2：需求很清楚，想全自动出产出物

**典型情况**：需求文档已经非常详细（源表、目标表、字段映射、业务规则全写清楚了），不需要讨论。

**推荐方式**：CLI `aqueduct dev`

**为什么**：
- 全自动，不需要人工干预
- 产出物格式标准化（固定文件名、固定结构）
- 可以后台运行，跑完看结果

**操作**：
```bash
aqueduct dev requirement.md
# Phase 1 后暂停确认（Y/n/q），确认需求理解是否正确
# 后续 Phase 自动执行
```

**适合**：
- 批量处理多个类似需求（写个 shell 脚本循环执行）
- 需求文档是模板化的，结构固定

**不适合**：
- 需求还比较模糊（CLI 不会主动问你问题）
- 需要快速迭代（14-24 分钟太慢）

---

### 场景 3：已有 SQL，想自动化审查 + DQC

**典型情况**：你或同事已经写好了 SQL（可能在 Claude.ai、Cursor 或其他工具中），想让 Aqueduct 自动校验、审查、生成 DQC。

**推荐方式**：CLI `aqueduct dev --sql-file`

**为什么**：
- **生成者与审查者分离**：同一个 LLM 既生成又审查会有确认偏差，分离后审查更客观
- ValidatorTool 独立检查 SQL 规范（命名、分区、注释等）
- EstimatorTool 预估计算资源
- Phase 4.5 独立上下文审查（能看到你看不到的问题）

**操作**：
```bash
# 假设你在 Claude.ai 中生成了 SQL，保存为 my_etl.sql
aqueduct dev requirement.md --sql-file my_etl.sql
```

**流程**：
1. Phase 1-3：CLI 生成需求理解、设计方案、DDL
2. Phase 4：直接读取你的 SQL，跳过 LLM 生成
3. Phase 4 工具链：ValidatorTool 检查规范、LineageTool 生成血缘、EstimatorTool 预估成本
4. Phase 4.5：独立审查你的 SQL（可能发现 Critical 问题 → 自动修复 → 重新审查）
5. Phase 5-6：生成 DQC 和交付文档

**适合**：
- 团队有 SQL 规范，想人工把关后再自动化审查
- 复杂需求，需要在外部工具中反复打磨 SQL
- 想用特定 LLM（如 GPT-4）生成 SQL

---

### 场景 4：IDE 插件 + 一键启动管道

**典型情况**：你在 IDE 中工作，想用 AI 助手启动 CLI 管道（不是直接生成）。

**推荐方式**：IDE 插件 + `/aqueduct-dev`

**为什么**：
- 自动检查/激活虚拟环境
- 自动执行 `aqueduct dev` 命令
- Phase 1 后展示摘要，询问是否继续

**注意**：底层还是 CLI 管道，速度和 CLI 一样慢（14-24 分钟）。如果你想要快速开发，用 `/data-developer`。

**操作**：
```
1. IDE 打开 AI 助手面板
2. 输入：/aqueduct-dev examples/requirement.md
3. AI 自动启动管道
4. Phase 1 后问你确认（Y/n/q）
5. 确认后自动跑完剩余阶段
```

**适合**：
- 想要 CLI 的标准化产出，但不想手动激活虚拟环境
- 团队统一使用 CLI 管道流程

---

### 场景 5：集成到 CI/CD 流水线

**典型情况**：代码提交后自动触发数据开发流程，生成产出物并归档。

**推荐方式**：CLI 或 Python API

**为什么**：
- 无交互模式（`interactive=False`）
- 可编程控制每个阶段
- 可集成到 Jenkins、GitLab CI 等

**Python API 操作**：
```python
from aqueduct import Aqueduct

agent = Aqueduct()

def on_progress(phase_name, idx, total, state):
    print(f"[CI] {phase_name} 完成 ({idx}/{total})")

result = agent.dev(
    requirement="requirement.md",
    output_dir="/archive/output",
    interactive=False,  # CLI 不支持 --no-interactive，只能用 Python API
    on_progress=on_progress
)

if not result.success:
    print(f"[CI] 失败: {result.errors}")
    exit(1)

# 归档产出物
import shutil
shutil.copytree("output/", f"/archive/{job_id}/")
```

**CLI 无交互限制**：当前 CLI 的 `aqueduct dev` 硬编码 `interactive=True`，没有 `--no-interactive` 参数。
CI/CD 场景请使用上面的 Python API。

---

### 场景 6：SQL 审查（对比线上版本 vs 变更版本）

**典型情况**：线上 SQL 要变更，需要审查变更是否安全、是否符合规范。

**推荐方式**：CLI `aqueduct review` 或 PyCharm 插件 `/aqueduct-review`

**操作**：
```bash
# CLI 模式
aqueduct review changed.sql

# PyCharm 插件
/aqueduct-review changed.sql
```

**产出**：
- 对比线上版本 vs 变更版本的 diff
- 检查是否违反 SQL 规范
- 评估下游影响
- 给出审查意见（通过/需修改/拒绝）

---

### 场景 7：需求变更管理

**典型情况**：需求已交付，业务方提出新增字段、修改逻辑、删除字段等变更。

**推荐方式**：CLI `aqueduct change` 或 PyCharm 插件 `/change-management`

**操作**：
```bash
# CLI 模式
aqueduct change original_requirement.md new_requirement.md

# PyCharm 插件
/change-management
# Claude 会问你变更内容，然后自动生成变更 SQL + 影响分析
```

**产出**：
- 变更识别报告（哪些表受影响、哪些字段变化）
- 变更 SQL（ALTER TABLE / INSERT OVERWRITE 等）
- 回滚方案
- 影响评估（下游表、任务、调度）

---

### 场景 8：批量处理多个需求

**典型情况**：有 10+ 个类似需求需要处理（如多个业务线的日报表）。

**推荐方式**：CLI + Shell 脚本

**操作**：
```bash
#!/bin/bash
for req in requirements/*.md; do
    echo "Processing $req..."
    aqueduct dev "$req" --output "output/$(basename $req .md)" --no-interactive
done
```

**为什么不用插件模式**：
- 插件模式需要人工交互（回答问题）
- 批量处理时无法逐个交互
- CLI 可以后台运行，跑完看结果

---

### 场景 9：第一次开发某个业务域

**典型情况**：你第一次接触某个业务域（如供应链库存周转、电商订单），不了解表结构和业务规则。

**推荐方式**：IDE 插件 + `/data-developer`

**为什么**：
- AI 助手可以结合 `knowledge/domains/` 中的业务知识库给出建议
- 可以问 AI 问题（"这个业务域的分区策略是什么？"、"哪些表是核心表？"）
- AI 会主动问你问题帮你理清需求

**操作**：
```
1. IDE 打开 AI 助手面板
2. 输入：/data-developer requirement.md
3. AI 读需求后可能会问：
   - "这个业务域的核心表是哪些？"
   - "分区策略是按天还是按小时？"
   - "有没有已知的数据质量问题？"
4. 回答后 AI 结合业务知识库生成方案
```

---

### 场景 10：需要精确控制每个阶段的模型选择

**典型情况**：你想用 Haiku 做需求理解、Sonnet 做设计方案、Opus 做 SQL 生成，精确控制每个阶段的模型。

**推荐方式**：Python API

**为什么**：
- 可以单独调用每个阶段的节点函数
- 可以自定义每个阶段的 prompt
- 可以自定义确认逻辑

**操作**：
```python
from aqueduct.engine.nodes import node_requirement, node_design, node_sql
from aqueduct.engine.state import WorkflowState

state: WorkflowState = {
    "requirement": "需求文档内容...",
    "mode": "dev",
    "errors": [],
    "artifacts": [],
}

# Phase 1: 用 Haiku
state = node_requirement(state)
print(state["requirement_summary"])

# 手动确认
if input("确认？(y/n)") != "y":
    exit()

# Phase 2: 用 Sonnet
state = node_design(state)
print(state["design_scheme"])

# Phase 4: 用 Opus
state = node_sql(state)
print(state["sql_content"])
```

---

## 场景速查表

| 场景 | 推荐方式 | 耗时 | 交互程度 |
|------|---------|------|---------|
| 日常开发需求 | `/data-developer` | 5-10 min | 中（问答） |
| 需求很清楚，全自动 | CLI `dev` | 14-24 min | 低（Phase 1 确认） |
| 已有 SQL，自动审查 | CLI `dev --sql-file` | 6-8 min | 无 |
| IDE + 一键管道 | `/aqueduct-dev` | 14-24 min | 低 |
| CI/CD 集成 | CLI / Python API | 14-24 min | 无 |
| SQL 审查 | `review` / `/aqueduct-review` | 2-3 min | 无 |
| 需求变更 | `change` / `/change-management` | 5-10 min | 中 |
| 批量处理 | CLI + Shell 脚本 | N × 14-24 min | 无 |
| 第一次接触业务域 | `/data-developer` | 10-15 min | 高（多轮问答） |
| 精确控制模型 | Python API | 自定义 | 自定义 |
| 使用自定义领域技能 | CLI `--skill-dir` / Python `load_plugins()` | 同底层方式 | 取决于 skill |

---

### 场景 11：加载外部自定义 Skill / Tool

**典型情况**：团队有领域特定技能（场景监控 SQL 生成、维度一致性校验、编码规范检查等），不想改 aqueduct 源码，想从外部目录动态加载。

**推荐方式**：CLI `--skill-dir` / `--tool-dir`

**为什么**：
- **不改源码**：外部 skill 放在独立目录，升级 aqueduct 不冲突
- **可复用**：团队共享一套自定义 skills，`pip install aqueduct` 即可使用
- **平等对待**：加载后与内置 skill 在 registry 中一样，workflow 节点不区分来源

**前置条件**：
```bash
pip install aqueduct  # 获取 BaseSkill / BaseTool 基类
```

**Skill 目录结构**：
```
my-skills/
├── scene_monitor.py      ← @register_skill class SceneMonitorSkill(BaseSkill)
├── dimension_check.py    ← @register_skill class DimensionCheckSkill(BaseSkill)
── hive_standard.py      ← @register_skill class HiveStandardSkill(BaseSkill)
```

**最小 Skill 示例**：
```python
# my-skills/hive_standard.py
from aqueduct.skills.base import BaseSkill
from aqueduct.skills.registry import register_skill

@register_skill
class HiveStandardSkill(BaseSkill):
    name = "hive-standard"
    description = "Hive SQL 编码规范检查"

    def execute(self, **kwargs):
        sql = kwargs.get("sql_content", "")
        issues = []
        if "SELECT *" in sql:
            issues.append("禁止使用 SELECT *")
        return {"success": len(issues) == 0, "issues": issues}
```

**操作**：
```bash
# CLI 加载外部 skill 目录
aqueduct dev requirement.md --skill-dir /path/to/my-skills/

# 同时加载 skill 和 tool
aqueduct dev requirement.md --skill-dir /path/to/skills/ --tool-dir /path/to/tools/

# SDK 加载
from aqueduct.skills.registry import load_plugins
load_plugins("/path/to/my-skills/")
```

**加载后**：外部 skill 出现在 registry 中，可被 workflow 节点调用，也可通过 `get_skill("hive-standard")` 直接调用。

**注意**：
- 外部 skill 的 `name` 不能与内置 skill 重复
- 外部 skill 依赖 aqueduct 的 `BaseSkill` / `BaseTool` 基类
- prompt 模板文件（`.tpl.md`）可放在 skill 同目录下

---

## 常见问题 / 踩坑指南

### Q1：协议不兼容 — 公司网关是 OpenAI 格式，Aqueduct 跑不通

**现象**：
```
Aqueduct (ClaudeLLM) → Anthropic Messages API  X
公司网关               → OpenAI Chat Completions   X
```

**原因**：Aqueduct 的 LLM 层只内置了 Anthropic Messages API 适配器，与 OpenAI 协议格式不同。

**解决**：见「开始之前 → 1. LLM 协议要求」。推荐先试方案 A（直连 Anthropic），长期用方案 C（扩展 OpenAI 适配器）。

---

### Q2：API Key 无效或 401 错误

**现象**：
```
AuthenticationError: Invalid API Key
```

**检查**：
1. `.env` 文件在项目根目录（和 `src/` 同级），不是别的地方
2. `ANTHROPIC_API_KEY` 拼写正确，没有多余空格
3. 如果是公司代理，`ANTHROPIC_BASE_URL` 必须指向正确地址

---

### Q3：CLI 超时或太慢（14-24 分钟）

**现象**：跑一次 `aqueduct dev` 要很久，经常超时。

**原因**：7 阶段管道，每阶段都调 LLM，累计耗时。

**解决**：
- 日常开发直接用 `/data-developer` 插件模式（5-10 分钟）
- 如果网络慢，检查 `ANTHROPIC_BASE_URL` 是否走了绕远路的代理
- CLI 适合后台跑，别在前台干等

---

### Q4：虚拟环境未激活

**现象**：
```
Command 'aqueduct' not found
```

**解决**：
```bash
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
```

---

### Q5：Phase 1 后管道卡住，无法继续

**现象**：跑完 Phase 1 后停住，看不到后续输出。

**原因**：CLI 默认 `interactive=True`，Phase 1 后会暂停等待用户输入 `Y/n/q`。
如果管道跑在 CI/CD 或非 TTY 环境里，stdin 不是终端，无法接收输入。

**解决**：CI/CD 场景用 Python API，设 `interactive=False`：
```python
agent.dev("req.md", interactive=False)
```

---

### Q6：`--sql-file` 指定了 SQL，但管道还是生成了新 SQL

**现象**：传了 `--sql-file my.sql`，结果 output 里又冒出一个新生成的 SQL。

**检查**：确认 `my.sql` 文件存在且路径正确。如果文件不存在，管道会回退到 LLM 生成。

---

### Q7：`--skill-dir` 加载了外部 skill，但管道没调用

**现象**：传了 `--skill-dir /path/to/skills/`，启动时显示已加载，但实际跑管道时没用到。

**原因**：外部 skill 加载后注册到 registry，但 workflow 节点需要**显式调用**才会执行。内置节点只调用内置 skill（如 `sql_develop`、`code_review`）。

**解决**：
- 如果想让外部 skill 在管道中自动执行，需要自定义 workflow 节点
- 如果只是想在对话中调用，用 `get_skill("your-skill-name")` 手动触发
- 参考 `internal/improvement-plan/20260625-external-plugin-loading-plan.md` 了解 workflow 适配方案
