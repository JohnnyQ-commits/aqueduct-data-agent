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

        save_artifact(state, "数据质量测试.sql", dqc_sql)
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
    """尝试自动执行 DQC 测试用例（需要 数据平台执行工具）。"""
    try:
        from ...mcp.tools.execute_sql import HiveExecuteTool

        sql_tool = HiveExecuteTool()
        test_results = []

        dqc_lines = dqc_sql.split("\n")
        current_test = None
        current_sql: list[str] = []

        for line in dqc_lines:
            if line.strip().startswith("-- ["):
                if current_test:
                    sql_to_run = "\n".join(current_sql).strip().rstrip(";")
                    if sql_to_run:
                        res = sql_tool.execute(sql_to_run)
                        test_results.append(
                            {
                                "name": current_test,
                                "result": res.success,
                                "row_count": res.row_count,
                            }
                        )
                current_test = line.strip()
                current_sql = []
            else:
                current_sql.append(line)

        # 执行最后一个测试
        if current_test and current_sql:
            sql_to_run = "\n".join(current_sql).strip().rstrip(";")
            if sql_to_run:
                res = sql_tool.execute(sql_to_run)
                test_results.append(
                    {"name": current_test, "result": res.success, "row_count": res.row_count}
                )

        state["dqc_results"] = test_results
        logger.info("Phase 5 DQC 测试已自动执行，结果已记录")
    except Exception:
        logger.warning("DQC 自动执行失败，可能是未配置 数据平台执行工具", exc_info=True)
