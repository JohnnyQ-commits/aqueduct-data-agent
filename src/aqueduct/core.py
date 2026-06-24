"""Aqueduct — 顶层入口类。

提供简洁的 Python API，封装 DAG 工作流执行。
支持开发模式、审查模式和变更管理模式。

用法:
    from aqueduct import Aqueduct

    agent = Aqueduct()
    result = agent.dev("requirement.md")
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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


def _is_halt_error(error_msg: str) -> bool:
    """判断错误消息是否表示工作流应终止。

    优先依赖结构化异常 WorkflowHaltError（参见 _run_pipeline 中的捕获）。
    此函数仅作降级辅助：当节点未抛出 WorkflowHaltError 但错误消息
    明确包含终止标记时，仍然终止工作流。
    """
    return "终止" in error_msg or "halt" in error_msg.lower()


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

    # 读取 sql_fix 模板
    try:
        from string import Template

        from .config.settings import get_settings

        settings = get_settings()
        tpl_path = settings.prompt_dir / "sql_fix.tpl.md"
        if tpl_path.exists():
            raw_tpl = tpl_path.read_text(encoding="utf-8")
            tpl = Template(raw_tpl)
            prompt = tpl.safe_substitute(
                sql_content=sql_content,
                issues_formatted=issues_formatted,
            )
        else:
            # 模板不存在时使用默认 prompt
            prompt = (
                f"你是一个 SQL 工程师。以下 SQL 在代码审查中发现了问题，请修复。\n\n"
                f"## 原始 SQL\n```sql\n{sql_content}\n```\n\n"
                f"## 审查发现的问题\n{issues_formatted}\n\n"
                f"请修复上述问题，输出完整的修复后 SQL。"
            )
    except Exception:
        prompt = (
            f"请修复以下 SQL 的审查问题：\n\n{issues_formatted}\n\n"
            f"原始 SQL：\n```sql\n{sql_content}\n```"
        )

    logger.info("[task=%s] 修复循环: 发送修复 prompt（%d 字符）", req_name, len(prompt))

    fix_response = call_llm(state, "sql_fix", prompt)
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
    sql_path = save_artifact(state, f"Phase4-{req_name}_fix{fix_iterations + 1}.sql", fixed_sql)
    state["sql_content"] = fixed_sql
    state["sql_file"] = sql_path
    state["fix_iterations"] = fix_iterations + 1
    state["_needs_fix_loop"] = False

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
) -> AqueductResult:
    """统一的工作流管线执行器。

    Args:
        state: 初始工作流状态。
        phases: 节点流水线列表，每项为 (阶段名, 节点函数)。
        interactive: 是否启用交互模式。
        confirm_after: 在该阶段完成后触发确认回调（如 "requirement"）。
        on_confirm: 确认回调函数。
        on_progress: 进度回调函数。

    Returns:
        AqueductResult 包含所有产出物和状态。
    """
    total = len(phases)
    halted = False

    # 设置任务级日志
    req_name = state.get("metadata", {}).get("requirement_name", "unknown")
    metadata = state.get("metadata", {})
    output_dir_name = metadata.get("output_dir") or req_name
    from .config.settings import get_settings

    out_dir = Path(output_dir_name)
    if not out_dir.is_absolute():
        out_dir = get_settings().project_root / "output" / out_dir
    task_handler = setup_task_logging(req_name, out_dir)

    logger.info("[task=%s] 管道启动: phases=%d", req_name, total)

    i = 0
    while i < len(phases):
        phase_name, node_func = phases[i]
        idx = i + 1

        # 进度回调
        if on_progress:
            on_progress(phase_name, idx, total, state)

        try:
            state = node_func(state)
        except WorkflowHaltError as e:
            state.setdefault("errors", []).append(f"{phase_name}: {e!s}")
            logger.warning("[task=%s] 管道终止: phase=%s, 原因=%s", req_name, phase_name, e)
            halted = True
            break
        except Exception as e:
            state.setdefault("errors", []).append(f"{phase_name}: {e!s}")
            logger.error("阶段 '%s' 异常: %s", phase_name, e, exc_info=True)

        # 检查是否有致命错误需要终止
        errors = state.get("errors", [])
        if errors and _is_halt_error(errors[-1]):
            logger.warning(
                "[task=%s] 管道终止: phase=%s, 原因=%s", req_name, phase_name, errors[-1]
            )
            halted = True
            break

        # 审查→修复回环
        if phase_name == "review" and state.get("_needs_fix_loop"):
            from .config.settings import get_settings

            fix_iterations = state.get("fix_iterations", 0)
            max_fix_iterations = get_settings().max_fix_iterations
            if fix_iterations >= max_fix_iterations:
                logger.warning(
                    "[task=%s] 修复循环: 已达最大迭代次数 %d/%d，跳过回环，继续后续阶段",
                    req_name,
                    fix_iterations,
                    max_fix_iterations,
                )
                state["_needs_fix_loop"] = False
            else:
                logger.info("[task=%s] 审查→修复回环：回到 SQL 阶段重新执行", req_name)
                state = _run_fix_loop(state)
                # 回到 sql 节点重新跑
                sql_idx = next(
                    (j for j, (name, _) in enumerate(phases) if name == "sql"),
                    None,
                )
                if sql_idx is not None:
                    i = sql_idx
                    continue

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
    ) -> AqueductResult:
        """开发模式：从需求文档到完整交付。

        Args:
            requirement: 需求文档路径（.md 文件）或需求文本内容。
            output_dir: 输出目录路径。默认 output/{需求名}/。
            interactive: 是否启用交互模式（Phase 1 后暂停确认）。
            on_confirm: 确认回调（interactive=True 时用于 Phase 1 后确认）。
            on_progress: 进度回调，每个阶段开始时调用。
            external_sql_path: 外部 SQL 文件路径。非空时 Phase 4 跳过 LLM 生成。

        Returns:
            AqueductResult 包含所有产出物和内容。
        """
        req_path = Path(requirement)
        if req_path.exists():
            requirement_text = req_path.read_text(encoding="utf-8")
            req_name = req_path.stem
        else:
            requirement_text = requirement
            req_name = "requirement"

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

        return _run_pipeline(
            state,
            _DEV_PHASES,
            interactive=interactive,
            confirm_after="requirement",
            on_confirm=on_confirm,
            on_progress=on_progress,
        )

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
