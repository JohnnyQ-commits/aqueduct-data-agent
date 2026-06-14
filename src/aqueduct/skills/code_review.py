"""代码评审 Skill — CodeReviewSkill。

对应 Phase 4.5: 代码审查。
负责审查线上版本与变更版本的差异。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class CodeReviewSkill(BaseSkill):
    """代码评审 Skill — 注册到全局 Skill 注册中心。"""

    name = "code_review"
    description = "代码审查 — 差异比对、需求覆盖度验证、下游影响分析"
    version = "1.0.0"
    prompt_template_path = "code_review.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行代码审查流程。

        步骤:
          1. 读取线上版本 SQL
          2. 读取变更版本 SQL
          3. 逐行差异比对
          4. 需求覆盖度验证
          5. 下游影响分析
          6. 潜在问题检查
        """
        inp = context.input if isinstance(context.input, dict) else {}

        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        online_sql = inp.get("online_sql") or context.state.get("online_sql", "")
        changed_sql = inp.get("changed_sql") or context.state.get("changed_sql", "")
        requirement_desc = (
            inp.get("requirement_desc")
            or context.state.get("requirement_desc", "")
            or context.state.get("requirement", "")
        )
        validation_result = inp.get("validation_result") or context.state.get(
            "validation_result", {}
        )

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_desc=requirement_desc,
            online_sql=online_sql,
            changed_sql=changed_sql,
            sql_content=sql_content,
            domain_context=context.state.get("domain_context", ""),
            validation_result=validation_result,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "review_ready"},
        )
