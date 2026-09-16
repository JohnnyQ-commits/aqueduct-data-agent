-- golden good 样本 2：CASE 守护形态全集——linter 历史误报重灾区（§7.2 校准回归）
-- 零守护（=0/is null）保 else 支路；正守护（!=0/<>0）保 then 支路；count(distinct) 分母合法
select
    case when t1.order_count = 0 then null
         else cast(t1.gmv / t1.order_count as decimal(20, 4)) end as avg_gmv,
    case when order_count = 0 then null
         else cast(gmv / order_count as decimal(20, 4)) end as avg_gmv_local,
    case when t1.cnt != 0 then t1.gmv / t1.cnt else null end as ratio_pos,
    case when order_count <> 0 then gmv / order_count end as ratio_pos2,
    case when b.order_count is not null and b.order_count != 0
         then a.gmv / b.order_count else null end as ratio_combo,
    round(sum(pickup_cnt) / count(distinct inc_day), 2) as daily_avg
from dwd.dwd_order_detail_di t1
where inc_day between '20260908' and '20260914';
