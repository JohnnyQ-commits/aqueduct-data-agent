-- ==========================================
-- 同城-同城骑手4月散单数据明细
-- 统计每位同城骑手4月份纯散件和月结散件的收派件总量
-- ==========================================

-- 1. 月结客户标签过滤
with monthly_cust as (
    select customer_code
    from dm_crm.dm_customer_allcust_info_df
    where inc_day=(select max(inc_day) from dm_crm.dm_customer_allcust_info_df)
    and cust_tier_label in ('1.1','1.2','1.3','2.1','2.2')
)

-- 2. 骑手基表: 取4月骑手分级信息
,emp_base as (
    select
        emp_code,
        emp_name,
        area_code,
        area_name
    from dw_demo.dm_emp_rating_info_mi
    where inc_day='202604'
)

-- 3. 骑手网点维度: 取在职且最新记录去重
,courier_org as (
    select
        login_id,
        org_dept_code
    from (
        select
            login_id,
            org_dept_code,
            row_number() over (partition by login_id order by update_time desc) as rn
        from dw_demo.dim_rider_org_info
        where inc_day='$[time(yyyyMMdd,-1d)]'
        and job_status=1
        and is_deleted=0
    ) t
    where rn=1
)

-- 4. 骑手服务区域: AOI区域聚合
,courier_service_area as (
    select
        emp_code as emp_code,
        concat_ws(',', collect_set(aoi_area_id)) as service_area
    from dw_demo.dwd_area_group_info_partitioned
    where inc_day='$[time(yyyyMMdd,-1d)]'
    and status=1
    group by emp_code
)

-- 5. 收件散单识别(4月)
,pickup_sand as (
    select
        t.emp_code,
        sum(case when t.is_sand=1 then 1 else 0 end) as pure_casual_pickup,
        sum(case when t.is_pb=1 then 1 else 0 end) as monthly_casual_pickup
    from (
        select
            t1.emp_code,
            case when t1.layer_name is null then 1 else 0 end as is_sand,
            case when t2.customer_code is not null then 1 else 0 end as is_pb
        from dw_demo.dwd_pickup_order_dtl_di t1
        left join monthly_cust t2
            on t1.freight_monthly_acct_code=t2.customer_code
        where t1.inc_day>='20260401'
        and t1.inc_day<='20260430'
    ) t
    group by t.emp_code
)

-- 6. 派件散单识别(4月)
,deliver_sand as (
    select
        t.emp_code,
        sum(case when t.is_sand=1 then 1 else 0 end) as pure_casual_deliver,
        sum(case when t.is_pb=1 then 1 else 0 end) as monthly_casual_deliver
    from (
        select
            t1.emp_code,
            case when t1.layer_name is null then 1 else 0 end as is_sand,
            case when t2.customer_code is not null then 1 else 0 end as is_pb
        from dw_demo.dwd_deliver_order_dtl_di t1
        left join monthly_cust t2
            on t1.freight_monthly_acct_code=t2.customer_code
        where t1.inc_day>='20260401'
        and t1.inc_day<='20260430'
    ) t
    group by t.emp_code
)

-- 7. 主查询: 合并输出
insert overwrite table tmp_dw_demo.dm_ads_rider_casual_stat_202604_di
partition (inc_day='$[time(yyyyMMdd,-1d)]')
select
    e.area_code,
    e.area_name,
    co.org_dept_code,
    sa.service_area,
    e.emp_code,
    e.emp_name,
    coalesce(ps.pure_casual_pickup, 0)+coalesce(ds.pure_casual_deliver, 0) as pure_casual_cnt,
    coalesce(ps.monthly_casual_pickup, 0)+coalesce(ds.monthly_casual_deliver, 0) as monthly_casual_cnt
from emp_base e
left join courier_org co
    on e.emp_code=co.login_id
left join courier_service_area sa
    on e.emp_code=sa.emp_code
left join pickup_sand ps
    on e.emp_code=ps.emp_code
left join deliver_sand ds
    on e.emp_code=ds.emp_code
;
