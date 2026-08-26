# Aqueduct SQL 开发规范

> 本规范适用于所有 Hive/Spark SQL ETL 开发
> 版本: v1.0 (2026-08-06)

## 核心原则

1. **可读性优先**: 代码结构清晰，易于理解和维护
2. **性能优化**: 减少表扫描次数，避免数据膨胀
3. **标准化**: 统一命名、格式、模式
4. **可排查性**: 每个临时表都应该可以独立验证

---

## 1. 命名规范

### 1.1 分区字段
- **统一使用 `inc_day`**，类型 `string`，格式 `YYYYMMDD`
- ❌ 禁止使用：`cur_date`, `data_date`, `riqi`, `dt`, `biz_date`（除非源表本身如此）

### 1.2 集群前缀
- **olap 集群表必须加 `olap.` 前缀**
- ✅ `olap.dwd_demo.emp_attendance_detail`
- ✅ `olap.dwd_demo.emp_duty_detail_di`
- ❌ `dwd_demo.emp_attendance_detail`

### 1.3 临时表命名
- **数据库名**：`tmp_demo`（不是 `dw_demo`）
- **表名格式**：`tmp_{业务域}_{功能描述}`
- ✅ `tmp_demo.tmp_diagnosis_dim_dept`
- ✅ `tmp_demo.tmp_attendance_dept_stats`
- ❌ `dw_demo.tmp_diagnosis_dim_dept`

### 1.4 维度表来源
- **必须用专用维度表** `dim.dim_dept_info_df`
- ❌ 不要用业务表构建维度（会导致数据缺失）
- ✅ `from dim.dim_dept_info_df where inc_day = '20260806'`
- ❌ `from olap.dwd_demo.emp_attendance_detail`（考勤表）
- **复用原则**：多个 SQL 需要相同维度表时，复用已创建的，不重复建表

---

## 2. 代码格式规范

### 2.1 SQL 关键字
- **全部小写**：`select`, `from`, `where`, `group by`, `join`, `case when` 等
- ❌ `SELECT`, `FROM`, `WHERE`

### 2.2 SELECT 字段格式
- **所有字段必须竖排**（包括子查询、CTE、JOIN）
- ✅
  ```sql
  select
      field1,
      field2,
      field3
  from table
  ```
- ❌
  ```sql
  select field1, field2, field3 from table
  ```

### 2.3 缩进
- 使用 4 个空格缩进
- 子查询、JOIN、CASE WHEN 都要缩进

---

## 3. JOIN 规范

### 3.1 DISTINCT 去重
- **JOIN 子查询必须加 DISTINCT**（除非明确知道不会重复）
- 防止数据膨胀
- ✅
  ```sql
  inner join (
      select distinct dept_code, area_code, dept_type_name
      from dim_table
  ) d on ...
  ```
- ❌
  ```sql
  inner join (
      select dept_code, area_code, dept_type_name
      from dim_table
  ) d on ...
  ```

### 3.2 派生表模式（先过滤再 JOIN）
- **所有 JOIN 都应该用派生表包装**
- 日期过滤放在子查询 WHERE 中，不在 JOIN ON 中
- ✅
  ```sql
  from (
      select * from table1 where inc_day = '20260806'
  ) t1
  inner join (
      select * from table2 where inc_day = '20260806'
  ) t2 on t1.id = t2.id
  ```
- ❌
  ```sql
  from table1 t1
  inner join table2 t2
      on t1.id = t2.id
      and t2.inc_day = '20260806'
  ```

### 3.3 过滤条件下推
- **右表过滤条件必须推入子查询**
- LEFT JOIN + WHERE 右表字段 → INNER JOIN
- ✅
  ```sql
  inner join (
      select * from right_table
      where status = 'active'
  ) r on l.id = r.id
  ```
- ❌
  ```sql
  left join right_table r
      on l.id = r.id
  where r.status = 'active'
  ```

