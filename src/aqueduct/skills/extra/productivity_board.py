"""提效看板 Skill — ProductivityBoardSkill。

附属 Skill，对接原有提效看板和 CI 发布链路。
"""

from __future__ import annotations

from ..base import BaseSkill, SkillContext, SkillResult
from ..registry import register_skill


@register_skill
class ProductivityBoardSkill(BaseSkill):
    """提效看板 Skill — 注册到全局 Skill 注册中心。"""

    name = "productivity_board"
    description = "提效看板 — 统计 Agent 节省的工时、代码行数，量化数字化身价值"
    version = "1.0.0"
    prompt_template_path = ""

    def execute(self, context: SkillContext) -> SkillResult:
        """执行提效看板生成流程。

        调用 ProductivityTool 进行统计。
        """
        from ...tools.registry import get_tool

        try:
            tool = get_tool("productivity")
            result = tool.execute(**context.state)
            return SkillResult(
                success=result.success,
                data=result.data,
                error=result.error,
                metadata=result.metadata,
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
