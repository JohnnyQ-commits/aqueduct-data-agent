-- ==========================================
-- 数据质量测试模板 (DQC Professional Template)
-- ==========================================
-- 使用说明：
-- 1. 将 $[time(yyyyMMdd,-1d)] 替换为实际日期。
-- 2. 必须结合业务逻辑编写 [业务反证] 和 [跨表一致性] 用例。

-- ==========================================
-- 1. 唯一性与核心约束 (Uniqueness & Constraints)
-- ==========================================

-- [唯一性-主键] 检查结果表主键或业务唯一键是否存在重复
select {主键或组合键}, count(*) as cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by {主键或组合键}
having cnt > 1;
-- 预期: 0 条

-- [非空-核心字段] 核心业务字段不允许出现 Null (左关联补全字段除外)
select count(*) as null_cnt
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and ({核心字段1} is null or {核心字段2} is null);
-- 预期: 0 条

-- ==========================================
-- 2. 业务逻辑反证 (Negative Testing / Anti-Cases)
-- ==========================================
-- 说明：验证“不该出现的数据确实没出现”

-- [业务反证-逻辑互斥] 场景：轮休人员不应有合规检查记录
-- 预期: 0 条
select a.*
from {结果表} a
join {排班表} b on a.emp_code = b.emp_code
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and b.inc_day = '$[time(yyyyMMdd,-1d)]'
  and b.work_status = '轮休';

-- [业务反证-状态闭环] 场景：已离职人员不应出现在活跃统计中
-- 预期: 0 条
select a.*
from {结果表} a
join {员工主表} b on a.emp_code = b.emp_code
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and b.employ_status = '离职';

-- [业务反证-过滤有效性] 验证 SQL 中的 WHERE 条件是否生效
-- 场景：检查是否存在超出业务范围的记录 (如：非一线岗位)
select count(*)
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and not ({业务过滤逻辑，如 position_attribute = '一线'});
-- 预期: 0 条

-- ==========================================
-- 3. 跨表一致性校验 (Cross-Table Consistency)
-- ==========================================

-- [一致性-维度对齐] 检查结果表中的维度属性是否与主维表一致
-- 重点：检查工号补齐 (lpad) 是否一致
select a.{主键}, a.emp_code as result_val, b.emp_code as dim_val
from {结果表} a
join {维度表} b on lpad(a.emp_code, 8, '0') = lpad(b.emp_code, 8, '0')
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and a.emp_code != b.emp_code;
-- 预期: 0 条

-- [一致性-总量对比] 结果表去重人数应小于等于源表活跃人数
-- SQL 1 (源表): select count(distinct emp_code) from {源表} where status='上班'
-- SQL 2 (结果表): select count(distinct emp_code) from {结果表}
-- 预期: 结果表人数 <= 源表人数 (偏差率应在 1% 以内)

-- ==========================================
-- 4. 边界值与格式校验 (Boundary & Format)
-- ==========================================

-- [边界-数值合理性] 检查比例、金额等是否在正常区间
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and ({比例字段} < 0 or {比例字段} > 1 or {金额字段} < 0);
-- 预期: 0 条

-- [格式-正则匹配] 校验手机号、编码等格式规范
select *
from {结果表}
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and {编码字段} not rlike '^[A-Z0-9]{10}$';
-- 预期: 0 条

-- ==========================================
-- 5. 波动监控 (Volatility)
-- ==========================================

-- [波动-总量环比] 检查记录数是否存在异常跌涨
select
    inc_day, count(*) as cnt,
    lag(count(*)) over(order by inc_day) as prev_cnt,
    round((count(*) - lag(count(*)) over(order by inc_day)) * 100.0 / lag(count(*)) over(order by inc_day), 2) as pct_change
from {结果表}
where inc_day >= '$[time(yyyyMMdd,-7d)]'
group by inc_day;
-- 预期: pct_change 在 ±20% 以内
