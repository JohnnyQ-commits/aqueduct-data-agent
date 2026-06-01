# SQL 代码风格规范

## 核心规则

### 1. 关键字全小写

```sql
-- 正确
select ... from ... where ... group by ... left join ... on ...
-- 错误
SELECT ... FROM ... WHERE ... GROUP BY ... LEFT JOIN ... ON ...
```

包括：`select`, `from`, `where`, `join`, `left join`, `inner join`, `case`, `when`, `then`, `end`, `group by`, `insert overwrite`, `partition`, `drop table if exists`, `and`, `or`, `in`, `is null`, `is not null`, `as`, `union all`

### 2. select 字段竖排，4空格缩进

```sql
select
    emp_code,
    emp_name,
    dept_code
from ...
```

### 3. where 条件紧凑

```sql
-- ≤3个条件写一行
where inc_day = '$[time(yyyyMMdd,-1d)]' and employ_status = '在职' and position_attr_tx = '一线'

-- 多条件换行，and 前导2空格
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and (
      (emp_source = 'HR' and peak = '否')
      or (emp_source = 'OA' and position_txt = 'delivery')
  )
```

### 4. 函数内逗号无空格

```sql
-- 正确
in ('a','b')
coalesce(a,0)
coalesce(a.tool_total_num,0)

-- 错误
in ('a', 'b')
coalesce(a, 0)
```

### 5. group by 灵活

```sql
-- ≤3字段紧凑
group by emp_code,dept_code

-- >3字段竖排
group by
    area_code,
    dept_code,
    tool_classify,
    tool_name,
    inc_day
```

### 6. join 与 on 分行

```sql
from (...) e
inner join (...) r
on e.emp_code = r.emp_code
left join 表名 别名
on 条件1 and 条件2;
```

### 7. 子查询优先

```sql
-- 简单SQL：子查询方式
from (select ... from 表 where ...) e
inner join (select ... from 表 where ...) r
on e.emp_code = r.emp_code

-- 不要直接 join 表名
from 表1 e
join 表2 r on e.id = r.id  -- 不推荐
```

### 8. 复杂场景用 CTE

```sql
with step1 as (
    select ... from ... where ...
),
step2 as (
    select ... from step1 join ...
)
select ... from step2 ...
```

### 9. 数仓分层

更复杂需求按数仓分层设计：
- ODS 层：原始数据接入
- DWD 层：数据清洗、标准化
- DWS 层：轻度汇总
- ADS 层：应用层结果表

该拆表就拆表，该分层就分层。

### 10. 文件头注释

```sql
-- 需求名称 - 简要说明
-- 需求文档链接
-- 数据来源说明
```

### 11. 变量约定

- 系统变量：`$[time(yyyyMMdd,-1d)]`
- 分区字段：`inc_day`
- 业务日期字段：`day`（如排班表）
