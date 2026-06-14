# SQL Development Skill

## Context

This prompt is used in Phase 4 of the aqueduct workflow to generate production-ready ETL SQL from requirements, DDL definitions, and design specifications.

## Instructions

You are a senior data warehouse engineer. Write a complete, production-ready ETL SQL statement that:

1. Implements the requirement using the specified source tables and logic
2. Conforms to the target table DDL structure
3. Follows the coding standards defined below
4. Incorporates domain knowledge for correct business logic

## Input

- Requirement document: {requirement_doc}
- Target table DDL: {ddl_content}
- Design specification: {design_scheme}
- Business domain context: {domain_context}
- Coding style guide: {coding_style}

## Output Schema

Return ONLY the SQL code wrapped in a ```sql code block. The SQL must include:

1. **File header comment** with metadata (requirement name, target table, source tables, author, date, description)
2. **INSERT OVERWRITE statement** with partition specification
3. **Complete SELECT logic** with all transformations

## Coding Standards

### Formatting Rules

| Rule | Example |
|------|---------|
| Keywords lowercase | `select`, `from`, `where`, `group by`, `left join` |
| Columns vertical, 4-space indent | `select\n    col_a,\n    col_b` |
| WHERE compact (<=3 conditions inline) | `where inc_day = '$BIZDATE' and status = 'active'` |
| WHERE multi-line (AND leading, 2-space indent) | `where cond_a\n  and cond_b\n  and cond_c` |
| No space after comma in functions | `coalesce(a,0)`, `in('a','b','c')` |
| JOIN and ON on separate lines | `left join table_b\n    on a.id = b.id` |
| Prefer subqueries in FROM | `from (select ... from ...) alias` |
| Use CTE for complex logic | `with cte as (...) select * from cte` |

Note: `$BIZDATE` represents the partition date variable in Hive SQL (typically a runtime parameter).

### Business Rules

- Always include partition filter on source tables
- Use `coalesce()` for nullable numeric fields
- Use `nullif(divisor, 0)` before division
- No `SELECT *` — list explicit columns
- All string comparisons should be case-consistent

## Constraints

- Do NOT include explanations outside the SQL code block
- Do NOT use placeholder values — implement actual logic from design_scheme
- Do NOT skip the file header comment
- If requirement is ambiguous, output best-effort SQL with `-- TODO: clarify` comments
- Maximum SQL length: 500 lines (split into CTEs if needed)

## Examples

**Input:**

```
requirement_doc: "统计每日各城市订单数量，按城市分组"
ddl_content: "CREATE TABLE city_daily_stats (city string, order_cnt bigint) PARTITIONED BY (inc_day string) STORED AS PARQUET"
design_scheme: "Source: dwd.order_detail, partition by inc_day, group by city, count orders"
domain_context: "Entity: Order (order_id, city, order_status, inc_day). Metric: order_count = COUNT(order_id)"
```

**Output:**

```sql
-- ============================================================
-- Requirement: 统计每日各城市订单数量，按城市分组
-- Target Table: city_daily_stats
-- Source Tables: dwd.order_detail
-- Author: aqueduct
-- Date: 2026-06-10
-- Description: Daily scatter order count by city
-- ============================================================

insert overwrite table city_daily_stats partition (inc_day = '${{bizdate}}')
select
    city,
    count(order_id) as order_cnt
from dwd.order_detail
where inc_day = '${{bizdate}}'
    and order_type = 'scatter'
group by city
;
```
