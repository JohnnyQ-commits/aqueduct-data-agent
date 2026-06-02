-- ==========================================
-- 数据质量测试模板 (DQC Enhanced Template)
-- ==========================================
-- 使用说明：
-- 1. 将 $[time(yyyyMMdd,-1d)] 替换为实际日期。
-- 2. 必须包含 [唯一性] 和 [边界测试] 类别。

-- ==========================================
-- 1. 唯一性测试 (Uniqueness & Primary Key)
-- ==========================================

-- [唯一性-主键] 检查结果表主键或业务唯一键是否存在重复
select {主键或组合键}, count(*) as cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by {主键或组合键}
having cnt > 1;
-- 预期: 0 条

-- [唯一性-多维度] 检查同一实体在特定维度下是否唯一 (如同一骑手同一天只能有一条排班)
select emp_code, inc_day, count(*) as cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by emp_code, inc_day
having cnt > 1;
-- 预期: 0 条

-- ==========================================
-- 2. 边界值与范围测试 (Boundary & Range)
-- ==========================================

-- [边界-日期逻辑] 结束时间必须大于等于开始时间
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and {结束时间} < {开始时间};
-- 预期: 0 条

-- [边界-数值范围] 检查百分比、评分、金额等是否在合理闭区间内 (如 0-100, >0)
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and ({比例字段} < 0 or {比例字段} > 100 or {金额字段} < 0);
-- 预期: 0 条

-- [边界-极端值] 检查是否存在异常的大值或小值 (如年龄 > 150)
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and {关键字段} > {阈值};
-- 预期: 0 条

-- ==========================================
-- 3. 逻辑一致性测试 (Consistency)
-- ==========================================

-- [一致性-状态依赖] 若状态为'已完成'，则完成时间不能为空
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and {状态字段} = '已完成'
  and {完成时间} is null;
-- 预期: 0 条

-- [一致性-父子关系] 子项的归属父项编码必须存在
select a.*
from {结果表} a
left join {维度表} b on a.{父项编码} = b.{主键}
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and b.{主键} is null;
-- 预期: 0 条

-- ==========================================
-- 4. 字段非空与格式校验 (Null & Format)
-- ==========================================

-- [非空-核心字段] 检查非空约束字段
select count(*)
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and ({核心字段1} is null or {核心字段2} is null);
-- 预期: 0 条

-- [格式-正则校验] 检查手机号、身份证、邮箱等格式
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and {手机号字段} not rlike '^1[3-9]\\d{9}$';
-- 预期: 0 条

-- ==========================================
-- 5. 波动与总量监控 (Volume & Fluctuation)
-- ==========================================

-- [波动-记录数环比] 检查记录数是否存在断崖式下跌或暴增
select
    inc_day, count(*) as cnt,
    lag(count(*)) over(order by inc_day) as prev_cnt,
    round((count(*) - lag(count(*)) over(order by inc_day)) * 100.0 / lag(count(*)) over(order by inc_day), 2) as pct_change
from {结果表}
where inc_day >= '$[time(yyyyMMdd,-7d)]'
group by inc_day;
-- 预期: pct_change 在 ±30% 以内
