---
name: aqueduct
description: >
  Aqueduct 数据开发自动化框架调用入口。
  DO trigger when 用户提到"数据开发"、"SQL开发"、"数仓开发"、"数据管道"、
  "表结构设计"、"数据质量测试"、"DA设计"、"aqueduct"、"需求转SQL"、"ETL开发"。
  Do NOT trigger for 仅查询数据血缘/上下游依赖、仅查询现有API/数据源/离线任务详情、
  讨论非数仓相关的通用编程问题。
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python -m aqueduct *)
  - Bash(aqueduct *)
  - Bash(python -m src.aqueduct.tools.*)
  - mcp__dp-asset-mcp__*
tags:
  - data-engineering
  - etl
  - sql
  - code-generation
  - data-warehouse
version: "0.3.0"
---

# Aqueduct 框架调用技能

用于调用 `aqueduct` Python 框架，执行数据仓库开发全流程。

本 Skill 是 Claude Code 与 aqueduct 框架的桥接层。
所有实际工作（DAG 编排、校验引擎、模板生成、数据模型）由 aqueduct Python 代码完成。
Claude Code 提供：LLM 智能、MCP 工具（查表结构）、文件读写能力。

## 参考文档

- [架构参考](references/architecture.md) — 分层架构、模块说明、CLI 命令

## 工作模式

| 模式 | 触发方式 | 流程 |
|------|---------|------|
| **开发模式** | `aqueduct dev 需求文档` | Phase 1→6 全流程 |
| **审查模式** | `aqueduct review 线上.sql 变更.sql` | Phase 4.5→6 |
| **单步校验** | `aqueduct validate SQL文件` | 仅校验 SQL 规范 |

## 工作流概览

详细工作流请参考 `/data-developer` 技能，或直接执行：

```bash
# 开发模式 — 从需求文档到完整交付
aqueduct dev <requirement.md>

# 审查模式 — 校验 SQL 变更
aqueduct review <online.sql> <changed.sql>

# 单步校验 — 检查 SQL 规范
aqueduct validate <sql_file> [--strict]

# 项目状态
aqueduct status
```

## 框架架构

| 组件 | 职责 | 文件 |
|------|------|------|
| **LLM 层** | 多模型路由（Haiku/Sonnet/Opus 三档分工） | `src/aqueduct/llm/` |
| **Tools 层** | 9 个原子工具（校验、血缘、成本、DQC 等） | `src/aqueduct/tools/` |
| **Skills 层** | 7 大业务 Skill + Prompt 模板解耦 | `src/aqueduct/skills/` |
| **Engine 层** | DAG 工作流（开发/审查双模式） | `src/aqueduct/engine/` |
| **Memory 层** | 本体模型知识库 + 自动召回 | `src/aqueduct/memory/` |
| **MCP 层** | 标准 MCP 接口（可对接任何数据平台） | `src/aqueduct/mcp/` |

## 交付物清单

| 文件 | 用途 | 阶段 |
|------|------|------|
| 需求理解摘要.md | 需求解析与问题清单 | Phase 1 |
| 设计方案.md | 取数逻辑与字段映射 | Phase 2 |
| 表结构.sql | 目标表 DDL 定义 | Phase 3 |
| {需求名}.sql | 核心 ETL 逻辑 | Phase 4 |
| SQL校验报告.md | SQL 语法/逻辑/场景覆盖校验 | Phase 4 |
| 成本预警.md | 数据量预估与优化建议 | Phase 4 |
| 字段级血缘图.md | Mermaid 血缘拓扑图 | Phase 4 |
| {需求名}_审查报告.md | 代码审查结果 | Phase 4.5 |
| 数据质量测试.sql | DQC 测试用例 | Phase 5 |
| 质量仪表盘.md | 数据质量测试结果看板 | Phase 5 |
| Design.md | 完整设计文档 | Phase 6 |
| 交付总报告.md | 项目交付总报告 | Phase 6 |
| 知识沉淀.md | 业务规则与编码约定沉淀 | Phase 6 |
| knowledge/domains/{domain_id}.json | 语义模型（新业务域时创建） | Phase 6 |

用户通过 `.mcp.json` 配置自己的数据平台，无需修改代码。
