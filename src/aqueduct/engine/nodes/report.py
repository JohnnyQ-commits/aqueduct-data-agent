"""Phase 6: 报告交付节点。"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..state import WorkflowState
from .helpers import call_llm, save_artifact
from .sql import wait_for_lineage

logger = logging.getLogger(__name__)


def node_report(state: WorkflowState) -> WorkflowState:
    """Phase 6: 报告交付节点。

    调用 ReportDeliverySkill 生成 prompt -> LLM 生成报告，
    同时生成 Design.md、交付总报告.md、知识沉淀.md。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=6] 报告交付开始", req_name)

    # OPT-5: 等待后台血缘 LLM 调用完成（在 Phase 4 中异步启动）
    wait_for_lineage(state)

    try:
        inp = {
            "requirement_name": state.get("metadata", {}).get("requirement_name", ""),
            "design_scheme": state.get("design_scheme", ""),
            "ddl_content": state.get("ddl_content", ""),
            "sql_content": state.get("sql_content", ""),
            "dqc_result": state.get("dqc_result", ""),
            "lineage_result": state.get("lineage_result") or {},
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

        # 自动更新 domain.json（从 DDL/SQL 提取增量数据，dict-level 合并）
        _update_domain_json(state)

        # 自动更新知识库语义文档（per-domain + INDEX.md）
        _regenerate_semantic_docs(state)

        # 生成提效看板
        try:
            prod_tool = get_tool("productivity")
            dqc_results = state.get("dqc_result") or {}
            dqc_data = dqc_results.get("results", []) if isinstance(dqc_results, dict) else []
            prod_result = prod_tool.execute(
                dqc_tests_run=len(dqc_data),
                dqc_auto_fixes=sum(1 for r in dqc_data if r.get("status") == "PASSED"),
            )
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
    vr = state.get("validation_result") or {}
    lr = state.get("lineage_result") or {}

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
    lines.extend(
        [
            "",
            "---",
            "",
            "## 四、上下游依赖",
            "",
            "**上游**:",
        ]
    )
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
        found = any(expected in a for a in artifacts)
        status = "已完成" if found else "缺失"
        lines.append(f"| {expected} | 产出物 | {status} |")
    lines.append("")

    if errors:
        lines.extend(["---", "", "## 六、执行错误", ""])
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines)


def _generate_knowledge_doc(state: WorkflowState) -> str:
    """通过 LLM 从工作流产出物中提炼知识沉淀文档。

    使用 knowledge_extract 模板调用 Sonnet，提取结构化业务知识。
    失败时 fallback 到纯 Python 截断拼接。
    """
    from string import Template

    from ...config.settings import get_settings

    req_name = state.get("metadata", {}).get("requirement_name", "unknown")

    try:
        settings = get_settings()
        tpl_path = settings.prompt_dir / "knowledge_extract.tpl.md"
        if not tpl_path.exists():
            logger.warning("知识提取模板不存在: %s，使用 fallback", tpl_path)
            return _generate_knowledge_doc_fallback(state)

        content = tpl_path.read_text(encoding="utf-8")
        prompt = Template(content).safe_substitute(
            requirement_name=req_name,
            requirement=state.get("requirement", "")[:3000],
            design_scheme=state.get("design_scheme", "")[:3000],
            ddl_content=state.get("ddl_content", "")[:2000],
            sql_content=state.get("sql_content", "")[:5000],
            review_result=(state.get("review_result") or "")[:3000],
            domain_context=state.get("domain_context", "")[:2000],
            table_schemas=_format_table_schemas(state.get("table_schemas", {})),
        )

        knowledge_doc = call_llm(state, "knowledge_extract", prompt)

        if not knowledge_doc or len(knowledge_doc.strip()) < 100:
            logger.warning(
                "知识提取 LLM 返回过短（%d 字符），使用 fallback", len(knowledge_doc or "")
            )
            return _generate_knowledge_doc_fallback(state)

        logger.info("知识沉淀 LLM 提取完成: %d 字符", len(knowledge_doc))
        return knowledge_doc

    except Exception:
        logger.warning("知识提取 LLM 调用失败，使用 fallback", exc_info=True)
        return _generate_knowledge_doc_fallback(state)


def _format_table_schemas(table_schemas: dict[str, str]) -> str:
    """将 table_schemas dict 格式化为可读文本。"""
    if not table_schemas:
        return "（无表结构信息）"
    lines = []
    for table_name, schema in table_schemas.items():
        lines.append(f"### {table_name}")
        lines.append(schema[:500])
        lines.append("")
    return "\n".join(lines)


