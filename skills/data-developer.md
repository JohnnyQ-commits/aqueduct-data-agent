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
2. **用户对齐与双向同步**：
   - 输出方案，若用户在 `design.md` 中直接修改了字段或关联关系。
   - **触发同步**：运行 `python scripts/sync_design.py <design_file> <ddl_file> <domain_json>`。
   - 自动更新 DDL 和知识库定义，确保设计与代码始终同步。
3. **用户最终确认**：确认无误后方可进入下一阶段。

### Phase 3: 表结构设计 (Schema Design)
1. **生成 DDL**：基于 [ddl.sql](file:///e:/data-agent/templates/ddl.sql) 模板生成 `CREATE TABLE` 语句。
2. **规范执行**：分区字段统一为 `inc_day`，格式 `YYYYMMDD`，存储格式默认 `PARQUET`。

### Phase 4: SQL 开发 (SQL Development)
1. **编码实现**：遵循 [coding-style.md](file:///e:/data-agent/docs/coding-style.md)。关键字全小写，字段竖排。
2. **架构选择**：简单场景用子查询，复杂场景使用 CTE (With 语法)。
3. **自动校验与智能修复 (Smart Fix)**：
   - 运行 `python scripts/validate_sql.py <sql_file> --json` 获取结构化问题列表。
   - **自动修复逻辑**：
     - 若存在 `SELECT *`：解析上游 DDL，自动替换为完整字段列表。
     - 若缺少分区过滤：根据 `semantic-model.json` 中的分区定义，自动添加默认分区过滤（如 `inc_day = '${bdp.system.bizdate}'`）。
     - 若关键字大写：一键转换为全小写。
     - 若除法未判空：自动包裹 `nvl(..., 0)` 或 `CASE WHEN` 逻辑。
   - **用户确认**：将修复前后的差异（Diff）展示给用户，获得显式确认后方可应用修复。
4. **资源成本预估 (Cost Estimation)**：
   - 运行 `python scripts/estimate_cost.py <sql_file> <design_file>`。
   - 分析扫描量风险，自动在设计文档中生成预警报告。
   - 若风险等级为“🔴 高”，必须在交付前告知用户并寻求优化方案。
5. **自动化血缘联动 (Auto Lineage)**：
   - 运行 `python scripts/gen_lineage.py <sql_file> <design_file>`。
   - **表级血缘**：自动生成源表到目标表的 Mermaid 关系图。
   - **字段级血缘**：解析核心字段的映射关系，生成字段级 Mermaid 拓扑图。
   - 将可视化血缘信息自动插入设计文档的第十一章。

### Phase 4.5: 代码审查 (Code Review)
1. **差异比对**：逐行对比线上 vs 变更版本，识别所有逻辑变化。
2. **影响分析**：评估变更对下游表/任务的潜在影响。
3. **输出报告**：以表格形式呈现“需求项 | 是否满足 | 说明”，列出问题及修复建议。

### Phase 5: 数据质量保障 (DQC)
 1. **业务规则提取 (Metadata Analysis)**:
    - 重新审阅 Phase 1 需求及 `knowledge/domains/` 中的 `business_rules`。
    - 识别所有潜在的业务逻辑冲突点（如：逻辑互斥、时间交叉、数值合理区间）。
 2. **DQC 套件设计 (Suite Design)**:
    - **普适性校验**: 必须包含 唯一性 (Uniqueness)、时效性 (Timeliness)、引用一致性 (Referential Integrity) 和 波动监控 (Volatility)。
    - **权重配置**: 在 SQL 注释中使用 `-- 权重: High/Medium/Low` 定义项的重要程度。
    - **业务定制反证**: 根据提取的规则编写 **Negative Testing** 用例，验证不该出现的数据确实没出现（如离职人员、非目标人群）。
 3. **自动化执行与闭环自愈 (DQC Loop)**:
    - 运行 `python scripts/check_data_quality.py <dqc_sql_file> <report_md>`。
    - **强制闭环**: 若报告中存在 `❌ FAILED` 项或健康得分低于 100，Agent **严禁直接交付**。
    - **故障自愈**: Agent 必须根据脚本输出的“修复建议”反查 ETL 代码，修复逻辑漏洞，并重复执行测试直到全绿通过。

### Phase 6: 交付与沉淀 (Delivery & Knowledge Capture)
1. **完善文档**：更新 [design.md](file:///e:/data-agent/templates/design.md) 并生成 [report.md](file:///e:/data-agent/templates/report.md) 交付总报告。
2. **知识归档**：主动询问并更新 [semantic-model.json](file:///e:/data-agent/knowledge/semantic-model.json)。
3. **文档同步**：更新 JSON 后必须运行 `python scripts/gen_semantic_doc.py` 同步生成 [semantic-model.md](file:///e:/data-agent/knowledge/semantic-model.md)，确保人工可审计。

---

## 输出规范 (Outputs)
- **表结构**：`表结构.sql`
- **核心逻辑**：`{需求名称}.sql`
- **审查报告**：`{需求名称}_审查报告.md`
- **交付报告**：`{需求名称}_交付总报告.md`
