"""Phase 5: DQC 质检节点。"""

from __future__ import annotations

import logging

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

logger = logging.getLogger(__name__)


def node_dqc(state: WorkflowState) -> WorkflowState:
    """Phase 5: DQC 质检节点。

    调用 DQCQualitySkill 生成 prompt -> LLM 生成质量测试用例。
    """
    try:
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
            state.setdefault("errors", []).append(f"DQC 质检失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "sql_gen", prompt)

        dqc_sql = extract_sql_block(llm_response)

        save_artifact(state, "Phase5-数据质量测试.sql", dqc_sql)
        state["dqc_result"] = dqc_sql
        state["metadata"] = {**(state.get("metadata", {})), "dqc_done": "true"}

        # 尝试自动执行 DQC 测试用例
        _auto_execute_dqc(state, dqc_sql)

        logger.info("Phase 5 DQC 质检完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"DQC 质检异常: {e!s}")
        logger.error("DQC 质检异常: %s", e, exc_info=True)

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
        batch_result = executor.execute_batch(
            sqls=[tc["sql"] for tc in test_cases]
        )

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
            merged.append({
                "name": tc["name"],
                "success": r.get("success", False),
                "rows": r.get("rows", []),
                "row_count": r.get("row_count", 0),
                "error": r.get("error", ""),
                "time_ms": r.get("time_ms", 0),
            })
        else:
            merged.append({
                "name": tc["name"],
                "success": False,
                "error": "未执行",
                "time_ms": 0,
            })

    return merged


def _generate_dqc_execution_report(
    merged: list[dict],
    batch_data: dict,
) -> str:
    """生成 DQC 执行报告 Markdown。"""
    from datetime import datetime

    total = batch_data.get("total", len(merged))
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
