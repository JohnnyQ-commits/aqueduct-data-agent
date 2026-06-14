"""工作流定义 — Workflow 引擎。

开发/审查双模式的 DAG 工作流执行器。
每个节点只做参数组装 + 状态透传 + 调用对应 Skill。

设计原则:
- 兼容 LangGraph StateGraph API（后续可无缝迁移）
- 节点无 Prompt、无业务逻辑
- 支持错误恢复和重试
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..exceptions import WorkflowError
from .recovery import RecoveryStrategy
from .state import WorkflowState

logger = logging.getLogger(__name__)


# 类型别名：节点函数签名
NodeFunc = Callable[[WorkflowState], WorkflowState]


class StateGraph:
    """状态图构建器（兼容 LangGraph StateGraph API）。

    用法:
        graph = StateGraph(WorkflowState)
        graph.add_node("requirement", node_requirement)
        graph.add_edge("requirement", "design")
        graph.set_entry_point("requirement")
        graph.add_edge("report", END)
        app = graph.compile()
        result = app.invoke(initial_state)
    """

    def __init__(self, state_type: type[WorkflowState]) -> None:
        """初始化状态图。

        Args:
            state_type: 工作流状态类型（TypedDict）。
        """
        self._nodes: dict[str, NodeFunc] = {}
        self._edges: list[tuple[str, str]] = []
        self._entry_point: str | None = None

    def add_node(self, name: str, func: NodeFunc) -> None:
        """添加一个节点到图中。

        Args:
            name: 节点名称。
            func: 节点执行函数，接收 WorkflowState，返回 WorkflowState。
        """
        self._nodes[name] = func

    def add_edge(self, source: str, target: str) -> None:
        """添加一条有向边。

        Args:
            source: 源节点名称。
            target: 目标节点名称。
        """
        self._edges.append((source, target))

    def set_entry_point(self, name: str) -> None:
        """设置入口节点。

        Args:
            name: 入口节点名称。
        """
        self._entry_point = name

    def compile(self, recovery: RecoveryStrategy | None = None) -> CompiledWorkflow:
        """编译工作流。

        Args:
            recovery: 错误恢复策略。未指定时使用默认策略。

        Returns:
            CompiledWorkflow 实例。
        """
        if not self._entry_point:
            raise WorkflowError("未设置入口节点")

        # 验证所有边引用的节点都存在
        for source, target in self._edges:
            if source not in self._nodes and source != "__start__":
                raise WorkflowError(f"边源节点 '{source}' 不存在")
            if target not in self._nodes and target != "__end__":
                raise WorkflowError(f"边目标节点 '{target}' 不存在")

        return CompiledWorkflow(
            nodes=self._nodes,
            edges=self._edges,
            entry_point=self._entry_point,
            recovery=recovery or RecoveryStrategy(),
        )


# 特殊常量
END = "__end__"


class CompiledWorkflow:
    """编译后的工作流实例。

    通过拓扑排序执行 DAG 节点，支持错误恢复。
    """

    def __init__(
        self,
        nodes: dict[str, NodeFunc],
        edges: list[tuple[str, str]],
        entry_point: str,
        recovery: RecoveryStrategy,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._entry_point = entry_point
        self._recovery = recovery

    def invoke(self, state: WorkflowState) -> WorkflowState:
        """执行工作流。

        按 DAG 拓扑顺序执行节点，每个节点的输出作为下一个节点的输入。
        错误时根据恢复策略决定重试/跳过/终止。

        Args:
            state: 初始工作流状态。

        Returns:
            最终工作流状态。

        Raises:
            WorkflowError: 工作流无法继续时抛出。
        """
        # 构建邻接表
        adj: dict[str, list[str]] = {}
        in_degree: dict[str, int] = {}
        for source, target in self._edges:
            if target == END:
                continue
            adj.setdefault(source, []).append(target)
            in_degree[target] = in_degree.get(target, 0) + 1

        # 初始化入口节点入度
        if self._entry_point not in in_degree:
            in_degree[self._entry_point] = 0

        # 拓扑排序 + 执行
        queue = [self._entry_point]
        executed: set[str] = set()

        while queue:
            # 取第一个入度为 0 的节点
            node_name = queue.pop(0)

            if node_name in executed:
                continue

            # 检查前置节点是否都已执行
            predecessors = [s for s, t in self._edges if t == node_name]
            if not all(p in executed or p == "__start__" for p in predecessors):
                queue.append(node_name)  # 重新入队
                continue

            # 执行节点
            if node_name in self._nodes:
                state = self._execute_node(node_name, state)

                # 检查是否有致命错误导致终止
                if state.get("errors"):
                    last_error = state["errors"][-1]
                    if "终止" in last_error or "halt" in last_error.lower():
                        logger.warning("工作流因错误终止于节点 '%s'", node_name)
                        break

            executed.add(node_name)

            # 将后继节点入队
            for next_node in adj.get(node_name, []):
                in_degree[next_node] = in_degree.get(next_node, 0) - 1
                if in_degree[next_node] <= 0:
                    queue.append(next_node)

        logger.info("工作流执行完成, 已执行 %d 个节点", len(executed))
        return state

    def _execute_node(
        self,
        node_name: str,
        state: WorkflowState,
    ) -> WorkflowState:
        """执行单个节点，带错误恢复。

        Args:
            node_name: 节点名称。
            state: 当前工作流状态。

        Returns:
            更新后的工作流状态。
        """
        node_func = self._nodes.get(node_name)
        if not node_func:
            state.setdefault("errors", []).append(f"节点 '{node_name}' 未注册")
            return state

        attempt = 0
        max_retries = self._recovery._policy.max_retries

        while attempt <= max_retries:
            attempt += 1
            try:
                logger.debug("执行节点 '%s'（第 %d 次）", node_name, attempt)
                return node_func(state)
            except Exception as e:
                result = self._recovery.recover(node_name, e, attempt)

                if result.action == "retry":
                    continue
                elif result.action == "skip" or result.action == "halt":
                    state.setdefault("errors", []).append(result.message)
                    return state

        # 不应到达这里
        state.setdefault("errors", []).append(f"节点 '{node_name}' 未知错误")
        return state


# ============================================================
# 开发模式 DAG 定义
# ============================================================


def build_dev_workflow(
    recovery: RecoveryStrategy | None = None,
) -> CompiledWorkflow:
    """构建开发模式工作流 DAG。

    强顺序链路:
        requirement → design → ddl → sql → review → dqc → report → END

    Args:
        recovery: 错误恢复策略。

    Returns:
        编译后的工作流实例。
    """
    from .nodes import (
        node_ddl,
        node_design,
        node_dqc,
        node_report,
        node_requirement,
        node_review,
        node_sql,
    )

    graph = StateGraph(WorkflowState)

    # 注册节点
    graph.add_node("requirement", node_requirement)
    graph.add_node("design", node_design)
    graph.add_node("ddl", node_ddl)
    graph.add_node("sql", node_sql)
    graph.add_node("review", node_review)
    graph.add_node("dqc", node_dqc)
    graph.add_node("report", node_report)

    # 强顺序边
    graph.set_entry_point("requirement")
    graph.add_edge("requirement", "design")
    graph.add_edge("design", "ddl")
    graph.add_edge("ddl", "sql")
    graph.add_edge("sql", "review")
    graph.add_edge("review", "dqc")
    graph.add_edge("dqc", "report")
    graph.add_edge("report", END)

    return graph.compile(recovery=recovery)


# ============================================================
# 审查模式 DAG 定义
# ============================================================


def build_review_workflow(
    recovery: RecoveryStrategy | None = None,
) -> CompiledWorkflow:
    """构建审查模式工作流 DAG。

    强顺序链路:
        requirement → review → dqc → report → END

    Args:
        recovery: 错误恢复策略。

    Returns:
        编译后的工作流实例。
    """
    from .nodes import (
        node_dqc,
        node_report,
        node_requirement,
        node_review,
    )

    graph = StateGraph(WorkflowState)

    # 注册节点
    graph.add_node("requirement", node_requirement)
    graph.add_node("review", node_review)
    graph.add_node("dqc", node_dqc)
    graph.add_node("report", node_report)

    # 强顺序边
    graph.set_entry_point("requirement")
    graph.add_edge("requirement", "review")
    graph.add_edge("review", "dqc")
    graph.add_edge("dqc", "report")
    graph.add_edge("report", END)

    return graph.compile(recovery=recovery)


# ============================================================
# 变更管理模式 DAG 定义
# ============================================================


def build_change_workflow(
    recovery: RecoveryStrategy | None = None,
) -> CompiledWorkflow:
    """构建变更管理工作流 DAG。

    强顺序链路:
        identify → document → sql → review → merge → archive → END

    Args:
        recovery: 错误恢复策略。

    Returns:
        编译后的工作流实例。
    """
    from .nodes import (
        node_change_archive,
        node_change_document,
        node_change_identify,
        node_change_merge,
        node_change_review,
        node_change_sql,
    )

    graph = StateGraph(WorkflowState)

    # 注册节点
    graph.add_node("identify", node_change_identify)
    graph.add_node("document", node_change_document)
    graph.add_node("sql", node_change_sql)
    graph.add_node("review", node_change_review)
    graph.add_node("merge", node_change_merge)
    graph.add_node("archive", node_change_archive)

    # 强顺序边
    graph.set_entry_point("identify")
    graph.add_edge("identify", "document")
    graph.add_edge("document", "sql")
    graph.add_edge("sql", "review")
    graph.add_edge("review", "merge")
    graph.add_edge("merge", "archive")
    graph.add_edge("archive", END)

    return graph.compile(recovery=recovery)
