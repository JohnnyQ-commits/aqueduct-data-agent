---
name: data-developer
description: >
  Aqueduct 数据开发自动化全流程执行技能（默认模式）。
  从需求文档出发，在单次对话中自动完成需求澄清、表结构设计、ETL SQL开发、
  代码审查、DQC质量保障、交付沉淀六步闭环。
  DO trigger when 用户提供需求文档要求开发SQL（默认首选此技能）、
  自然语言描述数据开发需求（如"帮我开发这个需求"、"从需求生成SQL"、
  "数据开发"、"SQL开发"、"ETL开发"、"需求转SQL"）、
  发送需求文档路径。
  DO NOT trigger when 用户明确要求 CLI/管道模式（此时用 /aqueduct-dev）、
  仅查询表结构/血缘/API、仅做SQL规范校验、通用编程问题。
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python -m aqueduct *)
  - Bash(aqueduct *)
  - Bash(python -m src.aqueduct.tools.*)
  - Bash(python src/aqueduct/memory/history.py *)
  - mcp__dp-asset-mcp__*
tags:
  - data-engineering
  - etl
  - sql
  - code-generation
  - data-warehouse
  - full-pipeline
version: "0.4.0"
---

# Aqueduct 数据开发自动化技能

> 本 Skill 描述完整的 6 阶段数据开发自动化流程。
> 实际执行通过 `aqueduct` CLI 或逐阶段手动执行。

## 参考文档

- [工作流详细参考](references/workflow.md) — 各阶段详细说明、交付物清单、编码规范
- [SQL 开发规范](references/sql_standards.md) — **必读** — SQL 编码标准、命名规范、JOIN 规范、GROUPING SETS、多期间打平处理、性能优化
- [数据验证 Checklist](references/verification_checklist.md) — **必读** — 上线前临时表门禁（V1~V7 双场景验证模板）、非确定性对比规则、平台验证坑清单
- [SQL 模板](assets/sql_template.sql) — ETL SQL 文件头模板

## 核心原则

1. **先问再做**: Phase 1 确认需求理解，Phase 2 确认数据架构，不直接生成 SQL
2. **两个固定确认点**: Phase 1 需求确认、Phase 2 架构确认，其余阶段自动执行，不再中断
3. **高成本决策对齐**: 涉及大表全表扫描、跨域 JOIN 等先与用户确认
4. **自动化引擎优先**: 优先使用 aqueduct 的 Tools/Skills 而非手写

## 工作流

### Phase 1: 需求理解

