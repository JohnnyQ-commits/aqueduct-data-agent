---
name: aqueduct-dev
description: >
  Aqueduct 数据开发一键启动入口。
  DO trigger when 用户提供需求文档要求开发SQL、提到"帮我开发这个需求"、
  "从需求生成SQL"、"走一遍管道"、"aqueduct dev"、"数据开发"、"SQL开发"、
  "ETL开发"、"需求转SQL"。
  Do NOT trigger for 仅查询表结构/血缘/API、仅做SQL规范校验、
  通用编程问题、变更管理（使用 /change-management）。
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python *)
  - Bash(pip *)
  - Bash(aqueduct *)
  - Bash(ls output/*)
  - Bash(dir output/*)
  - mcp__dp-asset-mcp__*
tags:
  - data-engineering
  - etl
  - sql
  - entry-point
version: "1.0.0"
---

# Aqueduct Dev — 管道启动入口

你是 Aqueduct 管道的**执行者**。你的职责是：确保环境就绪 → 启动管道 → 展示结果。

所有实际工作（SQL 生成、校验、审查、DQC）由 aqueduct Python 管道完成。
你不写 SQL，你不做分析——你**启动管道并呈现结果**。

## 执行流程

### Step 1: 环境就绪

验证虚拟环境已激活且 aqueduct 已安装：

```bash
python -c "import aqueduct; print(f'aqueduct {aqueduct.__version__}')"
```

如果失败：
1. 激活虚拟环境：`source .venv/bin/activate`（Windows: `.venv\Scripts\activate`）
2. 安装：`pip install -e ".[dev]"`
3. 再次验证

### Step 2: 启动管道

```bash
aqueduct dev {requirement_path}
```

如果用户提供了外部 SQL 文件：

```bash
aqueduct dev {requirement_path} --sql-file {sql_path}
```

### Step 3: Phase 1 确认

管道会在 Phase 1 后暂停。读取输出目录的 Phase 1 文件，向用户摘要展示：
- 源表、目标表、关键指标
- 等待用户确认后，管道自动继续 Phase 2-6

### Step 4: 展示产出物

管道完成后，列出 output 目录所有文件，按阶段分组：

| 阶段 | 文件 |
|------|------|
| Phase 3 | `Phase3-表结构.sql` — DDL |
| Phase 4 | `Phase4-{name}.sql` — ETL SQL / 校验报告 / 血缘图 / 成本预警 |
| Phase 4.5 | `Phase5-{name}_审查报告.md` — 审查结果 |
| Phase 5 | `Phase5-数据质量测试.sql` — DQC 用例 / 质量看板 |
| Phase 6 | `Phase6-Design.md` / 交付总报告 / 知识沉淀 |

如果有 errors，**重点标注**并分析原因，不要自行修复。

## 约束

- **绝对不要**自己读需求文档后直接写 SQL/DDL/Design — 必须走管道
- **绝对不要**跳过 Phase 4 的工具链校验（ValidatorTool / LineageTool / EstimatorTool）
- **绝对不要**忽略管道输出的 errors 列表
- **绝对不要**在管道失败时自己"帮忙修复" — 分析原因让用户决定
