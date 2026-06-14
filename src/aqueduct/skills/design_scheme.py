"""方案设计 Skill — DesignSchemeSkill。

对应 Phase 2: 设计方案。
负责输出取数逻辑、字段映射、上下游依赖。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class DesignSchemeSkill(BaseSkill):
    """方案设计 Skill — 注册到全局 Skill 注册中心。"""

    name = "design_scheme"
    description = "设计方案输出 — 取数逻辑、字段映射、上下游依赖"
    version = "1.0.0"
    prompt_template_path = "design_scheme.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行方案设计流程。

        步骤:
          1. 读取需求理解摘要
          2. 引用语义层模型
          3. 输出取数逻辑
          4. 输出字段映射关系
          5. 输出上下游依赖
        """
        inp = context.input if isinstance(context.input, dict) else {}

        requirement_doc = (
            inp.get("requirement_doc")
            or context.state.get("requirement_doc", "")
            or context.state.get("requirement", "")
        )
        requirement_summary = inp.get("requirement_summary") or context.state.get(
            "requirement_summary", ""
        )

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            requirement_summary=requirement_summary,
            domain_context=context.state.get("domain_context", ""),
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "design_ready"},
        )
