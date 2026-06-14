"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import logging

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def node_requirement(state: WorkflowState) -> WorkflowState:
    """Phase 1: 需求理解节点。

    调用 RequirementClarifySkill 生成 prompt -> LLM 解析需求。
    """
    try:
        skill = get_skill("requirement_clarify")
        context = SkillContext(
            input=state.get("requirement"),
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"需求解析失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "requirement_parse", prompt)

        save_artifact(state, "需求理解摘要.md", llm_response)
        state["requirement_summary"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "requirement_parsed": "true"}

        logger.info("Phase 1 需求解析完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"需求解析异常: {e!s}")
        logger.error("需求解析异常: %s", e, exc_info=True)

    return state
