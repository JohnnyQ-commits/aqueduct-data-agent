-- golden bad 样本：检查 9 —— 分区字段违禁词（§1.1，统一使用 inc_day）
select order_id
from dwd.dwd_order_detail_di
where data_date = '20260101';
