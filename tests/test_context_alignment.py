"""上下文对齐测试 — 验证节点传递的键与 Skill 读取的键完全匹配。

确保每个 Phase 的 Node → SkillContext → Skill 键名链路完整对齐，
消除死变量、键名不匹配和冗余传递。
"""

from __future__ import annotations

from unittest.mock import patch

from src.aqueduct.engine.nodes.report import node_report
from src.aqueduct.engine.nodes.requirement import node_requirement
from src.aqueduct.engine.nodes.review import node_review
from src.aqueduct.engine.nodes.sql import node_sql
from src.aqueduct.skills.base import SkillContext
from src.aqueduct.skills.code_review import CodeReviewSkill
from src.aqueduct.skills.ddl_generate import DDLGenerateSkill
from src.aqueduct.skills.design_ddl import DesignDDLSkill
from src.aqueduct.skills.dqc_quality import DQCQualitySkill
from src.aqueduct.skills.report_delivery import ReportDeliverySkill
from src.aqueduct.skills.requirement_clarify import RequirementClarifySkill
from src.aqueduct.skills.sql_develop import SQLDevelopSkill


class TestPhase45RequirementDesc:
    """Phase 4.5 requirement_desc 键传递测试。"""

    @staticmethod
    def _make_state() -> dict:
        return {
            "requirement": "完整需求文档内容（应该很长很长很长）" * 10,
            "requirement_summary": "需求摘要：统计每日城市订单量",
            "sql_content": "SELECT city, count(*) FROM orders GROUP BY city",
            "domain_context": "实体: Order (city, order_id)",
            "validation_result": {"issues": []},
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

    def test_review_node_passes_requirement_desc(self):
        """review 节点应将 requirement_summary 作为 requirement_desc 传入 Skill。"""
        state = self._make_state()
        captured_input = {}

        original_execute = CodeReviewSkill.execute

        def capture_execute(self_skill, context: SkillContext):
            inp = context.input if isinstance(context.input, dict) else {}
            captured_input.update(inp)
            return original_execute(self_skill, context)

        with patch.object(CodeReviewSkill, "execute", capture_execute):
            with patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="审查结果"):
                with patch("src.aqueduct.engine.nodes.helpers.save_artifact", return_value="output/test.md"):
                    node_review(state)

        # 核心断言：requirement_desc 必须是摘要而非完整文档
        assert "requirement_desc" in captured_input, "review 节点必须传递 requirement_desc"
        assert captured_input["requirement_desc"] == "需求摘要：统计每日城市订单量"
        assert len(captured_input["requirement_desc"]) < len(state["requirement"])
