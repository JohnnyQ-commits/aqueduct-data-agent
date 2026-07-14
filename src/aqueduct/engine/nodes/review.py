"""Phase 4.5: 代码审查节点。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...exceptions import WorkflowHaltError
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


# ── SQL 分块工具 ──────────────────────────────────────────────────────────────


def _split_sql_into_blocks(sql: str) -> list[str]:
    """将 SQL 按顶层语句拆分为独立块。

    仅按最外层分号拆分（忽略括号/字符串/注释内的分号）。
    空块和纯注释块会被过滤。

    Returns:
        拆分后的 SQL 语句列表。若仅 1 条语句则返回单元素列表。
    """
    blocks: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    paren_depth = 0
    i = 0

    while i < len(sql):
        ch = sql[i]

        # 行注释 --
        if (
            not in_single_quote
            and not in_double_quote
            and not in_block_comment
            and ch == "-"
            and i + 1 < len(sql)
            and sql[i + 1] == "-"
        ):
            in_line_comment = True
            current.append(ch)
            i += 1
            continue

        # 块注释 /* */
        if not in_single_quote and not in_double_quote and not in_line_comment:
            if ch == "/" and i + 1 < len(sql) and sql[i + 1] == "*":
                in_block_comment = True
                current.append(ch)
                i += 1
                continue
            if ch == "*" and i + 1 < len(sql) and sql[i + 1] == "/" and in_block_comment:
                in_block_comment = False
                current.append(ch)
                current.append(sql[i + 1])
                i += 2
                continue

        # 注释内原样保留
        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            current.append(ch)
            i += 1
            continue

        # 字符串引号
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote

        # 括号层级（仅在引号外跟踪）
        if not in_single_quote and not in_double_quote:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)

        # 顶层分号 → 拆分点
        if ch == ";" and not in_single_quote and not in_double_quote and paren_depth == 0:
            block = "".join(current).strip()
            if block:
                blocks.append(block)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # 处理最后一个块（可能没有分号结尾）
    last = "".join(current).strip()
    if last and not last.isspace():
        blocks.append(last)

    return blocks if blocks else [sql]


def _build_chunk_prompt(
    state: WorkflowState, sql_block: str, chunk_index: int, total_chunks: int
) -> str:
    """为单个 SQL 块构建审查 prompt（复用 code_review 模板）。"""
    skill = get_skill("code_review")
    context = SkillContext(
        input={
            "requirement_desc": state.get("requirement_summary", ""),
            "sql_content": sql_block,
            "domain_context": state.get("domain_context", ""),
            "validation_result": state.get("validation_result", {}),
        },
        state=state,
    )
    result = skill.execute(context)
    header = f"[审查第 {chunk_index}/{total_chunks} 个 SQL 语句块]\n\n" if total_chunks > 1 else ""
    return header + result.data.get("prompt", "")


def _review_chunk(state: WorkflowState, prompt: str) -> str:
    """在线程池中执行单个 SQL 块的 LLM 审查。"""
    return call_llm(state, "sql_review", prompt)


# ── 审查问题解析 ──────────────────────────────────────────────────────────────


def _parse_review_issues(review_result: str) -> list[dict[str, str]]:
    """从审查报告中提取 Critical/Warning 级别问题。

    解析格式如：
    - [Critical] ...
    - [Warning] ...
    - **Critical**: ...
    """
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

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
        key = (m.group(1).lower(), m.group(2).strip())
        if key not in seen:
            seen.add(key)
            issues.append({"severity": m.group(1), "message": m.group(2).strip()})
    for m in bold_pattern.finditer(review_result):
        key = (m.group(1).lower(), m.group(2).strip())
        if key not in seen:
            seen.add(key)
            issues.append({"severity": m.group(1), "message": m.group(2).strip()})

    return issues


def _should_parallel_review(sql_content: str) -> bool:
    """判断是否需要并行分块审查。"""
    lines = sql_content.count("\n") + 1
    blocks = _split_sql_into_blocks(sql_content)
    return lines > 100 and len(blocks) > 1


def node_review(state: WorkflowState) -> WorkflowState:
    """Phase 4.5: 代码审查节点。

    调用 CodeReviewSkill 生成 prompt -> LLM 审查 SQL。
    SQL 超过 100 行且包含多个语句时，自动分块并行审查。
    审查后检查 Critical/Warning 问题，决定是否需要修复循环。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=4.5] 代码审查开始", req_name)

    try:
        sql_content = state.get("sql_content", "")

        # ── 并行分块审查 ──
        if _should_parallel_review(sql_content):
            llm_response = _parallel_review(state, sql_content, req_name)
        else:
            # ── 单块审查（原路径） ──
            llm_response = _single_review(state, sql_content)
            if llm_response is None:
                state.setdefault("errors", []).append("代码审查失败: Skill 执行异常")
                return state

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

        if critical_count > 0 and fix_iterations < max_fix_iterations:
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
            if critical_count > 0 and fix_iterations >= max_fix_iterations:
                # P0 自动暂停：Critical 问题在修复循环后仍然存在，终止管道
                msg = (
                    f"[终止] 审查发现 {critical_count} 个 Critical 问题"
                    f"经 {max_fix_iterations} 轮修复仍未解决，管道终止"
                )
                logger.error("[task=%s] %s", req_name, msg)
                raise WorkflowHaltError(msg)
            if warning_count > 0 and critical_count == 0:
                logger.info(
                    "[task=%s] 审查发现 %d Warning（无 Critical），跳过修复循环",
                    req_name,
                    warning_count,
                )
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
    except WorkflowHaltError:
        raise
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


