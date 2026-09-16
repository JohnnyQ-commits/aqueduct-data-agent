-- golden bad 样本：检查 4 校准形态 —— 守护条件是别的字段（与分母无关）必须仍报
-- 来源：金样本校准 test_guard_on_other_column_still_flagged（守护列与分母列无关联，保护不成立）
select
    case when other_flag = 0 then null
         else cast(t1.gmv / t1.order_count as decimal(20, 4)) end as avg_gmv
from dwd.dwd_order_detail_di t1
where inc_day = '20260101';
