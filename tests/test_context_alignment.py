"""上下文对齐测试 — 验证节点传递的键与 Skill 读取的键完全匹配。

确保每个 Phase 的 Node → SkillContext → Skill 键名链路完整对齐，
消除死变量、键名不匹配和冗余传递。
"""

from __future__ import annotations

from unittest.mock import patch

from src.aqueduct.engine.nodes.report import node_report
from src.aqueduct.engine.nodes.requirement import _extract_target_table, node_requirement
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


class TestTargetTableExtraction:
    """target_table 提取测试。"""

    def test_extract_from_schema_dot_table(self):
        """从 'schema.table' 格式中提取目标表名。"""
        text = "请将数据写入 dw_report.city_daily_stats 表中"
        assert _extract_target_table(text) == "dw_report.city_daily_stats"

    def test_extract_from_create_table(self):
        """从 CREATE TABLE 语句中提取目标表名。"""
        text = "目标表结构如下：CREATE TABLE dw_demo.order_stats (id bigint)"
        assert _extract_target_table(text) == "dw_demo.order_stats"

    def test_extract_from_chinese_hint(self):
        """从中文提示'目标表: xxx'中提取。"""
        text = "目标表：dwd_order_detail"
        assert _extract_target_table(text) == "dwd_order_detail"

    def test_returns_empty_when_not_found(self):
        """无表名时返回空字符串。"""
        text = "这个需求没有指定具体表名"
        assert _extract_target_table(text) == ""

    def test_node_writes_target_table_to_state(self):
        """node_requirement 应将提取的 target_table 写入 state。"""
        state = {
            "requirement": "统计每日各城市订单数量，写入 dw_report.city_order_stats",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

        with patch("src.aqueduct.engine.nodes.requirement._recall_domain_knowledge"):
            with patch("src.aqueduct.engine.nodes.requirement.get_skill") as mock_skill:
                mock_skill.return_value.execute.return_value = type(
                    "R", (), {"success": True, "data": {"prompt": ""}, "error": ""}
                )()
                with patch("src.aqueduct.engine.nodes.requirement.call_llm", return_value="摘要"):
                    with patch("src.aqueduct.engine.nodes.requirement.save_artifact", return_value=""):
                        node_requirement(state)

        assert state.get("target_table") == "dw_report.city_order_stats"


class TestPhase5DomainContext:
    """Phase 5 domain_context 键名对齐测试。"""

    def test_dqc_skill_uses_domain_context(self):
        """dqc_quality Skill 应将 domain_context 传入 prompt 模板。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={
                "ddl_content": "CREATE TABLE test (id bigint) PARTITIONED BY (inc_day string)",
                "sql_content": "SELECT id FROM source WHERE inc_day = '20260101'",
                "domain_context": "实体: Order (order_id, city, amount). 规则: 金额必须大于 0",
            },
            state={},
        )

        result = skill.execute(context)

        assert result.success
        prompt = result.data["prompt"]
        # 核心断言：domain_context 的内容必须出现在 prompt 中
        assert "实体: Order" in prompt, "domain_context 内容应出现在 DQC prompt 中"
        assert "金额必须大于 0" in prompt, "业务规则应出现在 DQC prompt 中"


class TestDeadVariableCleanup:
    """死变量清理验证测试。"""

    def test_sql_develop_no_coding_style(self):
        """sql_develop Skill 的 prompt 不应包含空的 coding_style。"""
        skill = SQLDevelopSkill()
        context = SkillContext(
            input={
                "requirement_doc": "统计每日订单量",
                "ddl_content": "CREATE TABLE t (id bigint)",
                "design_scheme": "源表: orders, 按 city 分组",
                "domain_context": "",
            },
            state={},
        )

        result = skill.execute(context)

        assert result.success
        prompt = result.data["prompt"]
        # coding_style 已被移除，模板中不应出现空的"编码风格:"行
        assert "编码风格:" not in prompt or "编码风格: \n" not in prompt