def _parallel_review(state: WorkflowState, sql_content: str, req_name: str) -> str:
    """将 SQL 分块后并行审查，合并结果。"""
    blocks = _split_sql_into_blocks(sql_content)
    total = len(blocks)
    logger.info(
        "[task=%s] SQL 分块并行审查: %d 个语句块, 总行数=%d",
        req_name,
        total,
        sql_content.count("\n") + 1,
    )

    # 为每个块构建 prompt
    prompts: list[tuple[int, str]] = []
    for idx, block in enumerate(blocks, 1):
        prompt = _build_chunk_prompt(state, block, idx, total)
        prompts.append((idx, prompt))

    # 并行执行 LLM 审查
    max_workers = min(4, total)
    results: dict[int, str] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_review_chunk, state, prompt): idx for idx, prompt in prompts
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                errors.append(f"块 {idx} 审查失败: {e!s}")
                logger.error("[task=%s] 并行审查块 %d 失败: %s", req_name, idx, e)

    # 按顺序合并结果
    combined_parts: list[str] = []
    for idx in range(1, total + 1):
        if idx in results:
            combined_parts.append(f"## SQL 块 {idx}/{total}\n\n{results[idx]}")
    if errors:
        combined_parts.append("## 审查异常\n\n" + "\n".join(errors))

    combined = "\n\n---\n\n".join(combined_parts)
    logger.info(
        "[task=%s] 并行审查完成: %d/%d 块成功, 合并报告=%d 字符",
        req_name,
        len(results),
        total,
        len(combined),
    )
    return combined


def _single_review(state: WorkflowState, sql_content: str) -> str | None:
    """单块审查（原路径）：构建 prompt → 单次 LLM 调用。

    Returns:
        LLM 审查报告文本。Skill 执行失败时返回 None。
    """
    skill = get_skill("code_review")
    context = SkillContext(
        input={
            "requirement_desc": state.get("requirement_summary", ""),
            "sql_content": sql_content,
            "domain_context": state.get("domain_context", ""),
            "validation_result": state.get("validation_result", {}),
        },
        state=state,
    )
    result = skill.execute(context)

    if not result.success:
        # 保存失败信息到 state 并提前返回
        return None  # 调用方需要检查

    prompt = result.data.get("prompt", "")
    return call_llm(state, "sql_review", prompt)
