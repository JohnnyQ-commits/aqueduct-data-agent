"""Phase 1: 需求理解节点。"""

from __future__ import annotations

import logging
import time

from ...config.settings import get_settings
from ...memory.recall import KnowledgeRecall
from ...memory.store import MemoryStore
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


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
