"""Phase 5 DQC 需求上下文注入测试（TODO-6：requirement_summary + design_scheme 直达 DQC）。

背景（CLI vs Skill 质量差距根因分析）：CLI 管道的 Phase 5 DQC 只看得到
DDL + SQL + domain_context——从 SQL 反推业务规则是循环论证：用例只能测
「SQL 做了什么」，测不了「SQL 该做什么」。业务反证类用例（不该出现的
数据确实没出现）尤其依赖需求的过滤口径（如「仅统计有效订单」）与设计
方案的取数逻辑。插件模式 DQC 可见需求原文，CLI 盲生成。

契约（三处接线，同 TODO-1/TODO-5 模式）：
- dqc.py build_dqc_category_prompt 的 SkillContext input 加
  requirement_summary + design_scheme
- dqc_quality.py 双渲染路径（P0-3 拆分类 / 全量）都解析并传递两键
  （input 优先，state 兜底，缺失渲染「未获取」占位）
- dqc_quality.tpl.md / dqc_quality_category.tpl.md 输入区各加两行模板变量
"""

from __future__ import annotations

from src.aqueduct.engine.nodes.dqc import build_dqc_category_prompt
from src.aqueduct.skills.base import SkillContext
from src.aqueduct.skills.dqc_quality import DQCQualitySkill

_REQ = "统计每日各城市订单量，zz_req_marker 仅统计已完成的有效订单"
_DESIGN = "## 取数逻辑\nzz_design_marker 排除退款单与测试订单，按城市聚合"
_DDL = "CREATE TABLE zz_ddl_marker (city string, order_cnt bigint) PARTITIONED BY (inc_day string)"
_SQL = "SELECT city, count(1) AS order_cnt FROM dw_demo.dwd_order_info_di WHERE inc_day = '${bizdate}' GROUP BY city"
_CATEGORY = {
    "name": "业务逻辑反证",
    "focus": "不该出现的数据确实没出现（如退款单不应计入有效订单）",
    "example": "-- [业务反证-退款单] 检查退款单是否被排除",
}


def _make_state() -> dict:
    """Phase 5 时点的最小 state（Phase 1/2/3/4 产物均已就位）。"""
    return {
        "ddl_content": _DDL,
        "sql_content": _SQL,
        "domain_context": "",
        "requirement_summary": _REQ,
        "design_scheme": _DESIGN,
    }


class TestDqcSkillRequirementContext:
    """skill 层：dqc_quality 双渲染路径均含 requirement_summary + design_scheme。"""

    def test_category_prompt_contains_requirement_and_design(self):
        """P0-3 拆分类模式（生产主路径）：单类 prompt 含需求与设计上下文。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={
                "ddl_content": _DDL,
                "sql_content": _SQL,
                "domain_context": "",
                "requirement_summary": _REQ,
                "design_scheme": _DESIGN,
                "category": _CATEGORY,
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "业务逻辑反证" in prompt
        assert "zz_req_marker" in prompt, "需求摘要应直达 DQC prompt"
        assert "zz_design_marker" in prompt, "设计方案应直达 DQC prompt"

    def test_full_prompt_contains_requirement_and_design(self):
        """全量模式（无 category）：prompt 同样含两键。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={
                "ddl_content": _DDL,
                "sql_content": _SQL,
                "domain_context": "",
                "requirement_summary": _REQ,
                "design_scheme": _DESIGN,
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "zz_req_marker" in prompt
        assert "zz_design_marker" in prompt

    def test_skill_falls_back_to_state(self):
        """input 未传时从 state 兜底（input → state → default 链）。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={"ddl_content": _DDL, "sql_content": _SQL, "category": _CATEGORY},
            state={"requirement_summary": _REQ, "design_scheme": _DESIGN},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "zz_req_marker" in prompt
        assert "zz_design_marker" in prompt

    def test_missing_context_renders_placeholder(self):
        """两处都没有 → 渲染「未获取」占位，不得泄漏 $变量 或留空白。"""
        skill = DQCQualitySkill()
        context = SkillContext(
            input={"ddl_content": _DDL, "sql_content": _SQL, "category": _CATEGORY},
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "$requirement_summary" not in prompt, "模板变量不得泄漏"
        assert "$design_scheme" not in prompt
        assert prompt.count("未获取") >= 2, (
            "缺失的 requirement_summary/design_scheme 各渲染一处占位"
        )


class TestDqcNodeRequirementContext:
    """node 层：build_dqc_category_prompt 传需求与设计上下文。"""

    def test_node_prompt_includes_requirement_and_design(self):
        state = _make_state()
        prompt = build_dqc_category_prompt(state, _CATEGORY)  # type: ignore[arg-type]
        assert prompt is not None
        assert "业务逻辑反证" in prompt
        assert "zz_req_marker" in prompt
        assert "zz_design_marker" in prompt
