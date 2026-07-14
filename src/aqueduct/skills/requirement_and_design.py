"""需求分析 + 方案设计 + DDL 生成三合一 Skill — RequirementAndDesignSkill。

OPT-5: 合并 Phase 1（需求分析）、Phase 2（方案设计）、Phase 3（DDL 生成）
为单次 LLM 调用，减少 2 次 LLM 往返，节省 ~200-280s。

对应 Phase 1+2+3: 一次调用同时产出需求理解摘要、设计方案和 DDL。
"""

from __future__ import annotations

import logging

from .base import BaseSkill, SkillContext, SkillResult
from .design_ddl import _resolve_target_table
from .registry import register_skill

logger = logging.getLogger(__name__)


@register_skill
class RequirementAndDesignSkill(BaseSkill):
    """需求+方案+DDL 三合一 Skill — 注册到全局 Skill 注册中心。"""

    name = "requirement_and_design"
    description = "需求分析+方案设计+DDL 三合一 — 单次 LLM 调用产出全部"
    version = "1.0.0"
    prompt_template_path = "requirement_and_design.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行三合一流程。

        步骤:
          1. 读取需求文档、语义模型、表结构
          2. 渲染合并 prompt 模板
          3. 返回 prompt（由节点层调用 LLM）
        """
        inp = context.input if isinstance(context.input, dict) else {}

        requirement_doc = (
            inp.get("requirement_doc")
            or context.state.get("requirement_doc", "")
            or context.state.get("requirement", "")
        )
        table_schemas = inp.get("table_schemas") or context.state.get("table_schemas", {})
        target_table = _resolve_target_table(context)

        # 将 table_schemas dict 格式化为文本
        if isinstance(table_schemas, dict):
            table_schemas_text = "\n\n".join(table_schemas.values()) if table_schemas else "未获取"
        else:
            table_schemas_text = str(table_schemas) if table_schemas else "未获取"

        # 加载合并 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_doc=requirement_doc,
            domain_context=context.state.get("domain_context", ""),
            table_schemas=table_schemas_text,
            target_table=target_table,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "requirement_and_design_ready"},
        )
