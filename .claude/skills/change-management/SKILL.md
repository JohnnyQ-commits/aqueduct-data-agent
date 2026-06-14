---
name: change-management
description: >
  需求变更管理技能。管理数据开发交付后的需求变更，确保变更可追溯、可审查、可回滚。
  DO trigger when 业务方提出新增字段、修改逻辑、删除字段等需求变更，
  用户提及"变更"、"新增字段"、"改逻辑"、"加指标"、"需求变了"、"CR"等关键词，
  或对比新旧需求文档发现差异时。
  Do NOT trigger for 首次开发（使用 data-developer）、仅审查已有SQL（使用审查模式）。
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python -m aqueduct *)
  - Bash(aqueduct *)
  - mcp__dp-asset-mcp__*
tags:
  - data-engineering
  - change-management
  - code-review
  - sql
version: "1.0.0"
---

# 需求变更管理技能 (Requirement Change Management Skill)

用于管理数据开发过程中的需求变更，确保变更可追溯、可审查、可回滚。

## 参考文档

- [变更流程详细参考](references/workflow.md) — 6 阶段工作流、变更类型、回归验证清单

## Context

本技能用于**需求已交付后**的变更管理场景。当业务方提出新增字段、修改逻辑、删除字段等变更时，
必须通过标准化的 CR（Change Request）流程进行管理，确保：
- 每次变更有独立目录和完整文档链
- 变更 SQL 经过影响分析 + 回归验证后才能合并
- 所有变更记录持久化保存，支持事后回溯

## Instructions

执行变更管理 6 阶段流程：

### Phase 1: 变更识别 (Change Identification)

1. 读取新需求文档，与原始需求文档逐字段对比
2. 识别：新增字段、修改字段、删除字段、逻辑变更
3. 输出：变更字段清单 + 逻辑差异说明

### Phase 2: 变更需求文档

在 `output/{需求名}/changes/CR-{NNN}_{简称}/` 下创建 `变更需求.md`：

| 章节 | 内容 |
|------|------|
| 基本信息 | CR编号、日期、需求编号、变更类型 |
| 新增字段清单 | 字段名(英文/中文)、类型、所属模块 |
| 字段逻辑详情 | 每个新字段的取数逻辑、计算公式 |
| 标签分档规则 | 标签值定义、区间划分 |
| 影响分析 | 新增/修改TMP表、修改ADS表、数据源变化 |
| 待确认问题 | 需要业务方确认的歧义点 |

### Phase 3: 变更 SQL

创建 `变更SQL.sql`：

1. **修改的TMP表**：DROP + CREATE 完整重建
2. **新增的TMP表**：DROP + CREATE
3. **修改的ADS表**：追加字段的 SELECT 片段 + 追加 JOIN 片段
4. **重跑顺序**：明确列出需要重跑的表及顺序

**规范**：
- 变更SQL必须可独立执行
- 新增TMP命名：`tmp_{库名}.tmp_{需求编号}_{主题}`
- 变更字段注释：`-- [CR-NNN] 字段说明`

### Phase 4: 变更审查

创建 `变更审查报告.md`：

| 审查维度 | 检查项 |
|---------|--------|
| 逻辑正确性 | 新增字段逻辑是否符合需求定义 |
| 回归风险 | 已有字段是否受影响、JOIN是否膨胀 |
| 性能影响 | 新增扫描量、新增JOIN数量 |
| 数据一致性 | 新增字段与已有字段的交叉验证 |
| 待确认问题 | 标记已确认/未确认状态 |

**审查结论**：
- ✅ **通过** — 可执行
- ⚠️ **条件通过** — 待确认问题后可执行
- ❌ **不通过** — 需修改后重新审查

### Phase 5: 合并执行

1. 合并变更SQL到主ETL SQL
2. 更新DDL表结构
3. 追加DQC测试用例
4. 同步更新 Design.md、知识沉淀.md
5. 按重跑顺序执行，运行回归验证

### Phase 6: 归档

1. 更新交付总报告（追加变更记录）
2. 更新提效看板
3. 更新语义模型（如有新实体变更）

## Input Schema

| Variable | Type | Description |
|----------|------|-------------|
| `requirement_name` | string | 原始需求名称（用于定位 output 目录） |
| `original_requirement` | string | 原始需求文档路径或内容 |
| `new_requirement` | string | 新需求文档路径或内容 |
| `change_description` | string | 变更描述（可选，人工补充） |

