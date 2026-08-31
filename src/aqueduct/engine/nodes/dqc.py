"""Phase 5: DQC 质检节点。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, is_valid_sql, save_artifact

logger = logging.getLogger(__name__)


def _dqc_input_hash(state: WorkflowState) -> int:
    """计算投机 DQC 的输入指纹（sql + ddl）。

    修复循环改写 SQL 后哈希变化，node_dqc 据此丢弃过期投机结果。
    """
    return hash((state.get("sql_content", ""), state.get("ddl_content", "")))


def build_dqc_prompt(state: WorkflowState) -> str | None:
    """构建 dqc_quality 的 prompt（投机启动与正常路径共用）。

    Returns:
        prompt 文本。Skill 执行失败时返回 None。
    """
    skill = get_skill("dqc_quality")
    context = SkillContext(
        input={
            "ddl_content": state.get("ddl_content", ""),
            "sql_content": state.get("sql_content", ""),
            "domain_context": state.get("domain_context", ""),
        },
        state=state,
    )
    result = skill.execute(context)
    if not result.success:
        return None
    return result.data.get("prompt", "")


def start_dqc_speculative(state: WorkflowState) -> None:
    """投机启动 DQC 生成（PERF-9）：与 Phase 4.5 审查并行。

    DQC 的输入（ddl/sql/domain_context）不依赖审查结果，唯一串行原因是
    修复循环可能改写 SQL——node_dqc 消费时用输入哈希护栏兜住：
    哈希不一致即丢弃、走正常重新生成（见 take_speculative_dqc）。

    Future/executor 存 state（同 _lineage_future 范式）；
    修复循环重跑 review 时，本函数会关闭并替换上一轮的投机。
    失败不阻塞：不启动投机，Phase 5 走正常路径。
    """
    try:
        # 清理上一轮（修复循环重跑 review）的旧投机
        old_executor = state.pop("_dqc_spec_executor", None)
        state.pop("_dqc_spec_future", None)
        state.pop("_dqc_spec_input_hash", None)
        if old_executor:
            old_executor.shutdown(wait=False)

        sql_content = state.get("sql_content", "")
        # 与血缘守卫一致：无效/过短 SQL 不启动（含单测短 SQL 场景）
        if not is_valid_sql(sql_content):
            return

        prompt = build_dqc_prompt(state)
        if prompt is None:
            logger.warning("投机 DQC 启动失败（Skill 执行失败），Phase 5 走正常路径")
            return

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dqc-spec")
        future = executor.submit(call_llm, state, "dqc_gen", prompt)
        state["_dqc_spec_future"] = future
        state["_dqc_spec_executor"] = executor
        state["_dqc_spec_input_hash"] = _dqc_input_hash(state)
        logger.info("投机 DQC 生成已在后台启动（与审查并行，PERF-9）")
    except Exception:
        logger.warning("投机 DQC 启动失败，Phase 5 走正常路径", exc_info=True)
        state.pop("_dqc_spec_future", None)
        executor = state.pop("_dqc_spec_executor", None)
        state.pop("_dqc_spec_input_hash", None)
        if executor:
            executor.shutdown(wait=False)


def take_speculative_dqc(state: WorkflowState) -> str | None:
    """消费投机 DQC 结果：输入哈希一致才复用，否则丢弃。

    Returns:
        LLM 响应文本。无投机/哈希不一致/调用失败时返回 None（调用方走正常路径）。
    """
    future: Future | None = state.pop("_dqc_spec_future", None)
    executor: ThreadPoolExecutor | None = state.pop("_dqc_spec_executor", None)
    input_hash = state.pop("_dqc_spec_input_hash", None)

    if future is None:
        return None

    try:
        if input_hash != _dqc_input_hash(state):
            logger.info("投机 DQC 丢弃：SQL 已被修复循环改写（输入哈希不一致）")
            return None

        from ...config.settings import get_settings

        timeout = get_settings().llm_timeout_seconds
        response = future.result(timeout=timeout)
        logger.info("投机 DQC 命中：复用与审查并行生成的结果（PERF-9）")
        return response
    except Exception:
        logger.warning("投机 DQC 消费失败，走正常路径", exc_info=True)
        return None
    finally:
        if executor:
            executor.shutdown(wait=False)


def node_dqc(state: WorkflowState) -> WorkflowState:
    """Phase 5: DQC 质检节点。

    调用 DQCQualitySkill 生成 prompt -> LLM 生成质量测试用例。
    优先消费 Phase 4.5 投机启动的并行结果（PERF-9，哈希护栏校验）。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=5] DQC 质检开始", req_name)

    try:
        # PERF-9: 优先复用与审查并行的投机结果
        llm_response = take_speculative_dqc(state)

        if llm_response is None:
            prompt = build_dqc_prompt(state)
            if prompt is None:
                state.setdefault("errors", []).append("DQC 质检失败: Skill 执行异常")
                return state
            llm_response = call_llm(state, "dqc_gen", prompt)

        dqc_sql = extract_sql_block(llm_response)

        save_artifact(state, "Phase5-数据质量测试.sql", dqc_sql)
        state["dqc_result"] = dqc_sql
        state["metadata"] = {**(state.get("metadata", {})), "dqc_done": "true"}

        # 尝试自动执行 DQC 测试用例
        _auto_execute_dqc(state, dqc_sql)

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=5] DQC 质检完成: dqc=%d 字符, 耗时=%.1fs",
            req_name,
            len(dqc_sql),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"DQC 质检异常: {e!s}")
        logger.error(
            "[task=%s, phase=5] DQC 质检异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state


def _auto_execute_dqc(state: WorkflowState, dqc_sql: str) -> None:
    """通过注册工具执行 DQC 测试用例。

    执行失败不阻塞流程，标记 dqc_execution_skipped 继续。
    """
    try:
        from ...tools.registry import get_tool

        executor = get_tool("executor")

        # 先检查连接
        health = executor.health_check()
        if not health.success:
            logger.info("DQC 执行跳过: %s", health.data.get("message", ""))
            state["dqc_execution_skipped"] = True
            state["dqc_execution_reason"] = health.data.get("message", "")
            return

        # 解析 DQC SQL 为多条测试
        test_cases = _parse_dqc_sql(dqc_sql)
        if not test_cases:
            logger.info("DQC SQL 中未解析出测试用例")
            state["dqc_execution_skipped"] = True
            state["dqc_execution_reason"] = "未解析出测试用例"
            return

        # 批量执行
        batch_result = executor.execute_batch(sqls=[tc["sql"] for tc in test_cases])

        # 合并结果
        merged = _merge_dqc_results(test_cases, batch_result.data)
        state["dqc_results"] = merged
        state["dqc_execution_skipped"] = False

        # 生成执行报告
        report = _generate_dqc_execution_report(merged, batch_result.data)
        save_artifact(state, "Phase5-DQC执行报告.md", report)

        logger.info(
            "Phase 5 DQC 执行完成: %d passed, %d failed",
            batch_result.data.get("passed", 0),
            batch_result.data.get("failed", 0),
        )
    except Exception:
        logger.warning("DQC 执行异常，不阻塞流程", exc_info=True)
        state["dqc_execution_skipped"] = True
        state["dqc_execution_reason"] = "执行异常"


def _parse_dqc_sql(dqc_sql: str) -> list[dict[str, str]]:
    """解析 DQC SQL，按 '-- [' 注释拆分为多条测试用例。"""
    test_cases: list[dict[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in dqc_sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("-- ["):
            # 保存前一条
            if current_name and current_lines:
                sql = "\n".join(current_lines).strip().rstrip(";")
                if sql:
                    test_cases.append({"name": current_name, "sql": sql})
            current_name = stripped
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一条
    if current_name and current_lines:
        sql = "\n".join(current_lines).strip().rstrip(";")
        if sql:
            test_cases.append({"name": current_name, "sql": sql})

    return test_cases


def _merge_dqc_results(
    test_cases: list[dict[str, str]],
    batch_data: dict,
) -> list[dict]:
    """将测试用例名称与执行结果合并。"""
    merged: list[dict] = []
    results = batch_data.get("results", [])

    for idx, tc in enumerate(test_cases):
        if idx < len(results):
            r = results[idx]
            merged.append(
                {
                    "name": tc["name"],
                    "success": r.get("success", False),
                    "rows": r.get("rows", []),
                    "row_count": r.get("row_count", 0),
                    "error": r.get("error", ""),
                    "time_ms": r.get("time_ms", 0),
                }
            )
        else:
            merged.append(
                {
                    "name": tc["name"],
                    "success": False,
                    "error": "未执行",
                    "time_ms": 0,
                }
            )

    return merged


def _generate_dqc_execution_report(
    merged: list[dict],
    batch_data: dict,
) -> str:
    """生成 DQC 执行报告 Markdown。"""
    from datetime import datetime

    passed = batch_data.get("passed", sum(1 for m in merged if m["success"]))
    failed = batch_data.get("failed", sum(1 for m in merged if not m["success"]))
    total_time_ms = batch_data.get("total_time_ms", 0)

    lines = [
        "# DQC 执行报告",
        "",
        f"> 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 执行耗时：{total_time_ms / 1000:.1f}s",
        f"> 结果：{passed} PASS / {failed} FAIL",
        "",
        "| # | 规则名称 | 结果 | 耗时 | 备注 |",
        "|---|---------|------|------|------|",
    ]

    for i, m in enumerate(merged, 1):
        status = "PASS" if m["success"] else "FAIL"
        time_str = f"{m['time_ms']}ms"
        note = m.get("error", "") if not m["success"] else ""
        name = m["name"].replace("|", "\\|")
        lines.append(f"| {i} | {name} | {status} | {time_str} | {note} |")

    lines.append("")
    return "\n".join(lines)
