"""Phase 3: DDL 生成节点。

OPT-4 优化：如果 Phase 2 合并调用已产出 DDL，直接跳过本节点的 LLM 调用。
向后兼容：如果 Phase 2 未产出 DDL（如合并调用失败），回退到独立 DDL 生成。
"""

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

    OPT-4: 优先使用 Phase 2 合并调用已产出的 DDL。
    回退: 调用 DDLGenerateSkill 生成 prompt -> LLM 生成 DDL -> 保存到文件。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=3] DDL 生成节点进入", req_name)

    # OPT-4: 合并调用已产出 DDL → 跳过
    if state.get("ddl_content") and state.get("ddl_file"):
        logger.info(
            "[task=%s, phase=3] DDL 已由 Phase 2 合并调用产出，跳过 LLM 调用（节省 ~14s）",
            req_name,
        )
        state["metadata"] = {**(state.get("metadata", {})), "ddl_done": "true"}
        return state

    # 回退：独立 DDL 生成（合并调用未产出 DDL 时）
    logger.info("[task=%s, phase=3] DDL 未由合并调用产出，执行独立 DDL 生成", req_name)

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
