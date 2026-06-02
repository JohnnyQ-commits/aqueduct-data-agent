-- ==========================================
-- 同城-同城骑手4月散单数据明细 - 数据质量测试
-- ==========================================
-- 测试日期: $[time(yyyyMMdd,-1d)]

-- [记录数-总量校验] 输出记录数不应超过骑手基表记录数
-- 权重: Medium
select t1.cnt as output_cnt, t2.cnt as base_cnt
from (
    select count(1) as cnt
    from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
    where inc_day='$[time(yyyyMMdd,-1d)]'
) t1,
(
    select count(1) as cnt
    from dw_demo.dm_emp_rating_info_mi
    where inc_day='202604'
) t2;
-- 预期: output_cnt <= base_cnt

-- [唯一性-主键] 检查结果表emp_code是否存在重复
-- 权重: High
select emp_code, count(*) as cnt
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]'
group by emp_code
having cnt > 1;
-- 预期: 0 条

-- [非空-核心字段] emp_code不允许出现Null
-- 权重: High
select count(*) as null_cnt
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]'
  and emp_code is null;
-- 预期: 0 条

-- [非空-核心字段] area_code不允许出现Null
-- 权重: High
select count(*) as null_cnt
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]'
  and area_code is null;
-- 预期: 0 条

-- [边界-数值合理性] pure_casual_cnt不应为负数
-- 权重: Medium
select *
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]'
  and pure_casual_cnt < 0;
-- 预期: 0 条

-- [边界-数值合理性] monthly_casual_cnt不应为负数
-- 权重: Medium
select *
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]'
  and monthly_casual_cnt < 0;
-- 预期: 0 条

-- [一致性-收件反查] 抽样3个骑手反查收件表纯散件数量是否一致
-- 权重: High
select
    t1.emp_code,
    t1.pure_casual_cnt as table_total,
    t2.cnt as actual_pickup_pure_casual
from (
    select emp_code, pure_casual_cnt
    from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
    where inc_day='$[time(yyyyMMdd,-1d)]'
    and pure_casual_cnt > 0
    order by pure_casual_cnt desc
    limit 3
) t1
left join (
    select emp_code, count(1) as cnt
    from dw_demo.dwd_pickup_order_dtl_di
    where inc_day >= '20260401' and inc_day <= '20260430'
    and layer_name is null
    group by emp_code
) t2 on t1.emp_code = t2.emp_code;
-- 预期: table_total与实际汇总一致

-- [一致性-派件反查] 抽样3个骑手反查派件表月结散件数量是否一致
-- 权重: High
select
    t1.emp_code,
    t1.monthly_casual_cnt as table_total,
    t2.cnt as actual_deliver_monthly_casual
from (
    select emp_code, monthly_casual_cnt
    from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
    where inc_day='$[time(yyyyMMdd,-1d)]'
    and monthly_casual_cnt > 0
    order by monthly_casual_cnt desc
    limit 3
) t1
left join (
    select t1.emp_code, count(1) as cnt
    from dw_demo.dwd_deliver_order_dtl_di t1
    left join (
        select customer_code
        from dm_crm.dm_customer_allcust_info_df
        where inc_day = (select max(inc_day) from dm_crm.dm_customer_allcust_info_df)
        and cust_tier_label in ('1.1','1.2','1.3','2.1','2.2')
    ) t2 on t1.freight_monthly_acct_code = t2.customer_code
    where t1.inc_day >= '20260401' and t1.inc_day <= '20260430'
    and t2.customer_code is not null
    group by t1.emp_code
) t2 on t1.emp_code = t2.emp_code;
-- 预期: table_total与实际汇总一致

-- [一致性-维表覆盖率] 检查结果表中emp_code在骑手维度表中的覆盖率
-- 权重: Medium
select
    count(a.emp_code) as total_cnt,
    sum(case when b.login_id is null then 1 else 0 end) as miss_cnt
from (
    select emp_code
    from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
    where inc_day='$[time(yyyyMMdd,-1d)]'
) a
left join (
    select login_id
    from dw_demo.dim_rider_org_info
    where inc_day='$[time(yyyyMMdd,-1d)]'
    and job_status=1
    and is_deleted=0
) b on a.emp_code = b.login_id;
-- 预期: miss_rate < 0.1%

-- [波动-总量合理性] 有散单数据的骑手占比应大于10%
-- 权重: Low
select
    count(1) as total_cnt,
    sum(case when pure_casual_cnt + monthly_casual_cnt > 0 then 1 else 0 end) as sand_cnt,
    round(sum(case when pure_casual_cnt + monthly_casual_cnt > 0 then 1 else 0 end) * 100.0 / count(1), 2) as sand_ratio
from tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
where inc_day='$[time(yyyyMMdd,-1d)]';
-- 预期: sand_ratio > 10%
