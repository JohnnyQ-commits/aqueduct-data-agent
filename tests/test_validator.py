"""Validator 工具单元测试。

覆盖 SQL 校验器的 7 项检查规则。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.aqueduct.tools.registry import get_tool
from src.aqueduct.tools.validator import Validator


def _write_sql(content: str) -> Path:
    """将 SQL 内容写入临时文件，返回路径。"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".sql", delete=False, encoding="utf-8"
    )
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
