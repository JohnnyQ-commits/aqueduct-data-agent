"""报告交付 Skill — ReportDeliverySkill。

对应 Phase 6: 交付与沉淀。
负责整合全流程产出物，生成 Design.md、交付总报告、知识沉淀文档。
"""

from __future__ import annotations

import logging

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill

logger = logging.getLogger(__name__)


@register_skill
class ReportDeliverySkill(BaseSkill):
    """报告交付 Skill — 注册到全局 Skill 注册中心。"""

    name = "report_delivery"
    description = "报告交付 — 整合全流程产出物，生成设计文档、交付报告、知识沉淀"
    version = "1.0.0"
    prompt_template_path = "report_delivery.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行报告交付流程。

        步骤:
          1. 收集全流程产出物（DDL、SQL、DQC、血缘图）
          2. 生成 Design.md（按 templates/design.md）
          3. 生成 交付总报告.md（按 templates/report.md）
          4. 生成 知识沉淀.md
          5. 更新 knowledge/domains/{domain_id}.json
          6. 运行 gen_semantic_doc.py 同步 semantic-model.md
        """
        inp = context.input if isinstance(context.input, dict) else {}
        metadata = context.state.get("metadata", {})

        requirement_name = inp.get("requirement_name") or metadata.get(
            "requirement_name", "交付报告"
        )
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        dqc_result = inp.get("dqc_result") or context.state.get("dqc_result", "")

        lineage_result = inp.get("lineage_result") or context.state.get("lineage_result", {})
        lineage_mermaid = ""
        if isinstance(lineage_result, dict):
            lineage_mermaid = lineage_result.get("mermaid", "")

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            requirement_name=requirement_name,
            design_scheme=design_scheme,
            ddl_content=ddl_content,
            sql_content=sql_content,
            dqc_result=dqc_result,
            domain_context=context.state.get("domain_context", ""),
            lineage_mermaid=lineage_mermaid,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt, "requirement_name": requirement_name},
            metadata={"status": "delivery_ready"},
        )
