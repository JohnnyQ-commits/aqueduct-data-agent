# Aqueduct

**Data Engineering Automation Agent Framework**

[![CI](https://github.com/JohnnyQ-commits/aqueduct/actions/workflows/ci.yml/badge.svg)](https://github.com/JohnnyQ-commits/aqueduct/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-alpha-orange)

[English](#overview) | [中文](#overview-zh)

---

## Overview

**The problem**: Data engineers spend 60-70% of their time on repetitive work — understanding requirements, writing boilerplate SQL, creating DDL, writing DQC tests, generating documentation. The creative part (business logic) is small; the mechanical part is huge.

**Aqueduct automates the mechanical part.**

Give it a requirement document. Get back 11 standardized deliverables: DDL, ETL SQL, DQC test cases, field-level lineage, design documents, and a comprehensive report.

```text
Requirement (.md) --> Design (.md) --> DDL (.sql) --> SQL (.sql) --> Review --> DQC (.sql) --> Report (.md) --> Deliverables (11 files)
```

### What makes it different

| Principle | What it means |
|-----------|---------------|
| **Framework, not tool** | Embed into Claude Code, LangChain, or your own app via `from aqueduct import Aqueduct` |
| **7-layer architecture** | Clean separation: MCP / LLM / Tools / Skills / Engine / Memory / Config |
| **Platform agnostic** | Connects to any data platform via standard MCP protocol (`.mcp.json`) |
| **Ontology knowledge** | Business domains modeled as typed JSON — entities, relationships, metrics, axioms |
| **DAG orchestration** | StateGraph-based workflow with interactive checkpoints and error recovery |
| **Review-fix loop** | Code review finds bugs → auto-fix SQL → re-review → production-ready quality |
| **Full observability** | Per-task log files, phase timing, LLM call tracing, tool execution audit |
| **Prompt-code decoupled** | Prompts as `.tpl.md` files — edit without touching code, i18n-ready |

### Output: 11 mandatory deliverables

Every pipeline run produces a standardized output directory:

| # | File | Description |
|---|------|-------------|
| 1 | `Phase3-table_ddl.sql` | Target table DDL |
| 2 | `Phase4-{name}.sql` | ETL SQL with full lineage |
| 3 | `Phase5-dqc_tests.sql` | DQC test cases (5 categories) |
| 4 | `Phase6-Design.md` | Design document |
| 5 | `Phase5-{name}_review.md` | Code review report |
| 6 | `Phase6-Report.md` | Delivery summary report |
| 7-11 | Metadata files | Lineage, cost estimate, productivity metrics, etc. |

---

## Installation

### From source (recommended for now)

```bash
git clone https://github.com/JohnnyQ-commits/aqueduct.git
cd aqueduct
pip install -e .
```

### With dev dependencies

```bash
pip install -e ".[dev]"
```

### Prerequisites

- Python 3.10+
- An LLM API key (Anthropic Claude recommended)
- Optional: a data platform accessible via MCP for auto DQC execution

---

## Quick Start

### CLI

```bash
# Development mode: requirement --> full delivery
aqueduct dev requirement.md

# With external SQL file (skip LLM SQL generation)
aqueduct dev requirement.md --sql-file my_etl.sql

# Review mode: validate SQL changes against online version
aqueduct review online.sql changed.sql -d "Add customer filter"

# Change management: track post-delivery requirement changes
aqueduct change original_req.md new_req.md -d "New metrics added"

# SQL validation
aqueduct validate query.sql --strict

# Debug logging
aqueduct --verbose dev requirement.md
```

### Python API

```python
from aqueduct import Aqueduct

agent = Aqueduct()

# Development mode
result = agent.dev("requirement.md", output_dir="output/my_project")

# Access deliverables
print(result.artifacts)     # Generated file paths
print(result.state["sql"])  # Generated ETL SQL
```

### Interactive workflow

The `dev` command runs node-by-node with real-time progress:

```
$ aqueduct dev requirement.md

[INFO] Reading requirement: requirement.md
[INFO] Starting development mode workflow...
  [RUNNING] Phase 1/7: Requirement understanding
============================================================
[Phase 1 Complete] Requirement Summary:
============================================================
  Source tables: dw_ecommerce.orders, dw_ecommerce.users
  Target table:  dw_report.daily_order_stats
  Key metrics:   GMV, order count, avg order value
  ...
============================================================

Confirm? [Y/n/q]: y

  [RUNNING] Phase 2/7: Design scheme
  [RUNNING] Phase 3/7: DDL generation
  ...

[OK] Development mode workflow completed, 11 artifact(s):
  [FILE] output/my_project/表结构.sql
  [FILE] output/my_project/daily_order_stats.sql
  [FILE] output/my_project/数据质量测试.sql
  ...
```

---

## Data Platform Integration

Aqueduct connects to your data platform via the standard [MCP protocol](https://modelcontextprotocol.io/). No vendor lock-in.

### 1. Configure MCP server

```bash
cp .mcp.example.json .mcp.json
```

Edit `.mcp.json`:

```json
{
  "mcpServers": {
    "my-data-platform": {
      "command": "npx",
      "args": ["-y", "@your-org/mcp-server"],
      "env": {
        "PLATFORM_URL": "https://your-platform.example.com",
        "API_TOKEN": "your-token"
      }
    }
  }
}
```

### 2. Configure data platform connection (for auto DQC)

```bash
cp .env.example .env
```

Edit `.env`:

```env
DP_BASE_URL=https://your-data-platform.example.com
DP_COOKIE=your_session_cookie
DP_USER_ID=your_user_id
```

Once configured, `aqueduct dev` automatically executes DQC test cases and populates the Quality Dashboard.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│  MCP Layer                                               │
│  Standard MCP protocol / .mcp.json                       │
│  get_table_schema · execute_sql · list_tables            │
├─────────────────────────────────────────────────────────┤
│  Memory Layer                                            │
│  Ontology models / Domain knowledge / Auto-recall (Top-K)│
├─────────────────────────────────────────────────────────┤
│  Agent-DAG Layer (StateGraph)                            │
│  Dev: requirement --> design --> DDL --> SQL --> review --> DQC  │
│  Review: requirement --> review --> DQC --> report              │
├─────────────────────────────────────────────────────────┤
│  Skills Layer (BaseSkill ABC)                            │
│  7 core skills / Prompt-code decoupled (.tpl.md)         │
├─────────────────────────────────────────────────────────┤
│  Tools Layer (BaseTool ABC)                              │
│  9 atomic tools / SQL parsing / Template rendering       │
├─────────────────────────────────────────────────────────┤
│  LLM Layer (BaseLLM ABC)                                 │
│  3-tier routing: Haiku --> Sonnet --> Opus                  │
└─────────────────────────────────────────────────────────┘
```

### Layers at a glance

| Layer | Responsibility | Directory |
|-------|---------------|-----------|
| MCP | Data platform integration via standard protocol | `mcp/` |
| LLM | LLM abstraction, 3-tier routing, context management | `llm/` |
| Tools | 9 atomic tools: SQL validation, lineage, DQC, cost estimation... | `tools/` |
| Skills | 7 core skills with `.tpl.md` prompt templates | `skills/` |
| Engine | DAG orchestration, state graph, error recovery | `engine/` |
| Memory | Ontology knowledge base, domain models, Top-K recall | `memory/` |
| Config | Pydantic-settings based configuration | `config/` |

> See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design document.

---

## Workflow Modes

### Dev mode — `aqueduct dev`

Full pipeline from requirement document to standardized delivery.

```
Phase 1: Requirement understanding     (interactive checkpoint)
Phase 2: Design scheme                 (source analysis + table design)
Phase 3: DDL generation                (target table structure)
Phase 4: SQL development               (ETL logic with CTEs)
Phase 4.5: Code review                 (automated quality check)
  └── Fix loop: Critical/Warning → auto-fix SQL → back to Phase 4
Phase 5: DQC quality tests             (5 test categories)
Phase 6: Report delivery               (comprehensive delivery report)
```

> Phase 1 pauses for user confirmation. Review the requirement summary before the pipeline continues.

### Review mode — `aqueduct review`

Validate SQL changes against the online production version.

```
Phase 1: Requirement understanding
Phase 2: Diff analysis              (online vs. changed)
Phase 3: Code review                (logic verification)
Phase 4: DQC quality tests
Phase 5: Report delivery
```

### Change mode — `aqueduct change`

Manage requirement changes after delivery. Every change gets a CR number, full traceability, and rollback capability.

```
Phase 1: Change identification       (diff old vs. new requirement)
Phase 2: Change document             (generate CR document)
Phase 3: Change SQL generation       (delta SQL)
Phase 4: Change review               (impact analysis)
Phase 5: Merge execution             (apply changes)
Phase 6: Archive                     (CR record + rollback scripts)
```

---

## Knowledge Base

Business domains are modeled as typed JSON with entities, relationships, metrics, and axioms.

### Example: E-commerce domain

```json
{
  "domain_id": "ecommerce_order",
  "name": "E-commerce Order Analysis",
  "entities": [
    {
      "name": "Customer",
      "source_table": "dw_demo.dim_customer",
      "attributes": ["customer_id", "customer_name", "register_date"]
    },
    {
      "name": "Order",
      "source_table": "dw_demo.dwd_order",
      "attributes": ["order_id", "customer_id", "order_amount", "order_status"]
    }
  ],
  "metrics": [
    {
      "name": "GMV",
      "formula": "SUM(order_amount)",
      "source_field": "dwd_order.order_amount"
    }
  ]
}
```

### Built-in example domains

| Domain | File | Entities |
|--------|------|----------|
| E-commerce orders | `knowledge/domains/ecommerce_order.json` | Customer, Order, Product, Category |
| SaaS user activity | `knowledge/domains/saas_user_activity.json` | User, Session, Feature, Subscription |
| Supply chain inventory | `knowledge/domains/supply_chain_inventory.json` | Warehouse, SKU, StockMovement, Supplier |

Add your own domains: create a JSON file in `knowledge/domains/`, and Aqueduct will auto-discover it.

---

## Project Structure

```
aqueduct/
|-- .claude/skills/            # Claude Code agent skills
|   |-- aqueduct/              # Framework invocation
|   |-- data-developer/        # Full pipeline automation
|   +-- change-management/     # Requirement change tracking
|-- src/aqueduct/              # Layered architecture
|   |-- mcp/                   # MCP integration layer
|   |   |-- adapters/          # Data platform adapters
|   |   +-- tools/             # MCP tool implementations
|   |-- llm/                   # LLM base layer
|   |-- tools/                 # 9 atomic tools + registry
|   |-- skills/                # 7 core skills + prompt templates
|   |   +-- prompt/            # .tpl.md prompt templates
|   |-- engine/                # Agent-DAG layer
|   |   +-- nodes/             # 8 node modules
|   |-- memory/                # Semantic memory + Top-K recall
|   |-- config/                # Pydantic-settings configuration
|   |-- cli/                   # CLI entry point
|   +-- utils/                 # Logging + regex utilities
|-- knowledge/domains/         # Ontology knowledge base (JSON)
|-- tests/                     # Unit + integration tests
+-- examples/                  # Example requirement documents
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=src/aqueduct --cov-report=html

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Or use Make
make test
make lint
make format
```

---

## Configuration Reference

| File | Purpose |
|------|---------|
| `.env` | Data platform credentials (API keys, cookies) — see `.env.example` |
| `.mcp.json` | MCP server definitions — see `.mcp.example.json` |
| `pyproject.toml` | Python package metadata, tool configuration |
| `.pre-commit-config.yaml` | Git pre-commit hooks (ruff, bandit, markdownlint) |
| `.claude/settings.json` | Claude Code permissions |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AQUEDUCT_MAX_FIX_ITERATIONS` | `2` | Review-fix loop max iterations |
| `AQUEDUCT_LLM_MAX_RETRIES` | `2` | LLM timeout retry count (exponential backoff) |
| `AQUEDUCT_EXTERNAL_SQL_PATH` | `""` | External SQL file (skips LLM generation) |

---

## Design Principles

| Principle | Description |
|-----------|-------------|
| **Code as Infrastructure** | The agent framework is a pip-installable Python package — versioned, tested, distributed like any software library |
| **Ontology over Prompting** | Business knowledge is modeled as structured data (JSON), not buried in prompts. Prompts reference the model, not the other way around |
| **DAG over Chat** | Data development is a directed acyclic graph, not a conversation. Each node has defined inputs, outputs, and validation rules |
| **Deliverables over Answers** | The output is not a chat response. It is a set of 11 standardized, production-ready files in a structured directory |
| **Platform Agnostic** | MCP protocol abstraction means the framework works with any data platform — no vendor lock-in |
| **Progressive Disclosure** | Simple CLI for beginners (`aqueduct dev req.md`). Full Python API for advanced users. Skills system for customization |

---

## Comparison

| Feature | Aqueduct | Raw LLM Chat | dbt | Airflow |
|---------|----------|-------------|-----|---------|
| Input | Requirement doc | Free text | YAML config | Python DAG |
| Output | 11 standardized files | Text response | SQL + tests | Task graph |
| Code review | Built-in | Manual | PR review | N/A |
| DQC testing | Auto-generated | Manual | dbt tests | Custom |
| Knowledge base | Ontology JSON | Context window | Macros | Variables |
| Lineage | Auto from SQL | None | Compiled | Task-level |
| Embeddable | Python library | API only | Library | Library |
| Data platform | Any (via MCP) | Any | Any | Any |

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- Report bugs via [GitHub Issues](https://github.com/JohnnyQ-commits/aqueduct/issues)
- Suggest features via [GitHub Discussions](https://github.com/JohnnyQ-commits/aqueduct/discussions)
- Security issues: see [SECURITY.md](SECURITY.md)

---

## License

MIT. See [LICENSE](LICENSE).

---
---

## Overview (ZH)

**问题**：数据工程师 60-70% 的时间花在重复劳动上 —— 理解需求、写模板 SQL、建表 DDL、写 DQC 测试、生成文档。真正需要创造力的业务逻辑只占很小一部分。

**Aqueduct 自动化这些重复劳动。**

给它一份需求文档，拿回 11 个标准化交付物：DDL、ETL SQL、DQC 测试用例、字段级血缘、设计文档、完整交付报告。

### 核心特性

| 特性 | 说明 |
|------|------|
| **开发模式** | 需求理解 -> 设计方案 -> DDL 生成 -> SQL 开发 -> 代码审查 -> (修复循环) -> DQC 质检 -> 报告交付 |
| **审查→修复循环** | Phase 4.5 审查发现 Critical/Warning → 自动修复 SQL → 回环到 Phase 4 重新执行 |
| **全链路可观测** | 每任务独立日志文件、Phase 阶段耗时、LLM 调用追踪、工具执行审计 |
| **LLM 超时重试** | 指数退避重试（最多 2 次，timeout 翻倍），超时不再静默吞掉 |
| **外部 SQL 输入** | `--sql-file` 参数跳过 LLM 生成，直接使用已有的 SQL 文件 |
| **审查模式** | 需求理解 -> 差异比对 -> 代码审查 -> DQC 质检 -> 报告交付 |
| **变更管理** | CR 建档 -> 变更 SQL -> 影响分析 -> 审查 -> 合并 -> 归档（含回滚脚本） |
| **本体知识库** | 业务域建模为 JSON — 实体、关系、指标、公理，自动 Top-K 召回 |
| **SQL 校验** | 6+1 项检查：SELECT *、分区过滤、关键字大小写、除法判零、JOIN ON、聚合 NVL、分号 |
| **数据质量** | 5 大测试类别：唯一性、业务反证、一致性、边界、波动 |
| **字段血缘** | 从 SQL 自动生成 Mermaid ER 图 |
| **成本预估** | 静态分析：笛卡尔积、缺失分区过滤、大表关联风险 |
| **提效看板** | 量化 Agent 节省的工时、代码行数、修复率 |

### 快速开始

```bash
# 安装
git clone https://github.com/JohnnyQ-commits/aqueduct.git
cd aqueduct
pip install -e .

# 开发模式：需求文档 -> 完整交付
aqueduct dev 需求文档.md

# 使用外部 SQL 文件（跳过 LLM 生成）
aqueduct dev 需求文档.md --sql-file my_etl.sql

# 审查模式：对比线上版本 vs 变更版本
aqueduct review 线上版本.sql 变更版本.sql

# 变更管理：交付后的需求变更
aqueduct change 原始需求.md 新需求.md -d "新增指标"

# SQL 规范校验
aqueduct validate 查询.sql --strict
```

### 三种使用方式

| 方式 | 用法 | 适合场景 |
|------|------|----------|
| **CLI 命令** | `aqueduct dev 需求.md` | 独立使用、CI/CD 流水线 |
| **Python 库** | `from aqueduct import Aqueduct` | 嵌入 Claude Code、LangChain 等 |
| **参考架构** | 7 层分层设计 | 数据开发 Agent 的实现参考 |

### 连接你自己的数据平台

只需配置 `.mcp.json`，通过标准 MCP 协议对接，**不依赖任何特定平台**：

```json
{
  "mcpServers": {
    "your-platform": {
      "command": "npx",
      "args": ["-y", "@your-org/mcp-server"],
      "env": {
        "PLATFORM_URL": "https://your-platform.example.com",
        "API_TOKEN": "your-token"
      }
    }
  }
}
```

> 更多文档：[架构设计](ARCHITECTURE.md) | [贡献指南](CONTRIBUTING.md) | [安全策略](SECURITY.md)
