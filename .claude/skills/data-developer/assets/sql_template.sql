 ============================================================
 需求：{{requirement_name}}
 目标表：{{target_table}}
 源表：{{source_tables}}
 作者：aqueduct
 日期：{{date}}
 描述：{{description}}
 ============================================================

 ============================================================
 DDL: 建表模板（目标表）
 ============================================================
 create external table if not exists dw_demo.dws_order_daily_stat_di
 (
     order_count        bigint    comment '当日订单数',
     customer_count     bigint    comment '当日成交客户数',
     gmv                double    comment '当日GMV',
     avg_order_amount   double    comment '当日客单价'
 )
 comment '订单日活统计'
 partitioned by (inc_day string comment '日期分区')
 stored as parquet
 location 'hdfs://your-cluster/user/hive/warehouse/dw_demo/dws_order_daily_stat_di'
 ;

 ============================================================
 示例 1：简单场景（≤3 张源表）— 子查询方式
 ============================================================
 insert overwrite table dw_demo.dws_emp_tool_daily_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')
 select
     a.emp_code,
     a.emp_name,
     coalesce(b.dept_name, '') as dept_name,
     coalesce(a.tool_total_num, 0) as tool_total_num
 from (
     select
         emp_code,
         emp_name,
         tool_total_num
     from dwd.emp_info_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and employ_status = '在职'
 ) a
 left join (
     select
         emp_code,
         dept_name
     from dwd.dim_dept_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) b
 on a.emp_code = b.emp_code
 ;

 ============================================================
 示例 2：复杂场景（≥4 张源表）— 派生表方式
 ============================================================
 所有表均子查询包装（派生表），过滤条件下推到子查询内部
 同源表多用途时合并为一个子查询，用 CASE WHEN 条件聚合区分
 insert overwrite table dw_demo.dws_emp_daily_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')
 select
     e.emp_code,
     e.emp_name,
     coalesce(t.tool_cnt, 0) as tool_cnt,
     t.tool_total,
     coalesce(o.order_cnt, 0) as order_cnt,
     o.gmv,
     round(o.gmv / nullif(o.order_cnt, 0), 2) as avg_order_amount
 from (
     select
         emp_code,
         emp_name,
         dept_code,
         area_code,
         employ_status
     from dwd.emp_info_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and employ_status = '在职'
         and position_attr_tx = '一线'
 ) e
 left join (
     select
         emp_code,
         count(tool_name) as tool_cnt,
         coalesce(sum(tool_total_num), 0) as tool_total
     from dwd.emp_tool_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
     group by emp_code
 ) t on e.emp_code = t.emp_code
 left join (
     select
         emp_code,
         count(order_id) as order_cnt,
         coalesce(sum(pay_amount), 0) as gmv
     from dwd.dwd_order_detail_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and order_status >= '20'
     group by emp_code
 ) o on e.emp_code = o.emp_code
 ;

 ============================================================
 示例 3：更复杂场景（≥7 张源表或 60+ 字段）— 分层建表
 ============================================================
 中间表：tmp_demo.tmp_{需求简称}_{聚合主题}
 主表和右表均子查询包装，过滤条件推入子查询内部
 drop table if exists tmp_demo.tmp_order_daily_stat_city;
 create table tmp_demo.tmp_order_daily_stat_city as
 select
     o.inc_day,
     o.city,
     c.city_name,
     count(distinct o.order_id) as order_cnt,
     sum(o.pay_amount) as gmv,
     count(distinct o.customer_id) as customer_cnt,
     coalesce(sum(case when o.order_type = 'scatter' then o.pay_amount end), 0) as scatter_gmv,
     coalesce(sum(case when o.order_type = 'delivery' then o.pay_amount end), 0) as delivery_gmv
 from (
     select
         order_id,
         city,
         customer_id,
         product_id,
         shop_id,
         category_id,
         pay_amount,
         order_type,
         order_status,
         inc_day
     from dwd.dwd_order_detail_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and order_status >= '20'
 ) o
 inner join (
     select
         city_code,
         city_name
     from dwd.dim_city_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) c on o.city = c.city_code
 inner join (
     select
         customer_id
     from dwd.dim_customer_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) cu on o.customer_id = cu.customer_id
 inner join (
     select
         product_id
     from dwd.dim_product_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) p on o.product_id = p.product_id
 inner join (
     select
         shop_id
     from dwd.dim_shop_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) s on o.shop_id = s.shop_id
 inner join (
     select
         category_id
     from dwd.dim_category_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) cat on o.category_id = cat.category_id
 inner join (
     select
         payment_id
     from dwd.dim_payment_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) pay on o.payment_id = pay.payment_id
 inner join (
     select
         logistics_id
     from dwd.dim_logistics_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
 ) log on o.logistics_id = log.logistics_id
 group by
     o.inc_day,
     o.city,
     c.city_name
 ;

 最终表：从中间表取数
 insert overwrite table ads_demo.ads_order_daily_stat_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')
 select
     city,
     city_name,
     order_cnt,
     customer_cnt,
     gmv,
     scatter_gmv,
     delivery_gmv,
     round(gmv / nullif(order_cnt, 0), 2) as avg_order_amount,
     round(gmv / nullif(customer_cnt, 0), 2) as avg_customer_amount
 from tmp_demo.tmp_order_daily_stat_city
 where inc_day = '$[time(yyyyMMdd,-1d)]'
 ;

 ============================================================
 示例 4：宽表模式（条件≥3 个 / 需排查验收）— ★ 最常用
 ============================================================
 三个强制原则：
   1. 排查优先：判断中间字段（orgcode、servicedept 等）必须暴露
   2. 逻辑内聚：每个 is_sN 自包含，不依赖外层额外过滤
   3. ADS 统一过滤：WHERE 只写 is_sN = 1
 DWD+DWS 合并为一张宽表，减少中间表

 DWS层：宽表（合并基础池 + 所有判断条件 + 判断中间字段）
 drop table if exists tmp_demo.tmp_scenario_wide;
 create table tmp_demo.tmp_scenario_wide as
 select
     s.loginid,
     s.position_name,
     s.dept_code,
     -- 判断中间字段（暴露供排查）
     s.orgcode,
     s.servicedept,
     ac.single_area_id,
     adm.config_area_dept_code,
     pc.node                              as pc_matched_node,
     pc.related_node                      as pc_matched_related,
     -- 来源/子组校验（derived 标记）
     case when (sf.emp_source = 'HR'
                and sf.emp_subgroup not in ('短期合同制','灵活排遣'))
               or (sf.emp_source = 'OA'
                   and sf.emp_subgroup in ('合同工','劳务工'))
          then 1 else 0
     end                                  as is_valid_source,
     -- 培养状态
     case when tr.loginid is not null
          then '1' else '0'
     end                                  as train_status,
     -- 场景标记（每个自包含）
     case when s.position_name not in ('收派员','配送员')
          then 1 else 0
     end                                  as is_s1,              -- 岗位异动
     case when coalesce(ac.area_cnt, 0) = 1
               and adm.config_area_dept_code is not null
               and adm.config_area_dept_code <> s.dept_code
               and pc.node is null                           -- 父子排除内聚在此
          then 1 else 0
     end                                  as is_s2,              -- 网点异动（自包含）
     s.inc_day
 from (
     select
         loginid,
         positionname                                    as position_name,
         case when positionname = '配送员'
              then servicedept
              else orgcode
         end as dept_code,
         orgcode,
         servicedept,
         inc_day
     from dwd.emp_info_di
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and status = '1'
         and isdeleted = '0'
         and positionproperty = '0010'
 ) s
 inner join (                                                      -- 属性维度：过滤推入子查询
     select
         dept_code
     from dwd.dim_dept_info_df
     where inc_day = '$[time(yyyyMMdd,-1d)]'
         and hq_code in ('R01','R02','R03','R08')
 ) d on s.dept_code = d.dept_code
 left join dw_emp_base sf on s.loginid = sf.emp_code         -- 条件字段：LEFT JOIN 保留全量
 left join novice_train tr       on s.loginid = tr.loginid
 left join area_config ac        on s.loginid = ac.loginid
     -- ↑ area_config 子查询用 CASE WHEN 合并多用途字段：
     --   count(distinct area_id) as area_cnt,
     --   case when count(distinct area_id)=1 then max(area_id) end as single_area_id
 left join area_dept_map adm     on ac.single_area_id = adm.aoi_area_id
 left join parent_child pc       on s.dept_code = pc.node
     and adm.aoi_area_id = pc.related_node
 ;

 ADS层：宽表过滤落表（直接用 is_sN，不额外 JOIN）
 insert overwrite table ads_demo.ads_scenario_result_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')
 select
     loginid            as emp_code,
     position_name,
     dept_code,
     orgcode,                                                    -- 中间字段透传到 ADS 供排查
     servicedept,
     is_valid_source,
     train_status,
     is_s1,
     is_s2,
     inc_day
 from tmp_demo.tmp_scenario_wide
 where is_valid_source = 1                                       -- 条件1：来源校验
     and train_status = '1'                                      -- 条件2：已培养
     and (is_s1 = 1 or is_s2 = 1)                                -- 至少命中一个场景
 ;

 ============================================================
 示例 5：分层聚合（已有临时表 → 三层上卷）
 ============================================================
 适用场景：临时表已聚合到网点+日粒度，数据量小
 三层结构：网点(level=2) → 地区(level=1) → 总部(level=0)
 大明细表直接多维聚合时仍用 GROUPING SETS

 insert overwrite table ads_demo.ads_xxx_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')

 -- 网点级 (dept_level=2)
 select
     dept_code as org_code,
     dept_name as org_name,
     2 as dept_level,
     total_dispatch_cnt,
     xxx_cnt,
     round(xxx_cnt / nullif(total_dispatch_cnt, 0), 4) as xxx_ratio
 from tmp_demo.tmp_xxx_dept_di

 union all

 -- 地区级 (dept_level=1)
 select
     area_code as org_code,
     area_name as org_name,
     1 as dept_level,
     sum(total_dispatch_cnt) as total_dispatch_cnt,
     sum(xxx_cnt) as xxx_cnt,
     round(sum(xxx_cnt) / nullif(sum(total_dispatch_cnt), 0), 4) as xxx_ratio
 from tmp_demo.tmp_xxx_dept_di
 group by
     area_code,
     area_name

 union all

 -- 总部级 (dept_level=0)
 select
     '全部' as org_code,
     '全部' as org_name,
     0 as dept_level,
     sum(total_dispatch_cnt) as total_dispatch_cnt,
     sum(xxx_cnt) as xxx_cnt,
     round(sum(xxx_cnt) / nullif(sum(total_dispatch_cnt), 0), 4) as xxx_ratio
 from tmp_demo.tmp_xxx_dept_di
 ;

 ============================================================
 示例 6：Presto 导出查询
 ============================================================
 字段竖排，中文别名，与需求文档字段顺序一致
 单独文件 presto导出查询.sql

 select
     org_code as "组织编码",
     org_name as "组织名称",
     dept_level as "组织层级",
     total_dispatch_cnt as "总发件量",
     xxx_cnt as "xxx件量",
     xxx_ratio as "xxx占比"
 from ads_demo.ads_xxx_di
 where inc_day = '$[time(yyyyMMdd,-1d)]'
 order by
     dept_level,
     org_code
 ;

 ============================================================
 编码规范速查
 ============================================================
 1. 关键字小写：select, from, where, group by, left join
 2. 字段竖排：所有 select（含派生表/JOIN）字段均竖排，4 空格缩进，每字段独占一行
 3. WHERE 紧凑：≤3 个条件写一行，>3 个换行（and 前置 2 空格）
 4. 函数逗号无空格：coalesce(a,0), in('a','b')
 5. GROUP BY 灵活：≤3 字段紧凑，>3 字段竖排
 6. JOIN 与 ON 分行：left join table\n    on condition
 7. JOIN 键预处理：行级条件决定 JOIN 键时，先子查询计算再等值关联（禁止 ON 中写 CASE）
 8. 过滤条件下推：右表过滤条件推入子查询，LEFT JOIN → INNER JOIN（宽表模式除外）
 9. 所有表必须子查询包装：FROM 主表和所有 JOIN 表均先包成子查询，过滤条件推到子查询内部