### 3.4 先过滤再 JOIN（派生表模式）
- **所有源表查询都必须先用子查询过滤，再 JOIN**
- 日期过滤、业务过滤都要放在子查询的 WHERE 中
- ✅
  ```sql
  from (
      select inc_day, dept_code, weight, num
      from olap.dwd_demo.daily_volume_info
      where inc_day >= '20260801'  -- 日期过滤在子查询中
      and inc_day <= '20260806'
  ) d
  inner join (
      select distinct dept_code
      from tmp_demo.tmp_attendance_dim_dept
  ) dh
      on d.dept_code = dh.dept_code
  ```
- ❌
  ```sql
  from olap.dwd_demo.daily_volume_info d
  inner join (
      select distinct dept_code
      from tmp_demo.tmp_attendance_dim_dept
  ) dh
      on d.dept_code = dh.dept_code
  where d.inc_day >= '20260801'  -- 日期过滤在外部 WHERE（错误！）
  and d.inc_day <= '20260806'
  ```

---

## 4. GROUPING SETS 规范

### 4.1 完整维度
- **GROUPING SETS 必须包含完整维度（code + name）**
- ✅
  ```sql
  grouping sets (
      (inc_day, channel, dept_code, dept_name, division_code, division_name),
      (inc_day, channel, division_code, division_name),
      (inc_day, channel)
  )
  ```
- ❌
  ```sql
  grouping sets (
      (inc_day, channel, dept_code),
      (inc_day, channel, division_code),
      (inc_day, channel)
  )
  ```

### 4.2 派生表包装
- **LEFT JOIN 不能直接参与 GROUPING SETS**（Calcite 限制）
- 必须先 JOIN 成派生表，再 GROUP BY
- ✅
  ```sql
  select ... from (
      select t1.*, t2.name
      from table1 t1
      inner join table2 t2 on t1.id = t2.id
  ) sub
  group by grouping sets (...)
  ```
- ❌
  ```sql
  select t1.*, t2.name
  from table1 t1
  left join table2 t2 on t1.id = t2.id
  group by grouping sets (...)
  ```

---

## 5. 组织层级规范

### 5.1 网点类型（示例业务）
- **5种网点类型**：
  1. 标准营业点
  2. 大型营业点
  3. 项目营业点
  4. T2营业点
  5. 营业站
- ❌ 不要包含：合作点、项目营业部

### 5.2 组织层级编码
- `org_level = '3'`: 网点级
- `org_level = '2'`: 片区级
- `org_level = '1'`: 地区级
- `org_level = '0'`: 总部级

### 5.3 parent_code 处理
- **统一在最后一步处理**
- 使用单个 LEFT JOIN 到 `dim_department_relation_df`
- ✅
  ```sql
  -- 最终输出步骤
  left join (
      select distinct dept_code, dept_code_parent as parent_code
      from dw_demo.dim_department_relation_df
  ) pm on cur.org_code = pm.dept_code
  ```
- ❌ 不要在中间步骤处理 parent_code（会导致复杂的 CASE WHEN 或多个 JOIN）

---

## 6. 性能优化规范

### 6.1 合并日期范围
- **一次性处理多个期间**，减少源表扫描次数
- ✅
  ```sql
  select
      case
          when inc_day >= '20260801' and inc_day <= '20260806' then 'cur'
          when inc_day >= '20260701' and inc_day <= '20260731' then 'lm'
      end as period,
      ...
  from table
  where (
      (inc_day >= '20260801' and inc_day <= '20260806')
      or
      (inc_day >= '20260701' and inc_day <= '20260731')
  )
  group by
      case
          when inc_day >= '20260801' and inc_day <= '20260806' then 'cur'
          when inc_day >= '20260701' and inc_day <= '20260731' then 'lm'
      end
  ```
- ❌ 不要为每个期间写单独的查询

### 6.2 lateral view stack()
- **列转行减少表扫描**
- 用于将多个字段拆分成多行
- ✅
  ```sql
  select
      dept_code,
      channel,
      headcount
  from table
  lateral view stack(3,
      'total', total_headcount,
      'hr', hr_headcount,
      'oa', pmp_headcount
  ) stacked_data as channel, headcount
  ```

