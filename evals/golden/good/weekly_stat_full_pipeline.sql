-- ============================================================================
-- golden good 样本 1：全管道风格（TMP 中间表 + ADS 落表），金样本校准后的零误报基准
-- 形态来源：test_validator.py::test_golden_style_clean（脱敏自电商网点效能周表）
-- 要点：nullif 分母 / count(distinct inc_day) 分母 / 宏字面量 / inc_day 分区过滤
-- ============================================================================
drop table if exists tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)];
create table tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)]
stored as parquet as
select
    dept_code,
    sum(order_cnt)                                            as order_cnt,
    round(sum(order_amount) / nullif(sum(order_cnt), 0), 4)   as avg_amount,
    round(sum(pickup_cnt) / count(distinct inc_day), 2)       as daily_avg
from (
    select
        dept_code,
        inc_day,
        order_cnt,
        order_amount,
        pickup_cnt
    from dwd.dwd_order_detail_di
    where inc_day between '$[monday(yyyyMMdd,-1d)]'
                      and date_format(date_add(to_date('$[monday(yyyyMMdd,-1d)]', 'yyyyMMdd'), 6), 'yyyyMMdd')
) d
group by dept_code;

insert overwrite table dw_demo.ads_demo_stat_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')
select
    dept_code,
    order_cnt,
    avg_amount,
    daily_avg
from tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)];
