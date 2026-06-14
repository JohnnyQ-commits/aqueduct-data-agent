"""SQL 开发 Skill — SQLDevelopSkill。

对应 Phase 4: SQL 开发。
负责编写核心 ETL SQL，遵循代码规范。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class SQLDevelopSkill(BaseSkill):
    """SQL 开发 Skill — 注册到全局 Skill 注册中心。"""

    name = "sql_develop"
    description = "SQL 开发 — 编写核心 ETL SQL，遵循代码规范"
    version = "1.0.0"
    prompt_template_path = "sql_develop.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行 SQL 开发流程。

        步骤:
          1. 读取需求文档 + DDL + 设计方案
          2. 引用语义层模型
          3. 编写 SQL（遵循 coding-style.md）
          4. 调用 ValidatorTool 进行自动校验
        """
        inp = context.input if isinstance(context.input, dict) else {}

        requirement_doc = inp.get("requirement_doc") or context.state.get("requirement", "")
        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        coding_style = inp.get("coding_style") or context.state.get("coding_style", "")

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            ddl_content=ddl_content,
            design_scheme=design_scheme,
            domain_context=context.state.get("domain_context", ""),
            coding_style=coding_style,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "sql_ready"},
        )
