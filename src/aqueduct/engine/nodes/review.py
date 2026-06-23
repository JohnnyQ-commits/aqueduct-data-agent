"""Phase 4.5: 代码审查节点。"""

from __future__ import annotations

import logging
import re
import time

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def _parse_review_issues(review_result: str) -> list[dict[str, str]]:
    """从审查报告中提取 Critical/Warning 级别问题。

    解析格式如：
    - [Critical] ...
    - [Warning] ...
    - **Critical**: ...
    """
    issues: list[dict[str, str]] = []

    # 匹配 [Critical] / [Warning] / [INFO] 格式
    bracket_pattern = re.compile(
        r"\[(Critical|Warning|INFO)\]\s*(.+?)(?=\n|$)",
        re.IGNORECASE,
    )
    # 匹配 **Critical**: 格式
    bold_pattern = re.compile(
        r"\*\*(Critical|Warning|INFO)\*\*\s*[:：]\s*(.+?)(?=\n|$)",
        re.IGNORECASE,
    )

    for m in bracket_pattern.finditer(review_result):
        issues.append({"severity": m.group(1), "message": m.group(2).strip()})
    for m in bold_pattern.finditer(review_result):
        issues.append({"severity": m.group(1), "message": m.group(2).strip()})

    return issues


def node_review(state: WorkflowState) -> WorkflowState:
    """Phase 4.5: 代码审查节点。

    调用 CodeReviewSkill 生成 prompt -> LLM 审查 SQL。
    审查后检查 Critical/Warning 问题，决定是否需要修复循环。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=4.5] 代码审查开始", req_name)

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
        save_artifact(state, f"Phase5-{req_name}_审查报告.md", llm_response)
        state["review_result"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "review_done": "true"}

        # 解析审查问题，判断是否需要修复循环
        issues = _parse_review_issues(llm_response)
        critical_count = sum(1 for i in issues if i["severity"].lower() == "critical")
        warning_count = sum(1 for i in issues if i["severity"].lower() == "warning")
        fix_iterations = state.get("fix_iterations", 0)

        from ...config.settings import get_settings

        max_fix_iterations = get_settings().max_fix_iterations

        if (critical_count > 0 or warning_count > 0) and fix_iterations < max_fix_iterations:
            logger.warning(
                "[task=%s] 审查发现 %d Critical + %d Warning，启动修复循环（%d/%d）",
                req_name,
                critical_count,
                warning_count,
                fix_iterations + 1,
                max_fix_iterations,
            )
            state["_needs_fix_loop"] = True
            state["_review_issues"] = issues
        else:
            state["_needs_fix_loop"] = False
            if critical_count > 0 or warning_count > 0:
                logger.info(
                    "[task=%s] 审查发现 %d Critical + %d Warning，但已达最大修复次数（%d），跳过",
                    req_name,
                    critical_count,
                    warning_count,
                    max_fix_iterations,
                )

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=4.5] 代码审查完成: review=%d 字符, issues=%d (C:%d W:%d), 耗时=%.1fs",
            req_name,
            len(llm_response),
            len(issues),
            critical_count,
            warning_count,
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"代码审查异常: {e!s}")
        logger.error(
            "[task=%s, phase=4.5] 代码审查异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state