### 6.3 禁止使用 CTE（WITH 子句）

- **本项目全面禁止 CTE**，无论复杂度
- 替代方案：
  - **子查询派生表**：逻辑在当前 SQL 内复用，优先用子查询内联
  - **TMP 临时表**：逻辑被多张下游表复用，或嵌套过深需要分步，用 `DROP + CREATE` 临时表
- 原因：CTE 在 Hive 中常被展开为重复子查询，不利于性能控制；子查询/TMP 语义更明确

```sql
-- ❌ 禁止：CTE
with base as (
    select ... from table1
),
metrics as (
    select ... from base
)
insert overwrite table target
select ... from base left join metrics ...

-- ✅ 正确：子查询派生表（推荐）
insert overwrite table target
select
    b.field1,
    m.field2
from (
    select ... from table1
) b
left join (
    select ... from table1
) m on b.id = m.id

-- ✅ 正确：TMP 临时表（复用或复杂场景）
drop table if exists tmp_xxx.tmp_base;
create table tmp_xxx.tmp_base stored as parquet as
select ... from table1;

insert overwrite table target
select ... from tmp_xxx.tmp_base ...;
```

### 6.4 系数配置表模式（业务给定固定系数）

**适用场景**: 业务方按月份/年份/区域给出固定系数（运营天数、费率、权重参数等），
ETL 需要在计算中引用这些系数。

**反模式 1**: 内联 CASE WHEN + 对常量动态计算
```sql
-- ❌ 把系数写成一长串 CASE WHEN, 还对常量做动态计算
select
    case
        when substr(inc_day,1,6) = '202501' then 25
        when substr(inc_day,1,6) = '202502' then 25
        ...
    end as op_days,
    dayofmonth(last_day(...))  -- ❌ 每月天数固定, 不必动态算
        as natural_days
from source
```

**反模式 2**: 按期间多路 UNION ALL, 每路单独读源表
```sql
-- ❌ 每个期间单独读源表
select 'cur' as period, ..., sum(weight)
from src where inc_day >= cur_start and inc_day <= cur_end
union all
select 'm1', ..., sum(weight)
from src where inc_day >= m1_start and inc_day <= m1_end
union all ...
```

**正确做法**: 独立配置表 + 源表单次读 + JOIN
```sql
-- STEP N: 配置表 (系数硬编码, 自然天数固定常量)
drop table if exists tmp_xxx.tmp_xxx_cfg_$[time(yyyyMMdd,-1d)];
create table tmp_xxx.tmp_xxx_cfg_$[time(yyyyMMdd,-1d)] stored as parquet as
select
    inc_month,
    op_days,
    natural_days,
    cast(op_days as double) / natural_days   as op_ratio
from (
    -- 直接硬编码业务方给的系数 + 每月固定自然天数
    select '202501' as inc_month, 25 as op_days, 31 as natural_days
    union all select '202502', 25, 28
    ...
) t;

-- STEP N+1: 源表单次读 + JOIN (结合 §6.5 先聚合再 JOIN)
select
    dept_code,
    substr(inc_day, 1, 6)                                    as inc_month,
    month_weight
        / nullif(month_emp_count, 0)
        / nullif(c.op_ratio, 0)                              as weight_eff
from (
    -- 先聚合到月
    select zone_code as dept_code, substr(inc_day,1,6) as inc_month,
           sum(weight) as month_weight,
           count(distinct emp_no) as month_emp_count
    from source
    where inc_day >= '$[time(yyyyMM01,-3M)]'   -- 最大区间一次覆盖所有期间
      and inc_day <= '$[time(yyyyMMdd,-1d)]'
    group by zone_code, substr(inc_day, 1, 6)
) s
inner join (
    select inc_month, op_ratio
    from tmp_xxx.tmp_xxx_cfg
) c on s.inc_month = c.inc_month;
```

