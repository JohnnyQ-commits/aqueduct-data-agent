"""DQC 质检 Skill — DQCQualitySkill。

对应 Phase 5: 数据质量保障。
负责生成全面的质量测试用例，覆盖 5 大测试类别。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class DQCQualitySkill(BaseSkill):
    """DQC 质检 Skill — 注册到全局 Skill 注册中心。"""

    name = "dqc_quality"
    description = "DQC 质检 — 生成质量测试用例，覆盖唯一性、业务反证、一致性、边界、波动"
    version = "1.0.0"
    prompt_template_path = "dqc_quality.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行 DQC 质检流程。

        步骤:
          1. 读取目标表 DDL + 核心 SQL
          2. 从语义模型提取 business_rules + axioms
          3. 生成 5 大测试类别用例
          4. 输出 DQC SQL 文件（含权重标注）
        """
        inp = context.input if isinstance(context.input, dict) else {}

        ddl_content = inp.get("ddl_content") or context.state.get("ddl_content", "")
        sql_content = inp.get("sql_content") or context.state.get("sql_content", "")
        business_rules = inp.get("business_rules") or context.state.get("business_rules", "")
        domain_axioms = inp.get("domain_axioms") or context.state.get("domain_axioms", "")

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            ddl_content=ddl_content,
            sql_content=sql_content,
            business_rules=business_rules,
            domain_axioms=domain_axioms,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "dqc_ready"},
        )
