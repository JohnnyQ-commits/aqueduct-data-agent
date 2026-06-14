"""Skills 层测试。"""

from __future__ import annotations

import pytest

from src.aqueduct.exceptions import SkillNotFoundError
from src.aqueduct.skills.base import BaseSkill, SkillContext, SkillResult
from src.aqueduct.skills.registry import get_skill, is_skill_registered, list_skills


class TestSkillRegistry:
    """Skill 注册表测试。"""

    def test_all_core_skills_registered(self):
        expected = [
            "requirement_clarify",
            "design_scheme",
            "ddl_generate",
            "sql_develop",
            "code_review",
            "dqc_quality",
            "report_delivery",
        ]
        for name in expected:
            assert is_skill_registered(name), f"Skill '{name}' not registered"

    def test_get_skill_returns_instance(self):
        skill = get_skill("requirement_clarify")
        assert isinstance(skill, BaseSkill)
        assert skill.name == "requirement_clarify"

    def test_get_unknown_skill_raises(self):
        with pytest.raises(SkillNotFoundError):
            get_skill("nonexistent_skill")

    def test_list_skills_returns_all(self):
        skills = list_skills()
        assert len(skills) >= 7


class TestSkillExecution:
    """Skill 执行测试。"""

    def test_requirement_clarify_needs_input(self):
        skill = get_skill("requirement_clarify")
        context = SkillContext(input={}, state={})
        result = skill.execute(context)
        assert not result.success
        assert "缺少" in result.error or "需求" in result.error

    def test_requirement_clarify_with_content(self):
        skill = get_skill("requirement_clarify")
        context = SkillContext(
            input={"requirement_doc": "查询业务示例工单数据"},
            state={"domain_context": ""},
        )
        result = skill.execute(context)
        assert result.success
        assert "prompt" in result.data

    def test_design_scheme_execution(self):
        skill = get_skill("design_scheme")
        context = SkillContext(
            input={"requirement_doc": "查询工单数据", "domain_context": ""},
            state={},
        )
        result = skill.execute(context)
        assert result.success
        assert "prompt" in result.data

    def test_sql_develop_execution(self):
        skill = get_skill("sql_develop")
        context = SkillContext(
            input={
                "requirement_doc": "查询数据",
                "ddl_content": "CREATE TABLE test (id INT);",
                "design_scheme": "从 source 表取数",
                "domain_context": "",
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        assert "prompt" in result.data

    def test_code_review_execution(self):
        skill = get_skill("code_review")
        context = SkillContext(
            input={
                "sql_content": "SELECT * FROM test",
                "domain_context": "",
                "validation_result": {},
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        assert "prompt" in result.data


class TestSkillResult:
    """SkillResult 数据结构测试。"""

    def test_success_result(self):
        result = SkillResult(success=True, data={"key": "value"})
        assert result.success
        assert result.data["key"] == "value"
        assert result.error == ""

    def test_failure_result(self):
        result = SkillResult(success=False, error="something failed")
        assert not result.success
        assert "failed" in result.error