**要点**:
- 配置独立成表: 系数放配置表, 主查询只 JOIN, 不重复 CASE WHEN
- 硬编码常量: 每月天数 (31/28/31/30...) 是固定常量, 业务给的系数直接照抄
- 单次读源表: 最大区间一次覆盖, 通过 `substr(inc_day,1,6)` 派生期间键
- 下游兼容: 如需符号化期间名 (cur/m1/m2), 外层再包 CASE WHEN 映射

### 6.5 先聚合再 JOIN (系数型配置表, 慎用)

**适用场景**: 明细表需要 JOIN 一张粗粒度配置表, 且配置字段只是纯粹的系数
(标量乘除数, 如月度 op_ratio、年度汇率), 在同一聚合组内取值唯一。

**核心问题** (应用前先回答):
> 配置字段在 GROUP BY 之后, 对于每一个聚合组是否只有一个值?
> - **是** (如 op_ratio 按月唯一, 一个月份只有一个系数) → 适合先聚合再 JOIN
> - **否** (如每个员工有自己的权重, 同一月份内有多个值) → 必须先 JOIN 再聚合

**反模式**: 先 JOIN 再 GROUP BY (当配置是系数时)
```sql
-- ❌ 每条明细都去 JOIN, op_ratio 被复制到每一行
select
    dept_code,
    sum(weight) / count(distinct emp_no) / nullif(c.op_ratio, 0)
from detail d
inner join cfg c on substr(d.inc_day,1,6) = c.inc_month
group by dept_code, substr(d.inc_day,1,6), c.op_ratio
```

**正确做法**: 先 GROUP BY 到配置表粒度, 再 JOIN
```sql
-- ✅ JOIN 输入是聚合后的少量行, "先算出月累计, 再套系数"
select
    d.dept_code,
    d.month_weight
        / nullif(d.month_emp_count, 0)
        / nullif(c.op_ratio, 0)                              as weight_eff
from (
    select
        zone_code                                            as dept_code,
        substr(inc_day, 1, 6)                                as inc_month,
        sum(weight)                                          as month_weight,
        count(distinct emp_no)                               as month_emp_count
    from detail
    group by zone_code, substr(inc_day, 1, 6)
) d
inner join cfg c on d.inc_month = c.inc_month
```

**不能套用本规则的场景** (必须先 JOIN 再聚合):
- **配置字段参与 GROUP BY 维度**: 比如按配置表的"区域"聚合, 而区域字段只存在于配置表
- **配置字段影响过滤/分段**: 比如按配置表类别筛选后再聚合, 必须先 JOIN 才能知道该过滤哪些行
- **配置字段在同一聚合组内有多个不同值**: 比如每个员工有自己的权重, 聚合时权重必须参与计算
- **配置字段和聚合函数交织**: 比如 `sum(weight * config_per_row)`, 配置是行级乘数, 不能提到聚合后

**判断口诀**: "系数可后乘, 维度要先 JOIN"
- 系数 (乘除数, 同组内唯一) → 后乘, 先聚合再 JOIN
- 维度 (分组/过滤/行级乘数) → 先 JOIN 再聚合

### 6.6 消除 count(distinct) (两层聚合模式)

**适用场景**: 需要同时计算聚合指标 (sum/avg) 和去重计数 (count distinct X)
在同一个 GROUP BY 中。

**问题**: Hive/Spark 的 count(distinct X) 实现通常需要额外 shuffle:
1. 先按 GROUP BY key shuffle, 做部分聚合
2. 再按 X shuffle, 做全局去重
3. 再按 GROUP BY key shuffle, 合并结果

当数据量大时, 这个多次 shuffle 是性能瓶颈。

**优化**: 拆成两层聚合, 用 count(*) 替代 count(distinct):
```sql
-- ❌ 单步: 一次 GROUP BY 同时算 sum 和 count(distinct)
select
    dept_code,
    sum(weight) as total_weight,
    count(distinct employee_no) as emp_count
from source
group by dept_code
-- 代价: count(distinct) 触发额外 shuffle 按 employee_no 全局去重

-- ✅ 两步: 先去重到 (dept, employee), 再聚合
-- 第一层: 按 (dept, employee) 聚合, 每个员工每个 dept 只剩一行
select
    dept_code,
    employee_no,
    sum(weight) as emp_weight
from source
group by dept_code, employee_no

-- 第二层: 按 dept 聚合, count(*) = 员工数 (因为已去重)
select
    dept_code,
    sum(emp_weight) as total_weight,
    count(*) as emp_count
from first_layer
group by dept_code
```

