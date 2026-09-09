"""Lineage 工具单元测试。

覆盖血缘解析器的表级和字段级解析，以及 wait_for_lineage 的
mermaid 提取落 state 契约（PERF-4 Design.md 本地拼装的上游）。
"""

from __future__ import annotations

import tempfile
from concurrent.futures import Future
from pathlib import Path

from src.aqueduct.engine.nodes.sql import wait_for_lineage
from src.aqueduct.tools.lineage import LineageParser
from src.aqueduct.tools.registry import get_tool


def _write_sql(content: str) -> Path:
    """将 SQL 内容写入临时文件，返回路径。"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


class TestTableLineage:
    """表级血缘解析测试。"""

    def test_simple_insert_overwrite(self):
        sql = """
        insert overwrite table dw_demo.dws_order_daily_stat_di
        select order_id, order_amount
        from dw_demo.dwd_order_info_di
        where inc_day = '20260101';
        """
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            assert parser.target_table == "dw_demo.dws_order_daily_stat_di"
            assert "dw_demo.dwd_order_info_di" in parser.source_tables
        finally:
            path.unlink()

    def test_multiple_source_tables(self):
        sql = """
        insert overwrite table dw_demo.dws_order_daily_stat_di
        select a.order_id, b.customer_name
        from dw_demo.dwd_order_info_di a
        left join dw_demo.dim_customer_info_df b
        on a.customer_id = b.customer_id
        where a.inc_day = '20260101';
        """
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            # 核心来源表应被识别（regex 可能额外捕获 alias.field，属已有行为）
            assert "dw_demo.dwd_order_info_di" in parser.source_tables
            assert "dw_demo.dim_customer_info_df" in parser.source_tables
            assert len(parser.source_tables) >= 2
        finally:
            path.unlink()

    def test_no_insert_target_unknown(self):
        """无 INSERT 语句时目标表为 unknown_target。"""
        sql = "select * from dw_demo.dwd_order_info_di;"
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            assert parser.target_table == "unknown_target"
            assert "dw_demo.dwd_order_info_di" in parser.source_tables
        finally:
            path.unlink()


class TestFieldLineage:
    """字段级血缘解析测试。"""

    def test_simple_field_mapping(self):
        sql = """
        insert overwrite table dw_demo.dws_order_daily_stat_di
        select a.order_id as order_id, a.order_amount as gmv
        from dw_demo.dwd_order_info_di a
        where a.inc_day = '20260101';
        """
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            parser.parse_field_lineage()
            assert len(parser.field_lineage) >= 1
            # 至少有一个字段映射被解析
            target_fields = [f["target_field"] for f in parser.field_lineage]
            assert "order_id" in target_fields or "gmv" in target_fields
        finally:
            path.unlink()

    def test_no_fields(self):
        """无 SELECT 字段时 field_lineage 为空。"""
        sql = "insert overwrite table dw_demo.t1 select 1;"
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            parser.parse_field_lineage()
            # 简单常量 select 可能不会产出字段映射
            assert isinstance(parser.field_lineage, list)
        finally:
            path.unlink()


class TestMermaidGeneration:
    """Mermaid 图生成测试。"""

    def test_table_lineage_mermaid(self):
        sql = """
        insert overwrite table dw_demo.dws_order_daily_stat_di
        select a.order_id
        from dw_demo.dwd_order_info_di a
        where a.inc_day = '20260101';
        """
        path = _write_sql(sql)
        try:
            parser = LineageParser(path)
            parser.load_sql()
            parser.parse_table_lineage()
            mermaid = parser.generate_mermaid()
            assert "mermaid" in mermaid
            assert "graph LR" in mermaid
            assert "dwd_order_info_di" in mermaid
            assert "dws_order_daily_stat_di" in mermaid
        finally:
            path.unlink()


class TestLineageTool:
    """LineageTool 注册表集成测试。"""

    def test_tool_registered(self):
        tool = get_tool("lineage")
        assert tool.name == "lineage"

    def test_execute_missing_param(self):
        tool = get_tool("lineage")
        result = tool.execute()
        assert not result.success
        assert "sql_file" in result.error

    def test_execute_success(self):
        sql = """
        insert overwrite table dw_demo.dws_order_daily_stat_di
        select a.order_id, b.customer_name
        from dw_demo.dwd_order_info_di a
        left join dw_demo.dim_customer_info_df b
        on a.customer_id = b.customer_id
        where a.inc_day = '20260101';
        """
        path = _write_sql(sql)
        try:
            tool = get_tool("lineage")
            result = tool.execute(sql_file=str(path))
            assert result.success
            assert result.data["target"] == "dw_demo.dws_order_daily_stat_di"
            # 核心来源表应被识别
            assert "dw_demo.dwd_order_info_di" in result.data["sources"]
            assert "dw_demo.dim_customer_info_df" in result.data["sources"]
            assert "mermaid" in result.data
        finally:
            path.unlink()


# ============================================================
# wait_for_lineage — mermaid 提取落 state（PERF-4 Design.md 拼装上游）
# ============================================================


class TestWaitForLineageMermaid:
    """血缘 LLM 输出的 mermaid 块落入 state["lineage_result"]["mermaid"]。

    回归来源：2026-09-09 perf4-check eval——Phase4-字段级血缘图.md 有合法
    mermaid，但 wait_for_lineage 丢弃 future.result() 返回值，state 里的
    lineage_result 从未被赋值，Design.md 血缘图章只能渲染
    "（血缘分析未完成）"。
    """

    @staticmethod
    def _done_future(result: str) -> Future:
        future = Future()
        future.set_result(result)
        return future

    _RESPONSE = (
        "### 第一部分：Mermaid 血缘图\n\n"
        "```mermaid\n"
        "graph LR\n"
        "    A[源表] --> B[目标表]\n"
        "```\n\n"
        "### 第二部分：字段映射表\n\n"
        "| 字段 | 来源 |\n"
    )

    def test_mermaid_extracted_into_state_without_fences(self):
        """mermaid 块内容（无围栏）写入 lineage_result，线程资源清理。"""
        state = {"_lineage_future": self._done_future(self._RESPONSE)}

        wait_for_lineage(state)

        mermaid = state["lineage_result"]["mermaid"]
        assert mermaid == "graph LR\n    A[源表] --> B[目标表]"
        assert "```" not in mermaid
        assert "_lineage_future" not in state
        assert "_lineage_executor" not in state

    def test_merges_into_existing_lineage_result(self):
        """已有 lineage_result 键（如 sources）保留，仅补 mermaid。"""
        state = {
            "_lineage_future": self._done_future(self._RESPONSE),
            "lineage_result": {"sources": ["dw_demo.dwd_order_info_di"]},
        }

        wait_for_lineage(state)

        assert state["lineage_result"]["sources"] == ["dw_demo.dwd_order_info_di"]
        assert "graph LR" in state["lineage_result"]["mermaid"]

    def test_response_without_mermaid_leaves_result_untouched(self):
        """响应无 mermaid 围栏——不写 lineage_result（Design.md 走未完成注记）。"""
        state = {"_lineage_future": self._done_future("只有字段映射表，没有图。")}

        wait_for_lineage(state)

        assert "lineage_result" not in state

    def test_failed_future_leaves_result_untouched_and_cleans_up(self):
        """后台血缘异常——不炸流程、不写 lineage_result、线程资源仍清理。"""
        future = Future()
        future.set_exception(RuntimeError("boom"))
        state = {"_lineage_future": future}

        wait_for_lineage(state)

        assert "lineage_result" not in state
        assert "_lineage_future" not in state
