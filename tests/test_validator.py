"""Validator 工具单元测试。

覆盖 SQL 校验器的 7 项检查规则 + SQL 规范确定性校验（P0-1）：
新规则（金样本校准，见知识库"金样本特征提取与linter规则校准"）：
  8. CTE 禁止（§6.3）           9. 分区违禁词（§1.1）
  10. CROSS JOIN 禁止（§10.6）  11. 临时表库 tmp_ 前缀（§1.3）
校准：除法分母白名单（nullif/count）、字符串剥离、裸 sum 降 INFO。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from src.aqueduct.tools.registry import get_tool
from src.aqueduct.tools.validator import Validator


def _write_sql(content: str) -> Path:
    """将 SQL 内容写入临时文件，返回路径。"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


class TestCheckSelectStar:
    """检查 1: SELECT * 禁止。"""

    def test_select_star_detected(self):
        path = _write_sql("select * from dw_demo.dwd_order_info_di;")
        v = Validator(path)
        v.lines = ["select * from dw_demo.dwd_order_info_di;"]
        v.check_select_star()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"
        assert "SELECT *" in v.results[0]["message"]

    def test_select_star_allowed_in_union(self):
        """UNION ALL 场景下 SELECT * 允许。"""
        v = Validator("")
        v.lines = [
            "select * from table_a",
            "union all",
            "select * from table_b",
        ]
        v.check_select_star()
        assert len(v.results) == 0

    def test_explicit_columns_ok(self):
        v = Validator("")
        v.lines = ["select order_id, order_amount from dw_demo.dwd_order_info_di;"]
        v.check_select_star()
        assert len(v.results) == 0


class TestCheckPartitionFilter:
    """检查 2: 分区过滤。"""

    def test_missing_partition_filter(self):
        v = Validator("")
        v.lines = [
            "select order_id from dw_demo.dwd_order_info_di",
            "where order_status >= '20';",
        ]
        v.check_partition_filter()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "WARN"
        assert "分区" in v.results[0]["message"]

    def test_with_partition_filter(self):
        v = Validator("")
        v.lines = [
            "select order_id from dw_demo.dwd_order_info_di",
            "where inc_day = '20260101'",
            "  and order_status >= '20';",
        ]
        v.check_partition_filter()
        assert len(v.results) == 0

    def test_no_where_clause(self):
        """无 WHERE 子句时不报警。"""
        v = Validator("")
        v.lines = ["select count(*) from dw_demo.dwd_order_info_di;"]
        v.check_partition_filter()
        assert len(v.results) == 0


class TestCheckKeywordCase:
    """检查 3: 关键字大小写。"""

    def test_uppercase_keywords_warned(self):
        v = Validator("")
        v.lines = ["SELECT order_id FROM dw_demo.dwd_order_info_di;"]
        v.check_keyword_case()
        assert len(v.results) >= 1
        assert any("小写" in r["message"] for r in v.results)

    def test_lowercase_keywords_ok(self):
        v = Validator("")
        v.lines = ["select order_id from dw_demo.dwd_order_info_di;"]
        v.check_keyword_case()
        assert len(v.results) == 0


class TestCheckDivision:
    """检查 4: 除法判零。"""

    def test_raw_division_warned(self):
        v = Validator("")
        v.lines = ["select a / b as ratio from test;"]
        v.check_division()
        assert len(v.results) == 1
        assert "除法" in v.results[0]["message"]

    def test_nvl_protected_division_ok(self):
        v = Validator("")
        v.lines = ["select nvl(a, 0) / nvl(b, 1) as ratio from test;"]
        v.check_division()
        assert len(v.results) == 0


class TestCheckJoinWithoutOn:
    """检查 5: JOIN ON 条件。"""

    def test_join_without_on_warned(self):
        v = Validator("")
        v.lines = [
            "select a.id from table_a a",
            "left join table_b b",
            "where a.status = 1;",
        ]
        v.check_join_without_on()
        assert len(v.results) >= 1
        assert "ON" in v.results[0]["message"]

    def test_join_with_on_ok(self):
        v = Validator("")
        v.lines = [
            "select a.id from table_a a",
            "left join table_b b on a.id = b.id;",
        ]
        v.check_join_without_on()
        assert len(v.results) == 0


