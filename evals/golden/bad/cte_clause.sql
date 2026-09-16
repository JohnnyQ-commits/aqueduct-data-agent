-- golden bad 样本：检查 8 —— 禁止 CTE/WITH 子句（§6.3，改用子查询派生表或 TMP 临时表）
with base as (
    select dept_code, order_amount
    from dwd.dwd_order_detail_di
    where inc_day = '20260101'
)
insert overwrite table ads_demo.ads_dept_amount
select dept_code, sum(order_amount) as amt from base group by dept_code;
