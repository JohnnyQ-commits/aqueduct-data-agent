"""Phase 4.5: 代码审查节点。

PERF-11: 单块审查拆 3 维度并行（P0-3 DQC 拆分范式）——同输入不同
审查透镜，每维一次小调用，固定顺序合并。配套解析契约修复：输出锁定
`- [Critical/Warning/Confirm] ` 列表行（原模板教模型输出表格，解析器
只认方括号行——LLM 审查发现从未进过修复循环，2026-09-09 四个 eval
报告实录：报告含高质量 Critical 但可解析格式匹配数为 0）。
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict

from ...exceptions import LLMError, WorkflowHaltError
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .dqc import start_dqc_speculative
from .helpers import call_llm, is_valid_sql, save_artifact
from .report import start_knowledge_speculative

logger = logging.getLogger(__name__)


class _ReviewDimension(TypedDict):
    """审查维度定义（PERF-11 拆分生成的单元）。"""

    key: str  # 唯一标识（合并排序键）
    name: str  # 维度名（prompt 注入 + 合并章节标题）
    focus: str  # 本维度审查要点（单维 prompt 的核心指令）


# PERF-11: 单块审查拆 3 维度的定义（源自 code_review.tpl.md 全量模板的
# 任务清单/推理步骤/禁止项，顺序即合并顺序）。设计约束：focus 不含其他
# 维度的完整名（测试按 prompt 中的维度名路由识别）。
_REVIEW_DIMENSIONS: list[_ReviewDimension] = [
    {
        "key": "alignment",
        "name": "需求与设计对齐",
        "focus": (
            "- 需求覆盖度：需求摘要中的每个指标、口径、过滤条件是否都在 SQL 中体现，逐项核对\n"
            "- 取数逻辑、字段映射与设计方案核对：SQL 实现与设计方案的口径是否一致\n"
            "- SELECT 字段与目标表 DDL 对齐：字段名、类型、顺序、分区字段逐一对齐\n"
            "- INSERT 目标表名与 DDL 建表名一致"
        ),
    },
    {
        "key": "logic",
        "name": "逻辑正确性",
        "focus": (
            "- 解析 SQL 结构：识别所有源表、JOIN、WHERE、GROUP BY、分层子查询\n"
            "- 缺陷检测：JOIN 扇出（维表非唯一放大 sum 类指标）、聚合口径错误、"
            "映射不一致、逻辑矛盾、遗漏处理\n"
            "- 边界条件：空值传播（NULL 参与聚合/比较）、空分区（当日无数据的指标兜底）、"
            "除零保护\n"
            "- 幂等性：重跑/补数结果是否一致（排序键无 tie-breaker 等不确定取数）"
        ),
    },
    {
        "key": "standards",
        "name": "规范与影响",
        "focus": (
            "- 强制规范逐项核对：每个源表有分区过滤、禁 SELECT * 列出全部字段、"
            "可空数值字段 COALESCE 兜底、除法 NULLIF 保护、JOIN 显式 CAST 无隐式"
            "类型转换、WHERE 不对分区字段做函数转换、子查询嵌套不超 2 层"
            "（超了应拆 TMP 临时表）、文件头元数据注释\n"
            "- 性能风险：全表扫描、count(distinct) 双层聚合\n"
            "- 下游影响：目标表口径变化对下游消费方的影响（新表标注无下游）"
        ),
    },
]


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
            "design_scheme": state.get("design_scheme", ""),
            "ddl_content": state.get("ddl_content", ""),
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


def _lint_sql_issues(sql_content: str) -> list[dict[str, str]]:
    """P0-1: 确定性规范校验（零 token），结果转审查 issues 格式。

    规则与级别经真实交付金样本校准（见知识库"金样本特征提取与linter规则校准"）：
    ERROR → Critical（触发修复循环），WARN → Warning，INFO → 忽略。
    每次审查对 state 中最新 SQL 现场复检——修复循环回跳时自动复检修复结果。
    """
    from ...tools.validator import Validator

    if not sql_content or len(sql_content) < 50:
        return []
    try:
        report = Validator("", content=sql_content).run()
    except Exception:
        logger.warning("规范校验异常，跳过", exc_info=True)
        return []

    issues: list[dict[str, str]] = []
    for r in report.get("issues", []):
        level = r.get("level", "INFO")
        line = r.get("line") or "?"
        msg = f"[规范] {r.get('message', '')} (line {line})"
        if level == "ERROR":
            issues.append({"severity": "Critical", "message": msg})
        elif level == "WARN":
            issues.append({"severity": "Warning", "message": msg})
    return issues


def _trial_run_issues(state: WorkflowState) -> list[dict[str, str]]:
    """P1-2: 真实试跑门禁——每次审查对当前 SQL 现场试跑（LIMIT 10）。

    试跑失败（语法错误/字段不对齐/表不存在）作为 Critical 注入修复循环；
    修复循环回跳 review 时自动复检（与 P0-1 linter 同构）。
    跳过条件（零误报原则）：execution 未启用（`is True` 严格判断——
    单测 mock settings 未显式设 bool 时自动跳过，防止真连数据平台）、
    SQL 无效/过短、数据平台 health_check 不可用（连接故障不误报为 SQL 问题）。
    """
    from ...config.settings import get_settings
    from ...tools.registry import get_tool
    from .sql import _run_trial_selects

    sql_content = state.get("sql_content", "")
    if not sql_content or len(sql_content) < 50 or not is_valid_sql(sql_content):
        return []

    # 严格 is True：非 bool（单测 MagicMock 属性）一律跳过
    if get_settings().execution_enabled is not True:
        return []

    try:
        executor = get_tool("executor")
        health = executor.execute(action="health_check")
        if not health.success:
            logger.info("试跑门禁跳过: 数据平台不可用")
            return []
    except Exception:
        logger.warning("试跑门禁健康检查异常，跳过", exc_info=True)
        return []

    trial = _run_trial_selects(sql_content)
    state["trial_run_result"] = trial  # 更新为当前 SQL 的最新结果
    if not trial["errors"]:
        return []
    return [{"severity": "Critical", "message": f"[试跑] {err}"} for err in trial["errors"]]


def _parse_review_issues(review_result: str) -> list[dict[str, str]]:
    """从审查报告中提取 Critical/Warning/Confirm 级别问题。

    解析格式如：
    - [Critical] ...
    - [Warning] ...
    - [Confirm] ...（PERF-11：需人工确认的口径/依赖问题，不进修复循环）
    - **Critical**: ...
    """
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # 匹配 [Critical] / [Warning] / [INFO] / [Confirm] 格式
    bracket_pattern = re.compile(
        r"\[(Critical|Warning|INFO|Confirm)\]\s*(.+?)(?=\n|$)",
        re.IGNORECASE,
    )
    # 匹配 **Critical**: 格式
    bold_pattern = re.compile(
        r"\*\*(Critical|Warning|INFO|Confirm)\*\*\s*[:：]\s*(.+?)(?=\n|$)",
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

    PERF-11: 单块审查拆 3 维度并行（需求与设计对齐/逻辑正确性/规范与
    影响），固定顺序合并报告。SQL 超过 100 行且包含多个语句时走分块
    并行审查（全量模板逐块，不拆维度）。
    审查后检查 Critical/Warning 问题，决定是否需要修复循环；
    Confirm 级（需人工确认）落 state 不进修复循环。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=4.5] 代码审查开始", req_name)

    try:
        sql_content = state.get("sql_content", "")

        # PERF-9: 投机启动 DQC 生成（与审查并行）。
        # DQC 输入不依赖审查结果，唯一串行原因是修复循环改写 SQL——
        # node_dqc 消费时用输入哈希护栏兜住（见 dqc.take_speculative_dqc）。
        start_dqc_speculative(state)

        # P2-2: 投机启动知识提取（同范式）——151–186s 藏进审查窗口，
        # Phase 6 只剩 doc_gen；哈希护栏见 report.take_speculative_knowledge。
        start_knowledge_speculative(state)

        # ── 并行分块审查（>100 行多语句：全量模板逐块，不拆维度） ──
        if _should_parallel_review(sql_content):
            llm_response = _parallel_review(state, sql_content, req_name)
        else:
            # ── 单块审查（PERF-11: 3 维度拆分并行） ──
            llm_response = _review_by_dimensions(state, sql_content, req_name)
            if llm_response is None:
                state.setdefault("errors", []).append("代码审查失败: 全部审查维度生成失败")
                return state

        req_name = state.get("metadata", {}).get("requirement_name", "code_review")
        save_artifact(state, f"Phase5-{req_name}_审查报告.md", llm_response)
        state["review_result"] = llm_response
        state["metadata"] = {**(state.get("metadata", {})), "review_done": "true"}

        # 解析审查问题，判断是否需要修复循环
        # P1-2: 真实试跑门禁（LIMIT 10 实际执行）先行——最强信号，
        # 失败直接作为 Critical 触发修复循环（修复回跳时现场复检）
        trial_issues = _trial_run_issues(state)
        if trial_issues:
            logger.warning(
                "[task=%s] 试跑门禁: %d 条 SELECT 试跑失败，注入 Critical 触发修复循环",
                req_name,
                len(trial_issues),
            )
        # P0-1: 再注入确定性规范校验（零 token、结果确定），
        # 再合并 LLM 审查问题——ERROR 级违规直接作为 Critical 触发修复循环
        lint_issues = _lint_sql_issues(sql_content)
        if lint_issues:
            logger.info(
                "[task=%s] 确定性规范校验: %d 项违规（C:%d W:%d）注入审查 issues",
                req_name,
                len(lint_issues),
                sum(1 for i in lint_issues if i["severity"] == "Critical"),
                sum(1 for i in lint_issues if i["severity"] == "Warning"),
            )
        # PERF-11: Confirm 级（需人工确认的口径/依赖问题）单独路由——
        # 改代码无法消除，不进修复循环（greenfield 待确认是常态，
        # 归 Critical 会触发修复空转/halt）；结构化落 state 供人工跟进
        parsed = _parse_review_issues(llm_response)
        confirmations = [i for i in parsed if i["severity"].lower() == "confirm"]
        llm_issues = [i for i in parsed if i["severity"].lower() != "confirm"]
        state["review_confirmations"] = confirmations
        if confirmations:
            logger.info(
                "[task=%s] 审查发现 %d 项待确认事项（Confirm，不触发修复循环）",
                req_name,
                len(confirmations),
            )
        issues = trial_issues + lint_issues + llm_issues
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
            # 注：旧分支“Critical + Warning 但已达最大修复次数”已删除——
            # Critical 在上方 halt 分支已处理，走到这里 critical_count 必为 0，
            # 该消息只剩对 warning-only 场景打假话（perf11-check2 实录）

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


def _build_dimension_prompt(state: WorkflowState, dimension: _ReviewDimension) -> str | None:
    """构建单维度审查 prompt（PERF-11 拆分模式）。

    Returns:
        prompt 文本。Skill 执行失败时返回 None。
    """
    skill = get_skill("code_review_dimension")
    context = SkillContext(
        input={
            "requirement_desc": state.get("requirement_summary", ""),
            "sql_content": state.get("sql_content", ""),
            "domain_context": state.get("domain_context", ""),
            "validation_result": state.get("validation_result", {}),
            "design_scheme": state.get("design_scheme", ""),
            "ddl_content": state.get("ddl_content", ""),
            "dimension": dict(dimension),
        },
        state=state,
    )
    result = skill.execute(context)
    if not result.success:
        return None
    return result.data.get("prompt", "")


def _has_review_conclusion(response: str) -> bool:
    """检查审查响应是否含结论标记行（解析契约的有效性门）。

    与维度模板的输出契约一致：无 `审查结论` 标记的响应视为无效
    （网关拥塞时返回的罐头错误文本非空但无结构，曾被静默当成功）。
    """
    return "审查结论" in response


def _review_by_dimensions(state: WorkflowState, sql_content: str, req_name: str) -> str | None:
    """PERF-11: 单块审查拆 3 维度并行 —— 每维一次小调用，固定顺序合并。

    同输入不同审查透镜（每维都能看到全部上下文，只有审查指令不同），
    消除跨维度盲区；单维失败重试一次，仍失败降级（章节 banner +
    errors 记录，对齐 P0-3 DQC 降级模式），全维失败返回 None（调用方
    走既有审查失败降级路径）。

    Returns:
        合并后的审查报告（按 _REVIEW_DIMENSIONS 顺序）。全部维度失败
        时返回 None。
    """
    prompts: list[tuple[_ReviewDimension, str]] = []
    failures: dict[str, str] = {}  # 维度名 -> 错误（含 prompt 构建失败/调用失败）

    for dim in _REVIEW_DIMENSIONS:
        prompt = _build_dimension_prompt(state, dim)
        if prompt is None:
            logger.warning("审查维度「%s」prompt 构建失败，该维降级", dim["name"])
            failures[dim["name"]] = "prompt 构建失败"
            continue
        prompts.append((dim, prompt))

    if not prompts:
        return None

    results: dict[str, str] = {}  # key -> 该维度响应

    def _call_one(dim: _ReviewDimension, prompt: str) -> str:
        """单维审查：响应无审查结论标记视为无效（罐头错误/答非所问），
        重试一次，仍无效抛错（由外层降级）。"""
        for attempt in (1, 2):
            response = call_llm(state, "sql_review", prompt)
            if response and _has_review_conclusion(response):
                return response
            logger.warning(
                "审查维度「%s」响应无结论标记（尝试 %d/2），响应片段: %.80r",
                dim["name"],
                attempt,
                response,
            )
        raise LLMError(f"维度「{dim['name']}」响应无审查结论标记（格式不符）")

    with ThreadPoolExecutor(
        max_workers=max(1, len(prompts)), thread_name_prefix="review-split"
    ) as pool:
        future_to_dim = {pool.submit(_call_one, dim, prompt): dim for dim, prompt in prompts}
        for fut in as_completed(future_to_dim):
            dim = future_to_dim[fut]
            try:
                results[dim["key"]] = fut.result()
            except Exception as e:
                logger.warning("审查维度「%s」失败（已降级）: %s", dim["name"], e)
                failures[dim["name"]] = str(e)

    if not results:
        logger.error(
            "[task=%s] 审查维度拆分全部失败: %s",
            req_name,
            "、".join(failures),
        )
        return None

    # 固定顺序合并（失败维度保留降级 banner，报告可读性完整）
    parts: list[str] = []
    for dim in _REVIEW_DIMENSIONS:
        if dim["key"] in results:
            parts.append(f"## 维度审查: {dim['name']}\n\n{results[dim['key']]}")
        else:
            err = failures.get(dim["name"], "未知错误")
            parts.append(
                f"## 维度审查: {dim['name']}\n\n"
                f"[审查降级] 本维度审查失败（已重试），结果缺失: {err}"
            )
            state.setdefault("errors", []).append(f"审查维度「{dim['name']}」降级: {err}")
    combined = "\n\n---\n\n".join(parts)
    logger.info(
        "[task=%s] 维度拆分审查完成: %d/%d 维成功, 合并报告=%d 字符",
        req_name,
        len(results),
        len(_REVIEW_DIMENSIONS),
        len(combined),
    )
    return combined
