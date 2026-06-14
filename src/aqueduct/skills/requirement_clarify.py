"""需求澄清 Skill — RequirementClarifySkill。

对应 Phase 1: 需求理解。
负责读取需求文档、识别歧义点、查询源表结构、输出需求理解摘要。
"""

from __future__ import annotations

from pathlib import Path

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class RequirementClarifySkill(BaseSkill):
    """需求澄清 Skill — 注册到全局 Skill 注册中心。"""

    name = "requirement_clarify"
    description = "需求理解与澄清 — 识别歧义点、查询源表结构、输出需求理解摘要"
    version = "1.0.0"
    prompt_template_path = "requirement_clarify.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行需求澄清流程。

        步骤:
          1. 读取需求文档
          2. 匹配业务域本体模型
          3. 提取关键信息
          4. 识别歧义点
          5. 输出需求理解摘要 + 问题清单
        """
        inp = context.input if isinstance(context.input, dict) else {}

        # 从 input 或 state 中获取需求内容（优先取 input，回退到 state）
        requirement_doc = (
            inp.get("requirement_doc")
            or inp.get("requirement")
            or context.state.get("requirement", "")
        )
        if not requirement_doc:
            return SkillResult(
                success=False,
                error="缺少需求文档输入",
            )

        # 如果 requirement_doc 是文件路径，读取内容
        try:
            p = Path(requirement_doc)
            if p.exists():
                requirement_doc = p.read_text(encoding="utf-8")
        except OSError:
            pass  # 不是有效路径，当作内容字符串处理

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            domain_context=context.state.get("domain_context", ""),
            known_tables=", ".join(context.state.get("known_tables", [])),
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt, "requirement": requirement_doc},
            metadata={"status": "clarification_ready"},
        )