class TestCheckNvl:
    """检查 6: SUM NVL。"""

    def test_raw_sum_warned(self):
        v = Validator("")
        v.lines = ["select SUM(order_amount) from dw_demo.dwd_order_info_di;"]
        v.check_nvl()
        assert len(v.results) == 1
        assert "NVL" in v.results[0]["message"]

    def test_sum_with_nvl_ok(self):
        v = Validator("")
        v.lines = ["select SUM(nvl(order_amount, 0)) from dw_demo.dwd_order_info_di;"]
        v.check_nvl()
        assert len(v.results) == 0


class TestCheckStrict:
    """检查 7: 分号结尾（严格模式）。"""

    def test_missing_semicolon_strict(self):
        v = Validator("", strict=True)
        v.lines = ["select 1"]
        v.check_strict()
        assert len(v.results) == 1
        assert "分号" in v.results[0]["message"]

    def test_missing_semicolon_non_strict(self):
        v = Validator("", strict=False)
        v.lines = ["select 1"]
        v.check_strict()
        assert len(v.results) == 0

    def test_with_semicolon_ok(self):
        v = Validator("", strict=True)
        v.lines = ["select 1;"]
        v.check_strict()
        assert len(v.results) == 0


class TestValidatorRun:
    """Validator.run() 集成测试。"""

    def test_file_not_found(self):
        v = Validator("/nonexistent/path.sql")
        result = v.run()
        assert "error" in result

    def test_clean_sql(self):
        sql = (
            "select order_id, nvl(sum(order_amount), 0) as total\n"
            "from dw_demo.dwd_order_info_di\n"
            "where inc_day = '20260101'\n"
            "group by order_id;"
        )
        path = _write_sql(sql)
        try:
            v = Validator(path)
            result = v.run()
            assert result["error_count"] == 0
            assert "issues" in result
        finally:
            path.unlink()

    def test_multiple_issues(self):
        sql = "SELECT * FROM table_a\nleft join table_b\n"
        path = _write_sql(sql)
        try:
            v = Validator(path, strict=True)
            result = v.run()
            assert result["error_count"] >= 1  # SELECT *
            assert result["warn_count"] >= 1  # uppercase keywords, JOIN ON
        finally:
            path.unlink()


class TestValidatorTool:
    """ValidatorTool 注册表集成测试。"""

    def test_tool_registered(self):
        tool = get_tool("validator")
        assert tool.name == "validator"

    def test_execute_missing_param(self):
        tool = get_tool("validator")
        result = tool.execute()
        assert not result.success
        assert "sql_file" in result.error

    def test_execute_success(self):
        sql = "select order_id from dw_demo.dwd_order_info_di where inc_day = '20260101';"
        path = _write_sql(sql)
        try:
            tool = get_tool("validator")
            result = tool.execute(sql_file=str(path))
            assert result.success
        finally:
            path.unlink()


# ============================================================
# P0-1: SQL 规范确定性校验（金样本校准后的规则）
# ============================================================


class TestCheckCte:
    """检查 8: 禁止 CTE（WITH 子句，sql_standards §6.3）。"""

    def test_cte_detected(self):
        v = Validator("")
        v.lines = [
            "with base as (",
            "    select dept_code from dwd.dwd_order_detail_di where inc_day = '20260101'",
            ")",
            "insert overwrite table dw_demo.ads_x",
            "select * from base;",
        ]
        v.check_cte()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"
        assert "CTE" in v.results[0]["message"]

    def test_cte_after_semicolon_detected(self):
        """多语句场景：分号后的 with 也是 CTE。"""
        v = Validator("")
        v.lines = [
            "select 1 from dual;",
            "with b as (select 2 from dual)",
            "select * from b;",
        ]
        v.check_cte()
        assert len(v.results) == 1

    def test_subquery_not_cte(self):
        """普通子查询不是 CTE，不误报。"""
        v = Validator("")
        v.lines = [
            "select b.dept_code",
            "from (",
            "    select dept_code from dwd.dwd_order_detail_di where inc_day = '20260101'",
            ") b;",
        ]
        v.check_cte()
        assert len(v.results) == 0

    def test_comment_line_ignored(self):
        v = Validator("")
        v.lines = ["-- with base as (旧写法备注)", "select 1 from dual;"]
        v.check_cte()
        assert len(v.results) == 0


