# 数据开发自动化规范 (Data Development Automation Guidelines)

## 项目定位

本项目旨在通过自动化 Agent 实现数据仓库开发的标准化与工程化，覆盖从需求解析、架构设计、SQL 开发到质量验证的全生命周期。

## 技能引用

数据开发工作流定义在 `skills/data-developer.md`。用户提出数据开发需求时，激活该 skill 并按其中的 Phase 执行。

## 语义层模型

业务域语义定义在 `knowledge/semantic-model.json`。
- Phase 1 需求理解阶段，读取此文件判断需求属于哪个业务域
- Phase 3 SQL 开发时，严格参考对应业务域的语义层定义（字段映射、关联条件、过滤规则等）
- **追加原则**：新增业务域时，生成对应的语义层 JSON **追加到此文件的 JSON 数组中，绝不要覆盖已有内容**
- **合并沉淀**：当同一业务方向下积累 4~5 个语义条目后，与用户确认是否合并为该方向的领域级语义模型
- 如需求不属于任何已定义业务域，按通用规则开发，完成后询问用户是否沉淀为新的业务域定义

## 代码风格

参见 `docs/coding-style.md`，核心原则：
- 关键字全小写
- select 字段竖排4空格缩进
- where 条件紧凑
- join 与 on 分行
- 函数内逗号无空格
- 子查询优先于直接 join 表名

## 变量约定

- 系统变量格式：`$[time(yyyyMMdd,-1d)]`
- 分区字段：`inc_day`
- 业务日期字段：`day`（如排班表）

## 表命名规范

```
库.层级_业务_需求_区别字段
示例：dwd_demo.dwd_order_daily
```

- 层级前缀：ods/dwd/dws/ads/tmp
- 业务：sds/pd/gis 等业务域
- 粒度：di（日增量）/df（日全量）/hi/ho（小时）

## 空值处理规范

```sql
-- 数值字段：NVL 处理
SUM(NVL(order_amount, 0)) AS total_amount

-- 字符串字段：默认值替代
NVL(user_name, '-') AS user_name_cleaned

-- 除法必须判空和判零
IF(NVL(total_count, 0) = 0, 0, NVL(revenue, 0) / NVL(total_count, 0))
```

## SQL 红线

- ❌ 禁止 `SELECT *`（UNION ALL 合并场景除外）
- ❌ 禁止无分区过滤的查询
- ❌ 禁止未定义关联字段的 JOIN
- ❌ 禁止关键字大写

## 调度配置默认值

- 调度频率：T+1（每天一次）
- 调度时间：上游表产出后执行
- 分区变量：`inc_day = $[time(yyyyMMdd,-1d)]`
- 失败策略：告警通知 + 重试

## 数仓分层约定

| 层级 | 前缀 | 说明 |
|------|------|------|
| ODS | ods_ | 原始数据，不做加工 |
| DWD | dwd_ | 数据清洗、标准化、维度统一 |
| DWS | dws_ | 轻度汇总、聚合 |
| ADS | ads_ | 应用层结果表 |
| TMP | tmp_ | 临时中间表 |

## 文件命名

- 表结构：`表结构.sql`
- 核心SQL：`{需求名称}.sql`
- 测试用例：`数据质量测试.sql`
- 设计文档：`Design.md`
- 交付总报告：`{需求名称}_交付总报告.md`
