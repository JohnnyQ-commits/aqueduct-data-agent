# Data-Copilot 开源架构评估与重构方案

> 评估日期: 2026-06-03
> 评估基准: GitHub 开源 Agent 项目标准（分层架构、可扩展、可 pip install、CI 全绿）
> 目标定位: 工业级代码式数据开发 Agent（非 Prompt 玩具）
> 参考模型: LLM 基座层 → Tools 原子层 → Skills 插件层 → Agent-DAG 编排层 → Memory 语义层
> 重构原则: **存量 100% 保留、渐进式迁移、功能零丢失**

---

## 一、架构对标分析

### 1.1 目标五层架构模型

```
┌─────────────────────────────────────────────────────────────┐
│  Memory 语义层                                               │
│  knowledge/ 升级: 本体模型 / 历史SQL / 指标口径 / 自动召回    │
├─────────────────────────────────────────────────────────────┤
│  Agent 编排层 (LangGraph DAG)                                │
│  开发模式 DAG: 需求→方案→元数据→DDL→SQL→审查→DQC→报告          │
│  审查模式 DAG: 需求→审查→DQC→沉淀                             │
├─────────────────────────────────────────────────────────────┤
│  Skills 插件层 (BaseSkill ABC)                               │
│  7 大主 Skill + 看板/CI 附属 Skill / Prompt 与代码解耦        │
├─────────────────────────────────────────────────────────────┤
│  Tools 原子工具层 (BaseTool ABC)                             │
│  scripts/ 原子化工具平移 / MCP / SQL 解析 / 模板渲染 / 日志    │
├─────────────────────────────────────────────────────────────┤
│  LLM 基座层 (BaseLLM ABC)                                    │
│  三档分工: Haiku(轻量分析) → Sonnet(中等生成) → Opus(重度生成)  │
│  基于企业内部 LLM 平台 Claude 模型族（Haiku/Sonnet/Opus）          │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 当前代码到目标层的映射

| 目标层 | 现状 | 代码归属 | 完整度 | 核心问题 |
|--------|------|----------|--------|----------|
| **LLM 基座层** | ❌ 不存在 | — | **0%** | 无模型抽象、无 Prompt 管理、无上下文管理 |
| **Tools 原子层** | ⚠️ 半成品 | `scripts/*.py` | **25%** | 有工具但无注册机制、无统一 I/O、正则 6 处重复 |
| **Skills 插件层** | ⚠️ 伪实现 | `skills/data-developer.md`（纯 Markdown） | **15%** | Skill 是提示词不是代码，不可执行、不可测试 |
| **Agent 编排层** | ⚠️ 半成品 | `scripts/main.py`（硬编码线性调用） | **30%** | 非 DAG、无状态管理、无错误恢复、无条件分支 |
| **Memory 语义层** | ✅ 已实现 | `knowledge/domains/*.json` + `gen_semantic_doc.py` | **70%** | 有本体模型和聚合，缺查询 API 和自动召回 |

### 1.3 对标结论

```
目标架构:    [LLM] → [Tools] → [Skills] → [Agent-DAG] → [Memory]
              0%       25%       15%         30%         70%

当前架构:    [main.py 硬编码顺序调用 scripts/*.py + skills/*.md 提示词文件]
```

**核心判断**：当前项目的 LLM 层和 Skills 插件层几乎完全缺失。`skills/data-developer.md` 是一个 Markdown 提示词文件，不是代码插件——这就是"Prompt 玩具"和"代码式 Agent"的本质区别。

---

## 二、逐层深度评估

### 2.1 LLM 基座层（0% — 完全缺失）

**当前状态**：无任何 LLM 相关代码。Agent 的"智能"全部靠 `skills/*.md` 的提示词驱动。

**缺失组件**：

| 组件 | 职责 | 优先级 |
|------|------|--------|
| `BaseLLM` 抽象基类 | 统一 LLM API 接口，支持多模型后端 | P0 |
| Prompt 模板管理 | 结构化 Prompt，非 Markdown 硬编码 | P0 |
| 上下文管理器 | 上下文窗口管理、压缩、拼接策略 | P0 |
| 模型路由器 | 按任务复杂度路由到不同模型 | P1 |
| Token 预算 | Token 用量追踪和配额控制 | P1 |

### 2.2 Tools 原子工具层（25% — 半成品）

**当前状态**：`scripts/` 下有 10 个 Python 脚本，包含校验、血缘、成本、DQC 等能力。每个脚本是一个独立可执行工具，但缺少统一的抽象和注册机制。

**问题清单**：

| 问题 | 影响 | 范围 |
|------|------|------|
| `RE_TABLE_NAME` 在 6 个文件中重复定义 | DRY 违反，维护困难 | 全局 |
| 文件读写无统一接口（各自 `open()`） | 无路径校验、无重试、无错误码 | 全局 |
| 无工具注册/发现机制 | `main.py` 硬编码 import，无法动态扩展 | main.py |
| 无结构化错误（print + 吞异常） | 无法程序化判断成功/失败 | 全局 |

### 2.3 Skills 业务插件层（15% — 伪实现）

**当前状态**：`skills/data-developer.md` 是一个 193 行的 Markdown 文件，定义了 Phase 1→6 的工作流程。它不是代码，无法被代码调用、测试、版本化。

**核心问题**：**Skill = Prompt ≠ Code**。真正的代码式 Agent，Skill 应该是可执行的 Python 类，Prompt 应从代码中剥离为模板文件。

### 2.4 Agent 流程编排层（30% — 半成品）

**当前状态**：`main.py` 的 `run_full()` 是一个固定 4 步的线性流程，`COMMANDS` 字典是硬编码的命令路由。没有状态管理、没有错误恢复、没有条件分支。

```python
# 当前：硬编码线性流程（非 DAG）
def run_full(sql_file, name=None):
    steps = [
        ("1/4 SQL 校验", lambda: run_validate(sql_file)),
        ("2/4 生成设计文档", lambda: run_design(sql_file, name)),
        ("3/4 生成血缘关系", lambda: run_lineage(sql_file)),
        ("4/4 成本预估", lambda: run_cost(sql_file)),
    ]
```

### 2.5 Memory 语义层（70% — 已实现，需完善）

**当前状态**：这是当前项目最完整的层。`knowledge/domains/*.json` 有符合本体论结构的领域模型（含实体、关系、公理、度量），`gen_semantic_doc.py` 能聚合为 Markdown 文档。

**仍需完善**：缺查询 API、知识图谱导航、自动召回能力、版本变更追踪。

---

## 三、综合评分

| 维度 | 评分 | 权重 | 加权分 | 一句话 |
|------|------|------|--------|--------|
| 架构设计 | 3/10 | 15% | 0.45 | 平铺脚本集，无分层框架 |
| 代码质量 | 4/10 | 15% | 0.60 | 类型注解不均，零日志，print() 满天飞 |
| 测试覆盖 | 3/10 | 15% | 0.45 | 69 用例但 3 模块未测，集成测试为零 |
| 配置管理 | 2/10 | 10% | 0.20 | 硬编码路径，无环境变量 |
| 可扩展性 | 2/10 | 10% | 0.20 | 无插件系统，无 DI，无接口抽象 |
| 文档质量 | 5/10 | 10% | 0.50 | 中文文档不错，缺 LICENSE/英文/贡献指南 |
| 打包分发 | 2/10 | 10% | 0.20 | `pip install` 不可用，entry points 失效 |
| CI/CD | 2/10 | 5% | 0.10 | 无 lint、无 coverage、单版本 |
| 安全性 | 3/10 | 10% | 0.30 | 无输入校验、路径遍历风险 |
| **加权总计** | **3.0/10** | | | |

---

## 四、重构方案：从 Prompt 玩具到工业级代码式 Agent

### 4.1 核心原则

| 原则 | 说明 |
|------|------|
| **存量 100% 保留** | `scripts/`、`knowledge/`、`tests/`、`docs/` 完整留存，不删除任何文件 |
| **渐进式迁移** | 新增 4 层核心目录 + `workspace/`，新旧并行，第 7 周一次性切换 |
| **功能零丢失** | 开发/审查双模式、DQC、血缘、成本预估、Smart Fix、CodeReview、提效看板、CI/CD 全部保留 |
| **Prompt 与代码解耦** | 所有 Prompt 从 `.py` 文件剥离为 `.tpl.md` 模板，使用变量占位符动态渲染 |
| **插件化扩展** | 新增校验规则/业务能力仅新增 md 模板 + Skill 文件，不改动 DAG |

### 4.2 最终目录结构

```
data-copilot/
│
├── llm/                         # ═══ LLM 基座层（新增）═══
│   ├── __init__.py
│   ├── base.py                  # BaseLLM 抽象基类
│   ├── claude.py                # Claude Haiku / Sonnet / Opus 适配器
│   ├── qwen.py                  # Qwen 3.5/3.6 Plus 适配器
│   ├── minimax.py               # MiniMax M2.5/2.7 适配器
│   ├── kimis.py                 # Kimi K2.5/2.6 适配器
│   ├── prompts.py               # Prompt 模板注册中心
│   ├── context.py               # 上下文管理器
│   └── router.py                # 模型路由器（Qwen vs Claude 分工）
│
├── tools/                       # ═══ Tools 原子工具层（新增，scripts 平移）═══
│   ├── __init__.py
│   ├── base.py                  # BaseTool 抽象基类
│   ├── registry.py              # 工具注册中心
│   ├── validator.py             # SQL 校验工具（原 validate_sql.py）
│   ├── lineage.py               # 血缘解析工具（原 gen_lineage.py）
│   ├── estimator.py             # 成本预估工具（原 estimate_cost.py）
│   ├── dqc.py                   # 质量校验工具（原 check_data_quality.py）
│   ├── semantic.py              # 语义文档工具（原 gen_semantic_doc.py）
│   ├── design.py                # 设计文档工具（原 gen_design.py）
│   ├── sync.py                  # 设计同步工具（原 sync_design.py）
│   ├── productivity.py          # 提效统计工具（原 analyze_productivity.py）
│   ├── batch_query.py           # 批量查询工具（原 batch_query_tables.py）
│   ├── regex.py                 # 预编译正则（统一来源，消除 6 处重复）
│   └── extra/                   # CI/CD、看板采集等附属工具
│       ├── ci_runner.py
│       └── board_collector.py
│
├── skills/                      # ═══ Skills 业务插件层（新增）═══
│   ├── __init__.py
│   ├── base.py                  # BaseSkill 抽象基类
│   ├── registry.py              # Skill 注册中心
│   ├── prompt/                  # Prompt 模板（与代码解耦）
│   │   ├── requirement_clarify.tpl.md
│   │   ├── design_scheme.tpl.md
│   │   ├── ddl_generate.tpl.md
│   │   ├── sql_develop.tpl.md
│   │   ├── code_review.tpl.md
│   │   ├── dqc_quality.tpl.md
│   │   └── report_delivery.tpl.md
│   ├── requirement_clarify.py   # 需求澄清 Skill
│   ├── design_scheme.py         # 方案设计 Skill
│   ├── ddl_generate.py          # DDL 生成 Skill
│   ├── sql_develop.py           # SQL 开发 Skill（原 data-developer.md → 代码）
│   ├── code_review.py           # 代码评审 Skill
│   ├── dqc_quality.py           # DQC 质检 Skill
│   ├── report_delivery.py       # 报告 & 语义入库 Skill
│   └── extra/                   # 看板、CI 附属 Skill
│       ├── productivity_board.py
│       ├── quality_dashboard.py
│       └── ci_publish.py
│
├── agent/                       # ═══ Agent 编排层（新增，LangGraph DAG）═══
│   ├── __init__.py
│   ├── workflow.py              # 工作流定义（开发/审查双模式）
│   ├── nodes.py                 # DAG 节点定义（只做参数组装+状态透传）
│   ├── state.py                 # 工作流状态（TypedDict）
│   ├── dag_dev.py               # 开发模式 DAG
│   ├── dag_review.py            # 审查模式 DAG
│   └── recovery.py              # 错误恢复策略
│
├── memory/                      # ═══ Memory 语义层（原 knowledge/ 升级）═══
│   ├── __init__.py
│   ├── domain.py                # 本体模型 Pydantic 定义
│   ├── store.py                 # 知识存储与查询 API
│   ├── recall.py                # 需求阶段自动召回
│   ├── graph.py                 # 知识图谱生成
│   ├── changelog.py             # 版本变更追踪
│   └── semantic_doc.py          # 语义文档聚合（原 gen_semantic_doc.py 迁移）
│
├── scripts/                     # ⬅️ 保留：原有 10 个脚本 100% 留存（向后兼容）
├── knowledge/                   # ⬅️ 保留：原有业务分域 JSON 完整留存
├── tests/                       # ⬅️ 保留：原有 69 个测试用例完整留存
├── docs/                        # ⬅️ 保留：coding-style.md 等
├── templates/                   # ⬅️ 保留：SQL/DQC/设计模板
│
├── workspace/                   # 工作区（新增）
│   ├── input/                   # 输入需求文档
│   └── output/                  # 全流程输出物
│
├── pyproject.toml
├── LICENSE
└── README.md
```

### 4.3 各层核心接口设计

#### 4.3.1 LLM 基座层

```python
# llm/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str

@dataclass
class LLMResponse:
    content: str
    usage: dict  # {prompt_tokens, completion_tokens, total_tokens}
    model: str

class BaseLLM(ABC):
    """所有 LLM 后端的抽象基类"""

    @abstractmethod
    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        ...

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        ...

    @property
    @abstractmethod
    def max_context(self) -> int:
        ...
```

**LLM 路由策略**（三档分工，基于实际使用数据 + 未来规划）：

> 实际使用：主力模型 `qwen3.6-plus`（182.6M Input Token），次用 `claude-haiku-4-5`。
> Sonnet/Opus 尚未使用但保留作为代码生成和质量保障的重度任务路由。
> Input:Output 比 126:1（偏高），重构后目标降至 30:1 以下。

| 任务类型 | 推荐模型 | 环境变量 | 推荐场景 | 备选 |
|---------|---------|---------|---------|------|
| 轻量分析 | `claude-haiku-4-5` | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | 需求解析、摘要、统计、语义召回 | `qwen3.5-plus` |
| 中等生成 | `qwen3.6-plus` | `ANTHROPIC_DEFAULT_SONNET_MODEL` | 方案编写、DDL 生成、文档输出 | `claude-sonnet-4-6` |
| 重度生成 | `qwen3.6-plus` | `ANTHROPIC_DEFAULT_OPUS_MODEL` | SQL 生成、SQL 质检 | `deepseek-v4-pro` |
| CodeReview | 公司 Opus 模型 | `ANTHROPIC_DEFAULT_OPUS_MODEL` | 代码审查、逻辑分析 | `claude-sonnet-4-6` |

**关键优化目标**：将 Input:Output 比从 126:1 降至 30:1 以下
- Prompt 从 Markdown 硬编码改为 `.tpl.md` 模板 + Jinja2 变量渲染
- 上下文管理：限制读取文件的大小，按需加载
- 模型路由：轻量任务路由到 Haiku，中等任务用 Sonnet/Qwen，重度任务用 Opus

**公司内部可用模型参考**（详见 Claude Code 配置文档）：

| 类别 | 模型 | 图片识别 | 推荐场景 |
|------|------|---------|---------|
| Claude 官方 | `claude-haiku-4-5` | ✅ | 轻量分析（Haiku 档） |
| Claude 官方 | `claude-sonnet-4-6` | ✅ | 中等生成（Sonnet 档） |
| Claude 官方 | `claude-opus-4-7` | ✅ | 重度生成（Opus 档） |
| 阿里 | `qwen3.5-plus` / `qwen3.6-plus` | ✅ | 备选轻量分析模型 |
| 阿里 | `deepseek-v4-pro` | ❌ | 备选代码生成模型 |
| 阿里 | `MiniMax-M2.5` / `M2.7` | ❌ | 备选代码生成模型 |

> 注：主路由使用 Claude 三档（Haiku/Sonnet/Opus），阿里/私部署模型作为备选。
> 具体映射由 `ANTHROPIC_DEFAULT_*_MODEL` 环境变量控制，通过 `/model` 命令可切换。

```python
# llm/router.py
class ModelRouter:
    """按任务类型自动路由到合适的模型档位
    
    基于三档分工：
    - Haiku: 轻量分析（需求解析、统计、语义召回）
    - Sonnet/Qwen: 中等生成（方案、DDL、文档）
    - Opus: 重度生成（SQL、质检、CodeReview）
    """

    ANALYSIS_TASKS = {"requirement_parse", "summarize", "board_stats", "semantic_recall"}
    MEDIUM_TASKS = {"scheme_write", "ddl_gen", "doc_gen"}
    HEAVY_TASKS = {"sql_gen", "sql_review", "code_review"}

    def route(self, task_type: str) -> BaseLLM:
        if task_type in self.ANALYSIS_TASKS:
            return self._get_haiku_model()   # 轻量分析
        elif task_type in self.MEDIUM_TASKS:
            return self._get_sonnet_model()  # 中等生成
        elif task_type in self.HEAVY_TASKS:
            return self._get_opus_model()    # 重度生成
        raise ValueError(f"Unknown task type: {task_type}")
```

#### 4.3.2 Tools 原子工具层

```python
# tools/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class ToolResult:
    success: bool
    data: any = None
    error: str = ""
    metadata: dict = field(default_factory=dict)

class BaseTool(ABC):
    """所有工具的抽象基类"""
    name: str = ""
    description: str = ""

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        ...

    def validate(self, **kwargs) -> list[str]:
        """参数校验，返回错误列表"""
        return []
```

```python
# tools/registry.py
from typing import Type

TOOL_REGISTRY: dict[str, Type[BaseTool]] = {}

def register_tool(tool_cls: Type[BaseTool]) -> Type[BaseTool]:
    """装饰器：注册工具到全局注册表"""
    TOOL_REGISTRY[tool_cls.name] = tool_cls
    return tool_cls

def get_tool(name: str) -> BaseTool:
    if name not in TOOL_REGISTRY:
        raise ToolNotFoundError(f"Tool '{name}' not registered")
    return TOOL_REGISTRY[name]()
```

**脚本迁移映射**：

| 原脚本 | 目标 Tool | 说明 |
|--------|-----------|------|
| `scripts/validate_sql.py` | `tools/validator.py` | SQL 校验 + Smart Fix |
| `scripts/gen_lineage.py` | `tools/lineage.py` | 字段级血缘解析 |
| `scripts/estimate_cost.py` | `tools/estimator.py` | 成本预估 + 风险扫描 |
| `scripts/check_data_quality.py` | `tools/dqc.py` | DQC 质量校验 + 仪表盘 |
| `scripts/gen_semantic_doc.py` | `tools/semantic.py` | 语义文档聚合 |
| `scripts/gen_design.py` | `tools/design.py` | 设计文档生成 |
| `scripts/sync_design.py` | `tools/sync.py` | 设计同步 |
| `scripts/analyze_productivity.py` | `tools/productivity.py` | 提效统计 |
| `scripts/batch_query_tables.py` | `tools/batch_query.py` | 批量元数据查询 |
| `scripts/utils.py` | `tools/regex.py` | 统一正则来源 |

#### 4.3.3 Skills 业务插件层

```python
# skills/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class SkillContext:
    """Skill 执行上下文"""
    input: any
    state: dict = field(default_factory=dict)
    llm: BaseLLM = None  # 注入 LLM 实例

@dataclass
class SkillResult:
    success: bool
    artifacts: list[str] = field(default_factory=list)
    data: any = None
    error: str = ""

class BaseSkill(ABC):
    """所有业务 Skill 的抽象基类"""
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    prompt_template: str = ""  # skills/prompt/*.tpl.md 路径

    @abstractmethod
    def execute(self, context: SkillContext) -> SkillResult:
        ...

    def render_prompt(self, **kwargs) -> list[LLMMessage]:
        """从 .tpl.md 模板渲染 Prompt"""
        from ..tools.template import render_template
        content = render_template(self.prompt_template, **kwargs)
        return [LLMMessage("system", content)]
```

**7 大主 Skill 映射**：

| Skill | 对应 Phase | Prompt 模板 | 调用的 Tool |
|-------|-----------|-------------|-------------|
| `requirement_clarify` | Phase 1 | `requirement_clarify.tpl.md` | `batch_query`（元数据检索） |
| `design_scheme` | Phase 2 | `design_scheme.tpl.md` | — |
| `ddl_generate` | Phase 3 | `ddl_generate.tpl.md` | `design`（DDL 生成） |
| `sql_develop` | Phase 4 | `sql_develop.tpl.md` | `validator`（SQL 校验） |
| `code_review` | Phase 4.5 | `code_review.tpl.md` | `validator`, `lineage` |
| `dqc_quality` | Phase 5 | `dqc_quality.tpl.md` | `dqc`（质量校验） |
| `report_delivery` | Phase 6 | `report_delivery.tpl.md` | `semantic`（语义入库） |

**Prompt 模板示例**（`skills/prompt/sql_develop.tpl.md`）：

```markdown
# SQL 开发 Skill

## 角色
你是一名资深数据仓库开发工程师，负责从需求文档到核心 SQL 的编写。

## 输入
- 需求文档: {{requirement_doc}}
- 目标表结构: {{ddl_content}}
- 语义模型: {{domain_context}}
- 代码规范: {{coding_style}}

## 要求
1. 关键字全小写
2. select 字段竖排，4空格缩进
3. where 条件紧凑
4. 函数内逗号无空格
5. join 与 on 分行
6. 复杂场景使用 CTE

## 输出
输出完整的 SQL 代码，包含文件头注释。
```

#### 4.3.4 Agent 流程编排层（LangGraph DAG）

```python
# agent/state.py
from typing import TypedDict, Optional

class WorkflowState(TypedDict, total=False):
    """工作流状态"""
    requirement: str              # 需求文档内容
    domain_id: Optional[str]      # 匹配的业务域
    design_scheme: Optional[str]  # 设计方案
    ddl_content: Optional[str]    # DDL 内容
    sql_content: Optional[str]    # 核心 SQL
    review_result: Optional[str]  # 审查结果
    dqc_result: Optional[str]     # DQC 测试结果
    artifacts: list[str]          # 产出文件路径
    errors: list[str]             # 错误列表
    mode: str                     # "dev" | "review"
```

```python
# agent/dag_dev.py — 开发模式 DAG
from langgraph.graph import StateGraph, END

def build_dev_workflow() -> StateGraph:
    """开发模式：需求→方案→元数据→DDL→SQL→审查→DQC→报告"""
    graph = StateGraph(WorkflowState)

    # 节点只做参数组装、状态透传、调用对应 Skill
    graph.add_node("requirement", node_requirement)    # 需求理解
    graph.add_node("design", node_design)              # 方案设计
    graph.add_node("metadata", node_metadata)          # 元数据检索
    graph.add_node("ddl", node_ddl)                    # DDL 生成
    graph.add_node("sql", node_sql)                    # SQL 开发
    graph.add_node("review", node_review)              # 代码审查（可选）
    graph.add_node("dqc", node_dqc)                    # DQC 质检
    graph.add_node("report", node_report)              # 报告 + 语义入库

    # 强顺序边
    graph.set_entry_point("requirement")
    graph.add_edge("requirement", "design")
    graph.add_edge("design", "metadata")
    graph.add_edge("metadata", "ddl")
    graph.add_edge("ddl", "sql")
    graph.add_edge("sql", "review")
    graph.add_edge("review", "dqc")
    graph.add_edge("dqc", "report")
    graph.add_edge("report", END)

    # 可选分支：提效统计 + 看板 + CI 发布
    graph.add_node("productivity", node_productivity)
    graph.add_edge("report", "productivity")
    graph.add_edge("productivity", END)

    return graph.compile()
```

```python
# agent/dag_review.py — 审查模式 DAG
def build_review_workflow() -> StateGraph:
    """审查模式：需求→代码评审→DQC→沉淀"""
    graph = StateGraph(WorkflowState)

    graph.add_node("requirement", node_requirement)
    graph.add_node("review", node_review)
    graph.add_node("dqc", node_dqc)
    graph.add_node("report", node_report)

    graph.set_entry_point("requirement")
    graph.add_edge("requirement", "review")
    graph.add_edge("review", "dqc")
    graph.add_edge("dqc", "report")
    graph.add_edge("report", END)

    return graph.compile()
```

**Node 节点示例**（只做参数组装 + 状态透传 + 调用 Skill）：

```python
# agent/nodes.py
def node_sql(state: WorkflowState) -> WorkflowState:
    """SQL 开发节点 — 无 Prompt、无业务逻辑"""
    skill = get_skill("sql_develop")
    context = SkillContext(
        input={
            "requirement_doc": state["requirement"],
            "ddl_content": state["ddl_content"],
            "domain_context": state.get("domain_context", ""),
        },
        state=state,
        llm=router.route("sql_gen"),
    )
    result = skill.execute(context)
    state["sql_content"] = result.data
    if result.artifacts:
        state["artifacts"].extend(result.artifacts)
    if result.error:
        state["errors"].append(result.error)
    return state
```

#### 4.3.5 Memory 语义层（升级版）

```python
# memory/store.py
from pathlib import Path
from .domain import DomainModel

class MemoryStore:
    """知识存储与查询 API"""

    def __init__(self, domains_dir: Path):
        self.domains_dir = domains_dir
        self._cache: dict[str, DomainModel] = {}

    def load(self, domain_id: str) -> DomainModel:
        """加载指定业务域"""
        if domain_id not in self._cache:
            path = self.domains_dir / f"{domain_id}.json"
            self._cache[domain_id] = DomainModel.from_json(path)
        return self._cache[domain_id]

    def match_domain(self, requirement: str) -> DomainModel | None:
        """需求阶段自动召回：根据需求描述匹配最相关的业务域"""
        best_score, best_domain = 0, None
        for domain_id in self.list_domains():
            domain = self.load(domain_id)
            score = self._semantic_similarity(requirement, domain)
            if score > best_score:
                best_score, best_domain = score, domain
        return best_domain if best_score > 0.3 else None

    def find_entities(self, domain_id: str, keyword: str) -> list:
        """在本体中搜索匹配的实体"""
        domain = self.load(domain_id)
        return domain.search_entities(keyword)

    def find_metrics(self, domain_id: str, keyword: str) -> list:
        """在本体中搜索匹配的指标"""
        domain = self.load(domain_id)
        return domain.search_metrics(keyword)

    def get_relationship_graph(self, domain_id: str) -> str:
        """生成可导航的关系图谱（Mermaid）"""
        domain = self.load(domain_id)
        return domain.to_mermaid()

    def list_domains(self) -> list[str]:
        return [p.stem for p in self.domains_dir.glob("*.json")]
```

**Memory 层升级点**：

| 能力 | 现状 | 升级后 |
|------|------|--------|
| 存储格式 | `knowledge/domains/*.json` | 不变，兼容现有格式 |
| 加载方式 | 手动 `json.load()` | `MemoryStore.load()` 带缓存 |
| 实体搜索 | ❌ | `find_entities(domain, keyword)` |
| 指标搜索 | ❌ | `find_metrics(domain, keyword)` |
| 需求召回 | ❌ | `match_domain(requirement)` 自动匹配 |
| 关系图谱 | 仅聚合为 Markdown | `get_relationship_graph()` 返回 Mermaid |
| 版本追踪 | ❌ | `changelog.py` 记录每次变更 |

### 4.4 迁移策略：渐进式，不破不立

```
Week 1:  基础设施 + Tools 层骨架
         - 创建 llm/ tools/ skills/ agent/ memory/ 空包
         - 定义 BaseLLM / BaseTool / BaseSkill 抽象基类
         - 创建 tools/registry.py，迁移 scripts/utils.py → tools/regex.py
         - 保持 scripts/ 100% 可用

Week 2:  工具层迁移（每个脚本包装为 Tool）
         - 10 个 scripts/*.py 平移为 tools/*.py（import 转发，保持旧路径可用）
         - 统一正则来源，消除 6 处重复
         - 为每个 Tool 写单元测试

Week 3:  Skills 层构建
         - 定义 BaseSkill ABC + Prompt 模板系统
         - 将 skills/data-developer.md 转为 skills/sql_develop.py
         - 提取 7 个 .tpl.md Prompt 模板
         - 构建 Skill 注册中心

Week 4:  LLM 层构建
         - 实现 BaseLLM + 多模型适配器（Haiku/Qwen/Sonnet/Opus）
         - 实现 ModelRouter（按任务类型路由到 企业内部 LLM 平台模型）
         - 实现 ContextManager

Week 5:  Agent DAG 层构建
         - 用 LangGraph 实现开发模式 DAG
         - 用 LangGraph 实现审查模式 DAG
         - 实现 WorkflowState 和错误恢复

Week 6:  Memory 层升级 + CLI 层
         - MemoryStore 查询 API
         - 自动召回、语义相似度匹配
         - Typer CLI 入口

Week 7:  整合测试 + CI + 文档
         - 全链路集成测试
         - CI 矩阵：pytest + ruff + mypy + coverage + Python 3.10/3.11/3.12
         - 英文 README + MkDocs
         - 确认 scripts/ 可安全移除（保留归档）
```

**关键原则**：前 6 周 `scripts/` 保持可用，新层并行开发。第 7 周确认全绿后再标记 `scripts/` 为 deprecated。

### 4.5 重构前后对比

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| 安装 | `cd repo && python scripts/foo.py` | `pip install data-copilot && data-copilot dev --requirement req.md` |
| 架构 | 平铺 10 个脚本 + 1 个 Markdown | 5 层分层架构（LLM/Tools/Skills/Agent/Memory） |
| Prompt | 硬编码在 Markdown/Python 中 | 统一 `.tpl.md` 模板 + Jinja2 变量渲染 |
| 模型 | 无抽象，依赖 LLM 平台 | `BaseLLM` ABC + 三档路由（Haiku → Sonnet/Qwen → Opus） |
| 类型安全 | 40% 有类型注解 | 100% 类型注解 + mypy CI 门禁 |
| 日志 | `print()` | `logging` + 结构化 JSON |
| 配置 | 硬编码路径 | `pydantic-settings` + 环境变量 |
| 错误处理 | print + 吞异常 | 自定义异常 + 结构化错误码 |
| 可扩展性 | 修改 main.py 才能加新功能 | 实现 Skill ABC，自动注册，不改 DAG |
| 测试 | 69 用例，3 模块未测 | 150+ 用例，100% 核心覆盖，集成测试覆盖 DAG |
| 文档 | 中文，无英文 | 中英双语，API docs，CHANGELOG，CONTRIBUTING |
| CI | 仅 pytest | pytest + ruff + mypy + coverage + multi-python |
| 评分 | 3.0/10 | 目标 9.0/10 |

### 4.6 立即可做的 5 件事

| # | 任务 | 耗时 | 产出 |
|---|------|------|------|
| 1 | 恢复 `gen_lineage.py` | 30min | 测试套件可运行 |
| 2 | 添加 `LICENSE` 文件 | 1min | 法律合规 |
| 3 | 修复 `pyproject.toml` 打包配置 | 5min | `pip install -e .` 可用 |
| 4 | 创建 5 层空包 + `exceptions.py` | 30min | 分层架构起点 |
| 5 | 定义 `BaseLLM` / `BaseTool` / `BaseSkill` 三个 ABC | 1h | 接口契约 |

---

## 五、评分演进预测

| 维度 | 当前 | Week 1 | Week 2 | Week 3 | Week 4 | Week 5 | Week 6 | Week 7（目标） |
|------|------|--------|--------|--------|--------|--------|--------|----------------|
| LLM 基座层 | 0/10 | 1/10 | 1/10 | 1/10 | 7/10 | 7/10 | 8/10 | 9/10 |
| Tools 原子层 | 2.5/10 | 3/10 | 7/10 | 7/10 | 7/10 | 8/10 | 8/10 | 9/10 |
| Skills 插件层 | 1.5/10 | 1.5/10 | 1.5/10 | 7/10 | 7/10 | 7/10 | 8/10 | 9/10 |
| Agent 编排层 | 3/10 | 3/10 | 3/10 | 3/10 | 3/10 | 7/10 | 8/10 | 9/10 |
| Memory 语义层 | 7/10 | 7/10 | 7/10 | 7/10 | 7/10 | 7/10 | 9/10 | 9/10 |
| 工程化 | 2/10 | 3/10 | 4/10 | 4/10 | 4/10 | 5/10 | 7/10 | 9/10 |
| **加权总计** | **3.0** | **3.1** | **4.1** | **4.4** | **5.5** | **6.9** | **8.2** | **9.0/10** |
