-- golden bad 样本：检查 11 —— 临时表库名必须 tmp_ 前缀（§1.3，如 tmp_dw_demo.tmp_xxx）
drop table if exists dm_demo.tmp_order_x;
create table dm_demo.tmp_order_x stored as parquet as
select dept_code
from dwd.dwd_order_detail_di
where inc_day = '20260101';
