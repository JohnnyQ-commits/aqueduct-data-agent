-- golden good 样本 3：JOIN 聚合风格（ADS 落表 + left join 带 ON + nullif 聚合分母）
insert overwrite table ads_demo.ads_ecommerce_daily_stat partition (inc_day = '${bizdate}')
select
    t1.shop_id,
    nvl(sum(t1.pay_amount), 0) as pay_amount,
    count(distinct t1.order_id) as order_cnt,
    nvl(sum(t1.pay_amount), 0) / nullif(count(distinct t1.order_id), 0) as avg_ticket
from dwd_demo.dwd_order_payment_di t1
left join dim_demo.dim_shop t2
    on t1.shop_id = t2.shop_id
    and t2.inc_day = '20260101'
where t1.inc_day = '${bizdate}'
group by t1.shop_id;
