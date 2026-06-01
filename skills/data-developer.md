# 数据开发自动化技能 (Data Development Automation Skill)

用于执行数据仓库 SQL 开发、表结构设计及数据管道编写的自动化技能。

## 激活条件 (Trigger)
- 用户要求进行数据仓库 SQL 开发、表结构设计、数据管道编写。
- 提及关键词：`数据开发`、`SQL开发`、`数仓开发`、`数据管道`、`表结构设计`、`数据质量测试`、`DA设计`。

## 排除场景 (Skip)
- 仅查询数据血缘/上下游依赖。
- 仅查询现有 API、数据源或离线任务详情（直接使用相关工具）。
- 讨论非数仓相关的通用编程问题。

---

## 核心原则 (Principles)
1. **先问再做**：需求中存在歧义、标注“待补充”或有多种合理解读时，必须先与用户对齐，禁止自行假设。
2. **Phase 阶段确认**：每个 Phase 结束后必须获得用户确认，方可进入下一阶段，严禁跨阶段盲目推进。
3. **高成本决策对齐**：涉及存储方案变更、核心取数逻辑调整等改动面广的决策，必须显式征得用户同意。

---

## 工作流 (Workflow)

按以下阶段逐步推进，确保每个阶段产出物符合 [AGENT.md](file:///e:/data-agent/AGENT.md) 规范：

### 模式选择
- **开发模式**：从 Phase 1 执行至 Phase 6（全流程）。
- **审查模式**：用户提供线上/变更版本 SQL 后，直接从 Phase 4.5 开始。

### Phase 1: 需求理解 (Requirement Understanding)
1. **输入分析**：读取设计文档/PRD。若无文档，引导用户填写 [requirement.md](file:///e:/data-agent/templates/requirement.md)。
2. **知识引用**：检索 [semantic-model.json](file:///e:/data-agent/knowledge/semantic-model.json)，对齐业务域口径、关联关系和过滤规则。
3. **歧义识别**：主动列出歧义点，输出“需求理解摘要 + 问题清单”向用户确认。
4. **源表核对**：使用 MCP 工具（如 `bdp_hive_table_get_detail`）查询字段、类型、分区，并与需求核对。

### Phase 2: 设计方案 (Design Specification)
1. **产出方案草稿**：包含取数逻辑说明、源到目标的字段映射、上下游依赖关系。
2. **用户对齐**：输出方案，确认无误后方可进行 DDL 编写。

### Phase 3: 表结构设计 (Schema Design)
1. **生成 DDL**：基于 [ddl.sql](file:///e:/data-agent/templates/ddl.sql) 模板生成 `CREATE TABLE` 语句。
2. **规范执行**：分区字段统一为 `inc_day`，格式 `YYYYMMDD`，存储格式默认 `PARQUET`。

### Phase 4: SQL 开发 (SQL Development)
1. **编码实现**：遵循 [coding-style.md](file:///e:/data-agent/docs/coding-style.md)。关键字全小写，字段竖排。
2. **架构选择**：简单场景用子查询，复杂场景使用 CTE (With 语法)。
3. **自动校验**：运行 `python scripts/validate_sql.py <sql_file>`，确保无 SELECT *、无分区缺失、除法已判空。

### Phase 4.5: 代码审查 (Code Review)
1. **差异比对**：逐行对比线上 vs 变更版本，识别所有逻辑变化。
2. **影响分析**：评估变更对下游表/任务的潜在影响。
3. **输出报告**：以表格形式呈现“需求项 | 是否满足 | 说明”，列出问题及修复建议。

### Phase 5: 数据质量测试 (Data Quality Testing)
1. **生成用例**：基于 [dqc.sql](file:///e:/data-agent/templates/dqc.sql) 生成涵盖记录数校验、枚举值合法性、关联一致性等 7 大类测试。

### Phase 6: 交付与沉淀 (Delivery & Knowledge Capture)
1. **完善文档**：更新 [design.md](file:///e:/data-agent/templates/design.md) 并生成 [report.md](file:///e:/data-agent/templates/report.md) 交付总报告。
2. **知识归档**：主动询问并更新 [semantic-model.json](file:///e:/data-agent/knowledge/semantic-model.json)（新业务域）及 [AGENT.md](file:///e:/data-agent/AGENT.md)（新约定）。

---

## 输出规范 (Outputs)
- **表结构**：`表结构.sql`
- **核心逻辑**：`{需求名称}.sql`
- **审查报告**：`{需求名称}_审查报告.md`
- **交付报告**：`{需求名称}_交付总报告.md`
