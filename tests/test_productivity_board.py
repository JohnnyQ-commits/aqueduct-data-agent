"""Phase 6 提效看板 DQC 计数接线测试。

双层 bug（修复前）：report.py 读 ``state["dqc_result"]``（dqc.py:338 存的是
DQC SQL **字符串**）→ isinstance dict 分支永不触发 → dqc_tests_run 恒 0；
即便读对 key，字段也对不上——真实数据在 ``state["dqc_results"]``
（dqc.py:403，list[dict]，PASS 口径是 ``m["success"]`` 布尔，见
_generate_dqc_execution_report:490/506），而旧代码查 ``r.get("status") == "PASSED"``。

修复契约：看板从 ``state["dqc_results"]`` 列表取数——
dqc_tests_run=用例数、dqc_auto_fixes=success=True 数；
无执行结果（平台未启用/跳过）时 0/0，看板照常生成。
"""

from __future__ import annotations

from unittest.mock import patch

from src.aqueduct.engine.nodes.report import node_report
from src.aqueduct.tools.base import ToolResult

# 结构契约可通过的最小 Design.md / 知识沉淀（对齐 test_report_parallel fixtures）
_VALID_DOC = (
    "# 测试 — 设计文档\n\n## 需求背景\n背景。\n\n## 设计方案\n方案。\n\n"
    "## 表结构(DDL)\nDDL。\n\n## 核心 SQL\nSQL。\n\n## 血缘图\n血缘。\n"
)
_VALID_KN = (
    "# 知识沉淀 — 测试\n\n### 一、业务域知识\n实体。\n\n### 二、表结构经验\n经验。\n\n"
    "### 三、SQL开发经验\n模式。\n\n### 四、指标口径\n口径。\n\n### 五、待确认事项\n无。\n"
)


def _make_state() -> dict:
    return {
        "requirement": "需求文档",
        "design_scheme": "设计方案",
        "ddl_content": "CREATE TABLE t (id bigint)",
        "sql_content": "SELECT 1",
        "review_result": "审查通过",
        "dqc_result": "-- [用例1] SELECT 1",  # 真实形状：SQL 字符串
        "validation_result": {"issues": []},
        "lineage_result": {"sources": ["t"], "mermaid": "graph LR"},
        "domain_context": "域上下文",
        "metadata": {"requirement_name": "board_test"},
        "errors": [],
        "artifacts": [],
    }


def _run_node_report(state: dict, captured: dict) -> None:
    """跑 node_report，捕获 productivity 工具收到的 kwargs。"""

    class _FakeProdTool:
        def execute(self, **kwargs):
            captured.update(kwargs)
            return ToolResult(
                success=True, data={"report": "# 提效看板", "metrics": {}}, metadata={}
            )

    class _FakeSemanticTool:
        def execute(self, **kwargs):
            return ToolResult(success=True, data={"domain_count": 0, "files": []}, metadata={})

    def fake_get_tool(name: str):
        return _FakeProdTool() if name == "productivity" else _FakeSemanticTool()

    with (
        patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
        patch(
            "src.aqueduct.engine.nodes.report.call_llm",
            side_effect=lambda s, t, p: _VALID_DOC if t == "doc_gen" else _VALID_KN,
        ),
        patch("src.aqueduct.engine.nodes.report.save_artifact", return_value=""),
        patch("src.aqueduct.engine.nodes.report.get_tool", side_effect=fake_get_tool),
    ):
        node_report(state)


class TestProductivityBoardDqcCounts:
    """提效看板 DQC 计数应来自 dqc_results 执行结果列表。"""

    def test_counts_tests_run_and_passed_from_results_list(self):
        state = _make_state()
        state["dqc_results"] = [
            {
                "name": "-- [唯一性]",
                "success": True,
                "rows": [],
                "row_count": 0,
                "error": "",
                "time_ms": 12,
            },
            {
                "name": "-- [非空]",
                "success": False,
                "rows": [],
                "row_count": 0,
                "error": "连接失败",
                "time_ms": 0,
            },
            {
                "name": "-- [分区完整]",
                "success": True,
                "rows": [],
                "row_count": 0,
                "error": "",
                "time_ms": 5,
            },
        ]
        captured: dict = {}
        _run_node_report(state, captured)

        assert captured["dqc_tests_run"] == 3
        assert captured["dqc_auto_fixes"] == 2

    def test_no_execution_results_yields_zero_counts(self):
        """平台未启用/执行跳过 → 无 dqc_results 键 → 0/0，看板照常生成。"""
        state = _make_state()  # 不设 dqc_results
        captured: dict = {}
        _run_node_report(state, captured)

        assert captured["dqc_tests_run"] == 0
        assert captured["dqc_auto_fixes"] == 0