**原理**:
- 第一层的 GROUP BY (dept, employee) 已经去重了 employee (每个 employee 每个 dept 只有一行)
- 第二层的 count(*) 直接数行数 = 数员工数
- 避免了 count(distinct) 的额外 shuffle

**配套做法**:
- 中间结果建议落盘为临时表 (可独立验证, 减少单次 shuffle 数据量)
- 第一层的聚合粒度必须包含所有需要去重的字段 + 所有 GROUP BY key
- 如果只需要 count(distinct), 不需要其他聚合, 单步 count(distinct) 也可以接受

**典型场景**:
- 月累计重量 + 月累计人数 (本项目的效能计算)
- 部门总销售额 + 部门客户数
- 任何 "聚合指标 + 去重计数" 并存的需求

---

## 7. 环比同比规范

### 7.1 计算公式
- **DoD (日环比)**: `(today - yesterday) / nullif(yesterday, 0)`
- **MoM (月环比)**: `(today - last_month) / nullif(last_month, 0)`
- **YoY (年同比)**: `(today - last_year) / nullif(last_year, 0)`

### 7.2 除法保护
- **除法必须用 NULLIF 保护**
- ✅ `a / nullif(b, 0)`
- ❌ `a / b`

### 7.3 排名函数
- **sort_* 字段使用 row_number()**
- PARTITION BY 要包含 org_level 和 parent_code
- ✅
  ```sql
  row_number() over (
      partition by org_level, parent_code, channel, position
      order by metric desc nulls last
  ) as sort_metric
  ```

---

## 8. 排查点规范

### 8.1 临时表验证
- **每个临时表都应该可以独立验证**
- 在注释中提供验证 SQL
- ✅
  ```sql
  -- 排查: select org_level, channel, sum(headcount) from tmp group by 1,2
  ```

### 8.2 常见问题检查
- 主键/唯一性检查
- 分区有效性检查
- 关键字段值域分布
- 空值/异常值比例

---

## 9. 文件组织规范

### 9.1 SQL 文件结构
```sql
-- ============================================================================
-- 文件头注释（目标表、数据来源、指标说明）
-- ============================================================================

-- STEP 1: 临时表名称 + 用途说明
-- ============================================================================
drop table if exists ...;
create table ... stored as parquet as
select ...
;

-- STEP 2: ...
```

### 9.2 执行流程文档
- 每个 SQL 文件配套执行流程文档
- 包含：流程图、依赖关系、并行配置、临时表清理

---

## 10. 多期间处理规范（打平为列）

### 10.1 核心原则：低基打平，高基留行

**period/date_range 等基数 ≤ 10 的维度，在聚合阶段直接作为 CASE WHEN 列展开，禁止用 period 行 + 后期 JOIN 组装。**

判断标准：
- **低基维度（≤10 个值）**：period（cur/dod/cur_cum/mom_cum）、date_range（当月/上月）、channel（2-3 个值）→ **打平为列**
- **高基维度（≥100 个值）**：dept_code、employee_no、loginid → **保持为行**

### 10.2 正确做法：一步到位生成宽表

每张源表在聚合阶段直接打出所有期间的列，不产生 period 行：

```sql
-- ✅ 正确：period 维度打平为列，一张宽表
select
    dept_code,
    -- 无效能人数：各期间
    sum(case when inc_day = '${t1_day}' then zero_flag else 0 end)    as zero_eff_cur,
    sum(case when inc_day = '${t2_day}' then zero_flag else 0 end)    as zero_eff_dod,
    sum(case when inc_day >= '${m0_first_day}' and inc_day <= '${t1_day}'
        then zero_flag else 0 end)                                    as zero_eff_cur_cum,
    sum(case when inc_day >= '${m1_first_day}' and inc_day <= '${month_same_day}'
        then zero_flag else 0 end)                                    as zero_eff_mom_cum,
    -- 出勤人数：各期间
    sum(case when inc_day = '${t1_day}' then hr_on_duty else 0 end)  as hr_on_duty_cur,
    sum(case when inc_day = '${t2_day}' then hr_on_duty else 0 end)  as hr_on_duty_dod
from (...)
group by dept_code
```