class TestCheckForbiddenPartitionCols:
    """检查 9: 分区字段违禁词（sql_standards §1.1，统一 inc_day）。"""

    def test_cur_date_detected(self):
        v = Validator("")
        v.lines = ["select order_id from dwd.dwd_order_di where cur_date = '20260101';"]
        v.check_forbidden_partition_cols()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"
        assert "inc_day" in v.results[0]["message"]

    def test_data_date_detected(self):
        v = Validator("")
        v.lines = ["select 1 from t where data_date = '20260101';"]
        v.check_forbidden_partition_cols()
        assert len(v.results) == 1

    def test_inc_day_ok(self):
        v = Validator("")
        v.lines = ["select 1 from t where inc_day = '20260101';"]
        v.check_forbidden_partition_cols()
        assert len(v.results) == 0

    def test_comment_ignored(self):
        v = Validator("")
        v.lines = ["-- 源表用的是 cur_date（历史遗留），映射为 inc_day", "select 1 from t;"]
        v.check_forbidden_partition_cols()
        assert len(v.results) == 0

    def test_inline_comment_partition_word_not_flagged(self):
        """行中注释里的违禁词（如 cur_date）不应报错。

        回归来源：check_division 同款行中注释缺陷——
        行中 `-- 注释` 里的 cur_date 若不剥离会误报。
        """
        v = Validator("")
        v.lines = [
            "  and inc_day = '$[time(yyyyMMdd,-1)]'  -- 兼容旧分区 cur_date 写法",
        ]
        v.check_forbidden_partition_cols()
        assert len(v.results) == 0


class TestCheckCrossJoin:
    """检查 10: 禁止 CROSS JOIN 造维度骨架（sql_standards §10.6）。"""

    def test_cross_join_detected(self):
        v = Validator("")
        v.lines = [
            "from dim_dept d",
            "cross join (select distinct period from t) dp;",
        ]
        v.check_cross_join()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"
        assert "CROSS JOIN" in v.results[0]["message"]

    def test_inner_join_ok(self):
        v = Validator("")
        v.lines = ["inner join (select distinct dept_code from dim) d on a.id = d.id;"]
        v.check_cross_join()
        assert len(v.results) == 0


class TestCheckTmpDatabase:
    """检查 11: 临时表库名必须 tmp_ 前缀（sql_standards §1.3，金样本校准：tmp_ + 业务库）。"""

    def test_create_table_non_tmp_db_detected(self):
        v = Validator("")
        v.lines = [
            "create table dw_demo.tmp_base stored as parquet as",
            "select dept_code from dwd.dwd_order_detail_di;",
        ]
        v.check_tmp_database()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"
        assert "tmp_" in v.results[0]["message"]

    def test_create_table_tmp_db_ok(self):
        """金样本真实模式：tmp_ + 业务库（tmp_dm_tc_waybillinfo），零误报。"""
        v = Validator("")
        v.lines = [
            "create table tmp_dw_demo.tmp_base_$[time(yyyyMMdd,-1d)] stored as parquet as",
            "select dept_code from dwd.dwd_order_detail_di;",
        ]
        v.check_tmp_database()
        assert len(v.results) == 0

    def test_drop_if_exists_non_tmp_detected(self):
        v = Validator("")
        v.lines = ["drop table if exists dw_demo.tmp_base;"]
        v.check_tmp_database()
        assert len(v.results) == 1

    def test_create_if_not_exists_ok(self):
        """DDL 正式表（create table if not exists）不受限。"""
        v = Validator("")
        v.lines = ["create table if not exists dw_demo.ads_x (order_cnt bigint);"]
        v.check_tmp_database()
        assert len(v.results) == 0

    def test_create_external_ok(self):
        """外部表 DDL 不受限。"""
        v = Validator("")
        v.lines = ["create external table dw_demo.ads_x (order_cnt bigint);"]
        v.check_tmp_database()
        assert len(v.results) == 0


