"""报告交付 Skill — ReportDeliverySkill。

对应 Phase 6: 交付与沉淀。
PERF-4 拆分：doc_gen 只生成洞察章节（需求背景/待确认问题清单），
设计方案/表结构/核心 SQL/血缘图由 report 节点从 state 产物本地拼装
（零 LLM、零转写失真，prompt 不再包含转写类内容）。
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
    description = "报告交付 — 洞察章节生成（设计方案/DDL/SQL/血缘图由节点本地拼装）"
    version = "2.0.0"
    prompt_template_path = "report_delivery_insights.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """构建 doc_gen 洞察 prompt。

        输入只含洞察素材（设计方案/DQC/语义模型）——转写类产物
        （DDL/SQL/血缘图）不进 prompt，由节点本地填充。
        """
        inp = context.input if isinstance(context.input, dict) else {}
        metadata = context.state.get("metadata", {})

        requirement_name = inp.get("requirement_name") or metadata.get(
            "requirement_name", "交付报告"
        )
        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        dqc_result = inp.get("dqc_result") or context.state.get("dqc_result", "")
        domain_context = inp.get("domain_context") or context.state.get("domain_context", "")

        prompt = self.load_prompt_template(
            requirement_name=requirement_name,
            design_scheme=design_scheme,
            dqc_result=dqc_result,
            domain_context=domain_context,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt, "requirement_name": requirement_name},
            metadata={"status": "delivery_ready"},
        )
