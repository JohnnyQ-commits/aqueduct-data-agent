# CLAUDE.md — Aqueduct Project Instructions

> 这份文件是给 Claude（包括 Claude Code CLI、PyCharm Claude 等所有形态）的项目级指令。

## 项目简介

**Aqueduct** 是一个工业级数据开发自动化 Agent 框架。
给定需求文档，自动产出 11 个标准化交付物（DDL、ETL SQL、DQC、血缘图、设计文档等）。

核心特点：
- **Python 管道优先**：所有数据开发工作必须通过 `aqueduct` CLI 或 Python API 执行
- **7 层架构**：Config → LLM → Tools → Skills → Engine → Memory → MCP
- **DAG 编排**：7 个 Phase 按顺序执行，Phase 4.5 有审查→修复循环

## 最重要的规则

### ❌ 绝对不要做的事

1. **不要绕过管道**：不要自己读需求文档然后直接写 SQL/DDL/Design。
   - 必须通过 `aqueduct dev <requirement.md>` 或 `from aqueduct import Aqueduct; Aqueduct().dev()` 执行
   - 即使你觉得"直接写更快"，也必须走管道
2. **不要跳过 Phase 4.5 的工具链校验**：ValidatorTool、LineageTool、EstimatorTool 的结果必须保留
3. **不要忽略管道输出的 errors 列表**：如果有 errors，必须向用户报告并分析原因

### ✅ 必须做的事

1. **使用 Skills**：用户要求数据开发时，优先使用 `/aqueduct-dev` 或 `/data-developer` skill
2. **Phase 1 必须确认**：管道在 Phase 1 会暂停要求用户确认需求理解，不要跳过
3. **展示产出物**：管道完成后，列出 output/ 目录下所有生成文件

## 开发环境

### 虚拟环境

```bash
# 激活虚拟环境
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# 安装依赖（首次或依赖变更后）
pip install -e ".[dev]"
```

### 常用命令

```bash
# 运行测试
python -m pytest tests/ -v

# 代码检查
ruff check src/ tests/
ruff format src/ tests/

# 端到端测试
aqueduct dev examples/ecommerce_daily_stat.md

# 查看项目状态
aqueduct status
```

## 项目结构

```
src/aqueduct/
├── cli/main.py          # CLI 入口（argparse）
├── core.py              # 管道执行器 _run_pipeline()
├── exceptions.py        # 自定义异常（LLMTimeoutError 等）
├── config/settings.py   # Pydantic-settings 配置
├── engine/
│   ├── nodes/           # 7 个 Phase 节点（requirement → report）
│   ├── workflow.py      # StateGraph 定义
│   ├── recovery.py      # 错误恢复（指数退避）
│   └── state.py         # WorkflowState TypedDict
├── skills/              # 7 个业务 Skill + prompt/ 模板
├── tools/               # 9 个原子工具
├── llm/                 # Claude 适配器 + 模型路由
├── memory/              # 本体知识库 + Top-K 召回
└── utils/               # 日志、正则等工具
```

## 关键模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| 管道执行 | `core.py` | `_run_pipeline()` 线性管道 + 审查→修复循环 |
| Phase 节点 | `engine/nodes/*.py` | 每个 Phase 的状态组装和调用 |
| LLM 调用 | `engine/nodes/helpers.py` | `call_llm()` 统一入口，带完整日志 |
| SQL 开发 | `engine/nodes/sql.py` | Phase 4：生成/读取 SQL + 自动工具链 |
| 代码审查 | `engine/nodes/review.py` | Phase 4.5：独立上下文审查 + 解析问题 |
| 超时处理 | `llm/claude.py` | 超时抛 `LLMTimeoutError`，指数退避重试 |

## 测试方式

```bash
# 单元测试 + 集成测试（165 tests）
python -m pytest tests/ -v

# 端到端验证（需要 LLM API key）
aqueduct dev examples/ecommerce_daily_stat.md
```

## Claude Skills

本项目有 3 个 Claude Code Skills：

| Skill | 触发方式 | 用途 |
|-------|---------|------|
| `/aqueduct-dev` | 用户提供需求文档 | 一键启动完整管道 |
| `/data-developer` | 同上（更详细的领域知识） | Phase 1-6 详细指导 |
| `/change-management` | 用户提到"变更"、"CR" | 交付后需求变更管理 |

## 配置文件

| 文件 | 用途 |
|------|------|
| `.env` | 数据平台凭证（DP_BASE_URL, DP_COOKIE 等） |
| `.mcp.json` | MCP 服务器配置 |
| `.claude/settings.json` | Claude Code 权限 |
