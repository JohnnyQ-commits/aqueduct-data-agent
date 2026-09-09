"""Phase 5: DQC 质检节点。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TypedDict

from ...exceptions import LLMError
from ...skills.base import SkillContext
from ...skills.registry import get_skill
from ..state import WorkflowState
from .helpers import call_llm, extract_sql_block, is_valid_sql, save_artifact

logger = logging.getLogger(__name__)


class _DqcCategory(TypedDict):
    """DQC 测试类别定义（P0-3 拆分生成的单元）。"""

    key: str  # 唯一标识（合并排序键）
    name: str  # 类别名（prompt 与 -- [分类-名称] 前缀）
    focus: str  # 本类测试要求（单类 prompt 的核心指令）
    example: str  # 本类格式示例


# P0-3: 5 类测试拆 5 次小调用的类别定义（源自 dqc_quality.tpl.md 全量模板，
# 顺序即合并顺序）。根因（v5 实测）：单发大 prompt 要求同时覆盖 5 类 ×
# 严格注释格式 × 业务反证，约束密度过高——always-thinking 模型 9/9 确定性
# 思考螺旋（烧满 32768 零正文）。单类 prompt 只保留本类指令 + 全局格式契约。
_DQC_CATEGORIES: list[_DqcCategory] = [
    {
        "key": "uniqueness",
        "name": "唯一性",
        "focus": (
            "- 主键字段必须检查两种：重复（group by ... having count(*) > 1）与 NULL\n"
            "- 核心业务字段（度量值、维度键）逐个做非空检查\n"
            '- 主键不明确时，标注"主键待确认"并给出候选主键的唯一性测试'
        ),
        "example": (
            "-- [唯一性-主键重复] 检查 order_id 是否重复\n"
            "-- 权重: High\n"
            "-- 阈值: >0\n"
            "select\n"
            "    order_id,\n"
            "    count(*) as cnt\n"
            "from order_stats\n"
            "where inc_day = '${bizdate}'\n"
            "group by order_id\n"
            "having count(*) > 1\n"
            ";\n"
            "-- 预期: 0 行（主键不应重复）"
        ),
    },
    {
        "key": "refutation",
        "name": "业务逻辑反证",
        "focus": (
            '- 从 domain_context 提取业务规则，反证"不该出现的数据确实没出现"\n'
            "  （如负数金额、非法状态流转、未来日期）\n"
            "- 必须是业务语义反证，不能只检查 NULL\n"
            "- 业务规则有歧义时生成测试并加 -- TODO: clarify 注释"
        ),
        "example": (
            "-- [业务逻辑反证-金额正数] 检查金额是否大于 0\n"
            "-- 权重: High\n"
            "-- 阈值: <=0\n"
            "select\n"
            "    count(*) as invalid_count\n"
            "from order_stats\n"
            "where inc_day = '${bizdate}'\n"
            "  and amount <= 0\n"
            ";\n"
            "-- 预期: 0（金额必须为正）"
        ),
    },
    {
        "key": "consistency",
        "name": "跨表一致性",
        "focus": (
            "- 目标表与源表（从核心 SQL 的输入表识别）总量对比\n"
            "- 维度字段与源表/维表对齐检查\n"
            "- 差异判定写相对值或明确的判定条件，不写绝对值"
        ),
        "example": (
            "-- [跨表一致性-总量对比] 与源表总量对比\n"
            "-- 权重: Medium\n"
            "-- 阈值: 差异>0\n"
            "select\n"
            "    (select count(*) from order_stats where inc_day = '${bizdate}') as target_count,\n"
            "    (select count(*) from source_order where inc_day = '${bizdate}') as source_count\n"
            ";\n"
            "-- 预期: target_count = source_count（或差异在可接受范围内）"
        ),
    },
    {
        "key": "boundary",
        "name": "边界值",
        "focus": (
            "- 数值字段极值合理性（如年龄 > 150 为异常）\n"
            "- 空字符串与特殊字符检查\n"
            "- 格式类字段用正则匹配校验"
        ),
        "example": (
            "-- [边界值-城市非空] 检查 city 是否为空字符串\n"
            "-- 权重: Medium\n"
            "-- 阈值: >0\n"
            "select\n"
            "    count(*) as empty_count\n"
            "from order_stats\n"
            "where inc_day = '${bizdate}'\n"
            "  and (city is null or city = '')\n"
            ";\n"
            "-- 预期: 0（city 不应为空）"
        ),
    },
    {
        "key": "fluctuation",
        "name": "波动监控",
        "focus": (
            "- 总量环比波动率检测（如 |今日-昨日|/昨日 > 50% 为异常），用相对值不用绝对值\n"
            "- 与上一分区对比：inc_day = date_sub('${bizdate}', 1)\n"
            "- 若该表无历史数据场景，改为基于当日分布的异常检测"
        ),
        "example": (
            "-- [波动监控-总量环比] 与昨日总量对比\n"
            "-- 权重: Low\n"
            "-- 阈值: 波动率>50%\n"
            "select\n"
            "    (select count(*) from order_stats where inc_day = '${bizdate}') as today_count,\n"
            "    (select count(*) from order_stats where inc_day = date_sub('${bizdate}', 1)) as yesterday_count\n"
            ";\n"
            "-- 预期: 波动率 < 50%（突增突降需关注）"
        ),
    },
]


def _dqc_input_hash(state: WorkflowState) -> int:
    """计算投机 DQC 的输入指纹（sql + ddl）。

    修复循环改写 SQL 后哈希变化，node_dqc 据此丢弃过期投机结果。
    """
    return hash((state.get("sql_content", ""), state.get("ddl_content", "")))


def build_dqc_category_prompt(state: WorkflowState, category: _DqcCategory) -> str | None:
    """构建单类 DQC prompt（P0-3 拆分模式）。

    Returns:
        prompt 文本。Skill 执行失败时返回 None。
    """
    skill = get_skill("dqc_quality")
    context = SkillContext(
        input={
            "ddl_content": state.get("ddl_content", ""),
            "sql_content": state.get("sql_content", ""),
            "domain_context": state.get("domain_context", ""),
            # TODO-6: 需求摘要 + 设计方案直达 DQC——从 SQL 反推业务规则是
            # 循环论证，业务反证类用例必须知道「SQL 该做什么」
            "requirement_summary": state.get("requirement_summary", ""),
            "design_scheme": state.get("design_scheme", ""),
            "category": dict(category),
        },
        state=state,
    )
    result = skill.execute(context)
    if not result.success:
        return None
    return result.data.get("prompt", "")


def _has_case_header(sql: str) -> bool:
    """检查 SQL 是否含 DQC 用例注释头（-- [...]）。

    与 _parse_dqc_sql 的拆分契约一致：无注释头的响应无法解析为用例
    （冒烟实测：网关拥塞时返回 55 字符罐头错误文本，非空但无内容）。
    """
    return any(line.strip().startswith("-- [") for line in sql.split("\n"))


def _generate_dqc_split(state: WorkflowState) -> str:
    """P0-3: 拆分生成 DQC —— 每类测试一次小调用，5 类并行，固定顺序合并。

    单类失败降级跳过（合并结果末尾附 [DQC降级] 注释，node_dqc 据此记
    errors），全类失败才抛错——部分降级不再炸整轮（对齐修复循环降级模式）。

    Returns:
        合并后的 DQC SQL（按 _DQC_CATEGORIES 顺序）。
    Raises:
        LLMError: 全部类别生成失败。
    """
    prompts: list[tuple[_DqcCategory, str]] = []
    for cat in _DQC_CATEGORIES:
        prompt = build_dqc_category_prompt(state, cat)
        if prompt is None:
            logger.warning("DQC 类别「%s」prompt 构建失败，该类降级", cat["name"])
            continue
        prompts.append((cat, prompt))

    results: dict[str, str] = {}  # key -> 提取后的 SQL
    failures: list[str] = []  # 失败类别名（含空响应/异常/格式无效）

    def _call_one(cat: _DqcCategory, prompt: str) -> str:
        """单类生成：响应无 -- [ 注释头视为无效（罐头错误/答非所问），
        重试一次，仍无效抛 LLMError（由外层降级）。"""
        for attempt in (1, 2):
            response = call_llm(state, "dqc_gen", prompt)
            sql = extract_sql_block(response).strip()
            if sql and _has_case_header(sql):
                return sql
            logger.warning(
                "DQC 类别「%s」响应无有效用例注释头（尝试 %d/2），响应片段: %.80r",
                cat["name"],
                attempt,
                response,
            )
        raise LLMError(f"类别「{cat['name']}」响应无有效用例（格式不符）")

    with ThreadPoolExecutor(
        max_workers=max(1, len(prompts)), thread_name_prefix="dqc-split"
    ) as pool:
        future_to_cat = {pool.submit(_call_one, cat, prompt): cat for cat, prompt in prompts}
        for fut in as_completed(future_to_cat):
            cat = future_to_cat[fut]
            try:
                results[cat["key"]] = fut.result()
            except Exception as e:
                logger.warning("DQC 类别「%s」生成失败（已降级跳过）: %s", cat["name"], e)
                failures.append(cat["name"])

    if not results:
        raise LLMError(f"DQC 拆分生成失败: 全部 {len(failures)} 类生成失败")

    parts = [results[cat["key"]] for cat in _DQC_CATEGORIES if cat["key"] in results]
    if failures:
        parts.append("-- [DQC降级] 类别「" + "、".join(failures) + "」本轮生成失败，已跳过该类测试")
        logger.warning(
            "DQC 拆分生成部分降级: %d/%d 类成功，失败: %s",
            len(results),
            len(_DQC_CATEGORIES),
            "、".join(failures),
        )
    return "\n\n".join(parts)


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

        # P0-3: 投机也走拆分生成（5 类并行小调用，单 future 返回合并结果，
        # PERF-9 的 state 管道不变）
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dqc-spec")
        future = executor.submit(_generate_dqc_split, state)
        state["_dqc_spec_future"] = future
        state["_dqc_spec_executor"] = executor
        state["_dqc_spec_input_hash"] = _dqc_input_hash(state)
        logger.info("投机 DQC 拆分生成已在后台启动（5 类并行，与审查并行，PERF-9/P0-3）")
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

    P0-3: 拆分生成 —— 每类测试一次小调用（5 类并行）后按固定顺序合并，
    根治单发大 prompt 的确定性思考螺旋（v5 实测 9/9 烧满 32768 零正文）。
    优先消费 Phase 4.5 投机启动的并行结果（PERF-9，哈希护栏校验）。
    """
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    start = time.time()
    logger.info("[task=%s, phase=5] DQC 质检开始", req_name)

    try:
        # PERF-9: 优先复用与审查并行的投机结果
        llm_response = take_speculative_dqc(state)

        if llm_response is None:
            llm_response = _generate_dqc_split(state)

        dqc_sql = extract_sql_block(llm_response)

        save_artifact(state, "Phase5-数据质量测试.sql", dqc_sql)
        state["dqc_result"] = dqc_sql
        state["metadata"] = {**(state.get("metadata", {})), "dqc_done": "true"}

        # P0-3: 部分类别降级时记 errors（用户可见，不炸整轮）
        for line in dqc_sql.split("\n"):
            if "[DQC降级]" in line:
                degraded_msg = f"DQC 部分类别降级: {line.strip()}"
                state.setdefault("errors", []).append(degraded_msg)
                logger.warning("[task=%s] %s", req_name, degraded_msg)

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
