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
1. **先问再做**：需求中存在歧义、标注"待补充"或有多种合理解读时，必须先与用户对齐，禁止自行假设。
2. **Phase 阶段确认**：每个 Phase 结束后必须获得用户确认，方可进入下一阶段，严禁跨阶段盲目推进。
3. **高成本决策对齐**：涉及存储方案变更、核心取数逻辑调整等改动面广的决策，必须显式征得用户同意。
4. **自动化引擎优先**：Phase 产出文件后，**必须立即运行对应自动化脚本**（校验/血缘/成本/DQC/提效），脚本输出纳入交付物。禁止跳过自动化引擎直接交付。

---

## 工作流 (Workflow)

按以下阶段逐步推进，确保每个阶段产出物符合规范。

### 模式选择
- **开发模式**：从 Phase 1 执行至 Phase 6（全流程）。
- **审查模式**：用户提供线上/变更版本 SQL 后，直接从 Phase 4.5 开始。

### Phase 1: 需求理解 (Requirement Understanding)
1. **输入分析**：读取设计文档/PRD。若无文档，引导用户填写 `templates/requirement.md`。
2. **知识引用**：读取 `knowledge/domains/*.json` 中匹配的业务域定义，对齐口径、关联关系和过滤规则。若为新业务域，标注"后续需创建 domain JSON"。
3. **歧义识别**：主动列出歧义点，输出"需求理解摘要 + 问题清单"向用户确认。
4. **源表核对**：使用 MCP 工具（如 `bdp_hive_table_get_detail`）查询字段、类型、分区，并与需求核对。

### Phase 2: 设计方案 (Design Specification)
1. **产出方案草稿**：包含取数逻辑说明、源到目标的字段映射、上下游依赖关系。
2. **用户对齐与双向同步**：
   - 输出方案，若用户直接在 `Design.md` 中修改了字段或关联关系。
   - **触发同步**：运行 `python scripts/sync_design.py <design_file> <ddl_file> <domain_json>`。
   - 自动更新 DDL 和知识库定义，确保设计与代码始终同步。
3. **用户最终确认**：确认无误后方可进入下一阶段。

### Phase 3: 表结构设计 (Schema Design)
1. **生成 DDL**：基于 `templates/ddl.sql` 模板生成 `CREATE TABLE` 语句。
2. **规范执行**：分区字段统一为 `inc_day`，格式 `YYYYMMDD`，存储格式默认 `PARQUET`。
3. **自动化血缘**：运行 `python scripts/gen_lineage.py <sql_file>`（若 SQL 已存在）或待 Phase 4 后统一执行。

### Phase 4: SQL 开发 (SQL Development)
1. **编码实现**：遵循 `docs/coding-style.md`。关键字全小写，字段竖排。
2. **架构选择**：简单场景用子查询，复杂场景使用 CTE (With 语法)。
3. **自动校验与智能修复 (Smart Fix)**：
   - **必须运行** `python scripts/validate_sql.py <sql_file> --json` 获取结构化问题列表。
   - **自动修复逻辑**：
     - 若存在 `SELECT *`：解析上游 DDL，自动替换为完整字段列表。
     - 若缺少分区过滤：根据元数据，自动添加默认分区过滤。
     - 若关键字大写：一键转换为全小写。
     - 若除法未判空：自动包裹 `nvl(..., 0)` 或 `CASE WHEN` 逻辑。
   - **用户确认**：将修复前后的差异展示给用户，获得显式确认后方可应用修复。
   - 若校验无 ERROR/WARN，跳过修复直接进入下一步。
4. **资源成本预估 (Cost Estimation)**：
   - **必须运行** `python scripts/estimate_cost.py <sql_file> <design_file>`。
   - 分析扫描量风险，自动在设计文档中生成预警报告。
   - 若风险等级为"🔴 高"，必须在交付前告知用户。
5. **自动化血缘联动 (Auto Lineage)**：
   - **必须运行** `python scripts/gen_lineage.py <sql_file> <design_file>`。
   - 表级血缘：自动生成源表到目标表的 Mermaid 关系图。
   - 字段级血缘：解析核心字段的映射关系，生成字段级 Mermaid 拓扑图。
   - 将可视化血缘信息自动插入设计文档的第十一章。

### Phase 4.5: 代码审查 (Code Review)
1. **差异比对**：逐行对比线上 vs 变更版本，识别所有逻辑变化。
2. **影响分析**：评估变更对下游表/任务的潜在影响。
3. **输出报告**：以表格形式呈现"需求项 | 是否满足 | 说明"，列出问题及修复建议。

