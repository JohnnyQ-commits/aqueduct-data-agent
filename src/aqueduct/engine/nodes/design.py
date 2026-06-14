"""Phase 2: 方案设计节点。"""

from __future__ import annotations

import logging

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def node_design(state: WorkflowState) -> WorkflowState:
    """Phase 2: 方案设计节点。

    调用 DesignSchemeSkill 生成 prompt -> LLM 生成设计方案。
    """
    try:
        skill = get_skill("design_scheme")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "domain_context": state.get("domain_context", ""),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"方案设计失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "scheme_write", prompt)

        save_artifact(state, "设计方案.md", llm_response)
        state["design_scheme"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "design_done": "true"}

        logger.info("Phase 2 方案设计完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"方案设计异常: {e!s}")
        logger.error("方案设计异常: %s", e, exc_info=True)

    return state
