-- ============================================================
-- Requirement: {{requirement_name}}
-- Target Table: {{target_table}}
-- Source Tables: {{source_tables}}
-- Author: aqueduct
-- Date: {{date}}
-- Description: {{description}}
-- ============================================================

INSERT OVERWRITE TABLE {{target_table}} PARTITION (inc_day = '${bizdate}')
SELECT
    {{columns}}
FROM {{source_table}}
WHERE inc_day = '${bizdate}'
    {{additional_filters}}
{{group_by}}
;
