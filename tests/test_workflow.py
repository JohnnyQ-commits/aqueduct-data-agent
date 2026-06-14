"""工作流引擎测试。"""

from __future__ import annotations

from src.aqueduct.engine.state import WorkflowState
from src.aqueduct.engine.workflow import (
    StateGraph,
    build_dev_workflow,
    build_review_workflow,
)


class TestStateGraph:
    """StateGraph 基础测试。"""

    def test_create_graph(self):
        graph = StateGraph(state_type=dict)
        assert graph is not None

    def test_add_node(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("test_node", lambda state: state)
        assert "test_node" in graph._nodes

    def test_add_edge(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("a", lambda s: s)
        graph.add_node("b", lambda s: s)
        graph.add_edge("a", "b")
        assert ("a", "b") in graph._edges

    def test_set_entry_point(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("start", lambda s: s)
        graph.set_entry_point("start")
        assert graph._entry_point == "start"

    def test_compile(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("a", lambda s: s)
        graph.add_node("b", lambda s: s)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        compiled = graph.compile()
        assert compiled is not None


class TestBuildWorkflows:
    """工作流构建测试。"""

    def test_build_dev_workflow(self):
        workflow = build_dev_workflow()
        assert workflow is not None

    def test_build_review_workflow(self):
        workflow = build_review_workflow()
        assert workflow is not None


class TestWorkflowState:
    """WorkflowState 类型测试。"""

    def test_create_state(self):
        state: WorkflowState = {
            "requirement": "test requirement",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }
        assert state["requirement"] == "test requirement"
        assert state["mode"] == "dev"

    def test_state_optional_fields(self):
        state: WorkflowState = {
            "requirement": "",
            "mode": "dev",
            "metadata": {},
            "errors": [],
            "artifacts": [],
        }
        # Optional fields can be absent
        assert state.get("requirement_summary") is None
        assert state.get("design_scheme") is None
