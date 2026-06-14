"""Aqueduct — 顶层入口类。

提供简洁的 Python API，封装 DAG 工作流执行。
支持开发模式、审查模式和变更管理模式。

用法:
    from aqueduct import Aqueduct

    agent = Aqueduct()
    result = agent.dev("requirement.md")
"""

from __future__ import annotations

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


class AqueductResult:
    """工作流执行结果。"""

    def __init__(self, state: WorkflowState) -> None:
        self._state = state

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
        """是否执行成功（无致命错误）。"""
        return len(self.errors) == 0

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
        status = "success" if self.success else f"failed ({len(self.errors)} errors)"
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
    ) -> AqueductResult:
        """开发模式：从需求文档到完整交付。

        Args:
            requirement: 需求文档路径（.md 文件）或需求文本内容。
            output_dir: 输出目录路径。默认 output/{需求名}/。

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

        for phase_name, node_func in _DEV_PHASES:
            try:
                state = node_func(state)
            except Exception as e:
                state.setdefault("errors", []).append(f"{phase_name}: {e!s}")

        return AqueductResult(state)

    def change(
        self,
        original: str,
        new: str,
        desc: str = "",
        output_dir: str | None = None,
    ) -> AqueductResult:
        """变更管理：管理交付后的需求变更。

        Args:
            original: 原始需求文档路径。
            new: 新需求文档路径。
            desc: 变更描述。
            output_dir: 输出目录路径。

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

        for phase_name, node_func in _CHANGE_PHASES:
            try:
                state = node_func(state)
            except Exception as e:
                state.setdefault("errors", []).append(f"{phase_name}: {e!s}")

        return AqueductResult(state)
