-- golden bad 样本：检查 1 —— SELECT * 禁止（必须显式列出字段）
select *
from dwd.dwd_order_detail_di
where inc_day = '20260101';
