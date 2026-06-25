"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import logging
import re
import time

from ...config.settings import get_settings
from ...memory.recall import KnowledgeRecall
from ...memory.store import MemoryStore
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def _extract_target_table(text: str) -> str:
    """从需求文档中提取目标表名。

    匹配模式（按优先级）:
      1. '目标表[：:] xxx' 中文提示
      2. 'CREATE TABLE schema.table' SQL 语句
      3. '写入/输出到 schema.table' 中文动词 + 表名
      4. 独立的 schema.table 格式标识符

    Returns:
        提取到的表名，未找到时返回空字符串。
    """
    # 模式 1: 中文提示 "目标表：xxx" / "目标表: xxx"
    m = re.search(r"目标表[：:]\s*(\S+)", text)
    if m:
        return m.group(1).strip("，。,.")

    # 模式 2: CREATE TABLE 语句
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip("(，。,.")

    # 模式 3: 中文动词 + 表名
    m = re.search(r"(?:写入|输出到|存入|保存到)\s*(\w+\.\w+)", text)
    if m:
        return m.group(1)

    # 模式 4: 独立的 schema.table 格式（至少含一个点）
    candidates = re.findall(r"\b([a-z_]+\.[a-z_]+)\b", text, re.IGNORECASE)
    if candidates:
        return candidates[0]

    return ""


def _recall_domain_knowledge(state: WorkflowState) -> None:
    """从本体知识库中召回与需求匹配的业务域上下文。

    在需求理解阶段调用，将结果写入 state["domain_id"] 和 state["domain_context"]，
    供后续所有节点复用。全局仅执行一次。

    无匹配领域时写入空字符串，工作流正常继续。
    """
    try:
        settings = get_settings()
        store = MemoryStore(domains_dir=settings.knowledge_dir)
        recall = KnowledgeRecall(store=store)
        result = recall.recall(state.get("requirement", ""))

        domain_id = result.get("domain_id", "")
        domain_context = result.get("domain_context", "")

        state["domain_id"] = domain_id
        state["domain_context"] = domain_context

        logger.info(
            "领域知识召回完成: domain=%s, context_length=%d",
            domain_id or "(无匹配)",
            len(domain_context),
        )
        if domain_context:
            logger.debug("召回领域内容片段预览: %s", domain_context[:200])
    except Exception:
        # 召回失败不中断工作流，写入空值继续
        state["domain_id"] = ""
        state["domain_context"] = ""
        logger.warning("领域知识召回异常，跳过", exc_info=True)


def node_requirement(state: WorkflowState) -> WorkflowState:
    """Phase 1: 需求理解节点。

    调用 RequirementClarifySkill 生成 prompt -> LLM 解析需求。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=1] 需求理解开始", req_name)

    # 自动召回领域知识，填充 domain_context 供全流程使用
    _recall_domain_knowledge(state)

    # 从需求文档中提取目标表名，供 Phase 2+3 DDL 生成使用
    target_table = _extract_target_table(state.get("requirement", ""))
    if target_table:
        state["target_table"] = target_table
        logger.info("提取目标表名: %s", target_table)

    try:
        skill = get_skill("requirement_clarify")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "domain_context": state.get("domain_context", ""),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"需求解析失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "requirement_parse", prompt)

        save_artifact(state, "Phase1-需求理解摘要.md", llm_response)
        state["requirement_summary"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "requirement_parsed": "true"}

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=1] 需求理解完成: summary=%d 字符, 耗时=%.1fs",
            req_name,
            len(llm_response),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"需求解析异常: {e!s}")
        logger.error(
            "[task=%s, phase=1] 需求理解异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