### 10.3 反模式：窄表 + 高维联结

```sql
-- ❌ 反模式：period 作为行，最后用大量 JOIN 拼装
-- STEP 2: zero_eff (dept × period)
-- STEP 3: hr_cargo (dept × period)
-- STEP 4: hr_base (dept × period)
-- STEP 5: CROSS JOIN periods + LEFT JOIN × 3 → GROUPING SETS
-- STEP 6: LEFT JOIN org_agg × 5 (按 period 拆分自联)
```

问题：
- 每个窄表只有 1 个 period 维度，需要 4+ 个 JOIN 才能拼回完整数据
- JOIN 条件遗漏 period → 数据丢失（如 7405 行 period=NULL 的问题）
- 修改一个指标需要追多条链路，维护困难

### 10.4 环比/同比计算：用列间运算，禁止 period 自联

```sql
-- ✅ 正确：宽表中列间直接计算
select
    org_level,
    org_code,
    zero_eff_cur,
    round(
        (zero_eff_cur - zero_eff_dod) / nullif(zero_eff_dod, 0), 4
    ) as zero_eff_dod_rate,
    round(
        (zero_eff_cur_cum - zero_eff_mom_cum) / nullif(zero_eff_mom_cum, 0), 4
    ) as zero_eff_mom_rate
from org_agg
```

```sql
--  错误：对同一张表按 period 拆分后 LEFT JOIN
from (select ... where period = 'cur') cur
left join (select ... where period = 'dod') dod
    on cur.org_code = dod.org_code
left join (select ... where period = 'cur_cum') cur_cum
    on cur.org_code = cur_cum.org_code
```

### 10.5 中间表设计规范

**按"源表粒度"拆分，不按"指标×期间"拆分。**

| 做法 | 说明 |
|------|------|
| ✅ 一张源表 → 一张宽中间表 | 包含该源表产出的所有指标的所有期间列 |
| ❌ 一张源表 → 多张窄中间表 | 按 metric × period 拆分，最后靠 JOIN 拼装 |
| ✅ 不同源表的中间表按 dept JOIN 一次 | 只有 1 次 JOIN，按关联键 |
| ❌ 5+ 张窄表 + CROSS JOIN + LEFT JOIN × 3 | 高维联结，容易出 bug |

### 10.6 禁止 CROSS JOIN 驱动维度骨架

```sql
--  禁止：用 CROSS JOIN 造 dept × period 骨架行
from dim_dept d
cross join (select distinct period from all_periods) dp
left join metric on d.dept_code = metric.dept_code and dp.period = metric.period

-- ✅ 正确：在聚合阶段用 CASE WHEN 列打平，不造骨架行
select
    d.dept_code,
    sum(case when period = 'cur' then metric_val else 0 end) as metric_cur,
    sum(case when period = 'dod' then metric_val else 0 end) as metric_dod
from dim_dept d
left join metric on d.dept_code = metric.dept_code
group by d.dept_code
```

CROSS JOIN 只在确实需要笛卡尔积时使用（如维度组合枚举），不用于补齐缺失维度值。

---

## 11. 结构设计选型规范

### 11.1 五种结构模式

