---
name: data-developer
description: >
  Aqueduct 数据开发自动化全流程执行技能。
  从需求文档出发，自动完成需求澄清、表结构设计、ETL SQL开发、代码审查、DQC质量保障、交付沉淀六步闭环。
  DO trigger when 用户提供需求文档要求开发SQL、要求执行aqueduct全流程、
  提到"帮我开发这个需求"、"从需求生成SQL"、"走一遍aqueduct流程"。
  Do NOT trigger for 仅查询表结构/血缘/API、仅做SQL规范校验、通用编程问题。
allowed-tools:
  - Read
  - Write
  - Edit
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
  - full-pipeline
version: "0.3.0"
---

# Aqueduct 数据开发自动化技能

> 本 Skill 描述完整的 6 阶段数据开发自动化流程。
> 实际执行通过 `aqueduct` CLI 或逐阶段手动执行。

## 参考文档

- [工作流详细参考](references/workflow.md) — 各阶段详细说明、交付物清单、编码规范
- [SQL 模板](assets/sql_template.sql) — ETL SQL 文件头模板

## 核心原则

1. **先问再做**: Phase 1 必须向用户确认理解，不直接生成 SQL
2. **仅 Phase 1 确认**: 后续阶段自动执行，不再中断
3. **高成本决策对齐**: 涉及大表全表扫描、跨域 JOIN 等先与用户确认
4. **自动化引擎优先**: 优先使用 aqueduct 的 Tools/Skills 而非手写

## 工作流

### Phase 1: 需求理解

1. 读取用户指定的需求文档路径
2. 使用 MCP 工具查询需求中涉及的所有源表结构：
   - `dp_hive_table_get_detail` — 查询 Hive 表字段、类型、分区
   - `dp_mysql_get_detail` — 查询 MySQL 表结构
3. 读取 `knowledge/domains/*.json` 中匹配的业务域定义，对齐口径、关联关系和过滤规则
4. 识别歧义点，输出"需求理解摘要 + 问题清单"向用户确认
5. 用户确认后，自动进入 Phase 2

**输出**: `output/{需求名}/需求理解摘要.md`

### Phase 2: 设计方案

1. 输出取数逻辑说明（数据来源、过滤条件、关联关系）
2. 输出源到目标的字段映射关系
3. 输出上下游依赖关系
4. 将设计方案写入文件

**输出**: `output/{需求名}/设计方案.md`

### Phase 3: 表结构设计

1. 根据设计方案生成 DDL（CREATE TABLE 语句）
2. 规范：分区字段 `inc_day string`，格式 `YYYYMMDD`，存储格式 `PARQUET`
3. 调用 `ValidatorTool` 校验 DDL 规范性

**输出**: `output/{需求名}/表结构.sql`

### Phase 4: SQL 开发

1. 编写核心 ETL SQL，遵循 `CONTRIBUTING.md` 中的代码规范
2. 调用 `ValidatorTool` 进行 SQL 校验
3. 调用 `EstimatorTool` 进行成本预估
4. 调用 `LineageTool` 生成血缘图

**输出**:
- `output/{需求名}/{需求名}.sql`
- `output/{需求名}/SQL校验报告.md`
- `output/{需求名}/成本预警.md`
- `output/{需求名}/字段级血缘图.md`

### Phase 4.5: 代码审查（审查模式入口）

1. 差异比对：逐行对比线上 vs 变更版本
2. 需求覆盖度验证
3. 潜在问题检查

**输出**: `output/{需求名}/{需求名}_审查报告.md`

### Phase 5: 数据质量保障 (DQC)

1. 生成 DQC 测试用例（5 大类别）：
   - **唯一性**: 主键重复检查、空值检查
   - **业务反证**: 金额/数量为负、日期不合理
   - **一致性**: 与源表总量对比、汇总值对比
   - **边界**: 极值检查、空字符串/特殊字符
   - **波动**: 与历史同期对比、突增突降检测
2. 调用 `DQCTool` 解析测试用例并生成质量仪表盘

**输出**:
- `output/{需求名}/数据质量测试.sql`
- `output/{需求名}/质量仪表盘.md`

### Phase 6: 交付与沉淀

1. 生成 Design.md（完整设计文档）
2. 生成交付总报告
3. 生成知识沉淀文档
4. 更新/创建语义模型 JSON

**输出**:
- `output/{需求名}/Design.md`
- `output/{需求名}/交付总报告.md`
- `output/{需求名}/知识沉淀.md`
- `knowledge/domains/{domain_id}.json`（新业务域时创建）

## Smart Fix 自动修复

开发过程中自动修复以下常见问题：
- `SELECT *` → 展开为具体字段
- 缺少分区过滤 → 添加 `WHERE inc_day = '${bizdate}'`
- 关键字小写 → 统一大写
- 除法未保护 → 添加 `NULLIF(divisor, 0)` + `NVL`

## 语义模型规范

Phase 6 生成的 `knowledge/domains/{domain_id}.json` 须符合以下结构：

```json
{
  "domain_id": "unique_identifier",
  "domain_name": "业务域中文名称",
  "entities": [
    {
      "name": "实体名称",
      "table": "schema.table_name",
      "attributes": [{"name": "字段名", "type": "数据类型", "description": "说明"}],
      "primary_key": ["pk_field"]
    }
  ],
  "relationships": [
    {"from": "实体A", "to": "实体B", "type": "1:N", "description": "关系说明"}
  ],
  "metrics": [
    {"name": "指标名", "formula": "SUM/NVL/COUNT表达式", "description": "业务含义"}
  ]
}
```

## 快速执行

```bash
# 全流程开发
aqueduct dev <requirement.md>

# 审查模式
aqueduct review <online.sql> <changed.sql>

# 单步校验
aqueduct validate <sql_file> [--strict]
```

---

## 需求变更管理 (Post-Delivery Change Management)

需求交付后，业务方提出新增字段、修改逻辑等变更时，**必须使用 `/change-management` 技能**进行标准化管理。

### 变更流程

```
变更触发 → CR-NNN目录创建 → 变更需求文档 → 变更SQL → 变更审查 → 合并执行 → 归档
```

### 变更归档结构

```
output/{需求名}/changes/
├── CR-001_xxx/
│   ├── 变更需求.md
│   ├── 变更SQL.sql
│   └── 变更审查报告.md
├── CR-002_xxx/
│   └── ...
```

### 核心要求

- 每次变更**必须**建档（CR-NNN 目录）
- 变更 SQL **必须**经过审查后才能合并到主 SQL
- 变更记录支持完整回溯（谁提的、改了什么、影响什么）
- 交付总报告中记录所有变更历史

### 与其他技能的关系

```
data-developer (首次开发)
    ↓ 交付后
change-management (后续变更)
    ↓ 变更审查
data-developer Phase 4.5 (代码审查模式)
```

详见 [change-management SKILL.md](../change-management/SKILL.md)
