"""方案设计 + DDL 生成合并 Skill — DesignDDLSkill。

合并 Phase 2（方案设计）和 Phase 3（DDL 生成）为单次 LLM 调用。
减少一次 LLM 往返，节省 ~140s。

对应 Phase 2+3: 同时输出设计方案和目标表 DDL。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class DesignDDLSkill(BaseSkill):
    """方案 + DDL 合并 Skill — 注册到全局 Skill 注册中心。"""

    name = "design_ddl"
    description = "方案设计与 DDL 生成合并 — 单次 LLM 调用同时产出设计方案和 CREATE TABLE 语句"
    version = "1.0.0"
    prompt_template_path = "design_ddl.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行方案设计 + DDL 生成。

        步骤:
          1. 读取需求文档和语义模型
          2. 渲染合并 prompt 模板
          3. 返回 prompt（由节点层调用 LLM）
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
        target_table = inp.get("target_table") or context.state.get(
            "target_table", "dw_demo.tmp_target_table"
        )

        # 加载合并 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            requirement_summary=requirement_summary,
            domain_context=context.state.get("domain_context", ""),
            target_table=target_table,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "design_ddl_ready"},
        )