| 模式 | 结构 | 适用场景 |
|------|------|---------|
| **A：单 SQL 直通** | 源表 → 单条 INSERT → ADS | 逻辑简单（≤2 层嵌套），无下游复用 |
| **B：子查询内联** | 源表 → INSERT...SELECT with 派生表 | 多层逻辑但无跨表复用（推荐默认模式） |
| **C：TMP 链式物化** | 源表 → TMP1 → TMP2 → ADS | 嵌套 ≥4 层，或平台有 JOIN 数量限制 |
| **D：共享 DWS + 多 ADS** | 源表 → DWS → ADS1/ADS2 | 同一复杂逻辑被 ≥2 张下游表使用 |
| **E：原子宽表** | 源表 → 人/事件级宽表 DWS → 各 ADS 直接聚合 | 多张 ADS 表共享相同人员/事件级派生逻辑 |

### 11.2 模式 E：原子宽表（Atomic Wide Table）

**核心思想**：将所有**人员级别（或事件级别）的派生状态**集中到一张宽表计算一次，下游 ADS 表只做 `WHERE + GROUP BY`，不再重复写派生逻辑。

**与普通模式 D 的区别**：

| | 模式 D（普通共享 DWS） | 模式 E（原子宽表） |
|---|---|---|
| DWS 粒度 | 某个中间计算结果（如"失活员工明细"） | 最细粒度的实体（如"人 x 日"） |
| DWS 字段 | 只含该计算所需的字段 | 含所有下游需要用到的派生字段 |
| 下游做什么 | 仍需部分派生计算 | 只做 `WHERE + GROUP BY`，零派生逻辑 |
| 典型场景 | 某个复杂指标被多张表复用 | 整套报表基于同一实体，多张表从不同角度聚合 |

**判断标准**：当满足以下所有条件时，采用模式 E：
1. 多张 ADS 表基于同一个实体（如"员工"）
2. 实体级别的派生状态逻辑复杂（如 emp_type、emp_status、归属网点等）
3. 各 ADS 表只是按不同维度（网点/地区）聚合相同的基础指标

**宽表设计原则**：
- **字段面向下游**：包含所有下游 `GROUP BY` 或 `WHERE` 用到的派生字段，不包含纯中间计算字段
- **派生一次**：`emp_type`、`emp_status`、`final_area_code` 等只在宽表里算一次，下游直接读
- **不做聚合**：宽表粒度是最细实体粒度，聚合逻辑全部留给下游 ADS

**示例结构**（电商网点人员流失报表）：
```
DWS_ec_emp_wide_di (人 x 日 宽表)
  ├── 基础属性: emp_code, emp_name, position_txt, emp_source, ...
  ├── 网点归属: dept_code, area_code, dist_code, ...
  ├── 服务网点: service_dept, service_dept_type_code
  ├── 骑手标签: rider_level
  ├── 转岗标记: trans_flag
  └── 派生状态: emp_type, emp_status, final_area_code, final_dept_code

Table1 (人维度)   = DWS WHERE inc_day = '${bizdate}'
Table2 (网点维度) = DWS GROUP BY final_dept_code + LEFT JOIN KPI表/hierarchy表
Table3 (地区维度) = Table2 GROUP BY area_code
```

### 11.2 选择依据（5 个判断维度）

**1. 复用性（最重要）**

| 复用次数 | 推荐结构 |
|----------|---------|
| 0 次（仅当前表用） | 模式 A/B：单 SQL + 子查询内联 |
| ≥ 2 次（多张 ADS 表共用同一逻辑） | 模式 D：DWS 物化共享 |

**2. 嵌套深度**

| 嵌套层数 | 处理方式 |
|----------|---------|
| ≤ 3 层 | 子查询内联，用注释清晰标注 |
| ≥ 4 层 | 拆分为 TMP 临时表 |

**3. 数据量与性能**
- 源表被多次 JOIN 且数据量大（TB 级）→ 物化中间结果只扫一次
- 数据量小（百万级以下）→ 物化反而增加调度开销，内联更优

**4. 可调试性要求**
- SLA 高、排查频繁 → 物化中间层，可逐段验证
- 逻辑简单 → 内联即可

**5. 平台约束**
- Hive 单层 JOIN 数量限制 → 强制拆分 TMP
- Spark shuffle 内存限制 → 大表 JOIN 需提前过滤或分步
- 调度超时 → 复杂 SQL 需拆分保活

### 11.3 评价框架（6 个维度）

