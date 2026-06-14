-- ==========================================
-- DQC: 电商订单分析 (Order Data Quality Tests)
-- ==========================================

-- [唯一性-订单号] 检查每天每个订单只有一条记录
-- 权重: High
select order_id, count(*) as cnt
from dw_demo.dwd_order_info_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
group by order_id
having cnt > 1;
-- 预期: 0 条

-- [时效性-最新分区] 检查数据是否已更新至昨日
-- 权重: High
select max(inc_day) as max_day
from dw_demo.dwd_order_info_di;
-- 预期: max_day = '$[time(yyyyMMdd,-1d)]'

-- [一致性-金额校验] 实付金额 = 总金额 - 优惠金额
-- 权重: High
select order_id, total_amount, discount_amount, pay_amount
from dw_demo.dwd_order_info_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and abs(pay_amount - (total_amount - discount_amount)) > 0.01;
-- 预期: 0 条

-- [业务反证-状态闭环] 已取消订单不应有支付时间
-- 权重: Medium
select order_id, order_status, pay_time
from dw_demo.dwd_order_info_di
where inc_day = '$[time(yyyyMMdd,-1d)]'
  and order_status = '50'
  and pay_time is not null;
-- 预期: 0 条

-- [波动-记录数环比] 检查订单量日环比波动
-- 权重: Low
select
    inc_day, count(*) as cnt,
    lag(count(*)) over(order by inc_day) as prev_cnt,
    round((count(*) - lag(count(*)) over(order by inc_day)) * 100.0 / lag(count(*)) over(order by inc_day), 2) as pct_change
from dw_demo.dwd_order_info_di
where inc_day >= '$[time(yyyyMMdd,-7d)]'
group by inc_day;
-- 预期: pct_change 在 ±30% 以内（电商大促期间可放宽）
