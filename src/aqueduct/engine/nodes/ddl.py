"""Phase 3: DDL 生成节点。"""

from __future__ import annotations

import logging
import time

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

logger = logging.getLogger(__name__)


def node_ddl(state: WorkflowState) -> WorkflowState:
    """Phase 3: DDL 生成节点。

    调用 DDLGenerateSkill 生成 prompt -> LLM 生成 DDL -> 保存到文件。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=3] DDL 生成开始", req_name)

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

        ddl_path = save_artifact(state, "Phase3-表结构.sql", ddl_content)
        state["ddl_content"] = ddl_content
        state["ddl_file"] = ddl_path
        state["metadata"] = {**(state.get("metadata", {})), "ddl_done": "true"}

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=3] DDL 生成完成: ddl=%d 字符, 产出=%s, 耗时=%.1fs",
            req_name,
            len(ddl_content),
            ddl_path,
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"DDL 生成异常: {e!s}")
        logger.error(
            "[task=%s, phase=3] DDL 生成异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
