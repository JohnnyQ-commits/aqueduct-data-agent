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

        with (
            patch.object(CodeReviewSkill, "execute", capture_execute),
            patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="审查结果"),
            patch("src.aqueduct.engine.nodes.helpers.save_artifact", return_value="output/test.md"),
        ):
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

        with (
            patch("src.aqueduct.engine.nodes.requirement._recall_domain_knowledge"),
            patch("src.aqueduct.engine.nodes.requirement.get_skill") as mock_skill,
            patch("src.aqueduct.engine.nodes.requirement.call_llm", return_value="摘要"),
            patch("src.aqueduct.engine.nodes.requirement.save_artifact", return_value=""),
        ):
            mock_skill.return_value.execute.return_value = type(
                "R", (), {"success": True, "data": {"prompt": ""}, "error": ""}
            )()
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
        # coding_style 已被移除，模板中不应出现"编码风格:"标签
        assert "编码风格:" not in prompt


class TestPhase1Standardization:
    """Phase 1 上下文标准化测试。"""

    @staticmethod
    def _make_state() -> dict:
        return {
            "requirement": "统计每日各城市订单数量",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

    def test_phase1_passes_dict_input(self):
        """Phase 1 应向 Skill 传递 dict 类型的 input。"""
        state = self._make_state()
        captured_input = {}

        original_execute = RequirementClarifySkill.execute

        def capture_execute(self_skill, context: SkillContext):
            captured_input["type"] = type(context.input).__name__
            captured_input["value"] = context.input
            return original_execute(self_skill, context)

        with (
            patch.object(RequirementClarifySkill, "execute", capture_execute),
            patch("src.aqueduct.engine.nodes.requirement._recall_domain_knowledge"),
            patch("src.aqueduct.engine.nodes.requirement._extract_target_table", return_value=""),
            patch("src.aqueduct.engine.nodes.requirement.call_llm", return_value="摘要"),
            patch("src.aqueduct.engine.nodes.requirement.save_artifact", return_value=""),
        ):
            node_requirement(state)

        assert captured_input["type"] == "dict", f"input 应为 dict，实际为 {captured_input['type']}"

    def test_phase1_no_known_tables_in_prompt(self):
        """Phase 1 prompt 不应包含空的 known_tables。"""
        skill = RequirementClarifySkill()
        context = SkillContext(
            input={"requirement_doc": "统计每日订单量", "domain_context": ""},
            state={},
        )

        result = skill.execute(context)

        assert result.success
        prompt = result.data["prompt"]
        assert "已知源表:" not in prompt


class TestPhase6Redundancy:
    """Phase 6 冗余传递清理测试。"""

    @staticmethod
    def _make_state() -> dict:
        return {
            "requirement": "需求文档",
            "requirement_summary": "需求摘要",
            "design_scheme": "设计方案",
            "ddl_content": "CREATE TABLE t (id bigint)",
            "ddl_file": "output/ddl.sql",
            "sql_content": "SELECT 1",
            "sql_file": "output/sql.sql",
            "review_result": "审查通过",
            "dqc_result": "DQC 通过",
            "validation_result": {"issues": []},
            "lineage_result": {"mermaid": "graph LR"},
            "cost_result": {"total_cost": 0.1},
            "domain_context": "域上下文",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": ["output/test.sql"],
        }

    def test_report_node_only_passes_used_keys(self):
        """report 节点应只传递 Skill 实际读取的键。"""
        state = self._make_state()
        captured_input = {}

        original_execute = ReportDeliverySkill.execute

        def capture_execute(self_skill, context: SkillContext):
            inp = context.input if isinstance(context.input, dict) else {}
            captured_input.update(inp)
            return original_execute(self_skill, context)

        with (
            patch.object(ReportDeliverySkill, "execute", capture_execute),
            patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
            patch("src.aqueduct.engine.nodes.report.call_llm", return_value="报告"),
            patch("src.aqueduct.engine.nodes.report.save_artifact", return_value=""),
            patch("src.aqueduct.tools.registry.get_tool"),
        ):
            node_report(state)

        # Skill 实际使用的键
        expected_keys = {
            "requirement_name",
            "design_scheme",
            "ddl_content",
            "sql_content",
            "dqc_result",
            "lineage_result",
            "domain_context",
        }
        # 不应传递的冗余键
        redundant_keys = {
            "requirement_doc",
            "ddl_file",
            "sql_file",
            "review_result",
            "validation_result",
            "cost_result",
            "artifacts",
        }

        for key in redundant_keys:
            assert key not in captured_input, f"冗余键 {key} 不应被传递"
        for key in expected_keys:
            assert key in captured_input, f"必要键 {key} 缺失"


class TestPhase4Redundancy:
    """Phase 4 冗余加载清理测试。"""

    @staticmethod
    def _make_state() -> dict:
        return {
            "requirement": "完整需求文档" * 50,
            "requirement_summary": "需求摘要：统计每日各城市订单量",
            "ddl_content": "CREATE TABLE stats (city string, cnt bigint) PARTITIONED BY (inc_day string)",
            "design_scheme": "源表: dwd.order_detail, 按 city 分组, COUNT(order_id)",
            "domain_context": "实体: Order",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }

    def test_sql_node_passes_summary_not_full_doc(self):
        """sql 节点应传递 requirement_summary 而非完整 requirement_doc。"""
        state = self._make_state()
        captured_input = {}

        original_execute = SQLDevelopSkill.execute

        def capture_execute(self_skill, context: SkillContext):
            inp = context.input if isinstance(context.input, dict) else {}
            captured_input.update(inp)
            return original_execute(self_skill, context)

        with (
            patch.object(SQLDevelopSkill, "execute", capture_execute),
            patch(
                "src.aqueduct.engine.nodes.helpers.call_llm", return_value="```sql\nSELECT 1\n```"
            ),
            patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", return_value="SELECT 1"),
            patch("src.aqueduct.engine.nodes.helpers.save_artifact", return_value=""),
            patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True),
            patch("src.aqueduct.tools.registry.get_tool"),
        ):
            node_sql(state)

        # 不应传递完整需求文档
        assert "requirement_doc" not in captured_input, "不应传递 requirement_doc"
        # 应传递需求摘要
        assert "requirement_summary" in captured_input, "应传递 requirement_summary"
        assert captured_input["requirement_summary"] == "需求摘要：统计每日各城市订单量"


