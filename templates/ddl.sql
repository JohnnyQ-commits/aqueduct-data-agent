-- ==========================================
-- 表结构模板
-- ==========================================
-- 使用说明：
-- 1. 替换表名、字段名、注释
-- 2. 分区字段固定为 inc_day string
-- 3. 存储格式为 PARQUET

CREATE TABLE IF NOT EXISTS {库名}.{表名} (
    `{字段1}` string COMMENT '{注释}',
    `{字段2}` string COMMENT '{注释}',
    `{字段N}` string COMMENT '{注释}'
) COMMENT '{表注释}'
PARTITIONED BY (`inc_day` string COMMENT '数据分区日期，格式YYYYMMDD')
STORED AS PARQUET;
