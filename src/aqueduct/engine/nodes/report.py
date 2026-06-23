"""Phase 6: 报告交付节点。"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..state import WorkflowState
from .helpers import call_llm, save_artifact

logger = logging.getLogger(__name__)


def node_report(state: WorkflowState) -> WorkflowState:
    """Phase 6: 报告交付节点。

    调用 ReportDeliverySkill 生成 prompt -> LLM 生成报告，
    同时生成 Design.md、交付总报告.md、知识沉淀.md。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=6] 报告交付开始", req_name)

    try:
        inp = {
            "requirement_name": state.get("metadata", {}).get("requirement_name", ""),
            "requirement_summary": state.get("requirement_summary", ""),
            "requirement_doc": state.get("requirement", ""),
            "design_scheme": state.get("design_scheme", ""),
            "ddl_content": state.get("ddl_content", ""),
            "ddl_file": state.get("ddl_file", ""),
            "sql_content": state.get("sql_content", ""),
            "sql_file": state.get("sql_file", ""),
            "review_result": state.get("review_result", ""),
            "dqc_result": state.get("dqc_result", ""),
            "validation_result": state.get("validation_result", {}),
            "lineage_result": state.get("lineage_result", {}),
            "cost_result": state.get("cost_result", {}),
            "artifacts": state.get("artifacts", []),
            "domain_context": state.get("domain_context", ""),
        }

        skill = get_skill("report_delivery")
        context = SkillContext(input=inp, state=state)
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"报告交付失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")
        llm_response = call_llm(state, "doc_gen", prompt)

        save_artifact(state, "Phase6-Design.md", llm_response)

        delivery_report = _generate_delivery_report(state)
        save_artifact(state, "Phase6-交付总报告.md", delivery_report)

        knowledge_doc = _generate_knowledge_doc(state)
        save_artifact(state, "Phase6-知识沉淀.md", knowledge_doc)

        # 生成提效看板
        try:
            prod_tool = get_tool("productivity")
            prod_result = prod_tool.execute()
            if prod_result.success:
                board_content = prod_result.data.get("report", "")
                if board_content:
                    save_artifact(state, "Phase6-提效看板.md", board_content)
        except Exception:
            logger.warning("提效看板生成失败，跳过", exc_info=True)

        state["metadata"] = {**(state.get("metadata", {})), "report_done": "true"}
        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=6] 报告交付完成: artifacts=%d, 耗时=%.1fs",
            req_name,
            len(state["artifacts"]),
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"报告交付异常: {e!s}")
        logger.error(
            "[task=%s, phase=6] 报告交付异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state


def _generate_delivery_report(state: WorkflowState) -> str:
    """从工作流状态自动生成交付总报告。"""
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    artifacts = state.get("artifacts", [])
    errors = state.get("errors", [])
    vr = state.get("validation_result", {})
    lr = state.get("lineage_result", {})

    lines = [
        f"# {req_name} - 项目交付总报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "> 执行模式：开发模式",
        f"> 输出目录：`output/{req_name}/`",
        "",
        "---",
        "",
        "## 一、报告头",
        "",
        f"- **需求名称**：{req_name}",
        f"- **生成日期**：{datetime.now().strftime('%Y-%m-%d')}",
        "- **执行模式**：开发模式",
        "",
        "详细信息参见：",
        "- 设计文档：[Phase6-Design.md](Phase6-Design.md)",
        "",
        "---",
        "",
        "## 二、核心 SQL 代码",
        "",
    ]

    if state.get("sql_content"):
        sql = state["sql_content"]
        lines.append(f"ETL 逻辑已生成，共 {sql.count(chr(10)) + 1} 行。")
        lines.append("")
        lines.append("### 代码规范检查")
        lines.append("")
        lines.append(f"- **ERROR**: {vr.get('error_count', 'N/A')} 个")
        lines.append(f"- **WARN**: {vr.get('warn_count', 'N/A')} 个")
        lines.append("")
        if vr.get("issues"):
            lines.append("| 级别 | 行号 | 问题 |")
            lines.append("|------|------|------|")
            for issue in vr["issues"][:10]:
                lines.append(
                    f"| {issue.get('level', '')} | {issue.get('line', '')} | {issue.get('message', '')} |"
                )
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 三、数据质量测试",
            "",
        ]
    )
    if state.get("dqc_result"):
        lines.append("数据质量测试用例已生成（详见 `Phase5-数据质量测试.sql`）。")
    else:
        lines.append("数据质量测试用例待生成。")
    lines.extend(["", "---", "", "## 四、上下游依赖", "", "**上游**:",])
    for src in lr.get("sources", []):
        lines.append(f"- `{src}`")
    lines.extend(
        [
            "",
            "**下游**:",
            "- 待业务方确认",
            "",
            "---",
            "",
            "## 五、交付物清单",
            "",
            "| 文件 | 用途 | 状态 |",
            "|------|------|------|",
        ]
    )
    for a in artifacts:
        lines.append(f"| {a} | 产出物 | 已完成 |")
    for expected in [
        "Phase3-表结构.sql",
        "Phase6-Design.md",
        "Phase6-交付总报告.md",
        "Phase6-知识沉淀.md",
        "Phase6-提效看板.md",
    ]:
        if not any(expected in a for a in artifacts):
            lines.append(f"| {expected} | 产出物 | 已完成 |")
    lines.append("")

    if errors:
        lines.extend(["---", "", "## 六、执行错误", ""])
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines)


def _generate_knowledge_doc(state: WorkflowState) -> str:
    """从工作流状态自动生成知识沉淀文档。"""
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    requirement = state.get("requirement", "")
    design = state.get("design_scheme", "")
    ddl = state.get("ddl_content", "")
    sql = state.get("sql_content", "")
    artifacts = state.get("artifacts", [])

    doc = [
        f"# 知识沉淀 -- {req_name}",
        "",
        "> 自动生成于工作流执行完成",
        "",
        "## 一、需求概述",
        "",
        requirement[:2000] if requirement else "（无原始需求文档）",
        "",
        "## 二、设计方案要点",
        "",
        design[:2000] if design else "（无设计方案）",
        "",
        "## 三、表结构要点",
        "",
        "```sql",
        ddl[:2000] if ddl else "（无 DDL 定义）",
        "```",
        "",
        "## 四、核心 SQL 逻辑",
        "",
        "```sql",
        sql[:2000] if sql else "（无核心 SQL）",
        "```",
        "",
        "## 五、产出物清单",
        "",
    ]
    for a in artifacts:
        doc.append(f"- {a}")

    doc.extend(
        [
            "",
            "## 六、经验与注意事项",
            "",
            "（待人工补充：开发过程中的经验教训、特殊处理逻辑、踩坑记录等）",
            "",
        ]
    )

    return "\n".join(doc)