def _generate_knowledge_doc_fallback(state: WorkflowState) -> str:
    """fallback: 纯 Python 截断拼接（无 LLM）。"""
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    requirement = state.get("requirement", "")
    design = state.get("design_scheme", "")
    ddl = state.get("ddl_content", "")
    sql = state.get("sql_content", "")
    artifacts = state.get("artifacts", [])

    doc = [
        f"# 知识沉淀 — {req_name}",
        "",
        "> 自动生成于工作流执行完成（fallback 模式）",
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


def _update_domain_json(state: WorkflowState) -> None:
    """自动更新 domain.json：从 DDL/SQL 提取增量数据，dict-level 合并写入。

    策略：
    - 已有域：加载现有 domain.json → 合并新实体/指标/过滤规则 → 写回
    - 新域：创建骨架 + 提取数据 → 写入
    - 合并只增不覆盖（保留人工精写的 description 等字段）
    - 失败不阻塞管道
    """
    from ...config.settings import get_settings
    from ...utils.domain_extract import (
        create_new_domain,
        extract_entities_from_ddl,
        extract_filter_rules,
        extract_metrics_from_sql,
        load_domain_dict,
        merge_domain_updates,
        save_domain_dict,
    )

    req_name = state.get("metadata", {}).get("requirement_name", "unknown")

    try:
        ddl = state.get("ddl_content", "")
        sql = state.get("sql_content", "")
        if not ddl and not sql:
            logger.debug("[task=%s] 无 DDL/SQL 内容，跳过 domain.json 更新", req_name)
            return

        # 确定 domain_id
        domain_id = state.get("domain_id")
        if not domain_id:
            # 从需求名称生成 domain_id
            domain_id = re.sub(r"[^\w一-鿿]", "_", req_name)[:40].strip("_")
            if not domain_id:
                logger.debug("[task=%s] 无法确定 domain_id，跳过", req_name)
                return

        # 提取增量数据
        updates: dict = {}
        if ddl:
            entities = extract_entities_from_ddl(ddl)
            if entities:
                updates["entities"] = entities
                logger.info("[task=%s] 从 DDL 提取 %d 个实体", req_name, len(entities))
        if sql:
            metrics = extract_metrics_from_sql(sql)
            if metrics:
                updates["metrics"] = metrics
                logger.info("[task=%s] 从 SQL 提取 %d 个指标", req_name, len(metrics))
            filter_rules = extract_filter_rules(sql)
            if filter_rules:
                updates["filter_rules"] = filter_rules

        if not updates:
            logger.debug("[task=%s] 未提取到任何数据，跳过 domain.json 更新", req_name)
            return

        # 确定 domain.json 路径（只写入内部知识库）
        settings = get_settings()
        domains_dir = settings.project_root / "internal" / "knowledge" / "domains"
        domain_path = domains_dir / domain_id / "domain.json"

        # 加载现有或创建新域
        existing = load_domain_dict(domain_path)
        if existing:
            merge_domain_updates(existing, updates)
            logger.info("[task=%s] 已合并增量数据到 %s", req_name, domain_path)
        else:
            # 新域：从需求名推断中文名称
            domain_name = req_name.split("/")[-1].split("\\")[-1][:30]
            existing = create_new_domain(domain_id, domain_name, updates)
            logger.info("[task=%s] 创建新域 %s: %s", req_name, domain_id, domain_path)

        save_domain_dict(domain_path, existing)

    except Exception:
        logger.warning("domain.json 更新失败，跳过", exc_info=True)


def _regenerate_semantic_docs(state: WorkflowState) -> None:
    """自动更新知识库语义文档（per-domain semantic-model.md + INDEX.md）。

    只更新内部知识库（internal/knowledge/domains），不更新公开版。
    失败不阻塞管道，只记录 warning。
    """
    from ...config.settings import get_settings

    try:
        settings = get_settings()
        semantic_tool = get_tool("semantic")

        # 只更新内部知识库
        internal_dir = settings.project_root / "internal" / "knowledge" / "domains"
        if not internal_dir.exists():
            logger.info("首次运行，自动创建内部知识库: %s", internal_dir)
            internal_dir.mkdir(parents=True, exist_ok=True)
        result = semantic_tool.execute(domains_dir=str(internal_dir), mode="all")
        if result.success:
            logger.info(
                "内部知识库语义文档已更新: %d 个域, %d 个文件",
                result.data.get("domain_count", 0),
                len(result.data.get("files", [])),
            )
        else:
            logger.warning("内部知识库语义文档更新失败: %s", result.error)

    except Exception:
        logger.warning("语义文档自动更新失败，跳过", exc_info=True)
