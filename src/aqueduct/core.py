"""Aqueduct — 顶层入口类。

提供简洁的 Python API，封装 DAG 工作流执行。
支持开发模式、审查模式和变更管理模式。

用法:
    from aqueduct import Aqueduct

    agent = Aqueduct()
    result = agent.dev("requirement.md")
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine.nodes import (
    node_change_archive,
    node_change_document,
    node_change_identify,
    node_change_merge,
    node_change_review,
    node_change_sql,
    node_ddl,
    node_design,
    node_dqc,
    node_report,
    node_requirement,
    node_review,
    node_sql,
)
from .engine.recovery import ErrorSeverity, RecoveryStrategy
from .engine.state import WorkflowState
from .exceptions import WorkflowHaltError
from .utils.task_logger import remove_task_handler, setup_task_logging

logger = logging.getLogger(__name__)

# 开发模式节点流水线
_DEV_PHASES: list[tuple[str, Any]] = [
    ("requirement", node_requirement),
    ("design", node_design),
    ("ddl", node_ddl),
    ("sql", node_sql),
    ("review", node_review),
    ("dqc", node_dqc),
    ("report", node_report),
]

# 变更管理节点流水线
_CHANGE_PHASES: list[tuple[str, Any]] = [
    ("change_identify", node_change_identify),
    ("change_document", node_change_document),
    ("change_sql", node_change_sql),
    ("change_review", node_change_review),
    ("change_merge", node_change_merge),
    ("change_archive", node_change_archive),
]

# 确认回调类型：接收 WorkflowState，返回 True 继续 / False 停止
ConfirmCallback = Callable[[WorkflowState], bool]
# 进度回调类型：接收 (阶段名, 阶段序号, 总阶段数, 状态)
ProgressCallback = Callable[[str, int, int, WorkflowState], None]
# Phase 完成回调类型：接收 (阶段名, 状态) —— P1-3 断点续跑 checkpoint 落盘点
PhaseCompleteCallback = Callable[[str, WorkflowState], None]


def _completed_prefix_len(completed: list[str], phase_names: list[str]) -> int:
    """计算 completed 与当前 Phase 序列一致的真前缀长度。

    防御异构/损坏 manifest：首项不匹配即 0（全量重跑），
    尾部未知 Phase 名截断到已知前缀。
    """
    n = 0
    for done, current in zip(completed, phase_names, strict=False):
        if done != current:
            break
        n += 1
    return n


def _is_halt_error(error_msg: str) -> bool:
    """判断错误消息是否表示工作流应终止。

    优先依赖结构化异常 WorkflowHaltError（参见 _run_pipeline 中的捕获）。
    此函数仅作降级辅助：当节点未抛出 WorkflowHaltError 但错误消息
    明确包含终止标记时，仍然终止工作流。
    """
    lowered = error_msg.lower()
    # 精确匹配终止标记，避免误判（如"终止符"、"terminated"等非致命场景）
    halt_markers = ["[终止]", "终止工作流", "halt", "fatal"]
    return any(marker in lowered for marker in halt_markers)


def _run_fix_loop(state: WorkflowState) -> WorkflowState:
    """审查→修复循环：根据审查发现的问题让 LLM 修复 SQL。"""
    from .config.settings import get_settings
    from .engine.nodes.helpers import call_llm, extract_sql_block, is_valid_sql, save_artifact

    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    sql_content = state.get("sql_content", "")
    issues = state.get("_review_issues", [])

    if not sql_content or not issues:
        return state

    # 最大迭代保护：防止无限循环
    fix_iterations = state.get("fix_iterations", 0)
    max_fix_iterations = get_settings().max_fix_iterations
    if fix_iterations >= max_fix_iterations:
        logger.warning(
            "[task=%s] 修复循环: 已达最大迭代次数 %d，跳过",
            req_name,
            max_fix_iterations,
        )
        state["_needs_fix_loop"] = False
        return state

    # 格式化审查问题
    issues_lines = []
    for i, issue in enumerate(issues, 1):
        severity = issue.get("severity", "Unknown")
        message = issue.get("message", "")
        issues_lines.append(f"{i}. [{severity}] {message}")
    issues_formatted = "\n".join(issues_lines)

    # 组装修复 prompt（与 Phase 4 生成自检共用同一提示词契约）
    from .engine.nodes.helpers import build_sql_fix_prompt

    prompt = build_sql_fix_prompt(sql_content, issues_formatted)

    logger.info("[task=%s] 修复循环: 发送修复 prompt（%d 字符）", req_name, len(prompt))

    try:
        fix_response = call_llm(state, "sql_fix", prompt)
    except Exception as e:
        # 修复失败不应炸管道（v4 实测：空响应重试耗尽曾杀死 Phase 6）——
        # 降级：记录错误、保留未修复 SQL、清除回环标志、继续后续阶段
        state.setdefault("errors", []).append(f"修复循环 LLM 调用失败，保留未修复 SQL: {e!s}")
        logger.error(
            "[task=%s] 修复循环: LLM 调用失败，保留原 SQL 继续管道: %s",
            req_name,
            e,
            exc_info=True,
        )
        state["_needs_fix_loop"] = False
        return state

    fixed_sql = extract_sql_block(fix_response)

    if not is_valid_sql(fixed_sql):
        logger.warning(
            "[task=%s] 修复循环: LLM 修复输出无效（%d 字符），保留原 SQL",
            req_name,
            len(fixed_sql),
        )
        state["_needs_fix_loop"] = False
        return state

    # 保存修复后的 SQL
    fix_iterations = state.get("fix_iterations", 0)
    req_name = state.get("metadata", {}).get("requirement_name", "etl_sql")
    # 规范文件回写：Phase4-{req}.sql 是交付物本体（evals 对全部 Phase4-*.sql
    # 做 linter、下游按规范文件消费），_fixN 只作审计副本——不回写等于
    # 交付物停留在修复前版本
    canonical_sql_file = state.get("sql_file", "")
    sql_path = save_artifact(state, f"Phase4-{req_name}_fix{fix_iterations + 1}.sql", fixed_sql)
    state["sql_content"] = fixed_sql
    state["sql_file"] = canonical_sql_file or sql_path
    state["fix_iterations"] = fix_iterations + 1
    state["_needs_fix_loop"] = False

    if canonical_sql_file:
        try:
            from .engine.nodes.sql import _resolve_sql_path

            _resolve_sql_path(state, canonical_sql_file).write_text(fixed_sql, encoding="utf-8")
            logger.info(
                "[task=%s] 修复循环: 修复 SQL 已回写规范文件 %s", req_name, canonical_sql_file
            )
        except Exception:
            logger.warning(
                "[task=%s] 修复循环: 规范 SQL 文件回写失败（审计副本已保存）",
                req_name,
                exc_info=True,
            )

    logger.info(
        "[task=%s] 修复循环完成: fix_iterations=%d, fixed_sql=%d 字符",
        req_name,
        fix_iterations + 1,
        len(fixed_sql),
    )

    return state


def _run_pipeline(
    state: WorkflowState,
    phases: list[tuple[str, Any]],
    interactive: bool = False,
    confirm_after: str | None = None,
    on_confirm: ConfirmCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_phase_complete: PhaseCompleteCallback | None = None,
) -> AqueductResult:
    """统一的工作流管线执行器。

    Args:
        state: 初始工作流状态。
        phases: 节点流水线列表，每项为 (阶段名, 节点函数)。
        interactive: 是否启用交互模式。
        confirm_after: 在该阶段完成后触发确认回调（如 "requirement"）。
        on_confirm: 确认回调函数。
        on_progress: 进度回调函数。
        on_phase_complete: Phase 真正完成后的回调（P1-3 断点续跑 checkpoint）。
            触发时机：节点成功返回且未 halt；审查→修复回环中不触发
            （回环 continue 跳过），回环收敛后触发一次。回调异常只告警不阻塞管道。

    Returns:
        AqueductResult 包含所有产出物和状态。
    """
    total = len(phases)
    halted = False
    errors: list[str] = []

    # 设置任务级日志（不创建目录，FileHandler 在目录不存在时优雅降级）
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    metadata = state.get("metadata", {})
    output_dir_name = metadata.get("output_dir") or req_name
    from .config.settings import get_settings

    settings = get_settings()
    out_dir = Path(output_dir_name)
    if not out_dir.is_absolute():
        out_dir = settings.project_root / "output" / out_dir
    log_file_path = out_dir / f"task.{datetime.now():%Y-%m-%d}.log"
    task_handler = setup_task_logging(req_name, log_file_path)

    logger.info("[task=%s] 管道启动: phases=%d", req_name, total)

    # 初始化错误恢复策略
    recovery = RecoveryStrategy()

    # P1-3 降级冻结基线：上次干净 checkpoint 时的 errors 长度（errors 只增不减，
    # 超过基线 = 出现过降级）。resume 恢复的旧快照若含历史 errors，基线随
    # 管道启动时的 errors 初始化，不误冻结干净 Phase。
    checkpoint_errors_baseline = len(state.get("errors") or [])
    checkpoint_frozen = False

    i = 0
    while i < len(phases):
        phase_name, node_func = phases[i]
        idx = i + 1

        # 进度回调
        if on_progress:
            on_progress(phase_name, idx, total, state)

        # 带恢复策略的节点执行（临时错误可重试）
        attempt = 0
        max_retries = recovery._policy.max_retries

        while attempt <= max_retries:
            attempt += 1
            try:
                state = node_func(state)
                break
            except WorkflowHaltError as e:
                state.setdefault("errors", []).append(f"{phase_name}: {e!s}")
                logger.warning("[task=%s] 管道终止: phase=%s, 原因=%s", req_name, phase_name, e)
                halted = True
                break
            except Exception as e:
                severity = recovery.classify_error(e)

                if severity == ErrorSeverity.TRANSIENT and attempt < max_retries:
                    result = recovery.recover(phase_name, e, attempt)
                    logger.warning(
                        "[task=%s] 阶段 '%s' 临时错误，第 %d/%d 次重试 (%.1fs): %s",
                        req_name,
                        phase_name,
                        attempt,
                        max_retries,
                        result.delay_seconds,
                        e,
                    )
                    recovery.wait_and_retry(result)
                    continue

                # 非临时错误或已达重试上限
                if severity == ErrorSeverity.VALIDATION:
                    result = recovery.recover(phase_name, e, attempt)
                    state.setdefault("errors", []).append(f"{phase_name}: {result.message}")
                    logger.warning(
                        "[task=%s] 阶段 '%s' 校验错误，跳过: %s",
                        req_name,
                        phase_name,
                        e,
                    )
                    break

                # 致命错误
                state.setdefault("errors", []).append(f"{phase_name}: {e!s}")
                logger.error("阶段 '%s' 异常: %s", phase_name, e, exc_info=True)
                break

        if halted:
            break

        # 检查是否有致命错误需要终止（基于错误消息中的终止标记）
        errors = state.get("errors", [])
        if errors and _is_halt_error(errors[-1]):
            logger.warning(
                "[task=%s] 管道终止: phase=%s, 原因=%s",
                req_name,
                phase_name,
                errors[-1],
            )
            halted = True
            break

        # 审查→修复回环
        if phase_name == "review" and state.get("_needs_fix_loop"):
            fix_iterations = state.get("fix_iterations", 0)
            max_fix_iterations = settings.max_fix_iterations
            if fix_iterations >= max_fix_iterations:
                logger.warning(
                    "[task=%s] 修复循环: 已达最大迭代次数 %d/%d，跳过回环，继续后续阶段",
                    req_name,
                    fix_iterations,
                    max_fix_iterations,
                )
                state["_needs_fix_loop"] = False
            else:
                logger.info("[task=%s] 审查→修复回环：回到审查阶段重新审查", req_name)
                state = _run_fix_loop(state)
                # 回到 review 节点重新审查（修复后的 SQL 已在 state 中）
                review_idx = next(
                    (j for j, (name, _) in enumerate(phases) if name == "review"),
                    None,
                )
                if review_idx is not None:
                    i = review_idx
                    continue

        # P1-3 断点续跑：Phase 真正完成后落 checkpoint
        # （halt 的 Phase、回环 continue 重审中的 review 均不会走到这里）
        # 降级冻结：本 Phase 起出现降级（节点吞异常继续跑）时不记入
        # completed 前缀并冻结后续 checkpoint——前缀必须是干净前缀，
        # --resume 才能从首个降级 Phase 重跑而非跳过失败部分
        # （fixbatch-check 实录：sql_gen 超时降级仍被标记 completed，
        # 失败运行 resume 变 no-op）。判定用基线而非单次 pass 增量：
        # 修复循环降级的 errors 产生在上一 pass 的 _run_fix_loop 里。
        if on_phase_complete is not None and not halted and not checkpoint_frozen:
            if len(state.get("errors") or []) > checkpoint_errors_baseline:
                checkpoint_frozen = True
                logger.info(
                    "[task=%s] 断点续跑: phase=%s 降级完成（errors=%d），"
                    "checkpoint 冻结在上一干净 Phase，--resume 将从该 Phase 重跑",
                    req_name,
                    phase_name,
                    len(state.get("errors") or []),
                )
            else:
                try:
                    on_phase_complete(phase_name, state)
                except Exception:
                    logger.warning("断点续跑 checkpoint 回调异常（不阻塞管道）", exc_info=True)

        # 交互确认：在指定阶段完成后暂停等待用户确认
        if (
            interactive
            and confirm_after
            and phase_name == confirm_after
            and on_confirm
            and not on_confirm(state)
        ):
            logger.info("用户确认停止工作流")
            halted = True
            break

        i += 1

    logger.info(
        "[task=%s] 管道结束: success=%s, halted=%s, artifacts=%d, errors=%d",
        req_name,
        len(errors) == 0 and not halted,
        halted,
        len(state.get("artifacts", [])),
        len(errors),
    )

    # 清理后台线程资源（防止线程泄漏）
    lineage_executor = state.get("_lineage_executor")
    if lineage_executor is not None:
        with contextlib.suppress(Exception):
            lineage_executor.shutdown(wait=False)
        state.pop("_lineage_executor", None)
        state.pop("_lineage_future", None)

    # 清理任务日志处理器
    remove_task_handler(task_handler)

    return AqueductResult(state, halted=halted)


class AqueductResult:
    """工作流执行结果。"""

    def __init__(self, state: WorkflowState, halted: bool = False) -> None:
        self._state = state
        self._halted = halted

    @property
    def state(self) -> WorkflowState:
        """完整工作流状态。"""
        return self._state

    @property
    def artifacts(self) -> list[str]:
        """产出文件路径列表。"""
        return self._state.get("artifacts", [])

    @property
    def errors(self) -> list[str]:
        """错误消息列表。"""
        return self._state.get("errors", [])

    @property
    def success(self) -> bool:
        """是否执行成功（无致命错误且未中途终止）。"""
        return len(self.errors) == 0 and not self._halted

    @property
    def halted(self) -> bool:
        """工作流是否中途终止（用户取消或致命错误）。"""
        return self._halted

    @property
    def sql(self) -> str:
        """生成的 ETL SQL 内容。"""
        return self._state.get("sql_content", "")

    @property
    def ddl(self) -> str:
        """生成的 DDL 内容。"""
        return self._state.get("ddl_content", "")

    @property
    def design(self) -> str:
        """设计方案内容。"""
        return self._state.get("design_scheme", "")

    def __repr__(self) -> str:
        if self._halted:
            status = "halted"
        elif self.success:
            status = "success"
        else:
            status = f"failed ({len(self.errors)} errors)"
        return f"AqueductResult({status}, {len(self.artifacts)} artifacts)"


class Aqueduct:
    """Aqueduct 数据开发自动化框架入口。

    三种模式:
        dev()    — 从需求文档到完整交付
        review() — 验证 SQL 变更正确性
        change() — 管理交付后的需求变更

    用法:
        agent = Aqueduct()
        result = agent.dev("requirement.md", output_dir="output/project")
        print(result.artifacts)
        print(result.sql)
    """

    def dev(
        self,
        requirement: str,
        output_dir: str | None = None,
        interactive: bool = False,
        on_confirm: ConfirmCallback | None = None,
        on_progress: ProgressCallback | None = None,
        external_sql_path: str | None = None,
        resume: bool = False,
    ) -> AqueductResult:
        """开发模式：从需求文档到完整交付。

        Args:
            requirement: 需求文档路径（.md 文件）或需求文本内容。
            output_dir: 输出目录路径。默认 output/{需求名}/。
            interactive: 是否启用交互模式（Phase 1 后暂停确认）。
            on_confirm: 确认回调（interactive=True 时用于 Phase 1 后确认）。
            on_progress: 进度回调，每个阶段开始时调用。
            external_sql_path: 外部 SQL 文件路径。非空时 Phase 4 跳过 LLM 生成。
            resume: 断点续跑（P1-3）。True 时读取 output_dir 的 checkpoint，
                需求哈希匹配则跳过已完成 Phase 前缀，从断点继续；
                无 checkpoint / 需求已变更时自动退化为全量运行。

        失败自动断点续跑（AQUEDUCT_AUTO_RESUME_ATTEMPTS，默认 1）：降级收尾
        （success=False 且未 halt）且有干净 checkpoint 前缀时，自动带 resume
        重启只重跑降级部分；前缀无增长（确定性缺陷）即停，首 Phase 降级与
        halt 不续跑。

        Returns:
            AqueductResult 包含所有产出物和内容。
        """
        req_path = Path(requirement)
        if req_path.exists():
            from .utils.file_parser import parse_requirement_file

            requirement_text = parse_requirement_file(req_path)
            req_name = req_path.stem
        else:
            requirement_text = requirement
            req_name = "requirement"

        from .config.settings import get_settings
        from .utils.table_cache import TableSchemaCache

        settings = get_settings()
        auto_attempts_left = settings.auto_resume_attempts

        # 失败自动断点续跑：降级收尾时带 resume 重启（外层循环）。
        # 每次尝试从干净基态重建 state + 快照覆盖——降级尝试残留的
        # 中间产物（sql_content 等）不得泄漏进下一次尝试（手动 resume
        # 的干净语义同样适用于进程内自动续跑）。
        while True:
            state: WorkflowState = {
                "requirement": requirement_text,
                "mode": "dev",
                "metadata": {"requirement_name": req_name},
                "errors": [],
                "artifacts": [],
            }
            if output_dir:
                state["metadata"]["output_dir"] = output_dir
            if external_sql_path:
                state["external_sql_path"] = external_sql_path

            # 注入表结构缓存（跨 Phase 共享，避免重复 MCP 查询）
            cache_persist_path = settings.project_root / ".cache" / "table_schemas.json"
            state["_table_schema_cache"] = TableSchemaCache(
                ttl_seconds=86400,  # 24 小时
                persist_path=cache_persist_path,
            )

            # P1-3 断点续跑：读取 checkpoint，跳过已完成 Phase 前缀
            phases = _DEV_PHASES
            completed_phases: list[str] = []
            restored_prefix_len = 0
            if resume:
                from .engine.nodes.helpers import get_output_dir
                from .utils.change_analyzer import ChangeAnalyzer

                checkpoint = ChangeAnalyzer(output_dir=get_output_dir(state)).load_checkpoint(
                    requirement_text
                )
                if checkpoint is not None:
                    # 快照不含运行时对象（表结构缓存等），dev() 已注入的实例保持有效
                    ChangeAnalyzer.restore_state(state, checkpoint["state_snapshot"])
                    prefix_len = _completed_prefix_len(
                        checkpoint["phases_completed"], [name for name, _ in _DEV_PHASES]
                    )
                    completed_phases = checkpoint["phases_completed"][:prefix_len]
                    restored_prefix_len = prefix_len
                    phases = _DEV_PHASES[prefix_len:]
                    logger.info(
                        "[task=%s] 断点续跑: 跳过已完成 Phase %s",
                        req_name,
                        completed_phases,
                    )
                    if not phases:
                        logger.info(
                            "[task=%s] 断点续跑: 全部 Phase 已完成，直接返回上次结果", req_name
                        )
                        return AqueductResult(state, halted=False)

            def _save_checkpoint(
                phase_name: str, st: WorkflowState, _completed: list[str] = completed_phases
            ) -> None:
                from .engine.nodes.helpers import get_output_dir
                from .utils.change_analyzer import ChangeAnalyzer

                _completed.append(phase_name)
                ChangeAnalyzer(output_dir=get_output_dir(st)).save_checkpoint(
                    st.get("requirement", ""), _completed, st
                )

            result = _run_pipeline(
                state,
                phases,
                interactive=interactive,
                confirm_after="requirement",
                on_confirm=on_confirm,
                on_progress=on_progress,
                on_phase_complete=_save_checkpoint,
            )

            # 失败自动断点续跑：降级收尾（未 halt）且有干净前缀可复用、
            # 本轮前缀有增长（确定性缺陷重跑不增长，立即停）时带 resume 重启
            if (
                not result.success
                and not result.halted
                and auto_attempts_left > 0
                and len(completed_phases) > restored_prefix_len
            ):
                auto_attempts_left -= 1
                resume = True
                logger.info(
                    "[task=%s] 自动断点续跑: 管道失败收尾（errors=%d），"
                    "从干净前缀 %s 续跑（剩余自动续跑 %d 次）",
                    req_name,
                    len(result.errors),
                    completed_phases,
                    auto_attempts_left,
                )
                continue
            return result

    def change(
        self,
        original: str,
        new: str,
        desc: str = "",
        output_dir: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> AqueductResult:
        """变更管理：管理交付后的需求变更。

        Args:
            original: 原始需求文档路径。
            new: 新需求文档路径。
            desc: 变更描述。
            output_dir: 输出目录路径。
            on_progress: 进度回调，每个阶段开始时调用。

        Returns:
            AqueductResult 包含 CR 编号和归档信息。
        """
        orig_path = Path(original)
        new_path = Path(new)

        state: WorkflowState = {
            "requirement": "",
            "mode": "change",
            "original_requirement": orig_path.read_text(encoding="utf-8"),
            "new_requirement": new_path.read_text(encoding="utf-8"),
            "change_description": desc,
            "metadata": {"requirement_name": orig_path.stem},
            "errors": [],
            "artifacts": [],
        }
        if output_dir:
            state["metadata"]["output_dir"] = output_dir

        return _run_pipeline(state, _CHANGE_PHASES, on_progress=on_progress)