1. 读取用户指定的需求文档路径
2. **历史交付物检索（强制）**：运行 `python src/aqueduct/memory/history.py --doc <需求文档路径>`（或直接传表名；venv 环境下等价命令为 `aqueduct search-history`），检索 output/ 历史交付物与 knowledge/ 知识库：
   - 命中历史交付 SQL → 读取其 insert 语句（目标表/分区表达式）与头部注释（调度节奏）、该目录的知识沉淀.md，**直接对齐产出节奏与口径，禁止对已开发表做推断**
   - 命中历史知识沉淀/语义模型 → 引用其中业务规则
   - 提示"未沉淀语义模型" → 记入待办，Phase 6 补建 knowledge/domains/*.json
3. 使用 MCP 工具查询需求中涉及的所有源表结构：
   - `dp_hive_table_get_detail` — 查询 Hive 表字段、类型、分区
   - `dp_mysql_get_detail` — 查询 MySQL 表结构
4. 读取 `knowledge/domains/*.json` 中匹配的业务域定义，对齐口径、关联关系和过滤规则
5. **源表验证**：对关键源表进行数据探查，将结果写入 Phase1 文档的"源表验证"章节：
   - 主键/唯一性检查（是否有重复）
   - 分区有效性（最新分区日期、数据量）
   - 关键字段值域分布（枚举值是否覆盖预期）
   - 空值/异常值比例
6. 识别歧义点，输出"需求理解摘要 + 问题清单"向用户确认
7. 用户确认后，**将用户回答回写到 Phase1 文档对应问题下方**（用引用块 `>` 标注 ✅ 已确认），形成完整的"问题 → 回答 → 结论"闭环记录
8. 进入 Phase 2

**输出**: `output/{需求名}/Phase1-需求理解摘要.md`（含源表验证 + 问题 + 回答 + 结论）

### Phase 2: 设计方案

1. 输出取数逻辑说明（数据来源、过滤条件、关联关系）
2. 输出源到目标的字段映射关系
3. 输出上下游依赖关系
4. **结构设计选型**：根据复用性、嵌套深度、数据量判断采用哪种结构模式（详见 [sql_standards.md §11](references/sql_standards.md)）：
   - 同一逻辑被 ≥2 张下游表复用 → 提取为 DWS 物化
   - **多张 ADS 表基于同一实体（人/订单），属性派生复杂** → 原子宽表（人×日），下游仅 WHERE + GROUP BY
   - 嵌套 ≥4 层 → 拆分 TMP 临时表
   - 其余情况 → 子查询派生表内联（默认）
5. 将设计方案写入文件
6. **停止点 — 数据架构确认**：向用户输出"数据流总览 + 关键决策点清单"，逐项确认是否需要调整
7. 用户确认后，将回答回写 Phase2 文档"方案确认"章节（引用块 `>` 标注 ✅ 已确认），进入 Phase 3

**关键决策点清单**（默认 6 项，简单需求允许省略不适用项，如无后端交互时去掉第 5 项）：

| # | 决策点 | 说明 |
|---|--------|------|
| 1 | 目标表粒度 | 主键设计、聚合层级，决定存储量与下游用法 |
| 2 | 分层路径 | ODS→ADS 直采 或 ODS→DWS→ADS，是否需要中间层沉淀 |
| 3 | 更新策略 | 每日增量追加 或 全量快照，影响历史回溯与重跑方式 |
| 4 | 源表取舍 | 哪些源表进入方案，多一张表多一层依赖和成本 |
| 5 | 数开/后端边界 | 数仓与后端各自承担的计算（如预警计算放哪侧） |
| 6 | 调度依赖 | 上游依赖与就绪时序，影响 SLA |

**确认后的分支处理**：

- 全部确认 → 回写确认记录，直接进入 Phase 3
- 局部调整（改口径、过滤条件、加字段）→ 修订设计方案 → 回写确认记录 → 进入 Phase 3（不再二次确认）
- 架构级调整（换主源表 / 换粒度 / 换分层）→ 修订方案 → **只对变化项再快速确认一轮** → 进入 Phase 3

**输出**: `output/{需求名}/Phase2-设计方案.md`（含"方案确认"章节）

### Phase 3: 表结构设计

1. 根据设计方案生成 DDL（CREATE TABLE 语句）
2. 规范：分区字段 `inc_day string`，格式 `YYYYMMDD`，存储格式 `PARQUET`
3. 调用 `ValidatorTool` 校验 DDL 规范性

**输出**: `output/{需求名}/Phase3-表结构.sql`

### Phase 4: SQL 开发

1. 编写核心 ETL SQL，遵循 [SQL 开发规范](references/sql_standards.md)（**必读**）
2. 调用 `ValidatorTool` 进行 SQL 校验
3. 调用 `EstimatorTool` 进行成本预估
4. 调用 `LineageTool` 生成血缘图

**输出**:
- `output/{需求名}/Phase4-{需求名}.sql`
- `output/{需求名}/Phase4-SQL校验报告.md`
- `output/{需求名}/Phase4-成本预警.md`
- `output/{需求名}/Phase4-字段级血缘图.md`

**SQL 开发检查清单**（详见 [sql_standards.md](references/sql_standards.md)）：
- [ ] 分区字段统一为 `inc_day`
- [ ] olap 集群表加了 `olap.` 前缀
- [ ] SQL 关键字全小写
- [ ] SELECT 字段竖排
- [ ] JOIN 子查询加了 DISTINCT
- [ ] 使用派生表模式（先过滤再 JOIN）
- [ ] GROUPING SETS 包含完整维度（code + name）
- [ ] parent_code 在最后一步统一处理
- [ ] 网点类型使用 5 种（示例业务）
- [ ] 除法用 NULLIF 保护
- [ ] 合并日期范围减少表扫描
- [ ] 每个临时表有排查注释

### Phase 4.5: 代码审查（审查模式入口）

1. 差异比对：逐行对比线上 vs 变更版本
2. 需求覆盖度验证
3. 潜在问题检查

**输出**: `output/{需求名}/Phase5-{需求名}_审查报告.md`

### Phase 5: 数据验证与质量保障（DQC）

> 核心原则：**没在真实数据上跑过的 SQL 不算完成**。详细规则见 [数据验证 Checklist](references/verification_checklist.md)（**必读**）。

**第一层：上线前门禁（强制）—— 临时表实测**：

1. 生成验证版 SQL：Phase 4 正式 SQL 宏替换为当日实际值 + CTAS 到 tmp 临时表（`tmp_库.表名_YYYYMM`）；权限预检，个人无权限且属现有逻辑的非变更表做哑替换（注释标明）
2. 执行验证 V1~V7（**先判定场景：迭代需求 vs 全新需求**，基准差异详见 checklist）：
   - **V1 结构不变性**: 迭代=行数/主键 vs 生产表（证明新增 JOIN 不膨胀）；全新=vs **驱动表理论行数** + 主键唯一性**首验**
   - **V2 内容质量**: 填充率 / 值域 / 可解析性（用目标消费者同款解析方式，如后端解 JSON 就用 JSON 函数验）；全新需求为**全字段首次画像**
   - **V3 值正确性**: 新字段 vs 源表逐项对账（须 100% 含空对空）；全新需求**必须加抽样人工核对**（人肉按需求口径手算 3~5 条——SQL 跑通 ≠ 口径正确）
   - **V4 向后兼容**（迭代）: 旧字段 vs 生产表（⚠️ `collect_list` 等非确定性字段按集合对比，逐位置对比会假差异）；全新需求替换为 **DDL 对齐验证**（insert 列序/类型 vs CREATE TABLE）
   - **V5 边界容量**: 字段长度 vs 链路存储上限
   - **V6 DQC 用例实测**: 下面的监控用例在临时表跑通后才算交付
   - **V7 量级合理性**（全新特有）: 总量 vs 业务量级直觉 / 相关表对比（粒度错误第一道防线）
3. 全部通过 → 输出"具备上线条件"结论；任一失败 → 回 Phase 4 修复后重验

**第二层：上线后监控 —— DQC 用例交付**：

4. 生成 DQC 测试用例（5 大类别）：唯一性（主键重复/空值）/ 业务反证（负数金额/不合理日期）/ 一致性（与源表总量/汇总值对比）/ 边界（极值/特殊字符）/ 波动（历史同期/突增突降）
5. 调用 `DQCTool` 解析测试用例并生成质量仪表盘；提醒将用例接入数据质量平台调度（未接入调度的用例只是文档，不构成持续监控）

**输出**:
- `output/{需求名}/Phase5-临时表数据验证报告.md`（V1~V7 验证结果，每个数字须写明口径，标明场景类型与所用基准）
- `output/{需求名}/Phase5-数据质量测试.sql`（已实测跑通的版本）
- `output/{需求名}/Phase5-质量仪表盘.md`

### Phase 6: 交付与沉淀

1. 生成 Design.md（完整设计文档）
2. 生成交付总报告
3. 生成知识沉淀文档
4. 更新/创建语义模型 JSON
5. 同步知识库索引 — 新业务域时在 `knowledge/INDEX.md` 表格追加一行，已有域更新版本号；或运行 `aqueduct knowledge sync` 一键重建（INDEX.md + 各域审计文档）

**输出**:
- `output/{需求名}/Phase6-Design.md`
- `output/{需求名}/Phase6-交付总报告.md`
- `output/{需求名}/Phase6-知识沉淀.md`
- `knowledge/domains/{domain_id}.json`（新业务域时创建）
- `knowledge/INDEX.md`（同步更新）

## Smart Fix 自动修复

开发过程中自动修复以下常见问题：
- `SELECT *` → 展开为具体字段
- 缺少分区过滤 → 添加 `WHERE inc_day = '${bizdate}'`
- 关键字大写 → 统一小写（select, from, where, left join）
- 除法未保护 → 添加 `NULLIF(divisor, 0)` + `NVL`

## SQL 优化与编码规范

### 1. 禁止 SELECT *

所有查询（包括子查询、派生表）必须显式列出字段，禁止使用 `SELECT *`。

```sql
-- ❌ 错误
select * from table_name

-- ✅ 正确
select
    field1,
    field2,
    field3
from table_name
```

### 2. 同源表多次查询优化

同一张表需要查询多次取不同列时，使用 `lateral view stack()` 列转行，减少表扫描次数。

```sql
-- ❌ 错误：3 次表扫描
select 'total' as channel, total_sales from table
union all
select 'hr' as channel, hr_sales from table
union all
select 'oa' as channel, oa_sales from table

-- ✅ 正确：1 次表扫描
select
    channel,
    sales
from table
lateral view stack(3,
    'total', total_sales,
    'hr', hr_sales,
    'oa', oa_sales
) stacked_data as channel, sales
```

### 3. 派生表过滤日期

环比同比计算时，日期过滤放在子查询 WHERE 中，不在 JOIN ON 条件中。

```sql
-- ❌ 错误：日期过滤在 JOIN 条件中
from table cur
left join table lag1d
    on cur.id = lag1d.id
    and lag1d.inc_day = 'T-2'
where cur.inc_day = 'T-1'

-- ✅ 正确：日期过滤在子查询 WHERE 中
from (
    select field1, field2, ...
    from table
    where inc_day = 'T-1'
) cur
left join (
    select field1, field2, ...
    from table
    where inc_day = 'T-2'
) lag1d
    on cur.id = lag1d.id
```

**优势：**
- 可读性更好，日期过滤逻辑集中
- 性能更优，先过滤再 JOIN，数据量更小

### 4. JOIN 条件必须完整

JOIN 条件必须包含联合主键的所有字段，避免数据错配。

```sql
-- ❌ 错误：只用了部分主键字段
on cur.org_code = lag1d.org_code

-- ✅ 正确：包含所有主键字段
on cur.org_level = lag1d.org_level
and cur.org_code = lag1d.org_code
and cur.channel = lag1d.channel
and cur.position = lag1d.position
```

**原因：** 如果缺少某些字段，会导致不同层级/渠道/岗位的数据错误匹配。

### 5. GROUPING SETS 语法规范

使用 GROUPING SETS 时，每个 grouping set 必须明确列出所有列，不能省略。

```sql
-- ❌ 错误：省略了部分列
group by
    t1.inc_day,
    t1.channel,
    grouping sets (
        (t2.dept_code),  -- 缺少 t1.inc_day, t1.channel
        ()
    )

-- ✅ 正确：每个 grouping set 都包含所有列
group by
    grouping sets (
        (t1.inc_day, t1.channel, t2.dept_code),
        (t1.inc_day, t1.channel)
    )
```

### 6. 注释格式规范

ETL SQL 文件的每个 STEP 必须包含结构化注释：

```sql
-- =============================================================================
-- STEP N: 临时表名称
-- 用途: 一句话说明这个临时表的用途
-- 定义: (复杂步骤) 列出关键计算逻辑
-- 计算思路: (可选) 说明实现思路
-- 排查点: 列出常见问题排查方向
-- =============================================================================
```

### 7. 文件命名规范

Phase 4 SQL 文件命名格式：`Phase4-{中文名}-{表名}.sql`

示例：
- `Phase4-人数变化-ads_personnel_count_di.sql`
- `Phase4-出勤效能-ads_attendance_efficiency_di.sql`

### 8. 先理解数据，再写代码（防止过度设计）

**核心原则**：写 SQL 前必须先理解源表的真实结构和数据范围，不要凭表名猜测。

**必须做的事**：
1. **先查询表结构**：用 MCP 工具（`dp_hive_table_get_detail` 等）查询表字段、类型、分区
2. **验证数据范围**：不确定表包含哪些层级的数据时，先查一下实际数据
3. **优先考虑简单方案**：能用一张表搞定的，不要写多表 JOIN + 复杂条件
4. **避免过度推理**：不要主观推断"这张表可能只用于某个层级"，先看数据再说

**❌ 典型错误**：
```sql
-- ❌ 错误：凭表名猜测，过度设计
-- 假设 dim_department_relation_df 只用于网点级
left join (...) pm
    on cur.org_code = pm.dept_code
    and cur.org_level = '3'    -- 不要加这个限制！
left join (...) dim            -- 为片区级额外 JOIN
    on cur.org_code = dim.division_code
    and cur.org_level = '2'

-- ✅ 正确：先查表结构，发现它包含所有层级
left join (...) pm
    on cur.org_code = pm.dept_code    -- 一张表搞定所有层级
```

**检查清单**（写 SQL 前问自己）：
- [ ] 我是否已经查询过所有源表的表结构？
- [ ] 我是否理解每张表的字段含义和数据范围？
- [ ] 我是否优先考虑了最简单的实现方案？
- [ ] 我的 JOIN 条件是否基于实际数据，而不是主观猜测？

## 语义模型规范

Phase 6 生成的 `knowledge/domains/{domain_id}.json` 须符合以下结构。

**单一数据源原则**: JSON 是业务域的唯一权威定义，`knowledge/INDEX.md` 由 `aqueduct knowledge sync` 从全部 JSON 自动聚合生成，不要手工编辑。

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
│   ├── Phase2-变更需求.md
│   ├── Phase3-变更SQL.sql
│   └── Phase4-变更审查报告.md
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
