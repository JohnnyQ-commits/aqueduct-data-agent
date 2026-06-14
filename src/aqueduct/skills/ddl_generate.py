"""DDL 生成 Skill — DDLGenerateSkill。

对应 Phase 3: 表结构设计。
负责根据设计方案生成目标表 DDL。
"""

from __future__ import annotations

from .base import BaseSkill, SkillContext, SkillResult
from .registry import register_skill


@register_skill
class DDLGenerateSkill(BaseSkill):
    """DDL 生成 Skill — 注册到全局 Skill 注册中心。"""

    name = "ddl_generate"
    description = "DDL 生成 — 根据设计方案生成目标表 CREATE TABLE 语句"
    version = "1.0.0"
    prompt_template_path = "ddl_generate.tpl.md"

    def execute(self, context: SkillContext) -> SkillResult:
        """执行 DDL 生成流程。

        步骤:
          1. 读取设计方案
          2. 解析字段映射
          3. 生成 CREATE TABLE 语句
          4. 校验 DDL 规范（分区、存储格式、注释）
        """
        inp = context.input if isinstance(context.input, dict) else {}

        design_scheme = inp.get("design_scheme") or context.state.get("design_scheme", "")
        field_mapping = inp.get("field_mapping") or context.state.get("field_mapping", "")
        target_table = inp.get("target_table") or context.state.get(
            "target_table", "dw_demo.tmp_target_table"
        )

        # 加载 Prompt 模板
        prompt = self.load_prompt_template(
            design_scheme=design_scheme,
            field_mapping=field_mapping,
            target_table=target_table,
        )

        return SkillResult(
            success=True,
            data={"prompt": prompt},
            metadata={"status": "ddl_ready"},
        )
