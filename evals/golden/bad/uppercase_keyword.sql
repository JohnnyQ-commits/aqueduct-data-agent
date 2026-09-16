-- golden bad 样本：检查 3 —— 关键字必须全小写（§2.1 硬规范）
SELECT order_id
FROM dwd.dwd_order_detail_di
WHERE inc_day = '20260101';
