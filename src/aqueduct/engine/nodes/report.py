"""Phase 6: 报告交付节点。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..contract import (
    _add_banner,
    build_retry_prompt,
    ensure_structure,
    scan_degradation,
    validate_structure,
)
from ..state import WorkflowState
from .helpers import call_llm, is_valid_sql, save_artifact
from .sql import wait_for_lineage

logger = logging.getLogger(__name__)

# 洞察章节空值兜底注记（章节头永不下线，保结构契约确定性通过）
_INSIGHT_FALLBACK_BG = "（需求背景生成失败，请人工补充）"
_INSIGHT_FALLBACK_Q = "（待确认问题清单生成失败，请人工补充）"


def _knowledge_input_hash(state: WorkflowState) -> int:
    """计算投机知识提取的输入指纹（prompt 的全部输入，见 _generate_knowledge_doc）。

    修复循环改写 SQL 后哈希变化，node_report 据此丢弃过期投机结果。
    """
    return hash(
        (
            state.get("requirement", ""),
            state.get("design_scheme", ""),
            state.get("ddl_content", ""),
            state.get("sql_content", ""),
            state.get("domain_context", ""),
            str(sorted((state.get("table_schemas") or {}).items())),
        )
    )


def start_knowledge_speculative(state: WorkflowState) -> None:
    """投机启动知识提取（P2-2）：与 Phase 4.5 审查并行。

    knowledge_extract 的输入（需求/设计方案/DDL/SQL/域知识/表结构）不依赖
    审查结果，唯一串行原因是修复循环可能改写 SQL——node_report 消费时用
    输入哈希护栏兜住：哈希不一致即丢弃、走正常重新生成
    （见 take_speculative_knowledge）。151–186s 的调用由此藏进审查窗口
    （458–518s），Phase 6 只剩 doc_gen。

    Future/executor 存 state（同 _lineage_future / _dqc_spec_future 范式）；
    修复循环回跳重跑 review 时，本函数会关闭并替换上一轮的投机。
    失败不阻塞：不启动投机，Phase 6 走正常路径。
    """
    executor = None
    try:
        # 清理上一轮（修复循环回跳 review）的旧投机
        old_executor = state.pop("_kn_spec_executor", None)
        state.pop("_kn_spec_future", None)
        state.pop("_kn_spec_input_hash", None)
        if old_executor:
            old_executor.shutdown(wait=False)

        # 与血缘/DQC 守卫一致：无效/过短 SQL 不启动（含单测短 SQL 场景）
        if not is_valid_sql(state.get("sql_content", "")):
            return

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kn-spec")
        future = executor.submit(_generate_knowledge_doc, state)
        state["_kn_spec_future"] = future
        state["_kn_spec_executor"] = executor
        state["_kn_spec_input_hash"] = _knowledge_input_hash(state)
        logger.info("投机知识提取已在后台启动（与审查并行，P2-2）")
    except Exception:
        logger.warning("投机知识提取启动失败，Phase 6 走正常路径", exc_info=True)
        state.pop("_kn_spec_future", None)
        state.pop("_kn_spec_input_hash", None)
        if executor:
            executor.shutdown(wait=False)


def take_speculative_knowledge(state: WorkflowState) -> str | None:
    """消费投机知识提取结果：输入哈希一致才复用，否则丢弃。

    Returns:
        知识沉淀文档文本。无投机/哈希不一致/调用失败时返回 None（调用方走正常路径）。
    """
    future: Future | None = state.pop("_kn_spec_future", None)
    executor: ThreadPoolExecutor | None = state.pop("_kn_spec_executor", None)
    input_hash = state.pop("_kn_spec_input_hash", None)

    if future is None:
        return None

    try:
        if input_hash != _knowledge_input_hash(state):
            logger.info("投机知识提取丢弃：SQL 已被修复循环改写（输入哈希不一致）")
            return None

        from ...config.settings import get_settings

        timeout = get_settings().llm_timeout_seconds
        response = future.result(timeout=timeout)
        logger.info("投机知识提取命中：复用与审查并行生成的结果（P2-2）")
        return response
    except Exception:
        logger.warning("投机知识提取消费失败，走正常路径", exc_info=True)
        return None
    finally:
        if executor:
            executor.shutdown(wait=False)


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
        # PERF-4 拆分：doc_gen 只产洞察两章（需求背景/待确认问题清单），
        # 设计方案/DDL/SQL/血缘图由 _assemble_design_doc 本地拼装
        inp = {
            "requirement_name": state.get("metadata", {}).get("requirement_name", ""),
            "design_scheme": state.get("design_scheme", ""),
            "dqc_result": state.get("dqc_result", ""),
            "domain_context": state.get("domain_context", ""),
        }

        skill = get_skill("report_delivery")
        context = SkillContext(input=inp, state=state)
        result = skill.execute(context)

        if not result.success:
            state.setdefault("errors", []).append(f"报告交付失败: {result.error}")
            return state

        prompt = result.data.get("prompt", "")

        # PERF-3: doc_gen 与 knowledge_extract 输入互相独立（都来自 state），
        # 并行执行使 Phase 6 耗时 ≈ max(两次调用) 而非求和。
        # 与 Phase 4 血缘异步（sql.py wait_for_lineage）同为线程池范式。
        # P2-2: 先提交 doc_gen 再消费投机知识（审查窗口后台生成）——等待
        # 期间 doc_gen 已在跑，两者不串行；投机未命中走原并行路径。
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="report") as executor:
            doc_future = executor.submit(call_llm, state, "doc_gen", prompt)
            kn_spec = take_speculative_knowledge(state)
            if kn_spec is not None:
                knowledge_doc = kn_spec
                insights = doc_future.result()
            else:
                kn_future = executor.submit(_generate_knowledge_doc, state)
                insights = doc_future.result()
                knowledge_doc = kn_future.result()

        # 洞察拆章 → 本地拼装
        background, questions = _extract_insight_chapters(insights)

        # P0-2 结构门禁（PERF-4 形态）：4 个本地章节确定性过契约，唯一依赖
        # LLM 的需求背景缺失 → 1 次定向重生成洞察 + 重拼装；仍缺横幅降级 + errors
        design_missing: list[str] = []
        if not background:
            design_missing = ["需求背景"]
            logger.warning("[task=%s, phase=6] 洞察缺需求背景章，定向重生成 1 次", req_name)
            retry_insights = call_llm(
                state,
                "doc_gen",
                build_retry_prompt(prompt, "Phase6-Design.md", design_missing),
            )
            background, questions = _extract_insight_chapters(retry_insights)
            if background:
                design_missing = []

        design_doc = _assemble_design_doc(
            requirement_name=req_name,
            design_scheme=state.get("design_scheme", ""),
            ddl_content=state.get("ddl_content", ""),
            sql_content=state.get("sql_content", ""),
            lineage_mermaid=_lineage_mermaid(state),
            background=background,
            open_questions=questions,
        )

        if design_missing:
            design_doc = _add_banner(design_doc, "Phase6-Design.md", design_missing)
            state.setdefault("errors", []).append(
                f"Phase6-Design.md 结构缺章（定向重生成 1 次后仍缺）: {'、'.join(design_missing)}"
            )
            logger.warning(
                "[task=%s, phase=6] Design.md 结构缺章降级落盘: %s",
                req_name,
                "、".join(design_missing),
            )

        save_artifact(state, "Phase6-Design.md", design_doc)

        delivery_report = _generate_delivery_report(state)
        save_artifact(state, "Phase6-交付总报告.md", delivery_report)

        save_artifact(state, "Phase6-知识沉淀.md", knowledge_doc)

        # P0-2: 知识沉淀降级横幅扫描（线程内降级不碰 state，主线程统一记 errors）
        for line in scan_degradation(knowledge_doc):
            state.setdefault("errors", []).append(f"Phase6-知识沉淀.md 结构缺章: {line}")
            logger.warning("[task=%s, phase=6] 知识沉淀结构缺章降级落盘: %s", req_name, line)

        # 自动更新 domain.json（从 DDL/SQL 提取增量数据，dict-level 合并）
        _update_domain_json(state)

        # 自动更新知识库语义文档（per-domain + INDEX.md）
        _regenerate_semantic_docs(state)

        # 生成提效看板
        try:
            prod_tool = get_tool("productivity")
            # DQC 计数来自执行结果列表（dqc.py state["dqc_results"]，list[dict]，
            # PASS 口径 m["success"]）；state["dqc_result"] 是 DQC SQL 文本，勿混用
            dqc_data = state.get("dqc_results") or []
            if not isinstance(dqc_data, list):
                dqc_data = []
            prod_result = prod_tool.execute(
                dqc_tests_run=len(dqc_data),
                dqc_auto_fixes=sum(1 for r in dqc_data if r.get("success")),
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


# ── PERF-4: Design.md 拆分（洞察 LLM 生成 + 结构本地拼装） ───────────────────


def _extract_insight_chapters(text: str) -> tuple[str, str]:
    """从 LLM 洞察响应中拆出（需求背景, 待确认问题清单）两章正文。

    需求背景是必需锚点章——缺失时整个响应视为不可用，返回 ("", "")，
    由节点触发定向重生成（2026-09-08 三跑实证：网关降质时整段空响应，
    部分响应不值得信任）。容忍 H2/H3 标题与整体 markdown 围栏；章节
    正文止于下一个标题行；多余章节直接丢弃（本地填充说了算）。
    """
    if not text or not text.strip():
        return "", ""

    stripped = text.strip()
    # 整体围栏剥离（```markdown ... ``` / ``` ... ```）
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[\w-]*[ \t]*\n?", "", stripped)
        stripped = re.sub(r"\n?```[ \t]*$", "", stripped)

    background = ""
    questions = ""
    headers = list(re.finditer(r"^#{1,6}[ \t]+([^\n]+)$", stripped, re.MULTILINE))
    for idx, m in enumerate(headers):
        title = _normalize_header(m.group(1))
        body_end = headers[idx + 1].start() if idx + 1 < len(headers) else len(stripped)
        body = stripped[m.end() : body_end].strip()
        if not background and ("需求背景" in title or title.startswith("背景")):
            background = body
        elif not questions and "待确认" in title:
            questions = body

    if not background:
        return "", ""
    return background, questions


def _normalize_header(title: str) -> str:
    """标题规整：去序号前缀（一、/1.）与加粗修饰。"""
    t = title.strip().strip("*").strip()
    return re.sub(r"^[（(【\[]?[一二三四五六七八九十\d]{1,3}[)）】\]、.．]*\s*", "", t)


def _strip_leading_h1(text: str) -> str:
    """剥离首行 H1（拼装文档提供自己的文档级标题），其余原样保留。"""
    if re.match(r"^#[ \t]", text):
        return text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""
    return text


def _strip_leading_matching_header(text: str, title: str) -> str:
    """剥离与指定标题等价的首行标题（H1-H6，规整去序号后比对）。

    design_scheme 等中间产物常自带"## 设计方案"章头，拼装再补一个
    章头会得到连续同名标题；只剥首行、只剥等价标题，其余原样保留。
    """
    m = re.match(r"^\s*#{1,6}[ \t]+([^\n]+)", text)
    if m and _normalize_header(m.group(1)) == _normalize_header(title):
        return text[m.end() :].lstrip("\n")
    return text


def _lineage_mermaid(state: WorkflowState) -> str:
    """从 state 提取血缘 mermaid 文本（异常形状兜底空串）。"""
    lineage = state.get("lineage_result") or {}
    if isinstance(lineage, dict):
        return lineage.get("mermaid", "") or ""
    return ""


def _assemble_design_doc(
    requirement_name: str,
    design_scheme: str,
    ddl_content: str,
    sql_content: str,
    lineage_mermaid: str,
    background: str,
    open_questions: str,
) -> str:
    """本地拼装 Phase6-Design.md（PERF-4：零 LLM、零转写失真）。

    6 章头永远在场：设计方案/表结构/核心 SQL/血缘图逐字取自管道产物，
    需求背景/待确认问题清单来自 LLM 洞察（空值兜底注记——章节头由
    拼装兜底永不下线，保结构契约确定性通过）。
    """
    scheme = _strip_leading_matching_header(_strip_leading_h1(design_scheme.strip()), "设计方案")

    parts = [
        f"# {requirement_name} — 设计文档",
        "",
        "## 需求背景",
        "",
        background.strip() or _INSIGHT_FALLBACK_BG,
        "",
        "## 设计方案",
        "",
        scheme or "（设计方案未生成）",
        "",
        "## 表结构(DDL)",
        "",
    ]
    if ddl_content.strip():
        parts += ["```sql", ddl_content.rstrip(), "```"]
    else:
        parts.append("（DDL 未生成）")

    parts += ["", "## 核心 SQL", ""]
    if sql_content.strip():
        parts += ["```sql", sql_content.rstrip(), "```"]
    else:
        parts.append("（核心 SQL 未生成）")

    parts += ["", "## 血缘图", ""]
    if lineage_mermaid.strip():
        parts += ["```mermaid", lineage_mermaid.rstrip(), "```"]
    else:
        parts.append("（血缘分析未完成）")

    parts += [
        "",
        "## 待确认问题清单",
        "",
        open_questions.strip() or _INSIGHT_FALLBACK_Q,
        "",
    ]
    return "\n".join(parts)


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
            "## 五、待确认事项（审查）",
            "",
        ]
    )
    # PERF-11 收尾：审查 [Confirm] 项（需人工确认的口径/依赖问题，不进修复循环）
    # 此前止步于 state 键——交付总报告是验收人真正读的汇总，待确认清单应直达
    confirmations = state.get("review_confirmations") or []
    if confirmations:
        lines.append(
            f"> 代码审查发现 **{len(confirmations)} 项**需业务方/上游确认的口径与依赖问题"
            f"（Confirm 级），确认前不建议上线。详见"
            f" [Phase5-{req_name}_审查报告.md](Phase5-{req_name}_审查报告.md)。"
        )
        lines.append("")
        for i, c in enumerate(confirmations, 1):
            lines.append(f"{i}. {c.get('message', '')}")
        lines.append("")
    else:
        lines.append("无待确认事项。")
    lines.extend(
        [
            "---",
            "",
            "## 六、交付物清单",
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
        lines.extend(["---", "", "## 七、执行错误", ""])
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
        # P2-2 输入瘦身：review_result 不进 prompt——回跳审查时它是上一轮的
        # 过期结果（投机启动点在审查入口），且 review 派生的待确认事项已由
        # Design.md 待确认问题清单（doc_gen 洞察章）承载
        prompt = Template(content).safe_substitute(
            requirement_name=req_name,
            requirement=state.get("requirement", "")[:3000],
            design_scheme=state.get("design_scheme", "")[:3000],
            ddl_content=state.get("ddl_content", "")[:2000],
            sql_content=state.get("sql_content", "")[:5000],
            domain_context=state.get("domain_context", "")[:2000],
            table_schemas=_format_table_schemas(state.get("table_schemas", {})),
        )

        knowledge_doc = call_llm(state, "knowledge_extract", prompt)

        if not knowledge_doc or len(knowledge_doc.strip()) < 100:
            logger.warning(
                "知识提取 LLM 返回过短（%d 字符），使用 fallback", len(knowledge_doc or "")
            )
            return _generate_knowledge_doc_fallback(state)

        # P0-2: 5 章结构契约——缺章 1 次定向重生成，仍缺横幅降级。
        # 线程内不碰 state，errors 由 node_report 扫描横幅统一记录。
        kn_missing = validate_structure("Phase6-知识沉淀.md", knowledge_doc)
        if kn_missing:
            logger.warning("知识沉淀结构缺章: %s，定向重生成 1 次", "、".join(kn_missing))
            retry_prompt = build_retry_prompt(prompt, "Phase6-知识沉淀.md", kn_missing)
            knowledge_doc, kn_missing = ensure_structure(
                "Phase6-知识沉淀.md",
                call_llm(state, "knowledge_extract", retry_prompt),
            )

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
