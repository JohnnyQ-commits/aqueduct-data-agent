-- ==========================================
-- 同城-同城骑手4月散单数据明细 - 目标表结构
-- ==========================================
CREATE TABLE IF NOT EXISTS tmp_dw_demo.dm_ads_rider_casual_stat_202604_di (
    `area_code` string COMMENT '地区编码',
    `area_name` string COMMENT '地区名称',
    `org_dept_code` string COMMENT '所属网点编码',
    `service_area` string COMMENT '所属服务区域(AOI区域编码逗号拼接)',
    `emp_code` string COMMENT '骑手工号',
    `emp_name` string COMMENT '骑手姓名',
    `pure_casual_cnt` bigint COMMENT '纯散件总数(4月收派件合计)',
    `monthly_casual_cnt` bigint COMMENT '月结散件总数(4月收派件合计)'
) COMMENT '同城骑手4月散单数据明细统计表'
PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')
STORED AS PARQUET;
