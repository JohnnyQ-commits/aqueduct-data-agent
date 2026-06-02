-- ==========================================
-- DQC: 骑手工服合规检查 (Universal Compliance Test)
-- ==========================================

-- [唯一性-工号] 检查每天每个骑手只有一条合规记录
-- 权重: High
select emp_code, count(*) as cnt
from ads_courier_compliance_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by emp_code
having cnt > 1;
-- 预期: 0 条

-- [时效性-最新分区] 检查数据是否已更新至昨日
-- 权重: High
select max(inc_day) as max_day
from ads_courier_compliance_di;
-- 预期: max_day = '$[time(yyyyMMdd,-1d)]'

-- [一致性-维表关联率] 事实表关联员工主表的覆盖情况
-- 权重: Medium
select 
    count(a.emp_code) as total_cnt,
    sum(case when b.emp_code is null then 1 else 0 end) as miss_cnt,
    round(sum(case when b.emp_code is null then 1 else 0 end) * 100.0 / count(a.emp_code), 2) as miss_rate
from ads_courier_compliance_di a
left join dw_demo.dim_emp_info b on lpad(a.emp_code, 8, '0') = lpad(b.emp_code, 8, '0')
where a.inc_day = '$[time(yyyyMMdd,-1d)]';
-- 预期: miss_rate < 0.1%

-- [业务反证-状态闭环] 已离职人员不应有合规数据
-- 权重: Medium
select a.emp_code
from ads_courier_compliance_di a
join dw_demo.dim_emp_info b on a.emp_code = b.emp_code
where a.inc_day = '$[time(yyyyMMdd,-1d)]'
  and b.employ_status = '离职';
-- 预期: 0 条

-- [波动-记录数环比] 检查合规检查人数波动
-- 权重: Low
select
    inc_day, count(*) as cnt,
    lag(count(*)) over(order by inc_day) as prev_cnt,
    round((count(*) - lag(count(*)) over(order by inc_day)) * 100.0 / lag(count(*)) over(order by inc_day), 2) as pct_change
from ads_courier_compliance_di
where inc_day >= '$[time(yyyyMMdd,-7d)]'
group by inc_day;
-- 预期: pct_change 在 ±15% 以内
