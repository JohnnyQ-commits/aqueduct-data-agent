"""Phase 2: 方案设计 + DDL 生成节点（合并）。

OPT-4 优化：合并 Phase 2（方案设计）和 Phase 3（DDL 生成）为单次 LLM 调用，
减少一次 LLM 往返，节省 ~140s。

调用 DesignDDLSkill 生成 prompt -> LLM 同时生成设计方案和 DDL -> 解析并保存。
"""

from __future__ import annotations

import logging
import re
import time

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

logger = logging.getLogger(__name__)


def _split_design_and_ddl(llm_response: str) -> tuple[str, str]:
    """从 LLM 合并响应中拆分设计方案和 DDL。

    策略:
      - SQL 代码块 → DDL
      - SQL 代码块之前的所有内容 → 设计方案
      - 无代码块时 → 全部作为设计方案，DDL 为空

    Returns:
        (design_scheme, ddl_content)
    """
    # 找到第一个代码块的位置
    sql_match = re.search(r"```sql\s*\n", llm_response, re.IGNORECASE)
    code_match = re.search(r"```\s*\n", llm_response) if not sql_match else None

    if sql_match:
        design_scheme = llm_response[: sql_match.start()].strip()
        ddl_content = extract_sql_block(llm_response)
    elif code_match:
        design_scheme = llm_response[: code_match.start()].strip()
        ddl_content = extract_sql_block(llm_response)
    else:
        # 无代码块 → 全部作为设计方案
        design_scheme = llm_response.strip()
        ddl_content = ""

    return design_scheme, ddl_content


def node_design(state: WorkflowState) -> WorkflowState:
    """Phase 2: 方案设计 + DDL 生成节点（合并）。

    调用 DesignDDLSkill 生成 prompt -> LLM 同时生成设计方案和 DDL ->
    解析响应并保存两个产出物。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=2] 方案设计 + DDL 生成开始（合并）", req_name)

    try:
        skill = get_skill("design_ddl")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "domain_context": state.get("domain_context", ""),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"方案+DDL 生成失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "design_ddl", prompt)

        # 拆分设计方案和 DDL
        design_scheme, ddl_content = _split_design_and_ddl(llm_response)

        # 保存设计方案
        save_artifact(state, "Phase2-设计方案.md", design_scheme)
        state["design_scheme"] = design_scheme
        state["metadata"] = {**(state.get("metadata", {})), "design_done": "true"}

        # 保存 DDL
        if ddl_content and len(ddl_content) > 50:
            ddl_path = save_artifact(state, "Phase3-表结构.sql", ddl_content)
            state["ddl_content"] = ddl_content
            state["ddl_file"] = ddl_path
            state["metadata"] = {**state["metadata"], "ddl_done": "true"}
            logger.info(
                "[task=%s, phase=2] DDL 已在合并调用中生成: ddl=%d 字符",
                req_name,
                len(ddl_content),
            )
        else:
            logger.warning(
                "[task=%s, phase=2] 合并调用未产出有效 DDL（%d 字符），"
                "将由 Phase 3 节点独立生成",
                req_name,
                len(ddl_content),
            )
            # 清空标记，让 ddl.py 独立执行
            state.pop("ddl_content", None)
            state.pop("ddl_file", None)

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=2] 方案+DDL 合并完成: design=%d 字符, ddl=%d 字符, 耗时=%.1fs",
            req_name,
            len(design_scheme),
            len(ddl_content),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"方案+DDL 生成异常: {e!s}")
        logger.error(
            "[task=%s, phase=2] 方案+DDL 生成异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
