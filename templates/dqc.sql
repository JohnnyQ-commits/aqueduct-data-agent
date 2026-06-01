-- ==========================================
-- 数据质量测试模板
-- ==========================================
-- 使用说明：将 $[time(yyyyMMdd,-1d)] 替换为实际日期

-- ==========================================
-- 1. 记录数校验
-- ==========================================

-- 1.1 结果表不为空
select count(*) as total_cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]';
-- 预期: > 0

-- 1.2 结果表无重复 (按主键或唯一键组合)
select {主键字段}, count(*) as cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by {主键字段}
having cnt > 1;
-- 预期: 0 条

-- ==========================================
-- 2. 枚举值校验
-- ==========================================

-- 2.1 枚举字段合法值
select {枚举字段}, count(*) as cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by {枚举字段};
-- 预期: 只有预期枚举值,无 null

-- ==========================================
-- 3. 关联反查
-- ==========================================

-- 3.1 结果表数据反查源表
select a.{关联字段}, a.{校验字段}, b.{校验字段}
from {结果表} a
join {源表} b on a.{关联字段} = b.{关联字段}
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and a.{校验字段} != b.{校验字段};
-- 预期: 0 条

-- ==========================================
-- 4. 字段非空校验
-- ==========================================

select
    sum(case when {字段1} is null then 1 else 0 end) as null_{字段1},
    sum(case when {字段N} is null then 1 else 0 end) as null_{字段N}
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]';
-- 预期: 全部为 0 (left join字段允许null除外)

-- ==========================================
-- 5. 数据量波动监控
-- ==========================================

-- 5.1 近7天环比波动
select
    inc_day, count(*) as cnt,
    lag(count(*)) over(order by inc_day) as prev_cnt,
    round(
        (count(*) - lag(count(*)) over(order by inc_day)) * 100.0
        / lag(count(*)) over(order by inc_day), 2
    ) as pct_change
from {结果表}
where inc_day >= '$[time(yyyyMMdd,-7d)]'
group by inc_day;
-- 预期: pct_change 在 ±20% 以内
