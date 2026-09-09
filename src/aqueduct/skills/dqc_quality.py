"""DQC 质检 Skill — DQCQualitySkill。

对应 Phase 5: 数据质量保障。
负责生成全面的质量测试用例，覆盖 5 大测试类别。

P0-3 拆分模式：input 传入 category（单类定义）时渲染单类模板
dqc_quality_category.tpl.md —— 每类一次小调用，根治单发大 prompt 的
确定性思考螺旋（v5 实测 9/9 烧满 32768 零正文）。
不传 category 时保持原全量模板（单发模式）。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class DQCQualitySkill(BaseSkill):
    """DQC 质检 Skill — 注册到全局 Skill 注册中心。"""

    name = "dqc_quality"
    description = "DQC 质检 — 生成质量测试用例，覆盖唯一性、业务反证、一致性、边界、波动"
    version = "1.1.0"
    prompt_template_path = "dqc_quality.tpl.md"
    category_template_path = "dqc_quality_category.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行 DQC 质检流程。

        步骤:
          1. 读取目标表 DDL + 核心 SQL
          2. 读取业务域上下文（domain_context）
          3. 生成测试用例（category 传入时只生成该类，P0-3 拆分模式）
          4. 输出 DQC SQL 文件（含权重标注）
        """
        inp = context.input if isinstance(context.input, dict) else {}

        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        domain_context = inp.get("domain_context") or context.state.get("domain_context", "")
        # TODO-6: 需求摘要 + 设计方案（input 优先，state 兜底，缺失渲染占位）——
        # DQC 用例要测「SQL 该做什么」，不只测「SQL 做了什么」
        requirement_summary = inp.get("requirement_summary") or context.state.get(
            "requirement_summary", ""
        )
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        category = inp.get("category")

        if isinstance(category, dict) and category.get("name"):
            # P0-3 拆分模式：单类小 prompt（约束密度大降，防思考螺旋）
            prompt = self.load_prompt_template(
                template_name=self.category_template_path,
                ddl_content=ddl_content,
                sql_content=sql_content,
                domain_context=domain_context,
                requirement_summary=requirement_summary or "未获取",
                design_scheme=design_scheme or "未获取",
                category_name=category["name"],
                category_focus=category.get("focus", ""),
                category_example=category.get("example", ""),
            )
        else:
            # 全量模式（原单发路径）
            prompt = self.load_prompt_template(
                ddl_content=ddl_content,
                sql_content=sql_content,
                domain_context=domain_context,
                requirement_summary=requirement_summary or "未获取",
                design_scheme=design_scheme or "未获取",
            )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "dqc_ready", "mode": "category" if category else "full"},
        )
