-- golden bad 样本：检查 10 —— 禁止 CROSS JOIN 造维度骨架（§10.6，聚合阶段用 CASE WHEN 列打平）
select a.dept_code, b.dept_name
from dwd.dwd_order_detail_di a
cross join dim_demo.dim_dept b
where a.inc_day = '20260101';