class TestCheckDivisionCalibration:
    """检查 4 校准：分母白名单 + 字符串剥离 + 级别升 ERROR。"""

    def test_nullif_protected_ok(self):
        """nullif 显式白名单（此前靠子串 if 碰巧通过）。"""
        v = Validator("")
        v.lines = ["select round(a / nullif(b, 0), 4) as ratio from t;"]
        v.check_division()
        assert len(v.results) == 0

    def test_count_denominator_ok(self):
        """金样本 CR-001 模式：sum / count(distinct inc_day) 周日均分母，合法。"""
        v = Validator("")
        v.lines = [
            "select round(sum(order_cnt) / count(distinct inc_day), 2) as daily_avg",
            "from t group by dept_code;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_format_string_not_division(self):
        """格式串 'yyyy/MM/dd' 中的斜杠不是除法（字符串剥离）。"""
        v = Validator("")
        v.lines = [
            "select date_format(to_date(inc_day, 'yyyyMMdd'), 'yyyy/MM/dd') as dt from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_inline_comment_division_not_flagged(self):
        """行中注释里的斜杠短语（如 label_time/label_name）不是除法。

        回归来源：金样本公交共配标签 L39 行中注释误报。
        """
        v = Validator("")
        v.lines = [
            "       ,is_bus_common  -- ★ 本次新增：公交共配属性标签，不参与 label_time/label_name 变更检测",
            "FROM",
            "(",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_raw_division_is_error(self):
        """裸除法升级为 ERROR（§7.2 必须 nullif，金样本全部合规）。"""
        v = Validator("")
        v.lines = ["select a / b as ratio from t;"]
        v.check_division()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"


class TestDivisionCaseGuard:
    """检查 4 补全：CASE WHEN 分母守护识别（跨行/同行形态）。

    回归来源：2026-09-09 perf4-check eval——canonical SQL 写的是
    case when t1.order_count = 0 then null else cast(t1.gmv / t1.order_count ...)，
    旧逻辑只认"上一行 when ... > 0"单行形态：跨行守护 + = 0 判零 + 限定名
    分母（t1.order_count 捕获成 t1）三重漏判，eval 误判 FAIL。
    """

    def test_multiline_case_zero_guard_ok(self):
        """跨行 = 0 守护（eval 实际形态）：零分支置 null、除法在 else，合法。"""
        v = Validator("")
        v.lines = [
            "select",
            "    case when t1.order_count = 0 then null",
            "        else cast(t1.gmv / t1.order_count as decimal(20,4))",
            "    end as avg_order_amount",
            "from t1;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_one_line_case_zero_guard_ok(self):
        """守护与除法同行：case when x = 0 then null else ... / x，合法。"""
        v = Validator("")
        v.lines = [
            "select case when order_count = 0 then null else cast(gmv / order_count as decimal(20,4)) end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_positive_guard_then_branch_ok(self):
        """> 0 守护 + 除法在 then 分支（守护成立才除），合法。"""
        v = Validator("")
        v.lines = [
            "select case when order_count > 0 then gmv / order_count else null end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_division_in_zero_branch_still_flagged(self):
        """除法位于 = 0 分支内（恰在分母为零时执行）——必须仍报，修复不得过度放行。"""
        v = Validator("")
        v.lines = [
            "select case when order_count = 0 then gmv / order_count else null end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 1

    def test_guard_on_other_column_still_flagged(self):
        """守护条件是别的字段（与分母无关）——必须仍报。"""
        v = Validator("")
        v.lines = [
            "select",
            "    case when other_flag = 0 then null",
            "        else cast(t1.gmv / t1.order_count as decimal(20,4))",
            "    end as x",
            "from t;",
        ]
        v.check_division()
        assert len(v.results) == 1

    def test_multiline_ne_zero_guard_ok(self):
        """跨行 != 0 守护：then 支路除法（守护成立才除），合法。"""
        v = Validator("")
        v.lines = [
            "select",
            "    case when a.order_count != 0 then",
            "        cast(a.gmv / a.order_count as decimal(18,4))",
            "    else null end as avg_order_amount",
            "from t1;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_one_line_ne_zero_guard_ok(self):
        """同行 != 0 守护 + 限定名分母：case when t1.cnt != 0 then t1.gmv / t1.cnt。"""
        v = Validator("")
        v.lines = [
            "select case when t1.cnt != 0 then t1.gmv / t1.cnt else null end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_angle_bracket_ne_zero_guard_ok(self):
        """<> 0 守护形态（标准 SQL 不等号写法）。"""
        v = Validator("")
        v.lines = [
            "select case when order_count <> 0 then gmv / order_count end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_ne_zero_with_not_null_combo_ok(self):
        """is not null and != 0 复合守护：先判空再判零，合法。"""
        v = Validator("")
        v.lines = [
            "select case when b is not null and b != 0 then a / b else null end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 0

    def test_ne_zero_division_in_else_branch_flagged(self):
        """!= 0 守护下除法在 else 支路（恰在分母为零时执行）——必须仍报。"""
        v = Validator("")
        v.lines = [
            "select case when order_count != 0 then null else gmv / order_count end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 1

    def test_gt_zero_division_in_else_branch_flagged(self):
        """> 0 守护下除法在 else 支路——同样必须报（正守护只保护 then 支路）。"""
        v = Validator("")
        v.lines = [
            "select case when order_count > 0 then null else gmv / order_count end as x from t;",
        ]
        v.check_division()
        assert len(v.results) == 1

    def test_prev_line_positive_guard_then_null_flagged(self):
        """上一行正守护 + then 置空、除法在下一行 else 支路——旧单行捷径误放行，必须报。"""
        v = Validator("")
        v.lines = [
            "select",
            "    case when order_count > 0 then null",
            "        else gmv / order_count",
            "    end as x",
            "from t;",
        ]
        v.check_division()
        assert len(v.results) == 1


class TestCheckKeywordCaseLevel:
    """检查 3 校准：关键字大写升级为 ERROR（§2.1 硬规范，金样本校准确认）。"""

    def test_uppercase_keyword_is_error(self):
        v = Validator("")
        v.lines = ["SELECT  emp_code"]
        v.check_keyword_case()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "ERROR"


class TestCheckNvlDowngrade:
    """检查 6 校准：裸 sum 降为 INFO（金样本遍地 sum(裸字段)，合法写法）。"""

    def test_raw_sum_is_info(self):
        v = Validator("")
        v.lines = ["select sum(order_amount) from t;"]
        v.check_nvl()
        assert len(v.results) == 1
        assert v.results[0]["level"] == "INFO"


class TestContentMode:
    """内容模式：review 修复循环复检需要直接校验 state['sql_content']，不落盘。"""

    def test_validate_from_content(self):
        sql = "select a / b from t;"
        v = Validator("", content=sql)
        report = v.run()
        assert report["error_count"] >= 1

    def test_content_mode_skips_file_check(self):
        """content 模式下文件不存在也正常（修复循环场景 SQL 只在内存）。"""
        v = Validator("/nonexistent/path.sql", content="select 1 from t;")
        report = v.run()
        assert "error" not in report

    def test_golden_style_clean(self):
        """金样本风格合成 SQL（脱敏）：tmp_demo 库 + nullif + count(distinct inc_day)
        分母 + 周范围过滤，全规则零 ERROR——linter 上线的零误报基准。"""
        sql = "\n".join(
            [
                "-- ============================================================================",
                "-- 测试金样本风格 SQL（合成，脱敏自电商网点效能周表）",
                "-- 目标表: dw_demo.ads_demo_stat_di",
                "-- ============================================================================",
                "drop table if exists tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)];",
                "create table tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)]",
                "stored as parquet as",
                "select",
                "    dept_code,",
                "    sum(order_cnt)                                            as order_cnt,",
                "    round(sum(order_amount) / nullif(sum(order_cnt), 0), 4)   as avg_amount,",
                "    round(sum(pickup_cnt) / count(distinct inc_day), 2)       as daily_avg",
                "from (",
                "    select",
                "        dept_code,",
                "        inc_day,",
                "        order_cnt,",
                "        order_amount,",
                "        pickup_cnt",
                "    from dwd.dwd_order_detail_di",
                "    where inc_day between '$[monday(yyyyMMdd,-1d)]'",
                "                      and date_format(date_add(to_date('$[monday(yyyyMMdd,-1d)]', 'yyyyMMdd'), 6), 'yyyyMMdd')",
                ") d",
                "group by dept_code",
                ";",
                "",
                "insert overwrite table dw_demo.ads_demo_stat_di partition (inc_day = '$[time(yyyyMMdd,-1d)]')",
                "select",
                "    dept_code,",
                "    order_cnt,",
                "    avg_amount,",
                "    daily_avg",
                "from tmp_demo.tmp_demo_weekly_$[time(yyyyMMdd,-1d)]",
                ";",
            ]
        )
        v = Validator("", content=sql)
        report = v.run()
        assert report["error_count"] == 0, f"金样本零误报失败: {report['issues']}"


class TestReviewLintInjection:
    """review 节点注入确定性校验结果：ERROR → Critical → 触发修复循环。"""

    @patch("src.aqueduct.engine.nodes.review.start_dqc_speculative")
    @patch("src.aqueduct.engine.nodes.review.save_artifact")
    @patch("src.aqueduct.engine.nodes.review.call_llm")
    def test_review_injects_lint_critical(self, mock_llm, mock_save, mock_spec):
        """LLM 审查通过但 SQL 含 CTE → 规范 Critical 注入 issues 并触发修复循环。"""
        from src.aqueduct.engine.nodes.review import node_review

        mock_llm.return_value = "审查通过，未发现问题。"
        state = {
            "sql_content": (
                "with base as (\n"
                "    select dept_code from dwd.dwd_order_detail_di where inc_day = '20260101'\n"
                ")\n"
                "insert overwrite table dw_demo.ads_x\n"
                "select dept_code, count(*) as cnt from base group by dept_code;"
            ),
            "metadata": {"requirement_name": "lint_injection_test"},
            "errors": [],
            "artifacts": [],
        }
        result = node_review(state)
        assert result["_needs_fix_loop"] is True
        assert any(
            i["severity"] == "Critical" and "CTE" in i["message"] for i in result["_review_issues"]
        )

    @patch("src.aqueduct.engine.nodes.review.start_dqc_speculative")
    @patch("src.aqueduct.engine.nodes.review.save_artifact")
    @patch("src.aqueduct.engine.nodes.review.call_llm")
    def test_review_clean_sql_no_loop(self, mock_llm, mock_save, mock_spec):
        """干净 SQL + LLM 审查通过 → 不触发修复循环。"""
        from src.aqueduct.engine.nodes.review import node_review

        mock_llm.return_value = "审查通过，未发现问题。"
        state = {
            "sql_content": (
                "insert overwrite table dw_demo.ads_x partition (inc_day = '20260101')\n"
                "select dept_code, count(*) as cnt\n"
                "from (\n"
                "    select dept_code from dwd.dwd_order_detail_di where inc_day = '20260101'\n"
                ") d\n"
                "group by dept_code;"
            ),
            "metadata": {"requirement_name": "lint_clean_test"},
            "errors": [],
            "artifacts": [],
        }
        result = node_review(state)
        assert result.get("_needs_fix_loop") is False
