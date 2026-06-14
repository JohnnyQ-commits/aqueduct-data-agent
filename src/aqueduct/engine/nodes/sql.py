"""Phase 4: SQL 开发节点。"""

from __future__ import annotations

import logging
from pathlib import Path

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, save_artifact

logger = logging.getLogger(__name__)


def node_sql(state: WorkflowState) -> WorkflowState:
    """Phase 4: SQL 开发节点。

    调用 SQLDevelopSkill 生成 prompt -> LLM 生成 SQL -> 自动校验。
    """
    try:
        skill = get_skill("sql_develop")
        context = SkillContext(
            input={
                "requirement_doc": state.get("requirement", ""),
                "ddl_content": state.get("ddl_content", ""),
                "design_scheme": state.get("design_scheme", ""),
                "domain_context": state.get("domain_context", ""),
            },
            state=state,
        )
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"SQL 开发失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "sql_gen", prompt)

        sql_content = extract_sql_block(llm_response)

        req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
        sql_path = save_artifact(state, f"{req_name}.sql", sql_content)
        state["sql_content"] = sql_content
        state["sql_file"] = sql_path
        state["metadata"] = {**(state.get("metadata", {})), "sql_done": "true"}

        # 自动运行 SQL 校验
        _auto_validate(state, sql_path)
        # 自动运行血缘解析
        _auto_lineage(state, sql_path)
        # 自动运行成本预估
        _auto_cost_estimate(state, sql_path)

        logger.info("Phase 4 SQL 开发完成")
    except Exception as e:
        state.setdefault("errors", []).append(f"SQL 开发异常: {e!s}")
        logger.error("SQL 开发异常: %s", e, exc_info=True)

    return state


def _resolve_sql_path(state: WorkflowState, rel_path: str) -> Path:
    """将 save_artifact 返回的相对路径解析为绝对路径。"""
    from .helpers import _PROJECT_ROOT

    p = Path(rel_path)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / rel_path


def _auto_validate(state: WorkflowState, sql_path: str) -> None:
    """自动运行 SQL 校验并生成报告。"""
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        validator = get_tool("validator")
        validation_result = validator.execute(sql_file=str(abs_path))
        state["validation_result"] = validation_result.data

        if validation_result.data is None:
            logger.warning("SQL 校验返回空结果: %s", validation_result.error)
            return

        vr = validation_result.data
        report_lines = [
            "# SQL 校验报告",
            "",
            f"- **文件**: {vr.get('filename', sql_path)}",
            f"- **ERROR**: {vr.get('error_count', 0)} 个",
            f"- **WARN**: {vr.get('warn_count', 0)} 个",
            "",
        ]
        for issue in vr.get("issues", []):
            level = issue.get("level", "INFO")
            msg = issue.get("message", "")
            line = issue.get("line", "")
            report_lines.append(f"- [{level}] Line {line}: {msg}")
        save_artifact(state, "SQL校验报告.md", "\n".join(report_lines))
        logger.info(
            "SQL 校验完成: %d errors, %d warnings",
            vr.get("error_count", 0),
            vr.get("warn_count", 0),
        )
    except Exception:
        logger.warning("SQL 校验失败，跳过", exc_info=True)


def _auto_lineage(state: WorkflowState, sql_path: str) -> None:
    """自动运行血缘解析并生成血缘图。"""
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        lineage_tool = get_tool("lineage")
        lineage_result = lineage_tool.execute(sql_file=str(abs_path))
        state["lineage_result"] = lineage_result.data

        if lineage_result.data is None:
            logger.warning("血缘解析返回空结果: %s", lineage_result.error)
            return

        lr = lineage_result.data
        mermaid = lr.get("mermaid", "")
        sources = lr.get("sources", [])
        target = lr.get("target", "unknown")
        lines = [
            "# 字段级血缘图",
            "",
            f"目标表: `{target}`",
            f"来源表: {len(sources)} 张",
            "",
            mermaid,
            "",
            "## 来源表清单",
            "",
        ]
        for src in sources:
            lines.append(f"- `{src}`")
        save_artifact(state, "字段级血缘图.md", "\n".join(lines))
        logger.info("血缘解析完成: %d source tables", len(sources))
    except Exception:
        logger.warning("血缘解析失败，跳过", exc_info=True)


def _auto_cost_estimate(state: WorkflowState, sql_path: str) -> None:
    """自动运行成本预估。"""
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        cost_tool = get_tool("estimator")
        cost_result = cost_tool.execute(sql_file=str(abs_path))
        state["cost_result"] = cost_result.data

        if cost_result.data is None:
            logger.warning("成本预估返回空结果: %s", cost_result.error)
            return

        report_md = cost_result.data.get("report", "")
        if report_md:
            save_artifact(state, "成本预警.md", report_md)
        logger.info("成本预估完成")
    except Exception:
        logger.warning("成本预估失败，跳过", exc_info=True)
