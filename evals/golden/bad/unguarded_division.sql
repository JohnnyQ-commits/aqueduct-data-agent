-- golden bad 样本：检查 4 —— 除法未做判空判零保护（§7.2，应写 nullif）
select order_amount / order_count as avg_amount
from dwd.dwd_order_detail_di
where inc_day = '20260101';
