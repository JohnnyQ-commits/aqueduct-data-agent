"""Phase 4: SQL 开发节点。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ...tools.registry import get_tool
from ..state import WorkflowState
from .helpers import (
    RE_PATCH_BLOCK,
    apply_sql_patches,
    build_sql_fix_prompt,
    call_llm,
    extract_sql_block,
    is_valid_sql,
    save_artifact,
)

logger = logging.getLogger(__name__)

# 血缘 LLM 输出中的 mermaid 围栏（wait_for_lineage 提取落 state 用）
_RE_MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)

# 第十六刀（run 22 实录）：平台分区 hook 的编译期拒绝——
# "PartitionOverLimitException(can only query max 400 partition,...)"
_RE_PARTITION_OVER_LIMIT = re.compile(
    r"PartitionOverLimit|max\s+\d+\s+partitions?\b", re.IGNORECASE
)


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

            # 第二十一刀配套 P1(b)（run 27 实录）：网关截断等产出无效 SQL
            # 时以同一 prompt 全新重生成一次（不追加警示——第十五刀极简
            # 重试纪律：换形态优于补丁）；两次皆无效才走下方失败路径
            if not is_valid_sql(sql_content):
                logger.warning(
                    "[task=%s] sql_gen 输出无效（%d 字符，非 SQL），重试 1/1",
                    req_name,
                    len(sql_content),
                )
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

        # 第二十刀（run 29 实录）：生成端幻觉表静态拦截——清单外表在进
        # 试跑前先定向重生成一轮，省掉一次注定失败的平台试跑
        hallucinated = _hallucinated_tables(sql_content, state.get("table_schemas") or {})
        if hallucinated:
            sql_content = _hallucination_regenerate(state, sql_content, hallucinated) or sql_content

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
    轮数上限 AQUEDUCT_SQL_SELF_FIX_ROUNDS（默认 1，0=关闭）——失败轮消耗
    预算继续重试（第十五刀：run 20 实录 1 次失败烧光预算，旁路下无兜底）；
    LLM 失败/输出无效均保持原状——审查侧 P1-2 现场复检门禁照常兜底，语义不变。
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

        # 第十一刀 11a（run 13 实录）：响应含补丁块 → 逐块应用到原 SQL
        # （自检与修复环共用同一模板，LLM 遵契约输出补丁块，旧 extract 路径
        # 判"输出无效"——自检层报废）；失配保持原 SQL 交审查侧兜底。
        if RE_PATCH_BLOCK.search(fix_response):
            patched = apply_sql_patches(sql_content, fix_response)
            if patched is None:
                logger.warning(
                    "[task=%s] 生成自检: 补丁块应用失败（SEARCH 未恰好命中 1 次），保持原 SQL",
                    req_name,
                )
                return
            fixed_sql = patched
        else:
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
            # 修复未改善（甚至更差）→ 回退原 SQL 并还原校验结果。
            # 第十五刀（run 20 实录）：失败轮消耗 1 轮预算继续循环——原实现
            # 直接 return，1 次失败尝试烧光全部预算（AQUEDUCT_SQL_SELF_FIX_ROUNDS=3
            # 的轮 2/3 从未执行）。旁路模式（review_enforce=False）下无 4.5 兜底，
            # 重试是唯一自救；状态已还原重试安全，振荡由接受门禁（严格下降）
            # + rounds_left 上限兜底。LLM 异常/输出无效路径保持 return 不变
            # （网关故障重试无意义 / 输出形态问题同 prompt 大概率复现）。
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
            rounds_left -= 1
            continue

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


# 第八刀 8a（run 10 实录）：平台 "Table not found 'X'" 中的表名
_RE_TABLE_NOT_FOUND = re.compile(r"table not found '([\w.]+)'", re.IGNORECASE)

# 网络瞬时错误（2026-10-15 实录）：平台不可达（WinError 10060 类）不是 SQL
# 缺陷——小步重试后仍不通的单列 env_errors，与 6b 超时/8a 承接表同构改判。
# 判据与间隔收敛在 helpers（call_llm 同款），此处别名复用避免两处漂移。
from .helpers import NETWORK_RETRY_DELAYS as _TRIAL_RETRY_DELAYS  # noqa: E402
from .helpers import RE_NETWORK_TRANSIENT as _RE_NETWORK_TRANSIENT  # noqa: E402

# 调度参数哨兵（2026-10-16 UI 实录）：${param} 占位在试跑通道必然
# ParseException（参数未绑定），该 Critical 修复环修不掉——试跑前替换为
# 哨兵串让语法/字段验证照常进行；枚举回填属验收标准4 过渡态，
# 由审查 Confirm 项跟踪，不再让试跑门禁死锁在参数占位上。
# 两段式替换（run 2 实录 18:35 halt）：占位符在 SQL 中常已带引号
# （'${bizdate}'），替换值再包引号会产出 ''...'' 非法字面量——
# 先替换带引号形态，剩余裸占位（IN 列表等）再补引号。
_RE_SCHED_PARAM_QUOTED = re.compile(r"'\$\{(\w+)\}'")
_RE_SCHED_PARAM = re.compile(r"\$\{(\w+)\}")
_TRIAL_PARAM_SENTINEL = "__AQ_TRIAL_SENTINEL__"

_RE_TABLE_REF = re.compile(r"\b(?:from|join)\s+([\w]+(?:\.[\w]+)?)", re.IGNORECASE)
_RE_CTE_ALIAS = re.compile(r"(?:\bwith\b|,|\))\s*(\w+)\s+as\s*\(", re.IGNORECASE)
_RE_CREATE_TARGET = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.]+)", re.IGNORECASE)
_RE_INSERT_TARGET = re.compile(
    r"insert\s+(?:overwrite|into)\s+(?:table\s+)?([\w.]+)", re.IGNORECASE
)


def _script_created_tables(sql_masked: str) -> set[str]:
    """脚本自建表集合（CTAS / insert 目标，库限定名与裸表名都收）。

    承接表策略下，SELECT 引用的 tmp 表由本脚本前置 drop+create...as select
    创建——试跑单句执行时它们天然不存在，属结构性限制不是 SQL 缺陷。
    """
    names: set[str] = set()
    for pattern in (_RE_CREATE_TARGET, _RE_INSERT_TARGET):
        for m in pattern.finditer(sql_masked):
            lowered = m.group(1).lower()
            names.add(lowered)
            names.add(lowered.split(".")[-1])
    return names


def _execute_trial_stmt(executor: Any, stmt: str) -> Any:
    """试跑引擎链 spark→hive（2026-10-20 用户拍板：生产常态 spark，大数据量
    才 hive——试跑跟生产引擎走；presto 不进试跑链）。

    spark 被拒先转换重试（hive→spark 方言，见 utils/sql_engine）再降 hive；
    超时立即中止整条链——hive 只会更慢。链尾（hive）错误作为最终结论。
    """
    from ...utils.sql_engine import TRIAL_CHAIN, is_timeout_error, iter_engine_attempts

    result = None
    for engine, attempt_sql in iter_engine_attempts(stmt, chain=TRIAL_CHAIN):
        result = executor.execute(action="execute", sql=attempt_sql, engine=engine)
        if getattr(result, "success", False) or is_timeout_error(str(getattr(result, "error", ""))):
            return result
    return result


def _run_trial_selects(sql_content: str) -> dict:
    """对 SQL 中的 SELECT 查询体执行 LIMIT 10 试跑（P1-2 门禁核心）。

    ETL 形态（INSERT OVERWRITE ... SELECT）剥掉 INSERT 头取查询体试跑，
    实际执行比静态检查更能提前发现语法错误和字段不对齐。
    executor 通过函数内 import 获取（patch 点统一 tools.registry.get_tool）。

    Returns:
        {"total": 可试跑语句数, "tested": 实际试跑数, "passed": 通过数,
         "errors": [硬失败], "timeouts": [超时], "blocked": [承接表依赖改判],
         "env_errors": [网络瞬时错误重试后仍不通——平台不可达，SQL 未被验证]}
    """
    from ...tools.registry import get_tool

    select_stmts = _extract_select_statements(sql_content)
    if not select_stmts:
        return {
            "total": 0,
            "tested": 0,
            "passed": 0,
            "errors": [],
            "timeouts": [],
            "blocked": [],
            "env_errors": [],
        }

    executor = get_tool("executor")
    trial_results: list[dict] = []
    errors: list[str] = []
    timeouts: list[str] = []
    env_errors: list[str] = []
    # 第八刀 8a（run 10 实录）：脚本自建承接表的 Table not found 单列 blocked——
    # 试跑只单句执行 SELECT，承接表由前置 CTAS 创建，单句试跑结构性验证不了；
    # 该 Critical 修复环修不掉（run 10 两轮空转 halt），与 6b 超时同构改判。
    created = _script_created_tables(re.sub(r"--.*$", "", sql_content, flags=re.MULTILINE))
    blocked: list[str] = []

    # 第六刀 6b（run 8 实录）：超时单列——平台已受理并执行（语法/字段无错），
    # 只是慢（大表全分区扫描在 5min 门禁阈值边缘抖动，复跑即过）。
    # 超时计入 passed（记分卡真实试跑不因平台负载误判），由审查门禁
    # 降级 Confirm 慢查询标注；表不存在/字段不对齐等硬失败仍进 errors。
    for i, stmt in enumerate(select_stmts[:3]):  # 最多试跑 3 条 SELECT
        limited_stmt = stmt.rstrip().rstrip(";")
        # 调度参数占位替换为哨兵（否则 ${...} 必然 ParseException，修复环修不掉）
        if "${" in limited_stmt:
            limited_stmt = _RE_SCHED_PARAM_QUOTED.sub(f"'{_TRIAL_PARAM_SENTINEL}'", limited_stmt)
            limited_stmt = _RE_SCHED_PARAM.sub(f"'{_TRIAL_PARAM_SENTINEL}'", limited_stmt)
            logger.info("试跑语句含调度参数占位，已替换为哨兵串 %s", _TRIAL_PARAM_SENTINEL)
        if not re.search(r"\blimit\b", limited_stmt, re.IGNORECASE):
            limited_stmt += "\nLIMIT 10"

        result = _execute_trial_stmt(executor, limited_stmt)
        # 网络瞬时错误小步重试（2026-10-15 实录：真实需求跑遇 WinError 10060
        # 单发直接落 errors 报 0/1，SQL 未被验证）——平台不可达不是 SQL 缺陷，
        # 重试仍不通的单列 env_errors，与 6b 超时/8a 承接表同构改判。
        retries = 0
        while (
            not result.success
            and retries < len(_TRIAL_RETRY_DELAYS)
            and _RE_NETWORK_TRANSIENT.search(str(result.error))
        ):
            time.sleep(_TRIAL_RETRY_DELAYS[retries])
            retries += 1
            result = _execute_trial_stmt(executor, limited_stmt)

        trial_results.append(
            {
                "index": i,
                "success": result.success,
                "error": result.error if not result.success else None,
            }
        )
        if not result.success:
            msg = f"SELECT #{i + 1}: {result.error}"
            if "任务超时" in str(result.error):
                timeouts.append(msg)
            elif _RE_NETWORK_TRANSIENT.search(str(result.error)):
                env_errors.append(msg)
            else:
                m = _RE_TABLE_NOT_FOUND.search(str(result.error))
                if m and m.group(1).lower() in created:
                    blocked.append(msg)
                else:
                    errors.append(msg)

    return {
        "total": len(select_stmts),
        "tested": len(trial_results),
        "passed": len(trial_results) - len(errors) - len(env_errors),
        "errors": errors,
        "timeouts": timeouts,
        "blocked": blocked,
        "env_errors": env_errors,
    }


def _auto_trial_run(state: WorkflowState, sql_path: str) -> None:
    """试跑验证：对生成的 SQL 执行 LIMIT 10 试跑，提前发现语法错误。

    仅在平台声明 sql_execute 能力时执行（auto 语义=execution_enabled=True，
    platform=none 强制离线时同样走此处跳过）。失败不阻塞管道，仅记录警告
    （P1-2 强制门禁在 review 侧注入 Critical issues 触发修复循环）。
    借鉴 ai-sql-generate 的 Stage 3 预验证思路：实际执行比静态检查更能发现问题。
    """
    try:
        from ...platform import Capability, get_platform_adapter

        if not get_platform_adapter().has_capability(Capability.SQL_EXECUTE):
            logger.debug("试跑跳过: 平台未声明 sql_execute 能力")
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

        # 第十六/十七刀（run 22/23 实录）：分区超限与幻觉表是试跑可自愈的
        # 硬失败——定向自修一轮，部分改善经分桶门禁接受后以复跑结果替代
        if trial["errors"] and any(
            _RE_PARTITION_OVER_LIMIT.search(e) or _RE_TABLE_NOT_FOUND.search(e)
            for e in trial["errors"]
        ):
            fixed_trial = _trial_self_fix(state, trial)
            if fixed_trial is not None:
                trial = fixed_trial
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
        if trial.get("timeouts"):
            # 第六刀 6b：超时是慢查询标注不是失败（SQL 语义有效，已计入通过）
            report_lines.append(f"- **超时(慢查询标注)**: {len(trial['timeouts'])}")
            report_lines.extend(f"  - ⏱ {t[:100]}" for t in trial["timeouts"])
            report_lines.append("")
        if trial.get("blocked"):
            # 第八刀 8a：承接表依赖改判（单句试跑结构性验证不了，人工跑全脚本核验）
            report_lines.append(f"- **承接表依赖(改判待人工核验)**: {len(trial['blocked'])}")
            report_lines.extend(f"  - ⛔ {b[:100]}" for b in trial["blocked"])
            report_lines.append("")
        if trial.get("env_errors"):
            # 网络瞬时错误重试仍不通：平台不可达，SQL 未被验证（非 SQL 缺陷）
            report_lines.append(f"- **环境失败(平台不可达,已重试)**: {len(trial['env_errors'])}")
            report_lines.extend(f"  - ⚠️ {e[:100]}" for e in trial["env_errors"])
            report_lines.append("")
        # 逐语句状态按桶判定（索引取自 "SELECT #n" 前缀，不再依赖 passed 算术）
        err_idx = {int(m.group(1)) for e in trial["errors"] if (m := re.match(r"SELECT #(\d+)", e))}
        env_idx = {
            int(m.group(1))
            for e in trial.get("env_errors", [])
            if (m := re.match(r"SELECT #(\d+)", e))
        }
        for i in range(trial["tested"]):
            n = i + 1
            if n in err_idx:
                detail = next(e for e in trial["errors"] if e.startswith(f"SELECT #{n}"))
                line = f"- ❌ SELECT #{n} — `{detail[:100]}`"
            elif n in env_idx:
                detail = next(e for e in trial["env_errors"] if e.startswith(f"SELECT #{n}"))
                line = f"- ⚠️ SELECT #{n} — `{detail[:100]}`"
            else:
                line = f"- ✅ SELECT #{n}"
            report_lines.append(line)

        save_artifact(state, "Phase4-试跑报告.md", "\n".join(report_lines))

    except Exception:
        logger.warning("试跑验证失败，跳过", exc_info=True)


def _classify_trial_errors(errors: list[str]) -> dict[str, list[str]]:
    """试跑错误分桶（第十七刀）：partition / table / other。

    接受门禁按类别判定部分改善——run 23 实录：全有全无门禁否决了
    分区已修好、仅残留其他类错误的 SQL。分区类先判（错误串可能同时
    提及表名），幻觉表类次之，其余归 other。
    """
    cats: dict[str, list[str]] = {"partition": [], "table": [], "other": []}
    for e in errors:
        if _RE_PARTITION_OVER_LIMIT.search(e):
            cats["partition"].append(e)
        elif _RE_TABLE_NOT_FOUND.search(e):
            cats["table"].append(e)
        else:
            cats["other"].append(e)
    return cats


def _unfiltered_tables(sql_content: str) -> list[str]:
    """静态找出未带 inc_day 过滤的表引用（第十七刀(c) 构造性锚点）。

    run 23 实录：自修 prompt 只给抽象规则，模型不知道「哪张表缺过滤」，
    修复输出原样保留漏网之鱼。启发式：对每个 `from/join 库.表` 引用取
    它到下一个引用之间的窗口，窗口内无 inc_day 即嫌疑（注释已剥离，
    派生表 `from (select` 不匹配带点表名，天然跳过）。
    """
    cleaned = re.sub(r"--.*$", "", sql_content, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    refs = list(re.finditer(r"\b(?:from|join)\s+([\w]+\.[\w]+)", cleaned, re.IGNORECASE))
    suspects: list[str] = []
    for i, m in enumerate(refs):
        end = refs[i + 1].start() if i + 1 < len(refs) else len(cleaned)
        if not re.search(r"\binc_day\b", cleaned[m.end() : end], re.IGNORECASE):
            name = m.group(1).lower()
            if name not in suspects:
                suspects.append(name)
    return suspects


def _hallucinated_tables(sql_content: str, table_schemas: dict) -> list[str]:
    """生成端幻觉表静态拦截（第二十刀，run 29 实录）。

    幻觉表 5/8 run 复发，模板禁止清单外表规则被 LLM 方差击穿。在试跑前
    静态比对 FROM/JOIN 表引用与 table_schemas 在册清单（大小写不敏感，
    库限定名与裸表名互认），扣除脚本自建表（CTAS/insert 目标——多层合并
    下 SELECT 可合法引用本脚本前段产出的 DWS 中间表）与 CTE 别名；空清单
    （元数据未获取）直接放行防误杀。漏网者由试跑 Table not found 自修
    （第十七刀）兜底。
    """
    if not table_schemas:
        return []
    cleaned = re.sub(r"--.*$", "", sql_content, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    cte = {m.group(1).lower() for m in _RE_CTE_ALIAS.finditer(cleaned)}
    known = _script_created_tables(cleaned)
    for key in table_schemas:
        lowered = key.lower()
        known.add(lowered)
        known.add(lowered.split(".")[-1])
    suspects: list[str] = []
    for m in _RE_TABLE_REF.finditer(cleaned):
        name = m.group(1).lower()
        if name in known or name in cte or name in suspects:
            continue
        suspects.append(name)
    return suspects


def _hallucination_regenerate(
    state: WorkflowState, sql_content: str, hallucinated: list[str]
) -> str | None:
    """生成端幻觉表定向重生成（第二十刀）——仅一轮，试跑前拦截。

    run 29 实录：幻觉表被试跑自修的换表治好，但代价是先浪费一次平台
    试跑。静态拦截后置前，LLM 换表/删逻辑省一轮试跑。prompt 极简纪律
    （第十五刀）：幻觉表点名 + 真实表清单 + 换表分区要求，不携带
    persona/domain_context。接受门禁：复检清单外表清零才采用（审计副本
    hallucfix 落盘，由调用方走规范保存）；输出无效/未变更/长度腰斩/
    复检仍引用清单外表 → 保持原 SQL 并留痕（hallucfix_reject），由试跑
    Table not found 自修（第十七刀）兜底。

    Returns:
        清零后的修复 SQL；未接受时 None。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
    real_tables = sorted((state.get("table_schemas") or {}).keys())
    real_list = "\n".join(f"- {t}" for t in real_tables) if real_tables else "（未获取）"
    prompt = (
        "以下 Hive SQL 引用了源表清单之外的表（幻觉表，平台编译必失败），"
        "请修复并输出完整 SQL（```sql 代码块，不要输出任何解释或说明）：\n\n"
        f"幻觉表：{'、'.join(hallucinated)}\n\n"
        "以下为本次需求真实可用的源表清单（来自元数据平台，唯一权威），"
        "清单外的表视为不存在：\n"
        f"{real_list}\n\n"
        "要求：把幻觉表替换为清单中最接近的真实表（字段不兼容时同步调整"
        "引用字段），或删除对该表的引用逻辑；换上的表必须带 inc_day 分区"
        "过滤（参照原 SQL 中已有事实表的日期范围）；其余逻辑逐字保留，"
        "不改字段、不改聚合、不改输出列。\n\n"
        f"原 SQL：\n{sql_content}\n"
    )
    logger.info(
        "[task=%s] 生成端幻觉表静态拦截: %s，定向重生成（1 轮）",
        req_name,
        "、".join(hallucinated),
    )
    try:
        fix_response = call_llm(state, "sql_fix", prompt)
    except Exception as e:
        logger.warning("[task=%s] 幻觉表重生成: LLM 调用失败，保持原 SQL: %s", req_name, e)
        return None

    fixed_sql = extract_sql_block(fix_response)
    if not is_valid_sql(fixed_sql) or fixed_sql == sql_content:
        save_artifact(state, f"Phase4-{req_name}_hallucfix_reject.sql", fixed_sql)
        logger.warning(
            "[task=%s] 幻觉表重生成: 修复输出无效或未变更，保持原 SQL"
            "（被拒输出已存 Phase4-%s_hallucfix_reject.sql）",
            req_name,
            req_name,
        )
        return None
    if len(fixed_sql) < len(sql_content) * 0.5:
        # 长度腰斩=丢逻辑嫌疑（run 20 骨架残缺同族），直接拒绝
        save_artifact(state, f"Phase4-{req_name}_hallucfix_reject.sql", fixed_sql)
        logger.warning(
            "[task=%s] 幻觉表重生成: 修复输出过短（%d 字符 < 原 %d 一半），疑似丢逻辑，保持原 SQL",
            req_name,
            len(fixed_sql),
            len(sql_content),
        )
        return None
    still = _hallucinated_tables(fixed_sql, state.get("table_schemas") or {})
    if still:
        save_artifact(state, f"Phase4-{req_name}_hallucfix_reject.sql", fixed_sql)
        logger.warning(
            "[task=%s] 幻觉表重生成: 复检仍引用清单外表（%s），保持原 SQL；"
            "被拒修复已存 Phase4-%s_hallucfix_reject.sql，试跑自修兜底",
            req_name,
            "、".join(still),
            req_name,
        )
        return None
    save_artifact(state, f"Phase4-{req_name}_hallucfix.sql", fixed_sql)
    logger.info("[task=%s] 幻觉表重生成: 复检清零，采用修复 SQL", req_name)
    return fixed_sql


