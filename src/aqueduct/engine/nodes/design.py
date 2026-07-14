"""Phase 2: 方案设计 + DDL 生成节点（合并）。

OPT-4 优化：合并 Phase 2（方案设计）和 Phase 3（DDL 生成）为单次 LLM 调用，
减少一次 LLM 往返，节省 ~140s。

OPT-5 优化：Phase 1 已使用三合一 Skill（需求+方案+DDL）时，
Phase 2 检测 design_scheme 已存在则自动跳过，不再重复生成。

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


def _split_requirement_and_design(
    llm_response: str,
) -> tuple[str, str, str]:
    """从三合一 LLM 响应中拆分需求摘要、设计方案和 DDL。

    输出格式约定：
      - 第一部分：以 '## 需求理解摘要' 开头 → 需求理解摘要
      - 第二部分：以 '## 设计方案' 开头 → 设计方案
      - 第三部分：SQL 代码块（```sql ... ```） → DDL

    Returns:
        (requirement_summary, design_scheme, ddl_content)
    """
    # 以 '## 设计方案' 为分界点拆分
    design_marker = re.compile(r"^##\s+设计方案\s*$", re.MULTILINE)
    match = design_marker.search(llm_response)

    if not match:
        # 无设计方案标记 → 整个响应作为需求摘要
        return llm_response.strip(), "", ""

    req_summary = llm_response[: match.start()].strip()
    design_and_ddl = llm_response[match.start() :]

    # 从设计方案部分提取 DDL（SQL 代码块）
    # 先检查是否有 SQL 代码块，避免 extract_sql_block 在无代码块时返回整个文本
    if re.search(r"```sql\s*\n", design_and_ddl) or re.search(r"```\s*\n", design_and_ddl):
        ddl_content = extract_sql_block(design_and_ddl)
    else:
        ddl_content = ""

    # 设计方案 = 去掉 SQL 代码块后的文本
    design_scheme = re.sub(
        r"```sql\s*\n.*?```", "", design_and_ddl, count=1, flags=re.DOTALL
    ).strip()

    # 清理需求摘要中可能存在的 "## 需求理解摘要" 标题
    req_summary = re.sub(r"^##\s+需求理解摘要\s*\n?", "", req_summary, flags=re.MULTILINE)
    req_summary = req_summary.strip()

    return req_summary, design_scheme, ddl_content


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

    OPT-5: Phase 1 三合一已完成时自动跳过，避免重复 LLM 调用。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=2] 方案设计 + DDL 生成开始（合并）", req_name)

    # OPT-5: Phase 1 三合一已完成时跳过
    if state.get("design_scheme") and state.get("ddl_content"):
        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=2] Phase 1 三合一已完成设计和 DDL，跳过（耗时=%.1fs）",
            req_name,
            elapsed,
        )
        state["metadata"] = {
            **(state.get("metadata", {})),
            "design_done": "true",
            "ddl_done": "true",
        }
        return state

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
            state["metadata"] = {**state.get("metadata", {}), "ddl_done": "true"}
            logger.info(
                "[task=%s, phase=2] DDL 已在合并调用中生成: ddl=%d 字符",
                req_name,
                len(ddl_content),
            )
        else:
            logger.warning(
                "[task=%s, phase=2] 合并调用未产出有效 DDL（%d 字符），将由 Phase 3 节点独立生成",
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
