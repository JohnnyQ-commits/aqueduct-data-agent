"""Phase 4.5: 代码审查节点。"""

from __future__ import annotations

import logging

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def node_review(state: WorkflowState) -> WorkflowState:
    """Phase 4.5: 代码审查节点。

    调用 CodeReviewSkill 生成 prompt -> LLM 审查 SQL。
    """
    try:
        skill = get_skill("code_review")
        context = SkillContext(
            input={
                "sql_content": state.get("sql_content", ""),
                "domain_context": state.get("domain_context", ""),
                "validation_result": state.get("validation_result", {}),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"代码审查失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "sql_review", prompt)

        req_name = state.get("metadata", {}).get("requirement_name", "code_review")
        save_artifact(state, f"{req_name}_审查报告.md", llm_response)
        state["review_result"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "review_done": "true"}

        logger.info("Phase 4.5 代码审查完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"代码审查异常: {e!s}")
        logger.error("代码审查异常: %s", e, exc_info=True)

    return state