def _trial_self_fix(state: WorkflowState, trial: dict) -> dict | None:
    """试跑硬失败（分区超限/幻觉表）定向自修——仅一轮（第十六/十七刀）。

    run 22 实录：模型把维表/快照表排除在「源表」外，引用 ods_rmds.tb_res_user
    未带 inc_day 过滤，平台 hook 编译期拒绝全表 1455 分区扫描（LIMIT 挡不住，
    分区裁剪是编译期检查）。run 23 实录：幻觉表（Table not found）复发为周期
    性缺陷，且全有全无接受门禁否决了分区已修好的部分改善。

    修法（极简 prompt，第十五刀纪律，不携带 persona/domain_context）：
    - 分区类：静态点名缺 inc_day 的表引用（构造性锚点）+ 分区硬规则
    - 幻觉表类：注入 table_schemas 真实表清单，强制换表或删逻辑
    复跑接受门禁按类别分桶（第十九刀 run 25 实录修订）：改善与不恶化只看
    使命类别 partition/table（部分改善可接受）；other 新增仅在使命类别
    双清零时豁免（残留 other 交审查门禁），mission 未清时 other 新增仍否决；
    拒绝时被拒修复 SQL 存 trialfix_reject 审计副本 + 复跑错误文本进日志
    （事后可归因）。修复输出长度 < 原文一半视为丢逻辑直接拒绝；接受仍需
    linter 复检无 ERROR（审计副本落盘 + 规范文件回写 + 校验结果刷新），
    否则回退原状——trial_run_result 维持首次记录，不重试（同 prompt 大概率复现）。

    Returns:
        修复后的复跑试跑结果；未接受时 None。
    """
    sql_content = state.get("sql_content", "")
    canonical = state.get("sql_file") or ""
    req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
    errors_formatted = "\n".join(trial["errors"][:2])

    error_rules = ""
    if any(_RE_PARTITION_OVER_LIMIT.search(e) for e in trial["errors"]):
        suspects = _unfiltered_tables(sql_content)
        anchor_note = (
            f"注意：未检测到 inc_day 分区过滤的表引用——{'、'.join(suspects)}，优先补齐。\n\n"
            if suspects
            else ""
        )
        error_rules += (
            "原因：平台限制单次查询最多扫描 400 个分区。数仓表默认分区键为 "
            "inc_day（string，格式 YYYYMMDD），所有 FROM/JOIN 引用的数仓表"
            "（包括维表、快照表、用户表等非事实表）都必须带 inc_day 过滤。\n\n"
            "要求：给所有缺少分区过滤的表引用补上 inc_day 条件——事实表参照原 SQL "
            "中已有事实表的过滤范围，维表/快照表取相同日期；其余逻辑逐字保留，"
            "不改字段、不改聚合、不改输出列。\n\n"
            f"{anchor_note}"
        )
    if any(_RE_TABLE_NOT_FOUND.search(e) for e in trial["errors"]):
        real_tables = sorted((state.get("table_schemas") or {}).keys())
        real_list = "\n".join(f"- {t}" for t in real_tables) if real_tables else "（未获取）"
        error_rules += (
            "原因：SQL 引用了不存在的表。以下为本次需求真实可用的源表清单"
            "（来自元数据平台，唯一权威）：\n"
            f"{real_list}\n\n"
            "要求：把不存在的表替换为清单中最接近的真实表（字段不兼容时同步调整"
            "引用字段），或删除对该表的引用逻辑；换上的表必须带 inc_day 分区过滤"
            "（参照原 SQL 中已有事实表的日期范围，缺分区过滤会再次被平台拒绝）；"
            "其余逻辑逐字保留，不改字段、不改聚合、不改输出列。\n\n"
        )

    prompt = (
        "以下 Hive SQL 试跑被平台拒绝，请修复并输出完整 SQL（```sql 代码块，"
        "不要输出任何解释或说明）：\n\n"
        f"错误信息：\n{errors_formatted}\n\n"
        f"{error_rules}"
        f"原 SQL：\n{sql_content}\n"
    )
    logger.info("[task=%s] 试跑发现可自愈错误，定向自修（1 轮）", req_name)
    try:
        fix_response = call_llm(state, "sql_fix", prompt)
    except Exception as e:
        logger.warning("[task=%s] 试跑自修: LLM 调用失败，保持原 SQL: %s", req_name, e)
        return None

    fixed_sql = extract_sql_block(fix_response)
    if not is_valid_sql(fixed_sql) or fixed_sql == sql_content:
        logger.warning("[task=%s] 试跑自修: 修复输出无效或未变更，保持原 SQL", req_name)
        return None
    if len(fixed_sql) < len(sql_content) * 0.5:
        # 第十七刀：长度腰斩=丢逻辑嫌疑（run 20 骨架残缺同族），直接拒绝
        logger.warning(
            "[task=%s] 试跑自修: 修复输出过短（%d 字符 < 原 %d 一半），疑似丢逻辑，保持原 SQL",
            req_name,
            len(fixed_sql),
            len(sql_content),
        )
        return None

    # 复跑试跑：按类别分桶判定（第十九刀 run 25 实录修订）——改善与不恶化
    # 只看使命类别（partition/table，自修的触发类别），部分改善接受；other
    # 新增仅在使命类别双清零时豁免（残留 other 交审查门禁），mission 未清
    # 时 other 新增仍否决（防「换进更差 other」）
    new_trial = _run_trial_selects(fixed_sql)
    old_cats = _classify_trial_errors(trial["errors"])
    new_cats = _classify_trial_errors(new_trial["errors"])
    healable = ("partition", "table")
    improved = any(len(new_cats[k]) < len(old_cats[k]) for k in healable)
    not_worse = all(len(new_cats[k]) <= len(old_cats[k]) for k in healable)
    mission_cleared = all(len(new_cats[k]) == 0 for k in healable if old_cats[k])
    other_ok = len(new_cats["other"]) <= len(old_cats["other"]) or mission_cleared
    if not (improved and not_worse and other_ok):
        # 拒绝留痕（run 25「二换一被拒」无法事后归因的教训；_self_check_fix
        # 同语义：失败尝试不抹除，事后可归因）
        save_artifact(state, f"Phase4-{req_name}_trialfix_reject.sql", fixed_sql)
        logger.warning(
            "[task=%s] 试跑自修: 复跑未改善（错误分桶 %s → %s），保持原 SQL；"
            "被拒修复已存 Phase4-%s_trialfix_reject.sql，复跑错误: %s",
            req_name,
            {k: len(v) for k, v in old_cats.items()},
            {k: len(v) for k, v in new_cats.items()},
            req_name,
            "; ".join(new_trial["errors"])[:300],
        )
        return None

    # 接受：审计副本 + 规范文件回写 + linter 复检（修复不得引入新 ERROR）
    save_artifact(state, f"Phase4-{req_name}_trialfix.sql", fixed_sql)
    state["sql_content"] = fixed_sql
    if canonical:
        try:
            _resolve_sql_path(state, canonical).write_text(fixed_sql, encoding="utf-8")
        except Exception:
            logger.warning(
                "[task=%s] 试跑自修: 规范文件回写失败（审计副本已保存）",
                req_name,
                exc_info=True,
            )
    _auto_validate(state, canonical)
    error_issues = [
        i
        for i in (state.get("validation_result") or {}).get("issues", [])
        if i.get("level") == "ERROR"
    ]
    if error_issues:
        # linter 复检不过 → 回退原 SQL 并还原校验结果（第十五刀同款回退纪律）
        logger.warning(
            "[task=%s] 试跑自修: 修复引入 linter ERROR（%d 个），回退原 SQL",
            req_name,
            len(error_issues),
        )
        state["sql_content"] = sql_content
        if canonical:
            try:
                _resolve_sql_path(state, canonical).write_text(sql_content, encoding="utf-8")
            except Exception:
                logger.warning("[task=%s] 试跑自修: 原SQL回写失败", req_name, exc_info=True)
        _auto_validate(state, canonical)
        return None

    logger.info("[task=%s] 试跑自修: 修复生效（复跑经分桶门禁改善）", req_name)
    return new_trial


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
    except Exception as e:
        # 降级可见性：异常原本只进日志，交付总报告/UI无从得知缺血缘图。
        # 写 error 标记，report._collect_degradations 据此打降级横幅。
        logger.warning("后台血缘生成异常，不阻塞流程", exc_info=True)
        state["lineage_result"] = {
            **(state.get("lineage_result") or {}),
            "error": str(e)[:200] or type(e).__name__,
        }
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