class TestDDLGenerateCleanup:
    """ddl_generate 死变量清理测试。"""

    def test_ddl_generate_no_field_mapping(self):
        """ddl_generate Skill 的 prompt 不应包含空的 field_mapping。"""
        skill = DDLGenerateSkill()
        context = SkillContext(
            input={
                "design_scheme": "源表: orders, 按 city 分组",
                "target_table": "test_table",
            },
            state={},
        )

        result = skill.execute(context)

        assert result.success
        prompt = result.data["prompt"]
        # field_mapping 应已被移除（模板中不再包含"字段映射"标签）
        assert "字段映射:" not in prompt, "field_mapping 占位符应已从模板中移除"


class TestAllPhaseKeyAlignment:
    """所有 Phase 节点→Skill 键对齐的快照测试。

    确保每个节点传递的键与对应 Skill 读取的键完全匹配。
    """

    def test_phase1_keys(self):
        """Phase 1: requirement node → requirement_clarify skill。"""
        skill = RequirementClarifySkill()
        context = SkillContext(
            input={"requirement_doc": "需求", "domain_context": "域知识"},
            state={"domain_context": "域知识"},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "需求" in prompt
        assert "域知识" in prompt

    def test_phase2_3_keys(self):
        """Phase 2+3: design node → design_ddl skill。"""
        skill = DesignDDLSkill()
        context = SkillContext(
            input={
                "requirement_doc": "需求",
                "domain_context": "域知识",
            },
            state={
                "requirement_summary": "摘要",
                "domain_context": "域知识",
                "target_table": "dw.test_table",
            },
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "需求" in prompt
        assert "dw.test_table" in prompt

    def test_phase4_keys(self):
        """Phase 4: sql node → sql_develop skill。"""
        skill = SQLDevelopSkill()
        context = SkillContext(
            input={
                "requirement_summary": "摘要",
                "ddl_content": "CREATE TABLE t (id bigint)",
                "design_scheme": "设计方案",
                "domain_context": "",
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "摘要" in prompt
        assert "CREATE TABLE" in prompt

    def test_phase4_5_keys(self):
        """Phase 4.5: review node → code_review skill。"""
        skill = CodeReviewSkill()
        context = SkillContext(
            input={
                "requirement_desc": "需求摘要",
                "sql_content": "SELECT 1",
                "domain_context": "",
                "validation_result": {"issues": []},
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "需求摘要" in prompt
        assert "SELECT 1" in prompt

    def test_phase5_keys(self):
        """Phase 5: dqc node → dqc_quality skill。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={
                "ddl_content": "CREATE TABLE t (id bigint)",
                "sql_content": "SELECT 1",
                "domain_context": "业务规则: 金额>0",
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "业务规则: 金额>0" in prompt

    def test_phase6_keys(self):
        """Phase 6: report node → report_delivery skill。"""
        skill = ReportDeliverySkill()
        context = SkillContext(
            input={
                "requirement_name": "测试需求",
                "design_scheme": "设计方案",
                "ddl_content": "CREATE TABLE t (id bigint)",
                "sql_content": "SELECT 1",
                "dqc_result": "DQC 通过",
                "lineage_result": {"mermaid": "graph LR"},
                "domain_context": "域知识",
            },
            state={"metadata": {"requirement_name": "测试需求"}},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "测试需求" in prompt
        assert "graph LR" in prompt