## Output Schema

```
output/{需求名}/changes/
├── CR-{NNN}_{简称}/
│   ├── 变更需求.md          ← Phase 2 产出
│   ├── 变更SQL.sql          ← Phase 3 产出
│   └── 变更审查报告.md      ← Phase 4 产出
```

## Constraints

- 每次变更**必须**创建独立 CR 目录，禁止直接修改主 SQL
- 变更SQL**必须**经过审查后才能合并到主 SQL
- 变更字段**必须**用 `-- [CR-NNN]` 注释标记来源
- 合并后**必须**执行回归验证清单（5 项）
- 所有变更记录**必须**持久化保存，不可删除

## 变更编号规则

```
CR-{三位序号}_{变更简称}
示例: CR-001_电商网点效能指标
      CR-002_新增客户分层标签
      CR-003_修复杂收占比逻辑
```

序号在 `output/{需求名}/changes/` 目录下递增。

## 变更类型分类

| 类型 | 说明 | 影响范围 | 示例 |
|------|------|---------|------|
| **新增字段** | 添加新的计算字段 | 新增TMP+修改ADS | 效能指标、城市字段 |
| **逻辑修改** | 修改已有字段的计算逻辑 | 修改TMP+修改ADS | 修改标签区间 |
| **删除字段** | 移除不再需要的字段 | 修改ADS SELECT | 废弃指标 |
| **数据源变更** | 更换或新增数据源 | 可能影响多个TMP | 切换维表 |
| **架构调整** | TMP表拆分/合并 | 重构TMP层 | 新增DWS层 |

## 回归验证清单

| # | 验证项 | SQL模板 |
|---|--------|---------|
| 1 | 主键唯一性不变 | `select count(*)=count(distinct pk) from {ADS表}` |
| 2 | 已有字段值不变 | 对比变更前后原有字段值 |
| 3 | 新增字段非空率 | `select count({新字段})/count(*) from {ADS表}` |
| 4 | 新增字段枚举值 | `select distinct {标签字段} from {ADS表}` |
| 5 | 数据行数不变 | `select count(*) from {ADS表}` |

## 与其他技能的关系

```
data-developer (首次开发)
    ↓ 交付后
change-management (本技能, 后续变更)
    ↓ 变更审查
data-developer Phase 4.5 (代码审查模式)
```

## Examples

**场景**：业务方要求在已有的"业务效能报表"中新增"电商网点效能"指标

**Phase 1 输出**：
```markdown
### 变更识别结果

| 变更项 | 类型 | 说明 |
|--------|------|------|
| 电商网点效能指标 | 新增字段 | 新增计算字段，需新建TMP表 |
| 城市维度 | 无变更 | 已有字段，不受影响 |
```

**Phase 3 输出（变更SQL.sql）**：
```sql
-- ============================================================
-- CR-001_电商网点效能指标
-- 变更类型: 新增字段
-- 日期: 2026-06-10
-- ============================================================

-- 1. 新增TMP表: 电商网点效能明细
DROP TABLE IF EXISTS tmp_dwd.tmp_sample_business_efficiency;
CREATE TABLE tmp_dwd.tmp_sample_business_efficiency AS
SELECT
    emp_code,
    -- [CR-001] 电商网点效能指标
    COUNT(CASE WHEN order_type = 'ecommerce' THEN 1 END) as ecommerce_order_cnt,
    SUM(CASE WHEN order_type = 'ecommerce' THEN amount END) as ecommerce_amount
FROM dwd.order_detail
WHERE inc_day = '${bizdate}'
GROUP BY emp_code;

-- 2. 修改ADS表: 追加字段
-- 在 SELECT 中追加:
--   t_ecom.ecommerce_order_cnt,  -- [CR-001] 电商网点订单数
--   t_ecom.ecommerce_amount      -- [CR-001] 电商网点金额
-- 追加 JOIN:
--   LEFT JOIN tmp_dwd.tmp_sample_business_efficiency t_ecom
--     ON t_main.emp_code = t_ecom.emp_code

-- 3. 重跑顺序:
--   1) tmp_dwd.tmp_sample_business_efficiency
--   2) ads.ads_sample_business_report
```