```
                    正确性（必要非充分）
                        ↑
          可调试性  ←── ● ──→  性能
                  ↖     ↗
              可维护性    可扩展性
                    ↓
                资源成本
```

| 维度 | 核心问题 | 好的信号 | 坏的信号 |
|------|---------|---------|---------|
| **正确性** | 逻辑是否符合需求？ | 指标口径与需求文档一一对应 | 指标理解偏差、字段来源错误 |
| **可维护性** | 3个月后其他人能看懂吗？ | 注释清晰、结构对称、命名规范 | 深层嵌套、重复代码、隐式依赖 |
| **性能** | 能否在调度窗口内完成？ | 源表扫描次数最少、无冗余 shuffle | 大表多次全扫、笛卡尔积风险 |
| **可调试性** | 数据异常时能快速定位吗？ | 中间结果可查、每段可独立验证 | 全链路黑盒、只能看最终结果 |
| **可扩展性** | 需求变更时改动范围大吗？ | 修改局部不影响整体 | 改一个字段需重写整条 SQL |
| **资源成本** | 磁盘、内存、CPU 是否合理？ | TMP 用完即清、无冗余物化 | DWS 无限膨胀、TMP 残留 |

### 11.4 决策流程

```
收到需求
  │
  ├─ 逻辑简单（≤2层嵌套，无复用）？
  │     → 模式 A/B：单 SQL + 子查询内联
  │
  ├─ 嵌套深（≥4层）或平台有 JOIN 限制？
  │     → 模式 C：TMP 链
  │
  ├─ 同一逻辑被 ≥2 张下游表使用？
  │     → 模式 D：DWS 物化共享
  │
  └─ 企业级数仓、长期多业务方共用？
        → 标准分层（ODS→DWD→DWS→ADS）
```

**核心原则：结构服务于逻辑，先判断复用性和复杂度，再决定模式，不为分层而分层。**

---

## 12. 检查清单

开发完成后，逐项检查：

- [ ] 分区字段统一为 `inc_day`
- [ ] olap 集群表加了 `olap.` 前缀
- [ ] SQL 关键字全小写
- [ ] SELECT 字段竖排
- [ ] JOIN 子查询加了 DISTINCT
- [ ] 使用派生表模式（先过滤再 JOIN）
- [ ] GROUPING SETS 包含完整维度
- [ ] parent_code 在最后一步统一处理
- [ ] 网点类型使用 5 种（示例业务）
- [ ] 除法用 NULLIF 保护
- [ ] 合并日期范围减少表扫描
- [ ] 业务给定固定系数时使用独立配置表 (§6.4)
- [ ] 配置字段是纯系数 (同组内唯一) 时, 考虑先聚合再 JOIN (§6.5)
- [ ] 同时需要 sum + count(distinct) 时, 拆两层聚合消除 count(distinct) (§6.6)
- [ ] **多期间处理：period 维度是否打平为列？（§10）**
- [ ] **禁止 CROSS JOIN 造维度骨架（§10.6）**
- [ ] **环比/同比用列间运算，禁止 period 自联（§10.4）**
- [ ] **禁止使用 CTE（WITH 子句），用子查询派生表或 TMP 临时表替代（§6.3）**
- [ ] **结构设计合理：是否选择了正确的模式？复用逻辑是否提取为 DWS？（§11）**
- [ ] 每个临时表有排查注释
- [ ] 有配套的执行流程文档

---

## 附录：常见错误

### 错误 1: Calcite 解析错误
**问题**: LEFT JOIN 表直接参与 GROUPING SETS
**解决**: 先用派生表包装 JOIN

### 错误 2: 数据膨胀
**问题**: JOIN 子查询没有 DISTINCT
**解决**: 加 DISTINCT 去重

### 错误 3: 分区找不到
**问题**: 使用错误的分区字段名（cur_date 而非 inc_day）
**解决**: 统一使用 inc_day

### 错误 4: 网点类型不全
**问题**: 只包含部分网点类型
**解决**: 使用标准 5 种类型
