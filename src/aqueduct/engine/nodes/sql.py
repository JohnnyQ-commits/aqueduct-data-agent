"""Phase 4: SQL 开发节点。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..state import WorkflowState
from .helpers import (
    build_sql_fix_prompt,
    call_llm,
    extract_sql_block,
    is_valid_sql,
    save_artifact,
)

logger = logging.getLogger(__name__)

# 血缘 LLM 输出中的 mermaid 围栏（wait_for_lineage 提取落 state 用）
_RE_MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def node_sql(state: WorkflowState) -> WorkflowState:
    """Phase 4: SQL 开发节点。

    调用 SQLDevelopSkill 生成 prompt -> LLM 生成 SQL -> 自动校验。
    支持外部 SQL 输入（state["external_sql_path"]），跳过 LLM 生成。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=4] SQL 开发开始", req_name)

    try:
        external_sql_path = state.get("external_sql_path")
        if external_sql_path:
            # 外部 SQL 输入：直接读取文件，跳过 LLM 生成
            ext_path = Path(external_sql_path)
            if not ext_path.exists():
                state.setdefault("errors", []).append(
                    f"[终止] 外部 SQL 文件不存在: {external_sql_path}"
                )
                return state
            sql_content = ext_path.read_text(encoding="utf-8")
            logger.info(
                "[task=%s] 使用外部 SQL: file=%s, size=%d 字符",
                req_name,
                external_sql_path,
                len(sql_content),
            )
        else:
            # 原有流程：通过 LLM 生成 SQL
            skill = get_skill("sql_develop")
            context = SkillContext(
                input={
                    "requirement_doc": state.get("requirement", ""),
                    "requirement_summary": state.get("requirement_summary", ""),
                    "ddl_content": state.get("ddl_content", ""),
                    "design_scheme": state.get("design_scheme", ""),
                    "domain_context": state.get("domain_context", ""),
                    # 真实源表结构（MCP 查询，Phase 1 写入 state）——
                    # SQL 字段引用以此为准，不再依赖摘要转述
                    "table_schemas": state.get("table_schemas", {}),
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

        # SQL 有效性校验（防止 LLM 超时/异常产出垃圾内容）
        if not is_valid_sql(sql_content):
            error_msg = (
                f"[终止] Phase 4 SQL 生成失败：LLM 输出不包含有效 SQL"
                f"（内容前 100 字符: {sql_content[:100]!r}）"
            )
            state.setdefault("errors", []).append(error_msg)
            logger.error(
                "[task=%s, phase=4] SQL 开发失败: LLM 输出无效（%d 字符，非 SQL）",
                req_name,
                len(sql_content),
            )
            return state

        req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
        sql_path = save_artifact(state, f"Phase4-{req_name}.sql", sql_content)
        state["sql_content"] = sql_content
        state["sql_file"] = sql_path
        state["metadata"] = {**(state.get("metadata", {})), "sql_done": "true"}

        # 自动运行 SQL 校验
        _auto_validate(state, sql_path)
        # 生成自检（TODO-8）：linter ERROR 就地自修正——必须先于试跑/血缘/成本，
        # 后续工具与审查均基于修复后的 SQL
        _self_check_fix(state)
        # 试跑验证：LIMIT 10 提前发现语法错误和字段不对齐
        _auto_trial_run(state, sql_path)
        # 自动运行血缘解析（异步，不阻塞 review）
        _auto_lineage_async(state, sql_path)
        # 自动运行成本预估
        _auto_cost_estimate(state, sql_path)

        elapsed = time.time() - start
        logger.info(
            "[task=%s, phase=4] SQL 开发完成: sql=%d 字符, 产出=%s, 耗时=%.1fs",
            req_name,
            len(sql_content),
            sql_path,
            elapsed,
        )
    except Exception as e:
        elapsed = time.time() - start
        state.setdefault("errors", []).append(f"SQL 开发异常: {e!s}")
        logger.error(
            "[task=%s, phase=4] SQL 开发异常: %s, 耗时=%.1fs",
            req_name,
            e,
            elapsed,
            exc_info=True,
        )

    return state


def _resolve_sql_path(state: WorkflowState, rel_path: str) -> Path:
    """将 save_artifact 返回的相对路径解析为绝对路径。"""
    from ...config.settings import get_settings

    p = Path(rel_path)
    if p.is_absolute():
        return p
    return get_settings().project_root / rel_path


def _self_check_fix(state: WorkflowState) -> None:
    """生成自检（TODO-8）：linter ERROR 在 Phase 4 内就地自修正。

    修复前移：linter Critical 留到审查侧要走 review(~13min) → sql_fix →
    re-review(~13min) 的完整回环，而违规本身是本地零 token 可判定的——
    生成端就地修复只花一次 sql_fix。
    守护：仅 ERROR 级触发（与 review 侧 ERROR→Critical 映射同口径）；
    修复必须让 ERROR 数严格下降才接受，否则回退原 SQL 并还原校验结果；
    轮数上限 AQUEDUCT_SQL_SELF_FIX_ROUNDS（默认 1，0=关闭）；LLM 失败/
    输出无效均保持原状——审查侧 P1-2 现场复检门禁照常兜底，语义不变。
    """
    from ...config.settings import get_settings

    rounds_left = get_settings().sql_self_fix_rounds
    if rounds_left < 1:
        return

    def _error_issues() -> list[dict]:
        return [
            i
            for i in (state.get("validation_result") or {}).get("issues", [])
            if i.get("level") == "ERROR"
        ]

    req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
    canonical = state.get("sql_file") or ""
    round_no = 0

    while rounds_left > 0 and (errors := _error_issues()):
        sql_content = state.get("sql_content", "")
        issues_formatted = "\n".join(
            f"{n}. [Critical] {i.get('message', '')} (line {i.get('line') or '?'})"
            for n, i in enumerate(errors, 1)
        )
        round_no += 1
        logger.info(
            "[task=%s] 生成自检: linter %d 个 ERROR，就地自修正（第 %d 轮）",
            req_name,
            len(errors),
            round_no,
        )

        try:
            fix_response = call_llm(
                state, "sql_fix", build_sql_fix_prompt(sql_content, issues_formatted)
            )
        except Exception as e:
            logger.warning(
                "[task=%s] 生成自检: LLM 调用失败，保持原 SQL 交审查侧兜底: %s", req_name, e
            )
            return

        fixed_sql = extract_sql_block(fix_response)
        if not is_valid_sql(fixed_sql):
            logger.warning(
                "[task=%s] 生成自检: 修复输出无效（%d 字符），保持原 SQL",
                req_name,
                len(fixed_sql),
            )
            return

        # 试接受：审计副本 + 规范文件回写（Phase4-*.sql 是交付物本体）+ 复检
        save_artifact(state, f"Phase4-{req_name}_selffix{round_no}.sql", fixed_sql)
        state["sql_content"] = fixed_sql
        if canonical:
            try:
                _resolve_sql_path(state, canonical).write_text(fixed_sql, encoding="utf-8")
            except Exception:
                logger.warning(
                    "[task=%s] 生成自检: 规范文件回写失败（审计副本已保存）",
                    req_name,
                    exc_info=True,
                )
        _auto_validate(state, canonical)

        if len(_error_issues()) >= len(errors):
            # 修复未改善（甚至更差）→ 回退原 SQL 并还原校验结果，交审查侧兜底
            logger.warning(
                "[task=%s] 生成自检: 修复未让 ERROR 下降（%d → %d），回退原 SQL",
                req_name,
                len(errors),
                len(_error_issues()),
            )
            state["sql_content"] = sql_content
            if canonical:
                try:
                    _resolve_sql_path(state, canonical).write_text(sql_content, encoding="utf-8")
                except Exception:
                    logger.warning("[task=%s] 生成自检: 原SQL回写失败", req_name, exc_info=True)
            _auto_validate(state, canonical)
            return

        logger.info(
            "[task=%s] 生成自检: 第 %d 轮修复生效（%d → %d ERROR）",
            req_name,
            round_no,
            len(errors),
            len(_error_issues()),
        )
        rounds_left -= 1


def _auto_validate(state: WorkflowState, sql_path: str) -> None:
    """自动运行 SQL 校验并生成报告。"""
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        sql_content = abs_path.read_text(encoding="utf-8")

        # 输入有效性预检
        if len(sql_content) < 50 or not is_valid_sql(sql_content):
            logger.warning(
                "校验跳过: 文件内容非有效 SQL（%d 字符）",
                len(sql_content),
            )
            return

        validator = get_tool("validator")
        validation_result = validator.execute(sql_file=str(abs_path))

        if validation_result.data is None:
            logger.warning("SQL 校验返回空结果: %s", validation_result.error)
            state["validation_result"] = {}
            return

        state["validation_result"] = validation_result.data

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
        save_artifact(state, "Phase4-SQL校验报告.md", "\n".join(report_lines))
        logger.info(
            "SQL 校验完成: %d errors, %d warnings",
            vr.get("error_count", 0),
            vr.get("warn_count", 0),
        )
    except Exception:
        logger.warning("SQL 校验失败，跳过", exc_info=True)


def _run_trial_selects(sql_content: str) -> dict:
    """对 SQL 中的 SELECT 查询体执行 LIMIT 10 试跑（P1-2 门禁核心）。

    ETL 形态（INSERT OVERWRITE ... SELECT）剥掉 INSERT 头取查询体试跑，
    实际执行比静态检查更能提前发现语法错误和字段不对齐。
    executor 通过函数内 import 获取（patch 点统一 tools.registry.get_tool）。

    Returns:
        {"total": 可试跑语句数, "tested": 实际试跑数, "passed": 通过数, "errors": [错误信息]}
    """
    from ...tools.registry import get_tool

    select_stmts = _extract_select_statements(sql_content)
    if not select_stmts:
        return {"total": 0, "tested": 0, "passed": 0, "errors": []}

    executor = get_tool("executor")
    trial_results: list[dict] = []
    errors: list[str] = []

    for i, stmt in enumerate(select_stmts[:3]):  # 最多试跑 3 条 SELECT
        limited_stmt = stmt.rstrip().rstrip(";")
        if not re.search(r"\blimit\b", limited_stmt, re.IGNORECASE):
            limited_stmt += "\nLIMIT 10"

        result = executor.execute(action="execute", sql=limited_stmt)
        trial_results.append(
            {
                "index": i,
                "success": result.success,
                "error": result.error if not result.success else None,
            }
        )
        if not result.success:
            errors.append(f"SELECT #{i + 1}: {result.error}")

    return {
        "total": len(select_stmts),
        "tested": len(trial_results),
        "passed": len(trial_results) - len(errors),
        "errors": errors,
    }


def _auto_trial_run(state: WorkflowState, sql_path: str) -> None:
    """试跑验证：对生成的 SQL 执行 LIMIT 10 试跑，提前发现语法错误。

    仅在 execution_enabled=True 时执行。失败不阻塞管道，仅记录警告
    （P1-2 强制门禁在 review 侧注入 Critical issues 触发修复循环）。
    借鉴 ai-sql-generate 的 Stage 3 预验证思路：实际执行比静态检查更能发现问题。
    """
    try:
        from ...config.settings import get_settings

        settings = get_settings()
        if not settings.execution_enabled:
            logger.debug("试跑跳过: execution_enabled=False")
            return

        abs_path = _resolve_sql_path(state, sql_path)
        sql_content = abs_path.read_text(encoding="utf-8")

        if len(sql_content) < 50 or not is_valid_sql(sql_content):
            logger.warning("试跑跳过: 文件内容非有效 SQL（%d 字符）", len(sql_content))
            return

        trial = _run_trial_selects(sql_content)
        if trial["total"] == 0:
            logger.info("试跑跳过: 未找到可试跑的 SELECT 语句")
            return

        # 记录结果
        state["trial_run_result"] = trial

        if trial["errors"]:
            logger.warning(
                "试跑发现 %d 个错误: %s", len(trial["errors"]), "; ".join(trial["errors"])
            )
        else:
            logger.info("试跑通过: %d 条 SELECT 语句均成功", trial["tested"])

        # 保存试跑报告
        report_lines = [
            "# SQL 试跑报告",
            "",
            f"- **测试语句数**: {trial['tested']}/{trial['total']}",
            f"- **通过**: {trial['passed']}",
            f"- **失败**: {len(trial['errors'])}",
            "",
        ]
        for i in range(trial["tested"]):
            status = "✅" if i < trial["passed"] else "❌"
            line = f"- {status} SELECT #{i + 1}"
            if i >= trial["passed"] and i < trial["passed"] + len(trial["errors"]):
                line += f" — `{trial['errors'][i - trial['passed']][:100]}`"
            report_lines.append(line)

        save_artifact(state, "Phase4-试跑报告.md", "\n".join(report_lines))

    except Exception:
        logger.warning("试跑验证失败，跳过", exc_info=True)


def _extract_select_statements(sql_content: str) -> list[str]:
    """从 SQL 内容中提取可试跑的 SELECT 查询体。

    P1-2: ETL SQL 是 `INSERT [OVERWRITE] TABLE ... [PARTITION(...)] SELECT`
    单语句形态，按分号拆分后以 INSERT 开头——剥掉 INSERT 头取 SELECT
    查询体（行首 select/with 定位，避开字符串字面量），否则试跑对真实
    ETL SQL 100% 跳过。纯 INSERT VALUES（无查询体）不产出。
    """
    # 去掉注释
    cleaned = re.sub(r"--.*$", "", sql_content, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    # 按分号分割
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]

    selects = []
    for stmt in statements:
        upper = stmt.lstrip().upper()
        if upper.startswith("SELECT") or upper.startswith("WITH"):
            selects.append(stmt)
        elif upper.startswith("INSERT"):
            # 剥 INSERT 头：定位首个词边界 select/with——兼容单行
            # `insert into t select ...` 与跨行 `... partition (...) \n select`。
            # 头部含 values（INSERT VALUES）不产出：防 `'select'`/`'with'`
            # 字符串字面量被误剥成垃圾语句试跑（零误报原则）
            m = re.search(r"\b(select|with)\b", stmt, re.IGNORECASE)
            if m and not re.search(r"\bvalues\b", stmt[: m.start()], re.IGNORECASE):
                selects.append(stmt[m.start() :])

    return selects


def _auto_lineage_async(state: WorkflowState, sql_path: str) -> None:
    """用 LLM 生成字段级血缘图（异步版本，OPT-5）。

    在后台线程中执行 LLM 调用，不阻塞后续 review 阶段。
    Future 存储在 state["_lineage_future"] 中，
    在 node_report 开始前通过 wait_for_lineage() 等待完成。
    """
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        sql_content = abs_path.read_text(encoding="utf-8")

        # 输入有效性预检
        if len(sql_content) < 50 or not is_valid_sql(sql_content):
            logger.warning(
                "血缘跳过: 文件内容非有效 SQL（%d 字符）",
                len(sql_content),
            )
            return

        # 加载 prompt 模板
        from ...config.settings import get_settings

        settings = get_settings()
        tpl_path = settings.prompt_dir / "lineage.tpl.md"
        if not tpl_path.exists():
            logger.warning("lineage.tpl.md 不存在，跳过血缘生成")
            return

        prompt = tpl_path.read_text(encoding="utf-8")
        prompt = prompt.replace("{sql_content}", sql_content)

        # 在后台线程中执行 LLM 调用
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_run_lineage_llm, state, prompt)
        state["_lineage_future"] = future
        state["_lineage_executor"] = executor
        logger.info("血缘 LLM 调用已在后台启动（不阻塞 review）")
    except Exception:
        logger.warning("LLM 血缘生成启动失败，跳过", exc_info=True)


def _run_lineage_llm(state: WorkflowState, prompt: str) -> str:
    """在线程中执行的 LLM 血缘生成。"""
    result = call_llm(state, "lineage", prompt)
    save_artifact(state, "Phase4-字段级血缘图.md", result)
    logger.info("LLM 血缘生成完成: %d 字符", len(result))
    return result


def _extract_mermaid(text: str) -> str:
    """从血缘 LLM 输出中提取首个 mermaid 块内容（无围栏；无块返回空串）。"""
    if not text:
        return ""
    m = _RE_MERMAID_FENCE.search(text)
    return m.group(1).strip() if m else ""


def wait_for_lineage(state: WorkflowState) -> None:
    """等待后台血缘 LLM 调用完成（OPT-5）。

    在需要血缘产出物的节点（如 report）前调用。
    LLM 输出中的 mermaid 块提取后落入 state["lineage_result"]["mermaid"]——
    PERF-4 Design.md 本地拼装取此处，不再经 doc_gen 转写（此前返回值被
    丢弃，lineage_result 从未落 state，血缘图章只能渲染未完成注记）。
    """
    future: Future | None = state.get("_lineage_future")
    executor: ThreadPoolExecutor | None = state.get("_lineage_executor")

    if future is None:
        return

    try:
        result = future.result(timeout=300)  # 最多等待 5 分钟
        mermaid = _extract_mermaid(result)
        if mermaid:
            state["lineage_result"] = {
                **(state.get("lineage_result") or {}),
                "mermaid": mermaid,
            }
        logger.info("后台血缘生成已完成")
    except Exception:
        logger.warning("后台血缘生成异常，不阻塞流程", exc_info=True)
    finally:
        # 清理线程资源
        state.pop("_lineage_future", None)
        if executor:
            executor.shutdown(wait=False)
            state.pop("_lineage_executor", None)


def _auto_cost_estimate(state: WorkflowState, sql_path: str) -> None:
    """自动运行成本预估。"""
    try:
        abs_path = _resolve_sql_path(state, sql_path)
        sql_content = abs_path.read_text(encoding="utf-8")

        # 输入有效性预检
        if len(sql_content) < 50 or not is_valid_sql(sql_content):
            logger.warning(
                "成本预估跳过: 文件内容非有效 SQL（%d 字符）",
                len(sql_content),
            )
            return

        cost_tool = get_tool("estimator")
        cost_result = cost_tool.execute(sql_file=str(abs_path))

        if cost_result.data is None:
            logger.warning("成本预估返回空结果: %s", cost_result.error)
            state["cost_result"] = {}
            return

        state["cost_result"] = cost_result.data

        report_md = cost_result.data.get("report", "")
        if report_md:
            save_artifact(state, "Phase4-成本预警.md", report_md)
        logger.info("成本预估完成")
    except Exception:
        logger.warning("成本预估失败，跳过", exc_info=True)
