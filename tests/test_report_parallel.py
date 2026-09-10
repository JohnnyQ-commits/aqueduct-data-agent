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

# P0-2: Phase6 文档结构契约要求的章节骨架（无骨架的响应会被门禁降级加横幅）
# PERF-4: doc_gen 只产洞察两章——重叠标记必须写进章节正文才会进拼装后的 Design.md
_VALID_INSIGHTS = "## 需求背景\n\n背景。doc-{marker}\n\n## 待确认问题清单\n\n无\n"
_VALID_KN = (
    "# 知识沉淀 — 测试\n\n### 一、业务域知识\n实体。\n\n### 二、表结构经验\n经验。\n\n"
    "### 三、SQL 开发经验\n模式。\n\n### 四、指标口径\n口径。\n\n### 五、待确认事项\n无。\n"
)


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
                return _VALID_INSIGHTS.format(marker="PARALLEL" if overlapped else "SERIAL")
            if task_type == "knowledge_extract":
                started_kn.set()
                overlapped = started_doc.wait(timeout=3)
                return _VALID_KN + f"\n\nkn-{'PARALLEL' if overlapped else 'SERIAL'}\n"
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
        assert "doc-PARALLEL" in saved["Phase6-Design.md"]
        # knowledge_extract ≥100 字符 → 不走 fallback，内容即 LLM 返回
        assert "kn-PARALLEL" in saved["Phase6-知识沉淀.md"]

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


class TestDeliveryReportConfirmations:
    """PERF-11 收尾：review_confirmations 死键接入交付总报告。

    审查 [Confirm] 项（需人工确认的口径/依赖问题）此前止步于 state 键，
    人工只能在 Phase5 审查报告正文里翻——交付总报告是验收人真正读的
    汇总文档，待确认清单应直达。
    """

    @staticmethod
    def _report(state) -> str:
        from src.aqueduct.engine.nodes.report import _generate_delivery_report

        return _generate_delivery_report(state)

    def test_confirmations_listed_with_review_link(self):
        state = _make_state()
        state["review_confirmations"] = [
            {"severity": "Confirm", "message": "(L29) 目标表名与编排参数冲突，需裁决"},
            {"severity": "Confirm", "message": "(L54) 源表粒度未确认：一单一行还是状态流水"},
        ]
        report = self._report(state)

        assert "## 五、待确认事项（审查）" in report
        assert "目标表名与编排参数冲突" in report
        assert "源表粒度未确认" in report
        assert "2 项" in report, "应标注数量"
        assert "Phase5-parallel_test_审查报告.md" in report, "应链接审查报告"
        # 后续节顺延编号
        assert "## 六、交付物清单" in report
        assert "## 七、执行错误" not in report or "## 七、" in report

    def test_no_confirmations_shows_none(self):
        """无 Confirm（审查未跑或零待确认）→ 节在但显示无，编号不变。"""
        state = _make_state()
        report = self._report(state)

        assert "## 五、待确认事项（审查）" in report
        assert "无待确认事项" in report
        assert "## 六、交付物清单" in report

    def test_errors_section_renumbered_to_seven(self):
        """有错误时错误节顺延为七（原六）。"""
        state = _make_state()
        state["errors"] = ["Phase2 降级: 某错误"]
        report = self._report(state)

        assert "## 七、执行错误" in report
        assert "Phase2 降级: 某错误" in report