10. 禁止 EXISTS：改用 LEFT JOIN 子查询（select distinct key_col）
11. 简单场景用子查询：≤3 张源表用 from (select) alias
12. 复杂场景也用子查询：≥4 张源表，每张表各自子查询包装后关联
13. 更复杂场景分层建表：≥7 张源表或 60+ 字段按 DWD/DWS/ADS 分层（中间结果落盘，便于排查）
14. 宽表模式（★ 最常用）：条件≥3 个 → DWD+DWS 合并宽表 + ADS WHERE
15. 宽表三原则：① 排查优先（判断中间字段全暴露）② 逻辑内聚（is_sN 自包含）③ ADS 统一过滤
16. 多层合并：DWD 无复杂聚合时合并到 DWS；同表多次读取合并为一次
17. 架构决策树：先判断条件数量（≥3→宽表）再判断源表数量（≥7→分层），不要靠猜
18. 同源表多用途合并：同一张表+相同 WHERE 读多次时，合并为一个子查询，用 CASE WHEN 条件聚合区分用途
19. 桥接表拆分：当"关系对"表的 JOIN 条件同时引用两个不同 LEFT JOIN 表的字段时（跨表依赖），按业务域拆分子查询
    识别信号：pc ON a.dept_code AND b.config_dept（a 和 b 是两个不同的 LEFT JOIN）→ 拆为子查询 A + B
    好处：每个子查询可独立跑/验，桥接表两端来自同层，关系清晰
20. 临时表规范：库名统一 tmp_demo；CTAS 模式（drop table if exists + create table as select）；不用 create temporary table
21. 分层聚合：已有临时表（数据量小）用 UNION ALL 三层上卷（网点=2/地区=1/总部=0）；大明细表直接多维聚合仍用 GROUPING SETS
22. 时间变量：'$[time(yyyyMMdd,-1d)]' = T-1，'$[time(yyyyMMdd,-2d)]' = T-2，不写死日期
23. Presto 导出查询：单独文件 presto导出查询.sql，字段竖排+中文别名
24. 禁止使用 CTE（WITH 子句）：全面禁止，无论复杂度。同段逻辑复用 → 子查询派生表内联；跨表复用或嵌套 ≥4 层 → TMP 临时表（见 sql_standards.md §6.3）
