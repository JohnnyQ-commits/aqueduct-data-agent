"""Phase 3: DDL 生成节点。"""

from __future__ import annotations

import logging

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

logger = logging.getLogger(__name__)


def node_ddl(state: WorkflowState) -> WorkflowState:
    """Phase 3: DDL 生成节点。

    调用 DDLGenerateSkill 生成 prompt -> LLM 生成 DDL -> 保存到文件。
    """
    try:
        skill = get_skill("ddl_generate")
        context = SkillContext(
            input={
                "design_scheme": state.get("design_scheme", ""),
                "domain_context": state.get("domain_context", ""),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"DDL 生成失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "ddl_gen", prompt)

        ddl_content = extract_sql_block(llm_response)

        ddl_path = save_artifact(state, "表结构.sql", ddl_content)
        state["ddl_content"] = ddl_content
        state["ddl_file"] = ddl_path
        state["metadata"] = {**(state.get("metadata", {})), "ddl_done": "true"}

        logger.info("Phase 3 DDL 生成完成: %s", ddl_path)
    except Exception as e:
        state.setdefault("errors", []).append(f"DDL 生成异常: {e!s}")
        logger.error("DDL 生成异常: %s", e, exc_info=True)

    return state