### Phase 5: 数据质量保障 (DQC)
 1. **业务规则提取 (Metadata Analysis)**:
    - 重新审阅 Phase 1 需求及 `knowledge/domains/` 中对应 domain JSON 的 `business_rules` 字段。
    - 识别所有潜在的业务逻辑冲突点（如：逻辑互斥、时间交叉、数值合理区间）。
 2. **DQC 套件设计 (Suite Design)**:
    - **普适性校验**: 必须包含 唯一性 (Uniqueness)、时效性 (Timeliness)、引用一致性 (Referential Integrity) 和 波动监控 (Volatility)。
    - **格式规范**: 每个测试用例**必须**使用以下注释格式，否则 `check_data_quality.py` 无法解析：
      ```
      -- [分类-名称] 描述说明
      -- 权重: High/Medium/Low
      select ...
      -- 预期: 预期结果描述
      ```
      分类可选值：`唯一性`, `非空`, `一致性`, `边界`, `业务反证`, `波动`, `记录数`。
    - **业务定制反证**: 根据提取的规则编写 **Negative Testing** 用例，验证不该出现的数据确实没出现（如离职人员、非目标人群）。
 3. **自动化执行与闭环自愈 (DQC Loop)**:
    - **必须运行** `python scripts/check_data_quality.py <dqc_sql_file> <report_md>`。
    - **强制闭环**: 若报告中存在 `❌ FAILED` 项或健康得分低于 100，Agent **严禁直接交付**。
    - **故障自愈**: Agent 必须根据脚本输出的"修复建议"反查 ETL 代码，修复逻辑漏洞，并重复执行测试直到全绿通过。
    - 若为开发模式且数据尚未实际产出（无法真实执行 SQL），在交付总报告中如实标注"DQC 结果基于模拟执行，待数据产出后需重新运行"。

### Phase 6: 交付与沉淀 (Delivery & Knowledge Capture)
1. **完善设计文档**：按 `templates/design.md` 模板生成 `Design.md`，包含需求、取数逻辑、表结构、数据源、调度、质量、依赖、文件清单。**必须使用 .md 格式**。
2. **生成交付总报告**：按 `templates/report.md` 模板生成 `{需求名称}_交付总报告.md`（或 `交付总报告.md`），包含源表确认、数据流转分析、DQC 仪表盘、执行检查清单。
3. **知识沉淀文档**：生成 `知识沉淀.md`，记录业务规则、编码约定、枚举值、注意事项。
4. **语义模型归档**：
   - 若为**新业务域**：**必须创建** `knowledge/domains/{domain_id}.json` 文件（参考已有 domain JSON 结构）。
   - 若为**已有业务域**：更新对应 `knowledge/domains/{domain_id}.json` 中的实体、指标、关系。
   - 创建/更新 JSON 后，**必须运行** `python scripts/gen_semantic_doc.py` 同步生成 `knowledge/semantic-model.md`（人工审计用）。
5. **提效看板更新**：
   - **必须运行** `python scripts/analyze_productivity.py` 更新提效看板。
   - 将生成的 `PRODUCTIVITY_REPORT.md` 移动至输出目录或保留在根目录。
6. **最终交付物核对**（见下方 Phase 完成检查清单）。

---

## Phase 完成检查清单 (Phase Completion Checklist)

每个 Phase 执行完毕后，必须确认以下产出物已生成：

| Phase | 必须产出 | 必须运行的脚本 |
|-------|---------|---------------|
| Phase 1 | 需求理解摘要 + 问题清单 | — |
| Phase 2 | 设计方案草稿 | — |
| Phase 3 | 表结构.sql | `gen_lineage.py`（若SQL已存在） |
| Phase 4 | 核心SQL + validate_sql 校验报告 + 成本预警 + 血缘图 | `validate_sql.py`, `estimate_cost.py`, `gen_lineage.py` |
| Phase 5 | 数据质量测试.sql + DQC 质量仪表盘 | `check_data_quality.py` |
| Phase 6 | Design.md + 交付总报告.md + 知识沉淀.md + 语义模型JSON + 提效看板 | `gen_semantic_doc.py`, `analyze_productivity.py` |

---

## 输出规范 (Outputs)

| 文件 | 用途 | 阶段 |
|------|------|------|
| 表结构.sql | 目标表 DDL 定义 | Phase 3 |
| {需求名称}.sql | 核心 ETL 逻辑 | Phase 4 |
| 数据质量测试.sql | DQC 测试用例 | Phase 5 |
| Design.md | 完整设计文档（含血缘图+成本预估） | Phase 6 |
| 交付总报告.md | 项目交付总报告（含DQC仪表盘） | Phase 6 |
| 知识沉淀.md | 业务规则与编码约定沉淀 | Phase 6 |
| {需求名称}_审查报告.md | 代码审查报告（仅审查模式） | Phase 4.5 |
| knowledge/domains/{id}.json | 语义层模型定义 | Phase 6 |
| 提效看板.md | Agent 产品度量统计 | Phase 6 |

---

## 语义模型规范 (Semantic Model)

- **存储格式**: `knowledge/domains/{domain_id}.json`，一个业务域一个 JSON 文件。
- **聚合文档**: `knowledge/semantic-model.md` 由 `scripts/gen_semantic_doc.py` 自动从 `knowledge/domains/*.json` 聚合生成，**禁止手动编辑**。
- **JSON 必需字段**: `domain_id`, `name`, `description`, `entities`, `relationships`, `metrics`。可选字段：`computation_chains`, `derived_attributes`, `business_rules`, `filter_rules`。
- **新业务域创建步骤**:
  1. 创建 `knowledge/domains/{domain_id}.json` 文件
  2. 填充实体、关系、指标定义
  3. 运行 `python scripts/gen_semantic_doc.py` 更新 `semantic-model.md`
