"""Phase 6 报告节点并行化测试（PERF-3）。

doc_gen 与 knowledge_extract 两次 LLM 调用输入互相独立（都来自 state），
串行执行浪费一整次调用的时间；并行后 Phase 6 耗时 ≈ max(两次调用) 而非求和。
"""

from __future__ import annotations

import threading
from unittest.mock import patch

from src.aqueduct.engine.nodes.report import node_report

# _generate_knowledge_doc 要求 LLM 返回 ≥100 字符才不走 fallback
_PAD = "x" * 120


def _make_state() -> dict:
    return {
        "requirement": "需求文档",
        "requirement_summary": "需求摘要",
        "design_scheme": "设计方案",
        "ddl_content": "CREATE TABLE t (id bigint)",
        "sql_content": "SELECT 1",
        "review_result": "审查通过",
        "dqc_result": {"results": []},
        "validation_result": {"issues": []},
        "lineage_result": {"sources": ["t"], "mermaid": "graph LR"},
        "domain_context": "域上下文",
        "metadata": {"requirement_name": "parallel_test"},
        "errors": [],
        "artifacts": [],
    }


class TestReportParallelCalls:
    """doc_gen 与 knowledge_extract 应并行执行。"""

    def test_doc_gen_and_knowledge_extract_overlap(self):
        """两次 LLM 调用重叠执行：后启动者能观察到先启动者仍在运行。"""

        state = _make_state()
        started_doc = threading.Event()
        started_kn = threading.Event()

        def fake_call_llm(state, task_type, prompt):
            if task_type == "doc_gen":
                started_doc.set()
                overlapped = started_kn.wait(timeout=3)
                return ("doc-PARALLEL" if overlapped else "doc-SERIAL") + _PAD
            if task_type == "knowledge_extract":
                started_kn.set()
                overlapped = started_doc.wait(timeout=3)
                return ("kn-PARALLEL" if overlapped else "kn-SERIAL") + _PAD
            return "other" + _PAD

        saved: dict[str, str] = {}

        def fake_save(state, filename, content):
            saved[filename] = content
            return filename

        with (
            patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
            patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_call_llm),
            patch("src.aqueduct.engine.nodes.report.save_artifact", side_effect=fake_save),
            patch("src.aqueduct.engine.nodes.report.get_tool"),
        ):
            node_report(state)

        # 串行实现下 doc_gen 先跑、等 3s 超时返回 doc-SERIAL
        assert saved["Phase6-Design.md"].startswith("doc-PARALLEL")
        # knowledge_extract ≥100 字符 → 不走 fallback，内容即 LLM 返回
        assert saved["Phase6-知识沉淀.md"].startswith("kn-PARALLEL")

    def test_artifact_save_order_unchanged(self):
        """并行化不改变产出物保存顺序（Design → 交付总报告 → 知识沉淀）。"""

        state = _make_state()
        saved_order: list[str] = []

        def fake_save(state, filename, content):
            saved_order.append(filename)
            return filename

        with (
            patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
            patch(
                "src.aqueduct.engine.nodes.report.call_llm",
                side_effect=lambda s, t, p: "ok" + _PAD,
            ),
            patch("src.aqueduct.engine.nodes.report.save_artifact", side_effect=fake_save),
            patch("src.aqueduct.engine.nodes.report.get_tool"),
        ):
            node_report(state)

        assert saved_order[:3] == [
            "Phase6-Design.md",
            "Phase6-交付总报告.md",
            "Phase6-知识沉淀.md",
        ]

    def test_doc_gen_failure_records_error(self):
        """doc_gen 失败仍记入 errors（并行不吞异常）。"""
        from src.aqueduct.exceptions import LLMTimeoutError

        state = _make_state()

        def fake_call_llm(state, task_type, prompt):
            if task_type == "doc_gen":
                raise LLMTimeoutError("doc_gen 超时")
            return "kn" + _PAD

        with (
            patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
            patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_call_llm),
            patch("src.aqueduct.engine.nodes.report.save_artifact", return_value=""),
            patch("src.aqueduct.engine.nodes.report.get_tool"),
        ):
            node_report(state)

        assert any("doc_gen 超时" in e for e in state["errors"])
